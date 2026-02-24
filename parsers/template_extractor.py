#!/usr/bin/env python3
"""
Template-based extraction: detect document structure once, apply everywhere.

Instead of calling an LLM per chunk (N calls for N chunks), this module
detects the column layout from headers + a few sample rows (0 or 1 LLM call)
and returns a DocumentTemplate that the existing _row_to_transaction() pipeline
can consume via to_col_map().

Typical flow:
  1. TemplateDetector._try_heuristic  → FREE, no LLM
  2. TemplateDetector._detect_via_llm → 1 LLM call (~200-400 tokens)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Anthropic client singleton (mirrors get_openai_client in llm_table_extractor)
# ---------------------------------------------------------------------------

_anthropic_client = None


def _get_anthropic_client():
    """Get or create Anthropic client (lazy singleton)."""
    global _anthropic_client
    if _anthropic_client is None:
        try:
            import anthropic
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY environment variable not set. "
                    "Set it with: export ANTHROPIC_API_KEY='your-key'"
                )
            _anthropic_client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            raise ImportError("anthropic not installed. Install with: pip install anthropic")
    return _anthropic_client


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ColumnSpec:
    original_name: str       # e.g. "TXN DT"
    standard_field: str      # date | description | reference | debit | credit | balance | ignored
    position_index: int      # 0-based column index


@dataclass
class DocumentTemplate:
    columns: List[ColumnSpec]

    # Quick-access indexes  (standard_field → position_index)
    date_idx: Optional[int] = None
    description_idx: Optional[int] = None
    reference_idx: Optional[int] = None
    debit_idx: Optional[int] = None
    credit_idx: Optional[int] = None
    balance_idx: Optional[int] = None

    # Document-level patterns
    date_format: str = ""              # strftime, e.g. "%d/%m/%Y"
    date_regex: str = ""               # regex for the detected date style
    number_format: str = "western"     # "indian" | "western" | "plain"
    currency_symbol: str = ""
    has_merged_amounts: bool = False
    merge_order: str = "debit_first"   # "debit_first" or "credit_first"
    header_fingerprint: str = ""       # lowered/joined header text for duplicate detection

    # Quality metadata
    detection_confidence: float = 0.0
    llm_tokens_used: int = 0
    detection_method: str = "heuristic"  # "heuristic" or "llm"

    def to_col_map(self) -> Dict[str, int]:
        """Return the col_map dict that _row_to_transaction() expects."""
        m: Dict[str, int] = {}
        if self.date_idx is not None:
            m["date"] = self.date_idx
        if self.description_idx is not None:
            m["description"] = self.description_idx
        if self.reference_idx is not None:
            m["reference"] = self.reference_idx
        if self.debit_idx is not None:
            m["debit"] = self.debit_idx
        if self.credit_idx is not None:
            m["credit"] = self.credit_idx
        if self.balance_idx is not None:
            m["balance"] = self.balance_idx
        return m


# ---------------------------------------------------------------------------
# Format detection helpers
# ---------------------------------------------------------------------------

_DATE_PATTERNS: List[Tuple[str, str]] = [
    # (regex, strftime)  — ordered most-specific first
    (r"\d{4}[/-]\d{2}[/-]\d{2}", "%Y-%m-%d"),
    (r"\d{2}[/-]\d{2}[/-]\d{4}", "%d/%m/%Y"),
    (r"\d{2}[/-]\d{2}[/-]\d{2}", "%d/%m/%y"),
    (r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}", "%d %b %Y"),
    (r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2}", "%d %b %y"),
]


def detect_date_format(date_strings: Sequence[str]) -> Tuple[str, str]:
    """Return (strftime_format, regex) from a sample of date-like strings."""
    for regex, fmt in _DATE_PATTERNS:
        hits = sum(1 for s in date_strings if re.search(regex, s.strip(), re.IGNORECASE))
        if hits >= max(1, len(date_strings) * 0.5):
            return fmt, regex
    # Fallback: generic date pattern
    return "%d/%m/%Y", r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"


def detect_number_format(amount_strings: Sequence[str]) -> str:
    """Classify amount strings as 'indian', 'western', or 'plain'."""
    indian_re = re.compile(r"\d{1,2},\d{2},\d{3}")        # 1,23,456
    western_re = re.compile(r"\d{1,3}(?:,\d{3})+")         # 123,456
    indian_count = 0
    western_count = 0
    for s in amount_strings:
        cleaned = s.strip().replace(" ", "")
        if indian_re.search(cleaned):
            indian_count += 1
        elif western_re.search(cleaned):
            western_count += 1
    if indian_count > western_count and indian_count > 0:
        return "indian"
    if western_count > 0:
        return "western"
    return "plain"


def detect_merged_amounts(
    sample_rows: List[List[str]],
    potential_amount_cols: List[int],
) -> Tuple[bool, str]:
    """Check if debit+credit are merged in a single cell (e.g. '6,000.00 0.00')."""
    amount_pair_re = re.compile(
        r"-?\d[\d,]*(?:\.\d{2})\s+-?\d[\d,]*(?:\.\d{2})"
    )
    merge_count = 0
    for row in sample_rows:
        for idx in potential_amount_cols:
            if idx < len(row):
                cell = str(row[idx] or "")
                if amount_pair_re.search(cell):
                    merge_count += 1
                    break
    has_merged = merge_count >= max(1, len(sample_rows) * 0.4)
    return has_merged, "debit_first"


# ---------------------------------------------------------------------------
# Header keyword maps (mirrors universal_parser._map_headers logic)
# ---------------------------------------------------------------------------

_FIELD_KEYWORDS: Dict[str, List[str]] = {
    "date": ["date", "txn", "value", "posting"],
    "description": ["desc", "narr", "partic", "remark", "details"],
    "reference": ["ref", "cheq", "chq", "utr", "instrument"],
    "debit": ["debit", "withdraw", "dr"],
    "credit": ["credit", "deposit", "cr"],
    "balance": ["balance", "bal"],
}

_TOKEN_FIELDS: Dict[str, str] = {
    "dr": "debit",
    "cr": "credit",
}


def _normalize(text: str) -> str:
    cleaned = text.lower().replace("_", " ")
    cleaned = re.sub(r"[^a-z0-9\s]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


# ---------------------------------------------------------------------------
# TemplateDetector
# ---------------------------------------------------------------------------

class TemplateDetector:
    """Produce a DocumentTemplate from headers + sample rows."""

    def detect(
        self,
        headers: List[str],
        sample_rows: List[List[str]],
        bank_profile=None,
        first_page_text: str = "",
        first_pages_text: str = "",
    ) -> Optional[DocumentTemplate]:
        """Try heuristic first, fall back to LLM."""
        tpl = self._try_heuristic(headers, sample_rows, bank_profile)
        if tpl and tpl.detection_confidence >= 0.6:
            logger.debug("Heuristic template detection succeeded (confidence=%.2f)", tpl.detection_confidence)
            return tpl

        # LLM fallback
        logger.debug("Heuristic insufficient; trying LLM template detection")
        llm_tpl = self._detect_via_llm(headers, sample_rows, page_text=first_pages_text)
        if llm_tpl:
            return llm_tpl

        # Return heuristic even if low confidence (caller decides threshold)
        return tpl

    # ------------------------------------------------------------------
    # Heuristic path (FREE, no LLM)
    # ------------------------------------------------------------------

    def _try_heuristic(
        self,
        headers: List[str],
        sample_rows: List[List[str]],
        bank_profile=None,
    ) -> Optional[DocumentTemplate]:
        if not headers:
            return None

        mapping: Dict[str, int] = {}
        normalized = [_normalize(h) for h in headers]

        # 1) Bank-profile aliases (highest priority)
        if bank_profile and hasattr(bank_profile, "header_aliases"):
            for std_field, aliases in bank_profile.header_aliases.items():
                if std_field in mapping:
                    continue
                for alias in aliases:
                    alias_n = _normalize(alias)
                    if not alias_n:
                        continue
                    for idx, h in enumerate(normalized):
                        if alias_n in h:
                            mapping[std_field] = idx
                            break
                    if std_field in mapping:
                        break

        # 2) Generic keyword matching
        for idx, h in enumerate(normalized):
            if not h:
                continue
            tokens = set(h.split())

            for std_field, keywords in _FIELD_KEYWORDS.items():
                if std_field in mapping:
                    continue
                if any(kw in h for kw in keywords):
                    mapping[std_field] = idx
                    break

            # Token-exact matches (dr / cr)
            for tok, std_field in _TOKEN_FIELDS.items():
                if tok in tokens and std_field not in mapping:
                    mapping[std_field] = idx

        if "date" not in mapping:
            return None

        # Build ColumnSpec list
        assigned: Dict[int, str] = {v: k for k, v in mapping.items()}
        columns: List[ColumnSpec] = []
        for idx, h in enumerate(headers):
            std = assigned.get(idx, "ignored")
            columns.append(ColumnSpec(original_name=h, standard_field=std, position_index=idx))

        # Detect patterns from sample rows
        date_samples = self._collect_column_samples(sample_rows, mapping.get("date"))
        date_fmt, date_regex = detect_date_format(date_samples)

        amount_samples = self._collect_amount_samples(sample_rows, mapping)
        num_fmt = detect_number_format(amount_samples)

        amount_cols = [
            mapping[k] for k in ("debit", "credit", "balance") if k in mapping
        ]
        has_merged, merge_order = detect_merged_amounts(sample_rows, amount_cols)

        # Confidence heuristic
        matched_count = len(mapping)
        has_amounts = any(k in mapping for k in ("debit", "credit", "balance"))
        confidence = min(1.0, 0.3 + 0.15 * matched_count + (0.15 if has_amounts else 0))

        fingerprint = " ".join(normalized)

        return DocumentTemplate(
            columns=columns,
            date_idx=mapping.get("date"),
            description_idx=mapping.get("description"),
            reference_idx=mapping.get("reference"),
            debit_idx=mapping.get("debit"),
            credit_idx=mapping.get("credit"),
            balance_idx=mapping.get("balance"),
            date_format=date_fmt,
            date_regex=date_regex,
            number_format=num_fmt,
            currency_symbol="",
            has_merged_amounts=has_merged,
            merge_order=merge_order,
            header_fingerprint=fingerprint,
            detection_confidence=confidence,
            llm_tokens_used=0,
            detection_method="heuristic",
        )

    # ------------------------------------------------------------------
    # LLM path (1 API call)
    # ------------------------------------------------------------------

    _LLM_PROMPT = """You are analyzing a bank statement table structure.

