#!/usr/bin/env python3
"""
Evaluate extraction quality file-by-file.

For each PDF:
- If *_truth.csv exists, report true accuracy (%) against ground truth.
- Always report proxy quality metrics from parser internal validation.

Usage:
    python tools/evaluate_extraction.py --input-dir tests/data/india_v1
    python tools/evaluate_extraction.py --input-dir tests/data/synthetic_india --disable-paddle
"""
import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parsers.universal_parser import create_universal_parser  # noqa: E402


def load_truth(csv_path: Path) -> List[Dict[str, str]]:
    with csv_path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def normalize_date(date_str: str) -> str:
    if not date_str:
        return ""
    value = str(date_str).strip()
    date_formats = [
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%y",
    ]
    for fmt in date_formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value


def normalize_text(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _get_first_value(row: Dict[str, object], aliases: Tuple[str, ...]) -> object:
    for alias in aliases:
        value = row.get(alias)
        if value not in (None, ""):
            return value
    return ""


def _parse_amount(value: object) -> Optional[float]:
    if value in (None, ""):
        return None

    text = str(value).strip()
    if not text:
        return None

    negative = False
    upper = text.upper()
    if upper.endswith("DR"):
        negative = True
        text = text[:-2]
    elif upper.endswith("CR"):
        text = text[:-2]

    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    cleaned = (
        text.replace(",", "")
        .replace("₹", "")
        .replace("$", "")
        .replace("£", "")
        .replace("€", "")
        .strip()
    )
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return -abs(amount) if negative else amount


def _extract_amount_for_match(row: Dict[str, object]) -> Optional[float]:
    """
    Determine a single amount to match for accuracy scoring.
    Priority:
    1) Transaction_Amount
    2) -Withdrawal_Amount
    3) Deposit_Amount
    """
    tx = _get_first_value(row, ("Transaction_Amount", "Amount"))
    tx_amount = _parse_amount(tx)
    if tx_amount is not None:
        return tx_amount

    withdrawal = _get_first_value(row, ("Withdrawal_Amount", "Debit", "Debit_Amount"))
    withdrawal_amount = _parse_amount(withdrawal)
    if withdrawal_amount is not None:
        return -abs(withdrawal_amount)

    deposit = _get_first_value(row, ("Deposit_Amount", "Credit", "Credit_Amount"))
    deposit_amount = _parse_amount(deposit)
    if deposit_amount is not None:
        return abs(deposit_amount)

    return None


def match_rows(
    extracted: List[Dict[str, object]],
    truth: List[Dict[str, str]]
) -> List[Tuple[int, int]]:
    """
    Match rows by normalized date + amount (tolerance 0.01).
    Returns pairs of (extracted_index, truth_index).
    """
    matches: List[Tuple[int, int]] = []
    used_truth_indices = set()

    for extracted_index, tx in enumerate(extracted):
        tx_amt = _extract_amount_for_match(tx)
        tx_date = normalize_date(str(tx.get("Date", "")))
        if tx_amt is None or not tx_date:
            continue

        matched_index = -1
        for i, target in enumerate(truth):
            if i in used_truth_indices:
                continue

            truth_amt = _extract_amount_for_match(target)
            truth_date = normalize_date(str(target.get("Date", "")))
            if truth_amt is None:
                continue

            if abs(tx_amt - truth_amt) < 0.01 and tx_date == truth_date:
                matched_index = i
                break

        if matched_index != -1:
            matches.append((extracted_index, matched_index))
            used_truth_indices.add(matched_index)

    return matches


def score_accuracy(
    extracted: List[Dict[str, object]],
    truth: List[Dict[str, str]]
) -> Tuple[int, int, int, float]:
    """
    Match by normalized date + amount (tolerance 0.01).
    Returns (matches, extracted_count, truth_count, accuracy_pct).
    """
    matches = match_rows(extracted, truth)
    truth_count = len(truth)
    extracted_count = len(extracted)
    accuracy = (len(matches) / truth_count * 100.0) if truth_count else 0.0
    return len(matches), extracted_count, truth_count, accuracy


FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "date": ("Date", "date"),
    "description": ("Description", "description", "Narration", "Transaction_Details"),
    "withdrawal": ("Withdrawal_Amount", "Debit", "Debit_Amount"),
    "deposit": ("Deposit_Amount", "Credit", "Credit_Amount"),
    "balance": ("Balance", "Closing_Balance", "Running_Balance"),
}


def _field_matches(field: str, extracted_value: object, truth_value: object) -> bool:
    if field == "date":
        return normalize_date(str(extracted_value)) == normalize_date(str(truth_value))
    if field in {"withdrawal", "deposit", "balance"}:
        left = _parse_amount(extracted_value)
        right = _parse_amount(truth_value)
        return left is not None and right is not None and abs(left - right) < 0.01
    return normalize_text(extracted_value) == normalize_text(truth_value)


