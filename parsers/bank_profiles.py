#!/usr/bin/env python3
"""
Bank profile definitions and lightweight matching helpers.

Phase 3 goal:
- Identify known bank formats early.
- Reuse bank-specific header aliases to improve column mapping.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


def _norm_text(value: str) -> str:
    cleaned = value.lower().replace("_", " ")
    cleaned = re.sub(r"[^a-z0-9\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


@dataclass(frozen=True)
class BankProfile:
    key: str
    display_name: str
    filename_patterns: Sequence[str] = field(default_factory=tuple)
    text_signatures: Sequence[str] = field(default_factory=tuple)
    header_aliases: Dict[str, Sequence[str]] = field(default_factory=dict)

    def matches_filename(self, filename: str) -> bool:
        name = filename.lower()
        return any(re.search(pattern, name, re.IGNORECASE) for pattern in self.filename_patterns)

    def score_text(self, text: str) -> int:
        if not text:
            return 0
        normalized = _norm_text(text)
        score = 0
        for signature in self.text_signatures:
            sig = _norm_text(signature)
            if sig and sig in normalized:
                score += 15
        return min(score, 45)

    def score_headers(self, headers: Sequence[str]) -> int:
        if not headers:
            return 0
        normalized_headers = [_norm_text(h) for h in headers if h]
        score = 0
        for aliases in self.header_aliases.values():
            for alias in aliases:
                alias_n = _norm_text(alias)
                if alias_n and any(alias_n in h for h in normalized_headers):
                    score += 8
                    break
        return min(score, 40)

    def header_tokens(self) -> List[str]:
        tokens = set()
        for aliases in self.header_aliases.values():
            for alias in aliases:
                for token in _norm_text(alias).split():
                    if len(token) >= 2:
                        tokens.add(token)
        return sorted(tokens)


PROFILES: List[BankProfile] = [
    BankProfile(
        key="hdfc",
        display_name="HDFC Bank",
        filename_patterns=(r"\bhdfc\b",),
        text_signatures=(
            "hdfc bank",
            "statement of account",
            "closing balance",
        ),
        header_aliases={
            "date": ("date", "txn date", "transaction date", "value date"),
            "description": ("narration", "description", "particulars", "remarks"),
            "reference": ("chq/ref no", "chq no", "ref no", "utr", "instrument id"),
            "debit": ("withdrawal amt", "debit", "dr", "debit amount"),
            "credit": ("deposit amt", "credit", "cr", "credit amount"),
            "balance": ("closing balance", "balance", "running balance"),
        },
    ),
    BankProfile(
        key="kvb",
        display_name="Karur Vysya Bank",
        filename_patterns=(r"\bkvb\b", r"karur\s+vysya"),
        text_signatures=(
            "karur vysya bank",
            "statement period",
            "account statement",
        ),
        header_aliases={
            "date": ("txn dt", "date", "value dt", "transaction date"),
            "description": ("transaction remarks", "remarks", "description", "narration"),
            "reference": ("cheque number", "cheque no", "ref no", "reference"),
            "debit": ("withdrawal amount", "debit", "dr", "withdrawal"),
            "credit": ("deposit amount", "credit", "cr", "deposit"),
            "balance": ("balance inr", "closing balance", "balance"),
        },
    ),
]


def detect_bank_profile(
    filename: str,
    first_page_text: str = "",
    headers: Optional[Sequence[str]] = None
) -> Optional[BankProfile]:
    """
    Detect best matching bank profile using filename, text signature, and header hints.
    Returns None when confidence is too low.
    """
    best_profile: Optional[BankProfile] = None
    best_score = 0

    for profile in PROFILES:
        score = 0
        if profile.matches_filename(filename):
            score += 80
        score += profile.score_text(first_page_text)
        if headers:
            score += profile.score_headers(headers)

        if score > best_score:
            best_score = score
            best_profile = profile

    # Conservative threshold to avoid accidental misclassification.
    if best_profile and best_score >= 30:
        return best_profile
    return None


def get_profile_header_aliases(profile: Optional[BankProfile]) -> Dict[str, Sequence[str]]:
    if not profile:
        return {}
    return profile.header_aliases
