"""Public pages, SEO, health checks, and admin routes."""
import csv
import io
import logging
import os
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import distinct, func

from db import get_db_session
from models import Job, LoginEvent, SiteVisit, User
from parsers.universal_parser import UniversalBankParser

logger = logging.getLogger(__name__)

pages_bp = Blueprint('pages', __name__)


LASTMOD = {
    'home': '2026-05-15',
    'blogs': '2026-05-15',
    'pricing': '2026-05-15',
    'privacy': '2026-05-15',
    'terms': '2026-05-15',
}

BLOG_POSTS = [
    {
        'slug': 'how-to-convert-bank-statements-to-excel',
        'title': 'How to Convert Bank Statements to Excel',
        'category': 'Tutorial',
        'description': 'A practical guide to converting text-based and scanned bank statement PDFs into Excel.',
        'lastmod': '2026-05-15',
        'sections': [
            {
                'heading': 'Conversion workflow',
                'items': [
                    'Upload your bank statement PDF from the dashboard.',
                    'The extraction engine detects whether the PDF is text-based or scanned.',
                    'Text PDFs use direct table and layout extraction. Scanned PDFs use OCR and table-structure detection.',
                    'Download an Excel file with raw rows and normalized transaction columns.',
                ],
            },
            {
                'heading': 'What to check in the output',
                'paragraphs': [
                    'Always verify dates, debit and credit amounts, running balances, and any rows with merged descriptions before using the file for accounting or audit work.',
                ],
            },
        ],
    },
    {
        'slug': 'excel-tips-for-converted-bank-statements',
        'title': 'Excel Tips for Working with Converted Statements',
        'category': 'Tips',
        'description': 'Useful Excel checks and formulas after converting a bank statement PDF.',
        'lastmod': '2026-05-15',
        'sections': [
            {
                'heading': 'Recommended checks',
                'items': [
                    'Use pivot tables to summarize spending by month or description.',
                    'Use SUMIFS to total categories such as UPI, ATM, fees, or salary credits.',
                    'Freeze the header row before reviewing long statements.',
                    'Sort by date and compare the running balance against the original PDF.',
                ],
            },
        ],
    },
    {
        'slug': 'how-financial-data-is-handled',
        'title': 'How Financial Data Is Handled',
        'category': 'Security',
        'description': 'How Statement Converter handles temporary files, conversion results, and feedback PDFs.',
        'lastmod': '2026-05-15',
        'sections': [
            {
                'heading': 'Retention model',
                'items': [
                    'Uploaded PDFs are deleted after processing unless you explicitly retain one for feedback.',
                    'Converted Excel files are available only during a short download window.',
                    'Feedback PDFs are stored only when you opt in and expire after the configured feedback retention period.',
                    'Temporary files and generated results are cleaned up automatically.',
                ],
            },
        ],
    },
    {
        'slug': 'text-based-vs-scanned-bank-statement-pdfs',
        'title': 'Text-Based vs Scanned Bank Statement PDFs',
        'category': 'Guide',
        'description': 'How to tell whether a bank statement PDF is text-based or scanned and why it affects extraction quality.',
        'lastmod': '2026-05-15',
        'sections': [
            {
                'heading': 'How to tell the difference',
                'paragraphs': [
                    'Text-based PDFs are generated digitally by the bank and usually let you select individual words. Scanned PDFs are page images and require OCR before table extraction can work.',
                    'Scanned statements can take longer and may need high-quality mode when the original image is faded, rotated, or compressed.',
                ],
            },
        ],
    },
    {
        'slug': 'bank-statement-conversion-faq',
        'title': 'Bank Statement Conversion FAQ',
        'category': 'FAQ',
        'description': 'Answers about supported statement layouts, upload limits, privacy, and password-protected PDFs.',
        'lastmod': '2026-05-15',
        'sections': [
            {
                'heading': 'Common questions',
                'items': [
                    'Statement Converter is built for tabular bank statement PDFs, text-based or scanned.',
                    'Free, Pro, and Enterprise plans have different file-size and conversion limits.',
                    'Password-protected PDFs are rejected with a clear message. Remove the password before uploading.',
                    'If the result is incomplete, retry high-quality mode or submit feedback.',
                ],
            },
        ],
    },
]

BLOG_POST_BY_SLUG = {post['slug']: post for post in BLOG_POSTS}


