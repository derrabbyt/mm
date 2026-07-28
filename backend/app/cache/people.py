"""Redis read/write of Person objects.

Mechanics only: this module knows how a Person is stored in Redis, not when it
should be. Cache-aside policy lives in services/members.py.

NOTE: while there is no SQL layer yet, this is also the durable store, so
nothing survives a Redis flush. Once models/ + db/ exist, the durable copy moves
there and these functions become a pure cache.
"""

from redis.asyncio import Redis

from ..schemas.member import MeetupMember as Person
from . import keys


async def next_id(redis: Redis) -> str:
    """Mint a new person id.

    Belongs in the DB once a real persistence layer exists (a sequence or UUID) -
    losing people:seq would restart ids and collide with existing rows.
    """
    return str(await redis.incr(keys.PEOPLE_SEQ))


async def put(redis: Redis, person: Person) -> None:
    """Write a person and make sure their id is in the index."""
    # Pipeline so the blob and the id-set entry land together.
    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(keys.person(person.id), person.model_dump_json())
        pipe.sadd(keys.PEOPLE_IDS, person.id)
        await pipe.execute()


async def get(redis: Redis, person_id: str) -> Person | None:
    raw = await redis.get(keys.person(person_id))
    if raw is None:
        return None
    return Person.model_validate_json(raw)


async def list_all(redis: Redis) -> list[Person]:
    ids = await redis.smembers(keys.PEOPLE_IDS)
    if not ids:
        return []

    # MGET keeps this a single round trip regardless of how many people exist.
    person_keys = [keys.person(i.decode() if isinstance(i, bytes) else i) for i in ids]
    raws = await redis.mget(person_keys)

    people = [Person.model_validate_json(raw) for raw in raws if raw is not None]
    people.sort(key=lambda p: int(p.id))
    return people
