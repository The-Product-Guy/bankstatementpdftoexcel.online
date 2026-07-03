# Exact-Copy Extraction Fidelity (layout_replica v2)

**Date:** 2026-07-03
**Status:** Approved
**Owner:** Sasi (approved via Claude Code session)

## Problem

Product direction: the Excel output must be an exact copy of what is in the PDF —
correct rows and columns, nothing silently dropped. Users delete unwanted content
themselves. The default `layout_replica` mode gets columns right on real samples
(verified on KVB PART-02/PART-03 scans) but violates "exact copy" twice:

1. **Wrapped rows split.** One ruled PDF table row whose description wraps over
   2–3 printed lines becomes 2–3 Excel rows. KVB PART-03: 2,148 transactions →
   4,491 Excel rows. Sums/counts need manual cleanup.
2. **Content silently deleted.** `_is_document_context_line` and
   `_is_table_relevant_row` (parsers/layout_replica_parser.py) drop bank name,
   account number, statement period, footers — unrecoverable. A full-text sheet
   (`_write_lines_sheet`) exists in code but `write_excel` never calls it.

Alternatives rejected on evidence: img2table bordered detection (missed page 2 of
the KVB sample, still split wrapped rows, misassigned branch column); LLM-per-page
(cost/hallucination at 100+ pages).

## Design

All changes in `parsers/layout_replica_parser.py`. Worker/web untouched
(`write_excel` signature unchanged; worker's `transaction_count` automatically
becomes the merged row count).

### 1. One Excel row per PDF transaction (continuation merge)

In `_table_rows_from_page`, classify a line as a **continuation** of the previous
emitted row when ALL hold:

- a data row has already been emitted for the current page;
- no date-like value in the first two columns;
- no amount-like value in any amount-headed column (debit/credit/balance/amount);
- every populated column is text-ish (description/reference/details/remarks) —
  by header token, or column index 1–2 when headers are positional (`Column N`).

Merge instead of emitting: for each populated column, append the continuation
text to the parent row's same column (space-joined). Parent = last emitted row of
the same page. Lines that fail the test remain ordinary rows under existing rules.

### 2. Nothing silently deleted (`Full_Text` sheet)

`write_excel` writes a second sheet `Full_Text` (existing `_write_lines_sheet`,
retitled): Page | Line | Source | Text for every visual line of every page in
reading order. Context filters keep the table sheet clean; the full page content
— header blocks, account info, footers, rejected rows — is always recoverable
here.

**Invariant (tested):** every extracted visual line appears in the workbook at
least once — table sheet or `Full_Text`. The `Full_Text` sheet satisfies this by
construction: one row per `LayoutLine`, no filtering.

### 3. Old normalized mode

`structured_transactions` path stays dormant (env-var only, no UI toggle,
no deletion).

## Marketing/UX honesty fixes (same round, separate commit)

1. Free-tier copy: marketing says "5 free conversions", guests get 1
   (`GUEST_CONVERSION_LIMIT=1`, app.py:64). Fix copy in home.html, signin.html
   (and footer) to the truth: 1 free conversion as guest, 5/month with a free
   account.
2. pricing.html:227 advertises "API access" — no API exists. Remove the claim.
3. dashboard.html:264 file-limit modal links to third-party
   `smallpdfsplit.online`. Remove the external link.
4. script.js:652 "Retry in high quality" dead-ends because the file input was
   cleared. Keep a reference to the last selected File and reuse it.

## Out of scope (next rounds)

- SEO content build-out (per-bank landing pages, blog restructure) — the real
  SEO lever; separate project.
- Deleting the structured/universal parser path.
- img2table integration into replica mode.

## Test plan

- Unit tests (tests/test_layout_replica_parser.py): continuation-merge behavior
  (wrap into description; wrap into description+reference; non-continuation kept
  as row; no merge across pages), `Full_Text` sheet present with one row per
  visual line, no-line-lost invariant.
- Route tests still green (`python -m pytest tests/ -v`).
- End-to-end verification on both KVB sample PDFs: merged table sheet row count
  ≈ dated-row count (~2,148 for PART-03), balance column stays in BALANCE,
  Full_Text contains account-header lines that the table sheet drops.
