#!/usr/bin/env python3
"""
PDF to Excel Converter Web Application
Universal parser for bank statement PDFs
"""
# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

import logging
import os
import secrets
import shutil
import uuid
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
from flask import Flask, jsonify, flash, redirect, url_for, request, session
from werkzeug.exceptions import RequestEntityTooLarge
from flask_socketio import SocketIO

# Import parsers
from parsers.universal_parser import UniversalBankParser, ProcessingConfig
from storage_utils import get_storage_config, delete_file
from db import init_db, get_db_session
from models import User, UsageCounter, Job, FeedbackSubmission
from tracking import (
    VISITOR_COOKIE,
    VISITOR_COOKIE_MAX_AGE,
    normalize_visitor_id,
    record_page_view,
    should_track_page_view,
    cleanup_tracking_logs,
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _is_production_runtime() -> bool:
    flask_env = os.environ.get('FLASK_ENV', '').strip().lower()
    app_env = os.environ.get('APP_ENV', '').strip().lower()
    return (
        flask_env == 'production'
        or app_env == 'production'
        or bool(os.environ.get('RAILWAY_ENVIRONMENT'))
        or bool(os.environ.get('RAILWAY_PROJECT_ID'))
    )


IS_PRODUCTION = _is_production_runtime()
SECRET_KEY = os.environ.get('SECRET_KEY')
if IS_PRODUCTION and not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be set in production.")

app = Flask(__name__)
app.secret_key = SECRET_KEY or 'dev-secret-key-change-in-production'
MAX_UPLOAD_MB = int(os.environ.get('MAX_UPLOAD_MB', '20'))
MAX_PAGES = int(os.environ.get('MAX_PAGES', '250'))
GUEST_CONVERSION_LIMIT = int(os.environ.get('GUEST_CONVERSION_LIMIT', '1'))
USER_CONVERSION_LIMIT = int(os.environ.get('USER_CONVERSION_LIMIT', '5'))
MAGIC_LINK_EXP_MINUTES = int(os.environ.get('MAGIC_LINK_EXP_MINUTES', '15'))
DISABLE_QUOTAS = _env_bool('DISABLE_QUOTAS')
BETA_MODE = _env_bool('BETA_MODE', True)
ENGLISH_ONLY_BETA = _env_bool('ENGLISH_ONLY_BETA', True)
RATE_LIMIT_FAIL_CLOSED = _env_bool('RATE_LIMIT_FAIL_CLOSED')
FEEDBACK_RETENTION_DAYS = int(os.environ.get('FEEDBACK_RETENTION_DAYS', '30'))
FEEDBACK_RETENTION_SWEEP_MINS = int(os.environ.get('FEEDBACK_RETENTION_SWEEP_MINS', '60'))
FIRST_PARTY_ANALYTICS_RETENTION_DAYS = int(os.environ.get('FIRST_PARTY_ANALYTICS_RETENTION_DAYS', '180'))
FIRST_PARTY_ANALYTICS_SWEEP_MINS = int(os.environ.get('FIRST_PARTY_ANALYTICS_SWEEP_MINS', '1440'))
ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.environ.get('ADMIN_EMAILS', '').split(',')
    if email.strip()
}

import stripe
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

PLAN_CONFIG = {
    'free':       {'monthly_conversions': 5,    'max_upload_mb': 20,  'stripe_price_id': None},
    'pro':        {'monthly_conversions': 50,   'max_upload_mb': 100, 'stripe_price_id': os.environ.get('STRIPE_PRO_PRICE_ID')},
    'enterprise': {'monthly_conversions': None, 'max_upload_mb': 500, 'stripe_price_id': os.environ.get('STRIPE_ENTERPRISE_PRICE_ID')},
}

# Allow up to Enterprise max; per-plan size check happens in route logic
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = _env_bool('SESSION_COOKIE_SECURE', IS_PRODUCTION)
app.config['GA_MEASUREMENT_ID'] = os.environ.get('GA_MEASUREMENT_ID', '')
app.config['GTM_CONTAINER_ID'] = os.environ.get('GTM_CONTAINER_ID', '')
_last_feedback_retention_sweep = None
_last_analytics_retention_sweep = None

# Initialize SocketIO with proper configuration for production
allowed_origins_env = os.environ.get('SOCKETIO_CORS_ORIGINS') or os.environ.get('ALLOWED_ORIGINS')
if allowed_origins_env:
    allowed_origins = [origin.strip() for origin in allowed_origins_env.split(',') if origin.strip()]
