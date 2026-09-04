"""Scheduled work owned by the geodata module."""

import logging

logger = logging.getLogger(__name__)


def ingest_geodata() -> None:
    """Stub. Pulls the OSM extract and GTFS feed that `matrix` and `poi` both
    read back out of shared storage."""
    logger.info("ingest-geodata: ran (no ingestion implemented yet)")
