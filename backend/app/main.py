from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api import health, registry
from app.api.v1 import admin, auth, builds, dashboard, groups, internal, me, meta, packages, projects, users
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import request_id_var, setup_logging, user_id_var
from app.core.metrics import HTTP_DURATION, HTTP_REQUESTS
from app.core.rate_limit import hit

log = logging.getLogger("toolhub.http")

_REQUEST_ID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def _error(status: int, code: str, message: str, details: dict | None = None,
           headers: dict[str, str] | None = None) -> JSONResponse:
    body: dict = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    rid = request_id_var.get()
    if rid:
        body["error"]["request_id"] = rid
    return JSONResponse(body, status_code=status, headers=headers)


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    problems = settings.validate_for_runtime()
    if problems:
        raise RuntimeError("Cấu hình không hợp lệ: " + "; ".join(problems))

    app = FastAPI(title=settings.app_name, version=__version__, docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json")

    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if 0 < len(incoming) <= 64 and set(incoming) <= _REQUEST_ID_OK else uuid.uuid4().hex
        request_id_var.set(rid)
        user_id_var.set(None)
        path = request.url.path
        if path.startswith("/api/") and not path.startswith("/api/v1/internal") and path != "/api/health":
            ip = request.client.host if request.client else "unknown"
            allowed, retry = hit("api", ip, settings.api_rate_limit_per_minute)
            if not allowed:
                return _error(429, "RATE_LIMITED", "Quá nhiều yêu cầu, thử lại sau", headers={"Retry-After": str(retry)})
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            HTTP_REQUESTS.labels(request.method, route_path, str(status)).inc()
            HTTP_DURATION.labels(request.method, route_path).observe(elapsed)
            if not path.startswith(("/health", "/metrics")):
                log.info("%s %s %s", request.method, path, status,
                         extra={"method": request.method, "path": path, "status_code": status,
                                "duration_ms": round(elapsed * 1000, 1)})
        response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg")} for e in exc.errors()]
        return _error(422, "VALIDATION_ERROR", "Dữ liệu gửi lên không hợp lệ", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "INVALID_CREDENTIALS"}.get(exc.status_code, "HTTP_ERROR")
        return _error(exc.status_code, code, str(exc.detail), headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _error(500, "INTERNAL_ERROR", "Lỗi hệ thống, vui lòng thử lại hoặc báo quản trị viên")

    api_prefix = "/api/v1"
    for r in (auth.router, users.router, me.router, groups.router, projects.router, builds.router, packages.router,
              dashboard.router, admin.router, meta.router, internal.router):
        app.include_router(r, prefix=api_prefix)
    app.include_router(health.router)
    app.include_router(registry.router)
    return app


app = create_app()
