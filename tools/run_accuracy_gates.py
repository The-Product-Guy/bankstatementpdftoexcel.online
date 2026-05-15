#!/usr/bin/env python3
"""
Run configured extraction accuracy gates.

This is a thin orchestrator over tools/evaluate_extraction.py so each dataset
can keep its own preset, sampling mode, and acceptance thresholds.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "tests" / "evaluation" / "accuracy_gates.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "accuracy_gates"


def resolve_project_path(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_config(config_path: Path) -> Dict[str, object]:
    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise ValueError("Accuracy gate config must be a JSON object")
    datasets = config.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("Accuracy gate config must contain a non-empty 'datasets' list")
    for dataset in datasets:
        if not isinstance(dataset, dict):
            raise ValueError("Each dataset entry must be an object")
        if not dataset.get("name"):
            raise ValueError("Each dataset entry must define 'name'")
        if not dataset.get("input_dir"):
            raise ValueError(f"Dataset {dataset['name']} must define 'input_dir'")
    return config


def select_datasets(config: Dict[str, object], names: Optional[Sequence[str]]) -> List[Dict[str, object]]:
    datasets = [
        dataset for dataset in config["datasets"]
        if isinstance(dataset, dict) and dataset.get("enabled", True)
    ]
    if not names:
        return datasets

    selected = [dataset for dataset in datasets if dataset.get("name") in set(names)]
    missing = sorted(set(names) - {str(dataset.get("name")) for dataset in selected})
    if missing:
        raise ValueError(f"Unknown dataset(s): {', '.join(missing)}")
    return selected


def dataset_output_path(dataset: Dict[str, object], output_dir: Path) -> Path:
    output_name = str(dataset.get("output") or f"{dataset['name']}.csv")
    return output_dir / output_name


def build_evaluate_command(dataset: Dict[str, object], output_path: Path) -> List[str]:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "evaluate_extraction.py"),
        "--input-dir",
        str(PROJECT_ROOT / str(dataset["input_dir"])),
        "--output",
        str(output_path),
    ]

    option_flags = {
        "execution_preset": "--execution-preset",
        "dpi": "--dpi",
        "max_pages": "--max-pages",
        "min_true_accuracy": "--min-true-accuracy",
        "min_field_accuracy": "--min-field-accuracy",
        "min_proxy_accuracy": "--min-proxy-accuracy",
        "min_balance_consistency": "--min-balance-consistency",
    }
    for key, flag in option_flags.items():
        value = dataset.get(key)
        if value not in (None, ""):
            cmd.extend([flag, str(value)])

    boolean_flags = {
        "use_llm": "--use-llm",
        "disable_paddle": "--disable-paddle",
        "disable_img2table": "--disable-img2table",
        "require_truth": "--require-truth",
    }
    for key, flag in boolean_flags.items():
        if dataset.get(key):
            cmd.append(flag)

    return cmd


def run_dataset(dataset: Dict[str, object], output_dir: Path, dry_run: bool) -> Dict[str, object]:
    output_path = dataset_output_path(dataset, output_dir)
    cmd = build_evaluate_command(dataset, output_path)
    result = {
        "name": dataset["name"],
        "output": str(output_path),
        "command": cmd,
        "returncode": 0,
    }
    if dry_run:
        result["dry_run"] = True
        return result

    output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(cmd, cwd=PROJECT_ROOT)
    result["returncode"] = completed.returncode
    return result


def write_summary(results: List[Dict[str, object]], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    return summary_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Accuracy gate config JSON")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for reports")
    parser.add_argument("--dataset", action="append", help="Run one named dataset; repeat to run multiple")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running extraction")
    args = parser.parse_args()

    config = load_config(resolve_project_path(args.config))
    datasets = select_datasets(config, args.dataset)
    output_dir = resolve_project_path(args.output_dir)

    results: List[Dict[str, object]] = []
    for dataset in datasets:
        print(f"\nDataset: {dataset['name']}")
        result = run_dataset(dataset, output_dir, args.dry_run)
        results.append(result)
        print("Command:", " ".join(result["command"]))
        if result.get("dry_run"):
            continue
        print(f"Exit code: {result['returncode']}")

    summary_path = write_summary(results, output_dir)
    print(f"\nSaved summary to {summary_path}")

    failed = [result for result in results if int(result.get("returncode", 0)) != 0]
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
