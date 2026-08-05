from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when a pilot configuration changes the approved design."""


@dataclass(frozen=True)
class Condition:
    name: str
    victim_sm_limit: int
    neighbor_enabled: bool
    neighbor_sm_limit: int | None


@dataclass(frozen=True)
class PilotConfig:
    seed: int
    blocks: int
    warmup_seconds: int
    victim_duration_seconds: int
    neighbor_duration_seconds: int
    victim_target_qps: float | None
    conditions: tuple[Condition, ...]


_PILOT_KEYS = {
    "seed",
    "blocks",
    "warmup_seconds",
    "victim_duration_seconds",
    "neighbor_duration_seconds",
    "victim_target_qps",
    "conditions",
}
_CONDITION_KEYS = {
    "name",
    "victim_sm_limit",
    "neighbor_enabled",
    "neighbor_sm_limit",
}
_FIXED_CONDITIONS = {
    "P0": (100, False, None),
    "P1": (50, False, None),
    "P2": (100, True, 100),
    "P3": (50, True, 50),
}


def _positive_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if type(value) is not int or value <= 0:
        raise ConfigError(f"{key} must be positive")
    return value


def _parse_condition(raw: Any) -> Condition:
    if not isinstance(raw, dict):
        raise ConfigError("each condition must be a mapping")
    unknown = set(raw) - _CONDITION_KEYS
    if unknown:
        raise ConfigError(f"unknown condition keys: {sorted(unknown)}")
    missing = _CONDITION_KEYS - set(raw)
    if missing:
        raise ConfigError(f"missing condition keys: {sorted(missing)}")

    name = raw["name"]
    if name not in _FIXED_CONDITIONS:
        raise ConfigError("conditions must be exactly P0, P1, P2, P3")
    expected = _FIXED_CONDITIONS[name]
    actual = (
        raw["victim_sm_limit"],
        raw["neighbor_enabled"],
        raw["neighbor_sm_limit"],
    )
    if actual != expected:
        raise ConfigError(f"{name} does not match the fixed pilot design")
    return Condition(name, *expected)


def load_config(path: Path) -> PilotConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read configuration: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("top-level YAML value must be a mapping")

    unknown = set(raw) - _PILOT_KEYS
    if unknown:
        raise ConfigError(f"unknown pilot keys: {sorted(unknown)}")
    missing = _PILOT_KEYS - set(raw)
    if missing:
        raise ConfigError(f"missing pilot keys: {sorted(missing)}")

    blocks = _positive_int(raw, "blocks")
    if blocks != 5:
        raise ConfigError("blocks must be exactly 5")
    seed = _positive_int(raw, "seed")
    warmup_seconds = _positive_int(raw, "warmup_seconds")
    victim_duration_seconds = _positive_int(raw, "victim_duration_seconds")
    neighbor_duration_seconds = _positive_int(raw, "neighbor_duration_seconds")

    target_qps = raw["victim_target_qps"]
    if target_qps is not None:
        if type(target_qps) not in (int, float) or target_qps <= 0:
            raise ConfigError("victim_target_qps must be positive or null")
        target_qps = float(target_qps)

    raw_conditions = raw["conditions"]
    if not isinstance(raw_conditions, list):
        raise ConfigError("conditions must be a list")
    conditions = tuple(_parse_condition(item) for item in raw_conditions)
    if [condition.name for condition in conditions] != ["P0", "P1", "P2", "P3"]:
        raise ConfigError("conditions must be exactly P0, P1, P2, P3 in order")

    return PilotConfig(
        seed=seed,
        blocks=blocks,
        warmup_seconds=warmup_seconds,
        victim_duration_seconds=victim_duration_seconds,
        neighbor_duration_seconds=neighbor_duration_seconds,
        victim_target_qps=target_qps,
        conditions=conditions,
    )

