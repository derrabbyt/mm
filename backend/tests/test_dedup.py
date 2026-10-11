import datetime as dt

import pytest

from app.core.contracts import Position
from app.modules.events.dedup import (
    Candidate,
    Content,
    compare,
    completeness_score,
    gap_fill,
    group_day,
    km_apart,
    tokens,
)
from app.modules.events.normalize import norm_key

DAY = dt.date(2026, 8, 11)
VIENNA = Position(latitude=48.2082, longitude=16.3738)


def at(lat: float | None, lon: float | None) -> Position | None:
    return None if lat is None or lon is None else Position(latitude=lat, longitude=lon)


def c(
    listing_id: int,
    source: str,
    title: str,
    venue_name: str | None = None,
    *,
    lat: float | None = None,
    lon: float | None = None,
    **kw,
) -> Candidate:
    return Candidate(
        listing_id=listing_id,
        source=source,
        date_local=kw.pop("date_local", DAY),
        title=title,
        title_norm=norm_key(title) or "",
        venue_name=venue_name,
        position=at(lat, lon),
        **kw,
    )


class TestTokens:
    def test_drops_stopwords_and_short_tokens(self):
        assert tokens("Live at the Arena Wien") == {"arena"}

    def test_drops_years(self):
        assert tokens("Kultursommer Wien 2026") == {"kultursommer"}
        assert "2026" not in tokens("Afrika Tage Wien 2026")

    def test_keeps_numeric_identity(self):
        assert "72" not in tokens("B72")
        assert "novex" in tokens("Krimi Escape Theater Wien NOVEX-3")


SAME_PLACE = [
    ("Karlskirche", "Karlskirche, Wien", "St. Charles' Church", 48.1983, 16.3719),
    ("Staatsoper", "Wiener Staatsoper", "Vienna State Opera", 48.2028, 16.3690),
    ("Rathaus", "Rathaus Wien", "Vienna City Hall", 48.2108, 16.3573),
    (
        "Donauinsel",
        "Donauinsel (Floridsdorfer Brücke)",
        "Danube Island",
        48.2290,
        16.4110,
    ),
    (
        "Gasometer",
        "Bank Austria Halle",
        "Raiffeisen Halle im Gasometer",
        48.1858,
        16.4200,
    ),
    (
        "Hauptbücherei",
        "Hauptbücherei am Gürtel",
        "Dach der Hauptbücherei Wien",
        48.2016,
        16.3369,
    ),
    ("Strandbar", "Strandbar Hermann", "Strandbar Herrmann", 48.2119, 16.3849),
]


class TestVenueNamesDecide:
    @pytest.mark.parametrize("label,va,vb,lat,lon", SAME_PLACE)
    def test_names_disagree_but_coordinates_agree(self, label, va, vb, lat, lon):
        verdict = compare(
            c(1, "wien_info", "Deep Purple", va, lat=lat, lon=lon),
            c(2, "events_at", "Deep Purple", vb, lat=lat, lon=lon),
        )
        assert verdict.same, label
        assert verdict.venue_rule == "coords"

    def test_two_churches_in_the_same_district_stay_apart(self):
        distance = km_apart(at(48.1983, 16.3719), at(48.2085, 16.3735))
        assert 1.0 < distance < 1.3
        verdict = compare(
            c(
                1,
                "wien_info",
                "Antonio Vivaldi: Four Seasons",
                "St. Charles' Church",
                lat=48.1983,
                lon=16.3719,
            ),
            c(
                2,
                "eventfinder",
                "Antonio Vivaldi: Four Seasons",
                "Stephansdom",
                lat=48.2085,
                lon=16.3735,
            ),
        )
        assert not verdict.same
        assert verdict.reason.startswith("venue-coords-conflict")

    def test_geocoder_scatter_for_one_place_is_tolerated(self):
        verdict = compare(
            c(1, "a", "Deep Purple", "Wiener Stadthalle", lat=48.2020, lon=16.3350),
            c(2, "b", "Deep Purple", "Stadthalle Wien", lat=48.2028, lon=16.3372),
        )
        assert verdict.same

    def test_agreeing_names_survive_a_bad_geocode_within_the_veto(self):
        verdict = compare(
            c(1, "a", "Comedy Show", "The Comedy Pub", lat=48.1990, lon=16.3560),
            c(2, "b", "Comedy Show", "The Comedy Pub, Wien", lat=48.2050, lon=16.3640),
        )
        assert verdict.same
        assert verdict.venue_rule == "containment"

    def test_conflicting_city_beats_agreeing_names(self):
        verdict = compare(
            c(
                1,
                "a",
                "Show",
                "Wiener Stadthalle",
                city="Wien",
                lat=48.2020,
                lon=16.3350,
            ),
            c(2, "b", "Show", "Stadthalle Wels", city="Wels", lat=48.1575, lon=14.0289),
        )
        assert not verdict.same
        assert verdict.reason == "city-conflict"

    def test_names_still_used_when_coordinates_are_missing(self):
        verdict = compare(
            c(1, "a", "Deep Purple", "Szene"),
            c(2, "b", "Deep Purple", "Szene Wien"),
        )
        assert verdict.same
        assert verdict.venue_rule == "containment"


