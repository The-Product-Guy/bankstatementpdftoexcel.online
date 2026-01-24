"""
KVB (Karur Vysya Bank) parser for image-based PDF statements.
Converts KVB bank statements to standardized transaction format using OCR.
"""

import re
from typing import List, Dict, Any
from pathlib import Path
from PIL import Image
import pytesseract
from pdf2image import convert_from_path
from .base_parser import BaseParser


class KVBParser(BaseParser):
    """Parser for Karur Vysya Bank statements (image-based PDF)."""
    
    def __init__(self, progress_callback=None):
        super().__init__(progress_callback)
        # More flexible date patterns for KVB statements
        self.date_re = re.compile(r"^\s*\d{1,2}[/-]\d{1,2}[/-](\d{2}|\d{4})\b")  # dd/mm/yy or dd-mm-yy or dd/mm/yyyy
        self.col_split = re.compile(r"\s{2,}")  # two or more spaces -> column break
        
        # Keywords that indicate summary/total blocks
        self.summary_hints = (
            "Opening Balance",
            "Total Credit Amount", 
            "Total Debit Amount",
            "Closing Balance",
            "Net Available Balance",
        )
        
        # Junk lines to filter out
        self.junk_prefixes = (
            "THE KARUR VYSYA BANK", "BRANCH:", "STATEMENT OF ACCOUNT", "INDIAN RUPEES",
            "Period from:", "Period To:", "Period From:", "Account Number",
            "Regd. Office", "Helpline No.", "IFSC Code:", "Branch Address:",
            "Phone:", "ACRONYMS", "****", "| TXN DT", "| Date |", "page:", "Page:"
        )
    
    def ocr_page(self, img: Image.Image) -> List[str]:
        """Run OCR on a PIL image and return lines, preserving spaces."""
        # Enhanced OCR configuration for better accuracy on bank statements
        config = "--psm 6 -c preserve_interword_spaces=1 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/-:.,()[] "
        text = pytesseract.image_to_string(img, config=config)
        lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
        return [ln for ln in lines if ln.strip()]
    
    def normalize_space_runs(self, s: str) -> str:
        """Reduce any run of 3+ spaces to exactly 2."""
        return re.sub(r"\s{3,}", "  ", s.strip())
    
    def looks_like_txn_row(self, ln: str) -> bool:
        """Check if line looks like a transaction row."""
        return bool(self.date_re.match(ln))
    
    def is_header_or_junk(self, ln: str) -> bool:
        """Check if line is header or junk that should be filtered."""
        return ln.strip().startswith(self.junk_prefixes)
    
    def should_keep_line_anyway(self, ln: str) -> bool:
        """Keep summary lines even if they don't look like txn rows."""
        return any(k in ln for k in self.summary_hints)
    
    def stitch_logical_rows(self, all_lines: List[str]) -> List[str]:
        """Combine wrapped lines into logical transaction rows."""
        logical_rows: List[str] = []
        cur = ""
        
        for raw in all_lines:
            if self.is_header_or_junk(raw) and not self.should_keep_line_anyway(raw):
                continue
                
            ln = self.normalize_space_runs(raw)
            
            if self.looks_like_txn_row(ln):
                if cur:
                    logical_rows.append(cur)
                cur = ln
            else:
                # continuation of current row (description/reference wrapping)
                if cur:
                    cur = cur + " " + ln.strip()
                else:
                    # Keep summary-like standalone lines
                    if self.should_keep_line_anyway(ln):
                        logical_rows.append(ln)
        
        if cur:
            logical_rows.append(cur)
            
        return logical_rows
    
    def extract_amounts_from_text(self, text: str) -> List[float]:
        """Extract all monetary amounts from text using regex."""
        # Pattern to match amounts like 10,000.00, 485.00, 1,36,941.00, -485.00
        amount_pattern = r'-?[\d,]+\.\d{2}'
        amounts = re.findall(amount_pattern, text)
        return [self.parse_amount(amt) for amt in amounts]
    
    def split_into_columns(self, row: str) -> Dict[str, str]:
        """Extract KVB columns using proper column positioning analysis."""
        # Initialize result dictionary
        result = {
            'TXN_DT': '',
            'VALUE_DT': '',
            'BRN': '',
            'DESCRIPTION': '',
            'REFERENCE': '',
            'DEBITS': '',
            'CREDITS': '',
            'BALANCE': ''
        }
        
        # Extract dates at the beginning (TXN_DT and VALUE_DT)
        date_match = re.match(r'^(\d{2}/\d{2}/\d{2})\s+(\d{2}/\d{2}/\d{2})', row)
        if date_match:
            result['TXN_DT'] = date_match.group(1)
            result['VALUE_DT'] = date_match.group(2)
            remaining_text = row[date_match.end():].strip()
        else:
            remaining_text = row.strip()
        
        # Extract BRN (branch code) - usually 4 digits after dates
        brn_match = re.match(r'^(\d{3,4})', remaining_text)
        if brn_match:
            result['BRN'] = brn_match.group(1)
            remaining_text = remaining_text[brn_match.end():].strip()
        
        # Extract all amounts with their positions in the original row
        amounts_with_pos = []
        for match in re.finditer(r'-?[\d,]+\.\d{2}', row):
            amount = self.parse_amount(match.group())
            amounts_with_pos.append((amount, match.start(), match.end(), match.group()))
        
        # KVB format has specific column positions:
        # DEBITS: around position 65-75
        # CREDITS: around position 80-90  
        # BALANCE: around position 95-105
        
        # Classify amounts by their position in the line
        for amount, start_pos, end_pos, amt_str in amounts_with_pos:
            if start_pos >= 95:  # Balance column (rightmost)
                result['BALANCE'] = str(amount)
            elif start_pos >= 80:  # Credits column (middle-right)
                result['CREDITS'] = str(amount)
            elif start_pos >= 65:  # Debits column (middle-left)
                result['DEBITS'] = str(amount)
            # If position is too far left, it might be part of reference or description
        
        # Handle special cases and validation
        if len(amounts_with_pos) == 2:
            # Common case: one transaction amount + balance
            # The rightmost amount is always the balance
            rightmost = max(amounts_with_pos, key=lambda x: x[1])
            result['BALANCE'] = str(rightmost[0])
            
            # The other amount is either debit or credit
            other_amounts = [a for a in amounts_with_pos if a != rightmost]
            if other_amounts:
                other_amount = other_amounts[0]
                
                # Determine if it's debit or credit by analyzing description and context
                desc_for_analysis = remaining_text.lower()
                
                # Check for explicit credit indicators first (higher priority)
                if ('cr-' in desc_for_analysis or 'credit' in desc_for_analysis or
                    'neft :' in desc_for_analysis or 'by clg' in desc_for_analysis or
                    'initial balance' in desc_for_analysis):
                    result['DEBITS'] = ''
                    result['CREDITS'] = str(other_amount[0])
                # Check for explicit debit indicators
                elif ('dr-' in desc_for_analysis or 'debit' in desc_for_analysis or 
                      'charges' in desc_for_analysis or 'fee' in desc_for_analysis or
                      'atm' in desc_for_analysis or 'to clg' in desc_for_analysis or
                      'mpay' in desc_for_analysis):
                    result['DEBITS'] = str(other_amount[0])
                    result['CREDITS'] = ''
                # Fall back to position-based logic
                elif other_amount[1] <= 75:  # Position suggests debit column
                    result['DEBITS'] = str(other_amount[0])
                    result['CREDITS'] = ''
                else:
                    result['DEBITS'] = ''
                    result['CREDITS'] = str(other_amount[0])
                    
        elif len(amounts_with_pos) == 1:
            # Single amount - likely balance only
            result['BALANCE'] = str(amounts_with_pos[0][0])
        
        # Remove all amounts from remaining text to get description
        desc_text = remaining_text
        for _, _, _, amt_str in amounts_with_pos:
            desc_text = desc_text.replace(amt_str, '', 1)
        
        # Extract reference numbers (long numeric sequences, account numbers)
        ref_matches = re.findall(r'\b\d{10,}\b', desc_text)
        if ref_matches:
            result['REFERENCE'] = ' '.join(ref_matches)
            # Remove reference numbers from description
            for ref in ref_matches:
                desc_text = desc_text.replace(ref, '').strip()
        
        # Clean up description
        result['DESCRIPTION'] = re.sub(r'\s+', ' ', desc_text).strip()
        
        return result
    
    def parse_amount(self, amount_str: str) -> float:
        """Parse amount string to float, handling KVB formatting."""
        if not amount_str or amount_str.strip() == '':
            return 0.0
        
        # Remove commas and extra spaces
        cleaned = amount_str.replace(',', '').strip()
        
        # Handle negative amounts and different formats
        try:
            if cleaned in ['0', '0.0', '0.00', '']:
                return 0.0
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
    
    def format_date(self, date_str: str) -> str:
        """Format KVB date string to standard format."""
        if not date_str or not date_str.strip():
            return ""
        
        # KVB dates are typically dd/mm/yy format
        # First column might have two dates separated by space
        date_parts = date_str.strip().split()
        if date_parts:
            first_date = date_parts[0]  # Use the first date
            try:
                # Try to parse and reformat if needed
                return self.parse_date(first_date, '%d/%m/%y')
            except:
                # If parsing fails, return the original first date
                return first_date
        
        return date_str.strip()
    
    def parse_transaction_row(self, row: str, filename: str, line_ref: str) -> Dict[str, Any]:
        """Parse a single transaction row into standardized format."""
        # Use new column extraction method
        cols = self.split_into_columns(row)
        
        # Convert to standard transaction format
        formatted_date = self.format_date(cols['TXN_DT'])
        
        # Parse amounts
        withdrawal_amt = self.parse_amount(cols['DEBITS']) if cols['DEBITS'] else 0
        deposit_amt = self.parse_amount(cols['CREDITS']) if cols['CREDITS'] else 0
        balance_amt = self.parse_amount(cols['BALANCE']) if cols['BALANCE'] else 0
        
        return self.create_transaction_dict(
            date=formatted_date,
            description=cols['DESCRIPTION'],
            reference=cols['REFERENCE'],
            withdrawal_amt=withdrawal_amt,
            deposit_amt=deposit_amt,
            balance_amt=balance_amt,
            source_file=filename,
            line_ref=line_ref
        )
    
    def get_optimal_dpi(self, pdf_path: str) -> int:
        """Determine optimal DPI based on file size and page count."""
        import os
        file_size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        
        # Smart DPI selection to balance quality and memory usage
        if file_size_mb <= 5:
            return 200  # High quality for small files
        elif file_size_mb <= 10:
            return 180  # Good quality for medium files
        elif file_size_mb <= 15:
            return 150  # Reasonable quality for larger files
        else:
            return 120  # Memory-conserving for very large files
    
    def parse(self, pdf_path: str, filename: str) -> List[Dict[str, Any]]:
        """Parse KVB PDF statement and return list of transactions."""
        try:
            # Get total page count and optimal DPI
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                total_pages = len(pdf.pages)
            
            optimal_dpi = self.get_optimal_dpi(pdf_path)
            print(f"Using {optimal_dpi} DPI for optimal quality/memory balance")
            
            all_lines: List[str] = []
            
            print(f"Processing {total_pages} pages (memory-optimized)...")
            
            # Process pages one by one to minimize memory usage
            for page_num in range(total_pages):
                print(f"Processing page {page_num + 1}/{total_pages} for KVB statement...")
                self.emit_progress(page_num + 1, total_pages, f"OCR processing page {page_num + 1} of {total_pages}")
                
                # Convert single page to image with optimal DPI
                images = convert_from_path(pdf_path, dpi=optimal_dpi, first_page=page_num + 1, last_page=page_num + 1)
                if images:
                    img = images[0]
                    
                    # Enhanced image processing for better OCR
                    gray = img.convert("L")
                    # Light enhancement without excessive memory usage
                    enhanced = gray.point(lambda x: 0 if x < 160 else 255, '1').convert('L')
                    
                    lines = self.ocr_page(enhanced)
                    all_lines.extend(lines)
                    
                    # Clear images from memory immediately
                    del images, img, gray, enhanced
                    
                    # Force garbage collection every 5 pages
                    if page_num % 5 == 4:
                        import gc
                        gc.collect()
            
            # Stitch wrapped lines into logical rows
            logical_rows = self.stitch_logical_rows(all_lines)
            
            transactions = []
            
            for i, row in enumerate(logical_rows, 1):
                # Check if row matches date pattern
                if self.date_re.match(row):
                    # Parse transaction using new method
                    try:
                        transaction = self.parse_transaction_row(row, filename, f"Row_{i}")
                        if transaction.get('Date'):  # Only add if we have a date
                            transactions.append(transaction)
                    except Exception as e:
                        # Skip problematic rows
                        continue
            
            print(f"Successfully extracted {len(transactions)} transactions from KVB statement")
            return transactions
            
        except Exception as e:
            print(f"Error processing KVB PDF: {str(e)}")
            raise Exception(f"Failed to process KVB statement: {str(e)}")