import time


def expensive_job(item_id: int) -> dict:
    print(f"Worker started job for {item_id}")

    # Simulate expensive work.
    time.sleep(8)

    result = {
        "item_id": item_id,
        "value": item_id * 10,
    }

    print(f"Worker finished job for {item_id}")

    return result
