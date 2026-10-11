import uuid
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from .enums import TravelMode


class Position(BaseModel):
    latitude: float
    longitude: float


class Address(BaseModel):
    model_config = ConfigDict(frozen=True)

    venue_name: str | None = None
    street: str | None = None
    postcode: str | None = None
    city: str | None = None


class Located(BaseModel):
    model_config = ConfigDict(frozen=True)

    position: Position
    precision: str


class Locate(Protocol):
    def __call__(self, address: Address) -> Located | None: ...


class MeetupInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    starts_at: datetime


class ParticipantInfo(BaseModel):
    id: uuid.UUID
    name: str
    travel_mode: TravelMode
    position: Position
