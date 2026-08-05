import json
from dataclasses import replace
from pathlib import Path
import sys

from hami_tail_pilot.config import load_config
from hami_tail_pilot.runner import RuntimeAssets, build_container_command, run_spec
from hami_tail_pilot.schedule import build_schedule


FAKE = Path(__file__).parent / "fakes" / "fake_loadgen.py"


def config_with_qps():
    return replace(load_config(Path("configs/pilot.yaml")), victim_target_qps=12.5)


def spec_for(condition_name):
    return next(
        spec
        for spec in build_schedule(config_with_qps())
        if spec.condition.name == condition_name
    )


def assets(tmp_path):
    files = {}
    for name in ("model.pytorch", "dev-v1.1.json", "vocab.txt"):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        files[name] = path
    return RuntimeAssets(
        image_tag="hami-tail-bert:test",
        model_file=files["model.pytorch"],
        dataset_file=files["dev-v1.1.json"],
        vocab_file=files["vocab.txt"],
        gpu_index="0",
    )


def env_values(command):
    values = {}
    for index, token in enumerate(command):
        if token == "--env":
            key, value = command[index + 1].split("=", 1)
            values[key] = value
    return values


def test_build_container_command_uses_independent_role_cache_and_force_quota(tmp_path):
    run_dir = tmp_path / "b01-o01-P3"
    run_dir.mkdir()

    victim = build_container_command("victim", spec_for("P3"), config_with_qps(), run_dir, assets(tmp_path))
    neighbor = build_container_command("neighbor", spec_for("P3"), config_with_qps(), run_dir, assets(tmp_path))

    victim_env = env_values(victim)
    neighbor_env = env_values(neighbor)
    assert victim_env["CUDA_DEVICE_SM_LIMIT"] == "50"
    assert victim_env["GPU_CORE_UTILIZATION_POLICY"] == "force"
    assert neighbor_env["CUDA_DEVICE_SM_LIMIT"] == "50"
    assert victim_env["CUDA_DEVICE_MEMORY_SHARED_CACHE"] == "/hami-cache/victim.cache"
    assert neighbor_env["CUDA_DEVICE_MEMORY_SHARED_CACHE"] == "/hami-cache/neighbor.cache"
    assert victim_env["HAMI_WARMUP_SECONDS"] == "60"
    assert victim_env["HAMI_PROBE_OUTPUT"] == "/output/hami_probe.jsonl"
    assert "hami-tail-bert:test" in victim


def test_build_container_command_keeps_same_preload_path_without_force_at_limit_100(tmp_path):
    run_dir = tmp_path / "b01-o01-P0"
    run_dir.mkdir()

    command = build_container_command("victim", spec_for("P0"), config_with_qps(), run_dir, assets(tmp_path))
    environment = env_values(command)

    assert environment["CUDA_DEVICE_SM_LIMIT"] == "100"
    assert environment["LD_PRELOAD"] == "/opt/hami/libvgpu.so"
    assert "GPU_CORE_UTILIZATION_POLICY" not in environment


def fake_builder(*, ready=True, sleep=0.05, exit_code=0):
    def build(role, _spec, _config, run_dir, _assets):
        output = run_dir / role
        command = [
            sys.executable,
            str(FAKE),
            "--output",
            str(output),
            "--sleep",
            str(sleep if role == "neighbor" else 0.05),
            "--exit-code",
            str(exit_code if role == "victim" else 0),
        ]
        if ready:
            command.append("--ready")
        return command

    return build


def test_run_spec_completes_neighbor_then_victim_and_records_status(tmp_path):
    result = run_spec(
        spec_for("P3"),
        config_with_qps(),
        tmp_path,
        assets=assets(tmp_path),
        command_builder=fake_builder(sleep=2),
        ready_timeout_seconds=1,
        telemetry_command=None,
    )

    assert result.status == "complete"
    status = json.loads((result.run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "complete"
    assert (result.run_dir / "neighbor" / "pid").is_file()
    assert (result.run_dir / "victim" / "pid").is_file()


def test_run_spec_preserves_failure_when_neighbor_never_becomes_ready(tmp_path):
    result = run_spec(
        spec_for("P3"),
        config_with_qps(),
        tmp_path,
        assets=assets(tmp_path),
        command_builder=fake_builder(ready=False, sleep=2),
        ready_timeout_seconds=0.05,
        telemetry_command=None,
    )

    assert result.status == "failed"
    assert "neighbor ready timeout" in result.error
    assert json.loads((result.run_dir / "status.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_run_spec_preserves_nonzero_victim_exit(tmp_path):
    result = run_spec(
        spec_for("P0"),
        config_with_qps(),
        tmp_path,
        assets=assets(tmp_path),
        command_builder=fake_builder(exit_code=7),
        ready_timeout_seconds=1,
        telemetry_command=None,
    )

    assert result.status == "failed"
    assert "victim exited with code 7" in result.error


def test_run_spec_refuses_to_overwrite_an_existing_run_directory(tmp_path):
    run_dir = tmp_path / spec_for("P0").run_id
    run_dir.mkdir()

    result = run_spec(
        spec_for("P0"),
        config_with_qps(),
        tmp_path,
        assets=assets(tmp_path),
        command_builder=fake_builder(),
        telemetry_command=None,
    )

    assert result.status == "failed"
    assert result.error == "run directory already exists"
