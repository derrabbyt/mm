import asyncio
import random
from typing import AsyncIterable

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse
from redis_fastapi import cache
from rq import Queue
from rq.job import Job

from ..core.config import settings
from ..schemas.test import Item, TestDataRequest, TestDataResponse
from ..services.jobs import expensive_job

router = APIRouter(prefix="/api", tags=["main_test_api"])

queue = Queue(
    "default",
    connection=settings.redis,  # we can use the redis connection from settings here, sdk doesnt provide it like that i guess
)


# for open api generator to create a model of Item, it needs to be
# used in an endpoint that is included in the schema. Since SSE is
# exluded from the schema, we need to create a dummy endpoint that
# returns an Item. In prod this is probably not a problem since we
# would have other endpoints that return an Item(s). Otherwise it
# would be necessary to create the model in the frontend explicitly.
@router.get("/item", response_model=Item)
async def get_item() -> Item:
    return Item(name="Example Item", description="This is an example item.")

@router.post("/test", operation_id="testEndpoint")
async def test_endpoint(data: TestDataRequest)-> TestDataResponse:
    print("Received data:", data)
    rnd = random.randint(1, 100)
    print("Random value:", rnd)
    return {"calculated_value": rnd, "status": "success 1234"}

# sse
@router.get("/sse", operation_id="sseEndpoint", include_in_schema=False, response_class=EventSourceResponse)
async def sse_endpoint() -> AsyncIterable[Item]:
    items = [
        Item(name="Plumbus", description="A multi-purpose household device."),
        Item(name="Portal Gun", description="A portal opening device."),
        Item(name="Meeseeks Box", description="A box that summons a Meeseeks."),
    ]
    while True:
        for item in items:
            yield item
            await asyncio.sleep(1)


# redis chaching
@router.get(
    "/expensive/{item_id}",
    dependencies=[
        Depends(cache(ttl=30))
    ]
)
async def expensive(item_id: int):
    print(f"Actually executing endpoint for {item_id}")

    # Pretend this is an expensive DB/API call.
    await asyncio.sleep(2)

    return {
        "item_id": item_id,
        "value": random.randint(1, 1000),
    }


# rq
@router.post("/jobs/{item_id}")
async def create_job(item_id: int):
    job = queue.enqueue(
        expensive_job,
        item_id,
    )

    return {
        "job_id": job.id,
        "status": job.get_status(),
    }

@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = Job.fetch(
        job_id,
        connection=settings.redis,
    )

    return {
        "job_id": job.id,
        "status": job.get_status(),
        "result": job.return_value(),
    }
