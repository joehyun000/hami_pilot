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


def parse_mlperf_summary(
    path: Path,
    *,
    require_valid: bool = True,
) -> MLPerfMetrics:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MLPerfLogError(f"cannot read MLPerf summary: {exc}") from exc

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
    completed_samples = int(
        _extract(text, "completed samples", r"^Completed samples\s*:\s*(\d+)\s*$")
    )

    if not all(
        math.isfinite(value) and value >= 0 for value in (throughput, p50_ns, p99_ns)
    ):
        raise MLPerfLogError("metrics must be finite and nonnegative")
    if throughput <= 0:
        raise MLPerfLogError("completed samples per second must be positive")
    if completed_samples <= 0:
        raise MLPerfLogError("completed samples must be positive")

    return MLPerfMetrics(
        result_validity=validity,
        completed_samples_per_second=throughput,
        p50_ms=p50_ns / 1_000_000,
        p99_ms=p99_ns / 1_000_000,
        completed_samples=completed_samples,
    )
