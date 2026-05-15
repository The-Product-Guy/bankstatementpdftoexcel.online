import sys
from types import SimpleNamespace

import pytest


class _FakeDoc:
    def __init__(self, encrypted=False):
        self.needs_pass = encrypted
        self.is_encrypted = encrypted

    def close(self):
        pass


class _FakePDF:
    def __init__(self, page_count):
        self.pages = [object()] * page_count

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_detects_password_protected_pdf_from_pymupdf(monkeypatch):
    from pdf_utils import PasswordProtectedPDFError, raise_if_password_protected

    fake_pymupdf = SimpleNamespace(open=lambda path: _FakeDoc(encrypted=True))
    monkeypatch.setitem(sys.modules, "pymupdf", fake_pymupdf)

    with pytest.raises(PasswordProtectedPDFError):
        raise_if_password_protected("statement.pdf")


def test_get_pdf_page_count_normalizes_pdfplumber_password_errors(monkeypatch):
    from pdf_utils import PasswordProtectedPDFError, get_pdf_page_count

    fake_pymupdf = SimpleNamespace(open=lambda path: _FakeDoc(encrypted=False))

    def open_pdf(path):
        raise RuntimeError("PDF is encrypted and requires a password")

    monkeypatch.setitem(sys.modules, "pymupdf", fake_pymupdf)
    monkeypatch.setitem(sys.modules, "pdfplumber", SimpleNamespace(open=open_pdf))

    with pytest.raises(PasswordProtectedPDFError):
        get_pdf_page_count("statement.pdf")


def test_get_pdf_page_count_returns_page_count(monkeypatch):
    from pdf_utils import get_pdf_page_count

    fake_pymupdf = SimpleNamespace(open=lambda path: _FakeDoc(encrypted=False))
    fake_pdfplumber = SimpleNamespace(open=lambda path: _FakePDF(page_count=3))
    monkeypatch.setitem(sys.modules, "pymupdf", fake_pymupdf)
    monkeypatch.setitem(sys.modules, "pdfplumber", fake_pdfplumber)

    assert get_pdf_page_count("statement.pdf") == 3