def _public_base_url() -> str:
    configured = os.environ.get('CANONICAL_BASE_URL') or os.environ.get('PUBLIC_BASE_URL')
    if configured:
        return configured.rstrip('/')
    return request.url_root.rstrip('/')


def _admin_csv_response(filename: str, headers, rows) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    response = Response(buffer.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _admin_days_param(default: int = 30, maximum: int = 365) -> int:
    try:
        days = int(request.args.get('days', default))
    except (TypeError, ValueError):
        days = default
    return max(1, min(days, maximum))


def _admin_day_key(value) -> str:
    if hasattr(value, 'isoformat'):
        return value.isoformat()[:10]
    return str(value)[:10]


def _admin_daily_metrics(db, days: int) -> list:
    today = datetime.utcnow().date()
    start_day = today - timedelta(days=days - 1)
    cutoff = datetime.combine(start_day, datetime.min.time())
    metrics_by_day = {}
    for offset in range(days):
        day = (start_day + timedelta(days=offset)).isoformat()
        metrics_by_day[day] = {
            'day': day,
            'unique_visitors': 0,
            'page_views': 0,
            'magic_link_requests': 0,
            'login_successes': 0,
        }

    visit_day = func.date(SiteVisit.created_at)
    visit_rows = (
        db.query(
            visit_day,
            func.count(SiteVisit.id),
            func.count(distinct(SiteVisit.visitor_id)),
        )
        .filter(SiteVisit.created_at >= cutoff)
        .group_by(visit_day)
        .all()
    )
    for day, page_views, unique_visitors in visit_rows:
        key = _admin_day_key(day)
        if key in metrics_by_day:
            metrics_by_day[key]['page_views'] = page_views
            metrics_by_day[key]['unique_visitors'] = unique_visitors

    login_day = func.date(LoginEvent.created_at)
    login_rows = (
        db.query(login_day, LoginEvent.event_type, func.count(LoginEvent.id))
        .filter(
            LoginEvent.created_at >= cutoff,
            LoginEvent.event_type.in_(('magic_link_requested', 'login_success')),
        )
        .group_by(login_day, LoginEvent.event_type)
        .all()
    )
    for day, event_type, event_count in login_rows:
        key = _admin_day_key(day)
        if key not in metrics_by_day:
            continue
        if event_type == 'magic_link_requested':
            metrics_by_day[key]['magic_link_requests'] = event_count
        elif event_type == 'login_success':
            metrics_by_day[key]['login_successes'] = event_count

    return list(metrics_by_day.values())


def _require_admin():
    from app import is_admin_user
    return is_admin_user()


@pages_bp.route('/')
def home():
    """Marketing landing page."""
    from app import (
        cleanup_expired_s3_results, cleanup_feedback_shared_pdfs,
        cleanup_first_party_analytics, cleanup_old_files,
    )
    cleanup_old_files()
    cleanup_feedback_shared_pdfs()
    cleanup_expired_s3_results()
    cleanup_first_party_analytics()
    return render_template('home.html')


@pages_bp.route('/index')
def index():
    """Legacy route -- redirect to home."""
    return redirect(url_for('pages.home'))


@pages_bp.route('/blogs')
def blogs():
    return render_template('blogs.html', blog_posts=BLOG_POSTS)


@pages_bp.route('/blogs/<slug>')
def blog_post(slug):
    post = BLOG_POST_BY_SLUG.get(slug)
    if not post:
        return "Not found", 404
    return render_template('blog_post.html', post=post)


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
    from app import FEEDBACK_RETENTION_DAYS, FIRST_PARTY_ANALYTICS_RETENTION_DAYS
    return render_template(
        'privacy.html',
        feedback_retention_days=FEEDBACK_RETENTION_DAYS,
        analytics_retention_days=FIRST_PARTY_ANALYTICS_RETENTION_DAYS,
    )


@pages_bp.route('/terms')
def terms():
    return render_template('terms.html')


@pages_bp.route('/admin')
def admin_dashboard():
    from app import FIRST_PARTY_ANALYTICS_RETENTION_DAYS
    if not _require_admin():
        return "Not found", 404

    analytics_days = _admin_days_param()
    stats = {
        'users': 0,
        'jobs': 0,
        'completed': 0,
        'page_views_24h': 0,
        'unique_visitors_24h': 0,
        'page_views_7d': 0,
        'unique_visitors_7d': 0,
        'logins_24h': 0,
        'logins_7d': 0,
        'magic_links_24h': 0,
        'magic_links_7d': 0,
        'login_conversion_7d': 0,
    }
    daily_metrics = []
    recent_jobs = []
    recent_logins = []
    recent_users = []
    top_paths = []
    try:
        with get_db_session() as db:
            now = datetime.utcnow()
            day_ago = now - timedelta(days=1)
            week_ago = now - timedelta(days=7)
            analytics_cutoff = now - timedelta(days=analytics_days)
            stats['users'] = db.query(User).count()
            stats['jobs'] = db.query(Job).count()
            stats['completed'] = db.query(Job).filter(Job.status.like('completed%')).count()
            stats['page_views_24h'] = db.query(SiteVisit).filter(SiteVisit.created_at >= day_ago).count()
            stats['page_views_7d'] = db.query(SiteVisit).filter(SiteVisit.created_at >= week_ago).count()
            stats['unique_visitors_24h'] = (
                db.query(func.count(distinct(SiteVisit.visitor_id)))
                .filter(SiteVisit.created_at >= day_ago)
                .scalar()
                or 0
            )
            stats['unique_visitors_7d'] = (
                db.query(func.count(distinct(SiteVisit.visitor_id)))
                .filter(SiteVisit.created_at >= week_ago)
                .scalar()
                or 0
            )
            stats['logins_24h'] = db.query(LoginEvent).filter(
                LoginEvent.event_type == 'login_success',
                LoginEvent.created_at >= day_ago,
            ).count()
            stats['logins_7d'] = db.query(LoginEvent).filter(
                LoginEvent.event_type == 'login_success',
                LoginEvent.created_at >= week_ago,
            ).count()
            stats['magic_links_24h'] = db.query(LoginEvent).filter(
                LoginEvent.event_type == 'magic_link_requested',
                LoginEvent.created_at >= day_ago,
            ).count()
            stats['magic_links_7d'] = db.query(LoginEvent).filter(
                LoginEvent.event_type == 'magic_link_requested',
                LoginEvent.created_at >= week_ago,
            ).count()
            if stats['magic_links_7d']:
                stats['login_conversion_7d'] = round(
                    stats['logins_7d'] / stats['magic_links_7d'] * 100,
                    1,
                )
            recent_user_rows = (
                db.query(User)
                .order_by(
                    User.last_login_at.is_(None),
                    User.last_login_at.desc(),
                    User.created_at.desc(),
                )
                .limit(20)
                .all()
            )
            recent_users = [
                {
                    'email': user.email,
                    'role': user.role or 'user',
                    'plan_id': user.plan_id or 'free',
                    'plan_status': user.plan_status or 'free',
                    'created_at': user.created_at,
                    'last_login_at': user.last_login_at,
                    'is_active': user.is_active,
                }
                for user in recent_user_rows
            ]
            recent_jobs = db.query(Job).order_by(Job.created_at.desc()).limit(20).all()
            for job in recent_jobs:
                db.expunge(job)
            recent_login_rows = (
                db.query(LoginEvent)
                .order_by(LoginEvent.created_at.desc())
                .limit(20)
                .all()
            )
            recent_logins = [
                {
                    'email': event.email or '-',
                    'event_type': event.event_type,
                    'success': event.success,
                    'created_at': event.created_at,
                    'ip': event.ip or '-',
                }
                for event in recent_login_rows
            ]
            top_paths = [
                {'path': path, 'views': views}
                for path, views in (
                    db.query(SiteVisit.path, func.count(SiteVisit.id))
                    .filter(SiteVisit.created_at >= analytics_cutoff)
                    .group_by(SiteVisit.path)
                    .order_by(func.count(SiteVisit.id).desc())
                    .limit(10)
                    .all()
                )
            ]
            daily_metrics = _admin_daily_metrics(db, analytics_days)
    except Exception as e:
        logger.warning(f"Admin dashboard query failed: {e}")

    return render_template(
        'admin.html',
        stats=stats,
        recent_jobs=recent_jobs,
        recent_logins=recent_logins,
        recent_users=recent_users,
        top_paths=top_paths,
        daily_metrics=daily_metrics,
        analytics_days=analytics_days,
        analytics_retention_days=FIRST_PARTY_ANALYTICS_RETENTION_DAYS,
    )


@pages_bp.route('/admin/export/users.csv')
def admin_export_users():
    if not _require_admin():
        return "Not found", 404

    with get_db_session() as db:
        users = db.query(User).order_by(User.created_at.desc()).limit(5000).all()
        rows = [
            [
                user.id,
                user.email,
                user.role or 'user',
                user.plan_id or 'free',
                user.plan_status or 'free',
                bool(user.is_active),
                user.created_at.isoformat() if user.created_at else '',
                user.last_login_at.isoformat() if user.last_login_at else '',
            ]
            for user in users
        ]
    return _admin_csv_response(
        'users.csv',
        ['id', 'email', 'role', 'plan_id', 'plan_status', 'is_active', 'created_at', 'last_login_at'],
        rows,
    )


@pages_bp.route('/admin/export/login-events.csv')
def admin_export_login_events():
    if not _require_admin():
        return "Not found", 404

    cutoff = datetime.utcnow() - timedelta(days=_admin_days_param())
    with get_db_session() as db:
        events = (
            db.query(LoginEvent)
            .filter(LoginEvent.created_at >= cutoff)
            .order_by(LoginEvent.created_at.desc())
            .limit(10000)
            .all()
        )
        rows = [
            [
                event.id,
                event.user_id or '',
                event.email or '',
                event.event_type,
                bool(event.success),
                event.ip or '',
                event.user_agent or '',
                event.created_at.isoformat() if event.created_at else '',
            ]
            for event in events
        ]
    return _admin_csv_response(
        'login-events.csv',
        ['id', 'user_id', 'email', 'event_type', 'success', 'ip', 'user_agent', 'created_at'],
        rows,
    )


@pages_bp.route('/admin/export/site-visits.csv')
def admin_export_site_visits():
    if not _require_admin():
        return "Not found", 404

    cutoff = datetime.utcnow() - timedelta(days=_admin_days_param())
    with get_db_session() as db:
        visits = (
            db.query(SiteVisit)
            .filter(SiteVisit.created_at >= cutoff)
            .order_by(SiteVisit.created_at.desc())
            .limit(10000)
            .all()
        )
        rows = [
            [
                visit.id,
                visit.visitor_id,
                visit.user_id or '',
                visit.path,
                visit.referrer or '',
                visit.ip or '',
                visit.user_agent or '',
                visit.created_at.isoformat() if visit.created_at else '',
            ]
            for visit in visits
        ]
    return _admin_csv_response(
        'site-visits.csv',
        ['id', 'visitor_id', 'user_id', 'path', 'referrer', 'ip', 'user_agent', 'created_at'],
        rows,
    )


@pages_bp.route('/admin/export/analytics-daily.csv')
def admin_export_daily_analytics():
    if not _require_admin():
        return "Not found", 404

    days = _admin_days_param()
    with get_db_session() as db:
        rows = [
            [
                item['day'],
                item['unique_visitors'],
                item['page_views'],
                item['magic_link_requests'],
                item['login_successes'],
            ]
            for item in _admin_daily_metrics(db, days)
        ]
    return _admin_csv_response(
        'analytics-daily.csv',
        ['date_utc', 'unique_visitors', 'page_views', 'magic_link_requests', 'login_successes'],
        rows,
    )


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
    base_url = _public_base_url()
    urls = [
        ('/', LASTMOD['home'], 'weekly', '1.0'),
        ('/blogs', LASTMOD['blogs'], 'weekly', '0.8'),
        ('/pricing', LASTMOD['pricing'], 'monthly', '0.9'),
        ('/privacy', LASTMOD['privacy'], 'monthly', '0.3'),
        ('/terms', LASTMOD['terms'], 'monthly', '0.3'),
    ]
    urls.extend(
        (f"/blogs/{post['slug']}", post['lastmod'], 'monthly', '0.7')
        for post in BLOG_POSTS
    )

    entries = []
    for path, lastmod, changefreq, priority in urls:
        entries.append(
            "    <url>\n"
            f"        <loc>{escape(base_url + path)}</loc>\n"
            f"        <lastmod>{lastmod}</lastmod>\n"
            f"        <changefreq>{changefreq}</changefreq>\n"
            f"        <priority>{priority}</priority>\n"
            "    </url>"
        )
    sitemap_content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>"
    )
    return Response(sitemap_content, mimetype='application/xml')


@pages_bp.route('/robots.txt')
def robots():
    base_url = _public_base_url()
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
