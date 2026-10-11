import logging
from collections.abc import Mapping

import numpy as np

from ...core.dataset import (
    DEPARTURES,
    UNREACHABLE,
    DatasetMetadata,
    DayClass,
    departures_of,
)

logger = logging.getLogger(__name__)


class DatasetRejected(Exception):
    """A built Dataset that must not be published, and why."""


def coverage_pct(matrix: np.ndarray) -> float:
    return 100.0 * float((np.asarray(matrix) != UNREACHABLE).sum()) / matrix.size


def check_transit_coverage(
    metadata: DatasetMetadata,
    matrices: Mapping[str, np.ndarray],
    *,
    floor_pct: float,
) -> None:
    for departure in DEPARTURES:
        key = departure.matrix_key
        entry = metadata.manifest.matrices[key]
        covered = coverage_pct(matrices[key])
        if covered < floor_pct:
            raise DatasetRejected(
                f"{key} reaches {covered:.1f}% of cell pairs, below the "
                f"{floor_pct:.1f}% floor - the Departure {entry.departure} "
                "probably falls outside the transit feed's validity window"
            )
        logger.info("%s covers %.1f%% of cell pairs", key, covered)


def check_saturday_differs_from_sunday(matrices: Mapping[str, np.ndarray]) -> None:
    sundays = {
        departure.hour: departure.matrix_key
        for departure in departures_of(DayClass.SUNDAY)
    }
    for departure in departures_of(DayClass.SATURDAY):
        saturday_key = departure.matrix_key
        sunday_key = sundays[departure.hour]
        if np.array_equal(matrices[saturday_key], matrices[sunday_key]):
            raise DatasetRejected(
                f"{saturday_key} and {sunday_key} are identical, so the "
                "Saturday Departure was routed against Sunday service - it has "
                "most likely landed on a public holiday"
            )
