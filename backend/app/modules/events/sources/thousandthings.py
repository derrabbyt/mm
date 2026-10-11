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
    cleaned = _DATE.sub(" ", segment)
    match = _TIME.search(cleaned)
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2) or '00'}"


def parse_dateline(line: str, default_year: int) -> list[tuple[dt.date, str | None]]:
    hits = list(_DATE.finditer(line))
    if not hits:
        return []
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
    out: list[dict[str, str | None]] = []
    for part in re.split(r"\s+&\s+", line):
        part = part.strip(" .,")
        if not part:
            continue
        venue, _, address = part.partition("|")
        venue, address = venue.strip(" ,"), address.strip()
        if not address:
            address, venue = venue, _ZIP.sub("", venue).strip(" ,")
        zip_match = _ZIP.search(address)
        street = city = postcode = None
        if zip_match:
            postcode = zip_match.group(1)
            street = address[: zip_match.start()].strip(" ,") or None
            city = address[zip_match.end() :].strip(" ,") or None
        elif address != venue:
            street = address or None
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
        return

    html = (post.get("content") or {}).get("rendered", "")
    link = post.get("link")
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
            if text.upper() in WEEKDAYS:
                heading = None
            elif text and "Newsletter" not in text:
                heading = text
            else:
                heading = None
            continue

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

        occurrences = []
        for day, time_text in schedule:
            if time_text:
                hour, minute = (int(part) for part in time_text.split(":"))
                occurrences.append(
                    RawOccurrence(start=dt.datetime.combine(day, dt.time(hour, minute)))
                )
            else:
                occurrences.append(RawOccurrence(start=day))

        listing_key = f"{heading}-{schedule[0][0].isoformat()}"
        source_ref = re.sub(r"[^\w\-]+", "-", listing_key.casefold()).strip("-")[:120]
        if source_ref in seen:
            continue
        seen.add(source_ref)

        yield RawListing(
            source_ref=source_ref,
            occurrences=occurrences,
            url=link,
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
            categories_raw=[],
        )
