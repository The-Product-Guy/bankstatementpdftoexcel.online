#!/usr/bin/env python3
"""
Tests for the template-based extraction module.
Run with:
    python -m pytest tests/template_extraction_checks.py -v
"""
import unittest

from parsers.template_extractor import (
    ColumnSpec,
    DocumentTemplate,
    TemplateDetector,
    detect_date_format,
    detect_merged_amounts,
    detect_number_format,
)


class TestDateFormatDetection(unittest.TestCase):
    def test_dd_mm_yyyy_slash(self):
        fmt, regex = detect_date_format(["01/04/2025", "15/06/2024", "31/12/2023"])
        self.assertEqual(fmt, "%d/%m/%Y")

    def test_yyyy_mm_dd_dash(self):
        fmt, regex = detect_date_format(["2025-04-01", "2024-06-15", "2023-12-31"])
        self.assertEqual(fmt, "%Y-%m-%d")

    def test_dd_mm_yy(self):
        fmt, regex = detect_date_format(["01/04/25", "15/06/24", "31/12/23"])
        self.assertEqual(fmt, "%d/%m/%y")

    def test_mixed_with_majority(self):
        # Majority dd/mm/yyyy, one odd value
        fmt, _ = detect_date_format(["01/04/2025", "15/06/2024", "2023-12-31", "28/02/2024"])
        self.assertEqual(fmt, "%d/%m/%Y")

    def test_empty(self):
        fmt, regex = detect_date_format([])
        # Should return fallback
        self.assertIn("%d/%m/%Y", fmt)


class TestNumberFormatDetection(unittest.TestCase):
    def test_indian_format(self):
        result = detect_number_format(["1,23,456.78", "45,000.00", "1,00,000.00"])
        self.assertEqual(result, "indian")

    def test_western_format(self):
        result = detect_number_format(["123,456.78", "45,000.00", "1,000,000.00"])
        self.assertEqual(result, "western")

    def test_plain_format(self):
        result = detect_number_format(["12345.78", "45000", "1000000.00"])
        self.assertEqual(result, "plain")

    def test_empty(self):
        result = detect_number_format([])
        self.assertEqual(result, "plain")


class TestMergedAmountDetection(unittest.TestCase):
    def test_merged_detected(self):
        rows = [
            ["01/04/2025", "PAYMENT", "6,000.00 0.00", "1,23,456.78"],
            ["02/04/2025", "DEPOSIT", "0.00 10,000.00", "1,33,456.78"],
            ["03/04/2025", "ATM", "5,000.00 0.00", "1,28,456.78"],
        ]
        has_merged, order = detect_merged_amounts(rows, [2])
        self.assertTrue(has_merged)

    def test_not_merged(self):
        rows = [
            ["01/04/2025", "PAYMENT", "6,000.00", "", "1,23,456.78"],
            ["02/04/2025", "DEPOSIT", "", "10,000.00", "1,33,456.78"],
        ]
        has_merged, _ = detect_merged_amounts(rows, [2, 3])
        self.assertFalse(has_merged)


class TestHeuristicDetection(unittest.TestCase):
    def test_standard_headers(self):
        detector = TemplateDetector()
        headers = ["Date", "Description", "Debit", "Credit", "Balance"]
        rows = [
            ["01/04/2025", "NEFT PAYMENT", "6,000.00", "", "1,23,456.78"],
            ["02/04/2025", "SALARY CREDIT", "", "50,000.00", "1,73,456.78"],
        ]
        tpl = detector._try_heuristic(headers, rows, bank_profile=None)
        self.assertIsNotNone(tpl)
        self.assertEqual(tpl.date_idx, 0)
        self.assertEqual(tpl.description_idx, 1)
        self.assertEqual(tpl.debit_idx, 2)
        self.assertEqual(tpl.credit_idx, 3)
        self.assertEqual(tpl.balance_idx, 4)
        self.assertGreaterEqual(tpl.detection_confidence, 0.8)

    def test_hdfc_style_headers(self):
        detector = TemplateDetector()
        headers = ["Txn Date", "Narration", "Ref No", "Withdrawal", "Deposit", "Closing Balance"]
        rows = [
            ["01/04/2025", "IMPS-123", "000123", "6,000.00", "", "1,23,456.78"],
        ]
        tpl = detector._try_heuristic(headers, rows, bank_profile=None)
        self.assertIsNotNone(tpl)
        self.assertEqual(tpl.date_idx, 0)
        self.assertEqual(tpl.description_idx, 1)
        self.assertEqual(tpl.reference_idx, 2)
        self.assertEqual(tpl.debit_idx, 3)
        self.assertEqual(tpl.credit_idx, 4)
        self.assertEqual(tpl.balance_idx, 5)

    def test_no_headers_returns_none(self):
        detector = TemplateDetector()
        tpl = detector._try_heuristic([], [["a", "b"]], bank_profile=None)
        self.assertIsNone(tpl)

    def test_ambiguous_headers_low_confidence(self):
        detector = TemplateDetector()
        headers = ["Col1", "Col2", "Col3", "Col4"]
        rows = [["a", "b", "c", "d"]]
        tpl = detector._try_heuristic(headers, rows, bank_profile=None)
        # No date keyword → returns None
        self.assertIsNone(tpl)


