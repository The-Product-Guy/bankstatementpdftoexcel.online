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
    from parsers.layout_replica_parser import LayoutLine, LayoutPage, LayoutReplicaParser, LayoutWord

    def make_line(page_num, index, words):
        layout_words = [
            LayoutWord(
                text=text,
                x0=x0,
                x1=x1,
                top=index * 10.0,
                bottom=index * 10.0 + 8.0,
                page=page_num,
                source="pdf-text",
            )
            for text, x0, x1 in words
        ]
        return LayoutLine(
            page=page_num,
            index=index,
            top=index * 10.0,
            bottom=index * 10.0 + 8.0,
            center_y=index * 10.0 + 4.0,
            words=layout_words,
            text=" ".join(word.text for word in layout_words),
        )

    def make_page(page_num, lines):
        return LayoutPage(
            page_number=page_num,
            width=620,
            height=800,
            source="pdf-text",
            words=[word for line in lines for word in line.words],
            lines=lines,
        )

    separator = [("-" * 100, 16.6, 597.2)]
    header = [
        ("TXN", 16.6, 32.4),
        ("DT", 37.7, 48.2),
        ("VALUE_DT", 64.1, 106.3),
        ("BRN", 116.9, 132.7),
        ("DESCRIPTION", 169.6, 227.7),
        ("REFERENCE", 280.5, 328.0),
        ("DEBITS", 380.8, 412.5),
        ("CREDITS", 454.7, 491.6),
        ("BALANCE", 560.3, 597.2),
    ]
    first_page_lines = [
        make_line(1, 1, separator),
        make_line(1, 2, header),
        make_line(1, 3, separator),
        make_line(1, 4, [
            ("01/04/19", 16.6, 58.8),
            ("01/04/19", 64.1, 106.3),
            ("1763", 111.6, 132.7),
            ("OPENING", 143.2, 183.0),
            ("BALANCE", 187.0, 227.0),
            ("1,00,000.00", 533.9, 591.9),
        ]),
    ]
    second_page_lines = [
        make_line(2, 1, [
            ("13/04/19", 16.6, 58.8),
            ("13/04/19", 64.1, 106.3),
            ("1763", 111.6, 132.7),
            ("MPAY/UPI/FI", 143.2, 206.0),
            ("Funds", 210.0, 240.0),
            ("Trans-1", 244.0, 275.2),
            ("772108174541", 280.5, 343.8),
            ("8,600.00", 375.5, 423.0),
            ("42,557.10", 533.9, 591.9),
        ]),
        make_line(2, 2, [("684155000075893", 143.2, 217.0)]),
        make_line(2, 3, separator),
        make_line(2, 4, [("page", 280.0, 302.0), (":", 306.0, 310.0), ("2", 316.0, 322.0)]),
        make_line(2, 5, [
            ("STATEMENT", 170.0, 220.0),
            ("OF", 225.0, 238.0),
            ("ACCOUNT", 243.0, 285.0),
        ]),
        make_line(2, 6, [
            ("Account", 280.0, 315.0),
            ("Number", 320.0, 355.0),
            ("1684135000012107", 360.0, 450.0),
        ]),
        make_line(2, 7, separator),
        make_line(2, 8, header),
        make_line(2, 9, separator),
        make_line(2, 10, [
            ("15/04/19", 16.6, 58.8),
            ("15/04/19", 64.1, 106.3),
            ("1763", 111.6, 132.7),
            ("ATM", 143.2, 162.0),
            ("Withdrawal", 166.0, 222.0),
            ("123456789012", 280.5, 343.8),
            ("2,000.00", 375.5, 423.0),
            ("40,557.10", 533.9, 591.9),
        ]),
    ]

    parser = LayoutReplicaParser(use_ocr=False)
    parser.pages = [
        make_page(1, first_page_lines),
        make_page(2, second_page_lines),
    ]
    parser._build_table_replica()

    page_two_rows = [
        row.values for row in parser.table_rows
        if row.page == 2
    ]
    assert [
        "13/04/19",
        "13/04/19",
        "1763",
        "MPAY/UPI/FI Funds Trans-1 684155000075893",
        "772108174541",
        "8,600.00",
        "",
        "42,557.10",
    ] in page_two_rows
    assert not any("STATEMENT OF ACCOUNT" in " ".join(row) for row in page_two_rows)
    assert not any("Account Number" in " ".join(row) for row in page_two_rows)
    # continuation line must no longer be its own row
    assert not any(row[0] == "" and "684155000075893" in " ".join(row) for row in page_two_rows)


