"""The scrape, composed.

Two modules meet here and nowhere else. `events` owns the catalogue and knows how
to fill it; `geocoding` owns the geocoder and its cache. Neither may import the
other: both do their work in layers that hold a session, and the rule those
layers live under is that they do not know other modules exist. So the wiring
happens above them, which is what a `router.py` does for a request and what this
file does for a job.

It sits in `app/jobs/` rather than in `events/jobs.py` for exactly that reason -
`jobs.py` is one of the session-holding layers. Above the modules is the only
place allowed to know about both, the same reasoning that puts `app/metadata.py`
where it is.
"""

import logging

from ..core.config import settings
from ..db.session import SessionLocal
from ..modules.events import public as events
from ..modules.geocoding import public as geocoding

logger = logging.getLogger(__name__)


def scrape_events() -> None:
    """Scrape every Source into the catalogue, placing what it can.

    Raises when a Source could not be fetched, so the runner exits non-zero and a
    scheduler can tell.
    """
    # Two sessions on purpose. A scrape commits one Source at a time, so that
    # its Listings and the record of how it went land together; the geocode
    # cache is not part of that bargain. Sharing one session would commit
    # half-written Listings every time an address resolved, and would throw away
    # lookups already paid for if the Source that prompted them failed after.
    with SessionLocal() as db, SessionLocal() as cache:
        stats = events.scrape(
            db, locate=geocoding.locator(cache, enabled=settings.geocoder_enabled)
        )

    failed = [one.source for one in stats if not one.fetched_ok]
    logger.info(
        "scrape-events: %d source(s), %d stored, %d quarantined",
        len(stats),
        sum(one.valid_count for one in stats),
        sum(one.quarantined_count for one in stats),
    )
    if failed:
        raise RuntimeError(f"source(s) failed to fetch: {', '.join(sorted(failed))}")
