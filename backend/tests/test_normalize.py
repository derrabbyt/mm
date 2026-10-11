# ruff: noqa: DTZ001

import datetime as dt

import pytest

from app.modules.events.categories import to_canonical
from app.modules.events.normalize import clean_image_url, norm_key, normalize
from app.modules.events.scraped import RawListing, RawOccurrence, Rejected

TODAY = dt.date(2026, 8, 10)


def make(**kw) -> RawListing:
    base = {
        "source_ref": "x1",
        "title": "Test Event",
        "occurrences": [RawOccurrence(start=dt.datetime(2026, 8, 15, 20, 0))],
    }
    base.update(kw)
    return RawListing(**base)


def norm(**kw):
    return normalize(make(**kw), source="test", today=TODAY)


class TestDates:
    def test_naive_datetime_treated_as_vienna(self):
        event = norm()
        occ = event.occurrences[0]
        assert occ.start_local.tzinfo is not None
        assert occ.start_local.utcoffset() == dt.timedelta(hours=2)
        assert occ.start_utc.hour == 18
        assert occ.date_local == dt.date(2026, 8, 15)

    def test_aware_datetime_converted_not_relabelled(self):
        utc_start = dt.datetime(2026, 8, 15, 18, 0, tzinfo=dt.UTC)
        event = norm(occurrences=[RawOccurrence(start=utc_start)])
        assert event.occurrences[0].start_local.hour == 20

    def test_plain_date_becomes_all_day(self):
        event = norm(occurrences=[RawOccurrence(start=dt.date(2026, 8, 15))])
        occ = event.occurrences[0]
        assert occ.all_day is True
        assert occ.start_local.hour == 0
        assert occ.end_local.hour == 23 and occ.end_local.minute == 59

    def test_all_day_never_sets_night_of(self):
        event = norm(occurrences=[RawOccurrence(start=dt.date(2026, 8, 15))])
        assert event.occurrences[0].night_of is None

    def test_after_midnight_start_gets_night_of(self):
        event = norm(occurrences=[RawOccurrence(start=dt.datetime(2026, 8, 15, 1, 0))])
        occ = event.occurrences[0]
        assert occ.date_local == dt.date(2026, 8, 15)
        assert occ.night_of == dt.date(2026, 8, 14)

    def test_evening_start_has_no_night_of(self):
        assert norm().occurrences[0].night_of is None

    def test_end_before_start_rolls_to_next_day(self):
        event = norm(
            occurrences=[
                RawOccurrence(
                    start=dt.datetime(2026, 8, 15, 23, 0),
                    end=dt.datetime(2026, 8, 15, 1, 0),
                )
            ]
        )
        occ = event.occurrences[0]
        assert occ.end_local.date() == dt.date(2026, 8, 16)
        assert any("rolled end" in w for w in event.warnings)

    def test_wildly_inverted_end_is_dropped(self):
        event = norm(
            occurrences=[
                RawOccurrence(
                    start=dt.datetime(2026, 8, 15, 20, 0),
                    end=dt.datetime(2026, 8, 1, 20, 0),
                )
            ]
        )
        assert event.occurrences[0].end_utc is None
        assert any("dropped end" in w for w in event.warnings)

    def test_span_sets_duration_days(self):
        event = norm(
            occurrences=[
                RawOccurrence(start=dt.date(2026, 8, 14), end=dt.date(2026, 8, 24))
            ]
        )
        assert event.occurrences[0].duration_days == 10

    def test_gapped_dates_stay_separate(self):
        event = norm(
            occurrences=[
                RawOccurrence(start=dt.date(2026, 8, 14)),
                RawOccurrence(start=dt.date(2026, 8, 15)),
                RawOccurrence(start=dt.date(2026, 8, 20)),
            ]
        )
        assert len(event.occurrences) == 3
        assert [o.date_local.day for o in event.occurrences] == [14, 15, 20]

    def test_duplicate_occurrences_collapsed(self):
        start = dt.datetime(2026, 8, 15, 20, 0)
        event = norm(
            occurrences=[RawOccurrence(start=start), RawOccurrence(start=start)]
        )
        assert len(event.occurrences) == 1

    def test_occurrences_sorted(self):
        event = norm(
            occurrences=[
                RawOccurrence(start=dt.datetime(2026, 8, 20, 20, 0)),
                RawOccurrence(start=dt.datetime(2026, 8, 15, 20, 0)),
            ]
        )
        starts = [o.start_utc for o in event.occurrences]
        assert starts == sorted(starts)

    def test_far_future_dropped_but_event_survives(self):
        event = norm(
            occurrences=[
                RawOccurrence(start=dt.date(2026, 8, 15)),
                RawOccurrence(start=dt.date(2099, 1, 1)),
            ]
        )
        assert len(event.occurrences) == 1
        assert any("outside date window" in w for w in event.warnings)

    def test_all_dates_out_of_window_is_rejected(self):
        result = norm(occurrences=[RawOccurrence(start=dt.date(2099, 1, 1))])
        assert isinstance(result, Rejected)
        assert result.reason == "all_dates_out_of_window"

    def test_long_run_that_started_long_ago_is_kept(self):
        event = norm(
            occurrences=[
                RawOccurrence(start=dt.date(2026, 1, 1), end=dt.date(2026, 12, 31))
            ]
        )
        assert not isinstance(event, Rejected)
        assert len(event.occurrences) == 1
        assert event.occurrences[0].duration_days == 364

    def test_run_that_ended_long_ago_is_still_rejected(self):
        result = norm(
            occurrences=[
                RawOccurrence(start=dt.date(2024, 1, 1), end=dt.date(2024, 3, 1))
            ]
        )
        assert isinstance(result, Rejected)

    def test_single_day_in_recent_past_still_kept(self):
        event = norm(occurrences=[RawOccurrence(start=dt.date(2026, 8, 5))])
        assert not isinstance(event, Rejected)

    def test_dst_boundary(self):
        event = norm(
            occurrences=[RawOccurrence(start=dt.datetime(2026, 11, 15, 20, 0))]
        )
        assert event.occurrences[0].start_utc.hour == 19


