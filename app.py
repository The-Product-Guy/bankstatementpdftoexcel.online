#!/usr/bin/env python3
"""
PDF to Excel Converter Web Application
Universal parser for bank statement PDFs
"""
# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

import logging
import hashlib
import os
import secrets
import uuid
from datetime import datetime
from ipaddress import ip_address
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
from flask import Flask, jsonify, flash, redirect, url_for, request, session
from werkzeug.exceptions import RequestEntityTooLarge

from site_urls import normalize_base_url, public_base_url
from redis_utils import get_redis_client
from db import init_db, get_db_session
from models import User, UsageCounter, Job
from retention import (
    FEEDBACK_RETENTION_DAYS,
    FIRST_PARTY_ANALYTICS_RETENTION_DAYS,
    LOCAL_RESULT_RETENTION_HOURS,
    PROCESSED_FOLDER,
    RESULT_RETENTION_HOURS,
    S3_RESULT_RETENTION_HOURS,
    UPLOAD_FOLDER,
    cleanup_expired_s3_results,
    cleanup_feedback_shared_pdfs,
    cleanup_first_party_analytics,
    cleanup_old_files,
)
from tracking import (
    VISITOR_COOKIE,
    VISITOR_COOKIE_MAX_AGE,
    normalize_visitor_id,
    enqueue_page_view,
    should_track_page_view,
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
PUBLIC_ORIGIN_SETTING = os.environ.get('CANONICAL_BASE_URL') or os.environ.get('PUBLIC_BASE_URL')
if IS_PRODUCTION and not PUBLIC_ORIGIN_SETTING:
    raise RuntimeError("CANONICAL_BASE_URL or PUBLIC_BASE_URL must be set in production.")
if PUBLIC_ORIGIN_SETTING:
    try:
        normalize_base_url(PUBLIC_ORIGIN_SETTING)
    except ValueError as exc:
        raise RuntimeError(f"Invalid public base URL: {exc}") from exc

app = Flask(__name__)
app.secret_key = SECRET_KEY or 'dev-secret-key-change-in-production'
STATIC_ASSET_VERSIONS = {}
for _asset_name in ('styles.min.css', 'ui.min.js', 'script.min.js'):
    try:
        with open(os.path.join(app.static_folder, _asset_name), 'rb') as _asset_file:
            STATIC_ASSET_VERSIONS[_asset_name] = hashlib.sha256(_asset_file.read()).hexdigest()[:12]
    except OSError:
        logger.warning("Static asset is missing: %s", _asset_name)
PUBLIC_UPLOAD_LIMIT_MB = 50
try:
    _configured_upload_limit_mb = int(os.environ.get('MAX_UPLOAD_MB', str(PUBLIC_UPLOAD_LIMIT_MB)))
except ValueError as exc:
    raise RuntimeError('MAX_UPLOAD_MB must be a whole number of megabytes.') from exc
if _configured_upload_limit_mb <= 0:
    raise RuntimeError('MAX_UPLOAD_MB must be greater than zero.')
if _configured_upload_limit_mb > PUBLIC_UPLOAD_LIMIT_MB:
    logger.warning(
        'MAX_UPLOAD_MB=%s exceeds the public product cap; using %s MB.',
        _configured_upload_limit_mb,
        PUBLIC_UPLOAD_LIMIT_MB,
    )
MAX_UPLOAD_MB = min(_configured_upload_limit_mb, PUBLIC_UPLOAD_LIMIT_MB)
FILE_SPLITTER_URL = (
    os.environ.get('FILE_SPLITTER_URL', '').strip()
    or 'https://smallpdfsplit.online/'
)
MAX_PAGES = int(os.environ.get('MAX_PAGES', '250'))
GUEST_CONVERSION_LIMIT = int(os.environ.get('GUEST_CONVERSION_LIMIT', '1'))
USER_CONVERSION_LIMIT = int(os.environ.get('USER_CONVERSION_LIMIT', '5'))
MAGIC_LINK_EXP_MINUTES = int(os.environ.get('MAGIC_LINK_EXP_MINUTES', '15'))
DISABLE_QUOTAS = _env_bool('DISABLE_QUOTAS')
RATE_LIMIT_FAIL_CLOSED = _env_bool('RATE_LIMIT_FAIL_CLOSED', IS_PRODUCTION)
ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.environ.get('ADMIN_EMAILS', '').split(',')
    if email.strip()
}

import stripe
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

PLAN_CONFIG = {
    'free':       {'monthly_conversions': 5,    'max_upload_mb': MAX_UPLOAD_MB, 'stripe_price_id': None},
    'pro':        {'monthly_conversions': 50,   'max_upload_mb': MAX_UPLOAD_MB, 'stripe_price_id': os.environ.get('STRIPE_PRO_PRICE_ID')},
    'enterprise': {'monthly_conversions': None, 'max_upload_mb': MAX_UPLOAD_MB, 'stripe_price_id': os.environ.get('STRIPE_ENTERPRISE_PRICE_ID')},
}


