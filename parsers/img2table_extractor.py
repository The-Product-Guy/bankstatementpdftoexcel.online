#!/usr/bin/env python3
"""
img2table-based Table Extractor for Image-based / Scanned PDFs.

Uses img2table (OpenCV + RLSA) for table DETECTION (finding where tables are
and their cell structure), combined with PaddleOCR (ONNX Runtime) for OCR
(reading the text inside each cell).

This replaces our fragile custom spatial extraction code for scanned PDFs
while producing the same output format (raw_table dict with columns + rows).
"""
import os
import gc
import logging
from typing import List, Dict, Any, Optional, Tuple
from collections import OrderedDict

logger = logging.getLogger(__name__)


def _get_paddleocr_instance():
    """
    Create an img2table PaddleOCR backend instance.
    This reuses the same PaddleOCR ONNX models that are already warm
    from worker.py's _warmup_ocr_models().
    """
    from img2table.ocr import PaddleOCR
    return PaddleOCR(lang="en")


def _get_tesseract_instance():
    """Fallback: create an img2table Tesseract OCR backend."""
    from img2table.ocr import TesseractOCR
    return TesseractOCR(n_threads=1, lang="eng", psm=6)


class Img2TableExtractor:
    """
    Extracts tables from image-based PDFs using img2table.

    img2table handles:
    - Bordered tables (line detection + cell clustering)
    - Borderless tables (RLSA text mask + column/row detection)
    - Semi-bordered tables (partial lines)
    - Implicit rows/columns (content without visible separators)
    - Merged cells

    PaddleOCR (ONNX Runtime) reads the text in each detected cell.
    """

    def __init__(self, dpi: int = 150, use_paddleocr: bool = True):
        """
        Args:
            dpi: Not directly used by img2table (it renders at 200 DPI internally),
                 but stored for reference / future per-page fallback.
            use_paddleocr: If True, use PaddleOCR backend. If False, use Tesseract.
        """
        self.dpi = dpi
        self.use_paddleocr = use_paddleocr
        self._ocr = None
        self._ocr_failed = False

    @property
    def ocr(self):
        """Lazy-load the OCR backend."""
        if self._ocr is None and not self._ocr_failed:
            try:
                if self.use_paddleocr:
                    self._ocr = _get_paddleocr_instance()
                    logger.info("img2table using PaddleOCR backend")
                else:
                    self._ocr = _get_tesseract_instance()
                    logger.info("img2table using Tesseract backend")
            except Exception as e:
                logger.warning(f"img2table OCR init failed: {e}")
                self._ocr_failed = True
                # Try fallback
                if self.use_paddleocr:
                    try:
                        self._ocr = _get_tesseract_instance()
                        logger.info("img2table fell back to Tesseract")
                    except Exception as e2:
                        logger.error(f"img2table Tesseract fallback also failed: {e2}")
        return self._ocr

    def extract_tables_from_pdf(
        self,
        pdf_path: str,
        pages: Optional[List[int]] = None,
        implicit_rows: bool = True,
        implicit_columns: bool = False,
        borderless_tables: bool = True,
        min_confidence: int = 30,
    ) -> Dict[str, Any]:
        """
        Extract all tables from a PDF file.

        Args:
            pdf_path: Path to the PDF file.
            pages: List of 0-based page indexes to process. None = all pages.
            implicit_rows: Detect rows without visible separators.
            implicit_columns: Detect columns without visible separators.
            borderless_tables: Also detect tables without any border lines.
            min_confidence: Minimum OCR confidence (0-99).

        Returns:
            Dict with keys:
                'tables': list of dicts, each with 'page', 'columns', 'rows', 'bbox'
                'raw_table': merged dict with 'columns' and 'rows' (all tables combined)
                'page_count': total pages processed
        """
        from img2table.document import PDF

        if not self.ocr:
            logger.warning("No OCR backend available for img2table")
            return {"tables": [], "raw_table": None, "page_count": 0}

        try:
            doc = PDF(
                src=pdf_path,
                pages=pages,
                detect_rotation=True,
                pdf_text_extraction=True,  # Try native text first (faster for text-based pages)
            )

            extracted = doc.extract_tables(
                ocr=self.ocr,
                implicit_rows=implicit_rows,
                implicit_columns=implicit_columns,
                borderless_tables=borderless_tables,
                min_confidence=min_confidence,
            )
        except Exception as e:
            logger.error(f"img2table extraction failed: {e}")
            return {"tables": [], "raw_table": None, "page_count": 0}

        # extracted is OrderedDict: {page_idx: [ExtractedTable, ...], ...}
        return self._process_extracted_tables(extracted)

    def _process_extracted_tables(
        self, extracted: OrderedDict
    ) -> Dict[str, Any]:
        """
        Convert img2table's ExtractedTable objects into our raw_table format.

        Strategy for bank statements:
        - Bank statements typically have ONE main transaction table per page
        - We pick the LARGEST table from each page (by row count)
        - We merge all pages' tables into one continuous table
        - Header deduplication: if subsequent pages repeat the header row, skip it
        """
        all_tables = []
        merged_columns = None
        merged_rows = []
        header_row_text = None

        for page_idx in sorted(extracted.keys()):
            page_tables = extracted.get(page_idx) or []
            if not page_tables:
                continue

            # Pick the largest table on this page (most rows = most likely the transaction table)
            best_table = max(page_tables, key=lambda t: len(t.content) if t.content else 0)

            if not best_table.content or len(best_table.content) == 0:
                continue

            # Convert ExtractedTable to DataFrame then to our format
            try:
                df = best_table.df
            except Exception as e:
                logger.warning(f"Failed to get DataFrame from table on page {page_idx}: {e}")
                continue

            if df is None or df.empty:
                continue

            # Extract columns and rows
            columns = [str(c) for c in df.columns.tolist()]
            rows = []
            for _, row in df.iterrows():
                rows.append([str(v) if v is not None else "" for v in row.tolist()])

            if not rows:
                continue

            # Store individual table info
            bbox = None
            if hasattr(best_table, 'bbox') and best_table.bbox:
                bbox = {
                    'x1': best_table.bbox.x1,
                    'y1': best_table.bbox.y1,
                    'x2': best_table.bbox.x2,
                    'y2': best_table.bbox.y2,
                }

            all_tables.append({
                'page': page_idx,
                'columns': columns,
                'rows': rows,
                'bbox': bbox,
                'row_count': len(rows),
            })

            # --- Merge into single continuous table ---
            if merged_columns is None:
                # First page with data: use its header
                merged_columns = columns
                header_row_text = self._row_fingerprint(rows[0]) if rows else None
                merged_rows.extend(rows)
            else:
                # Subsequent pages: skip header row if it matches the first page's header
                start_idx = 0
                if rows and header_row_text:
                    first_row_fp = self._row_fingerprint(rows[0])
                    if self._is_header_match(first_row_fp, header_row_text):
                        start_idx = 1

                # Normalize column count
                target_cols = len(merged_columns)
                for row in rows[start_idx:]:
                    while len(row) < target_cols:
                        row.append("")
                    if len(row) > target_cols:
                        row = row[:target_cols]
                    merged_rows.append(row)

        # Build raw_table
        raw_table = None
        if merged_rows and merged_columns:
            raw_table = {
                "columns": merged_columns,
                "rows": merged_rows,
            }

        page_count = len(extracted) if extracted else 0

        return {
            "tables": all_tables,
            "raw_table": raw_table,
            "page_count": page_count,
        }

    @staticmethod
    def _row_fingerprint(row: List[str]) -> str:
        """Create a normalized fingerprint of a row for comparison."""
        return "|".join(cell.strip().lower() for cell in row if cell.strip())

    @staticmethod
    def _is_header_match(fp1: str, fp2: str) -> bool:
        """
        Check if two row fingerprints are similar enough to be the same header.
        Uses a fuzzy match because OCR may produce slightly different text on different pages.
        """
        if not fp1 or not fp2:
            return False
        if fp1 == fp2:
            return True

        # Check if most words overlap (handles minor OCR differences)
        words1 = set(fp1.replace("|", " ").split())
        words2 = set(fp2.replace("|", " ").split())
        if not words1 or not words2:
            return False
        overlap = len(words1 & words2)
        total = max(len(words1), len(words2))
        return overlap / total >= 0.6
