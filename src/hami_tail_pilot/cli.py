from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from hami_tail_pilot.config import load_config
from hami_tail_pilot.schedule import build_schedule, write_schedule_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hami-tail-pilot")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("validate")
    schedule = subparsers.add_parser("schedule")
    schedule.add_argument("--config", type=Path, required=True)
    schedule.add_argument("--output", type=Path, required=True)
    for command in ("calibrate", "run", "analyze"):
        subparsers.add_parser(command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "schedule":
        config = load_config(args.config)
        schedule = build_schedule(config)
        write_schedule_json(schedule, args.output, seed=config.seed)
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
