#!/usr/bin/env python3
"""Tests for optional PyMuPDF table fallback."""
import sys
from types import SimpleNamespace


class _FakeTable:
    def extract(self):
        return [
            ["Date", "Description", "Debit", "Credit", "Balance"],
            ["14 Jan 2025", "POS PURCHASE", "1,234.56 DR", "", "(12,345.67)"],
        ]


class _FakePage:
    def find_tables(self, strategy=None):
        if strategy == "text":
            return SimpleNamespace(tables=[_FakeTable()])
        return SimpleNamespace(tables=[])


class _FakeDoc:
    def __len__(self):
        return 1

    def __getitem__(self, index):
        assert index == 0
        return _FakePage()

    def close(self):
        pass


def test_pymupdf_table_fallback_extracts_transactions(monkeypatch):
    from parsers.universal_parser import create_universal_parser

    fake_pymupdf = SimpleNamespace(open=lambda path: _FakeDoc())
    monkeypatch.setitem(sys.modules, "pymupdf", fake_pymupdf)

    parser = create_universal_parser(
        use_paddleocr=False,
        use_img2table=False,
        use_pymupdf=True,
        use_llm=False,
    )

    transactions, raw_table = parser._extract_pymupdf_tables("sample.pdf", "sample.pdf")

    assert len(transactions) == 1
    assert transactions[0]["Date"] == "14 Jan 2025"
    assert transactions[0]["Withdrawal_Amount"] == 1234.56
    assert transactions[0]["Closing_Balance"] == -12345.67
    assert raw_table["columns"] == ["Date", "Description", "Debit", "Credit", "Balance"]
    assert raw_table["rows"][0][1] == "POS PURCHASE"
