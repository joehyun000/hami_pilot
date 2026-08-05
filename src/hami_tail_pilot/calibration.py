from __future__ import annotations

import math
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
import statistics
from collections.abc import Callable, Sequence

import yaml

from hami_tail_pilot.mlperf import MLPerfMetrics, parse_mlperf_summary
from hami_tail_pilot.config import PilotConfig
from hami_tail_pilot.runner import RunResult, RuntimeAssets, run_spec
from hami_tail_pilot.schedule import RunSpec


class CalibrationError(ValueError):
    """Raised when calibration data cannot determine a safe fixed load."""


@dataclass(frozen=True)
class CalibrationExecution:
    target_qps: float
    probe_overhead_ratio: float
    probe_overhead_pass: bool


CalibrationRun = Callable[..., RunResult]


def choose_target_qps(
    candidates: Sequence[tuple[float, MLPerfMetrics]],
    load_fraction: float = 0.70,
) -> float:
    if not math.isfinite(load_fraction) or not 0 < load_fraction <= 1:
        raise CalibrationError("load_fraction must be in (0, 1]")
    if not candidates:
        raise CalibrationError("calibration candidates are empty")

    sustainable: list[float] = []
    seen: set[float] = set()
    for target_qps, result in candidates:
        if not math.isfinite(target_qps) or target_qps <= 0:
            raise CalibrationError("target QPS values must be positive and finite")
        if target_qps in seen:
            raise CalibrationError("target QPS values must be unique")
        seen.add(target_qps)
        if result.result_validity != "VALID":
            continue
        if result.completed_samples_per_second >= target_qps * 0.98:
            sustainable.append(target_qps)

    if not sustainable:
        raise CalibrationError("no sustainable calibration point")
    return round(max(sustainable) * load_fraction, 3)


def probe_overhead_ratio(
    probe_off_p99: Sequence[float],
    probe_on_p99: Sequence[float],
) -> float:
    if len(probe_off_p99) != 3 or len(probe_on_p99) != 3:
        raise CalibrationError("probe overhead requires exactly three paired runs")
    pairs = tuple(zip(probe_off_p99, probe_on_p99, strict=True))
    if any(
        not math.isfinite(off) or not math.isfinite(on) or off <= 0 or on <= 0
        for off, on in pairs
    ):
        raise CalibrationError("paired p99 values must be positive and finite")
    return statistics.median(on / off for off, on in pairs)


