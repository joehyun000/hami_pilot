from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys

from hami_tail_pilot.calibration import (
    execute_calibration,
    CalibrationError,
    resolve_calibration_measurements,
    write_calibration_outputs,
)
from hami_tail_pilot.config import ConfigError, load_config
from hami_tail_pilot.execution import execute_schedule
from hami_tail_pilot.experiment import (
    ExperimentError,
    analyze_experiment,
    simulate_experiment,
)
from hami_tail_pilot.preflight import run_preflight
from hami_tail_pilot.runner import (
    DEFAULT_IMAGE_TAG,
    DEFAULT_VANILLA_IMAGE_TAG,
    RuntimeAssets,
)
from hami_tail_pilot.schedule import build_schedule, write_schedule_json
from hami_tail_pilot.smoke import execute_smoke


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hami-tail-pilot")
    subparsers = parser.add_subparsers(dest="command")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--config", type=Path, required=True)
    schedule = subparsers.add_parser("schedule")
    schedule.add_argument("--config", type=Path, required=True)
    schedule.add_argument("--output", type=Path, required=True)
    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--measurements", type=Path)
    calibrate.add_argument("--config", type=Path, required=True)
    calibrate.add_argument("--output", type=Path, required=True)
    calibrate.add_argument("--candidate-qps", type=float, nargs="+")
    calibrate.add_argument("--model-file", type=Path)
    calibrate.add_argument("--dataset-file", type=Path)
    calibrate.add_argument("--vocab-file", type=Path)
    calibrate.add_argument(
        "--source-manifest", type=Path, default=Path("artifacts/source_manifest.json")
    )
    calibrate.add_argument("--probe-image-tag", default=DEFAULT_IMAGE_TAG)
    calibrate.add_argument("--vanilla-image-tag", default=DEFAULT_VANILLA_IMAGE_TAG)
    calibrate.add_argument("--gpu-index", default="0")
    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--config", type=Path, required=True)
    smoke.add_argument("--output", type=Path, required=True)
    smoke.add_argument("--model-file", type=Path, required=True)
    smoke.add_argument("--dataset-file", type=Path, required=True)
    smoke.add_argument("--vocab-file", type=Path, required=True)
    smoke.add_argument(
        "--source-manifest", type=Path, default=Path("artifacts/source_manifest.json")
    )
    smoke.add_argument("--image-tag", default=DEFAULT_IMAGE_TAG)
    smoke.add_argument("--gpu-index", default="0")
    run = subparsers.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--model-file", type=Path)
    run.add_argument("--dataset-file", type=Path)
    run.add_argument("--vocab-file", type=Path)
    run.add_argument(
        "--source-manifest", type=Path, default=Path("artifacts/source_manifest.json")
    )
    run.add_argument("--image-tag", default=DEFAULT_IMAGE_TAG)
    run.add_argument("--gpu-index", default="0")
    run.add_argument("--preflight-only", action="store_true")
    run.add_argument("--rerun-failed", action="store_true")
    analyze = subparsers.add_parser("analyze")
    analyze.add_argument("--input", type=Path, required=True)
    analyze.add_argument("--probe-overhead-ratio", type=float)
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
            print(
                f"valid pilot config: {len(config.conditions)} conditions, {config.blocks} blocks"
            )
        elif args.command == "schedule":
            config = load_config(args.config)
            schedule = build_schedule(config)
            write_schedule_json(schedule, args.output, seed=config.seed)
        elif args.command == "calibrate":
            if args.measurements is not None:
                target_qps, overhead = resolve_calibration_measurements(
                    args.measurements
                )
                write_calibration_outputs(
                    args.config, args.output, target_qps, overhead
                )
            else:
                required = {
                    "--candidate-qps": args.candidate_qps,
                    "--model-file": args.model_file,
                    "--dataset-file": args.dataset_file,
                    "--vocab-file": args.vocab_file,
                }
                missing = [name for name, value in required.items() if not value]
                if missing:
                    print(
                        "automatic calibration requires: " + ", ".join(missing),
                        file=sys.stderr,
                    )
                    return 1
                config = load_config(args.config)
                probe_assets = RuntimeAssets(
                    args.probe_image_tag,
                    args.model_file,
                    args.dataset_file,
                    args.vocab_file,
                    args.gpu_index,
                )
                vanilla_assets = RuntimeAssets(
                    args.vanilla_image_tag,
                    args.model_file,
                    args.dataset_file,
                    args.vocab_file,
                    args.gpu_index,
                )
                preflights = [
                    run_preflight(probe_assets, args.source_manifest, args.output),
                    run_preflight(vanilla_assets, args.source_manifest, args.output),
                ]
                args.output.mkdir(parents=True, exist_ok=True)
                (args.output / "calibration_preflight.json").write_text(
                    json.dumps(
                        [
                            {
                                "ready": report.ready,
                                "errors": report.errors,
                                "warnings": report.warnings,
                                "environment": report.environment,
                            }
                            for report in preflights
                        ],
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                errors = [error for report in preflights for error in report.errors]
                if errors:
                    print(
                        "calibration preflight failed: " + "; ".join(errors),
                        file=sys.stderr,
                    )
                    return 1
                result = execute_calibration(
                    config,
                    args.output,
                    probe_assets,
                    vanilla_assets,
                    candidate_qps=args.candidate_qps,
                )
                target_qps = result.target_qps
                overhead = result.probe_overhead_ratio
            print(
                f"calibration target_qps={target_qps:g}, "
                f"probe_overhead_ratio={overhead:.6f}"
            )
            if overhead > 1.05:
                print("probe overhead exceeds 5%; pilot is blocked", file=sys.stderr)
                return 1
        elif args.command == "smoke":
            config = load_config(args.config)
            assets = RuntimeAssets(
                args.image_tag,
                args.model_file,
                args.dataset_file,
                args.vocab_file,
                args.gpu_index,
            )
            report = run_preflight(assets, args.source_manifest, args.output)
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "smoke_preflight.json").write_text(
                json.dumps(
                    {
                        "ready": report.ready,
                        "errors": report.errors,
                        "warnings": report.warnings,
                        "environment": report.environment,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            if not report.ready:
                print(
                    "smoke preflight failed: " + "; ".join(report.errors),
                    file=sys.stderr,
                )
                return 1
            result = execute_smoke(config, args.output, assets)
            if not result.passed:
                print("smoke failed: " + "; ".join(result.errors), file=sys.stderr)
                return 1
            print("smoke passed: each victim and neighbor wait path matched the design")
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
                    print(
                        "preflight failed: " + "; ".join(report.errors), file=sys.stderr
                    )
                    return 1
                if not args.preflight_only:
                    smoke_path = args.output / "smoke.json"
                    try:
                        smoke_passed = bool(
                            json.loads(smoke_path.read_text(encoding="utf-8")).get(
                                "passed"
                            )
                        )
                    except (OSError, json.JSONDecodeError):
                        smoke_passed = False
                    if not smoke_passed:
                        print(
                            "real run is blocked until the smoke command passes",
                            file=sys.stderr,
                        )
                        return 1
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
            overhead = args.probe_overhead_ratio
            if overhead is None:
                decision_path = args.input / "calibration_decision.json"
                try:
                    calibration = json.loads(decision_path.read_text(encoding="utf-8"))
                    overhead = float(calibration["probe_overhead_ratio"])
                except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                    print(
                        "analysis requires --probe-overhead-ratio or a valid "
                        "calibration_decision.json",
                        file=sys.stderr,
                    )
                    return 1
            analyze_experiment(args.input, overhead)
        return 0
    except (CalibrationError, ConfigError, ExperimentError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
