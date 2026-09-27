"""The values a scrape moves through, before any of it reaches a table.

Two shapes, and the step between them is `normalize.py`:

`RawListing` / `RawOccurrence`
    What a Source's `parse` yields. Source-shaped and permissive - the Source
    knows its own date formats, so it does its own date parsing, but it is not
    responsible for timezones, validation or canonical categories.

`NormalizedListing` / `NormalizedOccurrence`
    The canonical form. This is what `repository.py` writes, and its fields line
    up with the columns in `models.py`.

Plus what a run has to say for itself afterwards: `Rejected` for a record
normalisation refused, and `SourceRunStats` for what one Source did.

The `date` vs `datetime` distinction in `RawOccurrence.start` is load-bearing: a
plain `date` means "this happens on this day, time unknown" and becomes
`all_day` downstream. A `datetime` means a real clock time. A Source must not
fabricate 00:00 for an unknown time - several sites do exactly that, and passing
it through as a real midnight start sorts every time-unknown Listing to the top
of a day.
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The canonical vocabulary, deliberately small: it exists so that Sources
# sharing no taxonomy can be filtered across. `categories_raw` keeps a Source's
# own labels verbatim, so a wrong mapping is a re-map rather than a re-scrape.
Category = Literal[
    "music",
    "party",
    "theatre",
    "art",
    "film",
    "family",
    "food",
    "sport",
    "market",
    "talk",
    "other",
]

GeoSource = Literal["source", "geocoded", "none"]
# How precisely a position was placed. `approx` covers both a match too vague to
# name and one the address contradicted - see the geocoding module.
GeoPrecision = Literal["exact", "street", "city", "approx"]

VIENNA_TZ = "Europe/Vienna"

# A Listing starting before this hour is attributed to the previous night as
# well, so a 01:00 club night is reachable from the previous evening's view.
NIGHT_CUTOFF_HOUR = 6


class RawOccurrence(BaseModel):
    """One date, or date range, as the Source expressed it.

    `start` as a `date` means the time is unknown, so the Occurrence is all-day.
    `start` as a `datetime` is a real clock time; naive is read as Vienna local.
    """

    model_config = ConfigDict(frozen=True)

    start: datetime | date
    end: datetime | date | None = None


class RawListing(BaseModel):
    """What a Source's `parse` yields for one happening."""

    model_config = ConfigDict(extra="forbid")

    source_ref: str
    occurrences: list[RawOccurrence] = Field(default_factory=list)

    url: str | None = None
    # The origin (ticket shop, venue page, a JSON-LD `sameAs`). The strongest
    # cross-Source identity signal there is, when a Source publishes one.
    origin_url: str | None = None

    title: str | None = None
    description: str | None = None
    # Which language `title` and `description` are in.
    lang: Literal["de", "en"] = "de"
    # Filled when a Source hands over a second locale on the same route.
    title_alt: str | None = None
    description_alt: str | None = None
    lang_alt: Literal["de", "en"] | None = None

    venue_name: str | None = None
    street: str | None = None
    postcode: str | None = None
    city: str | None = None
    country: str | None = "AT"

    lat: float | None = None
    lon: float | None = None

    categories_raw: list[str] = Field(default_factory=list)

    price_min: float | None = None
    price_max: float | None = None
    price_currency: str | None = None
    is_free: bool | None = None
    ticket_url: str | None = None

    image_url: str | None = None
    organizer: str | None = None

    @field_validator("source_ref", mode="before")
    @classmethod
    def _id_to_str(cls, value: Any) -> str:
        # Sources use numeric ids freely.
        if value is None:
            raise ValueError("source_ref must not be None")
        text = str(value).strip()
        if not text:
            raise ValueError("source_ref must not be blank")
        return text

    @field_validator(
        "postcode",
        "source_ref",
        "title",
        "description",
        "venue_name",
        "street",
        "city",
        "country",
        "organizer",
        "price_currency",
        mode="before",
    )
    @classmethod
    def _coerce_scalar_to_str(cls, value: Any) -> Any:
        """Accept a number where a string is expected.

        Upstream JSON is inconsistent about this - one Source returns
        `zip_code: 1200` as an integer while others quote it. Coercing here
        keeps every parse free of defensive `str()` calls.
        """
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, int | float):
            # Avoid "1200.0" for a postcode that arrived as a float.
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)
        return value


