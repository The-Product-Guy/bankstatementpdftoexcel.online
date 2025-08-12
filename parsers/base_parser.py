#!/usr/bin/env python3
"""
Base Parser Class
Common utilities for all bank statement parsers
"""
import os
import re
from datetime import datetime
from abc import ABC, abstractmethod


class BaseParser(ABC):
    """Abstract base class for bank statement parsers"""
    
    def __init__(self, progress_callback=None):
        self.transactions = []
        self.bank_name = "Unknown Bank"
        self.progress_callback = progress_callback
    
    @abstractmethod
    def parse(self, pdf_path, original_filename):
        """Parse the PDF and return list of transactions"""
        pass
    
    def validate_pdf_file(self, pdf_path):
        """Validate if the PDF file exists and is readable"""
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        
        if not pdf_path.lower().endswith('.pdf'):
            raise ValueError("File must be a PDF")
        
        file_size = os.path.getsize(pdf_path)
        if file_size == 0:
            raise ValueError("PDF file is empty")
        
        return True
    
    def clean_amount_string(self, amount_str):
        """Clean and convert amount string to float"""
        if not amount_str or amount_str.strip() == '':
            return None
        
        # Remove commas and extra spaces
        cleaned = str(amount_str).replace(',', '').strip()
        
        # Handle different number formats
        try:
            # Check if it's zero
            if cleaned in ['0', '0.0', '0.00']:
                return None
            
            return float(cleaned)
        except (ValueError, TypeError):
            return None
    
    def parse_date(self, date_str, input_format='%d/%m/%Y'):
        """Parse date string and return standardized format"""
        try:
            date_obj = datetime.strptime(date_str, input_format)
            return date_obj.strftime('%d/%m/%y')  # Standard output format
        except (ValueError, TypeError):
            return date_str  # Return original if parsing fails
    
    def validate_transaction_line(self, line):
        """Basic validation for transaction lines"""
        if not line or len(line.strip()) < 10:
            return False
        
        # Check if line contains a date pattern
        date_patterns = [
            r'\d{2}/\d{2}/\d{4}',  # DD/MM/YYYY
            r'\d{2}/\d{2}/\d{2}',  # DD/MM/YY
            r'\d{1,2}-\d{1,2}-\d{4}',  # D-M-YYYY or DD-MM-YYYY
        ]
        
        for pattern in date_patterns:
            if re.search(pattern, line):
                return True
        
        return False
    
    def extract_amounts_from_text(self, text):
        """Extract monetary amounts from text"""
        # Pattern to match amounts (with or without commas)
        amount_pattern = r'[\d,]+\.\d{2}'
        amounts = re.findall(amount_pattern, text)
        
        return [self.clean_amount_string(amt) for amt in amounts if self.clean_amount_string(amt) is not None]
    
    def create_transaction_dict(self, date, description, reference, withdrawal_amt, deposit_amt, balance_amt, source_file, line_ref):
        """Create standardized transaction dictionary"""
        
        # Calculate net transaction amount
        net_amount = 0
        if deposit_amt and deposit_amt > 0:
            net_amount = deposit_amt
        elif withdrawal_amt and withdrawal_amt > 0:
            net_amount = -withdrawal_amt
        
        return {
            'Date': date,
            'Description': description.strip() if description else '',
            'Reference_Number': reference.strip() if reference else '',
            'Withdrawal_Amount': withdrawal_amt,
            'Deposit_Amount': deposit_amt,
            'Transaction_Amount': net_amount,
            'Closing_Balance': balance_amt,
            'Source_File': source_file,
            'Page_Line': str(line_ref)
        }
    
    def get_transaction_summary(self):
        """Get summary statistics for transactions"""
        if not self.transactions:
            return None
        
        total_count = len(self.transactions)
        deposits = [t for t in self.transactions if t.get('Transaction_Amount', 0) > 0]
        withdrawals = [t for t in self.transactions if t.get('Transaction_Amount', 0) < 0]
        
        deposits_total = sum(t.get('Transaction_Amount', 0) for t in deposits)
        withdrawals_total = abs(sum(t.get('Transaction_Amount', 0) for t in withdrawals))
        net_amount = deposits_total - withdrawals_total
        
        return {
            'total_transactions': total_count,
            'deposits_count': len(deposits),
            'deposits_total': deposits_total,
            'withdrawals_count': len(withdrawals),
            'withdrawals_total': withdrawals_total,
            'net_amount': net_amount
        }
    
    def log_parsing_stats(self, total_lines_processed, transactions_found):
        """Log parsing statistics"""
        print(f"  📊 Parsing Statistics:")
        print(f"    • Total lines processed: {total_lines_processed:,}")
        print(f"    • Transactions found: {transactions_found:,}")
        print(f"    • Success rate: {(transactions_found/total_lines_processed*100):.2f}%" if total_lines_processed > 0 else "    • Success rate: 0%")
    
    def emit_progress(self, current_page, total_pages, status="Processing"):
        """Emit progress update if callback is available"""
        if self.progress_callback:
            self.progress_callback({
                'current_page': current_page,
                'total_pages': total_pages,
                'status': status,
                'percentage': int((current_page / total_pages) * 100) if total_pages > 0 else 0
            })

    @staticmethod
    def detect_pdf_type(pdf_path):
        """Detect if PDF is text-based or image-based"""
        try:
            import pdfplumber
            
            with pdfplumber.open(pdf_path) as pdf:
                first_page = pdf.pages[0]
                text = first_page.extract_text()
                
                # If we can extract significant text, it's text-based
                return "text" if text and len(text.strip()) > 100 else "image"
        except Exception:
            return "image"  # Default to image if detection fails