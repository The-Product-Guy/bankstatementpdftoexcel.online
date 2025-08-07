#!/usr/bin/env python3
"""
HDFC Bank Statement Parser
Handles image-based PDF statements using OCR
"""
import re
from .base_parser import BaseParser


class HDFCParser(BaseParser):
    """Parser for HDFC Bank statements"""
    
    def __init__(self):
        super().__init__()
        self.bank_name = "HDFC Bank"
    
    def parse(self, pdf_path, original_filename):
        """Parse HDFC bank statement using OCR"""
        self.validate_pdf_file(pdf_path)
        self.transactions = []
        
        print(f"  🏦 Processing {self.bank_name} statement...")
        print(f"  📄 Using OCR for image-based PDF")
        
        try:
            # Import OCR libraries
            from pdf2image import convert_from_path
            import pytesseract
            
            # Convert PDF pages to images
            print("  🖼️  Converting PDF pages to images...")
            images = convert_from_path(pdf_path, dpi=150)
            print(f"  ✅ Generated {len(images)} page images")
            
            # Extract text using OCR
            all_text = ""
            for i, image in enumerate(images, 1):
                print(f"    📝 OCR processing page {i}...")
                page_text = pytesseract.image_to_string(image)
                all_text += page_text + "\n"
            
            print(f"  📄 Extracted {len(all_text):,} characters of text")
            
            # Parse transactions from OCR text
            self._parse_hdfc_transactions(all_text, original_filename)
            
            print(f"  🎉 Successfully extracted {len(self.transactions)} transactions!")
            self.log_parsing_stats(len(all_text.splitlines()), len(self.transactions))
            
            return self.transactions
            
        except ImportError as e:
            raise ImportError(f"Missing required library for HDFC parsing: {e}")
        except Exception as e:
            raise Exception(f"Error processing HDFC statement: {str(e)}")
    
    def _parse_hdfc_transactions(self, text, source_file):
        """Parse HDFC transactions from OCR text"""
        lines = text.splitlines()
        print(f"  📝 Processing {len(lines)} lines...")
        
        for line_no, line in enumerate(lines, 1):
            line = line.strip()
            
            # Look for HDFC transaction lines (date + pipe pattern)
            if re.match(r'^\d{2}/\d{2}/\d{2}.*\|', line):
                # Skip header lines
                if any(word in line.upper() for word in ['DATE', 'NARRATION', 'WITHDRAWAL', 'DEPOSIT', 'BALANCE']):
                    continue
                
                transaction = self._parse_hdfc_transaction_line(line, source_file, line_no)
                if transaction:
                    self.transactions.append(transaction)
    
    def _parse_hdfc_transaction_line(self, line, source_file, line_no):
        """Parse individual HDFC transaction line"""
        try:
            parts = line.split('|')
            if len(parts) < 3:
                return None
            
            # Extract transaction date and description
            date_match = re.search(r'(\d{2}/\d{2}/\d{2})', parts[0])
            if not date_match:
                return None
                
            trans_date = date_match.group(1)
            description = parts[0].replace(trans_date, '').strip()
            reference = parts[1].strip()
            amounts_part = parts[2].strip()
            
            # Extract amounts from the amounts part
            amounts = re.findall(r'([\d,]+\.\d{2})', amounts_part)
            if not amounts:
                return None
                
            amounts_num = [self.clean_amount_string(a) for a in amounts]
            amounts_num = [a for a in amounts_num if a is not None]
            
            if not amounts_num:
                return None
            
            # Parse amounts based on HDFC format
            withdrawal_amt = None
            deposit_amt = None
            balance_amt = None
            
            if len(amounts_num) >= 3:
                # Standard format: withdrawal, deposit, balance
                withdrawal_amt = amounts_num[0] if amounts_num[0] > 0 else None
                deposit_amt = amounts_num[1] if amounts_num[1] > 0 else None
                balance_amt = amounts_num[2]
            elif len(amounts_num) == 2:
                # Determine transaction type from description
                desc_lower = description.lower()
                if any(word in desc_lower for word in ['nwd', 'withdrawal', 'atm', 'fee', 'dr', 'charge', 'paid']):
                    withdrawal_amt = amounts_num[0]
                    deposit_amt = None
                else:
                    withdrawal_amt = None
                    deposit_amt = amounts_num[0]
                balance_amt = amounts_num[1]
            else:
                # Only one amount - assume it's balance
                balance_amt = amounts_num[0]
            
            return self.create_transaction_dict(
                date=trans_date,
                description=description,
                reference=reference,
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