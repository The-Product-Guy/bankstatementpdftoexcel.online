#!/usr/bin/env python3
"""
Tests for email sending (Resend integration).
Run with: python -m pytest tests/test_email.py -v
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("SECRET_KEY", "test-secret")


class TestMagicLinkEmail:
    """Test magic link email sending."""

    @patch.dict(os.environ, {"RESEND_API_KEY": "", "RESEND_FROM_EMAIL": ""})
    def test_missing_api_key_raises(self):
        from app import send_magic_link_email
        with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
            send_magic_link_email("user@example.com", "https://example.com/verify?token=abc")

    @patch.dict(os.environ, {
        "RESEND_API_KEY": "re_test_key",
        "RESEND_FROM_EMAIL": "noreply@test.com",
    })
    def test_send_calls_resend(self):
        mock_resend = MagicMock()
        with patch.dict("sys.modules", {"resend": mock_resend}):
            # Re-import to pick up the mocked resend module
            import importlib
            import app as app_module
            importlib.reload(app_module)
            app_module.send_magic_link_email("user@example.com", "https://example.com/verify?token=abc")

            mock_resend.Emails.send.assert_called_once()
            call_args = mock_resend.Emails.send.call_args[0][0]
            assert call_args["to"] == ["user@example.com"]
            assert "sign-in" in call_args["subject"].lower()
            assert "token=abc" in call_args["html"]
            assert "token=abc" in call_args["text"]
            assert call_args["from"] == "noreply@test.com"


class TestPlanChangeEmail:
    """Test subscription change emails."""

    @patch.dict(os.environ, {"RESEND_API_KEY": ""})
    def test_no_key_silently_returns(self):
        from app import _send_plan_change_email
        # Should not raise
        _send_plan_change_email("user@example.com", "pro", "activated")

    @patch.dict(os.environ, {
        "RESEND_API_KEY": "re_test_key",
        "RESEND_FROM_EMAIL": "noreply@test.com",
    })
    def test_activation_email(self):
        mock_resend = MagicMock()
        with patch.dict("sys.modules", {"resend": mock_resend}):
            from app import _send_plan_change_email
            _send_plan_change_email("user@example.com", "pro", "activated")

            mock_resend.Emails.send.assert_called_once()
            call_args = mock_resend.Emails.send.call_args[0][0]
            assert "Pro" in call_args["subject"]
            assert "user@example.com" in call_args["to"]

    @patch.dict(os.environ, {
        "RESEND_API_KEY": "re_test_key",
        "RESEND_FROM_EMAIL": "noreply@test.com",
    })
    def test_cancellation_email(self):
        mock_resend = MagicMock()
        with patch.dict("sys.modules", {"resend": mock_resend}):
            from app import _send_plan_change_email
            _send_plan_change_email("user@example.com", "free", "canceled")

            call_args = mock_resend.Emails.send.call_args[0][0]
            assert "canceled" in call_args["subject"].lower()
