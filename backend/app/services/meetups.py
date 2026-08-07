import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..core.exceptions import (
    MeetupCreateError,
    MeetupLoadError,
    MeetupNotFoundError,
    MeetupsLoadError,
)
from ..models.meetup import Meetup
from ..schemas.meetup import CreateMeetupRequest, MeetupRead

logger = logging.getLogger(__name__)


def parse_id(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def get_owned_meetup(db: Session, meetup_id: str, account_id: uuid.UUID) -> Meetup:
    pk = parse_id(meetup_id)
    if pk is None:
        raise MeetupNotFoundError(meetup_id=meetup_id)

    try:
        meetup = db.get(Meetup, pk)
    except SQLAlchemyError as exc:
        raise MeetupLoadError(meetup_id=meetup_id) from exc

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
    try:
        db.add(meetup)
        db.commit()
    except SQLAlchemyError as exc:
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.exception("Failed to roll back after meetup creation error")
        raise MeetupCreateError() from exc
    db.refresh(meetup)
    return MeetupRead.model_validate(meetup)


def get_meetups(db: Session, account_id: uuid.UUID) -> list[MeetupRead]:
    try:
        meetups = db.scalars(
            select(Meetup)
            .where(Meetup.created_by_account_id == account_id)
            .order_by(Meetup.starts_at)
        ).all()
    except SQLAlchemyError as exc:
        raise MeetupsLoadError() from exc
    return [MeetupRead.model_validate(meetup) for meetup in meetups]


def get_meetup(db: Session, meetup_id: str, account_id: uuid.UUID) -> MeetupRead:
    return MeetupRead.model_validate(get_owned_meetup(db, meetup_id, account_id))
