from __future__ import annotations

import logging

from app.api_client import ApiClient
from app.celery_app import celery_app
from app.runners import python_runner

log = logging.getLogger(__name__)


@celery_app.task(name="toolhub.maintenance", ignore_result=True)
def maintenance() -> dict:
    """Định kỳ: container build mồ côi (local) + token hết hạn, log quá hạn, cache upstream, build kẹt (API)."""
    removed = python_runner.cleanup_orphans()
    with ApiClient() as api:
        result = api.maintenance()
    result["orphan_containers_removed"] = removed
    log.info("maintenance: %s", result)
    return result
