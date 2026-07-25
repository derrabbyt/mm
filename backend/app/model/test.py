from pydantic import BaseModel

class TestDataRequest(BaseModel):
    brand: str
    model: str
    year: int

class TestDataResponse(BaseModel):
    calculated_value: float
    status: str