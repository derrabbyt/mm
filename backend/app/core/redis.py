from functools import lru_cache

from redis import Redis

from .config import settings


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    return Redis.from_url(settings.redis_url)
