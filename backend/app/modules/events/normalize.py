"""Turn a `RawListing` into a `NormalizedListing`, or quarantine it.

Three outcomes, never a silent drop:

* **valid** - stored as it is
* **repaired** - a fixable problem was fixed and recorded in `warnings`
* **invalid** - returned as a `Rejected` and quarantined with the raw record

The valid/quarantined ratio per Source doubles as a health signal, which is why
a rejection carries a machine-readable `reason`.

Values in, values out: no session, and nothing here reaches the network, so a
wrong repair can be reproduced from the record that caused it.
"""

import datetime as dt
import re
import unicodedata
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from .categories import to_canonical
from .scraped import (
    NIGHT_CUTOFF_HOUR,
    VIENNA_TZ,
    NormalizedListing,
    NormalizedOccurrence,
    RawListing,
    RawOccurrence,
    Rejected,
)

TZ = ZoneInfo(VIENNA_TZ)
UTC = dt.UTC

# An occurrence outside this window is almost certainly a parse error (a
# mis-inferred year, or a template date leaking through) rather than real data.
MAX_DAYS_AHEAD = 365 * 2
MAX_DAYS_BACK = 30

# Beyond this, expanding a range into per-day rows is pointless: it is a museum
# run or a permanent installation, not something with a per-day identity. Kept as
# a single range row; the day query filters on duration_days instead.
_SANE_MAX_SPAN_DAYS = 400

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
# Marketing prefixes that appear on some Sources and defeat title matching.
_TITLE_NOISE = re.compile(
    r"^(pick of the day|tipp|tip|neu|new|abgesagt|cancelled|canceled)\s*[:!|–-]\s*",
    re.IGNORECASE,
)


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace and strip; return None for empty."""
    if value is None:
        return None
    out = _WS.sub(" ", str(value)).strip()
    return out or None


# Stock artwork served in place of a real one. Storing it is worse than storing
# nothing: the consumer cannot tell it apart from a genuine image, so every
# Listing on the Source renders the same handful of pictures. Observed as both a
# directory (meetup serves /images/fallbacks/… for 100% of its JSON-LD `image`
# fields) and a filename (bandsintown's placeholder-artist.svg, on 84 Listings).
_IMAGE_PLACEHOLDER = re.compile(
    r"/(?:fallbacks?|placeholders?|default[-_]?images?|no[-_]image)s?/"
    r"|/(?:placeholder|default|no[-_]image|blank|dummy)[-_.]",
    re.IGNORECASE,
)
_SCHEME = re.compile(r"^[a-zA-Z][\w+.-]*:")
_NEEDS_QUOTING = re.compile(r"[^\w\-./:@&=+$,~%?#\[\]!*'()]")


def clean_image_url(
    value: str | None, base: str | None = None, warnings: list[str] | None = None
) -> str | None:
    """Absolutize a scraped image URL, or drop it if it is not usable.

    Sources publish images in three shapes and only the first is directly
    storable: absolute (`https://…`), protocol-relative (`//images.…`, from
    songkick) and site-relative (`/bilder/thumb_1.jpg`, from eventfinder and
    meinbezirk). The latter two are resolved against the page they came from,
    since a relative path is meaningless to a consumer reading the database.
    Anything left that is not http(s) - `data:` blobs, `javascript:` - is
    dropped, as are known placeholders.
    """
    if not value:
        return None
    url = str(value).strip()
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    elif not _SCHEME.match(url):
        if not base:
            return None
        url = urljoin(base, url)
    if not url.lower().startswith(("http://", "https://")):
        return None
    if _IMAGE_PLACEHOLDER.search(url):
        if warnings is not None:
            warnings.append("image_url is a placeholder; dropped")
        return None
    # Eventjet publishes filenames with literal spaces ("…/The Crown Shop.png"),
    # which urllib rejects outright. Encode the unsafe characters but leave any
    # existing %XX alone, so already-encoded URLs are not double-escaped.
    if _NEEDS_QUOTING.search(url):
        split = urlsplit(url)
        url = urlunsplit(
            (
                split.scheme,
                split.netloc,
                quote(split.path, safe="/%:@&=+$,~"),
                quote(split.query, safe="/%:@&=+$,~?"),
                split.fragment,
            )
        )
    return url


def norm_key(value: str | None) -> str | None:
    """Casefolded, accent-stripped, punctuation-free form for dedup keys.

    `Das Werk` / `dasWERK` and `Gleis19` / `Gleis 19` were observed as the
    same Venue across Sources, so Venue and title keys need aggressive
    normalisation to be useful to the deferred matcher.
    """
    if not value:
        return None
    text = _TITLE_NOISE.sub("", str(value))
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _PUNCT.sub(" ", text).casefold()
    text = _WS.sub("", text)
    return text or None


def _as_local(value: dt.datetime) -> dt.datetime:
    """Attach Vienna time to a naive datetime; convert an aware one."""
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def normalize_occurrence(
    raw: RawOccurrence, warnings: list[str]
) -> NormalizedOccurrence | None:
    """Resolve one raw occurrence into stored form.

    Returns None when the occurrence is unusable (caller decides whether the
    whole Listing is then invalid).
    """
    start = raw.start
    end = raw.end

    # A plain `date` (not datetime) is the Source saying "time unknown".
    all_day = isinstance(start, dt.date) and not isinstance(start, dt.datetime)

    if all_day:
        start_local = dt.datetime.combine(start, dt.time(0, 0), tzinfo=TZ)
        if end is None:
            end_date = start
        elif isinstance(end, dt.datetime):
            end_date = end.date()
        else:
            end_date = end
        if end_date < start:
            warnings.append(f"end {end_date} before start {start}; clamped")
            end_date = start
        end_local = dt.datetime.combine(end_date, dt.time(23, 59), tzinfo=TZ)
    else:
        start_local = _as_local(start)
        if end is None:
            end_local = None
        else:
            if isinstance(end, dt.datetime):
                end_local = _as_local(end)
            else:
                end_local = dt.datetime.combine(end, dt.time(23, 59), tzinfo=TZ)
            if end_local < start_local:
                # A club night listed 23:00–01:00 means the end is the next
                # morning. Roll it forward only if that yields a sane duration;
                # otherwise the end is simply wrong and gets dropped.
                rolled = end_local + dt.timedelta(days=1)
                if dt.timedelta(0) <= (rolled - start_local) <= dt.timedelta(hours=18):
                    end_local = rolled
                    warnings.append("end before start; rolled end to next day")
                else:
                    warnings.append("end before start; dropped end")
                    end_local = None

    date_local = start_local.date()

    span_days = 0
    if end_local is not None:
        span_days = (end_local.date() - date_local).days
        if span_days > _SANE_MAX_SPAN_DAYS:
            warnings.append(f"implausible span of {span_days} days; dropped end")
            end_local = None
            span_days = 0

    # After-midnight starts belong to the previous evening's programme too.
    night_of: dt.date | None = None
    if not all_day and start_local.hour < NIGHT_CUTOFF_HOUR:
        night_of = date_local - dt.timedelta(days=1)

    return NormalizedOccurrence(
        start_utc=start_local.astimezone(UTC),
        end_utc=end_local.astimezone(UTC) if end_local else None,
        start_local=start_local,
        end_local=end_local,
        date_local=date_local,
        night_of=night_of,
        all_day=all_day,
        duration_days=max(0, span_days),
    )


def _in_window(occ: NormalizedOccurrence, today: dt.date) -> bool:
    """Does this occurrence *overlap* the acceptable window?

    Testing the start date alone was wrong: a year-long exhibition that opened in
    January is running today, but its start is ~200 days in the past, so it was
    quarantined as out-of-window. That silently discarded most long runs -
    austria.info lost 12 of 16 Listings to it, and wien.info/wien.gv.at lost
    occurrences the same way.
    """
    window_start = today - dt.timedelta(days=MAX_DAYS_BACK)
    window_end = today + dt.timedelta(days=MAX_DAYS_AHEAD)
    occ_start = occ.date_local
    occ_end = occ.end_local.date() if occ.end_local else occ_start
    return occ_start <= window_end and occ_end >= window_start


def normalize(
    raw: RawListing, source: str, today: dt.date | None = None
) -> NormalizedListing | Rejected:
    """Normalise one parsed record, or return a Rejected explaining why not."""
    # Vienna's calendar day, not the host's: the window is a local-calendar
    # window, and Occurrences are dated by the Venue's own calendar.
    today = today or dt.datetime.now(TZ).date()
    warnings: list[str] = []

    title = clean_text(raw.title)
    title_alt = clean_text(raw.title_alt)
    if not title and not title_alt:
        return Rejected(
            source=source,
            source_ref=raw.source_ref,
            reason="missing_title",
            raw=raw.model_dump(mode="json"),
        )
    if not title:
        # Only the alternate locale had a title; promote it.
        title, title_alt = title_alt, None
        warnings.append("primary-locale title missing; used alternate")

    if not raw.occurrences:
        return Rejected(
            source=source,
            source_ref=raw.source_ref,
            reason="no_dates",
            raw=raw.model_dump(mode="json"),
        )

    occurrences: list[NormalizedOccurrence] = []
    seen_starts: set[dt.datetime] = set()
    out_of_window = 0
    for raw_occ in raw.occurrences:
        occ = normalize_occurrence(raw_occ, warnings)
        if occ is None:
            continue
        if not _in_window(occ, today):
            out_of_window += 1
            continue
        if occ.start_utc in seen_starts:
            continue
        seen_starts.add(occ.start_utc)
        occurrences.append(occ)

    if not occurrences:
        reason = "all_dates_out_of_window" if out_of_window else "no_valid_dates"
        return Rejected(
            source=source,
            source_ref=raw.source_ref,
            reason=reason,
            raw=raw.model_dump(mode="json"),
        )
    if out_of_window:
        warnings.append(f"dropped {out_of_window} occurrence(s) outside date window")

    occurrences.sort(key=lambda o: o.start_utc)

    # Language columns: map (lang, lang_alt) onto explicit de/en fields so the
    # site can pick with a fallback and a later translation pass can backfill.
    titles: dict[str, str | None] = {"de": None, "en": None}
    descriptions: dict[str, str | None] = {"de": None, "en": None}
    titles[raw.lang] = title
    descriptions[raw.lang] = clean_text(raw.description)
    if raw.lang_alt and raw.lang_alt != raw.lang:
        titles[raw.lang_alt] = title_alt
        descriptions[raw.lang_alt] = clean_text(raw.description_alt)

    # Austria's bounding box, generously. Catches swapped lat/lon and sentinel
    # zeros, both of which were observed in upstream data.
    lat, lon = raw.lat, raw.lon
    if (
        lat is not None
        and lon is not None
        and not (46.0 <= lat <= 49.5 and 9.0 <= lon <= 17.5)
    ):
        warnings.append(f"coordinates outside AT bbox ({lat},{lon}); dropped")
        lat = lon = None

    postcode = clean_text(raw.postcode)
    if postcode and not re.fullmatch(r"\d{4}", postcode):
        warnings.append(f"implausible postcode {postcode!r}; dropped")
        postcode = None

    city = clean_text(raw.city)
    if city and norm_key(city) in {"wien", "vienna", "wienaustria", "viennaaustria"}:
        city = "Wien"

    price_min, price_max = raw.price_min, raw.price_max
    if price_min is not None and price_max is not None and price_min > price_max:
        price_min, price_max = price_max, price_min
        warnings.append("price_min > price_max; swapped")

    return NormalizedListing(
        source=source,
        source_ref=raw.source_ref,
        url=raw.url,
        origin_url=raw.origin_url,
        title_de=titles["de"],
        title_en=titles["en"],
        description_de=descriptions["de"],
        description_en=descriptions["en"],
        lang_primary=raw.lang,
        title_norm=norm_key(title) or "",
        venue_name_norm=norm_key(raw.venue_name),
        venue_name_raw=clean_text(raw.venue_name),
        street=clean_text(raw.street),
        postcode=postcode,
        city=city,
        country=clean_text(raw.country) or "AT",
        lat=lat,
        lon=lon,
        geo_source="source" if lat is not None else "none",
        geo_precision="exact" if lat is not None else None,
        categories_raw=[c for c in (clean_text(c) for c in raw.categories_raw) if c],
        # The title is the fallback for Sources that publish no taxonomy at
        # all. Deliberately not the description - see to_canonical.
        category=to_canonical(raw.categories_raw, fallback_text=title),
        price_min=price_min,
        price_max=price_max,
        price_currency=clean_text(raw.price_currency),
        is_free=raw.is_free,
        ticket_url=raw.ticket_url,
        image_url=clean_image_url(
            raw.image_url, base=raw.url or raw.origin_url, warnings=warnings
        ),
        organizer=clean_text(raw.organizer),
        warnings=warnings,
        occurrences=occurrences,
    )
