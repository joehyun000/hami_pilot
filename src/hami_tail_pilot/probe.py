from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProbeLogError(ValueError):
    """Raised when a HAMi probe log is missing or inconsistent."""


@dataclass(frozen=True)
class ProbeMetrics:
    limiter_calls: int
    waited_calls: int
    sleep_calls: int
    wait_ns: int


_REQUIRED_KEYS = {
    "schema_version",
    "pid",
    "limiter_calls",
    "waited_calls",
    "sleep_calls",
    "wait_ns",
}
_COUNTER_KEYS = ("limiter_calls", "waited_calls", "sleep_calls", "wait_ns")


def _validate_record(record: Any, line_number: int) -> dict[str, int]:
    if not isinstance(record, dict):
        raise ProbeLogError(f"probe record {line_number} must be an object")
    if set(record) != _REQUIRED_KEYS:
        raise ProbeLogError(f"probe record {line_number} has unexpected schema keys")
    if record["schema_version"] != 1:
        raise ProbeLogError(f"unsupported probe schema: {record['schema_version']}")
    if type(record["pid"]) is not int or record["pid"] <= 0:
        raise ProbeLogError("probe pid must be positive")
    if any(type(record[key]) is not int or record[key] < 0 for key in _COUNTER_KEYS):
        raise ProbeLogError("probe counters must be nonnegative integers")
    if record["waited_calls"] > record["limiter_calls"]:
        raise ProbeLogError("waited_calls cannot exceed limiter_calls")
    if record["sleep_calls"] < record["waited_calls"]:
        raise ProbeLogError("sleep_calls cannot be smaller than waited_calls")
    return record


def parse_probe_jsonl(path: Path) -> ProbeMetrics:
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as exc:
        raise ProbeLogError(f"cannot read probe log: {exc}") from exc
    if not lines:
        raise ProbeLogError("probe log is empty")

    totals = {key: 0 for key in _COUNTER_KEYS}
    for line_number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProbeLogError(f"invalid probe JSON on line {line_number}") from exc
        validated = _validate_record(record, line_number)
        for key in _COUNTER_KEYS:
            totals[key] += validated[key]

    return ProbeMetrics(**totals)

