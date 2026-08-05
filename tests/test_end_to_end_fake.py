import csv
import json
import os
from pathlib import Path

from hami_tail_pilot.cli import main
from hami_tail_pilot.preflight import EXPECTED_SOURCE_MANIFEST


def test_dry_run_creates_twenty_synthetic_runs_and_go_report(tmp_path):
    experiment = tmp_path / "fake-pilot"

    assert main(
        [
            "run",
            "--dry-run",
            "--config",
            "configs/pilot.yaml",
            "--output",
            str(experiment),
        ]
    ) == 0
    assert main(
        [
            "analyze",
            "--input",
            str(experiment),
            "--probe-overhead-ratio",
            "1.03",
        ]
    ) == 0

    with (experiment / "pilot_metrics.csv").open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 20
    assert {row["condition"] for row in rows} == {"P0", "P1", "P2", "P3"}
    assert all(row["synthetic"] == "true" for row in rows)
    decision = json.loads((experiment / "pilot_decision.json").read_text(encoding="utf-8"))
    assert decision["decision"] == "GO"


def test_dry_run_is_resumable_and_does_not_overwrite_complete_runs(tmp_path):
    experiment = tmp_path / "fake-pilot"
    command = [
        "run",
        "--dry-run",
        "--config",
        "configs/pilot.yaml",
        "--output",
        str(experiment),
    ]

    assert main(command) == 0
    first_manifest = next(experiment.glob("*/manifest.json"))
    before = first_manifest.read_text(encoding="utf-8")
    assert main(command) == 0

    assert first_manifest.read_text(encoding="utf-8") == before
    assert len(list(experiment.glob("*/manifest.json"))) == 20


def test_analyze_rejects_an_incomplete_twenty_run_set(tmp_path, capsys):
    experiment = tmp_path / "fake-pilot"
    assert main(
        ["run", "--dry-run", "--config", "configs/pilot.yaml", "--output", str(experiment)]
    ) == 0
    first_status = next(experiment.glob("*/status.json"))
    first_status.write_text('{"status":"failed","error":"fixture"}\n', encoding="utf-8")

    assert main(
        ["analyze", "--input", str(experiment), "--probe-overhead-ratio", "1.0"]
    ) == 1
    assert "expected 20 complete runs" in capsys.readouterr().err


def test_real_run_preflight_only_records_a_validated_environment(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = version ]; then echo 27.1.1; exit 0; fi\n"
        "if [ \"$1\" = image ]; then echo sha256:test; exit 0; fi\n"
        "if [ \"$1\" = run ]; then echo 'torch=2.5.1 cuda=True'; exit 0; fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    nvidia_smi = bin_dir / "nvidia-smi"
    nvidia_smi.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  *compute-apps*) exit 0 ;;\n"
        "  *) echo 'NVIDIA A100, GPU-123, 550.54'; exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    nvidia_smi.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])

    config = tmp_path / "pilot.yaml"
    config.write_text(
        Path("configs/pilot.yaml").read_text(encoding="utf-8").replace(
            "victim_target_qps: null", "victim_target_qps: 10.0"
        ),
        encoding="utf-8",
    )
    files = []
    for name in ("model.pytorch", "dev-v1.1.json", "vocab.txt"):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        files.append(path)
    manifest = tmp_path / "source_manifest.json"
    manifest.write_text(json.dumps(EXPECTED_SOURCE_MANIFEST), encoding="utf-8")
    output = tmp_path / "real-pilot"

    assert main(
        [
            "run",
            "--config",
            str(config),
            "--output",
            str(output),
            "--model-file",
            str(files[0]),
            "--dataset-file",
            str(files[1]),
            "--vocab-file",
            str(files[2]),
            "--source-manifest",
            str(manifest),
            "--image-tag",
            "hami-tail-bert:test",
            "--preflight-only",
        ]
    ) == 0
    environment = json.loads((output / "environment.json").read_text(encoding="utf-8"))
    assert environment["image_digest"] == "sha256:test"
    assert environment["gpu"] == "NVIDIA A100, GPU-123, 550.54"
