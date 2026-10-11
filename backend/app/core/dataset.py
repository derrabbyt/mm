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

FORMAT_VERSION = 1

PROFILE_VERSION = 1

UNREACHABLE = 65535

MATRIX_DTYPE = "uint16"
MATRIX_UNIT = "seconds"
MATRIX_LAYOUT = "row-major; M[origin_cell, :] contiguous"

PERCENTILES = [50]

FALLBACK_TIMEZONE = "Europe/Vienna"

MINUTES_PER_DAY = 24 * 60


class MatrixMode(StrEnum):
    WALK = "walk"
    BIKE = "bike"
    CAR = "car"
    TRANSIT = "transit"


MAX_SECONDS_PER_MODE: Mapping[MatrixMode, int] = {
    MatrixMode.WALK: 28800,
    MatrixMode.BIKE: 5400,
    MatrixMode.CAR: 5400,
    MatrixMode.TRANSIT: 5400,
}

STATIC_MATRIX_KEY: Mapping[TravelMode, str] = {
    TravelMode.WALK: MatrixMode.WALK.value,
    TravelMode.BICYCLE: MatrixMode.BIKE.value,
    TravelMode.CAR: MatrixMode.CAR.value,
}


class DayClass(StrEnum):
    WEEKDAY = "wed"
    SATURDAY = "sat"
    SUNDAY = "sun"

    @property
    def weekday(self) -> int:
        return {DayClass.WEEKDAY: 2, DayClass.SATURDAY: 5, DayClass.SUNDAY: 6}[self]

    @classmethod
    def of(cls, weekday: int) -> "DayClass":
        if weekday == 5:
            return cls.SATURDAY
        if weekday == 6:
            return cls.SUNDAY
        return cls.WEEKDAY


@dataclass(frozen=True)
class Departure:
    day: DayClass
    hour: int

    @property
    def bucket(self) -> str:
        return f"{self.day.value}{self.hour:02d}"

    @property
    def matrix_key(self) -> str:
        return f"transit_{self.bucket}"


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

MATRIX_KEYS: tuple[str, ...] = (*STATIC_MATRIX_KEYS, *TRANSIT_MATRIX_KEYS)


def mode_of(matrix_key: str) -> MatrixMode:
    if matrix_key in STATIC_MATRIX_KEYS:
        return MatrixMode(matrix_key)
    if matrix_key in TRANSIT_MATRIX_KEYS:
        return MatrixMode.TRANSIT
    raise KeyError(f"{matrix_key!r} is not a matrix a Dataset carries")


def departures_of(day: DayClass) -> tuple[Departure, ...]:
    return tuple(d for d in DEPARTURES if d.day is day)


def pick_transit_key(local: datetime) -> str:
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


_STRICT = ConfigDict(extra="forbid", frozen=True)


class _Versioned(BaseModel):
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
    model_config = _STRICT

    dtype: Literal["uint16"]
    unit: Literal["seconds"]
    unreachable_sentinel: int
    layout: str
    aggregation_default: Aggregation
    max_seconds_per_mode: dict[str, int]

    @model_validator(mode="after")
    def _check(self) -> "MatrixConventions":
        if self.unreachable_sentinel != UNREACHABLE:
            raise ValueError(
                f"unreachable_sentinel {self.unreachable_sentinel} is not {UNREACHABLE}"
            )
        return self


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
    model_config = _STRICT

    format_version: int
    profile_version: int
    engine: str
    engine_version: str
    grid_version: str
    mode: MatrixMode
    max_seconds: int
    snap_to_network: bool
    percentiles: list[int]
    osm_sha256: str
    gtfs_sha256: str | None
    departure: datetime | None
    departure_window_min: int | None


class MatrixEntry(BaseModel):
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
    departure: datetime | None = None
    departure_window_min: int | None = None
    timezone: str | None = None

    @property
    def cap_seconds(self) -> int:
        return self.build_inputs.max_seconds

    @model_validator(mode="after")
    def _check(self) -> "MatrixEntry":
        if self.build_inputs.mode is MatrixMode.TRANSIT and self.departure is None:
            raise ValueError("a transit matrix has to record its departure")
        return self


class Manifest(_Versioned):
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
    try:
        return Manifest.model_validate(_read_json(path))
    except ValidationError as error:
        raise _reject(path, error) from error


def read_grid(path: Path) -> Grid:
    try:
        return Grid.model_validate(_read_json(path))
    except ValidationError as error:
        raise _reject(path, error) from error


def build_metadata(folder: Path, manifest: Manifest, grid: Grid) -> DatasetMetadata:
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
    manifest = read_manifest(folder / manifest_name)
    grid = read_grid(folder / manifest.grid.file)
    return build_metadata(folder, manifest, grid)


def open_matrices(folder: Path, manifest: Manifest) -> dict[str, np.ndarray]:
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
