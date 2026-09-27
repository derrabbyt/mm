"""A Source's parse, over a document it actually served.

The highest seam reachable without the network: a pure function from a saved
payload to Listings. Every case here is pointed at a trap the site sets, not at
the happy path - a parser that starts producing nonsense gets fixed against this
file, and the fixture is the exact document it broke on.
"""

import datetime as dt
import json
import pathlib

import pytest

from app.core.http import encode_query
from app.modules.events.scraped import RawListing
from app.modules.events.sources import (
    austria_info,
    dates,
    discover,
    event_spotter,
    eventfinder,
    events_at,
    goabase,
    goodnight,
    rausgegangen,
    songkick,
    thousandthings,
    warda,
    wien_info,
)
from app.modules.events.sources.dates import parse_iso_datetime
from app.modules.events.sources.spec import RawPayload

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def payload(source: str, filename: str, kind: str = "listing", **meta) -> RawPayload:
    path = FIXTURES / source / filename
    return RawPayload(
        url=f"fixture://{source}/{filename}",
        body=path.read_bytes(),
        kind=kind,
        meta=meta,
    )


def _raw_parties() -> list[dict]:
    text = (FIXTURES / "goabase" / "partylist.json").read_text(encoding="utf-8")
    return json.loads(text)["partylist"]


class TestGoabase:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(goabase.parse(payload("goabase", "partylist.json", "partylist")))

    def test_parses_parties(self, listings):
        assert listings

    def test_city_centroid_coordinates_are_dropped(self, listings):
        """The whole feed pins Vienna parties at (48.2, 16.4) - one fake point.

        A single shared pin is worse for "what is on near here" than no pin at
        all, so low-precision coordinates are dropped and left to the geocoder.
        """
        assert all(listing.lat is None for listing in listings), (
            "1-decimal coordinates are city centroids, not Venues"
        )

    def test_precise_coordinates_would_be_kept(self):
        assert goabase._coords({"geoLat": 48.20123, "geoLon": 16.37456}) == (
            48.20123,
            16.37456,
        )
        assert goabase._coords({"geoLat": 48.2, "geoLon": 16.4}) == (None, None)
        assert goabase._coords({"geoLat": 0, "geoLon": 0}) == (None, None)

    def test_all_three_city_spellings_are_accepted(self, listings):
        """The feed writes the same city as Wien, WIEN and Vienna."""
        vienna_like = sum(
            1
            for party in _raw_parties()
            if (party.get("nameTown") or "").casefold().startswith(("wien", "vienna"))
        )
        assert len(listings) == vienna_like

    def test_a_party_in_another_town_is_excluded(self, listings):
        titles = {listing.title for listing in listings}
        outside = [
            party
            for party in _raw_parties()
            if not (party.get("nameTown") or "")
            .casefold()
            .startswith(("wien", "vienna"))
        ]
        assert outside, "the fixture should carry at least one non-Vienna party"
        for party in outside:
            assert party["nameParty"] not in titles

    def test_the_json_path_segment_is_required(self):
        """Without `/json/` the same path answers with HTML."""
        assert "/json/" in goabase.ENDPOINT


class TestWienInfo:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(
            wien_info.parse(
                payload("wien_info", "events-en.json", "events-en", locale="en")
            )
        )

    def test_parses_listings(self, listings):
        assert len(listings) >= 20, "the fixture holds 24 items; most should parse"
        assert all(one.source_event_id for one in listings)
        assert all(one.occurrences for one in listings)

    def test_the_locale_comes_from_the_payload(self, listings):
        """The English payload must yield English rows, not the `de` default."""
        assert {one.lang for one in listings} == {"en"}

    def test_an_explicit_date_list_is_not_collapsed(self, listings):
        """A gapped `dates[]` must become one Occurrence per listed date.

        Collapsing [14th, 15th, 16th, 20th] into a 14th-20th span would claim
        the happening runs on the 17th to 19th, which is false.
        """
        multi = [one for one in listings if len(one.occurrences) > 1]
        assert multi, "the fixture includes items with an explicit dates[]"
        for one in multi:
            starts = [occurrence.start for occurrence in one.occurrences]
            assert len(starts) == len(set(starts))

    def test_a_span_stays_one_occurrence(self, listings):
        spans = [
            one
            for one in listings
            if len(one.occurrences) == 1 and one.occurrences[0].end
        ]
        assert spans, "the fixture includes startDate/endDate span items"
        for one in spans:
            assert one.occurrences[0].end >= one.occurrences[0].start

    def test_every_occurrence_is_all_day(self, listings):
        """The listing payload has no clock times, so every start is a `date`.

        A datetime here would mean 00:00 was fabricated, which sorts every
        Listing to the top of its day.
        """
        for one in listings:
            for occurrence in one.occurrences:
                assert not isinstance(occurrence.start, dt.datetime)

    def test_urls_are_absolute(self, listings):
        for one in listings:
            if one.url:
                assert one.url.startswith("http")


