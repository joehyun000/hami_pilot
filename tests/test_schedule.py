import json
from dataclasses import replace
from pathlib import Path

from hami_tail_pilot.config import load_config
from hami_tail_pilot.schedule import build_schedule, write_schedule_json


def test_build_schedule_has_one_of_each_condition_in_every_block():
    config = load_config(Path("configs/pilot.yaml"))

    schedule = build_schedule(config)

    assert len(schedule) == 30
    for block in range(1, 6):
        block_runs = [run for run in schedule if run.block == block]
        assert sorted(run.condition.name for run in block_runs) == [
            "C0", "C1", "C2", "C3", "C4", "C5"
        ]
        assert [run.order for run in block_runs] == [1, 2, 3, 4, 5, 6]
        assert all(run.run_id == f"b{block:02d}-o{run.order:02d}-{run.condition.name}" for run in block_runs)


def test_build_schedule_is_reproducible_without_changing_global_random_state():
    config = load_config(Path("configs/pilot.yaml"))

    first = build_schedule(config)
    second = build_schedule(config)
    different = build_schedule(replace(config, seed=20260806))

    assert first == second
    assert first != different


def test_write_schedule_json_preserves_seed_and_run_order(tmp_path):
    config = load_config(Path("configs/pilot.yaml"))
    schedule = build_schedule(config)
    output = tmp_path / "nested" / "schedule.json"

    write_schedule_json(schedule, output, seed=config.seed)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["seed"] == 20260805
    assert len(payload["runs"]) == 30
    assert payload["runs"][0]["run_id"] == schedule[0].run_id
    assert payload["runs"][-1]["condition"] == schedule[-1].condition.name
