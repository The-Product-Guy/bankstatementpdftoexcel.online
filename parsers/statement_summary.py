"""Statement summary extraction helpers."""
from __future__ import annotations

import re
from typing import Iterable, Optional

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
