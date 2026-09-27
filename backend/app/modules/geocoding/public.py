"""What other modules may use from `geocoding`.

They do not import it directly. A scrape runs in a job, and the layers that hold
a session may not reach another module - so `app/jobs/` builds the capability
here and hands it over as a `Locate`, the port declared in `core/contracts.py`.
That keeps the events module unable to reach these tables even by accident.
"""

from sqlalchemy.orm import Session

from ...core.contracts import Address, Locate, Located
from . import service
from .client import Disabled, Photon


def locator(db: Session, *, enabled: bool = True) -> Locate:
    """A `Locate` bound to this session.

    The session should be the caller's own rather than one it is already writing
    something else through: resolving an address commits, because a lookup is
    worth keeping whether or not whatever prompted it succeeds.

    `enabled=False` yields one that resolves nothing and remembers nothing, for a
    run that must not touch the geocoder at all.
    """
    geocoder = Photon() if enabled else Disabled()

    def locate(address: Address) -> Located | None:
        return service.locate(db, address, geocoder)

    return locate


__all__ = ["locator"]