class NormalizedOccurrence(BaseModel):
    """A storable time range, with everything the day query needs precomputed."""

    model_config = ConfigDict(extra="forbid")

    start_utc: datetime
    end_utc: datetime | None = None
    start_local: datetime
    end_local: datetime | None = None
    date_local: date
    night_of: date | None = None
    all_day: bool = False
    duration_days: int = 0


class NormalizedListing(BaseModel):
    """A storable Listing. One row of `content.listings` plus its Occurrences."""

    model_config = ConfigDict(extra="forbid")

    source: str
    source_ref: str
    url: str | None = None
    origin_url: str | None = None

    title_de: str | None = None
    title_en: str | None = None
    description_de: str | None = None
    description_en: str | None = None
    lang_primary: Literal["de", "en"] = "de"

    # Computed at ingest so that deduplication needs no backfill later.
    title_norm: str = ""
    venue_name_norm: str | None = None

    venue_name_raw: str | None = None
    street: str | None = None
    postcode: str | None = None
    city: str | None = None
    country: str | None = None

    lat: float | None = None
    lon: float | None = None
    geo_source: GeoSource = "none"
    geo_precision: GeoPrecision | None = None

    categories_raw: list[str] = Field(default_factory=list)
    category: Category = "other"

    price_min: float | None = None
    price_max: float | None = None
    price_currency: str | None = None
    is_free: bool | None = None
    ticket_url: str | None = None

    image_url: str | None = None
    organizer: str | None = None

    # Repairs normalisation applied, e.g. "end before start; rolled to next day".
    warnings: list[str] = Field(default_factory=list)

    occurrences: list[NormalizedOccurrence] = Field(default_factory=list)


class BuiltEvent(BaseModel):
    """One deduplicated Event, ready to store. One row of `content.events`.

    Not scraped and not normalised: derived from Listings that already are, by
    grouping a day's worth of them and rendering the richest of the group. It
    carries the Listings it was built from rather than a start time - when an
    Event is on stays the Occurrences' answer.
    """

    model_config = ConfigDict(extra="forbid")

    # Names the group by the Listing representing it, so an Event keeps its id
    # across runs for as long as the same Listing is the richest of the group.
    group_key: str
    date_local: date
    duration_days: int = 0

    primary_listing_id: int
    listing_ids: list[int] = Field(default_factory=list)

    source: str
    lang_primary: Literal["de", "en"] = "de"
    title_de: str | None = None
    title_en: str | None = None
    description_de: str | None = None
    description_en: str | None = None

    venue_name_raw: str | None = None
    street: str | None = None
    postcode: str | None = None
    city: str | None = None

    lat: float | None = None
    lon: float | None = None

    url: str | None = None
    origin_url: str | None = None
    image_url: str | None = None


class SourceRunStats(BaseModel):
    """What one Source did in one run. Written whether it succeeded or not.

    Recorded for every Source on every run, so that "did this Source quietly
    stop returning anything?" has an answer that does not depend on anyone
    having watched the logs at the time.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    source: str

    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None

    # False when the fetch raised. A run that could not fetch must not be
    # allowed to conclude that a Source's Listings are gone.
    fetched_ok: bool = False
    requests: int = 0
    raw_bytes: int = 0
    http_status_counts: dict[str, int] = Field(default_factory=dict)

    parsed_count: int = 0
    valid_count: int = 0
    quarantined_count: int = 0
    occurrences_count: int = 0

    error: str | None = None


class Rejected(BaseModel):
    """A parsed record normalisation refused. Quarantined, never dropped."""

    model_config = ConfigDict(extra="forbid")

    source: str
    source_ref: str | None = None
    # Machine-readable, because the valid/quarantined ratio per Source is a
    # health signal and "why" is the first question asked of a bad one.
    reason: str
    raw: dict[str, Any] = Field(default_factory=dict)
