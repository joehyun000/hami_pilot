from dataclasses import replace
import json
from pathlib import Path

import pytest

from hami_tail_pilot.calibration import (
    CalibrationError,
    choose_target_qps,
    execute_calibration,
    probe_overhead_ratio,
)
from hami_tail_pilot.config import load_config
from hami_tail_pilot.mlperf import MLPerfMetrics
from hami_tail_pilot.runner import RunResult, RuntimeAssets


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


def test_execute_calibration_runs_qps_sweep_and_three_paired_images(tmp_path):
    config = load_config(Path("configs/pilot.yaml"))
    input_file = tmp_path / "input"
    input_file.write_text("fixture", encoding="utf-8")
    probe_assets = RuntimeAssets("probe:image", input_file, input_file, input_file)
    vanilla_assets = replace(probe_assets, image_tag="vanilla:image")
    calls = []

    def fake_run(spec, run_config, root, *, assets):
        calls.append((spec.run_id, run_config.victim_target_qps, assets.image_tag))
        run_dir = root / spec.run_id
        victim = run_dir / "victim"
        victim.mkdir(parents=True)
        target = run_config.victim_target_qps
        achieved = target if target <= 4 else target * 0.90
        overhead = 1.04 if assets.image_tag == "probe:image" else 1.0
        (victim / "mlperf_log_summary.txt").write_text(
            "\n".join(
                (
                    "Result is : VALID",
                    f"Completed samples per second : {achieved}",
                    "50.00 percentile latency (ns) : 10000000",
                    f"99.00 percentile latency (ns) : {100000000 * overhead}",
                    "Completed samples : 1000",
                )
            ),
            encoding="utf-8",
        )
        (run_dir / "status.json").write_text('{"status":"complete"}', encoding="utf-8")
        return RunResult(run_dir, "complete", None)

    result = execute_calibration(
        config,
        tmp_path / "experiment",
        probe_assets,
        vanilla_assets,
        candidate_qps=(1.0, 2.0, 4.0, 8.0),
        run_one=fake_run,
    )

    assert result.target_qps == 2.8
    assert result.probe_overhead_ratio == pytest.approx(1.04)
    assert result.probe_overhead_pass is True
    assert len(calls) == 10
    assert [image for _, _, image in calls[-6:]] == [
        "vanilla:image",
        "probe:image",
        "probe:image",
        "vanilla:image",
        "vanilla:image",
        "probe:image",
    ]
    decision = json.loads(
        (tmp_path / "experiment" / "calibration_decision.json").read_text(
            encoding="utf-8"
        )
    )
    assert decision["target_qps"] == 2.8
    assert (tmp_path / "experiment" / "pilot.resolved.yaml").is_file()


def test_execute_calibration_stops_when_a_calibration_run_fails(tmp_path):
    config = load_config(Path("configs/pilot.yaml"))
    input_file = tmp_path / "input"
    input_file.write_text("fixture", encoding="utf-8")
    assets = RuntimeAssets("probe:image", input_file, input_file, input_file)

    def failed_run(spec, run_config, root, *, assets):
        return RunResult(root / spec.run_id, "failed", "simulated failure")

    with pytest.raises(CalibrationError, match="simulated failure"):
        execute_calibration(
            config,
            tmp_path / "experiment",
            assets,
            replace(assets, image_tag="vanilla:image"),
            candidate_qps=(1.0,),
            run_one=failed_run,
        )


@pytest.mark.parametrize("candidate_qps", [(), (0.0,), (float("nan"),), (1.0, 1.0)])
def test_execute_calibration_rejects_invalid_qps_before_starting_runs(
    tmp_path, candidate_qps
):
    config = load_config(Path("configs/pilot.yaml"))
    input_file = tmp_path / "input"
    input_file.write_text("fixture", encoding="utf-8")
    assets = RuntimeAssets("probe:image", input_file, input_file, input_file)
    called = False

    def should_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("invalid calibration input reached the runner")

    with pytest.raises(CalibrationError):
        execute_calibration(
            config,
            tmp_path / "experiment",
            assets,
            replace(assets, image_tag="vanilla:image"),
            candidate_qps=candidate_qps,
            run_one=should_not_run,
        )
    assert called is False
