from parsers.ledger_validation import (
    StatementSummary,
    format_minor,
    ledger_rows_from_transactions,
    parse_money_to_minor,
    validate_ledger_rows,
)
from parsers.statement_summary import parse_statement_summary_text


def test_parse_money_to_minor_handles_indian_grouping_and_spaces():
    assert parse_money_to_minor("1,53,265.70") == 15326570
    assert parse_money_to_minor("1,43, 666.60") == 14366660
    assert parse_money_to_minor("(5,000.00)") == -500000
    assert parse_money_to_minor("20.00 DR") == -2000
    assert format_minor(15326570) == "153265.70"


def test_parse_statement_summary_text_handles_kvb_ocr_noise():
    text = """
    Opening Balance : 1,43, 666.60
    Total Credit Amount 5 67, 55,298 .40 Credit Count :144
    Total Debit Amount : 67,45, 699.30 Debit Count :355
    Closing Balance s 1,53, 265.70
    """

    summary = parse_statement_summary_text(text)

    assert summary.opening_balance_minor == 14366660
    assert summary.total_credit_minor == 675529840
    assert summary.credit_count == 144
    assert summary.total_debit_minor == 674569930
    assert summary.debit_count == 355
    assert summary.closing_balance_minor == 15326570


def test_validate_ledger_detects_merged_reference_amount():
    transactions = [
        {
            "Date": "01/09/18",
            "Description": "B/F...",
            "Closing_Balance": "1,43,666.60",
            "Transaction_Amount": "0.00",
        },
        {
            "Date": "01/09/18",
            "Description": "IMPS CR reference merged into amount",
            "Deposit_Amount": "96,36,200.00",
            "Transaction_Amount": "96,36,200.00",
            "Closing_Balance": "1,79,866.60",
        },
    ]

    report = validate_ledger_rows(ledger_rows_from_transactions(transactions))

    assert report.balance_checks == 1
    assert report.balance_checks_passed == 0
    assert report.issues[0].code == "balance_delta_mismatch"
    assert report.issues[0].expected_minor == 3620000
    assert report.issues[0].actual_minor == 963620000


def test_validate_ledger_reconciles_against_summary_with_exact_minor_units():
    transactions = [
        {"Date": "01/01/24", "Description": "Opening", "Closing_Balance": "100.00", "Transaction_Amount": "0.00"},
        {"Date": "02/01/24", "Description": "Credit", "Deposit_Amount": "10.10", "Closing_Balance": "110.10"},
        {"Date": "03/01/24", "Description": "Debit", "Withdrawal_Amount": "5.05", "Closing_Balance": "105.05"},
    ]
    summary = StatementSummary(
        opening_balance_minor=10000,
        closing_balance_minor=10505,
        total_credit_minor=1010,
        total_debit_minor=505,
        credit_count=1,
        debit_count=1,
    )

    report = validate_ledger_rows(ledger_rows_from_transactions(transactions), summary)

    assert report.is_valid
    assert report.balance_consistency_pct == 100.0
    assert report.total_credit_minor == 1010
    assert report.total_debit_minor == 505
