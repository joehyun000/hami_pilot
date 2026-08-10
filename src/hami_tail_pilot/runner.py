from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import IO

from hami_tail_pilot.config import PilotConfig
from hami_tail_pilot.mlperf import render_user_conf
from hami_tail_pilot.schedule import RunSpec


DEFAULT_IMAGE_TAG = "hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0"
DEFAULT_VANILLA_IMAGE_TAG = "hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0-vanilla"
DEFAULT_TELEMETRY_COMMAND = (
    "nvidia-smi",
    "--query-gpu=timestamp,index,utilization.gpu,memory.used,power.draw,clocks.sm",
    "--format=csv,noheader,nounits",
    "-lms",
    "200",
)


@dataclass(frozen=True)
class RuntimeAssets:
    image_tag: str
    model_file: Path
    dataset_file: Path
    vocab_file: Path
    gpu_index: str = "0"


@dataclass(frozen=True)
class RunResult:
    run_dir: Path
    status: str
    error: str | None


CommandBuilder = Callable[[str, RunSpec, PilotConfig, Path, RuntimeAssets], list[str]]


def _role_sm_limit(role: str, spec: RunSpec) -> int | None:
    if role == "victim":
        return spec.condition.victim_sm_limit
    if role == "neighbor" and spec.condition.neighbor_sm_limit is not None:
        return spec.condition.neighbor_sm_limit
    raise ValueError(f"role {role!r} is not enabled for {spec.condition.name}")


def build_container_command(
    role: str,
    spec: RunSpec,
    config: PilotConfig,
    run_dir: Path,
    assets: RuntimeAssets,
) -> list[str]:
    if config.victim_target_qps is None:
        raise ValueError(
            "victim_target_qps must be resolved before building a run command"
        )
    if role not in {"victim", "neighbor"}:
        raise ValueError("role must be victim or neighbor")

    sm_limit = _role_sm_limit(role, spec)
    role_dir = (run_dir / role).resolve()
    cache_dir = (run_dir / "hami-cache").resolve()
    user_conf = (role_dir / "user.conf").resolve()
    environment = {
        "HAMI_READY_FILE": "/output/ready",
        "HAMI_WARMUP_SECONDS": str(config.warmup_seconds),
        "LOG_PATH": "/output",
        "ML_MODEL_FILE_WITH_PATH": "/inputs/model.pytorch",
        "DATASET_FILE": "/inputs/dev-v1.1.json",
        "VOCAB_FILE": "/inputs/vocab.txt",
    }
    if spec.condition.hami_enabled:
        if sm_limit is None:
            raise ValueError(f"HAMi-enabled role has no SM limit: {role}")
        environment.update(
            {
                "CUDA_DEVICE_SM_LIMIT": str(sm_limit),
                "CUDA_DEVICE_MEMORY_SHARED_CACHE": f"/hami-cache/{role}.cache",
                "HAMI_PROBE_OUTPUT": "/output/hami_probe.jsonl",
                "LD_PRELOAD": "/opt/hami/libvgpu.so",
            }
        )
        if sm_limit < 100:
            environment["GPU_CORE_UTILIZATION_POLICY"] = "force"
    else:
        environment["LD_PRELOAD"] = ""

    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        f"hami-tail-{spec.run_id}-{role}",
        "--gpus",
        f"device={assets.gpu_index}",
        "--ipc=host",
        "--volume",
        f"{role_dir}:/output",
        "--volume",
        f"{cache_dir}:/hami-cache",
        "--volume",
        f"{assets.model_file.resolve()}:/inputs/model.pytorch:ro",
        "--volume",
        f"{assets.dataset_file.resolve()}:/inputs/dev-v1.1.json:ro",
        "--volume",
        f"{assets.vocab_file.resolve()}:/inputs/vocab.txt:ro",
        "--volume",
        f"{user_conf}:/config/user.conf:ro",
    ]
    for key, value in environment.items():
        command.extend(("--env", f"{key}={value}"))
    command.extend(
        (
            assets.image_tag,
            "--backend=pytorch",
            "--scenario=Server",
            "--user_conf=/config/user.conf",
        )
    )
    return command


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _start_process(
    command: Sequence[str], output_dir: Path, handles: list[IO[str]]
) -> subprocess.Popen:
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout = (output_dir / "stdout.log").open("w", encoding="utf-8")
    stderr = (output_dir / "stderr.log").open("w", encoding="utf-8")
    handles.extend((stdout, stderr))
    return subprocess.Popen(list(command), stdout=stdout, stderr=stderr, text=True)


