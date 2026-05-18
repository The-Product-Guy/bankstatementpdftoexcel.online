#!/usr/bin/env python3
"""
Test suite for PDF-to-Excel converter parsers.
Run with: python -m pytest tests/test_parsers.py -v
"""
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from PIL import Image
import numpy as np

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestImagePreprocessor:
    """Test image preprocessing utilities."""
    
    def test_pil_to_cv2_rgb(self):
        """Test RGB image conversion."""
        from parsers.image_preprocessor import pil_to_cv2, cv2_to_pil
        
        # Create test RGB image
        pil_img = Image.new('RGB', (100, 100), color='red')
        cv2_img = pil_to_cv2(pil_img)
        
        assert cv2_img is not None
        assert cv2_img.shape == (100, 100, 3)
    
    def test_pil_to_cv2_grayscale(self):
        """Test grayscale image conversion."""
        from parsers.image_preprocessor import pil_to_cv2
        
        pil_img = Image.new('L', (100, 100), color=128)
        cv2_img = pil_to_cv2(pil_img)
        
        assert cv2_img is not None
        assert len(cv2_img.shape) == 2
    
    def test_enhance_contrast(self):
        """Test contrast enhancement."""
        from parsers.image_preprocessor import enhance_contrast
        
        # Create low contrast image
        img = np.ones((100, 100), dtype=np.uint8) * 128
        enhanced = enhance_contrast(img)
        
        assert enhanced is not None
        assert enhanced.shape == img.shape
    
    def test_preprocess_for_ocr(self):
        """Test full preprocessing pipeline."""
        from parsers.image_preprocessor import preprocess_for_ocr
        
        pil_img = Image.new('RGB', (100, 100), color='white')
        processed = preprocess_for_ocr(pil_img, deskew=True, enhance=True)
        
        assert processed is not None
        assert isinstance(processed, Image.Image)
    
    def test_quality_score(self):
        """Test image quality scoring."""
        from parsers.image_preprocessor import get_image_quality_score
        
        # Create a sharp image with text-like patterns
        img_array = np.random.randint(0, 255, (100, 100), dtype=np.uint8)
        pil_img = Image.fromarray(img_array)
        
        score = get_image_quality_score(pil_img)
        
        assert 0 <= score <= 1


class TestLLMTableExtractor:
    """Test LLM table extraction (mocked API calls)."""
    
    def test_parse_csv_response(self):
        """Test parsing CSV response."""
        from parsers.llm_table_extractor import LLMTableExtractor, ColumnMapping

        extractor = LLMTableExtractor.__new__(LLMTableExtractor)
        extractor.model = "gpt-4o-mini"

        cols = ["Date", "Description", "Debit", "Credit", "Balance"]
        mapping = ColumnMapping(columns=cols, date_col="Date", description_col="Description",
                                debit_col="Debit", credit_col="Credit", balance_col="Balance")
        response = '15/01/24,Test,100,,500'
        transactions = extractor._parse_csv_response(response, mapping)

        assert len(transactions) == 1
        assert transactions[0]['Date'] == '15/01/24'

    def test_parse_csv_response_with_markdown(self):
        """Test parsing CSV response wrapped in markdown."""
        from parsers.llm_table_extractor import LLMTableExtractor, ColumnMapping

        extractor = LLMTableExtractor.__new__(LLMTableExtractor)
        extractor.model = "gpt-4o-mini"

        cols = ["Date", "Description", "Debit", "Credit", "Balance"]
        mapping = ColumnMapping(columns=cols, date_col="Date", description_col="Description",
                                debit_col="Debit", credit_col="Credit", balance_col="Balance")
        response = '```csv\n15/01/24,Test,,500,1500\n```'
        transactions = extractor._parse_csv_response(response, mapping)

        assert len(transactions) == 1
    
    def test_parse_amount_string(self):
        """Test amount string parsing."""
        from parsers.llm_table_extractor import LLMTableExtractor
        
        extractor = LLMTableExtractor.__new__(LLMTableExtractor)
        
        assert extractor._parse_amount("1,234.56") == 1234.56
        assert extractor._parse_amount("₹500.00") == 500.0
        assert extractor._parse_amount(None) is None
        assert extractor._parse_amount("") is None
        assert extractor._parse_amount(0) is None
    
    def test_row_to_transaction(self):
        """Test row-to-transaction conversion."""
        from parsers.llm_table_extractor import LLMTableExtractor, ColumnMapping

        extractor = LLMTableExtractor.__new__(LLMTableExtractor)
        extractor.model = "gpt-4o-mini"

        cols = ["Date", "Description", "Ref", "Debit", "Credit", "Balance"]
        mapping = ColumnMapping(columns=cols, date_col="Date", description_col="Description",
                                reference_col="Ref", debit_col="Debit", credit_col="Credit",
                                balance_col="Balance")
        row = ["01/02/24", "Test deposit", "REF123", "", "1,000.00", "5,000.00"]
        tx = extractor._row_to_transaction(row, mapping)

        assert tx is not None
        assert tx['Date'] == '01/02/24'
        assert tx['Deposit_Amount'] == 1000.0
        assert tx['Closing_Balance'] == 5000.0
    
    def test_cost_estimation(self):
        """Test API cost estimation."""
        from parsers.llm_table_extractor import LLMTableExtractor
        
        extractor = LLMTableExtractor(model="gpt-4o-mini")
        
        # Test text-based estimation
        cost = extractor.estimate_cost(text_length=10000)
        assert cost > 0
        
        # Test page-based estimation
        cost_pages = extractor.estimate_cost(num_pages=5)
        assert cost_pages > 0


