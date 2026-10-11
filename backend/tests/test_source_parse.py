import datetime as dt
import json
import pathlib

import pytest

from app.core.http import FetchError, Response, encode_query
from app.modules.events import region
from app.modules.events import sources as sources_package
from app.modules.events.normalize import normalize
from app.modules.events.scraped import NormalizedListing, RawListing, Rejected
from app.modules.events.sources import (
    austria_info,
    bandsintown,
    dates,
    discover,
    event_spotter,
    eventbrite,
    eventfinder,
    eventjet,
    events_at,
    fever,
    goabase,
    goodnight,
    jsonld,
    meetup,
    meinbezirk,
    ohschonhell,
    rausgegangen,
    resident_advisor,
    songkick,
    thousandthings,
    warda,
    wien_gv_at,
    wien_info,
    wien_ticket,
)
from app.modules.events.sources.dates import parse_iso_datetime
from app.modules.events.sources.spec import FetchContext, RawPayload

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
        assert all(one.source_ref for one in listings)
        assert all(one.occurrences for one in listings)

    def test_the_locale_comes_from_the_payload(self, listings):
        assert {one.lang for one in listings} == {"en"}

    def test_an_explicit_date_list_is_not_collapsed(self, listings):
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
        urls = [one.url for one in listings]
        assert len(urls) == len(set(urls))

    def test_the_queried_day_wins_over_the_card_time(self, listings):
        for one in listings:
            assert len(one.occurrences) == 1
            assert one.occurrences[0].start == self.DAY

    def test_title_and_venue_are_extracted(self, listings):
        assert all(one.title for one in listings)
        assert any(one.venue_name for one in listings)

    def test_without_the_queried_date_nothing_is_yielded(self):
        assert (
            list(
                events_at.parse(
                    payload("events_at", f"listing-{self.DAY.isoformat()}.html")
                )
            )
            == []
        )

    def test_the_url_builder_keeps_the_brackets_literal(self):
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
        assert all(one.source_ref for one in listings)

    def test_the_same_item_in_several_day_buckets_is_one_listing(self, listings):
        ids = [one.source_ref for one in listings]
        assert len(ids) == len(set(ids))

    def test_an_empty_list_address_does_not_read_as_missing(self, listings):
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
        price, currency, is_free = rausgegangen._price(
            {"price": "0.00", "priceCurrency": "EUR"}
        )
        assert price is None, "must not claim a paid concert costs nothing"
        assert is_free is None, "free and unpriced are indistinguishable here"
        assert currency == "EUR"

    def test_the_category_is_threaded_from_the_listing(self):
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
        both = weekend + weekday
        with_street = [one for one in both if one.street and one.postcode]
        assert len(with_street) >= len(both) * 0.6

    def test_the_instagram_embed_lands_in_origin_url(self, weekend, weekday):
        both = weekend + weekday
        linked = [one for one in both if one.origin_url]
        assert len(linked) >= len(both) * 0.5
        assert all(
            one.origin_url.startswith("https://www.instagram.com/p/") for one in linked
        )
        links = [one.origin_url for one in linked]
        assert len(set(links)) == len(links)

    def test_a_repurposed_post_yields_nothing(self):
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
        result = thousandthings.parse_dateline(
            "DO, 6.8 – SA, 8.8.2026 , ab 17 Uhr", 2026
        )
        assert [day for day, _ in result] == [
            dt.date(2026, 8, 6),
            dt.date(2026, 8, 7),
            dt.date(2026, 8, 8),
        ]

    def test_an_ampersand_gives_each_day_its_own_time(self):
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
        (venue,) = thousandthings.parse_venues("Schlingermarkt, 1210")
        assert venue["postcode"] == "1210"
        assert venue["street"] is None


def test_an_offset_without_its_colon_is_still_read():
    parsed = parse_iso_datetime("2026-08-10T09:00+0200")
    assert parsed is not None
    assert parsed.utcoffset() == dt.timedelta(hours=2)


def test_a_trailing_z_is_read_as_utc():
    parsed = parse_iso_datetime("2026-08-10T09:00:00Z")
    assert parsed is not None
    assert parsed.utcoffset() == dt.timedelta(0)


def test_only_a_trailing_z_is_rewritten():
    assert parse_iso_datetime("2026-08-10T09:00:00+02:00Z") is not None
    assert parse_iso_datetime("ZZZ") is None


