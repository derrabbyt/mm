import uuid

from pydantic import BaseModel

from ..models.travel_mode import TravelMode
from .position import Position

DEFAULT_TRAVEL_MODE = TravelMode.TRANSIT


class MeetupParticipantRead(BaseModel):
    id: uuid.UUID
    name: str
    travel_mode: TravelMode
    position: Position | None = None
    account_id: uuid.UUID | None = None


class AddParticipantRequest(BaseModel):
    name: str
    travel_mode: TravelMode = DEFAULT_TRAVEL_MODE
    account_id: uuid.UUID | None = None


class UpdateParticipantRequest(BaseModel):
    name: str
    travel_mode: TravelMode = DEFAULT_TRAVEL_MODE
    position: Position | None = None
    account_id: uuid.UUID | None = None