def test_table_replica_infers_columns_when_header_is_missing():
    from parsers.layout_replica_parser import LayoutLine, LayoutPage, LayoutReplicaParser, LayoutWord

    def make_line(index, words):
        layout_words = [
            LayoutWord(
                text=text,
                x0=x0,
                x1=x1,
                top=index * 10.0,
                bottom=index * 10.0 + 8.0,
                page=1,
                source="ocr",
            )
            for text, x0, x1 in words
        ]
        return LayoutLine(
            page=1,
            index=index,
            top=index * 10.0,
            bottom=index * 10.0 + 8.0,
            center_y=index * 10.0 + 4.0,
            words=layout_words,
            text=" ".join(word.text for word in layout_words),
        )

    parser = LayoutReplicaParser(use_ocr=False)
    parser.pages = [
        LayoutPage(
            page_number=1,
            width=600,
            height=800,
            source="ocr",
            words=[],
            lines=[
                make_line(1, [("From", 5, 25), (":", 28, 32), ("01/04/2018", 40, 90)]),
                make_line(2, [
                    ("29/01/25", 6, 41),
                    ("UPI-SELVI", 47, 176),
                    ("0000024036557328", 266, 332),
                    ("29/01/25", 338, 370),
                    ("72.00", 426, 449),
                    ("0.00", 508, 526),
                    ("1,523.00", 570, 596),
                ]),
                make_line(3, [("PTY-YESB0MCHUPI-024036557328-PAYMENT", 47, 218)]),
                make_line(4, [("Closing balance includes funds earmarked for hold", 47, 300)]),
            ],
        )
    ]

    parser._build_table_replica()

    assert [column.header for column in parser.table_columns] == [
        "Column 1",
        "Column 2",
        "Column 3",
        "Column 4",
        "Column 5",
        "Column 6",
        "Column 7",
    ]
    assert parser.table_rows[0].values == [
        "29/01/25",
        "UPI-SELVI PTY-YESB0MCHUPI-024036557328-PAYMENT",
        "0000024036557328",
        "29/01/25",
        "72.00",
        "0.00",
        "1,523.00",
    ]
    assert len(parser.table_rows) == 1


