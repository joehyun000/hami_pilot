from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass
from pathlib import Path
import socket
import subprocess
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from hami_tail_pilot.probe import ProbeMetrics
from hami_tail_pilot.runner import RuntimeAssets


EXPECTED_SOURCE_MANIFEST = {
    "hami_release": "v2.9.0",
    "hami_commit": "3a006c6ae2f077a2683df7805c43656c07f6dc15",
    "hami_core_commit": "5091a2fbe1816df1265490f771346730f29e2c8d",
    "mlperf_release": "v5.1.1",
    "mlperf_commit": "6776245e99dce0600cfc9a6fb61efd310f87de3d",
}


@dataclass(frozen=True)
class PreflightReport:
    ready: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    environment: dict[str, Any]


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def validate_smoke_probes(probes: Mapping[str, ProbeMetrics]) -> tuple[str, ...]:
    errors: list[str] = []
    required = {"P0", "P1", "P3"}
    if set(probes) != required:
        return ("smoke probes must contain exactly P0, P1, and P3",)
    if probes["P0"].waited_calls > 0:
        errors.append("P0 recorded quota waits")
    for condition in ("P1", "P3"):
        if probes[condition].waited_calls == 0:
            errors.append(f"{condition} did not record quota waits")
    return tuple(errors)


def _run(runner: CommandRunner, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return runner(list(command), capture_output=True, text=True, check=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        errors.append("source manifest is missing or invalid")
        return None
    if payload != EXPECTED_SOURCE_MANIFEST:
        errors.append("source manifest does not match pinned commits")
    return payload


def run_preflight(
    assets: RuntimeAssets,
    source_manifest: Path,
    experiment_root: Path,
    *,
    runner: CommandRunner = subprocess.run,
) -> PreflightReport:
    errors: list[str] = []
    warnings: list[str] = []
    environment: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "gpu_index": assets.gpu_index,
        "image_tag": assets.image_tag,
    }

    manifest = _load_manifest(source_manifest, errors)
    if manifest is not None:
        environment["sources"] = manifest

    for label, path in (
        ("model", assets.model_file),
        ("dataset", assets.dataset_file),
        ("vocab", assets.vocab_file),
    ):
        if not path.is_file():
            errors.append(f"{label} input is missing: {path.name}")
        else:
            environment[f"{label}_sha256"] = _sha256(path)

    status_path = experiment_root / "status.json"
    if status_path.is_file():
        try:
            if json.loads(status_path.read_text(encoding="utf-8")).get("status") == "complete":
                errors.append("experiment directory is already complete")
        except json.JSONDecodeError:
            errors.append("existing experiment status is invalid")

    docker_version_command = ("docker", "version", "--format", "{{.Server.Version}}")
    docker_version = _run(runner, docker_version_command)
    if docker_version.returncode != 0:
        errors.append("Docker server is unavailable")
    else:
        environment["docker_server"] = docker_version.stdout.strip()

    image_command = (
        "docker",
        "image",
        "inspect",
        assets.image_tag,
        "--format",
        "{{.Id}}",
    )
    image = _run(runner, image_command)
    if image.returncode != 0:
        errors.append("pinned BERT image is unavailable")
    else:
        environment["image_digest"] = image.stdout.strip()

    gpu_command = (
        "nvidia-smi",
        "--query-gpu=name,uuid,driver_version",
        "--format=csv,noheader,nounits",
        "--id",
        assets.gpu_index,
    )
    gpu = _run(runner, gpu_command)
    if gpu.returncode != 0:
        errors.append("nvidia-smi cannot query the selected GPU")
    else:
        environment["gpu"] = gpu.stdout.strip()

    processes_command = (
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,gpu_uuid",
        "--format=csv,noheader,nounits",
    )
    processes = _run(runner, processes_command)
    if processes.returncode != 0:
        errors.append("nvidia-smi cannot query compute processes")
    elif processes.stdout.strip():
        errors.append("GPU has existing compute processes")
        environment["existing_compute_processes"] = processes.stdout.strip().splitlines()

    gpu_probe_command = (
        "docker",
        "run",
        "--rm",
        "--gpus",
        f"device={assets.gpu_index}",
        "--entrypoint",
        "python3",
        assets.image_tag,
        "-c",
        "import torch; print(f'torch={torch.__version__} cuda={torch.cuda.is_available()}')",
    )
    gpu_probe = _run(runner, gpu_probe_command)
    if gpu_probe.returncode != 0 or "cuda=True" not in gpu_probe.stdout:
        errors.append("Docker image cannot access CUDA")
    else:
        environment["container_cuda_probe"] = gpu_probe.stdout.strip()

    warnings.append("GPU clock control was not changed; record server clock and power policy before the pilot")
    return PreflightReport(not errors, tuple(errors), tuple(warnings), environment)