def score_field_accuracy(
    extracted: List[Dict[str, object]],
    truth: List[Dict[str, str]]
) -> Dict[str, object]:
    """
    Score matched rows field-by-field against ground truth.
    Fields absent from the truth row are excluded from the denominator.
    """
    row_matches = match_rows(extracted, truth)
    total_matched = 0
    total_expected = 0
    metrics: Dict[str, object] = {
        "field_accuracy_pct": 0.0,
        "field_matched": 0,
        "field_expected": 0,
    }

    for field, aliases in FIELD_ALIASES.items():
        matched = 0
        expected = 0
        for extracted_index, truth_index in row_matches:
            truth_value = _get_first_value(truth[truth_index], aliases)
            if truth_value in (None, ""):
                continue
            extracted_value = _get_first_value(extracted[extracted_index], aliases)
            expected += 1
            if _field_matches(field, extracted_value, truth_value):
                matched += 1

        metrics[f"{field}_accuracy_pct"] = round((matched / expected * 100.0) if expected else 0.0, 1)
        metrics[f"{field}_matched"] = matched
        metrics[f"{field}_expected"] = expected
        total_matched += matched
        total_expected += expected

    metrics["field_matched"] = total_matched
    metrics["field_expected"] = total_expected
    metrics["field_accuracy_pct"] = round(
        (total_matched / total_expected * 100.0) if total_expected else 0.0,
        1,
    )
    return metrics


