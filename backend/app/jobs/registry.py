import importlib
from collections.abc import Callable

JOBS: dict[str, str] = {
    "scrape-events": "app.jobs.scrape_events:scrape_events",
    "ingest-geodata": "app.modules.geodata.jobs:ingest_geodata",
    "build-pois": "app.modules.poi.jobs:build_pois",
    "bake-matrices": "app.modules.matrix.jobs:bake_matrices",
}


def load(name: str) -> Callable[[], None]:
    target = JOBS[name]
    module_name, _, attribute = target.partition(":")
    return getattr(importlib.import_module(module_name), attribute)