class TestEventsAt:
    DAY = dt.date(2026, 8, 15)

    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(
            events_at.parse(
                payload(
                    "events_at",
                    f"listing-{self.DAY.isoformat()}.html",
                    date=self.DAY.isoformat(),
                )
            )
        )

    def test_parses_listings(self, listings):
        assert len(listings) == 6, "the fixture was trimmed to 6 timeline blocks"

    def test_the_double_card_is_deduplicated(self, listings):
        """Each happening appears twice per block: a desktop and a mobile card."""
        urls = [one.url for one in listings]
        assert len(urls) == len(set(urls))

    def test_the_queried_day_wins_over_the_card_time(self, listings):
        """The card's <time> is the series' next showtime, not this day's."""
        for one in listings:
            assert len(one.occurrences) == 1
            assert one.occurrences[0].start == self.DAY

    def test_title_and_venue_are_extracted(self, listings):
        assert all(one.title for one in listings)
        assert any(one.venue_name for one in listings)

    def test_without_the_queried_date_nothing_is_yielded(self):
        """There is no way to know when it is on, so nothing is claimed."""
        assert (
            list(
                events_at.parse(
                    payload("events_at", f"listing-{self.DAY.isoformat()}.html")
                )
            )
            == []
        )

    def test_the_url_builder_keeps_the_brackets_literal(self):
        """%5B%5D returns 200 with the filters ignored - see core/http.py."""
        url = events_at._url(self.DAY, 1)
        assert "state[]=Wien" in url
        assert "event_type[]=konzert" in url
        assert "%5B%5D" not in url
        assert "date=2026-08-15" in url


class TestGoodnight:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(
            goodnight.parse(
                payload("goodnight", "grouped-events.json", "grouped-events")
            )
        )

    def test_parses_listings(self, listings):
        assert listings, "the fixture covers a 14-day window"
        assert all(one.source_event_id for one in listings)

    def test_the_same_item_in_several_day_buckets_is_one_listing(self, listings):
        """A multi-day item repeats in every day bucket of the response."""
        ids = [one.source_event_id for one in listings]
        assert len(ids) == len(set(ids))

    def test_an_empty_list_address_does_not_read_as_missing(self, listings):
        """A one_off_location item carries `address: []` - an empty LIST.

        Reading that as "no addresses anywhere" is the bug that made this Source
        look address-less at first.
        """
        assert [one for one in listings if one.street], (
            "linked-location items do carry street addresses"
        )

    def test_a_time_produces_a_datetime(self, listings):
        assert [
            one for one in listings if isinstance(one.occurrences[0].start, dt.datetime)
        ], "most of them have a time_start"

    def test_the_organiser_link_is_kept(self, listings):
        assert any(one.origin_url for one in listings), "event_link is a dedup key"


