import json
from dataclasses import replace
from pathlib import Path

from hami_tail_pilot.config import load_config
from hami_tail_pilot.execution import execute_schedule
from hami_tail_pilot.runner import RunResult, RuntimeAssets


def resolved_config():
    return replace(load_config(Path("configs/pilot.yaml")), victim_target_qps=10.0)


def dummy_assets(tmp_path):
    model = tmp_path / "model"
    dataset = tmp_path / "dataset"
    vocab = tmp_path / "vocab"
    for path in (model, dataset, vocab):
        path.write_text("x", encoding="utf-8")
    return RuntimeAssets("image:test", model, dataset, vocab)


class FakeRunOne:
    def __init__(self, fail_condition=None):
        self.fail_condition = fail_condition
        self.calls = []

    def __call__(self, spec, _config, root, *, assets, **_kwargs):
        self.calls.append(spec.run_id)
        run_dir = root / spec.run_id
        run_dir.mkdir(parents=True)
        status = "failed" if spec.condition.name == self.fail_condition else "complete"
        error = "fixture failure" if status == "failed" else None
        (run_dir / "status.json").write_text(
            json.dumps({"status": status, "error": error}), encoding="utf-8"
        )
        return RunResult(run_dir, status, error)


def test_execute_schedule_runs_all_thirty_conditions_and_marks_experiment_complete(tmp_path):
    runner = FakeRunOne()

    summary = execute_schedule(
        resolved_config(), tmp_path / "experiment", dummy_assets(tmp_path), run_one=runner
    )

    assert summary.completed == 30
    assert summary.failed == 0
    assert len(runner.calls) == 30
    assert json.loads((tmp_path / "experiment" / "status.json").read_text())["status"] == "complete"


def test_execute_schedule_skips_complete_runs_on_resume(tmp_path):
    runner = FakeRunOne()
    output = tmp_path / "experiment"
    execute_schedule(resolved_config(), output, dummy_assets(tmp_path), run_one=runner)
    second = FakeRunOne()

    summary = execute_schedule(resolved_config(), output, dummy_assets(tmp_path), run_one=second)

    assert summary.skipped == 30
    assert second.calls == []


def test_execute_schedule_archives_failed_run_only_when_rerun_is_explicit(tmp_path):
    output = tmp_path / "experiment"
    first = FakeRunOne(fail_condition="C2")
    initial = execute_schedule(resolved_config(), output, dummy_assets(tmp_path), run_one=first)
    blocked = FakeRunOne()

    without_rerun = execute_schedule(
        resolved_config(), output, dummy_assets(tmp_path), run_one=blocked, rerun_failed=False
    )

    assert initial.failed == 5
    assert without_rerun.failed == 5
    assert blocked.calls == []

    retry = FakeRunOne()
    recovered = execute_schedule(
        resolved_config(), output, dummy_assets(tmp_path), run_one=retry, rerun_failed=True
    )

    assert recovered.failed == 0
    assert len(retry.calls) == 5
    assert len(list((output / "attempts").glob("*-attempt01"))) == 5
