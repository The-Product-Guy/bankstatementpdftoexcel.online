#!/usr/bin/env python3
"""
Regression checks for chunk orchestration helpers and parser page ranges.
Run with:
    python -m unittest tests.chunking_checks -v
"""
import unittest

import os
from parsers.chunk_utils import (  # noqa: E402
    build_page_ranges,
    merge_raw_tables,
    sort_transactions_for_output,
)
from parsers.universal_parser import create_universal_parser  # noqa: E402


class TestChunkingHelpers(unittest.TestCase):
    def test_build_page_ranges(self):
        self.assertEqual(build_page_ranges(0, 40), [])
        self.assertEqual(build_page_ranges(5, 10), [(1, 5)])
        self.assertEqual(build_page_ranges(105, 40), [(1, 40), (41, 80), (81, 105)])

    def test_merge_raw_tables(self):
        a = {
            "columns": ["Date", "Desc", "Amt"],
            "rows": [["01/01/24", "A", "10"]],
        }
        b = {
            "columns": ["Date", "Desc", "Amt"],
            "rows": [["02/01/24", "B", "20"]],
        }
        merged = merge_raw_tables(a, b)
        self.assertIsNotNone(merged)
        self.assertEqual(len(merged["rows"]), 2)
        self.assertEqual(merged["rows"][1][1], "B")

    def test_sort_transactions_for_output_by_page_line(self):
        txs = [
            {"Date": "x", "Page_Line": "Page_3"},
            {"Date": "x", "Page_Line": "Page_1"},
            {"Date": "x", "Page_Line": "Pages_2-4"},
            {"Date": "x", "Page_Line": "Raw_Table"},
            {"Date": "x", "Page_Line": "Page_2"},
        ]
        sorted_txs = sort_transactions_for_output(txs)
        sorted_keys = [t.get("Page_Line") for t in sorted_txs]
        self.assertEqual(
            sorted_keys,
            ["Page_1", "Page_2", "Pages_2-4", "Page_3", "Raw_Table"]
        )


class TestParserPageRange(unittest.TestCase):
    def test_parse_selected_page_window(self):
        sample = "tests/data/india_v1/OpTransactionHistory07-08-2025.pdf"
        if not os.path.exists(sample):
            self.skipTest(f"Sample PDF not found: {sample}")

        parser = create_universal_parser(
            execution_preset="local-low-mem",
            use_llm=False,
            max_pages=5,
        )
        rows = parser.parse(
            sample,
            os.path.basename(sample),
            page_start=1,
            page_end=2
        )

        self.assertIsNotNone(rows)
        self.assertGreater(len(rows), 0)
        self.assertIsNotNone(parser.stats)
        self.assertEqual(parser.stats.pages_processed, 2)
        self.assertEqual(parser.stats.total_pages, 5)


if __name__ == "__main__":
    unittest.main()
