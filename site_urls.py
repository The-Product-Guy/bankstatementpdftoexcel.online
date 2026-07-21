"""Public base URL helpers shared by template globals and SEO routes."""
import os
from urllib.parse import urlsplit

from flask import request


def normalize_base_url(value: str) -> str:
    """Return a validated HTTPS origin with no path, query, or fragment.

    CANONICAL_BASE_URL set without a scheme (e.g. "example.com") otherwise
    leaks scheme-less URLs into canonical tags, sitemaps, and robots.txt,
    which crawlers resolve as relative paths that 404.
    """
    base = (value or '').strip()
    if not base:
        raise ValueError('Public base URL cannot be empty.')
    if '://' not in base:
        base = f'https://{base}'

    parsed = urlsplit(base)
    try:
        parsed.port  # Validate malformed and out-of-range ports before reuse.
    except ValueError as exc:
        raise ValueError('Public base URL contains an invalid port.') from exc
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('Public base URL must be an absolute HTTP(S) origin.')
    if any(character.isspace() for character in parsed.netloc):
        raise ValueError('Public base URL cannot contain whitespace.')
    if parsed.username or parsed.password:
        raise ValueError('Public base URL cannot contain credentials.')
    if parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
        raise ValueError('Public base URL must not contain a path, query, or fragment.')

    # Canonical and discovery URLs are always HTTPS in production. Converting an
    # explicitly configured http:// origin also prevents mixed canonical signals.
    return f"https://{parsed.netloc.lower()}"


def public_base_url() -> str:
    configured = os.environ.get('CANONICAL_BASE_URL') or os.environ.get('PUBLIC_BASE_URL')
    if configured:
        return normalize_base_url(configured)
    return request.url_root.rstrip('/')