def _stop_process(
    process: subprocess.Popen | None, timeout_seconds: float = 2.0
) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout_seconds)


def _wait_until_ready(
    process: subprocess.Popen, ready_file: Path, timeout_seconds: float
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if ready_file.is_file():
            return
        code = process.poll()
        if code is not None:
            raise RuntimeError(f"neighbor exited before ready with code {code}")
        time.sleep(min(0.02, timeout_seconds / 5))
    raise RuntimeError("neighbor ready timeout")


def run_spec(
    spec: RunSpec,
    config: PilotConfig,
    root: Path,
    *,
    assets: RuntimeAssets,
    command_builder: CommandBuilder = build_container_command,
    ready_timeout_seconds: float = 180.0,
    telemetry_command: Sequence[str] | None = DEFAULT_TELEMETRY_COMMAND,
) -> RunResult:
    run_dir = root / spec.run_id
    if run_dir.exists():
        return RunResult(run_dir, "failed", "run directory already exists")

    run_dir.mkdir(parents=True)
    (run_dir / "hami-cache").mkdir()
    for role in ("victim", "neighbor"):
        (run_dir / role).mkdir()

    manifest = {
        "run_id": spec.run_id,
        "block": spec.block,
        "order": spec.order,
        "condition": spec.condition.name,
        "status": "planned",
    }
    _write_json_atomic(run_dir / "manifest.json", manifest)
    _write_json_atomic(run_dir / "status.json", {"status": "planned", "error": None})

    neighbor: subprocess.Popen | None = None
    telemetry: subprocess.Popen | None = None
    handles: list[IO[str]] = []
    error: str | None = None
    try:
        if config.victim_target_qps is None:
            raise RuntimeError("victim_target_qps is unresolved")
        victim_conf = render_user_conf(
            config.victim_target_qps,
            config.victim_duration_seconds * 1000,
        )
        (run_dir / "victim" / "user.conf").write_text(victim_conf, encoding="utf-8")
        neighbor_conf = render_user_conf(
            config.victim_target_qps,
            config.neighbor_duration_seconds * 1000,
        )
        (run_dir / "neighbor" / "user.conf").write_text(neighbor_conf, encoding="utf-8")

        manifest["status"] = "running"
        _write_json_atomic(run_dir / "manifest.json", manifest)
        _write_json_atomic(
            run_dir / "status.json", {"status": "running", "error": None}
        )

        if telemetry_command is not None:
            telemetry = _start_process(
                telemetry_command, run_dir / "telemetry", handles
            )

        if spec.condition.neighbor_enabled:
            neighbor_command = command_builder(
                "neighbor", spec, config, run_dir, assets
            )
            neighbor = _start_process(neighbor_command, run_dir / "neighbor", handles)
            _wait_until_ready(
                neighbor,
                run_dir / "neighbor" / "ready",
                ready_timeout_seconds,
            )

        victim_command = command_builder("victim", spec, config, run_dir, assets)
        victim = _start_process(victim_command, run_dir / "victim", handles)
        victim_code = victim.wait()
        if victim_code != 0:
            raise RuntimeError(f"victim exited with code {victim_code}")
        if neighbor is not None and neighbor.poll() is not None:
            raise RuntimeError(
                f"neighbor exited before victim completed with code {neighbor.returncode}"
            )

        status = "complete"
    except Exception as exc:
        status = "failed"
        error = str(exc)
    finally:
        _stop_process(neighbor)
        _stop_process(telemetry)
        for handle in handles:
            handle.close()

    manifest["status"] = status
    if error is not None:
        manifest["error"] = error
    _write_json_atomic(run_dir / "manifest.json", manifest)
    _write_json_atomic(run_dir / "status.json", {"status": status, "error": error})
    return RunResult(run_dir, status, error)
