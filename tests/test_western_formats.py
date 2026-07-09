#!/usr/bin/env python3
"""US/UK/EU statement format families must survive the layout replica parser.

Fixtures are synthetic (tools/make_synthetic_statements.py). Each family
varies column sets, date formats, and amount conventions. These tests back
the /convert/<bank> marketing claims: no page ships for a format family the
parser can't replicate.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

FIXTURES = Path(__file__).parent / "data" / "synthetic"

CASES = [
    ("us-checking.pdf", ["Date", "Description", "Withdrawals", "Deposits", "Balance"], "06/03/2026", "3,250.00", 3),
    ("us-card.pdf", ["Date", "Description", "Amount"], "06/11", "2,100.00", 2),
    ("uk-current.pdf", ["Date", "Description", "Money out", "Money in", "Balance"], "3 Jun 2026", "£2,480.00", 3),
    ("eu-giro.pdf", ["Datum", "Omschrijving", "Af", "Bij", "Saldo"], "03-06-2026", "2.850,00", 3),
]


@pytest.mark.parametrize("fixture,expected_headers,sample_date,sample_amount,amount_col", CASES)
def test_western_format_replicated(fixture, expected_headers, sample_date, sample_amount, amount_col):
    from parsers.layout_replica_parser import create_layout_replica_parser

    parser = create_layout_replica_parser(use_ocr=False)
    parser.parse(str(FIXTURES / fixture), fixture)

    headers = [column.header for column in parser.table_columns]
    assert headers == expected_headers

    assert len(parser.table_rows) == 8, [row.values for row in parser.table_rows]

    date_cells = [(row.values[0] or "").strip() for row in parser.table_rows]
    assert any(cell.startswith(sample_date) for cell in date_cells), date_cells

    amount_cells = [(row.values[amount_col] or "").strip() for row in parser.table_rows]
    assert sample_amount in amount_cells, amount_cells
