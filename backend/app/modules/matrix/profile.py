from dataclasses import dataclass


@dataclass(frozen=True)
class BakeProfile:
    city: str
    south: float
    north: float
    west: float
    east: float
    h3_res: int
    grid_major: int
    departure_window_min: int

    @property
    def grid_version(self) -> str:
        return f"v{self.grid_major}_h3r{self.h3_res}"


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
