"""Scheduled work owned by the events module."""

import logging

from ...db.session import SessionLocal
from . import service

logger = logging.getLogger(__name__)


def scrape_events() -> None:
    """Scrape every Source into the catalogue.

    No logic of its own beyond opening a session: a job that reimplements what
    the service already does is two code paths that will drift. It raises when a
    run fails so the runner exits non-zero and a scheduler can tell.
    """
    with SessionLocal() as db:
        stats = service.scrape(db)

    failed = [one.source for one in stats if not one.fetched_ok]
    logger.info(
        "scrape-events: %d source(s), %d stored, %d quarantined",
        len(stats),
        sum(one.valid_count for one in stats),
        sum(one.quarantined_count for one in stats),
    )
    if failed:
        raise RuntimeError(f"source(s) failed to fetch: {', '.join(sorted(failed))}")
