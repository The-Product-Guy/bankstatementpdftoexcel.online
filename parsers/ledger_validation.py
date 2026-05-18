"""Canonical ledger validation for extracted bank-statement transactions.

This module is intentionally independent of OCR/table extraction. It validates
the normalized transaction layer using exact minor-unit arithmetic so we can
detect bad parses before generating a trusted workbook.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any, Dict, Iterable, List, Optional


MONEY_SCALE = Decimal("100")


@dataclass
class LedgerRow:
    index: int
    date: str = ""
    description: str = ""
    reference: str = ""
    debit_minor: Optional[int] = None
    credit_minor: Optional[int] = None
    amount_minor: Optional[int] = None
    balance_minor: Optional[int] = None
    source: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StatementSummary:
    opening_balance_minor: Optional[int] = None
    closing_balance_minor: Optional[int] = None
    total_debit_minor: Optional[int] = None
    total_credit_minor: Optional[int] = None
    debit_count: Optional[int] = None
    credit_count: Optional[int] = None


@dataclass
class ValidationIssue:
    code: str
    message: str
    row_index: Optional[int] = None
    severity: str = "error"
    expected_minor: Optional[int] = None
    actual_minor: Optional[int] = None


@dataclass
class LedgerValidationReport:
    row_count: int
    balance_checks: int
    balance_checks_passed: int
    debit_count: int
    credit_count: int
    total_debit_minor: int
    total_credit_minor: int
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def balance_consistency_pct(self) -> float:
        if self.balance_checks == 0:
            return 0.0
        return round((self.balance_checks_passed / self.balance_checks) * 100.0, 2)

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_count": self.row_count,
            "balance_checks": self.balance_checks,
            "balance_checks_passed": self.balance_checks_passed,
            "balance_consistency_pct": self.balance_consistency_pct,
            "debit_count": self.debit_count,
            "credit_count": self.credit_count,
            "total_debit": format_minor(self.total_debit_minor),
            "total_credit": format_minor(self.total_credit_minor),
            "is_valid": self.is_valid,
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "row_index": issue.row_index,
                    "severity": issue.severity,
                    "expected": format_minor(issue.expected_minor),
                    "actual": format_minor(issue.actual_minor),
                }
                for issue in self.issues
            ],
        }


def parse_money_to_minor(value: Any) -> Optional[int]:
    """Parse a money value into integer minor units without float arithmetic."""
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    text = str(value).strip()
    if not text:
        return None

    is_negative = False
    lowered = text.lower()
    if lowered.endswith("dr"):
        is_negative = True
        text = text[:-2]
    elif lowered.endswith("cr"):
        text = text[:-2]

    if text.startswith("(") and text.endswith(")"):
        is_negative = True
        text = text[1:-1]

    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", "", text)
    text = text.replace(",", "")
    text = text.replace("O", "0").replace("o", "0")
    text = text.replace("I", "1").replace("l", "1")
    text = re.sub(r"[^0-9.\-]", "", text)

    if not text or text in {"-", ".", "-."}:
        return None

    if text.count("-") > 1:
        return None
    if text.startswith("-"):
        is_negative = True
        text = text[1:]
    elif "-" in text:
        return None

    if text.count(".") > 1:
        return None

    try:
        amount = Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None

    minor = int((amount * MONEY_SCALE).to_integral_value(rounding=ROUND_HALF_UP))
    return -minor if is_negative else minor


def format_minor(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    major, minor = divmod(absolute, 100)
    return f"{sign}{major}.{minor:02d}"


def _first_present(row: Dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in row and row.get(name) not in (None, ""):
            return row.get(name)
    return None


def ledger_rows_from_transactions(transactions: Iterable[Dict[str, Any]]) -> List[LedgerRow]:
    rows: List[LedgerRow] = []
    for index, tx in enumerate(transactions, start=1):
        debit_minor = parse_money_to_minor(_first_present(tx, ("Withdrawal_Amount", "Debit", "Debits")))
        credit_minor = parse_money_to_minor(_first_present(tx, ("Deposit_Amount", "Credit", "Credits")))
        amount_minor = parse_money_to_minor(_first_present(tx, ("Transaction_Amount", "Amount")))
        if amount_minor is None:
            if credit_minor is not None and debit_minor is None:
                amount_minor = credit_minor
            elif debit_minor is not None and credit_minor is None:
                amount_minor = -abs(debit_minor)

        balance_minor = parse_money_to_minor(_first_present(tx, ("Closing_Balance", "Balance")))
        rows.append(
            LedgerRow(
                index=index,
                date=str(_first_present(tx, ("Date", "Txn Date", "Transaction_Date")) or ""),
                description=str(_first_present(tx, ("Description", "Narration", "Particulars")) or ""),
                reference=str(_first_present(tx, ("Reference_Number", "Reference", "Ref")) or ""),
                debit_minor=debit_minor,
                credit_minor=credit_minor,
                amount_minor=amount_minor,
                balance_minor=balance_minor,
                source=dict(tx),
            )
        )
    return rows


def validate_ledger_rows(
    rows: List[LedgerRow],
    summary: Optional[StatementSummary] = None,
    *,
    balance_tolerance_minor: int = 0,
    suspicious_amount_minor: int = 10_000_000_00,
) -> LedgerValidationReport:
    issues: List[ValidationIssue] = []
    balance_checks = 0
    balance_checks_passed = 0
    total_debit = 0
    total_credit = 0
    debit_count = 0
    credit_count = 0
    previous_balance: Optional[int] = None

    for row in rows:
        has_debit = row.debit_minor is not None
        has_credit = row.credit_minor is not None

        if has_debit and has_credit:
            issues.append(ValidationIssue(
                "both_debit_credit",
                "Row has both debit and credit amounts.",
                row.index,
            ))

        if row.amount_minor is None:
            issues.append(ValidationIssue(
                "missing_amount",
                "Row does not have a usable transaction amount.",
                row.index,
            ))
        elif abs(row.amount_minor) > suspicious_amount_minor:
            issues.append(ValidationIssue(
                "suspicious_large_amount",
                "Transaction amount is unusually large.",
                row.index,
                severity="warning",
                actual_minor=row.amount_minor,
            ))

        if has_debit:
            debit_count += 1
            total_debit += abs(row.debit_minor or 0)
        elif row.amount_minor is not None and row.amount_minor < 0:
            debit_count += 1
            total_debit += abs(row.amount_minor)

        if has_credit:
            credit_count += 1
            total_credit += abs(row.credit_minor or 0)
        elif row.amount_minor is not None and row.amount_minor > 0:
            credit_count += 1
            total_credit += abs(row.amount_minor)

        if previous_balance is not None and row.balance_minor is not None and row.amount_minor is not None:
            balance_checks += 1
            expected_amount = row.balance_minor - previous_balance
            if abs(expected_amount - row.amount_minor) <= balance_tolerance_minor:
                balance_checks_passed += 1
            else:
                issues.append(ValidationIssue(
                    "balance_delta_mismatch",
                    "Transaction amount does not match the running balance delta.",
                    row.index,
                    expected_minor=expected_amount,
                    actual_minor=row.amount_minor,
                ))

        if row.balance_minor is not None:
            previous_balance = row.balance_minor

    report = LedgerValidationReport(
        row_count=len(rows),
        balance_checks=balance_checks,
        balance_checks_passed=balance_checks_passed,
        debit_count=debit_count,
        credit_count=credit_count,
        total_debit_minor=total_debit,
        total_credit_minor=total_credit,
        issues=issues,
    )

    if summary:
        _append_summary_issues(report, rows, summary)

    return report


def _append_summary_issues(
    report: LedgerValidationReport,
    rows: List[LedgerRow],
    summary: StatementSummary,
) -> None:
    comparisons = [
        (
            "total_debit_mismatch",
            "Total debit does not match statement summary.",
            summary.total_debit_minor,
            report.total_debit_minor,
        ),
        (
            "total_credit_mismatch",
            "Total credit does not match statement summary.",
            summary.total_credit_minor,
            report.total_credit_minor,
        ),
        (
            "debit_count_mismatch",
            "Debit count does not match statement summary.",
            summary.debit_count,
            report.debit_count,
        ),
        (
            "credit_count_mismatch",
            "Credit count does not match statement summary.",
            summary.credit_count,
            report.credit_count,
        ),
    ]
    for code, message, expected, actual in comparisons:
        if expected is not None and expected != actual:
            if "count" in code:
                message = f"{message} Expected {expected}, got {actual}."
            report.issues.append(ValidationIssue(
                code,
                message,
                expected_minor=expected if isinstance(expected, int) and "count" not in code else None,
                actual_minor=actual if isinstance(actual, int) and "count" not in code else None,
            ))

    first_balance = next((row.balance_minor for row in rows if row.balance_minor is not None), None)
    last_balance = next((row.balance_minor for row in reversed(rows) if row.balance_minor is not None), None)
    if summary.opening_balance_minor is not None and first_balance != summary.opening_balance_minor:
        report.issues.append(ValidationIssue(
            "opening_balance_mismatch",
            "First extracted balance does not match statement opening balance.",
            expected_minor=summary.opening_balance_minor,
            actual_minor=first_balance,
        ))
    if summary.closing_balance_minor is not None and last_balance != summary.closing_balance_minor:
        report.issues.append(ValidationIssue(
            "closing_balance_mismatch",
            "Last extracted balance does not match statement closing balance.",
            expected_minor=summary.closing_balance_minor,
            actual_minor=last_balance,
        ))
