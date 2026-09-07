"""A Source's parse, over a document it actually served.

The highest seam reachable without the network: a pure function from a saved
payload to Listings. Every case here is pointed at a trap the site sets, not at
the happy path - a parser that starts producing nonsense gets fixed against this
file, and the fixture is the exact document it broke on.
"""

import json
import pathlib

import pytest

from app.modules.events.scraped import RawListing
from app.modules.events.sources import discover, goabase
from app.modules.events.sources.spec import RawPayload

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def payload(source: str, filename: str, kind: str = "listing") -> RawPayload:
    path = FIXTURES / source / filename
    return RawPayload(
        url=f"fixture://{source}/{filename}", body=path.read_bytes(), kind=kind
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


def test_goabase_is_discovered_as_a_source():
    assert "goabase" in discover()
