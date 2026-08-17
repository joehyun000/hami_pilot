from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path


class MLPerfLogError(ValueError):
    """Raised when an MLPerf summary cannot support a valid run."""


@dataclass(frozen=True)
class MLPerfMetrics:
    result_validity: str
    completed_samples_per_second: float
    p50_ms: float
    p99_ms: float
    completed_samples: int
    scheduled_samples_per_second: float | None = None


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def render_user_conf(target_qps: float, duration_ms: int) -> str:
    if not math.isfinite(target_qps) or target_qps <= 0:
        raise ValueError("target_qps must be positive and finite")
    if type(duration_ms) is not int or duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    return (
        f"*.Server.target_qps = {target_qps:g}\n"
        f"*.Server.min_duration = {duration_ms}\n"
        f"*.Server.target_duration = {duration_ms}\n"
    )


def _extract(text: str, label: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if match is None:
        raise MLPerfLogError(f"missing {label}")
    return match.group(1)


def _read_summary(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MLPerfLogError(f"cannot read MLPerf summary: {exc}") from exc


def parse_mlperf_summary(
    path: Path,
    *,
    require_valid: bool = True,
    detail_path: Path | None = None,
) -> MLPerfMetrics:
    text = _read_summary(path)

    validity = _extract(text, "result validity", r"^Result is\s*:\s*(\S+)\s*$")
    if validity not in {"VALID", "INVALID"}:
        raise MLPerfLogError(f"unknown result validity: {validity}")
    if require_valid and validity != "VALID":
        raise MLPerfLogError(f"result is not VALID: {validity}")

    throughput = float(
        _extract(
            text,
            "completed samples per second",
            rf"^Completed samples per second\s*:\s*({_NUMBER})\s*$",
        )
    )
    scheduled_match = re.search(
        rf"^Scheduled samples per second\s*:\s*({_NUMBER})\s*$",
        text,
        flags=re.MULTILINE,
    )
    scheduled_throughput = (
        float(scheduled_match.group(1)) if scheduled_match is not None else None
    )
    p50_ns = float(
        _extract(
            text,
            "50th-percentile latency",
            rf"^50\.00 percentile latency \(ns\)\s*:\s*({_NUMBER})\s*$",
        )
    )
    p99_ns = float(
        _extract(
            text,
            "99th-percentile latency",
            rf"^99\.00 percentile latency \(ns\)\s*:\s*({_NUMBER})\s*$",
        )
    )
    completed_match = re.search(
        r"^Completed samples\s*:\s*(\d+)\s*$", text, flags=re.MULTILINE
    )
    if completed_match is None:
        completed_match = re.search(
            r"^- Processed\s+(\d+)\s+queries\.\s*$", text, flags=re.MULTILINE
        )
    if completed_match is not None:
        completed_samples = int(completed_match.group(1))
    elif detail_path is not None:
        detail_text = _read_summary(detail_path)
        detail_match = re.search(
            r'^:::MLLOG\s+\{[^\n]*"key"\s*:\s*"result_query_count"'
            r'[^\n]*"value"\s*:\s*(\d+)',
            detail_text,
            flags=re.MULTILINE,
        )
        if detail_match is None:
            raise MLPerfLogError("missing completed samples")
        completed_samples = int(detail_match.group(1))
    else:
        raise MLPerfLogError("missing completed samples")

    if not all(
        math.isfinite(value) and value >= 0 for value in (throughput, p50_ns, p99_ns)
    ):
        raise MLPerfLogError("metrics must be finite and nonnegative")
    if throughput <= 0:
        raise MLPerfLogError("completed samples per second must be positive")
    if scheduled_throughput is not None and (
        not math.isfinite(scheduled_throughput) or scheduled_throughput <= 0
    ):
        raise MLPerfLogError("scheduled samples per second must be positive and finite")
    if completed_samples <= 0:
        raise MLPerfLogError("completed samples must be positive")

    return MLPerfMetrics(
        result_validity=validity,
        completed_samples_per_second=throughput,
        p50_ms=p50_ns / 1_000_000,
        p99_ms=p99_ns / 1_000_000,
        completed_samples=completed_samples,
        scheduled_samples_per_second=scheduled_throughput,
    )


def parse_mlperf_readiness_summary(
    path: Path,
    *,
    detail_path: Path | None = None,
) -> MLPerfMetrics:
    metrics = parse_mlperf_summary(
        path,
        require_valid=False,
        detail_path=detail_path,
    )
    if metrics.result_validity == "VALID":
        return metrics

    text = _read_summary(path)
    required_patterns = (
        r"^\s*Performance constraints satisfied\s*:\s*Yes\s*$",
        r"^\s*Min duration satisfied\s*:\s*Yes\s*$",
        r"^\s*Min queries satisfied\s*:\s*Yes\s*$",
        r"^\s*Early stopping satisfied\s*:\s*NO\s*$",
        r"^No errors encountered during test\.\s*$",
    )
    if not all(
        re.search(pattern, text, flags=re.MULTILINE) is not None
        for pattern in required_patterns
    ):
        raise MLPerfLogError("short readiness run failed for a reason other than early stopping")
    return metrics
