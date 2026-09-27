"""1000thingsmagazine.com - curated editorial picks with real street addresses.

The site has **no event post type**; it publishes two WordPress posts that are
rewritten in place each week and between them cover Mon–Sun. Their ids are
therefore stable and are fetched directly - `?search=` is too fuzzy, ranking the
monthly roundups in alongside them.

The **monthly roundup is deliberately excluded**: only 5 of its 15 entries carry a
full `Venue: Street N, ZIP`, the rest degrade to a postcode or a bare venue name,
and it uses a different template (`Die wichtigsten Infos:` sub-headings). Two
parsers for worse data is a poor trade.

Structure per event: an `<h2>` under a weekday divider, then a `<ul>`:

    li[0]  MO, 10.8.2026, 20:30 Uhr                  <- weekday, date(s), time(s)
    li[1]  Kino am Dach | Urban-Loritz-Platz 2, 1070 <- venue | street, ZIP
    li[2:] description lines, last one usually price

Three dateline shapes, all of which occur:

    DO, 6.8.2026, 17–20 Uhr                            one day
    DO, 6.8 – SA, 8.8.2026, ab 17 Uhr                  a run; note the year-less first date
    FR, 7.8.2026, ab 17 Uhr & SA, 8.8.2026, 10–23 Uhr  separate days, each with its OWN time

That third shape is why each date takes the time from its own segment rather than
the first time in the line - otherwise Saturday inherits Friday's 17:00 instead of
10:00.

**No `image_url`, deliberately.** The rendered post body contains no `<img>`
at all - it is a text roundup. The post has a WordPress featured image, but that
is one image for the whole week's article, not per event.

What it does carry per event is an **Instagram embed** - the organiser's own
announcement - which lands in `origin_url`. Two things hide it: the markup is
base64 in a Borlabs cookie-blocker attribute rather than a `<blockquote>`, and
the embed follows the event's `<ul>` rather than preceding it, so headings are
mapped to permalinks in a pre-pass. Instagram itself answers a login wall
unauthenticated, so the permalink cannot be resolved to a picture without an
oEmbed token; it is stored as the link it is, not as an image.
"""

import base64
import binascii
import datetime as dt
import json
import re
from collections.abc import Iterator
from html import unescape
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..scraped import VIENNA_TZ, RawListing, RawOccurrence
from .spec import FetchContext, RawPayload, SourceSpec

TZ = ZoneInfo(VIENNA_TZ)

SPEC = SourceSpec(
    name="thousandthings",
    locales=("de",),
    delay_seconds=0.5,
    notes=(
        "No event post type: two weekly posts (ids 22888, 918422) rewritten in "
        "place, current week only. Dateline 'A & B' gives each date its OWN time. "
        "Dates carry no year. Monthly roundup excluded (worse addresses, second "
        "template)."
    ),
)

API = "https://www.1000thingsmagazine.com/wp-json/wp/v2/posts"
# Stable ids; a title check guards against the posts being repurposed.
POSTS = {22888: "Weekend-Preview", 918422: "Wochenvorschau"}

WEEKDAYS = {
    "MONTAG",
    "DIENSTAG",
    "MITTWOCH",
    "DONNERSTAG",
    "FREITAG",
    "SAMSTAG",
    "SONNTAG",
}

# Matches "6.8.2026" and the year-less "6.8" of "DO, 6.8 - SA, 8.8.2026".
_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.?(?:(\d{4}))?")
_TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(?:[–—-]\s*\d{1,2}(?::\d{2})?\s*)?Uhr")
_ZIP = re.compile(r"\b(\d{4})\b")
_PRICE_HINT = re.compile(r"Eintritt|Tickets|€|,-|kostenlos|gratis", re.IGNORECASE)
_PRICE_VALUE = re.compile(r"(\d+(?:[.,]\d{2})?)\s*€|€\s*(\d+(?:[.,]\d{2})?)")


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    for post_id in POSTS:
        resp = ctx.http.get(
            f"{API}/{post_id}?_fields=id,title,modified,link,content",
            headers={
                "Accept": "application/json",
                "Referer": "https://www.1000thingsmagazine.com/de/at/wien/",
            },
        )
        yield RawPayload(
            url=resp.url, body=resp.body, kind="post", meta={"post_id": post_id}
        )


