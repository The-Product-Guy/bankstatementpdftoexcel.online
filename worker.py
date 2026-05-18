
# Load environment variables FIRST before anything else
from dotenv import load_dotenv
load_dotenv()

import os
import tempfile
import redis
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from celery_config import celery_app
from parsers.chunk_utils import (
    build_page_ranges,
    merge_raw_tables,
    merge_quality_reports,
    sort_transactions_for_output,
)
from parsers.ledger_validation import (
    StatementSummary,
    format_minor,
    has_summary_values,
    ledger_rows_from_transactions,
    validate_ledger_rows,
)
from parsers.ledger_repair import LedgerRepairReport, repair_transactions_from_balance_deltas
from parsers.statement_summary import extract_statement_summary_from_pdf
from parsers.universal_parser import create_universal_parser, UnsupportedLanguageError
from pdf_utils import PasswordProtectedPDFError, get_pdf_page_count
from storage_utils import get_storage_config, delete_file, download_file, upload_file
from db import get_db_session, init_db, DATABASE_URL
from models import Job, UsageCounter
import logging
import pandas as pd

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Suppress verbose HTTP logs from OpenAI SDK
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

# Ensure database tables exist for worker (especially in local/one-off runs).
try:
    init_db()
except Exception as e:
    logger.warning(f"Database initialization failed: {e}")

if DATABASE_URL.startswith("sqlite"):
    logger.warning("DATABASE_URL not set; worker is using SQLite. Connect Postgres to this service in Railway.")

# Redis for progress updates (separate from Celery broker if needed, but usually same)
redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
redis_client = redis.StrictRedis.from_url(redis_url)
_PADDLE_WARMUP_AVAILABLE = False


def _summary_metric_rows(summary: StatementSummary) -> List[List[Any]]:
    return [
        ["Statement opening balance", format_minor(summary.opening_balance_minor)],
        ["Statement closing balance", format_minor(summary.closing_balance_minor)],
        ["Statement debit count", summary.debit_count],
        ["Statement debit total", format_minor(summary.total_debit_minor)],
        ["Statement credit count", summary.credit_count],
        ["Statement credit total", format_minor(summary.total_credit_minor)],
    ]


def _ledger_validation_sheets(
    transactions: List[Dict[str, Any]],
    summary: Optional[StatementSummary] = None,
    repair_report: Optional[LedgerRepairReport] = None,
):
    if not transactions:
        return None, None, None

    summary = summary or StatementSummary()
    report = validate_ledger_rows(ledger_rows_from_transactions(transactions), summary)
    summary_df = pd.DataFrame([
        ["Rows", report.row_count],
        ["Balance checks passed", report.balance_checks_passed],
        ["Balance checks total", report.balance_checks],
        ["Balance consistency %", report.balance_consistency_pct],
        ["Rows repaired", repair_report.repaired_count if repair_report else 0],
        ["Debit count", report.debit_count],
        ["Debit total", format_minor(report.total_debit_minor)],
        ["Credit count", report.credit_count],
        ["Credit total", format_minor(report.total_credit_minor)],
        ["Statement summary found", has_summary_values(summary)],
        *_summary_metric_rows(summary),
        ["Issue count", len(report.issues)],
        ["Valid", report.is_valid],
    ], columns=["Metric", "Value"])
    issues_df = pd.DataFrame([
        {
            "Severity": issue.severity,
            "Code": issue.code,
            "Row": issue.row_index,
            "Expected": format_minor(issue.expected_minor),
            "Actual": format_minor(issue.actual_minor),
            "Message": issue.message,
        }
        for issue in report.issues
    ])
    if issues_df.empty:
        issues_df = pd.DataFrame([{
            "Severity": "info",
            "Code": "none",
            "Row": "",
            "Expected": "",
            "Actual": "",
            "Message": "No ledger validation issues detected.",
        }])
    return report, summary_df, issues_df


def _repair_audit_sheet(repair_report: Optional[LedgerRepairReport]):
    if not repair_report or not repair_report.actions:
        return None
    return pd.DataFrame(repair_report.to_records())


