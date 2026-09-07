"""This module's data: a baked travel-time dataset on disk, not a table.

A folder of numpy matrices plus two metadata files, described in
`app/data/ttm_backend_integration.md`. Only this file knows that format.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import h3
import numpy as np

from ...core.config import settings
from ...core.contracts import Position
from ...core.enums import TravelMode

logger = logging.getLogger(__name__)

SUPPORTED_FORMAT_VERSION = 1
UNREACHABLE = 65535
FALLBACK_TIMEZONE = "Europe/Vienna"

MINUTES_PER_DAY = 24 * 60

# Departure hour -> matrix key, per class of day.
#
# The Saturday and Sunday matrices in the shipped dataset are byte-identical,
# which is a defect in that bake, not a property of the city: the Saturday
# departure was set to 2026-08-15, a public holiday, on which Wiener Linien runs
# Sunday service. Its own feed defines 34 Saturday-only and 38 Sunday-only
# service patterns and runs 15% more trips on a Saturday, so every Saturday
# rendezvous is currently computed from Sunday timetables. The tables below are
# right; the data behind two of them is not, until the next bake.
WEEKDAY_TRANSIT_KEYS = {
    8: "transit_wed08",
    12: "transit_wed12",
    18: "transit_wed18",
    22: "transit_wed22",
}
SATURDAY_TRANSIT_KEYS = {10: "transit_sat10", 14: "transit_sat14", 20: "transit_sat20"}
SUNDAY_TRANSIT_KEYS = {10: "transit_sun10", 14: "transit_sun14", 20: "transit_sun20"}

# Modes served by a single, time-independent matrix.
STATIC_MATRIX_KEYS = {
    TravelMode.WALK: ("walk",),
    TravelMode.BICYCLE: ("bike",),
    TravelMode.CAR: ("car",),
}


class Dataset:
    """One baked dataset folder, mmapped. Immutable after __init__."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.manifest = json.loads((folder / "manifest.json").read_text())
        self.grid = json.loads((folder / "grid.json").read_text())

        # Refuse to serve rather than serve partially valid data.
        if self.manifest["format_version"] != SUPPORTED_FORMAT_VERSION:
            raise RuntimeError(
                f"Unsupported format_version {self.manifest['format_version']}"
            )
        if self.grid["grid_version"] != self.manifest["grid_version"]:
            raise RuntimeError("grid.json / manifest grid_version mismatch")

        self.n_cells = self.grid["n_cells"]
        if self.n_cells != self.manifest["grid"]["n_cells"]:
            raise RuntimeError("grid.n_cells / manifest.grid.n_cells mismatch")

        self.h3_res = self.grid["h3_res"]
        self._h3_to_id = {cell["h3"]: cell["id"] for cell in self.grid["cells"]}
        self._latlng = np.array(
            [[cell["lat"], cell["lon"]] for cell in self.grid["cells"]]
        )

        self.matrices: dict[str, np.ndarray] = {}
        self.max_seconds: dict[str, int] = {}
        for key, entry in self.manifest["matrices"].items():
            matrix = np.load(folder / entry["file"], mmap_mode="r")
            if list(matrix.shape) != [self.n_cells, self.n_cells]:
                raise RuntimeError(
                    f"{key} shape {matrix.shape} != ({self.n_cells}, {self.n_cells})"
                )
            self.matrices[key] = matrix
            self.max_seconds[key] = entry["max_seconds"]
        self.timezone = ZoneInfo(
            next(
                (
                    entry["timezone"]
                    for entry in self.manifest["matrices"].values()
                    if "timezone" in entry
                ),
                FALLBACK_TIMEZONE,
            )
        )

    def snap(self, latitude: float, longitude: float) -> int:
        """Cell containing this coordinate, or -1 when it is off the grid."""
        cell = h3.latlng_to_cell(latitude, longitude, self.h3_res)
        return self._h3_to_id.get(cell, -1)

    def position(self, cell_id: int) -> Position:
        latitude, longitude = self._latlng[cell_id]
        return Position(latitude=float(latitude), longitude=float(longitude))


dataset = Dataset(settings.dataset_dir)
logger.info(
    "Loaded travel-time dataset %s (%s cells, matrices: %s)",
    dataset.manifest["grid_version"],
    dataset.n_cells,
    ", ".join(dataset.matrices),
)


def to_local(when: datetime) -> datetime:
    """`when` in the dataset's timezone. A naive value is read as already local,
    since the only way to get one here is a column that dropped its offset."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=dataset.timezone)
    return when.astimezone(dataset.timezone)


def pick_transit_key(when: datetime) -> str:
    """Matrix whose departure best matches `when`, in the dataset's timezone."""
    local = to_local(when)

    weekday = local.weekday()
    if weekday == 5:
        table = SATURDAY_TRANSIT_KEYS
    elif weekday == 6:
        table = SUNDAY_TRANSIT_KEYS
    else:
        table = WEEKDAY_TRANSIT_KEYS

    # Distance around the clock, so 00:30 lands on the 22:00 bake rather than
    # the 08:00 one. The day class stays whatever the meetup's own day says.
    minutes = local.hour * 60 + local.minute

    def distance(hour: int) -> int:
        gap = abs(hour * 60 - minutes)
        return min(gap, MINUTES_PER_DAY - gap)

    return table[min(table, key=distance)]


def matrix_keys(mode: TravelMode, transit_key: str) -> tuple[str, ...]:
    """Matrices a traveller on this mode may use; combined by taking the min.

    Transit riders walk to and from stops, so short hops are faster on foot
    than the transit matrix admits - hence the union. Every other mode is
    honoured strictly.
    """
    if mode is TravelMode.TRANSIT:
        return ("walk", transit_key)
    return STATIC_MATRIX_KEYS[mode]
