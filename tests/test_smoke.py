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


def test_execute_smoke_checks_expected_waits_for_each_role(tmp_path):
    config = load_config(Path("configs/pilot.yaml"))
    config = config.__class__(**{**config.__dict__, "victim_target_qps": 2.8})

    def fake_run(spec, run_config, root, *, assets):
        run_dir = root / spec.run_id
        victim = run_dir / "victim"
        victim.mkdir(parents=True)
        _write_summary(victim / "mlperf_log_summary.txt")
        expected_waits = {
            "C1": {"victim": 0},
            "C2": {"victim": 3},
            "C3": {"victim": 0, "neighbor": 0},
            "C4": {"victim": 3, "neighbor": 0},
            "C5": {"victim": 3, "neighbor": 3},
        }
        for role, waited in expected_waits[spec.condition.name].items():
            role_dir = run_dir / role
            role_dir.mkdir(parents=True, exist_ok=True)
            (role_dir / "hami_probe.jsonl").write_text(
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
    assert set(report["conditions"]) == {"C1", "C2", "C3", "C4", "C5"}
    assert report["conditions"]["C4"]["roles"]["victim"]["waited_calls"] == 3
    assert report["conditions"]["C4"]["roles"]["neighbor"]["waited_calls"] == 0


def test_execute_smoke_blocks_when_limited_roles_record_no_wait(tmp_path):
    config = load_config(Path("configs/pilot.yaml"))
    config = config.__class__(**{**config.__dict__, "victim_target_qps": 2.8})

    def fake_run(spec, run_config, root, *, assets):
        run_dir = root / spec.run_id
        victim = run_dir / "victim"
        victim.mkdir(parents=True)
        _write_summary(victim / "mlperf_log_summary.txt")
        roles = ("victim", "neighbor") if spec.condition.neighbor_enabled else ("victim",)
        for role in roles:
            role_dir = run_dir / role
            role_dir.mkdir(parents=True, exist_ok=True)
            (role_dir / "hami_probe.jsonl").write_text(
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
    assert "C2 victim did not record expected waits" in result.errors
    assert "C4 victim did not record expected waits" in result.errors
    assert "C5 victim did not record expected waits" in result.errors
    assert "C5 neighbor did not record expected waits" in result.errors
