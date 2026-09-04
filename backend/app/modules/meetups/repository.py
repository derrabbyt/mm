"""Every SQLAlchemy statement the meetups module runs.

`SQLAlchemyError` never leaves this file. Primary keys arrive parsed, but the
caller's original spelling comes along so errors can name the id it sent.
"""

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ...core.exceptions import (
    AccountNotFoundError,
    MeetupCreateError,
    MeetupLoadError,
    MeetupsLoadError,
    ParticipantCreateError,
    ParticipantLoadError,
    ParticipantsLoadError,
    ParticipantUpdateError,
)
from .models import Meetup, MeetupParticipant

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


def get_meetup(db: Session, pk: uuid.UUID, *, meetup_id: str) -> Meetup | None:
    try:
        return db.get(Meetup, pk)
    except SQLAlchemyError as exc:
        raise MeetupLoadError(meetup_id=meetup_id) from exc


def list_meetups(db: Session, account_id: uuid.UUID) -> Sequence[Meetup]:
    try:
        return db.scalars(
            select(Meetup)
            .where(Meetup.created_by_account_id == account_id)
            .order_by(Meetup.starts_at)
        ).all()
    except SQLAlchemyError as exc:
        raise MeetupsLoadError() from exc


def add_meetup(db: Session, meetup: Meetup) -> Meetup:
    try:
        db.add(meetup)
        db.commit()
    except SQLAlchemyError as exc:
        _rollback(db, "meetup creation error")
        raise MeetupCreateError() from exc
    db.refresh(meetup)
    return meetup


def list_participants(db: Session, meetup_id: uuid.UUID) -> Sequence[MeetupParticipant]:
    try:
        return db.scalars(
            select(MeetupParticipant)
            .where(MeetupParticipant.meetup_id == meetup_id)
            .order_by(MeetupParticipant.created_at, MeetupParticipant.id)
        ).all()
    except SQLAlchemyError as exc:
        raise ParticipantsLoadError() from exc


def get_participant(
    db: Session, meetup_id: uuid.UUID, pk: uuid.UUID, *, participant_id: str
) -> MeetupParticipant | None:
    """Looked up by id *and* meetup, so an id from another meetup reads as
    absent instead of being editable through the wrong path."""
    try:
        return db.scalars(
            select(MeetupParticipant).where(
                MeetupParticipant.id == pk,
                MeetupParticipant.meetup_id == meetup_id,
            )
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise ParticipantLoadError(participant_id=participant_id) from exc


def add_participant(
    db: Session, participant: MeetupParticipant, *, linked_account_id: uuid.UUID | None
) -> MeetupParticipant:
    try:
        db.add(participant)
        db.commit()
    except SQLAlchemyError as exc:
        _rollback(db, "participant creation error")
        if _linked_account_missing(exc, linked_account_id):
            raise AccountNotFoundError(account_id=str(linked_account_id)) from exc
        raise ParticipantCreateError() from exc
    db.refresh(participant)
    return participant


def save_participant(
    db: Session,
    participant: MeetupParticipant,
    *,
    linked_account_id: uuid.UUID | None,
    participant_id: str,
) -> MeetupParticipant:
    """Flush the pending changes on an already-loaded participant."""
    try:
        db.commit()
    except SQLAlchemyError as exc:
        _rollback(db, "participant update error")
        if _linked_account_missing(exc, linked_account_id):
            raise AccountNotFoundError(account_id=str(linked_account_id)) from exc
        raise ParticipantUpdateError(participant_id=participant_id) from exc
    db.refresh(participant)
    return participant
