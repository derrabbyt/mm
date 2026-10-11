from datetime import datetime

from pydantic import BaseModel

from ...core.contracts import Position


class EventRead(BaseModel):
    id: int
    title: str
    starts_at: datetime
    all_day: bool
    description: str | None = None
    origin_url: str | None = None
    image_url: str | None = None
    venue_name: str | None = None
    address: str | None = None
    position: Position
    distance_meters: int
