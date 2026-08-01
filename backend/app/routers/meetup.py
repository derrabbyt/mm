from fastapi import APIRouter

from ..core.exceptions import (
    MemberCreateError,
    MemberLoadError,
    MemberNotFoundError,
    MembersLoadError,
    MemberUpdateError,
    default_responses,
    responses,
)
from ..db.session import DbSessionDep
from ..schemas.member import (
    AddMeetupMemberRequest,
    MeetupMember,
    UpdateMeetupMemberRequest,
)
from ..services import members

router = APIRouter(
    prefix="/api/meetup",
    tags=["meetup"],
    responses=default_responses(),
)


@router.get(
    "/members",
    operation_id="getMembers",
    responses=responses(MembersLoadError),
)
def get_members(db: DbSessionDep) -> list[MeetupMember]:
    return members.get_members(db)


@router.get(
    "/members/{member_id}",
    operation_id="getMember",
    responses=responses(MemberNotFoundError, MemberLoadError),
)
def get_member(member_id: str, db: DbSessionDep) -> MeetupMember:
    return members.get_member(db, member_id)


@router.post(
    "/members",
    operation_id="addMember",
    responses=responses(MemberCreateError),
)
def add_member(data: AddMeetupMemberRequest, db: DbSessionDep) -> MeetupMember:
    return members.add_member(db, data)


@router.put(
    "/members/{member_id}",
    operation_id="updateMember",
    responses=responses(MemberNotFoundError, MemberLoadError, MemberUpdateError),
)
def update_member(
    member_id: str, data: UpdateMeetupMemberRequest, db: DbSessionDep
) -> MeetupMember:
    return members.update_member(db, member_id, data)
