from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
GeoPrecision = Literal["exact", "street", "city", "approx"]

VIENNA_TZ = "Europe/Vienna"

NIGHT_CUTOFF_HOUR = 6


class RawOccurrence(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: datetime | date
    end: datetime | date | None = None


class RawListing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str
    occurrences: list[RawOccurrence] = Field(default_factory=list)

    url: str | None = None
    origin_url: str | None = None

    title: str | None = None
    description: str | None = None
    lang: Literal["de", "en"] = "de"
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
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, int | float):
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)
        return value


class NormalizedOccurrence(BaseModel):
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

    warnings: list[str] = Field(default_factory=list)

    occurrences: list[NormalizedOccurrence] = Field(default_factory=list)


class BuiltEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

    run_id: str
    source: str

    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None

    fetched_ok: bool = False
    requests: int = 0
    raw_bytes: int = 0
    http_status_counts: dict[str, int] = Field(default_factory=dict)

    parsed_count: int = 0
    valid_count: int = 0
    quarantined_count: int = 0
    occurrences_count: int = 0

    error: str | None = None


class ScrapeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    declined: str | None = None
    sources: list[SourceRunStats] = Field(default_factory=list)


class Rejected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_ref: str | None = None
    reason: str
    raw: dict[str, Any] = Field(default_factory=dict)
