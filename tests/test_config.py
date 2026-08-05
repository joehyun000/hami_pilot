from pathlib import Path

import pytest
import yaml

from hami_tail_pilot.config import ConfigError, load_config


def valid_payload() -> dict:
    return {
        "seed": 20260805,
        "blocks": 5,
        "warmup_seconds": 60,
        "victim_duration_seconds": 300,
        "neighbor_duration_seconds": 600,
        "victim_target_qps": None,
        "conditions": [
            {
                "name": "P0",
                "victim_sm_limit": 100,
                "neighbor_enabled": False,
                "neighbor_sm_limit": None,
            },
            {
                "name": "P1",
                "victim_sm_limit": 50,
                "neighbor_enabled": False,
                "neighbor_sm_limit": None,
            },
            {
                "name": "P2",
                "victim_sm_limit": 100,
                "neighbor_enabled": True,
                "neighbor_sm_limit": 100,
            },
            {
                "name": "P3",
                "victim_sm_limit": 50,
                "neighbor_enabled": True,
                "neighbor_sm_limit": 50,
            },
        ],
    }


def write_config(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "pilot.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_load_config_accepts_the_fixed_four_condition_design(tmp_path):
    config = load_config(write_config(tmp_path, valid_payload()))

    assert config.blocks == 5
    assert config.victim_target_qps is None
    assert [condition.name for condition in config.conditions] == ["P0", "P1", "P2", "P3"]
    assert config.conditions[3].neighbor_sm_limit == 50


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update(blocks=4), "blocks must be exactly 5"),
        (lambda data: data.update(warmup_seconds=0), "warmup_seconds must be positive"),
        (lambda data: data.update(victim_target_qps=0), "victim_target_qps must be positive"),
        (lambda data: data.update(unexpected=True), "unknown pilot keys"),
        (lambda data: data["conditions"].pop(), "conditions must be exactly P0, P1, P2, P3"),
        (
            lambda data: data["conditions"][1].update(victim_sm_limit=60),
            "P1 does not match the fixed pilot design",
        ),
        (
            lambda data: data["conditions"][0].update(unexpected=True),
            "unknown condition keys",
        ),
    ],
)
def test_load_config_rejects_changes_that_break_the_pilot_design(tmp_path, mutation, message):
    payload = valid_payload()
    mutation(payload)

    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path, payload))


def test_load_config_rejects_yaml_that_is_not_a_mapping(tmp_path):
    path = tmp_path / "pilot.yaml"
    path.write_text("- P0\n- P1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="top-level YAML value must be a mapping"):
        load_config(path)
