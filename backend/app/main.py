import asyncio
import random
from typing import AsyncIterable

from fastapi import FastAPI, APIRouter
from fastapi.params import Depends
from fastapi.sse import EventSourceResponse
from fastapi.middleware.cors import CORSMiddleware
from redis_fastapi import FastAPIRedis, cache

from .config import settings
from .model.item import Item
from .model.test import TestDataRequest, TestDataResponse

app = FastAPI(
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api",
                tags=["main_test_api"])

# for open api generator to create a model of Item, it needs to be  
# used in an endpoint that is included in the schema. Since SSE is
# exluded from the schema, we need to create a dummy endpoint that 
# returns an Item. In prod this is probably not a problem since we 
# would have other endpoints that return an Item(s). Otherwise it 
# would be necessary to create the model in the frontend explicitly.
@api.get("/item", response_model=Item)
async def get_item() -> Item:
    return Item(name="Example Item", description="This is an example item.")

@api.post("/test", operation_id="testEndpoint")
async def test_endpoint(data: TestDataRequest)-> TestDataResponse:
    print("Received data:", data)
    rnd = random.randint(1, 100)
    print("Random value:", rnd)
    return {"calculated_value": rnd, "status": "success 1234"}

# sse
@api.get("/sse", operation_id="sseEndpoint", include_in_schema=False, response_class=EventSourceResponse) 
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
FastAPIRedis(app).lifespan().caching()

@api.get(
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

app.include_router(api)


