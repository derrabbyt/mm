"""The two checks that refuse a Dataset nobody would have questioned.

Both failures produce matrices of exactly the right shape and entirely the
wrong content, so neither is visible in anything the format can say. They are
pure functions over a Dataset on disk: no bake, no routing engine, no JVM.
"""

import numpy as np
import pytest

from app.core import dataset as fmt
from app.modules.matrix.checks import (
    DatasetRejected,
    check_saturday_differs_from_sunday,
    check_transit_coverage,
)

FLOOR = 10.0


def opened(folder):
    """A written Dataset as the checks see it: its metadata and its matrices."""
    metadata = fmt.load_metadata(folder)
    return metadata, fmt.open_matrices(folder, metadata.manifest)


# --- transit coverage ---------------------------------------------------


def test_a_dataset_whose_transit_still_goes_anywhere_is_accepted(write_dataset):
    metadata, matrices = opened(write_dataset())
    check_transit_coverage(metadata, matrices, floor_pct=FLOOR)


def test_transit_that_reaches_nothing_is_rejected_naming_the_matrix(write_dataset):
    """A Departure outside the feed's validity window routes against no service
    at all, and the bake writes a perfectly-shaped matrix of sentinels."""
    empty = np.full((3, 3), fmt.UNREACHABLE, dtype=np.uint16)
    metadata, matrices = opened(write_dataset({"transit_sat14": empty}))

    with pytest.raises(DatasetRejected) as raised:
        check_transit_coverage(metadata, matrices, floor_pct=FLOOR)

    assert "transit_sat14" in str(raised.value)
    assert "0.0" in str(raised.value)
    assert "10.0" in str(raised.value)


def test_coverage_is_measured_rather_than_read_out_of_the_manifest(write_dataset):
    """The manifest's own `coverage_pct` is what a wrong bake would also be
    wrong about, so the check counts the sentinels itself."""
    folder = write_dataset()
    empty = np.full((3, 3), fmt.UNREACHABLE, dtype=np.uint16)
    np.save(folder / "transit_wed08.npy", empty, allow_pickle=False)

    metadata, matrices = opened(folder)
    assert metadata.manifest.matrices["transit_wed08"].coverage_pct == 100.0
    with pytest.raises(DatasetRejected, match="transit_wed08"):
        check_transit_coverage(metadata, matrices, floor_pct=FLOOR)


def test_a_static_matrix_is_not_held_to_the_transit_floor(write_dataset):
    """Walk, bike and car reach what the street network reaches; the floor is
    about a timetable having been found."""
    empty = np.full((3, 3), fmt.UNREACHABLE, dtype=np.uint16)
    metadata, matrices = opened(write_dataset({"bike": empty}))
    check_transit_coverage(metadata, matrices, floor_pct=FLOOR)


# --- Saturday is not Sunday ---------------------------------------------


def test_a_dataset_whose_weekend_days_differ_is_accepted(write_dataset):
    _, matrices = opened(write_dataset())
    check_saturday_differs_from_sunday(matrices)


def test_identical_weekend_matrices_are_rejected_naming_the_pair(write_dataset):
    """Reproduces the shipped Dataset: its Saturday Departure fell on a public
    holiday, on which the operator runs the Sunday timetable, so every Saturday
    rendezvous is computed from Sunday service."""
    sunday = np.full((3, 3), 480, dtype=np.uint16)
    _, matrices = opened(
        write_dataset({"transit_sat14": sunday, "transit_sun14": sunday})
    )

    with pytest.raises(DatasetRejected) as raised:
        check_saturday_differs_from_sunday(matrices)

    assert "transit_sat14" in str(raised.value)
    assert "transit_sun14" in str(raised.value)
