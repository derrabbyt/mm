"""Shapes one module hands another.

Not the HTTP API - request and response bodies live in `modules/<x>/schemas.py`
and appear in the OpenAPI schema. Nothing here does.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .enums import TravelMode


class Position(BaseModel):
    latitude: float
    longitude: float


class MeetupInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    starts_at: datetime


class ParticipantInfo(BaseModel):
    """`position` is required here, unlike the nullable column pair behind it -
    this is only built for participants that have one."""

    id: uuid.UUID
    name: str
    travel_mode: TravelMode
    position: Position
