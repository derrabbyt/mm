"""Asking the configured geocoder, and nothing else.

The endpoint is a setting, and there is no default pointing anywhere public:
`geocoder_url` names the self-hosted Photon that `docker compose` already runs.
That is deliberate - an address is somebody's whereabouts, and a misconfiguration
that silently sent 3,000 of them to a third party would look exactly like
working software.

A lookup has three outcomes, and the difference between the last two is what
keeps the cache honest:

* a `Found` - the geocoder answered and the answer was believable;
* `None` - the geocoder answered and the answer was not worth having;
* `Unreachable` - the geocoder did not answer at all.

Only the first two are facts about the address. The third is a fact about us, and
caching it would mean an outage silently and permanently marked every address it
touched as unresolvable.
"""

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
    """What `service.locate` needs of a geocoder."""

    name: str

    def lookup(self, query: str) -> Found | None: ...


class Photon:
    """A Photon instance, asked one question at a time."""

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
            # Not a miss. An unreachable geocoder is not a reason to fail a
            # scrape either: the address is stored, so a later run can still
            # place it - which is only true because this is not remembered.
            logger.warning("geocoder did not answer for %r: %s", query, exc)
            raise Unreachable(query) from exc
        return photon.read(payload)


class Disabled:
    """Asks nothing. What runs when geocoding is switched off.

    Every lookup is `Unreachable` rather than a miss, so a run with geocoding off
    leaves the cache exactly as it found it and turning it back on resolves
    everything that was skipped.
    """

    name = "disabled"

    def lookup(self, query: str) -> Found | None:
        raise Unreachable(query)
