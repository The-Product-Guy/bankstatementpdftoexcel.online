# StatementFlow - Pending Tasks

Last updated: 2026-03-08

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
- [ ] **Acceptance accuracy gates** (Phase 5) - Define true extraction accuracy thresholds by dataset.
- [ ] **Balance-consistency SLO** (Phase 5) - Define SLO and failure budget for balance checks.
- [ ] **CI regression gate** (Phase 5) - Add CI job to fail builds when accuracy regresses.

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

- [ ] **`app.py` is 1100+ lines** - Auth, billing, admin, conversion, health checks, SEO routes all in one file. Consider splitting into Flask Blueprints.
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
- [x] Bank profile system (20+ profiles)
- [x] Resend email integration (replaced SES)
