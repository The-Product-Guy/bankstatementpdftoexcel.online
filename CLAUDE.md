# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**Statement Converter** by **Ambion Softwares** — a web app that converts bank statement PDFs (text-based and scanned) into Excel files using a universal parser. Deployed on Railway with Stripe billing.

## Development Commands

### Quick Start (recommended)
```bash
./run_local.sh  # Installs deps, starts Redis, Celery worker, and Flask app
```

### Manual Start (3 terminals)
```bash
redis-server                                                    # Terminal 1
celery -A celery_config.celery_app worker --loglevel=info --pool=solo  # Terminal 2
python app.py                                                   # Terminal 3
```
App runs at http://localhost:5001.

### Tests
```bash
python -m pytest tests/ -v                          # Full test suite
python -m pytest tests/test_routes.py -v            # Route tests
python -m pytest tests/test_email.py -v             # Email tests
python -m pytest tests/test_parsers.py -v           # Parser unit tests
python -m pytest tests/test_parsers.py::TestImagePreprocessor::test_pil_to_cv2_rgb -v  # Single test
python test_universal.py                            # Integration test with a sample PDF
```

### Benchmarking
```bash
python tests/evaluation/run_benchmark.py            # Accuracy benchmark against test data
python tools/evaluate_extraction.py                 # Evaluate extraction quality
python tools/run_accuracy_gates.py --dry-run        # Show configured accuracy gate commands
```

## Architecture

### Request Flow
```
Browser → Flask (app.py) → /convert POST → creates Job in DB → dispatches Celery task
Celery worker (worker.py) → process_pdf_task → LayoutReplicaParser (default) → Excel (table + Full_Text) → S3 → presigned URL
Browser polls the authenticated /status/<job_id> endpoint for Redis-backed progress
```

### Service Model
The app runs as separate services sharing Redis + Postgres:
- **Web** (`app.py`): Flask + Gunicorn threads — handles HTTP, auth, Stripe webhooks, and status polling. No OCR models loaded.
- **Worker** (`worker.py`): Celery with `solo` pool — runs PDF extraction. Loads RapidOCR/ONNX for scanned pages and falls back to Tesseract. Use `--pool=solo` because native OpenCV/ONNX libraries are unsafe to initialise across forks.
- **Scheduler** (`celery beat`): exactly one replica enqueues hourly retention maintenance.

In production (Railway), these are separate services (`SERVICE_ROLE=web`, `worker`, or `scheduler`) configured in `entrypoint.sh`. Scale by adding worker replicas, never scheduler replicas.

### Parser Pipeline (`parsers/`)

**Product direction (2026-08): preserve before interpreting.** `Exact_Copy` writes every successfully extracted word in visual row order with approximate page geometry. `Table_Data` is a best-effort convenience view and must never silently truncate an earlier row when a later schema changes. Users clean the workbook themselves and verify OCR output against the PDF.

**Default mode — `layout_replica`** (`layout_replica_parser.py`, see `docs/superpowers/specs/2026-07-03-exact-copy-extraction-design.md`):
1. Extract words with coordinates (pdfplumber for text PDFs; RapidOCR/ONNX for scans with Tesseract fallback). Sparse text layers are supplemented with OCR instead of suppressing the visible page.
2. Group words into visual lines; detect table headers and geometry; carry a stable schema across continuation pages and isolate materially different schemas.
3. Build a best-effort table view without normalising values. Wrapped OCR fragments may merge into their parent row, but source visual lines remain separate in the fidelity sheets.
4. Workbook = `Exact_Copy` first (all successfully extracted words in visual order/approximate geometry), one or more `Table_Data` sheets, and `Full_Text` (every extracted visual line).

**Dormant mode — `structured_transactions`** (`universal_parser.py`, internal only, no public route): the old normalize-to-9-columns cascade (bank profiles → template detection → optional img2table/PaddleOCR → LLM fallback). It produced unusable output on scanned statements and is kept only for reference; do not extend it. Its optional heavy dependencies are not installed by the public production image.

