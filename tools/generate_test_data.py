#!/usr/bin/env python3
"""
Synthetic Bank Statement Generator
Generates random bank statements in various regional formats (US, India, Canada, etc.)
and can optionally simulate "scanned" PDFs using image noise and rotation.

Usage:
    python generate_test_data.py --count 5 --region india --chaos_level 0 --output_dir tests/data/india_clean
    python generate_test_data.py --count 5 --region canada --chaos_level 2 --output_dir tests/data/canada_noisy
"""
import os
import argparse
import random
import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from faker import Faker
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from pdf2image import convert_from_path
from PIL import Image, ImageEnhance, ImageOps
import numpy as np

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class Transaction:
    date: str
    description: str
    ref_no: str
    withdrawal: float
    deposit: float
    balance: float

    def to_dict(self):
        return {
            'Date': self.date,
            'Description': self.description,
            'Reference_Number': self.ref_no,
            'Withdrawal_Amount': self.withdrawal,
            'Deposit_Amount': self.deposit,
            'Closing_Balance': self.balance
        }

class BankStatementGenerator:
    def __init__(self, region='us', seed=None):
        self.region = region.lower()
        self.fake = Faker()
        if self.region == 'india':
            self.fake = Faker('en_IN')
        elif self.region == 'uk':
            self.fake = Faker('en_GB')
        if seed is not None:
            self.fake.seed_instance(seed)
        
    def _format_date(self, date_obj):
        if self.region == 'us':
            return date_obj.strftime('%m/%d/%Y')
        elif self.region == 'india':
            return date_obj.strftime('%d-%m-%Y')
        elif self.region == 'canada':
            return date_obj.strftime('%Y-%m-%d')
        elif self.region == 'uk':
            return date_obj.strftime('%d/%m/%Y')
        return date_obj.strftime('%Y-%m-%d')

    def _format_currency(self, amount):
        if amount == 0:
            return ""
        
        if self.region == 'india':
            # Indian formatting 1,23,456.00
            s, *d = str(amount).partition(".")
            r = ",".join([s[x-2:x] for x in range(-3, -len(s), -2)][::-1] + [s[-3:]])
            return "".join([r] + d) if d else r
        elif self.region == 'uk':
            return f"{amount:,.2f}"
        
        return f"{amount:,.2f}"

    def generate_transactions(self, count=20, start_balance=5000.0):
        transactions = []
        current_balance = start_balance
        start_date = datetime.now() - timedelta(days=count*2)
        
        for i in range(count):
            day_offset = int(i * 1.5)
            tx_date = start_date + timedelta(days=day_offset)
            
            is_deposit = random.random() > 0.7
            amount = round(random.uniform(10.0, 2000.0), 2)
            
            withdrawal = 0
            deposit = 0
            
            if is_deposit:
                deposit = amount
                current_balance += amount
                if self.region == 'india':
                    desc = f"NEFT CR-{self.fake.company()}-{self.fake.bs()}"
                else:
                    desc = f"Deposit from {self.fake.company()}"
            else:
                withdrawal = amount
                current_balance -= amount
                if self.region == 'india':
                    desc = f"UPI-DEBIT-{self.fake.first_name()}-{self.fake.bothify(text='??#####')}"
                else:
                    desc = self.fake.company()
            
            # Add some randomness to descriptions
            if random.random() > 0.8:
                desc += f"\nREF: {self.fake.bothify(text='???####')}"
                
            tx = Transaction(
                date=self._format_date(tx_date),
                description=desc,
                ref_no=self.fake.bothify(text='REF#######'),
                withdrawal=withdrawal,
                deposit=deposit,
                balance=current_balance
            )
            transactions.append(tx)
            
        return transactions

    def create_pdf(self, transactions, filepath, bank_name="Global Bank"):
        doc = SimpleDocTemplate(filepath, pagesize=A4)
        elements = []
        styles = getSampleStyleSheet()
        
        # Header
        elements.append(Paragraph(f"<b>{bank_name}</b>", styles['Title']))
        elements.append(Paragraph(f"Account Statement - {self.region.upper()}", styles['Heading2']))
        elements.append(Spacer(1, 20))
        
        # Table Data
        data = []
        # Columns based on region
        if self.region == 'india':
            headers = ['Txn Date', 'Value Date', 'Description', 'Ref No.', 'Debit', 'Credit', 'Balance']
            col_widths = [60, 60, 150, 60, 60, 60, 70]
        elif self.region == 'canada':
            headers = ['Date', 'Description', 'Debits (-)', 'Credits (+)', 'Balance']
            col_widths = [70, 200, 70, 70, 80]
        else:
            headers = ['Date', 'Description', 'Reference', 'Withdrawals', 'Deposits', 'Balance']
            col_widths = [60, 160, 70, 70, 70, 70]
            
        data.append(headers)
        
        for tx in transactions:
            row = []
            if self.region == 'india':
                row = [
                    tx.date, 
                    tx.date, # Value date same as txn date for simplicity
                    tx.description,
                    tx.ref_no,
                    self._format_currency(tx.withdrawal),
                    self._format_currency(tx.deposit),
                    self._format_currency(tx.balance)
                ]
            elif self.region == 'canada':
                # Canadian banks often put withdrawals as negative in one column or separate
                # We'll use separate columns for clarity but different header style
                row = [
                    tx.date,
                    tx.description,
                    self._format_currency(tx.withdrawal),
                    self._format_currency(tx.deposit),
                    self._format_currency(tx.balance)
                ]
            else:
                row = [
                    tx.date,
                    tx.description,
                    tx.ref_no,
                    self._format_currency(tx.withdrawal),
                    self._format_currency(tx.deposit),
                    self._format_currency(tx.balance)
                ]
            data.append(row)
            
        table = Table(data, colWidths=col_widths)
        
        # Style
        style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),  # Default alignment
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ])
        
        # Align numbers to right
        if self.region == 'india':
             style.add('ALIGN', (4, 1), (6, -1), 'RIGHT')
        elif self.region == 'canada':
             style.add('ALIGN', (2, 1), (4, -1), 'RIGHT')
        else:
             style.add('ALIGN', (3, 1), (5, -1), 'RIGHT')
             
        table.setStyle(style)
        elements.append(table)
        
        doc.build(elements)
        return filepath

    def apply_chaos(self, pdf_path, chaos_level=1):
        """
        Converts PDF to images, adds noise/rotation, and saves back as PDF.
        chaos_level: 0 (none), 1 (light), 2 (heavy)
        """
        if chaos_level == 0:
            return pdf_path
            
        logger.info(f"Applying Chaos Level {chaos_level} to {pdf_path}")
        
        try:
            images = convert_from_path(pdf_path)
            processed_images = []
            
            for img in images:
                # 1. Rotation (Skew)
                angle = random.uniform(-1.5, 1.5) * chaos_level
                img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor='white')
                
                # 2. Add Noise/Blur
                if chaos_level >= 1:
                    # Resize down and up to simulate low DPI scan
                    w, h = img.size
                    img = img.resize((int(w/1.5), int(h/1.5)), resample=Image.BILINEAR)
                    img = img.resize((w, h), resample=Image.NEAREST)
                    
                    # Convert to grayscale
                    img = ImageOps.grayscale(img)
                    
                if chaos_level >= 2:
                    # Add salt and pepper noise
                    img_array = np.array(img)
                    noise = np.random.randint(0, 255, img_array.shape, dtype='uint8')
                    # Mix noise with image
                    mask = np.random.rand(*img_array.shape) < 0.05 # 5% opacity noise
                    img_array[mask] = noise[mask]
                    img = Image.fromarray(img_array)
                    
                processed_images.append(img)
            
            # Save back as PDF
            output_path = pdf_path.replace(".pdf", "_scanned.pdf")
            processed_images[0].save(
                output_path, "PDF", resolution=100.0, save_all=True, append_images=processed_images[1:]
            )
            return output_path
            
        except Exception as e:
            logger.error(f"Chaos application failed: {e}")
            logger.warning("Make sure poppler is installed! (brew install poppler)")
            return pdf_path

