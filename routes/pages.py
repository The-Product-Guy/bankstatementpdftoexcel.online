"""Public pages, SEO, health checks, and admin routes."""
import logging
import os
from datetime import datetime

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, session, url_for

from db import get_db_session
from models import Job, User
from parsers.universal_parser import UniversalBankParser

logger = logging.getLogger(__name__)

pages_bp = Blueprint('pages', __name__)


@pages_bp.route('/')
def home():
    """Home page with upload functionality."""
    from app import (
        BETA_MODE, ENGLISH_ONLY_BETA, FEEDBACK_RETENTION_DAYS, MAX_PAGES, MAX_UPLOAD_MB,
        SUPPORTED_BANKS, UPLOAD_FOLDER,
        cleanup_expired_s3_results, cleanup_feedback_shared_pdfs, cleanup_old_files,
    )
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


@pages_bp.route('/index')
def index():
    """Legacy route -- redirect to home."""
    return redirect(url_for('pages.home'))


@pages_bp.route('/blogs')
def blogs():
    return render_template('blogs.html')


@pages_bp.route('/pricing')
def pricing():
    from app import sync_session_plan
    sync_session_plan()
    return render_template(
        'pricing.html',
        current_plan=session.get('plan_id', 'free'),
        plan_status=session.get('plan_status', 'free'),
        stripe_publishable_key=os.environ.get('STRIPE_PUBLISHABLE_KEY', '')
    )


@pages_bp.route('/privacy')
def privacy():
    from app import FEEDBACK_RETENTION_DAYS
    return render_template('privacy.html', feedback_retention_days=FEEDBACK_RETENTION_DAYS)


@pages_bp.route('/terms')
def terms():
    return render_template('terms.html')


@pages_bp.route('/admin')
def admin_dashboard():
    from app import is_admin_user
    if not is_admin_user():
        return "Not found", 404

    stats = {'users': 0, 'jobs': 0, 'completed': 0}
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


# -- Health checks --

@pages_bp.route('/health')
def health_check():
    """Simple health check for Railway."""
    from app import UPLOAD_FOLDER
    try:
        if not os.path.exists(UPLOAD_FOLDER):
            return "UPLOADS_DIR_MISSING", 500
        try:
            UniversalBankParser()
        except Exception:
            return "PARSERS_FAILED", 500
        return "OK", 200
    except Exception as e:
        return f"ERROR: {str(e)}", 500


@pages_bp.route('/health/detailed')
def detailed_health():
    """Detailed health check with component status."""
    from app import UPLOAD_FOLDER
    try:
        checks = {
            'flask': 'OK',
            'uploads_dir': 'OK' if os.path.exists(UPLOAD_FOLDER) else 'MISSING',
            'parsers': 'OK',
            'redis': 'OK',
        }

        try:
            UniversalBankParser()
        except Exception:
            checks['parsers'] = 'ERROR'

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


# -- SEO --

@pages_bp.route('/sitemap.xml')
def sitemap():
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


@pages_bp.route('/robots.txt')
def robots():
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
