#!/usr/bin/env python3
"""
Tests for Flask routes.
Run with: python -m pytest tests/test_routes.py -v
"""
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Set required env vars before importing app
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DISABLE_QUOTAS", "true")


@pytest.fixture
def client():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestPublicRoutes:
    """Test unauthenticated public pages."""

    def test_home(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Statement Converter" in resp.data

    def test_pricing(self, client):
        resp = client.get("/pricing")
        assert resp.status_code == 200
        assert b"Pricing" in resp.data
        assert b"<h1>Simple, Transparent Pricing</h1>" in resp.data

    def test_blogs(self, client):
        resp = client.get("/blogs")
        assert resp.status_code == 200
        assert b"Blog" in resp.data
        assert b"<h1>Blog and Guides</h1>" in resp.data
        assert b"/blogs/how-to-convert-bank-statements-to-excel" in resp.data

    def test_blog_article(self, client):
        resp = client.get("/blogs/how-to-convert-bank-statements-to-excel")
        assert resp.status_code == 200
        assert b"<h1>How to Convert Bank Statements to Excel</h1>" in resp.data
        assert b"Article" in resp.data

    def test_blog_article_missing(self, client):
        resp = client.get("/blogs/not-a-real-post")
        assert resp.status_code == 404

    def test_signin(self, client):
        resp = client.get("/signin")
        assert resp.status_code == 200
        assert b"sign-in link" in resp.data.lower()

    def test_privacy(self, client):
        resp = client.get("/privacy")
        assert resp.status_code == 200
        assert b"Privacy Policy" in resp.data

    def test_terms(self, client):
        resp = client.get("/terms")
        assert resp.status_code == 200
        assert b"Terms of Service" in resp.data

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.data == b"OK"

    def test_health_detailed(self, client):
        resp = client.get("/health/detailed")
        data = resp.get_json()
        assert data["service"] == "pdf-excel-converter"
        assert "checks" in data
        assert data["checks"]["flask"] == "OK"

    def test_sitemap(self, client):
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert b"<urlset" in resp.data
        assert b"/privacy" in resp.data
        assert b"/terms" in resp.data
        assert b"/blogs/how-to-convert-bank-statements-to-excel" in resp.data
        assert b"<lastmod>2026-05-15</lastmod>" in resp.data

    def test_robots(self, client):
        resp = client.get("/robots.txt")
        assert resp.status_code == 200
        assert b"Sitemap:" in resp.data

    def test_index_redirects(self, client):
        resp = client.get("/index")
        assert resp.status_code == 302


class TestAuthRoutes:
    """Test auth flow without actually sending emails."""

    def test_auth_start_missing_email(self, client):
        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
        resp = client.post("/auth/start", data={
            "email": "",
            "csrf_token": "test-token",
        })
        assert resp.status_code == 302  # redirect back to signin

    def test_auth_start_bad_csrf(self, client):
        resp = client.post("/auth/start", data={
            "email": "test@example.com",
            "csrf_token": "wrong",
        })
        assert resp.status_code == 302

    @patch("routes.auth.send_magic_link_email")
    def test_auth_start_sends_email(self, mock_send, client):
        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
        resp = client.post("/auth/start", data={
            "email": "user@example.com",
            "csrf_token": "test-token",
        })
        assert resp.status_code == 302
        mock_send.assert_called_once()
        args = mock_send.call_args
        assert args[0][0] == "user@example.com"

    def test_auth_verify_missing_token(self, client):
        resp = client.get("/auth/verify")
        assert resp.status_code == 302

    def test_signout(self, client):
        resp = client.get("/signout")
        assert resp.status_code == 302


class TestProtectedRoutes:
    """Test routes that require auth."""

    def test_account_requires_login(self, client):
        resp = client.get("/account")
        assert resp.status_code == 302  # redirect to signin

    def test_admin_requires_admin(self, client):
        resp = client.get("/admin")
        assert resp.status_code == 302  # redirect to signin

    def test_admin_hides_for_non_admin_user(self, client):
        with client.session_transaction() as sess:
            sess["user_email"] = f"non-admin-{uuid.uuid4().hex}@example.com"
        resp = client.get("/admin")
        assert resp.status_code == 404

    def test_dashboard_requires_login(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 302  # redirect to signin

    def test_convert_requires_file(self, client):
        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
        resp = client.post("/convert", data={
            "csrf_token": "test-token",
        })
        # Should fail gracefully (no file uploaded)
        assert resp.status_code in (302, 400, 500)


class TestRateLimiting:
    """Test rate limiter behavior when Redis is unavailable."""

    @staticmethod
    def _failing_redis_module():
        def from_url(*args, **kwargs):
            raise RuntimeError("redis down")

        return SimpleNamespace(
            StrictRedis=SimpleNamespace(from_url=from_url)
        )

    def test_rate_limit_backend_failure_defaults_open(self, monkeypatch):
        import app as app_module

        monkeypatch.setattr(app_module, "RATE_LIMIT_FAIL_CLOSED", False)
        monkeypatch.setitem(sys.modules, "redis", self._failing_redis_module())

        assert app_module.rate_limited("rate:test", 1, 60) is False

    def test_rate_limit_backend_failure_can_fail_closed(self, monkeypatch):
        import app as app_module

        monkeypatch.setattr(app_module, "RATE_LIMIT_FAIL_CLOSED", True)
        monkeypatch.setitem(sys.modules, "redis", self._failing_redis_module())

        assert app_module.rate_limited("rate:test", 1, 60) is True


class TestStripeWebhook:
    """Test Stripe webhook endpoint."""

    def test_webhook_missing_secret(self, client):
        with patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": ""}):
            resp = client.post("/stripe/webhook", data=b"test")
            assert resp.status_code == 400

    def test_webhook_bad_signature(self, client):
        with patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": "whsec_test"}):
            resp = client.post("/stripe/webhook", data=b"test", headers={
                "Stripe-Signature": "bad"
            })
            assert resp.status_code == 400
