#!/usr/bin/env python3
"""
Universal Bank Statement Parser
Works with any bank statement format globally by combining:
1. PaddleOCR for enhanced text extraction
2. OpenAI Vision for intelligent table understanding
3. Fallback to legacy parsers for known formats
"""
import logging
import os
import re
import gc
import time
from typing import List, Dict, Any, Optional, Tuple, Callable
from dataclasses import dataclass
from PIL import Image
from datetime import datetime

from pdf_utils import raise_if_password_protected
from .base_parser import BaseParser
from .bank_profiles import BankProfile, detect_bank_profile, get_profile_header_aliases

logger = logging.getLogger(__name__)


class UnsupportedLanguageError(ValueError):
    """Raised when document content is outside current supported language scope."""


def _env_bool(name: str, default: bool) -> bool:
    """Read boolean from environment."""
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_int(name: str, default: int) -> int:
    """Read integer from environment."""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    """Read float from environment."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


EXECUTION_PRESETS: Dict[str, Dict[str, Any]] = {
    # Conservative preset for local development on low-memory machines.
    "local-low-mem": {
        "use_paddleocr": False,
        "use_img2table": False,
        "use_pymupdf": True,
        "use_llm": False,
        "dpi": 120,
        "preprocess_images": False,
        "adaptive_preprocess": False,
        "min_table_transactions": 3,
    },
    # Balanced production preset.
    "prod-balanced": {
        "use_paddleocr": True,
        "use_img2table": True,
        "use_pymupdf": True,
        "use_llm": False,
        "dpi": 150,
        "preprocess_images": True,
        "adaptive_preprocess": False,
        "min_table_transactions": 5,
    },
    # Higher-accuracy preset for difficult documents.
    "prod-high-accuracy": {
        "use_paddleocr": True,
        "use_img2table": True,
        "use_pymupdf": True,
        "use_llm": False,
        "dpi": 200,
        "preprocess_images": True,
        "adaptive_preprocess": True,
        "min_table_transactions": 3,
    },
}


def _normalize_execution_preset(name: Optional[str]) -> str:
    if not name:
        return ""
    cleaned = name.strip().lower().replace("_", "-")
    return cleaned


def _apply_execution_preset(config: "ProcessingConfig", preset_name: Optional[str]) -> None:
    """
    Apply preset values onto config.
    Unknown preset names are ignored and keep current config.
    """
    normalized = _normalize_execution_preset(preset_name)
    if not normalized:
        return

    preset_values = EXECUTION_PRESETS.get(normalized)
    if not preset_values:
        return

    for key, value in preset_values.items():
        setattr(config, key, value)
    config.execution_preset = normalized


@dataclass
class ProcessingConfig:
    """
    Configuration for document processing.
    All settings can be overridden via environment variables for easy tuning.
    
    Quality Modes (user-selectable):
    - STANDARD: dpi=150, preprocess=True, paddleocr=True  (default, fast)
    - HIGH:     dpi=200, preprocess=True, adaptive=True, paddleocr=True  (slower, for poor scans)
    
    Resource Usage Guide:
    - LOW:    dpi=150, preprocess=False, adaptive=False, paddleocr=False  (~1 GB RAM)
    - MEDIUM: dpi=150, preprocess=True, adaptive=False, paddleocr=True   (~2-3 GB RAM, default)
    - HIGH:   dpi=200, preprocess=True, adaptive=True, paddleocr=True    (~3-4 GB RAM)
    
    Note: Using ONNX Runtime backend (instead of full PaddlePaddle) reduces RAM by ~1.5 GB
    and speeds up inference by 30-50% with identical accuracy.
    """
    # OCR Engine Selection
    use_paddleocr: bool = _env_bool('USE_PADDLEOCR', True)  # PaddleOCR by default (better accuracy)
    use_img2table: bool = _env_bool('USE_IMG2TABLE', True)  # img2table primary path for scanned docs
    use_pymupdf: bool = _env_bool('USE_PYMUPDF', True)  # PyMuPDF fallback for text/vector tables
    use_llm: bool = _env_bool('USE_LLM', False)  # LLM extraction (requires API key)
    use_table_structure: bool = _env_bool('USE_TABLE_STRUCTURE', False)  # PPStructure (heavy)
    prefer_vision: bool = _env_bool('PREFER_VISION', False)  # Send images to LLM
    llm_model: str = os.environ.get('LLM_MODEL', 'gpt-4o-mini')
    
    # Page Limits
    max_pages: Optional[int] = None  # Limit for SaaS tiers
    
    # Image Processing - RESOURCE CRITICAL
    dpi: int = _env_int('OCR_DPI', 150)  # Lower = faster, less RAM (150 is good for most)
    dpi_high: int = _env_int('OCR_DPI_HIGH', 200)  # For low-quality re-render
    preprocess_images: bool = _env_bool('PREPROCESS_IMAGES', True)  # deskew, denoise
    adaptive_preprocess: bool = _env_bool('ADAPTIVE_PREPROCESS', False)  # Re-render at high DPI
    quality_threshold: float = _env_float('QUALITY_THRESHOLD', 0.55)
    min_ocr_chars: int = _env_int('MIN_OCR_CHARS', 40)
    
    # Extraction Tuning
    min_table_transactions: Optional[int] = _env_int('MIN_TABLE_TRANSACTIONS', 5)
    english_only_beta: bool = _env_bool('ENGLISH_ONLY_BETA', True)
    row_confidence_threshold: float = _env_float('ROW_CONFIDENCE_THRESHOLD', 0.65)
    low_conf_ratio_for_fallback: float = _env_float('LOW_CONF_RATIO_FOR_FALLBACK', 0.20)
    use_template: bool = _env_bool('USE_TEMPLATE', True)
    template_confidence_threshold: float = _env_float('TEMPLATE_CONFIDENCE_THRESHOLD', 0.6)
    execution_preset: str = "custom"


@dataclass 
class ProcessingStats:
    """Statistics about the processing job."""
    total_pages: int
    pages_processed: int
    transactions_found: int
    ocr_method: str
    llm_tokens_used: int
    estimated_cost: float
    processing_time_seconds: float


@dataclass
class ExtractionMetadata:
    """
    Metadata about what was extracted -- used to communicate result quality
    to the frontend so it can show appropriate messages (retry, feedback, etc.).
    """
    row_count: int = 0
    col_count: int = 0
    has_data: bool = False
    extraction_method: str = ""         # 'spatial', 'table_structure', 'layout', 'regex', 'llm'
    pdf_type: str = ""                  # 'text' or 'image'
    document_hint: str = "statement"    # 'statement', 'non_tabular', 'unknown'
    confidence: str = "good"            # 'good', 'low', 'empty'
    message: str = ""                   # Human-readable quality summary


class UniversalBankParser(BaseParser):
    """
    Universal parser that works with any bank statement format.
    Combines enhanced OCR with LLM-based intelligent extraction.
    """
    
    def __init__(
        self, 
        progress_callback: Optional[Callable] = None,
        config: Optional[ProcessingConfig] = None
    ):
        super().__init__(progress_callback)
        self.bank_name = "Universal"
        self.config = config or ProcessingConfig()
        self.stats = None
        self.raw_table = None
        self.extraction_metadata = ExtractionMetadata()
        self.quality_report: Dict[str, Any] = {}
        self.row_confidence_summary: Dict[str, Any] = {}
        self.active_profile: Optional[BankProfile] = None
        self.source_filename: str = ""
        self._profile_announced = False
        self._row_confidences: List[float] = []
        
        # Lazy-loaded processors
        self._paddle_processor = None
        self._paddle_unavailable = False  # Track if PaddleOCR is known unavailable
        self._llm_extractor = None
        self._tesseract_available = True
    
    @property
    def paddle_processor(self):
        """Lazy load PaddleOCR processor."""
        # Skip if already known to be unavailable
        if self._paddle_unavailable:
            return None
            
        if self._paddle_processor is None and self.config.use_paddleocr:
            try:
                from .paddleocr_processor import PaddleOCRProcessor
                self._paddle_processor = PaddleOCRProcessor(
                    use_table_structure=self.config.use_table_structure
                )
            except ImportError:
                logger.warning("PaddleOCR not available, falling back to Tesseract")
                self._paddle_unavailable = True
        return self._paddle_processor
    
    def mark_paddle_unavailable(self):
        """Mark PaddleOCR as unavailable after a runtime failure."""
        self._paddle_processor = None
        self._paddle_unavailable = True
    
    @property
    def llm_extractor(self):
        """Lazy load LLM extractor."""
        if self._llm_extractor is None and self.config.use_llm:
            try:
                from .llm_table_extractor import LLMTableExtractor
                self._llm_extractor = LLMTableExtractor(model=self.config.llm_model)
            except Exception as e:
                logger.warning("LLM extractor not available: %s", e)
        return self._llm_extractor
    
    def parse(
        self,
        pdf_path: str,
        original_filename: str,
        page_start: int = 1,
        page_end: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Parse any bank statement PDF.
        
        Args:
            pdf_path: Path to the PDF file
            original_filename: Original name of the uploaded file
            page_start: 1-based first page to process
            page_end: 1-based last page to process (inclusive), None = end of document
            
        Returns:
            List of transaction dictionaries
        """
        start_time = time.time()
        
        self.validate_pdf_file(pdf_path)
        raise_if_password_protected(pdf_path)
        self.transactions = []
        self.raw_table = None
        self.quality_report = {}
        self.row_confidence_summary = {}
        self._row_confidences = []
        self.active_profile = None
        self.source_filename = original_filename
        self._profile_announced = False
        
        logger.info("Universal Parser processing: %s", original_filename)

        # Detect PDF type
        pdf_type = self.detect_pdf_type(pdf_path)
        logger.info("Detected %s-based PDF", pdf_type)

        # Get page count and validate against limits
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            first_page_text = ""
            second_page_text = ""
            try:
                if total_pages > 0:
                    first_page_text = (pdf.pages[0].extract_text() or "")
                if total_pages > 1:
                    second_page_text = (pdf.pages[1].extract_text() or "")
            except Exception:
                first_page_text = first_page_text or ""

        # Build combined first-pages text for template detection
        # (page 1 may be a cover page without table headers)
        first_pages_text = first_page_text
        if second_page_text:
            first_pages_text = first_page_text + "\n\n--- Page 2 ---\n\n" + second_page_text
        self._first_pages_text = first_pages_text

        self._maybe_detect_profile(first_page_text=first_page_text)

        if pdf_type == "text":
            self._enforce_english_only(first_page_text, "first page text")

        start_page, end_page = self._resolve_page_window(total_pages, page_start, page_end)
        pages_to_process = end_page - start_page + 1
        
        if self.config.max_pages and pages_to_process > self.config.max_pages:
            raise ValueError(
                f"PDF has {pages_to_process} pages but limit is {self.config.max_pages}. "
                f"Please upgrade your plan or split the document."
            )
        
        if pages_to_process == total_pages:
            logger.info("Processing %d pages", pages_to_process)
        else:
            logger.info("Processing pages %d-%d (%d pages)", start_page, end_page, pages_to_process)
        
        total_tokens = 0
        ocr_method = "paddleocr" if self.paddle_processor else "tesseract"
        
        if pdf_type == "text":
            # Text-based PDF - extract directly and use LLM if available
            total_tokens = self._process_text_based(
                pdf_path,
                original_filename,
                page_start=start_page,
                page_end=end_page
            )
        else:
            # Image-based PDF - use enhanced OCR pipeline
            total_tokens = self._process_image_based(
                pdf_path,
                original_filename,
                total_pages,
                page_start=start_page,
                page_end=end_page
            )
        
        # Calculate stats
        processing_time = time.time() - start_time
        estimated_cost = 0.0
        
        if self.llm_extractor and total_tokens > 0:
            estimated_cost = self.llm_extractor.estimate_cost(
                num_pages=pages_to_process,
                text_length=total_tokens * 4  # Rough estimate
            )
        
        self.stats = ProcessingStats(
            total_pages=total_pages,
            pages_processed=pages_to_process,
            transactions_found=len(self.transactions),
            ocr_method=ocr_method,
            llm_tokens_used=total_tokens,
            estimated_cost=estimated_cost,
            processing_time_seconds=processing_time
        )
        
        # --- Build extraction metadata for frontend quality feedback ---
        self._build_extraction_metadata(pdf_type, pages_to_process)
        self.quality_report = self._build_quality_report(pages_to_process, pdf_type)
        self.row_confidence_summary = self._build_row_confidence_summary()
        if self.quality_report and self.row_confidence_summary:
            self.quality_report.update(self.row_confidence_summary)
        
        logger.info("Extracted %d transactions in %.1fs", len(self.transactions), processing_time)
        if self.quality_report:
            logger.info(
                "Quality score: %.1f%% (balance checks: %.1f%%)",
                self.quality_report.get('accuracy_proxy_pct', 0.0),
                self.quality_report.get('balance_consistency_pct', 0.0),
            )
        if self.extraction_metadata.confidence != "good":
            logger.warning("Quality: %s — %s", self.extraction_metadata.confidence, self.extraction_metadata.message)
        
        return self.transactions

    @staticmethod
    def _resolve_page_window(
        total_pages: int,
        page_start: int = 1,
        page_end: Optional[int] = None
    ) -> Tuple[int, int]:
        """Validate and normalize a 1-based inclusive page window."""
        if total_pages <= 0:
            raise ValueError("PDF contains no pages.")

        start = page_start or 1
        end = page_end if page_end is not None else total_pages
        if start < 1:
            start = 1
        if end > total_pages:
            end = total_pages
        if start > end:
            raise ValueError(
                f"Invalid page range: start={start}, end={end}, total_pages={total_pages}"
            )
        return start, end

    def _maybe_detect_profile(
        self,
        first_page_text: str = "",
        headers: Optional[List[str]] = None
    ) -> None:
        """
        Best-effort bank profile detection from filename, text signatures, and headers.
        """
        profile = detect_bank_profile(
            filename=self.source_filename or "",
            first_page_text=first_page_text,
            headers=headers
        )
        if not profile:
            return
        if self.active_profile and self.active_profile.key == profile.key:
            return

        self.active_profile = profile
        self.bank_name = profile.display_name
        if not self._profile_announced:
            logger.info("Bank profile matched: %s", profile.display_name)
            self._profile_announced = True

    @staticmethod
    def _normalize_header_text(value: str) -> str:
        cleaned = value.lower().replace("_", " ")
        cleaned = re.sub(r"[^a-z0-9\s]", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _build_quality_report(self, total_pages: int, pdf_type: str) -> Dict[str, Any]:
        """
        Build a proxy quality report for a parsed document.
        This is not label-truth accuracy; it is internal consistency scoring.
        """
        txs = self.transactions or []
        row_count = len(txs)

        if row_count == 0:
            return {
                "is_proxy": True,
                "pdf_type": pdf_type,
                "execution_preset": self.config.execution_preset,
                "total_pages": total_pages,
                "row_count": 0,
                "date_parse_pct": 0.0,
                "amount_coverage_pct": 0.0,
                "balance_coverage_pct": 0.0,
                "balance_checks": 0,
                "balance_checks_passed": 0,
                "balance_consistency_pct": 0.0,
                "accuracy_proxy_pct": 0.0,
            }

        date_ok = 0
        amount_ok = 0
        balance_ok = 0
        balance_checks = 0
        balance_checks_passed = 0
        prev_balance: Optional[float] = None

        for tx in txs:
            date_val = str(tx.get("Date") or "").strip()
            if self._looks_like_date(date_val):
                date_ok += 1

            has_amount = (
                tx.get("Transaction_Amount") is not None
                or tx.get("Withdrawal_Amount") is not None
                or tx.get("Deposit_Amount") is not None
            )
            if has_amount:
                amount_ok += 1

            curr_balance = tx.get("Closing_Balance")
            if curr_balance is not None:
                balance_ok += 1

            # Balance consistency: prev_balance + txn_amount ~= curr_balance
            txn_amount = tx.get("Transaction_Amount")
            if prev_balance is not None and curr_balance is not None and txn_amount is not None:
                balance_checks += 1
                try:
                    expected = float(prev_balance) + float(txn_amount)
                    if abs(float(curr_balance) - expected) <= 1.0:
                        balance_checks_passed += 1
                except (TypeError, ValueError):
                    pass

            if curr_balance is not None:
                prev_balance = curr_balance

        date_rate = date_ok / row_count
        amount_rate = amount_ok / row_count
        balance_rate = balance_ok / row_count
        balance_consistency = (
            (balance_checks_passed / balance_checks) if balance_checks > 0 else 0.5
        )

        # Weighted proxy score (0-100)
        proxy_score = (
            0.35 * date_rate
            + 0.25 * amount_rate
            + 0.10 * balance_rate
            + 0.30 * balance_consistency
        ) * 100.0

        return {
            "is_proxy": True,
            "pdf_type": pdf_type,
            "execution_preset": self.config.execution_preset,
            "total_pages": total_pages,
            "row_count": row_count,
            "date_parse_pct": round(date_rate * 100.0, 1),
            "amount_coverage_pct": round(amount_rate * 100.0, 1),
            "balance_coverage_pct": round(balance_rate * 100.0, 1),
            "balance_checks": balance_checks,
            "balance_checks_passed": balance_checks_passed,
            "balance_consistency_pct": round(balance_consistency * 100.0, 1),
            "accuracy_proxy_pct": round(proxy_score, 1),
        }

    def _derive_transactions_from_raw_table(
        self, source_file: str, page_ref: str = "Raw_Table"
    ) -> int:
        """
        Convert raw_table rows into canonical transactions when possible.
        Helps keep downstream output consistent even for image-heavy statements.
        """
        if not self.raw_table:
            return 0

        rows = self.raw_table.get("rows") or []
        columns = self.raw_table.get("columns") or []
        if not rows:
            return 0

        # Build a table with a header row if present.
        table: List[List[Any]] = []
        if columns:
            table.append(columns)
        table.extend(rows)

        derived = self._transactions_from_table(
            table=table,
            source_file=source_file,
            page_ref=page_ref
        )
        if not derived:
            return 0

        existing_keys = {
            (
                tx.get("Date"),
                tx.get("Description"),
                tx.get("Withdrawal_Amount"),
                tx.get("Deposit_Amount"),
                tx.get("Closing_Balance")
            )
            for tx in self.transactions
        }

        added = 0
        for tx in derived:
            key = (
                tx.get("Date"),
                tx.get("Description"),
                tx.get("Withdrawal_Amount"),
                tx.get("Deposit_Amount"),
                tx.get("Closing_Balance")
            )
            if key in existing_keys:
                continue
            self.transactions.append(tx)
            existing_keys.add(key)
            added += 1
        return added
    
    def _build_extraction_metadata(self, pdf_type: str, total_pages: int):
        """
        Populate self.extraction_metadata after parsing completes.
        Performs heuristic checks to detect:
        - Empty extractions (no data at all)
        - Non-tabular documents (resumes, letters, etc.)
        - Low confidence extractions (few rows relative to pages)
        """
        meta = self.extraction_metadata
        meta.pdf_type = pdf_type
        
        # Determine row/col counts from raw_table or transactions
        if self.raw_table and self.raw_table.get("rows"):
            meta.row_count = len(self.raw_table["rows"])
            meta.col_count = len(self.raw_table.get("columns", []))
            meta.extraction_method = "spatial"
        elif self.transactions:
            meta.row_count = len(self.transactions)
            # transactions is List[Dict]; len(dict) = number of keys = columns
            try:
                first = self.transactions[0]
                meta.col_count = len(first) if isinstance(first, dict) else 0
            except (IndexError, TypeError):
                meta.col_count = 0
            meta.extraction_method = "regex"
        else:
            meta.row_count = 0
            meta.col_count = 0
            meta.extraction_method = "none"
        
        meta.has_data = meta.row_count > 0
        
        # --- Confidence / quality assessment ---
        if meta.row_count == 0:
            meta.confidence = "empty"
            meta.document_hint = self._detect_document_type(meta)
            if meta.document_hint == "non_tabular":
                meta.message = (
                    "This PDF does not appear to contain tabular data (like a bank statement). "
                    "Statement Converter works best with bank statements and financial reports."
                )
            else:
                meta.message = (
                    "No data could be extracted. The PDF may be encrypted, image-heavy, "
                    "or in an unsupported format. Try 'High Quality' mode or submit feedback."
                )
        elif meta.row_count < 3 and total_pages > 1:
            meta.confidence = "low"
            meta.message = (
                f"Only {meta.row_count} rows extracted from {total_pages} pages. "
                "Results may be incomplete. Try 'High Quality' mode for better accuracy."
            )
        elif meta.col_count < 2:
            meta.confidence = "low"
            meta.message = (
                "The extracted data has very few columns. The table structure may not have "
                "been detected correctly. Try 'High Quality' mode or submit feedback."
            )
        else:
            meta.confidence = "good"
            meta.message = f"Extracted {meta.row_count} rows with {meta.col_count} columns."
    
    def _detect_document_type(self, meta: 'ExtractionMetadata') -> str:
        """
        Heuristic to determine if the document is likely a bank statement or not.
        Called when extraction produces zero results.
        
        Checks the raw_table and transactions; if both are empty, inspects
        whatever text was captured during processing for statement-like patterns.
        """
        # If we got data, it's probably a statement (even if low quality)
        if meta.has_data:
            return "statement"
        
        # Check if any text was captured that looks financial
        # Look at the parser's internal state for clues
        financial_patterns = [
            r'\b\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}\b',  # Date patterns
            r'\b\d{1,3}(?:[,\.]\d{3})*(?:\.\d{2})\b',      # Amount patterns (1,234.56)
            r'\b(?:balance|debit|credit|withdrawal|deposit|transaction)\b',  # Financial terms
            r'\b(?:opening|closing|statement|account)\b',    # Statement terms
        ]
        
        # We don't have the full text stored, but we can infer from
        # whether we even attempted extraction and how far we got
        # If the parser tried spatial extraction and got zero rows on all pages,
        # and regex also found nothing, the document likely isn't tabular
        if self.stats and self.stats.transactions_found == 0:
            return "non_tabular"
        
        return "unknown"
    
    def _process_text_based(
        self, 
        pdf_path: str, 
        source_file: str,
        page_start: int = 1,
        page_end: Optional[int] = None
    ) -> int:
        """
        Process text-based PDF using direct extraction. 
        Uses page-based chunking (5 pages per chunk) for reliable LLM extraction.
        Returns tokens used.
        """
        import pdfplumber
        
        chunk_mode = os.environ.get("LLM_TEXT_CHUNKING_MODE", "pages").strip().lower()
        pages_per_chunk = self._get_int_env("LLM_TEXT_PAGES_PER_CHUNK", 10)
        target_chars = self._get_int_env("LLM_TEXT_TARGET_CHARS", 12000)
        max_chars = self._get_int_env("LLM_TEXT_MAX_CHARS", 18000)
        
        # Extract all pages' text and try table extraction first
        page_texts = []
        table_transactions = []
        low_conf_pages: Optional[set] = None
        with pdfplumber.open(pdf_path) as pdf:
            doc_total_pages = len(pdf.pages)
            start_page, end_page = self._resolve_page_window(doc_total_pages, page_start, page_end)
            selected_pages = end_page - start_page + 1

            for local_page_num, actual_page_num in enumerate(range(start_page, end_page + 1), 1):
                page = pdf.pages[actual_page_num - 1]
                self.emit_progress(
                    local_page_num, selected_pages,
                    f"Extracting text from page {actual_page_num} ({local_page_num}/{selected_pages})",
                    stage="text_extract"
                )
                
                page_text = page.extract_text()
                if page_text:
                    page_texts.append((actual_page_num, page_text))

                try:
                    logger.debug("Starting extract_tables for page %d", actual_page_num)
                    _t0 = time.monotonic()
                    tables = page.extract_tables(self._table_settings())
                    logger.debug("extract_tables page %d completed in %.2fs", actual_page_num, time.monotonic() - _t0)
                except Exception:
                    tables = []

                for table in tables:
                    table_transactions.extend(
                        self._transactions_from_table(
                            table,
                            source_file=source_file,
                            page_ref=f"Page_{actual_page_num}"
                        )
                    )
        
        total_chars = sum(len(text) for _, text in page_texts)
        logger.info("Extracted %s characters from %d pages", f"{total_chars:,}", len(page_texts))
        if page_texts:
            sample_text = "\n".join(text for _, text in page_texts[:3])
            self._enforce_english_only(sample_text, "text extraction sample")

        if table_transactions:
            min_table_tx = self.config.min_table_transactions
            if min_table_tx is None or len(table_transactions) >= min_table_tx:
                if self._transactions_quality_ok(table_transactions):
                    low_ratio = self._low_confidence_ratio(table_transactions)
                    if low_ratio <= self.config.low_conf_ratio_for_fallback:
                        logger.info("Table extraction found %d transactions; skipping LLM.", len(table_transactions))
                        self.transactions.extend(table_transactions)
                        return 0
                    logger.warning(
                        "Table extraction low-confidence ratio %.1f%% exceeds threshold %.1f%%; "
                        "trying targeted fallback path.",
                        low_ratio * 100, self.config.low_conf_ratio_for_fallback * 100,
                    )
                logger.warning("Table extraction quality low, falling back to layout/LLM.")

        if self.config.use_pymupdf:
            pymupdf_transactions, pymupdf_raw_table = self._extract_pymupdf_tables(
                pdf_path,
                source_file,
                page_start=page_start,
                page_end=page_end
            )
            if pymupdf_transactions and self._transactions_quality_ok(pymupdf_transactions):
                low_ratio = self._low_confidence_ratio(pymupdf_transactions)
                if low_ratio <= self.config.low_conf_ratio_for_fallback or not self.llm_extractor:
                    logger.info("PyMuPDF table extraction found %d transactions; skipping LLM.", len(pymupdf_transactions))
                    self.transactions.extend(pymupdf_transactions)
                    self.raw_table = pymupdf_raw_table
                    return 0
                logger.warning(
                    "PyMuPDF table extraction low-confidence ratio %.1f%%; continuing fallback.",
                    low_ratio * 100,
                )

        layout_transactions, raw_table = self._extract_layout_transactions(
            pdf_path,
            source_file,
            page_start=page_start,
            page_end=page_end
        )
        if layout_transactions and self._transactions_quality_ok(layout_transactions):
            low_ratio = self._low_confidence_ratio(layout_transactions)
            if low_ratio <= self.config.low_conf_ratio_for_fallback or not self.llm_extractor:
                logger.info("Layout extraction found %d transactions; skipping LLM.", len(layout_transactions))
                self.transactions.extend(layout_transactions)
                self.raw_table = raw_table
                return 0
            logger.warning(
                "Layout low-confidence ratio %.1f%%; escalating only low-confidence content to fallback.",
                low_ratio * 100,
            )
            low_conf_pages = set()
            for tx in layout_transactions:
                score = float(tx.get("Row_Confidence", 0.0))
                if score >= self.config.row_confidence_threshold:
                    continue
                page_line = str(tx.get("Page_Line", ""))
                m = re.search(r"Page[_\s-]*(\d+)", page_line, re.IGNORECASE)
                if m:
                    low_conf_pages.add(int(m.group(1)))
        
        if not self.llm_extractor:
            # Fallback to regex-based parsing
            all_text = "\n\n".join(text for _, text in page_texts)
            self._fallback_regex_parse(all_text, source_file)
            return 0

        # --- Template-based extraction (1 LLM call max) ---
        if self.config.use_template:
            # Build first_pages_text from extracted page texts
            _fp_texts = [t for _, t in page_texts[:2]]
            _first_pages_text = ("\n\n--- Page 2 ---\n\n".join(_fp_texts)) if _fp_texts else ""
            template_result = self._try_template_extraction(
                page_texts=page_texts,
                raw_table=raw_table if layout_transactions else None,
                source_file=source_file,
                pdf_path=pdf_path,
                page_start=page_start,
                page_end=page_end,
                first_pages_text=_first_pages_text,
            )
            if template_result is not None:
                template_tokens, template_txns = template_result
                if template_txns and self._transactions_quality_ok(template_txns):
                    self.transactions.extend(template_txns)
                    return template_tokens

        # Detect columns from first page
        if not page_texts:
            return 0
        
        # Safely get first page text
        try:
            first_page_text = page_texts[0][1] if page_texts else ""
        except (IndexError, TypeError):
            first_page_text = ""
        
        if first_page_text:
            logger.info("Detecting column structure from first page...")
            column_mapping = self.llm_extractor.detect_columns(first_page_text)
            logger.info("Detected columns: %s", ", ".join(column_mapping.columns))
        
        # Build chunks list
        fallback_page_texts = page_texts
        if low_conf_pages:
            scoped = [(p, t) for (p, t) in page_texts if p in low_conf_pages]
            if scoped:
                fallback_page_texts = scoped
                logger.info("Targeted fallback scope: %d low-confidence pages", len(fallback_page_texts))

        chunks = self._build_chunks(
            page_texts=fallback_page_texts,
            chunk_mode=chunk_mode,
            pages_per_chunk=pages_per_chunk,
            target_chars=target_chars,
            max_chars=max_chars
        )
        num_chunks = len(chunks)
        
        logger.info("Processing %d chunks in parallel (%s chunking)...", num_chunks, chunk_mode)
        self.emit_progress(
            0, num_chunks,
            "LLM starting...",
            stage="llm_text"
        )
        
        # Process chunks in parallel (5 concurrent requests)
        MAX_WORKERS = min(self._get_int_env("LLM_MAX_WORKERS", 5), max(1, num_chunks))
        total_tokens = 0
        results_by_idx = {}
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def process_chunk(chunk_data):
            chunk_idx, page_range, chunk_text = chunk_data
            result = self.llm_extractor.extract_from_text(chunk_text, column_mapping)
            return chunk_idx, page_range, result
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_chunk, chunk): chunk[0] for chunk in chunks}
            completed = 0
            
            for future in as_completed(futures):
                completed += 1
                chunk_idx = futures[future]
                try:
                    chunk_idx, page_range, result = future.result(timeout=120)
                    results_by_idx[chunk_idx] = (page_range, result)

                    if result.success:
                        logger.info("Chunk %d/%d: %d transactions (%d tokens)", chunk_idx + 1, num_chunks, len(result.transactions), result.tokens_used)
                    else:
                        logger.warning("Chunk %d/%d failed: %s", chunk_idx + 1, num_chunks, result.error_message)

                    self.emit_progress(
                        completed, num_chunks,
                        f"LLM extracting chunk {completed}/{num_chunks}",
                        stage="llm_text"
                    )
                except TimeoutError:
                    logger.error("LLM chunk %d timed out after 120s", chunk_idx)
                except Exception as e:
                    logger.error("Chunk error: %s", e)
        
        # Collect results in order
        for chunk_idx in range(num_chunks):
            if chunk_idx in results_by_idx:
                page_range, result = results_by_idx[chunk_idx]
                if result.success:
                    for tx in result.transactions:
                        tx['Source_File'] = source_file
                        tx['Page_Line'] = f"Pages_{page_range}"
                        self.transactions.append(tx)
                    total_tokens += result.tokens_used
        
        return total_tokens

    def _extract_pymupdf_tables(
        self,
        pdf_path: str,
        source_file: str,
        page_start: int = 1,
        page_end: Optional[int] = None
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Extract text/vector PDF tables using PyMuPDF's table detector."""
        try:
            try:
                import pymupdf
            except ImportError:
                import fitz as pymupdf
        except ImportError:
            logger.info("PyMuPDF not installed; skipping PyMuPDF table fallback.")
            return [], None

        transactions: List[Dict[str, Any]] = []
        raw_columns: Optional[List[str]] = None
        raw_rows: List[List[str]] = []
        doc = None
        try:
            doc = pymupdf.open(pdf_path)
            doc_total_pages = len(doc)
            start_page, end_page = self._resolve_page_window(doc_total_pages, page_start, page_end)
            strategies = ("lines", "lines_strict", "text")

            for actual_page_num in range(start_page, end_page + 1):
                page = doc[actual_page_num - 1]
                page_tables = []
                for strategy in strategies:
                    try:
                        finder = page.find_tables(strategy=strategy)
                    except Exception as exc:
                        logger.debug(
                            "PyMuPDF find_tables failed on page %d with strategy=%s: %s",
                            actual_page_num,
                            strategy,
                            exc,
                        )
                        continue
                    page_tables = list(getattr(finder, "tables", []) or [])
                    if page_tables:
                        logger.debug(
                            "PyMuPDF found %d tables on page %d using strategy=%s",
                            len(page_tables),
                            actual_page_num,
                            strategy,
                        )
                        break

                for table in page_tables:
                    try:
                        extracted = table.extract()
                    except Exception as exc:
                        logger.debug("PyMuPDF table extraction failed on page %d: %s", actual_page_num, exc)
                        continue
                    if not extracted or len(extracted) < 2:
                        continue

                    rows = [
                        [str(cell or "").strip() for cell in row]
                        for row in extracted
                        if row and any(str(cell or "").strip() for cell in row)
                    ]
                    if len(rows) < 2:
                        continue

                    transactions.extend(
                        self._transactions_from_table(
                            rows,
                            source_file=source_file,
                            page_ref=f"PyMuPDF_Page_{actual_page_num}"
                        )
                    )

                    columns = rows[0]
                    data_rows = rows[1:]
                    if raw_columns is None:
                        raw_columns = columns
                    for row in data_rows:
                        if raw_columns and len(row) < len(raw_columns):
                            row = row + [""] * (len(raw_columns) - len(row))
                        raw_rows.append(row[:len(raw_columns)] if raw_columns else row)

            raw_table = {"columns": raw_columns or [], "rows": raw_rows} if raw_rows else None
            if transactions:
                logger.info("PyMuPDF extracted %d normalized transactions", len(transactions))
            return transactions, raw_table
        except Exception as exc:
            logger.warning("PyMuPDF table fallback failed: %s", exc)
            return [], None
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    def _extract_layout_transactions(
        self,
        pdf_path: str,
        source_file: str,
        page_start: int = 1,
        page_end: Optional[int] = None
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Extract transactions using word coordinates and header positions."""
        import pdfplumber

        transactions: List[Dict[str, Any]] = []
        last_tx: Optional[Dict[str, Any]] = None
        raw_columns: Optional[List[str]] = None
        raw_rows: List[List[str]] = []
        raw_col_map: Optional[Dict[str, int]] = None

        last_header = None
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            start_page, end_page = self._resolve_page_window(total_pages, page_start, page_end)
            for actual_page_num in range(start_page, end_page + 1):
                page = pdf.pages[actual_page_num - 1]
                header = self._detect_header_layout(page)
                if header:
                    last_header = header
                elif last_header:
                    header = dict(last_header)
                    header["header_top"] = 0
                else:
                    continue
                if raw_columns is None:
                    raw_columns = self._normalize_header_labels([g["text"] for g in header["columns"]])
                    raw_col_map = header["col_map"]

                rows = self._extract_rows_from_page(page, header)
                for row in rows:
                    if self._is_separator_row(row):
                        continue
                    if raw_columns and self._is_header_row(row, raw_columns):
                        continue
                    raw_rows.append(row)

                    tx = self._row_to_transaction(
                        row=row,
                        col_map=header["col_map"],
                        source_file=source_file,
                        page_ref=f"Page_{actual_page_num}"
                    )
                    if tx:
                        transactions.append(tx)
                        last_tx = tx
                    else:
                        # Continuation line (reference/details)
                        if last_tx and any(cell.strip() for cell in row):
                            ref_idx = header["col_map"].get("reference")
                            desc_idx = header["col_map"].get("description")
                            ref_text = row[ref_idx].strip() if ref_idx is not None and ref_idx < len(row) else ""
                            desc_text = row[desc_idx].strip() if desc_idx is not None and desc_idx < len(row) else ""
                            extra = ref_text or desc_text or " ".join(c for c in row if c.strip())
                            if extra:
                                if self._looks_like_reference(extra):
                                    last_tx["Reference_Number"] = (last_tx.get("Reference_Number") or "") + extra
                                else:
                                    last_tx["Description"] = (last_tx.get("Description") or "") + " " + extra
        raw_table = None
        if raw_columns and raw_rows and raw_col_map:
            raw_rows = self._merge_continuation_rows(raw_rows, raw_col_map)
            raw_table = {"columns": raw_columns, "rows": raw_rows}

        return transactions, raw_table

    def _normalize_header_labels(self, headers: List[str]) -> List[str]:
        """Normalize header labels and de-duplicate."""
        normalized = []
        seen = {}
        for header in headers:
            label = " ".join(header.strip().split())
            if not label:
                label = "Column"
            if label in seen:
                seen[label] += 1
                label = f"{label}_{seen[label]}"
            else:
                seen[label] = 1
            normalized.append(label)
        return normalized

    def _is_separator_row(self, row: List[str]) -> bool:
        return all(cell and set(cell) <= {"-"} for cell in row if cell.strip()) and any(cell.strip() for cell in row)

    def _is_header_row(self, row: List[str], headers: List[str]) -> bool:
        joined = " ".join(cell.lower() for cell in row if cell).strip()
        if not joined:
            return False
        header_text = " ".join(h.lower() for h in headers)
        if all(token in joined for token in header_text.split()[:3]):
            return True
        if self._row_has_header_tokens(joined) and not self._looks_like_date(joined):
            return True
        return False

    def _merge_continuation_rows(
        self,
        rows: List[List[str]],
        col_map: Dict[str, int]
    ) -> List[List[str]]:
        merged: List[List[str]] = []
        last = None
        date_idx = col_map.get("date", 0)
        desc_idx = col_map.get("description")
        ref_idx = col_map.get("reference")

        for row in rows:
            date_val = row[date_idx].strip() if date_idx < len(row) else ""
            if date_val and self._looks_like_date(date_val):
                merged.append(row)
                last = row
                continue

            if last is None:
                merged.append(row)
                continue

            if ref_idx is not None and ref_idx < len(row):
                extra_ref = row[ref_idx].strip()
                if extra_ref:
                    last[ref_idx] = f"{last[ref_idx]} {extra_ref}".strip()
            if desc_idx is not None and desc_idx < len(row):
                extra_desc = row[desc_idx].strip()
                if extra_desc and not self._looks_like_reference(extra_desc):
                    last[desc_idx] = f"{last[desc_idx]} {extra_desc}".strip()

        return merged

    def _detect_header_layout(self, page) -> Optional[Dict[str, Any]]:
        """Detect header row and column boundaries using word positions."""
        words = page.extract_words()
        if not words:
            return None

        header_tokens = {
            "txn", "dt", "date", "value", "brn", "description",
            "reference", "debits", "credits", "balance",
            "s", "no", "transaction", "cheque", "number", "remarks",
            "withdrawal", "deposit", "amount", "inr"
        }
        if self.active_profile:
            header_tokens.update(self.active_profile.header_tokens())

        def normalize(text: str) -> str:
            cleaned = text.lower().replace("_", " ")
            cleaned = re.sub(r"[^a-z0-9\\s]", " ", cleaned)
            cleaned = re.sub(r"\\s+", " ", cleaned).strip()
            return cleaned

        def token_list(text: str) -> List[str]:
            return normalize(text).split()

        # Group potential header words by y position
        y_groups: Dict[float, List[Dict[str, Any]]] = {}
        for w in words:
            tokens = token_list(w["text"])
            if any(t in header_tokens for t in tokens):
                key = round(w["top"], 1)
                y_groups.setdefault(key, []).append(w)

        if not y_groups:
            return None

        header_top, header_words = max(y_groups.items(), key=lambda item: len(item[1]))
        if len(header_words) < 4:
            return None

        # Merge header tokens into column labels using known pairs
        header_words = sorted(header_words, key=lambda w: w["x0"])
        header_groups: List[Dict[str, Any]] = []
        merge_threshold = 25
        merge_pairs = {
            ("txn", "dt"),
            ("value", "dt"),
            ("value", "date"),
            ("s", "no"),
            ("transaction", "date"),
            ("cheque", "number"),
            ("transaction", "remarks"),
            ("withdrawal", "amount"),
            ("deposit", "amount"),
            ("balance", "inr"),
            ("closing", "balance"),
            ("opening", "balance"),
        }

        def norm_token(text: str) -> str:
            return normalize(text)

        for w in header_words:
            token = norm_token(w["text"])
            token_first = token.split()[0] if token else ""
            if not header_groups:
                header_groups.append({
                    "x0": w["x0"],
                    "x1": w["x1"],
                    "text": w["text"],
                    "token": token
                })
                continue

            prev = header_groups[-1]
            prev_token = prev["token"].split()[-1] if prev["token"] else ""
            pair = (prev_token, token_first)

            if pair in merge_pairs and w["x0"] - prev["x1"] <= merge_threshold:
                prev["x1"] = max(prev["x1"], w["x1"])
                prev["text"] = f"{prev['text']} {w['text']}"
                prev["token"] = f"{prev['token']} {token}".strip()
            else:
                header_groups.append({
                    "x0": w["x0"],
                    "x1": w["x1"],
                    "text": w["text"],
                    "token": token
                })

        # Merge second-line header words like "Amount(INR)"
        secondary_words = [
            w for w in words
            if 0 < (w["top"] - header_top) <= 14
            and any(t in header_tokens for t in token_list(w["text"]))
        ]
        for w in secondary_words:
            token = norm_token(w["text"])
            if not token:
                continue
            # Append to nearest header group
            target = min(header_groups, key=lambda g: abs(w["x0"] - g["x0"]))
            target["text"] = f"{target['text']} {w['text']}".strip()
            target["token"] = f"{target['token']} {token}".strip()

        # Build boundaries
        xs = [g["x0"] for g in header_groups]
        if len(xs) < 2:
            return None

        boundaries = [0.0]
        for i in range(1, len(xs)):
            boundaries.append((xs[i - 1] + xs[i]) / 2)
        boundaries.append(page.width + 1)

        col_map = self._map_headers([g["text"] for g in header_groups])
        if "date" not in col_map or "balance" not in col_map:
            return None

        return {
            "columns": header_groups,
            "boundaries": boundaries,
            "col_map": col_map,
            "header_top": header_top
        }

    def _row_has_header_tokens(self, text: str) -> bool:
        import re
        cleaned = text.lower().replace("_", " ")
        cleaned = re.sub(r"[^a-z0-9\\s]", " ", cleaned)
        cleaned = re.sub(r"\\s+", " ", cleaned).strip()
        tokens = cleaned.split()
        header_tokens = {
            "s", "no", "value", "date", "transaction", "cheque", "number",
            "remarks", "withdrawal", "deposit", "balance", "amount", "inr"
        }
        return sum(1 for t in tokens if t in header_tokens) >= 3

    def _extract_rows_from_page(self, page, header: Dict[str, Any]) -> List[List[str]]:
        """Extract rows below header using column boundaries."""
        words = page.extract_words()
        if not words:
            return []

        # Group words into rows by y position
        row_tolerance = 3
        rows: List[List[Dict[str, Any]]] = []
        current_row: List[Dict[str, Any]] = []
        current_top = None

        for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
            if w["top"] <= header["header_top"] + 2:
                continue
            if current_top is None or abs(w["top"] - current_top) <= row_tolerance:
                current_row.append(w)
                current_top = w["top"] if current_top is None else current_top
            else:
                rows.append(current_row)
                current_row = [w]
                current_top = w["top"]

        if current_row:
            rows.append(current_row)

        boundaries = header["boundaries"]
        table_rows: List[List[str]] = []

        for row_words in rows:
            cols = [""] * (len(boundaries) - 1)
            for w in row_words:
                idx = self._boundary_index(w["x0"], boundaries)
                if idx is None:
                    continue
                cols[idx] = f"{cols[idx]} {w['text']}".strip()
            table_rows.append(cols)

        return table_rows

    def _boundary_index(self, x0: float, boundaries: List[float]) -> Optional[int]:
        for i in range(len(boundaries) - 1):
            if boundaries[i] <= x0 < boundaries[i + 1]:
                return i
        return None

    def _looks_like_reference(self, text: str) -> bool:
        digits = sum(1 for c in text if c.isdigit())
        letters = sum(1 for c in text if c.isalpha())
        return digits >= 6 and digits >= letters

    def _transactions_quality_ok(self, transactions: List[Dict[str, Any]]) -> bool:
        """Heuristic quality check to decide if extraction is usable."""
        if not transactions:
            return False

        total = len(transactions)
        date_like_desc = 0
        missing_amount = 0
        suspicious_amount = 0

        for tx in transactions:
            desc = (tx.get("Description") or "").strip()
            if desc and self._looks_like_date(desc):
                date_like_desc += 1

            if not tx.get("Withdrawal_Amount") and not tx.get("Deposit_Amount") and not tx.get("Transaction_Amount"):
                missing_amount += 1

            balance = tx.get("Closing_Balance")
            amount = tx.get("Transaction_Amount")
            if balance and amount and amount > 1e7 and balance < (amount / 5):
                suspicious_amount += 1

        if date_like_desc / total > 0.3:
            return False
        if missing_amount / total > 0.3:
            return False
        if suspicious_amount / total > 0.2:
            return False

        return True

    def _looks_like_date(self, text: str) -> bool:
        """Check if text looks like a date in any common global format."""
        import re
        # DD/MM/YY(YY), MM/DD/YYYY, DD-MM-YYYY
        if re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', text):
            return True
        # YYYY-MM-DD, YYYY/MM/DD
        if re.search(r'\d{4}[/-]\d{2}[/-]\d{2}', text):
            return True
        # DD-Mon-YYYY, DD Mon YYYY, Mon DD YYYY
        months = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*'
        if re.search(r'\d{1,2}[\s-]' + months + r'[\s-]\d{2,4}', text, re.IGNORECASE):
            return True
        if re.search(months + r'\s+\d{1,2},?\s+\d{4}', text, re.IGNORECASE):
            return True
        return False

    def _is_probably_english_text(self, text: str) -> bool:
        """
        Heuristic language gate for English-only beta.
        Returns True when content is likely English/Latin-script dominant.
        """
        if not text:
            return True

        alpha_chars = [ch for ch in text if ch.isalpha()]
        if len(alpha_chars) < 20:
            # Not enough signal to confidently classify.
            return True

        latin_count = sum(1 for ch in alpha_chars if ('a' <= ch.lower() <= 'z'))
        non_latin_count = len(alpha_chars) - latin_count
        non_latin_ratio = non_latin_count / max(len(alpha_chars), 1)
        return non_latin_ratio <= 0.20

    def _enforce_english_only(self, text: str, context: str) -> None:
        """Raise when ENGLISH_ONLY_BETA is enabled and text looks non-English."""
        if not self.config.english_only_beta:
            return
        if not text:
            return
        if not self._is_probably_english_text(text):
            raise UnsupportedLanguageError(
                f"English-only beta: unsupported language/script detected in {context}."
            )

    def _build_row_confidence_summary(self) -> Dict[str, Any]:
        if not self._row_confidences:
            return {
                "row_confidence_avg": 0.0,
                "row_confidence_low_ratio_pct": 0.0,
                "row_confidence_low_count": 0,
            }

        low_threshold = self.config.row_confidence_threshold
        total = len(self._row_confidences)
        low_count = sum(1 for score in self._row_confidences if score < low_threshold)
        avg_score = sum(self._row_confidences) / total
        low_ratio_pct = (low_count / total) * 100.0
        return {
            "row_confidence_avg": round(avg_score, 3),
            "row_confidence_low_ratio_pct": round(low_ratio_pct, 1),
            "row_confidence_low_count": low_count,
        }

    def _low_confidence_ratio(self, transactions: List[Dict[str, Any]]) -> float:
        if not transactions:
            return 0.0
        low_threshold = self.config.row_confidence_threshold
        scored = [
            float(tx.get("Row_Confidence", 0.0))
            for tx in transactions
            if tx.get("Row_Confidence") is not None
        ]
        if not scored:
            return 0.0
        low_count = sum(1 for score in scored if score < low_threshold)
        return low_count / max(len(scored), 1)

    def _try_template_extraction(
        self,
        page_texts: Optional[List[Tuple[int, str]]],
        raw_table: Optional[Dict[str, Any]],
        source_file: str,
        pdf_path: str,
        page_start: int = 1,
        page_end: Optional[int] = None,
        first_pages_text: str = "",
    ) -> Optional[Tuple[int, List[Dict]]]:
        """
        Attempt template-based extraction:
        1. Build raw_table if not provided (from pdfplumber layout)
        2. Detect template (heuristic first, then LLM)
        3. Apply template to all rows using existing _row_to_transaction()
        Returns (tokens_used, transactions) or None on failure.
        """
        from .template_extractor import TemplateDetector

        # --- Obtain raw table data ---
        if raw_table is None:
            # Try to get table from pdfplumber on first page
            if not page_texts:
                return None
            try:
                import pdfplumber
                logger.debug("Starting pdfplumber open for template extraction")
                _t0 = time.monotonic()
                with pdfplumber.open(pdf_path) as pdf:
                    start_pg = max(0, page_start - 1)
                    end_pg = page_end if page_end else len(pdf.pages)
                    tables_found = []
                    for pg_idx in range(start_pg, min(end_pg, len(pdf.pages))):
                        page = pdf.pages[pg_idx]
                        for tbl in (page.extract_tables() or []):
                            if tbl and len(tbl) >= 2:
                                tables_found.append(tbl)
                        if tables_found:
                            break  # Only need first page with tables
                    if not tables_found:
                        return None
                    # Use the largest table from first page
                    best_table = max(tables_found, key=lambda t: len(t))
                    raw_table = {
                        "columns": best_table[0] if best_table else [],
                        "rows": best_table[1:] if len(best_table) > 1 else [],
                    }
                logger.debug("pdfplumber template extraction completed in %.2fs", time.monotonic() - _t0)
            except Exception:
                return None

        rows = raw_table.get("rows") or []
        columns = raw_table.get("columns") or []
        if not rows:
            return None

        # Normalize all cells
        all_rows = [
            [str(cell).strip() if cell is not None else "" for cell in row]
            for row in rows if row
        ]
        if columns:
            header_row = [str(c).strip() if c is not None else "" for c in columns]
        else:
            header_row = []

        # Pad to uniform width
        full_table = ([header_row] + all_rows) if header_row else all_rows
        if not full_table:
            return None
        max_cols = max(len(r) for r in full_table)
        full_table = [r + [""] * (max_cols - len(r)) for r in full_table]

        # Detect header row
        header_idx = self._find_header_row(full_table)
        if header_idx is not None:
            headers = full_table[header_idx]
            data_rows = full_table[header_idx + 1:]
        elif header_row:
            headers = full_table[0]
            data_rows = full_table[1:]
        else:
            headers = []
            data_rows = full_table

        if not data_rows:
            return None

        sample_rows = data_rows[:5]

        # If no clear headers, skip template (fall through to per-chunk LLM)
        if not headers or not any(h.strip() for h in headers):
            return None

        # --- Detect template ---
        detector = TemplateDetector()
        template = detector.detect(
            headers=headers,
            sample_rows=sample_rows,
            bank_profile=self.active_profile,
            first_pages_text=first_pages_text,
        )

        if template is None:
            return None

        if template.detection_confidence < self.config.template_confidence_threshold:
            logger.warning(
                "Template confidence %.2f < threshold %.2f; skipping template.",
                template.detection_confidence, self.config.template_confidence_threshold,
            )
            return None

        col_map = template.to_col_map()
        if "date" not in col_map:
            return None

        logger.info(
            "Template detected (%s): confidence=%.2f, cols=%s, tokens=%d",
            template.detection_method, template.detection_confidence,
            list(col_map.keys()), template.llm_tokens_used,
        )

        # --- Apply template to all rows ---
        transactions: List[Dict[str, Any]] = []
        for row in data_rows:
            # Pad short rows
            if len(row) < max_cols:
                row = row + [""] * (max_cols - len(row))
            if not any(cell.strip() for cell in row):
                continue
            # Skip repeated header rows
            if template.header_fingerprint:
                row_fp = " ".join(
                    self._normalize_header_text(c) for c in row
                )
                if row_fp == template.header_fingerprint:
                    continue
            tx = self._row_to_transaction(
                row=row,
                col_map=col_map,
                source_file=source_file,
                page_ref="Template",
            )
            if tx:
                transactions.append(tx)

        if transactions:
            logger.info(
                "Template extraction produced %d transactions from %d rows (%s, %d tokens)",
                len(transactions), len(data_rows), template.detection_method, template.llm_tokens_used,
            )

        return (template.llm_tokens_used, transactions)

    def _annotate_row_confidence(
        self,
        tx: Dict[str, Any],
        row: List[str],
        col_map: Dict[str, int]
    ) -> Dict[str, Any]:
        """Attach row/cell confidence without modifying extracted values."""
        def _cell(key: str) -> str:
            idx = col_map.get(key)
            if idx is None or idx >= len(row):
                return ""
            return (row[idx] or "").strip()

        date_val = _cell("date")
        desc_val = _cell("description")
        debit_val = _cell("debit")
        credit_val = _cell("credit")
        balance_val = _cell("balance")

        date_conf = 1.0 if date_val and self._looks_like_date(date_val) else 0.0
        desc_conf = 1.0 if len(desc_val) >= 3 else (0.4 if desc_val else 0.0)
        debit_conf = 1.0 if self.clean_amount_string(debit_val) is not None else (0.5 if not debit_val else 0.2)
        credit_conf = 1.0 if self.clean_amount_string(credit_val) is not None else (0.5 if not credit_val else 0.2)
        balance_conf = 1.0 if self.clean_amount_string(balance_val) is not None else 0.0

        amount_conf = max(debit_conf, credit_conf)
        row_conf = (
            0.35 * date_conf +
            0.20 * desc_conf +
            0.25 * amount_conf +
            0.20 * balance_conf
        )
        row_conf = round(row_conf, 3)

        reasons: List[str] = []
        if date_conf < 1.0:
            reasons.append("date_unparseable")
        if desc_conf < 0.8:
            reasons.append("description_weak")
        if amount_conf < 0.8:
            reasons.append("amount_unreliable")
        if balance_conf < 0.8:
            reasons.append("balance_missing_or_invalid")

        tx["Row_Confidence"] = row_conf
        tx["Confidence_Reasons"] = ";".join(reasons)
        tx["CellConf_Date"] = round(date_conf, 3)
        tx["CellConf_Description"] = round(desc_conf, 3)
        tx["CellConf_Amount"] = round(amount_conf, 3)
        tx["CellConf_Balance"] = round(balance_conf, 3)
        self._row_confidences.append(row_conf)
        return tx

    def _table_settings(self) -> Dict[str, Any]:
        """Table extraction settings for pdfplumber."""
        return {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "intersection_tolerance": 5,
            "snap_tolerance": 2,
            "join_tolerance": 2,
            "min_words_vertical": 3,
            "min_words_horizontal": 1,
        }

    def _get_int_env(self, name: str, default: int) -> int:
        """Read integer from environment with fallback."""
        try:
            return int(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default

    def _build_chunks(
        self,
        page_texts: List[Tuple[int, str]],
        chunk_mode: str,
        pages_per_chunk: int,
        target_chars: int,
        max_chars: int
    ) -> List[Tuple[int, str, str]]:
        """Build LLM chunks from page text."""
        if not page_texts:
            return []

        if max_chars < target_chars:
            max_chars = target_chars

        chunks: List[Tuple[int, str, str]] = []

        def finalize(chunk_idx: int, pages: List[Tuple[int, str]]):
            if not pages:
                return
            # Safely access page numbers
            try:
                first_page = pages[0][0]
                last_page = pages[-1][0]
                page_range = f"{first_page}-{last_page}"
            except (IndexError, TypeError):
                page_range = f"chunk_{chunk_idx}"
            
            chunk_text = "\n\n".join(
                f"--- Page {page_num} ---\n{text}"
                for page_num, text in pages
            )
            chunks.append((chunk_idx, page_range, chunk_text))

        if chunk_mode == "pages":
            total = len(page_texts)
            num_chunks = (total + pages_per_chunk - 1) // pages_per_chunk
            for chunk_idx in range(num_chunks):
                start_idx = chunk_idx * pages_per_chunk
                end_idx = min(start_idx + pages_per_chunk, total)
                finalize(chunk_idx, page_texts[start_idx:end_idx])
            return chunks

        chunk_idx = 0
        current_pages: List[Tuple[int, str]] = []
        current_chars = 0

        for page_num, text in page_texts:
            text_len = len(text)
            if current_pages and current_chars + text_len > max_chars:
                finalize(chunk_idx, current_pages)
                chunk_idx += 1
                current_pages = []
                current_chars = 0

            current_pages.append((page_num, text))
            current_chars += text_len

            if current_chars >= target_chars:
                finalize(chunk_idx, current_pages)
                chunk_idx += 1
                current_pages = []
                current_chars = 0

        if current_pages:
            finalize(chunk_idx, current_pages)

        return chunks

    def _transactions_from_table(
        self,
        table: List[List[Any]],
        source_file: str,
        page_ref: str
    ) -> List[Dict[str, Any]]:
        """Convert a table (list of rows) to standardized transactions."""
        if not table:
            return []

        # Clean rows and normalize column counts
        rows = [
            [str(cell).strip() if cell is not None else "" for cell in row]
            for row in table
            if row
        ]
        if not rows:
            return []

        max_cols = max(len(r) for r in rows)
        rows = [r + [""] * (max_cols - len(r)) for r in rows]

        header_idx = self._find_header_row(rows)
        if header_idx is not None:
            headers = rows[header_idx]
            data_rows = rows[header_idx + 1:]
            col_map = self._map_headers(headers)
        else:
            headers = []
            data_rows = rows
            col_map = self._infer_columns_from_rows(rows)

        transactions = []
        for row in data_rows:
            if not any(cell.strip() for cell in row):
                continue

            tx = self._row_to_transaction(
                row=row,
                col_map=col_map,
                source_file=source_file,
                page_ref=page_ref
            )
            if tx:
                transactions.append(tx)

        if not transactions:
            logger.debug(
                "Page %s: 0 transactions from %d data rows (header_idx=%s, col_map=%s)",
                page_ref, len(data_rows), header_idx, col_map,
            )

        return transactions

    def _find_header_row(self, rows: List[List[str]]) -> Optional[int]:
        """Find a probable header row by keyword matching."""
        import re

        header_keywords = [
            "date", "txn", "value", "description", "narration", "particular",
            "reference", "ref", "cheque", "debit", "credit", "withdraw", "deposit",
            "balance", "amount"
        ]
        if self.active_profile:
            header_keywords.extend(self.active_profile.header_tokens())
        header_keywords = sorted(set(header_keywords))

        strong_keywords = {
            "description", "narration", "particular", "reference",
            "debit", "credit", "withdraw", "deposit", "balance", "amount"
        }

        for idx, row in enumerate(rows[:3]):
            joined = " ".join(self._normalize_header_text(cell) for cell in row if cell).strip()
            if not joined:
                continue

            matches = {k for k in header_keywords if k in joined}
            has_strong = any(k in joined for k in strong_keywords)
            looks_data_like = bool(
                re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', joined)
                or re.search(r'\d{1,3}(?:,\d{2,3})*(?:\.\d{2})', joined)
            )

            if len(matches) >= 2:
                return idx
            if has_strong and not looks_data_like:
                return idx
        return None

    def _map_headers(self, headers: List[str]) -> Dict[str, int]:
        """Map header names to standard fields."""
        self._maybe_detect_profile(headers=headers)

        mapping: Dict[str, int] = {}
        normalized_headers = [self._normalize_header_text(h) for h in headers]
        profile_aliases = get_profile_header_aliases(self.active_profile)

        # Profile-first mapping for known bank formats.
        for idx, h in enumerate(normalized_headers):
            if not h:
                continue
            for field, aliases in profile_aliases.items():
                if field in mapping:
                    continue
                for alias in aliases:
                    alias_n = self._normalize_header_text(alias)
                    if alias_n and alias_n in h:
                        mapping[field] = idx
                        break
                if field in mapping:
                    break

        for idx, header in enumerate(headers):
            h = self._normalize_header_text(header)
            if not h:
                continue

            tokens = set(h.split())

            if 'date' in h or 'txn' in h or 'value' in h:
                mapping.setdefault('date', idx)
            elif any(x in h for x in ['desc', 'narr', 'partic', 'remark', 'details']):
                mapping.setdefault('description', idx)
            elif any(x in h for x in ['ref', 'cheq', 'chq', 'utr', 'instrument']):
                mapping.setdefault('reference', idx)
            elif 'debit' in h or 'withdraw' in h or 'dr' in tokens:
                mapping.setdefault('debit', idx)
            elif 'credit' in h or 'deposit' in h or 'cr' in tokens:
                mapping.setdefault('credit', idx)
            elif 'balance' in h or 'bal' in h:
                mapping.setdefault('balance', idx)
        return mapping

    def _infer_columns_from_rows(self, rows: List[List[str]]) -> Dict[str, int]:
        """Infer column roles when no header is available."""
        import re

        date_re = re.compile(
            r'\d{4}[/-]\d{2}[/-]\d{2}'
            r'|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}'
        )
        exact_date_re = re.compile(
            r'^(?:\d{4}[/-]\d{2}[/-]\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})$'
        )
        # Require either decimal precision or grouped separators to avoid
        # misclassifying long reference numbers as amount columns.
        amount_re = re.compile(
            r'^-?(?:\d{1,3}(?:,\d{2,3})+\.\d{2}|\d+\.\d{2}|\d{1,3}(?:,\d{2,3})+)$'
        )
        reference_re = re.compile(r'^(?:\d{10,18}|[A-Z0-9]{10,24})$', re.IGNORECASE)

        data_like_rows = []
        for row in rows:
            has_date = any(date_re.search((cell or "").replace(" ", "")) for cell in row if cell)
            has_amount = any(amount_re.match((cell or "").replace(" ", "")) for cell in row if cell)
            if has_date or has_amount:
                data_like_rows.append(row)
            if len(data_like_rows) >= 8:
                break

        sample_rows = data_like_rows[:5] if data_like_rows else rows[:5]
        col_count = max(len(r) for r in sample_rows)
        date_scores = [0] * col_count
        exact_date_scores = [0] * col_count
        mixed_date_scores = [0] * col_count
        amount_scores = [0] * col_count
        reference_scores = [0] * col_count

        for row in sample_rows:
            for idx, cell in enumerate(row):
                cell_text = (cell or "").strip()
                cell_clean = cell_text.replace(" ", "")
                if date_re.search(cell_clean):
                    date_scores[idx] += 1
                    if exact_date_re.match(cell_clean):
                        exact_date_scores[idx] += 1
                    elif len(cell_clean) > 10:
                        mixed_date_scores[idx] += 1
                if amount_re.match(cell_clean):
                    amount_scores[idx] += 1
                if reference_re.match(cell_clean):
                    reference_scores[idx] += 1

        mapping: Dict[str, int] = {}
        if max(exact_date_scores) > 0:
            mapping['date'] = int(exact_date_scores.index(max(exact_date_scores)))
        elif max(date_scores) > 0:
            mapping['date'] = int(date_scores.index(max(date_scores)))

        # Identify amount columns (sorted by frequency, descending)
        amount_cols = sorted(
            range(col_count),
            key=lambda i: amount_scores[i],
            reverse=True
        )
        amount_cols = [i for i in amount_cols if amount_scores[i] > 0]

        if amount_cols:
            # Balance = most frequently filled amount column
            # (balance is present on every transaction row)
            mapping['balance'] = amount_cols[0]
            balance_col = amount_cols[0]

            # For remaining amount columns, use POSITION (left-to-right), not frequency.
            # Convention for Indian bank statements: Debit | Credit | Balance
            # So: leftmost remaining = debit, rightmost remaining = credit
            remaining = sorted(c for c in amount_cols[1:] if c != balance_col)

            if len(remaining) == 1:
                # Single non-balance amount column
                mapping['credit'] = remaining[0]
            elif len(remaining) >= 2:
                mapping['debit'] = remaining[0]
                mapping['credit'] = remaining[-1]

        if max(reference_scores) > 0:
            mapping['reference'] = int(reference_scores.index(max(reference_scores)))

        # Description: first non-date, non-amount column
        mixed_candidates = [
            i for i, score in enumerate(mixed_date_scores)
            if score > 0 and i != mapping.get('date')
        ]
        if mixed_candidates:
            mapping['description'] = max(mixed_candidates, key=lambda i: mixed_date_scores[i])
        else:
            for idx in range(col_count):
                if idx == mapping.get('date'):
                    continue
                if idx in [mapping.get('debit'), mapping.get('credit'), mapping.get('balance')]:
                    continue
                if idx == mapping.get('reference'):
                    continue
                mapping.setdefault('description', idx)
                break

        return mapping

    def _row_to_transaction(
        self,
        row: List[str],
        col_map: Dict[str, int],
        source_file: str,
        page_ref: str
    ) -> Optional[Dict[str, Any]]:
        """Convert a table row into a transaction dict."""
        import re

        def get_cell(key: str) -> str:
            idx = col_map.get(key)
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        date_val = get_cell('date')
        description = get_cell('description')
        reference = get_cell('reference')

        extracted_date, remaining = self.extract_date_token(date_val, anchored=True)
        if not extracted_date:
            return None

        date_val = extracted_date
        if remaining and (not description or self._looks_like_date(description) or self._looks_like_reference(description)):
            description = remaining
        if description:
            description = re.sub(
                r'^\s*' + re.escape(date_val) + r'\b[\s\-|:]*',
                '',
                description,
                flags=re.IGNORECASE,
            ).strip(" -|")

        withdrawal = self.positive_amount(self.clean_amount_string(get_cell('debit')))
        deposit = self.positive_amount(self.clean_amount_string(get_cell('credit')))
        balance = self.clean_amount_string(get_cell('balance'))

        def amount_tokens(cell_text: str) -> List[float]:
            text = (cell_text or "").strip()
            if not text:
                return []
            # Prefer decimal/grouped amounts to avoid treating long references as money.
            raw = re.findall(
                r'\(?-?(?:[₹$£€]\s*)?(?:\d{1,3}(?:[,\s]\d{2,3})+|\d+)(?:[.,]\d{1,2})\s*(?:CR|DR)?\)?'
                r'|\(?-?(?:[₹$£€]\s*)?\d{1,3}(?:[,\s]\d{2,3})+\s*(?:CR|DR)?\)?',
                text,
                flags=re.IGNORECASE,
            )
            vals: List[float] = []
            for tok in raw:
                amt = self.clean_amount_string(tok)
                if amt is None:
                    continue
                vals.append(amt)
            return vals

        # Common scanned-table pattern: debit and credit in a single merged cell
        # like "6,000.00 0.00". Parse this before generic fallbacks.
        merged_amount_idx: Optional[int] = None
        if withdrawal is None and deposit is None:
            merged_amount_idx = col_map.get('debit')
            if merged_amount_idx is None:
                merged_amount_idx = col_map.get('credit')
            if merged_amount_idx is None:
                merged_amount_idx = col_map.get('amount')

            merged_amount_col = ""
            if merged_amount_idx is not None and merged_amount_idx < len(row):
                merged_amount_col = str(row[merged_amount_idx] or "")

            if not merged_amount_col:
                for idx_cell, cell in enumerate(row):
                    raw_count = len(
                        re.findall(r'-?\d{1,3}(?:,\d{2,3})*(?:\.\d{2})', str(cell or ""))
                    )
                    if raw_count >= 2:
                        merged_amount_col = str(cell or "")
                        merged_amount_idx = idx_cell
                        break

            pair = amount_tokens(merged_amount_col)
            if len(pair) >= 2:
                a, b = pair[0], pair[1]
                if a and b:
                    withdrawal, deposit = abs(a), abs(b)
                elif a and not b:
                    withdrawal = abs(a)
                elif b and not a:
                    deposit = abs(b)
            elif len(pair) == 1:
                desc_lower = description.lower()
                if any(w in desc_lower for w in ['cr', 'credit', 'deposit']):
                    deposit = abs(pair[0])
                else:
                    withdrawal = abs(pair[0])

            # In some scanned bordered tables, balance is shifted to the next column
            # while the value-date + debit/credit pair is merged in one column.
            if balance is None and merged_amount_idx is not None:
                for idx_cell in range(merged_amount_idx + 1, len(row)):
                    vals = amount_tokens(str(row[idx_cell] or ""))
                    if vals:
                        balance = vals[-1]
                        break

            if balance is None:
                all_row_amounts: List[float] = []
                for cell in row:
                    all_row_amounts.extend(amount_tokens(cell))
                if len(all_row_amounts) >= 3:
                    balance = all_row_amounts[-1]

        if withdrawal is None and deposit is None and balance is None:
            # Attempt fallback from any numeric cells
            amounts: List[float] = []
            for cell in row:
                amounts.extend(amount_tokens(cell))

            if len(amounts) >= 3:
                withdrawal, deposit, balance = abs(amounts[0]), abs(amounts[1]), amounts[-1]
            elif len(amounts) == 2:
                desc_lower = description.lower()
                if any(w in desc_lower for w in ['cr', 'credit', 'deposit']):
                    deposit = abs(amounts[0])
                else:
                    withdrawal = abs(amounts[0])
                balance = amounts[1]
            elif len(amounts) == 1:
                balance = amounts[0]

        tx = self.create_transaction_dict(
            date=date_val,
            description=description,
            reference=reference,
            withdrawal_amt=withdrawal,
            deposit_amt=deposit,
            balance_amt=balance,
            source_file=source_file,
            line_ref=page_ref
        )
        return self._annotate_row_confidence(tx, row, col_map)
    
    def _process_image_based(
        self, 
        pdf_path: str, 
        source_file: str,
        total_pages: int,
        page_start: int = 1,
        page_end: Optional[int] = None
    ) -> int:
        """
        Process image-based PDF with enhanced OCR pipeline.
        
        Priority order:
        1. img2table extraction (OpenCV + RLSA table detection, PaddleOCR for text)
        2. Legacy spatial extraction fallback (our custom OpenCV code)
        3. Full-text OCR + regex fallback
        
        Returns total tokens used by LLM (0 if no LLM was used).
        """
        total_tokens = 0

        if self.config.use_img2table:
            # ===== Priority 1: img2table extraction (primary) =====
            # img2table handles both table DETECTION and OCR in one pass.
            # It processes the entire PDF at once, detecting tables across all pages.
            img2table_result = self._try_img2table_extraction(
                pdf_path,
                total_pages,
                page_start=page_start,
                page_end=page_end
            )

            if img2table_result:
                raw_table = img2table_result.get("raw_table")
                if raw_table and raw_table.get("rows") and len(raw_table["rows"]) >= 2:
                    num_cols = len(raw_table.get("columns", []))
                    num_rows = len(raw_table["rows"])
                    min_transactions = max(1, self.config.min_table_transactions)
                    if num_cols >= 3:
                        self.raw_table = raw_table
                        derived = self._derive_transactions_from_raw_table(
                            source_file=source_file,
                            page_ref="Image_Table"
                        )
                        logger.info("img2table extraction: %d rows, %d columns", num_rows, num_cols)
                        if derived:
                            logger.info("Derived %d normalized transactions from img2table output", derived)
                        if derived >= min_transactions:
                            return 0
                        logger.warning(
                            "img2table produced only %d normalized transactions; trying fallbacks",
                            derived
                        )
                    else:
                        logger.warning("img2table found only %d columns; trying fallbacks", num_cols)

            # Try template on img2table raw_table that had low column count
            if self.config.use_template and img2table_result:
                raw_table = img2table_result.get("raw_table")
                if raw_table and raw_table.get("rows"):
                    template_result = self._try_template_extraction(
                        page_texts=None,
                        raw_table=raw_table,
                        source_file=source_file,
                        pdf_path=pdf_path,
                        first_pages_text=getattr(self, "_first_pages_text", ""),
                    )
                    if template_result is not None:
                        _, template_txns = template_result
                        if template_txns and self._transactions_quality_ok(template_txns):
                            self.transactions.extend(template_txns)
                            return 0
        else:
            logger.info("img2table disabled via USE_IMG2TABLE=false; using spatial/OCR fallback pipeline")

        # ===== Priority 2: Legacy spatial extraction fallback =====
        # Falls back to our custom OpenCV spatial extraction + per-page OCR
        logger.info("img2table did not produce usable results; trying legacy spatial extraction...")
        legacy_result = self._try_legacy_spatial_extraction(
            pdf_path,
            source_file,
            total_pages,
            page_start=page_start,
            page_end=page_end
        )
        if legacy_result is not None:
            return legacy_result  # 0 tokens used, raw_table is set

        # ===== Priority 3: Full-text OCR + regex fallback =====
        # If both img2table and spatial failed, do a full OCR pass and try regex
        logger.info("Spatial extraction failed; collecting OCR text for regex fallback...")
        total_tokens = self._try_ocr_text_fallback(
            pdf_path,
            source_file,
            total_pages,
            page_start=page_start,
            page_end=page_end
        )

        gc.collect()
        return total_tokens

    def _try_img2table_extraction(
        self,
        pdf_path: str,
        total_pages: int,
        page_start: int = 1,
        page_end: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Attempt table extraction using img2table library.
        Returns the result dict from Img2TableExtractor, or None on failure.
        """
        try:
            if self.config.use_paddleocr and not self.paddle_processor:
                logger.warning("Skipping img2table: PaddleOCR backend unavailable in runtime")
                return None

            from .img2table_extractor import Img2TableExtractor
            start_page, end_page = self._resolve_page_window(total_pages, page_start, page_end)
            selected_pages = end_page - start_page + 1
            page_indexes = list(range(start_page - 1, end_page))

            self.emit_progress(
                1, selected_pages,
                f"Detecting tables with img2table ({selected_pages} pages)...",
                stage="ocr"
            )

            extractor = Img2TableExtractor(
                dpi=self.config.dpi,
                use_paddleocr=self.config.use_paddleocr,
            )

            logger.debug("Starting img2table extract_tables_from_pdf")
            _t0 = time.monotonic()
            result = extractor.extract_tables_from_pdf(
                pdf_path=pdf_path,
                pages=page_indexes,
                implicit_rows=True,
                implicit_columns=False,
                borderless_tables=True,
                min_confidence=30,
            )
            logger.debug("img2table extract_tables_from_pdf completed in %.2fs", time.monotonic() - _t0)

            table_count = len(result.get("tables", []))
            raw = result.get("raw_table")
            row_count = len(raw["rows"]) if raw and raw.get("rows") else 0
            logger.info("img2table found %d tables, %d rows total", table_count, row_count)

            col_count = len(raw.get("columns", [])) if raw else 0
            if row_count == 0 or col_count < 3:
                logger.info(
                    "img2table found %d columns; retrying with implicit column detection",
                    col_count
                )
                retry = extractor.extract_tables_from_pdf(
                    pdf_path=pdf_path,
                    pages=page_indexes,
                    implicit_rows=True,
                    implicit_columns=True,
                    borderless_tables=True,
                    min_confidence=30,
                )
                retry_raw = retry.get("raw_table")
                retry_rows = len(retry_raw["rows"]) if retry_raw and retry_raw.get("rows") else 0
                retry_cols = len(retry_raw.get("columns", [])) if retry_raw else 0
                if retry_rows >= row_count and retry_cols > col_count:
                    logger.info(
                        "img2table implicit-column retry improved shape: %d rows, %d columns",
                        retry_rows,
                        retry_cols
                    )
                    result = retry
                    raw = retry_raw
                    row_count = retry_rows

            self.emit_progress(
                selected_pages, selected_pages,
                f"img2table extracted {row_count} rows",
                stage="ocr"
            )

            return result

        except ImportError:
            logger.warning("img2table not installed; skipping")
            return None
        except Exception as e:
            logger.error("img2table extraction failed: %s", e)
            return None

    def _try_legacy_spatial_extraction(
        self,
        pdf_path: str,
        source_file: str,
        total_pages: int,
        page_start: int = 1,
        page_end: Optional[int] = None
    ) -> Optional[int]:
        """
        Legacy spatial extraction using our custom OpenCV grid detection + PaddleOCR.
        Returns 0 (tokens used) if successful, or None if it failed.
        """
        from pdf2image import convert_from_path

        all_spatial_rows: List[List[str]] = []
        spatial_header: Optional[List[str]] = None
        spatial_num_cols: Optional[int] = None
        _prev_col_xs: Optional[List[int]] = None
        start_page, end_page = self._resolve_page_window(total_pages, page_start, page_end)
        selected_pages = end_page - start_page + 1

        for local_page_num, actual_page_num in enumerate(range(start_page, end_page + 1), 1):
            try:
                self.emit_progress(
                    local_page_num, selected_pages,
                    f"Spatial extraction page {actual_page_num} ({local_page_num}/{selected_pages})",
                    stage="ocr"
                )

                logger.debug("Starting convert_from_path for spatial page %d", actual_page_num)
                _t0 = time.monotonic()
                images = convert_from_path(
                    pdf_path,
                    dpi=self.config.dpi,
                    first_page=actual_page_num,
                    last_page=actual_page_num
                )
                logger.debug("convert_from_path spatial page %d completed in %.2fs", actual_page_num, time.monotonic() - _t0)
                if not images:
                    continue

                page_image = images[0]

                try:
                    spatial_rows = self._extract_table_from_image(
                        page_image, prev_col_xs=_prev_col_xs
                    )
                except Exception as e:
                    logger.warning("Spatial extraction failed on page %d: %s", actual_page_num, e)
                    spatial_rows = None

                if spatial_rows:
                    if hasattr(self, '_last_col_xs') and self._last_col_xs:
                        _prev_col_xs = self._last_col_xs

                    if spatial_header is None and all_spatial_rows == []:
                        header, data_rows = self._detect_image_table_header(spatial_rows)
                        if header:
                            spatial_header = header
                            spatial_rows = data_rows
                        if spatial_rows:
                            spatial_num_cols = len(spatial_rows[0])
                    else:
                        if spatial_header:
                            header_check, data_rows = self._detect_image_table_header(spatial_rows)
                            if header_check:
                                spatial_rows = data_rows

                    if spatial_num_cols is not None:
                        for row in spatial_rows:
                            while len(row) < spatial_num_cols:
                                row.append('')
                            if len(row) > spatial_num_cols:
                                row[:] = row[:spatial_num_cols]

                    all_spatial_rows.extend(spatial_rows)

                del images
                if local_page_num % 3 == 0:
                    gc.collect()

            except Exception as e:
                logger.error("Legacy spatial error on page %d: %s", actual_page_num, e)
                continue

        # Check if spatial extraction produced usable results
        if all_spatial_rows and len(all_spatial_rows) >= 2:
            num_cols = len(all_spatial_rows[0]) if all_spatial_rows else 0
            if num_cols >= 3:
                col_fill = [0] * num_cols
                sample = all_spatial_rows[:min(20, len(all_spatial_rows))]
                for row in sample:
                    for ci, cell in enumerate(row):
                        if cell.strip():
                            col_fill[ci] += 1
                cols_with_data = sum(1 for f in col_fill if f >= len(sample) * 0.2)
                if cols_with_data >= 3:
                    if self._looks_like_blobbed_rows(all_spatial_rows):
                        logger.warning("Legacy spatial output appears blobbed; rejecting and continuing fallback.")
                        return None
                    columns = spatial_header if spatial_header else [
                        f"Column_{i+1}" for i in range(num_cols)
                    ]
                    for row in all_spatial_rows:
                        while len(row) < num_cols:
                            row.append('')
                        if len(row) > num_cols:
                            row[:] = row[:num_cols]
                    self.raw_table = {"columns": columns, "rows": all_spatial_rows}
                    derived = self._derive_transactions_from_raw_table(
                        source_file=source_file,
                        page_ref="Spatial_Table"
                    )
                    logger.info("Legacy spatial: %d rows, %d columns", len(all_spatial_rows), num_cols)
                    if derived:
                        logger.info("Derived %d normalized transactions from spatial table", derived)
                    return 0

        return None  # Signal failure

    def _try_ocr_text_fallback(
        self,
        pdf_path: str,
        source_file: str,
        total_pages: int,
        page_start: int = 1,
        page_end: Optional[int] = None
    ) -> int:
        """
        Full-text OCR fallback: OCR every page and try regex-based parsing.
        Also supports LLM extraction if enabled.
        Returns total tokens used.
        """
        from pdf2image import convert_from_path

        total_tokens = 0
        page_texts = []
        page_images = []
        total_ocr_chars = 0
        start_page, end_page = self._resolve_page_window(total_pages, page_start, page_end)
        selected_pages = end_page - start_page + 1

        for local_page_num, actual_page_num in enumerate(range(start_page, end_page + 1), 1):
            try:
                self.emit_progress(
                    local_page_num, selected_pages,
                    f"OCR fallback page {actual_page_num} ({local_page_num}/{selected_pages})",
                    stage="ocr"
                )

                logger.debug("Starting convert_from_path for OCR page %d", actual_page_num)
                _t0 = time.monotonic()
                images = convert_from_path(
                    pdf_path,
                    dpi=self.config.dpi,
                    first_page=actual_page_num,
                    last_page=actual_page_num
                )
                logger.debug("convert_from_path OCR page %d completed in %.2fs", actual_page_num, time.monotonic() - _t0)
                if not images:
                    continue

                page_image = images[0]
                raw_image = page_image

                # Preprocess if enabled
                if self.config.preprocess_images:
                    try:
                        from .image_preprocessor import preprocess_for_ocr
                        page_image = preprocess_for_ocr(page_image)
                    except Exception:
                        pass

                # Run OCR
                page_text = ""
                if self.paddle_processor:
                    try:
                        page_text = self.paddle_processor.process_image_to_text(page_image)
                    except Exception as exc:
                        logger.warning("PaddleOCR failed: %s", exc)
                        self.mark_paddle_unavailable()
                        try:
                            import pytesseract
                            page_text = pytesseract.image_to_string(page_image, config='--psm 6')
                        except Exception:
                            page_text = ""
                else:
                    try:
                        import pytesseract
                        page_text = pytesseract.image_to_string(page_image, config='--psm 6')
                    except Exception:
                        page_text = ""

                if page_text.strip():
                    page_texts.append((actual_page_num, page_text))
                    total_ocr_chars += len(page_text)

                if self.config.prefer_vision:
                    page_images.append(page_image)

                del images
                if local_page_num % 3 == 0:
                    gc.collect()

            except Exception as e:
                logger.error("OCR fallback error on page %d: %s", actual_page_num, e)
                continue

        logger.info("OCR fallback extracted %s characters from %d pages", f"{total_ocr_chars:,}", len(page_texts))
        if page_texts:
            sample_text = "\n".join(text for _, text in page_texts[:3])
            self._enforce_english_only(sample_text, "OCR fallback sample")

        # Try LLM extraction if available
        if self.llm_extractor and page_texts:
            if self.config.prefer_vision and page_images:
                for i, img in enumerate(page_images):
                    result = self.llm_extractor.extract_from_image(img)
                    if result.success:
                        self.transactions.extend(result.transactions)
                        total_tokens += result.tokens_used
                        for tx in self.transactions[-len(result.transactions):]:
                            tx['Source_File'] = source_file
                            tx['Page_Line'] = f"Page_{i+1}"
            else:
                first_page_text = ""
                for _, text in page_texts:
                    if text and text.strip():
                        first_page_text = text
                        break

                if first_page_text:
                    column_mapping = self.llm_extractor.detect_columns(first_page_text)
                    chunk_mode = os.environ.get("LLM_OCR_CHUNKING_MODE", "adaptive").strip().lower()
                    pages_per_chunk = self._get_int_env("LLM_OCR_PAGES_PER_CHUNK", 5)
                    target_chars = self._get_int_env("LLM_OCR_TARGET_CHARS", 8000)
                    max_chars = self._get_int_env("LLM_OCR_MAX_CHARS", 12000)
                    chunks = self._build_chunks(
                        page_texts=page_texts,
                        chunk_mode=chunk_mode,
                        pages_per_chunk=pages_per_chunk,
                        target_chars=target_chars,
                        max_chars=max_chars
                    )
                    for chunk_idx, page_range, chunk_text in chunks:
                        result = self.llm_extractor.extract_from_text(chunk_text, column_mapping)
                        if result.success:
                            for tx in result.transactions:
                                tx['Source_File'] = source_file
                                tx['Page_Line'] = f"Pages_{page_range}"
                                self.transactions.append(tx)
                            total_tokens += result.tokens_used
        else:
            # Regex fallback
            if page_texts:
                all_ocr_text = "\n\n".join(
                    f"--- Page {page_num} ---\n{text}"
                    for page_num, text in page_texts
                )
                self._fallback_regex_parse(all_ocr_text, source_file)

        page_images.clear()
        gc.collect()
        return total_tokens

    def _parse_table_html(self, html: str) -> List[List[str]]:
        """Parse HTML table into list of rows."""
        import re

        rows = []
        if not html:
            return rows

        for tr in re.findall(r'<tr[^>]*>.*?</tr>', html, flags=re.IGNORECASE | re.DOTALL):
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, flags=re.IGNORECASE | re.DOTALL)
            cleaned = []
            for cell in cells:
                text = re.sub(r'<[^>]+>', ' ', cell)
                text = re.sub(r'&nbsp;|&#160;', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                cleaned.append(text)
            if cleaned:
                rows.append(cleaned)
        return rows

    def _dedupe_transactions(self, transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate transactions based on key fields."""
        seen = set()
        unique = []
        for tx in transactions:
            key = (
                tx.get('Date'),
                tx.get('Description'),
                tx.get('Withdrawal_Amount'),
                tx.get('Deposit_Amount'),
                tx.get('Closing_Balance')
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(tx)
        return unique

    # ------------------------------------------------------------------ #
    #  Spatial table extraction from images (OpenCV + Tesseract)          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _cluster_values(values: List[int], threshold: int = 20) -> List[int]:
        """Cluster nearby integer values and return their means."""
        if not values:
            return []
        sorted_vals = sorted(values)
        clusters: List[List[int]] = [[sorted_vals[0]]]
        for v in sorted_vals[1:]:
            if v - clusters[-1][-1] <= threshold:
                clusters[-1].append(v)
            else:
                clusters.append([v])
        return [int(sum(c) / len(c)) for c in clusters]

    def _detect_table_grid(self, gray_img) -> Tuple[List[int], List[int]]:
        """
        Detect horizontal and vertical table grid lines using OpenCV.
        Returns (row_ys, col_xs) – clustered line positions.
        Uses adaptive threshold and contour-size filtering to handle
        varying image contrast and DPI.
        """
        import cv2
        import numpy as np

        h, w = gray_img.shape[:2]
        # Use small kernels to detect line candidates, then filter by size
        h_kernel_w = max(30, min(w // 20, 60))
        v_kernel_h = max(25, min(h // 20, 60))
        min_h_line_width = w * 0.15   # horizontal line must span 15% of page
        min_v_line_height = h * 0.10  # vertical line must span 10% of page

        best_h_ys: List[int] = []
        best_v_xs: List[int] = []

        for thresh_val in (120, 140, 160, 180):
            _, binary = cv2.threshold(gray_img, thresh_val, 255, cv2.THRESH_BINARY_INV)

            # Horizontal lines
            h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_kernel_w, 2))
            h_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
            h_contours, _ = cv2.findContours(h_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            # Filter: only keep lines that actually span a significant width
            good_h = [c for c in h_contours if cv2.boundingRect(c)[2] >= min_h_line_width]
            h_ys = self._cluster_values(
                [cv2.boundingRect(c)[1] for c in good_h], threshold=10
            )

            # Vertical lines
            v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, v_kernel_h))
            v_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
            v_contours, _ = cv2.findContours(v_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            good_v = [c for c in v_contours if cv2.boundingRect(c)[3] >= min_v_line_height]
            v_cluster_threshold = max(15, min(w // 80, 30))
            v_xs = self._cluster_values(
                [cv2.boundingRect(c)[0] for c in good_v], threshold=v_cluster_threshold
            )

            # Keep the result with the most horizontal lines (best contrast)
            if len(h_ys) > len(best_h_ys):
                best_h_ys = h_ys
                best_v_xs = v_xs

        return best_h_ys, best_v_xs

    @staticmethod
    def _select_dense_row_band(row_ys: List[int]) -> List[int]:
        """
        Pick the densest horizontal-line run (transaction table band) from all
        detected horizontal lines on the page.
        """
        if len(row_ys) < 6:
            return row_ys

        ys = sorted(set(row_ys))
        runs: List[List[int]] = []
        current = [ys[0]]
        for y in ys[1:]:
            # Statement rows are usually tightly packed; large jumps often
            # separate header/account blocks from the transaction table.
            if y - current[-1] <= 80:
                current.append(y)
            else:
                runs.append(current)
                current = [y]
        runs.append(current)

        best = max(runs, key=len) if runs else ys
        # Avoid over-filtering on weak detections.
        return best if len(best) >= 6 else ys

    @staticmethod
    def _remove_table_lines(gray_img):
        """
        Remove horizontal and vertical table lines from a grayscale image.
        Returns a cleaned image that yields better OCR results.
        """
        import cv2
        import numpy as np

        h, w = gray_img.shape[:2]
        _, binary = cv2.threshold(gray_img, 150, 255, cv2.THRESH_BINARY_INV)

        # Detect and remove horizontal lines (use small kernel, filter by width)
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, min(w // 10, 100)), 2))
        h_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)

        # Detect and remove vertical lines
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, max(30, min(h // 15, 80))))
        v_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)

        # Combine line masks
        line_mask = cv2.bitwise_or(h_mask, v_mask)

        # Dilate slightly to catch edges of lines
        dilate_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        line_mask = cv2.dilate(line_mask, dilate_kernel, iterations=1)

        # Fill lines with white in the original image
        cleaned = gray_img.copy()
        cleaned[line_mask > 0] = 255
        return cleaned

    def _get_ocr_words(self, img_pil, psm: int = 11, min_conf: int = 15):
        """
        Get word-level bounding boxes from OCR.
        Uses PaddleOCR when available (much better accuracy), falls back to Tesseract.
        Returns a list of dicts with keys: left, top, width, height, conf, text.
        """
        # Try PaddleOCR first – it gives much better results on scanned documents
        if self.paddle_processor:
            try:
                results = self.paddle_processor.process_image(img_pil)
                if results:
                    words = []
                    for item in results:
                        bbox = item['bbox']  # (x_min, y_min, x_max, y_max)
                        text = item['text'].strip()
                        if not text:
                            continue
                        conf = item.get('confidence', 0.0) * 100  # Normalize to 0-100
                        if conf < min_conf:
                            continue
                        words.append({
                            'left': int(bbox[0]),
                            'top': int(bbox[1]),
                            'width': int(bbox[2] - bbox[0]),
                            'height': int(bbox[3] - bbox[1]),
                            'conf': conf,
                            'text': text,
                        })
                    if words:
                        return words
            except Exception as e:
                logger.debug("PaddleOCR word detection failed, falling back to Tesseract: %s", e)
                self.mark_paddle_unavailable()

        # Fallback to Tesseract
        try:
            import pytesseract
            from pytesseract import Output

            data = pytesseract.image_to_data(
                img_pil, output_type=Output.DATAFRAME,
                config=f'--psm {psm}'
            )
            data = data[data['text'].notna()]
            data = data[data['text'].astype(str).str.strip() != '']
            data = data[data['conf'] >= min_conf]
            words = []
            for _, row in data.iterrows():
                words.append({
                    'left': int(row['left']),
                    'top': int(row['top']),
                    'width': int(row['width']),
                    'height': int(row['height']),
                    'conf': float(row['conf']),
                    'text': str(row['text']).strip(),
                })
            return words
        except Exception as e:
            logger.debug("Tesseract word detection also failed: %s", e)
            return []

    def _extract_table_from_image_bordered(
        self, img_pil, row_ys: List[int], col_xs: List[int]
    ) -> List[List[str]]:
        """
        Extract a table from an image with visible grid lines.
        Uses row-band OCR + column anchoring to avoid full-page OCR block
        collapse on scanned statements.
        """
        import cv2
        import numpy as np
        from PIL import Image as PILImage

        gray = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2GRAY)
        img_h, img_w = gray.shape[:2]
        col_xs = sorted(set(int(x) for x in col_xs if 0 <= int(x) < img_w))
        if not col_xs:
            return []
        # Many statements have a rightmost balance column where the border can be
        # faint/missed by line detection. Add image right edge as fallback boundary.
        if (img_w - 1) - col_xs[-1] > 60:
            col_xs = col_xs + [img_w - 1]

        # Remove table lines for cleaner OCR
        cleaned = self._remove_table_lines(gray)

        num_rows = len(row_ys) - 1
        num_cols = len(col_xs) - 1
        if num_rows < 1 or num_cols < 1:
            return []

        # Some statements expose only top/bottom horizontal borders while keeping
        # strong vertical column lines. In that case, derive row boundaries from
        # OCR line clustering inside the bordered table band.
        if num_rows < 8:
            return self._extract_bordered_table_with_ocr_row_clustering(
                cleaned_gray=cleaned,
                row_ys=row_ys,
                col_xs=col_xs,
            )

        matrix: List[List[str]] = []
        for r in range(num_rows):
            y0 = max(0, row_ys[r] + 1)
            y1 = min(img_h, row_ys[r + 1] - 1)
            if y1 - y0 < 8:
                continue

            # OCR one row band at a time: much more stable than full-page word
            # OCR for dense bordered statements.
            row_crop = cleaned[y0:y1, :]
            row_img = PILImage.fromarray(row_crop)
            psm = 7 if (y1 - y0) < 28 else 6
            row_words = self._get_ocr_words(row_img, psm=psm, min_conf=10)
            if not row_words:
                row_words = self._get_ocr_words(PILImage.fromarray(gray[y0:y1, :]), psm=psm, min_conf=10)

            cells_words: Dict[int, List[Tuple[int, str]]] = {ci: [] for ci in range(num_cols)}
            for w in row_words:
                text = (w.get('text') or '').strip()
                if not text:
                    continue
                cx = w['left'] + (w['width'] / 2.0)
                for c in range(num_cols):
                    if col_xs[c] <= cx <= col_xs[c + 1]:
                        cells_words[c].append((w['left'], text))
                        break

            row_cells: List[str] = []
            for c in range(num_cols):
                w_list = sorted(cells_words.get(c, []), key=lambda x: x[0])
                row_cells.append(" ".join(t for _, t in w_list).strip())

            # If row-band OCR is weak for a row, OCR each cell region for that row only.
            populated = sum(1 for cell in row_cells if cell.strip())
            if populated <= 1:
                fallback_cells: List[str] = []
                for c in range(num_cols):
                    x0 = max(0, col_xs[c] + 2)
                    x1 = min(img_w, col_xs[c + 1] - 2)
                    if x1 - x0 < 6:
                        fallback_cells.append("")
                        continue
                    cell_crop = cleaned[y0:y1, x0:x1]
                    cell_img = PILImage.fromarray(cell_crop)
                    cell_psm = 7 if (x1 - x0) < 140 else 6
                    words = self._get_ocr_words(cell_img, psm=cell_psm, min_conf=10)
                    if words:
                        words = sorted(words, key=lambda w: (w["top"], w["left"]))
                        text = " ".join((w.get("text") or "").strip() for w in words).strip()
                    else:
                        text = ""
                    fallback_cells.append(text)
                if sum(1 for cell in fallback_cells if cell.strip()) > populated:
                    row_cells = fallback_cells

            matrix.append(row_cells)

        return matrix

    def _extract_bordered_table_with_ocr_row_clustering(
        self,
        cleaned_gray,
        row_ys: List[int],
        col_xs: List[int],
    ) -> List[List[str]]:
        """
        Build table rows by clustering OCR words by Y-position within the
        bordered table area, while assigning words to fixed vertical columns.
        """
        from PIL import Image as PILImage

        img_h, img_w = cleaned_gray.shape[:2]
        if len(col_xs) < 2:
            return []

        ys = sorted(set(row_ys))
        table_top, table_bottom = 0, img_h
        if len(ys) >= 2:
            widest_gap = max(
                ((ys[i + 1] - ys[i], ys[i], ys[i + 1]) for i in range(len(ys) - 1)),
                key=lambda t: t[0],
            )
            gap_h, gap_top, gap_bottom = widest_gap
            if gap_h >= max(120, int(img_h * 0.15)):
                table_top, table_bottom = gap_top, gap_bottom
            else:
                table_top, table_bottom = ys[0], ys[-1]
        table_top = max(0, table_top - 40)  # capture header row just above first data row
        table_bottom = min(img_h, table_bottom + 5)

        words = self._get_ocr_words(PILImage.fromarray(cleaned_gray), psm=11, min_conf=10)
        if not words:
            return []

        col_min = max(0, min(col_xs) - 10)
        col_max = min(img_w, max(col_xs) + 10)
        table_words = []
        for w in words:
            cx = w["left"] + (w["width"] / 2.0)
            cy = w["top"] + (w["height"] / 2.0)
            if table_top <= cy <= table_bottom and col_min <= cx <= col_max:
                table_words.append(w)

        if not table_words:
            return []

        heights = sorted(w["height"] for w in table_words if w["height"] > 0)
        median_h = heights[len(heights) // 2] if heights else 12
        row_tolerance = max(8, int(median_h * 0.9))

        sorted_words = sorted(table_words, key=lambda w: (w["top"], w["left"]))
        grouped_rows: List[List[Dict[str, Any]]] = []
        current_row: List[Dict[str, Any]] = []
        current_y: Optional[float] = None

        for w in sorted_words:
            cy = w["top"] + (w["height"] / 2.0)
            if current_y is None or abs(cy - current_y) <= row_tolerance:
                current_row.append(w)
                if current_y is None:
                    current_y = cy
                else:
                    current_y = (current_y + cy) / 2.0
            else:
                grouped_rows.append(current_row)
                current_row = [w]
                current_y = cy
        if current_row:
            grouped_rows.append(current_row)

        num_cols = len(col_xs) - 1
        matrix: List[List[str]] = []
        for row_words in grouped_rows:
            cells: List[List[Tuple[int, str]]] = [[] for _ in range(num_cols)]
            for w in sorted(row_words, key=lambda x: x["left"]):
                text = (w.get("text") or "").strip()
                if not text:
                    continue
                cx = w["left"] + (w["width"] / 2.0)
                col_idx = None
                for ci in range(num_cols):
                    if col_xs[ci] <= cx <= col_xs[ci + 1]:
                        col_idx = ci
                        break
                if col_idx is None:
                    if cx < col_xs[0]:
                        col_idx = 0
                    elif cx > col_xs[-1]:
                        col_idx = num_cols - 1
                if col_idx is not None:
                    cells[col_idx].append((w["left"], text))

            row_cells: List[str] = []
            for cell_words in cells:
                cell_words.sort(key=lambda x: x[0])
                row_cells.append(" ".join(text for _, text in cell_words).strip())
            if any(c for c in row_cells):
                matrix.append(row_cells)

        if not matrix:
            return matrix

        # Drop non-table preamble rows and keep optional header row if present.
        date_token_re = re.compile(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b')
        amount_token_re = re.compile(r'\b\d{1,3}(?:,\d{2,3})*(?:\.\d{2})\b')
        header_kws = (
            "date", "txn", "value", "description", "narration", "particular",
            "reference", "debit", "credit", "withdraw", "deposit", "balance",
        )

        start_idx = 0
        for i, row in enumerate(matrix):
            row_joined = " ".join(c.lower() for c in row if c).strip()
            date_hits = sum(len(date_token_re.findall(c or "")) for c in row)
            amt_hits = sum(len(amount_token_re.findall(c or "")) for c in row)
            if date_hits >= 1 and amt_hits >= 1:
                if i > 0:
                    prev_joined = " ".join(c.lower() for c in matrix[i - 1] if c).strip()
                    if any(kw in prev_joined for kw in header_kws):
                        start_idx = i - 1
                    else:
                        start_idx = i
                else:
                    start_idx = i
                break
        matrix = matrix[start_idx:]

        return matrix

    def _extract_table_from_image_borderless(self, img_pil) -> List[List[str]]:
        """
        Extract a table from an image WITHOUT visible grid lines.
        Clusters words into rows by Y-position and detects column
        boundaries from consistent vertical gaps.
        """
        import numpy as np

        # Use PSM 6 (uniform block) for borderless tables
        words = self._get_ocr_words(img_pil, psm=6, min_conf=15)
        if not words:
            words = self._get_ocr_words(img_pil, psm=11, min_conf=10)
        if not words:
            return []

        # Estimate median line height
        heights = [w['height'] for w in words]
        if not heights:
            return []
        median_h = sorted(heights)[len(heights) // 2]
        row_tolerance = max(median_h * 0.6, 8)

        # Cluster words into rows by top position
        sorted_words = sorted(words, key=lambda w: (w['top'], w['left']))
        rows_of_words: List[List[dict]] = []
        current_row: List[dict] = []
        current_top: Optional[float] = None

        for w in sorted_words:
            if current_top is None or abs(w['top'] - current_top) <= row_tolerance:
                current_row.append(w)
                if current_top is None:
                    current_top = w['top']
            else:
                if current_row:
                    rows_of_words.append(current_row)
                current_row = [w]
                current_top = w['top']
        if current_row:
            rows_of_words.append(current_row)

        if not rows_of_words:
            return []

        # Detect column boundaries from word positions across all rows.
        # Collect all word left-edges and right-edges to find gap regions.
        all_rights: List[int] = []
        all_lefts: List[int] = []
        for row_words in rows_of_words:
            for w in row_words:
                all_lefts.append(w['left'])
                all_rights.append(w['left'] + w['width'])

        if not all_lefts:
            return []

        img_width = max(all_rights) + 50

        # Build a histogram of horizontal occupancy
        occupancy = np.zeros(img_width, dtype=int)
        for row_words in rows_of_words:
            for w in row_words:
                x0 = max(0, w['left'])
                x1 = min(img_width, w['left'] + w['width'])
                occupancy[x0:x1] += 1

        # Find sustained gaps (zero-occupancy regions wider than a threshold)
        min_gap_width = max(15, img_width // 40)
        in_gap = False
        gap_start = 0
        gaps: List[Tuple[int, int]] = []
        for x in range(img_width):
            if occupancy[x] == 0:
                if not in_gap:
                    in_gap = True
                    gap_start = x
            else:
                if in_gap:
                    if x - gap_start >= min_gap_width:
                        gaps.append((gap_start, x))
                    in_gap = False
        # End-of-image gap
        if in_gap and img_width - gap_start >= min_gap_width:
            gaps.append((gap_start, img_width))

        if not gaps:
            # Can't determine columns – return each row as single cell
            return [
                [' '.join(w['text'] for w in sorted(rw, key=lambda w: w['left']))]
                for rw in rows_of_words
            ]

        # Column boundaries are the midpoints of the gaps
        col_boundaries = [0]
        for g_start, g_end in gaps:
            col_boundaries.append((g_start + g_end) // 2)
        col_boundaries.append(img_width)

        num_cols = len(col_boundaries) - 1

        # Assign words to columns
        matrix: List[List[str]] = []
        for row_words in rows_of_words:
            cells = [''] * num_cols
            for w in sorted(row_words, key=lambda w: w['left']):
                cx = w['left'] + w['width'] // 2
                col_idx = None
                for i in range(num_cols):
                    if col_boundaries[i] <= cx < col_boundaries[i + 1]:
                        col_idx = i
                        break
                if col_idx is not None:
                    cells[col_idx] = f"{cells[col_idx]} {w['text']}".strip()
            matrix.append(cells)

        return matrix

    def _extract_table_from_image(
        self, img_pil, prev_col_xs: Optional[List[int]] = None
    ) -> Optional[List[List[str]]]:
        """
        Primary spatial table extraction from a page image.
        1. Try bordered extraction (OpenCV grid detection)
        2. If this page has weak vertical lines, reuse prev_col_xs from earlier page
        3. Fall back to borderless extraction (word position clustering)
        Returns a list of rows (each a list of cell strings), or None.
        Also stores detected col_xs in self._last_col_xs for reuse.
        """
        import cv2
        import numpy as np

        try:
            gray = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2GRAY)
        except Exception:
            return None

        # Try bordered table first
        row_ys, col_xs = self._detect_table_grid(gray)
        row_ys = self._select_dense_row_band(row_ys)

        min_rows_for_table = 3
        min_cols_for_table = 2

        # If we have good horizontal lines but weak verticals, try prev page's columns
        if (
            len(row_ys) >= min_rows_for_table + 1
            and len(col_xs) < min_cols_for_table + 1
            and prev_col_xs is not None
            and len(prev_col_xs) >= min_cols_for_table + 1
        ):
            # Scale prev_col_xs to match this page's width (may differ slightly)
            h_this, w_this = gray.shape[:2]
            col_xs = prev_col_xs  # reuse previous page's column positions

        if (
            len(row_ys) >= min_rows_for_table + 1
            and len(col_xs) >= min_cols_for_table + 1
        ):
            logger.debug("Grid detected: %d rows x %d cols", len(row_ys)-1, len(col_xs)-1)
            self._last_col_xs = col_xs  # remember for next page
            matrix = self._extract_table_from_image_bordered(img_pil, row_ys, col_xs)
            if matrix and len(matrix) >= 2:
                # Filter out completely empty rows
                matrix = [row for row in matrix if any(cell.strip() for cell in row)]
                if matrix and not self._looks_like_blobbed_rows(matrix):
                    return matrix
                if matrix:
                    logger.debug("Bordered grid output looks blobbed; trying borderless fallback...")

        # Fall back to borderless extraction
        logger.debug("No grid lines; trying borderless spatial extraction...")
        matrix = self._extract_table_from_image_borderless(img_pil)
        if matrix and len(matrix) >= 2:
            matrix = [row for row in matrix if any(cell.strip() for cell in row)]
            if matrix and not self._looks_like_blobbed_rows(matrix):
                return matrix
            if matrix:
                logger.debug("Borderless output looks blobbed; discarding page result.")

        return None

    def _detect_image_table_header(
        self, rows: List[List[str]]
    ) -> Tuple[Optional[List[str]], List[List[str]]]:
        """
        Detect whether the first row of a spatially-extracted table is a header.
        Returns (header_row_or_None, data_rows).
        """
        if not rows:
            return None, rows

        first_row = rows[0]
        joined = ' '.join(cell.lower() for cell in first_row if cell.strip())

        # Common header keywords across global bank statements
        header_kws = [
            'date', 'description', 'narration', 'particular', 'detail',
            'debit', 'credit', 'balance', 'amount', 'withdrawal', 'deposit',
            'reference', 'ref', 'cheque', 'check', 'remark', 'transaction',
        ]

        matches = sum(1 for kw in header_kws if kw in joined)
        if matches >= 2:
            return first_row, rows[1:]

        # Heuristic: if the first row has no digits at all, it's likely a header
        has_digit = any(any(c.isdigit() for c in cell) for cell in first_row if cell.strip())
        if not has_digit and any(cell.strip() for cell in first_row):
            return first_row, rows[1:]

        return None, rows

    def _looks_like_blobbed_rows(self, rows: List[List[str]]) -> bool:
        """
        Detect low-quality extraction where a row contains page-level text blobs
        (e.g., many dates and amounts concatenated into a single cell).
        """
        if not rows:
            return False

        date_token_re = re.compile(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b')
        amount_token_re = re.compile(r'\b\d{1,3}(?:,\d{2,3})*(?:\.\d{2})\b')

        sample = rows[: min(80, len(rows))]
        blob_like = 0
        sparse_rows = 0

        for row in sample:
            non_empty = [c for c in row if c and c.strip()]
            if len(non_empty) <= 1:
                sparse_rows += 1

            multi_date_cells = 0
            multi_amt_cells = 0
            long_cells = 0
            for cell in non_empty:
                txt = str(cell).strip()
                if len(txt) > 140:
                    long_cells += 1
                if len(date_token_re.findall(txt)) >= 3:
                    multi_date_cells += 1
                if len(amount_token_re.findall(txt)) >= 4:
                    multi_amt_cells += 1

            if multi_date_cells >= 1 and (multi_amt_cells >= 1 or long_cells >= 1):
                blob_like += 1

        sample_len = max(1, len(sample))
        blob_ratio = blob_like / sample_len
        sparse_ratio = sparse_rows / sample_len

        return blob_ratio >= 0.20 or (blob_ratio >= 0.10 and sparse_ratio >= 0.40)

    def _fallback_regex_parse(
        self, 
        text: str, 
        source_file: str
    ) -> None:
        """
        Structured OCR text parsing that handles multi-line descriptions
        and properly extracts columns from bank statement format.
        Handles OCR artifacts like split amounts (e.g., "1,79, 866.60" should be "1,79,866.60").
        """
        import re
        
        logger.info("Using structured OCR parsing...")
        
        # Pre-process text to fix common OCR issues with number formats
        # Fix split amounts like "1,79, 866.60" -> "1,79,866.60"
        text = re.sub(r'(\d),\s+(\d)', r'\1,\2', text)
        # Fix amounts with extra spaces like "1, 43, 666.60" -> "1,43,666.60"
        text = re.sub(r'(\d{1,2}),\s*(\d{2}),\s*(\d{3}\.\d{2})', r'\1,\2,\3', text)
        
        lines = text.splitlines()
        
        # Date patterns – supports global formats:
        #   DD/MM/YY, DD/MM/YYYY, DD-MM-YYYY       (UK/India)
        #   YYYY-MM-DD, YYYY/MM/DD                  (ISO/Canada)
        #   MM/DD/YYYY, MM-DD-YYYY                  (US)
        #   DD-Mon-YYYY, DD Mon YYYY                (14-Jan-2025, 14 Jan 2025)
        #   Mon DD, YYYY                            (Jan 14, 2025)
        _MONTHS = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*'
        date_pattern = re.compile(
            r'^('
            r'\d{4}[/-]\d{2}[/-]\d{2}'           # YYYY-MM-DD / YYYY/MM/DD
            r'|\d{2}[/-]\d{2}[/-]\d{2,4}'        # DD/MM/YY(YY) or MM/DD/YYYY
            r'|\d{1,2}[\s-]' + _MONTHS + r'[\s-]\d{2,4}'   # DD-Mon-YYYY
            r'|' + _MONTHS + r'\s+\d{1,2},?\s+\d{4}'       # Mon DD, YYYY
            r')',
            re.IGNORECASE,
        )
        
        # Amount pattern – global formats:
        #   Indian: 1,43,666.60   US/UK: 1,234.56   European: 1.234,56
        #   With or without decimals, optionally negative or in parentheses
        amount_pattern = re.compile(
            r'-?(\d{1,3}(?:[,.\s]\d{2,3})*(?:[.,]\d{1,2})?)'
        )
        
        # Reference pattern - 12+ digit transaction references (IMPS/NEFT refs)
        # More specific to avoid catching ATM IDs and other numbers
        reference_pattern = re.compile(r'(\d{12,18})')
        
        # Skip patterns - generic headers and footers for any bank statement
        # These patterns are designed to work across different banks globally
        skip_patterns = [
            # Column headers
            r'TXN\s*D(T|ATE)',
            r'VALUE\s*D(T|ATE)',
            r'TRANS(ACTION)?\s*D(T|ATE)',
            r'^DATE$',
            r'^DESCRIPTION$',
            r'^PARTICULARS$',
            r'^NARRATION$',
            r'^REFERENCE$',
            r'^REF\.?\s*(NO\.?)?$',
            r'DEBITS?\s*\/?\s*CREDITS?',
            r'WITHDRAW(AL)?S?',
            r'DEPOSITS?',
            r'^BALANCE$',
            r'^AMOUNT$',
            r'^DR\.?$',
            r'^CR\.?$',
            # Page headers/footers
            r'Scanned by',
            r'Page\s*\d+',
            r'Page\s*of',
            r'Continued',
            r'^\*+$',
            r'^-+$',
            # Statement headers (generic)
            r'STATEMENT\s+OF\s+ACCOUNT',
            r'ACCOUNT\s+STATEMENT',
            r'BANK\s+(LTD|LIMITED)',
            r'INDIAN\s+RUPEES',
            r'CURRENCY\s*:',
            r'Account\s*(Number|No\.?)',
            r'A/C\s*(No\.?|Number)',
            r'Period\s*(from|to)',
            r'Statement\s*Period',
            r'From\s*Date',
            r'To\s*Date',
            r'BRANCH\s*:',
            r'IFSC\s*:',
            r'MICR\s*:',
            # Customer info (generic patterns)
            r'^(Mr|Mrs|Ms|Dr|Shri|Smt)\.\s+',
            r'Customer\s*(Name|ID)',
            r'CIF\s*(No\.?|Number)',
            # Empty/junk lines
            r'^\d{1,3}\s*$',  # Lines with only short numbers
            r'^[\s\-_\.]+$',  # Lines with only punctuation
            # Common footers
            r'This\s+is\s+(a\s+)?computer',
            r'Generated\s+(on|by)',
            r'E\.?\s*&?\s*O\.?\s*E\.?',  # E&OE
            r'Opening\s*Balance',
            r'Closing\s*Balance\s*:',
            r'Total\s*(Debit|Credit)',
        ]
        skip_re = re.compile('|'.join(skip_patterns), re.IGNORECASE)
        
        transactions = []
        current_tx = None
        line_num = 0
        
        for line in lines:
            line_num += 1
            original_line = line
            line = line.strip()
            
            # Skip empty lines and headers
            if not line or len(line) < 5:
                continue
            if skip_re.search(line):
                continue
            
            # Check if line starts with a date (new transaction)
            date_match = date_pattern.match(line)
            
            if date_match:
                # Save previous transaction if exists
                if current_tx:
                    # Clean up description before saving
                    current_tx['description'] = self._clean_description(current_tx['description'])
                    transactions.append(current_tx)
                
                txn_date = date_match.group(1)
                rest_of_line = line[date_match.end():].strip()
                
                # Check for second date (value date) - skip it
                value_date_match = date_pattern.match(rest_of_line)
                if value_date_match:
                    rest_of_line = rest_of_line[value_date_match.end():].strip()
                
                # Extract all amounts from the line
                amounts_raw = amount_pattern.findall(line)
                amounts = [self._parse_amount(a) for a in amounts_raw]
                amounts = [a for a in amounts if a is not None and a > 0]
                
                # Extract reference number - look for common reference patterns globally
                reference = ""
                ref_patterns = [
                    # Indian IMPS/NEFT/UPI references
                    r'IMPS\s*(?:CR|DR)?[^\d]*(\d{12,16})',
                    r'NEFT\s*:?\s*(\w{10,20})',
                    r'UPI[/\s]*(\d{12,})',
                    r'UTR[:\s]*(\w{12,22})',
                    # International/Generic patterns
                    r'REF[:\s#]*(\w{8,20})',
                    r'TXN[:\s#]*(\w{8,20})',
                    r'TRANS(?:ACTION)?[:\s#]*(\w{8,20})',
                    # Cheque numbers
                    r'CHQ[:\s#]*(\d{6,})',
                    r'CHEQUE[:\s#]*(\d{6,})',
                    # Generic long reference numbers (12+ alphanumeric)
                    r'\b([A-Z]{2,4}\d{10,18})\b',
                ]
                for ref_pat in ref_patterns:
                    ref_match = re.search(ref_pat, rest_of_line, re.IGNORECASE)
                    if ref_match:
                        reference = ref_match.group(1)
                        break
                
                # Build description - start with the rest of line
                description = rest_of_line
                
                # Remove amounts from description
                for amt_str in amounts_raw:
                    description = description.replace(amt_str, '')
                
                # Remove branch code at start (like "1763 ", "1684 ")
                description = re.sub(r'^\d{4}\s+', '', description)
                
                # Clean up IMPS/NEFT format
                description = re.sub(r'IMPS\s+(CR|DR)-\d+-', r'IMPS \1 ', description)
                description = re.sub(r'-\s*$', '', description)  # Remove trailing dash
                
                # Clean up extra spaces and dashes
                description = re.sub(r'\s+', ' ', description)
                description = re.sub(r'\s*-\s*-\s*', ' ', description)
                description = description.strip(' -')
                
                # Determine debit/credit based on amounts and keywords
                withdrawal = None
                deposit = None
                balance = None
                
                if len(amounts) >= 2:
                    balance = amounts[-1]  # Last amount is balance
                    amount = amounts[-2]   # Second to last is transaction amount
                    
                    # Determine if credit or debit based on keywords (global patterns)
                    desc_upper = description.upper()
                    
                    # Credit indicators (money coming in)
                    credit_keywords = [
                        # Indian banking
                        'IMPS CR', 'NEFT CR', 'RTGS CR', 'UPI CR',
                        'NEFT :', 'RTGS :', 'UPI /',  # Usually credits in statements
                        'CASH DEPOSIT', 'BY TRANSFER', 'BY CLG',
                        # Global/Generic
                        'CREDIT', 'DEPOSIT', 'RECEIVED', 'INWARD',
                        'INTEREST', 'DIVIDEND', 'REFUND', 'REVERSAL',
                        'SALARY', 'BONUS', 'CASHBACK',
                        'FROM ', 'BY ',  # "From ABC", "By Transfer"
                        'CR$', ' CR ', 'CR.',  # CR indicator
                    ]
                    
                    # Debit indicators (money going out)
                    debit_keywords = [
                        # Indian banking
                        'IMPS DR', 'NEFT DR', 'RTGS DR', 'UPI DR',
                        'ATM', 'CASH WITHDRAWAL', 'TO CLG',
                        'ATW', 'CSW',  # ATM withdrawal codes
                        # Global/Generic
                        'DEBIT', 'WITHDRAW', 'PAYMENT', 'PURCHASE',
                        'TRANSFER TO', 'OUTWARD', 'CHARGE', 'FEE',
                        'TO ', 'POS ', 'BILL PAY', 'AUTO DEBIT',
                        'EMI', 'LOAN', 'INSURANCE',
                        'DR$', ' DR ', 'DR.',  # DR indicator
                    ]
                    
                    is_credit = any(kw in desc_upper for kw in credit_keywords)
                    is_debit = any(kw in desc_upper for kw in debit_keywords)
                    
                    # Opening/Brought forward balance - no transaction amount
                    if any(bf in desc_upper for bf in ['B/F', 'B/E', 'OPENING', 'BROUGHT FORWARD', 'O/B']):
                        withdrawal = None
                        deposit = None
                    elif is_credit and not is_debit:
                        deposit = amount
                    else:
                        # Default to debit if unclear (most transactions are debits)
                        withdrawal = amount
                elif len(amounts) == 1:
                    balance = amounts[0]
                
                current_tx = {
                    'txn_date': txn_date,
                    'description': description,
                    'reference': reference,
                    'withdrawal': withdrawal,
                    'deposit': deposit,
                    'balance': balance,
                    'line_num': line_num,
                }
            
            elif current_tx:
                # Continuation line - append to description
                if not skip_re.search(line) and not date_pattern.match(line):
                    # Skip lines that are just amounts or short numbers
                    if re.match(r'^[\d,.\s]+$', line):
                        continue
                    # Skip ATM location codes and short fragments
                    if len(line) < 4:
                        continue
                    
                    # Clean continuation text
                    cont_text = line
                    # Remove leading numbers (like "914" continuation of reference)
                    cont_text = re.sub(r'^\d{1,4}\s*', '', cont_text)
                    cont_text = re.sub(r'\s+', ' ', cont_text).strip()
                    
                    if cont_text and len(cont_text) >= 3:
                        current_tx['description'] += ' ' + cont_text
        
        # Don't forget the last transaction
        if current_tx:
            current_tx['description'] = self._clean_description(current_tx['description'])
            transactions.append(current_tx)
        
        # Convert to standard format and store as raw_table for Excel output
        raw_rows = []
        for tx in transactions:
            std_tx = self.create_transaction_dict(
                date=tx['txn_date'],
                description=tx['description'][:500],
                reference=tx['reference'],
                withdrawal_amt=tx['withdrawal'],
                deposit_amt=tx['deposit'],
                balance_amt=tx['balance'],
                source_file=source_file,
                line_ref=tx['line_num']
            )
            self.transactions.append(std_tx)
            
            # Also build raw table for direct Excel output
            raw_rows.append([
                tx['txn_date'],
                tx['description'],
                tx['reference'],
                tx['withdrawal'] if tx['withdrawal'] else '',
                tx['deposit'] if tx['deposit'] else '',
                tx['balance'] if tx['balance'] else '',
            ])
        
        # Set raw_table for worker.py to use
        if raw_rows:
            self.raw_table = {
                'columns': ['Date', 'Description', 'Reference', 'Debit', 'Credit', 'Balance'],
                'rows': raw_rows
            }
        
        logger.info("Parsed %d transactions from OCR text", len(transactions))
    
    def _clean_description(self, desc: str) -> str:
        """Clean up description text, removing OCR artifacts."""
        import re
        if not desc:
            return ""
        # Remove partial amounts like "1,43," or "1,"
        desc = re.sub(r'\d{1,2},\s*$', '', desc)
        desc = re.sub(r'\b\d{1,2},\d{2},\s*$', '', desc)
        desc = re.sub(r'\b\d{1,2},\s+', ' ', desc)
        # Remove standalone short numbers
        desc = re.sub(r'\s+\d{1,3}\s+', ' ', desc)
        # Remove extra spaces
        desc = re.sub(r'\s+', ' ', desc).strip()
        # Remove trailing/leading dashes
        desc = desc.strip(' -/')
        return desc
    
    def _parse_amount(self, amount_str: str) -> Optional[float]:
        """
        Parse various global number formats to float.
        Supports:
        - Indian: 1,23,456.78
        - US/UK: 1,234,567.89
        - European: 1.234.567,89 (comma as decimal)
        """
        if not amount_str:
            return None
        try:
            cleaned = amount_str.replace(' ', '')
            
            # Detect format based on last separator
            # If comma is near the end (e.g., "1234,56"), it's European format
            if ',' in cleaned and '.' in cleaned:
                # Has both - determine which is decimal
                last_comma = cleaned.rfind(',')
                last_dot = cleaned.rfind('.')
                if last_comma > last_dot:
                    # European format: 1.234,56 -> comma is decimal
                    cleaned = cleaned.replace('.', '').replace(',', '.')
                else:
                    # US/Indian format: 1,234.56 -> dot is decimal
                    cleaned = cleaned.replace(',', '')
            elif ',' in cleaned:
                # Only commas - could be European decimal or thousand separator
                # If exactly 2 digits after comma, treat as decimal
                parts = cleaned.split(',')
                if len(parts) == 2 and len(parts[1]) == 2:
                    cleaned = cleaned.replace(',', '.')
                else:
                    cleaned = cleaned.replace(',', '')
            # Dot-only is standard format, no change needed
            
            return float(cleaned)
        except (ValueError, TypeError):
            return None
    
    def get_processing_stats(self) -> Optional[ProcessingStats]:
        """Get statistics from the last processing run."""
        return self.stats

    def get_quality_report(self) -> Dict[str, Any]:
        """Get proxy quality metrics from the last processing run."""
        return self.quality_report or {}
    
    def estimate_processing_cost(
        self, 
        pdf_path: str
    ) -> Tuple[int, float]:
        """
        Estimate the cost of processing a PDF before actually processing.
        
        Args:
            pdf_path: Path to the PDF
            
        Returns:
            Tuple of (page_count, estimated_cost_usd)
        """
        import pdfplumber
        
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
        
        if self.llm_extractor and self.config.prefer_vision:
            cost = self.llm_extractor.estimate_cost(num_images=page_count)
        elif self.llm_extractor:
            # Estimate ~2000 chars per page of OCR text
            cost = self.llm_extractor.estimate_cost(text_length=page_count * 2000)
        else:
            cost = 0.0
        
        return page_count, cost


# Factory function for easy instantiation
def create_universal_parser(
    progress_callback: Optional[Callable] = None,
    execution_preset: Optional[str] = None,
    use_paddleocr: Optional[bool] = None,
    use_img2table: Optional[bool] = None,
    use_pymupdf: Optional[bool] = None,
    use_llm: Optional[bool] = None,
    prefer_vision: Optional[bool] = None,
    llm_model: Optional[str] = None,
    max_pages: Optional[int] = None,
    use_table_structure: Optional[bool] = None,
    min_table_transactions: Optional[int] = None,
    dpi: Optional[int] = None,
    use_template: Optional[bool] = None,
) -> UniversalBankParser:
    """
    Create a configured universal parser.
    
    All parameters default to None, which means "use environment variable".
    Set explicit values to override environment configuration.
    
    Environment Variables (see ProcessingConfig for full list):
        EXECUTION_PRESET: One of local-low-mem | prod-balanced | prod-high-accuracy
        USE_PADDLEOCR: Use PaddleOCR instead of Tesseract (default: true)
        USE_IMG2TABLE: Use img2table as primary scanned-doc path (default: true)
        USE_PYMUPDF: Use PyMuPDF table fallback for text/vector PDFs (default: true)
        USE_LLM: Use LLM for extraction (default: false)
        USE_TABLE_STRUCTURE: Use PPStructure for tables (default: false)
        OCR_DPI: Image DPI for OCR (default: 150)
        PREPROCESS_IMAGES: Enable image preprocessing (default: true)
        ADAPTIVE_PREPROCESS: Re-render at high DPI for low quality (default: false)
    
    Args:
        progress_callback: Optional callback for progress updates
        execution_preset: Runtime preset name (None = use EXECUTION_PRESET env if set)
        use_paddleocr: Whether to use PaddleOCR (None = use env)
        use_img2table: Whether to use img2table pipeline (None = use env)
        use_pymupdf: Whether to use PyMuPDF table fallback (None = use env)
        use_llm: Whether to use LLM for extraction (None = use env)
        prefer_vision: Whether to send images directly to LLM (None = use env)
        llm_model: Which OpenAI model to use (None = use env)
        max_pages: Maximum pages to process (for SaaS limits)
        use_table_structure: Use table structure detection (None = use env)
        min_table_transactions: Minimum transactions for table mode (None = use env)
        dpi: OCR resolution in dots per inch. 150 = standard, 200 = high quality.
             Higher DPI improves accuracy on faded/poor scans but is slower.
             (None = use OCR_DPI env var, default 150)
        
    Returns:
        Configured UniversalBankParser instance
    """
    # Start with environment-based defaults
    config = ProcessingConfig()

    # Apply execution preset first; explicit params below may override it.
    env_preset = os.environ.get("EXECUTION_PRESET", "").strip()
    selected_preset = execution_preset if execution_preset is not None else env_preset
    _apply_execution_preset(config, selected_preset)
    
    # Override with explicit parameters if provided
    if use_paddleocr is not None:
        config.use_paddleocr = use_paddleocr
    if use_img2table is not None:
        config.use_img2table = use_img2table
    if use_pymupdf is not None:
        config.use_pymupdf = use_pymupdf
    if use_llm is not None:
        config.use_llm = use_llm
    if prefer_vision is not None:
        config.prefer_vision = prefer_vision
    if llm_model is not None:
        config.llm_model = llm_model
    if max_pages is not None:
        config.max_pages = max_pages
    if use_table_structure is not None:
        config.use_table_structure = use_table_structure
    if min_table_transactions is not None:
        config.min_table_transactions = min_table_transactions
    if dpi is not None:
        config.dpi = dpi
    if use_template is not None:
        config.use_template = use_template

    return UniversalBankParser(progress_callback, config)
