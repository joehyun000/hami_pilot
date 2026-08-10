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
                "name": "C0",
                "hami_enabled": False,
                "victim_sm_limit": None,
                "neighbor_enabled": False,
                "neighbor_sm_limit": None,
            },
            {
                "name": "C1",
                "hami_enabled": True,
                "victim_sm_limit": 100,
                "neighbor_enabled": False,
                "neighbor_sm_limit": None,
            },
            {
                "name": "C2",
                "hami_enabled": True,
                "victim_sm_limit": 50,
                "neighbor_enabled": False,
                "neighbor_sm_limit": None,
            },
            {
                "name": "C3",
                "hami_enabled": True,
                "victim_sm_limit": 100,
                "neighbor_enabled": True,
                "neighbor_sm_limit": 100,
            },
            {
                "name": "C4",
                "hami_enabled": True,
                "victim_sm_limit": 50,
                "neighbor_enabled": True,
                "neighbor_sm_limit": 100,
            },
            {
                "name": "C5",
                "hami_enabled": True,
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


def test_load_config_accepts_the_fixed_six_condition_design(tmp_path):
    config = load_config(write_config(tmp_path, valid_payload()))

    assert config.blocks == 5
    assert config.victim_target_qps is None
    assert [condition.name for condition in config.conditions] == [
        "C0", "C1", "C2", "C3", "C4", "C5"
    ]
    assert config.conditions[0].hami_enabled is False
    assert config.conditions[0].victim_sm_limit is None
    assert config.conditions[4].neighbor_sm_limit == 100
    assert config.conditions[5].neighbor_sm_limit == 50


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update(blocks=4), "blocks must be exactly 5"),
        (lambda data: data.update(warmup_seconds=0), "warmup_seconds must be positive"),
        (lambda data: data.update(victim_target_qps=0), "victim_target_qps must be positive"),
        (lambda data: data.update(unexpected=True), "unknown pilot keys"),
        (
            lambda data: data["conditions"].pop(),
            "conditions must be exactly C0, C1, C2, C3, C4, C5",
        ),
        (
            lambda data: data["conditions"][2].update(victim_sm_limit=60),
            "C2 does not match the fixed pilot design",
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
    path.write_text("- C0\n- C1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="top-level YAML value must be a mapping"):
        load_config(path)