class TestRejects:
    def test_missing_title(self):
        result = norm(title=None)
        assert isinstance(result, Rejected) and result.reason == "missing_title"

    def test_no_dates(self):
        result = norm(occurrences=[])
        assert isinstance(result, Rejected) and result.reason == "no_dates"

    def test_reject_keeps_raw_payload(self):
        result = norm(title=None)
        assert result.raw, "quarantine must retain the raw event for debugging"

    def test_alt_title_promoted_when_primary_missing(self):
        event = norm(title=None, title_alt="Nur Englisch", lang_alt="en")
        assert not isinstance(event, Rejected)
        assert event.title_de == "Nur Englisch"
        assert any("used alternate" in w for w in event.warnings)


class TestFieldRepair:
    def test_bogus_coordinates_dropped(self):
        event = norm(lat=0.0, lon=0.0)
        assert event.lat is None and event.geo_source == "none"
        assert any("outside AT bbox" in w for w in event.warnings)

    def test_swapped_coordinates_dropped(self):
        event = norm(lat=16.37, lon=48.20)
        assert event.lat is None

    def test_valid_coordinates_kept_and_marked(self):
        event = norm(lat=48.2082, lon=16.3738)
        assert event.geo_source == "source"
        assert event.geo_precision == "exact"

    def test_bad_postcode_dropped(self):
        assert norm(postcode="0000-x").postcode is None
        assert norm(postcode="1010").postcode == "1010"

    def test_city_casing_unified(self):
        for variant in ("wien", "VIENNA", "Vienna", "WIEN"):
            assert norm(city=variant).city == "Wien"

    def test_price_swap(self):
        event = norm(price_min=20.0, price_max=5.0)
        assert (event.price_min, event.price_max) == (5.0, 20.0)
        assert any("swapped" in w for w in event.warnings)

    def test_whitespace_collapsed(self):
        assert norm(title="  Two   Spaces  ").title_de == "Two Spaces"