def test_a_timestamp_with_no_seconds_is_still_read():
    parsed = parse_iso_datetime("2026-08-10T09:00")
    assert parsed is not None
    assert (parsed.hour, parsed.minute) == (9, 0)


def test_an_unreadable_timestamp_is_none_rather_than_a_guess():
    assert parse_iso_datetime("next Tuesday") is None
    assert parse_iso_datetime(None) is None
    assert parse_iso_datetime("") is None


def test_a_bare_iso_day_is_read_as_a_day_not_a_midnight():
    assert dates.parse_iso_when("2026-08-15") == dt.date(2026, 8, 15)
    parsed = dates.parse_iso_when("2026-08-15T20:00:00+02:00")
    assert isinstance(parsed, dt.datetime) and parsed.hour == 20


def test_a_when_it_cannot_read_falls_back_to_the_day():
    assert dates.parse_iso_when("2026-08-15Tnonsense") == dt.date(2026, 8, 15)
    assert dates.parse_iso_when("not a date") is None
    assert dates.parse_iso_when(None) is None


def test_an_exact_midnight_is_read_as_the_day_it_names():
    midnight = dt.datetime(2026, 7, 1, 0, 0, tzinfo=dates.TZ)
    assert dates.day_if_midnight(midnight) == dt.date(2026, 7, 1)
    evening = dt.datetime(2026, 7, 1, 19, 30, tzinfo=dates.TZ)
    assert dates.day_if_midnight(evening) == evening
    assert dates.day_if_midnight(dt.date(2026, 7, 1)) == dt.date(2026, 7, 1)
    assert dates.day_if_midnight(None) is None


def test_an_end_of_a_different_kind_than_its_start_is_dropped():
    day = dt.date(2026, 7, 1)
    moment = dt.datetime(2026, 7, 1, 23, 59, tzinfo=dates.TZ)
    assert dates.paired_end(day, moment) is None
    assert dates.paired_end(moment, day) is None
    assert dates.paired_end(day, dt.date(2026, 7, 2)) == dt.date(2026, 7, 2)
    assert dates.paired_end(moment, moment) == moment
    assert dates.paired_end(moment, None) is None


class TestAustriaInfo:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(
            austria_info.parse(payload("austria_info", "query.json", "query", page=0))
        )

    def test_parses_hits(self, listings):
        assert listings

    def test_the_cross_locale_id_is_used(self, listings):
        raw = json.loads(
            (FIXTURES / "austria_info" / "query.json").read_text(encoding="utf-8")
        )
        story_ids = {str(hit["story_id"]) for hit in raw["hits"] if hit.get("story_id")}
        assert {one.source_ref for one in listings} <= story_ids

    def test_a_long_run_stays_a_span(self, listings):
        assert [one for one in listings if one.occurrences[0].end]

    def test_dates_are_day_level(self, listings):
        for one in listings:
            assert not isinstance(one.occurrences[0].start, dt.datetime)

    def test_the_language_facet_is_pinned(self):
        assert austria_info.LOCALE == "en-gb"


class TestEventfinder:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(eventfinder.parse(payload("eventfinder", "search.html", page=1)))

    def test_parses_results(self, listings):
        assert len(listings) == 8

    def test_the_time_comes_from_the_url_slug(self, listings):
        for one in listings:
            start = one.occurrences[0].start
            assert isinstance(start, dt.datetime)
            assert start.year >= 2026

    def test_ids_are_numeric(self, listings):
        assert all(one.source_ref.isdigit() for one in listings)

    def test_a_carousel_card_is_not_a_result(self):
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
        assert eventfinder.SPEC.use_cookies is True


class TestEventSpotter:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(event_spotter.parse(payload("event_spotter", "listing.html")))

    def test_parses_a_listing_with_no_detail_fetch(self, listings):
        assert listings, "the JSON-LD is on the listing page itself"

    def test_the_origin_venue_page_is_kept(self, listings):
        assert any(one.origin_url for one in listings)

    def test_addresses_are_present(self, listings):
        assert any(one.street for one in listings)

    def test_a_junk_city_is_rejected(self, listings):
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
        assert any(one.lat is not None for one in listings)

    def test_the_date_filter_is_us_format(self):
        assert songkick._us_date(dt.date(2026, 8, 15)) == "08/15/2026"

    def test_the_filter_brackets_stay_literal(self):
        query = encode_query([("filters[minDate]", "08/15/2026")])
        assert "filters[minDate]=" in query
        assert "%5B" not in query

    def test_everything_here_is_music(self, listings):
        assert all(one.categories_raw == ["Konzert"] for one in listings)