elif IS_PRODUCTION:
    public_origin = os.environ.get('CANONICAL_BASE_URL') or os.environ.get('PUBLIC_BASE_URL')
    allowed_origins = [public_origin.rstrip('/')] if public_origin else []
    if not allowed_origins:
        logger.warning("SocketIO CORS origins are empty in production; set SOCKETIO_CORS_ORIGINS or PUBLIC_BASE_URL.")
else:
    allowed_origins = "*"

socketio = SocketIO(
    app,
    cors_allowed_origins=allowed_origins,
    async_mode=os.environ.get('SOCKETIO_ASYNC_MODE', 'threading'),
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25
)

# Create uploads directory if it doesn't exist
STORAGE_ROOT = os.environ.get('SHARED_STORAGE_PATH')
UPLOAD_FOLDER = os.path.join(STORAGE_ROOT, 'uploads') if STORAGE_ROOT else 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
PROCESSED_FOLDER = os.path.join(STORAGE_ROOT, 'processed') if STORAGE_ROOT else 'processed'

try:
    init_db()
except Exception as e:
    logger.warning(f"Database initialization failed: {e}")

# Supported banks and their parsers
SUPPORTED_BANKS = {
    'universal': {
        'name': 'Universal (Any Bank)',
        'parser': UniversalBankParser,
        'description': 'AI-powered parser for any bank statement (uses OpenAI)',
        'is_ai': True
    }
}


# ── Shared Utility Functions ─────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() == 'pdf'

def is_pdf_file(file_storage):
    """Validate uploaded file is a PDF by extension and magic bytes."""
    if not file_storage or not file_storage.filename:
        return False
    if not allowed_file(file_storage.filename):
        return False
    mimetype = (file_storage.mimetype or '').lower()
    if mimetype and mimetype not in {'application/pdf', 'application/x-pdf', 'application/octet-stream'}:
        return False
    try:
        header = file_storage.stream.read(1024)
        file_storage.stream.seek(0)
    except Exception:
        return False
    return b'%PDF-' in header[:1024]

def is_ajax_request():
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'

def get_client_ip():
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'

def rate_limited(key, limit, window_seconds):
    try:
        import redis
        redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
        r = redis.StrictRedis.from_url(redis_url)
        count = r.incr(key)
        if count == 1:
            r.expire(key, window_seconds)
        return count > limit
    except Exception as exc:
        logger.warning(f"Rate-limit backend unavailable: {exc}")
        return RATE_LIMIT_FAIL_CLOSED

def get_csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return token

def get_guest_id() -> str:
    guest_id = session.get('guest_id')
    if not guest_id:
        guest_id = str(uuid.uuid4())
        session['guest_id'] = guest_id
    return guest_id

def get_identity():
    return session.get('user_id'), get_guest_id()

def get_usage_counter(db, user_id, guest_id, scope='lifetime'):
    return db.query(UsageCounter).filter_by(
        user_id=user_id,
        guest_id=guest_id,
        scope=scope
    ).first()

def sync_session_plan():
    """Refresh plan_status and plan_id from DB into Flask session."""
    user_id = session.get('user_id')
    if not user_id:
        return
    try:
        with get_db_session() as db:
            user = db.query(User).filter_by(id=user_id).first()
            if user:
                session['plan_id'] = user.plan_id or 'free'
                session['plan_status'] = user.plan_status or 'free'
    except Exception as e:
        logger.warning(f"sync_session_plan failed: {e}")

def check_conversion_quota(user_id, guest_id):
    if DISABLE_QUOTAS or not os.environ.get('RESEND_API_KEY'):
        return True, None
    try:
        monthly_scope = f"monthly:{datetime.utcnow().strftime('%Y-%m')}"
        with get_db_session() as db:
            if user_id:
                user = db.query(User).filter_by(id=user_id).first()
                plan_id = (user.plan_id if user else None) or 'free'
                plan_status = (user.plan_status if user else None) or 'free'
                plan = PLAN_CONFIG.get(plan_id, PLAN_CONFIG['free'])
                monthly_limit = plan['monthly_conversions']

                if plan_status in ('active', 'past_due') and monthly_limit is None:
                    return True, None

                if plan_status in ('active', 'past_due', 'free'):
                    counter = get_usage_counter(db, user_id=user_id, guest_id=None, scope=monthly_scope)
                    used = counter.conversions_count if counter else 0
                    limit = monthly_limit if monthly_limit is not None else PLAN_CONFIG['free']['monthly_conversions']
                    if used >= limit:
                        return False, {
                            'error': f'You have reached your {limit} conversions this month. Please upgrade to continue.',
                            'error_code': 'USER_LIMIT_EXCEEDED'
                        }
            else:
                counter = get_usage_counter(db, user_id=None, guest_id=guest_id, scope='lifetime')
                used = counter.conversions_count if counter else 0
                if used >= GUEST_CONVERSION_LIMIT:
                    return False, {
                        'error': 'You have used your free conversion. Sign in to get more.',
                        'error_code': 'GUEST_LIMIT_EXCEEDED',
                        'requires_login': True
                    }
    except Exception as e:
        logger.warning(f"Quota check failed: {e}")
    return True, None