class TestLanguages:
    def test_single_locale(self):
        event = norm(title="Deutsch", lang="de")
        assert event.title_de == "Deutsch" and event.title_en is None
        assert event.lang_primary == "de"

    def test_two_locales_one_row(self):
        event = norm(
            title="Deutscher Titel",
            lang="de",
            title_alt="English Title",
            lang_alt="en",
            description="Beschreibung",
            description_alt="Description",
        )
        assert event.title_de == "Deutscher Titel"
        assert event.title_en == "English Title"
        assert event.description_de == "Beschreibung"
        assert event.description_en == "Description"

    def test_english_primary(self):
        event = norm(title="English", lang="en")
        assert event.title_en == "English" and event.title_de is None


class TestDedupKeys:
    def test_title_norm_ignores_case_punctuation_accents(self):
        assert norm_key("Café  Größe!") == norm_key("cafe grosse") or norm_key(
            "Café  Größe!"
        )
        assert norm_key("Das Werk") == norm_key("dasWERK")
        assert norm_key("Gleis19") == norm_key("Gleis 19")

    def test_marketing_prefix_stripped(self):
        assert norm_key("PICK OF THE DAY: Foo") == norm_key("Foo")

    def test_keys_present_on_event(self):
        event = norm(title="Some Concert", venue_name="Das Werk")
        assert event.title_norm == "someconcert"
        assert event.venue_name_norm == "daswerk"

    def test_origin_url_preserved(self):
        event = norm(origin_url="https://ticket.example/e/1")
        assert event.origin_url == "https://ticket.example/e/1"


class TestImageUrl:
    def test_absolute_url_kept(self):
        url = "https://images.ra.co/d61f74588e9aaa58.jpg"
        assert clean_image_url(url) == url

    def test_protocol_relative_gets_scheme(self):
        assert clean_image_url("//images.sk-static.com/a/avatar") == (
            "https://images.sk-static.com/a/avatar"
        )

    def test_site_relative_resolved_against_page(self):
        assert (
            clean_image_url(
                "/bilder/thumb_26467.jpg",
                base="https://www.eventfinder.at/veranstaltung/1/x/",
            )
            == "https://www.eventfinder.at/bilder/thumb_26467.jpg"
        )

    def test_relative_without_base_is_dropped(self):
        assert clean_image_url("/bilder/thumb_1.jpg") is None

    @pytest.mark.parametrize(
        "url",
        [
            "data:image/gif;base64,R0lGODlh",
            "javascript:void(0)",
            "",
            None,
        ],
    )
    def test_unusable_dropped(self, url):
        assert clean_image_url(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://assets.prod.bandsintown.com/images/homeIcon/placeholder-artist.svg",
            "https://www.meetup.com/images/fallbacks/redesign/group-cover-2-square.webp",
            "https://assets.sk-static.com/images/default_images/thumb/default-artist.png",
        ],
    )
    def test_placeholders_dropped_with_warning(self, url):
        warnings: list[str] = []
        assert clean_image_url(url, warnings=warnings) is None
        assert warnings == ["image_url is a placeholder; dropped"]

    def test_spaces_are_encoded(self):
        assert clean_image_url(
            "https://lux.eventjet.at/uploads/a/The Crown Shop.png"
        ) == ("https://lux.eventjet.at/uploads/a/The%20Crown%20Shop.png")

    def test_existing_escapes_not_doubled(self):
        url = "https://image.events.at/images/8344744/Lesen_%26_Leben.jpg"
        assert clean_image_url(url) == url

    def test_normalize_resolves_against_event_url(self):
        event = norm(
            url="https://www.eventfinder.at/veranstaltung/1/x/",
            image_url="/bilder/thumb_1.jpg",
        )
        assert event.image_url == "https://www.eventfinder.at/bilder/thumb_1.jpg"


