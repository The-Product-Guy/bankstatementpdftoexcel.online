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


def test_zero_debit_does_not_hide_deposit_match():
    from tools.evaluate_extraction import score_accuracy, score_field_accuracy

    extracted = [{
        "Date": "2026-04-14",
        "Description": "Deposit from Parker-Evans",
        "Withdrawal_Amount": None,
        "Deposit_Amount": 390.27,
        "Transaction_Amount": 390.27,
        "Closing_Balance": 3853.70,
    }]
    truth = [{
        "Date": "2026-04-14",
        "Description": "Deposit from Parker-Evans",
        "Withdrawal_Amount": "0",
        "Deposit_Amount": "390.27",
        "Closing_Balance": "3853.70",
    }]

    matches, extracted_count, truth_count, accuracy = score_accuracy(extracted, truth)
    field_metrics = score_field_accuracy(extracted, truth)

    assert (matches, extracted_count, truth_count, accuracy) == (1, 1, 1, 100.0)
    assert field_metrics["withdrawal_expected"] == 0
    assert field_metrics["deposit_accuracy_pct"] == 100.0


def test_evaluation_summary_and_threshold_checks():
    from tools.evaluate_extraction import check_thresholds, summarize_rows

    rows = [
        {
            "status": "ok",
            "true_accuracy_pct": 100.0,
            "field_accuracy_pct": 98.0,
            "proxy_accuracy_pct": 90.0,
            "balance_consistency_pct": 95.0,
        },
        {
            "status": "ok",
            "true_accuracy_pct": 80.0,
            "field_accuracy_pct": 82.0,
            "proxy_accuracy_pct": 70.0,
            "balance_consistency_pct": 85.0,
        },
        {
            "status": "error",
            "proxy_accuracy_pct": "",
            "balance_consistency_pct": "",
        },
    ]

    summary = summarize_rows(rows)

    assert summary["files"] == 3
    assert summary["ok_files"] == 2
    assert summary["error_files"] == 1
    assert summary["truth_files"] == 2
    assert summary["avg_true_accuracy"] == 90.0
    assert summary["avg_field_accuracy"] == 90.0
    assert summary["avg_proxy_accuracy"] == 80.0
    assert summary["avg_balance_consistency"] == 90.0
    assert check_thresholds(summary, min_true_accuracy=89.0, require_truth=True) == []

    failures = check_thresholds(
        summary,
        min_true_accuracy=95.0,
        min_balance_consistency=92.0,
    )

    assert "Row-match accuracy 90.0% is below threshold 95.0%" in failures
    assert "Balance consistency 90.0% is below threshold 92.0%" in failures


def test_require_truth_fails_when_no_truth_rows():
    from tools.evaluate_extraction import check_thresholds, summarize_rows

    summary = summarize_rows([{
        "status": "ok",
        "proxy_accuracy_pct": 90.0,
        "balance_consistency_pct": 95.0,
        "true_accuracy_pct": "",
    }])

    assert check_thresholds(summary, require_truth=True) == [
        "No ground-truth files were evaluated"
    ]
