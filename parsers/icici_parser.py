#!/usr/bin/env python3
"""
ICICI Bank Statement Parser
Handles text-based PDF statements using direct text extraction
"""
import re
from .base_parser import BaseParser


class ICICIParser(BaseParser):
    """Parser for ICICI Bank statements"""
    
    def __init__(self, progress_callback=None):
        super().__init__(progress_callback)
        self.bank_name = "ICICI Bank"
    
    def parse(self, pdf_path, original_filename):
        """Parse ICICI bank statement using text extraction"""
        self.validate_pdf_file(pdf_path)
        self.transactions = []
        
        print(f"  🏦 Processing {self.bank_name} statement...")
        
        # Determine PDF type
        pdf_type = self.detect_pdf_type(pdf_path)
        print(f"  📄 Detected {pdf_type}-based PDF")
        
        if pdf_type == "text":
            self._parse_text_based_pdf(pdf_path, original_filename)
        else:
            self._parse_image_based_pdf(pdf_path, original_filename)
        
        print(f"  🎉 Successfully extracted {len(self.transactions)} transactions!")
        return self.transactions
    
    def _parse_text_based_pdf(self, pdf_path, source_file):
        """Parse text-based ICICI PDF using direct text extraction"""
        try:
            import pdfplumber
            
            print("  📄 Extracting text directly from PDF...")
            
            with pdfplumber.open(pdf_path) as pdf:
                all_text = ""
                total_pages = len(pdf.pages)
                
                for page_num, page in enumerate(pdf.pages, 1):
                    print(f"    📝 Processing page {page_num}...")
                    self.emit_progress(page_num, total_pages, f"Extracting text from page {page_num} of {total_pages}")
                    page_text = page.extract_text()
                    if page_text:
                        all_text += page_text + "\n"
                
                print(f"  📄 Extracted {len(all_text):,} characters of text")
                
                # Parse transactions from extracted text
                self._parse_icici_transactions(all_text, source_file)
                self.log_parsing_stats(len(all_text.splitlines()), len(self.transactions))
                
        except ImportError:
            raise ImportError("PDFPlumber not available. Install with: pip install pdfplumber")
        except Exception as e:
            raise Exception(f"Error in text extraction: {str(e)}")
    
    def _parse_image_based_pdf(self, pdf_path, source_file):
        """Parse image-based ICICI PDF using OCR"""
        try:
            from pdf2image import convert_from_path
            import pytesseract
            
            print("  🖼️  Using OCR for image-based PDF...")
            
            images = convert_from_path(pdf_path, dpi=150)
            print(f"  ✅ Generated {len(images)} page images")
            
            all_text = ""
            total_pages = len(images)
            
            for i, image in enumerate(images, 1):
                print(f"    📝 OCR processing page {i}...")
                self.emit_progress(i, total_pages, f"OCR processing page {i} of {total_pages}")
                page_text = pytesseract.image_to_string(image)
                all_text += page_text + "\n"
            
            print(f"  📄 Extracted {len(all_text):,} characters of text")
            
            # Parse transactions from OCR text
            self._parse_icici_transactions(all_text, source_file)
            self.log_parsing_stats(len(all_text.splitlines()), len(self.transactions))
            
        except ImportError as e:
            raise ImportError(f"Missing library for OCR processing: {e}")
        except Exception as e:
            raise Exception(f"Error in OCR processing: {str(e)}")
    
    def _parse_icici_transactions(self, text, source_file):
        """Parse ICICI transactions from text"""
        lines = text.splitlines()
        print(f"  📝 Processing {len(lines)} lines...")
        
        for line_no, line in enumerate(lines, 1):
            line = line.strip()
            
            # Try multiple patterns for ICICI transactions
            transaction = None
            
            # Pattern 1: Serial number + two dates + description + amounts
            # Example: "1 23/07/2025 23/07/2025 UPI/LAKSHMI BH/... 115.00 0.00 724.09"
            pattern1 = r'^(\d{1,3})\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(.*?)\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)$'
            match1 = re.match(pattern1, line)
            
            if match1:
                transaction = self._parse_icici_pattern1(match1, source_file, line_no)
            else:
                # Pattern 2: Just dates + description + amounts (no serial number)
                # Example: "23/07/2025 23/07/2025 UPI/LAKSHMI BH/... 115.00 0.00 724.09"
                pattern2 = r'^(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(.*?)\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)$'
                match2 = re.match(pattern2, line)
                
                if match2:
                    transaction = self._parse_icici_pattern2(match2, source_file, line_no)
            
            if transaction:
                self.transactions.append(transaction)
    
    def _parse_icici_pattern1(self, match, source_file, line_no):
        """Parse ICICI pattern with serial number"""
        try:
            s_no = match.group(1)
            value_date = match.group(2)
            trans_date = match.group(3)
            description = match.group(4).strip()
            withdrawal_str = match.group(5).replace(',', '')
            deposit_str = match.group(6).replace(',', '')
            balance_str = match.group(7).replace(',', '')
            
            # Convert amounts
            withdrawal_amt = self.clean_amount_string(withdrawal_str)
            deposit_amt = self.clean_amount_string(deposit_str)
            balance_amt = self.clean_amount_string(balance_str)
            
            # Skip zero amounts (ICICI shows 0.00 for non-applicable columns)
            if withdrawal_amt == 0:
                withdrawal_amt = None
            if deposit_amt == 0:
                deposit_amt = None
            
            # Convert date to standard format
            formatted_date = self.parse_date(value_date, '%d/%m/%Y')
            
            return self.create_transaction_dict(
                date=formatted_date,
                description=description,
                reference=s_no,
                withdrawal_amt=withdrawal_amt,
                deposit_amt=deposit_amt,
                balance_amt=balance_amt,
                source_file=source_file,
                line_ref=line_no
            )
            
        except Exception:
            return None
    
    def _parse_icici_pattern2(self, match, source_file, line_no):
        """Parse ICICI pattern without serial number"""
        try:
            value_date = match.group(1)
            trans_date = match.group(2)
            description = match.group(3).strip()
            withdrawal_str = match.group(4).replace(',', '')
            deposit_str = match.group(5).replace(',', '')
            balance_str = match.group(6).replace(',', '')
            
            # Convert amounts
            withdrawal_amt = self.clean_amount_string(withdrawal_str)
            deposit_amt = self.clean_amount_string(deposit_str)
            balance_amt = self.clean_amount_string(balance_str)
            
            # Skip zero amounts
            if withdrawal_amt == 0:
                withdrawal_amt = None
            if deposit_amt == 0:
                deposit_amt = None
            
            # Convert date to standard format
            formatted_date = self.parse_date(value_date, '%d/%m/%Y')
            
            return self.create_transaction_dict(
                date=formatted_date,
                description=description,
                reference="",
                withdrawal_amt=withdrawal_amt,
                deposit_amt=deposit_amt,
                balance_amt=balance_amt,
                source_file=source_file,
                line_ref=line_no
            )
            
        except Exception:
            return None
    
    def get_sample_transactions(self, count=3):
        """Get sample transactions for display"""
        if not self.transactions:
            return []
        
        samples = []
        for i, tx in enumerate(self.transactions[:count], 1):
            amt = tx.get('Transaction_Amount', 0)
            amt_str = f"₹{amt:,.2f}" if amt else "₹0.00"
            desc = tx.get('Description', '')[:30] + "..." if len(tx.get('Description', '')) > 30 else tx.get('Description', '')
            samples.append(f"{i}. {tx.get('Date')} | {desc} | {amt_str}")
        
        return samples