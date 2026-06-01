#!/usr/bin/env python3
"""Visual layout replica parser for PDF-to-Excel conversion.

This parser does not normalize bank statements. It extracts visible words with
coordinates, groups them into visual lines, and writes an Excel workbook that
approximates the PDF page layout. All cell values are written as strings.
"""
from __future__ import annotations

import logging
import os
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from pdf_utils import raise_if_password_protected
from .base_parser import BaseParser

logger = logging.getLogger(__name__)


@dataclass
class LayoutWord:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    page: int
    source: str = "pdf-text"
    confidence: Optional[float] = None

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.bottom - self.top)

    @property
    def width(self) -> float:
        return max(1.0, self.x1 - self.x0)


@dataclass
class LayoutLine:
    page: int
    index: int
    top: float
    bottom: float
    center_y: float
    words: List[LayoutWord] = field(default_factory=list)
    text: str = ""

    @property
    def height(self) -> float:
        return max(1.0, self.bottom - self.top)


@dataclass
class LayoutPage:
    page_number: int
    width: float
    height: float
    words: List[LayoutWord]
    lines: List[LayoutLine]
    source: str


@dataclass
class LayoutExtractionMetadata:
    row_count: int = 0
    col_count: int = 0
    has_data: bool = False
    extraction_method: str = "layout_replica"
    pdf_type: str = ""
    document_hint: str = "layout_replica"
    confidence: str = "good"
    message: str = ""


