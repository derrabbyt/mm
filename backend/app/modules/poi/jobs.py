"""Scheduled work owned by the poi module."""

import logging

logger = logging.getLogger(__name__)


def build_pois() -> None:
    """Stub. Derives points of interest from the OSM extract `geodata` writes."""
    logger.info("build-pois: ran (no derivation implemented yet)")