def resolve_calibration_measurements(path: Path) -> tuple[float, float]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"cannot read calibration measurements: {exc}") from exc
    required = {"candidates", "probe_off_p99_ms", "probe_on_p99_ms"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise CalibrationError("calibration measurements have unexpected schema")
    candidates = []
    for item in payload["candidates"]:
        if not isinstance(item, dict) or set(item) != {"target_qps", "summary"}:
            raise CalibrationError("calibration candidate has unexpected schema")
        candidates.append(
            (float(item["target_qps"]), parse_mlperf_summary(Path(item["summary"])))
        )
    target_qps = choose_target_qps(candidates)
    overhead = probe_overhead_ratio(
        payload["probe_off_p99_ms"], payload["probe_on_p99_ms"]
    )
    return target_qps, overhead


def write_calibration_outputs(
    config_path: Path,
    output_dir: Path,
    target_qps: float,
    overhead_ratio: float,
) -> None:
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CalibrationError(f"cannot read source pilot config: {exc}") from exc
    if not isinstance(config, dict):
        raise CalibrationError("source pilot config must be a mapping")
    _write_calibration_outputs(config, output_dir, target_qps, overhead_ratio)


def _write_calibration_outputs(
    config: dict,
    output_dir: Path,
    target_qps: float,
    overhead_ratio: float,
) -> None:
    config["victim_target_qps"] = target_qps
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pilot.resolved.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    (output_dir / "calibration_decision.json").write_text(
        json.dumps(
            {
                "target_qps": target_qps,
                "probe_overhead_ratio": overhead_ratio,
                "probe_overhead_pass": overhead_ratio <= 1.05,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _config_payload(config: PilotConfig) -> dict:
    return {
        "seed": config.seed,
        "blocks": config.blocks,
        "warmup_seconds": config.warmup_seconds,
        "victim_duration_seconds": config.victim_duration_seconds,
        "neighbor_duration_seconds": config.neighbor_duration_seconds,
        "victim_target_qps": config.victim_target_qps,
        "conditions": [asdict(condition) for condition in config.conditions],
    }


def _qps_token(qps: float) -> str:
    return format(qps, ".12g").replace(".", "p")


def _run_calibration_case(
    spec: RunSpec,
    config: PilotConfig,
    root: Path,
    assets: RuntimeAssets,
    run_one: CalibrationRun,
    *,
    require_valid: bool = True,
) -> MLPerfMetrics:
    result = run_one(spec, config, root, assets=assets)
    if result.status != "complete":
        raise CalibrationError(
            f"{spec.run_id} failed: {result.error or 'unknown calibration failure'}"
        )
    summary = result.run_dir / "victim" / "mlperf_log_summary.txt"
    try:
        return parse_mlperf_summary(summary, require_valid=require_valid)
    except ValueError as exc:
        raise CalibrationError(
            f"{spec.run_id} has invalid MLPerf output: {exc}"
        ) from exc


def execute_calibration(
    config: PilotConfig,
    output_dir: Path,
    probe_assets: RuntimeAssets,
    vanilla_assets: RuntimeAssets,
    *,
    candidate_qps: Sequence[float],
    run_one: CalibrationRun = run_spec,
) -> CalibrationExecution:
    if config.victim_target_qps is not None:
        raise CalibrationError("calibration requires an unresolved victim_target_qps")
    if not candidate_qps:
        raise CalibrationError("candidate_qps must not be empty")
    normalized_qps = tuple(float(value) for value in candidate_qps)
    if any(not math.isfinite(value) or value <= 0 for value in normalized_qps):
        raise CalibrationError("candidate_qps values must be positive and finite")
    if len(set(normalized_qps)) != len(normalized_qps):
        raise CalibrationError("candidate_qps values must be unique")

    p0 = config.conditions[0]
    candidate_root = output_dir / "calibration" / "candidates"
    candidates: list[tuple[float, MLPerfMetrics]] = []
    candidate_records: list[dict[str, object]] = []
    for order, qps in enumerate(normalized_qps, start=1):
        run_config = replace(config, victim_target_qps=float(qps))
        spec = RunSpec(0, order, p0, f"qps-{_qps_token(float(qps))}")
        metrics = _run_calibration_case(
            spec,
            run_config,
            candidate_root,
            probe_assets,
            run_one,
            require_valid=False,
        )
        candidates.append((float(qps), metrics))
        candidate_records.append(
            {
                "target_qps": float(qps),
                "summary": str(
                    (
                        candidate_root
                        / spec.run_id
                        / "victim"
                        / "mlperf_log_summary.txt"
                    ).resolve()
                ),
            }
        )

    target_qps = choose_target_qps(candidates)
    overhead_root = output_dir / "calibration" / "probe-overhead"
    paired_order = (
        ("vanilla", vanilla_assets, "probe", probe_assets),
        ("probe", probe_assets, "vanilla", vanilla_assets),
        ("vanilla", vanilla_assets, "probe", probe_assets),
    )
    p99: dict[str, list[float]] = {"vanilla": [], "probe": []}
    target_config = replace(config, victim_target_qps=target_qps)
    for pair_index, pair in enumerate(paired_order, start=1):
        for position in (0, 2):
            label = pair[position]
            assets = pair[position + 1]
            spec = RunSpec(
                pair_index,
                position // 2 + 1,
                p0,
                f"pair-{pair_index:02d}-{label}",
            )
            metrics = _run_calibration_case(
                spec, target_config, overhead_root, assets, run_one
            )
            p99[label].append(metrics.p99_ms)

    overhead = probe_overhead_ratio(p99["vanilla"], p99["probe"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "calibration_measurements.json").write_text(
        json.dumps(
            {
                "candidates": candidate_records,
                "probe_off_p99_ms": p99["vanilla"],
                "probe_on_p99_ms": p99["probe"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_calibration_outputs(
        _config_payload(config), output_dir, target_qps, overhead
    )
    return CalibrationExecution(target_qps, overhead, overhead <= 1.05)
