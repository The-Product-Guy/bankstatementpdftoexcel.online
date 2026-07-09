#!/usr/bin/env python3
"""Bank landing pages: data shape, routes, honesty rules, sitemap."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DISABLE_QUOTAS", "true")


def test_bank_pages_shape():
    from routes.bank_pages import BANK_PAGES, BANK_BY_SLUG

    assert len(BANK_PAGES) == 12
    for bank in BANK_PAGES:
        assert bank["slug"].endswith("-statement-to-excel")
        for key in ("name", "country", "currency", "date_format", "columns", "layout_notes", "faqs", "lastmod"):
            assert bank.get(key), f"{bank['slug']} missing {key}"
        assert len(bank["faqs"]) >= 3
        banned = ("official", "partner", "supported bank", "% accuracy")
        text = (bank["layout_notes"] + " ".join(f["q"] + f["a"] for f in bank["faqs"])).lower()
        assert not any(term in text for term in banned), bank["slug"]
    assert set(BANK_BY_SLUG) == {bank["slug"] for bank in BANK_PAGES}


@pytest.fixture()
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def test_bank_index_renders(client):
    resp = client.get("/convert/")
    assert resp.status_code == 200
    assert b"Convert any bank" in resp.data


def test_bank_landing_renders(client):
    resp = client.get("/convert/chase-statement-to-excel")
    assert resp.status_code == 200
    assert b"Convert Chase Statements to Excel" in resp.data
    assert b"FAQPage" in resp.data
    assert b"BreadcrumbList" in resp.data


def test_unknown_bank_404(client):
    assert client.get("/convert/notabank-statement-to-excel").status_code == 404


def test_bank_pages_in_sitemap(client):
    xml = client.get("/sitemap.xml").data.decode()
    assert "/convert/" in xml
    assert "/convert/chase-statement-to-excel" in xml
    assert "/convert/santander-statement-to-excel" in xml
