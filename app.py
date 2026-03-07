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
import tempfile
import uuid
import gc
from datetime import datetime, timedelta
import hashlib
import secrets

logger = logging.getLogger(__name__)
from flask import Flask, render_template, request, jsonify, send_file, flash, redirect, url_for, session
from werkzeug.exceptions import RequestEntityTooLarge
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

# Import parsers
from parsers.universal_parser import UniversalBankParser, ProcessingConfig
from storage_utils import get_storage_config, upload_file, generate_presigned_url, delete_file
from db import init_db, get_db_session
from models import User, AuthToken, Job, UsageCounter, FeedbackSubmission

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
MAX_UPLOAD_MB = int(os.environ.get('MAX_UPLOAD_MB', '20'))
MAX_PAGES = int(os.environ.get('MAX_PAGES', '250'))
GUEST_CONVERSION_LIMIT = int(os.environ.get('GUEST_CONVERSION_LIMIT', '1'))
USER_CONVERSION_LIMIT = int(os.environ.get('USER_CONVERSION_LIMIT', '5'))
MAGIC_LINK_EXP_MINUTES = int(os.environ.get('MAGIC_LINK_EXP_MINUTES', '15'))
DISABLE_QUOTAS = os.environ.get('DISABLE_QUOTAS', '').strip().lower() in {'1', 'true', 'yes', 'on'}
BETA_MODE = os.environ.get('BETA_MODE', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
ENGLISH_ONLY_BETA = os.environ.get('ENGLISH_ONLY_BETA', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
FEEDBACK_RETENTION_DAYS = int(os.environ.get('FEEDBACK_RETENTION_DAYS', '30'))
FEEDBACK_RETENTION_SWEEP_MINS = int(os.environ.get('FEEDBACK_RETENTION_SWEEP_MINS', '60'))
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
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
app.config['GA_MEASUREMENT_ID'] = os.environ.get('GA_MEASUREMENT_ID', '')  # Google Analytics Measurement ID (e.g., G-XXXXXXXXXX)
app.config['GTM_CONTAINER_ID'] = os.environ.get('GTM_CONTAINER_ID', '')  # Google Tag Manager Container ID (optional)
_last_feedback_retention_sweep = None

# Initialize SocketIO with proper configuration for production
allowed_origins_env = os.environ.get('SOCKETIO_CORS_ORIGINS') or os.environ.get('ALLOWED_ORIGINS')
if allowed_origins_env:
    allowed_origins = [origin.strip() for origin in allowed_origins_env.split(',') if origin.strip()]
else:
    allowed_origins = "*"

socketio = SocketIO(
    app, 
    cors_allowed_origins=allowed_origins,
    async_mode='eventlet',
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

def allowed_file(filename):
    """Check if uploaded file is allowed"""
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
    except Exception:
        return False

def get_csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return token

@app.context_processor
def inject_csrf_token():
    return {'csrf_token': get_csrf_token}

@app.context_processor
def inject_user_context():
    return {
        'current_user_email': session.get('user_email'),
        'now': datetime.utcnow,
    }

def get_guest_id() -> str:
    guest_id = session.get('guest_id')
    if not guest_id:
        guest_id = str(uuid.uuid4())
        session['guest_id'] = guest_id
    return guest_id

def get_identity():
    return session.get('user_id'), get_guest_id()

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()

def send_magic_link_email(email: str, link: str) -> None:
    import resend

    api_key = os.environ.get('RESEND_API_KEY')
    if not api_key:
        raise RuntimeError('RESEND_API_KEY is not configured')

    resend.api_key = api_key
    sender = os.environ.get('RESEND_FROM_EMAIL', 'onboarding@resend.dev')
    expiry_minutes = MAGIC_LINK_EXP_MINUTES

    resend.Emails.send({
        "from": sender,
        "to": [email],
        "subject": "Your sign-in link for StatementFlow",
        "text": (
            f"Sign in to StatementFlow\n\n"
            f"Click the link below to sign in:\n{link}\n\n"
            f"This link expires in {expiry_minutes} minutes.\n"
            f"If you didn't request this, you can safely ignore this email.\n\n"
            f"- StatementFlow"
        ),
        "html": f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f7;font-family:'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f7;padding:40px 0;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.08);">
        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#667eea,#764ba2);padding:32px 40px;text-align:center;">
            <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:600;letter-spacing:-0.3px;">StatementFlow</h1>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:36px 40px 20px;">
            <p style="margin:0 0 20px;color:#333;font-size:16px;line-height:1.5;">
              Click the button below to sign in to your account:
            </p>
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr><td align="center" style="padding:8px 0 28px;">
                <a href="{link}" style="display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#667eea,#764ba2);color:#ffffff;text-decoration:none;border-radius:8px;font-size:16px;font-weight:600;letter-spacing:0.3px;">
                  Sign in
                </a>
              </td></tr>
            </table>
            <p style="margin:0 0 8px;color:#666;font-size:13px;line-height:1.5;">
              This link expires in <strong>{expiry_minutes} minutes</strong> and can only be used once.
            </p>
            <p style="margin:0 0 8px;color:#666;font-size:13px;line-height:1.5;">
              If the button doesn't work, copy and paste this URL into your browser:
            </p>
            <p style="margin:0 0 20px;word-break:break-all;color:#667eea;font-size:12px;">{link}</p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="padding:20px 40px 28px;border-top:1px solid #eee;">
            <p style="margin:0;color:#999;font-size:12px;line-height:1.6;">
              If you didn't request this email, you can safely ignore it.
              <br>StatementFlow &mdash; PDF to Excel Converter
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
""",
    })

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

                # Enterprise (unlimited) or active/past_due paid plan
                if plan_status in ('active', 'past_due') and monthly_limit is None:
                    return True, None

                # Check monthly usage for active/past_due paid plans and free users
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
                # Guest users: keep 1 lifetime limit
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

def cleanup_old_files():
    """Clean up old uploaded files"""
    try:
        current_time = datetime.now().timestamp()
        for filename in os.listdir(UPLOAD_FOLDER):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.isfile(filepath):
                # Delete files older than 1 hour
                if current_time - os.path.getctime(filepath) > 3600:
                    os.remove(filepath)
    except Exception:
        pass


def cleanup_feedback_shared_pdfs(force: bool = False):
    """
    Delete user-consented shared PDFs after retention period.
    Retention applies only to feedback-linked analysis copies.
    """
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

_last_s3_cleanup_sweep = None
S3_RESULT_RETENTION_HOURS = int(os.environ.get('S3_RESULT_RETENTION_HOURS', '24'))

def cleanup_expired_s3_results(force: bool = False):
    """Delete S3 result files for jobs older than the retention window."""
    global _last_s3_cleanup_sweep

    if S3_RESULT_RETENTION_HOURS <= 0:
        return

    now = datetime.utcnow()
    # Run at most once per hour
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
                .filter(Job.storage_key.isnot(None))
                .filter(Job.finished_at < cutoff)
                .limit(100)
                .all()
            )
            for job in stale_jobs:
                try:
                    delete_file(storage, job.storage_key)
                except Exception as exc:
                    logger.warning(f"S3 cleanup failed for {job.storage_key}: {exc}")
                    continue
                job.storage_key = None
    except Exception as exc:
        logger.warning(f"S3 cleanup sweep failed: {exc}")
    finally:
        _last_s3_cleanup_sweep = now


def progress_callback(progress_data):
    """Callback function to emit progress updates via WebSocket"""
    socketio.emit('progress_update', progress_data)

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
    return redirect(url_for('home'))

@app.route('/')
def home():
    """Home page with upload functionality"""
    cleanup_old_files()
    cleanup_feedback_shared_pdfs()
    cleanup_expired_s3_results()
    return render_template(
        'home.html',
        banks=SUPPORTED_BANKS,
        max_upload_mb=MAX_UPLOAD_MB,
        max_pages=MAX_PAGES,
        beta_mode=BETA_MODE,
        english_only_beta=ENGLISH_ONLY_BETA,
        feedback_retention_days=FEEDBACK_RETENTION_DAYS
    )

@app.route('/index')
def index():
    """Legacy route - redirect to home"""
    return redirect(url_for('home'))

@app.route('/blogs')
def blogs():
    """Blogs page"""
    return render_template('blogs.html')

@app.route('/pricing')
def pricing():
    """Pricing page"""
    sync_session_plan()
    return render_template(
        'pricing.html',
        current_plan=session.get('plan_id', 'free'),
        plan_status=session.get('plan_status', 'free'),
        stripe_publishable_key=os.environ.get('STRIPE_PUBLISHABLE_KEY', '')
    )

@app.route('/privacy')
def privacy():
    return render_template('privacy.html', feedback_retention_days=FEEDBACK_RETENTION_DAYS)

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/signin')
def signin():
    return render_template('signin.html')

@app.route('/auth/start', methods=['POST'])
def auth_start():
    email = (request.form.get('email') or '').strip().lower()
    csrf_token = request.form.get('csrf_token')
    if not csrf_token or csrf_token != session.get('csrf_token'):
        message = 'Invalid request token. Please refresh and try again.'
        if is_ajax_request():
            return jsonify({'status': 'error', 'error': message}), 400
        flash(message, 'error')
        return redirect(url_for('signin'))

    if not email or '@' not in email:
        message = 'Please enter a valid email address.'
        if is_ajax_request():
            return jsonify({'status': 'error', 'error': message}), 400
        flash(message, 'error')
        return redirect(url_for('signin'))

    if rate_limited(f"rate:auth_start:{get_client_ip()}:{email}", 5, 900):
        message = 'Too many sign-in attempts. Please try again later.'
        if is_ajax_request():
            return jsonify({'status': 'error', 'error': message}), 429
        flash(message, 'error')
        return redirect(url_for('signin'))

    try:
        with get_db_session() as db:
            user = db.query(User).filter_by(email=email).first()
            if not user:
                user = User(email=email)
                if email in ADMIN_EMAILS:
                    user.role = 'admin'
                db.add(user)
                db.flush()

            token = secrets.token_urlsafe(32)
            token_hash = hash_token(token)
            expires_at = datetime.utcnow() + timedelta(minutes=MAGIC_LINK_EXP_MINUTES)
            auth_token = AuthToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=expires_at,
                ip=get_client_ip(),
                user_agent=request.headers.get('User-Agent', '')[:512]
            )
            db.add(auth_token)

        link = url_for('auth_verify', token=token, _external=True)
        send_magic_link_email(email, link)

        message = 'Check your email for your sign-in link.'
        if is_ajax_request():
            return jsonify({'status': 'ok', 'message': message}), 200
        flash(message, 'success')
        return redirect(url_for('signin'))
    except Exception as e:
        message = f'Unable to send magic link: {str(e)}'
        if is_ajax_request():
            return jsonify({'status': 'error', 'error': message}), 500
        flash(message, 'error')
        return redirect(url_for('signin'))

@app.route('/auth/verify')
def auth_verify():
    token = request.args.get('token', '')
    if not token:
        flash('Invalid or expired sign-in link.', 'error')
        return redirect(url_for('signin'))

    try:
        token_hash = hash_token(token)
        with get_db_session() as db:
            auth_token = db.query(AuthToken).filter_by(token_hash=token_hash).first()
            if not auth_token or auth_token.used_at is not None or auth_token.expires_at < datetime.utcnow():
                flash('Invalid or expired sign-in link.', 'error')
                return redirect(url_for('signin'))

            user = db.query(User).filter_by(id=auth_token.user_id).first()
            if not user or not user.is_active:
                flash('Account not available.', 'error')
                return redirect(url_for('signin'))

            auth_token.used_at = datetime.utcnow()
            user.last_login_at = datetime.utcnow()

            session['user_id'] = user.id
            session['user_email'] = user.email
            session['plan_status'] = user.plan_status
            session['plan_id'] = user.plan_id

        flash('Signed in successfully.', 'success')
        return redirect(url_for('home'))
    except Exception:
        flash('Unable to verify sign-in link. Please try again.', 'error')
        return redirect(url_for('signin'))

@app.route('/account')
def account():
    """Account dashboard — plan, usage, and recent conversions."""
    user_id = session.get('user_id')
    if not user_id:
        flash('Please sign in to view your account.', 'error')
        return redirect(url_for('signin'))

    sync_session_plan()
    plan_id = session.get('plan_id', 'free')
    plan_status = session.get('plan_status', 'free')
    plan = PLAN_CONFIG.get(plan_id, PLAN_CONFIG['free'])
    monthly_limit = plan['monthly_conversions']

    monthly_scope = f"monthly:{datetime.utcnow().strftime('%Y-%m')}"
    used_this_month = 0
    recent_jobs = []

    try:
        with get_db_session() as db:
            counter = get_usage_counter(db, user_id=user_id, guest_id=None, scope=monthly_scope)
            used_this_month = counter.conversions_count if counter else 0
            recent_jobs = (
                db.query(Job)
                .filter_by(user_id=user_id)
                .order_by(Job.created_at.desc())
                .limit(10)
                .all()
            )
            # Detach from session so template can read attributes after session closes
            for job in recent_jobs:
                db.expunge(job)
    except Exception as e:
        flash(f'Unable to load account data.', 'error')

    return render_template(
        'account.html',
        plan_id=plan_id,
        plan_status=plan_status,
        monthly_limit=monthly_limit,
        used_this_month=used_this_month,
        max_upload_mb=plan['max_upload_mb'],
        recent_jobs=recent_jobs,
        stripe_publishable_key=os.environ.get('STRIPE_PUBLISHABLE_KEY', ''),
    )

@app.route('/signout')
def signout():
    guest_id = session.get('guest_id')
    session.clear()
    if guest_id:
        session['guest_id'] = guest_id
    flash('Signed out successfully.', 'success')
    return redirect(url_for('home'))

@app.route('/admin')
def admin_dashboard():
    if not is_admin_user():
        return "Not found", 404

    stats = {
        'users': 0,
        'jobs': 0,
        'completed': 0
    }
    recent_jobs = []
    try:
        with get_db_session() as db:
            stats['users'] = db.query(User).count()
            stats['jobs'] = db.query(Job).count()
            stats['completed'] = db.query(Job).filter(Job.status.like('completed%')).count()
            recent_jobs = db.query(Job).order_by(Job.created_at.desc()).limit(20).all()
    except Exception as e:
        logger.warning(f"Admin dashboard query failed: {e}")

    return render_template('admin.html', stats=stats, recent_jobs=recent_jobs)

@app.route('/convert', methods=['POST'])
def convert():
    """Handle PDF conversion"""
    try:
        user_id, guest_id = get_identity()

        allowed, quota_error = check_conversion_quota(user_id, guest_id)
        if not allowed:
            if is_ajax_request():
                return jsonify({'status': 'error', **quota_error}), 403
            flash(quota_error.get('error', 'Conversion limit reached.'), 'error')
            return redirect(url_for('home'))

        if rate_limited(f"rate:convert:{get_client_ip()}", int(os.environ.get('RATE_LIMIT_CONVERT', '15')), 3600):
            message = 'Too many conversion requests. Please try again later.'
            if is_ajax_request():
                return jsonify({'status': 'error', 'error': message}), 429
            flash(message, 'error')
            return redirect(url_for('home'))

        csrf_token = request.form.get('csrf_token')
        if not csrf_token or csrf_token != session.get('csrf_token'):
            message = 'Invalid request token. Please refresh and try again.'
            if is_ajax_request():
                return jsonify({'status': 'error', 'error': message}), 400
            flash(message, 'error')
            return redirect(url_for('home'))

        # Validate form data
        if 'pdf_file' not in request.files:
            flash('Please upload a PDF file.', 'error')
            return redirect(url_for('home'))

        bank_code = request.form.get('bank', 'universal')
        pdf_file = request.files['pdf_file']
        
        # Validate file
        if pdf_file.filename == '':
            flash('No file selected.', 'error')
            return redirect(url_for('home'))
            
        if not is_pdf_file(pdf_file):
            flash('Please upload a PDF file.', 'error')
            return redirect(url_for('home'))
        
        # Generate unique job ID
        job_id = str(uuid.uuid4())
        filename = secure_filename(pdf_file.filename)
        
        # Save uploaded file with ABSOLUTE path (critical for Celery worker)
        unique_filename = f"{job_id}_{filename}"
        filepath = os.path.join(os.path.abspath(UPLOAD_FOLDER), unique_filename)
        pdf_file.save(filepath)

        file_size_bytes = os.path.getsize(filepath)
        page_count = None
        try:
            import pdfplumber
            with pdfplumber.open(filepath) as pdf:
                page_count = len(pdf.pages)
        except Exception as e:
            if os.path.exists(filepath):
                os.remove(filepath)
            message = f'Unable to read PDF pages: {str(e)}'
            if is_ajax_request():
                return jsonify({'status': 'error', 'error': message}), 400
            flash(message, 'error')
            return redirect(url_for('home'))

        if page_count and page_count > MAX_PAGES:
            if os.path.exists(filepath):
                os.remove(filepath)
            message = f'File has {page_count} pages. Maximum allowed is {MAX_PAGES}.'
            if is_ajax_request():
                return jsonify({
                    'status': 'error',
                    'error': message,
                    'error_code': 'PAGE_LIMIT_EXCEEDED',
                    'max_pages': MAX_PAGES,
                    'page_count': page_count
                }), 413
            flash(message, 'error')
            return redirect(url_for('home'))

        storage = get_storage_config()
        object_key = None
        if storage:
            object_key = f"uploads/{job_id}/{filename}"
            upload_file(storage, filepath, object_key)
            file_ref = {"type": "s3", "key": object_key}
            try:
                os.remove(filepath)
            except Exception:
                pass
        else:
            file_ref = {"type": "local", "path": filepath}

        try:
            with get_db_session() as db:
                job = Job(
                    id=job_id,
                    user_id=user_id,
                    guest_id=guest_id,
                    filename=filename,
                    file_size_bytes=file_size_bytes,
                    page_count=page_count,
                    status='queued',
                    storage_key=object_key if storage else None,
                    ip=get_client_ip(),
                    user_agent=request.headers.get('User-Agent', '')[:512]
                )
                db.add(job)
        except Exception as e:
            logger.warning(f"Failed to record job: {e}")
        
        # Get optional API key and quality mode
        api_key = request.form.get('api_key')
        quality = request.form.get('quality', 'standard')
        if quality not in ('standard', 'high'):
            quality = 'standard'
        
        # Clear any old status for this job_id (in case it existed)
        try:
            import redis
            redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
            r = redis.StrictRedis.from_url(redis_url)
            r.delete(f"job_status:{job_id}")
        except:
            pass
        
        # Trigger Celery Task
        from worker import process_pdf_task
        task = process_pdf_task.delay(file_ref, filename, job_id, api_key, quality)
        
        # Return JSON response for AJAX handling, or redirect for legacy
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'status': 'accepted',
                'job_id': job_id,
                'task_id': task.id,
                'message': 'Processing started'
            }), 202
            
        # For non-AJAX, existing templates might need update or we show a pending page
        # But per user request, we are moving to Progress Bar approach which implies JS
        return render_template('processing.html', job_id=job_id, filename=filename)
    
    except Exception as e:
        flash(f'Error processing request: {str(e)}', 'error')
        # Clean up uploaded file if it exists
        try:
            if 'filepath' in locals() and os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass
        return redirect(url_for('home'))


@app.route('/status/<job_id>')
def job_status(job_id):
    """Check job status (fallback/polling)"""
    if rate_limited(f"rate:status:{get_client_ip()}", int(os.environ.get('RATE_LIMIT_STATUS', '120')), 60):
        return jsonify({'status': 'error', 'percent': 0, 'error': 'Too many status requests.'}), 429
    import redis
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    r = redis.StrictRedis.from_url(redis_url)
    
    # Check Redis for progress
    status_data = r.get(f"job_status:{job_id}")
    if status_data:
        try:
            import ast
            data = ast.literal_eval(status_data.decode('utf-8'))
            
            # Check for completion to provide download link
            if data.get('percent') >= 100 or data.get('status') == 'Completed successfully':
                if data.get('storage') == 's3' and data.get('download_key'):
                    storage = get_storage_config()
                    if storage:
                        data['download_url'] = generate_presigned_url(storage, data['download_key'])
                else:
                    # Find the actual Excel file in the processed directory
                    processed_root = os.environ.get('SHARED_STORAGE_PATH') or os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), 'processed'
                    )
                    processed_dir = os.path.join(processed_root, job_id)
                    if os.path.exists(processed_dir):
                        excel_files = [f for f in os.listdir(processed_dir) if f.endswith('.xlsx')]
                        if excel_files:
                            data['download_url'] = url_for('download_result', job_id=job_id, filename=excel_files[0])
            
            return jsonify(data)
        except Exception as e:
            return jsonify({'status': 'error', 'percent': 0, 'error': str(e)})
            
    return jsonify({'status': 'processing', 'percent': 0})

@app.route('/download/<job_id>/<filename>')
def download_result(job_id, filename):
    """Download processed file"""
    processed_root = os.environ.get('SHARED_STORAGE_PATH') or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'processed'
    )
    directory = os.path.join(processed_root, job_id)
    filepath = os.path.join(directory, filename)
    
    if not os.path.exists(filepath):
        flash('File not found. It may have expired.', 'error')
        return redirect(url_for('home'))
    
    return send_file(filepath, as_attachment=True, download_name=filename)


@app.route('/feedback', methods=['POST'])
def submit_feedback():
    """
    Accept user feedback when a conversion produces poor or empty results.
    Optionally, the user can consent to share their PDF for analysis.
    """
    if rate_limited(f"rate:feedback:{get_client_ip()}", 10, 3600):
        return jsonify({'status': 'error', 'error': 'Too many feedback submissions.'}), 429

    try:
        user_id, guest_id = get_identity()
        job_id = request.form.get('job_id', '').strip()
        feedback_type = request.form.get('feedback_type', 'other').strip()
        message = request.form.get('message', '').strip()[:2000]  # Cap length
        share_pdf = request.form.get('share_pdf', '').lower() in {'1', 'true', 'yes', 'on'}
        quality_used = request.form.get('quality_used', 'standard')
        extraction_rows = int(request.form.get('extraction_rows', 0) or 0)
        extraction_cols = int(request.form.get('extraction_cols', 0) or 0)

        if feedback_type not in ('empty_result', 'incorrect_data', 'formatting', 'other'):
            feedback_type = 'other'

        pdf_storage_key = None
        # If user consents to share PDF, copy it to a feedback storage location
        if share_pdf and job_id:
            storage = get_storage_config()
            if storage:
                # PDF is already in S3 under uploads/{job_id}/
                # Just record the reference — we won't delete feedback PDFs on cleanup
                try:
                    with get_db_session() as db:
                        job = db.get(Job, job_id)
                        if job and job.storage_key:
                            pdf_storage_key = job.storage_key  # Reference existing upload
                except Exception:
                    pass

        with get_db_session() as db:
            fb = FeedbackSubmission(
                job_id=job_id or None,
                user_id=user_id,
                guest_id=guest_id,
                feedback_type=feedback_type,
                message=message,
                pdf_shared=share_pdf,
                pdf_storage_key=pdf_storage_key,
                extraction_rows=extraction_rows,
                extraction_cols=extraction_cols,
                quality_used=quality_used,
                ip=get_client_ip(),
                user_agent=request.headers.get('User-Agent', '')[:512]
            )
            db.add(fb)

        return jsonify({'status': 'ok', 'message': 'Thank you for your feedback!'}), 200

    except Exception as e:
        logger.warning(f"Feedback submission error: {e}")
        return jsonify({'status': 'error', 'error': 'Failed to submit feedback.'}), 500


# ── Stripe Checkout & Billing ──────────────────────────────────────────

@app.route('/checkout/create', methods=['POST'])
def checkout_create():
    """Create a Stripe Checkout Session and return the redirect URL."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Sign in required.'}), 401

    csrf_token = request.form.get('csrf_token') or (request.get_json(silent=True) or {}).get('csrf_token')
    if not csrf_token or csrf_token != session.get('csrf_token'):
        return jsonify({'error': 'Invalid request token.'}), 400

    plan_id = request.form.get('plan_id') or (request.get_json(silent=True) or {}).get('plan_id')
    plan = PLAN_CONFIG.get(plan_id)
    if not plan or not plan.get('stripe_price_id'):
        return jsonify({'error': 'Invalid plan.'}), 400

    try:
        with get_db_session() as db:
            user = db.query(User).filter_by(id=user_id).first()
            if not user:
                return jsonify({'error': 'User not found.'}), 404

            # Lazily create Stripe Customer
            if not user.stripe_customer_id:
                customer = stripe.Customer.create(email=user.email)
                user.stripe_customer_id = customer.id
                db.flush()

            customer_id = user.stripe_customer_id

        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            mode='subscription',
            line_items=[{'price': plan['stripe_price_id'], 'quantity': 1}],
            success_url=url_for('checkout_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('pricing', _external=True),
            metadata={'user_id': user_id, 'plan_id': plan_id},
        )
        return jsonify({'checkout_url': checkout_session.url})

    except Exception as e:
        logger.warning(f"Checkout create failed: {e}")
        return jsonify({'error': 'Unable to create checkout session.'}), 500


@app.route('/checkout/success')
def checkout_success():
    """Post-checkout landing page. Subscription is activated by webhook, not here."""
    flash('Your subscription is being activated. This usually takes a few seconds.', 'success')
    return redirect(url_for('home'))


@app.route('/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """Receive Stripe webhook events. Signature-verified (no CSRF)."""
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')

    if not webhook_secret:
        logger.warning("STRIPE_WEBHOOK_SECRET not configured")
        return '', 400

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        return '', 400

    event_type = event['type']
    data_object = event['data']['object']

    handlers = {
        'checkout.session.completed': _handle_checkout_completed,
        'customer.subscription.updated': _handle_subscription_updated,
        'customer.subscription.deleted': _handle_subscription_deleted,
        'invoice.payment_failed': _handle_payment_failed,
    }

    handler = handlers.get(event_type)
    if handler:
        try:
            handler(data_object)
        except Exception as e:
            logger.warning(f"Webhook handler error for {event_type}: {e}")

    return '', 200


def _handle_checkout_completed(session_obj):
    """checkout.session.completed — activate subscription."""
    user_id = (session_obj.get('metadata') or {}).get('user_id')
    plan_id = (session_obj.get('metadata') or {}).get('plan_id')
    subscription_id = session_obj.get('subscription')
    customer_id = session_obj.get('customer')

    if not user_id:
        # Fallback: find user by Stripe customer ID
        if customer_id:
            with get_db_session() as db:
                user = db.query(User).filter_by(stripe_customer_id=customer_id).first()
                if user:
                    user_id = user.id

    if not user_id:
        logger.warning(f"checkout.session.completed: no user_id found")
        return

    with get_db_session() as db:
        user = db.query(User).filter_by(id=user_id).first()
        if user:
            user.plan_id = plan_id or 'pro'
            user.plan_status = 'active'
            user.stripe_subscription_id = subscription_id
            if customer_id and not user.stripe_customer_id:
                user.stripe_customer_id = customer_id
            _send_plan_change_email(user.email, plan_id or 'pro', 'activated')


def _send_plan_change_email(email: str, plan_id: str, action: str) -> None:
    """Best-effort notification when a subscription changes."""
    try:
        import resend
        api_key = os.environ.get('RESEND_API_KEY')
        if not api_key:
            return
        resend.api_key = api_key
        sender = os.environ.get('RESEND_FROM_EMAIL', 'onboarding@resend.dev')

        subjects = {
            'activated': f'Your {plan_id.capitalize()} plan is active',
            'canceled': 'Your subscription has been canceled',
        }
        bodies = {
            'activated': f'Your <strong>{plan_id.capitalize()}</strong> plan is now active. Enjoy your upgraded limits!',
            'canceled': 'Your subscription has been canceled and your account has been moved to the Free plan. You can re-subscribe anytime from the Pricing page.',
        }
        subject = subjects.get(action, f'Subscription update: {action}')
        body = bodies.get(action, f'Your subscription status has changed to: {action}.')

        resend.Emails.send({
            "from": sender,
            "to": [email],
            "subject": subject,
            "text": body.replace('<strong>', '').replace('</strong>', ''),
            "html": f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f7;font-family:'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f7;padding:40px 0;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.08);">
        <tr>
          <td style="background:linear-gradient(135deg,#667eea,#764ba2);padding:28px 40px;text-align:center;">
            <h1 style="margin:0;color:#ffffff;font-size:20px;font-weight:600;">StatementFlow</h1>
          </td>
        </tr>
        <tr>
          <td style="padding:32px 40px;">
            <p style="margin:0 0 16px;color:#333;font-size:16px;line-height:1.5;">{body}</p>
            <p style="margin:0;color:#666;font-size:13px;">If you have questions, reply to this email.</p>
          </td>
        </tr>
        <tr>
          <td style="padding:16px 40px 24px;border-top:1px solid #eee;">
            <p style="margin:0;color:#999;font-size:12px;">StatementFlow &mdash; PDF to Excel Converter</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
""",
        })
    except Exception as e:
        logger.warning(f"Plan change email failed for {email}: {e}")


def _handle_subscription_updated(subscription):
    """customer.subscription.updated — sync plan status."""
    customer_id = subscription.get('customer')
    stripe_status = subscription.get('status')

    status_map = {
        'active': 'active',
        'trialing': 'active',
        'past_due': 'past_due',
        'canceled': 'free',
        'unpaid': 'free',
        'incomplete': 'free',
        'incomplete_expired': 'free',
    }
    new_status = status_map.get(stripe_status, 'free')

    with get_db_session() as db:
        user = db.query(User).filter_by(stripe_customer_id=customer_id).first()
        if user:
            user.plan_status = new_status
            if new_status == 'free':
                user.plan_id = 'free'


def _handle_subscription_deleted(subscription):
    """customer.subscription.deleted — reset to free plan."""
    customer_id = subscription.get('customer')

    with get_db_session() as db:
        user = db.query(User).filter_by(stripe_customer_id=customer_id).first()
        if user:
            user.plan_id = 'free'
            user.plan_status = 'free'
            user.stripe_subscription_id = None
            _send_plan_change_email(user.email, 'free', 'canceled')


def _handle_payment_failed(invoice):
    """invoice.payment_failed — mark as past_due (grace period)."""
    customer_id = invoice.get('customer')

    with get_db_session() as db:
        user = db.query(User).filter_by(stripe_customer_id=customer_id).first()
        if user and user.plan_status == 'active':
            user.plan_status = 'past_due'


@app.route('/billing/portal', methods=['POST'])
def billing_portal():
    """Create a Stripe Customer Portal session and redirect."""
    user_id = session.get('user_id')
    if not user_id:
        flash('Please sign in first.', 'error')
        return redirect(url_for('signin'))

    csrf_token = request.form.get('csrf_token')
    if not csrf_token or csrf_token != session.get('csrf_token'):
        flash('Invalid request token. Please refresh and try again.', 'error')
        return redirect(url_for('pricing'))

    try:
        with get_db_session() as db:
            user = db.query(User).filter_by(id=user_id).first()
            if not user or not user.stripe_customer_id:
                flash('No active subscription found.', 'error')
                return redirect(url_for('pricing'))
            customer_id = user.stripe_customer_id

        portal_session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=url_for('pricing', _external=True),
        )
        return redirect(portal_session.url)

    except Exception as e:
        logger.warning(f"Billing portal failed: {e}")
        flash('Unable to open billing portal.', 'error')
        return redirect(url_for('pricing'))


@app.route('/health')
def health_check():
    """Health check endpoint for Railway - Simple but accurate"""
    try:
        # Check if uploads directory exists
        if not os.path.exists(UPLOAD_FOLDER):
            return "UPLOADS_DIR_MISSING", 500
        
        # Test parser imports (critical for app functionality)
        try:
            UniversalBankParser()
        except Exception:
            return "PARSERS_FAILED", 500
        
        # If we get here, everything is working
        return "OK", 200
        
    except Exception as e:
        return f"ERROR: {str(e)}", 500

@app.route('/health/detailed')
def detailed_health():
    """Detailed health check with more info"""
    try:
        checks = {
            'flask': 'OK',
            'uploads_dir': 'OK' if os.path.exists(UPLOAD_FOLDER) else 'MISSING',
            'parsers': 'OK',
            'redis': 'OK',
        }

        # Test parsers import
        try:
            UniversalBankParser()
        except Exception:
            checks['parsers'] = 'ERROR'

        # Test Redis connectivity
        try:
            import redis as _redis
            r = _redis.StrictRedis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))
            r.ping()
        except Exception:
            checks['redis'] = 'ERROR'

        all_ok = all(v == 'OK' for v in checks.values())

        response_data = {
            'status': 'healthy' if all_ok else 'degraded',
            'timestamp': datetime.utcnow().isoformat(),
            'service': 'pdf-excel-converter',
            'version': os.environ.get('APP_VERSION', 'dev'),
            'checks': checks,
        }

        return jsonify(response_data), 200 if all_ok else 503

    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat()
        }), 500


@app.route('/sitemap.xml')
def sitemap():
    """Generate sitemap.xml for SEO"""
    from flask import Response
    base_url = request.url_root.rstrip('/')
    
    today = datetime.utcnow().strftime('%Y-%m-%d')
    sitemap_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>{base_url}/</loc>
        <lastmod>{today}</lastmod>
        <changefreq>weekly</changefreq>
        <priority>1.0</priority>
    </url>
    <url>
        <loc>{base_url}/blogs</loc>
        <lastmod>{today}</lastmod>
        <changefreq>weekly</changefreq>
        <priority>0.8</priority>
    </url>
    <url>
        <loc>{base_url}/pricing</loc>
        <lastmod>{today}</lastmod>
        <changefreq>monthly</changefreq>
        <priority>0.9</priority>
    </url>
    <url>
        <loc>{base_url}/privacy</loc>
        <lastmod>{today}</lastmod>
        <changefreq>monthly</changefreq>
        <priority>0.3</priority>
    </url>
    <url>
        <loc>{base_url}/terms</loc>
        <lastmod>{today}</lastmod>
        <changefreq>monthly</changefreq>
        <priority>0.3</priority>
    </url>
</urlset>"""
    
    return Response(sitemap_content, mimetype='application/xml')

@app.route('/robots.txt')
def robots():
    """Generate robots.txt for SEO"""
    from flask import Response
    base_url = request.url_root.rstrip('/')
    
    robots_content = f"""User-agent: *
Allow: /
Allow: /blogs
Allow: /pricing
Allow: /privacy
Allow: /terms
Disallow: /uploads/
Disallow: /processed/
Disallow: /status/
Disallow: /download/

Sitemap: {base_url}/sitemap.xml"""
    
    return Response(robots_content, mimetype='text/plain')

# For deployment with gunicorn, we need to expose the SocketIO app
# This allows gunicorn to use: gunicorn app:socketio

if __name__ == '__main__':
    # For local development
    port = int(os.environ.get('PORT', 5001))
    print(f"Starting Flask app on port {port}")
    socketio.run(app, debug=False, host='0.0.0.0', port=port)
