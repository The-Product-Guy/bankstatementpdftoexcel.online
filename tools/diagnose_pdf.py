#!/usr/bin/env python3
"""
PDF Diagnostic Tool
Analyzes a PDF to determine why extraction might fail.
Checks for:
1. Text Content (Is it scanned?)
2. Layout Detection (Header rows)
3. Date/Transaction Patterns

Usage:
    python tools/diagnose_pdf.py --file path/to/failing.pdf
"""
import os
import sys
import argparse
import pdfplumber
import re
from datetime import datetime

# Add project root to path to import parsers if needed (not strict dependency)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def analyze_pdf(file_path):
    print(f"🔍 Analyzing: {file_path}")
    
    if not os.path.exists(file_path):
        print("❌ File not found.")
        return

    try:
        with pdfplumber.open(file_path) as pdf:
            print(f"📄 Pages: {len(pdf.pages)}")
            
            total_chars = 0
            all_text = ""
            
            # 1. Text Extraction Check (First 3 pages)
            for i, page in enumerate(pdf.pages[:3]):
                text = page.extract_text()
                chars = len(text) if text else 0
                total_chars += chars
                if text:
                    all_text += text + "\n"
                
                print(f"  • Page {i+1}: Found {chars} characters")
                
            if total_chars < 100:
                print("\n⚠️  [DIAGNOSIS]: LOW TEXT COUNT")
                print("   This likely means the PDF is a SCANNED IMAGE.")
                print("   The standard parser requires text. The tool will switch to OCR/Vision,")
                print("   but if that failed, it means OCR didn't work well or was disabled.")
                return

            # 2. Header Detection
            print("\n🔎 Checking for typical headers...")
            common_headers = ['date', 'description', 'reference', 'withdrawal', 'debit', 'deposit', 'credit', 'balance']
            
            lines = all_text.lower().split('\n')
            header_candidates = []
            
            for line in lines[:50]: # Check top 50 lines only
                found = [h for h in common_headers if h in line]
                if len(found) >= 3:
                    header_candidates.append((line, found))
            
            if header_candidates:
                print(f"✅ Found {len(header_candidates)} potential header rows:")
                for line, found in header_candidates:
                    print(f"   - '{line.strip()[:60]}...' matched {found}")
            else:
                print("⚠️  [DIAGNOSIS]: NO HEADERS FOUND")
                print("   Could not find standard bank headers (Date, Description, Debit, Credit).")
                print("   This might be a non-standard layout or different language.")

            # 3. Date Pattern Check
            print("\n📅 Checking for dates (Transaction candidates)...")
            # Common date formats: DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD, DD-MMM-YYYY
            date_patterns = [
                r'\d{2}/\d{2}/\d{4}',
                r'\d{2}-\d{2}-\d{4}',
                r'\d{4}-\d{2}-\d{2}',
                r'\d{2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}'
            ]
            
            dates_found = 0
            for line in lines:
                for pat in date_patterns:
                    if re.search(pat, line, re.IGNORECASE):
                        dates_found += 1
                        break
            
            print(f"   Found {dates_found} lines with recognizable dates.")
            
            if dates_found < 3:
                print("⚠️  [DIAGNOSIS]: FEW DATES FOUND")
                print("   The parser relies on finding dates to identify transaction rows.")
                print("   If dates are formatted strangely (e.g., 2023.01.25), key extraction will fail.")

            # Summary
            print("\n📋 SUMMARY Recommendation:")
            if total_chars < 100:
                print("   -> FORCE OCR (Use 'chaos' mode in tests to replicate). Check Tesseract/PaddleOCR installation.")
            elif not header_candidates:
                print("   -> UPDATE HEADER LIST in `universal_parser.py` or check for separate header columns.")
            elif dates_found < 3:
                print("   -> ADD DATE FORMAT to regex patterns.")
            else:
                print("   -> Structure looks okay... might be column alignment issues.")

    except Exception as e:
        print(f"❌ Error reading PDF: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Diagnose PDF extraction issues")
    parser.add_argument("--file", required=True, help="Path to PDF file")
    args = parser.parse_args()
    
    analyze_pdf(args.file)