class TestUsageTracker:
    """Test usage tracking and rate limiting."""
    
    def test_default_free_plan(self):
        """Test that new users get free plan."""
        from parsers.usage_tracker import UsageTracker
        
        tracker = UsageTracker()
        plan = tracker.get_user_plan("new_user_123")
        
        assert plan.plan_type == 'free'
        assert plan.documents_per_month == 5
        assert plan.pages_per_document == 10
    
    def test_set_user_plan(self):
        """Test setting user plan."""
        from parsers.usage_tracker import UsageTracker
        
        tracker = UsageTracker()
        plan = tracker.set_user_plan("test_user_456", "professional")
        
        assert plan.plan_type == 'monthly'
        assert plan.documents_per_month == 200
        assert plan.expires_at is not None
    
    def test_check_can_process_within_limits(self):
        """Test processing within limits."""
        from parsers.usage_tracker import UsageTracker
        
        tracker = UsageTracker()
        can_process, message, cost = tracker.check_can_process("new_user_789", page_count=5)
        
        assert can_process is True
        assert message == "OK"
    
    def test_check_can_process_exceeded_page_limit(self):
        """Test exceeding page limit."""
        from parsers.usage_tracker import UsageTracker
        
        tracker = UsageTracker()
        # Free plan allows 10 pages per doc
        can_process, message, _ = tracker.check_can_process("new_user_abc", page_count=50)
        
        assert can_process is False
        assert "pages" in message.lower()
    
    def test_document_cost_calculation(self):
        """Test pay-per-document cost calculation."""
        from parsers.usage_tracker import UsageTracker
        
        tracker = UsageTracker()
        
        # Base price for 5 pages
        cost_5 = tracker._calculate_document_cost(5)
        assert cost_5 == 0.10
        
        # Extra pages cost more
        cost_20 = tracker._calculate_document_cost(20)
        assert cost_20 > cost_5
    
    def test_record_usage(self):
        """Test recording usage."""
        from parsers.usage_tracker import UsageTracker, generate_document_id
        
        tracker = UsageTracker()
        user_id = "test_record_user"
        doc_id = generate_document_id("test.pdf", user_id)
        
        record = tracker.record_usage(
            user_id=user_id,
            document_id=doc_id,
            pages=5,
            transactions=25,
            tokens=1000,
            api_cost=0.05,
            processing_time=5.5,
            bank_type="universal",
            success=True
        )
        
        assert record.pages_processed == 5
        assert record.transactions_extracted == 25


class TestBaseParser:
    """Test base parser utilities."""
    
    def test_clean_amount_string(self):
        """Test amount string cleaning."""
        from parsers.base_parser import BaseParser
        
        parser = Mock(spec=BaseParser)
        parser.clean_amount_string = BaseParser.clean_amount_string
        
        assert parser.clean_amount_string(parser, "1,234.56") == 1234.56
        assert parser.clean_amount_string(parser, "₹ 1,23,456.78 CR") == 123456.78
        assert parser.clean_amount_string(parser, "1.234,56") == 1234.56
        assert parser.clean_amount_string(parser, "(1,234.56)") == -1234.56
        assert parser.clean_amount_string(parser, "1,234.56 DR") == -1234.56
        assert parser.clean_amount_string(parser, "0.00") is None
        assert parser.clean_amount_string(parser, "") is None
    
    def test_parse_date(self):
        """Test date parsing."""
        from parsers.base_parser import BaseParser
        
        parser = Mock(spec=BaseParser)
        parser.parse_date = BaseParser.parse_date
        
        result = parser.parse_date(parser, "15/01/2024", '%d/%m/%Y')
        assert result == "15/01/24"

        result = parser.parse_date(parser, "15 Jan 2024")
        assert result == "15/01/24"

        result = parser.parse_date(parser, "2024-01-15")
        assert result == "15/01/24"
    
    def test_create_transaction_dict(self):
        """Test transaction dictionary creation."""
        from parsers.base_parser import BaseParser
        
        parser = Mock(spec=BaseParser)
        parser.create_transaction_dict = BaseParser.create_transaction_dict
        parser.positive_amount = lambda amount: BaseParser.positive_amount(parser, amount)
        
        tx = parser.create_transaction_dict(
            parser,
            date="15/01/24",
            description="Test transaction",
            reference="REF001",
            withdrawal_amt=100.0,
            deposit_amt=None,
            balance_amt=500.0,
            source_file="test.pdf",
            line_ref=1
        )
        
        assert tx['Date'] == '15/01/24'
        assert tx['Transaction_Amount'] == -100.0  # Negative for withdrawal
        assert tx['Closing_Balance'] == 500.0