class TestDistanceVeto:
    GRAZ = (48.1940, 16.3780)
    WIEN = (48.2434, 16.4481)

    def _orpheum_pair(self) -> tuple[Candidate, Candidate]:
        return (
            c(
                1,
                "events_at",
                "DanzerMania - 80 Jahre Georg Danzer",
                "Orpheum Graz",
                city="Wien",
                lat=self.GRAZ[0],
                lon=self.GRAZ[1],
            ),
            c(
                2,
                "events_at",
                "Danzer Band & Freunde - DanzerMania - 80 Jahre Georg Danzer",
                "Orpheum Wien",
                city="Wien",
                lat=self.WIEN[0],
                lon=self.WIEN[1],
            ),
        )

    def test_agreeing_names_lose_to_a_seven_kilometre_gap(self):
        assert 7.5 < km_apart(at(*self.GRAZ), at(*self.WIEN)) < 8.0
        a, b = self._orpheum_pair()
        verdict = compare(a, b)
        assert not verdict.same
        assert verdict.title_rule == "subset"
        assert verdict.reason.startswith("venue-too-far")

    def test_two_clubs_the_geocoder_confused_are_cut_where_they_differ(self):
        assert 6.4 < km_apart(at(48.2030, 16.3400), at(48.1690, 16.4120)) < 6.8
        verdict = compare(
            c(1, "a", "Gürtel Nightwalk", "B72", lat=48.2030, lon=16.3400),
            c(2, "b", "Gürtel Nightwalk", "Szene Wien", lat=48.1690, lon=16.4120),
        )
        assert not verdict.same
        assert verdict.reason.startswith("venue-coords-conflict")

    def test_the_veto_does_not_fire_when_only_one_side_is_positioned(self):
        verdict = compare(
            c(1, "a", "Deep Purple", "Szene", lat=48.1660, lon=16.4180),
            c(2, "b", "Deep Purple", "Szene Wien"),
        )
        assert verdict.same
        assert verdict.venue_rule == "containment"

    def test_an_unpositioned_listing_still_matches_on_names(self):
        verdict = compare(
            c(1, "wien_gv_at", "Afrika Tage", "Donauinsel"),
            c(
                2,
                "eventfinder",
                "Afrika Tage Wien",
                "Donauinsel (Floridsdorfer Brücke)",
            ),
        )
        assert verdict.same

    def test_the_threshold_is_five_kilometres(self):
        near = c(1, "a", "Deep Purple", "Szene", lat=48.2440, lon=16.3700)
        far = c(2, "a", "Deep Purple", "Szene", lat=48.2460, lon=16.3700)
        anchor = c(3, "b", "Deep Purple", "Szene Wien", lat=48.2000, lon=16.3700)
        assert km_apart(at(48.2000, 16.3700), near.position) < 5.0
        assert km_apart(at(48.2000, 16.3700), far.position) > 5.0
        assert compare(near, anchor).same
        assert not compare(far, anchor).same

    @pytest.mark.parametrize(
        "label,va,vb,pos_a,pos_b",
        [
            (
                "Arena",
                "Arena - Große Halle",
                "Arena Wien",
                (48.1858, 16.4200),
                (48.2400, 16.3820),
            ),
            (
                "Karlskirche",
                "Karlskirche, Wien",
                "Karlskirche - Pfarre St. Karl Borromäus",
                (48.1982, 16.3719),
                (48.1728, 16.2747),
            ),
        ],
    )
    def test_the_accepted_cost_correct_merges_the_veto_loses(
        self, label, va, vb, pos_a, pos_b
    ):
        verdict = compare(
            c(1, "a", "VIVALDI", va, lat=pos_a[0], lon=pos_a[1]),
            c(2, "b", "VIVALDI", vb, lat=pos_b[0], lon=pos_b[1]),
        )
        assert not verdict.same, label
        assert verdict.reason.startswith("venue-too-far"), label