def test_continuation_merges_into_description_and_reference_columns():
    from parsers.layout_replica_parser import LayoutLine, LayoutPage, LayoutReplicaParser, LayoutWord

    def make_line(page_num, index, words):
        layout_words = [
            LayoutWord(text=text, x0=x0, x1=x1, top=index * 10.0,
                       bottom=index * 10.0 + 8.0, page=page_num, source="pdf-text")
            for text, x0, x1 in words
        ]
        return LayoutLine(page=page_num, index=index, top=index * 10.0,
                          bottom=index * 10.0 + 8.0, center_y=index * 10.0 + 4.0,
                          words=layout_words,
                          text=" ".join(word.text for word in layout_words))

    separator = [("-" * 100, 16.6, 597.2)]
    header = [
        ("TXN", 16.6, 32.4), ("DT", 37.7, 48.2), ("VALUE_DT", 64.1, 106.3),
        ("BRN", 116.9, 132.7), ("DESCRIPTION", 169.6, 227.7),
        ("REFERENCE", 280.5, 328.0), ("DEBITS", 380.8, 412.5),
        ("CREDITS", 454.7, 491.6), ("BALANCE", 560.3, 597.2),
    ]
    lines = [
        make_line(1, 1, separator),
        make_line(1, 2, header),
        make_line(1, 3, separator),
        make_line(1, 4, [
            ("03/04/19", 16.6, 58.8), ("03/04/19", 64.1, 106.3), ("1763", 111.6, 132.7),
            ("IMPS", 143.2, 168.0), ("DR-1763308", 172.0, 227.0),
            ("909223247145", 280.5, 343.8), ("5,000.00", 375.5, 423.0),
            ("1,66,685.70", 533.9, 591.9),
        ]),
        # wraps into DESCRIPTION and REFERENCE columns, no date, no amounts
        make_line(1, 5, [("HDFC0000240-3017FA", 143.2, 230.0), ("835", 280.5, 300.0)]),
    ]
    parser = LayoutReplicaParser(use_ocr=False)
    parser.pages = [LayoutPage(page_number=1, width=620, height=800, source="pdf-text",
                               words=[w for line in lines for w in line.words], lines=lines)]
    parser._build_table_replica()

    assert len(parser.table_rows) == 1
    # 8 columns: "TXN" and "DT" merge into one header cell, so
    # idx 3 = DESCRIPTION, idx 4 = REFERENCE, idx 5 = DEBITS
    row = parser.table_rows[0].values
    assert row[3] == "IMPS DR-1763308 HDFC0000240-3017FA"
    assert row[4] == "909223247145 835"
    assert row[5] == "5,000.00"


def test_date_starting_line_is_never_merged_as_continuation():
    from parsers.layout_replica_parser import LayoutLine, LayoutPage, LayoutReplicaParser, LayoutWord

    def make_line(page_num, index, words):
        layout_words = [
            LayoutWord(text=text, x0=x0, x1=x1, top=index * 10.0,
                       bottom=index * 10.0 + 8.0, page=page_num, source="pdf-text")
            for text, x0, x1 in words
        ]
        return LayoutLine(page=page_num, index=index, top=index * 10.0,
                          bottom=index * 10.0 + 8.0, center_y=index * 10.0 + 4.0,
                          words=layout_words,
                          text=" ".join(word.text for word in layout_words))

    separator = [("-" * 100, 16.6, 597.2)]
    header = [
        ("TXN", 16.6, 32.4), ("DT", 37.7, 48.2), ("VALUE_DT", 64.1, 106.3),
        ("BRN", 116.9, 132.7), ("DESCRIPTION", 169.6, 227.7),
        ("REFERENCE", 280.5, 328.0), ("DEBITS", 380.8, 412.5),
        ("CREDITS", 454.7, 491.6), ("BALANCE", 560.3, 597.2),
    ]
    lines = [
        make_line(1, 1, separator),
        make_line(1, 2, header),
        make_line(1, 3, separator),
        make_line(1, 4, [
            ("01/04/19", 16.6, 58.8), ("01/04/19", 64.1, 106.3), ("1763", 111.6, 132.7),
            ("ATM", 143.2, 162.0), ("CSW", 166.0, 190.0),
            ("909110624882", 280.5, 343.8), ("10,000.00", 375.5, 423.0),
            ("1,43,265.70", 533.9, 591.9),
        ]),
        make_line(1, 5, [
            ("01/04/19", 16.6, 58.8), ("01/04/19", 64.1, 106.3), ("1763", 111.6, 132.7),
            ("CA", 143.2, 155.0), ("ATM", 159.0, 178.0), ("TXN", 182.0, 200.0),
            ("909110624882", 280.5, 343.8), ("20.00", 375.5, 423.0),
            ("1,43,245.70", 533.9, 591.9),
        ]),
    ]
    parser = LayoutReplicaParser(use_ocr=False)
    parser.pages = [LayoutPage(page_number=1, width=620, height=800, source="pdf-text",
                               words=[w for line in lines for w in line.words], lines=lines)]
    parser._build_table_replica()

    assert len(parser.table_rows) == 2
    assert parser.table_rows[0].values[5] == "10,000.00"
    assert parser.table_rows[1].values[5] == "20.00"