def _parse_time(segment: str) -> str | None:
    """First clock time in a dateline segment, ignoring the dates themselves."""
    cleaned = _DATE.sub(" ", segment)
    match = _TIME.search(cleaned)
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2) or '00'}"


def parse_dateline(line: str, default_year: int) -> list[tuple[dt.date, str | None]]:
    """Turn the date line into (date, start_time) pairs.

    Each date takes the time from *its own* segment of the line, because the
    "A & B" form gives different times per day.
    """
    hits = list(_DATE.finditer(line))
    if not hits:
        return []
    # A year stated anywhere applies to the dates that omit it.
    year = next((int(m.group(3)) for m in reversed(hits) if m.group(3)), default_year)

    dated: list[tuple[dt.date, str | None]] = []
    for index, match in enumerate(hits):
        try:
            day = dt.date(
                int(match.group(3) or year), int(match.group(2)), int(match.group(1))
            )
        except ValueError:
            continue
        segment_end = hits[index + 1].start() if index + 1 < len(hits) else len(line)
        dated.append((day, _parse_time(line[match.end() : segment_end])))
    if not dated:
        return []

    fallback = next((time for _, time in dated if time), None)
    dated = [(day, time or fallback) for day, time in dated]
    if len(dated) < 2:
        return dated

    # A dash between the first two dates is a run; "&" or "," means separate days.
    between = line[hits[0].end() : hits[1].start()]
    if re.search(r"[–—-]", between) and "&" not in between:
        first, last = min(d for d, _ in dated), max(d for d, _ in dated)
        if (last - first).days <= 60:
            return [
                (first + dt.timedelta(days=offset), fallback)
                for offset in range((last - first).days + 1)
            ]
    return dated


def parse_venues(line: str) -> list[dict[str, str | None]]:
    """Split "Venue | Street N, ZIP", possibly joined by "&"."""
    out: list[dict[str, str | None]] = []
    for part in re.split(r"\s+&\s+", line):
        part = part.strip(" .,")
        if not part:
            continue
        venue, _, address = part.partition("|")
        venue, address = venue.strip(" ,"), address.strip()
        if not address:
            # "Schlingermarkt, 1210" - a bare name plus postcode, no street.
            address, venue = venue, _ZIP.sub("", venue).strip(" ,")
        zip_match = _ZIP.search(address)
        street = city = postcode = None
        if zip_match:
            postcode = zip_match.group(1)
            street = address[: zip_match.start()].strip(" ,") or None
            city = address[zip_match.end() :].strip(" ,") or None
        elif address != venue:
            street = address or None
        # Never echo the venue name back as a street: it geocodes to something
        # arbitrary. "Schlingermarkt, 1210" has no street at all.
        if street and street == venue:
            street = None
        out.append(
            {
                "name": venue or None,
                "street": street,
                "postcode": postcode,
                "city": city,
            }
        )
    return out


_PERMALINK = re.compile(r'data-instgrm-permalink="([^"?]+)')


def embed_links(soup: BeautifulSoup) -> dict[str, str]:
    """Map each heading to the Instagram post embedded under it.

    The site runs a consent plugin that replaces every embed with a placeholder
    holding the real markup base64-encoded in `data-borlabs-cookie-content` -
    so the permalink is invisible to any search of the rendered HTML.
    """
    out: dict[str, str] = {}
    heading: str | None = None
    for element in soup.find_all(["h2", "h3", "div"]):
        if element.name in ("h2", "h3"):
            text = re.sub(r"\s+", " ", element.get_text(" ", strip=True))
            heading = None if text.upper() in WEEKDAYS else (text or None)
            continue
        blob = element.get("data-borlabs-cookie-content")
        if not blob or heading is None or heading in out:
            continue
        try:
            decoded = base64.b64decode(unescape(blob)).decode("utf-8", "replace")
        except (ValueError, binascii.Error):
            continue
        match = _PERMALINK.search(decoded)
        if match:
            out[heading] = match.group(1)
    return out