class TestWarda:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(warda.parse(payload("warda", "detail.html", "detail")))

    def test_parses_a_detail_page(self, listings):
        assert len(listings) == 1

    def test_the_date_and_time_come_from_the_date_bar(self, listings):
        start = listings[0].occurrences[0].start
        assert isinstance(start, dt.datetime)
        assert start.hour == 23

    def test_the_time_is_only_read_after_beginn(self):
        text = "Freitag 14. August 2026 Beginn: 23:00"
        assert dates.parse_date(text) == dt.date(2026, 8, 14)
        assert dates.parse_time(text.split("Beginn", 1)[1]) == dt.time(23, 0)

    def test_a_dotted_date_reads_as_a_time_if_not_split_off_first(self):
        assert dates.parse_time("14.08.2026 Beginn: 23:00") == dt.time(14, 8)
        assert dates.parse_time("Freitag 14. August 2026") is None

    def test_venue_and_postcode(self, listings):
        assert listings[0].venue_name
        assert listings[0].postcode == "1010"

    def test_a_listing_payload_yields_nothing(self):
        assert list(warda.parse(payload("warda", "listing.html", "listing"))) == []


class TestDateHelpers:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("15. August 2026", dt.date(2026, 8, 15)),
            ("Mi, 12. Aug 2026", dt.date(2026, 8, 12)),
            ("2026-08-15", dt.date(2026, 8, 15)),
            ("15.08.2026", dt.date(2026, 8, 15)),
            ("3. Jänner 2027", dt.date(2027, 1, 3)),
            ("21. März 2026", dt.date(2026, 3, 21)),
            ("1. Dezember 2026", dt.date(2026, 12, 1)),
            ("nothing here", None),
        ],
    )
    def test_parse_date(self, text, expected):
        assert dates.parse_date(text, reference=dt.date(2026, 8, 10)) == expected

    def test_a_missing_year_is_inferred_forward(self):
        reference = dt.date(2026, 12, 20)
        assert dates.parse_date("5. Jänner", reference) == dt.date(2027, 1, 5)
        assert dates.parse_date("28. Dezember", reference) == dt.date(2026, 12, 28)

    def test_the_recent_past_stays_this_year(self):
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
        result = dates.combine(dt.date(2026, 8, 15), None)
        assert isinstance(result, dt.date) and not isinstance(result, dt.datetime)

    def test_a_known_time_makes_a_datetime(self):
        assert isinstance(
            dates.combine(dt.date(2026, 8, 15), dt.time(20, 0)), dt.datetime
        )


class TestMeetup:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(meetup.parse(payload("meetup", "listing.html")))

    def test_parses_events(self, listings):
        assert len(listings) >= 10

    def test_the_city_is_stripped_out_of_the_street(self, listings):
        for one in listings:
            if one.street:
                assert not one.street.casefold().endswith("vienna")
                assert not one.street.casefold().endswith("austria")

    def test_the_numeric_event_id_is_the_key(self, listings):
        assert any(one.source_ref.isdigit() for one in listings)

    @pytest.mark.parametrize(
        ("raw", "locality", "expected"),
        [
            ("Operngasse 7, Vienna", None, ("Operngasse 7", None, None)),
            (
                "Währinger Gürtel 1, 1180 Vienna",
                None,
                ("Währinger Gürtel 1", "1180", None),
            ),
            ("Alser Strasse 4, 1080", None, ("Alser Strasse 4", "1080", None)),
            ("Some Street 1", "Wien", ("Some Street 1", None, "Wien")),
            (None, "Wien", (None, None, "Wien")),
        ],
    )
    def test_the_one_free_text_field_splits_into_three(self, raw, locality, expected):
        assert meetup._split_address(raw, locality) == expected

    def test_no_image_is_stored(self, listings):
        assert all(one.image_url is None for one in listings)


