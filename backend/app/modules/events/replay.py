"""Re-run a Source's parse over what the site actually served.

    python -m app.modules.events.replay rausgegangen
    python -m app.modules.events.replay rausgegangen --day 2026-09-27
    python -m app.modules.events.replay rausgegangen --day 2026-09-27 --verbose

This is what the archive is for, made reachable. A parse is a pure function from
one document to Listings, so fixing a broken one is a matter of having the
document - and by the time anyone notices, the site has moved on. The archive
keeps it; this reads it back.

No network, no database, and no session: it fetches nothing and writes nothing.
Point it at a Source and it parses every payload archived for it, reporting what
came out and what blew up, so the edit-and-re-run loop is seconds long and does
not involve asking the site anything.
"""

import argparse
import datetime as dt
import logging
import sys
from collections.abc import Sequence

from ...core.logging import setup_logging
from . import archive, sources

logger = logging.getLogger(__name__)


def replay(source_name: str, day: dt.date | None = None, *, verbose: bool = False):
    """Parse every archived payload for one Source. Returns what came out."""
    discovered = sources.discover()
    source = discovered.get(source_name)
    if source is None:
        raise SystemExit(
            f"no Source named {source_name!r}. Known: {', '.join(sorted(discovered))}"
        )

    listings = []
    payloads = failures = 0

    for payload in archive.payloads_for(source_name, day):
        payloads += 1
        try:
            found = list(source.parse(payload))
        except Exception:
            # The point of the exercise: report which document it was and keep
            # going, so one run shows every payload that breaks rather than the
            # first. The traceback is what someone came for.
            failures += 1
            logger.exception("parse failed on %s", payload.url)
            continue
        listings.extend(found)
        if verbose:
            for one in found:
                logger.info(
                    "  %s %s | %s",
                    one.source_ref,
                    one.occurrences[0].start if one.occurrences else "no dates",
                    one.title,
                )

    if not payloads:
        logger.warning(
            "nothing archived for %s%s - the archive keeps %d day(s)",
            source_name,
            f" on {day}" if day else "",
            archive.settings.scrape_archive_retention_days,
        )
    logger.info(
        "%s: %d payload(s), %d listing(s), %d parse failure(s)",
        source_name,
        payloads,
        len(listings),
        failures,
    )
    return listings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.modules.events.replay")
    parser.add_argument("source", help="the Source whose archive to parse")
    parser.add_argument(
        "--day", type=dt.date.fromisoformat, help="one archived day (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print every Listing that came out"
    )
    args = parser.parse_args(argv)

    setup_logging()
    replay(args.source, args.day, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
