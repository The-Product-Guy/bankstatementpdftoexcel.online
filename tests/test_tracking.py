import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DISABLE_QUOTAS", "true")


def test_should_track_page_view_filters_non_content_routes():
    from tracking import is_probable_bot, should_track_page_view

    human_agent = "Mozilla/5.0 AppleWebKit/537.36"
    assert should_track_page_view("/", "GET", 200, "text/html", human_agent) is True
    assert should_track_page_view("/pricing", "GET", 200, "text/html", human_agent) is True
    assert should_track_page_view("/", "GET", 200, "text/html", "Googlebot/2.1") is False
    assert should_track_page_view("/static/styles.css", "GET", 200, "text/css") is False
    assert should_track_page_view("/auth/verify", "GET", 302, "text/html") is False
    assert should_track_page_view("/health", "GET", 200, "text/html") is False
    assert should_track_page_view("/", "POST", 200, "text/html") is False
    assert should_track_page_view("/", "GET", 404, "text/html") is False
    assert is_probable_bot("Mozilla/5.0 AppleWebKit/537.36") is False
    assert is_probable_bot("Mozilla/5.0 (compatible; Googlebot/2.1)") is True
    assert is_probable_bot("SiteAuditBot/1.0") is True
    assert is_probable_bot("") is True


def test_public_page_sets_visitor_cookie():
    from app import app
    from tracking import VISITOR_COOKIE

    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/")

    cookies = resp.headers.getlist("Set-Cookie")
    assert resp.status_code == 200
    assert any(cookie.startswith(f"{VISITOR_COOKIE}=") for cookie in cookies)


def test_bot_page_view_does_not_set_visitor_cookie():
    from app import app
    from tracking import VISITOR_COOKIE

    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/", headers={
            "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
        })

    cookies = resp.headers.getlist("Set-Cookie")
    assert resp.status_code == 200
    assert not any(cookie.startswith(f"{VISITOR_COOKIE}=") for cookie in cookies)


def test_tracking_enqueue_is_fail_open_and_disables_publish_retry(monkeypatch):
    from tracking import enqueue_page_view

    calls = []

    def send_task(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("broker unavailable")

    monkeypatch.setitem(
        sys.modules,
        "celery_config",
        SimpleNamespace(celery_app=SimpleNamespace(send_task=send_task)),
    )

    enqueue_page_view(
        visitor_id=str(uuid.uuid4()),
        user_id=None,
        path="/pricing",
        referrer="",
        ip="127.0.0.1",
        user_agent="Mozilla/5.0",
    )

    assert calls[0][0][0] == "maintenance.persist_page_view"
    assert calls[0][1]["retry"] is False


def test_retention_sweep_is_scheduled_hourly():
    config_source = (Path(__file__).parent.parent / "celery_config.py").read_text()

    assert "'hourly-retention-sweep'" in config_source
    assert "'task': 'maintenance.run_retention_sweep'" in config_source
    assert "'schedule': 3600.0" in config_source
    assert "'options': {'queue': 'maintenance'}" in config_source


def test_auth_verify_logs_successful_login():
    from db import get_db_session, init_db
    from models import AuthToken, LoginEvent, User
    from routes.auth import hash_token
    from app import app

    suffix = uuid.uuid4().hex
    email = f"tracking-login-{suffix}@example.com"
    raw_token = f"tracking-token-{suffix}"
    init_db()
    with get_db_session() as db:
        user = db.query(User).filter_by(email=email).first()
        if not user:
            user = User(email=email)
            db.add(user)
            db.flush()
        db.add(AuthToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        ))
        user_id = user.id

    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get(f"/auth/verify?token={raw_token}")

    assert resp.status_code == 302
    with get_db_session() as db:
        event = (
            db.query(LoginEvent)
            .filter_by(user_id=user_id, event_type="login_success")
            .order_by(LoginEvent.created_at.desc())
            .first()
        )
        assert event is not None
        assert event.email == email


