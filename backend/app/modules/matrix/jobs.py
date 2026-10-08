"""Scheduled work owned by the matrix module."""

import logging

from ...core.config import settings
from . import service

logger = logging.getLogger(__name__)


def bake_matrices() -> None:
    """Bake geodata's OSM extract and transit feed into the Dataset that
    `modules/rendezvous` reads.

    Raises when the bake cannot run or when what it built must not be published,
    so the runner exits non-zero and a scheduler can tell. Nothing is published
    in that case: the previous Dataset stays exactly where it was and the API
    goes on serving it.
    """
    report = service.bake(
        osm=settings.matrix_osm_path,
        gtfs=settings.matrix_gtfs_path,
        into=settings.dataset_dir,
        min_transit_coverage_pct=settings.matrix_min_transit_coverage_pct,
    )
    logger.info("bake-matrices: %s", report.summary)
