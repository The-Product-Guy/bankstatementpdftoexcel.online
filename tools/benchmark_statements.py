#!/usr/bin/env python3
"""
Benchmark PDF-to-Excel extraction for latency and accuracy proxies.
"""
import argparse
import json
import os
import time
import re
from typing import Dict, Any, List, Optional

import pandas as pd

from parsers.universal_parser import create_universal_parser


def find_pdfs(root: str) -> List[str]:
    pdfs = []
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.lower().endswith(".pdf"):
                pdfs.append(os.path.join(dirpath, filename))
    return sorted(pdfs)


def normalize_text(value: str) -> str:
    if value is None:
        return ""
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "", value)
    return value


def parse_amount(value: Any) -> Optional[float]:
    if value is None or value == "" or value == "null" or value == "-":
        return None
    if isinstance(value, (int, float)):
        return float(value) if value != 0 else None
    if isinstance(value, str):
        cleaned = re.sub(r"[₹$€£,\s]", "", value)
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        try:
            amount = float(cleaned)
            return amount if amount != 0 else None
        except ValueError:
            return None
    return None


def normalize_transactions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for row in rows:
        tx = dict(row)
        tx["Withdrawal_Amount"] = parse_amount(tx.get("Withdrawal_Amount"))
        tx["Deposit_Amount"] = parse_amount(tx.get("Deposit_Amount"))
        tx["Closing_Balance"] = parse_amount(tx.get("Closing_Balance"))
        tx["Transaction_Amount"] = parse_amount(tx.get("Transaction_Amount"))
        if tx.get("Transaction_Amount") is None:
            if tx.get("Deposit_Amount"):
                tx["Transaction_Amount"] = tx["Deposit_Amount"]
            elif tx.get("Withdrawal_Amount"):
                tx["Transaction_Amount"] = -tx["Withdrawal_Amount"]
            else:
                tx["Transaction_Amount"] = None
        normalized.append(tx)
    return normalized


