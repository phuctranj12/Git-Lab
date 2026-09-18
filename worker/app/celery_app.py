from __future__ import annotations

import logging
import os
import platform
import socket
import threading

from celery import Celery
from celery.signals import worker_ready, worker_shutdown

from app.config import get_settings

BUILD_QUEUE = "builds"

s = get_settings()
celery_app = Celery("toolhub-worker", broker=s.redis_url, include=["app.tasks.build_python", "app.tasks.cleanup"])
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_default_queue=BUILD_QUEUE,
    task_acks_late=True,                 # worker chết giữa build → message được giao lại
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,        # không giữ build hộ worker khác
    worker_concurrency=s.concurrency,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 3600},
    beat_schedule={
        "maintenance": {"task": "toolhub.maintenance", "schedule": s.maintenance_minutes * 60.0,
                        "options": {"queue": BUILD_QUEUE, "expires": 300}},
    },
    timezone="UTC",
)

log = logging.getLogger("toolhub.worker")
# httpx log mỗi request (heartbeat 30s/lần, đẩy log build) → chỉ giữ cảnh báo.
logging.getLogger("httpx").setLevel(logging.WARNING)
_stop = threading.Event()


def _heartbeat_loop() -> None:
    from app.api_client import ApiClient
    from app.runners.python_runner import running_jobs

    while not _stop.is_set():
        try:
            with ApiClient() as api:
                api.heartbeat({"name": s.runner_name, "hostname": socket.gethostname(),
                               "labels": {"os": platform.system().lower(), "arch": platform.machine(),
                                          "python": True, "image": s.builder_image},
                               "version": os.environ.get("TOOLHUB_VERSION", "1.0.0"),
                               "max_concurrent_jobs": s.concurrency, "current_jobs": running_jobs()})
        except Exception as exc:  # noqa: BLE001
            log.warning("heartbeat failed: %s", exc)
        _stop.wait(s.heartbeat_seconds)


@worker_ready.connect
def _on_ready(**_kwargs) -> None:
    threading.Thread(target=_heartbeat_loop, name="runner-heartbeat", daemon=True).start()


@worker_shutdown.connect
def _on_shutdown(**_kwargs) -> None:
    _stop.set()