def _warmup_ocr_models():
    """
    Pre-load PaddleOCR models at worker startup so first request isn't slow.
    Without this, the first conversion takes an extra 15-30s for model loading.
    With ONNX Runtime, warmup takes ~5-10s and reduces first-request latency to match subsequent ones.
    """
    global _PADDLE_WARMUP_AVAILABLE

    if not os.environ.get('USE_PADDLEOCR', 'true').lower() in {'1', 'true', 'yes', 'on'}:
        logger.info("PaddleOCR disabled via USE_PADDLEOCR, skipping warmup")
        _PADDLE_WARMUP_AVAILABLE = False
        return

    try:
        import time
        start = time.time()
        logger.info("Pre-warming PaddleOCR models (ONNX Runtime backend)...")
        from parsers.paddleocr_processor import get_paddleocr
        ocr = get_paddleocr()
        # Run a tiny inference to fully initialize the ONNX/Paddle runtime graph
        import numpy as np
        dummy = np.zeros((64, 64, 3), dtype=np.uint8)
        try:
            ocr.ocr(dummy, cls=False)
        except Exception:
            # Some versions throw on blank images; model is still loaded
            pass
        elapsed = time.time() - start
        logger.info(f"PaddleOCR models ready in {elapsed:.1f}s")
        _PADDLE_WARMUP_AVAILABLE = True
    except Exception as e:
        logger.warning(f"OCR model warmup failed (will lazy-load on first request): {e}")
        _PADDLE_WARMUP_AVAILABLE = False


# Warm up models at worker import time (before accepting tasks)
_warmup_ocr_models()

def _update_job(job_id, **fields):
    try:
        with get_db_session() as db:
            job = db.get(Job, job_id)
            if not job:
                return
            for key, value in fields.items():
                setattr(job, key, value)
    except Exception as e:
        logger.warning(f"DB job update failed for {job_id}: {e}")

def _increment_usage(job_id):
    try:
        with get_db_session() as db:
            job = db.get(Job, job_id)
            if not job:
                return

            monthly_scope = f"monthly:{datetime.utcnow().strftime('%Y-%m')}"

            for scope in ('lifetime', monthly_scope):
                if job.user_id:
                    counter = db.query(UsageCounter).filter_by(
                        user_id=job.user_id,
                        guest_id=None,
                        scope=scope
                    ).first()
                else:
                    counter = db.query(UsageCounter).filter_by(
                        user_id=None,
                        guest_id=job.guest_id,
                        scope=scope
                    ).first()

                if not counter:
                    counter = UsageCounter(
                        user_id=job.user_id,
                        guest_id=job.guest_id,
                        scope=scope,
                        conversions_count=0,
                        pages_total=0,
                        bytes_total=0
                    )
                    db.add(counter)

                counter.conversions_count += 1
                counter.pages_total += job.page_count or 0
                counter.bytes_total += job.file_size_bytes or 0
                counter.updated_at = datetime.utcnow()
    except Exception as e:
        logger.warning(f"DB usage update failed for {job_id}: {e}")

def update_progress(job_id, current, total, status_message, percent_override=None, extra=None):
    """Publish progress update to Redis channel"""
    try:
        if percent_override is not None:
            percent = max(0, min(100, int(percent_override)))
        elif total > 0:
            percent = int((current / total) * 100)
        else:
            percent = 0
            
        message = {
            'job_id': job_id,
            'current': current,
            'total': total,
            'percent': percent,
            'status': status_message
        }
        if extra:
            message.update(extra)
        
        # Publish to job-specific channel
        channel = f"job_progress:{job_id}"
        redis_client.publish(channel, str(message))
        # Also set a key for polling fallback
        redis_client.setex(f"job_status:{job_id}", 3600, str(message))
        
    except Exception as e:
        logger.error(f"Failed to update progress: {str(e)}")

