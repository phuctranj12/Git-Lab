from __future__ import annotations

import logging
import time

import redis

from app.core.errors import TooManyRequests
from app.core.redis import get_redis

log = logging.getLogger(__name__)


def hit(bucket: str, ident: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
    """Fixed-window counter trên Redis (chạy được nhiều worker/replica).

    Trả (allowed, retry_after). Redis lỗi → cho qua (fail-open) để không sập login/registry,
    nhưng ghi log cảnh báo.
    """
    if limit <= 0:
        return True, 0
    window = int(time.time()) // window_seconds
    key = f"rl:{bucket}:{ident}:{window}"
    try:
        pipe = get_redis().pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds + 5)
        count, _ = pipe.execute()
    except redis.RedisError:
        log.warning("rate limiter unavailable (redis)", extra={"extra_data": {"bucket": bucket}})
        return True, 0
    if int(count) > limit:
        retry = window_seconds - (int(time.time()) % window_seconds)
        return False, max(retry, 1)
    return True, 0


def enforce(bucket: str, ident: str, limit: int, window_seconds: int = 60) -> None:
    allowed, retry = hit(bucket, ident, limit, window_seconds)
    if not allowed:
        raise TooManyRequests(retry_after=retry)
