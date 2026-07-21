"""First-party visitor and login tracking helpers."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from db import get_db_session
from models import FunnelEvent, LoginEvent, SiteVisit


VISITOR_COOKIE = "sf_visitor_id"
VISITOR_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
BOT_USER_AGENT_KEYWORDS = (
    "ahrefsbot",
    "amazonbot",
    "applebot",
    "baiduspider",
    "bingbot",
    "bingpreview",
    "bytespider",
    "ccbot",
    "chatgpt-user",
    "claudebot",
    "crawler",
    "datadog",
    "discordbot",
    "dotbot",
    "duckduckbot",
    "facebookexternalhit",
    "gptbot",
    "googlebot",
    "google-inspectiontool",
    "linkedinbot",
    "mj12bot",
    "monitoring",
    "petalbot",
    "pingdom",
    "perplexitybot",
    "siteauditbot",
    "semrushbot",
    "slurp",
    "spider",
    "uptimerobot",
    "whatsapp",
    "yandexbot",
)
IGNORED_EXACT_PATHS = {
    "/favicon.ico",
    "/health",
    "/health/detailed",
    "/humans.txt",
    "/indexnow-key.txt",
    "/llms.txt",
    "/robots.txt",
    "/security.txt",
    "/sitemap.xml",
    "/sitemap.txt",
    "/signout",
}
IGNORED_PATH_PREFIXES = (
    "/.well-known/",
    "/admin",
    "/auth/verify",
    "/download/",
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


def is_probable_bot(user_agent: Optional[str]) -> bool:
    normalized = (user_agent or "").strip().lower()
    if not normalized:
        return True
    return any(keyword in normalized for keyword in BOT_USER_AGENT_KEYWORDS)


def should_track_page_view(
    path: str,
    method: str,
    status_code: int,
    mimetype: Optional[str],
    user_agent: Optional[str] = None,
) -> bool:
    if method != "GET":
        return False
    if status_code >= 400:
        return False
    if is_probable_bot(user_agent):
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
    if is_probable_bot(user_agent):
        return

    with get_db_session() as db:
        db.add(SiteVisit(
            visitor_id=visitor_id,
            user_id=user_id,
            path=path[:512],
            referrer=(referrer or "")[:512],
            ip=ip[:128],
            user_agent=user_agent[:512],
        ))


def record_funnel_event(
    *,
    event_type: str,
    visitor_id: Optional[str] = None,
    user_id: Optional[str] = None,
    guest_id: Optional[str] = None,
    job_id: Optional[str] = None,
    email: Optional[str] = None,
    path: Optional[str] = None,
    extra: Optional[str] = None,
    ip: str = "",
    user_agent: str = "",
) -> None:
    if is_probable_bot(user_agent):
        return

    with get_db_session() as db:
        db.add(FunnelEvent(
            visitor_id=visitor_id,
            user_id=user_id,
            guest_id=guest_id,
            job_id=job_id,
            email=(email or "")[:255] if email else None,
            event_type=event_type[:128],
            path=(path or "")[:512],
            extra=(extra or "")[:2000] if extra else None,
            ip=(ip or "")[:128],
            user_agent=(user_agent or "")[:512],
        ))


def _enqueue_tracking_task(task_name: str, payload: dict) -> None:
    """Queue analytics without allowing telemetry to affect the request."""
    try:
        from flask import current_app, has_app_context

        if has_app_context() and current_app.config.get("TESTING"):
            if task_name == "maintenance.persist_page_view":
                record_page_view(**payload)
            else:
                record_funnel_event(**payload)
            return

        from celery_config import celery_app

        celery_app.send_task(task_name, kwargs=payload, retry=False)
    except Exception:
        # Analytics is deliberately best-effort and must never fail a request.
        return


def enqueue_page_view(**payload) -> None:
    _enqueue_tracking_task("maintenance.persist_page_view", payload)


def enqueue_funnel_event(**payload) -> None:
    _enqueue_tracking_task("maintenance.persist_funnel_event", payload)


def cleanup_tracking_logs(retention_days: int) -> dict:
    if retention_days <= 0:
        return {"site_visits": 0, "login_events": 0, "funnel_events": 0}

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
        funnel_deleted = (
            db.query(FunnelEvent)
            .filter(FunnelEvent.created_at < cutoff)
            .delete(synchronize_session=False)
        )
    return {
        "site_visits": site_deleted,
        "login_events": login_deleted,
        "funnel_events": funnel_deleted,
    }


def clear_tracking_logs() -> dict:
    with get_db_session() as db:
        funnel_deleted = db.query(FunnelEvent).delete(synchronize_session=False)
        login_deleted = db.query(LoginEvent).delete(synchronize_session=False)
        site_deleted = db.query(SiteVisit).delete(synchronize_session=False)
    return {
        "site_visits": site_deleted,
        "login_events": login_deleted,
        "funnel_events": funnel_deleted,
    }
