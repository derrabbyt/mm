"""What other modules may import from `meetups`.

Answers in `core.contracts` values rather than ORM rows, so the tables behind
them stay private.
"""

import uuid

from sqlalchemy.orm import Session

from ...core.contracts import MeetupInfo, ParticipantInfo
from . import service


def get_owned_meetup(db: Session, meetup_id: str, account_id: uuid.UUID) -> MeetupInfo:
    """Raises MeetupNotFoundError if it is missing, malformed or someone else's."""
    return MeetupInfo.model_validate(
        service.get_owned_meetup(db, meetup_id, account_id)
    )


def get_positioned_participants(
    db: Session, meetup_id: uuid.UUID
) -> tuple[list[ParticipantInfo], list[uuid.UUID]]:
    """Participants that can be routed, plus the ids of those that cannot."""
    return service.get_positioned_participants(db, meetup_id)


__all__ = ["get_owned_meetup", "get_positioned_participants"]
