from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys

from hami_tail_pilot.config import ConfigError, load_config
from hami_tail_pilot.execution import execute_schedule
from hami_tail_pilot.experiment import ExperimentError, analyze_experiment, simulate_experiment
from hami_tail_pilot.preflight import run_preflight
from hami_tail_pilot.runner import DEFAULT_IMAGE_TAG, RuntimeAssets
from hami_tail_pilot.schedule import build_schedule, write_schedule_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hami-tail-pilot")
    subparsers = parser.add_subparsers(dest="command")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--config", type=Path, required=True)
    schedule = subparsers.add_parser("schedule")
    schedule.add_argument("--config", type=Path, required=True)
    schedule.add_argument("--output", type=Path, required=True)
    subparsers.add_parser("calibrate")
    run = subparsers.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--model-file", type=Path)
    run.add_argument("--dataset-file", type=Path)
    run.add_argument("--vocab-file", type=Path)
    run.add_argument("--source-manifest", type=Path, default=Path("artifacts/source_manifest.json"))
    run.add_argument("--image-tag", default=DEFAULT_IMAGE_TAG)
    run.add_argument("--gpu-index", default="0")
    run.add_argument("--preflight-only", action="store_true")
    run.add_argument("--rerun-failed", action="store_true")
    analyze = subparsers.add_parser("analyze")
    analyze.add_argument("--input", type=Path, required=True)
    analyze.add_argument("--probe-overhead-ratio", type=float, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "validate":
            config = load_config(args.config)
            print(f"valid pilot config: {len(config.conditions)} conditions, {config.blocks} blocks")
        elif args.command == "schedule":
            config = load_config(args.config)
            schedule = build_schedule(config)
            write_schedule_json(schedule, args.output, seed=config.seed)
        elif args.command == "run":
            config = load_config(args.config)
            if args.dry_run:
                simulate_experiment(config, args.output)
            else:
                required = {
                    "--model-file": args.model_file,
                    "--dataset-file": args.dataset_file,
                    "--vocab-file": args.vocab_file,
                }
                missing = [name for name, value in required.items() if value is None]
                if missing:
                    print(f"real run requires: {', '.join(missing)}", file=sys.stderr)
                    return 1
                assets = RuntimeAssets(
                    image_tag=args.image_tag,
                    model_file=args.model_file,
                    dataset_file=args.dataset_file,
                    vocab_file=args.vocab_file,
                    gpu_index=args.gpu_index,
                )
                report = run_preflight(assets, args.source_manifest, args.output)
                args.output.mkdir(parents=True, exist_ok=True)
                (args.output / "environment.json").write_text(
                    json.dumps(report.environment, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                (args.output / "preflight.json").write_text(
                    json.dumps(
                        {
                            "ready": report.ready,
                            "errors": report.errors,
                            "warnings": report.warnings,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                if not report.ready:
                    print("preflight failed: " + "; ".join(report.errors), file=sys.stderr)
                    return 1
                if not args.preflight_only:
                    summary = execute_schedule(
                        config,
                        args.output,
                        assets,
                        rerun_failed=args.rerun_failed,
                    )
                    if summary.failed:
                        print("experiment has failed runs", file=sys.stderr)
                        return 1
        elif args.command == "analyze":
            analyze_experiment(args.input, args.probe_overhead_ratio)
        return 0
    except (ConfigError, ExperimentError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