class LayoutReplicaParser(BaseParser):
    """Recreate visible PDF layout in Excel without transaction semantics."""

    def __init__(
        self,
        progress_callback=None,
        dpi: int = 150,
        points_per_column: float = 6.0,
        max_columns: int = 140,
        use_ocr: bool = True,
    ):
        super().__init__(progress_callback)
        self.bank_name = "Layout Replica"
        self.dpi = dpi
        self.points_per_column = points_per_column
        self.max_columns = max_columns
        self.use_ocr = use_ocr
        self.pages: List[LayoutPage] = []
        self.raw_table: Optional[Dict[str, Any]] = None
        self.extraction_metadata = LayoutExtractionMetadata()
        self.quality_report: Dict[str, Any] = {}
        self.source_filename = ""

    def parse(
        self,
        pdf_path: str,
        original_filename: str,
        page_start: int = 1,
        page_end: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        self.validate_pdf_file(pdf_path)
        raise_if_password_protected(pdf_path)
        self.source_filename = original_filename
        self.transactions = []
        self.pages = []
        self.raw_table = None
        self.quality_report = {}

        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            if total_pages <= 0:
                raise ValueError("PDF contains no pages.")
            start, end = self._resolve_page_window(total_pages, page_start, page_end)

            for page_num in range(start, end + 1):
                page = pdf.pages[page_num - 1]
                self._emit_progress(page_num - start + 1, end - start + 1, f"Reading page {page_num}")
                words = self._extract_pdf_words(page, page_num)
                source = "pdf-text"
                if not words and self.use_ocr:
                    words = self._extract_ocr_words(pdf_path, page_num, page.width, page.height)
                    source = "ocr" if words else "empty"
                elif not words:
                    source = "empty"
                lines = self._group_words_into_lines(words, page_num)
                self.pages.append(LayoutPage(
                    page_number=page_num,
                    width=float(page.width),
                    height=float(page.height),
                    words=words,
                    lines=lines,
                    source=source,
                ))

        self._build_raw_table()
        self._build_metadata()
        self.quality_report = self._build_quality_report()
        return self.transactions

    def write_excel(self, output_path: str) -> None:
        """Write a coordinate-based workbook. All values are strings."""
        wb = Workbook()
        default_sheet = wb.active
        wb.remove(default_sheet)

        combined = wb.create_sheet("Replica_All")
        current_row = 1
        for page in self.pages:
            current_row = self._write_page_sheet_rows(
                combined,
                page,
                start_row=current_row,
                include_page_header=True,
            )
            current_row += 2

        for page in self.pages:
            sheet = wb.create_sheet(self._safe_sheet_title(f"Page_{page.page_number}"))
            self._write_page_sheet_rows(sheet, page, start_row=1, include_page_header=False)

        self._write_page_index_sheet(wb)
        self._write_lines_sheet(wb)
        self._write_metadata_sheet(wb)
        wb.save(output_path)

    def get_quality_report(self) -> Dict[str, Any]:
        return self.quality_report or {}

    @staticmethod
    def _resolve_page_window(total_pages: int, page_start: int, page_end: Optional[int]) -> Tuple[int, int]:
        start = max(1, page_start or 1)
        end = page_end if page_end is not None else total_pages
        end = min(total_pages, end)
        if start > end:
            raise ValueError(f"Invalid page range: start={start}, end={end}, total_pages={total_pages}")
        return start, end

    def _emit_progress(self, current: int, total: int, status: str) -> None:
        if not self.progress_callback:
            return
        try:
            self.progress_callback({
                "current_page": current,
                "total_pages": total,
                "status": status,
                "stage": "text_extract",
            })
        except Exception:
            pass

    def _extract_pdf_words(self, page, page_num: int) -> List[LayoutWord]:
        try:
            extracted = page.extract_words(
                x_tolerance=1,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=False,
            )
        except TypeError:
            extracted = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)
        except Exception as exc:
            logger.warning("PDF word extraction failed on page %s: %s", page_num, exc)
            return []

        words = []
        for item in extracted or []:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                words.append(LayoutWord(
                    text=text,
                    x0=float(item["x0"]),
                    x1=float(item["x1"]),
                    top=float(item["top"]),
                    bottom=float(item["bottom"]),
                    page=page_num,
                    source="pdf-text",
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return words

    def _extract_ocr_words(
        self,
        pdf_path: str,
        page_num: int,
        page_width: float,
        page_height: float,
    ) -> List[LayoutWord]:
        try:
            from .paddleocr_processor import PaddleOCRProcessor

            image = self._render_page_image(pdf_path, page_num)
            processor = PaddleOCRProcessor(use_table_structure=False)
            results = processor.extract_with_coordinates(image, confidence_threshold=0.35)
        except Exception as exc:
            logger.warning("OCR layout extraction failed on page %s: %s", page_num, exc)
            return []

        scale_x = page_width / max(float(image.width), 1.0)
        scale_y = page_height / max(float(image.height), 1.0)
        words: List[LayoutWord] = []
        for item in results:
            text = str(item.get("text") or "").strip()
            bbox = item.get("bbox")
            if not text or not bbox:
                continue
            try:
                x0, top, x1, bottom = [float(value) for value in bbox]
                words.append(LayoutWord(
                    text=text,
                    x0=x0 * scale_x,
                    x1=x1 * scale_x,
                    top=top * scale_y,
                    bottom=bottom * scale_y,
                    page=page_num,
                    source="ocr",
                    confidence=float(item.get("confidence", 0.0)),
                ))
            except (TypeError, ValueError):
                continue
        return words

    def _render_page_image(self, pdf_path: str, page_num: int):
        try:
            import fitz
            from PIL import Image

            doc = fitz.open(pdf_path)
            try:
                page = doc.load_page(page_num - 1)
                scale = self.dpi / 72.0
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            finally:
                doc.close()
        except ImportError:
            from pdf2image import convert_from_path

            images = convert_from_path(
                pdf_path,
                dpi=self.dpi,
                first_page=page_num,
                last_page=page_num,
            )
            if not images:
                raise RuntimeError("Unable to render PDF page for OCR.")
            return images[0].convert("RGB")

    def _group_words_into_lines(self, words: List[LayoutWord], page_num: int) -> List[LayoutLine]:
        if not words:
            return []

        heights = [word.height for word in words]
        median_height = statistics.median(heights) if heights else 8.0
        line_tolerance = max(2.5, median_height * 0.45)
        sorted_words = sorted(words, key=lambda word: (word.center_y, word.x0))
        line_buckets: List[List[LayoutWord]] = []
        centers: List[float] = []

        for word in sorted_words:
            matched_index = None
            for idx, center in enumerate(centers):
                if abs(word.center_y - center) <= line_tolerance:
                    matched_index = idx
                    break
            if matched_index is None:
                line_buckets.append([word])
                centers.append(word.center_y)
            else:
                line_buckets[matched_index].append(word)
                bucket = line_buckets[matched_index]
                centers[matched_index] = sum(item.center_y for item in bucket) / len(bucket)

        lines: List[LayoutLine] = []
        for idx, bucket in enumerate(sorted(line_buckets, key=lambda group: min(word.top for word in group)), 1):
            bucket.sort(key=lambda word: word.x0)
            top = min(word.top for word in bucket)
            bottom = max(word.bottom for word in bucket)
            center_y = sum(word.center_y for word in bucket) / len(bucket)
            lines.append(LayoutLine(
                page=page_num,
                index=idx,
                top=top,
                bottom=bottom,
                center_y=center_y,
                words=bucket,
                text=self._line_text(bucket),
            ))
        return lines

    def _line_text(self, words: List[LayoutWord]) -> str:
        if not words:
            return ""
        char_widths = [word.width / max(len(word.text), 1) for word in words if word.text]
        char_width = statistics.median(char_widths) if char_widths else self.points_per_column
        char_width = max(2.0, min(char_width, 9.0))

        leading_spaces = " " * max(0, min(24, round(words[0].x0 / char_width)))
        parts = [f"{leading_spaces}{words[0].text}"]
        previous = words[0]
        for word in words[1:]:
            gap = word.x0 - previous.x1
            if gap <= 0.75:
                spaces = ""
            else:
                spaces = " " * max(1, min(16, round(gap / char_width)))
            parts.append(f"{spaces}{word.text}")
            previous = word
        return "".join(parts)

    def _column_for_x(self, x0: float, page_width: float) -> int:
        col = int(round(max(0.0, x0) / self.points_per_column)) + 1
        page_max = int(round(page_width / self.points_per_column)) + 2
        return max(1, min(col, min(self.max_columns, page_max)))

    def _write_page_sheet_rows(
        self,
        sheet,
        page: LayoutPage,
        start_row: int,
        include_page_header: bool,
    ) -> int:
        max_col = min(self.max_columns, int(round(page.width / self.points_per_column)) + 2)
        self._prepare_replica_sheet(sheet, max_col)
        row_idx = start_row

        if include_page_header:
            sheet.cell(row=row_idx, column=1, value=f"Page {page.page_number}").number_format = "@"
            sheet.cell(row=row_idx, column=1).font = Font(bold=True)
            row_idx += 1

        for line in page.lines:
            height = max(12, min(28, line.height * 1.25))
            sheet.row_dimensions[row_idx].height = height
            for word in line.words:
                col_idx = self._column_for_x(word.x0, page.width)
                cell = sheet.cell(row=row_idx, column=col_idx)
                cell.number_format = "@"
                cell.alignment = Alignment(vertical="top", wrap_text=False)
                if cell.value:
                    cell.value = f"{cell.value} {word.text}"
                else:
                    cell.value = str(word.text)
            row_idx += 1
        return row_idx

    def _prepare_replica_sheet(self, sheet, max_col: int) -> None:
        sheet.sheet_view.showGridLines = True
        for col_idx in range(1, max_col + 1):
            sheet.column_dimensions[get_column_letter(col_idx)].width = 1.6

    def _write_lines_sheet(self, wb: Workbook) -> None:
        sheet = wb.create_sheet("Text_Lines")
        headers = ["Page", "Line", "Source", "Text"]
        for col_idx, header in enumerate(headers, 1):
            cell = sheet.cell(row=1, column=col_idx, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="EAF2F8")
        row_idx = 2
        for page in self.pages:
            for line in page.lines:
                sheet.cell(row=row_idx, column=1, value=str(page.page_number)).number_format = "@"
                sheet.cell(row=row_idx, column=2, value=str(line.index)).number_format = "@"
                sheet.cell(row=row_idx, column=3, value=page.source).number_format = "@"
                sheet.cell(row=row_idx, column=4, value=line.text).number_format = "@"
                row_idx += 1
        sheet.column_dimensions["A"].width = 8
        sheet.column_dimensions["B"].width = 8
        sheet.column_dimensions["C"].width = 14
        sheet.column_dimensions["D"].width = 120

    def _write_page_index_sheet(self, wb: Workbook) -> None:
        sheet = wb.create_sheet("Page_Index")
        headers = ["Page", "Source", "Visual Lines", "Words", "Status"]
        for col_idx, header in enumerate(headers, 1):
            cell = sheet.cell(row=1, column=col_idx, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="EAF2F8")

        for row_idx, page in enumerate(self.pages, 2):
            status = "ok" if page.words else "empty"
            values = [
                str(page.page_number),
                page.source,
                str(len(page.lines)),
                str(len(page.words)),
                status,
            ]
            for col_idx, value in enumerate(values, 1):
                cell = sheet.cell(row=row_idx, column=col_idx, value=value)
                cell.number_format = "@"

        widths = [10, 14, 14, 12, 12]
        for col_idx, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(col_idx)].width = width

    def _write_metadata_sheet(self, wb: Workbook) -> None:
        sheet = wb.create_sheet("Extraction_Metadata")
        rows = [
            ("Mode", "layout_replica"),
            ("Source file", self.source_filename),
            ("Pages", str(len(self.pages))),
            ("Visual lines", str(self.extraction_metadata.row_count)),
            ("Approx. layout columns", str(self.extraction_metadata.col_count)),
            ("PDF type", self.extraction_metadata.pdf_type),
            ("Message", self.extraction_metadata.message),
        ]
        for row_idx, (key, value) in enumerate(rows, 1):
            sheet.cell(row=row_idx, column=1, value=key).font = Font(bold=True)
            sheet.cell(row=row_idx, column=2, value=str(value)).number_format = "@"
        sheet.column_dimensions["A"].width = 28
        sheet.column_dimensions["B"].width = 80

    def _build_raw_table(self) -> None:
        rows = []
        for page in self.pages:
            for line in page.lines:
                rows.append([str(page.page_number), str(line.index), line.text])
        self.raw_table = {
            "columns": ["Page", "Line", "Text"],
            "rows": rows,
        }

    def _build_metadata(self) -> None:
        line_count = sum(len(page.lines) for page in self.pages)
        word_count = sum(len(page.words) for page in self.pages)
        text_pages = sum(1 for page in self.pages if page.source == "pdf-text")
        ocr_pages = sum(1 for page in self.pages if page.source == "ocr")
        empty_pages = sum(1 for page in self.pages if not page.words)
        max_cols = 0
        for page in self.pages:
            page_cols = int(round(page.width / self.points_per_column)) + 2
            max_cols = max(max_cols, min(self.max_columns, page_cols))

        if word_count == 0:
            confidence = "empty"
            message = "No visible text was extracted from the PDF."
        elif empty_pages:
            confidence = "low"
            message = f"Replica created with text from {len(self.pages) - empty_pages} of {len(self.pages)} pages."
        else:
            confidence = "good"
            message = "Visual layout replica created from PDF text/OCR coordinates."

        if ocr_pages and text_pages:
            pdf_type = "hybrid"
        elif ocr_pages:
            pdf_type = "image"
        elif text_pages:
            pdf_type = "text"
        else:
            pdf_type = "unknown"

        self.extraction_metadata = LayoutExtractionMetadata(
            row_count=line_count,
            col_count=max_cols,
            has_data=word_count > 0,
            pdf_type=pdf_type,
            confidence=confidence,
            message=message,
        )

    def _build_quality_report(self) -> Dict[str, Any]:
        word_count = sum(len(page.words) for page in self.pages)
        ocr_confidences = [
            word.confidence
            for page in self.pages
            for word in page.words
            if word.confidence is not None
        ]
        avg_ocr_conf = (
            round(sum(ocr_confidences) / len(ocr_confidences), 3)
            if ocr_confidences else 0.0
        )
        return {
            "is_proxy": True,
            "mode": "layout_replica",
            "pdf_type": self.extraction_metadata.pdf_type,
            "total_pages": len(self.pages),
            "row_count": self.extraction_metadata.row_count,
            "word_count": word_count,
            "ocr_page_count": sum(1 for page in self.pages if page.source == "ocr"),
            "text_page_count": sum(1 for page in self.pages if page.source == "pdf-text"),
            "ocr_confidence_avg": avg_ocr_conf,
            "accuracy_proxy_pct": 100.0 if word_count else 0.0,
        }

    @staticmethod
    def _safe_sheet_title(title: str) -> str:
        cleaned = "".join("_" if ch in "[]:*?/\\'" else ch for ch in title)
        return cleaned[:31] or "Sheet"


def create_layout_replica_parser(progress_callback=None, quality: str = "standard", use_ocr: bool = True):
    dpi = 200 if quality == "high" else 150
    return LayoutReplicaParser(progress_callback=progress_callback, dpi=dpi, use_ocr=use_ocr)
