"""What this deployment bakes: the area, the grid, the departure window.

Next to the code rather than in the environment. These values define what a
Dataset *is*: they change together, `grid_version` is derived from two of them,
and changing any of them means a different set of cells and a folder of its own.
They want reviewing in a diff, not setting in a shell.

The bounding box especially. The shipped Dataset was baked with one passed on a
command line in another repo, and the only surviving record of it is the first
line of a log file - the default in that repo's code is a different box. Stating
it here is most of the reason this file exists.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BakeProfile:
    """One bake's area and grid. Immutable, and named by what it produces."""

    city: str
    #: south, north, west, east, in WGS84 degrees.
    south: float
    north: float
    west: float
    east: float
    h3_res: int
    #: Bumped whenever the cell set changes, so a reader can tell two Datasets
    #: apart by name and a grid is never silently replaced under one.
    grid_major: int
    #: How long a window around a Departure the router samples, in minutes. The
    #: travel time recorded is the median across it.
    departure_window_min: int

    @property
    def grid_version(self) -> str:
        return f"v{self.grid_major}_h3r{self.h3_res}"


#: Vienna, as the shipped Dataset was actually baked.
PROFILE = BakeProfile(
    city="vienna",
    south=48.11,
    north=48.34,
    west=16.17,
    east=16.58,
    h3_res=9,
    grid_major=2,
    departure_window_min=10,
)
