"""Statement summary extraction helpers."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional, Union

from .ledger_validation import StatementSummary, parse_money_to_minor


MONEY_RE = re.compile(r"\d[\d,\s]*\.\s*\d{1,2}")


def _money_values(text: str) -> list[int]:
    values: list[int] = []
    for match in MONEY_RE.findall(text or ""):
        candidate = re.sub(r"^\d\s+(?=\d{2,3}\s*,)", "", match.strip())
        parsed = parse_money_to_minor(candidate)
        if parsed is not None:
            values.append(abs(parsed))
    return values


def _last_money(text: str) -> Optional[int]:
    values = _money_values(text)
    return values[-1] if values else None


def _last_int_after(label: str, text: str) -> Optional[int]:
    pattern = re.compile(label + r"\s*[:\-]?\s*(\d+)", re.IGNORECASE)
    matches = pattern.findall(text or "")
    return int(matches[-1]) if matches else None


def parse_statement_summary_text(text: str) -> StatementSummary:
    summary = StatementSummary()
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in lines:
        normalized = re.sub(r"\s+", " ", line).lower()
        if "opening balance" in normalized:
            summary.opening_balance_minor = _last_money(line)
        elif "closing balance" in normalized:
            summary.closing_balance_minor = _last_money(line)
        elif "total credit amount" in normalized:
            summary.total_credit_minor = _last_money(line)
            count = _last_int_after(r"credit\s+count", line)
            if count is not None:
                summary.credit_count = count
        elif "total debit amount" in normalized:
            summary.total_debit_minor = _last_money(line)
            count = _last_int_after(r"debit\s+count", line)
            if count is not None:
                summary.debit_count = count

    joined = "\n".join(lines)
    if summary.credit_count is None:
        summary.credit_count = _last_int_after(r"credit\s+count", joined)
    if summary.debit_count is None:
        summary.debit_count = _last_int_after(r"debit\s+count", joined)

    return summary


def merge_summaries(summaries: Iterable[StatementSummary]) -> StatementSummary:
    merged = StatementSummary()
    for summary in summaries:
        for field_name in (
            "opening_balance_minor",
            "closing_balance_minor",
            "total_debit_minor",
            "total_credit_minor",
            "debit_count",
            "credit_count",
        ):
            value = getattr(summary, field_name)
            if value is not None:
                setattr(merged, field_name, value)
    return merged


def _summary_complete(summary: StatementSummary) -> bool:
    return all(
        getattr(summary, field_name) is not None
        for field_name in (
            "opening_balance_minor",
            "closing_balance_minor",
            "total_debit_minor",
            "total_credit_minor",
            "debit_count",
            "credit_count",
        )
    )


def page_sample(pdf_path: Path, sample_pages: int = 2) -> List[int]:
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        total_pages = len(pdf.pages)
    pages = set(range(1, min(sample_pages, total_pages) + 1))
    pages.update(range(max(1, total_pages - sample_pages + 1), total_pages + 1))
    return sorted(pages)


def extract_pdf_text_native(pdf_path: Path, page_numbers: Iterable[int]) -> str:
    import pdfplumber

    chunks: List[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number in page_numbers:
            if 1 <= page_number <= len(pdf.pages):
                text = pdf.pages[page_number - 1].extract_text() or ""
                if text.strip():
                    chunks.append(text)
    return "\n".join(chunks)


def extract_pdf_text_ocr(pdf_path: Path, page_numbers: Iterable[int], dpi: int = 180) -> str:
    from pdf2image import convert_from_path
    import pytesseract

    chunks: List[str] = []
    for page_number in page_numbers:
        images = convert_from_path(
            str(pdf_path),
            dpi=dpi,
            first_page=page_number,
            last_page=page_number,
        )
        if images:
            chunks.append(pytesseract.image_to_string(images[0], config="--psm 6"))
    return "\n".join(chunks)


def extract_statement_summary_from_pdf(
    pdf_path: Union[Path, str],
    *,
    sample_pages: int = 2,
    use_ocr: bool = True,
    ocr_dpi: int = 180,
    logger=None,
) -> StatementSummary:
    path = Path(pdf_path)
    pages = page_sample(path, sample_pages)
    summaries: List[StatementSummary] = []

    native_text = extract_pdf_text_native(path, pages)
    if native_text.strip():
        summaries.append(parse_statement_summary_text(native_text))

    should_try_ocr = use_ocr and not any(_summary_complete(summary) for summary in summaries)
    if should_try_ocr:
        try:
            ocr_text = extract_pdf_text_ocr(path, pages, dpi=ocr_dpi)
            if ocr_text.strip():
                summaries.append(parse_statement_summary_text(ocr_text))
        except Exception as exc:
            if logger:
                logger.warning("OCR statement summary extraction failed: %s", exc)

    return merge_summaries(summaries)