class TestRausgegangen:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(
            rausgegangen.parse(payload("rausgegangen", "detail-event.html", "detail"))
        )

    def test_parses_a_detail_page(self, listings):
        assert len(listings) >= 1

    def test_a_listing_payload_yields_nothing(self):
        """Listing pages exist to find detail URLs; the structure is in details."""
        assert (
            list(
                rausgegangen.parse(
                    payload("rausgegangen", "listing-concerts.html", "listing")
                )
            )
            == []
        )

    def test_exact_times_keep_their_offset(self, listings):
        occurrence = listings[0].occurrences[0]
        assert isinstance(occurrence.start, dt.datetime)
        assert occurrence.start.tzinfo is not None, "the site emits +0200"

    def test_the_address_is_complete(self, listings):
        assert listings[0].postcode, "JSON-LD carries a full PostalAddress"
        assert listings[0].city

    def test_a_price_is_extracted(self):
        assert rausgegangen._price(
            {"@type": "Offer", "price": "12.00", "priceCurrency": "EUR"}
        ) == (12.0, "EUR", False)
        assert rausgegangen._price(None) == (None, None, None)

    def test_a_zero_price_is_unknown_rather_than_free(self):
        """0.00 is this site's placeholder, seen on clearly ticketed shows."""
        price, currency, is_free = rausgegangen._price(
            {"price": "0.00", "priceCurrency": "EUR"}
        )
        assert price is None, "must not claim a paid concert costs nothing"
        assert is_free is None, "free and unpriced are indistinguishable here"
        assert currency == "EUR"

    def test_the_category_is_threaded_from_the_listing(self):
        """A detail page's JSON-LD has no category; the listing that found it does."""
        listings = list(
            rausgegangen.parse(
                payload(
                    "rausgegangen",
                    "detail-event.html",
                    "detail",
                    category="concerts-and-music",
                )
            )
        )
        assert listings and listings[0].categories_raw == ["concerts-and-music"]


class TestThousandThings:
    @pytest.fixture
    def weekend(self) -> list[RawListing]:
        return list(
            thousandthings.parse(
                payload("thousandthings", "post-22888.json", "post", post_id=22888)
            )
        )

    @pytest.fixture
    def weekday(self) -> list[RawListing]:
        return list(
            thousandthings.parse(
                payload("thousandthings", "post-918422.json", "post", post_id=918422)
            )
        )

    def test_parses_both_posts(self, weekend, weekday):
        assert weekend and weekday

    def test_street_addresses_are_present(self, weekend, weekday):
        """Addresses are the whole reason to use this Source."""
        both = weekend + weekday
        with_street = [one for one in both if one.street and one.postcode]
        assert len(with_street) >= len(both) * 0.6

    def test_the_instagram_embed_lands_in_origin_url(self, weekend, weekday):
        """The embed is base64 inside a consent-plugin attribute, and it follows
        the `<ul>` rather than preceding it - so a naive in-order read either
        attributes it to the next heading or misses it entirely."""
        both = weekend + weekday
        linked = [one for one in both if one.origin_url]
        assert len(linked) >= len(both) * 0.5
        assert all(
            one.origin_url.startswith("https://www.instagram.com/p/") for one in linked
        )
        # One permalink belongs to one happening, not to the whole week.
        links = [one.origin_url for one in linked]
        assert len(set(links)) == len(links)

    def test_a_repurposed_post_yields_nothing(self):
        """The ids are stable, so a title check is what guards against reuse."""
        one = payload("thousandthings", "post-22888.json", "post", post_id=22888)
        post = json.loads(one.text)
        post["title"]["rendered"] = "Completely different article"
        one.body = json.dumps(post).encode()
        assert list(thousandthings.parse(one)) == []

    def test_a_single_day_dateline(self):
        assert thousandthings.parse_dateline("MO, 10.8.2026, 20:30 Uhr", 2026) == [
            (dt.date(2026, 8, 10), "20:30")
        ]

    def test_a_run_expands_from_a_year_less_first_date(self):
        """In "DO, 6.8 - SA, 8.8.2026" only the second date carries a year."""
        result = thousandthings.parse_dateline(
            "DO, 6.8 – SA, 8.8.2026 , ab 17 Uhr", 2026
        )
        assert [day for day, _ in result] == [
            dt.date(2026, 8, 6),
            dt.date(2026, 8, 7),
            dt.date(2026, 8, 8),
        ]

    def test_an_ampersand_gives_each_day_its_own_time(self):
        """The trap: Saturday must not inherit Friday's 17:00."""
        assert thousandthings.parse_dateline(
            "FR, 7.8.2026, ab 17 Uhr & SA, 8.8.2026, 10–23 Uhr", 2026
        ) == [(dt.date(2026, 8, 7), "17:00"), (dt.date(2026, 8, 8), "10:00")]

    def test_a_venue_with_a_street_and_postcode(self):
        venues = thousandthings.parse_venues(
            "Kino am Dach | Urban-Loritz-Platz 2, 1070"
        )
        assert venues[0]["name"] == "Kino am Dach"
        assert venues[0]["street"] == "Urban-Loritz-Platz 2"
        assert venues[0]["postcode"] == "1070"

    def test_two_venues_joined_by_an_ampersand(self):
        venues = thousandthings.parse_venues(
            "Drogerie | Pfauengasse 8, 1060 & Gleisneunzehn | Gunoldstraße 12, 1190"
        )
        assert len(venues) == 2
        assert venues[1]["postcode"] == "1190"

    def test_a_bare_name_and_postcode_is_not_echoed_as_a_street(self):
        """ "Schlingermarkt, 1210" has no street; echoing the name geocodes it
        to something arbitrary."""
        (venue,) = thousandthings.parse_venues("Schlingermarkt, 1210")
        assert venue["postcode"] == "1210"
        assert venue["street"] is None


