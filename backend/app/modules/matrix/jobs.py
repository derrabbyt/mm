import logging

from ...core.config import settings
from . import service

logger = logging.getLogger(__name__)


def bake_matrices() -> None:
    report = service.bake(
        osm=settings.matrix_osm_path,
        gtfs=settings.matrix_gtfs_path,
        into=settings.dataset_dir,
        min_transit_coverage_pct=settings.matrix_min_transit_coverage_pct,
    )
    logger.info("bake-matrices: %s", report.summary)