class TestDocumentTemplateColMap(unittest.TestCase):
    def test_to_col_map(self):
        tpl = DocumentTemplate(
            columns=[],
            date_idx=0,
            description_idx=1,
            reference_idx=2,
            debit_idx=3,
            credit_idx=4,
            balance_idx=5,
        )
        col_map = tpl.to_col_map()
        self.assertEqual(col_map, {
            "date": 0,
            "description": 1,
            "reference": 2,
            "debit": 3,
            "credit": 4,
            "balance": 5,
        })

    def test_to_col_map_partial(self):
        tpl = DocumentTemplate(
            columns=[],
            date_idx=0,
            description_idx=2,
        )
        col_map = tpl.to_col_map()
        self.assertEqual(col_map, {"date": 0, "description": 2})

    def test_col_map_matches_map_headers_output(self):
        """Verify template col_map matches what _map_headers would produce."""
        from parsers.universal_parser import create_universal_parser

        parser = create_universal_parser(
            use_paddleocr=False, use_img2table=False, use_llm=False
        )
        headers = ["Date", "Description", "Reference", "Debit", "Credit", "Balance"]
        parser_map = parser._map_headers(headers)

        detector = TemplateDetector()
        tpl = detector._try_heuristic(headers, [["01/01/2025", "TEST", "REF", "100.00", "", "900.00"]])
        self.assertIsNotNone(tpl)
        template_map = tpl.to_col_map()

        # Both should agree on the standard fields present
        for key in ("date", "description", "debit", "credit", "balance"):
            self.assertEqual(
                parser_map.get(key),
                template_map.get(key),
                f"Mismatch on '{key}': parser={parser_map.get(key)}, template={template_map.get(key)}",
            )


class TestEndToEndTemplateApplication(unittest.TestCase):
    def test_template_produces_transactions(self):
        from parsers.universal_parser import create_universal_parser

        parser = create_universal_parser(
            use_paddleocr=False, use_img2table=False, use_llm=False
        )
        headers = ["Date", "Description", "Ref", "Debit", "Credit", "Balance"]
        rows = [
            ["01/04/2025", "NEFT PAYMENT", "REF001", "6,000.00", "", "94,000.00"],
            ["02/04/2025", "SALARY", "REF002", "", "50,000.00", "1,44,000.00"],
            ["03/04/2025", "ATM WITHDRAWAL", "REF003", "5,000.00", "", "1,39,000.00"],
        ]

        detector = TemplateDetector()
        tpl = detector._try_heuristic(headers, rows)
        self.assertIsNotNone(tpl)
        col_map = tpl.to_col_map()

        transactions = []
        for row in rows:
            tx = parser._row_to_transaction(
                row=row,
                col_map=col_map,
                source_file="test.pdf",
                page_ref="Template",
            )
            if tx:
                transactions.append(tx)

        self.assertEqual(len(transactions), 3)
        self.assertEqual(transactions[0]["Date"], "01/04/2025")
        self.assertEqual(transactions[0]["Description"], "NEFT PAYMENT")
        self.assertIsNotNone(transactions[0]["Withdrawal_Amount"])
        self.assertEqual(transactions[1]["Date"], "02/04/2025")
        self.assertIsNotNone(transactions[1]["Deposit_Amount"])


class TestDetectFullFlow(unittest.TestCase):
    def test_detect_returns_heuristic_for_clear_headers(self):
        detector = TemplateDetector()
        headers = ["Transaction Date", "Particulars", "Cheque No", "Debit", "Credit", "Balance"]
        rows = [
            ["01/04/2025", "NEFT PAYMENT", "000123", "6,000.00", "", "94,000.00"],
            ["02/04/2025", "SALARY CREDIT", "", "", "50,000.00", "1,44,000.00"],
        ]
        tpl = detector.detect(headers, rows)
        self.assertIsNotNone(tpl)
        self.assertEqual(tpl.detection_method, "heuristic")
        self.assertGreaterEqual(tpl.detection_confidence, 0.6)
        self.assertEqual(tpl.llm_tokens_used, 0)


if __name__ == "__main__":
    unittest.main()
