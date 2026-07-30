"""Redis read/write of Person objects.

Mechanics only: this module knows how a Person is stored in Redis, not when it
should be. Cache-aside policy lives in services/members.py.

NOTE: while there is no SQL layer yet, this is also the durable store, so
nothing survives a Redis flush. Once models/ + db/ exist, the durable copy moves
there and these functions become a pure cache.
"""

from redis.asyncio import Redis

from ..core.exceptions import (
    MemberCreateError,
    MemberLoadError,
    MembersLoadError,
    MemberUpdateError,
)
from ..schemas.member import MeetupMember as Person
from . import keys


async def next_id(redis: Redis) -> str:
    """Mint a new person id."""
    try:
        return str(await redis.incr(keys.PEOPLE_SEQ))
    except Exception as e:
        raise MemberCreateError() from e


async def put(redis: Redis, person: Person) -> None:
    """Write a person and make sure their id is in the index."""
    async with redis.pipeline(transaction=True) as pipe:
        try:
            pipe.set(keys.person(person.id), person.model_dump_json())
            pipe.sadd(keys.PEOPLE_IDS, person.id)
            await pipe.execute()
        except Exception as e:
            raise MemberUpdateError(member_id=person.id) from e


async def get(redis: Redis, person_id: str) -> Person | None:
    try:
        raw = await redis.get(keys.person(person_id))
    except Exception as e:
        raise MemberLoadError(member_id=person_id) from e

    if raw is None:
        return None
    return Person.model_validate_json(raw)


async def list_all(redis: Redis) -> list[Person]:
    try:
        ids = await redis.smembers(keys.PEOPLE_IDS)
        if not ids:
            return []

        person_keys = [
            keys.person(i.decode() if isinstance(i, bytes) else i) for i in ids
        ]
        raws = await redis.mget(person_keys)

        people = [Person.model_validate_json(raw) for raw in raws if raw is not None]
        people.sort(key=lambda p: int(p.id))
        return people
    except Exception as e:
        raise MembersLoadError() from e