HEADERS: {headers}
SAMPLE ROWS (first 3-5 data rows):
{formatted_rows}

FULL TEXT FROM FIRST PAGES OF THE DOCUMENT:
{page_text}

Return a JSON object:
{{
  "columns": [
    {{"original_name": "<header>", "standard_field": "<date|description|reference|debit|credit|balance|ignored>", "position_index": <0-based>}}
  ],
  "date_format": "<strftime like %d/%m/%Y>",
  "number_format": "<indian|western|plain>",
  "has_merged_amounts": <true|false>,
  "merge_order": "<debit_first|credit_first>",
  "currency_symbol": "<symbol or empty>",
  "confidence": <0.0-1.0>
}}

Rules:
- date: transaction date, value date, posting date
- description: narration, particulars, remarks, details
- reference: cheque no, ref no, UTR, instrument ID
- debit: withdrawal, DR
- credit: deposit, CR
- balance: closing balance, running balance
- ignored: serial no, branch code, duplicate date columns
- indian number format: 1,23,456.78 (groups of 2 after first 3)
- western: 123,456.78 (groups of 3)
- has_merged_amounts: true when one cell contains "6,000.00 0.00" (both amounts)

Return ONLY the JSON. No markdown fences, no explanation."""

    def _detect_via_llm(
        self,
        headers: List[str],
        sample_rows: List[List[str]],
        page_text: str = "",
    ) -> Optional[DocumentTemplate]:
        try:
            client = _get_anthropic_client()
        except Exception as exc:
            logger.warning("Anthropic client unavailable: %s", exc)
            return None

        formatted = "\n".join(
            "  " + " | ".join(str(c) for c in row)
            for row in sample_rows[:5]
        )
        prompt = self._LLM_PROMPT.format(
            headers=" | ".join(headers),
            formatted_rows=formatted,
            page_text=page_text[:4000] if page_text else "(not available)",
        )

        try:
            logger.debug("Starting Claude Haiku template detection API call")
            _t0 = time.monotonic()
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            elapsed = time.monotonic() - _t0
            raw = response.content[0].text.strip()
            tokens_used = (response.usage.input_tokens + response.usage.output_tokens)
            logger.debug(
                "Claude Haiku API call completed in %.2fs (%d tokens)",
                elapsed, tokens_used
            )

            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)

            data = json.loads(raw)
        except Exception as exc:
            logger.warning("Claude Haiku template detection failed: %s", exc)
            return None

        return self._parse_llm_response(data, headers, tokens_used, sample_rows)

    def _parse_llm_response(
        self,
        data: dict,
        headers: List[str],
        tokens_used: int,
        sample_rows: List[List[str]],
    ) -> Optional[DocumentTemplate]:
        cols_data = data.get("columns", [])
        if not cols_data:
            return None

        columns: List[ColumnSpec] = []
        idx_map: Dict[str, int] = {}

        for item in cols_data:
            std = item.get("standard_field", "ignored")
            pos = item.get("position_index", 0)
            name = item.get("original_name", "")
            columns.append(ColumnSpec(original_name=name, standard_field=std, position_index=pos))
            if std != "ignored" and std not in idx_map:
                idx_map[std] = pos

        date_fmt = data.get("date_format", "")
        if not date_fmt:
            date_samples = self._collect_column_samples(sample_rows, idx_map.get("date"))
            date_fmt, _ = detect_date_format(date_samples)

        date_regex_str = ""
        for regex, fmt in _DATE_PATTERNS:
            if fmt == date_fmt:
                date_regex_str = regex
                break
        if not date_regex_str:
            date_regex_str = r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"

        conf = float(data.get("confidence", 0.8))
        normalized = [_normalize(h) for h in headers]

        return DocumentTemplate(
            columns=columns,
            date_idx=idx_map.get("date"),
            description_idx=idx_map.get("description"),
            reference_idx=idx_map.get("reference"),
            debit_idx=idx_map.get("debit"),
            credit_idx=idx_map.get("credit"),
            balance_idx=idx_map.get("balance"),
            date_format=date_fmt,
            date_regex=date_regex_str,
            number_format=data.get("number_format", "western"),
            currency_symbol=data.get("currency_symbol", ""),
            has_merged_amounts=bool(data.get("has_merged_amounts", False)),
            merge_order=data.get("merge_order", "debit_first"),
            header_fingerprint=" ".join(normalized),
            detection_confidence=conf,
            llm_tokens_used=tokens_used,
            detection_method="llm",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_column_samples(
        rows: List[List[str]], col_idx: Optional[int], limit: int = 10
    ) -> List[str]:
        if col_idx is None:
            return []
        out: List[str] = []
        for row in rows:
            if col_idx < len(row):
                val = str(row[col_idx] or "").strip()
                if val:
                    out.append(val)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _collect_amount_samples(
        rows: List[List[str]], mapping: Dict[str, int], limit: int = 15
    ) -> List[str]:
        cols = [mapping[k] for k in ("debit", "credit", "balance") if k in mapping]
        out: List[str] = []
        for row in rows:
            for idx in cols:
                if idx < len(row):
                    val = str(row[idx] or "").strip()
                    if val and re.search(r"\d", val):
                        out.append(val)
            if len(out) >= limit:
                break
        return out
