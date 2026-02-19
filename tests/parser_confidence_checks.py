#!/usr/bin/env python3
"""
Regression checks for language gating and row confidence scoring.
Run with:
    python -m unittest tests.parser_confidence_checks -v
"""
import unittest

from parsers.universal_parser import create_universal_parser


class TestParserConfidenceAndLanguage(unittest.TestCase):
    def test_english_language_gate_heuristic(self):
        parser = create_universal_parser(use_paddleocr=False, use_img2table=False, use_llm=False)
        parser.config.english_only_beta = True

        english_text = "Transaction Date Description Debit Credit Balance Opening Closing"
        non_english_text = "سجل المعاملات تاريخ الوصف مدين دائن الرصيد الافتتاحي"

        self.assertTrue(parser._is_probably_english_text(english_text))
        self.assertFalse(parser._is_probably_english_text(non_english_text))

    def test_row_confidence_annotation(self):
        parser = create_universal_parser(use_paddleocr=False, use_img2table=False, use_llm=False)
        row = ["01/01/2025", "PAYMENT RECEIVED", "REF1234", "100.00", "", "1000.00"]
        col_map = {
            "date": 0,
            "description": 1,
            "reference": 2,
            "debit": 3,
            "credit": 4,
            "balance": 5,
        }
        tx = parser._row_to_transaction(
            row=row,
            col_map=col_map,
            source_file="sample.pdf",
            page_ref="Page_1",
        )

        self.assertIsNotNone(tx)
        self.assertIn("Row_Confidence", tx)
        self.assertIn("CellConf_Date", tx)
        self.assertGreaterEqual(float(tx["Row_Confidence"]), 0.0)
        self.assertLessEqual(float(tx["Row_Confidence"]), 1.0)


if __name__ == "__main__":
    unittest.main()
