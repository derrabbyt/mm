from fastapi import APIRouter
from redis_fastapi import AsyncRedisDep

from ..core.exceptions import (
    MemberCreateError,
    MemberLoadError,
    MemberNotFoundError,
    MembersLoadError,
    MemberUpdateError,
    default_responses,
    responses,
)
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
async def get_members(redis: AsyncRedisDep) -> list[MeetupMember]:
    return await members.get_members(redis)


@router.get(
    "/members/{member_id}",
    operation_id="getMember",
    responses=responses(MemberNotFoundError, MemberLoadError),
)
async def get_member(member_id: str, redis: AsyncRedisDep) -> MeetupMember:
    return await members.get_member(redis, member_id)


@router.post(
    "/members",
    operation_id="addMember",
    responses=responses(MemberCreateError),
)
async def add_member(
    data: AddMeetupMemberRequest, redis: AsyncRedisDep
) -> MeetupMember:
    return await members.add_member(redis, data)


@router.put(
    "/members/{member_id}",
    operation_id="updateMember",
    responses=responses(MemberNotFoundError, MemberLoadError, MemberUpdateError),
)
async def update_member(
    member_id: str, data: UpdateMeetupMemberRequest, redis: AsyncRedisDep
) -> MeetupMember:
    return await members.update_member(redis, member_id, data)
