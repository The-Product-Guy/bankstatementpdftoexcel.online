#!/usr/bin/env python3
"""Generate synthetic bank-statement PDFs for parser validation and demos.

Every statement here is invented (DEMO BANK). Never commit a real statement.
Fixtures cover the US/UK/EU format families the marketing pages talk about:
distinct column sets, date formats, and amount conventions per family.
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfgen import canvas

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "data" / "synthetic"


def _statement(path, pagesize, bank_lines, headers, rows, title_font="Helvetica-Bold"):
    """headers/rows: list of (x, text). One page, ruled row pitch of 20pt."""
    c = canvas.Canvas(str(path), pagesize=pagesize)
    y = pagesize[1] - 60
    c.setFont(title_font, 12)
    for line in bank_lines:
        c.drawString(50, y, line)
        y -= 16
    y -= 10
    c.setFont("Helvetica-Bold", 9)
    for x, text in headers:
        c.drawString(x, y, text)
    y -= 20
    c.setFont("Helvetica", 9)
    for row in rows:
        for x, text in row:
            c.drawString(x, y, text)
        y -= 20
    c.save()
    return path


def us_checking(path=FIXTURE_DIR / "us-checking.pdf"):
    headers = [(50, "Date"), (120, "Description"), (330, "Withdrawals"), (420, "Deposits"), (500, "Balance")]
    data = [
        ("06/01/2026", "BEGINNING BALANCE", "", "", "8,410.22"),
        ("06/03/2026", "DIRECT DEPOSIT - EMPLOYER PAYROLL", "", "3,250.00", "11,660.22"),
        ("06/05/2026", "CARD PURCHASE - GROCERY STORE", "182.45", "", "11,477.77"),
        ("06/09/2026", "ACH PAYMENT - UTILITIES", "240.10", "", "11,237.67"),
        ("06/12/2026", "ATM WITHDRAWAL", "300.00", "", "10,937.67"),
        ("06/18/2026", "CHECK 1042", "1,150.00", "", "9,787.67"),
        ("06/24/2026", "CARD PURCHASE - GAS STATION", "58.90", "", "9,728.77"),
        ("06/30/2026", "INTEREST PAYMENT", "", "1.12", "9,729.89"),
    ]
    rows = [[(50, r[0]), (120, r[1])] + [(x, v) for x, v in ((330, r[2]), (420, r[3]), (500, r[4])) if v] for r in data]
    return _statement(path, LETTER, ["FIRST DEMO BANK N.A.", "Checking Statement 06/01/2026 - 06/30/2026"], headers, rows)


def us_card(path=FIXTURE_DIR / "us-card.pdf"):
    headers = [(60, "Date"), (140, "Description"), (450, "Amount")]
    data = [
        ("06/02", "ONLINE SUBSCRIPTION SERVICE", "-15.99"),
        ("06/04", "RESTAURANT - DOWNTOWN", "-64.20"),
        ("06/07", "AIRLINE TICKET", "-412.50"),
        ("06/11", "PAYMENT RECEIVED - THANK YOU", "2,100.00"),
        ("06/15", "GROCERY STORE", "-133.07"),
        ("06/19", "RIDESHARE", "-27.80"),
        ("06/23", "HARDWARE STORE", "-89.99"),
        ("06/28", "STREAMING SERVICE", "-12.99"),
    ]
    rows = [[(60, d), (140, desc), (450, amt)] for d, desc, amt in data]
    return _statement(path, LETTER, ["DEMO BANK CARD SERVICES", "Cardmember Statement - June 2026"], headers, rows)


def uk_current(path=FIXTURE_DIR / "uk-current.pdf"):
    headers = [(50, "Date"), (130, "Description"), (320, "Money out"), (410, "Money in"), (495, "Balance")]
    data = [
        ("1 Jun 2026", "BALANCE BROUGHT FORWARD", "", "", "£4,120.55"),
        ("3 Jun 2026", "FASTER PAYMENT - SALARY", "", "£2,480.00", "£6,600.55"),
        ("5 Jun 2026", "DIRECT DEBIT - COUNCIL TAX", "£165.00", "", "£6,435.55"),
        ("9 Jun 2026", "CARD PAYMENT - SUPERMARKET", "£82.14", "", "£6,353.41"),
        ("12 Jun 2026", "STANDING ORDER - RENT", "£1,234.56", "", "£5,118.85"),
        ("18 Jun 2026", "CARD PAYMENT - RAIL TICKETS", "£45.30", "", "£5,073.55"),
        ("24 Jun 2026", "DIRECT DEBIT - ENERGY", "£98.77", "", "£4,974.78"),
        ("30 Jun 2026", "INTEREST", "", "£0.84", "£4,975.62"),
    ]
    rows = [[(50, r[0]), (130, r[1])] + [(x, v) for x, v in ((320, r[2]), (410, r[3]), (495, r[4])) if v] for r in data]
    return _statement(path, A4, ["DEMO BANK UK PLC", "Current Account Statement 1 Jun - 30 Jun 2026"], headers, rows)


def eu_giro(path=FIXTURE_DIR / "eu-giro.pdf"):
    headers = [(50, "Datum"), (130, "Omschrijving"), (330, "Af"), (410, "Bij"), (490, "Saldo")]
    data = [
        ("01-06-2026", "BEGINSALDO", "", "", "6.410,22"),
        ("03-06-2026", "SALARIS WERKGEVER", "", "2.850,00", "9.260,22"),
        ("05-06-2026", "INCASSO ENERGIE", "112,40", "", "9.147,82"),
        ("09-06-2026", "PINBETALING SUPERMARKT", "94,18", "", "9.053,64"),
        ("12-06-2026", "HUUR JUNI", "1.234,56", "", "7.819,08"),
        ("18-06-2026", "OVERBOEKING SPAARREKENING", "500,00", "", "7.319,08"),
        ("24-06-2026", "PINBETALING TANKSTATION", "68,45", "", "7.250,63"),
        ("30-06-2026", "RENTE", "", "1,05", "7.251,68"),
    ]
    rows = [[(50, r[0]), (130, r[1])] + [(x, v) for x, v in ((330, r[2]), (410, r[3]), (490, r[4])) if v] for r in data]
    return _statement(path, A4, ["DEMO BANK EUROPE", "Rekeningafschrift 01-06-2026 t/m 30-06-2026"], headers, rows)


def demo_sample(path=REPO_ROOT / "static" / "sample-statement.pdf"):
    """Neutral Western demo shipped on the marketing site."""
    headers = [(50, "Date"), (120, "Description"), (330, "Withdrawals"), (420, "Deposits"), (500, "Balance")]
    data = [
        ("06/01/2026", "OPENING BALANCE", "", "", "12,500.00"),
        ("06/03/2026", "CARD PURCHASE - GROCERY", "450.00", "", "12,050.00"),
        ("06/07/2026", "DIRECT DEPOSIT - EMPLOYER", "", "5,500.00", "17,550.00"),
        ("06/12/2026", "ATM WITHDRAWAL", "500.00", "", "17,050.00"),
        ("06/18/2026", "ACH PAYMENT - UTILITIES", "124.00", "", "16,926.00"),
        ("06/25/2026", "INTEREST PAYMENT", "", "8.20", "16,934.20"),
    ]
    rows = [[(50, r[0]), (120, r[1])] + [(x, v) for x, v in ((330, r[2]), (420, r[3]), (500, r[4])) if v] for r in data]
    return _statement(path, LETTER, ["DEMO BANK", "Account Statement 06/01/2026 - 06/30/2026"], headers, rows)


def demo_sample_workbook(
    pdf_path=REPO_ROOT / "static" / "sample-statement.pdf",
    output_path=REPO_ROOT / "static" / "sample-statement.xlsx",
):
    """Regenerate the downloadable workbook with the production layout parser."""
    from parsers.layout_replica_parser import create_layout_replica_parser

    pdf_path = Path(pdf_path)
    output_path = Path(output_path)
    if not pdf_path.exists():
        demo_sample(pdf_path)

    parser = create_layout_replica_parser(use_ocr=False, use_paddleocr=False)
    parser.parse(str(pdf_path), pdf_path.name)
    parser.write_excel(str(output_path))
    return output_path


if __name__ == "__main__":
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for fn in (us_checking, us_card, uk_current, eu_giro):
        print("wrote", fn())
    print("wrote", demo_sample())
    print("wrote", demo_sample_workbook())
