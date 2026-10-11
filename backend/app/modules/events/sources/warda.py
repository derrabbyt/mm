import datetime as dt
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup

from ....core.http import FetchError
from ..scraped import RawListing, RawOccurrence
from . import dates, markup
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="warda",
    locales=("de",),
    delay_seconds=0.8,
    notes=(
        "No JSON-LD anywhere. The day is in .date_bar ('Freitag 14. August "
        "2026'); the time is NOT - it follows a 'Beginn:' label elsewhere, and "
        "must be read only from after that label. Venue in .event_place. Custom "
        "post type hidden from REST. No "
        "confirmed date filter: sweep the listing and filter locally."
    ),
)

BASE = "https://warda.at"
LISTING = f"{BASE}/events/"
MAX_DETAILS = 80

_ZIP = re.compile(r"\b(\d{4})\b")


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    resp = ctx.http.get(LISTING)
    yield RawPayload(url=resp.url, body=resp.body, kind="listing")

    soup = BeautifulSoup(resp.text, "lxml")
    seen: set[str] = set()
    urls: list[str] = []
    for anchor in soup.select("a[href]"):
        href = anchor["href"].split("?")[0]
        if "/events/" not in href or href.rstrip("/").endswith("/events"):
            continue
        if href in seen:
            continue
        seen.add(href)
        urls.append(href if href.startswith("http") else BASE + href)

    for url in urls[:MAX_DETAILS]:
        try:
            detail = ctx.http.get(url)
        except FetchError:
            continue
        yield RawPayload(url=detail.url, body=detail.body, kind="detail")


def _place(soup: BeautifulSoup) -> tuple[str | None, str | None, str | None]:
    container = soup.select_one(".event_place")
    if container is None:
        return None, None, None

    heading = container.select_one("h6")
    venue = heading.get_text(" ", strip=True) if heading else None

    address_el = container.select_one("p")
    address = (
        re.sub(r"\s+", " ", address_el.get_text(" ", strip=True)) if address_el else ""
    )
    zip_match = _ZIP.search(address)
    postcode = zip_match.group(1) if zip_match else None
    street = (address[: zip_match.start()] if zip_match else address).strip(
        " ,"
    ) or None
    return venue or None, street, postcode


def _start_time(soup: BeautifulSoup) -> dt.time | None:
    label = soup.find(string=re.compile(r"Beginn", re.IGNORECASE))
    if label is None:
        return None
    container = label.parent.parent if label.parent else None
    text = container.get_text(" ", strip=True) if container else str(label)
    after = re.split(r"Beginn\s*:?", text, maxsplit=1, flags=re.IGNORECASE)
    return dates.parse_time(after[1]) if len(after) > 1 else None


def parse(payload: RawPayload) -> Iterator[RawListing]:
    if payload.kind != "detail":
        return

    soup = BeautifulSoup(payload.text, "lxml")

    date_bar = soup.select_one(".date_bar")
    if date_bar is None:
        return
    day = dates.parse_date(
        re.sub(r"\s+", " ", date_bar.get_text(" ", strip=True)),
        reference=payload.fetched_at.date(),
    )
    if day is None:
        return
    start = dates.combine(day, _start_time(soup))

    heading = soup.select_one("h1")
    title = heading.get_text(" ", strip=True) if heading else None
    if not title:
        return

    venue, street, postcode = _place(soup)

    categories = [
        anchor.get_text(" ", strip=True)
        for anchor in soup.select(
            'a[rel~="tag"], a[href*="/category/"], a[href*="/event-kategorie/"]'
        )
        if anchor.get_text(strip=True)
    ]
    categories = list(dict.fromkeys(categories))[:6] or ["Party"]

    slug = payload.url.rstrip("/").rsplit("/", 1)[-1]
    image = markup.og_image(soup)
    if image is None:
        img = soup.select_one(".event_image img[src], article img[src]")
        if img is not None and not markup.is_tracking_pixel(img):
            image = img.get("src")

    yield RawListing(
        source_ref=slug[:120],
        occurrences=[RawOccurrence(start=start)],
        url=payload.url,
        title=title,
        lang="de",
        venue_name=venue,
        street=street,
        postcode=postcode,
        city="Wien",
        country="AT",
        categories_raw=categories,
        image_url=image,
    )