For large files (≥80 pages), `chunk_utils.py` splits into 40-page chunks processed independently then merged (structured mode only).

### Key Modules
- `app.py`: Flask app setup, shared config/utilities, context processors, middleware. Routes split into Blueprints:
  - `routes/auth.py`: signin, magic link, verify, signout, account
  - `routes/billing.py`: Stripe checkout, webhooks, billing portal
  - `routes/converter.py`: public `/dashboard` sign-in shell; verified-login-only upload/preflight; ownership-protected status, download, and feedback
  - `routes/pages.py`: public pages, SEO (sitemap/robots), health checks, admin
- `worker.py`: `process_pdf_task` Celery task — downloads PDF from S3, runs the layout replica parser, uploads the workbook, and removes non-retained inputs
- `models.py`: SQLAlchemy models — User, AuthToken, Job, UsageCounter, FeedbackSubmission
- `db.py`: Engine/session management. Falls back to `sqlite:///local.db` when `DATABASE_URL` is unset
- `site_urls.py`: Public base URL for canonical/og/sitemap/robots URLs — normalizes `CANONICAL_BASE_URL`/`PUBLIC_BASE_URL` to an absolute `https://` origin (scheme-less env values once broke all crawler-facing URLs; see Semrush section in `docs/Launch-SEO-Readiness.md`)
- `storage_utils.py`: Thin S3 wrapper (upload/download/presign/delete)
- `celery_config.py`: Celery setup with Redis broker, solo pool default, JSON serialization

### Execution Presets
Configured via `EXECUTION_PRESET` env var (defined in `universal_parser.py`):
- `local-low-mem`: conservative optional integrations for local development
- `prod-balanced`: 150 DPI layout extraction with RapidOCR/ONNX available for scans
- `prod-high-accuracy`: 200 DPI retry for poor-quality scans

### Environment
Copy `.env.example` to `.env`. Key variables: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `REDIS_URL`, `DATABASE_URL`, `SECRET_KEY`, `RESEND_API_KEY`, and `RESEND_FROM_EMAIL`. For local dev without cloud services, the app falls back to SQLite and local file storage. Production also needs `CANONICAL_BASE_URL=https://multistatementpdftoexcel.online` (absolute, with scheme) — it drives every crawler-facing URL and emailed magic link. The live domain is Cloudflare-proxied; Cloudflare's managed robots.txt prepends AI-bot blocks the app cannot override.

For the production Cloudflare-to-Railway trust boundary, set a 32+ character `CLOUDFLARE_ORIGIN_SECRET` in Railway and configure Cloudflare to **overwrite** `X-Statement-Origin` with the same value on the proxied hostname. Keep `CLOUDFLARE_PROXY_ENABLED=false` until that rule is live, then enable it in Railway. Production requests without the verified Cloudflare header return `421`; `/health` and `/health/detailed` are intentionally exempt for Railway checks. Never enable the Railway flag before the Cloudflare rule. Magic-link controls default to 10 requests/IP and 5 requests/normalized email per 900 seconds, with a warning at 5 distinct emails/IP per 86400 seconds. The conversion limit remains independent at 15/IP/hour.

## Conventions

- Branding: "Statement Converter" by "Ambion Softwares". Logo at `static/ambion-logo.svg` (navy-to-blue gradient)
- Frontend uses Inter font (Google Fonts), inline SVG icons (no Font Awesome), light professional theme
- Conversion is auth-gated: homepage is marketing-only; `/dashboard` is a public sign-in shell and renders the upload interface only for a verified active user
- Auth is magic-link email via Resend SDK (no passwords)
- Plans: free (5 conversions/month), pro (50), enterprise (unlimited) — managed via Stripe subscriptions
- Worker communicates progress through expiring Redis JSON keys; the browser polls an ownership-protected HTTP endpoint
- Output format is the PDF's own table: the bank's header row becomes the Excel header, one row per PDF table row, all values as strings; `Full_Text` sheet carries every visual line. (The standardized 9-column format only exists in the dormant `structured_transactions` mode.)
- The `tools/` directory contains standalone diagnostic/debugging scripts (not imported by the app)
