
# Load environment variables FIRST before anything else
from dotenv import load_dotenv
load_dotenv()

import os
import tempfile
import redis
from datetime import datetime
from celery_config import celery_app
from parsers.universal_parser import create_universal_parser
from storage_utils import get_storage_config, download_file, upload_file
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


def _warmup_ocr_models():
    """
    Pre-load PaddleOCR models at worker startup so first request isn't slow.
    Without this, the first conversion takes an extra 15-30s for model loading.
    With ONNX Runtime, warmup takes ~5-10s and reduces first-request latency to match subsequent ones.
    """
    if not os.environ.get('USE_PADDLEOCR', 'true').lower() in {'1', 'true', 'yes', 'on'}:
        logger.info("PaddleOCR disabled via USE_PADDLEOCR, skipping warmup")
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
    except Exception as e:
        logger.warning(f"OCR model warmup failed (will lazy-load on first request): {e}")


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

            if job.user_id:
                counter = db.query(UsageCounter).filter_by(
                    user_id=job.user_id,
                    guest_id=None,
                    scope='lifetime'
                ).first()
            else:
                counter = db.query(UsageCounter).filter_by(
                    user_id=None,
                    guest_id=job.guest_id,
                    scope='lifetime'
                ).first()

            if not counter:
                counter = UsageCounter(
                    user_id=job.user_id,
                    guest_id=job.guest_id,
                    scope='lifetime',
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
    
    # Set API key if provided (important for worker environment)
    if api_key:
        os.environ['OPENAI_API_KEY'] = api_key
    
    temp_dir = None
    local_input_path = None
    try:
        storage = get_storage_config()
        if isinstance(file_ref, dict):
            ref_type = file_ref.get("type")
            if ref_type == "s3":
                if not storage:
                    raise RuntimeError("Storage is not configured for S3 input.")
                temp_dir = tempfile.mkdtemp(prefix=f"job_{job_id}_")
                local_input_path = os.path.join(temp_dir, original_filename)
                download_file(storage, file_ref.get("key"), local_input_path)
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

        # Create parser instance with environment-based configuration
        # All settings are controlled via environment variables in ProcessingConfig
        # See ProcessingConfig docstring for resource usage guide
        #
        # Quality modes:
        #   'standard' = 150 DPI (fast, good for most bank statements)
        #   'high'     = 200 DPI (slower, better for faded/poor scans)
        dpi_override = 200 if quality == 'high' else 150
        parser = create_universal_parser(
            progress_callback=progress_callback,
            # These override environment defaults - remove to use env vars fully
            use_llm=False,  # Set USE_LLM=true to enable
            dpi=dpi_override,
        )
        
        # Determine output path using absolute paths
        processed_root = os.environ.get('SHARED_STORAGE_PATH') or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'processed'
        )
        output_dir = os.path.join(processed_root, job_id)
        os.makedirs(output_dir, exist_ok=True)
        
        update_progress(job_id, 0, 100, "Starting extraction...")
        
        # Run parsing
        transactions = parser.parse(file_path, original_filename)
        
        update_progress(job_id, 0, 1, "Generating Excel file...", percent_override=95)
        # Generate Excel file from transactions
        excel_filename = f"{os.path.splitext(original_filename)[0]}_extracted.xlsx"
        excel_path = os.path.join(output_dir, excel_filename)
        
        raw_table = getattr(parser, "raw_table", None)
        has_data = False

        if raw_table and raw_table.get("rows"):
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
            df.to_excel(excel_path, index=False)
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
        }
        if ext_meta:
            result_extra.update({
                "extraction_rows": ext_meta.row_count,
                "extraction_cols": ext_meta.col_count,
                "extraction_method": ext_meta.extraction_method,
                "confidence": ext_meta.confidence,        # 'good', 'low', 'empty'
                "document_hint": ext_meta.document_hint,  # 'statement', 'non_tabular', 'unknown'
                "quality_message": ext_meta.message,
            })

        # Determine completion status message
        if not has_data:
            if ext_meta and ext_meta.document_hint == "non_tabular":
                status_msg = "This PDF does not appear to contain tabular data."
            else:
                status_msg = "Completed - no data extracted"
        else:
            status_msg = "Completed successfully"

        # Upload and update progress
        if storage:
            output_key = f"outputs/{job_id}/{excel_filename}"
            upload_file(storage, excel_path, output_key)
            result_extra["storage"] = "s3"
            result_extra["download_key"] = output_key
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
        }
        
    except Exception as e:
        logger.error(f"Job {job_id} failed: {str(e)}")
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
