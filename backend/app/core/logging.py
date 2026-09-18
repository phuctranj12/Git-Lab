from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)

# Không bao giờ log các khoá này (spec 29.1, 31).
_SENSITIVE_KEYS = {"password", "token", "authorization", "secret", "refresh_token", "access_token", "cookie"}


def _scrub(value: object) -> object:
    if isinstance(value, dict):
        return {k: ("***" if any(s in str(k).lower() for s in _SENSITIVE_KEYS) else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        uid = user_id_var.get()
        if uid:
            payload["user_id"] = uid
        for key in ("action", "build_id", "project_id", "status_code", "path", "method", "duration_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        extra = getattr(record, "extra_data", None)
        if extra:
            payload["data"] = _scrub(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn access log trùng với middleware log của app → tắt bớt.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
