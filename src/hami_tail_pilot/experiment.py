from __future__ import annotations

import csv
import json
from pathlib import Path

from hami_tail_pilot.analysis import PilotDecision, RunMetrics, evaluate_go, write_decision_reports
from hami_tail_pilot.config import PilotConfig
from hami_tail_pilot.mlperf import parse_mlperf_summary
from hami_tail_pilot.probe import ProbeMetrics, parse_probe_jsonl
from hami_tail_pilot.schedule import RunSpec, build_schedule, write_schedule_json


class ExperimentError(ValueError):
    """Raised when an experiment directory is incomplete or ambiguous."""


_SYNTHETIC_P99_MS = {
    "C0": (98.0, 98.0, 98.0, 98.0, 98.0),
    "C1": (100.0, 100.0, 100.0, 100.0, 100.0),
    "C2": (120.0, 115.0, 112.0, 111.0, 95.0),
    "C3": (105.0, 105.0, 105.0, 105.0, 105.0),
    "C4": (126.0, 121.0, 118.0, 117.0, 100.0),
    "C5": (126.0, 121.0, 118.0, 117.0, 100.0),
}


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _synthetic_summary(p99_ms: float) -> str:
    p50_ns = int(p99_ms * 0.4 * 1_000_000)
    p99_ns = int(p99_ms * 1_000_000)
    return (
        "MLPerf Results Summary\n"
        "Completed samples per second : 10.0\n"
        "Result is : VALID\n"
        f"50.00 percentile latency (ns) : {p50_ns}\n"
        f"99.00 percentile latency (ns) : {p99_ns}\n"
        "Completed samples : 3000\n"
    )


def _write_synthetic_run(spec: RunSpec, output: Path) -> None:
    run_dir = output / spec.run_id
    status_path = run_dir / "status.json"
    if status_path.is_file():
        try:
            if json.loads(status_path.read_text(encoding="utf-8")).get("status") == "complete":
                return
        except json.JSONDecodeError as exc:
            raise ExperimentError(f"invalid existing status for {spec.run_id}") from exc
        raise ExperimentError(f"existing incomplete synthetic run: {spec.run_id}")

    victim_dir = run_dir / "victim"
    victim_dir.mkdir(parents=True)
    p99_ms = _SYNTHETIC_P99_MS[spec.condition.name][spec.block - 1]
    (victim_dir / "mlperf_log_summary.txt").write_text(
        _synthetic_summary(p99_ms), encoding="utf-8"
    )
    waited = spec.condition.name in {"C2", "C4", "C5"}
    probe = {
        "schema_version": 1,
        "pid": 1000 + spec.block,
        "limiter_calls": 1000,
        "waited_calls": 40 if waited else 0,
        "sleep_calls": 80 if waited else 0,
        "wait_ns": 800_000_000 if waited else 0,
    }
    if spec.condition.hami_enabled:
        (victim_dir / "hami_probe.jsonl").write_text(
            json.dumps(probe, sort_keys=True) + "\n", encoding="utf-8"
        )
    _write_json(
        run_dir / "manifest.json",
        {
            "run_id": spec.run_id,
            "block": spec.block,
            "order": spec.order,
            "condition": spec.condition.name,
            "status": "complete",
            "synthetic": True,
        },
    )
    _write_json(status_path, {"status": "complete", "error": None})


def simulate_experiment(config: PilotConfig, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    schedule = build_schedule(config)
    schedule_path = output / "schedule.json"
    if not schedule_path.exists():
        write_schedule_json(schedule, schedule_path, seed=config.seed)
    for spec in schedule:
        _write_synthetic_run(spec, output)
    _write_json(output / "status.json", {"status": "complete", "synthetic": True})


def load_experiment_runs(input_dir: Path) -> tuple[list[RunMetrics], list[dict[str, str]]]:
    metrics: list[RunMetrics] = []
    csv_rows: list[dict[str, str]] = []
    for manifest_path in sorted(input_dir.glob("*/manifest.json")):
        run_dir = manifest_path.parent
        status_path = run_dir / "status.json"
        if not status_path.is_file():
            continue
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ExperimentError(f"invalid JSON under {run_dir.name}") from exc
        if status.get("status") != "complete":
            continue
        mlperf = parse_mlperf_summary(run_dir / "victim" / "mlperf_log_summary.txt")
        if manifest["condition"] == "C0":
            probe = ProbeMetrics(0, 0, 0, 0)
        else:
            probe = parse_probe_jsonl(run_dir / "victim" / "hami_probe.jsonl")
        run = RunMetrics(
            block=int(manifest["block"]),
            condition=str(manifest["condition"]),
            p50_ms=mlperf.p50_ms,
            p99_ms=mlperf.p99_ms,
            throughput_qps=mlperf.completed_samples_per_second,
            probe=probe,
        )
        metrics.append(run)
        csv_rows.append(
            {
                "run_id": str(manifest["run_id"]),
                "block": str(run.block),
                "condition": run.condition,
                "p50_ms": f"{run.p50_ms:.6f}",
                "p99_ms": f"{run.p99_ms:.6f}",
                "throughput_qps": f"{run.throughput_qps:.6f}",
                "limiter_calls": str(probe.limiter_calls),
                "waited_calls": str(probe.waited_calls),
                "sleep_calls": str(probe.sleep_calls),
                "wait_ns": str(probe.wait_ns),
                "synthetic": str(bool(manifest.get("synthetic", False))).lower(),
            }
        )
    if len(metrics) != 30:
        raise ExperimentError(f"expected 30 complete runs, found {len(metrics)}")
    return metrics, csv_rows


def analyze_experiment(input_dir: Path, probe_overhead_ratio: float) -> PilotDecision:
    metrics, rows = load_experiment_runs(input_dir)
    csv_path = input_dir / "pilot_metrics.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    decision = evaluate_go(metrics, probe_overhead_ratio)
    write_decision_reports(decision, input_dir)
    return decision
