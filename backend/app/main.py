from fastapi import FastAPI, APIRouter

app = FastAPI(
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url="/api/redoc",
)

api = APIRouter(prefix="/api")

@api.get("/")
async def root():
    return {"message": "Hello World"}

@api.get("/health")
async def health():
    return {"message": "Service is running"}

@api.post("/items/")
async def create_item(item: dict):
    print(f"Received item: {item}")
    return {"item": item["brand"]}

@api.get("/item/{item_id}")
async def get_item(item_id: int):
    return {"should get item for id: ": item_id}

app.include_router(api)
