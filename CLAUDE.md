# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A web app that converts bank statement PDFs (text-based and scanned) into Excel files. Supports 20+ Indian banks with universal fallback. Deployed on Railway with Stripe billing.

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
python -m pytest tests/test_parsers.py -v          # Unit tests (image preprocessor, parsers)
python -m pytest tests/test_parsers.py::TestImagePreprocessor::test_pil_to_cv2_rgb -v  # Single test
python test_universal.py                            # Integration test with a sample PDF
```

### Other test/check scripts in `tests/`
Files named `*_checks.py` are standalone verification scripts (not pytest), run directly:
```bash
python tests/bank_profiles_checks.py
python tests/template_extraction_checks.py
```

### Benchmarking
```bash
python tests/evaluation/run_benchmark.py            # Accuracy benchmark against test data
python tools/evaluate_extraction.py                 # Evaluate extraction quality
```

## Architecture

### Request Flow
```
Browser → Flask (app.py) → /convert POST → creates Job in DB → dispatches Celery task
Celery worker (worker.py) → process_pdf_task → UniversalBankParser → Excel → S3 → presigned URL
Browser polls /status/<job_id> or receives WebSocket updates via Flask-SocketIO
```

### Two-Process Model
The app runs as **two separate processes** sharing Redis + Postgres:
- **Web** (`app.py`): Flask + Gunicorn/eventlet — handles HTTP, WebSocket, auth, Stripe webhooks. No OCR models loaded.
- **Worker** (`worker.py`): Celery with `solo` pool — runs PDF extraction. Loads PaddleOCR/ONNX (~2-3 GB RAM). Must use `--pool=solo` because native C++ libs (PaddleOCR, OpenCV, ONNX Runtime) crash with `fork()`.

In production (Railway), these are separate services (`SERVICE_ROLE=web` vs `SERVICE_ROLE=worker`) configured in `entrypoint.sh`. Scale by adding worker replicas.

### Parser Pipeline (`parsers/`)
The extraction pipeline in `universal_parser.py` follows this strategy cascade:
1. **Text extraction** (pdfplumber) — fast, free, works for text-based PDFs
2. **Bank profile detection** (`bank_profiles.py`) — matches filename/text signatures against 20+ known bank profiles, provides header aliases for better column mapping
3. **Template detection** (`template_extractor.py`) — uses Claude Haiku to detect column layout once from first 2 pages, reuses for all pages (reduces LLM calls from N to 1)
4. **img2table + PaddleOCR** (`img2table_extractor.py`) — for scanned PDFs: OpenCV/RLSA finds table structure, PaddleOCR reads cell text
5. **LLM fallback** (`llm_table_extractor.py`) — OpenAI for edge cases where heuristics fail

For large files (≥80 pages), `chunk_utils.py` splits into 40-page chunks processed independently then merged.

### Key Modules
- `app.py`: All Flask routes (auth, convert, billing, admin, health), SocketIO setup, Stripe integration
- `worker.py`: `process_pdf_task` Celery task — downloads PDF from S3, runs parser, builds Excel (2 sheets: transactions + summary), uploads result
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

- Auth is magic-link email via AWS SES (no passwords)
- Plans: free (5 conversions/month), pro (50), enterprise (unlimited) — managed via Stripe subscriptions
- Worker communicates progress to web via Redis keys; web pushes to browser via SocketIO
- Transaction output format is standardized: Date, Description, Reference_Number, Withdrawal_Amount, Deposit_Amount, Transaction_Amount, Closing_Balance, Source_File, Page_Line
- The `tools/` directory contains standalone diagnostic/debugging scripts (not imported by the app)