def test_an_offset_without_its_colon_is_still_read():
    """rausgegangen emits `+0200`, which `fromisoformat` will not take as-is.

    Shared by every Source reading an ISO timestamp, which is why it lives in
    `sources/dates.py` rather than in the one that first hit it.
    """
    parsed = parse_iso_datetime("2026-08-10T09:00+0200")
    assert parsed is not None
    assert parsed.utcoffset() == dt.timedelta(hours=2)


def test_a_trailing_z_is_read_as_utc():
    """Two Sources write `Z` rather than `+00:00`."""
    parsed = parse_iso_datetime("2026-08-10T09:00:00Z")
    assert parsed is not None
    assert parsed.utcoffset() == dt.timedelta(0)


def test_only_a_trailing_z_is_rewritten():
    """Replacing every `Z` would corrupt a value that merely contains one."""
    assert parse_iso_datetime("2026-08-10T09:00:00+02:00Z") is not None
    # A timestamp-shaped prefix still parses; the point is that the Z in the
    # middle of a value is not turned into an offset.
    assert parse_iso_datetime("ZZZ") is None


def test_a_timestamp_with_no_seconds_is_still_read():
    """One Source truncates to the minute, which the 19-character retry misses."""
    parsed = parse_iso_datetime("2026-08-10T09:00")
    assert parsed is not None
    assert (parsed.hour, parsed.minute) == (9, 0)


def test_an_unreadable_timestamp_is_none_rather_than_a_guess():
    assert parse_iso_datetime("next Tuesday") is None
    assert parse_iso_datetime(None) is None
    assert parse_iso_datetime("") is None


class TestAustriaInfo:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(
            austria_info.parse(payload("austria_info", "query.json", "query", page=0))
        )

    def test_parses_hits(self, listings):
        assert listings

    def test_the_cross_locale_id_is_used(self, listings):
        """`objectID` differs per locale; `story_id` is the identity across them."""
        raw = json.loads(
            (FIXTURES / "austria_info" / "query.json").read_text(encoding="utf-8")
        )
        story_ids = {str(hit["story_id"]) for hit in raw["hits"] if hit.get("story_id")}
        assert {one.source_event_id for one in listings} <= story_ids

    def test_a_long_run_stays_a_span(self, listings):
        """An institution runs for months; that is a span, not a fabricated day."""
        assert [one for one in listings if one.occurrences[0].end]

    def test_dates_are_day_level(self, listings):
        """The index stores 00:00 throughout, so these are dates, not datetimes."""
        for one in listings:
            assert not isinstance(one.occurrences[0].start, dt.datetime)

    def test_the_language_facet_is_pinned(self):
        """Without it the index returns ~10 locale copies of everything."""
        assert austria_info.LOCALE == "en-gb"


class TestEventfinder:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(eventfinder.parse(payload("eventfinder", "search.html", page=1)))

    def test_parses_results(self, listings):
        assert len(listings) == 8

    def test_the_time_comes_from_the_url_slug(self, listings):
        """The slug encodes `am-YYYY-MM-DD-um-HH-MM`; card text is less reliable."""
        for one in listings:
            start = one.occurrences[0].start
            assert isinstance(start, dt.datetime)
            assert start.year >= 2026

    def test_ids_are_numeric(self, listings):
        assert all(one.source_event_id.isdigit() for one in listings)

    def test_a_carousel_card_is_not_a_result(self):
        """A card inside `.splide__slide` is a recommendation, not a search hit.

        Built here rather than read from the fixture, which was trimmed to
        results only - so the upstream version of this test could never fail.
        """
        html = """
        <div class="splide__slide">
          <div class="card"><div class="card-body">
            <h3 class="titel"><a href="/veranstaltung/111/x-am-2026-08-15-um-20-00-uhr/">Recommended</a></h3>
          </div></div>
        </div>
        <div class="card"><div class="card-body">
          <h3 class="titel"><a href="/veranstaltung/222/y-am-2026-08-15-um-21-00-uhr/">A real result</a></h3>
        </div></div>
        """
        found = list(
            eventfinder.parse(
                RawPayload(url="fixture://inline", body=html.encode(), meta={"page": 1})
            )
        )
        assert [one.title for one in found] == ["A real result"]

    def test_the_spec_says_cookies_are_needed(self):
        """The result set lives server-side against the session."""
        assert eventfinder.SPEC.use_cookies is True


