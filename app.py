#!/usr/bin/env python3
"""
PDF to Excel Converter Web Application
Universal parser for bank statement PDFs
"""
# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

import os
import tempfile
import uuid
import gc
from datetime import datetime, timedelta
import hashlib
import secrets
from flask import Flask, render_template, request, jsonify, send_file, flash, redirect, url_for, session
from werkzeug.exceptions import RequestEntityTooLarge
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

# Import parsers
from parsers.universal_parser import UniversalBankParser, ProcessingConfig
from storage_utils import get_storage_config, upload_file, generate_presigned_url
from db import init_db, get_db_session
from models import User, AuthToken, Job, UsageCounter

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
MAX_UPLOAD_MB = int(os.environ.get('MAX_UPLOAD_MB', '20'))
MAX_PAGES = int(os.environ.get('MAX_PAGES', '250'))
GUEST_CONVERSION_LIMIT = int(os.environ.get('GUEST_CONVERSION_LIMIT', '1'))
USER_CONVERSION_LIMIT = int(os.environ.get('USER_CONVERSION_LIMIT', '5'))
MAGIC_LINK_EXP_MINUTES = int(os.environ.get('MAGIC_LINK_EXP_MINUTES', '15'))
DISABLE_QUOTAS = os.environ.get('DISABLE_QUOTAS', '').strip().lower() in {'1', 'true', 'yes', 'on'}
ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.environ.get('ADMIN_EMAILS', '').split(',')
    if email.strip()
}
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_MB * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
app.config['GA_MEASUREMENT_ID'] = os.environ.get('GA_MEASUREMENT_ID', '')  # Google Analytics Measurement ID (e.g., G-XXXXXXXXXX)
app.config['GTM_CONTAINER_ID'] = os.environ.get('GTM_CONTAINER_ID', '')  # Google Tag Manager Container ID (optional)

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
    print(f"⚠️ Database initialization failed: {e}")

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
        'current_user_email': session.get('user_email')
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
    from boto3 import client

    sender = os.environ.get('SES_FROM_EMAIL')
    if not sender:
        raise RuntimeError('SES_FROM_EMAIL is not configured')

    region = os.environ.get('SES_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'us-east-1'
    ses_client = client('ses', region_name=region)

    subject = "Your magic sign-in link"
    body_text = f"Click the link to sign in:\n\n{link}\n\nIf you didn't request this, you can ignore this email."
    body_html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #111;">
        <p>Click the link below to sign in:</p>
        <p><a href="{link}">Sign in to StatementFlow</a></p>
        <p style="color:#555;font-size:12px;">If you didn't request this, you can ignore this email.</p>
      </body>
    </html>
    """

    ses_client.send_email(
        Source=sender,
        Destination={'ToAddresses': [email]},
        Message={
            'Subject': {'Data': subject},
            'Body': {
                'Text': {'Data': body_text},
                'Html': {'Data': body_html}
            }
        }
    )

def get_usage_counter(db, user_id, guest_id):
    return db.query(UsageCounter).filter_by(
        user_id=user_id,
        guest_id=guest_id,
        scope='lifetime'
    ).first()

def check_conversion_quota(user_id, guest_id):
    if DISABLE_QUOTAS or not os.environ.get('SES_FROM_EMAIL'):
        return True, None
    try:
        with get_db_session() as db:
            if user_id:
                user = db.query(User).filter_by(id=user_id).first()
                if user and user.plan_status == 'active':
                    return True, None

                counter = get_usage_counter(db, user_id=user_id, guest_id=None)
                used = counter.conversions_count if counter else 0
                if used >= USER_CONVERSION_LIMIT:
                    return False, {
                        'error': 'You have reached your lifetime conversion limit. Please upgrade to continue.',
                        'error_code': 'USER_LIMIT_EXCEEDED'
                    }
            else:
                counter = get_usage_counter(db, user_id=None, guest_id=guest_id)
                used = counter.conversions_count if counter else 0
                if used >= GUEST_CONVERSION_LIMIT:
                    return False, {
                        'error': 'You have used your free conversion. Sign in to get more.',
                        'error_code': 'GUEST_LIMIT_EXCEEDED',
                        'requires_login': True
                    }
    except Exception as e:
        print(f"⚠️ Quota check failed: {e}")
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
    return render_template(
        'home.html',
        banks=SUPPORTED_BANKS,
        max_upload_mb=MAX_UPLOAD_MB,
        max_pages=MAX_PAGES
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
    return render_template('pricing.html')

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
        print(f"⚠️ Admin dashboard query failed: {e}")

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
            print(f"⚠️ Failed to record job: {e}")
        
        # Get optional API key
        api_key = request.form.get('api_key')
        
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
        task = process_pdf_task.delay(file_ref, filename, job_id, api_key)
        
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
        # Test basic functionality
        response_data = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'service': 'pdf-excel-converter',
            'version': '1.0.0',
            'checks': {
                'flask': 'OK',
                'uploads_dir': 'OK' if os.path.exists(UPLOAD_FOLDER) else 'MISSING',
                'parsers': 'OK'
            }
        }
        
        # Test parsers import
        try:
            UniversalBankParser()
            response_data['checks']['parsers'] = 'OK'
        except Exception:
            response_data['checks']['parsers'] = 'ERROR'
            
        return jsonify(response_data), 200
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500


@app.route('/sitemap.xml')
def sitemap():
    """Generate sitemap.xml for SEO"""
    from flask import Response
    base_url = request.url_root.rstrip('/')
    
    sitemap_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>{base_url}/</loc>
        <lastmod>2025-01-27</lastmod>
        <changefreq>weekly</changefreq>
        <priority>1.0</priority>
    </url>
    <url>
        <loc>{base_url}/blogs</loc>
        <lastmod>2025-01-27</lastmod>
        <changefreq>weekly</changefreq>
        <priority>0.8</priority>
    </url>
    <url>
        <loc>{base_url}/pricing</loc>
        <lastmod>2025-01-27</lastmod>
        <changefreq>monthly</changefreq>
        <priority>0.9</priority>
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
