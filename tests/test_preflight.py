import json
from pathlib import Path
import subprocess

from hami_tail_pilot.preflight import run_preflight, validate_smoke_probes
from hami_tail_pilot.probe import ProbeMetrics
from hami_tail_pilot.runner import RuntimeAssets


EXPECTED_MANIFEST = {
    "hami_release": "v2.9.0",
    "hami_commit": "3a006c6ae2f077a2683df7805c43656c07f6dc15",
    "hami_core_commit": "5091a2fbe1816df1265490f771346730f29e2c8d",
    "deep_learning_examples_commit": "b03375bd6c2c5233130e61a3be49e26d1a20ac7c",
    "mlperf_release": "v5.1.1",
    "mlperf_commit": "6776245e99dce0600cfc9a6fb61efd310f87de3d",
}


def make_inputs(tmp_path):
    model = tmp_path / "model.pytorch"
    dataset = tmp_path / "dev-v1.1.json"
    vocab = tmp_path / "vocab.txt"
    for path in (model, dataset, vocab):
        path.write_text(path.name, encoding="utf-8")
    manifest = tmp_path / "source_manifest.json"
    manifest.write_text(json.dumps(EXPECTED_MANIFEST), encoding="utf-8")
    return RuntimeAssets("hami-tail-bert:test", model, dataset, vocab), manifest


class CommandResults:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}

    def __call__(self, command, **_kwargs):
        key = tuple(command)
        if key in self.overrides:
            return self.overrides[key]
        if command[:2] == ["docker", "version"]:
            return subprocess.CompletedProcess(command, 0, "27.1.1\n", "")
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "sha256:image123\n", "")
        if command[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(command, 0, "torch=2.5.1 cuda=True\n", "")
        if "--query-gpu=name,uuid,driver_version" in command:
            return subprocess.CompletedProcess(command, 0, "NVIDIA A100, GPU-123, 550.54\n", "")
        if "--query-compute-apps=pid,process_name,gpu_uuid" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected command: {command}")


def failed(command, stderr):
    return subprocess.CompletedProcess(command, 1, "", stderr)


def test_run_preflight_accepts_pinned_idle_gpu_environment(tmp_path):
    assets, manifest = make_inputs(tmp_path)

    report = run_preflight(assets, manifest, tmp_path / "new-experiment", runner=CommandResults())

    assert report.ready is True
    assert report.errors == ()
    assert report.environment["gpu"] == "NVIDIA A100, GPU-123, 550.54"
    assert report.environment["image_digest"] == "sha256:image123"


def test_run_preflight_rejects_an_unavailable_docker_server(tmp_path):
    assets, manifest = make_inputs(tmp_path)
    command = ("docker", "version", "--format", "{{.Server.Version}}")

    report = run_preflight(
        assets,
        manifest,
        tmp_path / "experiment",
        runner=CommandResults({command: failed(command, "daemon unavailable")}),
    )

    assert report.ready is False
    assert "Docker server is unavailable" in report.errors


def test_run_preflight_rejects_other_compute_processes(tmp_path):
    assets, manifest = make_inputs(tmp_path)
    command = (
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,gpu_uuid",
        "--format=csv,noheader,nounits",
    )
    busy = subprocess.CompletedProcess(command, 0, "3333, python, GPU-123\n", "")

    report = run_preflight(
        assets,
        manifest,
        tmp_path / "experiment",
        runner=CommandResults({command: busy}),
    )

    assert report.ready is False
    assert "GPU has existing compute processes" in report.errors


def test_run_preflight_rejects_source_pin_drift_and_existing_complete_experiment(tmp_path):
    assets, manifest = make_inputs(tmp_path)
    payload = dict(EXPECTED_MANIFEST)
    payload["mlperf_commit"] = "wrong"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    (experiment / "status.json").write_text('{"status":"complete"}', encoding="utf-8")

    report = run_preflight(assets, manifest, experiment, runner=CommandResults())

    assert report.ready is False
    assert "source manifest does not match pinned commits" in report.errors
    assert "experiment directory is already complete" in report.errors


def test_run_preflight_rejects_missing_model_data(tmp_path):
    assets, manifest = make_inputs(tmp_path)
    assets.model_file.unlink()

    report = run_preflight(assets, manifest, tmp_path / "experiment", runner=CommandResults())

    assert report.ready is False
    assert any("model.pytorch" in error for error in report.errors)


def test_validate_smoke_probes_accepts_expected_waits_for_each_role():
    no_wait = ProbeMetrics(100, 0, 0, 0)
    with_wait = ProbeMetrics(100, 5, 10, 100_000_000)

    probes = {
        "C1": {"victim": no_wait},
        "C2": {"victim": with_wait},
        "C3": {"victim": no_wait, "neighbor": no_wait},
        "C4": {"victim": with_wait, "neighbor": no_wait},
        "C5": {"victim": with_wait, "neighbor": with_wait},
    }

    assert validate_smoke_probes(probes) == ()


def test_validate_smoke_probes_reports_wrong_manipulation_direction():
    no_wait = ProbeMetrics(100, 0, 0, 0)
    with_wait = ProbeMetrics(100, 5, 10, 100_000_000)

    probes = {
        "C1": {"victim": with_wait},
        "C2": {"victim": no_wait},
        "C3": {"victim": no_wait, "neighbor": no_wait},
        "C4": {"victim": with_wait, "neighbor": with_wait},
        "C5": {"victim": with_wait, "neighbor": with_wait},
    }
    errors = validate_smoke_probes(probes)

    assert "C1 victim recorded unexpected waits" in errors
    assert "C2 victim did not record expected waits" in errors
    assert "C4 neighbor recorded unexpected waits" in errors