class TestEventSpotter:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(event_spotter.parse(payload("event_spotter", "listing.html")))

    def test_parses_a_listing_with_no_detail_fetch(self, listings):
        assert listings, "the JSON-LD is on the listing page itself"

    def test_the_origin_venue_page_is_kept(self, listings):
        """`sameAs` is the venue page it was scraped from - an exact key."""
        assert any(one.origin_url for one in listings)

    def test_addresses_are_present(self, listings):
        assert any(one.street for one in listings)

    def test_a_junk_city_is_rejected(self, listings):
        """A few rows carry "0000" or a street name in `addressLocality`."""
        for one in listings:
            if one.city:
                assert not one.city.isdigit()


class TestSongkick:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(songkick.parse(payload("songkick", "listing.html")))

    def test_parses_listings(self, listings):
        assert listings

    def test_coordinates_are_present(self, listings):
        """`GeoCoordinates` is why these Listings need no geocoding."""
        assert any(one.lat is not None for one in listings)

    def test_the_date_filter_is_us_format(self):
        """`filters[minDate]` takes MM/DD/YYYY; an ISO date is silently ignored."""
        assert songkick._us_date(dt.date(2026, 8, 15)) == "08/15/2026"

    def test_the_filter_brackets_stay_literal(self):
        query = encode_query([("filters[minDate]", "08/15/2026")])
        assert "filters[minDate]=" in query
        assert "%5B" not in query

    def test_everything_here_is_music(self, listings):
        """The site has no per-entry category; it is all live music."""
        assert all(one.categories_raw == ["Konzert"] for one in listings)


class TestWarda:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(warda.parse(payload("warda", "detail.html", "detail")))

    def test_parses_a_detail_page(self, listings):
        assert len(listings) == 1

    def test_the_date_and_time_come_from_the_date_bar(self, listings):
        """ "Freitag 14. August 2026 Beginn: 23:00"."""
        start = listings[0].occurrences[0].start
        assert isinstance(start, dt.datetime)
        assert start.hour == 23

    def test_the_time_is_only_read_after_beginn(self):
        """The date's own digits must not be read as a clock time."""
        text = "Freitag 14. August 2026 Beginn: 23:00"
        assert dates.parse_date(text) == dt.date(2026, 8, 14)
        assert dates.parse_time(text.split("Beginn", 1)[1]) == dt.time(23, 0)

    def test_a_dotted_date_reads_as_a_time_if_not_split_off_first(self):
        """Which is why the time is only ever read from after "Beginn:".

        warda writes the long German form, where a whole-line scan happens not to
        collide - so this pins the reason the guard exists rather than a live
        failure of this Source.
        """
        assert dates.parse_time("14.08.2026 Beginn: 23:00") == dt.time(14, 8)
        assert dates.parse_time("Freitag 14. August 2026") is None

    def test_venue_and_postcode(self, listings):
        assert listings[0].venue_name
        assert listings[0].postcode == "1010"

    def test_a_listing_payload_yields_nothing(self):
        assert list(warda.parse(payload("warda", "listing.html", "listing"))) == []


