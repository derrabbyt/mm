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
        assert photon.read(feature(16.3731, 48.2083, kind)) is None

    def test_a_result_outside_austria_is_refused(self):
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
        asked = query.variants(
            Address(
                venue_name="Das Werk", street="Spittelauer Lände 12", postcode="1090"
            )
        )
        assert asked[0] == "Das Werk, Spittelauer Lände 12, 1090, Wien, Austria"
        assert "Spittelauer Lände 12, 1090, Wien, Austria" in asked

    def test_a_qualifier_is_retried_without_it(self):
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
    name = "fake"

    def __init__(self, answers: dict[str, Found | None]):
        self.answers = answers
        self.asked: list[str] = []

    def lookup(self, question: str) -> Found | None:
        self.asked.append(question)
        return self.answers.get(question)


class DownGeocoder:
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
        remembered = repository.cached(db, query.cache_key(asked[0]))
        assert remembered is not None
        assert (remembered.lat, remembered.lon) == (48.2083, 16.3731)

    def test_a_second_run_does_not_ask_again(self, db, unique):
        address = Address(venue_name=unique("Grelle Forelle"), city="Wien")
        first = FakeGeocoder({query.variants(address)[0]: found()})
        geocoding.locate(db, address, first)

        second = FakeGeocoder({})
        located = geocoding.locate(db, address, second)

        assert located is not None
        assert second.asked == [], "it asked again for an address it had resolved"

    def test_a_miss_is_remembered_too(self, db, unique):
        address = Address(venue_name=unique("Nowhere At All"), city="Wien")
        geocoding.locate(db, address, FakeGeocoder({}))

        again = FakeGeocoder({})
        assert geocoding.locate(db, address, again) is None
        assert again.asked == []

    def test_the_answer_matching_the_stated_postcode_wins(self, db, unique):
        address = Address(
            venue_name=unique("Karlskirche") + " - Pfarre St. Karl",
            postcode="1040",
            city="Wien",
        )
        asked = query.variants(address)
        geocoder = FakeGeocoder(
            {
                asked[0]: found(lat=48.16, lon=16.29, postcode="1130"),
                asked[-1]: found(lat=48.1985, lon=16.3719, postcode="1040"),
            }
        )

        located = geocoding.locate(db, address, geocoder)

        assert located is not None
        assert round(located.position.latitude, 3) == 48.199

    def test_with_no_postcode_the_first_answer_stands_as_it_is(self, db, unique):
        address = Address(venue_name=unique("Somewhere Unverifiable"), city="Wien")
        geocoder = FakeGeocoder({query.variants(address)[0]: found(postcode="1210")})

        located = geocoding.locate(db, address, geocoder)

        assert located is not None
        assert located.precision == "exact"

    def test_an_answer_the_address_contradicts_is_downgraded(self, db, unique):
        address = Address(
            venue_name=unique("Contradicted"), postcode="1040", city="Wien"
        )
        geocoder = FakeGeocoder({query.variants(address)[0]: found(postcode="1130")})

        located = geocoding.locate(db, address, geocoder)

        assert located is not None
        assert located.precision == "approx"

    def test_a_geocoder_that_never_answered_is_not_remembered(self, db, unique):
        address = Address(venue_name=unique("During An Outage"), city="Wien")

        assert geocoding.locate(db, address, DownGeocoder()) is None
        assert (
            repository.cached(db, query.cache_key(query.variants(address)[0])) is None
        )

        recovered = FakeGeocoder({query.variants(address)[0]: found()})
        assert geocoding.locate(db, address, recovered) is not None
        assert recovered.asked, "it should have asked, having remembered nothing"

    def test_an_address_with_nothing_to_go_on_asks_nothing(self, db):
        geocoder = FakeGeocoder({})
        assert geocoding.locate(db, Address(city="Wien"), geocoder) is None
        assert geocoder.asked == []