class TestCompare:
    def test_different_days_never_match(self):
        a = c(1, "wien_info", "Deep Purple", "Wiener Stadthalle")
        b = c(
            2,
            "wien_ticket",
            "Deep Purple",
            "Wiener Stadthalle",
            date_local=dt.date(2026, 8, 12),
        )
        assert compare(a, b).reason == "different-day"

    @pytest.mark.parametrize(
        "ta,tb,va,vb",
        [
            (
                "Carpenter Brut",
                "CARPENTER BRUT (fra) + HEALTH (us) *OPEN AIR*",
                "Arena Wien Open Air",
                "Arena Wien",
            ),
            (
                "Carpenter Brut",
                "Carpenter Brut @ Arena Wien",
                "Arena Wien",
                "Arena Wien",
            ),
            ("Broken By The Scream", "broken by the scream", "Szene", "Szene Wien"),
            ("ALBERT & TINA", "ALBERT&TINA", "Albertina", "Albertina"),
            (
                "Wiener Mozart Konzert",
                "Wiener Mozart Konzerte",
                "Ehrbar Saal",
                "Ehrbar Saal",
            ),
            (
                "Beatles an Bord",
                "BEATLES AN BOARD",
                "Tschauner Bühne",
                "Tschauner Bühne",
            ),
            (
                "WAR ON WOMEN (USA) / ADAM BOMB (USA)",
                "MJ Presents: War on Women & Adam Bomb",
                "Chelsea",
                "Chelsea",
            ),
        ],
    )
    def test_merges(self, ta, tb, va, vb):
        assert compare(c(1, "a", ta, va), c(2, "b", tb, vb)).same

    @pytest.mark.parametrize(
        "ta,tb,va,vb,reason",
        [
            (
                "Kultursommer Wien",
                "Waltzing in the Clouds - Kultursommer Wien",
                None,
                None,
                "umbrella-weak-venue",
            ),
            (
                "Theater im Park 2026",
                "Max Müller @ Theater im Park",
                "Theater im Park am Belvedere",
                "Theater Im Park",
                "umbrella-venue-season",
            ),
            (
                "Antonio Vivaldi: Four Seasons",
                "VIVALDI",
                "St. Stephen's Cathedral",
                "Karlskirche, Wien",
                "venue-conflict",
            ),
            ("Gürtel Nightwalk", "Gürtel Nightwalk", "B72", "Rhiz", "venue-conflict"),
            (
                "Gürtel Nightwalk",
                "Gürtel Nightwalk",
                "B72",
                "Chelsea",
                "venue-conflict",
            ),
            (
                "Sport.Platz Wien",
                "Sport.Platz Wien",
                "Rathausplatz",
                "Arenbergpark",
                "venue-conflict",
            ),
            (
                "Made in Austria. Furniture Design",
                "Die Donauinsel - 21 Kilometer Freiraum",
                "Wien Museum",
                "Wien Museum",
                "title-mismatch",
            ),
        ],
    )
    def test_keeps_apart(self, ta, tb, va, vb, reason):
        verdict = compare(c(1, "a", ta, va), c(2, "b", tb, vb))
        assert not verdict.same
        assert verdict.reason == reason

    def test_umbrella_guard_survives_geocoding(self):
        for label, a, b in [
            (
                "both geocoded, no venue names",
                c(1, "wien_gv_at", "Kultursommer Wien", lat=48.2100, lon=16.3400),
                c(
                    2,
                    "bandsintown",
                    "Waltzing in the Clouds - Kultursommer Wien",
                    lat=48.2102,
                    lon=16.3405,
                ),
            ),
            (
                "one venue named, both geocoded",
                c(3, "wien_gv_at", "Kultursommer Wien", lat=48.2100, lon=16.3400),
                c(
                    4,
                    "bandsintown",
                    "LEETA @ Kultursommer",
                    "Reithofferpark",
                    lat=48.2102,
                    lon=16.3405,
                ),
            ),
            (
                "ungeocoded, as originally pinned",
                c(5, "wien_gv_at", "Kultursommer Wien"),
                c(6, "wien_gv_at", "Waltzing in the Clouds - Kultursommer Wien"),
            ),
        ]:
            verdict = compare(a, b)
            assert not verdict.same, label
            assert verdict.reason == "umbrella-weak-venue", label

    def test_a_named_venue_on_both_sides_still_merges_short_titles(self):
        verdict = compare(
            c(1, "a", "TJARK", "Szene Wien", lat=48.19, lon=16.40),
            c(2, "b", "TJARK @ Szene Wien", "Szene", lat=48.19, lon=16.40),
        )
        assert verdict.same

    def test_weak_evidence_needs_more_than_a_fuzzy_title(self):
        boiler = " - Kostenlose Veranstaltungen für Kinder und Jugendliche im Park"
        verdict = compare(
            c(1, "wien_gv_at", '"Favoriten spielt"' + boiler),
            c(2, "wien_gv_at", '"Hernals spielt"' + boiler),
        )
        assert not verdict.same
        assert verdict.reason == "weak-evidence"

    def test_weak_evidence_survives_geocoding(self):
        boiler = " - Kostenlose Veranstaltungen für Kinder und Jugendliche im Park"
        verdict = compare(
            c(1, "wien_gv_at", '"Favoriten spielt"' + boiler, lat=48.2082, lon=16.3738),
            c(2, "wien_gv_at", '"Hernals spielt"' + boiler, lat=48.2082, lon=16.3738),
        )
        assert not verdict.same
        assert verdict.reason == "weak-evidence"

    def test_coordinates_still_merge_when_a_venue_name_agrees(self):
        verdict = compare(
            c(1, "a", "Wiener Mozart Konzert", "Musikverein", lat=48.2003, lon=16.3726),
            c(
                2,
                "b",
                "Wiener Mozart Konzerte",
                "Musikverein Wien",
                lat=48.2003,
                lon=16.3726,
            ),
        )
        assert verdict.same

    def test_coordinates_override_matching_titles(self):
        verdict = compare(
            c(
                1,
                "event_spotter",
                "CONNI - DAS MUSICAL",
                "Wiener Stadthalle",
                lat=48.2020,
                lon=16.3350,
            ),
            c(
                2,
                "events_at",
                "Conni - Das Musical",
                "VAZ St. Pölten",
                lat=48.2047,
                lon=15.6256,
            ),
        )
        assert not verdict.same
        assert verdict.reason.startswith("venue-coords-conflict")


