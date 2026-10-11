import re
from collections.abc import Iterator

from bs4 import BeautifulSoup

from ....core.http import FetchError
from .. import region
from ..scraped import RawListing, RawOccurrence
from . import jsonld, markup
from .dates import day_if_midnight, paired_end, parse_iso_when
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="eventjet",
    locales=("de",),
    delay_seconds=0.8,
    notes=(
        "wp-json is 401 and listings carry no event markup, but DETAIL pages "
        "carry JSON-LD Event. No date filter - sweep and filter locally. "
        "Austria-wide, so region-filtered - and the JSON-LD has NO location, so "
        "the venue comes from .single__hero-venue-name plus address.address--venue "
        "and the coordinates from og `event:location:latitude` and "
        "`event:location:longitued`, the site's own typo. Midnight stamps mean "
        "'this day', not 00:00. The Event's own `image` is an unresolved @id to "
        "a shared site header - read og:image instead."
    ),
)

BASE = "https://events.eventjet.at"
MAX_DETAILS = 80


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    resp = ctx.http.get(f"{BASE}/")
    yield RawPayload(url=resp.url, body=resp.body, kind="listing")

    soup = BeautifulSoup(resp.text, "lxml")
    seen: set[str] = set()
    urls: list[str] = []
    for anchor in soup.select("a[href]"):
        href = anchor["href"].split("?")[0]
        if "/e/" not in href:
            continue
        full = href if href.startswith("http") else BASE + href
        if full in seen:
            continue
        seen.add(full)
        urls.append(full)

    for url in urls[:MAX_DETAILS]:
        try:
            detail = ctx.http.get(url)
        except FetchError:
            continue
        yield RawPayload(url=detail.url, body=detail.body, kind="detail")


_ADDRESS = re.compile(
    r"^(?P<street>.*?),\s*(?P<postcode>\d{4})\s+(?P<city>[^,]+)", re.DOTALL
)


def _coordinates(soup: BeautifulSoup) -> tuple[float | None, float | None]:

    def content(key: str) -> str:
        tag = soup.find("meta", attrs={"property": key}) or soup.find(
            "meta", attrs={"name": key}
        )
        return (tag.get("content") or "").strip() if tag else ""

    try:
        latitude = float(content("event:location:latitude"))
        longitude = float(
            content("event:location:longitued") or content("event:location:longitude")
        )
    except ValueError:
        return None, None
    return latitude, longitude


def _venue(soup: BeautifulSoup) -> dict[str, str | None]:
    name = soup.select_one(".single__hero-venue-name")
    details = soup.select_one("address.address--venue .address__details")
    where: dict[str, str | None] = {
        "venue_name": name.get_text(" ", strip=True) if name else None,
        "street": None,
        "postcode": None,
        "city": None,
    }
    if details is not None:
        found = _ADDRESS.match(details.get_text(" ", strip=True))
        if found:
            where["street"] = found.group("street").strip() or None
            where["postcode"] = found.group("postcode")
            where["city"] = found.group("city").strip() or None
    return where


def parse(payload: RawPayload) -> Iterator[RawListing]:
    if payload.kind != "detail":
        return

    soup = BeautifulSoup(payload.text, "lxml")
    on_page = _venue(soup)
    latitude, longitude = _coordinates(soup)
    page_image = markup.og_image(soup)

    seen: set[str] = set()

    for node in jsonld.events_in(payload.text):
        start = day_if_midnight(parse_iso_when(node.get("startDate")))
        if start is None:
            continue
        end = paired_end(start, parse_iso_when(node.get("endDate")))

        slug = (node.get("url") or payload.url).rstrip("/").rsplit("/", 1)[-1]
        source_ref = f"{slug}-{start.isoformat()[:16]}"[:120]
        if source_ref in seen:
            continue

        where = {**jsonld.place(node), **{k: v for k, v in on_page.items() if v}}
        if not region.is_vienna(latitude, longitude, where["postcode"], where["city"]):
            continue
        seen.add(source_ref)

        value, currency = jsonld.price(node)

        yield RawListing(
            source_ref=source_ref,
            occurrences=[RawOccurrence(start=start, end=end)],
            url=node.get("url") or payload.url,
            ticket_url=payload.url,
            title=node.get("name"),
            description=node.get("description"),
            lang="de",
            venue_name=where["venue_name"],
            street=where["street"],
            postcode=where["postcode"],
            city=where["city"] or "Wien",
            country=where["country"] or "AT",
            lat=latitude,
            lon=longitude,
            price_min=value,
            price_currency=currency,
            image_url=jsonld.image(node) or page_image,
        )