def is_admin_user():
    email = (session.get('user_email') or '').lower()
    if not email:
        return False
    if email in ADMIN_EMAILS:
        return True
    try:
        with get_db_session() as db:
            user = db.query(User).filter_by(email=email).first()
            return bool(user and user.role == 'admin')
    except Exception:
        return False


# ── Cleanup Jobs ─────────────────────────────────────────────────────────

def cleanup_old_files():
    """Clean up stale local uploads and generated result files."""
    try:
        current_time = datetime.now().timestamp()
        for filename in os.listdir(UPLOAD_FOLDER):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.isfile(filepath):
                if current_time - os.path.getctime(filepath) > 3600:
                    os.remove(filepath)
    except Exception:
        pass

    try:
        if LOCAL_RESULT_RETENTION_HOURS <= 0 or not os.path.exists(PROCESSED_FOLDER):
            return

        cutoff_seconds = LOCAL_RESULT_RETENTION_HOURS * 3600
        current_time = datetime.now().timestamp()
        for name in os.listdir(PROCESSED_FOLDER):
            path = os.path.join(PROCESSED_FOLDER, name)
            if current_time - os.path.getmtime(path) <= cutoff_seconds:
                continue
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.isfile(path):
                os.remove(path)
    except Exception as exc:
        logger.warning(f"Local result cleanup failed: {exc}")

def cleanup_feedback_shared_pdfs(force: bool = False):
    """Delete user-consented shared PDFs after retention period."""
    global _last_feedback_retention_sweep

    if FEEDBACK_RETENTION_DAYS <= 0:
        return

    now = datetime.utcnow()
    if (
        not force
        and _last_feedback_retention_sweep
        and now - _last_feedback_retention_sweep < timedelta(minutes=FEEDBACK_RETENTION_SWEEP_MINS)
    ):
        return

    storage = get_storage_config()
    if not storage:
        _last_feedback_retention_sweep = now
        return

    cutoff = now - timedelta(days=FEEDBACK_RETENTION_DAYS)
    try:
        with get_db_session() as db:
            stale = (
                db.query(FeedbackSubmission)
                .filter(FeedbackSubmission.pdf_shared.is_(True))
                .filter(FeedbackSubmission.pdf_storage_key.isnot(None))
                .filter(FeedbackSubmission.created_at < cutoff)
                .limit(200)
                .all()
            )
            for item in stale:
                key = item.pdf_storage_key
                if key:
                    try:
                        delete_file(storage, key)
                    except Exception as exc:
                        logger.warning(f"Feedback retention delete failed for {key}: {exc}")
                        continue
                item.pdf_storage_key = None
                if item.status == "new":
                    item.status = "expired"
    except Exception as exc:
        logger.warning(f"Feedback retention sweep failed: {exc}")
    finally:
        _last_feedback_retention_sweep = now


def cleanup_first_party_analytics(force: bool = False):
    """Delete old first-party analytics and login-event rows."""
    global _last_analytics_retention_sweep

    if FIRST_PARTY_ANALYTICS_RETENTION_DAYS <= 0:
        return

    now = datetime.utcnow()
    if (
        not force
        and _last_analytics_retention_sweep
        and now - _last_analytics_retention_sweep < timedelta(minutes=FIRST_PARTY_ANALYTICS_SWEEP_MINS)
    ):
        return

    try:
        deleted = cleanup_tracking_logs(FIRST_PARTY_ANALYTICS_RETENTION_DAYS)
        if deleted.get("site_visits") or deleted.get("login_events"):
            logger.info(
                "Analytics retention sweep deleted site_visits=%s login_events=%s",
                deleted.get("site_visits", 0),
                deleted.get("login_events", 0),
            )
    except Exception as exc:
        logger.warning(f"Analytics retention sweep failed: {exc}")
    finally:
        _last_analytics_retention_sweep = now

_last_s3_cleanup_sweep = None
RESULT_RETENTION_HOURS = int(os.environ.get(
    'RESULT_RETENTION_HOURS',
    os.environ.get('S3_RESULT_RETENTION_HOURS', '24')
))
S3_RESULT_RETENTION_HOURS = RESULT_RETENTION_HOURS
LOCAL_RESULT_RETENTION_HOURS = int(os.environ.get(
    'LOCAL_RESULT_RETENTION_HOURS',
    str(RESULT_RETENTION_HOURS)
))

