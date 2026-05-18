import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DISABLE_QUOTAS", "true")


def test_should_track_page_view_filters_non_content_routes():
    from tracking import should_track_page_view

    assert should_track_page_view("/", "GET", 200, "text/html") is True
    assert should_track_page_view("/pricing", "GET", 200, "text/html") is True
    assert should_track_page_view("/static/styles.css", "GET", 200, "text/css") is False
    assert should_track_page_view("/auth/verify", "GET", 302, "text/html") is False
    assert should_track_page_view("/health", "GET", 200, "text/html") is False
    assert should_track_page_view("/", "POST", 200, "text/html") is False
    assert should_track_page_view("/", "GET", 404, "text/html") is False


def test_public_page_sets_visitor_cookie():
    from app import app
    from tracking import VISITOR_COOKIE

    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/")

    cookies = resp.headers.getlist("Set-Cookie")
    assert resp.status_code == 200
    assert any(cookie.startswith(f"{VISITOR_COOKIE}=") for cookie in cookies)


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
    from models import LoginEvent, SiteVisit, User

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

    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_email"] = email
        resp = client.get("/admin")

    assert resp.status_code == 200
    assert b"Visitors 24h" in resp.data
    assert b"Daily Analytics" in resp.data
    assert b"Login Rate 7d" in resp.data
    assert b"Recent Users" in resp.data
    assert b"Login Events" in resp.data
    assert email.encode("utf-8") in resp.data


def test_admin_exports_users_logins_and_visits():
    from app import app
    from db import get_db_session, init_db
    from models import LoginEvent, SiteVisit, User

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

    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_email"] = email
        users_resp = client.get("/admin/export/users.csv")
        logins_resp = client.get("/admin/export/login-events.csv")
        visits_resp = client.get("/admin/export/site-visits.csv")
        daily_resp = client.get("/admin/export/analytics-daily.csv?days=7")

    assert users_resp.status_code == 200
    assert logins_resp.status_code == 200
    assert visits_resp.status_code == 200
    assert daily_resp.status_code == 200
    assert users_resp.mimetype == "text/csv"
    assert daily_resp.mimetype == "text/csv"
    assert email.encode("utf-8") in users_resp.data
    assert email.encode("utf-8") in logins_resp.data
    assert b"/blogs" in visits_resp.data
    assert b"date_utc,unique_visitors,page_views,magic_link_requests,login_successes" in daily_resp.data


def test_tracking_cleanup_deletes_old_rows_only():
    from db import get_db_session, init_db
    from models import LoginEvent, SiteVisit
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

    deleted = cleanup_tracking_logs(retention_days=180)

    assert deleted == {"site_visits": 1, "login_events": 1}
    with get_db_session() as db:
        assert db.query(SiteVisit).filter_by(visitor_id=old_visitor_id).first() is None
        assert db.query(SiteVisit).filter_by(visitor_id=fresh_visitor_id).first() is not None
        assert db.query(LoginEvent).filter_by(email=old_email).first() is None
        assert db.query(LoginEvent).filter_by(email=fresh_email).first() is not None