def load_expected(pdf_path: str) -> Optional[pd.DataFrame]:
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    base_dir = os.path.dirname(pdf_path)
    candidates = [
        os.path.join(base_dir, f"{base}.expected.csv"),
        os.path.join(base_dir, "expected", f"{base}.csv"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return pd.read_csv(path)
    return None


def remap_expected(df: pd.DataFrame) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []

    col_map = {}
    for col in df.columns:
        c = col.lower().strip()
        if c in {"date", "txn date", "transaction date", "value date"}:
            col_map[col] = "Date"
        elif c in {"description", "narration", "particulars", "remarks", "details"}:
            col_map[col] = "Description"
        elif c in {"reference", "ref", "ref no", "cheque", "chq"}:
            col_map[col] = "Reference_Number"
        elif c in {"debit", "withdrawal", "withdrawal_amount", "dr"}:
            col_map[col] = "Withdrawal_Amount"
        elif c in {"credit", "deposit", "deposit_amount", "cr"}:
            col_map[col] = "Deposit_Amount"
        elif c in {"balance", "closing balance"}:
            col_map[col] = "Closing_Balance"

    remapped = df.rename(columns=col_map).to_dict(orient="records")
    return normalize_transactions(remapped)


def build_keys(rows: List[Dict[str, Any]], mode: str) -> set:
    keys = set()
    for row in rows:
        date = str(row.get("Date", "")).strip()
        desc = normalize_text(str(row.get("Description", "")))
        withdrawal = row.get("Withdrawal_Amount")
        deposit = row.get("Deposit_Amount")
        balance = row.get("Closing_Balance")
        txn_amt = row.get("Transaction_Amount")

        if mode == "strict":
            keys.add((date, desc, withdrawal, deposit, balance))
        else:
            keys.add((date, txn_amt, balance))
    return keys


def compute_quality_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    if total == 0:
        return {
            "total_transactions": 0,
            "missing_date": 0,
            "missing_amount": 0,
            "amount_parse_rate": 0.0,
            "balance_consistency_rate": None,
        }

    missing_date = sum(1 for r in rows if not r.get("Date"))
    missing_amount = sum(
        1 for r in rows
        if not r.get("Withdrawal_Amount") and not r.get("Deposit_Amount") and not r.get("Transaction_Amount")
    )
    amount_parse_rate = round((total - missing_amount) / total, 4)

    checks = 0
    consistent = 0
    for i in range(1, total):
        prev = rows[i - 1]
        cur = rows[i]
        prev_bal = prev.get("Closing_Balance")
        cur_bal = cur.get("Closing_Balance")
        txn_amt = cur.get("Transaction_Amount")
        if prev_bal is None or cur_bal is None or txn_amt is None:
            continue
        checks += 1
        if abs((prev_bal + txn_amt) - cur_bal) <= 0.01:
            consistent += 1

    balance_rate = round(consistent / checks, 4) if checks > 0 else None

    return {
        "total_transactions": total,
        "missing_date": missing_date,
        "missing_amount": missing_amount,
        "amount_parse_rate": amount_parse_rate,
        "balance_consistency_rate": balance_rate,
    }


def run_benchmark(pdf_path: str, use_llm: bool, prefer_vision: bool) -> Dict[str, Any]:
    stage_times: Dict[str, Dict[str, float]] = {}

    def progress_callback(data: Dict[str, Any]):
        stage = data.get("stage") or "unknown"
        now = time.perf_counter()
        if stage not in stage_times:
            stage_times[stage] = {"start": now, "end": now}
        else:
            stage_times[stage]["end"] = now

    parser = create_universal_parser(
        progress_callback=progress_callback,
        use_llm=use_llm,
        prefer_vision=prefer_vision,
        llm_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        max_pages=None,
        use_table_structure=True,
        min_table_transactions=int(os.environ.get("MIN_TABLE_TRANSACTIONS", "5"))
    )

    start = time.perf_counter()
    transactions = parser.parse(pdf_path, os.path.basename(pdf_path))
    total_seconds = round(time.perf_counter() - start, 3)

    stage_durations = {
        stage: round(data["end"] - data["start"], 3)
        for stage, data in stage_times.items()
    }

    normalized = normalize_transactions(transactions)
    metrics = compute_quality_metrics(normalized)

    stats = parser.get_processing_stats()
    stats_dict = {
        "pages": stats.pages_processed if stats else None,
        "ocr_method": stats.ocr_method if stats else None,
        "llm_tokens": stats.llm_tokens_used if stats else None,
        "estimated_cost": stats.estimated_cost if stats else None,
    }

    expected_df = load_expected(pdf_path)
    accuracy = {}
    if expected_df is not None:
        expected_rows = remap_expected(expected_df)
        if expected_rows:
            pred_strict = build_keys(normalized, "strict")
            exp_strict = build_keys(expected_rows, "strict")
            pred_loose = build_keys(normalized, "loose")
            exp_loose = build_keys(expected_rows, "loose")

            strict_hits = len(pred_strict & exp_strict)
            loose_hits = len(pred_loose & exp_loose)

            accuracy = {
                "strict_precision": round(strict_hits / len(pred_strict), 4) if pred_strict else None,
                "strict_recall": round(strict_hits / len(exp_strict), 4) if exp_strict else None,
                "loose_precision": round(loose_hits / len(pred_loose), 4) if pred_loose else None,
                "loose_recall": round(loose_hits / len(exp_loose), 4) if exp_loose else None,
                "expected_rows": len(expected_rows),
            }

    return {
        "file": os.path.basename(pdf_path),
        "path": pdf_path,
        "total_seconds": total_seconds,
        "stage_seconds": stage_durations,
        "metrics": metrics,
        "stats": stats_dict,
        "accuracy": accuracy,
        "transactions": normalized,
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark statement parsing")
    parser.add_argument("--input", default="test_files", help="Input folder containing PDFs")
    parser.add_argument("--output", default="processed/benchmarks", help="Output folder for results")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM usage")
    parser.add_argument("--prefer-vision", action="store_true", help="Use LLM vision for image PDFs")
    parser.add_argument("--save-output", action="store_true", help="Save per-file CSV outputs")
    args = parser.parse_args()

    pdfs = find_pdfs(args.input)
    if not pdfs:
        raise SystemExit(f"No PDFs found under {args.input}")

    os.makedirs(args.output, exist_ok=True)

    results = []
    for pdf_path in pdfs:
        result = run_benchmark(
            pdf_path=pdf_path,
            use_llm=not args.no_llm,
            prefer_vision=args.prefer_vision
        )
        results.append(result)

        if args.save_output:
            csv_path = os.path.join(
                args.output,
                f"{os.path.splitext(os.path.basename(pdf_path))[0]}.csv"
            )
            pd.DataFrame(result["transactions"]).to_csv(csv_path, index=False)

    json_path = os.path.join(args.output, "benchmark_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    flat_rows = []
    for r in results:
        row = {
            "file": r["file"],
            "total_seconds": r["total_seconds"],
            "transactions": r["metrics"]["total_transactions"],
            "amount_parse_rate": r["metrics"]["amount_parse_rate"],
            "balance_consistency_rate": r["metrics"]["balance_consistency_rate"],
            "llm_tokens": r["stats"]["llm_tokens"],
            "estimated_cost": r["stats"]["estimated_cost"],
        }
        for stage, secs in r["stage_seconds"].items():
            row[f"stage_{stage}_seconds"] = secs
        if r["accuracy"]:
            row.update(r["accuracy"])
        flat_rows.append(row)

    pd.DataFrame(flat_rows).to_csv(
        os.path.join(args.output, "benchmark_results.csv"),
        index=False
    )


if __name__ == "__main__":
    main()
