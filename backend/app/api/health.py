from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import __version__
from app.core.metrics import BUILD_QUEUE_DEPTH
from app.services import queue_service
from app.services.health_service import readiness

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/health/ready")
def ready() -> JSONResponse:
    result = readiness()
    return JSONResponse(result, status_code=200 if result["ready"] else 503)


@router.get("/api/health")
def api_health() -> JSONResponse:
    """Alias theo chuẩn HAWEE (compose healthcheck / monitoring gọi /api/health)."""
    return ready()


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics() -> PlainTextResponse:
    depth = queue_service.queue_depth()
    if depth is not None:
        BUILD_QUEUE_DEPTH.set(depth)
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)