def unlimited_quota_emails():
    return {
        email.strip().lower()
        for email in os.environ.get('UNLIMITED_QUOTA_EMAILS', '').split(',')
        if email.strip()
    }


def has_unlimited_quota_email(email: str = '') -> bool:
    return bool(email and email.strip().lower() in unlimited_quota_emails())

# Multipart framing adds a small amount beyond the file itself. The converter
# enforces the exact file-byte cap before parsing, storage, or task dispatch.
app.config['MAX_CONTENT_LENGTH'] = (MAX_UPLOAD_MB + 1) * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = _env_bool('SESSION_COOKIE_SECURE', IS_PRODUCTION)
app.config['GA_MEASUREMENT_ID'] = os.environ.get('GA_MEASUREMENT_ID', '')
app.config['GTM_CONTAINER_ID'] = os.environ.get('GTM_CONTAINER_ID', '')
# Create uploads directory if it doesn't exist
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

try:
    init_db()
except Exception as e:
    logger.warning(f"Database initialization failed: {e}")

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
    # Railway injects X-Real-IP at its trusted edge. Do not use the left-most
    # X-Forwarded-For value: clients can prepend arbitrary values to that chain.
    candidates = (
        request.headers.get('X-Real-IP', ''),
        request.remote_addr or '',
    )
    for candidate in candidates:
        normalized = candidate.strip()
        if not normalized:
            continue
        try:
            return str(ip_address(normalized))
        except ValueError:
            continue
    return 'unknown'

def rate_limited(key, limit, window_seconds, *, fail_closed=None):
    try:
        r = get_redis_client()
        count = r.eval(
            """
            local current = redis.call('INCR', KEYS[1])
            if current == 1 or redis.call('TTL', KEYS[1]) < 0 then
                redis.call('EXPIRE', KEYS[1], ARGV[1])
            end
            return current
            """,
            1,
            key,
            int(window_seconds),
        )
        return count > limit
    except Exception as exc:
        logger.warning(f"Rate-limit backend unavailable: {exc}")
        if fail_closed is None:
            fail_closed = RATE_LIMIT_FAIL_CLOSED
        return bool(fail_closed)

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
    if DISABLE_QUOTAS:
        return True, None
    try:
        monthly_scope = f"monthly:{datetime.utcnow().strftime('%Y-%m')}"
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with get_db_session() as db:
            if user_id:
                user = db.query(User).filter_by(id=user_id).first()
                if user and has_unlimited_quota_email(user.email):
                    return True, None
                plan_id = (user.plan_id if user else None) or 'free'
                plan_status = (user.plan_status if user else None) or 'free'
                plan = PLAN_CONFIG.get(plan_id, PLAN_CONFIG['free'])
                monthly_limit = plan['monthly_conversions']

                if plan_status in ('active', 'past_due') and monthly_limit is None:
                    return True, None

                if plan_status in ('active', 'past_due', 'free'):
                    counter = get_usage_counter(db, user_id=user_id, guest_id=None, scope=monthly_scope)
                    counter_count = counter.conversions_count if counter else 0
                    completed_job_count = (
                        db.query(Job)
                        .filter(
                            Job.user_id == user_id,
                            Job.created_at >= month_start,
                            Job.status.like('completed%'),
                        )
                        .count()
                    )
                    used = max(counter_count, completed_job_count)
                    limit = monthly_limit if monthly_limit is not None else PLAN_CONFIG['free']['monthly_conversions']
                    if used >= limit:
                        return False, {
                            'error': f'You have reached your {limit} conversions this month. Please upgrade to continue.',
                            'error_code': 'USER_LIMIT_EXCEEDED'
                        }
            else:
                counter = get_usage_counter(db, user_id=None, guest_id=guest_id, scope='lifetime')
                counter_count = counter.conversions_count if counter else 0
                completed_job_count = (
                    db.query(Job)
                    .filter(
                        Job.guest_id == guest_id,
                        Job.status.like('completed%'),
                    )
                    .count()
                )
                used = max(counter_count, completed_job_count)
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


# ── Context Processors & Middleware ──────────────────────────────────────

@app.context_processor
def inject_csrf_token():
    return {'csrf_token': get_csrf_token}

