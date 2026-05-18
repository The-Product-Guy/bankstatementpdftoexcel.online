#!/usr/bin/env python3
"""Validate a generated bank-statement workbook against ledger rules.

This tool is intended for local/private benchmark PDFs. It does not need truth
CSV labels; it uses exact balance continuity and optional statement summary
figures from the source PDF.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parsers.ledger_validation import (  # noqa: E402
    StatementSummary,
    ledger_rows_from_transactions,
    validate_ledger_rows,
)
from parsers.ledger_repair import repair_transactions_from_balance_deltas  # noqa: E402
from parsers.statement_summary import extract_statement_summary_from_pdf  # noqa: E402


def load_transactions_from_workbook(path: Path) -> List[Dict[str, Any]]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheet = (
        workbook["Normalized_Transactions"]
        if "Normalized_Transactions" in workbook.sheetnames
        else workbook[workbook.sheetnames[0]]
    )
    headers = [sheet.cell(1, col).value for col in range(1, sheet.max_column + 1)]
    rows: List[Dict[str, Any]] = []
    for row_index in range(2, sheet.max_row + 1):
        row = {
            str(headers[col - 1]): sheet.cell(row_index, col).value
            for col in range(1, sheet.max_column + 1)
            if headers[col - 1]
        }
        if any(value not in (None, "") for value in row.values()):
            row["_workbook_row"] = row_index
            rows.append(row)
    workbook.close()
    return rows


def extract_statement_summary(pdf_path: Optional[Path], sample_pages: int) -> StatementSummary:
    if not pdf_path:
        return StatementSummary()
    return extract_statement_summary_from_pdf(pdf_path, sample_pages=sample_pages, use_ocr=True)


def summary_to_dict(summary: StatementSummary) -> Dict[str, Any]:
    from parsers.ledger_validation import format_minor

    return {
        "opening_balance": format_minor(summary.opening_balance_minor),
        "closing_balance": format_minor(summary.closing_balance_minor),
        "total_debit": format_minor(summary.total_debit_minor),
        "total_credit": format_minor(summary.total_credit_minor),
        "debit_count": summary.debit_count,
        "credit_count": summary.credit_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated statement workbook")
    parser.add_argument("workbook", type=Path, help="Generated .xlsx workbook")
    parser.add_argument("--pdf", type=Path, help="Source PDF for summary extraction")
    parser.add_argument("--sample-pages", type=int, default=2, help="Pages to sample from start and end of PDF")
    parser.add_argument("--issue-samples", type=int, default=20, help="Maximum issues to print")
    parser.add_argument("--repair", action="store_true", help="Apply deterministic balance-delta repairs before validation")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    transactions = load_transactions_from_workbook(args.workbook)
    summary = extract_statement_summary(args.pdf, args.sample_pages) if args.pdf else StatementSummary()
    repair_report = None
    if args.repair:
        transactions, repair_report = repair_transactions_from_balance_deltas(transactions, summary)
    report = validate_ledger_rows(ledger_rows_from_transactions(transactions), summary)

    payload = {
        "workbook": str(args.workbook),
        "pdf": str(args.pdf) if args.pdf else None,
        "statement_summary": summary_to_dict(summary),
        "repairs": repair_report.repaired_count if repair_report else 0,
        "validation": report.to_dict(),
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Workbook: {args.workbook}")
        if args.pdf:
            print(f"PDF: {args.pdf}")
        print("Statement summary:")
        for key, value in payload["statement_summary"].items():
            print(f"  {key}: {value}")
        validation = payload["validation"]
        print("Validation:")
        if repair_report:
            print(f"  repairs applied: {repair_report.repaired_count}")
        print(f"  rows: {validation['row_count']}")
        print(f"  balance checks: {validation['balance_checks_passed']}/{validation['balance_checks']}")
        print(f"  balance consistency: {validation['balance_consistency_pct']}%")
        print(f"  debits: count={validation['debit_count']} total={validation['total_debit']}")
        print(f"  credits: count={validation['credit_count']} total={validation['total_credit']}")
        print(f"  valid: {validation['is_valid']}")
        issues = validation["issues"]
        if issues:
            print(f"Issues ({len(issues)} total, showing {min(len(issues), args.issue_samples)}):")
            for issue in issues[: args.issue_samples]:
                row = f" row={issue['row_index']}" if issue.get("row_index") else ""
                expected = f" expected={issue['expected']}" if issue.get("expected") else ""
                actual = f" actual={issue['actual']}" if issue.get("actual") else ""
                print(f"  - {issue['code']}{row}:{expected}{actual} {issue['message']}")

    return 0 if report.is_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