class TestEventbrite:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(eventbrite.parse(payload("eventbrite", "listing.html")))

    def test_parses_events(self, listings):
        assert len(listings) >= 10

    def test_coordinates_arrive_with_the_listing(self, listings):
        placed = [one for one in listings if one.lat is not None]
        assert placed, "the JSON-LD carries geo"
        for one in placed:
            assert 46 < one.lat < 50 and 9 < one.lon < 18

    def test_a_full_address_arrives_too(self, listings):
        assert any(one.street and one.postcode for one in listings)

    def test_a_day_is_never_paired_with_a_clock_time(self, listings):
        for one in listings:
            occurrence = one.occurrences[0]
            if occurrence.end is not None:
                assert isinstance(occurrence.start, dt.datetime) == isinstance(
                    occurrence.end, dt.datetime
                )

    def test_ids_are_unique(self, listings):
        refs = [one.source_ref for one in listings]
        assert len(refs) == len(set(refs))

    def test_the_radius_search_leakage_is_dropped(self, listings):
        offered = {
            (jsonld.place(node)["city"] or "").casefold()
            for node in jsonld.events_in(
                (FIXTURES / "eventbrite" / "listing.html").read_text(encoding="utf-8")
            )
        }
        outside = {one for one in offered if one and not region.is_vienna(city=one)}
        assert outside, "the fixture no longer contains out-of-region leakage"

        kept = {(one.city or "").casefold() for one in listings}
        assert not (kept & outside)
        assert listings, "the filter must not remove everything"


def goabase_detail(fixture: str) -> RawPayload:
    one = payload("goabase", fixture, "party")
    party_id = (
        json.loads(one.text)["@id"].partition("#")[0].rstrip("/").rsplit("/", 1)[-1]
    )
    listed = json.loads((FIXTURES / "goabase" / "partylist.json").read_text())
    one.meta = {
        "party": next(
            item for item in listed["partylist"] if str(item["id"]) == party_id
        )
    }
    return one


class TestGoabaseDetail:
    def test_the_venue_and_street_are_read(self):
        (one,) = goabase.parse(goabase_detail("party-jsonld.json"))

        assert one.venue_name == "Flex Vienna"
        assert one.street == "Augartenbrücke 1"
        assert one.postcode == "1010"
        assert one.city == "Wien"

    def test_the_placeholder_venue_name_is_not_a_venue(self):
        (one,) = goabase.parse(goabase_detail("party-jsonld-placeholder.json"))

        assert one.venue_name is None

    def test_a_placeholder_name_does_not_cost_the_address(self):
        node = json.loads(
            (FIXTURES / "goabase" / "party-jsonld-placeholder.json").read_text()
        )
        node["location"]["address"]["streetAddress"] = "Donaukanal 1"

        where = goabase._venue(node)

        assert where["venue_name"] is None
        assert where["street"] == "Donaukanal 1"

    def test_the_centroid_coordinates_are_still_dropped(self):
        (one,) = goabase.parse(goabase_detail("party-jsonld.json"))

        assert one.lat is None
        assert one.lon is None

    def test_the_published_price_is_kept(self):
        (one,) = goabase.parse(goabase_detail("party-jsonld.json"))

        assert one.price_min == 20
        assert one.price_currency == "EUR"

    def test_the_party_list_still_yields_its_listings(self):
        assert (
            len(list(goabase.parse(payload("goabase", "partylist.json", "partylist"))))
            > 1
        )


class TestWienTicket:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(
            wien_ticket.parse(
                payload("wien_ticket", "show-mozart-vivaldi.html", "show")
            )
        )

    def test_one_show_page_yields_every_performance(self, listings):
        assert len(listings) == 31
        starts = {one.occurrences[0].start for one in listings}
        assert len(starts) == 31

    def test_each_performance_keeps_the_site_own_id(self, listings):
        assert all(one.source_ref.isdigit() for one in listings)
        assert len({one.source_ref for one in listings}) == len(listings)

    def test_performances_share_their_production(self, listings):
        assert len({one.title for one in listings}) == 1
        assert len({one.venue_name for one in listings}) == 1

    def test_address_and_coordinates_arrive_together(self, listings):
        one = listings[0]
        assert one.postcode == "1010"
        assert one.street
        assert one.lat == pytest.approx(48.2006)
        assert one.lon == pytest.approx(16.3725)

    def test_the_ticket_url_points_at_the_page_it_came_from(self, listings):
        assert all(one.ticket_url for one in listings)

    def test_a_start_page_payload_yields_nothing(self):
        assert (
            list(
                wien_ticket.parse(
                    payload("wien_ticket", "show-mozart-vivaldi.html", "listing")
                )
            )
            == []
        )


