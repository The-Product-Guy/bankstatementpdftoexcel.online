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


if __name__ == "__main__":
    unittest.main()
