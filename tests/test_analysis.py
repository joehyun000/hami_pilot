import json

import pytest

from hami_tail_pilot.analysis import AnalysisError, RunMetrics, evaluate_go, write_decision_reports
from hami_tail_pilot.probe import ProbeMetrics


NO_WAIT = ProbeMetrics(limiter_calls=1000, waited_calls=0, sleep_calls=0, wait_ns=0)
WITH_WAIT = ProbeMetrics(limiter_calls=1000, waited_calls=40, sleep_calls=80, wait_ns=800_000_000)


def runs_with_p99(p0, p1, p2, p3):
    rows = []
    values = {"P0": p0, "P1": p1, "P2": p2, "P3": p3}
    for block in range(1, 6):
        for condition, per_block in values.items():
            rows.append(
                RunMetrics(
                    block=block,
                    condition=condition,
                    p50_ms=10.0,
                    p99_ms=per_block[block - 1],
                    throughput_qps=10.0,
                    probe=WITH_WAIT if condition in {"P1", "P3"} else NO_WAIT,
                )
            )
    return rows


def test_evaluate_go_uses_paired_block_ratios_and_four_of_five_rule():
    runs = runs_with_p99(
        p0=[100, 100, 100, 100, 100],
        p1=[120, 115, 112, 111, 95],
        p2=[130, 130, 130, 130, 130],
        p3=[150, 150, 150, 150, 150],
    )

    decision = evaluate_go(runs, probe_overhead_ratio=1.03)

    assert decision.decision == "GO"
    p1_p0 = next(contrast for contrast in decision.contrasts if contrast.name == "P1/P0")
    assert p1_p0.same_direction_blocks == 4
    assert p1_p0.median_ratio == pytest.approx(1.12)
    assert p1_p0.ratios == pytest.approx((1.20, 1.15, 1.12, 1.11, 0.95))


def test_evaluate_go_returns_partial_go_for_stable_but_small_effect():
    runs = runs_with_p99(
        p0=[100, 100, 100, 100, 100],
        p1=[105, 106, 104, 105, 99],
        p2=[107, 108, 106, 107, 101],
        p3=[110, 111, 109, 110, 100],
    )

    decision = evaluate_go(runs, probe_overhead_ratio=1.01)

    assert decision.decision == "PARTIAL_GO"
    assert any("10%" in reason for reason in decision.reasons)


@pytest.mark.parametrize(
    ("overhead", "mutate", "reason"),
    [
        (1.051, None, "probe overhead exceeds 5%"),
        (1.0, "missing_wait", "quota conditions did not record waits"),
        (1.0, "baseline_wait", "no-quota conditions recorded waits"),
        (1.0, "missing_run", "each block must contain P0, P1, P2, P3 exactly once"),
    ],
)
def test_evaluate_go_rejects_invalid_measurement_sets(overhead, mutate, reason):
    runs = runs_with_p99(
        p0=[100, 100, 100, 100, 100],
        p1=[120, 120, 120, 120, 120],
        p2=[130, 130, 130, 130, 130],
        p3=[150, 150, 150, 150, 150],
    )
    if mutate == "missing_wait":
        runs[1] = RunMetrics(**{**runs[1].__dict__, "probe": NO_WAIT})
    elif mutate == "baseline_wait":
        runs[0] = RunMetrics(**{**runs[0].__dict__, "probe": WITH_WAIT})
    elif mutate == "missing_run":
        runs.pop()

    decision = evaluate_go(runs, probe_overhead_ratio=overhead)

    assert decision.decision == "NO_GO"
    assert reason in decision.reasons


def test_evaluate_go_rejects_nonpositive_latency():
    runs = runs_with_p99(
        p0=[0, 100, 100, 100, 100],
        p1=[120, 120, 120, 120, 120],
        p2=[130, 130, 130, 130, 130],
        p3=[150, 150, 150, 150, 150],
    )

    with pytest.raises(AnalysisError, match="latency and throughput must be positive"):
        evaluate_go(runs, probe_overhead_ratio=1.0)


def test_write_decision_reports_keeps_machine_and_human_outputs_consistent(tmp_path):
    runs = runs_with_p99(
        p0=[100, 100, 100, 100, 100],
        p1=[120, 120, 120, 120, 120],
        p2=[130, 130, 130, 130, 130],
        p3=[150, 150, 150, 150, 150],
    )
    decision = evaluate_go(runs, probe_overhead_ratio=1.0)

    json_path, markdown_path = write_decision_reports(decision, tmp_path)

    assert json.loads(json_path.read_text(encoding="utf-8"))["decision"] == "GO"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# Pilot decision: GO" in markdown
    assert "10%는 논문 유의성 기준이 아니라" in markdown
