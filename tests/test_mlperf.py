from pathlib import Path

import pytest

from hami_tail_pilot.mlperf import (
    MLPerfLogError,
    parse_mlperf_readiness_summary,
    parse_mlperf_summary,
    render_user_conf,
)


def test_render_user_conf_fixes_qps_and_measurement_duration():
    assert render_user_conf(12.5, 300_000) == (
        "*.Server.target_qps = 12.5\n"
        "*.Server.min_duration = 300000\n"
        "*.Server.target_duration = 300000\n"
    )


def test_parse_mlperf_summary_reads_valid_server_metrics():
    metrics = parse_mlperf_summary(Path("tests/fixtures/mlperf_log_summary.txt"))

    assert metrics.result_validity == "VALID"
    assert metrics.completed_samples_per_second == 12.375
    assert metrics.p50_ms == 8.125
    assert metrics.p99_ms == 43.75
    assert metrics.completed_samples == 3713


def test_parse_mlperf_summary_can_retain_invalid_candidate_metrics(tmp_path):
    path = tmp_path / "mlperf_log_summary.txt"
    path.write_text(
        "Result is : INVALID\n"
        "Completed samples per second : 7.2\n"
        "50.00 percentile latency (ns) : 10000000\n"
        "99.00 percentile latency (ns) : 200000000\n"
        "Completed samples : 1000\n",
        encoding="utf-8",
    )

    metrics = parse_mlperf_summary(path, require_valid=False)

    assert metrics.result_validity == "INVALID"
    assert metrics.completed_samples_per_second == 7.2


def test_parse_mlperf_readiness_accepts_a_short_run_that_only_misses_early_stopping():
    metrics = parse_mlperf_readiness_summary(
        Path("tests/fixtures/mlperf_log_summary_short_check.txt")
    )

    assert metrics.result_validity == "INVALID"
    assert metrics.completed_samples == 38
    assert metrics.completed_samples_per_second == 1.23
    assert metrics.scheduled_samples_per_second == 1.25
    assert metrics.p50_ms == 19.545328
    assert metrics.p99_ms == 28.357917


def test_parse_mlperf_readiness_reads_query_count_from_detail_log(tmp_path):
    summary_text = Path(
        "tests/fixtures/mlperf_log_summary_short_check.txt"
    ).read_text(encoding="utf-8")
    summary_path = tmp_path / "mlperf_log_summary.txt"
    summary_path.write_text(
        summary_text.replace("- Processed 38 queries.\n", ""),
        encoding="utf-8",
    )
    detail_path = tmp_path / "mlperf_log_detail.txt"
    detail_path.write_text(
        ':::MLLOG {"key": "result_query_count", "value": 38, '
        '"time_ms": 30001.0}\n',
        encoding="utf-8",
    )

    metrics = parse_mlperf_readiness_summary(summary_path, detail_path=detail_path)

    assert metrics.completed_samples == 38


def test_parse_mlperf_readiness_rejects_a_short_run_with_failed_constraints(tmp_path):
    summary = Path("tests/fixtures/mlperf_log_summary_short_check.txt").read_text(
        encoding="utf-8"
    )
    path = tmp_path / "mlperf_log_summary.txt"
    path.write_text(
        summary.replace("Performance constraints satisfied : Yes", "Performance constraints satisfied : No"),
        encoding="utf-8",
    )

    with pytest.raises(MLPerfLogError, match="short readiness run failed"):
        parse_mlperf_readiness_summary(path)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Result is : INVALID\n", "result is not VALID"),
        (
            "Result is : VALID\nCompleted samples per second : 1\n"
            "50.00 percentile latency (ns) : 100\nCompleted samples : 1\n",
            "missing 99th-percentile latency",
        ),
        (
            "Result is : VALID\nCompleted samples per second : nan\n"
            "50.00 percentile latency (ns) : 100\n"
            "99.00 percentile latency (ns) : 200\nCompleted samples : 1\n",
            "missing completed samples per second",
        ),
        (
            "Result is : VALID\nCompleted samples per second : 1\n"
            "50.00 percentile latency (ns) : 100\n"
            "99.00 percentile latency (ns) : 200\nCompleted samples : 0\n",
            "completed samples must be positive",
        ),
    ],
)
def test_parse_mlperf_summary_rejects_incomplete_or_invalid_runs(
    tmp_path, text, message
):
    path = tmp_path / "mlperf_log_summary.txt"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(MLPerfLogError, match=message):
        parse_mlperf_summary(path)


@pytest.mark.parametrize(("qps", "duration"), [(0, 300_000), (1, 0)])
def test_render_user_conf_rejects_nonpositive_settings(qps, duration):
    with pytest.raises(ValueError, match="must be positive"):
        render_user_conf(qps, duration)
