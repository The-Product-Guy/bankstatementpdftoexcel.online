"""Stripe billing routes: checkout, webhook, billing portal."""
import logging
import os

import stripe
from flask import Blueprint, flash, jsonify, redirect, request, session, url_for

from db import get_db_session
from models import User

logger = logging.getLogger(__name__)

billing_bp = Blueprint('billing', __name__)


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
          <td style="background:linear-gradient(135deg,#000a63,#046bca);padding:28px 40px;text-align:center;">
            <h1 style="margin:0;color:#ffffff;font-size:20px;font-weight:600;">Statement Converter</h1>
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
            <p style="margin:0;color:#999;font-size:12px;">Statement Converter by Ambion Softwares</p>
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


# -- Webhook handlers --

def _handle_checkout_completed(session_obj):
    """checkout.session.completed -- activate subscription."""
    from app import PLAN_CONFIG
    user_id = (session_obj.get('metadata') or {}).get('user_id')
    plan_id = (session_obj.get('metadata') or {}).get('plan_id')
    subscription_id = session_obj.get('subscription')
    customer_id = session_obj.get('customer')

    if not user_id:
        if customer_id:
            with get_db_session() as db:
                user = db.query(User).filter_by(stripe_customer_id=customer_id).first()
                if user:
                    user_id = user.id

    if not user_id:
        logger.warning("checkout.session.completed: no user_id found")
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


def _handle_subscription_updated(subscription):
    """customer.subscription.updated -- sync plan status."""
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
    """customer.subscription.deleted -- reset to free plan."""
    customer_id = subscription.get('customer')

    with get_db_session() as db:
        user = db.query(User).filter_by(stripe_customer_id=customer_id).first()
        if user:
            user.plan_id = 'free'
            user.plan_status = 'free'
            user.stripe_subscription_id = None
            _send_plan_change_email(user.email, 'free', 'canceled')


def _handle_payment_failed(invoice):
    """invoice.payment_failed -- mark as past_due (grace period)."""
    customer_id = invoice.get('customer')

    with get_db_session() as db:
        user = db.query(User).filter_by(stripe_customer_id=customer_id).first()
        if user and user.plan_status == 'active':
            user.plan_status = 'past_due'


# -- Routes --

@billing_bp.route('/checkout/create', methods=['POST'])
def checkout_create():
    """Create a Stripe Checkout Session and return the redirect URL."""
    from app import PLAN_CONFIG

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

            if not user.stripe_customer_id:
                customer = stripe.Customer.create(email=user.email)
                user.stripe_customer_id = customer.id
                db.flush()

            customer_id = user.stripe_customer_id

        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            mode='subscription',
            line_items=[{'price': plan['stripe_price_id'], 'quantity': 1}],
            success_url=url_for('billing.checkout_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('pages.pricing', _external=True),
            metadata={'user_id': user_id, 'plan_id': plan_id},
        )
        return jsonify({'checkout_url': checkout_session.url})

    except Exception as e:
        logger.warning(f"Checkout create failed: {e}")
        return jsonify({'error': 'Unable to create checkout session.'}), 500


@billing_bp.route('/checkout/success')
def checkout_success():
    """Post-checkout landing page. Subscription is activated by webhook, not here."""
    flash('Your subscription is being activated. This usually takes a few seconds.', 'success')
    return redirect(url_for('pages.home'))


@billing_bp.route('/stripe/webhook', methods=['POST'])
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


@billing_bp.route('/billing/portal', methods=['POST'])
def billing_portal():
    """Create a Stripe Customer Portal session and redirect."""
    user_id = session.get('user_id')
    if not user_id:
        flash('Please sign in first.', 'error')
        return redirect(url_for('auth.signin'))

    csrf_token = request.form.get('csrf_token')
    if not csrf_token or csrf_token != session.get('csrf_token'):
        flash('Invalid request token. Please refresh and try again.', 'error')
        return redirect(url_for('pages.pricing'))

    try:
        with get_db_session() as db:
            user = db.query(User).filter_by(id=user_id).first()
            if not user or not user.stripe_customer_id:
                flash('No active subscription found.', 'error')
                return redirect(url_for('pages.pricing'))
            customer_id = user.stripe_customer_id

        portal_session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=url_for('pages.pricing', _external=True),
        )
        return redirect(portal_session.url)

    except Exception as e:
        logger.warning(f"Billing portal failed: {e}")
        flash('Unable to open billing portal.', 'error')
        return redirect(url_for('pages.pricing'))