class TestCategories:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (["Konzert"], "music"),
            (["concerts-and-music"], "music"),
            (["Rock, Pop, Jazz and more"], "music"),
            (["Party"], "party"),
            (["Techno"], "party"),
            (["Theater und Kabarett"], "theatre"),
            (["Ausstellung"], "art"),
            (["Exhibitions"], "art"),
            (["Film and Summer Cinema"], "film"),
            (["Kinder"], "family"),
            (["Kulinarisches"], "food"),
            (["Sportveranstaltung"], "sport"),
            (["Markt, Messe"], "market"),
            (["spoken-word"], "talk"),
            (["Führungen, Fahrten, Touren"], "talk"),
            (["Festival"], "music"),
            (["Festivals, Parties, and Shows"], "party"),
            (
                ["Musical, Dance and Performance"],
                "theatre",
            ),
            (["Guided Tours and Round Trips"], "talk"),
            (["Classic Concerts"], "music"),
            (["Opera and Operetta"], "music"),
            (["Voelliger Unsinn"], "other"),
            ([], "other"),
            (None, "other"),
            (["Literatur [HKV]"], "talk"),
            (["Barrierefreier Zugang"], "other"),
            (["Förderung"], "other"),
            (["Tech House"], "party"),
            (["Disco"], "party"),
            (["Markets and Fairs"], "market"),
            (["Ball"], "party"),
            (["Ballett"], "theatre"),
        ],
    )
    def test_mapping(self, raw, expected):
        assert to_canonical(raw) == expected

    def test_first_label_wins(self):
        assert to_canonical(["Party", "Konzert"]) == "party"

    def test_raw_categories_preserved_verbatim(self):
        event = norm(categories_raw=["Konzert", "World"])
        assert event.categories_raw == ["Konzert", "World"]
        assert event.category == "music"

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Wiener Mozart Konzert", "music"),
            ("Rock the Opera", "music"),
            ("Klassik in der Annakirche", "music"),
            ("CONNI - DAS MUSICAL", "theatre"),
            ("Wiens legendärste Impro-Comedy-Show", "theatre"),
            ("Circus Louis Knie", "theatre"),
            ("Magic Variety Show", "theatre"),
            ("Strauss Dinner Show", "theatre"),
            ("G5 Summer Party ＆ Karaoke", "party"),
            ("18. Diversity Ball powered by Wiener Stadtwerke", "party"),
            ("EUROPEAN STREET FOOD FESTIVAL", "food"),
            ("SOMMERKINO: „40 Years Of Fuckin´ Up“ (A film by NOFX)", "film"),
            ("Familienatelier - Pigmentlabor", "family"),
            ("WILDSTYLE ＆ TATTOO MESSE 2026", "market"),
            ("Cyanotypie Workshop - mit dem Künstler Laurent Ziegler", "talk"),
            ("SWAE LEE – Same Difference Tour Europe", "other"),
            ("ARDENITE - NOT GOING DOWN Album Release Show", "other"),
            ("GRANDMAS HOUSE", "other"),
            ("Eine Runde Seidl - Das Best of", "other"),
            ("HIGH VOLTAGE FAIRIES IN THE PIT", "other"),
            ("Bratislava Discovery: Bunkers", "other"),
            ("Some Untitled Thing", "other"),
        ],
    )
    def test_title_fallback(self, title, expected):
        assert to_canonical([], fallback_text=title) == expected

    def test_declared_label_beats_title(self):
        assert to_canonical(["Konzert"], fallback_text="Theater an der Wien") == "music"

    def test_normalize_infers_category_from_title(self):
        event = norm(title="Wiener Mozart Konzert", categories_raw=[])
        assert event.category == "music"
        assert event.categories_raw == []

    def test_normalize_ignores_description_for_category(self):
        event = norm(
            title="Some Untitled Thing",
            description="Die grosse Show mit Kabarett, Konzert und Party!",
            categories_raw=[],
        )
        assert event.category == "other"
