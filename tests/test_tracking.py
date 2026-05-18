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
    assert b"Login Events" in resp.data
    assert email.encode("utf-8") in resp.data
