import json
from pathlib import Path

from hami_tail_pilot.config import load_config
from hami_tail_pilot.runner import RunResult, RuntimeAssets
from hami_tail_pilot.smoke import execute_smoke


def _assets(tmp_path):
    value = tmp_path / "input"
    value.write_text("fixture", encoding="utf-8")
    return RuntimeAssets("probe:image", value, value, value)


def _write_summary(path):
    path.write_text(
        "\n".join(
            (
                "Result is : VALID",
                "Completed samples per second : 2.8",
                "50.00 percentile latency (ns) : 10000000",
                "99.00 percentile latency (ns) : 100000000",
                "Completed samples : 1000",
            )
        ),
        encoding="utf-8",
    )


def test_execute_smoke_checks_no_wait_and_quota_wait_paths(tmp_path):
    config = load_config(Path("configs/pilot.yaml"))
    config = config.__class__(**{**config.__dict__, "victim_target_qps": 2.8})

    def fake_run(spec, run_config, root, *, assets):
        run_dir = root / spec.run_id
        victim = run_dir / "victim"
        victim.mkdir(parents=True)
        _write_summary(victim / "mlperf_log_summary.txt")
        waited = 0 if spec.condition.name == "P0" else 3
        (victim / "hami_probe.jsonl").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "pid": 10,
                    "limiter_calls": 10,
                    "waited_calls": waited,
                    "sleep_calls": waited,
                    "wait_ns": waited * 1000,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return RunResult(run_dir, "complete", None)

    result = execute_smoke(
        config,
        tmp_path / "experiment",
        _assets(tmp_path),
        run_one=fake_run,
    )

    assert result.passed is True
    assert result.errors == ()
    report = json.loads(
        (tmp_path / "experiment" / "smoke.json").read_text(encoding="utf-8")
    )
    assert report["passed"] is True
    assert set(report["conditions"]) == {"P0", "P1", "P3"}


def test_execute_smoke_blocks_when_quota_condition_records_no_wait(tmp_path):
    config = load_config(Path("configs/pilot.yaml"))
    config = config.__class__(**{**config.__dict__, "victim_target_qps": 2.8})

    def fake_run(spec, run_config, root, *, assets):
        run_dir = root / spec.run_id
        victim = run_dir / "victim"
        victim.mkdir(parents=True)
        _write_summary(victim / "mlperf_log_summary.txt")
        (victim / "hami_probe.jsonl").write_text(
            '{"schema_version":1,"pid":10,"limiter_calls":10,'
            '"waited_calls":0,"sleep_calls":0,"wait_ns":0}\n',
            encoding="utf-8",
        )
        return RunResult(run_dir, "complete", None)

    result = execute_smoke(
        config,
        tmp_path / "experiment",
        _assets(tmp_path),
        run_one=fake_run,
    )

    assert result.passed is False
    assert "P1 did not record quota waits" in result.errors
    assert "P3 did not record quota waits" in result.errors