@app.context_processor
def inject_user_context():
    user_email = session.get('user_email')
    normalized_email = (user_email or '').lower()

    site_base_url = public_base_url

    def public_url_for(endpoint, **values):
        anchor = values.pop('_anchor', None)
        values.pop('_external', None)
        path = url_for(endpoint, **values)
        if anchor:
            path = f"{path}#{anchor}"
        return f"{site_base_url()}{path}"

    def canonical_current_url():
        return f"{site_base_url()}{request.path}"

    def static_asset_url(filename):
        version = STATIC_ASSET_VERSIONS.get(filename)
        if version:
            return url_for('static', filename=filename, v=version)
        return url_for('static', filename=filename)

    return {
        'current_user_email': user_email,
        'current_user_is_admin': bool(
            normalized_email
            and (normalized_email in ADMIN_EMAILS or session.get('role') == 'admin')
        ),
        'now': datetime.utcnow,
        'site_base_url': site_base_url,
        'public_url_for': public_url_for,
        'canonical_current_url': canonical_current_url,
        'static_asset_url': static_asset_url,
        'max_upload_mb': MAX_UPLOAD_MB,
        'file_splitter_url': FILE_SPLITTER_URL,
    }


@app.before_request
def enforce_canonical_host():
    """Redirect the www alias and reject production host-header bypasses."""
    configured = os.environ.get('CANONICAL_BASE_URL') or os.environ.get('PUBLIC_BASE_URL')
    if not configured:
        return None

    canonical_origin = normalize_base_url(configured)
    canonical_host = urlsplit(canonical_origin).hostname
    request_host = (request.host.split(':', 1)[0] or '').lower().rstrip('.')
    if canonical_host and not canonical_host.startswith('www.') and request_host == f'www.{canonical_host}':
        target_path = request.full_path[:-1] if request.full_path.endswith('?') else request.full_path
        return redirect(f'{canonical_origin}{target_path}', code=308)
    if (
        IS_PRODUCTION
        and canonical_host
        and request_host != canonical_host
        and request.path not in {'/health', '/health/detailed'}
    ):
        return 'Misdirected request', 421
    return None

@app.after_request
def add_security_headers(response):
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com "
        "https://www.googletagmanager.com https://www.google-analytics.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: blob: https://www.google-analytics.com; "
        "connect-src 'self' https://www.google-analytics.com https://www.googletagmanager.com; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "object-src 'none'"
    )
    response.headers.setdefault('Content-Security-Policy', csp)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    if IS_PRODUCTION:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    noindex_exact_paths = {
        '/account',
        '/billing/portal',
        '/checkout/create',
        '/checkout/success',
        '/convert',
        '/convert/preflight',
        '/dashboard',
        '/download/email',
        '/feedback',
        '/health',
        '/health/detailed',
        '/signin',
        '/signout',
        '/stripe/webhook',
        '/track/event',
    }
    noindex_prefixes = (
        '/admin',
        '/auth/',
        '/download/',
        '/status/',
    )
    if request.path in noindex_exact_paths or any(request.path.startswith(prefix) for prefix in noindex_prefixes):
        response.headers.setdefault('X-Robots-Tag', 'noindex, nofollow')
    if request.path in {'/static/sample-statement.pdf', '/static/sample-statement.xlsx'}:
        response.headers.setdefault('X-Robots-Tag', 'noindex, noarchive')
    if request.path.endswith(('.min.css', '.min.js')):
        response.headers['Cache-Control'] = 'public, max-age=604800'
    try:
        user_agent = request.headers.get('User-Agent', '')
        if should_track_page_view(
            request.path,
            request.method,
            response.status_code,
            response.mimetype,
            user_agent,
        ):
            visitor_id = normalize_visitor_id(request.cookies.get(VISITOR_COOKIE))
            response.set_cookie(
                VISITOR_COOKIE,
                visitor_id,
                max_age=VISITOR_COOKIE_MAX_AGE,
                httponly=True,
                secure=app.config['SESSION_COOKIE_SECURE'],
                samesite='Lax',
            )
            enqueue_page_view(
                visitor_id=visitor_id,
                user_id=session.get('user_id'),
                path=request.path,
                referrer=request.headers.get('Referer', ''),
                ip=get_client_ip(),
                user_agent=user_agent,
            )
    except Exception as exc:
        logger.warning(f"Page-view tracking failed: {exc}")
    return response

@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(error):
    message = f'File too large. Maximum allowed size is {MAX_UPLOAD_MB} MB.'
    if is_ajax_request():
        return jsonify({
            'status': 'error',
            'error': message,
            'error_code': 'FILE_TOO_LARGE',
            'max_mb': MAX_UPLOAD_MB
        }), 413
    flash(message, 'error')
    return redirect(url_for('converter.dashboard'))


# ── Register Blueprints ──────────────────────────────────────────────────

from routes import all_blueprints
for bp in all_blueprints:
    app.register_blueprint(bp)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    print(f"Starting Flask app on port {port}")
    app.run(debug=False, host='0.0.0.0', port=port)