class TestResidentAdvisor:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(
            resident_advisor.parse(
                payload("resident_advisor", "graphql.json", "graphql", page=1)
            )
        )

    def test_parses_listings(self, listings):
        assert len(listings) >= 10

    def test_the_query_asks_for_event_listings(self):
        assert "eventListings" in resident_advisor.QUERY
        assert resident_advisor.VIENNA_AREA == 450

    def test_start_times_are_real_clock_times(self, listings):
        assert [
            one for one in listings if isinstance(one.occurrences[0].start, dt.datetime)
        ]

    def test_urls_are_absolute(self, listings):
        for one in listings:
            if one.url:
                assert one.url.startswith("http")

    def test_a_graphql_error_yields_nothing_rather_than_raising(self):
        broken = payload("resident_advisor", "graphql.json", "graphql")
        broken.body = json.dumps({"errors": [{"message": "boom"}]}).encode()
        assert list(resident_advisor.parse(broken)) == []

    def test_the_flyer_front_is_preferred_over_the_rest(self):
        event = {
            "images": [
                {"filename": "back.jpg", "type": "FLYERBACK"},
                {"filename": "front.jpg", "type": "FLYERFRONT"},
            ]
        }
        assert resident_advisor._image(event) == "front.jpg"
        assert resident_advisor._image({"images": [{"filename": "back.jpg"}]}) == (
            "back.jpg"
        )
        assert resident_advisor._image({}) is None


class TestEventjet:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(
            eventjet.parse(payload("eventjet", "detail-trio-lepschi.html", "detail"))
        )

    def test_a_detail_page_yields_its_event(self, listings):
        assert len(listings) == 1
        assert listings[0].title

    def test_a_midnight_stamp_means_the_day(self):
        (one,) = eventjet.parse(payload("eventjet", "detail-midnight.html", "detail"))
        start = one.occurrences[0].start
        assert isinstance(start, dt.date) and not isinstance(start, dt.datetime)

    def test_a_timed_event_keeps_its_clock_time(self, listings):
        start = listings[0].occurrences[0].start
        assert isinstance(start, dt.datetime)
        assert (start.hour, start.minute) == (19, 0)

    def test_the_picture_comes_from_the_social_preview(self, listings):
        image = listings[0].image_url
        assert image and image.startswith("https://")
        assert "ticketjet-cover-images" in image

    def test_the_source_ref_survives_a_slug_hosting_a_series(self, listings):
        assert listings[0].source_ref.startswith("trio-lepschi-")

    def test_the_venue_is_read_out_of_the_markup(self):
        (one,) = eventjet.parse(
            payload("eventjet", "detail-vienna-venue.html", "detail")
        )

        assert one.venue_name == "Kapuzinerkirche"
        assert one.street == "Neuer Markt"
        assert one.postcode == "1010"
        assert one.city == "Wien"

    def test_the_coordinates_are_read_from_a_misspelt_meta_tag(self):
        (one,) = eventjet.parse(
            payload("eventjet", "detail-vienna-venue.html", "detail")
        )

        assert one.lat == pytest.approx(48.206093)
        assert one.lon == pytest.approx(16.370538)

    def test_an_event_outside_vienna_is_dropped(self):
        assert (
            list(
                eventjet.parse(
                    payload("eventjet", "detail-out-of-region.html", "detail")
                )
            )
            == []
        )

    def test_a_listing_payload_yields_nothing(self):
        assert (
            list(
                eventjet.parse(
                    payload("eventjet", "detail-trio-lepschi.html", "listing")
                )
            )
            == []
        )


class TestBandsintown:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(bandsintown.parse(payload("bandsintown", "page.json", "page")))

    def test_parses_a_json_page(self, listings):
        assert listings

    def test_out_of_region_results_are_dropped(self, listings):
        offered = json.loads(
            (FIXTURES / "bandsintown" / "page.json").read_text(encoding="utf-8")
        )["events"]
        assert 0 < len(listings) < len(offered)

    @pytest.mark.parametrize(
        "spelling",
        ["Wien, Austria", "Vienna, Austria", "WIEN, Austria", " vienna , austria "],
    )
    def test_all_three_city_spellings_are_accepted(self, spelling):
        assert bandsintown.VIENNA_TEXT.match(spelling)

    @pytest.mark.parametrize(
        "outside",
        ["Bratislava, Slovakia", "Tulln, Austria", "Wiener Neustadt, Austria"],
    )
    def test_a_neighbouring_town_is_not_vienna(self, outside):
        assert not bandsintown.VIENNA_TEXT.match(outside)

    def test_the_landing_page_yields_nothing(self):
        assert (
            list(bandsintown.parse(payload("bandsintown", "landing.html", "landing")))
            == []
        )

    def test_the_landing_state_still_parses(self):
        html = payload("bandsintown", "landing.html", "landing").text
        state = bandsintown.extract_state(html)
        assert state is not None
        upcoming = (state.get("initialState") or {}).get("upcomingEvents") or {}
        assert "urlForNextPageOfEvents" in upcoming

    def test_the_chain_starts_at_page_one_as_a_city_page(self):
        assert "page=1" in bandsintown.first_url()
        assert "page_type=cityPage" in bandsintown.first_url()


