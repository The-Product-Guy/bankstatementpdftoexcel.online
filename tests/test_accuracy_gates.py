from pathlib import Path


def test_accuracy_gate_config_loads_default_datasets():
    from tools.run_accuracy_gates import DEFAULT_CONFIG, load_config, select_datasets

    config = load_config(DEFAULT_CONFIG)
    selected = select_datasets(config, ["synthetic_canada"])

    assert len(selected) == 1
    assert selected[0]["name"] == "synthetic_canada"
    assert selected[0]["require_truth"] is True


def test_accuracy_gate_command_includes_dataset_controls(tmp_path):
    from tools.run_accuracy_gates import build_evaluate_command

    dataset = {
        "name": "synthetic_canada",
        "input_dir": "tests/data/synthetic",
        "execution_preset": "local-low-mem",
        "disable_paddle": True,
        "disable_img2table": True,
        "require_truth": True,
        "max_pages": 5,
        "min_true_accuracy": 95.0,
        "min_field_accuracy": 95.0,
        "min_balance_consistency": 95.0,
    }
    output_path = tmp_path / "synthetic.csv"

    cmd = build_evaluate_command(dataset, output_path)

    assert Path(cmd[1]).name == "evaluate_extraction.py"
    assert "--input-dir" in cmd
    assert "--output" in cmd
    assert str(output_path) in cmd
    assert "--execution-preset" in cmd
    assert "local-low-mem" in cmd
    assert "--disable-paddle" in cmd
    assert "--disable-img2table" in cmd
    assert "--require-truth" in cmd
    assert "--max-pages" in cmd
    assert "--min-true-accuracy" in cmd
    assert "--min-field-accuracy" in cmd
    assert "--min-balance-consistency" in cmd
