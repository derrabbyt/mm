from sqlalchemy.orm import Session

from ...core.contracts import Address, Locate, Located
from . import service
from .client import Disabled, Photon


def locator(db: Session, *, enabled: bool = True) -> Locate:
    geocoder = Photon() if enabled else Disabled()

    def locate(address: Address) -> Located | None:
        return service.locate(db, address, geocoder)

    return locate


__all__ = ["locator"]