class TestFever:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(
            fever.parse(
                payload("fever", "plan-urzeit-chroniken.html", "plan", plan_id="640141")
            )
        )

    def test_one_plan_is_one_listing(self, listings):
        assert len(listings) == 1
        assert listings[0].source_ref == "640141"

    def test_the_plan_arrives_placed_and_priced(self, listings):
        one = listings[0]
        assert one.lat == pytest.approx(48.2403397)
        assert one.lon == pytest.approx(16.4100505)
        assert one.venue_name
        assert one.price_min and one.price_currency == "EUR"

    def test_plan_ids_come_from_the_config_and_the_links(self):
        html = payload("fever", "city-wien.html", "city").text
        ids = fever.plan_ids(html)
        assert len(ids) > 20
        assert len(ids) == len(set(ids))
        assert all(one.isdigit() for one in ids)

    def test_the_city_page_yields_nothing(self):
        assert list(fever.parse(payload("fever", "city-wien.html", "city"))) == []


class TestMeinBezirk:
    FIXTURE = "listing-2026-08-15.html"
    DAY = dt.date(2026, 8, 15)

    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(
            meinbezirk.parse(
                payload("meinbezirk", self.FIXTURE, date=self.DAY.isoformat())
            )
        )

    def test_parses_the_cards(self, listings):
        assert len(listings) == 8, "the fixture was trimmed to 8 article cards"

    def test_the_double_link_per_card_is_not_two_listings(self, listings):
        refs = [one.source_ref for one in listings]
        assert len(refs) == len(set(refs))

    def test_the_queried_day_is_authoritative(self, listings):
        for one in listings:
            start = one.occurrences[0].start
            actual = start.date() if isinstance(start, dt.datetime) else start
            assert actual == self.DAY

    def test_the_category_comes_from_the_url_path(self, listings):
        assert any(one.categories_raw for one in listings)

    def test_every_card_names_its_happening_and_some_name_a_venue(self, listings):
        assert all(one.title for one in listings)
        assert any(one.venue_name for one in listings)

    def test_wien_is_not_a_venue(self, listings):
        for one in listings:
            if one.venue_name:
                assert one.venue_name.lower() not in {"wien", "vienna"}

    def test_the_lazy_load_placeholder_is_not_an_image(self, listings):
        for one in listings:
            if one.image_url:
                assert not one.image_url.startswith("data:")

    def test_a_payload_with_no_day_yields_nothing(self):
        assert list(meinbezirk.parse(payload("meinbezirk", self.FIXTURE))) == []


class TestWienGvAt:
    @pytest.fixture
    def index_meta(self) -> dict:
        return json.loads((FIXTURES / "wien_gv_at" / "detail-meta.json").read_text())

    @pytest.fixture
    def listings(self, index_meta) -> list[RawListing]:
        return list(
            wien_gv_at.parse(
                payload("wien_gv_at", "detail-subevent.html", "detail", **index_meta)
            )
        )

    def test_a_detail_page_is_one_listing(self, listings):
        assert len(listings) == 1

    def test_the_subevent_list_is_expanded(self, listings):
        assert len(listings[0].occurrences) > 1

    def test_a_midnight_subevent_is_all_day(self, listings):
        for occurrence in listings[0].occurrences:
            if isinstance(occurrence.start, dt.datetime):
                assert (occurrence.start.hour, occurrence.start.minute) != (0, 0)

    def test_a_day_long_span_keeps_both_of_its_ends(self):
        (occurrence,) = wien_gv_at._occurrences(
            {
                "startDate": "2026-09-16T00:00:00+02:00",
                "endDate": "2026-09-20T23:59:00+02:00",
            }
        )
        assert occurrence.start == dt.date(2026, 9, 16)
        assert occurrence.end == dt.date(2026, 9, 20)

    def test_coordinates_come_from_the_index(self, listings, index_meta):
        assert listings[0].lat == pytest.approx(index_meta["lat"])
        assert listings[0].lon == pytest.approx(index_meta["lon"])

    def test_the_address_is_read_from_the_city_own_shape(self, listings):
        assert listings[0].street == "Mechelgasse 2"
        assert listings[0].postcode == "1030"
        assert listings[0].venue_name == "Botanischer Garten"

    def test_the_tags_are_kept(self, listings):
        assert listings[0].categories_raw

    def test_the_index_payload_yields_nothing(self):
        assert (
            list(wien_gv_at.parse(payload("wien_gv_at", "index.json", "index"))) == []
        )

    def test_an_index_with_a_raw_newline_inside_a_string_is_still_read(self):
        body = (
            b'{"type":"FeatureCollection","features":[{"properties":'
            b'{"description":"BACH BWV 1050\nSCHOENBERG Op. 38"}}]}'
        )
        response = Response(url=wien_gv_at.INDEX, status=200, body=body)
        assert len(response.json()["features"]) == 1

    def test_an_index_that_is_actually_broken_still_raises(self):
        response = Response(url=wien_gv_at.INDEX, status=200, body=b'{"features": [')
        with pytest.raises(ValueError):
            response.json()


