import logging
import uuid
from datetime import datetime

import numpy as np

from ...core.contracts import ParticipantInfo
from ...core.dataset import UNREACHABLE
from ...core.enums import TravelMode
from ...core.exceptions import (
    NoPositionedParticipantsError,
    ParticipantOffGridError,
    RendezvousInfeasibleError,
)
from . import repository
from .repository import dataset
from .schemas import ParticipantTravelTime, RendezvousRead

logger = logging.getLogger(__name__)


def _travel_times(participants: list[ParticipantInfo], transit_key: str) -> np.ndarray:
    times = np.full((len(participants), dataset.n_cells), UNREACHABLE, dtype=np.uint16)
    for row, participant in zip(times, participants, strict=True):
        cell = dataset.snap(
            participant.position.latitude, participant.position.longitude
        )
        if cell == -1:
            raise ParticipantOffGridError(participant_name=participant.name)
        for key in repository.matrix_keys(participant.travel_mode, transit_key):
            np.minimum(row, dataset.matrices[key][cell], out=row)
    return times


def _best_cell(times: np.ndarray) -> int:
    worst = times.max(axis=0)
    candidates = np.flatnonzero(worst == worst.min())
    totals = times[:, candidates].sum(axis=0, dtype=np.int64)
    return int(candidates[int(np.argmin(totals))])


def compute_rendezvous(
    starts_at: datetime,
    positioned: list[ParticipantInfo],
    excluded_ids: list[uuid.UUID],
) -> RendezvousRead:
    if not positioned:
        raise NoPositionedParticipantsError()

    transit_key = repository.pick_transit_key(starts_at)
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
            dataset.cap_seconds[key]
            for participant in stranded
            for key in repository.matrix_keys(participant.travel_mode, transit_key)
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
