import logging
import uuid

from sqlalchemy.orm import Session

from ...core.contracts import ParticipantInfo, Position
from ...core.exceptions import (
    MeetupNotFoundError,
    ParticipantNotFoundError,
)
from . import repository
from .models import Meetup, MeetupParticipant
from .schemas import (
    AddParticipantRequest,
    CreateMeetupRequest,
    MeetupParticipantRead,
    MeetupRead,
    UpdateParticipantRequest,
)

logger = logging.getLogger(__name__)


def parse_id(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _to_schema(participant: MeetupParticipant) -> MeetupParticipantRead:
    position = None
    if participant.latitude is not None and participant.longitude is not None:
        position = Position(
            latitude=participant.latitude, longitude=participant.longitude
        )
    return MeetupParticipantRead(
        id=participant.id,
        name=participant.name,
        travel_mode=participant.travel_mode,
        position=position,
        account_id=participant.account_id,
    )


def get_owned_meetup(db: Session, meetup_id: str, account_id: uuid.UUID) -> Meetup:
    """The meetup, or `MeetupNotFoundError` when it does not exist, when the id
    is not a UUID at all, or when it belongs to somebody else - all three read
    the same from outside, so an id cannot be probed for existence."""
    pk = parse_id(meetup_id)
    if pk is None:
        raise MeetupNotFoundError(meetup_id=meetup_id)

    meetup = repository.get_meetup(db, pk, meetup_id=meetup_id)

    if meetup is None or meetup.created_by_account_id != account_id:
        raise MeetupNotFoundError(meetup_id=meetup_id)
    return meetup


def create_meetup(
    db: Session, account_id: uuid.UUID, data: CreateMeetupRequest
) -> MeetupRead:
    meetup = Meetup(
        created_by_account_id=account_id,
        name=data.name,
        location=data.location,
        starts_at=data.starts_at,
    )
    return MeetupRead.model_validate(repository.add_meetup(db, meetup))


def get_meetups(db: Session, account_id: uuid.UUID) -> list[MeetupRead]:
    meetups = repository.list_meetups(db, account_id)
    return [MeetupRead.model_validate(meetup) for meetup in meetups]


def get_meetup(db: Session, meetup_id: str, account_id: uuid.UUID) -> MeetupRead:
    return MeetupRead.model_validate(get_owned_meetup(db, meetup_id, account_id))


def _owned_participant(
    db: Session, meetup_id: str, participant_id: str, account_id: uuid.UUID
) -> MeetupParticipant:
    meetup = get_owned_meetup(db, meetup_id, account_id)

    pk = parse_id(participant_id)
    participant = (
        None
        if pk is None
        else repository.get_participant(
            db, meetup.id, pk, participant_id=participant_id
        )
    )

    if participant is None:
        raise ParticipantNotFoundError(participant_id=participant_id)
    return participant


def add_participant(
    db: Session,
    meetup_id: str,
    account_id: uuid.UUID,
    data: AddParticipantRequest,
) -> MeetupParticipantRead:
    meetup = get_owned_meetup(db, meetup_id, account_id)

    participant = MeetupParticipant(
        meetup_id=meetup.id,
        account_id=data.account_id,
        created_by_account_id=account_id,
        name=data.name,
        travel_mode=data.travel_mode,
    )
    stored = repository.add_participant(
        db, participant, linked_account_id=data.account_id
    )
    return _to_schema(stored)


def get_participants(
    db: Session, meetup_id: str, account_id: uuid.UUID
) -> list[MeetupParticipantRead]:
    meetup = get_owned_meetup(db, meetup_id, account_id)
    participants = repository.list_participants(db, meetup.id)
    return [_to_schema(participant) for participant in participants]


def get_positioned_participants(
    db: Session, meetup_id: uuid.UUID
) -> tuple[list[ParticipantInfo], list[uuid.UUID]]:
    """Participants that can be routed, plus the ids of those that cannot."""
    participants = repository.list_participants(db, meetup_id)

    positioned = [
        ParticipantInfo(
            id=participant.id,
            name=participant.name,
            travel_mode=participant.travel_mode,
            position=Position(
                latitude=participant.latitude, longitude=participant.longitude
            ),
        )
        for participant in participants
        if participant.latitude is not None and participant.longitude is not None
    ]
    excluded = [
        participant.id
        for participant in participants
        if participant.latitude is None or participant.longitude is None
    ]
    return positioned, excluded


def get_participant(
    db: Session, meetup_id: str, participant_id: str, account_id: uuid.UUID
) -> MeetupParticipantRead:
    return _to_schema(_owned_participant(db, meetup_id, participant_id, account_id))


def update_participant(
    db: Session,
    meetup_id: str,
    participant_id: str,
    account_id: uuid.UUID,
    data: UpdateParticipantRequest,
) -> MeetupParticipantRead:
    participant = _owned_participant(db, meetup_id, participant_id, account_id)

    participant.name = data.name
    participant.travel_mode = data.travel_mode
    participant.account_id = data.account_id
    participant.latitude = data.position.latitude if data.position else None
    participant.longitude = data.position.longitude if data.position else None

    stored = repository.save_participant(
        db,
        participant,
        linked_account_id=data.account_id,
        participant_id=participant_id,
    )
    return _to_schema(stored)
