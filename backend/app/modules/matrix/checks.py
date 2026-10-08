"""Two reasons to refuse a Dataset that is shaped exactly right.

Everything the format can say about a Dataset is about its shape, and both
failures here produce a Dataset of perfect shape: thirteen matrices, the right
dtype, the right dimensions, a manifest that validates. What is wrong is the
content, and in both cases it is wrong in a way nobody would have questioned.

Pure functions over a built Dataset, run before it is published. Neither needs
a bake to exercise, which is the point - three hours and a JVM is not a test.
"""

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
    """Share of origin/destination pairs that have a route at all."""
    return 100.0 * float((np.asarray(matrix) != UNREACHABLE).sum()) / matrix.size


def check_transit_coverage(
    metadata: DatasetMetadata,
    matrices: Mapping[str, np.ndarray],
    *,
    floor_pct: float,
) -> None:
    """Refuse a Dataset whose transit matrices reach almost nothing.

    This is what a Departure outside the transit feed's validity window looks
    like from the outside: the router finds no service, every pair comes back
    unreachable, and the bake writes matrices in which nothing can be got to by
    public transport. Neither the writer nor the reader could tell that from a
    city with poor transit, so there is a floor and it is a deployment setting.

    Measured here rather than read from the manifest's own `coverage_pct`: a
    bake that got this wrong would have written that number from the same
    arrays, so trusting it would be asking the suspect for an alibi.
    """
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
    """Refuse a Dataset whose Saturday matrices are its Sunday ones.

    A Saturday Departure that lands on a public holiday is routed against the
    Sunday timetable, and the two matrices come out byte-identical. Cheap to
    check and it would have caught the defect in the shipped Dataset on the day
    it was introduced: Wiener Linien's own feed defines 34 Saturday-only and 38
    Sunday-only service patterns and runs 15% more trips on a Saturday, so two
    identical matrices are never the truth about the city.
    """
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
