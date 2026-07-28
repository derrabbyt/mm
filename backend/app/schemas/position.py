from pydantic import BaseModel


class Position(BaseModel):
    latitude: float
    longitude: float
