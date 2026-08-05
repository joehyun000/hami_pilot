from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Sequence

from hami_tail_pilot.config import Condition, PilotConfig


@dataclass(frozen=True)
class RunSpec:
    block: int
    order: int
    condition: Condition
    run_id: str


def build_schedule(config: PilotConfig) -> tuple[RunSpec, ...]:
    rng = random.Random(config.seed)
    schedule: list[RunSpec] = []
    for block in range(1, config.blocks + 1):
        conditions = list(config.conditions)
        rng.shuffle(conditions)
        for order, condition in enumerate(conditions, start=1):
            schedule.append(
                RunSpec(
                    block=block,
                    order=order,
                    condition=condition,
                    run_id=f"b{block:02d}-o{order:02d}-{condition.name}",
                )
            )
    return tuple(schedule)


def write_schedule_json(schedule: Sequence[RunSpec], path: Path, *, seed: int) -> None:
    payload = {
        "seed": seed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs": [
            {
                "block": run.block,
                "order": run.order,
                "condition": run.condition.name,
                "run_id": run.run_id,
            }
            for run in schedule
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