def cleanup_expired_s3_results(force: bool = False):
    """Delete S3 job files older than the retention window."""
    global _last_s3_cleanup_sweep

    if S3_RESULT_RETENTION_HOURS <= 0:
        return

    now = datetime.utcnow()
    if (
        not force
        and _last_s3_cleanup_sweep
        and now - _last_s3_cleanup_sweep < timedelta(hours=1)
    ):
        return

    storage = get_storage_config()
    if not storage:
        _last_s3_cleanup_sweep = now
        return

    cutoff = now - timedelta(hours=S3_RESULT_RETENTION_HOURS)
    try:
        with get_db_session() as db:
            stale_jobs = (
                db.query(Job)
                .filter(Job.finished_at < cutoff)
                .filter(
                    Job.input_storage_key.isnot(None)
                    | Job.output_storage_key.isnot(None)
                    | Job.storage_key.isnot(None)
                )
                .limit(100)
                .all()
            )
            for job in stale_jobs:
                candidate_keys = [
                    job.input_storage_key,
                    job.output_storage_key,
                    job.storage_key,
                ]
                for key in dict.fromkeys(k for k in candidate_keys if k):
                    try:
                        delete_file(storage, key)
                    except Exception as exc:
                        logger.warning(f"S3 cleanup failed for {key}: {exc}")
                        continue

                    if key == job.input_storage_key:
                        job.input_storage_key = None
                        job.input_deleted_at = now
                    if key == job.output_storage_key:
                        job.output_storage_key = None
                        job.output_deleted_at = now
                    if key == job.storage_key:
                        job.storage_key = None
    except Exception as exc:
        logger.warning(f"S3 cleanup sweep failed: {exc}")
    finally:
        _last_s3_cleanup_sweep = now


def progress_callback(progress_data):
    """Callback function to emit progress updates via WebSocket."""
    socketio.emit('progress_update', progress_data)


# ── Context Processors & Middleware ──────────────────────────────────────

@app.context_processor
def inject_csrf_token():
    return {'csrf_token': get_csrf_token}

@app.context_processor
def inject_user_context():
    user_email = session.get('user_email')
    normalized_email = (user_email or '').lower()
    return {
        'current_user_email': user_email,
        'current_user_is_admin': bool(
            normalized_email
            and (normalized_email in ADMIN_EMAILS or session.get('role') == 'admin')
        ),
        'now': datetime.utcnow,
    }

@app.after_request
def add_security_headers(response):
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.socket.io https://cdnjs.cloudflare.com "
        "https://www.googletagmanager.com https://www.google-analytics.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' ws: wss: https://www.google-analytics.com https://www.googletagmanager.com; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "object-src 'none'"
    )
    response.headers.setdefault('Content-Security-Policy', csp)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    try:
        if should_track_page_view(request.path, request.method, response.status_code, response.mimetype):
            visitor_id = normalize_visitor_id(request.cookies.get(VISITOR_COOKIE))
            response.set_cookie(
                VISITOR_COOKIE,
                visitor_id,
                max_age=VISITOR_COOKIE_MAX_AGE,
                httponly=True,
                secure=app.config['SESSION_COOKIE_SECURE'],
                samesite='Lax',
            )
            record_page_view(
                visitor_id=visitor_id,
                user_id=session.get('user_id'),
                path=request.path,
                referrer=request.headers.get('Referer', ''),
                ip=get_client_ip(),
                user_agent=request.headers.get('User-Agent', ''),
            )
    except Exception as exc:
        logger.warning(f"Page-view tracking failed: {exc}")
    return response

@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(error):
    message = f'File too large. Maximum allowed size is {MAX_UPLOAD_MB}MB.'
    if is_ajax_request():
        return jsonify({
            'status': 'error',
            'error': message,
            'error_code': 'FILE_TOO_LARGE',
            'max_mb': MAX_UPLOAD_MB
        }), 413
    flash(message, 'error')
    return redirect(url_for('pages.home'))


# ── Register Blueprints ──────────────────────────────────────────────────

from routes import all_blueprints
for bp in all_blueprints:
    app.register_blueprint(bp)


# For deployment with gunicorn, we need to expose the SocketIO app
# This allows gunicorn to use: gunicorn app:socketio

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    print(f"Starting Flask app on port {port}")
    socketio.run(app, debug=False, host='0.0.0.0', port=port)
