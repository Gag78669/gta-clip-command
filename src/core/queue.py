"""Shared Redis queue and event-bus connection helpers."""
from functools import lru_cache
import redis


@lru_cache(maxsize=4)
def get_redis_client(url: str):
    return redis.Redis.from_url(url, decode_responses=True)
