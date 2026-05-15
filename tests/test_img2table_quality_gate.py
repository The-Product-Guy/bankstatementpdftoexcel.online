#!/usr/bin/env python3
"""Tests for scanned-PDF img2table quality gating."""


def _parser_for_img2table_gate():
    from parsers.universal_parser import create_universal_parser

    parser = create_universal_parser(
        execution_preset="prod-balanced",
        use_img2table=True,
        use_paddleocr=False,
        use_llm=False,
        use_template=False,
        min_table_transactions=3,
    )
    return parser


def test_img2table_low_normalized_rows_continues_to_fallback():
    parser = _parser_for_img2table_gate()
    fallback_called = {"value": False}

    parser._try_img2table_extraction = lambda *args, **kwargs: {
        "raw_table": {
            "columns": ["Date", "Description", "Amount"],
            "rows": [["01-01-2026", "A", "10"], ["02-01-2026", "B", "20"]],
        }
    }
    parser._derive_transactions_from_raw_table = lambda *args, **kwargs: 0

    def fallback(*args, **kwargs):
        fallback_called["value"] = True
        return 0

    parser._try_legacy_spatial_extraction = fallback
    parser._try_ocr_text_fallback = lambda *args, **kwargs: 0

    parser._process_image_based("statement.pdf", "statement.pdf", total_pages=1)

    assert fallback_called["value"] is True


def test_img2table_sufficient_normalized_rows_short_circuits_fallback():
    parser = _parser_for_img2table_gate()
    fallback_called = {"value": False}

    parser._try_img2table_extraction = lambda *args, **kwargs: {
        "raw_table": {
            "columns": ["Date", "Description", "Amount"],
            "rows": [
                ["01-01-2026", "A", "10"],
                ["02-01-2026", "B", "20"],
                ["03-01-2026", "C", "30"],
            ],
        }
    }
    parser._derive_transactions_from_raw_table = lambda *args, **kwargs: 3

    def fallback(*args, **kwargs):
        fallback_called["value"] = True
        return 0

    parser._try_legacy_spatial_extraction = fallback
    parser._try_ocr_text_fallback = lambda *args, **kwargs: 0

    parser._process_image_based("statement.pdf", "statement.pdf", total_pages=1)

    assert fallback_called["value"] is False
