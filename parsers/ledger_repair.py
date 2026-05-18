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
    raw_balance_minor: Optional[int]
    final_debit_minor: Optional[int]
    final_credit_minor: Optional[int]
    final_amount_minor: int
    final_balance_minor: Optional[int]
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
                "Raw Balance": format_minor(action.raw_balance_minor),
                "Final Debit": format_minor(action.final_debit_minor),
                "Final Credit": format_minor(action.final_credit_minor),
                "Final Amount": format_minor(action.final_amount_minor),
                "Final Balance": format_minor(action.final_balance_minor),
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


def _next_supports_balance(
    candidate_balance_minor: Optional[int],
    next_row: Optional[LedgerRow],
) -> bool:
    if (
        candidate_balance_minor is None
        or next_row is None
        or next_row.balance_minor is None
        or next_row.amount_minor is None
    ):
        return False
    return next_row.balance_minor - candidate_balance_minor == next_row.amount_minor


def _major_digit_count(value: Optional[int]) -> int:
    if value is None:
        return 0
    major = abs(value) // 100
    return len(str(major))


def _balance_looks_truncated(
    previous_balance_minor: int,
    signed_amount_minor: int,
    raw_balance_minor: int,
    candidate_balance_minor: int,
) -> bool:
    """Detect balances where OCR likely dropped leading grouped digits."""
    if signed_amount_minor == 0:
        return False

    raw_delta = raw_balance_minor - previous_balance_minor
    if raw_delta == 0:
        return False

    direction_conflicts = (raw_delta > 0) != (signed_amount_minor > 0)
    if not direction_conflicts:
        return False

    raw_delta_dominates = abs(raw_delta) > max(abs(signed_amount_minor) * 3, 10_000_00)
    if not raw_delta_dominates:
        return False

    expected_digits = min(
        _major_digit_count(previous_balance_minor),
        _major_digit_count(candidate_balance_minor),
    )
    raw_digits = _major_digit_count(raw_balance_minor)
    digit_gap = raw_digits + 2 <= expected_digits

    expected_scale = max(abs(previous_balance_minor), abs(candidate_balance_minor), 1)
    raw_is_tiny = abs(raw_balance_minor) * 20 < expected_scale

    return digit_gap or raw_is_tiny


def _apply_amount(tx: Dict[str, Any], amount_minor: int) -> Tuple[Optional[int], Optional[int]]:
    final_debit, final_credit = _desired_sides(amount_minor)
    tx["Withdrawal_Amount"] = _minor_to_output_number(final_debit)
    tx["Deposit_Amount"] = _minor_to_output_number(final_credit)
    tx["Transaction_Amount"] = _minor_to_output_number(amount_minor)
    return final_debit, final_credit


def _apply_balance(tx: Dict[str, Any], balance_minor: Optional[int]) -> None:
    if balance_minor is not None:
        tx["Closing_Balance"] = _minor_to_output_number(balance_minor)


def _description_signed_amount(row: LedgerRow) -> Optional[int]:
    if row.amount_minor is None:
        return None

    text = f"{row.description} {row.reference}".upper()
    debit_markers = (
        "CHARGE", "CHARGES", "CHARY", "CHG", "FEE", "ATM", "IMPS DR", "NEFT DR", "RTGS DR",
        "UPI DR", " TO CLG", "PAYMENT", "WITHDRAW", "DEBIT",
    )
    credit_markers = (
        "IMPS CR", "NEFT CR", "RTGS CR", "UPI CR", "CASH DEPOSIT",
        "DEPOSIT", "FTD FROM", "FTN FROM", " FROM ", "CREDIT",
    )
    debit_hint = any(marker in text for marker in debit_markers)
    credit_hint = any(marker in text for marker in credit_markers)

    if debit_hint and not credit_hint:
        return -abs(row.amount_minor)
    if credit_hint and not debit_hint:
        return abs(row.amount_minor)
    return row.amount_minor


