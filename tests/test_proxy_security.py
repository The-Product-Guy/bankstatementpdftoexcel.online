"""Security tests for the Cloudflare-to-origin trust boundary."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('DISABLE_QUOTAS', 'true')


@pytest.fixture
def client():
    from app import app

    app.config['TESTING'] = True
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture
def cloudflare_enabled(monkeypatch):
    import app as app_module

    secret = 's' * 32
    monkeypatch.setenv('CLOUDFLARE_PROXY_ENABLED', 'true')
    monkeypatch.setenv('CLOUDFLARE_ORIGIN_SECRET', secret)
    monkeypatch.setattr(app_module, 'IS_PRODUCTION', True)
    return secret


def test_disabled_proxy_uses_tcp_peer_and_ignores_forwarding_headers(monkeypatch):
    import app as app_module

    monkeypatch.delenv('CLOUDFLARE_PROXY_ENABLED', raising=False)
    monkeypatch.delenv('CLOUDFLARE_ORIGIN_SECRET', raising=False)
    with app_module.app.test_request_context(
        '/',
        headers={
            'CF-Connecting-IP': '203.0.113.10',
            'X-Statement-Origin': 's' * 32,
            'X-Real-IP': '203.0.113.11',
            'X-Forwarded-For': '203.0.113.12',
        },
        environ_base={'REMOTE_ADDR': '198.51.100.10'},
    ):
        assert app_module.get_client_ip() == '198.51.100.10'


@pytest.mark.parametrize('forwarded_ip', ['203.0.113.24', '2001:db8::24'])
def test_authenticated_cloudflare_header_supports_ipv4_and_ipv6(
    cloudflare_enabled, forwarded_ip
):
    import app as app_module

    with app_module.app.test_request_context(
        '/',
        headers={
            'CF-Connecting-IP': forwarded_ip,
            'X-Statement-Origin': cloudflare_enabled,
        },
        environ_base={'REMOTE_ADDR': '198.51.100.10'},
    ):
        assert app_module.get_client_ip() == forwarded_ip


def test_forged_cloudflare_headers_are_ignored_without_origin_secret(cloudflare_enabled):
    import app as app_module

    with app_module.app.test_request_context(
        '/',
        headers={'CF-Connecting-IP': '203.0.113.24'},
        environ_base={'REMOTE_ADDR': '198.51.100.10'},
    ):
        assert app_module.get_client_ip() == '198.51.100.10'


@pytest.mark.parametrize(
    'headers',
    [
        {},
        {'X-Statement-Origin': 'wrong' * 8, 'CF-Connecting-IP': '203.0.113.24'},
        {
            'X-Statement-Origin': f"{'s' * 32}, {'s' * 32}",
            'CF-Connecting-IP': '203.0.113.24',
        },
        {'X-Statement-Origin': 's' * 32, 'CF-Connecting-IP': '203.0.113.24, 198.51.100.10'},
        {'X-Statement-Origin': 's' * 32, 'CF-Connecting-IP': 'not-an-ip'},
    ],
)
def test_production_rejects_missing_wrong_or_malformed_proxy_proof(
    client, cloudflare_enabled, headers
):
    response = client.get('/', headers=headers)
    assert response.status_code == 421


def test_production_rejects_direct_origin_even_for_canonical_host(client, cloudflare_enabled, monkeypatch):
    monkeypatch.setenv('CANONICAL_BASE_URL', 'https://canonical.example')

    response = client.get('/', headers={'Host': 'canonical.example'})
    assert response.status_code == 421


@pytest.mark.parametrize('path', ['/health', '/health/detailed'])
def test_health_endpoints_are_exempt_from_cloudflare_origin_proof(
    client, cloudflare_enabled, path
):
    response = client.get(path)
    assert response.status_code != 421


@pytest.mark.parametrize('secret', ['', 'too-short', 'x' * 31])
def test_proxy_configuration_requires_a_32_character_secret(monkeypatch, secret):
    import app as app_module

    monkeypatch.setenv('CLOUDFLARE_PROXY_ENABLED', 'true')
    monkeypatch.setenv('CLOUDFLARE_ORIGIN_SECRET', secret)
    with pytest.raises(RuntimeError, match='at least 32 characters'):
        app_module.cloudflare_proxy_config()


def test_proxy_configuration_is_disabled_by_default(monkeypatch):
    import app as app_module

    monkeypatch.delenv('CLOUDFLARE_PROXY_ENABLED', raising=False)
    monkeypatch.delenv('CLOUDFLARE_ORIGIN_SECRET', raising=False)
    assert app_module.cloudflare_proxy_config() == (False, '')


def test_distinct_subject_observer_uses_hmac_and_returns_threshold_crossing(monkeypatch):
    import app as app_module

    calls = []

    class FakeRedis:
        def eval(self, script, key_count, key, subject_digest, window, threshold):
            calls.append((script, key_count, key, subject_digest, window, threshold))
            return [3, 1]

    monkeypatch.setattr(app_module, 'get_redis_client', lambda: FakeRedis())
    raw_ip = '198.51.100.10'
    key = f"rate:auth:subjects:{app_module.hmac_rate_limit_subject(raw_ip, 'ip-bucket')}"
    result = app_module.observe_distinct_rate_limit_subject(
        key, 'person@example.com', 3600, 3
    )

    assert result == (3, True)
    script, key_count, redis_key, digest, window, threshold = calls[0]
    assert key_count == 1
    assert redis_key == key
    assert digest == app_module.hmac_rate_limit_subject('person@example.com', 'distinct-subject')
    serialized_args = repr(calls[0])
    assert raw_ip not in serialized_args
    assert 'person@example.com' not in serialized_args
    assert 'was_new == 1 and count == tonumber(ARGV[3])' in script
    assert (window, threshold) == (3600, 3)
