"""Every ORM model, imported for its side effect on `Base.metadata`.

Without this, Alembic autogenerate sees half-empty metadata and emits a DROP
for every table it cannot find. `migrations/env.py` imports it; nothing else
does, which is why it can sit above the modules instead of inside `db/`.
"""

from .modules.accounts.models import Account
from .modules.events.models import Listing, Occurrence, QuarantinedListing, SourceRun
from .modules.geocoding.models import GeocodeCache
from .modules.meetups.models import Meetup, MeetupParticipant

__all__ = [
    "Account",
    "GeocodeCache",
    "Listing",
    "Meetup",
    "MeetupParticipant",
    "Occurrence",
    "QuarantinedListing",
    "SourceRun",
]
