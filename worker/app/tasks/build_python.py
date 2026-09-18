from __future__ import annotations

from app.celery_app import celery_app
from app.runners import python_runner


@celery_app.task(name="toolhub.build", ignore_result=True)
def build(build_id: str) -> dict:
    """Task duy nhất cho build Python. Idempotent: build không còn QUEUED → API trả 409 → bỏ qua."""
    return python_runner.execute(build_id)
