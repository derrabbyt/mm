from fastapi import APIRouter

from ...core.exceptions import (
    AUTH_ERRORS,
    MeetupLoadError,
    MeetupNotFoundError,
    NoPositionedParticipantsError,
    ParticipantOffGridError,
    ParticipantsLoadError,
    RendezvousInfeasibleError,
    ValidationError,
    default_responses,
    responses,
)
from ...db.session import DbSessionDep
from ..accounts.public import CurrentAccountDep
from ..meetups import public as meetups
from . import service
from .schemas import RendezvousRead

# Mounted under /api/meetups/{meetup_id} by app/api/main.py.
router = APIRouter(responses=default_responses())


@router.get(
    "/rendezvous",
    operation_id="getRendezvous",
    responses=responses(
        *AUTH_ERRORS,
        MeetupNotFoundError,
        MeetupLoadError,
        ParticipantsLoadError,
        NoPositionedParticipantsError,
        ParticipantOffGridError,
        RendezvousInfeasibleError,
        ValidationError,
    ),
)
def get_rendezvous(
    meetup_id: str, account: CurrentAccountDep, db: DbSessionDep
) -> RendezvousRead:
    # Loaded here rather than in `service`, which never holds a session.
    # Comment, not a docstring: FastAPI publishes docstrings in the schema.
    meetup = meetups.get_owned_meetup(db, meetup_id, account.id)
    positioned, excluded_ids = meetups.get_positioned_participants(db, meetup.id)
    return service.compute_rendezvous(meetup.starts_at, positioned, excluded_ids)
