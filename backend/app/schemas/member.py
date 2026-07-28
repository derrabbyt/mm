from pydantic import BaseModel

from .position import Position


class MeetupMember(BaseModel):
    id: str
    name: str
    position: Position | None = None


class AddMeetupMemberRequest(BaseModel):
    name: str


class UpdateMeetupMemberRequest(BaseModel):
    name: str
    position: Position
