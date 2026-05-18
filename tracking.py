"""First-party visitor and login tracking helpers."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from db import get_db_session
from models import LoginEvent, SiteVisit


VISITOR_COOKIE = "sf_visitor_id"
VISITOR_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
IGNORED_EXACT_PATHS = {
    "/favicon.ico",
    "/health",
    "/health/detailed",
    "/robots.txt",
    "/sitemap.xml",
    "/signout",
}
IGNORED_PATH_PREFIXES = (
    "/admin",
    "/auth/verify",
    "/download/",
    "/socket.io/",
    "/static/",
    "/status/",
)


def normalize_visitor_id(raw_value: Optional[str]) -> str:
    if raw_value:
        try:
            return str(uuid.UUID(raw_value))
        except (TypeError, ValueError):
            pass
    return str(uuid.uuid4())


def should_track_page_view(
    path: str,
    method: str,
    status_code: int,
    mimetype: Optional[str],
) -> bool:
    if method != "GET":
        return False
    if status_code >= 400:
        return False
    if path in IGNORED_EXACT_PATHS:
        return False
    if any(path.startswith(prefix) for prefix in IGNORED_PATH_PREFIXES):
        return False
    return mimetype in {None, "text/html"}


def record_page_view(
    *,
    visitor_id: str,
    user_id: Optional[str],
    path: str,
    referrer: Optional[str],
    ip: str,
    user_agent: str,
) -> None:
    with get_db_session() as db:
        db.add(SiteVisit(
            visitor_id=visitor_id,
            user_id=user_id,
            path=path[:512],
            referrer=(referrer or "")[:512],
            ip=ip[:128],
            user_agent=user_agent[:512],
        ))


def cleanup_tracking_logs(retention_days: int) -> dict:
    if retention_days <= 0:
        return {"site_visits": 0, "login_events": 0}

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    with get_db_session() as db:
        site_deleted = (
            db.query(SiteVisit)
            .filter(SiteVisit.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        login_deleted = (
            db.query(LoginEvent)
            .filter(LoginEvent.created_at < cutoff)
            .delete(synchronize_session=False)
        )
    return {"site_visits": site_deleted, "login_events": login_deleted}
