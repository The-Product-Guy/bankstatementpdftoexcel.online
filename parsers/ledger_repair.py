"""Deterministic ledger repair helpers.

Repairs here are intentionally conservative: a transaction amount is rewritten
only when adjacent running balances prove the exact debit/credit value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .ledger_validation import (
    LedgerRow,
    StatementSummary,
    format_minor,
    ledger_rows_from_transactions,
)


@dataclass
class LedgerRepairAction:
    row_index: int
    reason: str
    confidence: float
    previous_balance_minor: Optional[int]
    closing_balance_minor: Optional[int]
    expected_amount_minor: int
    raw_debit_minor: Optional[int]
    raw_credit_minor: Optional[int]
    raw_amount_minor: Optional[int]
    final_debit_minor: Optional[int]
    final_credit_minor: Optional[int]
    final_amount_minor: int
    date: str = ""
    description: str = ""
    reference: str = ""
    page_line: str = ""


@dataclass
class LedgerRepairReport:
    row_count: int
    repaired_count: int = 0
    actions: List[LedgerRepairAction] = field(default_factory=list)

    @property
    def has_repairs(self) -> bool:
        return self.repaired_count > 0

    def to_records(self) -> List[Dict[str, Any]]:
        return [
            {
                "Row": action.row_index,
                "Date": action.date,
                "Description": action.description,
                "Reference": action.reference,
                "Page_Line": action.page_line,
                "Reason": action.reason,
                "Confidence": action.confidence,
                "Previous Balance": format_minor(action.previous_balance_minor),
                "Closing Balance": format_minor(action.closing_balance_minor),
                "Expected Amount": format_minor(action.expected_amount_minor),
                "Raw Debit": format_minor(action.raw_debit_minor),
                "Raw Credit": format_minor(action.raw_credit_minor),
                "Raw Amount": format_minor(action.raw_amount_minor),
                "Final Debit": format_minor(action.final_debit_minor),
                "Final Credit": format_minor(action.final_credit_minor),
                "Final Amount": format_minor(action.final_amount_minor),
            }
            for action in self.actions
        ]


def _minor_to_output_number(value: Optional[int]) -> Optional[float]:
    if value is None:
        return None
    return float(Decimal(value) / Decimal("100"))


def _desired_sides(amount_minor: int) -> Tuple[Optional[int], Optional[int]]:
    if amount_minor > 0:
        return None, amount_minor
    if amount_minor < 0:
        return abs(amount_minor), None
    return None, None


def _matches_expected(row: LedgerRow, expected_amount_minor: int) -> bool:
    expected_debit, expected_credit = _desired_sides(expected_amount_minor)
    return (
        row.amount_minor == expected_amount_minor
        and row.debit_minor == expected_debit
        and row.credit_minor == expected_credit
    )


def repair_transactions_from_balance_deltas(
    transactions: Iterable[Dict[str, Any]],
    summary: Optional[StatementSummary] = None,
    *,
    max_delta_minor: int = 10_000_000_00,
) -> Tuple[List[Dict[str, Any]], LedgerRepairReport]:
    """Repair debit/credit/amount fields using exact running-balance deltas.

    The function returns a copied transaction list and an audit report. It does
    not attempt fuzzy reconstruction, OCR correction, or row merging.
    """
    repaired = [dict(tx) for tx in (transactions or [])]
    rows = ledger_rows_from_transactions(repaired)
    report = LedgerRepairReport(row_count=len(rows))

    previous_balance: Optional[int] = summary.opening_balance_minor if summary else None
    for row, tx in zip(rows, repaired):
        if previous_balance is not None and row.balance_minor is not None:
            expected_amount = row.balance_minor - previous_balance
            if abs(expected_amount) <= max_delta_minor and not _matches_expected(row, expected_amount):
                final_debit, final_credit = _desired_sides(expected_amount)
                tx["Withdrawal_Amount"] = _minor_to_output_number(final_debit)
                tx["Deposit_Amount"] = _minor_to_output_number(final_credit)
                tx["Transaction_Amount"] = _minor_to_output_number(expected_amount)

                report.actions.append(
                    LedgerRepairAction(
                        row_index=row.index,
                        reason="balance_delta",
                        confidence=1.0,
                        previous_balance_minor=previous_balance,
                        closing_balance_minor=row.balance_minor,
                        expected_amount_minor=expected_amount,
                        raw_debit_minor=row.debit_minor,
                        raw_credit_minor=row.credit_minor,
                        raw_amount_minor=row.amount_minor,
                        final_debit_minor=final_debit,
                        final_credit_minor=final_credit,
                        final_amount_minor=expected_amount,
                        date=row.date,
                        description=row.description,
                        reference=row.reference,
                        page_line=str(row.source.get("Page_Line") or ""),
                    )
                )

        if row.balance_minor is not None:
            previous_balance = row.balance_minor

    report.repaired_count = len(report.actions)
    return repaired, report
