from functools import lru_cache

from redis import Redis

from .config import settings


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    """Process-wide sync Redis client"""
    return Redis.from_url(settings.redis_url)
