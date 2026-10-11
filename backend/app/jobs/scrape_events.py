import logging

from ..core.config import settings
from ..db.session import ScraperSessionLocal
from ..modules.events import public as events
from ..modules.geocoding import public as geocoding

logger = logging.getLogger(__name__)


def scrape_events() -> None:
    if settings.scraper_configured:
        logger.info("scrape-events: connecting as %s", settings.scraper_postgres_user)
    else:
        logger.warning(
            "scrape-events: no scraper role configured, so this run connects as "
            "the application and can reach its tables - set "
            "SCRAPER_POSTGRES_PASSWORD"
        )

    # Two sessions on purpose. A scrape commits one Source at a time, so that
    # its Listings and the record of how it went land together; the geocode
    # cache is not part of that bargain. Sharing one session would commit
    # half-written Listings every time an address resolved, and would throw away
    # lookups already paid for if the Source that prompted them failed after.
    #
    # Both are the scraping role's: the geocode cache lives in `content` too, so
    # nothing a run does needs a connection that can write `public`.
    with ScraperSessionLocal() as db, ScraperSessionLocal() as cache:
        outcome = events.scrape(
            db, locate=geocoding.locator(cache, enabled=settings.geocoder_enabled)
        )

    if outcome.declined:
        logger.warning("scrape-events: %s, so this run did nothing", outcome.declined)
        return

    stats = outcome.sources
    failed = [one.source for one in stats if not one.fetched_ok]
    logger.info(
        "scrape-events: %d source(s), %d stored, %d quarantined",
        len(stats),
        sum(one.valid_count for one in stats),
        sum(one.quarantined_count for one in stats),
    )
    if failed:
        raise RuntimeError(f"source(s) failed to fetch: {', '.join(sorted(failed))}")
