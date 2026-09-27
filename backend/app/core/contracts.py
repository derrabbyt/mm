"""Shapes one module hands another, and the ports they hand them across.

Not the HTTP API - request and response bodies live in `modules/<x>/schemas.py`
and appear in the OpenAPI schema. Nothing here does.

A *shape* is a value one module gives another. A *port* is a capability one
module needs and another provides, named here so that neither has to import the
other - which is what the layers holding a session are forbidden from doing.
"""

import uuid
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from .enums import TravelMode


class Position(BaseModel):
    latitude: float
    longitude: float


class Address(BaseModel):
    """Where something is, as a Source described it in words.

    What `geocoding` is asked to turn into a `Position`. A value rather than a
    Listing, because a Listing is the events module's to know about and the
    points-of-interest work will hand over the same shape from somewhere else.
    """

    model_config = ConfigDict(frozen=True)

    venue_name: str | None = None
    street: str | None = None
    postcode: str | None = None
    city: str | None = None


class Located(BaseModel):
    """Where an `Address` turned out to be, and how precisely.

    `precision` travels with the position because a position resolved to a house
    number and one resolved to a district are both "a position", and only the
    first is worth much to a "what is on near here" query.
    """

    model_config = ConfigDict(frozen=True)

    position: Position
    precision: str


class Locate(Protocol):
    """Turning an `Address` into a `Located`, or admitting it cannot.

    A port rather than an import: the scrape needs geocoding done, but the layer
    that holds the session may not reach another module, so the caller is handed
    the capability instead of fetching it. `app/jobs/` supplies the real one.
    """

    def __call__(self, address: Address) -> Located | None: ...


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
