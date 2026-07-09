#!/usr/bin/env python3
"""
Tests for Flask routes.
Run with: python -m pytest tests/test_routes.py -v
"""
import os
import sys
from io import BytesIO
import uuid
from datetime import datetime
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
        assert b'<link rel="canonical" href="http://localhost/">' in resp.data

    def test_pricing(self, client):
        resp = client.get("/pricing")
        assert resp.status_code == 200
        assert b"Pricing" in resp.data
        assert b"Simple pricing for" in resp.data
        assert b"FAQPage" in resp.data

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
        assert b"<lastmod>2026-06-10</lastmod>" in resp.data
        assert b"<changefreq>" not in resp.data
        assert b"<priority>" not in resp.data

    def test_robots(self, client):
        resp = client.get("/robots.txt")
        assert resp.status_code == 200
        assert b"Sitemap:" in resp.data
        assert b"User-agent: GPTBot" in resp.data
        assert b"Disallow: /admin" in resp.data
        assert b"Disallow: /*?*" in resp.data

    def test_sitemap_txt(self, client):
        resp = client.get("/sitemap.txt")
        assert resp.status_code == 200
        assert b"http://localhost/blogs/how-to-convert-bank-statements-to-excel" in resp.data

    def test_llms_txt(self, client):
        resp = client.get("/llms.txt")
        assert resp.status_code == 200
        assert b"# Statement Converter" in resp.data
        assert b"Machine-Readable Discovery" in resp.data

    def test_security_txt(self, client):
        resp = client.get("/.well-known/security.txt")
        assert resp.status_code == 200
        assert b"Contact:" in resp.data
        assert b"Canonical: http://localhost/.well-known/security.txt" in resp.data

    def test_indexnow_key_is_404_until_configured(self, client):
        resp = client.get("/indexnow-key.txt")
        assert resp.status_code == 404

    def test_public_base_url_drives_canonical_and_sitemap(self, client, monkeypatch):
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://statement.example")
        home = client.get("/")
        sitemap = client.get("/sitemap.xml")
        assert b'<link rel="canonical" href="https://statement.example/">' in home.data
        assert b"<loc>https://statement.example/</loc>" in sitemap.data

    def test_noindex_header_on_functional_routes(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert resp.headers["X-Robots-Tag"] == "noindex, nofollow"

    def test_index_redirects(self, client):
        resp = client.get("/index")
        assert resp.status_code == 302

    def test_track_event_records_allowed_funnel_event(self, client):
        from db import get_db_session, init_db
        from models import FunnelEvent

        init_db()
        resp = client.post("/track/event", json={
            "event_type": "home_primary_cta_click",
            "path": "/",
        })

        assert resp.status_code == 200
        with get_db_session() as db:
            event = (
                db.query(FunnelEvent)
                .filter_by(event_type="home_primary_cta_click", path="/")
                .order_by(FunnelEvent.created_at.desc())
                .first()
            )
            assert event is not None
            db.delete(event)


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

    def test_account_usage_falls_back_to_completed_jobs(self, client):
        from db import get_db_session, init_db
        from models import Job, User

        suffix = uuid.uuid4().hex
        email = f"usage-{suffix}@example.com"
        job_id = f"usage-job-{suffix}"
        init_db()
        with get_db_session() as db:
            user = User(email=email)
            db.add(user)
            db.flush()
            user_id = user.id
            db.add(Job(
                id=job_id,
                user_id=user_id,
                filename="statement.pdf",
                status="completed",
                created_at=datetime.utcnow(),
            ))

        try:
            with client.session_transaction() as sess:
                sess["user_id"] = user_id
                sess["user_email"] = email
                sess["plan_id"] = "free"
                sess["plan_status"] = "free"
            resp = client.get("/account")
            assert resp.status_code == 200
            assert b"1 / 5 conversions used" in resp.data
        finally:
            with get_db_session() as db:
                job = db.get(Job, job_id)
                if job:
                    db.delete(job)
                user = db.get(User, user_id)
                if user:
                    db.delete(user)

    def test_admin_requires_admin(self, client):
        resp = client.get("/admin")
        assert resp.status_code == 302  # redirect to signin

    def test_admin_hides_for_non_admin_user(self, client):
        with client.session_transaction() as sess:
            sess["user_email"] = f"non-admin-{uuid.uuid4().hex}@example.com"
        resp = client.get("/admin")
        assert resp.status_code == 404

    def test_dashboard_allows_guest_uploads(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert b"Email is collected at download" in resp.data

    def test_convert_requires_file(self, client):
        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
        resp = client.post("/convert", data={
            "csrf_token": "test-token",
        })
        # Should fail gracefully (no file uploaded)
        assert resp.status_code in (302, 400, 500)

    def test_convert_preflight_accepts_valid_request(self, client):
        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"

        resp = client.post("/convert/preflight", data={
            "csrf_token": "test-token",
        })

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

    def test_convert_preflight_rejects_quota_before_upload(self, client, monkeypatch):
        import app as app_module

        monkeypatch.setattr(
            app_module,
            "check_conversion_quota",
            lambda user_id, guest_id: (
                False,
                {
                    "error": "You have reached your 5 conversions this month. Please upgrade to continue.",
                    "error_code": "USER_LIMIT_EXCEEDED",
                },
            ),
        )

        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
            sess["user_id"] = 123

        resp = client.post("/convert/preflight", data={
            "csrf_token": "test-token",
        })

        assert resp.status_code == 403
        data = resp.get_json()
        assert data["error_code"] == "USER_LIMIT_EXCEEDED"
        assert "conversions this month" in data["error"]

    def test_unlimited_quota_email_bypasses_monthly_limit(self, client, monkeypatch):
        from db import get_db_session, init_db
        from models import Job, User

        suffix = uuid.uuid4().hex
        email = f"owner-{suffix}@example.com"
        monkeypatch.setenv("UNLIMITED_QUOTA_EMAILS", email)
        init_db()

        with get_db_session() as db:
            user = User(email=email, plan_id="free", plan_status="free")
            db.add(user)
            db.flush()
            user_id = user.id
            for index in range(5):
                db.add(Job(
                    id=f"quota-bypass-{suffix}-{index}",
                    user_id=user_id,
                    filename=f"statement-{index}.pdf",
                    status="completed",
                    created_at=datetime.utcnow(),
                ))

        try:
            with client.session_transaction() as sess:
                sess["csrf_token"] = "test-token"
                sess["user_id"] = user_id
                sess["user_email"] = email
                sess["plan_id"] = "free"
                sess["plan_status"] = "free"

            preflight_resp = client.post("/convert/preflight", data={
                "csrf_token": "test-token",
            })
            assert preflight_resp.status_code == 200

            account_resp = client.get("/account")
            assert account_resp.status_code == 200
            assert b"conversions used (unlimited)" in account_resp.data
        finally:
            with get_db_session() as db:
                db.query(Job).filter(Job.id.like(f"quota-bypass-{suffix}-%")).delete(synchronize_session=False)
                user = db.get(User, user_id)
                if user:
                    db.delete(user)

    def test_convert_enqueues_without_importing_worker(self, client, monkeypatch):
        from reportlab.pdfgen import canvas

        sys.modules.pop("worker", None)
        task_id = f"task-{uuid.uuid4().hex}"
        mock_task = SimpleNamespace(id=task_id)
        mock_send = MagicMock(return_value=mock_task)
        fake_celery_config = SimpleNamespace(
            celery_app=SimpleNamespace(send_task=mock_send)
        )
        monkeypatch.setitem(sys.modules, "celery_config", fake_celery_config)

        pdf_buffer = BytesIO()
        c = canvas.Canvas(pdf_buffer)
        c.drawString(72, 720, "Test PDF")
        c.save()
        pdf_buffer.seek(0)

        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"

        resp = client.post(
            "/convert",
            data={
                "csrf_token": "test-token",
                "pdf_file": (pdf_buffer, "statement.pdf"),
                "quality": "standard",
                "extraction_mode": "layout_replica",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            content_type="multipart/form-data",
        )

        assert resp.status_code == 202
        data = resp.get_json()
        assert data["task_id"] == task_id
        mock_send.assert_called_once()
        assert mock_send.call_args.args[0] == "worker.process_pdf"
        assert "worker" not in sys.modules

    def test_download_email_capture_links_guest_job_to_user(self, client):
        from db import get_db_session, init_db
        from models import FunnelEvent, Job, User

        suffix = uuid.uuid4().hex
        guest_id = f"guest-{suffix}"
        job_id = f"download-email-job-{suffix}"
        email = f"download-{suffix}@example.com"
        init_db()
        with get_db_session() as db:
            db.add(Job(
                id=job_id,
                guest_id=guest_id,
                filename="statement.pdf",
                status="completed",
                created_at=datetime.utcnow(),
            ))

        try:
            with client.session_transaction() as sess:
                sess["guest_id"] = guest_id
                sess["csrf_token"] = "test-token"
            resp = client.post("/download/email", data={
                "job_id": job_id,
                "filename": "statement.xlsx",
                "email": email,
                "csrf_token": "test-token",
            })

            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert f"/download/{job_id}/statement.xlsx" in data["download_url"]

            with get_db_session() as db:
                user = db.query(User).filter_by(email=email).first()
                job = db.get(Job, job_id)
                assert user is not None
                assert job.user_id == user.id
        finally:
            with get_db_session() as db:
                db.query(FunnelEvent).filter_by(job_id=job_id).delete(synchronize_session=False)
                job = db.get(Job, job_id)
                if job:
                    db.delete(job)
                user = db.query(User).filter_by(email=email).first()
                if user:
                    db.delete(user)


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


def test_blogs_listing_is_excerpt_only(client):
    resp = client.get("/blogs")
    assert resp.status_code == 200
    assert b"Blog and Guides" in resp.data
    # sentences that live only in post BODIES must not leak into the listing
    assert b"Pivot tables" not in resp.data
    assert b"drag-and-drop or browse" not in resp.data


def test_new_universal_parser_posts_render(client):
    resp = client.get("/blogs/how-exact-copy-extraction-works")
    assert resp.status_code == 200
    assert b"Exact-Copy Extraction" in resp.data
    resp = client.get("/blogs/bank-statements-to-excel-for-reconciliation")
    assert resp.status_code == 200
    assert b"Reconciliation" in resp.data
