import json

import pytest

from hami_tail_pilot.analysis import (
    AnalysisError,
    RunMetrics,
    evaluate_go,
    write_decision_reports,
)
from hami_tail_pilot.probe import ProbeMetrics


NO_WAIT = ProbeMetrics(limiter_calls=1000, waited_calls=0, sleep_calls=0, wait_ns=0)
WITH_WAIT = ProbeMetrics(
    limiter_calls=1000,
    waited_calls=40,
    sleep_calls=80,
    wait_ns=800_000_000,
)


def runs_with_p99(**values):
    rows = []
    waiting_conditions = {"C2", "C4", "C5"}
    for block in range(1, 6):
        for condition in ("C0", "C1", "C2", "C3", "C4", "C5"):
            rows.append(
                RunMetrics(
                    block=block,
                    condition=condition,
                    p50_ms=10.0,
                    p99_ms=values[condition][block - 1],
                    throughput_qps=10.0,
                    probe=WITH_WAIT if condition in waiting_conditions else NO_WAIT,
                )
            )
    return rows


def default_values():
    return {
        "C0": [98, 98, 98, 98, 98],
        "C1": [100, 100, 100, 100, 100],
        "C2": [120, 115, 112, 111, 95],
        "C3": [105, 105, 105, 105, 105],
        "C4": [126, 121, 118, 117, 100],
        "C5": [126, 121, 118, 117, 100],
    }


def test_evaluate_go_uses_primary_paired_ratios_and_four_of_five_rule():
    decision = evaluate_go(runs_with_p99(**default_values()), 1.03)

    assert decision.decision == "GO"
    c2_c1 = next(
        contrast for contrast in decision.contrasts if contrast.name == "C2/C1"
    )
    assert c2_c1.kind == "primary"
    assert c2_c1.direction == "increase"
    assert c2_c1.same_direction_blocks == 4
    assert c2_c1.median_ratio == pytest.approx(1.12)
    assert c2_c1.ratios == pytest.approx((1.20, 1.15, 1.12, 1.11, 0.95))


def test_evaluate_go_accepts_stable_protection_effect_in_primary_contrast():
    values = default_values()
    values["C4"] = [100, 100, 100, 100, 100]
    values["C5"] = [80, 82, 85, 88, 105]
    values["C2"] = [100, 100, 100, 100, 100]

    decision = evaluate_go(runs_with_p99(**values), 1.01)

    c5_c4 = next(
        contrast for contrast in decision.contrasts if contrast.name == "C5/C4"
    )
    assert decision.decision == "GO"
    assert c5_c4.direction == "decrease"
    assert c5_c4.same_direction_blocks == 4
    assert c5_c4.median_ratio == pytest.approx(0.85)


def test_descriptive_contrast_does_not_trigger_go_by_itself():
    values = {condition: [100] * 5 for condition in default_values()}
    values["C0"] = [70, 70, 70, 70, 70]

    decision = evaluate_go(runs_with_p99(**values), 1.01)

    c1_c0 = next(
        contrast for contrast in decision.contrasts if contrast.name == "C1/C0"
    )
    assert c1_c0.kind == "descriptive"
    assert c1_c0.median_ratio > 1.10
    assert decision.decision == "NO_GO"


def test_evaluate_go_returns_partial_go_for_stable_but_small_effect():
    values = {condition: [100] * 5 for condition in default_values()}
    values["C2"] = [105, 106, 104, 105, 99]

    decision = evaluate_go(runs_with_p99(**values), 1.01)

    assert decision.decision == "PARTIAL_GO"
    assert any("10%" in reason for reason in decision.reasons)


@pytest.mark.parametrize(
    ("overhead", "mutate", "reason"),
    [
        (1.051, None, "측정 장치 자체의 응답시간 변화가 5%를 초과함"),
        (
            1.0,
            "missing_wait",
            "사용 한도가 있는 측정 대상에서 실행 대기가 기록되지 않음",
        ),
        (
            1.0,
            "unexpected_wait",
            "사용 한도가 없는 측정 대상에서 예상하지 않은 실행 대기가 기록됨",
        ),
        (
            1.0,
            "missing_run",
            "각 묶음에는 C0, C1, C2, C3, C4, C5가 한 번씩 있어야 함",
        ),
    ],
)
def test_evaluate_go_rejects_invalid_measurement_sets(overhead, mutate, reason):
    runs = runs_with_p99(**default_values())
    if mutate == "missing_wait":
        index = next(i for i, run in enumerate(runs) if run.condition == "C2")
        runs[index] = RunMetrics(**{**runs[index].__dict__, "probe": NO_WAIT})
    elif mutate == "unexpected_wait":
        index = next(i for i, run in enumerate(runs) if run.condition == "C1")
        runs[index] = RunMetrics(**{**runs[index].__dict__, "probe": WITH_WAIT})
    elif mutate == "missing_run":
        runs.pop()

    decision = evaluate_go(runs, probe_overhead_ratio=overhead)

    assert decision.decision == "NO_GO"
    assert reason in decision.reasons


def test_evaluate_go_rejects_nonpositive_latency():
    values = default_values()
    values["C0"][0] = 0

    with pytest.raises(AnalysisError, match="latency and throughput must be positive"):
        evaluate_go(runs_with_p99(**values), probe_overhead_ratio=1.0)


def test_write_decision_reports_keeps_machine_and_human_outputs_consistent(tmp_path):
    decision = evaluate_go(runs_with_p99(**default_values()), 1.0)

    json_path, markdown_path = write_decision_reports(decision, tmp_path)

    assert json.loads(json_path.read_text(encoding="utf-8"))["decision"] == "GO"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# 파일럿 판정: GO" in markdown
    assert "10%는 논문 유의성 기준이 아니라" in markdown
