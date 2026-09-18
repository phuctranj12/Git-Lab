from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from functools import lru_cache


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class WorkerSettings:
    api_base_url: str = field(default_factory=lambda: _env("API_BASE_URL", "http://backend:8000").rstrip("/"))
    runner_token: str = field(default_factory=lambda: _env("RUNNER_TOKEN"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://redis:6379/0"))
    runner_name: str = field(default_factory=lambda: _env("RUNNER_NAME") or socket.gethostname())
    concurrency: int = field(default_factory=lambda: int(_env("BUILD_MAX_CONCURRENCY", "2")))
    builder_image: str = field(default_factory=lambda: _env("BUILDER_IMAGE", "toolhub-builder:1.0.0"))
    build_network: str = field(default_factory=lambda: _env("BUILD_NETWORK", "toolhub-build"))
    # URL registry nhìn từ BÊN TRONG container build (qua mạng build nội bộ).
    build_registry_url: str = field(default_factory=lambda: _env("BUILD_REGISTRY_URL", "http://registry:8081/simple"))
    pids_limit: int = field(default_factory=lambda: int(_env("BUILD_PIDS_LIMIT", "1024")))
    storage_opt_size: str = field(default_factory=lambda: _env("BUILD_STORAGE_SIZE", ""))  # vd 5G (cần overlay2+xfs pquota)
    log_flush_seconds: float = field(default_factory=lambda: float(_env("BUILD_LOG_FLUSH_SECONDS", "2")))
    max_log_bytes: int = field(default_factory=lambda: int(_env("BUILD_MAX_LOG_BYTES", str(10 * 1024 * 1024))))
    heartbeat_seconds: int = field(default_factory=lambda: int(_env("RUNNER_HEARTBEAT_SECONDS", "30")))
    maintenance_minutes: int = field(default_factory=lambda: int(_env("MAINTENANCE_INTERVAL_MINUTES", "10")))


@lru_cache
def get_settings() -> WorkerSettings:
    s = WorkerSettings()
    if not s.runner_token:
        raise RuntimeError("RUNNER_TOKEN chưa được đặt cho worker")
    return s
