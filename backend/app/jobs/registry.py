"""The jobs this application can run, by name.

Targets are strings, not imported functions, so the runner imports only the
module it was asked for - otherwise a scraper container would load the mmapped
travel-time dataset too.
"""

import importlib
from collections.abc import Callable

JOBS: dict[str, str] = {
    "scrape-events": "app.modules.events.jobs:scrape_events",
    "ingest-geodata": "app.modules.geodata.jobs:ingest_geodata",
    "build-pois": "app.modules.poi.jobs:build_pois",
    "bake-matrices": "app.modules.matrix.jobs:bake_matrices",
}


def load(name: str) -> Callable[[], None]:
    """Import and return the job `name` refers to."""
    target = JOBS[name]
    module_name, _, attribute = target.partition(":")
    return getattr(importlib.import_module(module_name), attribute)