class TestDateHelpers:
    """The German listing-page dates the HTML Sources have to read."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("15. August 2026", dt.date(2026, 8, 15)),
            ("Mi, 12. Aug 2026", dt.date(2026, 8, 12)),
            ("2026-08-15", dt.date(2026, 8, 15)),
            ("15.08.2026", dt.date(2026, 8, 15)),
            # Austrian, and the ones with umlauts.
            ("3. Jänner 2027", dt.date(2027, 1, 3)),
            ("21. März 2026", dt.date(2026, 3, 21)),
            ("1. Dezember 2026", dt.date(2026, 12, 1)),
            ("nothing here", None),
        ],
    )
    def test_parse_date(self, text, expected):
        assert dates.parse_date(text, reference=dt.date(2026, 8, 10)) == expected

    def test_a_missing_year_is_inferred_forward(self):
        """A month already past means the page means next year."""
        reference = dt.date(2026, 12, 20)
        assert dates.parse_date("5. Jänner", reference) == dt.date(2027, 1, 5)
        assert dates.parse_date("28. Dezember", reference) == dt.date(2026, 12, 28)

    def test_the_recent_past_stays_this_year(self):
        """Within a month back is a genuine recent date, not next year."""
        assert dates.parse_date("5. August", dt.date(2026, 8, 20)) == dt.date(
            2026, 8, 5
        )

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("20:30 Uhr", dt.time(20, 30)),
            ("ab 9.00", dt.time(9, 0)),
            ("no time", None),
            ("99:99", None),
        ],
    )
    def test_parse_time(self, text, expected):
        assert dates.parse_time(text) == expected

    def test_a_bare_date_stays_a_date(self):
        """That is what marks an Occurrence all-day rather than midnight."""
        result = dates.combine(dt.date(2026, 8, 15), None)
        assert isinstance(result, dt.date) and not isinstance(result, dt.datetime)

    def test_a_known_time_makes_a_datetime(self):
        assert isinstance(
            dates.combine(dt.date(2026, 8, 15), dt.time(20, 0)), dt.datetime
        )


# --- what holds for every Source ----------------------------------------


PARSEABLE = [
    (goabase, "partylist.json", "partylist", {}),
    (wien_info, "events-en.json", "events-en", {"locale": "en"}),
    (events_at, "listing-2026-08-15.html", "listing", {"date": "2026-08-15"}),
    (goodnight, "grouped-events.json", "grouped-events", {}),
    (rausgegangen, "detail-event.html", "detail", {}),
    (thousandthings, "post-22888.json", "post", {"post_id": 22888}),
    (austria_info, "query.json", "query", {"page": 0}),
    (eventfinder, "search.html", "listing", {"page": 1}),
    (event_spotter, "listing.html", "listing", {}),
    (songkick, "listing.html", "listing", {}),
    (warda, "detail.html", "detail", {}),
]


@pytest.mark.parametrize(
    ("source", "fixture", "kind", "meta"),
    PARSEABLE,
    ids=lambda v: getattr(v, "__name__", None),
)
def test_every_parse_yields_usable_listings(source, fixture, kind, meta):
    """Whatever a parse yields has to satisfy the `RawListing` contract.

    An end before its start is deliberately *not* asserted: a club night listed
    23:00-01:00 is exactly that, and rolling it forward is normalisation's job.
    """
    listings = list(source.parse(payload(source.SPEC.name, fixture, kind, **meta)))
    assert listings, f"{source.SPEC.name} yielded nothing from its own fixture"
    for one in listings:
        assert isinstance(one, RawListing)
        assert one.source_event_id.strip()
        assert one.occurrences
        # Normalisation quarantines a title-less record, so a parse yielding one
        # is a silent loss rather than an error - which is why it is caught here.
        assert one.title or one.title_alt, (
            f"{source.SPEC.name} yielded a Listing with no title"
        )


# Every Source ported so far. Named rather than counted, so that a Source
# dropping out of discovery - a renamed SPEC, an import that starts raising -
# fails here instead of quietly shrinking the catalogue. The suites above would
# not catch it: they import each module directly.
PORTED = {
    "austria_info",
    "event_spotter",
    "eventfinder",
    "events_at",
    "goabase",
    "goodnight",
    "rausgegangen",
    "songkick",
    "thousandthings",
    "warda",
    "wien_info",
}


def test_every_ported_source_is_discovered():
    assert set(discover()) == PORTED


def test_every_source_documents_its_traps():
    """So that nobody "simplifies" one away."""
    discovered = discover()
    assert discovered, "discovery found nothing, so the checks below are vacuous"
    for name, source in discovered.items():
        assert source.SPEC.notes, f"{name} has no SPEC.notes"
        assert source.SPEC.name == name
