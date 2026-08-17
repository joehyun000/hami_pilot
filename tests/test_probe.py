from pathlib import Path
import subprocess

import pytest

from hami_tail_pilot.probe import ProbeLogError, parse_probe_jsonl


def test_probe_counters_can_be_reset_before_the_measurement_window(tmp_path):
    executable = tmp_path / "probe-counter-test"
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "tests/probe_counter_test.c",
            "-o",
            str(executable),
        ],
        check=True,
    )

    subprocess.run([str(executable)], check=True)


def test_parse_probe_jsonl_sums_multiple_worker_processes():
    metrics = parse_probe_jsonl(Path("tests/fixtures/hami_probe.jsonl"))

    assert metrics.limiter_calls == 1000
    assert metrics.waited_calls == 40
    assert metrics.sleep_calls == 83
    assert metrics.wait_ns == 812_000_000


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (
            '{"schema_version":2,"pid":1,"limiter_calls":1,"waited_calls":0,"sleep_calls":0,"wait_ns":0}',
            "unsupported probe schema",
        ),
        (
            '{"schema_version":1,"pid":1,"limiter_calls":1,"waited_calls":2,"sleep_calls":2,"wait_ns":1}',
            "waited_calls cannot exceed limiter_calls",
        ),
        (
            '{"schema_version":1,"pid":1,"limiter_calls":1,"waited_calls":0,"sleep_calls":-1,"wait_ns":0}',
            "probe counters must be nonnegative",
        ),
        ("not-json", "invalid probe JSON"),
    ],
)
def test_parse_probe_jsonl_rejects_corrupt_records(tmp_path, line, message):
    path = tmp_path / "probe.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    with pytest.raises(ProbeLogError, match=message):
        parse_probe_jsonl(path)


def test_parse_probe_jsonl_rejects_an_empty_log(tmp_path):
    path = tmp_path / "probe.jsonl"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ProbeLogError, match="probe log is empty"):
        parse_probe_jsonl(path)