class _FlakyHttp:
    def __init__(self, index_body: bytes, fail_on: set[str]) -> None:
        self.index_body = index_body
        self.fail_on = fail_on
        self.request_count = 0
        self.bytes_fetched = 0
        self.status_counts: dict[int, int] = {}

    def get(self, url, headers=None, method="GET", data=None) -> Response:
        self.request_count += 1
        if url in self.fail_on:
            raise FetchError(f"HTTP 404 for {url}")
        body = self.index_body if url.endswith(".json") else b"<html></html>"
        return Response(url=url, status=200, body=body)


class TestDeadDetailUrls:
    @staticmethod
    def _context(http) -> FetchContext:
        return FetchContext(
            http=http, date_from=dt.date(2026, 8, 14), date_to=dt.date(2026, 8, 16)
        )

    def test_one_dead_url_does_not_end_the_crawl(self):
        index = (FIXTURES / "wien_gv_at" / "index.json").read_bytes()
        urls = [
            feature["properties"]["url"]
            for feature in json.loads(index)["features"]
            if (feature.get("properties") or {}).get("url")
        ]
        assert len(urls) >= 3

        http = _FlakyHttp(index, fail_on={urls[0]})
        payloads = list(wien_gv_at.fetch(self._context(http)))

        details = [one for one in payloads if one.kind == "detail"]
        assert len(details) == len(urls) - 1
        assert any(one.kind == "index" for one in payloads)

    def test_a_failed_index_is_a_failed_source(self):
        http = _FlakyHttp(b"{}", fail_on={wien_gv_at.INDEX})
        with pytest.raises(FetchError):
            list(wien_gv_at.fetch(self._context(http)))


class TestOhschonhell:
    @pytest.fixture
    def listings(self) -> list[RawListing]:
        return list(ohschonhell.parse(payload("ohschonhell", "month.json", "month")))

    def test_parses_a_month(self, listings):
        assert listings

    def test_the_two_buckets_are_not_two_listings(self, listings):
        refs = [one.source_ref for one in listings]
        assert len(refs) == len(set(refs))

    def test_the_lineup_html_is_flattened(self, listings):
        for one in listings:
            if not one.description:
                continue
            lowered = one.description.lower()
            for tag in ("<br", "</", "<a ", "<iframe", "<div", "<p>", "<span"):
                assert tag not in lowered, f"unstripped {tag!r} in {one.title!r}"

    def test_the_city_parameter_is_mandatory(self):
        assert ohschonhell.CITY == "wien"

    def test_the_month_window_covers_the_whole_range(self):
        assert ohschonhell.months_in(dt.date(2026, 11, 20), dt.date(2027, 2, 3)) == [
            "2026-11",
            "2026-12",
            "2027-01",
            "2027-02",
        ]

    def test_a_zero_coordinate_is_not_a_position(self, listings):
        for one in listings:
            assert one.lat != 0
            assert one.lon != 0


