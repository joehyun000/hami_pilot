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
    kind: str
    ratios: tuple[float, ...]
    median_ratio: float
    same_direction_blocks: int
    direction: str


@dataclass(frozen=True)
class PilotDecision:
    decision: str
    reasons: tuple[str, ...]
    contrasts: tuple[ContrastResult, ...]


_CONDITIONS = ("C0", "C1", "C2", "C3", "C4", "C5")
_PRIMARY_CONTRASTS = (
    ("C2/C1", "C2", "C1"),
    ("C4/C3", "C4", "C3"),
    ("C5/C4", "C5", "C4"),
)
_DESCRIPTIVE_CONTRASTS = (
    ("C1/C0", "C1", "C0"),
    ("C3/C1", "C3", "C1"),
    ("C5/C3", "C5", "C3"),
)


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


def _contrast(
    indexed: dict[tuple[int, str], RunMetrics],
    kind: str,
    name: str,
    numerator: str,
    denominator: str,
) -> ContrastResult:
    ratios = tuple(
        indexed[(block, numerator)].p99_ms / indexed[(block, denominator)].p99_ms
        for block in range(1, 6)
    )
    increases = sum(ratio > 1.0 for ratio in ratios)
    decreases = sum(ratio < 1.0 for ratio in ratios)
    if increases > decreases:
        direction = "increase"
    elif decreases > increases:
        direction = "decrease"
    else:
        direction = "mixed"
    return ContrastResult(
        name=name,
        kind=kind,
        ratios=ratios,
        median_ratio=statistics.median(ratios),
        same_direction_blocks=max(increases, decreases),
        direction=direction,
    )


def evaluate_go(runs: Sequence[RunMetrics], probe_overhead_ratio: float) -> PilotDecision:
    _validate_numeric_inputs(runs, probe_overhead_ratio)
    indexed = _index_complete_runs(runs)
    if indexed is None:
        return PilotDecision(
            decision="NO_GO",
            reasons=(
                "각 묶음에는 C0, C1, C2, C3, C4, C5가 한 번씩 있어야 함",
            ),
            contrasts=(),
        )

    contrasts = tuple(
        _contrast(indexed, "primary", *definition)
        for definition in _PRIMARY_CONTRASTS
    ) + tuple(
        _contrast(indexed, "descriptive", *definition)
        for definition in _DESCRIPTIVE_CONTRASTS
    )
    invalid_reasons: list[str] = []
    if probe_overhead_ratio > 1.05:
        invalid_reasons.append("측정 장치 자체의 응답시간 변화가 5%를 초과함")
    if any(
        indexed[(block, condition)].probe.waited_calls == 0
        for block in range(1, 6)
        for condition in ("C2", "C4", "C5")
    ):
        invalid_reasons.append("사용 한도가 있는 측정 대상에서 실행 대기가 기록되지 않음")
    if any(
        indexed[(block, condition)].probe.waited_calls > 0
        for block in range(1, 6)
        for condition in ("C0", "C1", "C3")
    ):
        invalid_reasons.append("사용 한도가 없는 측정 대상에서 예상하지 않은 실행 대기가 기록됨")
    if invalid_reasons:
        return PilotDecision("NO_GO", tuple(invalid_reasons), contrasts)

    primary = [contrast for contrast in contrasts if contrast.kind == "primary"]
    go_contrasts = [
        contrast
        for contrast in primary
        if contrast.same_direction_blocks >= 4
        and (
            contrast.median_ratio >= 1.10
            or contrast.median_ratio <= 1 / 1.10
        )
    ]
    if go_contrasts:
        names = ", ".join(contrast.name for contrast in go_contrasts)
        return PilotDecision(
            "GO",
            (
                f"{names}: 5개 묶음 중 4개 이상 같은 방향이며, "
                "느린 요청 응답시간의 가운데 변화가 10% 이상임",
            ),
            contrasts,
        )

    stable_small = [
        contrast
        for contrast in primary
        if contrast.same_direction_blocks >= 4
        and not math.isclose(contrast.median_ratio, 1.0)
    ]
    if stable_small:
        names = ", ".join(contrast.name for contrast in stable_small)
        return PilotDecision(
            "PARTIAL_GO",
            (
                f"{names}: 5개 묶음 중 4개 이상 같은 방향이지만, "
                "느린 요청 응답시간의 가운데 변화가 10% 미만임",
            ),
            contrasts,
        )

    return PilotDecision(
        "NO_GO",
        ("판정용 비교에서 5개 묶음 중 4개 이상 반복되는 변화가 없음",),
        contrasts,
    )


def write_decision_reports(decision: PilotDecision, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "pilot_decision.json"
    markdown_path = output_dir / "pilot_decision.md"

    payload = asdict(decision)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [f"# 파일럿 판정: {decision.decision}", "", "## 판정 이유", ""]
    lines.extend(f"- {reason}" for reason in decision.reasons)
    lines.extend(
        [
            "",
            "## 묶음별 느린 요청 응답시간 비율",
            "",
            "| 비교 | 용도 | 묶음별 비율 | 비율의 가운데 값 | 같은 방향 묶음 | 방향 |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for contrast in decision.contrasts:
        ratios = ", ".join(f"{ratio:.3f}" for ratio in contrast.ratios)
        kind = {"primary": "판정용", "descriptive": "설명용"}[contrast.kind]
        direction = {
            "increase": "증가",
            "decrease": "감소",
            "mixed": "혼합",
        }[contrast.direction]
        lines.append(
            f"| {contrast.name} | {kind} | {ratios} | "
            f"{contrast.median_ratio:.3f} | {contrast.same_direction_blocks}/5 | "
            f"{direction} |"
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
