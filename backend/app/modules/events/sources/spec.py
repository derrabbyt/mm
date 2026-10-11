from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from ....core.http import HttpClient
from ..scraped import RawListing


@dataclass(frozen=True)
class SourceSpec:
    name: str
    locales: tuple[str, ...] = ("de",)
    delay_seconds: float = 0.5
    use_cookies: bool = False
    timeout_seconds: float | None = None
    notes: str = ""


@dataclass
class RawPayload:
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
    http: HttpClient
    date_from: date
    date_to: date
    locales: tuple[str, ...] = ("de",)

    def dates(self) -> Iterator[date]:
        day = self.date_from
        while day <= self.date_to:
            yield day
            day += timedelta(days=1)


class Source(Protocol):
    SPEC: SourceSpec

    def fetch(self, ctx: FetchContext) -> Iterable[RawPayload]: ...

    def parse(self, payload: RawPayload) -> Iterable[RawListing]: ...
