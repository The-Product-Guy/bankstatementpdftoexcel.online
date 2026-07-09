# SEO Round — Bank Landing Pages, Blog Restructure, Universal-Parser Positioning

**Date:** 2026-07-08
**Status:** Approved
**Owner:** Sasi (approved via Claude Code session)

## Problem

~10 indexable pages; traffic skews US/EU (per owner's analytics) while all
proof-samples are Indian banks. Search demand is bank-specific
("chase statement to excel"), not generic. The blog listing duplicates full
article bodies (self-competing content), og-image is 1.45 MB, socket.io
loads on every marketing page, and the demo sample reads Indian
("UPI-GROCERY MART").

## Decisions (locked with owner)

1. **US/EU-first bank list, round one (12):**
   US — Chase, Bank of America, Wells Fargo, Citi, Capital One;
   UK — Barclays, HSBC, Lloyds, NatWest;
   EU — ING, Deutsche Bank, Santander. (India round later.)
2. **URL style:** keyword slugs — `/convert/chase-statement-to-excel`.
3. **Validation before claims:** no bank page ships before its statement
   *format shape* passes through the real parser.
4. Positioning: universal geometry-based extraction ("we don't need to know
   your bank") is the story; per-bank pages capture the search demand.
   Standard honesty rules apply: no "official", "partner", or "supported
   bank" claims; no accuracy percentages; claims limited to what the
   validation gate demonstrates.

## Components

### 0. Format validation gate (prerequisite)

- Synthetic text-PDF statements via reportlab, one per *format family*,
  mimicking real layout conventions:
  - **US style** (Chase/BofA/Wells Fargo/Citi/Capital One): MM/DD or
    MM/DD/YYYY dates, `$1,234.56` amounts, columns like
    `Date | Description | Amount | Balance` and
    `Date | Description | Withdrawals | Deposits | Balance`.
  - **UK style** (Barclays/HSBC/Lloyds/NatWest): `12 Jun 2026`-style dates,
    `£` amounts, `Date | Description | Money out | Money in | Balance`.
  - **EU style** (ING/Deutsche/Santander): `12-06-2026` / `12.06.2026`
    dates, `1.234,56` decimal-comma amounts, debit/credit columns.
- Run each through `LayoutReplicaParser` directly (venv, text path, no web
  stack). Pass criteria per fixture: bank header row detected (not
  positional columns), expected column count, one table row per printed
  transaction, date values land in the date column, amount values in their
  amount columns.
- Failures are parser bugs to fix TDD-style **before** the pages ship.
- Fixtures live in `tests/data/synthetic/` (committed — synthetic data,
  whitelisted past the `*.pdf` ignore) with pytest cases in
  `tests/test_western_formats.py`, giving the suite its first Western-format
  regression coverage.

### 1. Bank landing pages (data-driven)

- `routes/bank_pages.py`: `BANK_PAGES` list of dicts —
  `slug`, `name`, `country`, `currency`, `date_format`, `columns`
  (typical statement columns), `layout_notes` (2–3 sentences of genuinely
  bank-specific format description), `faqs` (3–4 Q/A pairs), `lastmod`.
  `BANK_BY_SLUG` lookup mirror. Slugs follow
  `<bank>-statement-to-excel` (e.g. `chase-statement-to-excel`).
- Routes on the existing pages blueprint:
  - `/convert/` — index: intro ("any bank works — these are the ones people
    ask about") + card grid of all bank pages.
  - `/convert/<slug>` — landing page; 404 for unknown slugs.
- `templates/bank_landing.html` (ledger design system): h1
  `Convert <Bank> Statements to Excel`, subhead, bank-specific layout notes
  as a spec-sheet block (columns/date format/currency), 3-step how-it-works,
  honest FAQ (`<details>`, mirrored FAQPage JSON-LD), CTA band → dashboard.
  Breadcrumb JSON-LD (Home → Convert → Bank). Canonical + meta description
  per bank. One `<h1>` per page.
- `templates/bank_index.html` for `/convert/`.
- Sitemap: add `/convert/` + every bank page to `_sitemap_urls()` with
  per-entry lastmod.
- Discovery links: footer gains a "Convert" column (index + top 4 banks);
  blogs listing links the index; home capability strip links the index.

### 2. Blog restructure + new content

- `blogs.html`: excerpt-only cards (category eyebrow, serif title, 1–2
  sentence excerpt, date, link). Full bodies render only on
  `blog_post.html`. URLs unchanged. The `excerpt` field comes from
  `BLOG_POSTS` (add where missing).
- Existing posts: reframe copy to universal/exact-copy truth (no
  normalized-output references; keep factual claims true).
- Two new `BLOG_POSTS` entries (full HTML bodies):
  1. **Pillar:** "How exact-copy extraction works — any bank, typed or
     scanned" (geometry-first parsing, header detection, Full_Text
     no-loss invariant, why normalization was retired).
  2. **Workflow:** "Convert bank statements to Excel for QuickBooks/Xero
     reconciliation" (accountant intent; honest: we output XLSX, the
     import into QBO/Xero is described generically).

### 3. Site hygiene

- `static/og-image.png`: regenerate 1200×630 in the ledger system (ink
  background, logo mark, wordmark, one-line value prop) via Pillow,
  target < 150 KB.
- socket.io `<script>` moves from `base.html` into the templates that use
  it (`dashboard.html`, `processing.html`) via the existing scripts block.
- Demo sample de-Indianized: regenerate `sample-statement.pdf` with
  Western transactions (DIRECT DEPOSIT, CARD PURCHASE, ACH PAYMENT, etc.),
  reconvert through the real pipeline, replace
  `static/sample-statement.xlsx`.

### 4. Tests

- `tests/test_western_formats.py`: parser fixtures (component 0).
- Route tests: `/convert/` and every bank page return 200 with the right
  h1, FAQPage JSON-LD present, unknown slug 404s; sitemap.xml contains all
  bank URLs; blogs listing contains excerpts but NOT full article bodies
  (assert a known body-only sentence is absent); home/pricing/blogs
  responses do not contain `socket.io`; dashboard still does.

## Out of scope

- India bank pages (later round, samples already exist).
- Localization/hreflang (site stays English).
- Paid acquisition, analytics changes, real-bank sample PDFs (synthetic
  only — never commit a real statement).

## Success criteria

- 13 new indexable pages (12 banks + index), all in sitemap, all honest.
- Parser demonstrably handles US/UK/EU format families (tests prove it).
- Blog listing no longer duplicates post bodies.
- og-image < 150 KB; marketing pages free of socket.io.
- Full suite green.
