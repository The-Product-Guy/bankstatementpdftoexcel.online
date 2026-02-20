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

    def test_select_dense_row_band(self):
        parser = create_universal_parser(use_paddleocr=False, use_img2table=False, use_llm=False)
        # Sparse header lines + dense statement table lines
        lines = [40, 78, 116, 420, 448, 476, 504, 532, 560, 588, 616]
        dense = parser._select_dense_row_band(lines)
        self.assertGreaterEqual(len(dense), 6)
        self.assertEqual(dense[0], 420)
        self.assertEqual(dense[-1], 616)

    def test_blobbed_rows_detection(self):
        parser = create_universal_parser(use_paddleocr=False, use_img2table=False, use_llm=False)
        blob_rows = [
            [
                "21/04/18 24/04/18 25/04/18 26/04/18 27/04/18 IMPS NEFT CASH ATM TRANSFER",
                "0000811418398816 0000811510383408",
                "21/04/18 24/04/18 25/04/18 26/04/18 27/04/18",
                "69,249.00 10,000.00 14,000.00 15,000.00 6,000.00",
                "75,062.3",
            ]
            for _ in range(12)
        ]
        self.assertTrue(parser._looks_like_blobbed_rows(blob_rows))

        good_rows = [
            ["21/04/18", "IMPS-811110358391-PALANI", "0000811110358391", "69,249.00", "", "3,257.23"],
            ["24/04/18", "INST-ALERT CHG IN GST", "MIR1810965687041", "17.70", "", "3,239.53"],
            ["24/04/18", "ROADWAYS-HDFC-ADVANCE PAYMENT", "0000811415466702", "", "26,560.00", "29,799.53"],
        ]
        self.assertFalse(parser._looks_like_blobbed_rows(good_rows))

    def test_mixed_date_description_and_amount_pair_parsing(self):
        parser = create_universal_parser(use_paddleocr=False, use_img2table=False, use_llm=False)
        rows = [
            ["21/04/18 IMPS-811110358391-PALANI", "0000811110358391", "21/04/18", "6,000.00 0.00", "72,506.23"],
            ["21/04/18 CTG CHE K 1584 DR -", "0000000000000007", "21/04/18", "69,249.00 0.00", "3,257.23"],
        ]
        col_map = parser._infer_columns_from_rows(rows)
        self.assertEqual(col_map.get("date"), 2)
        self.assertEqual(col_map.get("description"), 0)
        self.assertEqual(col_map.get("reference"), 1)

        tx = parser._row_to_transaction(
            row=rows[0],
            col_map=col_map,
            source_file="sample.pdf",
            page_ref="Spatial_Table",
        )
        self.assertIsNotNone(tx)
        self.assertEqual(tx["Date"], "21/04/18")
        self.assertIn("IMPS-811110358391-PALANI", tx["Description"])
        self.assertEqual(tx["Withdrawal_Amount"], 6000.0)
        self.assertIsNone(tx["Deposit_Amount"])
        self.assertEqual(tx["Closing_Balance"], 72506.23)


if __name__ == "__main__":
    unittest.main()
