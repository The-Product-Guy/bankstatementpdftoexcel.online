#!/usr/bin/env python3
"""Chunk orchestration utility helpers."""
from typing import Any, Dict, List, Optional, Tuple


def build_page_ranges(total_pages: int, chunk_size: int) -> List[Tuple[int, int]]:
    """Build 1-based inclusive page ranges for chunked processing."""
    if total_pages <= 0:
        return []
    if chunk_size <= 0:
        return [(1, total_pages)]

    ranges: List[Tuple[int, int]] = []
    start = 1
    while start <= total_pages:
        end = min(start + chunk_size - 1, total_pages)
        ranges.append((start, end))
        start = end + 1
    return ranges


def merge_raw_tables(
    base: Optional[Dict[str, Any]],
    incoming: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Merge two raw table dicts into a single table with aligned column widths."""
    if not incoming or not incoming.get("rows"):
        return base
    if not base:
        return {
            "columns": list(incoming.get("columns", [])),
            "rows": [list(r) for r in incoming.get("rows", [])],
        }

    base_columns = list(base.get("columns", []))
    base_rows = base.get("rows", [])
    target_cols = len(base_columns) if base_columns else len(incoming.get("columns", []))
    if target_cols <= 0:
        target_cols = max((len(r) for r in (incoming.get("rows") or [])), default=0)
    if target_cols <= 0:
        return base

    if not base_columns:
        base_columns = [f"Column_{i+1}" for i in range(target_cols)]
        base["columns"] = base_columns

    for row in incoming.get("rows", []):
        norm = list(row)
        while len(norm) < target_cols:
            norm.append("")
        if len(norm) > target_cols:
            norm = norm[:target_cols]
        base_rows.append(norm)

    base["rows"] = base_rows
    return base


def merge_quality_reports(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge chunk-level proxy quality reports into one weighted summary."""
    if not reports:
        return {}

    def _w(report: Dict[str, Any]) -> float:
        return float(report.get("row_count") or 1.0)

    total_weight = sum(_w(r) for r in reports)
    if total_weight <= 0:
        total_weight = float(len(reports))

    def _weighted(metric: str) -> float:
        return round(
            sum(float(r.get(metric, 0.0)) * _w(r) for r in reports) / total_weight,
            1
        )

    total_rows = int(sum(int(r.get("row_count") or 0) for r in reports))
    total_balance_checks = int(sum(int(r.get("balance_checks") or 0) for r in reports))
    total_balance_passed = int(sum(int(r.get("balance_checks_passed") or 0) for r in reports))
    balance_consistency = (
        round((total_balance_passed / total_balance_checks) * 100.0, 1)
        if total_balance_checks > 0 else _weighted("balance_consistency_pct")
    )

    return {
        "is_proxy": True,
        "chunk_orchestrated": True,
        "execution_preset": reports[0].get("execution_preset", "custom"),
        "row_count": total_rows,
        "date_parse_pct": _weighted("date_parse_pct"),
        "amount_coverage_pct": _weighted("amount_coverage_pct"),
        "balance_coverage_pct": _weighted("balance_coverage_pct"),
        "balance_checks": total_balance_checks,
        "balance_checks_passed": total_balance_passed,
        "balance_consistency_pct": balance_consistency,
        "accuracy_proxy_pct": _weighted("accuracy_proxy_pct"),
    }
