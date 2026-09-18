"""Prometheus metrics (spec mục 43) — expose tại /metrics."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_DURATION = Histogram("http_request_duration_seconds", "HTTP request duration", ["method", "route"])
BUILDS_TOTAL = Counter("builds_total", "Builds finished", ["status", "trigger"])
BUILD_DURATION = Histogram("build_duration_seconds", "Build duration",
                           buckets=(10, 30, 60, 120, 300, 600, 900, 1800, 3600))
BUILD_QUEUE_DEPTH = Gauge("build_queue_depth", "Builds waiting in Redis queue")
BUILD_FAILURES = Counter("build_failures_total", "Failed builds", ["error_code"])
PACKAGE_DOWNLOADS = Counter("package_downloads_total", "Package file downloads", ["source"])
UPSTREAM_CACHE_HITS = Counter("upstream_cache_hits_total", "Upstream file served from cache")
UPSTREAM_CACHE_MISSES = Counter("upstream_cache_misses_total", "Upstream file fetched from PyPI")
UPSTREAM_DOWNLOAD_BYTES = Counter("upstream_download_bytes", "Bytes downloaded from upstream")
INTERNAL_PACKAGE_STORAGE = Gauge("internal_package_storage_bytes", "Bytes of internal package files")
