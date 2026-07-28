from redis.asyncio import Redis

from ..cache import people as members_cache
from ..schemas.member import (
    AddMeetupMemberRequest,
    MeetupMember,
    UpdateMeetupMemberRequest,
)


async def add_member(redis: Redis, data: AddMeetupMemberRequest) -> MeetupMember:
    member_id = await members_cache.next_id(redis)
    member = MeetupMember(id=member_id, name=data.name)
    await members_cache.put(redis, member)
    return member


async def get_member(redis: Redis, member_id: str) -> MeetupMember | None:
    return await members_cache.get(redis, member_id)


async def get_members(redis: Redis) -> list[MeetupMember]:
    return await members_cache.list_all(redis)


async def update_member(
    redis: Redis, member_id: str, data: UpdateMeetupMemberRequest
) -> MeetupMember | None:
    existing = await members_cache.get(redis, member_id)

    if existing is None:
        return None

    updated = MeetupMember(id=member_id, name=data.name, position=data.position)
    await members_cache.put(redis, updated)
    return updated
