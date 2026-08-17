from datetime import datetime

from pydantic import BaseModel

from .position import Position


class EventRead(BaseModel):
    id: int
    title: str
    # Wall-clock time at the venue, without an offset - the scraper has already
    # converted it, and attaching UTC here would shift it in the browser.
    starts_at: datetime
    all_day: bool
    description: str | None = None
    origin_url: str | None = None
    # Points at the source's own server; about 10% of events have none.
    image_url: str | None = None
    venue_name: str | None = None
    # None when the scraper never recorded a street, which is common; the
    # caller can reverse geocode `position` to fill the gap.
    address: str | None = None
    position: Position
    distance_meters: int
