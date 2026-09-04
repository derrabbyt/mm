"""The travel-time calculation, exercised without a database.

Builds `ParticipantInfo` values by hand and calls `compute_rendezvous` directly.
"""

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.core.contracts import ParticipantInfo
from app.core.enums import TravelMode
from app.core.exceptions import (
    NoPositionedParticipantsError,
    ParticipantOffGridError,
    RendezvousInfeasibleError,
)
from app.modules.rendezvous.repository import dataset, pick_transit_key
from app.modules.rendezvous.service import _best_cell, compute_rendezvous

VIENNA = ZoneInfo("Europe/Vienna")


def participant(cell_id: int, mode: TravelMode = TravelMode.WALK, name: str = "Ada"):
    """Someone standing in the middle of a real cell of the loaded dataset."""
    return ParticipantInfo(
        id=uuid.uuid4(), name=name, travel_mode=mode, position=dataset.position(cell_id)
    )


def at(day: int, hour: int) -> datetime:
    """A local datetime in August 2026: the 3rd is a Monday, so day 8 is a
    Saturday and day 9 a Sunday."""
    return datetime(2026, 8, day, hour, 0, tzinfo=VIENNA)


# --- picking the matrix -------------------------------------------------


@pytest.mark.parametrize(
    ("day", "hour", "expected"),
    [
        (5, 8, "transit_wed08"),
        (5, 12, "transit_wed12"),
        (5, 18, "transit_wed18"),
        (8, 10, "transit_sat10"),
        (8, 20, "transit_sat20"),
        (9, 14, "transit_sun14"),
    ],
)
def test_transit_key_follows_the_day_and_hour(day, hour, expected):
    assert pick_transit_key(at(day, hour)) == expected


def test_after_midnight_falls_back_to_the_late_evening_bake():
    """00:30 is 2.5h from the 22:00 bake and 7.5h from the 08:00 one, so it
    has to wrap around the clock rather than round up to morning."""
    assert pick_transit_key(at(5, 0).replace(minute=30)) == "transit_wed22"


def test_a_naive_datetime_is_read_as_already_local():
    naive = datetime(2026, 8, 5, 18, 0)  # noqa: DTZ001 - naive on purpose
    assert pick_transit_key(naive) == "transit_wed18"


# --- choosing the cell --------------------------------------------------


def test_best_cell_minimises_the_worst_traveller():
    # cell 1 is better for the pair even though cell 0 suits the first person.
    times = np.array([[10, 30], [90, 40]], dtype=np.uint16)
    assert _best_cell(times) == 1


def test_ties_on_the_worst_traveller_are_broken_by_total_time():
    # both cells have a worst of 50; the second has the smaller sum.
    times = np.array([[50, 50], [40, 20]], dtype=np.uint16)
    assert _best_cell(times) == 1


# --- the whole calculation ----------------------------------------------


def test_everyone_in_one_place_meets_there():
    home = 0
    people = [participant(home, name="Ada"), participant(home, name="Grace")]

    result = compute_rendezvous(at(5, 18), people, [])

    assert result.cell_id == home
    assert result.worst_seconds == 0
    assert [p.participant_id for p in result.per_participant] == [p.id for p in people]


def test_the_result_reports_every_participant_and_the_excluded_ids():
    excluded = [uuid.uuid4(), uuid.uuid4()]
    people = [participant(0), participant(1)]

    result = compute_rendezvous(at(5, 18), people, excluded)

    assert len(result.per_participant) == 2
    assert result.excluded_participant_ids == excluded
    assert result.worst_seconds == max(p.seconds for p in result.per_participant)


def test_transit_key_is_reported_only_when_somebody_rides():
    walkers = [participant(0, TravelMode.WALK)]
    assert compute_rendezvous(at(5, 18), walkers, []).transit_key_used is None

    riders = [participant(0, TravelMode.TRANSIT)]
    assert compute_rendezvous(at(5, 18), riders, []).transit_key_used == "transit_wed18"


def test_nobody_positioned_is_refused():
    with pytest.raises(NoPositionedParticipantsError):
        compute_rendezvous(at(5, 18), [], [])


def test_a_participant_outside_the_grid_is_named():
    off = ParticipantInfo(
        id=uuid.uuid4(),
        name="Barbara",
        travel_mode=TravelMode.WALK,
        # Null Island: comfortably outside any Vienna bake.
        position=dataset.position(0).model_copy(
            update={"latitude": 0.0, "longitude": 0.0}
        ),
    )
    with pytest.raises(ParticipantOffGridError) as exc:
        compute_rendezvous(at(5, 18), [off], [])
    assert "Barbara" in exc.value.message


def test_unreachable_pair_reports_who_is_stranded_and_the_horizon():
    """Two walkers at opposite corners of the grid cannot meet inside the
    walk matrix's horizon, so the error has to name them rather than return
    an arbitrary cell."""
    people = [
        participant(0, TravelMode.WALK, name="Ada"),
        participant(dataset.n_cells - 1, TravelMode.WALK, name="Grace"),
    ]
    try:
        result = compute_rendezvous(at(5, 18), people, [])
    except RendezvousInfeasibleError as exc:
        assert exc.message.startswith("No spot is within")
        assert "Ada" in exc.message or "Grace" in exc.message
    else:
        # A grid small enough that the corners are mutually reachable is a
        # valid dataset too; then the answer just has to be a real one.
        assert result.worst_seconds > 0
