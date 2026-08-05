from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ready", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "pid").write_text(str(os.getpid()), encoding="utf-8")
    if args.ready:
        (args.output / "ready").touch(exist_ok=False)

    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    deadline = time.monotonic() + args.sleep
    while not stopped and time.monotonic() < deadline:
        time.sleep(0.01)
    return args.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

