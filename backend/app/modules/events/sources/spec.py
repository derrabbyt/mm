"""The contract every Source implements.

Three parts, deliberately separated:

`SourceSpec`
    Static facts about the site - its rate limit, its locales, whether paging
    needs cookies, how long it takes to answer, and the traps someone found the
    hard way. These live here rather than in application settings because the
    *reason* for them is knowledge about the site, not a deployment preference:
    one Source's robots.txt asks for a ten-second crawl delay, and that fact
    belongs next to the code that honours it.

`fetch(ctx) -> Iterable[RawPayload]`
    The only part that touches the network.

`parse(payload) -> Iterable[RawListing]`
    A **pure** function from one saved document to Listings. This is the seam
    the tests sit on: a parser that starts producing nonsense is fixed against
    the exact document that broke it, with no network.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from ....core.http import HttpClient
from ..scraped import RawListing


@dataclass(frozen=True)
class SourceSpec:
    """Static description of one Source."""

    name: str
    # Which locales to fetch. Fetching two produces ONE Listing with two
    # language columns, joined on source_event_id - never two Listings.
    locales: tuple[str, ...] = ("de",)
    # Politeness budget between requests, in seconds.
    delay_seconds: float = 0.5
    # Some sites keep search state server-side against a session, so paging
    # only works when cookies are carried between requests.
    use_cookies: bool = False
    # Read timeout, when the shared default is too tight. None keeps the
    # client's. Site knowledge like the rest: one Source server-generates a
    # ~490 KB index and takes ~32s to hand it over.
    timeout_seconds: float | None = None
    # Any trap a maintainer must not "simplify" away.
    notes: str = ""


@dataclass
class RawPayload:
    """One fetched document, as it arrived.

    `kind` lets a `parse` tell payload types apart - a listing page against a
    detail page - without re-deriving it from the URL.
    """

    url: str
    body: bytes
    kind: str = "listing"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@dataclass
class FetchContext:
    """Everything a `fetch` needs from the run."""

    http: HttpClient
    date_from: date
    date_to: date
    locales: tuple[str, ...] = ("de",)

    def dates(self) -> Iterator[date]:
        """Every day in the window, for Sources that must be queried per day."""
        day = self.date_from
        while day <= self.date_to:
            yield day
            day += timedelta(days=1)


class Source(Protocol):
    """The structural type a Source module satisfies."""

    SPEC: SourceSpec

    def fetch(self, ctx: FetchContext) -> Iterable[RawPayload]: ...

    def parse(self, payload: RawPayload) -> Iterable[RawListing]: ...
