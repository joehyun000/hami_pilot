import pytest

from hami_tail_pilot.calibration import CalibrationError, choose_target_qps, probe_overhead_ratio
from hami_tail_pilot.mlperf import MLPerfMetrics


def metrics(achieved_qps, validity="VALID"):
    return MLPerfMetrics(
        result_validity=validity,
        completed_samples_per_second=achieved_qps,
        p50_ms=10,
        p99_ms=20,
        completed_samples=1000,
    )


def test_choose_target_qps_uses_seventy_percent_of_largest_sustainable_target():
    candidates = [
        (1.0, metrics(1.0)),
        (2.0, metrics(1.99)),
        (4.0, metrics(3.95)),
        (8.0, metrics(7.7)),
    ]

    assert choose_target_qps(candidates) == 2.8


def test_choose_target_qps_is_independent_of_candidate_order():
    candidates = [(8.0, metrics(7.7)), (4.0, metrics(3.95)), (2.0, metrics(1.99))]

    assert choose_target_qps(candidates) == 2.8


@pytest.mark.parametrize(
    "candidates",
    [
        [],
        [(1.0, metrics(0.5))],
        [(0.0, metrics(1.0))],
        [(1.0, metrics(1.0, validity="INVALID"))],
    ],
)
def test_choose_target_qps_rejects_missing_or_invalid_calibration(candidates):
    with pytest.raises(CalibrationError):
        choose_target_qps(candidates)


def test_probe_overhead_ratio_uses_paired_median_not_ratio_of_unpaired_medians():
    off = [100.0, 200.0, 400.0]
    on = [104.0, 204.0, 416.0]

    assert probe_overhead_ratio(off, on) == pytest.approx(1.04)


@pytest.mark.parametrize(
    ("off", "on"),
    [([], []), ([100], [100, 101]), ([0], [1]), ([100], [float("nan")])],
)
def test_probe_overhead_ratio_rejects_unpaired_or_nonpositive_values(off, on):
    with pytest.raises(CalibrationError):
        probe_overhead_ratio(off, on)
