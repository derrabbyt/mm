"""The bake: an OSM extract and a transit feed in, a Dataset out.

One process, three hours, a JVM, and an exit code. It fetches nothing - the
extract and the feed are paths it is handed, so the geodata job being unwritten
does not stop it running against a directory somebody filled by hand.

Three things it does that the script it came from did not:

It writes *through* `core/dataset.py`, the same definition the rendezvous
calculation reads with. A manifest entry is assembled from the array it
describes, so there is no second path that could leave a field out - which is
what the old re-run branch did, producing a Dataset that raised `KeyError` at
import of the reader and took the API down.

It derives every Departure from the feed (`feed.py`) instead of carrying ten
calendar dates, and it refuses to publish a Dataset that fails either check in
`checks.py`.

And it publishes by renaming into place, so a reader never sees a half-written
file and the previous Dataset stays readable for the whole of a bake. See ADR
0006.
"""

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import h3
import numpy as np

from ...core import dataset as fmt
from . import checks, engine, feed
from .profile import PROFILE, BakeProfile

logger = logging.getLogger(__name__)

#: The manifest is written under this name and renamed last. Dotted so that a
#: reader scanning the folder - which no reader should - would skip it anyway.
STAGED_MANIFEST = ".manifest.json.staged"

ENGINE = "r5py"


class BakeError(Exception):
    """The bake cannot run, or cannot publish what it built."""


@dataclass(frozen=True)
class BakeReport:
    """What an operator wants off a run: how long, how much, and from what."""

    folder: Path
    grid_version: str
    n_cells: int
    built: tuple[str, ...]
    reused: tuple[str, ...]
    bytes_on_disk: int
    seconds: float

    @property
    def summary(self) -> str:
        return (
            f"{self.folder.name} ({self.grid_version}, {self.n_cells} cells): "
            f"{len(self.built)} matrix(es) built, {len(self.reused)} reused, "
            f"{self.bytes_on_disk / 1024**3:.2f} GiB on disk, "
            f"{self.seconds / 60:.1f} min"
        )


# --- the grid -----------------------------------------------------------


def build_grid(profile: BakeProfile) -> fmt.Grid:
    """The cells a Dataset is built on, derived from the profile alone.

    H3 ids are sorted before they are numbered, which is what makes cell ids
    reproducible: the same profile gives the same grid, so a rebuild does not
    silently renumber a Dataset the API has positions for.
    """
    polygon = h3.LatLngPoly(
        [
            (profile.south, profile.west),
            (profile.south, profile.east),
            (profile.north, profile.east),
            (profile.north, profile.west),
        ]
    )
    hex_ids = sorted(h3.polygon_to_cells(polygon, res=profile.h3_res))
    if not hex_ids:
        raise BakeError(f"the {profile.city} bounding box contains no H3 cells")

    cells = []
    for index, hex_id in enumerate(hex_ids):
        latitude, longitude = h3.cell_to_latlng(hex_id)
        cells.append(fmt.GridCell(id=index, lat=latitude, lon=longitude, h3=hex_id))
    return fmt.Grid(
        format_version=fmt.FORMAT_VERSION,
        grid_version=profile.grid_version,
        grid_shape="hex",
        bbox_wgs84=fmt.BoundingBox(
            south=profile.south,
            north=profile.north,
            west=profile.west,
            east=profile.east,
        ),
        n_cells=len(cells),
        h3_res=profile.h3_res,
        cells=cells,
    )


