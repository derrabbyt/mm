"""What the bake does around the three hours in the middle.

Nothing here runs a bake - that is a JVM and roughly four gigabytes - but
everything a bake does that a *reader* can observe happens in these functions:
the grid it builds, the Dataset it refuses, and the rename that publishes one.
"""

import json

import numpy as np
import pytest

from app.core import dataset as fmt
from app.modules.matrix.checks import DatasetRejected
from app.modules.matrix.profile import BakeProfile
from app.modules.matrix.service import (
    BakeError,
    _matrix_writer,
    _write_atomically,
    build_grid,
    commit_grid,
    publish_dataset,
)

PROFILE = BakeProfile(
    city="testville",
    south=48.20,
    north=48.22,
    west=16.36,
    east=16.39,
    h3_res=9,
    grid_major=1,
    departure_window_min=10,
)
FLOOR = 10.0


def manifest_of(folder):
    return fmt.read_manifest(folder / "manifest.json")


# --- the grid -----------------------------------------------------------


def test_the_grid_comes_out_of_the_profile_and_comes_out_the_same_every_time():
    """Cell ids are what a Dataset's rows and columns mean, so the same profile
    has to produce the same numbering or a rebuild renumbers the city."""
    first, second = build_grid(PROFILE), build_grid(PROFILE)

    assert first.n_cells > 0
    assert first.grid_version == "v1_h3r9"
    assert [cell.h3 for cell in first.cells] == [cell.h3 for cell in second.cells]
    assert [cell.id for cell in first.cells] == list(range(first.n_cells))


def test_a_grid_written_once_is_verified_rather_than_overwritten(tmp_path):
    grid = build_grid(PROFILE)
    first = commit_grid(tmp_path, grid)
    assert commit_grid(tmp_path, grid) == first


def test_a_folder_holding_another_grid_under_the_same_version_is_refused(tmp_path):
    commit_grid(tmp_path, build_grid(PROFILE))
    wider = BakeProfile(**{**PROFILE.__dict__, "east": 16.45})

    with pytest.raises(BakeError, match="immutable"):
        commit_grid(tmp_path, build_grid(wider))


def test_a_folder_holding_a_different_grid_version_is_refused(tmp_path):
    commit_grid(tmp_path, build_grid(PROFILE))
    bumped = BakeProfile(**{**PROFILE.__dict__, "grid_major": 2})

    with pytest.raises(BakeError, match="directory"):
        commit_grid(tmp_path, build_grid(bumped))


# --- writing a matrix ---------------------------------------------------


def test_a_matrix_appears_under_its_own_name_or_not_at_all(tmp_path):
    """It is written beside the name and renamed on top, and the name a
    manifest will point at has to be the one that ends up there."""
    matrix = np.arange(9, dtype=np.uint16).reshape(3, 3)
    path = tmp_path / "walk__abcdef123456.npy"

    _write_atomically(path, _matrix_writer(matrix))

    assert [p.name for p in tmp_path.iterdir()] == [path.name]
    assert np.array_equal(np.load(path), matrix)


def test_nothing_is_left_behind_when_writing_a_matrix_fails(tmp_path):
    def explode(_path):
        raise OSError("disk full")

    with pytest.raises(BakeError, match="disk full"):
        _write_atomically(tmp_path / "walk__abcdef123456.npy", explode)
    assert not list(tmp_path.iterdir())


# --- publication --------------------------------------------------------


def test_a_dataset_is_published_by_its_manifest_appearing(write_dataset):
    folder = write_dataset()
    manifest = manifest_of(folder)
    (folder / "manifest.json").unlink()

    publish_dataset(folder, manifest, FLOOR)

    assert fmt.load_metadata(folder).manifest.grid_version == manifest.grid_version
    assert not list(folder.glob(".*staged*"))


def test_a_dataset_failing_the_coverage_check_is_never_published(write_dataset):
    """The previous Dataset is what the API is serving. A bad bake has to leave
    it exactly where it is, because the alternative is a travel-time service
    answering out of empty matrices."""
    empty = np.full((3, 3), fmt.UNREACHABLE, dtype=np.uint16)
    folder = write_dataset({"transit_wed12": empty})
    manifest = manifest_of(folder)
    previous = (folder / "manifest.json").read_text()

    with pytest.raises(DatasetRejected, match="transit_wed12"):
        publish_dataset(folder, manifest, FLOOR)

    assert (folder / "manifest.json").read_text() == previous
    assert not list(folder.glob(".*staged*"))


def test_a_dataset_failing_the_saturday_check_is_never_published(write_dataset):
    same = np.full((3, 3), 900, dtype=np.uint16)
    folder = write_dataset({"transit_sat20": same, "transit_sun20": same})
    manifest = manifest_of(folder)
    previous = (folder / "manifest.json").read_text()

    with pytest.raises(DatasetRejected, match="transit_sat20"):
        publish_dataset(folder, manifest, FLOOR)

    assert (folder / "manifest.json").read_text() == previous


def test_a_manifest_the_reader_could_not_load_fails_here_instead(write_dataset):
    """The whole reason the writer and the reader share one definition: the
    Dataset is read back through it before it is published."""
    folder = write_dataset()
    manifest = manifest_of(folder)
    (folder / manifest.matrices["car"].file).unlink()

    with pytest.raises(fmt.DatasetFormatError, match="car"):
        publish_dataset(folder, manifest, FLOOR)


def test_the_published_manifest_records_the_extract_and_the_feed(write_dataset):
    folder = write_dataset()
    recorded = json.loads((folder / "manifest.json").read_text())["sources"]

    assert set(recorded) == {"osm", "gtfs"}
    for source in recorded.values():
        assert len(source["sha256"]) == 64
        assert source["path"]