class TestGrouping:
    def test_transitive_closure_recovers_one_happening(self):
        members = [
            c(1, "bandsintown", "Carpenter Brut", "Arena Wien Open Air"),
            c(
                2,
                "bandsintown",
                "VIENNA :: CARPENTER BRUT x HEALTH x CHAT PILE",
                "Arena Wien",
            ),
            c(
                3,
                "event_spotter",
                "CARPENTER BRUT (fra) + HEALTH (us) *OPEN AIR*",
                "Arena Wien",
            ),
            c(4, "eventfinder", "Carpenter Brut with", "Arena Wien Open Air, Wien"),
            c(5, "songkick", "Carpenter Brut @ Arena Wien", "Arena Wien"),
        ]
        groups = group_day(members)
        assert len(groups) == 1
        assert groups[0].sources == [
            "bandsintown",
            "event_spotter",
            "eventfinder",
            "songkick",
        ]

    def test_unrelated_happenings_stay_separate(self):
        groups = group_day(
            [
                c(1, "wien_info", "Deftones", "METAstadt"),
                c(2, "goodnight", "Salsa Bachata Merengue", "Volksgarten"),
                c(3, "warda", "Techno Night", "Grelle Forelle"),
            ]
        )
        assert len(groups) == 3

    def test_a_group_is_one_day(self):
        groups = group_day(
            [
                c(1, "wien_ticket", "Maria Theresia", "Schlosstheater"),
                c(
                    2,
                    "wien_ticket",
                    "Maria Theresia",
                    "Schlosstheater",
                    date_local=dt.date(2026, 8, 12),
                ),
            ]
        )
        assert len(groups) == 2

    def test_one_source_listing_the_same_thing_twice_is_one_group(self):
        members = [
            c(
                1,
                "eventfinder",
                "Krimi Escape Theater Wien NOVEX-3",
                "Peterskirche, Wien",
            ),
            c(
                2,
                "eventfinder",
                "Krimi Escape Theater Wien NOVEX-3",
                "Peterskirche, Wien",
            ),
        ]
        assert len(group_day(members)) == 1

    def test_primary_is_the_most_complete_member(self):
        poor = c(
            1,
            "wien_info",
            "Deep Purple",
            "Wiener Stadthalle",
            all_day=True,
            completeness=2,
        )
        rich = c(2, "wien_ticket", "DEEP PURPLE", "Wiener Stadthalle", completeness=9)
        groups = group_day([poor, rich])
        assert len(groups) == 1
        assert groups[0].primary.listing_id == rich.listing_id

    def test_primary_choice_is_deterministic_on_ties(self):
        a = c(1, "aaa", "Same Thing", "Venue", completeness=5)
        b = c(2, "bbb", "Same Thing", "Venue", completeness=5)
        assert group_day([a, b])[0].primary == group_day([b, a])[0].primary

    def test_a_group_records_the_rules_that_built_it(self):
        groups = group_day(
            [
                c(1, "a", "Deep Purple", "Szene"),
                c(2, "b", "Deep Purple", "Szene Wien"),
            ]
        )
        assert groups[0].reasons == {"exact/containment": 1}


