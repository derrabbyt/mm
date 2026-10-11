import logging
import urllib.parse
from typing import Protocol

from ...core.config import settings
from ...core.http import FetchError, HttpClient
from . import photon
from .photon import Found

logger = logging.getLogger(__name__)


class Unreachable(Exception):
    """The geocoder was not asked, or did not answer. Not a fact about the address."""


class Geocoder(Protocol):
    name: str

    def lookup(self, query: str) -> Found | None: ...


class Photon:
    name = "photon"

    def __init__(self, endpoint: str | None = None, delay: float | None = None) -> None:
        self.endpoint = endpoint or settings.geocoder_url
        self.http = HttpClient(
            delay=settings.geocoder_delay_seconds if delay is None else delay,
            retries=2,
        )

    def lookup(self, query: str) -> Found | None:
        params = urllib.parse.urlencode(
            {
                "q": query,
                "limit": "1",
                "lang": "de",
                "bbox": f"{photon.LON_MIN},{photon.LAT_MIN},{photon.LON_MAX},{photon.LAT_MAX}",
                "lat": str(photon.VIENNA[0]),
                "lon": str(photon.VIENNA[1]),
            }
        )
        try:
            response = self.http.get(f"{self.endpoint}?{params}")
            payload = response.json()
        except (FetchError, ValueError) as exc:
            logger.warning("geocoder did not answer for %r: %s", query, exc)
            raise Unreachable(query) from exc
        return photon.read(payload)


class Disabled:
    name = "disabled"

    def lookup(self, query: str) -> Found | None:
        raise Unreachable(query)
