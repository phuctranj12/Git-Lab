from __future__ import annotations

import redis

from app.core.config import get_settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True,
                                       socket_timeout=3, socket_connect_timeout=3, health_check_interval=30)
    return _client


def reset_redis() -> None:
    global _client
    _client = None
