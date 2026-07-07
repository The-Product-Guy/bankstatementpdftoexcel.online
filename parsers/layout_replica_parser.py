#!/usr/bin/env python3
"""Visual layout replica parser for PDF-to-Excel conversion.

This parser does not normalize bank statements. It extracts visible words with
coordinates, groups them into visual lines, and writes an Excel workbook that
approximates the PDF page layout. All cell values are written as strings.
"""
from __future__ import annotations

import logging
import os
import re
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


@dataclass
class TableColumn:
    header: str
    x0: float
    x1: float
    left: float
    right: float

    @property
    def width(self) -> float:
        return max(1.0, self.right - self.left)


@dataclass
class TableReplicaRow:
    page: int
    line: int
    values: List[str]
    source: str


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
        self.table_columns: List[TableColumn] = []
        self.table_rows: List[TableReplicaRow] = []
        self.table_page_summaries: List[Dict[str, Any]] = []
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
        self.table_columns = []
        self.table_rows = []
        self.table_page_summaries = []
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
        self._build_table_replica()
        self._build_metadata()
        self.quality_report = self._build_quality_report()
        return self.transactions

    def write_excel(self, output_path: str) -> None:
        """Write the table replica plus a Full_Text sheet with every visual line."""
        wb = Workbook()
        default_sheet = wb.active
        wb.remove(default_sheet)

        self._write_table_replica_sheet(wb)
        self._write_lines_sheet(wb)
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
            image = self._render_page_image(pdf_path, page_num)
        except Exception as exc:
            logger.warning("Unable to render page %s for OCR: %s", page_num, exc)
            return []

        try:
            from .paddleocr_processor import PaddleOCRProcessor

            processor = PaddleOCRProcessor(use_table_structure=False)
            results = processor.extract_with_coordinates(image, confidence_threshold=0.35)
            words = self._ocr_results_to_layout_words(
                results,
                page_num,
                page_width,
                page_height,
                image.width,
                image.height,
                source="ocr",
            )
            if words:
                return words
        except Exception as exc:
            logger.warning("PaddleOCR layout extraction failed on page %s: %s", page_num, exc)

        words = self._extract_tesseract_words(image, page_num, page_width, page_height)
        if not words:
            logger.warning("OCR layout extraction returned no words on page %s", page_num)
        return words

    def _ocr_results_to_layout_words(
        self,
        results: List[Dict[str, Any]],
        page_num: int,
        page_width: float,
        page_height: float,
        image_width: int,
        image_height: int,
        source: str,
    ) -> List[LayoutWord]:
        scale_x = page_width / max(float(image_width), 1.0)
        scale_y = page_height / max(float(image_height), 1.0)
        words: List[LayoutWord] = []
        for item in results:
            text = str(item.get("text") or "").strip()
            bbox = item.get("bbox")
            if not text or not bbox:
                continue
            try:
                x0, top, x1, bottom = [float(value) for value in bbox]
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                continue
            for token, token_x0, token_x1 in self._split_ocr_tokens(text, x0 * scale_x, x1 * scale_x):
                words.append(LayoutWord(
                    text=token,
                    x0=token_x0,
                    x1=token_x1,
                    top=top * scale_y,
                    bottom=bottom * scale_y,
                    page=page_num,
                    source=source,
                    confidence=confidence,
                ))
        return words

    @staticmethod
    def _split_ocr_tokens(text: str, x0: float, x1: float) -> List[Tuple[str, float, float]]:
        """Split a multi-token OCR box into per-token boxes, width allocated
        proportionally by character count. Paddle detection boxes often span
        several table cells on tight scans; kept whole, one box centered on the
        wrong column drags every token in it into that column."""
        tokens = text.split()
        if len(tokens) <= 1:
            return [(text, x0, x1)]
        total_chars = sum(len(token) for token in tokens) + (len(tokens) - 1)
        per_char = max(x1 - x0, 1.0) / total_chars
        result = []
        cursor = x0
        for token in tokens:
            token_width = len(token) * per_char
            result.append((token, cursor, cursor + token_width))
            cursor += token_width + per_char
        return result

    def _extract_tesseract_words(
        self,
        image,
        page_num: int,
        page_width: float,
        page_height: float,
    ) -> List[LayoutWord]:
        try:
            import pytesseract
        except ImportError:
            logger.warning("Tesseract OCR fallback unavailable; pytesseract is not installed.")
            return []

        if image.mode != "RGB":
            image = image.convert("RGB")

        scale_x = page_width / max(float(image.width), 1.0)
        scale_y = page_height / max(float(image.height), 1.0)
        configs = (
            "--psm 6 -c preserve_interword_spaces=1",
            "--psm 11",
        )

        for config in configs:
            try:
                data = pytesseract.image_to_data(
                    image,
                    output_type=pytesseract.Output.DICT,
                    config=config,
                )
            except Exception as exc:
                logger.warning("Tesseract OCR failed on page %s with %s: %s", page_num, config, exc)
                continue

            words: List[LayoutWord] = []
            texts = data.get("text", [])
            for idx, raw_text in enumerate(texts):
                text = str(raw_text or "").strip()
                if not text:
                    continue
                try:
                    confidence = float(data.get("conf", [0])[idx])
                except (TypeError, ValueError, IndexError):
                    confidence = 0.0
                if confidence >= 0 and confidence < 30:
                    continue
                try:
                    left = float(data["left"][idx])
                    top = float(data["top"][idx])
                    width = float(data["width"][idx])
                    height = float(data["height"][idx])
                except (KeyError, TypeError, ValueError, IndexError):
                    continue
                if width <= 0 or height <= 0:
                    continue
                words.append(LayoutWord(
                    text=text,
                    x0=left * scale_x,
                    x1=(left + width) * scale_x,
                    top=top * scale_y,
                    bottom=(top + height) * scale_y,
                    page=page_num,
                    source="ocr-tesseract",
                    confidence=confidence / 100 if confidence > 1 else confidence,
                ))
            if words:
                return words
        return []

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

    def _build_table_replica(self) -> None:
        """Build a table-first view using inferred table headers and columns."""
        active_columns: List[TableColumn] = []
        table_columns_status = ""

        def adopt_workbook_columns(columns: List[TableColumn], status: str) -> None:
            # Real header rows always outrank positional inference; a single
            # noisy page must not replace detected bank headers just because
            # its word clusters produced more columns.
            nonlocal table_columns_status
            if table_columns_status == "header" and status != "header":
                return
            if status != "header" or table_columns_status == "header":
                if len(columns) <= len(self.table_columns):
                    return
            self.table_columns = [
                TableColumn(col.header, col.x0, col.x1, col.left, col.right)
                for col in columns
            ]
            table_columns_status = status

        for page in self.pages:
            header_line = self._detect_table_header_line(page)
            page_columns: List[TableColumn] = []
            column_status = "carried"

            if header_line:
                page_columns = self._columns_from_header_line(header_line, page.width)
                if len(page_columns) >= 2:
                    active_columns = page_columns
                    column_status = "header"
                    adopt_workbook_columns(page_columns, "header")

            if not active_columns:
                inferred_columns = self._columns_from_transaction_lines(page)
                if inferred_columns:
                    active_columns = inferred_columns
                    column_status = "inferred"
                    adopt_workbook_columns(inferred_columns, "inferred")
                else:
                    self.table_page_summaries.append({
                        "page": page.page_number,
                        "header_line": "",
                        "columns": 0,
                        "rows": 0,
                        "status": "no_table_header",
                    })
                    continue

            before_count = len(self.table_rows)
            page_rows = self._table_rows_from_page(page, active_columns, header_line)

            if not page_rows:
                inferred_columns = self._columns_from_transaction_lines(page)
                if inferred_columns:
                    inferred_rows = self._table_rows_from_page(page, inferred_columns, None)
                    if inferred_rows:
                        active_columns = inferred_columns
                        column_status = "inferred"
                        header_line = None
                        page_rows = inferred_rows
                        adopt_workbook_columns(inferred_columns, "inferred")

            self.table_rows.extend(page_rows)

            self.table_page_summaries.append({
                "page": page.page_number,
                "header_line": str(header_line.index) if header_line and column_status == "header" else column_status,
                "columns": len(active_columns),
                "rows": len(self.table_rows) - before_count,
                "status": "ok" if len(self.table_rows) > before_count else "no_table_rows",
            })

        if not self.table_columns and self.table_rows:
            max_width = max(len(row.values) for row in self.table_rows)
            self.table_columns = [
                TableColumn(f"Column {idx}", 0.0, 0.0, 0.0, 0.0)
                for idx in range(1, max_width + 1)
            ]

    def _table_rows_from_page(
        self,
        page: LayoutPage,
        columns: List[TableColumn],
        header_line: Optional[LayoutLine],
    ) -> List[TableReplicaRow]:
        rows: List[TableReplicaRow] = []
        saw_data_row = False
        for line in page.lines:
            if self._is_separator_line(line):
                continue
            if header_line and line.index == header_line.index:
                continue

            values = self._assign_line_to_table_columns(line, columns)
            populated = sum(1 for value in values if value)
            if not populated:
                continue

            # Wrapped cell text belongs to the previous row, not a new one.
            if self._is_continuation_row(line, values, columns, rows):
                self._merge_continuation_row(rows[-1], values)
                continue

            if not self._is_table_relevant_row(line, values, columns, saw_data_row):
                continue

            if populated == 1 and not saw_data_row:
                continue
            if populated >= 2:
                saw_data_row = True

            rows.append(TableReplicaRow(
                page=page.page_number,
                line=line.index,
                values=values,
                source=page.source,
            ))
        return rows

    def _detect_table_header_line(self, page: LayoutPage) -> Optional[LayoutLine]:
        best_line: Optional[LayoutLine] = None
        best_score = 0.0
        lines_by_index = {line.index: line for line in page.lines}

        for line in page.lines:
            if len(line.words) < 3 or self._is_separator_line(line):
                continue

            words = [word.text.strip() for word in line.words if word.text.strip()]
            if not words:
                continue

            alpha_words = sum(1 for word in words if re.search(r"[A-Za-z_]", word))
            numeric_words = sum(1 for word in words if re.search(r"\d", word))
            date_like_words = sum(1 for word in words if self._looks_like_date_value(word))
            amount_like_words = sum(1 for word in words if self._looks_like_amount_value(word))
            if alpha_words < 2 or numeric_words >= alpha_words:
                continue
            if date_like_words or amount_like_words >= 2:
                continue

            previous_separator = any(
                self._is_separator_line(lines_by_index[idx])
                for idx in range(max(1, line.index - 2), line.index)
                if idx in lines_by_index
            )
            next_separator = any(
                self._is_separator_line(lines_by_index[idx])
                for idx in range(line.index + 1, line.index + 3)
                if idx in lines_by_index
            )
            word_span = max(word.x1 for word in line.words) - min(word.x0 for word in line.words)
            uppercaseish = sum(
                1 for word in words
                if not re.search(r"[a-z]", word) or "_" in word
            )

            score = len(words) + alpha_words + (word_span / 120.0) + (uppercaseish / 2.0)
            if previous_separator:
                score += 4.0
            if next_separator:
                score += 5.0

            if score > best_score:
                best_score = score
                best_line = line

        return best_line if best_score >= 8.0 else None

    def _columns_from_transaction_lines(self, page: LayoutPage) -> List[TableColumn]:
        candidates = [
            line for line in page.lines
            if len(line.words) >= 4 and self._line_starts_with_date(line)
        ]
        if not candidates:
            return []

        candidate = max(
            candidates,
            key=lambda line: (
                len(line.words),
                max(word.x1 for word in line.words) - min(word.x0 for word in line.words),
            ),
        )
        words = sorted(candidate.words, key=lambda item: item.x0)
        if len(words) < 4:
            return []

        columns: List[TableColumn] = []
        for idx, word in enumerate(words):
            if idx == 0:
                left = max(0.0, word.x0 - 6.0)
            else:
                previous = words[idx - 1]
                left = (previous.x1 + word.x0) / 2.0

            if idx == len(words) - 1:
                right = min(page.width, word.x1 + 18.0)
            else:
                nxt = words[idx + 1]
                if idx == 1:
                    right = max(word.x1, nxt.x0 - 2.0)
                else:
                    right = (word.x1 + nxt.x0) / 2.0

            columns.append(TableColumn(
                header=f"Column {idx + 1}",
                x0=word.x0,
                x1=word.x1,
                left=left,
                right=right,
            ))
        return columns

    def _line_starts_with_date(self, line: LayoutLine) -> bool:
        if not line.words:
            return False
        first_word = sorted(line.words, key=lambda item: item.x0)[0].text
        return self._looks_like_date_value(first_word)

    @staticmethod
    def _looks_like_date_value(text: str) -> bool:
        return bool(re.fullmatch(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", text.strip()))

    @staticmethod
    def _looks_like_amount_value(text: str) -> bool:
        value = text.strip().replace(",", "")
        return bool(re.fullmatch(r"\d+(?:\.\d{1,4})?", value))

    @staticmethod
    def _is_separator_line(line: LayoutLine) -> bool:
        text = "".join(word.text for word in line.words).strip()
        if len(text) < 12:
            return False
        separator_chars = sum(1 for ch in text if ch in "-_=—–.")
        return separator_chars / max(len(text), 1) >= 0.85

    def _columns_from_header_line(self, header_line: LayoutLine, page_width: float) -> List[TableColumn]:
        groups: List[List[LayoutWord]] = []
        current: List[LayoutWord] = []
        char_widths = [
            word.width / max(len(word.text), 1)
            for word in header_line.words
            if word.text
        ]
        median_char_width = statistics.median(char_widths) if char_widths else 5.0
        merge_gap = max(5.5, min(8.5, median_char_width * 1.45))

        for word in sorted(header_line.words, key=lambda item: item.x0):
            if not current:
                current = [word]
                continue
            gap = word.x0 - current[-1].x1
            if gap <= merge_gap:
                current.append(word)
            else:
                groups.append(current)
                current = [word]
        if current:
            groups.append(current)

        header_cells = []
        for group in groups:
            text = " ".join(word.text.strip() for word in group if word.text.strip())
            if not text:
                continue
            header_cells.append({
                "header": text,
                "x0": min(word.x0 for word in group),
                "x1": max(word.x1 for word in group),
            })

        columns: List[TableColumn] = []
        for idx, cell in enumerate(header_cells):
            if idx == 0:
                left = max(0.0, cell["x0"] - 8.0)
            else:
                prev = header_cells[idx - 1]
                left = (prev["x1"] + cell["x0"]) / 2.0

            if idx == len(header_cells) - 1:
                right = min(page_width, cell["x1"] + 20.0)
            else:
                nxt = header_cells[idx + 1]
                if self._is_wide_text_header(str(cell["header"])):
                    right = max(cell["x1"], nxt["x0"] - 2.0)
                else:
                    right = (cell["x1"] + nxt["x0"]) / 2.0

            columns.append(TableColumn(
                header=str(cell["header"]),
                x0=float(cell["x0"]),
                x1=float(cell["x1"]),
                left=float(left),
                right=float(right),
            ))
        return columns

    @staticmethod
    def _is_wide_text_header(header: str) -> bool:
        normalized = header.lower()
        tokens = ("description", "details", "narration", "particular", "remarks", "reference", "ref")
        return any(token in normalized for token in tokens)

    def _assign_line_to_table_columns(self, line: LayoutLine, columns: List[TableColumn]) -> List[str]:
        buckets: List[List[LayoutWord]] = [[] for _ in columns]
        for word in sorted(line.words, key=lambda item: item.x0):
            col_idx = self._table_column_index_for_word(word, columns)
            if col_idx is not None:
                buckets[col_idx].append(word)

        self._move_narrow_column_overflow(buckets, columns)
        return [self._join_words(words) for words in buckets]

    def _is_table_relevant_row(
        self,
        line: LayoutLine,
        values: List[str],
        columns: List[TableColumn],
        saw_data_row: bool,
    ) -> bool:
        text = line.text.strip()
        if self._is_document_context_line(text):
            return False

        populated_indices = [idx for idx, value in enumerate(values) if value]
        if not populated_indices:
            return False

        first_two_values = values[:2]
        if any(self._looks_like_date_value(value) for value in first_two_values if value):
            return True

        amount_col_indices = [
            idx for idx, column in enumerate(columns)
            if self._is_amount_header(column.header)
        ]
        if any(
            idx in amount_col_indices and self._contains_amount_value(values[idx])
            for idx in populated_indices
        ):
            return True

        if len(populated_indices) == 1 and saw_data_row:
            column = columns[populated_indices[0]]
            return self._is_wide_text_header(column.header) or populated_indices[0] in {1, 2}

        return False

    @staticmethod
    def _is_document_context_line(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text.strip().lower())
        if not normalized:
            return True
        context_patterns = (
            r"^page\s*:",
            r"statement of account",
            r"account number",
            r"period from",
            r"period to",
            r"nominee\s*:",
            r"^branch\s*:",
            r"\bindian rupees\b",
            r"\bbank ltd\b",
            r"\bbank limited\b",
            r"closing balance includes",
            r"\bgstn\b",
            r"\bgstin\b",
            r"https?://",
            r"registered office",
            r"computer generated",
            r"system generated",
        )
        return any(re.search(pattern, normalized) for pattern in context_patterns)

    @staticmethod
    def _is_amount_header(header: str) -> bool:
        normalized = header.lower()
        return any(token in normalized for token in ("debit", "credit", "balance", "amount", "withdraw", "deposit"))

    def _contains_amount_value(self, text: str) -> bool:
        return any(self._looks_like_amount_value(part) for part in str(text).split())

    @staticmethod
    def _table_column_index_for_word(word: LayoutWord, columns: List[TableColumn]) -> Optional[int]:
        center = (word.x0 + word.x1) / 2.0
        best_idx = None
        best_key = None
        for idx, column in enumerate(columns):
            overlap = min(word.x1, column.right) - max(word.x0, column.left)
            if overlap <= 0:
                continue
            # Snap to the column holding most of the word's width; on exact
            # ties keep the column containing the word's center.
            key = (overlap, 1 if column.left <= center < column.right else 0)
            if best_key is None or key > best_key:
                best_idx, best_key = idx, key
        if best_idx is not None:
            return best_idx
        for idx, column in enumerate(columns):
            if column.left <= center < column.right:
                return idx
        if columns and center >= columns[-1].right and word.x0 <= columns[-1].right + 16.0:
            return len(columns) - 1
        return None

    @staticmethod
    def _move_narrow_column_overflow(buckets: List[List[LayoutWord]], columns: List[TableColumn]) -> None:
        for idx, column in enumerate(columns[:-1]):
            if column.width >= 45.0 or len(buckets[idx]) <= 1:
                continue
            keep = buckets[idx][:1]
            overflow = buckets[idx][1:]
            buckets[idx] = keep
            buckets[idx + 1] = sorted(overflow + buckets[idx + 1], key=lambda item: item.x0)

    @staticmethod
    def _join_words(words: List[LayoutWord]) -> str:
        return " ".join(word.text for word in sorted(words, key=lambda item: item.x0)).strip()

    def _is_continuation_row(
        self,
        line: LayoutLine,
        values: List[str],
        columns: List[TableColumn],
        page_rows: List[TableReplicaRow],
    ) -> bool:
        """A wrapped fragment of the previous row: no date, no amounts, and
        every populated cell sits in a text-ish column."""
        if not page_rows:
            return False
        if self._is_document_context_line(line.text.strip()):
            return False
        populated = [idx for idx, value in enumerate(values) if value]
        if not populated:
            return False
        if any(self._looks_like_date_value(value) for value in values[:2] if value):
            return False
        for idx in populated:
            if self._is_amount_header(columns[idx].header) and self._contains_amount_value(values[idx]):
                return False
            if not self._is_text_column(columns[idx], idx):
                return False
        return True

    def _is_text_column(self, column: TableColumn, idx: int) -> bool:
        header = column.header.lower()
        if self._is_wide_text_header(column.header):
            return True
        if any(token in header for token in ("reference", "ref", "remarks")):
            return True
        if header.startswith("column"):
            # positional layouts: description/reference usually sit at index 1-2
            return idx in {1, 2}
        return False

    @staticmethod
    def _merge_continuation_row(parent: TableReplicaRow, values: List[str]) -> None:
        for idx, value in enumerate(values):
            if not value or idx >= len(parent.values):
                continue
            parent.values[idx] = f"{parent.values[idx]} {value}".strip()

    def _write_table_replica_sheet(self, wb: Workbook) -> None:
        sheet = wb.create_sheet("sheet1")
        headers = [column.header for column in self.table_columns]
        if not headers:
            headers = ["No table detected"]

        for col_idx, header in enumerate(headers, 1):
            cell = sheet.cell(row=1, column=col_idx, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="EAF2F8")
            cell.number_format = "@"
            sheet.column_dimensions[get_column_letter(col_idx)].width = self._table_column_width(header)

        for row_idx, row in enumerate(self.table_rows, 2):
            values = row.values[:len(headers)]
            if len(values) < len(headers):
                values += [""] * (len(headers) - len(values))
            for col_idx, value in enumerate(values, 1):
                cell = sheet.cell(row=row_idx, column=col_idx, value=str(value) if value else "")
                cell.number_format = "@"
                cell.alignment = Alignment(vertical="top", wrap_text=True)

        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

    @staticmethod
    def _table_column_width(header: str) -> float:
        normalized = header.lower()
        if any(token in normalized for token in ("description", "details", "narration", "particular")):
            return 42
        if any(token in normalized for token in ("reference", "ref", "remarks")):
            return 24
        if any(token in normalized for token in ("balance", "debit", "credit", "amount")):
            return 16
        if any(token in normalized for token in ("date", "dt")):
            return 13
        return max(10, min(22, len(header) + 4))

    def _write_table_index_sheet(self, wb: Workbook) -> None:
        sheet = wb.create_sheet("Table_Index")
        headers = ["Page", "Header Line", "Columns", "Rows", "Status"]
        for col_idx, header in enumerate(headers, 1):
            cell = sheet.cell(row=1, column=col_idx, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="EAF2F8")

        for row_idx, summary in enumerate(self.table_page_summaries, 2):
            values = [
                str(summary.get("page", "")),
                str(summary.get("header_line", "")),
                str(summary.get("columns", "")),
                str(summary.get("rows", "")),
                str(summary.get("status", "")),
            ]
            for col_idx, value in enumerate(values, 1):
                cell = sheet.cell(row=row_idx, column=col_idx, value=value)
                cell.number_format = "@"

        widths = [10, 14, 12, 10, 18]
        for col_idx, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(col_idx)].width = width

    def _write_lines_sheet(self, wb: Workbook) -> None:
        sheet = wb.create_sheet("Full_Text")
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
            ("Table rows", str(len(self.table_rows))),
            ("Table columns", str(len(self.table_columns))),
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
            "table_row_count": len(self.table_rows),
            "table_column_count": len(self.table_columns),
            "table_page_count": sum(1 for summary in self.table_page_summaries if summary.get("rows", 0)),
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
