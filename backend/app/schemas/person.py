from pydantic import BaseModel

from .position import Position

class Person(BaseModel):
    id: int | None
    name: str
    position: Position
