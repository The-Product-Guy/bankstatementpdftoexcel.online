"""Auth routes: signin, magic link, verify, signout, account."""
import hashlib
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from db import get_db_session
from models import AuthToken, Job, LoginEvent, User, UsageCounter

auth_bp = Blueprint('auth', __name__)


def _record_auth_funnel_event(event_type: str, email: str = "", extra: str = "") -> None:
    try:
        from app import get_client_ip
        from tracking import record_funnel_event

        record_funnel_event(
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
    from app import ADMIN_EMAILS, MAGIC_LINK_EXP_MINUTES, get_client_ip, is_ajax_request, rate_limited

    email = (request.form.get('email') or '').strip().lower()
    _record_auth_funnel_event('auth_submit_attempt', email=email)
    csrf_token = request.form.get('csrf_token')
    if not csrf_token or csrf_token != session.get('csrf_token'):
        _record_auth_funnel_event('auth_submit_invalid_csrf', email=email)
        message = 'Invalid request token. Please refresh and try again.'
        if is_ajax_request():
            return jsonify({'status': 'error', 'error': message}), 400
        flash(message, 'error')
        return redirect(url_for('auth.signin'))

    if not email or '@' not in email:
        _record_auth_funnel_event('auth_submit_invalid_email', email=email)
        message = 'Please enter a valid email address.'
        if is_ajax_request():
            return jsonify({'status': 'error', 'error': message}), 400
        flash(message, 'error')
        return redirect(url_for('auth.signin'))

    if rate_limited(f"rate:auth_start:{get_client_ip()}:{email}", 5, 900):
        _record_auth_funnel_event('auth_submit_rate_limited', email=email)
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
                ip=get_client_ip(),
                user_agent=request.headers.get('User-Agent', '')[:512]
            )
            db.add(auth_token)
            db.add(LoginEvent(
                user_id=user.id,
                email=user.email,
                event_type='magic_link_requested',
                success=True,
                ip=get_client_ip(),
                user_agent=request.headers.get('User-Agent', '')[:512],
            ))

        link = url_for('auth.auth_verify', token=token, _external=True)
        send_magic_link_email(email, link)
        _record_auth_funnel_event('magic_link_sent', email=email)

        message = 'Check your email for your sign-in link.'
        if is_ajax_request():
            return jsonify({'status': 'ok', 'message': message}), 200
        flash(message, 'success')
        return redirect(url_for('auth.signin'))
    except Exception as e:
        _record_auth_funnel_event('magic_link_failed', email=email, extra=str(e)[:500])
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
        with get_db_session() as db:
            auth_token = db.query(AuthToken).filter_by(token_hash=token_hash).first()
            if not auth_token or auth_token.used_at is not None or auth_token.expires_at < datetime.utcnow():
                _record_auth_funnel_event('login_failed', extra='invalid_or_expired_token')
                flash('Invalid or expired sign-in link.', 'error')
                return redirect(url_for('auth.signin'))

            user = db.query(User).filter_by(id=auth_token.user_id).first()
            if not user or not user.is_active:
                _record_auth_funnel_event('login_failed', extra='inactive_user')
                flash('Account not available.', 'error')
                return redirect(url_for('auth.signin'))

            auth_token.used_at = datetime.utcnow()
            user.last_login_at = datetime.utcnow()
            db.add(LoginEvent(
                user_id=user.id,
                email=user.email,
                event_type='login_success',
                success=True,
                ip=get_client_ip(),
                user_agent=request.headers.get('User-Agent', '')[:512],
            ))

            session['user_id'] = user.id
            session['user_email'] = user.email
            session['role'] = user.role or 'user'
            session['plan_status'] = user.plan_status
            session['plan_id'] = user.plan_id

        _record_auth_funnel_event('login_success', email=session.get('user_email', ''))
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