class TestCompletenessScore:
    def test_coordinates_dominate(self):
        assert completeness_score(positioned=True) > completeness_score(
            described=True, image=True
        )

    def test_known_start_time_beats_all_day(self):
        assert completeness_score(all_day=False) > completeness_score(all_day=True)

    def test_an_empty_listing_scores_nothing(self):
        assert completeness_score() == 0


def content(listing_id: int = 1, **kw) -> Content:
    return Content(listing_id=listing_id, source=kw.pop("source", "a"), **kw)


class TestGapFill:
    def test_gaps_are_filled_from_the_other_listings(self):
        merged = gap_fill(
            content(1, source="wien_gv_at", title_de="Afrika Tage", position=VIENNA),
            [
                content(
                    2,
                    source="eventfinder",
                    venue_name="Donauinsel",
                    street="Floridsdorfer Brücke",
                    completeness=3,
                )
            ],
        )
        assert merged.venue_name == "Donauinsel"
        assert merged.street == "Floridsdorfer Brücke"
        assert merged.title_de == "Afrika Tage"

    def test_the_primary_is_never_overwritten(self):
        merged = gap_fill(
            content(1, venue_name="Wiener Stadthalle"),
            [content(2, venue_name="Stadthalle Wien", completeness=99)],
        )
        assert merged.venue_name == "Wiener Stadthalle"

    def test_the_richest_listing_wins_a_gap(self):
        merged = gap_fill(
            content(1),
            [
                content(2, source="poor", image_url="poor.jpg", completeness=1),
                content(3, source="rich", image_url="rich.jpg", completeness=9),
            ],
        )
        assert merged.image_url == "rich.jpg"

    def test_an_empty_string_counts_as_a_gap(self):
        merged = gap_fill(
            content(1, title_en=""),
            [content(2, title_en="Africa Days", completeness=1)],
        )
        assert merged.title_en == "Africa Days"

    def test_the_position_travels_whole(self):
        merged = gap_fill(content(1), [content(2, position=VIENNA, completeness=1)])
        assert merged.position == VIENNA

    def test_identity_stays_with_the_primary(self):
        merged = gap_fill(
            content(1, source="wien_info", url="https://wien.info/x"),
            [
                content(
                    2,
                    source="songkick",
                    url="https://songkick.test/y",
                    origin_url="https://venue.test/y",
                    completeness=9,
                )
            ],
        )
        assert merged.listing_id == 1
        assert merged.source == "wien_info"
        assert merged.url == "https://wien.info/x"
        assert merged.origin_url is None

    def test_one_listing_alone_is_returned_unchanged(self):
        only = content(1, title_de="Solo", position=VIENNA)
        assert gap_fill(only, []) == only
