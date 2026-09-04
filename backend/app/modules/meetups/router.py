from fastapi import APIRouter

from ...core.exceptions import (
    AUTH_ERRORS,
    AccountNotFoundError,
    MeetupCreateError,
    MeetupLoadError,
    MeetupNotFoundError,
    MeetupsLoadError,
    ParticipantCreateError,
    ParticipantLoadError,
    ParticipantNotFoundError,
    ParticipantsLoadError,
    ParticipantUpdateError,
    default_responses,
    responses,
)
from ...db.session import DbSessionDep
from ..accounts.public import CurrentAccountDep
from . import service
from .schemas import (
    AddParticipantRequest,
    CreateMeetupRequest,
    MeetupParticipantRead,
    MeetupRead,
    UpdateParticipantRequest,
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
    return service.create_meetup(db, account.id, data)


@router.get(
    "",
    operation_id="getMeetups",
    responses=responses(*AUTH_ERRORS, MeetupsLoadError),
)
def get_meetups(account: CurrentAccountDep, db: DbSessionDep) -> list[MeetupRead]:
    return service.get_meetups(db, account.id)


@router.get(
    "/{meetup_id}",
    operation_id="getMeetup",
    responses=responses(*AUTH_ERRORS, MeetupNotFoundError, MeetupLoadError),
)
def get_meetup(
    meetup_id: str, account: CurrentAccountDep, db: DbSessionDep
) -> MeetupRead:
    return service.get_meetup(db, meetup_id, account.id)


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
    return service.get_participants(db, meetup_id, account.id)


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
    return service.add_participant(db, meetup_id, account.id, data)


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
    return service.get_participant(db, meetup_id, participant_id, account.id)


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
    return service.update_participant(db, meetup_id, participant_id, account.id, data)
