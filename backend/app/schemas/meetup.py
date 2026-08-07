import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
