from typing import Annotated

from fastapi import APIRouter, Query

from ...core.exceptions import (
    AUTH_ERRORS,
    EventsLoadError,
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
from ..rendezvous import public as rendezvous
from . import service
from .schemas import EventRead

router = APIRouter(responses=default_responses())


@router.get(
    "/rendezvous/events",
    operation_id="getRendezvousEvents",
    responses=responses(
        *AUTH_ERRORS,
        MeetupNotFoundError,
        MeetupLoadError,
        ParticipantsLoadError,
        NoPositionedParticipantsError,
        ParticipantOffGridError,
        RendezvousInfeasibleError,
        EventsLoadError,
        ValidationError,
    ),
)
def get_rendezvous_events(
    meetup_id: str,
    account: CurrentAccountDep,
    db: DbSessionDep,
    radius_meters: Annotated[int, Query(ge=100, le=10_000)] = (
        service.DEFAULT_RADIUS_METERS
    ),
    limit: Annotated[int, Query(ge=1, le=100)] = service.DEFAULT_LIMIT,
) -> list[EventRead]:
    meetup = meetups.get_owned_meetup(db, meetup_id, account.id)
    positioned, excluded_ids = meetups.get_positioned_participants(db, meetup.id)
    spot = rendezvous.compute_rendezvous(meetup.starts_at, positioned, excluded_ids)
    return service.get_events_near(
        db,
        position=spot.position,
        day=rendezvous.to_local(meetup.starts_at).date(),
        not_before=meetup.starts_at,
        radius_meters=radius_meters,
        limit=limit,
    )