def _price(lines: list[str]) -> tuple[float | None, bool | None, str | None]:
    for line in reversed(lines):
        if not _PRICE_HINT.search(line):
            continue
        if re.search(r"kostenlos|gratis|Eintritt frei", line, re.IGNORECASE):
            return 0.0, True, line
        match = _PRICE_VALUE.search(line)
        if match:
            raw = match.group(1) or match.group(2)
            try:
                return float(raw.replace(",", ".")), False, line
            except ValueError:
                return None, None, line
        return None, None, line
    return None, None, None


def parse(payload: RawPayload) -> Iterator[RawListing]:
    post = json.loads(payload.text)
    post_id = payload.meta.get("post_id")
    title = (post.get("title") or {}).get("rendered", "")
    expected = POSTS.get(post_id)
    if expected and expected not in title:
        # The post was repurposed; emitting whatever is there now would be worse
        # than emitting nothing, and the health rules will flag the zero.
        return

    html = (post.get("content") or {}).get("rendered", "")
    link = post.get("link")
    # The post's own `modified` stamp decides the year a date omits, which is
    # what keeps this parse reproducible over a saved payload. Vienna's current
    # year is only the fallback for a post that carries no stamp at all.
    modified = str(post.get("modified") or "")
    default_year = dt.datetime.now(TZ).year
    if len(modified) >= 4 and modified[:4].isdigit():
        default_year = int(modified[:4])

    soup = BeautifulSoup(html, "lxml")
    links = embed_links(soup)
    heading: str | None = None
    seen: set[str] = set()

    for element in soup.find_all(["h2", "h3", "ul"]):
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True))

        if element.name in ("h2", "h3"):
            # A weekday divider is not a heading; it only ends the previous one.
            if text.upper() in WEEKDAYS:
                heading = None
            elif text and "Newsletter" not in text:
                heading = text
            else:
                heading = None
            continue

        # A <ul> only counts as detail if a heading preceded it.
        if heading is None:
            continue
        items = [
            re.sub(r"\s+", " ", li.get_text(" ", strip=True))
            for li in element.select("li")
        ]
        items = [item for item in items if item]
        if len(items) < 2 or items[0].startswith("Mehr lesen"):
            continue

        schedule = parse_dateline(items[0], default_year)
        if not schedule:
            continue
        venues = parse_venues(items[1])
        primary = venues[0] if venues else {}
        body = items[2:]
        price, is_free, price_line = _price(body)
        description = " | ".join(line for line in body if line != price_line) or None

        # One Listing with all its dates; each Occurrence keeps the time from
        # its own dateline segment.
        occurrences = []
        for day, time_text in schedule:
            if time_text:
                hour, minute = (int(part) for part in time_text.split(":"))
                occurrences.append(
                    RawOccurrence(start=dt.datetime.combine(day, dt.time(hour, minute)))
                )
            else:
                occurrences.append(RawOccurrence(start=day))

        # The post carries no id per happening, so derive one that survives the
        # week's rewrites: the title plus its first date.
        listing_key = f"{heading}-{schedule[0][0].isoformat()}"
        source_ref = re.sub(r"[^\w\-]+", "-", listing_key.casefold()).strip("-")[:120]
        if source_ref in seen:
            continue
        seen.add(source_ref)

        yield RawListing(
            source_ref=source_ref,
            occurrences=occurrences,
            url=link,
            # The organiser's own announcement, and a strong cross-Source key:
            # two Sources pointing at one Instagram post are describing one
            # real-world happening.
            origin_url=links.get(heading),
            title=heading,
            description=description,
            lang="de",
            venue_name=primary.get("name"),
            street=primary.get("street"),
            postcode=primary.get("postcode"),
            city=primary.get("city") or "Wien",
            country="AT",
            price_min=price,
            price_currency="EUR" if price is not None else None,
            is_free=is_free,
            # The post has no taxonomy; the weekday divider is not a category.
            categories_raw=[],
        )
