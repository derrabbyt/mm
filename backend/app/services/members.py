import uuid

from fastapi import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..core.exceptions import (
    MemberCreateError,
    MemberLoadError,
    MemberNotFoundError,
    MembersLoadError,
    MemberUpdateError,
)
from ..models.member import Member
from ..schemas.member import (
    AddMeetupMemberRequest,
    MeetupMember,
    UpdateMeetupMemberRequest,
)
from ..schemas.position import Position


def _to_schema(member: Member) -> MeetupMember:
    position = None
    if member.latitude is not None and member.longitude is not None:
        position = Position(latitude=member.latitude, longitude=member.longitude)
    return MeetupMember(id=str(member.id), name=member.display_name or "", position=position)


def _get(db: Session, member_id: str) -> Member | None:
    try:
        pk = uuid.UUID(member_id)
    except ValueError:
        return None
    return db.get(Member, pk)

def add_member(db: Session, data: AddMeetupMemberRequest) -> MeetupMember:
    member = Member(display_name=data.name)
    try:
        db.add(member)
        db.commit()
    except SQLAlchemyError as exc:
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.exception("Failed to roll back after member creation error")
        raise MemberCreateError() from exc
    db.refresh(member)
    return _to_schema(member)


def get_member(db: Session, member_id: str) -> MeetupMember:
    try:
        member = _get(db, member_id)
    except SQLAlchemyError as exc:
        raise MemberLoadError(member_id=member_id) from exc

    if member is None:
        raise MemberNotFoundError(member_id=member_id)
    return _to_schema(member)


def get_members(db: Session) -> list[MeetupMember]:
    try:
        members = db.scalars(select(Member).order_by(Member.id)).all()
    except SQLAlchemyError as exc:
        raise MembersLoadError() from exc
    return [_to_schema(member) for member in members]


def update_member(
    db: Session, member_id: str, data: UpdateMeetupMemberRequest
) -> MeetupMember:
    try:
        member = _get(db, member_id)
    except SQLAlchemyError as exc:
        raise MemberLoadError(member_id=member_id) from exc

    if member is None:
        raise MemberNotFoundError(member_id=member_id)

    member.display_name = data.name
    member.latitude = data.position.latitude
    member.longitude = data.position.longitude
    try:
        db.commit()
    except SQLAlchemyError as exc:
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.exception("Failed to roll back after member update error")
        raise MemberUpdateError(member_id=member_id) from exc
    db.refresh(member)
    return _to_schema(member)
