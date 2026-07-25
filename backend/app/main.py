from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.get("/health")
async def health():
    return {"message": "Service is running"}

@app.post("/items/")
async def create_item(item: dict):
    print(f"Received item: {item}")
    return {"item": item["brand"]}

@app.get("/item/{item_id}")
async def get_item(item_id: int):
    return {"should get item for id: ": item_id}


