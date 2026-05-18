#!/usr/bin/env python3
"""
Regression checks for optional universal-parser header hints.
Run with:
    python -m unittest tests.bank_profiles_checks -v
"""
import unittest

from parsers.bank_profiles import detect_bank_profile
from parsers.universal_parser import create_universal_parser


class TestBankProfiles(unittest.TestCase):
    def test_detect_profile_from_filename(self):
        hdfc = detect_bank_profile("HDFC BANK SB-01.04.2018 TO 31.03.2025.pdf")
        kvb = detect_bank_profile("KVB-CA-PART-03- 01.04.2019 TO 31.03.2020.pdf")

        self.assertIsNotNone(hdfc)
        self.assertEqual(hdfc.key, "hdfc")
        self.assertIsNotNone(kvb)
        self.assertEqual(kvb.key, "kvb")

    def test_generic_bank_text_does_not_trigger_profile(self):
        profile = detect_bank_profile(
            "stmt_canada_001.pdf",
            first_page_text="CANADA National Bank Account Statement Balance Credits Debits",
            headers=["Date", "Description", "Debits (-)", "Credits (+)", "Balance"],
        )

        self.assertIsNone(profile)

    def test_hdfc_header_alias_mapping(self):
        parser = create_universal_parser(use_paddleocr=False, use_img2table=False, use_llm=False)
        parser.source_filename = "HDFC BANK SB-01.04.2018 TO 31.03.2025.pdf"

        headers = [
            "Txn Date",
            "Narration",
            "Chq/Ref No",
            "Withdrawal Amt",
            "Deposit Amt",
            "Closing Balance",
        ]
        mapping = parser._map_headers(headers)

        self.assertEqual(mapping.get("date"), 0)
        self.assertEqual(mapping.get("description"), 1)
        self.assertEqual(mapping.get("reference"), 2)
        self.assertEqual(mapping.get("debit"), 3)
        self.assertEqual(mapping.get("credit"), 4)
        self.assertEqual(mapping.get("balance"), 5)

    def test_kvb_header_alias_mapping(self):
        parser = create_universal_parser(use_paddleocr=False, use_img2table=False, use_llm=False)
        parser.source_filename = "KVB-CA-PART-03- 01.04.2019 TO 31.03.2020.pdf"

        headers = [
            "Txn Dt",
            "Transaction Remarks",
            "Cheque Number",
            "Withdrawal Amount",
            "Deposit Amount",
            "Balance INR",
        ]
        mapping = parser._map_headers(headers)

        self.assertEqual(mapping.get("date"), 0)
        self.assertEqual(mapping.get("description"), 1)
        self.assertEqual(mapping.get("reference"), 2)
        self.assertEqual(mapping.get("debit"), 3)
        self.assertEqual(mapping.get("credit"), 4)
        self.assertEqual(mapping.get("balance"), 5)

    def test_generic_multiline_header_geometry(self):
        class FakePage:
            width = 595

            def extract_words(self):
                return [
                    {"text": "S", "x0": 23.54, "x1": 28.0, "top": 222.0, "bottom": 230.0},
                    {"text": "No.", "x0": 30.0, "x1": 44.45, "top": 222.0, "bottom": 230.0},
                    {"text": "Transaction", "x0": 60.24, "x1": 107.76, "top": 217.0, "bottom": 225.0},
                    {"text": "Date", "x0": 70.0, "x1": 98.0, "top": 227.0, "bottom": 235.0},
                    {"text": "Cheque", "x0": 122.42, "x1": 153.0, "top": 222.0, "bottom": 230.0},
                    {"text": "Number", "x0": 156.0, "x1": 185.58, "top": 222.0, "bottom": 230.0},
                    {"text": "Transaction", "x0": 247.13, "x1": 291.0, "top": 222.0, "bottom": 230.0},
                    {"text": "Remarks", "x0": 294.0, "x1": 331.87, "top": 222.0, "bottom": 230.0},
                    {"text": "Withdrawal", "x0": 399.0, "x1": 447.0, "top": 217.0, "bottom": 225.0},
                    {"text": "Amount", "x0": 395.6, "x1": 427.3, "top": 227.0, "bottom": 235.0},
                    {"text": "(INR)", "x0": 429.6, "x1": 450.4, "top": 227.0, "bottom": 235.0},
                    {"text": "Deposit", "x0": 473.8, "x1": 504.2, "top": 217.0, "bottom": 225.0},
                    {"text": "Amount", "x0": 461.6, "x1": 493.3, "top": 227.0, "bottom": 235.0},
                    {"text": "(INR)", "x0": 495.6, "x1": 516.4, "top": 227.0, "bottom": 235.0},
                    {"text": "Balance", "x0": 532.1, "x1": 563.9, "top": 217.0, "bottom": 225.0},
                    {"text": "(INR)", "x0": 537.6, "x1": 558.4, "top": 227.0, "bottom": 235.0},
                ]

        parser = create_universal_parser(use_paddleocr=False, use_img2table=False, use_llm=False)
        header = parser._detect_header_layout(FakePage())

        self.assertIsNotNone(header)
        self.assertEqual(header["col_map"].get("date"), 1)
        self.assertEqual(header["col_map"].get("description"), 3)
        self.assertEqual(header["col_map"].get("debit"), 4)
        self.assertEqual(header["col_map"].get("credit"), 5)
        self.assertEqual(header["col_map"].get("balance"), 6)
        self.assertEqual(parser._boundary_index(431.9, header["boundaries"]), 4)
        self.assertEqual(parser._boundary_index(493.1, header["boundaries"]), 5)


if __name__ == "__main__":
    unittest.main()
