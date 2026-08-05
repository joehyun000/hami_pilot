from __future__ import annotations

import argparse
from collections.abc import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hami-tail-pilot")
    subparsers = parser.add_subparsers(dest="command")
    for command in ("validate", "schedule", "calibrate", "run", "analyze"):
        subparsers.add_parser(command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