def commit_grid(folder: Path, grid: fmt.Grid) -> str:
    """Write `grid.json` if the folder has none; verify it if it has.

    A grid is immutable for the lifetime of its folder, because cell ids are
    what a Dataset's rows and columns mean. A folder holding a different grid is
    a different Dataset and wants a different directory.
    """
    canonical = grid.model_dump_json().encode()
    digest = hashlib.sha256(canonical).hexdigest()
    path = folder / "grid.json"

    if path.exists():
        existing = fmt.read_grid(path)
        if existing.grid_version != grid.grid_version:
            raise BakeError(
                f"{path} holds grid {existing.grid_version!r} but this bake "
                f"builds {grid.grid_version!r}. Point the bake at a directory "
                "of its own, or bump the profile's grid_major."
            )
        if existing.model_dump() != grid.model_dump():
            raise BakeError(
                f"{path} holds a different grid under the same version "
                f"{grid.grid_version!r}. Grids are immutable per version: "
                "bump the profile's grid_major."
            )
        # The same grid, possibly formatted differently on disk. Left alone,
        # and reported by the bytes that are there, because that is what the
        # manifest's digest has to match.
        return hashlib.sha256(path.read_bytes()).hexdigest()

    _write_atomically(path, lambda temporary: temporary.write_bytes(canonical))
    return digest


# --- publishing ---------------------------------------------------------


def _write_atomically(path: Path, write: Callable[[Path], None]) -> None:
    """Write `path` by writing beside it and renaming on top.

    The rename is the publication. A reader holding the previous file keeps
    reading it - the mapping follows the inode, not the name - so a three-hour
    bake never hands anybody a half-written matrix.
    """
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        write(temporary)
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise BakeError(f"cannot write {path}: {error}") from error
    return path.stat().st_size


# --- matrices -----------------------------------------------------------