def repair_transactions_from_balance_deltas(
    transactions: Iterable[Dict[str, Any]],
    summary: Optional[StatementSummary] = None,
    *,
    max_delta_minor: int = 10_000_000_00,
    suspicious_amount_ratio: int = 20,
    small_balance_error_minor: int = 10_000_00,
) -> Tuple[List[Dict[str, Any]], LedgerRepairReport]:
    """Repair debit/credit/amount fields using exact running-balance deltas.

    The function returns a copied transaction list and an audit report. It does
    not attempt fuzzy reconstruction, OCR correction, or row merging.
    """
    repaired = [dict(tx) for tx in (transactions or [])]
    rows = ledger_rows_from_transactions(repaired)
    report = LedgerRepairReport(row_count=len(rows))

    previous_balance: Optional[int] = summary.opening_balance_minor if summary else None
    for index, (row, tx) in enumerate(zip(rows, repaired)):
        final_balance = row.balance_minor
        final_amount = row.amount_minor
        reason: Optional[str] = None

        if previous_balance is not None:
            next_row = rows[index + 1] if index + 1 < len(rows) else None
            signed_amount = _description_signed_amount(row)

            if row.balance_minor is not None and signed_amount is not None:
                delta_from_balance = row.balance_minor - previous_balance
                balance_from_amount = previous_balance + signed_amount
                amount_is_suspicious = (
                    abs(signed_amount) > max_delta_minor
                    or (
                        delta_from_balance != 0
                        and abs(signed_amount) > abs(delta_from_balance) * suspicious_amount_ratio
                    )
                )

                if delta_from_balance == signed_amount:
                    if not _matches_expected(row, signed_amount):
                        final_amount = signed_amount
                        reason = "side_from_amount"
                elif amount_is_suspicious:
                    final_amount = delta_from_balance
                    reason = "amount_from_balance_delta"
                else:
                    next_supports_balance_fix = _next_supports_balance(balance_from_amount, next_row)
                    next_supports_amount_fix = _next_supports_balance(row.balance_minor, next_row)
                    balance_error = abs(balance_from_amount - row.balance_minor)
                    balance_looks_truncated = _balance_looks_truncated(
                        previous_balance,
                        signed_amount,
                        row.balance_minor,
                        balance_from_amount,
                    )

                    if balance_looks_truncated:
                        final_amount = signed_amount
                        final_balance = balance_from_amount
                        reason = "balance_from_amount_delta"
                    elif next_supports_balance_fix and not next_supports_amount_fix:
                        final_amount = signed_amount
                        final_balance = balance_from_amount
                        reason = "balance_from_amount_delta"
                    elif next_supports_amount_fix and not next_supports_balance_fix:
                        final_amount = delta_from_balance
                        reason = "amount_from_balance_delta"
                    elif balance_error <= small_balance_error_minor:
                        final_amount = signed_amount
                        final_balance = balance_from_amount
                        reason = "balance_from_amount_delta"
                    else:
                        final_amount = delta_from_balance
                        reason = "amount_from_balance_delta"

            elif row.balance_minor is not None:
                final_amount = row.balance_minor - previous_balance
                reason = "amount_from_balance_delta"
            elif signed_amount is not None:
                final_amount = signed_amount
                final_balance = previous_balance + signed_amount
                reason = "balance_from_amount_delta"

        if reason and final_amount is not None:
            if abs(final_amount) <= max_delta_minor or reason != "amount_from_balance_delta":
                final_debit, final_credit = _apply_amount(tx, final_amount)
                _apply_balance(tx, final_balance)

                report.actions.append(
                    LedgerRepairAction(
                        row_index=row.index,
                        reason=reason,
                        confidence=1.0,
                        previous_balance_minor=previous_balance,
                        closing_balance_minor=row.balance_minor,
                        expected_amount_minor=final_amount,
                        raw_debit_minor=row.debit_minor,
                        raw_credit_minor=row.credit_minor,
                        raw_amount_minor=row.amount_minor,
                        raw_balance_minor=row.balance_minor,
                        final_debit_minor=final_debit,
                        final_credit_minor=final_credit,
                        final_amount_minor=final_amount,
                        final_balance_minor=final_balance,
                        date=row.date,
                        description=row.description,
                        reference=row.reference,
                        page_line=str(row.source.get("Page_Line") or ""),
                    )
                )

        if final_balance is not None:
            previous_balance = final_balance

    report.repaired_count = len(report.actions)
    return repaired, report
