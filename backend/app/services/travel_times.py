"""Optimal rendezvous spots from a baked travel-time dataset."""

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

MINUTES_PER_DAY = 24 * 60

# Departure hour -> matrix key, per class of day. Saturday and Sunday get their
# own tables even though the current bake wrote them byte-identical, so a real
# Sunday schedule later needs no change here.
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


def pick_transit_key(when: datetime) -> str:
    """Matrix whose departure best matches `when`, in the dataset's timezone."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=dataset.timezone)
    local = when.astimezone(dataset.timezone)

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
            participant
            for participant, value in zip(positioned, seconds, strict=True)
            if value == UNREACHABLE
        ]
        horizon = max(
            dataset.max_seconds[key]
            for participant in stranded
            for key in matrix_keys(participant.travel_mode, transit_key)
        )
        raise RendezvousInfeasibleError(
            participant_names=[participant.name for participant in stranded],
            max_minutes=horizon // 60,
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
