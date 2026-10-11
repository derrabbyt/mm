import uuid

from sqlalchemy.orm import Session

from ...core.contracts import MeetupInfo, ParticipantInfo
from . import service


def get_owned_meetup(db: Session, meetup_id: str, account_id: uuid.UUID) -> MeetupInfo:
    return MeetupInfo.model_validate(
        service.get_owned_meetup(db, meetup_id, account_id)
    )


def get_positioned_participants(
    db: Session, meetup_id: uuid.UUID
) -> tuple[list[ParticipantInfo], list[uuid.UUID]]:
    return service.get_positioned_participants(db, meetup_id)


__all__ = ["get_owned_meetup", "get_positioned_participants"]
