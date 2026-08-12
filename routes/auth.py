"""Auth routes: signin, magic link, verify, signout, account."""
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from db import get_db_session
from models import AuthToken, Job, LoginEvent, User, UsageCounter
from site_urls import public_base_url

auth_bp = Blueprint('auth', __name__)
logger = logging.getLogger(__name__)


def _record_auth_funnel_event(event_type: str, email: str = "", extra: str = "") -> None:
    try:
        from app import get_client_ip
        from tracking import enqueue_funnel_event

        enqueue_funnel_event(
            event_type=event_type,
            visitor_id=request.cookies.get('sf_visitor_id'),
            user_id=session.get('user_id'),
            guest_id=session.get('guest_id'),
            email=email,
            path=request.path,
            extra=extra,
            ip=get_client_ip(),
            user_agent=request.headers.get('User-Agent', ''),
        )
    except Exception:
        pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _positive_int_env(name: str, default: int) -> int:
    """Read a positive integer setting without letting a bad deploy value disable auth."""
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def send_magic_link_email(email: str, link: str) -> None:
    import os
    import resend

    api_key = os.environ.get('RESEND_API_KEY')
    if not api_key:
        raise RuntimeError('RESEND_API_KEY is not configured')

    resend.api_key = api_key
    sender = os.environ.get('RESEND_FROM_EMAIL', 'onboarding@resend.dev')
    from app import MAGIC_LINK_EXP_MINUTES
    expiry_minutes = MAGIC_LINK_EXP_MINUTES

    resend.Emails.send({
        "from": sender,
        "to": [email],
        "subject": "Your sign-in link for Statement Converter",
        "text": (
            f"Sign in to Statement Converter\n\n"
            f"Click the link below to sign in:\n{link}\n\n"
            f"This link expires in {expiry_minutes} minutes.\n"
            f"If you didn't request this, you can safely ignore this email.\n\n"
            f"- Statement Converter"
        ),
        "html": f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f7;font-family:'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f7;padding:40px 0;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.08);">
        <tr>
          <td style="background:linear-gradient(135deg,#000a63,#046bca);padding:32px 40px;text-align:center;">
            <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:600;letter-spacing:-0.3px;">Statement Converter</h1>
          </td>
        </tr>
        <tr>
          <td style="padding:36px 40px 20px;">
            <p style="margin:0 0 20px;color:#333;font-size:16px;line-height:1.5;">
              Click the button below to sign in to your account:
            </p>
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr><td align="center" style="padding:8px 0 28px;">
                <a href="{link}" style="display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#000a63,#046bca);color:#ffffff;text-decoration:none;border-radius:8px;font-size:16px;font-weight:600;letter-spacing:0.3px;">
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
            <p style="margin:0 0 20px;word-break:break-all;color:#046bca;font-size:12px;">{link}</p>
          </td>
        </tr>
        <tr>
          <td style="padding:20px 40px 28px;border-top:1px solid #eee;">
            <p style="margin:0;color:#999;font-size:12px;line-height:1.6;">
              If you didn't request this email, you can safely ignore it.
              <br>Statement Converter by Ambion Softwares
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


@auth_bp.route('/signin')
def signin():
    _record_auth_funnel_event('signin_page_view')
    return render_template('signin.html')


@auth_bp.route('/auth/start', methods=['POST'])
def auth_start():
    from app import (
        ADMIN_EMAILS,
        MAGIC_LINK_EXP_MINUTES,
        get_client_ip,
        hmac_rate_limit_subject,
        is_ajax_request,
        observe_distinct_rate_limit_subject,
        rate_limited,
    )

    email = (request.form.get('email') or '').strip().lower()
    csrf_token = request.form.get('csrf_token')
    if not csrf_token or csrf_token != session.get('csrf_token'):
        _record_auth_funnel_event('auth_submit_invalid_csrf')
        message = 'Invalid request token. Please refresh and try again.'
        if is_ajax_request():
            return jsonify({'status': 'error', 'error': message}), 400
        flash(message, 'error')
        return redirect(url_for('auth.signin'))

    if not email or '@' not in email:
        _record_auth_funnel_event('auth_submit_invalid_email')
        message = 'Please enter a valid email address.'
        if is_ajax_request():
            return jsonify({'status': 'error', 'error': message}), 400
        flash(message, 'error')
        return redirect(url_for('auth.signin'))

    # Preserve the aggregate auth-attempt metric only after validation, and do
    # not attach the submitted email address to funnel telemetry.
    _record_auth_funnel_event('auth_submit_attempt')

    client_ip = get_client_ip()
    ip_digest = hmac_rate_limit_subject(client_ip, 'auth-ip')
    email_digest = hmac_rate_limit_subject(email, 'auth-email')
    window_seconds = _positive_int_env('RATE_LIMIT_AUTH_WINDOW_SECONDS', 15 * 60)
    ip_limit = _positive_int_env('RATE_LIMIT_AUTH_IP', 10)
    email_limit = _positive_int_env('RATE_LIMIT_AUTH_EMAIL', 5)
    distinct_email_threshold = _positive_int_env(
        'AUTH_DISTINCT_EMAIL_ALERT_THRESHOLD', 5,
    )
    distinct_email_window_seconds = _positive_int_env(
        'AUTH_DISTINCT_EMAIL_WINDOW_SECONDS', 24 * 60 * 60,
    )

    # Always evaluate both independent limits. Otherwise an attacker can use
    # one exhausted limit to probe whether the other is still available.
    ip_limited = rate_limited(
        f"rate:auth_start:ip:{ip_digest}", ip_limit, window_seconds,
    )
    email_limited = rate_limited(
        f"rate:auth_start:email:{email_digest}", email_limit, window_seconds,
    )

    # This is strictly observability, never another sign-in gate. The helper
    # atomically reports only the threshold-crossing request for each IP/day.
    try:
        observed = observe_distinct_rate_limit_subject(
            f"observe:auth:ip-emails:{ip_digest}",
            email_digest,
            threshold=distinct_email_threshold,
            window_seconds=distinct_email_window_seconds,
        )
        if observed is not None:
            distinct_count, crossed_threshold = observed
            if crossed_threshold:
                logger.warning(
                    'event=auth_ip_email_spray ip_fingerprint=%s '
                    'distinct_email_count=%s window_seconds=%s',
                    ip_digest[:12], distinct_count, distinct_email_window_seconds,
                )
    except Exception:
        # Auth availability must not depend on optional abuse telemetry.
        logger.warning('Unable to observe distinct magic-link subjects', exc_info=True)

    if ip_limited or email_limited:
        _record_auth_funnel_event('auth_submit_rate_limited')
        message = 'Too many sign-in attempts. Please try again later.'
        if is_ajax_request():
            return jsonify({'status': 'error', 'error': message}), 429
        flash(message, 'error')
        return redirect(url_for('auth.signin'))

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
                ip=client_ip,
                user_agent=request.headers.get('User-Agent', '')[:512]
            )
            db.add(auth_token)
            db.add(LoginEvent(
                user_id=user.id,
                email=user.email,
                event_type='magic_link_requested',
                success=True,
                ip=client_ip,
                user_agent=request.headers.get('User-Agent', '')[:512],
            ))

        link = f"{public_base_url()}{url_for('auth.auth_verify', token=token)}"
        send_magic_link_email(email, link)
        _record_auth_funnel_event('magic_link_sent')

        message = 'Check your email for your sign-in link.'
        if is_ajax_request():
            return jsonify({'status': 'ok', 'message': message}), 200
        flash(message, 'success')
        return redirect(url_for('auth.signin'))
    except Exception as e:
        _record_auth_funnel_event('magic_link_failed')
        message = f'Unable to send magic link: {str(e)}'
        if is_ajax_request():
            return jsonify({'status': 'error', 'error': message}), 500
        flash(message, 'error')
        return redirect(url_for('auth.signin'))


@auth_bp.route('/auth/verify')
def auth_verify():
    from app import get_client_ip

    token = request.args.get('token', '')
    if not token:
        _record_auth_funnel_event('login_failed', extra='missing_token')
        flash('Invalid or expired sign-in link.', 'error')
        return redirect(url_for('auth.signin'))

    try:
        token_hash = hash_token(token)
        verified_user = None
        with get_db_session() as db:
            auth_token = db.query(AuthToken).filter_by(token_hash=token_hash).first()
            claimed_at = datetime.utcnow()
            if not auth_token or auth_token.used_at is not None or auth_token.expires_at < claimed_at:
                _record_auth_funnel_event('login_failed', extra='invalid_or_expired_token')
                flash('Invalid or expired sign-in link.', 'error')
                return redirect(url_for('auth.signin'))

            user = db.query(User).filter_by(id=auth_token.user_id).first()
            if not user or not user.is_active:
                _record_auth_funnel_event('login_failed', extra='inactive_user')
                flash('Account not available.', 'error')
                return redirect(url_for('auth.signin'))

            # Atomically claim the one-use token. Concurrent requests can both
            # read it, but only one can update the still-unused row.
            claimed = (
                db.query(AuthToken)
                .filter(
                    AuthToken.id == auth_token.id,
                    AuthToken.used_at.is_(None),
                    AuthToken.expires_at >= claimed_at,
                )
                .update({AuthToken.used_at: claimed_at}, synchronize_session=False)
            )
            if claimed != 1:
                _record_auth_funnel_event('login_failed', extra='token_already_claimed')
                flash('Invalid or expired sign-in link.', 'error')
                return redirect(url_for('auth.signin'))

            user.last_login_at = claimed_at
            db.add(LoginEvent(
                user_id=user.id,
                email=user.email,
                event_type='login_success',
                success=True,
                ip=get_client_ip(),
                user_agent=request.headers.get('User-Agent', '')[:512],
            ))

            # Copy scalar values before the database session closes. Flask auth
            # state is established only after the token claim commits.
            verified_user = {
                'id': user.id,
                'email': user.email,
                'role': user.role or 'user',
                'plan_status': user.plan_status,
                'plan_id': user.plan_id,
            }

        # Rotate authentication state so a newly verified account cannot
        # inherit another account's captured-download or plan session data.
        guest_id = session.get('guest_id')
        session.clear()
        if guest_id:
            session['guest_id'] = guest_id
        session['user_id'] = verified_user['id']
        session['user_email'] = verified_user['email']
        session['role'] = verified_user['role']
        session['plan_status'] = verified_user['plan_status']
        session['plan_id'] = verified_user['plan_id']

        _record_auth_funnel_event('login_success')
        flash('Signed in successfully.', 'success')
        return redirect(url_for('converter.dashboard'))
    except Exception:
        _record_auth_funnel_event('login_failed', extra='exception')
        flash('Unable to verify sign-in link. Please try again.', 'error')
        return redirect(url_for('auth.signin'))


@auth_bp.route('/account')
def account():
    """Account dashboard -- plan, usage, and recent conversions."""
    import os
    from app import PLAN_CONFIG, get_usage_counter, has_unlimited_quota_email, sync_session_plan

    user_id = session.get('user_id')
    if not user_id:
        flash('Please sign in to view your account.', 'error')
        return redirect(url_for('auth.signin'))

    sync_session_plan()
    plan_id = session.get('plan_id', 'free')
    plan_status = session.get('plan_status', 'free')
    plan = PLAN_CONFIG.get(plan_id, PLAN_CONFIG['free'])
    monthly_limit = plan['monthly_conversions']
    if has_unlimited_quota_email(session.get('user_email', '')):
        monthly_limit = None

    monthly_scope = f"monthly:{datetime.utcnow().strftime('%Y-%m')}"
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used_this_month = 0
    recent_jobs = []

    try:
        with get_db_session() as db:
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
            used_this_month = max(counter_count, completed_job_count)
            recent_jobs = (
                db.query(Job)
                .filter_by(user_id=user_id)
                .order_by(Job.created_at.desc())
                .limit(10)
                .all()
            )
            for job in recent_jobs:
                db.expunge(job)
    except Exception:
        flash('Unable to load account data.', 'error')

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


@auth_bp.route('/signout')
def signout():
    guest_id = session.get('guest_id')
    user_id = session.get('user_id')
    user_email = session.get('user_email')
    if user_id:
        try:
            from app import get_client_ip
            with get_db_session() as db:
                db.add(LoginEvent(
                    user_id=user_id,
                    email=user_email,
                    event_type='signout',
                    success=True,
                    ip=get_client_ip(),
                    user_agent=request.headers.get('User-Agent', '')[:512],
                ))
        except Exception:
            pass
    session.clear()
    if guest_id:
        session['guest_id'] = guest_id
    flash('Signed out successfully.', 'success')
    return redirect(url_for('pages.home'))
