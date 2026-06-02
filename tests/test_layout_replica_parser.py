from pathlib import Path

from openpyxl import load_workbook
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def _make_layout_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 10)
    c.drawString(40, 742, "Example Bank")
    c.drawString(40, 724, "Statement Details")
    c.drawString(40, 690, "Date")
    c.drawString(120, 690, "Description")
    c.drawString(330, 690, "Debit")
    c.drawString(410, 690, "Credit")
    c.drawString(500, 690, "Balance")
    c.drawString(40, 670, "01/01/2026")
    c.drawString(120, 670, "OPENING BALANCE")
    c.drawString(500, 670, "1,234.50 CR")
    c.drawString(40, 650, "02/01/2026")
    c.drawString(120, 650, "CARD PAYMENT ABC-123")
    c.drawString(330, 650, "25.75")
    c.drawString(500, 650, "1,208.75 CR")
    c.save()


def test_layout_replica_parser_preserves_visible_lines_and_strings(tmp_path):
    from parsers.layout_replica_parser import LayoutReplicaParser

    pdf_path = tmp_path / "statement.pdf"
    _make_layout_pdf(pdf_path)

    parser = LayoutReplicaParser(use_ocr=False)
    parser.parse(str(pdf_path), "statement.pdf")

    assert parser.extraction_metadata.has_data is True
    assert parser.extraction_metadata.extraction_method == "layout_replica"
    assert parser.extraction_metadata.pdf_type == "text"
    assert parser.raw_table is not None
    assert [column.header for column in parser.table_columns] == [
        "Date",
        "Description",
        "Debit",
        "Credit",
        "Balance",
    ]
    assert parser.table_rows[1].values == [
        "02/01/2026",
        "CARD PAYMENT ABC-123",
        "25.75",
        "",
        "1,208.75 CR",
    ]

    raw_lines = [" ".join(row) for row in parser.raw_table["rows"]]
    assert any("Statement Details" in line for line in raw_lines)
    assert any("01/01/2026" in line and "OPENING BALANCE" in line for line in raw_lines)

    output_path = tmp_path / "replica.xlsx"
    parser.write_excel(str(output_path))

    wb = load_workbook(output_path)
    assert wb.sheetnames == ["sheet1"]

    table_sheet = wb["sheet1"]
    assert [table_sheet.cell(1, col).value for col in range(1, 6)] == [
        "Date",
        "Description",
        "Debit",
        "Credit",
        "Balance",
    ]
    assert [table_sheet.cell(3, col).value for col in range(1, 6)] == [
        "02/01/2026",
        "CARD PAYMENT ABC-123",
        "25.75",
        None,
        "1,208.75 CR",
    ]

    table_values = [
        cell.value
        for row in table_sheet.iter_rows()
        for cell in row
        if cell.value is not None
    ]
    assert "1,234.50 CR" in table_values
    assert "25.75" in table_values

    amount_cell = next(cell for row in table_sheet.iter_rows() for cell in row if cell.value == "25.75")
    assert amount_cell.number_format == "@"


def test_layout_replica_workbook_exports_only_table_sheet(tmp_path):
    from parsers.layout_replica_parser import LayoutReplicaParser

    pdf_path = tmp_path / "statement.pdf"
    _make_layout_pdf(pdf_path)

    parser = LayoutReplicaParser(use_ocr=False)
    parser.parse(str(pdf_path), "statement.pdf")
    output_path = tmp_path / "replica.xlsx"
    parser.write_excel(str(output_path))

    wb = load_workbook(output_path)
    assert wb.sheetnames == ["sheet1"]
    sheet = wb["sheet1"]
    assert sheet.max_column == 5
    assert sheet.max_row == 3


def test_table_replica_keeps_rows_before_repeated_header_on_same_pdf_page():
    from parsers.layout_replica_parser import LayoutReplicaParser

    pdf_path = (
        Path(__file__).parent
        / "data"
        / "india_v1"
        / "KVB-CA-PART-03- 01.04.2019 TO 31.03.2020.pdf"
    )

    parser = LayoutReplicaParser(use_ocr=False)
    parser.parse(str(pdf_path), pdf_path.name, page_start=1, page_end=2)

    page_two_rows = [
        row.values for row in parser.table_rows
        if row.page == 2
    ]
    assert [
        "13/04/19",
        "13/04/19",
        "1763",
        "MPAY/UPI/FI Funds Trans-1",
        "772108174541",
        "8,600.00",
        "",
        "42,557.10",
    ] in page_two_rows
    assert not any("STATEMENT OF ACCOUNT" in " ".join(row) for row in page_two_rows)
