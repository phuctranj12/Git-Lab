from __future__ import annotations

import logging
import uuid

from celery import Celery

from app.core.config import get_settings

log = logging.getLogger(__name__)

BUILD_TASK = "toolhub.build"
BUILD_QUEUE = "builds"

_celery: Celery | None = None


def get_celery() -> Celery:
    """Backend chỉ là *producer*: gửi task theo tên, không import code worker."""
    global _celery
    if _celery is None:
        url = get_settings().redis_url
        _celery = Celery("toolhub-api", broker=url)
        _celery.conf.update(task_serializer="json", accept_content=["json"], broker_connection_retry_on_startup=True,
                            broker_transport_options={"visibility_timeout": 3600})
    return _celery


def enqueue_build(build_id: uuid.UUID) -> bool:
    try:
        get_celery().send_task(BUILD_TASK, args=[str(build_id)], queue=BUILD_QUEUE)
        return True
    except Exception:  # noqa: BLE001 — build vẫn QUEUED trong DB, job requeue sẽ gửi lại
        log.exception("enqueue build failed", extra={"build_id": str(build_id)})
        return False


def queue_depth() -> int | None:
    from app.core.redis import get_redis

    try:
        return int(get_redis().llen(BUILD_QUEUE))
    except Exception:  # noqa: BLE001
        return None
