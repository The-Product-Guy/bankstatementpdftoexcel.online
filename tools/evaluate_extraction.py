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


def _extract_amount_for_match(row: Dict[str, object]) -> Optional[float]:
    """
    Determine a single amount to match for accuracy scoring.
    Priority:
    1) Transaction_Amount
    2) -Withdrawal_Amount
    3) Deposit_Amount
    """
    tx = row.get("Transaction_Amount")
    if tx not in (None, ""):
        try:
            return float(str(tx).replace(",", ""))
        except (TypeError, ValueError):
            pass

    w = row.get("Withdrawal_Amount")
    if w not in (None, ""):
        try:
            return -abs(float(str(w).replace(",", "")))
        except (TypeError, ValueError):
            pass

    d = row.get("Deposit_Amount")
    if d not in (None, ""):
        try:
            return abs(float(str(d).replace(",", "")))
        except (TypeError, ValueError):
            pass

    return None


def score_accuracy(
    extracted: List[Dict[str, object]],
    truth: List[Dict[str, str]]
) -> Tuple[int, int, int, float]:
    """
    Match by normalized date + amount (tolerance 0.01).
    Returns (matches, extracted_count, truth_count, accuracy_pct).
    """
    matches = 0
    used_truth_indices = set()

    for tx in extracted:
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
            matches += 1
            used_truth_indices.add(matched_index)

    truth_count = len(truth)
    extracted_count = len(extracted)
    accuracy = (matches / truth_count * 100.0) if truth_count else 0.0
    return matches, extracted_count, truth_count, accuracy


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
            row["truth_file"] = truth_csv.name
            row["true_accuracy_pct"] = round(true_accuracy, 1)
            row["truth_rows"] = truth_count
            row["matched_rows"] = matches
            row["extracted_rows"] = extracted_count

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
