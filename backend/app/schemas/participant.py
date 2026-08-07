import uuid

from pydantic import BaseModel

from .position import Position

DEFAULT_TRAVEL_MODE = "transit"


class MeetupParticipantRead(BaseModel):
    id: uuid.UUID
    name: str
    travel_mode: str
    position: Position | None = None
    account_id: uuid.UUID | None = None


class AddParticipantRequest(BaseModel):
    name: str
    travel_mode: str = DEFAULT_TRAVEL_MODE
    account_id: uuid.UUID | None = None


class UpdateParticipantRequest(BaseModel):
    name: str
    travel_mode: str = DEFAULT_TRAVEL_MODE
    position: Position
    account_id: uuid.UUID | None = None