@celery_app.task(bind=True, name='worker.process_pdf')
def process_pdf_task(self, file_ref, original_filename, job_id, api_key=None, quality='standard'):
    """
    Celery task to process a PDF file.
    
    Args:
        quality: 'standard' (150 DPI) or 'high' (200 DPI) for OCR resolution.
                 Higher DPI improves accuracy on faded/poor-quality scans but is slower.
    """
    logger.info(f"Starting job {job_id} for file {original_filename} (quality={quality})")
    
    if api_key:
        logger.warning("Job %s provided a per-request API key; per-job API keys are ignored.", job_id)
    
    temp_dir = None
    local_input_path = None
    storage = None
    input_storage_key = None
    output_storage_key = None
    retain_input_pdf = False
    try:
        storage = get_storage_config()
        if isinstance(file_ref, dict):
            ref_type = file_ref.get("type")
            retain_input_pdf = bool(file_ref.get("retain_for_feedback"))
            if ref_type == "s3":
                if not storage:
                    raise RuntimeError("Storage is not configured for S3 input.")
                input_storage_key = file_ref.get("key")
                temp_dir = tempfile.mkdtemp(prefix=f"job_{job_id}_")
                local_input_path = os.path.join(temp_dir, original_filename)
                download_file(storage, input_storage_key, local_input_path)
                file_path = local_input_path
            elif ref_type == "local":
                file_path = file_ref.get("path")
            else:
                raise RuntimeError(f"Unsupported file_ref type: {ref_type}")
        else:
            file_path = file_ref

        if not file_path or not os.path.exists(file_path):
            raise FileNotFoundError(f"PDF file not found: {file_path}")

        _update_job(job_id, status="processing", started_at=datetime.utcnow())

        # Define progress callback wrapper
        def progress_callback(data):
            current_page = data.get('current_page') or data.get('page_num') or data.get('current') or 0
            total_pages = data.get('total_pages') or data.get('total') or 0
            status = data.get('status', 'Processing...')
            stage = data.get('stage')

            percent_override = None
            if stage and total_pages > 0:
                if stage == 'text_extract':
                    start, end = 0, 40
                elif stage == 'ocr':
                    start, end = 0, 60
                elif stage == 'llm_text':
                    start, end = 40, 95
                elif stage == 'llm_ocr':
                    start, end = 60, 95
                elif stage == 'excel':
                    start, end = 95, 100
                else:
                    start = end = None

                if start is not None:
                    ratio = min(max(current_page / total_pages, 0), 1)
                    percent_override = start + (end - start) * ratio

            update_progress(job_id, current_page, total_pages, status, percent_override=percent_override)

        # Select execution preset by environment + quality mode:
        # - Local default: local-low-mem
        # - Railway default: prod-balanced
        # - High quality: prod-high-accuracy (except local low-mem default)
        railway_detected = bool(
            os.environ.get("RAILWAY_ENVIRONMENT")
            or os.environ.get("RAILWAY_PROJECT_ID")
            or os.environ.get("RAILWAY_SERVICE_ID")
        )
        env_preset = os.environ.get("EXECUTION_PRESET", "").strip().lower()
        if env_preset:
            execution_preset = env_preset
        else:
            execution_preset = "prod-balanced" if railway_detected else "local-low-mem"

        if quality == "high":
            if not (execution_preset == "local-low-mem" and not railway_detected and not env_preset):
                execution_preset = "prod-high-accuracy"

        runtime_use_paddleocr = True
        runtime_use_img2table = True
        runtime_use_pymupdf = os.environ.get('USE_PYMUPDF', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
        runtime_use_llm = os.environ.get('USE_LLM', '').strip().lower() in {'1', 'true', 'yes', 'on'}
        if not _PADDLE_WARMUP_AVAILABLE:
            # In environments where PaddleOCR isn't installed (or failed init),
            # img2table's Paddle backend can fall back to very heavy Tesseract paths
            # and cause worker instability. Force the safer fallback pipeline.
            runtime_use_paddleocr = False
            runtime_use_img2table = False
            if execution_preset != "local-low-mem":
                logger.warning(
                    f"Job {job_id}: PaddleOCR unavailable, forcing safe OCR mode "
                    f"(preset={execution_preset} -> paddleocr/img2table disabled)"
                )

        logger.info(f"Job {job_id}: execution_preset={execution_preset}, quality={quality}")
        parser = create_universal_parser(
            progress_callback=progress_callback,
            # These override environment defaults - remove to use env vars fully
            execution_preset=execution_preset,
            use_paddleocr=runtime_use_paddleocr,
            use_img2table=runtime_use_img2table,
            use_pymupdf=runtime_use_pymupdf,
            use_llm=runtime_use_llm,
        )
        
        # Determine output path using absolute paths
        processed_root = os.environ.get('SHARED_STORAGE_PATH') or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'processed'
        )
        output_dir = os.path.join(processed_root, job_id)
        os.makedirs(output_dir, exist_ok=True)
        
        update_progress(job_id, 0, 100, "Starting extraction...")
        chunk_orchestrated = False

        chunk_enabled = os.environ.get("CHUNK_ORCHESTRATION_ENABLED", "true").strip().lower() in {
            "1", "true", "yes", "on"
        }
        chunk_min_pages = int(os.environ.get("CHUNK_ORCHESTRATION_MIN_PAGES", "80"))
        chunk_size_pages = int(os.environ.get("CHUNK_SIZE_PAGES", "40"))

        total_pages_for_chunk: Optional[int] = None
        try:
            total_pages_for_chunk = get_pdf_page_count(file_path)
        except PasswordProtectedPDFError:
            raise
        except Exception as exc:
            logger.warning(f"Job {job_id}: failed to read page count for chunking: {exc}")

        # Run parsing
        if (
            chunk_enabled
            and total_pages_for_chunk
            and total_pages_for_chunk >= chunk_min_pages
            and chunk_size_pages > 0
        ):
            chunk_orchestrated = True
            page_ranges = build_page_ranges(total_pages_for_chunk, chunk_size_pages)
            logger.info(
                f"Job {job_id}: chunk orchestration enabled, pages={total_pages_for_chunk}, "
                f"chunk_size={chunk_size_pages}, chunks={len(page_ranges)}"
            )

            all_transactions: List[Dict[str, Any]] = []
            merged_raw_table: Optional[Dict[str, Any]] = None
            chunk_quality_reports: List[Dict[str, Any]] = []
            last_chunk_parser = parser

            for chunk_idx, (start_page, end_page) in enumerate(page_ranges, 1):
                chunk_total = end_page - start_page + 1
                update_progress(
                    job_id,
                    chunk_idx - 1,
                    len(page_ranges),
                    f"Processing chunk {chunk_idx}/{len(page_ranges)} (pages {start_page}-{end_page})",
                    percent_override=5 + ((chunk_idx - 1) / max(len(page_ranges), 1)) * 85,
                    extra={
                        "chunk_index": chunk_idx,
                        "chunk_total": len(page_ranges),
                        "chunk_page_start": start_page,
                        "chunk_page_end": end_page,
                    }
                )

                def chunk_progress(
                    data,
                    start_page=start_page,
                    end_page=end_page,
                    chunk_idx=chunk_idx,
                    chunk_total=len(page_ranges),
                    chunk_total_pages=chunk_total
                ):
                    local_current = data.get('current_page') or data.get('page_num') or data.get('current') or 0
                    local_total = data.get('total_pages') or data.get('total') or chunk_total_pages
                    stage = data.get('stage')
                    status = data.get('status', 'Processing chunk...')

                    global_current = (start_page - 1) + max(0, int(local_current))
                    percent_override = None
                    if total_pages_for_chunk and total_pages_for_chunk > 0:
                        if stage == 'excel':
                            percent_override = 95
                        else:
                            percent_override = 5 + min(
                                85,
                                (global_current / total_pages_for_chunk) * 85
                            )

                    update_progress(
                        job_id,
                        global_current,
                        total_pages_for_chunk or local_total,
                        status,
                        percent_override=percent_override,
                        extra={
                            "chunk_index": chunk_idx,
                            "chunk_total": chunk_total,
                            "chunk_page_start": start_page,
                            "chunk_page_end": end_page,
                        }
                    )

                chunk_parser = create_universal_parser(
                    progress_callback=chunk_progress,
                    execution_preset=execution_preset,
                    use_paddleocr=runtime_use_paddleocr,
                    use_img2table=runtime_use_img2table,
                    use_pymupdf=runtime_use_pymupdf,
                    use_llm=runtime_use_llm,
                )
                chunk_transactions = chunk_parser.parse(
                    file_path,
                    original_filename,
                    page_start=start_page,
                    page_end=end_page
                )
                all_transactions.extend(chunk_transactions)
                merged_raw_table = merge_raw_tables(
                    merged_raw_table,
                    getattr(chunk_parser, "raw_table", None)
                )

                if hasattr(chunk_parser, "get_quality_report"):
                    try:
                        report = chunk_parser.get_quality_report() or {}
                        if report:
                            chunk_quality_reports.append(report)
                    except Exception:
                        pass
                last_chunk_parser = chunk_parser

            # Rehydrate parser fields with merged results for downstream output.
            parser = last_chunk_parser
            parser.transactions = all_transactions
            parser.raw_table = merged_raw_table
            try:
                pdf_type_for_meta = parser.detect_pdf_type(file_path)
                parser._build_extraction_metadata(pdf_type_for_meta, total_pages_for_chunk or len(page_ranges))
            except Exception:
                pass
            parser.quality_report = merge_quality_reports(chunk_quality_reports)
            transactions = all_transactions
        else:
            transactions = parser.parse(file_path, original_filename)

        # Enforce stable output ordering by page sequence to prevent mixed rows in Excel.
        transactions = sort_transactions_for_output(transactions)

        statement_summary = StatementSummary()
        try:
            use_summary_ocr = os.environ.get("VALIDATION_SUMMARY_OCR", "true").strip().lower() in {
                "1", "true", "yes", "on"
            }
            statement_summary = extract_statement_summary_from_pdf(
                file_path,
                sample_pages=int(os.environ.get("VALIDATION_SUMMARY_SAMPLE_PAGES", "2")),
                use_ocr=use_summary_ocr,
                logger=logger,
            )
        except Exception as exc:
            logger.warning("Statement summary extraction failed: %s", exc)

        repair_report = LedgerRepairReport(row_count=len(transactions))
        try:
            transactions, repair_report = repair_transactions_from_balance_deltas(
                transactions,
                statement_summary,
            )
            if repair_report.repaired_count:
                logger.info(
                    "Job %s: deterministically repaired %d transaction rows",
                    job_id,
                    repair_report.repaired_count,
                )
            parser.transactions = transactions
        except Exception as exc:
            logger.warning("Ledger repair failed: %s", exc)
        
        update_progress(job_id, 0, 1, "Generating Excel file...", percent_override=95)
        # Generate Excel file from transactions
        excel_filename = f"{os.path.splitext(original_filename)[0]}_extracted.xlsx"
        excel_path = os.path.join(output_dir, excel_filename)
        
        raw_table = getattr(parser, "raw_table", None)
        quality_report = {}
        if hasattr(parser, "get_quality_report"):
            try:
                quality_report = parser.get_quality_report() or {}
            except Exception:
                quality_report = {}
        ledger_report = None
        validation_df = None
        issues_df = None
        repair_df = _repair_audit_sheet(repair_report)
        try:
            ledger_report, validation_df, issues_df = _ledger_validation_sheets(
                transactions,
                statement_summary,
                repair_report,
            )
        except Exception as exc:
            logger.warning("Ledger validation failed: %s", exc)
        has_data = False

        if raw_table and raw_table.get("rows") and transactions:
            # Write both representations:
            # 1) Raw statement-like table for audit
            # 2) Normalized transactions for downstream use
            raw_df = pd.DataFrame(raw_table["rows"], columns=raw_table["columns"])

            tx_df = pd.DataFrame(transactions)
            preferred_order = [
                'Date', 'Description', 'Reference_Number',
                'Withdrawal_Amount', 'Deposit_Amount', 'Transaction_Amount',
                'Closing_Balance', 'Source_File', 'Page_Line'
            ]
            columns = [col for col in preferred_order if col in tx_df.columns]
            columns += [col for col in tx_df.columns if col not in preferred_order]
            tx_df = tx_df[columns]

            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                raw_df.to_excel(writer, index=False, sheet_name="Raw_Statement")
                tx_df.to_excel(writer, index=False, sheet_name="Normalized_Transactions")
                if validation_df is not None:
                    validation_df.to_excel(writer, index=False, sheet_name="Validation")
                if issues_df is not None:
                    issues_df.to_excel(writer, index=False, sheet_name="Issues")
                if repair_df is not None:
                    repair_df.to_excel(writer, index=False, sheet_name="Repairs")

            has_data = len(raw_table["rows"]) > 0 or len(transactions) > 0
            logger.info(
                f"Job {job_id}: Wrote {len(raw_table['rows'])} raw rows and "
                f"{len(transactions)} normalized transactions"
            )
        elif raw_table and raw_table.get("rows"):
            # Use raw table format (from layout/table extraction)
            raw_df = pd.DataFrame(raw_table["rows"], columns=raw_table["columns"])
            raw_df.to_excel(excel_path, index=False)
            has_data = len(raw_table["rows"]) > 0
            logger.info(f"Job {job_id}: Wrote {len(raw_table['rows'])} rows from raw_table")
        elif transactions:
            # Use transactions list (from regex fallback or LLM extraction)
            # Convert transactions to DataFrame with standard columns
            df = pd.DataFrame(transactions)
            # Reorder columns for better readability
            preferred_order = [
                'Date', 'Description', 'Reference_Number', 
                'Withdrawal_Amount', 'Deposit_Amount', 'Transaction_Amount',
                'Closing_Balance', 'Source_File', 'Page_Line'
            ]
            # Only include columns that exist
            columns = [col for col in preferred_order if col in df.columns]
            # Add any extra columns not in preferred order
            columns += [col for col in df.columns if col not in preferred_order]
            df = df[columns]
            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Normalized_Transactions")
                if validation_df is not None:
                    validation_df.to_excel(writer, index=False, sheet_name="Validation")
                if issues_df is not None:
                    issues_df.to_excel(writer, index=False, sheet_name="Issues")
                if repair_df is not None:
                    repair_df.to_excel(writer, index=False, sheet_name="Repairs")
            has_data = len(transactions) > 0
            logger.info(f"Job {job_id}: Wrote {len(transactions)} transactions to Excel")
        else:
            # No data extracted at all
            pd.DataFrame().to_excel(excel_path, index=False)
            logger.warning(f"Job {job_id}: No data extracted, created empty Excel")

        # --- Build extraction metadata for frontend ---
        ext_meta = getattr(parser, "extraction_metadata", None)
        result_extra = {
            "quality_used": quality,
            "execution_preset": execution_preset,
            "runtime_use_paddleocr": runtime_use_paddleocr,
            "runtime_use_img2table": runtime_use_img2table,
            "chunk_orchestrated": chunk_orchestrated,
            "statement_summary_found": has_summary_values(statement_summary),
            "ledger_repair_count": repair_report.repaired_count,
        }
        if has_summary_values(statement_summary):
            result_extra.update({
                "statement_opening_balance": format_minor(statement_summary.opening_balance_minor),
                "statement_closing_balance": format_minor(statement_summary.closing_balance_minor),
                "statement_total_debit": format_minor(statement_summary.total_debit_minor),
                "statement_total_credit": format_minor(statement_summary.total_credit_minor),
                "statement_debit_count": statement_summary.debit_count,
                "statement_credit_count": statement_summary.credit_count,
            })
        if ext_meta:
            result_extra.update({
                "extraction_rows": ext_meta.row_count,
                "extraction_cols": ext_meta.col_count,
                "extraction_method": ext_meta.extraction_method,
                "confidence": ext_meta.confidence,        # 'good', 'low', 'empty'
                "document_hint": ext_meta.document_hint,  # 'statement', 'non_tabular', 'unknown'
                "quality_message": ext_meta.message,
            })
        if quality_report:
            result_extra.update({
                "execution_preset_applied": quality_report.get("execution_preset", execution_preset),
                "accuracy_proxy_pct": quality_report.get("accuracy_proxy_pct", 0.0),
                "balance_consistency_pct": quality_report.get("balance_consistency_pct", 0.0),
                "date_parse_pct": quality_report.get("date_parse_pct", 0.0),
                "amount_coverage_pct": quality_report.get("amount_coverage_pct", 0.0),
                "quality_row_count": quality_report.get("row_count", 0),
                "row_confidence_avg": quality_report.get("row_confidence_avg", 0.0),
                "row_confidence_low_ratio_pct": quality_report.get("row_confidence_low_ratio_pct", 0.0),
                "row_confidence_low_count": quality_report.get("row_confidence_low_count", 0),
            })
        if ledger_report:
            result_extra.update({
                "ledger_valid": ledger_report.is_valid,
                "ledger_issue_count": len(ledger_report.issues),
                "ledger_balance_checks": ledger_report.balance_checks,
                "ledger_balance_checks_passed": ledger_report.balance_checks_passed,
                "ledger_balance_consistency_pct": ledger_report.balance_consistency_pct,
                "ledger_debit_count": ledger_report.debit_count,
                "ledger_credit_count": ledger_report.credit_count,
                "ledger_total_debit": format_minor(ledger_report.total_debit_minor),
                "ledger_total_credit": format_minor(ledger_report.total_credit_minor),
            })

        # Determine completion status message
        if not has_data:
            if ext_meta and ext_meta.document_hint == "non_tabular":
                status_msg = "This PDF does not appear to contain tabular data."
            else:
                status_msg = "Completed - no data extracted"
        elif ledger_report and not ledger_report.is_valid:
            status_msg = "Completed - extraction needs review"
        else:
            status_msg = "Completed successfully"

        # Upload and update progress
        if storage:
            output_storage_key = f"outputs/{job_id}/{excel_filename}"
            upload_file(storage, excel_path, output_storage_key)
            result_extra["storage"] = "s3"
            result_extra["download_key"] = output_storage_key
            update_progress(
                job_id, 100, 100, status_msg,
                percent_override=100,
                extra=result_extra
            )
        else:
            update_progress(
                job_id, 100, 100, status_msg,
                percent_override=100,
                extra=result_extra
            )
        
        _update_job(
            job_id,
            status="completed" if has_data else "completed_no_data",
            finished_at=datetime.utcnow(),
            output_storage_key=output_storage_key,
            storage_key=output_storage_key,
            transaction_count=len(transactions)
        )
        _increment_usage(job_id)

        return {
            'status': 'success',
            'job_id': job_id,
            'excel_path': excel_path,
            'transaction_count': len(transactions),
            'filename': excel_filename,
            'confidence': ext_meta.confidence if ext_meta else 'unknown',
            'accuracy_proxy_pct': quality_report.get("accuracy_proxy_pct", 0.0) if quality_report else 0.0,
            'ledger_valid': ledger_report.is_valid if ledger_report else None,
            'ledger_balance_consistency_pct': ledger_report.balance_consistency_pct if ledger_report else None,
            'ledger_repair_count': repair_report.repaired_count,
            'statement_summary_found': has_summary_values(statement_summary),
            'chunk_orchestrated': chunk_orchestrated,
        }
        
    except Exception as e:
        logger.error(f"Job {job_id} failed: {str(e)}")
        if isinstance(e, UnsupportedLanguageError):
            _update_job(job_id, status="unsupported_language", finished_at=datetime.utcnow(), error=str(e))
            update_progress(job_id, 0, 0, f"Unsupported language: {str(e)}")
        else:
            _update_job(job_id, status="failed", finished_at=datetime.utcnow(), error=str(e))
            update_progress(job_id, 0, 0, f"Error: {str(e)}")
        return {
            'status': 'error',
            'job_id': job_id,
            'error': str(e)
        }
    finally:
        if local_input_path and os.path.exists(local_input_path):
            try:
                os.remove(local_input_path)
            except Exception:
                pass
        if temp_dir and os.path.exists(temp_dir):
            try:
                os.rmdir(temp_dir)
            except Exception:
                pass
        if storage and input_storage_key and not retain_input_pdf:
            try:
                delete_file(storage, input_storage_key)
                fields = {
                    "input_storage_key": None,
                    "input_deleted_at": datetime.utcnow(),
                }
                if not output_storage_key:
                    fields["storage_key"] = None
                _update_job(job_id, **fields)
            except Exception as exc:
                logger.warning("Failed to delete input PDF for job %s: %s", job_id, exc)
