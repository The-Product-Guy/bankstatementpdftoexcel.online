"""Public pages, SEO, health checks, and admin routes."""
import csv
import io
import logging
import os
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import distinct, func

from db import get_db_session
from models import FeedbackSubmission, FunnelEvent, Job, LoginEvent, SiteVisit, User
from parsers.universal_parser import UniversalBankParser
from tracking import clear_tracking_logs, record_funnel_event

logger = logging.getLogger(__name__)

pages_bp = Blueprint('pages', __name__)


LASTMOD = {
    'home': '2026-06-10',
    'blogs': '2026-06-10',
    'pricing': '2026-06-10',
    'privacy': '2026-06-10',
    'terms': '2026-06-10',
}

PRIVATE_ROBOTS_PATHS = [
    '/admin',
    '/account',
    '/auth/',
    '/billing/',
    '/checkout/',
    '/convert',
    '/download/',
    '/feedback',
    '/health',
    '/processed/',
    '/socket.io/',
    '/status/',
    '/stripe/',
    '/track/',
    '/uploads/',
    '/*?*',
]

AI_CRAWLER_USER_AGENTS = [
    'GPTBot',
    'OAI-SearchBot',
    'ChatGPT-User',
    'ClaudeBot',
    'Claude-SearchBot',
    'PerplexityBot',
    'Google-Extended',
]

