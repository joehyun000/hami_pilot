from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from hami_tail_pilot.config import PilotConfig
from hami_tail_pilot.runner import RunResult, RuntimeAssets, run_spec
from hami_tail_pilot.schedule import RunSpec, build_schedule, write_schedule_json


@dataclass(frozen=True)
class ExecutionSummary:
    completed: int
    skipped: int
    failed: int
    errors: tuple[str, ...]


RunOne = Callable[..., RunResult]


def _read_status(run_dir: Path) -> str | None:
    path = run_dir / "status.json"
    if not path.is_file():
        return None
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("status"))
    except json.JSONDecodeError:
        return None


def _archive_failed(run_dir: Path, attempts_dir: Path) -> Path:
    attempts_dir.mkdir(parents=True, exist_ok=True)
    attempt = 1
    while True:
        destination = attempts_dir / f"{run_dir.name}-attempt{attempt:02d}"
        if not destination.exists():
            run_dir.rename(destination)
            return destination
        attempt += 1


def _write_experiment_status(output: Path, status: str, errors: list[str]) -> None:
    (output / "status.json").write_text(
        json.dumps({"status": status, "errors": errors}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def execute_schedule(
    config: PilotConfig,
    output: Path,
    assets: RuntimeAssets,
    *,
    run_one: RunOne = run_spec,
    rerun_failed: bool = False,
) -> ExecutionSummary:
    if config.victim_target_qps is None:
        raise ValueError("victim_target_qps must be resolved before a real experiment")
    output.mkdir(parents=True, exist_ok=True)
    schedule = build_schedule(config)
    schedule_path = output / "schedule.json"
    if not schedule_path.exists():
        write_schedule_json(schedule, schedule_path, seed=config.seed)
    _write_experiment_status(output, "running", [])

    completed = 0
    skipped = 0
    failed = 0
    errors: list[str] = []
    for spec in schedule:
        run_dir = output / spec.run_id
        if run_dir.exists():
            status = _read_status(run_dir)
            if status == "complete":
                skipped += 1
                continue
            if not rerun_failed:
                failed += 1
                errors.append(f"{spec.run_id}: existing run is not complete")
                continue
            _archive_failed(run_dir, output / "attempts")

        result = run_one(spec, config, output, assets=assets)
        if result.status == "complete":
            completed += 1
        else:
            failed += 1
            errors.append(f"{spec.run_id}: {result.error or 'unknown failure'}")

    final_status = "complete" if failed == 0 else "failed"
    _write_experiment_status(output, final_status, errors)
    return ExecutionSummary(completed, skipped, failed, tuple(errors))