class TestUniversalParser:
    """Test universal parser (integration tests with mocks)."""
    
    def test_parser_initialization(self):
        """Test parser can be initialized."""
        from parsers.universal_parser import UniversalBankParser, ProcessingConfig
        
        config = ProcessingConfig(use_llm=False)
        parser = UniversalBankParser(config=config)
        
        assert parser.bank_name == "Universal"
        assert parser.config.use_llm is False
    
    def test_factory_function(self):
        """Test create_universal_parser factory."""
        from parsers.universal_parser import create_universal_parser
        
        parser = create_universal_parser(
            use_llm=True,
            max_pages=50,
            llm_model="gpt-4o-mini"
        )
        
        assert parser is not None
        assert parser.config.max_pages == 50
        assert parser.config.llm_model == "gpt-4o-mini"

    def test_ocr_fallback_parses_statement_rows_from_right_edge(self):
        """OCR fallback should not treat reference digits as money."""
        from parsers.ledger_validation import ledger_rows_from_transactions, validate_ledger_rows
        from parsers.ledger_repair import repair_transactions_from_balance_deltas
        from parsers.universal_parser import create_universal_parser

        text = """
        TXN DT VALUE DT BRN DESCRIPTION REFERENCE DEBITS CREDITS BALANCE
        01/09/18 01/09/18 B/F... 1,43, 666.60
        01/09/18 01/09/18 1763 IMPS CR-1763308000000128- 824418052096 36,200.00 1,79,866.60
        GOLDEN EA-249805000396
        03/09/18 03/09/18 1763 IMPS DR-1763308000000116- 824521658273 15,000.00 1,64,866.60
        HDFC0000240-2017FO0100498
        914
        03/09/18 03/09/18 1763 ATM CSW/0100162693/Kovai- 824610043534 10,000.00 1,54,866.60
        Salem Rd/Salem
        03/09/18 03/09/18 1763 CA ATM TXN OTHER BANK CHA 824610043534 20.00 1,54,846.60
        RGES
        03/09/18 03/09/18 1763 ATM CSW/0100162693/Kovai- 824610044170 5,000.00 1,49,846.60
        Salem Rd/Salew
        03/09/18 03/09/18 1763 CA ATM TXN OTHER BANK CHA 824610044170 20.00 1,49,826.60
        RGES
        04/09/18 04/09/18 1221 To Clg : PALANIAPPAN ocacoo000008 61,415.00 88,411.60
        04/09/18 04/09/18 1684 NEFT : 000065525534 - ANG 33,400.00 1,21,811.60
        AN FOODS PVT LTD
        04/09/18 04/09/18 1763 IMPS DR-1763308000000116- 824715614177 30,000.00 91,811.60
        HDEFC0000240-3017FO0100498
        914
        05/09/18 05/09/18 1684 ATM CSwW/0100162693/KARUR 3470 10,000.00 81,911.60
        VYSYA BANK/SALEM
        06/09/18 06/09/18 1289 Cash Deposit 29,100.00 1,10,911.60
        06/09/18 06/09/18 1684 NEFT : KKBKH18249778072 - 30,000.00 1,40,911.60
        SHREE PRASANNA TRANSPORT
        06/09/18 06/09/18 1684 NEFT : IDIBH18249145739 - 50,000.00 1,90,911.60
        SIVA LOGISTICS
        06/09/18 06/09/18 1763 IMPS DR-1763308000000116- 824918954222 50,000.00 1,40,911.60
        HDFC0000240-3017F00100498
        914
        07/09/18 07/09/18 1763 IMPS DR-1763308000000116- 825013281637 10,000.00 1,30,911.60
        SBIN0007464-32372805878
        07/09/18 07/09/18 1684 AIM CSW/0100162693/KARUR 4370 1,500.00 1,29,411.60
        VYSYA BANK/SALEM
        Scanned by CamScanner
        """

        parser = create_universal_parser(use_paddleocr=False, use_img2table=False, use_llm=False)
        parser._fallback_regex_parse(text, "statement.pdf")

        assert len(parser.transactions) == 17
        assert parser.transactions[1]["Deposit_Amount"] == 36200.0
        assert parser.transactions[2]["Withdrawal_Amount"] == 15000.0
        assert parser.transactions[7]["Withdrawal_Amount"] == 61415.0
        assert parser.transactions[11]["Deposit_Amount"] == 29100.0

        report = validate_ledger_rows(ledger_rows_from_transactions(parser.transactions))
        assert report.balance_checks == 16
        assert report.balance_checks_passed == 14

        repaired, repair_report = repair_transactions_from_balance_deltas(parser.transactions)
        repaired_report = validate_ledger_rows(ledger_rows_from_transactions(repaired))
        assert repair_report.repaired_count == 1
        assert repaired[10]["Closing_Balance"] == 81811.6
        assert repaired_report.balance_checks_passed == 16


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
