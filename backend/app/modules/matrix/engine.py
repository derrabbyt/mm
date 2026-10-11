import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from ...core import dataset as fmt

logger = logging.getLogger(__name__)


def version() -> str:
    import r5py

    return r5py.__version__


def to_uint16(frame: Any, n_cells: int, cap_seconds: int) -> np.ndarray:
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
    def __init__(self, osm: Path, gtfs: Path, grid: fmt.Grid):
        import geopandas as gpd
        import r5py
        from shapely.geometry import Point

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