def test_admin_dashboard_shows_visit_and_login_metrics():
    from app import app
    from db import get_db_session, init_db
    from models import FeedbackSubmission, FunnelEvent, Job, LoginEvent, SiteVisit, User

    suffix = uuid.uuid4().hex
    email = f"admin-{suffix}@example.com"
    init_db()
    with get_db_session() as db:
        user = User(email=email, role="admin")
        db.add(user)
        db.flush()
        user_id = user.id
        db.add(SiteVisit(visitor_id=str(uuid.uuid4()), user_id=user_id, path="/pricing"))
        db.add(LoginEvent(
            user_id=user_id,
            email=email,
            event_type="magic_link_requested",
            success=True,
        ))
        db.add(LoginEvent(
            user_id=user_id,
            email=email,
            event_type="login_success",
            success=True,
        ))
        job = Job(id=f"admin-feedback-{suffix}", user_id=user_id, filename="statement.pdf", status="completed")
        db.add(job)
        db.add(FeedbackSubmission(
            job_id=job.id,
            user_id=user_id,
            feedback_type="success",
            message="Output looked accurate",
            extraction_rows=42,
            extraction_cols=6,
            quality_used="standard",
        ))
        db.add(FunnelEvent(
            user_id=user_id,
            event_type="home_primary_cta_click",
            path="/",
            email=email,
        ))

    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_email"] = email
        resp = client.get("/admin")

    assert resp.status_code == 200
    assert b"Visitors 30d" in resp.data
    assert b"Daily Analytics" in resp.data
    assert b"Login Rate 30d" in resp.data
    assert b"Recent Funnel Events" in resp.data
    assert b"home_primary_cta_click" in resp.data
    assert b"Recent Users" in resp.data
    assert b"Login Events" in resp.data
    assert b"Recent Feedback" in resp.data
    assert b"Output looked accurate" in resp.data
    assert email.encode("utf-8") in resp.data


def test_admin_exports_users_logins_and_visits():
    from app import app
    from db import get_db_session, init_db
    from models import FeedbackSubmission, FunnelEvent, Job, LoginEvent, SiteVisit, User

    suffix = uuid.uuid4().hex
    email = f"export-admin-{suffix}@example.com"
    init_db()
    with get_db_session() as db:
        user = User(email=email, role="admin")
        db.add(user)
        db.flush()
        user_id = user.id
        db.add(SiteVisit(visitor_id=str(uuid.uuid4()), user_id=user_id, path="/blogs"))
        db.add(LoginEvent(
            user_id=user_id,
            email=email,
            event_type="login_success",
            success=True,
        ))
        job = Job(id=f"export-feedback-{suffix}", user_id=user_id, filename="export-statement.pdf", status="completed")
        db.add(job)
        db.add(FeedbackSubmission(
            job_id=job.id,
            user_id=user_id,
            feedback_type="incorrect_data",
            message="Columns shifted",
            extraction_rows=10,
            extraction_cols=5,
            quality_used="high",
        ))
        db.add(FunnelEvent(
            user_id=user_id,
            event_type="auth_submit_attempt",
            path="/auth/start",
            email=email,
        ))

    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_email"] = email
        users_resp = client.get("/admin/export/users.csv")
        logins_resp = client.get("/admin/export/login-events.csv")
        visits_resp = client.get("/admin/export/site-visits.csv")
        daily_resp = client.get("/admin/export/analytics-daily.csv?days=7")
        funnel_resp = client.get("/admin/export/funnel-events.csv?days=7")
        feedback_resp = client.get("/admin/export/feedback.csv?days=7")

    assert users_resp.status_code == 200
    assert logins_resp.status_code == 200
    assert visits_resp.status_code == 200
    assert daily_resp.status_code == 200
    assert funnel_resp.status_code == 200
    assert feedback_resp.status_code == 200
    assert users_resp.mimetype == "text/csv"
    assert daily_resp.mimetype == "text/csv"
    assert funnel_resp.mimetype == "text/csv"
    assert feedback_resp.mimetype == "text/csv"
    assert email.encode("utf-8") in users_resp.data
    assert email.encode("utf-8") in logins_resp.data
    assert b"/blogs" in visits_resp.data
    assert b"date_utc,unique_visitors,page_views,magic_link_requests,login_successes" in daily_resp.data
    assert b"event_type" in funnel_resp.data
    assert b"auth_submit_attempt" in funnel_resp.data
    assert b"feedback_type" in feedback_resp.data
    assert b"Columns shifted" in feedback_resp.data


