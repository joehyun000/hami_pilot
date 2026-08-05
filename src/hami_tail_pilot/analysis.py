from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Sequence

from hami_tail_pilot.probe import ProbeMetrics


class AnalysisError(ValueError):
    """Raised when numeric inputs cannot be analyzed safely."""


@dataclass(frozen=True)
class RunMetrics:
    block: int
    condition: str
    p50_ms: float
    p99_ms: float
    throughput_qps: float
    probe: ProbeMetrics


@dataclass(frozen=True)
class ContrastResult:
    name: str
    ratios: tuple[float, ...]
    median_ratio: float
    same_direction_blocks: int


@dataclass(frozen=True)
class PilotDecision:
    decision: str
    reasons: tuple[str, ...]
    contrasts: tuple[ContrastResult, ...]


_CONDITIONS = ("P0", "P1", "P2", "P3")
_CONTRASTS = (("P1/P0", "P1", "P0"), ("P3/P1", "P3", "P1"), ("P3/P2", "P3", "P2"))


def _validate_numeric_inputs(runs: Sequence[RunMetrics], probe_overhead_ratio: float) -> None:
    if not math.isfinite(probe_overhead_ratio) or probe_overhead_ratio <= 0:
        raise AnalysisError("probe overhead ratio must be positive and finite")
    for run in runs:
        values = (run.p50_ms, run.p99_ms, run.throughput_qps)
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise AnalysisError("latency and throughput must be positive and finite")


def _index_complete_runs(runs: Sequence[RunMetrics]) -> dict[tuple[int, str], RunMetrics] | None:
    indexed: dict[tuple[int, str], RunMetrics] = {}
    for run in runs:
        key = (run.block, run.condition)
        if key in indexed:
            return None
        indexed[key] = run
    expected = {(block, condition) for block in range(1, 6) for condition in _CONDITIONS}
    return indexed if set(indexed) == expected else None


def _contrast(indexed: dict[tuple[int, str], RunMetrics], name: str, numerator: str, denominator: str) -> ContrastResult:
    ratios = tuple(
        indexed[(block, numerator)].p99_ms / indexed[(block, denominator)].p99_ms
        for block in range(1, 6)
    )
    return ContrastResult(
        name=name,
        ratios=ratios,
        median_ratio=statistics.median(ratios),
        same_direction_blocks=sum(ratio > 1.0 for ratio in ratios),
    )


def evaluate_go(runs: Sequence[RunMetrics], probe_overhead_ratio: float) -> PilotDecision:
    _validate_numeric_inputs(runs, probe_overhead_ratio)
    indexed = _index_complete_runs(runs)
    if indexed is None:
        return PilotDecision(
            decision="NO_GO",
            reasons=("each block must contain P0, P1, P2, P3 exactly once",),
            contrasts=(),
        )

    contrasts = tuple(_contrast(indexed, *definition) for definition in _CONTRASTS)
    invalid_reasons: list[str] = []
    if probe_overhead_ratio > 1.05:
        invalid_reasons.append("probe overhead exceeds 5%")
    if any(
        indexed[(block, condition)].probe.waited_calls == 0
        for block in range(1, 6)
        for condition in ("P1", "P3")
    ):
        invalid_reasons.append("quota conditions did not record waits")
    if any(
        indexed[(block, condition)].probe.waited_calls > 0
        for block in range(1, 6)
        for condition in ("P0", "P2")
    ):
        invalid_reasons.append("no-quota conditions recorded waits")
    if invalid_reasons:
        return PilotDecision("NO_GO", tuple(invalid_reasons), contrasts)

    go_contrasts = [
        contrast
        for contrast in contrasts
        if contrast.same_direction_blocks >= 4 and contrast.median_ratio >= 1.10
    ]
    if go_contrasts:
        names = ", ".join(contrast.name for contrast in go_contrasts)
        return PilotDecision(
            "GO",
            (f"{names} met the 4-of-5 direction rule and median p99 slowdown >=10%",),
            contrasts,
        )

    stable_small = [
        contrast
        for contrast in contrasts
        if contrast.same_direction_blocks >= 4 and contrast.median_ratio > 1.0
    ]
    if stable_small:
        names = ", ".join(contrast.name for contrast in stable_small)
        return PilotDecision(
            "PARTIAL_GO",
            (f"{names} was directionally stable but median p99 slowdown was below 10%",),
            contrasts,
        )

    return PilotDecision(
        "NO_GO",
        ("no contrast showed a stable p99 increase in at least 4 of 5 blocks",),
        contrasts,
    )


def write_decision_reports(decision: PilotDecision, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "pilot_decision.json"
    markdown_path = output_dir / "pilot_decision.md"

    payload = asdict(decision)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [f"# Pilot decision: {decision.decision}", "", "## Reasons", ""]
    lines.extend(f"- {reason}" for reason in decision.reasons)
    lines.extend(["", "## Paired p99 ratios", "", "| Contrast | Ratios by block | Median | Increasing blocks |", "|---|---|---:|---:|"])
    for contrast in decision.contrasts:
        ratios = ", ".join(f"{ratio:.3f}" for ratio in contrast.ratios)
        lines.append(
            f"| {contrast.name} | {ratios} | {contrast.median_ratio:.3f} | {contrast.same_direction_blocks}/5 |"
        )
    lines.extend(
        [
            "",
            "> 10%는 논문 유의성 기준이 아니라 후속 CUPTI 계측 비용을 투입할지 정하는 탐색 기준이다.",
            "",
        ]
    )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path
