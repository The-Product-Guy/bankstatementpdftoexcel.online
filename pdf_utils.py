"""PDF preflight helpers shared by upload and worker paths."""
from __future__ import annotations

from pathlib import Path
from typing import Union


PDFPath = Union[str, Path]
PASSWORD_PROTECTED_PDF_MESSAGE = (
    "Password-protected PDFs are not supported yet. Remove the password before uploading."
)


class PasswordProtectedPDFError(ValueError):
    """Raised when a PDF is encrypted or requires a password."""


def _looks_like_password_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(token in text for token in ("password", "encrypted", "decrypt"))


def is_password_protected_pdf(pdf_path: PDFPath) -> bool:
    """
    Return True when PyMuPDF can identify the file as encrypted.

    If PyMuPDF is unavailable or cannot inspect the file, callers still get a
    second chance through pdfplumber/pdfminer error detection.
    """
    try:
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf  # type: ignore

        doc = pymupdf.open(str(pdf_path))
        try:
            return bool(getattr(doc, "needs_pass", False) or getattr(doc, "is_encrypted", False))
        finally:
            doc.close()
    except Exception:
        return False


def raise_if_password_protected(pdf_path: PDFPath) -> None:
    if is_password_protected_pdf(pdf_path):
        raise PasswordProtectedPDFError(PASSWORD_PROTECTED_PDF_MESSAGE)


def get_pdf_page_count(pdf_path: PDFPath) -> int:
    """Read a PDF page count and normalize encrypted-PDF errors."""
    raise_if_password_protected(pdf_path)
    try:
        import pdfplumber

        with pdfplumber.open(str(pdf_path)) as pdf:
            return len(pdf.pages)
    except Exception as exc:
        if _looks_like_password_error(exc):
            raise PasswordProtectedPDFError(PASSWORD_PROTECTED_PDF_MESSAGE) from exc
        raise
