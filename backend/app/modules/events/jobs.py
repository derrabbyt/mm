"""Scheduled work owned by the events module."""

import logging

logger = logging.getLogger(__name__)


def scrape_events() -> None:
    """Stub. The scraper still lives in the activity-loader repo; when it moves
    here this module owns the tables `models.py` currently only reads."""
    logger.info("scrape-events: ran (no scraping implemented yet)")
