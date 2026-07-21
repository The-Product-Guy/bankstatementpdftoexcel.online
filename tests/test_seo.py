"""Regression checks for crawler-facing SEO behavior."""
import html
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DISABLE_QUOTAS", "true")


@pytest.fixture()
def client():
    from app import app

    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def _match_text(markup: str, tag: str) -> str:
    match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", markup, re.I | re.S)
    assert match, f"missing <{tag}>"
    return html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).strip()


def _visible_text(markup: str) -> str:
    without_noncontent = re.sub(
        r"<(?:script|style)\b.*?</(?:script|style)>",
        " ",
        markup,
        flags=re.I | re.S,
    )
    return html.unescape(re.sub(r"<[^>]+>", " ", without_noncontent))


def _word_count(markup: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", _visible_text(markup)))


def test_every_sitemap_page_has_concise_unique_metadata(client, monkeypatch):
    from routes.pages import _sitemap_urls

    monkeypatch.setenv("CANONICAL_BASE_URL", "https://statement.example")
    titles = set()
    descriptions = set()

    for path, _lastmod in _sitemap_urls():
        response = client.get(path)
        assert response.status_code == 200, path
        markup = response.get_data(as_text=True)
        title = _match_text(markup, "title")
        assert len(title) <= 70, (path, title, len(title))
        assert title not in titles, (path, title)
        titles.add(title)

        description_match = re.search(
            r'<meta\s+name="description"\s+content="([^"]+)"',
            markup,
            re.I,
        )
        assert description_match, path
        description = html.unescape(description_match.group(1)).strip()
        assert 50 <= len(description) <= 160, (path, len(description), description)
        assert description not in descriptions, (path, description)
        descriptions.add(description)

        assert len(re.findall(r"<h1\b", markup, re.I)) == 1, path
        assert f'<link rel="canonical" href="https://statement.example{path}">' in markup


def test_blog_articles_clear_semrush_content_thresholds(client):
    from routes.pages import BLOG_POSTS

    for post in BLOG_POSTS:
        path = f"/blogs/{post['slug']}"
        markup = client.get(path).get_data(as_text=True)
        main_match = re.search(r"<main\b[^>]*>(.*?)</main>", markup, re.I | re.S)
        assert main_match, path
        assert _word_count(main_match.group(1)) > 250, path

        visible_characters = len(re.sub(r"\s+", "", _visible_text(markup)))
        assert visible_characters / len(markup) > 0.10, path
        assert 'class="breadcrumbs"' in markup, path
        assert "By Ambion Softwares" in markup, path
        assert len(re.findall(r'class="related-guide-card"', markup)) >= 3, path


def test_bank_pages_receive_multiple_contextual_links(client):
    from routes.bank_pages import BANK_PAGES

    for bank in BANK_PAGES:
        path = f"/convert/{bank['slug']}"
        markup = client.get(path).get_data(as_text=True)
        assert 'class="breadcrumbs"' in markup, path
        assert len(re.findall(r'class="related-guide-card"', markup)) >= 3, path


def test_reported_orphan_and_single_link_targets_have_multiple_sources(client):
    from routes.bank_pages import BANK_PAGES
    from routes.pages import BLOG_POSTS, _sitemap_urls

    incoming = defaultdict(set)
    for source_path, _lastmod in _sitemap_urls():
        markup = client.get(source_path).get_data(as_text=True)
        for raw_href in re.findall(r'href="([^"#]+)"', markup, re.I):
            target_path = urlsplit(html.unescape(raw_href)).path
            incoming[target_path].add(source_path)

    reported_targets = {
        *(f"/blogs/{post['slug']}" for post in BLOG_POSTS),
        *(f"/convert/{bank['slug']}" for bank in BANK_PAGES),
        "/static/sample-statement.pdf",
        "/static/sample-statement.xlsx",
    }
    for target_path in reported_targets:
        assert len(incoming[target_path]) >= 2, (target_path, incoming[target_path])


def test_json_ld_is_valid_and_home_has_no_unverified_reviews(client):
    from routes.pages import _sitemap_urls

    for path, _lastmod in _sitemap_urls():
        markup = client.get(path).get_data(as_text=True)
        blocks = re.findall(
            r'<script\s+type="application/ld\+json">(.*?)</script>',
            markup,
            re.I | re.S,
        )
        for block in blocks:
            json.loads(html.unescape(block))

    homepage = client.get("/").get_data(as_text=True)
    assert '"@type": "WebApplication"' not in homepage
    assert '"@type": "Service"' in homepage
    assert '"aggregateRating"' not in homepage
    assert '"review"' not in homepage


def test_only_minified_first_party_assets_are_loaded(client):
    homepage = client.get("/").get_data(as_text=True)
    assert '/static/styles.min.css' in homepage
    assert '/static/ui.min.js' in homepage
    assert re.search(r'/static/styles\.min\.css\?v=[0-9a-f]{12}', homepage)
    assert re.search(r'/static/ui\.min\.js\?v=[0-9a-f]{12}', homepage)
    assert '/static/styles.css' not in homepage
    assert '/static/ui.js' not in homepage
    assert 'umami-production-9269.up.railway.app' not in homepage

    dashboard = client.get("/dashboard").get_data(as_text=True)
    assert '/static/script.min.js' in dashboard
    assert re.search(r'/static/script\.min\.js\?v=[0-9a-f]{12}', dashboard)
    assert '/static/script.js' not in dashboard

    for path in ("/static/styles.min.css", "/static/ui.min.js", "/static/script.min.js"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["Cache-Control"] == "public, max-age=604800"

    static_dir = Path(__file__).parent.parent / "static"
    for source_name, minified_name in (
        ("styles.css", "styles.min.css"),
        ("ui.js", "ui.min.js"),
        ("script.js", "script.min.js"),
    ):
        assert (static_dir / minified_name).stat().st_size < (static_dir / source_name).stat().st_size


def test_www_alias_redirects_to_https_canonical(client, monkeypatch):
    import app as app_module

    monkeypatch.setenv("CANONICAL_BASE_URL", "statement.example")
    monkeypatch.setattr(app_module, "IS_PRODUCTION", True)
    response = client.get(
        "/blogs?source=www",
        headers={"Host": "www.statement.example"},
        follow_redirects=False,
    )
    assert response.status_code == 308
    assert response.headers["Location"] == "https://statement.example/blogs?source=www"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"


def test_public_origin_validation():
    from site_urls import normalize_base_url

    assert normalize_base_url("statement.example/") == "https://statement.example"
    assert normalize_base_url("http://STATEMENT.EXAMPLE") == "https://statement.example"
    with pytest.raises(ValueError):
        normalize_base_url("")
    with pytest.raises(ValueError):
        normalize_base_url("ftp://statement.example")
    with pytest.raises(ValueError):
        normalize_base_url("https://statement.example/path")
    with pytest.raises(ValueError):
        normalize_base_url("https://statement.example?campaign=test")
    with pytest.raises(ValueError):
        normalize_base_url("https://user:password@statement.example")
    with pytest.raises(ValueError):
        normalize_base_url("https://statement.example:not-a-port")
    with pytest.raises(ValueError):
        normalize_base_url("https://statement example")


def test_sample_downloads_are_not_indexable(client):
    for path in ("/static/sample-statement.pdf", "/static/sample-statement.xlsx"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["X-Robots-Tag"] == "noindex, noarchive"


def test_social_image_metadata_matches_real_asset(client):
    homepage = client.get("/").get_data(as_text=True)
    assert '<meta property="og:image:width" content="1200">' in homepage
    assert '<meta property="og:image:height" content="630">' in homepage
    assert '<meta name="twitter:card" content="summary_large_image">' in homepage
