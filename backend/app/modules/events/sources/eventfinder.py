"""eventfinder.at - a large Austrian calendar, searched against a session.

**The visible date form is a decoy.** `GET /wien/veranstaltungen/?datum_ab=...&
datum_bis=...` is accepted and silently ignored - it returns today's list whatever
you pass, which looks entirely plausible. The working route is a POST search:

    POST /veranstaltungen.php  action=do_search&search_ort=Wien
                               &search_von=YYYY-MM-DD&search_bis=YYYY-MM-DD

The result set is then held **server-side against the session**, so paging with
`GET ?page=N` only works when cookies are carried between requests, which is what
`use_cookies` is for.

Second trap: **same-day results decay through the evening.** An entry is dropped
once it has started, so a query for *today* returns fewer and fewer hits as the
day goes on, and eventually zero - observed going 15 to 0 between 14:50 and 22:12.
Future dates are unaffected. An empty same-day result is therefore not
necessarily breakage, which is worth knowing before reading a zero here as one.
"""

import datetime as dt
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..scraped import RawListing, RawOccurrence
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="eventfinder",
    locales=("de",),
    delay_seconds=0.6,
    use_cookies=True,
    notes=(
        "GET ?datum_ab/datum_bis is a DECOY, silently ignored. Must POST "
        "/veranstaltungen.php then page with the session cookie. Same-day "
        "results decay to 0 through the evening (started events are dropped)."
    ),
)

BASE = "https://www.eventfinder.at"
SEARCH_URL = f"{BASE}/veranstaltungen.php"
LISTING_REFERER = f"{BASE}/wien/veranstaltungen/"
MAX_PAGES = 20

# The URL slug encodes the exact occurrence: ...-am-2026-08-15-um-20-00-uhr/
_SLUG_DATETIME = re.compile(r"am-(\d{4})-(\d{2})-(\d{2})-um-(\d{2})-(\d{2})")
_LISTING_ID = re.compile(r"/veranstaltung/(\d+)/")


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    form = {
        "action": "do_search",
        "search_ort": "Wien",
        "search_was": "",
        "search_von": ctx.date_from.isoformat(),
        "search_bis": ctx.date_to.isoformat(),
        "umkreis": "0",
        "suche_free": "0",
        "suche_onlinetickets": "0",
    }
    resp = ctx.http.post_form(SEARCH_URL, form, headers={"Referer": LISTING_REFERER})
    yield RawPayload(url=resp.url, body=resp.body, kind="listing", meta={"page": 1})

    if _count_results(resp.text) == 0:
        return

    for page in range(2, MAX_PAGES + 1):
        resp = ctx.http.get(
            f"{SEARCH_URL}?page={page}", headers={"Referer": SEARCH_URL}
        )
        yield RawPayload(
            url=resp.url, body=resp.body, kind="listing", meta={"page": page}
        )
        if _count_results(resp.text) == 0:
            break


def _result_anchors(soup: BeautifulSoup) -> Iterator[tuple[Tag, Tag]]:
    """Result links only: a carousel card is a recommendation, not a result."""
    for heading in soup.select("h3.titel"):
        anchor = heading.select_one("a[href]")
        if anchor is None or anchor.find_parent(class_="splide__slide"):
            continue
        yield heading, anchor


def _count_results(html: str) -> int:
    return sum(1 for _ in _result_anchors(BeautifulSoup(html, "lxml")))


def parse(payload: RawPayload) -> Iterator[RawListing]:
    soup = BeautifulSoup(payload.text, "lxml")

    for heading, anchor in _result_anchors(soup):
        href = anchor["href"]
        slug_match = _SLUG_DATETIME.search(href)
        if not slug_match:
            # Without a date in the slug there is no placing it, and the
            # card's own text is not reliable enough to guess from.
            continue
        year, month, day, hour, minute = (int(g) for g in slug_match.groups())

        id_match = _LISTING_ID.search(href)
        source_ref = (
            id_match.group(1) if id_match else href.rstrip("/").rsplit("/", 1)[-1]
        )

        card = heading.find_parent("div", class_="card-body")
        venue = teaser = None
        if card is not None:
            footer = card.select_one(".card-body-footer")
            if footer:
                venue = footer.get_text(" ", strip=True) or None
            description = card.select_one(".beschreibung")
            if description:
                teaser = description.get_text(" ", strip=True) or None

        # The thumbnail is a sibling of .card-body, not a child: div.card.event
        # wraps both. Scoping the lookup to that card keeps a carousel image from
        # being attached to a result row.
        image = None
        wrapper = heading.find_parent("div", class_="card")
        if wrapper is not None:
            img = wrapper.select_one("img.img-thumbnail[src]")
            if img is not None:
                image = img["src"]

        yield RawListing(
            source_ref=source_ref,
            # Naive on purpose: the slug states Vienna wall-clock, which is
            # what `RawOccurrence` reads a naive datetime as. Attaching an
            # offset here would shift the very time the slug spelled out.
            occurrences=[
                RawOccurrence(
                    start=dt.datetime(year, month, day, hour, minute)  # noqa: DTZ001
                )
            ],
            url=href if href.startswith("http") else BASE + href,
            title=anchor.get_text(" ", strip=True),
            description=teaser,
            lang="de",
            # The footer reads "Venue Name, Wien". Kept whole: splitting on the
            # last comma is unreliable when the Venue's name contains one.
            venue_name=venue,
            city="Wien",
            country="AT",
            # Site-relative ("/bilder/thumb_1.jpg"); normalize resolves it
            # against `url`.
            image_url=image,
        )
