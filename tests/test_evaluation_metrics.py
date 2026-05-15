#!/usr/bin/env python3
"""Tests for extraction evaluation metrics."""


def test_score_field_accuracy_reports_field_level_matches():
    from tools.evaluate_extraction import score_accuracy, score_field_accuracy

    extracted = [
        {
            "Date": "13/02/2026",
            "Description": "UPI Payment",
            "Withdrawal_Amount": "100.00",
            "Deposit_Amount": "",
            "Balance": "900.00",
        },
        {
            "Date": "14/02/2026",
            "Description": "Salary",
            "Withdrawal_Amount": "",
            "Deposit_Amount": "1,000.00",
            "Balance": "1,900.00",
        },
    ]
    truth = [
        {
            "Date": "2026-02-13",
            "Description": "UPI Payment",
            "Withdrawal_Amount": "100.00",
            "Deposit_Amount": "",
            "Balance": "900.00",
        },
        {
            "Date": "2026-02-14",
            "Description": "Salary",
            "Withdrawal_Amount": "",
            "Deposit_Amount": "1000.00",
            "Balance": "1900.00",
        },
    ]

    matches, extracted_count, truth_count, accuracy = score_accuracy(extracted, truth)
    field_metrics = score_field_accuracy(extracted, truth)

    assert (matches, extracted_count, truth_count, accuracy) == (2, 2, 2, 100.0)
    assert field_metrics["field_accuracy_pct"] == 100.0
    assert field_metrics["date_accuracy_pct"] == 100.0
    assert field_metrics["description_accuracy_pct"] == 100.0
    assert field_metrics["withdrawal_accuracy_pct"] == 100.0
    assert field_metrics["deposit_accuracy_pct"] == 100.0
    assert field_metrics["balance_accuracy_pct"] == 100.0


def test_score_field_accuracy_catches_field_errors_after_row_match():
    from tools.evaluate_extraction import score_accuracy, score_field_accuracy

    extracted = [{
        "Date": "2026-02-01",
        "Description": "Wrong merchant",
        "Withdrawal_Amount": "100.00",
        "Balance": "800.00",
    }]
    truth = [{
        "Date": "2026-02-01",
        "Description": "UPI Payment",
        "Withdrawal_Amount": "100.00",
        "Balance": "900.00",
    }]

    matches, _, _, accuracy = score_accuracy(extracted, truth)
    field_metrics = score_field_accuracy(extracted, truth)

    assert matches == 1
    assert accuracy == 100.0
    assert field_metrics["description_accuracy_pct"] == 0.0
    assert field_metrics["balance_accuracy_pct"] == 0.0
    assert field_metrics["field_accuracy_pct"] < 100.0