def test_tracking_cleanup_deletes_old_rows_only():
    from db import get_db_session, init_db
    from models import FunnelEvent, LoginEvent, SiteVisit
    from tracking import cleanup_tracking_logs

    old_visitor_id = str(uuid.uuid4())
    fresh_visitor_id = str(uuid.uuid4())
    old_email = f"old-{uuid.uuid4().hex}@example.com"
    fresh_email = f"fresh-{uuid.uuid4().hex}@example.com"
    init_db()
    with get_db_session() as db:
        db.add(SiteVisit(
            visitor_id=old_visitor_id,
            path="/old",
            created_at=datetime.utcnow() - timedelta(days=400),
        ))
        db.add(SiteVisit(visitor_id=fresh_visitor_id, path="/fresh"))
        db.add(LoginEvent(
            email=old_email,
            event_type="login_success",
            created_at=datetime.utcnow() - timedelta(days=400),
        ))
        db.add(LoginEvent(email=fresh_email, event_type="login_success"))
        db.add(FunnelEvent(
            visitor_id=old_visitor_id,
            event_type="home_primary_cta_click",
            created_at=datetime.utcnow() - timedelta(days=400),
        ))
        db.add(FunnelEvent(visitor_id=fresh_visitor_id, event_type="signin_page_view"))

    deleted = cleanup_tracking_logs(retention_days=180)

    assert deleted == {"site_visits": 1, "login_events": 1, "funnel_events": 1}
    with get_db_session() as db:
        assert db.query(SiteVisit).filter_by(visitor_id=old_visitor_id).first() is None
        assert db.query(SiteVisit).filter_by(visitor_id=fresh_visitor_id).first() is not None
        assert db.query(LoginEvent).filter_by(email=old_email).first() is None
        assert db.query(LoginEvent).filter_by(email=fresh_email).first() is not None


def test_admin_reset_analytics_clears_traffic_without_deleting_users_or_jobs():
    from app import app
    from db import get_db_session, init_db
    from models import FunnelEvent, Job, LoginEvent, SiteVisit, User

    suffix = uuid.uuid4().hex
    email = f"reset-admin-{suffix}@example.com"
    job_id = f"reset-job-{suffix}"
    init_db()
    with get_db_session() as db:
        user = User(email=email, role="admin")
        db.add(user)
        db.flush()
        user_id = user.id
        db.add(Job(id=job_id, user_id=user_id, filename="statement.pdf", status="completed"))
        db.add(SiteVisit(visitor_id=f"reset-visitor-{suffix}", user_id=user_id, path="/"))
        db.add(LoginEvent(user_id=user_id, email=email, event_type="login_success"))
        db.add(FunnelEvent(user_id=user_id, event_type="home_primary_cta_click", path="/"))

    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_email"] = email
            sess["csrf_token"] = "reset-token"
        resp = client.post("/admin/analytics/reset", data={
            "csrf_token": "reset-token",
            "confirm": "RESET_TRAFFIC",
        })

    assert resp.status_code == 302
    with get_db_session() as db:
        assert db.query(SiteVisit).count() == 0
        assert db.query(LoginEvent).count() == 0
        assert db.query(FunnelEvent).count() == 0
        assert db.get(User, user_id) is not None
        assert db.get(Job, job_id) is not None
