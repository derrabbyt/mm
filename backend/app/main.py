import random

from fastapi import FastAPI, APIRouter

from .model.test import TestDataRequest, TestDataResponse

app = FastAPI(
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url="/api/redoc",
)

api = APIRouter(prefix="/api",
                tags=["main_test_api"])

@api.get("/")
async def root():
    return {"message": "Hello World"}

@api.post("/test",
          operation_id="testEndpoint")

async def test_endpoint(data: TestDataRequest)-> TestDataResponse:
    print("Received data:", data)
    rnd = random.randint(1, 100)
    print("Random value:", rnd)
    return {"calculated_value": rnd, "status": "success 1234"}


app.include_router(api)
