from fastapi import APIRouter

from ..core.exceptions import (
    AccountNotFoundError,
    AccountUpsertError,
    InvalidBearerTokenError,
    MeetupCreateError,
    MeetupLoadError,
    MeetupNotFoundError,
    MeetupsLoadError,
    MissingBearerTokenError,
    NoPositionedParticipantsError,
    ParticipantCreateError,
    ParticipantLoadError,
    ParticipantNotFoundError,
    ParticipantOffGridError,
    ParticipantsLoadError,
    ParticipantUpdateError,
    RendezvousInfeasibleError,
    UnauthenticatedRoleError,
    ValidationError,
    default_responses,
    responses,
)
from ..db.session import DbSessionDep
from ..schemas.meetup import CreateMeetupRequest, MeetupRead
from ..schemas.participant import (
    AddParticipantRequest,
    MeetupParticipantRead,
    UpdateParticipantRequest,
)
from ..schemas.rendezvous import RendezvousRead
from ..services import meetups, participants, travel_times
from ..services.accounts import CurrentAccountDep

AUTH_ERRORS = (
    MissingBearerTokenError,
    InvalidBearerTokenError,
    UnauthenticatedRoleError,
    AccountUpsertError,
)

router = APIRouter(
    prefix="/api/meetups",
    tags=["meetups"],
    responses=default_responses(),
)


@router.post(
    "",
    operation_id="createMeetup",
    responses=responses(*AUTH_ERRORS, MeetupCreateError),
)
def create_meetup(
    data: CreateMeetupRequest, account: CurrentAccountDep, db: DbSessionDep
) -> MeetupRead:
    return meetups.create_meetup(db, account.id, data)


@router.get(
    "",
    operation_id="getMeetups",
    responses=responses(*AUTH_ERRORS, MeetupsLoadError),
)
def get_meetups(account: CurrentAccountDep, db: DbSessionDep) -> list[MeetupRead]:
    return meetups.get_meetups(db, account.id)


@router.get(
    "/{meetup_id}",
    operation_id="getMeetup",
    responses=responses(*AUTH_ERRORS, MeetupNotFoundError, MeetupLoadError),
)
def get_meetup(
    meetup_id: str, account: CurrentAccountDep, db: DbSessionDep
) -> MeetupRead:
    return meetups.get_meetup(db, meetup_id, account.id)


@router.get(
    "/{meetup_id}/participants",
    operation_id="getParticipants",
    responses=responses(
        *AUTH_ERRORS, MeetupNotFoundError, MeetupLoadError, ParticipantsLoadError
    ),
)
def get_participants(
    meetup_id: str, account: CurrentAccountDep, db: DbSessionDep
) -> list[MeetupParticipantRead]:
    return participants.get_participants(db, meetup_id, account.id)


@router.get(
    "/{meetup_id}/rendezvous",
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
    return travel_times.get_rendezvous(db, meetup_id, account.id)


@router.post(
    "/{meetup_id}/participants",
    operation_id="addParticipant",
    responses=responses(
        *AUTH_ERRORS,
        MeetupNotFoundError,
        AccountNotFoundError,
        MeetupLoadError,
        ParticipantCreateError,
    ),
)
def add_participant(
    meetup_id: str,
    data: AddParticipantRequest,
    account: CurrentAccountDep,
    db: DbSessionDep,
) -> MeetupParticipantRead:
    return participants.add_participant(db, meetup_id, account.id, data)


@router.get(
    "/{meetup_id}/participants/{participant_id}",
    operation_id="getParticipant",
    responses=responses(
        *AUTH_ERRORS,
        MeetupNotFoundError,
        ParticipantNotFoundError,
        MeetupLoadError,
        ParticipantLoadError,
    ),
)
def get_participant(
    meetup_id: str, participant_id: str, account: CurrentAccountDep, db: DbSessionDep
) -> MeetupParticipantRead:
    return participants.get_participant(db, meetup_id, participant_id, account.id)


@router.put(
    "/{meetup_id}/participants/{participant_id}",
    operation_id="updateParticipant",
    responses=responses(
        *AUTH_ERRORS,
        MeetupNotFoundError,
        ParticipantNotFoundError,
        AccountNotFoundError,
        MeetupLoadError,
        ParticipantLoadError,
        ParticipantUpdateError,
    ),
)
def update_participant(
    meetup_id: str,
    participant_id: str,
    data: UpdateParticipantRequest,
    account: CurrentAccountDep,
    db: DbSessionDep,
) -> MeetupParticipantRead:
    return participants.update_participant(
        db, meetup_id, participant_id, account.id, data
    )
