import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.core import dataset as fmt
from app.core.config import settings
from app.core.dataset import DatasetFormatError
from app.core.enums import TravelMode

FIXTURES = Path(__file__).parent / "fixtures" / "dataset"
VIENNA = ZoneInfo("Europe/Vienna")

shipped = pytest.mark.skipif(
    not (settings.dataset_dir / "manifest.json").exists(),
    reason=f"no baked dataset at {settings.dataset_dir}",
)


def test_the_departure_table_names_the_thirteen_matrices_a_dataset_carries():
    assert fmt.MATRIX_KEYS == (
        "walk",
        "bike",
        "car",
        "transit_wed08",
        "transit_wed12",
        "transit_wed18",
        "transit_wed22",
        "transit_sat10",
        "transit_sat14",
        "transit_sat20",
        "transit_sun10",
        "transit_sun14",
        "transit_sun20",
    )


def test_every_departure_carries_its_own_matrix_key():
    keys = [departure.matrix_key for departure in fmt.DEPARTURES]
    assert len(set(keys)) == len(keys)
    assert set(keys) == set(fmt.TRANSIT_MATRIX_KEYS)


@pytest.mark.parametrize(
    ("day", "hour", "expected"),
    [
        (5, 8, "transit_wed08"),
        (5, 12, "transit_wed12"),
        (5, 18, "transit_wed18"),
        (8, 10, "transit_sat10"),
        (8, 20, "transit_sat20"),
        (9, 14, "transit_sun14"),
    ],
)
def test_a_transit_key_follows_the_day_class_and_the_nearest_hour(day, hour, expected):
    assert fmt.pick_transit_key(datetime(2026, 8, day, hour, tzinfo=VIENNA)) == expected


def test_after_midnight_wraps_round_to_the_late_evening_bake():
    late = datetime(2026, 8, 5, 0, 30, tzinfo=VIENNA)
    assert fmt.pick_transit_key(late) == "transit_wed22"


def test_a_static_mode_has_one_time_independent_matrix():
    assert fmt.STATIC_MATRIX_KEY[TravelMode.WALK] == "walk"
    assert fmt.STATIC_MATRIX_KEY[TravelMode.BICYCLE] == "bike"
    assert fmt.STATIC_MATRIX_KEY[TravelMode.CAR] == "car"
    assert TravelMode.TRANSIT not in fmt.STATIC_MATRIX_KEY


@shipped
def test_the_shipped_dataset_validates_and_yields_the_values_the_reader_needs():
    metadata = fmt.load_metadata(settings.dataset_dir)

    assert metadata.manifest.format_version == fmt.FORMAT_VERSION
    assert metadata.grid.grid_version == metadata.manifest.grid_version
    assert metadata.n_cells == len(metadata.grid.cells)
    assert metadata.timezone == VIENNA
    assert set(fmt.MATRIX_KEYS) <= set(metadata.manifest.matrices)

    assert metadata.manifest.matrices["walk"].cap_seconds == 28800
    assert metadata.manifest.matrices["transit_wed08"].cap_seconds == 5400


@shipped
def test_every_matrix_the_manifest_lists_opens_at_the_grid_s_shape():
    metadata = fmt.load_metadata(settings.dataset_dir)
    matrices = fmt.open_matrices(settings.dataset_dir, metadata.manifest)

    assert set(matrices) == set(metadata.manifest.matrices)
    for matrix in matrices.values():
        assert matrix.shape == (metadata.n_cells, metadata.n_cells)
        assert matrix.dtype == np.uint16


def test_a_manifest_missing_a_ceiling_is_rejected_naming_the_field():
    with pytest.raises(DatasetFormatError) as raised:
        fmt.read_manifest(FIXTURES / "manifest-missing-max-seconds.json")

    assert "max_seconds" in str(raised.value)
    assert "manifest-missing-max-seconds.json" in str(raised.value)


def test_a_manifest_from_a_format_this_reader_does_not_know_is_rejected(tmp_path):
    manifest = json.loads((FIXTURES / "manifest-missing-max-seconds.json").read_text())
    manifest["format_version"] = fmt.FORMAT_VERSION + 1
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))

    with pytest.raises(DatasetFormatError) as raised:
        fmt.read_manifest(path)
    assert "format_version" in str(raised.value)


def test_a_manifest_that_is_not_there_names_the_file(tmp_path):
    with pytest.raises(DatasetFormatError) as raised:
        fmt.load_metadata(tmp_path)
    assert "manifest.json" in str(raised.value)
