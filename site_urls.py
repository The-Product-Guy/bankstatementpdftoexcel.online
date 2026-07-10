"""Public base URL helpers shared by template globals and SEO routes."""
import os

from flask import request


def normalize_base_url(value: str) -> str:
    """Trim and force an absolute https:// origin.

    CANONICAL_BASE_URL set without a scheme (e.g. "example.com") otherwise
    leaks scheme-less URLs into canonical tags, sitemaps, and robots.txt,
    which crawlers resolve as relative paths that 404.
    """
    base = value.strip().rstrip('/')
    if base and '://' not in base:
        base = f'https://{base}'
    return base


def public_base_url() -> str:
    configured = os.environ.get('CANONICAL_BASE_URL') or os.environ.get('PUBLIC_BASE_URL')
    if configured:
        return normalize_base_url(configured)
    return request.url_root.rstrip('/')
