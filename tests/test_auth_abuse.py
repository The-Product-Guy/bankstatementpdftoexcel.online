"""Focused tests for magic-link abuse protections."""
import hashlib
import logging
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest


os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('DISABLE_QUOTAS', 'true')


@pytest.fixture
def client():
    from app import app

    app.config['TESTING'] = True
    with app.test_client() as test_client:
        yield test_client


def _post_auth_start(client, email='User@Example.COM'):
    with client.session_transaction() as sess:
        sess['csrf_token'] = 'csrf-token'
    return client.post(
        '/auth/start',
        data={'email': email, 'csrf_token': 'csrf-token'},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )


def _install_auth_helpers(monkeypatch, *, rate_limited, observer, client_ip='198.51.100.99'):
    import app as app_module

    subjects = []
    observed = []
    funnel_events = []
    next_user_id = 1

    class FakeQuery:
        def filter_by(self, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDb:
        def query(self, _model):
            return FakeQuery()

        def add(self, model):
            nonlocal next_user_id
            if model.__class__.__name__ == 'User' and model.id is None:
                model.id = next_user_id
                next_user_id += 1

        def flush(self):
            return None

    @contextmanager
    def fake_db_session():
        yield FakeDb()

    def digest(value, namespace):
        subjects.append((value, namespace))
        return hashlib.sha256(f'{namespace}:{value}'.encode('utf-8')).hexdigest()

    def observe(scope_digest, member_digest, threshold, window_seconds):
        observed.append((scope_digest, member_digest, threshold, window_seconds))
        return observer(scope_digest, member_digest, threshold, window_seconds)

    monkeypatch.setattr(
        app_module,
        'get_client_ip',
        lambda: client_ip() if callable(client_ip) else client_ip,
    )
    monkeypatch.setattr(app_module, 'hmac_rate_limit_subject', digest, raising=False)
    monkeypatch.setattr(
        app_module,
        'observe_distinct_rate_limit_subject',
        observe,
        raising=False,
    )
    monkeypatch.setattr(app_module, 'rate_limited', rate_limited)
    monkeypatch.setattr('routes.auth.get_db_session', fake_db_session)
    monkeypatch.setattr(
        'routes.auth._record_auth_funnel_event',
        lambda *args, **kwargs: funnel_events.append((args, kwargs)),
    )
    monkeypatch.setattr('routes.auth.send_magic_link_email', lambda *_args: None)
    return subjects, observed, funnel_events


def test_magic_link_limits_are_independent_normalized_and_hmaced(client, monkeypatch):
    rate_calls = []

    def rate_limited(key, limit, window_seconds):
        rate_calls.append((key, limit, window_seconds))
        return key.startswith('rate:auth_start:ip:')

    subjects, observed, funnel_events = _install_auth_helpers(
        monkeypatch,
        rate_limited=rate_limited,
        observer=lambda *_args: (1, False),
    )
    monkeypatch.setenv('RATE_LIMIT_AUTH_IP', '17')
    monkeypatch.setenv('RATE_LIMIT_AUTH_EMAIL', '3')
    monkeypatch.setenv('RATE_LIMIT_AUTH_WINDOW_SECONDS', '1200')
    monkeypatch.setenv('AUTH_DISTINCT_EMAIL_ALERT_THRESHOLD', '7')
    monkeypatch.setenv('AUTH_DISTINCT_EMAIL_WINDOW_SECONDS', '3600')

    response = _post_auth_start(client, '  User@Example.COM ')

    ip_digest = hashlib.sha256(b'auth-ip:198.51.100.99').hexdigest()
    email_digest = hashlib.sha256(b'auth-email:user@example.com').hexdigest()

    assert response.status_code == 429
    assert subjects == [
        ('198.51.100.99', 'auth-ip'),
        ('user@example.com', 'auth-email'),
    ]
    assert rate_calls == [
        (f'rate:auth_start:ip:{ip_digest}', 17, 1200),
        (f'rate:auth_start:email:{email_digest}', 3, 1200),
    ]
    assert observed == [
        (f'observe:auth:ip-emails:{ip_digest}', email_digest, 7, 3600),
    ]
    # Neither original subject can make it into a Redis key, observer payload,
    # or auth funnel-event arguments.
    serialized = repr((rate_calls, observed, funnel_events))
    assert 'user@example.com' not in serialized
    assert '198.51.100.99' not in serialized
    assert funnel_events == [
        (('auth_submit_attempt',), {}),
        (('auth_submit_rate_limited',), {}),
    ]


def test_magic_link_evaluates_email_limit_when_ip_is_already_limited(client, monkeypatch):
    calls = []

    def rate_limited(key, *_args):
        calls.append(key)
        return key.startswith('rate:auth_start:ip:')

    _install_auth_helpers(
        monkeypatch,
        rate_limited=rate_limited,
        observer=lambda *_args: (1, False),
    )

    response = _post_auth_start(client)

    assert response.status_code == 429
    assert [key.split(':')[2] for key in calls] == ['ip', 'email']


def test_magic_link_distinct_email_warning_is_once_and_contains_no_pii(client, monkeypatch, caplog):
    observations = iter(((5, True), (6, False)))
    _install_auth_helpers(
        monkeypatch,
        rate_limited=lambda *_args: True,
        observer=lambda *_args: next(observations),
    )
    caplog.set_level(logging.WARNING, logger='routes.auth')

    first = _post_auth_start(client, 'first@example.com')
    second = _post_auth_start(client, 'second@example.com')

    assert first.status_code == second.status_code == 429
    warnings = [
        record.getMessage()
        for record in caplog.records
        if 'event=auth_ip_email_spray' in record.getMessage()
    ]
    assert len(warnings) == 1
    expected_fingerprint = hashlib.sha256(b'auth-ip:198.51.100.99').hexdigest()[:12]
    assert f'ip_fingerprint={expected_fingerprint}' in warnings[0]
    assert 'distinct_email_count=5' in warnings[0]
    assert 'window_seconds=86400' in warnings[0]
    assert 'first@example.com' not in warnings[0]
    assert 'second@example.com' not in warnings[0]
    assert '198.51.100.99' not in warnings[0]


@pytest.mark.parametrize(
    ('email', 'csrf_token'),
    [('not-an-email', 'csrf-token'), ('valid@example.com', 'wrong-token')],
)
def test_invalid_auth_requests_do_not_observe_email_subject(client, monkeypatch, email, csrf_token):
    import app as app_module

    monkeypatch.setattr(
        app_module,
        'observe_distinct_rate_limit_subject',
        lambda *_args: pytest.fail('invalid request must not be observed'),
        raising=False,
    )
    monkeypatch.setattr(
        'routes.auth._record_auth_funnel_event',
        lambda *_args, **_kwargs: None,
    )
    with client.session_transaction() as sess:
        sess['csrf_token'] = 'csrf-token'

    response = client.post(
        '/auth/start',
        data={'email': email, 'csrf_token': csrf_token},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )

    assert response.status_code == 400


def _in_memory_rate_limiter():
    counts = {}

    def rate_limited(key, limit, _window_seconds):
        counts[key] = counts.get(key, 0) + 1
        return counts[key] > limit

    return rate_limited


def test_one_ip_hits_the_ip_magic_link_ceiling_across_different_emails(client, monkeypatch):
    limiter = _in_memory_rate_limiter()
    _install_auth_helpers(
        monkeypatch,
        rate_limited=limiter,
        observer=lambda *_args: None,
    )
    monkeypatch.setenv('RATE_LIMIT_AUTH_IP', '2')
    monkeypatch.setenv('RATE_LIMIT_AUTH_EMAIL', '5')

    emails = [f'ip-limit-{uuid.uuid4().hex}-{index}@example.com' for index in range(3)]
    responses = [_post_auth_start(client, email) for email in emails]

    assert [response.status_code for response in responses] == [200, 200, 429]


def test_one_normalized_email_hits_its_ceiling_across_different_ips(client, monkeypatch):
    limiter = _in_memory_rate_limiter()
    ip_values = iter(('198.51.100.10', '198.51.100.11', '198.51.100.12'))
    _install_auth_helpers(
        monkeypatch,
        rate_limited=limiter,
        observer=lambda *_args: None,
        client_ip=lambda: next(ip_values),
    )
    monkeypatch.setenv('RATE_LIMIT_AUTH_IP', '5')
    monkeypatch.setenv('RATE_LIMIT_AUTH_EMAIL', '2')

    email = f'email-limit-{uuid.uuid4().hex}@example.com'
    responses = [
        _post_auth_start(client, email.upper()),
        _post_auth_start(client, email),
        _post_auth_start(client, f'  {email}  '),
    ]

    assert [response.status_code for response in responses] == [200, 200, 429]