def find_truth_csv(pdf_path: Path) -> Optional[Path]:
    stem = pdf_path.stem
    candidates = [
        pdf_path.with_name(f"{stem}_truth.csv"),
        pdf_path.with_name(f"{stem.replace('_scanned', '')}_truth.csv"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def get_total_pages(pdf_path: Path) -> Optional[int]:
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


def evaluate_file(
    pdf_path: Path,
    execution_preset: Optional[str],
    use_paddleocr: bool,
    use_img2table: bool,
    use_llm: bool,
    dpi: int,
    max_pages: Optional[int]
) -> Dict[str, object]:
    start = time.time()
    page_end: Optional[int] = None
    total_pages_hint = get_total_pages(pdf_path)
    if max_pages and max_pages > 0:
        if total_pages_hint and total_pages_hint > 0:
            page_end = min(total_pages_hint, max_pages)
        else:
            page_end = max_pages

    parser = create_universal_parser(
        execution_preset=execution_preset,
        use_paddleocr=use_paddleocr,
        use_img2table=use_img2table,
        use_llm=use_llm,
        dpi=dpi,
    )

    try:
        extracted = parser.parse(
            str(pdf_path),
            pdf_path.name,
            page_start=1,
            page_end=page_end,
        )
        quality = parser.get_quality_report() if hasattr(parser, "get_quality_report") else {}
        elapsed = time.time() - start
        total_pages = parser.stats.total_pages if parser.stats else total_pages_hint
        pages_processed = parser.stats.pages_processed if parser.stats else (
            page_end if page_end else total_pages_hint
        )

        row = {
            "file": pdf_path.name,
            "status": "ok",
            "profile_key": parser.active_profile.key if parser.active_profile else "",
            "profile_name": parser.active_profile.display_name if parser.active_profile else "unknown",
            "execution_preset": getattr(parser.config, "execution_preset", "custom"),
            "pages": total_pages,
            "pages_processed": pages_processed,
            "extracted_rows": len(extracted),
            "proxy_accuracy_pct": quality.get("accuracy_proxy_pct", 0.0),
            "balance_consistency_pct": quality.get("balance_consistency_pct", 0.0),
            "date_parse_pct": quality.get("date_parse_pct", 0.0),
            "amount_coverage_pct": quality.get("amount_coverage_pct", 0.0),
            "duration_sec": round(elapsed, 2),
            "truth_file": "",
            "true_accuracy_pct": "",
            "truth_rows": "",
            "matched_rows": "",
        }

        truth_csv = find_truth_csv(pdf_path)
        if truth_csv:
            truth = load_truth(truth_csv)
            matches, extracted_count, truth_count, true_accuracy = score_accuracy(extracted, truth)
            field_metrics = score_field_accuracy(extracted, truth)
            row["truth_file"] = truth_csv.name
            row["true_accuracy_pct"] = round(true_accuracy, 1)
            row["truth_rows"] = truth_count
            row["matched_rows"] = matches
            row["extracted_rows"] = extracted_count
            row.update(field_metrics)

        return row
    except Exception as exc:
        elapsed = time.time() - start
        return {
            "file": pdf_path.name,
            "status": "error",
            "profile_key": "",
            "profile_name": "unknown",
            "execution_preset": execution_preset or "custom",
            "pages": "",
            "pages_processed": "",
            "extracted_rows": "",
            "proxy_accuracy_pct": "",
            "balance_consistency_pct": "",
            "date_parse_pct": "",
            "amount_coverage_pct": "",
            "duration_sec": round(elapsed, 2),
            "truth_file": "",
            "true_accuracy_pct": "",
            "truth_rows": "",
            "matched_rows": "",
            "field_accuracy_pct": "",
            "field_matched": "",
            "field_expected": "",
            "error": str(exc),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, help="Directory containing PDFs")
    parser.add_argument("--use-llm", action="store_true", help="Enable LLM extraction")
    parser.add_argument("--disable-paddle", action="store_true", help="Disable PaddleOCR")
    parser.add_argument("--disable-img2table", action="store_true", help="Disable img2table path for scanned docs")
    parser.add_argument(
        "--execution-preset",
        default=None,
        help="Execution preset: local-low-mem | prod-balanced | prod-high-accuracy",
    )
    parser.add_argument("--dpi", type=int, default=150, help="OCR DPI")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Process only the first N pages per file (sampling mode)",
    )
    parser.add_argument(
        "--output",
        default="extraction_accuracy_report.csv",
        help="Output CSV report path",
    )
    parser.add_argument(
        "--min-true-accuracy",
        type=float,
        default=None,
        help="Fail with exit code 2 if average row-match accuracy is below this percent",
    )
    parser.add_argument(
        "--min-field-accuracy",
        type=float,
        default=None,
        help="Fail with exit code 2 if average field accuracy is below this percent",
    )
    args = parser.parse_args()

    use_paddleocr = not args.disable_paddle
    use_img2table = not args.disable_img2table

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}")
        return 1

    pdf_files = sorted(input_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {input_dir}")
        return 1

    print(f"Evaluating {len(pdf_files)} files from {input_dir}")
    rows: List[Dict[str, object]] = []
    for pdf_file in pdf_files:
        print(f"\nProcessing: {pdf_file.name}")
        row = evaluate_file(
            pdf_path=pdf_file,
            execution_preset=args.execution_preset,
            use_paddleocr=use_paddleocr,
            use_img2table=use_img2table,
            use_llm=args.use_llm,
            dpi=args.dpi,
            max_pages=args.max_pages,
        )
        rows.append(row)

        if row["status"] == "ok":
            true_acc = row.get("true_accuracy_pct", "")
            true_acc_disp = f"{true_acc}%" if true_acc != "" else "N/A (no truth)"
            print(
                f"  -> profile={row.get('profile_name', 'unknown')} | "
                f"preset={row.get('execution_preset', 'custom')} | "
                f"proxy={row.get('proxy_accuracy_pct', 0.0)}% | "
                f"true={true_acc_disp} | rows={row.get('extracted_rows', 0)} | "
                f"time={row.get('duration_sec', 0)}s"
            )
        else:
            print(f"  -> error: {row.get('error', 'unknown')}")

    fieldnames = [
        "file",
        "status",
        "profile_key",
        "profile_name",
        "execution_preset",
        "pages",
        "pages_processed",
        "extracted_rows",
        "proxy_accuracy_pct",
        "balance_consistency_pct",
        "date_parse_pct",
        "amount_coverage_pct",
        "truth_file",
        "true_accuracy_pct",
            "truth_rows",
            "matched_rows",
            "field_accuracy_pct",
            "field_matched",
            "field_expected",
            "date_accuracy_pct",
            "date_matched",
            "date_expected",
            "description_accuracy_pct",
            "description_matched",
            "description_expected",
            "withdrawal_accuracy_pct",
            "withdrawal_matched",
            "withdrawal_expected",
            "deposit_accuracy_pct",
            "deposit_matched",
            "deposit_expected",
            "balance_accuracy_pct",
            "balance_matched",
            "balance_expected",
            "duration_sec",
            "error",
        ]
    output_path = Path(args.output)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"\nSaved report to {output_path}")
    truth_rows = [
        row for row in rows
        if isinstance(row.get("true_accuracy_pct"), (int, float))
    ]
    if truth_rows:
        avg_true_accuracy = sum(float(row["true_accuracy_pct"]) for row in truth_rows) / len(truth_rows)
        field_rows = [
            row for row in truth_rows
            if isinstance(row.get("field_accuracy_pct"), (int, float))
        ]
        avg_field_accuracy = (
            sum(float(row["field_accuracy_pct"]) for row in field_rows) / len(field_rows)
            if field_rows
            else 0.0
        )
        print(f"Average row-match accuracy: {avg_true_accuracy:.1f}%")
        print(f"Average field accuracy: {avg_field_accuracy:.1f}%")

        if args.min_true_accuracy is not None and avg_true_accuracy < args.min_true_accuracy:
            print(
                f"Row-match accuracy {avg_true_accuracy:.1f}% is below threshold "
                f"{args.min_true_accuracy:.1f}%"
            )
            return 2
        if args.min_field_accuracy is not None and avg_field_accuracy < args.min_field_accuracy:
            print(
                f"Field accuracy {avg_field_accuracy:.1f}% is below threshold "
                f"{args.min_field_accuracy:.1f}%"
            )
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
