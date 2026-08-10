from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from collections.abc import Callable

from hami_tail_pilot.config import PilotConfig
from hami_tail_pilot.mlperf import parse_mlperf_summary
from hami_tail_pilot.preflight import validate_smoke_probes
from hami_tail_pilot.probe import ProbeMetrics, parse_probe_jsonl
from hami_tail_pilot.runner import RunResult, RuntimeAssets, run_spec
from hami_tail_pilot.schedule import RunSpec


@dataclass(frozen=True)
class SmokeResult:
    passed: bool
    errors: tuple[str, ...]


SmokeRun = Callable[..., RunResult]


def execute_smoke(
    config: PilotConfig,
    output_dir: Path,
    assets: RuntimeAssets,
    *,
    run_one: SmokeRun = run_spec,
) -> SmokeResult:
    if config.victim_target_qps is None:
        raise ValueError("smoke requires a resolved victim_target_qps")

    smoke_config = replace(
        config,
        victim_duration_seconds=30,
        neighbor_duration_seconds=max(300, config.warmup_seconds + 120),
    )
    smoke_root = output_dir / "smoke"
    smoke_names = ("C1", "C2", "C3", "C4", "C5")
    conditions = {
        condition.name: condition
        for condition in config.conditions
        if condition.name in smoke_names
    }
    errors: list[str] = []
    probes: dict[str, dict[str, ProbeMetrics]] = {}
    report_conditions: dict[str, dict] = {}

    for order, name in enumerate(smoke_names, start=1):
        spec = RunSpec(0, order, conditions[name], f"smoke-{name}")
        result = run_one(spec, smoke_config, smoke_root, assets=assets)
        if result.status != "complete":
            errors.append(f"{name} smoke failed: {result.error or 'unknown failure'}")
            continue
        try:
            metrics = parse_mlperf_summary(
                result.run_dir / "victim" / "mlperf_log_summary.txt"
            )
            roles = ("victim", "neighbor") if spec.condition.neighbor_enabled else ("victim",)
            role_probes = {
                role: parse_probe_jsonl(result.run_dir / role / "hami_probe.jsonl")
                for role in roles
            }
        except ValueError as exc:
            errors.append(f"{name} smoke output is invalid: {exc}")
            continue
        probes[name] = role_probes
        report_conditions[name] = {
            "p99_ms": metrics.p99_ms,
            "completed_samples_per_second": metrics.completed_samples_per_second,
            "roles": {role: asdict(probe) for role, probe in role_probes.items()},
        }

    if not errors:
        errors.extend(validate_smoke_probes(probes))

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "smoke.json").write_text(
        json.dumps(
            {
                "passed": not errors,
                "errors": errors,
                "conditions": report_conditions,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return SmokeResult(not errors, tuple(errors))
