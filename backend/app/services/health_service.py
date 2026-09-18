from __future__ import annotations

import logging

from sqlalchemy import text

from app.core.redis import get_redis
from app.db.session import get_engine
from app.services import git_service, storage_service

log = logging.getLogger(__name__)


def _check(name: str, fn) -> dict:
    try:
        fn()
        return {"name": name, "ok": True}
    except Exception as exc:  # noqa: BLE001
        log.warning("readiness check failed: %s", name, exc_info=True)
        return {"name": name, "ok": False, "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}


def _db() -> None:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


def readiness() -> dict:
    """PostgreSQL, Redis, MinIO, Git storage. PyPI upstream KHÔNG nằm trong readiness (spec 28)."""
    checks = [
        _check("postgresql", _db),
        _check("redis", lambda: get_redis().ping()),
        _check("minio", storage_service.check_ready),
        _check("git_storage", git_service.check_storage_writable),
    ]
    return {"ready": all(c["ok"] for c in checks), "checks": checks}
