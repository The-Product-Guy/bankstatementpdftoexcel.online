# PDF to Excel Production Tracker

Last updated: 2026-02-07

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

## Phase 4 - Selective AI Fallback (Planned)
- [ ] Route only low-confidence segments to LLM (not full-document LLM extraction).
- [ ] Add max-token and timeout guards per LLM request.
- [ ] Persist row-level confidence reasons for auditability.

## Phase 5 - Release Gates (Planned)
- [ ] Define acceptance gates for true extraction accuracy by dataset.
- [ ] Define balance-consistency SLO and failure budget.
- [ ] Add CI job to fail builds when regression thresholds are breached.

## Benchmark Snapshots
- [x] Local bounded baseline generated: `reports/india_v1_accuracy_local_baseline.csv` (`local-low-mem`, first 20 pages/file, `--disable-paddle`, `--disable-img2table`).