def main():
    parser = argparse.ArgumentParser(description="Generate synthetic bank statements")
    parser.add_argument("--count", type=int, default=1, help="Number of files to generate")
    parser.add_argument("--region", type=str, default="us", choices=['us', 'india', 'canada', 'uk'], help="Region format")
    parser.add_argument("--chaos_level", type=int, default=0, help="0=Clean, 1=Scanned, 2=Messy")
    parser.add_argument("--output_dir", type=str, default="tests/data/synthetic")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible fixture generation")
    parser.add_argument("--min_transactions", type=int, default=10, help="Minimum transactions per statement")
    parser.add_argument("--max_transactions", type=int, default=50, help="Maximum transactions per statement")
    
    args = parser.parse_args()

    if args.min_transactions <= 0:
        parser.error("--min_transactions must be greater than zero")
    if args.max_transactions < args.min_transactions:
        parser.error("--max_transactions must be greater than or equal to --min_transactions")

    if args.seed is not None:
        random.seed(args.seed)
        Faker.seed(args.seed)
        np.random.seed(args.seed)
    
    os.makedirs(args.output_dir, exist_ok=True)
    
    generator = BankStatementGenerator(region=args.region, seed=args.seed)
    
    for i in range(args.count):
        filename_base = f"stmt_{args.region}_{i+1:03d}"
        pdf_path = os.path.join(args.output_dir, f"{filename_base}.pdf")
        csv_path = os.path.join(args.output_dir, f"{filename_base}_truth.csv")
        
        # Generate Data
        txs = generator.generate_transactions(
            count=random.randint(args.min_transactions, args.max_transactions)
        )
        
        # Save Ground Truth CSV
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=txs[0].to_dict().keys())
            writer.writeheader()
            for tx in txs:
                writer.writerow(tx.to_dict())
                
        # Generate PDF
        generator.create_pdf(txs, pdf_path, bank_name=f"{args.region.upper()} National Bank")
        
        # Apply Chaos (Scan Simulation)
        if args.chaos_level > 0:
            final_pdf = generator.apply_chaos(pdf_path, chaos_level=args.chaos_level)
            # If chaos succeeded, remove the clean one
            if final_pdf != pdf_path:
                os.remove(pdf_path)
                logger.info(f"Generated {final_pdf} (Scanned)")
        else:
            logger.info(f"Generated {pdf_path} (Clean)")
            
    print(f"✅ Generated {args.count} {args.region.upper()} statements in {args.output_dir}")

from dataclasses import dataclass

if __name__ == "__main__":
    main()
