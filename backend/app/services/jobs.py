import logging
import time

logger = logging.getLogger(__name__)


def expensive_job(item_id: int) -> dict:
    logger.debug("Worker started job for %s", item_id)

    # Simulate expensive work.
    time.sleep(8)

    result = {
        "item_id": item_id,
        "value": item_id * 10,
    }

    logger.debug("Worker finished job for %s", item_id)

    return result
