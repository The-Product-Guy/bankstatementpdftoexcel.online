#!/usr/bin/env python3
"""
Benchmark Runner
Runs the Universal Parser against a directory of synthetic test data (PDF + Truth CSV).
Calculates accuracy metrics.

Usage:
    python tests/evaluation/run_benchmark.py --input_dir tests/data/synthetic_india
"""
import os
import sys
import csv
import argparse
import logging
import pandas as pd
from datetime import datetime

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from parsers.universal_parser import create_universal_parser

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

def load_ground_truth(csv_path):
    """Load truth CSV into list of dicts"""
    with open(csv_path, 'r') as f:
        return list(csv.DictReader(f))

def normalize_date(date_str):
    if not date_str: return ""
    # Attempt simple normalization for comparison
    date_str = str(date_str).strip()
    for fmt in ['%d-%m-%Y', '%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y']:
        try:
            return datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    return date_str

def smart_compare(extracted, truth):
    """
    Compare extracted transactions (list of dicts) with ground truth.
    Returns (matches, extractions_count, truth_count)
    """
    # Simple matching strategy: Match by roughly equal Amount AND Date
    # This is O(N*M) but fine for testing (N < 100)
    
    matches = 0
    used_truth_indices = set()
    
    for tx in extracted:
        tx_amt = str(tx.get('Transaction_Amount', '') or tx.get('Withdrawal_Amount', 0)).replace(',', '')
        tx_date = normalize_date(tx.get('Date', ''))
        
        # Try to find match in truth
        best_match_idx = -1
        
        for i, target in enumerate(truth):
            if i in used_truth_indices:
                continue
                
            # Check amount match (fuzzy float)
            t_amt = str(target.get('Transaction_Amount') or target.get('Withdrawal_Amount') or 0).replace(',', '')
            try:
                amt_match = abs(float(tx_amt) - float(t_amt)) < 0.01
            except:
                amt_match = False
                
            # Check date match
            t_date = normalize_date(target.get('Date', ''))
            date_match = tx_date == t_date
            
            if amt_match and date_match:
                best_match_idx = i
                break
        
        if best_match_idx != -1:
            matches += 1
            used_truth_indices.add(best_match_idx)
            
    return matches, len(extracted), len(truth)

def run_benchmark(input_dir, output_report="benchmark_report.md"):
    print(f"🚀 Running Benchmark on {input_dir}")
    print(f"   Using Universal Parser (Text + OCR + LLM)")
    
    # Init Parser
    parser = create_universal_parser(use_llm=False) # Start with non-LLM to test core logic first
    
    files = sorted([f for f in os.listdir(input_dir) if f.endswith('.pdf')])
    results = []
    
    print(f"   Found {len(files)} PDFs")
    
    for pdf_file in files:
        base_name = os.path.splitext(pdf_file)[0]
        # Look for truth file
        # It might be named differently depending on chaos mode, but usually base matches
        # e.g. stmt_001.pdf -> stmt_001_truth.csv
        # If chaos: stmt_001_scanned.pdf -> stmt_001_truth.csv
        
        truth_base = base_name.replace('_scanned', '')
        truth_file = os.path.join(input_dir, f"{truth_base}_truth.csv")
        
        if not os.path.exists(truth_file):
            print(f"⚠️  Skipping {pdf_file}: No truth CSV found ({truth_file})")
            continue
            
        print(f" PROCESSING: {pdf_file}...")
        try:
            pdf_path = os.path.join(input_dir, pdf_file)
            truth = load_ground_truth(truth_file)
            
            # Run Extraction
            extracted = parser.parse(pdf_path, pdf_file)
            
            # Compare
            matches, ext_count, truth_count = smart_compare(extracted, truth)
            
            accuracy = (matches / truth_count) * 100 if truth_count > 0 else 0
            
            print(f"   -> Found {ext_count}/{truth_count} txns. Accuracy: {accuracy:.1f}%")
            
            results.append({
                'File': pdf_file,
                'Accuracy': accuracy,
                'Extracted': ext_count,
                'Actual': truth_count
            })
            
        except Exception as e:
            print(f"   ❌ FAILED: {e}")
            results.append({'File': pdf_file, 'Accuracy': 0, 'Error': str(e)})

    # Generate Report
    with open(output_report, 'w') as f:
        f.write(f"# Benchmark Report: {os.path.basename(input_dir)}\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write("| File | Accuracy | Found | Actual | Status |\n")
        f.write("|---|---|---|---|---|\n")
        
        total_acc = 0
        for r in results:
            status = "✅" if r.get('Accuracy', 0) > 90 else "⚠️" if r.get('Accuracy', 0) > 50 else "❌"
            error = f" ({r['Error']})" if 'Error' in r else ""
            f.write(f"| {r['File']} | {r.get('Accuracy',0):.1f}% | {r.get('Extracted',0)} | {r.get('Actual',0)} | {status}{error} |\n")
            total_acc += r.get('Accuracy', 0)
            
        avg_acc = total_acc / len(results) if results else 0
        f.write(f"\n**Average Accuracy: {avg_acc:.1f}%**\n")
        
    print(f"\n✅ Benchmark Complete. Report saved to {output_report}")
    print(f"   Average Accuracy: {avg_acc:.1f}%")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", required=True)
    args = parser.parse_args()
    
    run_benchmark(args.input_dir)