class TestRegionFilter:
    def test_coordinates_are_trusted_first(self):
        assert region.is_vienna(48.21, 16.37)
        assert not region.is_vienna(48.14, 17.11)
        assert not region.is_vienna(47.07, 15.44)

    def test_the_postcode_answers_when_there_are_no_coordinates(self):
        assert region.is_vienna(postcode="1010")
        assert region.is_vienna(postcode="1230")
        assert not region.is_vienna(postcode="8010")
        assert not region.is_vienna(postcode="2340")

    def test_the_city_name_is_the_last_resort(self):
        assert region.is_vienna(city="Wien")
        assert region.is_vienna(city="vienna")
        assert not region.is_vienna(city="Bratislava")

    def test_no_signal_at_all_keeps_the_listing(self):
        assert region.is_vienna()
        assert region.is_vienna(postcode="", city="")


_WIEN_GV_AT_INDEX_META = json.loads(
    (FIXTURES / "wien_gv_at" / "detail-meta.json").read_text()
)

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
    (meetup, "listing.html", "listing", {}),
    (eventbrite, "listing.html", "listing", {}),
    (wien_ticket, "show-mozart-vivaldi.html", "show", {}),
    (resident_advisor, "graphql.json", "graphql", {"page": 1}),
    (eventjet, "detail-vienna-venue.html", "detail", {}),
    (bandsintown, "page.json", "page", {}),
    (fever, "plan-urzeit-chroniken.html", "plan", {"plan_id": "640141"}),
    (meinbezirk, "listing-2026-08-15.html", "listing", {"date": "2026-08-15"}),
    (wien_gv_at, "detail-subevent.html", "detail", _WIEN_GV_AT_INDEX_META),
    (ohschonhell, "month.json", "month", {}),
]


@pytest.mark.parametrize(
    ("source", "fixture", "kind", "meta"),
    PARSEABLE,
    ids=lambda v: getattr(v, "__name__", None),
)
def test_every_parse_yields_usable_listings(source, fixture, kind, meta):
    listings = list(source.parse(payload(source.SPEC.name, fixture, kind, **meta)))
    assert listings, f"{source.SPEC.name} yielded nothing from its own fixture"
    for one in listings:
        assert isinstance(one, RawListing)
        assert one.source_ref.strip()
        assert one.occurrences
        assert one.title or one.title_alt, (
            f"{source.SPEC.name} yielded a Listing with no title"
        )


PORTED = {
    "austria_info",
    "bandsintown",
    "event_spotter",
    "eventbrite",
    "eventfinder",
    "events_at",
    "eventjet",
    "fever",
    "goabase",
    "goodnight",
    "meetup",
    "meinbezirk",
    "ohschonhell",
    "rausgegangen",
    "resident_advisor",
    "songkick",
    "thousandthings",
    "warda",
    "wien_gv_at",
    "wien_info",
    "wien_ticket",
}


def test_every_ported_source_is_discovered():
    assert set(discover()) == PORTED


@pytest.mark.parametrize(
    ("source", "fixture", "kind", "meta"),
    PARSEABLE,
    ids=lambda v: getattr(v, "__name__", None),
)
def test_every_parse_yields_listings_normalisation_accepts(source, fixture, kind, meta):
    listings = list(source.parse(payload(source.SPEC.name, fixture, kind, **meta)))
    starts = [one.occurrences[0].start for one in listings if one.occurrences]
    days = [one.date() if isinstance(one, dt.datetime) else one for one in starts]
    today = min(days)

    results = [normalize(one, source.SPEC.name, today=today) for one in listings]
    refused = [
        one
        for one in results
        if isinstance(one, Rejected) and one.reason != "all_dates_out_of_window"
    ]
    assert not refused, f"{source.SPEC.name}: normalisation refused " + ", ".join(
        sorted({one.reason for one in refused})
    )
    assert [one for one in results if isinstance(one, NormalizedListing)], (
        f"{source.SPEC.name} contributed no Listing to the catalogue"
    )


SOURCE_FILES = sorted(
    pathlib.Path(sources_package.__file__).parent.glob("*.py")  # type: ignore[arg-type]
)

FORBIDDEN_IMPORTS = ("sqlalchemy", "app.core.database", "..models", "..repository")


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
def test_no_source_reaches_the_database(path: pathlib.Path):
    text = path.read_text()
    reached = [
        one
        for one in FORBIDDEN_IMPORTS
        if f"import {one}" in text or f"from {one}" in text
    ]
    assert not reached, f"{path.name} imports {', '.join(reached)}"


def test_every_source_documents_its_traps():
    discovered = discover()
    assert discovered, "discovery found nothing, so the checks below are vacuous"
    for name, source in discovered.items():
        assert source.SPEC.notes, f"{name} has no SPEC.notes"
        assert source.SPEC.name == name
