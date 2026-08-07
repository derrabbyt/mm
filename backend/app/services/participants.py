import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..core.exceptions import (
    AccountNotFoundError,
    ParticipantCreateError,
    ParticipantLoadError,
    ParticipantNotFoundError,
    ParticipantsLoadError,
    ParticipantUpdateError,
)
from ..models.meetup_participant import MeetupParticipant
from ..schemas.participant import (
    AddParticipantRequest,
    MeetupParticipantRead,
    UpdateParticipantRequest,
)
from ..schemas.position import Position
from .meetups import get_owned_meetup, parse_id

logger = logging.getLogger(__name__)


def _rollback(db: Session, what: str) -> None:
    try:
        db.rollback()
    except SQLAlchemyError:
        logger.exception("Failed to roll back after %s", what)


def _linked_account_missing(exc: SQLAlchemyError, account_id: uuid.UUID | None) -> bool:
    """account_id is the only foreign key the client supplies - the meetup is
    resolved beforehand and the creator is the authenticated caller - so a
    constraint violation with one set means the account does not exist. That is
    a client mistake, and a 503 would invite a retry that can never succeed."""
    return account_id is not None and isinstance(exc, IntegrityError)


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


def _get(
    db: Session, meetup_id: uuid.UUID, participant_id: str
) -> MeetupParticipant | None:
    """Looked up by id *and* meetup, so an id from another meetup reads as
    absent instead of being editable through the wrong path."""
    pk = parse_id(participant_id)
    if pk is None:
        return None

    return db.scalars(
        select(MeetupParticipant).where(
            MeetupParticipant.id == pk,
            MeetupParticipant.meetup_id == meetup_id,
        )
    ).one_or_none()


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
    try:
        db.add(participant)
        db.commit()
    except SQLAlchemyError as exc:
        _rollback(db, "participant creation error")
        if _linked_account_missing(exc, data.account_id):
            raise AccountNotFoundError(account_id=str(data.account_id)) from exc
        raise ParticipantCreateError() from exc
    db.refresh(participant)
    return _to_schema(participant)


def get_participants(
    db: Session, meetup_id: str, account_id: uuid.UUID
) -> list[MeetupParticipantRead]:
    meetup = get_owned_meetup(db, meetup_id, account_id)

    try:
        participants = db.scalars(
            select(MeetupParticipant)
            .where(MeetupParticipant.meetup_id == meetup.id)
            .order_by(MeetupParticipant.created_at, MeetupParticipant.id)
        ).all()
    except SQLAlchemyError as exc:
        raise ParticipantsLoadError() from exc
    return [_to_schema(participant) for participant in participants]


def get_participant(
    db: Session, meetup_id: str, participant_id: str, account_id: uuid.UUID
) -> MeetupParticipantRead:
    meetup = get_owned_meetup(db, meetup_id, account_id)

    try:
        participant = _get(db, meetup.id, participant_id)
    except SQLAlchemyError as exc:
        raise ParticipantLoadError(participant_id=participant_id) from exc

    if participant is None:
        raise ParticipantNotFoundError(participant_id=participant_id)
    return _to_schema(participant)


def update_participant(
    db: Session,
    meetup_id: str,
    participant_id: str,
    account_id: uuid.UUID,
    data: UpdateParticipantRequest,
) -> MeetupParticipantRead:
    meetup = get_owned_meetup(db, meetup_id, account_id)

    try:
        participant = _get(db, meetup.id, participant_id)
    except SQLAlchemyError as exc:
        raise ParticipantLoadError(participant_id=participant_id) from exc

    if participant is None:
        raise ParticipantNotFoundError(participant_id=participant_id)

    participant.name = data.name
    participant.travel_mode = data.travel_mode
    participant.account_id = data.account_id
    participant.latitude = data.position.latitude
    participant.longitude = data.position.longitude
    try:
        db.commit()
    except SQLAlchemyError as exc:
        _rollback(db, "participant update error")
        if _linked_account_missing(exc, data.account_id):
            raise AccountNotFoundError(account_id=str(data.account_id)) from exc
        raise ParticipantUpdateError(participant_id=participant_id) from exc
    db.refresh(participant)
    return _to_schema(participant)
