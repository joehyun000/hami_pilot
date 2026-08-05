from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

from hami_tail_pilot.mlperf import MLPerfMetrics


class CalibrationError(ValueError):
    """Raised when calibration data cannot determine a safe fixed load."""


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
