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


@pytest.fixture
def authenticated_session(client):
    """Attach a real active magic-link-style user session to the test client."""
    from db import get_db_session, init_db
    from models import (
        AuthToken, FeedbackSubmission, FunnelEvent, Job, LoginEvent,
        SiteVisit, UsageCounter, User,
    )

    init_db()
    email = f"route-auth-{uuid.uuid4().hex}@example.com"
    with get_db_session() as db:
        user = User(email=email, plan_id="free", plan_status="free", is_active=True)
        db.add(user)
        db.flush()
        user_id = user.id

    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["user_email"] = email
        sess["role"] = "user"
        sess["plan_id"] = "free"
        sess["plan_status"] = "free"

    yield user_id

    with get_db_session() as db:
        db.query(FunnelEvent).filter_by(user_id=user_id).delete(synchronize_session=False)
        db.query(FeedbackSubmission).filter_by(user_id=user_id).delete(synchronize_session=False)
        db.query(UsageCounter).filter_by(user_id=user_id).delete(synchronize_session=False)
        db.query(AuthToken).filter_by(user_id=user_id).delete(synchronize_session=False)
        db.query(LoginEvent).filter_by(user_id=user_id).delete(synchronize_session=False)
        db.query(SiteVisit).filter_by(user_id=user_id).delete(synchronize_session=False)
        db.query(Job).filter_by(user_id=user_id).delete(synchronize_session=False)
        user = db.get(User, user_id)
        if user:
            db.delete(user)


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

    def test_health_detailed_requires_admin(self, client):
        resp = client.get("/health/detailed")
        assert resp.status_code == 404

    def test_health_detailed_for_admin(self, client, monkeypatch):
        import redis_utils
        import routes.pages as pages_module

        monkeypatch.setattr(pages_module, "_require_admin", lambda: True)
        monkeypatch.setattr(pages_module, "_check_parser_health", lambda: None)
        monkeypatch.setattr(
            redis_utils,
            "get_redis_client",
            lambda: SimpleNamespace(ping=lambda: True),
        )
        resp = client.get("/health/detailed")
        data = resp.get_json()
        assert resp.status_code == 200
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
        assert b"Sitemap: http://localhost/sitemap.xml" in resp.data
        assert b"User-agent: GPTBot" in resp.data
        assert b"Disallow: /admin" in resp.data
        assert b"Disallow: /*?*" not in resp.data
        # Public bank guides are explicitly crawlable; only preflight stays private.
        assert b"Allow: /convert/" in resp.data
        assert b"Disallow: /convert$" not in resp.data
        assert b"Disallow: /convert\n" not in resp.data
        assert b"Disallow: /convert/preflight" in resp.data
        assert b"Sitemap: http://localhost/sitemap.txt" not in resp.data
        assert resp.data.endswith(b"\n")

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

    def test_schemeless_base_url_gets_https(self, client, monkeypatch):
        monkeypatch.setenv("CANONICAL_BASE_URL", "statement.example/")
        home = client.get("/")
        sitemap = client.get("/sitemap.xml")
        robots = client.get("/robots.txt")
        assert b'<link rel="canonical" href="https://statement.example/">' in home.data
        assert b"<loc>https://statement.example/</loc>" in sitemap.data
        assert b"Sitemap: https://statement.example/sitemap.xml" in robots.data
        assert b"statement.example/statement.example" not in home.data

    def test_noindex_header_on_functional_routes(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert resp.headers["X-Robots-Tag"] == "noindex, nofollow"

    def test_index_redirects(self, client):
        resp = client.get("/index")
        assert resp.status_code == 308
        assert resp.headers["Location"].endswith("/")

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

    @patch("routes.auth.send_magic_link_email")
    def test_auth_link_uses_configured_public_origin(self, mock_send, client):
        with patch.dict(os.environ, {
            "CANONICAL_BASE_URL": "https://canonical.example",
            "PUBLIC_BASE_URL": "https://canonical.example",
        }):
            with client.session_transaction() as sess:
                sess["csrf_token"] = "test-token"
            resp = client.post("/auth/start", data={
                "email": f"canonical-{uuid.uuid4().hex}@example.com",
                "csrf_token": "test-token",
            })

        assert resp.status_code == 302
        assert mock_send.call_args.args[1].startswith(
            "https://canonical.example/auth/verify?token="
        )

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

    def test_dashboard_is_public_but_guests_cannot_upload(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert b"Secure sign-in required" in resp.data
        assert b"Sign in to convert" in resp.data
        assert b'id="uploadForm"' not in resp.data
        assert b'/static/script.min.js' not in resp.data

    def test_authenticated_dashboard_renders_upload_form(
        self, client, authenticated_session,
    ):
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert b'id="uploadForm"' in resp.data
        assert b'/static/script.min.js' in resp.data

    def test_dashboard_restores_profile_from_valid_user_id(
        self, client, authenticated_session,
    ):
        with client.session_transaction() as sess:
            sess.pop("user_email", None)
            sess.pop("role", None)
            sess.pop("plan_id", None)
            sess.pop("plan_status", None)

        resp = client.get("/dashboard")

        assert resp.status_code == 200
        assert b'id="uploadForm"' in resp.data
        with client.session_transaction() as sess:
            assert sess["user_id"] == authenticated_session
            assert sess["user_email"].endswith("@example.com")

    def test_inactive_user_dashboard_hides_upload_interface(
        self, client, authenticated_session,
    ):
        from db import get_db_session
        from models import User

        with get_db_session() as db:
            user = db.get(User, authenticated_session)
            user.is_active = False

        resp = client.get("/dashboard")

        assert resp.status_code == 200
        assert b"Secure sign-in required" in resp.data
        assert b'id="uploadForm"' not in resp.data
        assert b'/static/script.min.js' not in resp.data

    def test_anonymous_preflight_requires_login_even_when_quotas_are_disabled(self, client):
        resp = client.post(
            "/convert/preflight",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        assert resp.status_code == 401
        assert resp.get_json() == {
            "status": "error",
            "error": "Please sign in with your email before converting a statement.",
            "error_code": "LOGIN_REQUIRED",
            "requires_login": True,
            "signin_url": "/signin",
        }

    def test_anonymous_convert_is_rejected_before_request_validation(self, client):
        ajax_resp = client.post(
            "/convert",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert ajax_resp.status_code == 401
        assert ajax_resp.get_json()["error_code"] == "LOGIN_REQUIRED"

        browser_resp = client.post("/convert", follow_redirects=False)
        assert browser_resp.status_code == 303
        assert browser_resp.headers["Location"].endswith("/signin")

    @pytest.mark.parametrize("session_state", ["missing", "inactive"])
    def test_stale_or_inactive_user_session_cannot_convert(
        self, client, session_state,
    ):
        from db import get_db_session, init_db
        from models import User

        init_db()
        user_id = f"stale-route-user-{uuid.uuid4().hex}"
        if session_state == "inactive":
            with get_db_session() as db:
                db.add(User(
                    id=user_id,
                    email=f"{user_id}@example.com",
                    is_active=False,
                ))

        try:
            with client.session_transaction() as sess:
                sess["user_id"] = user_id
                sess["user_email"] = f"{user_id}@example.com"
                sess["role"] = "admin"
                sess["plan_id"] = "pro"
                sess["plan_status"] = "active"
                sess["csrf_token"] = "test-token"

            resp = client.post(
                "/convert/preflight",
                data={"csrf_token": "test-token"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

            assert resp.status_code == 401
            assert resp.get_json()["error_code"] == "LOGIN_REQUIRED"
            with client.session_transaction() as sess:
                assert "user_id" not in sess
                assert "user_email" not in sess
                assert "role" not in sess
                assert "plan_id" not in sess
                assert "plan_status" not in sess
                assert sess["csrf_token"] == "test-token"
        finally:
            if session_state == "inactive":
                with get_db_session() as db:
                    user = db.get(User, user_id)
                    if user:
                        db.delete(user)

    def test_conversion_auth_validation_fails_closed_when_database_is_unavailable(
        self, client, monkeypatch,
    ):
        import routes.converter as converter_module

        def unavailable():
            raise converter_module.ConversionSessionValidationError

        monkeypatch.setattr(converter_module, "_active_session_user_id", unavailable)

        dashboard = client.get("/dashboard")
        preflight = client.post(
            "/convert/preflight",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        convert = client.post(
            "/convert",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        assert dashboard.status_code == 503
        assert b"Sign-in verification is unavailable" in dashboard.data
        assert b'id="uploadForm"' not in dashboard.data
        assert preflight.status_code == 503
        assert preflight.get_json()["error_code"] == "AUTH_UNAVAILABLE"
        assert convert.status_code == 503
        assert convert.get_json()["error_code"] == "AUTH_UNAVAILABLE"

    def test_user_deactivated_after_preflight_is_blocked_before_upload(
        self, client, authenticated_session,
    ):
        from db import get_db_session
        from models import User

        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
        preflight = client.post(
            "/convert/preflight",
            data={"csrf_token": "test-token"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert preflight.status_code == 200

        with get_db_session() as db:
            user = db.get(User, authenticated_session)
            user.is_active = False

        resp = client.post(
            "/convert",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 401
        assert resp.get_json()["error_code"] == "LOGIN_REQUIRED"

    def test_convert_requires_file(self, client, authenticated_session):
        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
        resp = client.post("/convert", data={
            "csrf_token": "test-token",
        })
        # Should fail gracefully (no file uploaded)
        assert resp.status_code in (302, 400, 500)

    def test_convert_preflight_accepts_valid_request(self, client, authenticated_session):
        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"

        resp = client.post("/convert/preflight", data={
            "csrf_token": "test-token",
        })

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

    def test_convert_preflight_rejects_quota_before_upload(
        self, client, authenticated_session, monkeypatch,
    ):
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

    def test_convert_enqueues_without_importing_worker(
        self, client, authenticated_session, monkeypatch,
    ):
        from db import get_db_session
        from models import Job
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
                "extraction_mode": "structured_transactions",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            content_type="multipart/form-data",
        )

        assert resp.status_code == 202
        data = resp.get_json()
        assert data["task_id"] == task_id
        mock_send.assert_called_once()
        assert mock_send.call_args.args[0] == "worker.process_pdf"
        file_ref = mock_send.call_args.kwargs["args"][0]
        assert file_ref["extraction_mode"] == "layout_replica"
        assert "worker" not in sys.modules
        with get_db_session() as db:
            job = db.get(Job, data["job_id"])
            assert job.user_id == authenticated_session
            assert job.guest_id is None

    def test_convert_rejects_file_over_50_mb_before_parsing(
        self, client, authenticated_session, monkeypatch, tmp_path,
    ):
        import app as app_module
        import routes.converter as converter_module

        parser_probe = MagicMock(side_effect=AssertionError("page parser must not run"))
        storage_probe = MagicMock(side_effect=AssertionError("storage must not run"))
        monkeypatch.setattr(app_module, "UPLOAD_FOLDER", str(tmp_path))
        monkeypatch.setattr(app_module, "MAX_UPLOAD_MB", 50)
        monkeypatch.setattr(app_module, "rate_limited", lambda *args, **kwargs: False)
        monkeypatch.setattr(
            converter_module,
            "_saved_file_size",
            lambda path: 50 * 1024 * 1024 + 1,
        )
        monkeypatch.setattr(converter_module, "get_pdf_page_count", parser_probe)
        monkeypatch.setattr(converter_module, "get_storage_config", storage_probe)

        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
        resp = client.post(
            "/convert",
            data={
                "csrf_token": "test-token",
                "pdf_file": (BytesIO(b"%PDF-1.4\n%%EOF\n"), "oversized.pdf"),
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            content_type="multipart/form-data",
        )

        assert resp.status_code == 413
        assert resp.get_json()["error_code"] == "FILE_TOO_LARGE"
        assert resp.get_json()["max_mb"] == 50
        assert list(tmp_path.iterdir()) == []
        parser_probe.assert_not_called()
        storage_probe.assert_not_called()

    def test_convert_does_not_upload_or_enqueue_when_job_insert_fails(
        self, client, authenticated_session, monkeypatch, tmp_path,
    ):
        from contextlib import contextmanager
        import app as app_module
        import routes.converter as converter_module

        uploaded = []
        deleted = []
        send_task = MagicMock()

        @contextmanager
        def failing_db_session():
            raise RuntimeError("database unavailable")
            yield

        monkeypatch.setattr(app_module, "UPLOAD_FOLDER", str(tmp_path))
        monkeypatch.setattr(app_module, "rate_limited", lambda *args, **kwargs: False)
        monkeypatch.setattr(converter_module, "get_pdf_page_count", lambda path: 1)
        monkeypatch.setattr(
            converter_module,
            "get_storage_config",
            lambda: {"bucket": "test"},
        )
        monkeypatch.setattr(
            converter_module,
            "upload_file",
            lambda storage, path, key: uploaded.append(key),
        )
        monkeypatch.setattr(
            converter_module,
            "delete_file",
            lambda storage, key: deleted.append(key),
        )
        monkeypatch.setattr(converter_module, "get_db_session", failing_db_session)
        monkeypatch.setattr(
            converter_module,
            "_active_session_user_id",
            lambda: authenticated_session,
        )
        monkeypatch.setitem(
            sys.modules,
            "celery_config",
            SimpleNamespace(celery_app=SimpleNamespace(send_task=send_task)),
        )

        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
        resp = client.post(
            "/convert",
            data={
                "csrf_token": "test-token",
                "pdf_file": (BytesIO(b"%PDF-1.4\n%%EOF\n"), "statement.pdf"),
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            content_type="multipart/form-data",
        )

        assert resp.status_code == 503
        assert resp.get_json()["error_code"] == "CONVERSION_UNAVAILABLE"
        assert uploaded == []
        assert deleted == []
        assert list(tmp_path.iterdir()) == []
        send_task.assert_not_called()

    def test_enqueue_failure_keeps_s3_key_when_immediate_delete_fails(
        self, client, authenticated_session, monkeypatch, tmp_path,
    ):
        import app as app_module
        import routes.converter as converter_module
        from db import get_db_session
        from models import Job

        uploaded = []

        def send_failure(*args, **kwargs):
            raise RuntimeError("broker unavailable")

        def delete_failure(storage, key):
            raise RuntimeError("object storage unavailable")

        monkeypatch.setattr(app_module, "UPLOAD_FOLDER", str(tmp_path))
        monkeypatch.setattr(app_module, "rate_limited", lambda *args, **kwargs: False)
        monkeypatch.setattr(converter_module, "get_pdf_page_count", lambda path: 1)
        monkeypatch.setattr(converter_module, "get_storage_config", lambda: {"bucket": "test"})
        monkeypatch.setattr(
            converter_module,
            "upload_file",
            lambda storage, path, key: uploaded.append(key),
        )
        monkeypatch.setattr(converter_module, "delete_file", delete_failure)
        monkeypatch.setattr(
            converter_module,
            "get_redis_client",
            lambda: SimpleNamespace(delete=lambda *keys: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "celery_config",
            SimpleNamespace(celery_app=SimpleNamespace(send_task=send_failure)),
        )

        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
        resp = client.post(
            "/convert",
            data={
                "csrf_token": "test-token",
                "pdf_file": (BytesIO(b"%PDF-1.4\n%%EOF\n"), "statement.pdf"),
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            content_type="multipart/form-data",
        )

        assert resp.status_code == 503
        assert len(uploaded) == 1
        job_id = uploaded[0].split('/')[1]
        try:
            with get_db_session() as db:
                job = db.get(Job, job_id)
                assert job.status == "failed"
                assert job.input_storage_key == uploaded[0]
                assert job.storage_key == uploaded[0]
        finally:
            with get_db_session() as db:
                job = db.get(Job, job_id)
                if job:
                    db.delete(job)

    def test_exactly_50_mb_is_within_the_upload_limit(self):
        from routes.converter import _exceeds_upload_limit

        assert _exceeds_upload_limit(50 * 1024 * 1024, 50) is False
        assert _exceeds_upload_limit(50 * 1024 * 1024 + 1, 50) is True

    def test_status_reads_json_and_remains_owner_protected(self, client, monkeypatch):
        import json
        import app as app_module
        import routes.converter as converter_module
        from db import get_db_session, init_db
        from models import Job

        suffix = uuid.uuid4().hex
        job_id = f"status-{suffix}"
        owner_guest_id = f"owner-{suffix}"
        init_db()
        with get_db_session() as db:
            db.add(Job(
                id=job_id,
                guest_id=owner_guest_id,
                filename="statement.pdf",
                status="completed",
                output_storage_key=f"outputs/{job_id}/statement.xlsx",
            ))

        redis_status = json.dumps({
            "status": "Completed successfully",
            "percent": 100,
            "storage": "s3",
            "download_key": f"outputs/{job_id}/statement.xlsx",
        }).encode("utf-8")
        fake_redis = SimpleNamespace(get=lambda key: redis_status)
        monkeypatch.setattr(app_module, "rate_limited", lambda *args, **kwargs: False)
        monkeypatch.setattr(converter_module, "get_redis_client", lambda: fake_redis)

        try:
            with client.session_transaction() as sess:
                sess["guest_id"] = owner_guest_id
            owner_resp = client.get(f"/status/{job_id}")
            assert owner_resp.status_code == 200
            assert owner_resp.get_json()["download_url"].endswith(
                f"/download/{job_id}/statement.xlsx"
            )

            with client.session_transaction() as sess:
                sess["guest_id"] = f"other-{suffix}"
            other_resp = client.get(f"/status/{job_id}")
            assert other_resp.status_code == 404
        finally:
            with get_db_session() as db:
                job = db.get(Job, job_id)
                if job:
                    db.delete(job)

    def test_completed_status_falls_back_to_db_when_redis_is_down(self, client, monkeypatch):
        import app as app_module
        import routes.converter as converter_module
        from db import get_db_session, init_db
        from models import Job

        suffix = uuid.uuid4().hex
        job_id = f"status-fallback-{suffix}"
        guest_id = f"guest-{suffix}"
        output_key = f"outputs/{job_id}/statement.xlsx"
        init_db()
        with get_db_session() as db:
            db.add(Job(
                id=job_id,
                guest_id=guest_id,
                filename="statement.pdf",
                status="completed",
                output_storage_key=output_key,
            ))

        def redis_down():
            raise RuntimeError("redis unavailable")

        monkeypatch.setattr(app_module, "RATE_LIMIT_FAIL_CLOSED", True)
        monkeypatch.setattr(app_module, "get_redis_client", redis_down)
        monkeypatch.setattr(converter_module, "get_redis_client", redis_down)

        try:
            with client.session_transaction() as sess:
                sess["guest_id"] = guest_id
            resp = client.get(f"/status/{job_id}")

            assert resp.status_code == 200
            data = resp.get_json()
            assert data["percent"] == 100
            assert data["requires_download_email"] is False
            assert data["confidence"] == "unknown"
            assert data["review_required"] is True
            assert data["extraction_rows"] == 0
            assert data["table_row_count"] == 0
            assert data["download_url"].endswith(
                f"/download/{job_id}/statement.xlsx"
            )
        finally:
            with get_db_session() as db:
                job = db.get(Job, job_id)
                if job:
                    db.delete(job)

    def test_terminal_db_status_beats_stale_redis_and_supports_legacy_storage(
        self, client, monkeypatch,
    ):
        import json
        import app as app_module
        import routes.converter as converter_module
        from db import get_db_session, init_db
        from models import Job

        suffix = uuid.uuid4().hex
        job_id = f"legacy-status-{suffix}"
        guest_id = f"guest-{suffix}"
        legacy_key = f"outputs/{job_id}/legacy.xlsx"
        init_db()
        with get_db_session() as db:
            db.add(Job(
                id=job_id,
                guest_id=guest_id,
                filename="statement.pdf",
                status="completed",
                storage_key=legacy_key,
                transaction_count=12,
            ))

        stale_status = json.dumps({
            "status": "Processing page 1",
            "percent": 10,
        }).encode("utf-8")
        fake_redis = SimpleNamespace(get=lambda key: stale_status)
        monkeypatch.setattr(app_module, "rate_limited", lambda *args, **kwargs: False)
        monkeypatch.setattr(converter_module, "get_redis_client", lambda: fake_redis)

        try:
            with client.session_transaction() as sess:
                sess["guest_id"] = guest_id
            resp = client.get(f"/status/{job_id}")
            data = resp.get_json()

            assert resp.status_code == 200
            assert data["percent"] == 100
            assert data["status"] == "Completed - workbook ready for review"
            assert data["confidence"] == "unknown"
            assert data["review_required"] is True
            assert data["extraction_rows"] == 0
            assert data["table_row_count"] == 12
            assert data["download_url"].endswith(
                f"/download/{job_id}/legacy.xlsx"
            )
        finally:
            with get_db_session() as db:
                job = db.get(Job, job_id)
                if job:
                    db.delete(job)

    def test_legacy_download_email_capture_does_not_create_or_claim_user(self, client):
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
                assert user is None
                assert job.user_id is None
            with client.session_transaction() as sess:
                assert sess["download_email_jobs"][job_id] == email
        finally:
            with get_db_session() as db:
                db.query(FunnelEvent).filter_by(job_id=job_id).delete(synchronize_session=False)
                job = db.get(Job, job_id)
                if job:
                    db.delete(job)
                user = db.query(User).filter_by(email=email).first()
                if user:
                    db.delete(user)

    def test_signout_cannot_access_user_job_through_preserved_guest_cookie(
        self, client, authenticated_session,
    ):
        from db import get_db_session
        from models import Job

        suffix = uuid.uuid4().hex
        job_id = f"user-owned-{suffix}"
        guest_id = f"browser-{suffix}"
        with get_db_session() as db:
            db.add(Job(
                id=job_id,
                user_id=authenticated_session,
                guest_id=guest_id,
                filename="statement.pdf",
                status="queued",
            ))

        with client.session_transaction() as sess:
            sess["guest_id"] = guest_id

        assert client.get(f"/status/{job_id}").status_code == 200
        signout = client.get("/signout", follow_redirects=False)
        assert signout.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("guest_id") == guest_id
            assert "user_id" not in sess

        assert client.get(f"/status/{job_id}").status_code == 404

    def test_deactivated_user_cannot_access_existing_job(
        self, client, authenticated_session,
    ):
        from db import get_db_session
        from models import Job, User

        job_id = f"inactive-owner-{uuid.uuid4().hex}"
        with get_db_session() as db:
            db.add(Job(
                id=job_id,
                user_id=authenticated_session,
                filename="statement.pdf",
                status="queued",
            ))

        with get_db_session() as db:
            user = db.get(User, authenticated_session)
            user.is_active = False

        assert client.get(f"/status/{job_id}").status_code == 404
        with client.session_transaction() as sess:
            assert "user_id" not in sess
            assert "user_email" not in sess


class TestRateLimiting:
    """Test rate limiter behavior when Redis is unavailable."""

    @staticmethod
    def _failing_redis_client():
        raise RuntimeError("redis down")

    def test_rate_limit_backend_failure_defaults_open(self, monkeypatch):
        import app as app_module

        monkeypatch.setattr(app_module, "RATE_LIMIT_FAIL_CLOSED", False)
        monkeypatch.setattr(app_module, "get_redis_client", self._failing_redis_client)

        assert app_module.rate_limited("rate:test", 1, 60) is False

    def test_rate_limit_backend_failure_can_fail_closed(self, monkeypatch):
        import app as app_module

        monkeypatch.setattr(app_module, "RATE_LIMIT_FAIL_CLOSED", True)
        monkeypatch.setattr(app_module, "get_redis_client", self._failing_redis_client)

        assert app_module.rate_limited("rate:test", 1, 60) is True

    def test_rate_limit_increment_and_expiry_are_atomic(self, monkeypatch):
        import app as app_module

        calls = []

        class FakeRedis:
            def eval(self, script, key_count, key, window):
                calls.append((script, key_count, key, window))
                return 2

        monkeypatch.setattr(app_module, "get_redis_client", lambda: FakeRedis())

        assert app_module.rate_limited("rate:atomic", 1, 60) is True
        script, key_count, key, window = calls[0]
        assert "INCR" in script and "TTL" in script and "EXPIRE" in script
        assert (key_count, key, window) == (1, "rate:atomic", 60)


class TestProxyTrust:
    def test_client_ip_ignores_spoofed_forwarded_for(self):
        import app as app_module

        with app_module.app.test_request_context(
            "/",
            headers={"X-Forwarded-For": "203.0.113.99"},
            environ_base={"REMOTE_ADDR": "198.51.100.10"},
        ):
            assert app_module.get_client_ip() == "198.51.100.10"

    def test_client_ip_ignores_untrusted_railway_real_ip(self):
        import app as app_module

        with app_module.app.test_request_context(
            "/",
            headers={
                "X-Real-IP": "203.0.113.25",
                "X-Forwarded-For": "192.0.2.55",
            },
            environ_base={"REMOTE_ADDR": "198.51.100.10"},
        ):
            assert app_module.get_client_ip() == "198.51.100.10"

    def test_production_rejects_noncanonical_host(self, client, monkeypatch):
        import app as app_module

        monkeypatch.setattr(app_module, "IS_PRODUCTION", True)
        monkeypatch.setenv("CANONICAL_BASE_URL", "https://canonical.example")

        resp = client.get("/", headers={"Host": "raw-origin.example"})
        assert resp.status_code == 421
        assert client.get("/health", headers={"Host": "raw-origin.example"}).status_code == 200

    def test_www_alias_redirects_to_canonical_host(self, client, monkeypatch):
        monkeypatch.setenv("CANONICAL_BASE_URL", "https://canonical.example")
        resp = client.get("/pricing?src=test", headers={"Host": "www.canonical.example"})
        assert resp.status_code == 308
        assert resp.headers["Location"] == "https://canonical.example/pricing?src=test"


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


def test_stripe_urls_use_configured_origin_not_request_host(client, monkeypatch):
    import app as app_module
    import routes.billing as billing_module
    from db import get_db_session, init_db
    from models import User

    email = f"stripe-origin-{uuid.uuid4().hex}@example.com"
    init_db()
    with get_db_session() as db:
        user = User(email=email, stripe_customer_id="cus_origin_test")
        db.add(user)
        db.flush()
        user_id = user.id

    checkout_create = MagicMock(return_value=SimpleNamespace(url="https://checkout.stripe.test"))
    portal_create = MagicMock(return_value=SimpleNamespace(url="https://portal.stripe.test"))
    monkeypatch.setattr(billing_module.stripe.checkout.Session, "create", checkout_create)
    monkeypatch.setattr(billing_module.stripe.billing_portal.Session, "create", portal_create)
    monkeypatch.setitem(app_module.PLAN_CONFIG["pro"], "stripe_price_id", "price_origin_test")

    attacker_base = "http://attacker.example"
    try:
        with patch.dict(os.environ, {
            "CANONICAL_BASE_URL": "https://canonical.example",
            "PUBLIC_BASE_URL": "https://canonical.example",
        }):
            with client.session_transaction(base_url=attacker_base) as sess:
                sess["user_id"] = user_id
                sess["csrf_token"] = "stripe-csrf"

            checkout_resp = client.post(
                "/checkout/create",
                base_url=attacker_base,
                data={"plan_id": "pro", "csrf_token": "stripe-csrf"},
            )
            portal_resp = client.post(
                "/billing/portal",
                base_url=attacker_base,
                data={"csrf_token": "stripe-csrf"},
            )

        assert checkout_resp.status_code == 200
        checkout_kwargs = checkout_create.call_args.kwargs
        assert checkout_kwargs["success_url"] == (
            "https://canonical.example/checkout/success"
            "?session_id={CHECKOUT_SESSION_ID}"
        )
        assert checkout_kwargs["cancel_url"] == "https://canonical.example/pricing"
        assert portal_resp.status_code == 302
        assert portal_create.call_args.kwargs["return_url"] == (
            "https://canonical.example/pricing"
        )
    finally:
        with get_db_session() as db:
            user = db.get(User, user_id)
            if user:
                db.delete(user)


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


def test_marketing_pages_skip_socketio(client):
    for path in ("/", "/pricing", "/blogs"):
        assert b"socket.io" not in client.get(path).data, path


def test_dashboard_does_not_load_socketio(client):
    assert b"socket.io" not in client.get("/dashboard").data


def test_dashboard_advertises_enforced_limit_and_splitter(
    client, authenticated_session,
):
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert b"Max file size: 50 MB" in resp.data
    assert b"https://smallpdfsplit.online/" in resp.data
    assert b"Beta:" not in resp.data
    assert b"active testing" not in resp.data
    assert b"Current beta scope" not in resp.data
    assert b"designed to preserve its visual rows and column positions" in resp.data
    assert b"Leave this unchecked to delete the source PDF after processing" in resp.data
    assert b"Reconstructing rows and columns" in resp.data

    script = client.get("/static/script.js").data
    assert b"window.addEventListener('pageshow'" in script
    assert b"event.persisted && activeJobId" in script

    minified_script = client.get("/static/script.min.js").data
    assert b"initializeWebSocket" not in minified_script
    assert b"socket.io" not in minified_script
    assert b"feedbackCsrfToken" in minified_script
    assert b"pageshow" in minified_script
    assert b"Unsupported language" in minified_script
    assert b"review_required" in minified_script
    # Terser may rename local variables; assert the stable XSS-safe assignment.
    assert b"textContent=String(" in minified_script

    script_source = script.decode("utf-8")
    assert "confidence === 'good' && !reviewRequired" in script_source
    assert "confidence === 'low' || reviewRequired" in script_source
    assert "confidence === 'low' || data.review_required" in script_source


def test_non_ajax_processing_page_handles_terminal_statuses():
    template = (
        Path(__file__).parent.parent / "templates" / "processing.html"
    ).read_text()

    assert "response.status >= 400 && response.status < 500" in template
    assert "status.startsWith('Unsupported language')" in template
    assert "data.state === 'unsupported_language'" in template
    assert "statusFinished = true" in template


def test_worker_reserves_remote_output_before_upload_and_checks_db_commit():
    worker_source = (Path(__file__).parent.parent / "worker.py").read_text()

    first_reservation = worker_source.index('Unable to reserve conversion output storage.')
    first_output_upload = worker_source.index('upload_file(storage, excel_path, output_storage_key)')
    assert first_reservation < first_output_upload
    assert worker_source.count('Unable to persist completed conversion.') == 2
    assert 'if storage and output_dir and os.path.exists(output_dir):' in worker_source


def test_home_does_not_run_retention_cleanup(client, monkeypatch):
    import app as app_module

    def unexpected_cleanup(*args, **kwargs):
        raise AssertionError("retention cleanup ran during a page request")

    monkeypatch.setattr(app_module, "cleanup_old_files", unexpected_cleanup)
    monkeypatch.setattr(app_module, "cleanup_feedback_shared_pdfs", unexpected_cleanup)
    monkeypatch.setattr(app_module, "cleanup_expired_s3_results", unexpected_cleanup)
    monkeypatch.setattr(app_module, "cleanup_first_party_analytics", unexpected_cleanup)
    assert client.get("/").status_code == 200
