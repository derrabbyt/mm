"""Optimal rendezvous spots from a baked travel-time dataset.

A dataset is one self-contained `<city>_<grid_version>` folder holding
`manifest.json`, `grid.json` and the matrices the manifest points at. Two rules
keep this code portable across cities and grid versions: the manifest is the
only directory (never scan the folder), and `grid.json` is frozen for the
lifetime of the folder.

Matrix values are seconds as `uint16`, capped at `max_seconds`; anything slower
is written as `UNREACHABLE` and carries no information about how much slower.
See `app/data/ttm_backend_integration.md`.
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import h3
import numpy as np
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.exceptions import (
    NoPositionedParticipantsError,
    ParticipantOffGridError,
    RendezvousInfeasibleError,
)
from ..models.meetup_participant import MeetupParticipant
from ..models.travel_mode import TravelMode
from ..schemas.position import Position
from ..schemas.rendezvous import ParticipantTravelTime, RendezvousRead
from .meetups import get_owned_meetup
from .participants import get_positioned_participants

logger = logging.getLogger(__name__)

SUPPORTED_FORMAT_VERSION = 1
UNREACHABLE = 65535
FALLBACK_TIMEZONE = "Europe/Vienna"

# Departure hour -> matrix key, per class of day. No weekend matrices have been
# baked yet, so the weekend table deliberately points at the Wednesday ones -
# a Saturday meetup silently gets Wednesday travel times. Once transit_sat* is
# baked, swapping the values here is the only change needed.
WEEKDAY_TRANSIT_KEYS = {8: "transit_wed08", 18: "transit_wed18"}
WEEKEND_TRANSIT_KEYS = {8: "transit_wed08", 18: "transit_wed18"}

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
        for key, entry in self.manifest["matrices"].items():
            matrix = np.load(folder / entry["file"], mmap_mode="r")
            if list(matrix.shape) != [self.n_cells, self.n_cells]:
                raise RuntimeError(
                    f"{key} shape {matrix.shape} != ({self.n_cells}, {self.n_cells})"
                )
            self.matrices[key] = matrix

        self.max_seconds = self.manifest["matrix_conventions"]["max_seconds"]
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


def pick_transit_key(when: datetime) -> str:
    """Matrix whose departure best matches `when`, in the dataset's timezone."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=dataset.timezone)
    local = when.astimezone(dataset.timezone)

    table = WEEKEND_TRANSIT_KEYS if local.weekday() >= 5 else WEEKDAY_TRANSIT_KEYS
    minutes = local.hour * 60 + local.minute
    return min(table.items(), key=lambda item: abs(item[0] * 60 - minutes))[1]


def matrix_keys(mode: TravelMode, transit_key: str) -> tuple[str, ...]:
    """Matrices a traveller on this mode may use; combined by taking the min.

    Transit riders walk to and from stops, so short hops are faster on foot
    than the transit matrix admits - hence the union. Every other mode is
    honoured strictly.
    """
    if mode is TravelMode.TRANSIT:
        return ("walk", transit_key)
    return STATIC_MATRIX_KEYS[mode]


def _travel_times(
    participants: list[MeetupParticipant], transit_key: str
) -> np.ndarray:
    """(n_participants, n_cells) matrix of seconds from each person to each cell."""
    times = np.full((len(participants), dataset.n_cells), UNREACHABLE, dtype=np.uint16)
    for row, participant in zip(times, participants, strict=True):
        cell = dataset.snap(participant.latitude, participant.longitude)
        if cell == -1:
            raise ParticipantOffGridError(participant_name=participant.name)
        for key in matrix_keys(participant.travel_mode, transit_key):
            np.minimum(row, dataset.matrices[key][cell], out=row)
    return times


def _best_cell(times: np.ndarray) -> int:
    """Cell minimising the worst traveller, ties broken by total travel time.

    Ties are common - times are whole seconds capped at an hour - and without
    the tiebreak `argmin` would return an arbitrary one of them.
    """
    worst = times.max(axis=0)
    candidates = np.flatnonzero(worst == worst.min())
    totals = times[:, candidates].sum(axis=0, dtype=np.int64)
    return int(candidates[int(np.argmin(totals))])


def get_rendezvous(
    db: Session, meetup_id: str, account_id: uuid.UUID
) -> RendezvousRead:
    meetup = get_owned_meetup(db, meetup_id, account_id)
    positioned, excluded_ids = get_positioned_participants(db, meetup.id)
    if not positioned:
        raise NoPositionedParticipantsError()

    transit_key = pick_transit_key(meetup.starts_at)
    times = _travel_times(positioned, transit_key)
    best = _best_cell(times)

    seconds = times[:, best]
    if UNREACHABLE in seconds:
        stranded = [
            participant.name
            for participant, value in zip(positioned, seconds, strict=True)
            if value == UNREACHABLE
        ]
        raise RendezvousInfeasibleError(
            participant_names=stranded, max_minutes=dataset.max_seconds // 60
        )

    uses_transit = any(p.travel_mode is TravelMode.TRANSIT for p in positioned)
    return RendezvousRead(
        position=dataset.position(best),
        cell_id=best,
        worst_seconds=int(seconds.max()),
        per_participant=[
            ParticipantTravelTime(participant_id=participant.id, seconds=int(value))
            for participant, value in zip(positioned, seconds, strict=True)
        ],
        excluded_participant_ids=excluded_ids,
        transit_key_used=transit_key if uses_transit else None,
    )
