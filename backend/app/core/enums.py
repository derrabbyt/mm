"""Enums used by both a table column and the schemas built from it.

They sit in `core` because `core.contracts` needs them too, and `core` cannot
import from `modules`.
"""

from enum import StrEnum


class TravelMode(StrEnum):
    WALK = "walk"
    BICYCLE = "bicycle"
    CAR = "car"
    TRANSIT = "transit"
