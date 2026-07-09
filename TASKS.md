# StatementFlow - Pending Tasks

Last updated: 2026-07-03

---

## 2026-07-03 — Exact-Copy Fidelity Round (PR #1)

Product direction locked: **Excel = exact copy of the PDF** (bank's own rows/columns, nothing silently dropped, users delete unwanted content themselves). Spec: `docs/superpowers/specs/2026-07-03-exact-copy-extraction-design.md`. PR: https://github.com/The-Product-Guy/bankstatementpdftoexcel.online/pull/1 (merge = Railway deploy).

### Shipped

- [x] **One Excel row per PDF transaction** — wrapped continuation lines merge into their parent row (KVB PART-03: 4,491 rows → 2,281; 2,148 dated = actual transaction count).
- [x] **`Full_Text` sheet** — every visual line of every page; no-loss invariant (account info, headers, footers all recoverable).
- [x] **Multi-token OCR boxes split per token** — poor scans no longer dump date+branch into one column (PART-02: dated rows 17 → 303, orphans 542 → 168).
- [x] **Header-detected columns outrank positional inference** — one noisy page can't override real bank headers.
- [x] **Marketing honesty**: free-tier copy matches code (guest = 1, free account = 5/mo); removed fake "API access" from pricing; removed third-party smallpdfsplit.online link; "Retry in high quality" reuses the selected file.
- [x] 152 tests green; full OCR verification on both KVB sample statements.

### Open follow-ups

- [x] **Column drift on very poor scans** — proportional token split occasionally places a boundary word one column over (PART-02 rows like "03/09/18 1763 ATM" bleeding into VALUE DT). Candidate fix: snap tokens to the column containing most of their width.
- [x] **Pricing still claims "Unlimited batch processing"** (enterprise) — no batch upload exists; same honesty issue as the removed API claim. Needs owner call.
- [x] **Quota gating silently disables when `RESEND_API_KEY` is missing** (app.py) — a misconfigured deploy turns off all limits.
- [ ] **SEO content round** (biggest distribution lever): only ~10 indexable pages; no per-bank landing pages ("HDFC statement to Excel", …); `/blogs` listing duplicates and out-contents its own post pages; 1.4 MB og-image; socket.io CDN loaded on every marketing page.
- [ ] **UI trust round**: hero preview is fake static data — replace with real sample output + downloadable sample file.

---

## P0 - Must Fix (Blocking Production Quality)

- [x] **Missing `logo.png`** - Structured data now references `og-image.png` which exists.
- [x] **Footer legal links are dead** - Created `/privacy` and `/terms` routes with dedicated pages. Cleaned up duplicate footer entries.
- [x] **`index.html` is orphaned** - Deleted. `/index` route already redirects to `home`.
- [x] **`processing.html` doesn't extend `base.html`** - Refactored to extend `base.html` with nav, footer, and proper blocks.
- [x] **Sitemap has hardcoded dates** - Now uses `datetime.utcnow()`. Also added `/privacy` and `/terms` to sitemap and robots.txt.
- [x] **Copyright year is 2025** - Now dynamic via `{{ now().year }}`.

## P1 - Pending Feature Work

### From IMPLEMENTATION-TASKS.md (unfinished phases)

- [x] **LLM token/timeout guards** (Phase 4) - Added `LLM_MAX_OUTPUT_TOKENS` (default 4096) and `LLM_REQUEST_TIMEOUT` (default 60s) to both OpenAI and Anthropic clients.
- [x] **Acceptance accuracy gates** (Phase 5) - Added configurable dataset gates via `tests/evaluation/accuracy_gates.json` and `tools/run_accuracy_gates.py`.
- [x] **Balance-consistency SLO** (Phase 5) - CI gate now requires deterministic synthetic benchmark balance consistency >= 95%.
- [x] **CI regression gate** (Phase 5) - Added GitHub Actions job for tests plus deterministic synthetic accuracy gate.

### Auth & Email

- [x] **Switch from AWS SES to Resend** - Magic link emails now use Resend SDK.
- [x] **Magic link email design** - Branded HTML template with gradient header, CTA button, expiry notice, fallback URL, and footer.
- [x] **Signin page - no flash message display** - Added flash messages block to `signin.html`. Also fixed copy ("5 free conversions per month").

### Billing & Stripe

- [x] **No plan management UI for logged-in users** - Added `/account` page with plan details, monthly usage bar, recent conversions table, and billing portal link. Added "Account" nav link for logged-in users.
- [x] **Stripe webhook error handling uses `print()`** - All `print()` calls in `app.py` replaced with `logger.warning()`.
- [x] **No email confirmation on subscription change** - Added `_send_plan_change_email()` — sends branded Resend email on plan activation and cancellation.

### Blog

- [x] **Blog posts are static placeholder cards** - Replaced with 5 real inline articles (tutorial, tips, security, guide, FAQ) with actual content. Removed dead `href="#"` links and unused category filter UI.

### Frontend / UX

- [x] **Two different UI systems** - Deleted orphaned `index.html`; only `home.html` (via `base.html`) remains.
- [x] **No account/dashboard page** - Added `/account` with plan, usage, and conversion history.
- [x] **Flash messages missing on signin page** - Fixed (see auth section).
- [x] **Mobile nav accessibility** - Added Escape-to-close, click-outside-to-close, and auto-focus on first link when menu opens.

### Backend / Infrastructure

- [x] **`app.py` is 1100+ lines** - Split into Flask Blueprints: `routes/auth.py` (254), `routes/billing.py` (277), `routes/converter.py` (280), `routes/pages.py` (212). `app.py` down to 407 lines (shared config + utilities). All 83 tests pass.
- [x] **`print()` statements scattered** - Replaced all `print()` with `logger.warning()` in `app.py`.
- [x] **Health check doesn't verify Redis/Celery** - `/health/detailed` now pings Redis and returns `degraded` (503) if any check fails.
- [x] **No file cleanup job for expired S3 files** - Added `cleanup_expired_s3_results()` with configurable `S3_RESULT_RETENTION_HOURS` (default 24h). Runs hourly from home route.
- [x] **Version hardcoded as `1.0.0`** - Now reads from `APP_VERSION` env var (defaults to `dev`).

### Testing

- [x] **Minimal test coverage** - Added `tests/test_routes.py` (21 tests: public routes, auth, protected routes, Stripe webhook) and `tests/test_email.py` (5 tests: magic link + plan change emails). Total 26 tests, all passing.
- [x] **No integration tests for email** - Added `tests/test_email.py` with mocked Resend tests for magic link and plan change emails.
- [x] **`*_checks.py` scripts aren't integrated with pytest** - Renamed to `test_*.py` for pytest auto-discovery. Also fixed 4 broken tests in `test_parsers.py` (stale API references). Full suite: 83 tests, all passing.

---

## P2 - Nice to Have

- [ ] **Multi-file upload / batch processing** - Upload and convert multiple PDFs at once.
- [ ] **Conversion history retention** - Let users re-download past conversions (currently files deleted after download).
- [ ] **Password-protected PDF support** - Common for bank statements; currently these fail silently.
- [ ] **Non-English statement support** - Currently gated by `ENGLISH_ONLY_BETA` flag.
- [ ] **Rate limiting feedback to user** - When rate limited, show remaining cooldown time.
- [ ] **Admin dashboard improvements** - Currently minimal (job count + recent jobs). Could show conversion success rate, error breakdown, revenue metrics.
- [ ] **Run full 128-page HDFC benchmark on Railway** - Record production baseline on real hardware.

---

## Recently Completed

- [x] Stripe billing + subscription management
- [x] Template-based extraction (LLM calls N to 1)
- [x] Structured logging + Claude Haiku for templates
- [x] Progress bar fixes, debit/credit inference
- [x] Universal parser header-hint layer
- [x] Resend email integration (replaced SES)
