"""Turning an address into a position.

The two pure pieces - which questions to ask, and whether to believe the answer -
are tested over values, so a wrong position is reproducible from the payload that
caused it with no network and no database. The cache is tested through the
service, because "does a second run ask again?" is the only thing about it that
matters to anyone.

Nothing here reaches a geocoder: every test supplies its own.
"""

import uuid

import pytest

from app.core.contracts import Address
from app.modules.geocoding import photon, query, repository
from app.modules.geocoding import service as geocoding
from app.modules.geocoding.client import Unreachable
from app.modules.geocoding.photon import Found


def feature(lon: float, lat: float, kind: str = "house", postcode: str | None = None):
    properties: dict[str, object] = {"type": kind}
    if postcode:
        properties["postcode"] = postcode
    return {
        "features": [
            {"geometry": {"coordinates": [lon, lat]}, "properties": properties}
        ]
    }


class TestReadingPhoton:
    def test_a_house_is_exact(self):
        found = photon.read(feature(16.3731, 48.2083))
        assert found is not None
        assert (found.latitude, found.longitude) == (48.2083, 16.3731)
        assert found.precision == "exact"

    @pytest.mark.parametrize("kind", sorted(photon.COARSE))
    def test_a_coarse_match_is_refused(self, kind):
        """One pin for every unplaced Listing is worse than no pin at all.

        It breaks "what is on near here" and hands deduplication a distance of
        zero between happenings that have nothing to do with each other.
        """
        assert photon.read(feature(16.3731, 48.2083, kind)) is None

    def test_a_result_outside_austria_is_refused(self):
        """Photon answers with a same-named place elsewhere quite happily."""
        assert photon.read(feature(-0.1276, 51.5072)) is None

    def test_an_empty_or_malformed_answer_is_none(self):
        assert photon.read({}) is None
        assert photon.read({"features": []}) is None
        assert photon.read(None) is None
        assert photon.read({"features": [{"geometry": {"coordinates": [1]}}]}) is None

    def test_the_postcode_is_carried_for_checking(self):
        found = photon.read(feature(16.3731, 48.2083, postcode="1010"))
        assert found is not None and found.postcode == "1010"


class TestQuestionsAsked:
    def test_the_venue_with_its_street_is_asked_first(self):
        """The most specific question: a street alone can resolve to the wrong
        building when two venues share one address."""
        asked = query.variants(
            Address(
                venue_name="Das Werk", street="Spittelauer Lände 12", postcode="1090"
            )
        )
        assert asked[0] == "Das Werk, Spittelauer Lände 12, 1090, Wien, Austria"
        assert "Spittelauer Lände 12, 1090, Wien, Austria" in asked

    def test_a_qualifier_is_retried_without_it(self):
        """A venue named with its parish lands on a different building 7.7km off."""
        asked = query.variants(Address(venue_name="Karlskirche - Pfarre St. Karl"))
        assert asked[-1] == "Karlskirche, Wien, Austria"

    def test_an_address_with_nothing_to_go_on_asks_nothing(self):
        assert query.variants(Address(city="Wien")) == []

    def test_two_spellings_share_one_cache_entry(self):
        assert query.cache_key("Grelle Forelle, Wien") == query.cache_key(
            "grelle  forelle , wien"
        )

    def test_accents_do_not_split_the_cache(self):
        assert query.cache_key("Spittelauer Lände") == query.cache_key(
            "Spittelauer Lande"
        )


class FakeGeocoder:
    """Answers from a script, and records what it was asked.

    An address not in the script is a *miss* - asked, nothing found - which is
    the thing worth caching. A geocoder that could not be reached at all is
    `DownGeocoder` below, and is not.
    """

    name = "fake"

    def __init__(self, answers: dict[str, Found | None]):
        self.answers = answers
        self.asked: list[str] = []

    def lookup(self, question: str) -> Found | None:
        self.asked.append(question)
        return self.answers.get(question)


class DownGeocoder:
    """Never answers. What an outage, or geocoding switched off, looks like."""

    name = "down"

    def __init__(self):
        self.asked: list[str] = []

    def lookup(self, question: str) -> Found | None:
        self.asked.append(question)
        raise Unreachable(question)


def found(lat=48.2083, lon=16.3731, precision="exact", postcode=None) -> Found:
    return Found(latitude=lat, longitude=lon, precision=precision, postcode=postcode)