def build_hash(inputs: fmt.BuildInputs) -> str:
    """A short digest of everything that decided this matrix's bytes.

    It names the file, so a matrix whose inputs have not changed keeps its name
    and a bake that was interrupted can pick the finished ones back up. Nothing
    about the run itself goes in - a rebuild from the same inputs is the same
    matrix.
    """
    canonical = json.dumps(
        inputs.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(canonical).hexdigest()[:12]


def _build_inputs(
    *,
    mode: fmt.MatrixMode,
    grid_version: str,
    engine_version: str,
    osm_sha256: str,
    gtfs_sha256: str,
    departure: datetime,
    window_min: int,
) -> fmt.BuildInputs:
    transit = mode is fmt.MatrixMode.TRANSIT
    return fmt.BuildInputs(
        format_version=fmt.FORMAT_VERSION,
        profile_version=fmt.PROFILE_VERSION,
        engine=ENGINE,
        engine_version=engine_version,
        grid_version=grid_version,
        mode=mode,
        max_seconds=fmt.MAX_SECONDS_PER_MODE[mode],
        snap_to_network=True,
        percentiles=fmt.PERCENTILES,
        osm_sha256=osm_sha256,
        gtfs_sha256=gtfs_sha256 if transit else None,
        departure=departure if transit else None,
        departure_window_min=window_min if transit else None,
    )


def describe_matrix(
    *,
    file: str,
    inputs: fmt.BuildInputs,
    matrix: np.ndarray,
    timezone: str,
    wall_seconds: float | None,
) -> fmt.MatrixEntry:
    """A manifest entry, measured off the matrix it describes.

    Measured rather than carried alongside: the entry and the array cannot
    disagree, and there is no second code path on which a field goes missing.
    That is the defect this whole move was for.
    """
    routed = matrix != fmt.UNREACHABLE
    transit = inputs.mode is fmt.MatrixMode.TRANSIT
    return fmt.MatrixEntry(
        file=file,
        build_hash=build_hash(inputs),
        build_inputs=inputs,
        shape=(matrix.shape[0], matrix.shape[1]),
        aggregation=fmt.Aggregation(kind="percentile", percentiles=fmt.PERCENTILES),
        coverage_pct=round(checks.coverage_pct(matrix), 2),
        min_seconds=int(matrix[routed].min()) if routed.any() else None,
        max_seconds=int(matrix[routed].max()) if routed.any() else None,
        wall_seconds=None if wall_seconds is None else round(wall_seconds, 1),
        departure=inputs.departure if transit else None,
        departure_window_min=inputs.departure_window_min if transit else None,
        timezone=timezone if transit else None,
    )


# --- inputs -------------------------------------------------------------


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _source(path: Path) -> fmt.SourceFile:
    """An input, recorded by content as well as by name.

    The digest is what a build hash is folded from and what makes "which
    extract produced this travel time" answerable; the path on its own would
    not survive the file being replaced.
    """
    digest = _digest(path)
    return fmt.SourceFile(
        path=str(path),
        sha256=digest,
        sha256_short=digest[:8],
        size_bytes=path.stat().st_size,
    )


def _require(path: Path, what: str) -> None:
    if not path.is_file():
        raise BakeError(f"no {what} at {path}")


# --- the bake -----------------------------------------------------------


def bake(
    *,
    osm: Path,
    gtfs: Path,
    into: Path,
    min_transit_coverage_pct: float,
    profile: BakeProfile = PROFILE,
) -> BakeReport:
    """Build a Dataset in `into` and publish it, or raise and publish nothing."""
    started = time.monotonic()
    _require(osm, "OSM extract")
    _require(gtfs, "transit feed")
    into.mkdir(parents=True, exist_ok=True)

    logger.info(
        "bake: %s over %s from %s + %s",
        profile.grid_version,
        (profile.south, profile.north, profile.west, profile.east),
        osm,
        gtfs,
    )

    sources = {"osm": _source(osm), "gtfs": _source(gtfs)}
    logger.info(
        "inputs: osm=%s gtfs=%s",
        sources["osm"].sha256_short,
        sources["gtfs"].sha256_short,
    )

    calendar = feed.read_service_calendar(gtfs)
    week = feed.reference_week(calendar)
    moments = feed.departures_for(week, calendar)
    timezone = str(calendar.timezone)
    # The engine insists on a departure even for a mode that has no timetable to
    # read, so a static mode gets the earliest weekday one. It is not recorded
    # in that matrix's build inputs, because free-flow street speeds do not
    # depend on it and it therefore does not decide a single byte.
    static_departure = week.moment(
        min(fmt.departures_of(fmt.DayClass.WEEKDAY), key=lambda one: one.hour),
        calendar.timezone,
    )

    grid = build_grid(profile)
    grid_sha = commit_grid(into, grid)
    logger.info("grid: %d cells, sha=%s", grid.n_cells, grid_sha[:8])

    network = engine.Network(osm, gtfs, grid)
    engine_version = engine.version()

    entries: dict[str, fmt.MatrixEntry] = {}
    built: list[str] = []
    reused: list[str] = []

    for key in fmt.MATRIX_KEYS:
        mode = fmt.mode_of(key)
        inputs = _build_inputs(
            mode=mode,
            grid_version=profile.grid_version,
            engine_version=engine_version,
            osm_sha256=sources["osm"].sha256,
            gtfs_sha256=sources["gtfs"].sha256,
            departure=moments.get(key, static_departure),
            window_min=profile.departure_window_min,
        )
        name = f"{key}__{build_hash(inputs)}.npy"
        path = into / name

        matrix = _finished_matrix(path, grid.n_cells)
        if matrix is not None:
            logger.info("%s: reusing %s", key, name)
            wall = None
            reused.append(key)
        else:
            at = time.monotonic()
            matrix = network.route(
                mode,
                inputs.departure or static_departure,
                profile.departure_window_min,
            )
            wall = time.monotonic() - at
            _write_atomically(path, _matrix_writer(matrix))
            built.append(key)

        entries[key] = describe_matrix(
            file=name,
            inputs=inputs,
            matrix=matrix,
            timezone=timezone,
            wall_seconds=wall,
        )
        logger.info(
            "%s: %s, coverage %.1f%%%s",
            key,
            name,
            entries[key].coverage_pct,
            f", {wall:.0f}s" if wall else "",
        )

    manifest = fmt.Manifest(
        format_version=fmt.FORMAT_VERSION,
        city=profile.city,
        grid_version=profile.grid_version,
        built_at=datetime.now(calendar.timezone),
        grid=fmt.ManifestGrid(
            file="grid.json",
            sha256=grid_sha,
            n_cells=grid.n_cells,
            grid_shape="hex",
        ),
        sources=sources,
        matrix_conventions=fmt.CONVENTIONS,
        matrices=entries,
    )

    publish_dataset(into, manifest, min_transit_coverage_pct)

    report = BakeReport(
        folder=into,
        grid_version=profile.grid_version,
        n_cells=grid.n_cells,
        built=tuple(built),
        reused=tuple(reused),
        bytes_on_disk=_dataset_size(into, manifest),
        seconds=time.monotonic() - started,
    )
    _log_unreferenced(into, manifest)
    return report


def _dataset_size(folder: Path, manifest: fmt.Manifest) -> int:
    """How much the published Dataset is, on disk.

    What the Dataset weighs rather than what this run happened to write: a run
    that reused every matrix wrote almost nothing, and "0 bytes" is not what an
    operator is asking when they ask how big it came out.
    """
    files = [entry.file for entry in manifest.matrices.values()]
    files += [manifest.grid.file, "manifest.json"]
    return sum((folder / name).stat().st_size for name in files)


def _matrix_writer(matrix: np.ndarray) -> Callable[[Path], None]:
    """Writing `matrix` to wherever it is handed, for the atomic write to place.

    Through an open handle, because `np.save` given a *path* appends `.npy` to
    it - and the path it is handed here is a temporary name that already ends
    in one, so the file would land beside the name the rename then looks for.
    """

    def write(path: Path) -> None:
        with path.open("wb") as handle:
            np.save(handle, matrix, allow_pickle=False)

    return write


def _finished_matrix(path: Path, n_cells: int) -> np.ndarray | None:
    """A finished matrix of this exact build, if the folder already has one.

    Filenames are content-addressed, so a file under this name was built from
    these inputs and is the same matrix. Nothing half-written can be here:
    every matrix is written beside its name and renamed on top.
    """
    if not path.exists():
        return None
    try:
        matrix = np.load(path, mmap_mode="r")
    except (OSError, ValueError):
        logger.warning("%s is not readable; rebuilding it", path.name)
        return None
    if matrix.shape != (n_cells, n_cells) or matrix.dtype != np.uint16:
        logger.warning("%s does not match the grid; rebuilding it", path.name)
        return None
    return matrix


def publish_dataset(
    folder: Path, manifest: fmt.Manifest, min_transit_coverage_pct: float
) -> None:
    """Read the Dataset back as the reader would, then publish it.

    The manifest goes to disk under a staged name, is loaded through the same
    definition `rendezvous` loads with, and is renamed into place only if that
    worked and both checks passed. A Dataset that would not load in the API
    fails here instead, in the job, which is the point of the two sides sharing
    one format.
    """
    staged = folder / STAGED_MANIFEST
    _write_atomically(
        staged,
        lambda temporary: temporary.write_text(manifest.model_dump_json(indent=2)),
    )
    try:
        metadata = fmt.load_metadata(folder, manifest_name=STAGED_MANIFEST)
        matrices = fmt.open_matrices(folder, metadata.manifest)
        checks.check_transit_coverage(
            metadata, matrices, floor_pct=min_transit_coverage_pct
        )
        checks.check_saturday_differs_from_sunday(matrices)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise

    os.replace(staged, folder / "manifest.json")
    logger.info("published %s", folder / "manifest.json")


def _log_unreferenced(folder: Path, manifest: fmt.Manifest) -> None:
    """Name the matrices of earlier bakes still in the folder.

    Not deleted: a reader that has not reopened yet is still mapping them, and
    which of those are safe to remove is an operator's call rather than a
    running job's.
    """
    live = {entry.file for entry in manifest.matrices.values()}
    stale = sorted(p.name for p in folder.glob("*.npy") if p.name not in live)
    if stale:
        logger.info(
            "%d superseded matrix file(s) left in place: %s",
            len(stale),
            ", ".join(stale),
        )
