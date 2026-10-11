import logging
from datetime import datetime
from pathlib import Path

import h3
import numpy as np

from ...core import dataset as dataset_format
from ...core.config import settings
from ...core.contracts import Position
from ...core.enums import TravelMode

logger = logging.getLogger(__name__)


class Dataset:
    def __init__(self, folder: Path):
        self.metadata = dataset_format.load_metadata(folder)
        self.matrices: dict[str, np.ndarray] = dataset_format.open_matrices(
            folder, self.metadata.manifest
        )

        self.n_cells = self.metadata.n_cells
        self.h3_res = self.metadata.h3_res
        self.timezone = self.metadata.timezone

        self.cap_seconds = {
            key: entry.cap_seconds
            for key, entry in self.metadata.manifest.matrices.items()
        }

        cells = self.metadata.grid.cells
        self._h3_to_id = {cell.h3: cell.id for cell in cells}
        self._latlng = np.array([[cell.lat, cell.lon] for cell in cells])

    def snap(self, latitude: float, longitude: float) -> int:
        cell = h3.latlng_to_cell(latitude, longitude, self.h3_res)
        return self._h3_to_id.get(cell, -1)

    def position(self, cell_id: int) -> Position:
        latitude, longitude = self._latlng[cell_id]
        return Position(latitude=float(latitude), longitude=float(longitude))


dataset = Dataset(settings.dataset_dir)
logger.info(
    "Loaded travel-time dataset %s (%s cells, matrices: %s)",
    dataset.metadata.manifest.grid_version,
    dataset.n_cells,
    ", ".join(dataset.matrices),
)


def to_local(when: datetime) -> datetime:
    if when.tzinfo is None:
        when = when.replace(tzinfo=dataset.timezone)
    return when.astimezone(dataset.timezone)


def pick_transit_key(when: datetime) -> str:
    return dataset_format.pick_transit_key(to_local(when))


def matrix_keys(mode: TravelMode, transit_key: str) -> tuple[str, ...]:
    if mode is TravelMode.TRANSIT:
        return ("walk", transit_key)
    return (dataset_format.STATIC_MATRIX_KEY[mode],)
