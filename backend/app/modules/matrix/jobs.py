"""Scheduled work owned by the matrix module."""

import logging

logger = logging.getLogger(__name__)


def bake_matrices() -> None:
    """Stub. Bakes geodata's OSM/GTFS into the travel-time dataset that
    `modules/rendezvous` reads - format in app/data/ttm_backend_integration.md.
    The bake still lives in the ttm repo; it needs r5py and a JDK."""
    logger.info("bake-matrices: ran (no baking implemented yet)")
