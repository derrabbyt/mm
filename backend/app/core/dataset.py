"""What a baked travel-time Dataset is, defined once for both sides of it.

`modules/matrix` writes a Dataset and `modules/rendezvous` reads one. Neither
may import the other - both do their work in layers that are forbidden from
knowing another module exists - so the format itself has to sit below them, and
this is the only place it can. Same reasoning as `core/enums.py`: a fact that a
writer and a reader both have to agree on cannot live in either of them.

It used to live in both. The reader hand-rolled six `if`s and read
`entry["max_seconds"]` unconditionally while the writer had a re-run path that
left that key out, so a perfectly ordinary second bake wrote a Dataset that
raised `KeyError` at import of the rendezvous module - the API never started.
The Departure table was duplicated the same way, which is how a Saturday
Departure landed on a public holiday and nobody noticed. Both are one statement
now, and a Dataset the reader could not load fails validation where it is
written.

A Dataset is a folder:

    manifest.json   what to load, and the exact inputs it was built from
    grid.json       the cells, frozen for the lifetime of the folder
    <key>__<hash>.npy   one (n_cells, n_cells) uint16 matrix of seconds

Never scan the folder. `manifest.matrices` is the directory; filenames carry a
build hash and a folder may hold superseded ones.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from .enums import TravelMode

#: Bumped when the shape of `manifest.json` or `grid.json` changes. A reader
#: refuses a Dataset it does not recognise rather than guessing at it.
FORMAT_VERSION = 1

#: Bumped when the routing profile changes - speeds, snapping - so that build
#: hashes stop matching and matrices are rebuilt rather than reused.
PROFILE_VERSION = 1

#: Travel time meaning "no route at all", not "an hour and a half".
UNREACHABLE = 65535

MATRIX_DTYPE = "uint16"
MATRIX_UNIT = "seconds"
MATRIX_LAYOUT = "row-major; M[origin_cell, :] contiguous"

#: The aggregation the engine is asked for. `travel_time` is the column r5py
#: emits only for exactly this value; anything else renames it.
PERCENTILES = [50]

#: Where a Dataset says nothing about its timezone. Only a transit matrix
#: records one, so a Dataset of static modes alone has none to record.
FALLBACK_TIMEZONE = "Europe/Vienna"

MINUTES_PER_DAY = 24 * 60


class MatrixMode(StrEnum):
    """How a matrix was routed. Not `TravelMode`: that is what a Participant
    chooses, and `bike` here is `bicycle` there - the matrix keys are what the
    files on disk are called, and they are frozen by the format."""

    WALK = "walk"
    BIKE = "bike"
    CAR = "car"
    TRANSIT = "transit"


#: The ceiling each mode is routed to, in seconds. Walk gets a generous one so
#: that its sentinel means genuinely unroutable rather than merely far - the
#: minimax has to be able to tell those apart. The others reach across the city
#: well inside ninety minutes.
MAX_SECONDS_PER_MODE: Mapping[MatrixMode, int] = {
    MatrixMode.WALK: 28800,
    MatrixMode.BIKE: 5400,
    MatrixMode.CAR: 5400,
    MatrixMode.TRANSIT: 5400,
}

#: The one matrix a traveller on a time-independent mode uses.
STATIC_MATRIX_KEY: Mapping[TravelMode, str] = {
    TravelMode.WALK: MatrixMode.WALK.value,
    TravelMode.BICYCLE: MatrixMode.BIKE.value,
    TravelMode.CAR: MatrixMode.CAR.value,
}


class DayClass(StrEnum):
    """The kind of day a transit matrix was baked for, as its key spells it.

    Three classes because that is what a transit operator runs: a weekday
    timetable, a Saturday one and a Sunday one. `WEEKDAY` is spelled `wed`
    because the reference day for it is a Wednesday.
    """

    WEEKDAY = "wed"
    SATURDAY = "sat"
    SUNDAY = "sun"

    @property
    def weekday(self) -> int:
        """`date.weekday()` of the day this class is represented by."""
        return {DayClass.WEEKDAY: 2, DayClass.SATURDAY: 5, DayClass.SUNDAY: 6}[self]

    @classmethod
    def of(cls, weekday: int) -> "DayClass":
        """Which class a `date.weekday()` falls in."""
        if weekday == 5:
            return cls.SATURDAY
        if weekday == 6:
            return cls.SUNDAY
        return cls.WEEKDAY


@dataclass(frozen=True)
class Departure:
    """One time a transit matrix was computed for: a class of day and an hour.

    Not a calendar date. A Departure becomes one only against a particular
    transit feed, because the router reads whatever service actually ran on the
    date it is given - which is why the bake derives the date from the feed and
    nothing here states one.
    """

    day: DayClass
    hour: int

    @property
    def bucket(self) -> str:
        return f"{self.day.value}{self.hour:02d}"

    @property
    def matrix_key(self) -> str:
        return f"transit_{self.bucket}"


#: The Departure table, stated once. Adding a time here adds it to the bake and
#: to the matrix the reader picks, in one edit.
DEPARTURES: tuple[Departure, ...] = (
    Departure(DayClass.WEEKDAY, 8),
    Departure(DayClass.WEEKDAY, 12),
    Departure(DayClass.WEEKDAY, 18),
    Departure(DayClass.WEEKDAY, 22),
    Departure(DayClass.SATURDAY, 10),
    Departure(DayClass.SATURDAY, 14),
    Departure(DayClass.SATURDAY, 20),
    Departure(DayClass.SUNDAY, 10),
    Departure(DayClass.SUNDAY, 14),
    Departure(DayClass.SUNDAY, 20),
)

STATIC_MATRIX_KEYS: tuple[str, ...] = (
    MatrixMode.WALK.value,
    MatrixMode.BIKE.value,
    MatrixMode.CAR.value,
)
TRANSIT_MATRIX_KEYS: tuple[str, ...] = tuple(d.matrix_key for d in DEPARTURES)

#: Every matrix a complete Dataset carries. A Dataset missing one of these
#: would load and then fail per request, so it is refused at load instead.
MATRIX_KEYS: tuple[str, ...] = (*STATIC_MATRIX_KEYS, *TRANSIT_MATRIX_KEYS)


def mode_of(matrix_key: str) -> MatrixMode:
    """How the matrix under this key was routed.

    The key is the format's, so reading a mode out of it is the format's job -
    otherwise every writer works it out again and they can disagree.
    """
    if matrix_key in STATIC_MATRIX_KEYS:
        return MatrixMode(matrix_key)
    if matrix_key in TRANSIT_MATRIX_KEYS:
        return MatrixMode.TRANSIT
    raise KeyError(f"{matrix_key!r} is not a matrix a Dataset carries")


def departures_of(day: DayClass) -> tuple[Departure, ...]:
    return tuple(d for d in DEPARTURES if d.day is day)


def pick_transit_key(local: datetime) -> str:
    """The matrix whose Departure best matches `local`, a time in the Dataset's
    own timezone.

    Distance is measured around the clock, so 00:30 lands on the 22:00 bake
    rather than the 08:00 one. The class of day stays whatever the date says it
    is - a meetup at half past midnight on Sunday is a Sunday meetup, and the
    late-evening Sunday timetable is the honest answer for it.
    """
    table = departures_of(DayClass.of(local.weekday()))
    minutes = local.hour * 60 + local.minute

    def distance(departure: Departure) -> int:
        gap = abs(departure.hour * 60 - minutes)
        return min(gap, MINUTES_PER_DAY - gap)

    return min(table, key=distance).matrix_key


class DatasetFormatError(Exception):
    """A Dataset that cannot be read as one, with the field that says so.

    Raised by the reader when loading and by the bake before publishing, which
    is the point of them sharing this file: the bake cannot write a Dataset
    that would raise this in the API.
    """


# --- the two metadata files, as models ----------------------------------

_STRICT = ConfigDict(extra="forbid", frozen=True)


class _Versioned(BaseModel):
    """A metadata file that states which format it is written in.

    Checked as a field rather than after the whole model, so that a Dataset
    from a format this code does not know says so even when it also has other
    problems - the version is the thing that explains the others.
    """

    model_config = _STRICT

    format_version: int

    @field_validator("format_version")
    @classmethod
    def _supported(cls, value: int) -> int:
        if value != FORMAT_VERSION:
            raise ValueError(f"{value} is not the supported format {FORMAT_VERSION}")
        return value


class BoundingBox(BaseModel):
    model_config = _STRICT

    south: float
    north: float
    west: float
    east: float


class GridCell(BaseModel):
    model_config = _STRICT

    id: int
    lat: float
    lon: float
    h3: str


class Grid(_Versioned):
    """`grid.json` - the coordinate system, frozen for the folder's lifetime.

    Hex only. A square grid was a second shape the producing script could emit
    and this reader never had a branch for: positions snap through H3, so a
    square Dataset was unloadable here and is refused rather than half-read.
    """

    grid_version: str
    grid_shape: Literal["hex"]
    bbox_wgs84: BoundingBox
    n_cells: int
    h3_res: int
    cells: list[GridCell]

    @model_validator(mode="after")
    def _check(self) -> "Grid":
        if len(self.cells) != self.n_cells:
            raise ValueError(f"n_cells {self.n_cells} but {len(self.cells)} cells")
        return self


class SourceFile(BaseModel):
    """An input a Dataset was built from, by content rather than by name."""

    model_config = _STRICT

    path: str
    sha256: str
    sha256_short: str
    size_bytes: int


class ManifestGrid(BaseModel):
    model_config = _STRICT

    file: str
    sha256: str
    n_cells: int
    grid_shape: str


class Aggregation(BaseModel):
    model_config = _STRICT

    kind: Literal["percentile"]
    percentiles: list[int]


class MatrixConventions(BaseModel):
    """What every matrix in the folder holds, and how.

    The reader does not take its sentinel or its dtype from here - it could not,
    they are compiled into the calculation - so this is checked against them
    instead. A Dataset declaring a different sentinel is not a Dataset this
    reader can serve.
    """

    model_config = _STRICT

    dtype: Literal["uint16"]
    unit: Literal["seconds"]
    unreachable_sentinel: int
    layout: str
    aggregation_default: Aggregation
    #: The ceiling each mode was routed to. Per matrix, `build_inputs` is what
    #: the reader acts on; this is the table as the bake held it.
    max_seconds_per_mode: dict[str, int]

    @model_validator(mode="after")
    def _check(self) -> "MatrixConventions":
        if self.unreachable_sentinel != UNREACHABLE:
            raise ValueError(
                f"unreachable_sentinel {self.unreachable_sentinel} is not {UNREACHABLE}"
            )
        return self


#: What every matrix in a Dataset this code writes holds, and how. Stated here
#: rather than assembled by the bake, because it is the format speaking about
#: itself - and a Dataset the reader loads is checked against the parts of it
#: the calculation has compiled in, not handed them.
CONVENTIONS = MatrixConventions(
    dtype=MATRIX_DTYPE,
    unit=MATRIX_UNIT,
    unreachable_sentinel=UNREACHABLE,
    layout=MATRIX_LAYOUT,
    aggregation_default=Aggregation(kind="percentile", percentiles=PERCENTILES),
    max_seconds_per_mode={
        mode.value: cap for mode, cap in MAX_SECONDS_PER_MODE.items()
    },
)


class BuildInputs(BaseModel):
    """Everything that decided this matrix's bytes, so a build hash means
    something and a wrong travel time can be traced to what produced it."""

    model_config = _STRICT

    format_version: int
    profile_version: int
    engine: str
    engine_version: str
    grid_version: str
    mode: MatrixMode
    #: The ceiling this matrix was routed to. The cap, not the observed maximum
    #: - `MatrixEntry.max_seconds` is that, and the two coincide only by
    #: accident.
    max_seconds: int
    snap_to_network: bool
    percentiles: list[int]
    osm_sha256: str
    gtfs_sha256: str | None
    departure: datetime | None
    departure_window_min: int | None


class MatrixEntry(BaseModel):
    """One matrix, as the manifest lists it.

    `max_seconds` and `min_seconds` are *observed*, over reachable pairs, and
    are null for a matrix in which nothing is reachable at all. The ceiling the
    matrix was routed to is `cap_seconds`. Reading the observed maximum as the
    ceiling is what made an all-unreachable matrix a `TypeError` on the request
    path rather than a rejected bake.

    Required but nullable, both of them: absent is a Dataset built by the re-run
    path that never filled them in, and that Dataset is not servable.
    """

    model_config = _STRICT

    file: str
    build_hash: str
    build_inputs: BuildInputs
    shape: tuple[int, int]
    aggregation: Aggregation
    coverage_pct: float
    min_seconds: int | None
    max_seconds: int | None
    wall_seconds: float | None = None
    #: Transit only: which moment the timetable was read at.
    departure: datetime | None = None
    departure_window_min: int | None = None
    timezone: str | None = None

    @property
    def cap_seconds(self) -> int:
        """The ceiling this matrix was routed to - the furthest it can speak
        for, and so the horizon to report when nobody can reach anybody."""
        return self.build_inputs.max_seconds

    @model_validator(mode="after")
    def _check(self) -> "MatrixEntry":
        if self.build_inputs.mode is MatrixMode.TRANSIT and self.departure is None:
            raise ValueError("a transit matrix has to record its departure")
        return self


class Manifest(_Versioned):
    """`manifest.json` - the directory of a Dataset, and its provenance."""

    city: str
    grid_version: str
    built_at: datetime
    grid: ManifestGrid
    sources: dict[str, SourceFile]
    matrix_conventions: MatrixConventions
    matrices: dict[str, MatrixEntry]

    @model_validator(mode="after")
    def _check(self) -> "Manifest":
        missing = [key for key in MATRIX_KEYS if key not in self.matrices]
        if missing:
            raise ValueError(f"matrices missing: {', '.join(missing)}")
        return self


@dataclass(frozen=True)
class DatasetMetadata:
    """A Dataset's two metadata files, agreeing with each other.

    What the reader needs before it opens a single matrix, and what the bake
    constructs to prove the Dataset it has built is one the reader would accept.
    """

    folder: Path
    manifest: Manifest
    grid: Grid

    @property
    def n_cells(self) -> int:
        return self.grid.n_cells

    @property
    def h3_res(self) -> int:
        return self.grid.h3_res

    @cached_property
    def timezone(self) -> ZoneInfo:
        """The timezone the travel times were baked for. Only transit matrices
        record one, so a Dataset without any falls back."""
        named = next(
            (
                entry.timezone
                for entry in self.manifest.matrices.values()
                if entry.timezone
            ),
            FALLBACK_TIMEZONE,
        )
        return ZoneInfo(named)


def _reject(path: Path, error: ValidationError) -> DatasetFormatError:
    """A validation failure as a message that names the fields it is about."""
    fields = "; ".join(
        f"{'.'.join(str(part) for part in problem['loc']) or '<root>'}: "
        f"{problem['msg']}"
        for problem in error.errors()
    )
    return DatasetFormatError(f"{path.name} is not a valid Dataset: {fields}")


def _read_json(path: Path) -> object:
    try:
        text = path.read_text()
    except OSError as error:
        raise DatasetFormatError(f"cannot read {path}: {error}") from error
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise DatasetFormatError(f"{path.name} is not valid JSON: {error}") from error


def read_manifest(path: Path) -> Manifest:
    """`manifest.json` at `path`, validated, or a rejection naming the field."""
    try:
        return Manifest.model_validate(_read_json(path))
    except ValidationError as error:
        raise _reject(path, error) from error


def read_grid(path: Path) -> Grid:
    """`grid.json` at `path`, validated, or a rejection naming the field."""
    try:
        return Grid.model_validate(_read_json(path))
    except ValidationError as error:
        raise _reject(path, error) from error


def build_metadata(folder: Path, manifest: Manifest, grid: Grid) -> DatasetMetadata:
    """The two files as one value, having checked they describe one Dataset.

    Called by the reader on the way in and by the bake on the way out, which is
    what stops a Dataset being written that the reader would refuse.
    """
    if grid.grid_version != manifest.grid_version:
        raise DatasetFormatError(
            f"grid_version {grid.grid_version!r} in grid.json but "
            f"{manifest.grid_version!r} in manifest.json"
        )
    if grid.n_cells != manifest.grid.n_cells:
        raise DatasetFormatError(
            f"n_cells {grid.n_cells} in grid.json but {manifest.grid.n_cells} "
            "in manifest.json"
        )
    return DatasetMetadata(folder=folder, manifest=manifest, grid=grid)


def load_metadata(
    folder: Path, *, manifest_name: str = "manifest.json"
) -> DatasetMetadata:
    """Read and validate the Dataset in `folder`.

    `manifest_name` is for the bake, which validates a manifest still under its
    temporary name so that a Dataset is only published once it has been read
    back as one.
    """
    manifest = read_manifest(folder / manifest_name)
    grid = read_grid(folder / manifest.grid.file)
    return build_metadata(folder, manifest, grid)


def open_matrices(folder: Path, manifest: Manifest) -> dict[str, np.ndarray]:
    """Every matrix the manifest lists, memory-mapped rather than read.

    Mapped, not loaded: 1.4 GB of matrices the calculation touches one row of
    per participant belongs in the page cache, not in the heap.
    """
    expected = (manifest.grid.n_cells, manifest.grid.n_cells)
    matrices: dict[str, np.ndarray] = {}
    for key, entry in manifest.matrices.items():
        path = folder / entry.file
        try:
            matrix = np.load(path, mmap_mode="r")
        except OSError as error:
            raise DatasetFormatError(f"cannot open {key} at {path}: {error}") from error
        if matrix.shape != expected:
            raise DatasetFormatError(
                f"{key} is {matrix.shape} but the grid has {expected[0]} cells"
            )
        if matrix.dtype != np.uint16:
            raise DatasetFormatError(
                f"{key} is {matrix.dtype} rather than {MATRIX_DTYPE}"
            )
        matrices[key] = matrix
    return matrices
