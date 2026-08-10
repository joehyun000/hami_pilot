from hami_tail_pilot.cli import main
from hami_tail_pilot.calibration import CalibrationExecution
from hami_tail_pilot.preflight import PreflightReport
from hami_tail_pilot.smoke import SmokeResult
import json
from pathlib import Path
import subprocess
import sys

import yaml


def test_cli_without_command_prints_help(capsys):
    assert main([]) == 2
    assert "validate" in capsys.readouterr().out


def test_schedule_command_writes_the_approved_thirty_run_schedule(tmp_path):
    output = tmp_path / "schedule.json"

    assert (
        main(["schedule", "--config", "configs/pilot.yaml", "--output", str(output)])
        == 0
    )
    assert output.is_file()
    assert output.read_text(encoding="utf-8").count('"run_id"') == 30


def test_python_module_entrypoint_executes_the_requested_command():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hami_tail_pilot.cli",
            "validate",
            "--config",
            "configs/pilot.yaml",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "valid pilot config" in result.stdout


def test_calibrate_command_writes_resolved_qps_and_probe_overhead(tmp_path):
    summary = Path("tests/fixtures/mlperf_log_summary.txt").resolve()
    measurements = tmp_path / "calibration.json"
    measurements.write_text(
        json.dumps(
            {
                "candidates_by_condition": {
                    condition: [
                        {"target_qps": qps, "summary": str(summary)}
                        for qps in (1, 2, 4, 8)
                    ]
                    for condition in ("C2", "C4", "C5")
                },
                "probe_off_p99_ms": [100, 200, 400],
                "probe_on_p99_ms": [104, 204, 416],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "calibration-output"

    assert (
        main(
            [
                "calibrate",
                "--measurements",
                str(measurements),
                "--config",
                "configs/pilot.yaml",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    decision = json.loads(
        (output / "calibration_decision.json").read_text(encoding="utf-8")
    )
    assert decision["target_qps"] == 5.6
    assert decision["probe_overhead_ratio"] == 1.04
    resolved = yaml.safe_load(
        (output / "pilot.resolved.yaml").read_text(encoding="utf-8")
    )
    assert resolved["victim_target_qps"] == 5.6


def test_calibrate_command_blocks_the_pilot_when_probe_overhead_exceeds_five_percent(
    tmp_path, capsys
):
    summary = Path("tests/fixtures/mlperf_log_summary.txt").resolve()
    measurements = tmp_path / "calibration.json"
    measurements.write_text(
        json.dumps(
            {
                "candidates_by_condition": {
                    condition: [{"target_qps": 8, "summary": str(summary)}]
                    for condition in ("C2", "C4", "C5")
                },
                "probe_off_p99_ms": [100, 100, 100],
                "probe_on_p99_ms": [106, 107, 108],
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "calibrate",
                "--measurements",
                str(measurements),
                "--config",
                "configs/pilot.yaml",
                "--output",
                str(tmp_path / "output"),
            ]
        )
        == 1
    )
    assert "probe overhead exceeds 5%" in capsys.readouterr().err


def test_calibrate_command_can_run_server_measurements_automatically(
    tmp_path, monkeypatch
):
    inputs = []
    for name in ("model.pt", "dataset.json", "vocab.txt"):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        inputs.append(path)
    preflight_tags = []
    captured = {}

    def fake_preflight(assets, source_manifest, experiment_root):
        preflight_tags.append(assets.image_tag)
        return PreflightReport(True, (), (), {"image_tag": assets.image_tag})

    def fake_execute(config, output, probe_assets, vanilla_assets, *, candidate_qps):
        captured["candidate_qps"] = candidate_qps
        captured["probe"] = probe_assets.image_tag
        captured["vanilla"] = vanilla_assets.image_tag
        return CalibrationExecution(2.8, 1.04, True)

    monkeypatch.setattr("hami_tail_pilot.cli.run_preflight", fake_preflight)
    monkeypatch.setattr("hami_tail_pilot.cli.execute_calibration", fake_execute)

    assert (
        main(
            [
                "calibrate",
                "--config",
                "configs/pilot.yaml",
                "--output",
                str(tmp_path / "output"),
                "--candidate-qps",
                "1",
                "2",
                "4",
                "8",
                "--model-file",
                str(inputs[0]),
                "--dataset-file",
                str(inputs[1]),
                "--vocab-file",
                str(inputs[2]),
            ]
        )
        == 0
    )

    assert len(preflight_tags) == 2
    assert captured["candidate_qps"] == [1.0, 2.0, 4.0, 8.0]
    assert captured["probe"].endswith("v2.9.0")
    assert captured["vanilla"].endswith("v2.9.0-vanilla")


def test_smoke_command_runs_preflight_and_manipulation_check(tmp_path, monkeypatch):
    inputs = []
    for name in ("model.pt", "dataset.json", "vocab.txt"):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        inputs.append(path)
    monkeypatch.setattr(
        "hami_tail_pilot.cli.run_preflight",
        lambda *args: PreflightReport(True, (), (), {"gpu": "fake"}),
    )
    called = {}

    def fake_smoke(config, output, assets):
        called["qps"] = config.victim_target_qps
        called["image"] = assets.image_tag
        return SmokeResult(True, ())

    monkeypatch.setattr("hami_tail_pilot.cli.execute_smoke", fake_smoke)
    resolved = tmp_path / "resolved.yaml"
    config = yaml.safe_load(Path("configs/pilot.yaml").read_text(encoding="utf-8"))
    config["victim_target_qps"] = 2.8
    resolved.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    assert (
        main(
            [
                "smoke",
                "--config",
                str(resolved),
                "--output",
                str(tmp_path / "output"),
                "--model-file",
                str(inputs[0]),
                "--dataset-file",
                str(inputs[1]),
                "--vocab-file",
                str(inputs[2]),
            ]
        )
        == 0
    )
    assert called == {
        "qps": 2.8,
        "image": "hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0",
    }
