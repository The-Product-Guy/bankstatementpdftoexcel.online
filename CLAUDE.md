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
Browser polls /status/<job_id> or receives WebSocket updates via Flask-SocketIO
```

### Two-Process Model
The app runs as **two separate processes** sharing Redis + Postgres:
- **Web** (`app.py`): Flask + Gunicorn threads + simple-websocket — handles HTTP, WebSocket, auth, Stripe webhooks. No OCR models loaded.
- **Worker** (`worker.py`): Celery with `solo` pool — runs PDF extraction. Loads PaddleOCR/ONNX (~2-3 GB RAM). Must use `--pool=solo` because native C++ libs (PaddleOCR, OpenCV, ONNX Runtime) crash with `fork()`.

In production (Railway), these are separate services (`SERVICE_ROLE=web` vs `SERVICE_ROLE=worker`) configured in `entrypoint.sh`. Scale by adding worker replicas.

### Parser Pipeline (`parsers/`)

**Product direction (2026-07): the Excel output is an exact copy of the PDF** — the bank's own rows and columns, nothing silently dropped. Users delete unwanted content themselves. No normalization.

**Default mode — `layout_replica`** (`layout_replica_parser.py`, see `docs/superpowers/specs/2026-07-03-exact-copy-extraction-design.md`):
1. Extract words with coordinates (pdfplumber for text PDFs; PaddleOCR for scans, multi-token OCR boxes split proportionally per token)
2. Group words into visual lines; detect the bank's header row; derive column x-ranges (header-detected columns always outrank positional inference)
3. One Excel row per PDF table row — wrapped continuation lines merge into their parent row's cells
4. Workbook = `sheet1` (the table, bank's own headers) + `Full_Text` (every visual line of every page — the no-loss invariant: every extracted line appears in the workbook at least once)

**Dormant mode — `structured_transactions`** (`universal_parser.py`, env-var only, no UI): the old normalize-to-9-columns cascade (bank profiles → template detection via Claude Haiku → img2table+PaddleOCR → LLM fallback). Produced unusable output on scanned statements; kept for reference, do not extend. img2table was also evaluated as a replica engine and rejected on evidence (missed pages, split wraps, column misassignment).

For large files (≥80 pages), `chunk_utils.py` splits into 40-page chunks processed independently then merged (structured mode only).

### Key Modules
- `app.py`: Flask app setup, shared config/utilities, context processors, middleware. Routes split into Blueprints:
  - `routes/auth.py`: signin, magic link, verify, signout, account
  - `routes/billing.py`: Stripe checkout, webhooks, billing portal
  - `routes/converter.py`: `/dashboard` (auth-gated converter UI), PDF upload, status polling, download, feedback
  - `routes/pages.py`: public pages, SEO (sitemap/robots), health checks, admin
- `worker.py`: `process_pdf_task` Celery task — downloads PDF from S3, runs the layout replica parser, uploads the workbook (table sheet + `Full_Text`)
- `models.py`: SQLAlchemy models — User, AuthToken, Job, UsageCounter, FeedbackSubmission
- `db.py`: Engine/session management. Falls back to `sqlite:///local.db` when `DATABASE_URL` is unset
- `storage_utils.py`: Thin S3 wrapper (upload/download/presign/delete)
- `celery_config.py`: Celery setup with Redis broker, solo pool default, JSON serialization

### Execution Presets
Configured via `EXECUTION_PRESET` env var (defined in `universal_parser.py`):
- `local-low-mem`: No OCR, no LLM — for local dev on low-memory machines
- `prod-balanced`: PaddleOCR + img2table at 150 DPI (default in production)
- `prod-high-accuracy`: 200 DPI for poor quality scans

### Environment
Copy `.env.example` to `.env`. Key variables: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `REDIS_URL`, `DATABASE_URL`, `SECRET_KEY`. For local dev without cloud services, the app falls back to SQLite and local file storage.

## Conventions

- Branding: "Statement Converter" by "Ambion Softwares". Logo at `static/ambion-logo.svg` (navy-to-blue gradient)
- Frontend uses Inter font (Google Fonts), inline SVG icons (no Font Awesome), light professional theme
- Conversion is auth-gated: homepage is marketing-only, converter lives at `/dashboard` (requires login)
- Auth is magic-link email via Resend SDK (no passwords)
- Plans: free (5 conversions/month), pro (50), enterprise (unlimited) — managed via Stripe subscriptions
- Worker communicates progress to web via Redis keys; web pushes to browser via SocketIO
- Output format is the PDF's own table: the bank's header row becomes the Excel header, one row per PDF table row, all values as strings; `Full_Text` sheet carries every visual line. (The standardized 9-column format only exists in the dormant `structured_transactions` mode.)
- The `tools/` directory contains standalone diagnostic/debugging scripts (not imported by the app)
