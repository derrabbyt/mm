"""What other modules and the job runtime may use from `events`.

`scrape` is here for `app/jobs/`, which composes it with geocoding. The module
cannot reach geocoding itself - every layer that holds a session is forbidden
from knowing another module exists - so the capability is passed in.
"""

from .service import scrape

__all__ = ["scrape"]
