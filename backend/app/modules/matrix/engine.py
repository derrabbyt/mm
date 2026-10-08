"""The routing engine, behind one small surface.

Everything that knows r5py exists is here: starting it, handing it the grid,
and turning what it answers with into the matrix a Dataset carries. The bake
above it asks for a matrix and gets one.

Kept apart because this is an adapter to an outside thing rather than anything
about Datasets - the same reason `events/sources/` sits beside the module that
uses it - and because importing the engine is not free. Importing it resolves a
Java class path, starts a JVM and would fetch a 64 MB jar if the image did not
carry one, so the imports are inside the functions: a process that merely
imports this module must not do any of that, and only the bake's image installs
the package at all.
"""

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from ...core import dataset as fmt

logger = logging.getLogger(__name__)


def version() -> str:
    """Which engine built a matrix, for the manifest to record."""
    import r5py

    return r5py.__version__


def to_uint16(frame: Any, n_cells: int, cap_seconds: int) -> np.ndarray:
    """The engine's long result as the matrix a Dataset carries.

    It answers in minutes with a blank for unreachable; a Dataset holds whole
    seconds with a sentinel, because that is what fits in the sixteen bits a
    7466-square matrix can afford per cell pair.
    """
    matrix = np.full((n_cells, n_cells), fmt.UNREACHABLE, dtype=np.uint16)
    if len(frame) == 0:
        return matrix
    origins = frame["from_id"].astype(np.int64).to_numpy()
    destinations = frame["to_id"].astype(np.int64).to_numpy()
    minutes = frame["travel_time"].to_numpy(dtype=np.float64)

    routed = ~np.isnan(minutes)
    seconds = np.clip(np.round(minutes[routed] * 60.0), 0, cap_seconds).astype(
        np.uint16
    )
    matrix[origins[routed], destinations[routed]] = seconds
    return matrix


class Network:
    """A loaded street and transit network, and the grid to route over it."""

    def __init__(self, osm: Path, gtfs: Path, grid: fmt.Grid):
        import geopandas as gpd
        import r5py
        from shapely.geometry import Point

        # The engine wants the cells as a frame of points carrying their ids.
        self.points = gpd.GeoDataFrame(
            {"id": [cell.id for cell in grid.cells]},
            geometry=[Point(cell.lon, cell.lat) for cell in grid.cells],
            crs="EPSG:4326",
        )
        self.n_cells = grid.n_cells

        logger.info("loading the routing network from %s + %s", osm.name, gtfs.name)
        at = time.monotonic()
        self._network = r5py.TransportNetwork(str(osm), [str(gtfs)])
        logger.info("network ready in %.1fs", time.monotonic() - at)

    def route(
        self,
        mode: fmt.MatrixMode,
        departure: datetime,
        window_min: int,
    ) -> np.ndarray:
        """One cell-to-cell matrix of travel times on `mode`."""
        import r5py

        modes = {
            fmt.MatrixMode.WALK: [r5py.TransportMode.WALK],
            fmt.MatrixMode.BIKE: [r5py.TransportMode.BICYCLE],
            fmt.MatrixMode.CAR: [r5py.TransportMode.CAR],
            fmt.MatrixMode.TRANSIT: [
                r5py.TransportMode.TRANSIT,
                r5py.TransportMode.WALK,
            ],
        }
        cap = fmt.MAX_SECONDS_PER_MODE[mode]
        arguments: dict[str, Any] = {
            "origins": self.points,
            "destinations": self.points,
            "transport_modes": modes[mode],
            "max_time": timedelta(seconds=cap),
            "snap_to_network": True,
            "percentiles": fmt.PERCENTILES,
            "departure": departure,
        }
        if mode is fmt.MatrixMode.TRANSIT:
            arguments["departure_time_window"] = timedelta(minutes=window_min)
        return to_uint16(
            r5py.TravelTimeMatrix(self._network, **arguments), self.n_cells, cap
        )
