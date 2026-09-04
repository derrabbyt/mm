"""Run one job and exit.

    python -m app.jobs.runner scrape-events

`--every` adds a sleep loop so a compose container can stand in for a scheduler
locally; production runs the same command without it.
"""

import argparse
import logging
import sys
import time

from ..core.logging import setup_logging
from .registry import JOBS, load

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="app.jobs.runner")
    parser.add_argument("name", nargs="?", choices=sorted(JOBS), help="job to run")
    parser.add_argument(
        "--every",
        type=float,
        metavar="SECONDS",
        help="repeat forever, waiting this long after each run (development only)",
    )
    parser.add_argument(
        "--list", action="store_true", help="print the known job names and exit"
    )
    return parser.parse_args(argv)


def run_once(name: str) -> bool:
    """Run `name`, returning whether it succeeded. Never raises: a job that
    blows up must not take an `--every` loop down with it."""
    job = load(name)
    started = time.monotonic()
    logger.info("Job %s starting", name)
    try:
        job()
    except Exception:
        logger.exception("Job %s failed after %.1fs", name, time.monotonic() - started)
        return False
    logger.info("Job %s finished in %.1fs", name, time.monotonic() - started)
    return True


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    setup_logging()

    if args.list:
        for name in sorted(JOBS):
            print(name)
        return 0

    if args.name is None:
        logger.error("No job given. Use --list to see the known names.")
        return 2

    if args.every is None:
        return 0 if run_once(args.name) else 1

    logger.info("Repeating %s every %.0fs", args.name, args.every)
    while True:
        run_once(args.name)
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