BLOG_POSTS = [
    {
        'slug': 'how-exact-copy-extraction-works',
        'title': 'How Exact-Copy Extraction Works — Any Bank, Typed or Scanned',
        'category': 'Deep dive',
        'description': 'Why Statement Converter reads your PDF\'s geometry instead of using bank templates, and how the Full_Text sheet guarantees nothing is silently dropped.',
        'lastmod': '2026-07-09',
        'sections': [
            {
                'heading': 'Geometry first, text second',
                'paragraphs': [
                    'Most converters work from templates: they recognise a known bank layout and map it into fixed output columns. That breaks the moment a bank redesigns its statement, and it quietly rewrites your data on the way through.',
                    'Statement Converter works from the page itself. Every word on the page is extracted with its coordinates — from the embedded text layer on digital PDFs, or from OCR coordinates on scans. Words are grouped into visual lines, the bank\'s own header row is detected, and each column\'s horizontal range is derived from where the bank actually printed it.',
                ],
            },
            {
                'heading': 'One row per printed transaction',
                'paragraphs': [
                    'Statements wrap long descriptions onto continuation lines. A naive converter turns one transaction into two or three rows, which breaks counts and sums. The parser recognises continuation lines — no date, no amount in an amount column, text landing in description-like columns — and merges them back into their parent row.',
                ],
            },
            {
                'heading': 'The no-loss invariant',
                'paragraphs': [
                    'Every workbook carries a Full_Text sheet: page number, line number, source, and text for every visual line of every page, in reading order. Account headers, footers, summary blocks — everything the table view filters out stays recoverable. The rule is simple: every extracted line appears in the workbook at least once.',
                    'That is also why the output deliberately avoids normalisation. Amounts stay as printed text (£1,234.56 stays £1,234.56, 1.234,56 stays 1.234,56), dates are never reinterpreted, and columns keep the bank\'s own names. You decide what to clean up — with the original always in view.',
                ],
            },
            {
                'heading': 'Scanned statements',
                'paragraphs': [
                    'Scans route through OCR into the same layout reconstruction. Multi-word OCR boxes are split so each token lands in its own column, and tokens snap to the column holding most of their width. Very poor scans are flagged so you can retry in high quality.',
                ],
            },
        ],
    },
    {
        'slug': 'bank-statements-to-excel-for-reconciliation',
        'title': 'Converting Bank Statements to Excel for QuickBooks or Xero Reconciliation',
        'category': 'Workflow',
        'description': 'A practical workflow for accountants and bookkeepers: turn client statement PDFs into reviewable Excel before importing into your bookkeeping tool.',
        'lastmod': '2026-07-09',
        'sections': [
            {
                'heading': 'Why exact rows beat auto-categorised output',
                'paragraphs': [
                    'For reconciliation you need the statement\'s own numbers — the bank\'s running balance, its dates, its references — not a tool\'s interpretation of them. An exact-copy workbook gives you an auditable middle step: what the bank printed, in a grid you can filter.',
                ],
            },
            {
                'heading': 'The workflow',
                'items': [
                    'Convert each client statement PDF — typed or scanned — into Excel.',
                    'Review in Excel: check opening and closing balances against the printed summary, scan for gaps in dates, and confirm row counts look right. The Full_Text sheet holds every line of the original if anything needs cross-checking.',
                    'Clean only what your import needs: most bookkeeping tools want a date, description, and amount column, which the workbook already has under the bank\'s own headings.',
                    'Save as CSV or XLSX and import through your tool\'s bank-statement import. QuickBooks and Xero both accept spreadsheet-based statement imports; map the columns once and reuse the mapping for that client.',
                ],
            },
            {
                'heading': 'Things that save time',
                'items': [
                    'Separate money-out and money-in columns (common on UK statements) can be combined with a simple formula when your tool wants one signed amount column.',
                    'European decimal-comma amounts stay as printed — convert them with Excel\'s Text to Columns or VALUE/SUBSTITUTE once you\'ve verified the rows.',
                    'Keep the original workbook unchanged and do clean-up on a copy of the sheet: the exact copy is your audit trail.',
                ],
            },
        ],
    },
    {
        'slug': 'how-to-convert-bank-statements-to-excel',
        'title': 'How to Convert Bank Statements to Excel',
        'category': 'Tutorial',
        'description': 'A practical guide to converting text-based and scanned bank statement PDFs into Excel.',
        'lastmod': '2026-06-10',
        'sections': [
            {
                'heading': 'Conversion workflow',
                'items': [
                    'Upload your bank statement PDF from the dashboard.',
                    'The extraction engine detects whether the PDF is text-based or scanned.',
                    'Text PDFs use direct table and layout extraction. Scanned PDFs use OCR and table-structure detection.',
                    "Download an Excel workbook that mirrors the statement: the bank's own column headers, one row per transaction, plus a Full_Text sheet carrying every line of the PDF.",
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
        'lastmod': '2026-06-10',
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
        'lastmod': '2026-06-10',
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
        'lastmod': '2026-06-10',
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
        'lastmod': '2026-06-10',
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


def _public_url(path: str) -> str:
    if not path.startswith('/'):
        path = f'/{path}'
    return f"{_public_base_url()}{path}"


def _sitemap_urls():
    urls = [
        ('/', LASTMOD['home']),
        ('/blogs', LASTMOD['blogs']),
        ('/pricing', LASTMOD['pricing']),
        ('/privacy', LASTMOD['privacy']),
        ('/terms', LASTMOD['terms']),
    ]
    urls.extend((f"/blogs/{post['slug']}", post['lastmod']) for post in BLOG_POSTS)
    from routes.bank_pages import BANK_PAGES
    urls.append(("/convert/", "2026-07-08"))
    urls.extend((f"/convert/{bank['slug']}", bank["lastmod"]) for bank in BANK_PAGES)
    return urls


def _robots_rules() -> str:
    rules = ["Allow: /", "Allow: /static/"]
    rules.extend(f"Disallow: {path}" for path in PRIVATE_ROBOTS_PATHS)
    return "\n".join(rules)


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


def _admin_count_funnel_events(db, cutoff: datetime, event_types) -> int:
    if isinstance(event_types, str):
        event_types = (event_types,)
    return (
        db.query(FunnelEvent)
        .filter(
            FunnelEvent.created_at >= cutoff,
            FunnelEvent.event_type.in_(tuple(event_types)),
        )
        .count()
    )


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


@pages_bp.route('/track/event', methods=['POST'])
def track_event():
    from app import get_client_ip, rate_limited

    allowed_events = {
        'home_primary_cta_click',
        'home_secondary_cta_click',
        'home_footer_cta_click',
        'nav_signin_click',
        'nav_convert_click',
        'blog_inline_cta_click',
        'blog_footer_cta_click',
        'blog_post_cta_click',
        'pricing_cta_click',
    }
    client_ip = get_client_ip()
    if rate_limited(f"rate:track:{client_ip}", 120, 3600):
        return jsonify({'status': 'error'}), 429

    payload = request.get_json(silent=True) or request.form
    event_type = (payload.get('event_type') or '').strip()
    if event_type not in allowed_events:
        return jsonify({'status': 'ignored'}), 202

    try:
        record_funnel_event(
            event_type=event_type,
            visitor_id=request.cookies.get('sf_visitor_id'),
            user_id=session.get('user_id'),
            guest_id=session.get('guest_id'),
            path=(payload.get('path') or request.referrer or request.path),
            ip=client_ip,
            user_agent=request.headers.get('User-Agent', ''),
        )
    except Exception:
        logger.warning("Unable to record funnel event", exc_info=True)
    return jsonify({'status': 'ok'}), 200


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


@pages_bp.route('/convert/')
def bank_index():
    from routes.bank_pages import BANK_PAGES
    return render_template('bank_index.html', banks=BANK_PAGES)


@pages_bp.route('/convert/<slug>')
def bank_landing(slug):
    from routes.bank_pages import BANK_BY_SLUG
    bank = BANK_BY_SLUG.get(slug)
    if not bank:
        abort(404)
    return render_template('bank_landing.html', bank=bank)


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
        if not session.get('user_email'):
            flash('Please sign in with an admin email to continue.', 'error')
            return redirect(url_for('auth.signin'))
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
        'feedback_24h': 0,
        'feedback_7d': 0,
        'range_days': 0,
        'range_users': 0,
        'range_jobs': 0,
        'range_completed': 0,
        'range_page_views': 0,
        'range_home_views': 0,
        'range_unique_visitors': 0,
        'range_signin_visits': 0,
        'range_cta_clicks': 0,
        'range_home_cta_rate': 0,
        'range_auth_attempts': 0,
        'range_magic_links': 0,
        'range_magic_link_failures': 0,
        'range_logins': 0,
        'range_login_conversion': 0,
        'range_dashboard_visits': 0,
        'range_conversion_starts': 0,
        'range_upload_start_rate': 0,
        'range_conversion_completions': 0,
        'range_download_email_submits': 0,
        'range_downloads': 0,
        'range_download_email_rate': 0,
        'range_download_rate': 0,
        'range_feedback': 0,
    }
    daily_metrics = []
    recent_jobs = []
    recent_feedback = []
    recent_funnel_events = []
    recent_logins = []
    recent_users = []
    top_paths = []
    try:
        with get_db_session() as db:
            now = datetime.utcnow()
            day_ago = now - timedelta(days=1)
            week_ago = now - timedelta(days=7)
            analytics_cutoff = now - timedelta(days=analytics_days)
            stats['range_days'] = analytics_days
            stats['users'] = db.query(User).count()
            stats['jobs'] = db.query(Job).count()
            stats['completed'] = db.query(Job).filter(Job.status.like('completed%')).count()
            stats['range_users'] = db.query(User).filter(User.created_at >= analytics_cutoff).count()
            stats['range_jobs'] = db.query(Job).filter(Job.created_at >= analytics_cutoff).count()
            stats['range_completed'] = db.query(Job).filter(
                Job.created_at >= analytics_cutoff,
                Job.status.like('completed%'),
            ).count()
            stats['feedback_24h'] = db.query(FeedbackSubmission).filter(
                FeedbackSubmission.created_at >= day_ago,
            ).count()
            stats['feedback_7d'] = db.query(FeedbackSubmission).filter(
                FeedbackSubmission.created_at >= week_ago,
            ).count()
            stats['range_feedback'] = db.query(FeedbackSubmission).filter(
                FeedbackSubmission.created_at >= analytics_cutoff,
            ).count()
            stats['page_views_24h'] = db.query(SiteVisit).filter(SiteVisit.created_at >= day_ago).count()
            stats['page_views_7d'] = db.query(SiteVisit).filter(SiteVisit.created_at >= week_ago).count()
            stats['range_page_views'] = db.query(SiteVisit).filter(
                SiteVisit.created_at >= analytics_cutoff,
            ).count()
            stats['range_home_views'] = db.query(SiteVisit).filter(
                SiteVisit.created_at >= analytics_cutoff,
                SiteVisit.path == '/',
            ).count()
            stats['range_signin_visits'] = db.query(SiteVisit).filter(
                SiteVisit.created_at >= analytics_cutoff,
                SiteVisit.path == '/signin',
            ).count()
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
            stats['range_unique_visitors'] = (
                db.query(func.count(distinct(SiteVisit.visitor_id)))
                .filter(SiteVisit.created_at >= analytics_cutoff)
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
            stats['range_logins'] = db.query(LoginEvent).filter(
                LoginEvent.event_type == 'login_success',
                LoginEvent.created_at >= analytics_cutoff,
            ).count()
            stats['magic_links_24h'] = db.query(LoginEvent).filter(
                LoginEvent.event_type == 'magic_link_requested',
                LoginEvent.created_at >= day_ago,
            ).count()
            stats['magic_links_7d'] = db.query(LoginEvent).filter(
                LoginEvent.event_type == 'magic_link_requested',
                LoginEvent.created_at >= week_ago,
            ).count()
            stats['range_magic_links'] = db.query(LoginEvent).filter(
                LoginEvent.event_type == 'magic_link_requested',
                LoginEvent.created_at >= analytics_cutoff,
            ).count()
            stats['range_cta_clicks'] = _admin_count_funnel_events(
                db,
                analytics_cutoff,
                (
                    'home_primary_cta_click',
                    'home_secondary_cta_click',
                    'home_footer_cta_click',
                    'nav_signin_click',
                    'nav_convert_click',
                    'blog_inline_cta_click',
                    'blog_footer_cta_click',
                    'blog_post_cta_click',
                    'pricing_cta_click',
                ),
            )
            stats['range_auth_attempts'] = _admin_count_funnel_events(db, analytics_cutoff, 'auth_submit_attempt')
            stats['range_magic_link_failures'] = _admin_count_funnel_events(db, analytics_cutoff, 'magic_link_failed')
            stats['range_dashboard_visits'] = _admin_count_funnel_events(db, analytics_cutoff, 'dashboard_visit')
            stats['range_conversion_starts'] = _admin_count_funnel_events(db, analytics_cutoff, 'conversion_start')
            stats['range_conversion_completions'] = _admin_count_funnel_events(db, analytics_cutoff, 'conversion_completed')
            stats['range_download_email_submits'] = _admin_count_funnel_events(db, analytics_cutoff, 'download_email_submitted')
            stats['range_downloads'] = _admin_count_funnel_events(db, analytics_cutoff, 'download_started')
            if stats['range_home_views']:
                stats['range_home_cta_rate'] = round(stats['range_cta_clicks'] / stats['range_home_views'] * 100, 1)
            if stats['range_dashboard_visits']:
                stats['range_upload_start_rate'] = round(
                    stats['range_conversion_starts'] / stats['range_dashboard_visits'] * 100,
                    1,
                )
            if stats['range_conversion_completions']:
                stats['range_download_email_rate'] = round(
                    stats['range_download_email_submits'] / stats['range_conversion_completions'] * 100,
                    1,
                )
                stats['range_download_rate'] = round(
                    stats['range_downloads'] / stats['range_conversion_completions'] * 100,
                    1,
                )
            if stats['magic_links_7d']:
                stats['login_conversion_7d'] = round(
                    stats['logins_7d'] / stats['magic_links_7d'] * 100,
                    1,
                )
            if stats['range_magic_links']:
                stats['range_login_conversion'] = round(
                    stats['range_logins'] / stats['range_magic_links'] * 100,
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
            recent_feedback_rows = (
                db.query(FeedbackSubmission)
                .order_by(FeedbackSubmission.created_at.desc())
                .limit(20)
                .all()
            )
            feedback_user_ids = [row.user_id for row in recent_feedback_rows if row.user_id]
            feedback_job_ids = [row.job_id for row in recent_feedback_rows if row.job_id]
            feedback_user_email_by_id = {
                user.id: user.email
                for user in db.query(User).filter(User.id.in_(feedback_user_ids)).all()
            } if feedback_user_ids else {}
            feedback_job_filename_by_id = {
                job.id: job.filename
                for job in db.query(Job).filter(Job.id.in_(feedback_job_ids)).all()
            } if feedback_job_ids else {}
            recent_feedback = [
                {
                    'feedback_type': row.feedback_type,
                    'message': row.message or '',
                    'pdf_shared': row.pdf_shared,
                    'quality_used': row.quality_used or '-',
                    'extraction_rows': row.extraction_rows,
                    'extraction_cols': row.extraction_cols,
                    'status': row.status or 'new',
                    'created_at': row.created_at,
                    'user_email': feedback_user_email_by_id.get(row.user_id, '-'),
                    'filename': feedback_job_filename_by_id.get(row.job_id, '-'),
                    'job_id': row.job_id or '',
                }
                for row in recent_feedback_rows
            ]
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
            recent_funnel_rows = (
                db.query(FunnelEvent)
                .order_by(FunnelEvent.created_at.desc())
                .limit(20)
                .all()
            )
            recent_funnel_events = [
                {
                    'event_type': event.event_type,
                    'email': event.email or '-',
                    'path': event.path or '-',
                    'job_id': event.job_id or '',
                    'created_at': event.created_at,
                    'ip': event.ip or '-',
                }
                for event in recent_funnel_rows
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
        recent_feedback=recent_feedback,
        recent_funnel_events=recent_funnel_events,
        recent_logins=recent_logins,
        recent_users=recent_users,
        top_paths=top_paths,
        daily_metrics=daily_metrics,
        analytics_days=analytics_days,
        analytics_retention_days=FIRST_PARTY_ANALYTICS_RETENTION_DAYS,
    )


@pages_bp.route('/admin/analytics/reset', methods=['POST'])
def admin_reset_analytics():
    if not _require_admin():
        return "Not found", 404

    csrf_token = request.form.get('csrf_token')
    if not csrf_token or csrf_token != session.get('csrf_token'):
        flash('Invalid request token. Please refresh and try again.', 'error')
        return redirect(url_for('pages.admin_dashboard'))

    if request.form.get('confirm') != 'RESET_TRAFFIC':
        flash('Analytics reset was not confirmed.', 'error')
        return redirect(url_for('pages.admin_dashboard'))

    deleted = clear_tracking_logs()
    flash(
        (
            "Traffic analytics cleared: "
            f"{deleted.get('site_visits', 0)} visits, "
            f"{deleted.get('funnel_events', 0)} funnel events, "
            f"{deleted.get('login_events', 0)} login events."
        ),
        'success',
    )
    return redirect(url_for('pages.admin_dashboard'))


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


@pages_bp.route('/admin/export/funnel-events.csv')
def admin_export_funnel_events():
    if not _require_admin():
        return "Not found", 404

    cutoff = datetime.utcnow() - timedelta(days=_admin_days_param())
    with get_db_session() as db:
        events = (
            db.query(FunnelEvent)
            .filter(FunnelEvent.created_at >= cutoff)
            .order_by(FunnelEvent.created_at.desc())
            .limit(10000)
            .all()
        )
        rows = [
            [
                event.id,
                event.visitor_id or '',
                event.user_id or '',
                event.guest_id or '',
                event.job_id or '',
                event.email or '',
                event.event_type,
                event.path or '',
                event.extra or '',
                event.ip or '',
                event.user_agent or '',
                event.created_at.isoformat() if event.created_at else '',
            ]
            for event in events
        ]
    return _admin_csv_response(
        'funnel-events.csv',
        [
            'id', 'visitor_id', 'user_id', 'guest_id', 'job_id', 'email',
            'event_type', 'path', 'extra', 'ip', 'user_agent', 'created_at',
        ],
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


@pages_bp.route('/admin/export/feedback.csv')
def admin_export_feedback():
    if not _require_admin():
        return "Not found", 404

    cutoff = datetime.utcnow() - timedelta(days=_admin_days_param())
    with get_db_session() as db:
        submissions = (
            db.query(FeedbackSubmission)
            .filter(FeedbackSubmission.created_at >= cutoff)
            .order_by(FeedbackSubmission.created_at.desc())
            .limit(10000)
            .all()
        )
        user_ids = [submission.user_id for submission in submissions if submission.user_id]
        job_ids = [submission.job_id for submission in submissions if submission.job_id]
        user_email_by_id = {
            user.id: user.email
            for user in db.query(User).filter(User.id.in_(user_ids)).all()
        } if user_ids else {}
        job_filename_by_id = {
            job.id: job.filename
            for job in db.query(Job).filter(Job.id.in_(job_ids)).all()
        } if job_ids else {}
        rows = [
            [
                submission.id,
                submission.job_id or '',
                job_filename_by_id.get(submission.job_id, ''),
                user_email_by_id.get(submission.user_id, ''),
                submission.guest_id or '',
                submission.feedback_type,
                submission.message or '',
                bool(submission.pdf_shared),
                submission.pdf_storage_key or '',
                submission.extraction_rows if submission.extraction_rows is not None else '',
                submission.extraction_cols if submission.extraction_cols is not None else '',
                submission.quality_used or '',
                submission.status or '',
                submission.ip or '',
                submission.user_agent or '',
                submission.created_at.isoformat() if submission.created_at else '',
            ]
            for submission in submissions
        ]
    return _admin_csv_response(
        'feedback.csv',
        [
            'id', 'job_id', 'filename', 'email', 'guest_id', 'feedback_type',
            'message', 'pdf_shared', 'pdf_storage_key', 'extraction_rows',
            'extraction_cols', 'quality_used', 'status', 'ip', 'user_agent',
            'created_at',
        ],
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
    entries = []
    for path, lastmod in _sitemap_urls():
        entries.append(
            "    <url>\n"
            f"        <loc>{escape(_public_url(path))}</loc>\n"
            f"        <lastmod>{lastmod}</lastmod>\n"
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
    groups = [f"User-agent: *\n{_robots_rules()}"]
    groups.extend(f"User-agent: {agent}\n{_robots_rules()}" for agent in AI_CRAWLER_USER_AGENTS)
    robots_content = (
        "\n\n".join(groups)
        + f"\n\nSitemap: {base_url}/sitemap.xml\n"
        + f"Sitemap: {base_url}/sitemap.txt"
    )
    return Response(robots_content, mimetype='text/plain')


@pages_bp.route('/sitemap.txt')
def sitemap_txt():
    content = "\n".join(_public_url(path) for path, _lastmod in _sitemap_urls())
    return Response(f"{content}\n", mimetype='text/plain')


@pages_bp.route('/llms.txt')
def llms_txt():
    base_url = _public_base_url()
    blog_links = "\n".join(
        f"- [{post['title']}]({_public_url('/blogs/' + post['slug'])}): {post['description']}"
        for post in BLOG_POSTS
    )
    content = f"""# Statement Converter

> Statement Converter is a web application from Ambion Softwares that converts bank statement PDF files into Excel workbooks for review, reconciliation, and analysis.

## Product
- Primary use case: convert text-based and scanned bank statement PDFs into Excel workbooks.
- Audience: accountants, auditors, founders, small businesses, and finance teams.
- Supported input: PDF bank statements with tabular transaction layouts.
- Output: XLSX workbook with reviewable extracted rows and source context.
- Data handling: uploaded PDFs and generated workbooks are temporary; feedback copies are retained only when a user opts in.

## Canonical Pages
- [Home]({base_url}/)
- [Pricing]({base_url}/pricing)
- [Blog and guides]({base_url}/blogs)
- [Privacy Policy]({base_url}/privacy)
- [Terms of Service]({base_url}/terms)

## Key Guides
{blog_links}

## Common Questions
- What does Statement Converter do? It converts bank statement PDFs into Excel workbooks.
- Does it support scanned PDFs? Yes, scanned statements use OCR and table-structure detection.
- Is every conversion guaranteed? No. Users should review dates, amounts, balances, and ambiguous rows before accounting or audit use.
- Are files stored permanently? No. Files are processed temporarily and expire after the configured retention windows unless feedback sharing is explicitly selected.

## Machine-Readable Discovery
- XML sitemap: {base_url}/sitemap.xml
- Plain text sitemap: {base_url}/sitemap.txt
- Robots policy: {base_url}/robots.txt
"""
    return Response(content, mimetype='text/plain')


@pages_bp.route('/humans.txt')
def humans_txt():
    content = f"""/* TEAM */
Company: Ambion Softwares
Product: Statement Converter
Site: {_public_base_url()}

/* SITE */
Purpose: Bank statement PDF to Excel conversion
Language: English
Standards: HTML5, CSS, JavaScript, Flask
"""
    return Response(content, mimetype='text/plain')


@pages_bp.route('/.well-known/security.txt')
@pages_bp.route('/security.txt')
def security_txt():
    contact = os.environ.get('SECURITY_CONTACT') or f"{_public_base_url()}/privacy"
    expires = (datetime.utcnow() + timedelta(days=365)).strftime('%Y-%m-%dT%H:%M:%SZ')
    content = f"""Contact: {contact}
Policy: {_public_base_url()}/privacy
Preferred-Languages: en
Canonical: {_public_base_url()}/.well-known/security.txt
Expires: {expires}
"""
    return Response(content, mimetype='text/plain')


@pages_bp.route('/indexnow-key.txt')
def indexnow_key():
    key = (os.environ.get('INDEXNOW_KEY') or '').strip()
    if not key:
        return "Not found", 404
    return Response(f"{key}\n", mimetype='text/plain')