@pytest.fixture
def unique():
    """A venue name no real run can have cached.

    What a test writes is rolled back, but what it *reads* is not: these run
    against the dev database, and the cache deliberately outlives the Listings
    that filled it. So an address a live run has already resolved would answer
    from the cache and never reach the geocoder under test.
    """

    def _name(label: str) -> str:
        return f"zz-{uuid.uuid4().hex[:10]} {label}"

    return _name


class TestResolving:
    def test_a_position_is_returned_and_remembered(self, db, unique):
        address = Address(venue_name=unique("Grelle Forelle"), city="Wien")
        asked = query.variants(address)
        geocoder = FakeGeocoder({asked[0]: found()})

        located = geocoding.locate(db, address, geocoder)

        assert located is not None
        assert (located.position.latitude, located.position.longitude) == (
            48.2083,
            16.3731,
        )
        assert located.precision == "exact"
        # The entry for *this* address, not a count of the table: the dev
        # database this runs against holds whatever real runs have cached.
        remembered = repository.cached(db, query.cache_key(asked[0]))
        assert remembered is not None
        assert (remembered.lat, remembered.lon) == (48.2083, 16.3731)

    def test_a_second_run_does_not_ask_again(self, db, unique):
        """The corpus is regenerated by re-scraping, so this is what stops every
        run paying for every address over again."""
        address = Address(venue_name=unique("Grelle Forelle"), city="Wien")
        first = FakeGeocoder({query.variants(address)[0]: found()})
        geocoding.locate(db, address, first)

        second = FakeGeocoder({})
        located = geocoding.locate(db, address, second)

        assert located is not None
        assert second.asked == [], "it asked again for an address it had resolved"

    def test_a_miss_is_remembered_too(self, db, unique):
        """Otherwise every run re-asks for exactly the addresses that have already
        proved unresolvable, which is most of what a repeat run would do."""
        address = Address(venue_name=unique("Nowhere At All"), city="Wien")
        geocoding.locate(db, address, FakeGeocoder({}))

        again = FakeGeocoder({})
        assert geocoding.locate(db, address, again) is None
        assert again.asked == []

    def test_the_answer_matching_the_stated_postcode_wins(self, db, unique):
        """The failure a fuzzy match produces is a *confident* wrong answer."""
        address = Address(
            venue_name=unique("Karlskirche") + " - Pfarre St. Karl",
            postcode="1040",
            city="Wien",
        )
        asked = query.variants(address)
        geocoder = FakeGeocoder(
            {
                # The full name resolves to a different church in another district.
                asked[0]: found(lat=48.16, lon=16.29, postcode="1130"),
                # The bare name is the right one.
                asked[-1]: found(lat=48.1985, lon=16.3719, postcode="1040"),
            }
        )

        located = geocoding.locate(db, address, geocoder)

        assert located is not None
        assert round(located.position.latitude, 3) == 48.199

    def test_with_no_postcode_the_first_answer_stands_as_it_is(self, db, unique):
        """Nothing to corroborate against means nothing to downgrade on either."""
        address = Address(venue_name=unique("Somewhere Unverifiable"), city="Wien")
        geocoder = FakeGeocoder({query.variants(address)[0]: found(postcode="1210")})

        located = geocoding.locate(db, address, geocoder)

        assert located is not None
        assert located.precision == "exact"

    def test_an_answer_the_address_contradicts_is_downgraded(self, db, unique):
        """Kept, because an approximate position beats none - but marked, so the
        row is visibly less trustworthy rather than quietly wrong."""
        address = Address(
            venue_name=unique("Contradicted"), postcode="1040", city="Wien"
        )
        geocoder = FakeGeocoder({query.variants(address)[0]: found(postcode="1130")})

        located = geocoding.locate(db, address, geocoder)

        assert located is not None
        assert located.precision == "approx"

    def test_a_geocoder_that_never_answered_is_not_remembered(self, db, unique):
        """Otherwise one outage becomes a permanent verdict on every address it
        touched, and turning geocoding back on would resolve nothing."""
        address = Address(venue_name=unique("During An Outage"), city="Wien")

        assert geocoding.locate(db, address, DownGeocoder()) is None
        assert (
            repository.cached(db, query.cache_key(query.variants(address)[0])) is None
        )

        # Once it is back, the address resolves as if it had never been asked.
        recovered = FakeGeocoder({query.variants(address)[0]: found()})
        assert geocoding.locate(db, address, recovered) is not None
        assert recovered.asked, "it should have asked, having remembered nothing"

    def test_an_address_with_nothing_to_go_on_asks_nothing(self, db):
        geocoder = FakeGeocoder({})
        assert geocoding.locate(db, Address(city="Wien"), geocoder) is None
        assert geocoder.asked == []
