"""Shared Redis connection helpers."""
from functools import lru_cache
import os


@lru_cache(maxsize=1)
def get_redis_client():
    """Return one connection-pooled Redis client per process."""
    import redis

    return redis.StrictRedis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        socket_connect_timeout=float(os.environ.get("REDIS_CONNECT_TIMEOUT_SECONDS", "1")),
        socket_timeout=float(os.environ.get("REDIS_SOCKET_TIMEOUT_SECONDS", "1")),
        health_check_interval=30,
    )
