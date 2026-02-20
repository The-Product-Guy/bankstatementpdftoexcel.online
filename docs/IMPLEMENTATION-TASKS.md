# PDF to Excel Production Tracker

Last updated: 2026-02-19

## Phase 1 - Foundation (Completed)
- [x] Add parser quality report (proxy accuracy, balance consistency, coverage metrics).
- [x] Add canonical transaction derivation from extracted raw tables where possible.
- [x] Expose quality metrics to worker/job status payload.
- [x] Support dual-sheet Excel output (`Raw_Statement` + `Normalized_Transactions`).
- [x] Add file-by-file evaluation script with truth accuracy + proxy quality reporting.

## Phase 2 - Runtime Controls (Completed)
- [x] Add runtime switch to enable/disable `img2table` (`USE_IMG2TABLE`).
- [x] Add evaluator flags for `--disable-paddle` and `--disable-img2table`.
- [x] Add profile-based execution presets (`local-low-mem`, `prod-balanced`, `prod-high-accuracy`).
- [x] Add chunk-level worker orchestration for very large statements.
- [x] Move chunk merge/page-range logic into shared helper module (`parsers/chunk_utils.py`).
- [x] Add regression checks for execution presets and chunk orchestration page windows.
- [x] Update evaluator `--max-pages` to sample first N pages (instead of hard page-limit failure).
- [ ] Run full-file benchmark for 128-page HDFC statement on Railway worker hardware and record baseline.

## Phase 3 - Bank Profile System (In Progress)
- [x] Create bank profile module (`HDFC`, `KVB`) with filename/text/header signatures.
- [x] Integrate profile detection at parse-time.
- [x] Apply profile-aware header aliases before generic mapping.
- [x] Extend header keyword detection with profile tokens.
- [x] Add profile stats logging (matched/unknown count) to evaluation reports.
- [x] Add first round of profile regression fixtures (header mapping unit tests).

## Phase 4 - Selective AI Fallback (In Progress)
- [x] Route only low-confidence page segments to fallback scope (avoid full-document fallback when confidence is weak).
- [ ] Add max-token and timeout guards per LLM request.
- [x] Persist row-level confidence reasons for auditability.
- [x] Add generic bordered-table extraction path (grid + OCR row clustering) for image PDFs.
- [x] Add blob-output rejection gate so page-level concatenation does not pass as valid extraction.

## Phase 5 - Release Gates (Planned)
- [ ] Define acceptance gates for true extraction accuracy by dataset.
- [ ] Define balance-consistency SLO and failure budget.
- [ ] Add CI job to fail builds when regression thresholds are breached.

## Benchmark Snapshots
- [x] Local bounded baseline generated: `reports/india_v1_accuracy_local_baseline.csv` (`local-low-mem`, first 20 pages/file, `--disable-paddle`, `--disable-img2table`).

## Hotfixes
- [x] Worker stability patch: force safe runtime mode (`use_paddleocr=false`, `use_img2table=false`) when PaddleOCR warmup fails in production.
- [x] Skip `img2table` path in parser when PaddleOCR backend is unavailable (avoid heavy tesseract fallback inside img2table).
- [x] Pin Celery worker to `--concurrency=1` and cap native thread env vars in `entrypoint.sh`.
- [x] Enforce transaction ordering by `Page_Line` before writing Excel to preserve page sequence.
- [x] Make img2table merge deterministic by sorting page indices before row consolidation.
- [x] Add regression test for page-order sorting (`tests/chunking_checks.py`).
- [x] Add English-only beta language guard in parser (`ENGLISH_ONLY_BETA`).
- [x] Add row/cell confidence fields and low-confidence ratio summary to extraction output.
- [x] Add beta banner and explicit redaction + retention messaging in feedback UI.
- [x] Add feedback shared-PDF retention cleanup sweep (`FEEDBACK_RETENTION_DAYS`).
- [x] Add right-edge bordered-column fallback to retain balance column when final vertical line is faint.
- [x] Improve mixed-column parsing (date+description and debit/credit in merged amount cell).
