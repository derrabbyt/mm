import uuid

from pydantic import BaseModel

from .position import Position


class ParticipantTravelTime(BaseModel):
    participant_id: uuid.UUID
    seconds: int


class RendezvousRead(BaseModel):
    position: Position
    cell_id: int
    worst_seconds: int
    per_participant: list[ParticipantTravelTime]
    excluded_participant_ids: list[uuid.UUID]
    transit_key_used: str | None = None
