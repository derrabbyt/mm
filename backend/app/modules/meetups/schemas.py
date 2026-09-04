import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ...core.contracts import Position
from ...core.enums import TravelMode

DEFAULT_TRAVEL_MODE = TravelMode.TRANSIT


class MeetupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    location: str
    starts_at: datetime


class CreateMeetupRequest(BaseModel):
    name: str
    location: str
    starts_at: datetime


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
