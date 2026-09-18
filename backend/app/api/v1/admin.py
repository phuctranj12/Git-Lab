from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.deps import AdminPrincipal, DbDep
from app.core.errors import NotFound
from app.db.models import AuditLog, Build, PackageFile, Project, RunnerNode, UpstreamFile, User
from app.db.models.enums import BuildStatus, RunnerStatus
from app.schemas import AuditOut, Page, RunnerOut, RunnerUpdate
from app.services import audit_service, queue_service
from app.services.audit_service import A
from app.services.health_service import readiness

router = APIRouter(prefix="/admin", tags=["admin"])

EXPORT_LIMIT = 100_000


def _audit_query(action: str | None, user_id: uuid.UUID | None, resource_type: str | None, resource_id: str | None,
                 since: datetime | None, until: datetime | None, q: str | None):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.in_([a.strip().upper() for a in action.split(",") if a.strip()]))
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AuditLog.created_at <= until)
    if q:
        stmt = stmt.where(func.lower(AuditLog.actor_name).like(f"%{q.lower()}%"))
    return stmt


@router.get("/audit-logs", response_model=Page[AuditOut])
def list_audit(principal: AdminPrincipal, db: DbDep, action: str | None = None, user_id: uuid.UUID | None = None,
               resource_type: str | None = None, resource_id: str | None = None, since: datetime | None = None,
               until: datetime | None = None, q: str | None = None, page: int = Query(1, ge=1),
               page_size: int = Query(50, ge=1, le=500)) -> Page[AuditOut]:
    stmt = _audit_query(action, user_id, resource_type, resource_id, since, until, q)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    return Page(items=[AuditOut.model_validate(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/audit-logs/export")
def export_audit(request: Request, principal: AdminPrincipal, db: DbDep,
                 fmt: str = Query("csv", alias="format", pattern="^(csv|json)$"), action: str | None = None,
                 user_id: uuid.UUID | None = None, resource_type: str | None = None, resource_id: str | None = None,
                 since: datetime | None = None, until: datetime | None = None) -> StreamingResponse:
    stmt = _audit_query(action, user_id, resource_type, resource_id, since, until, None)
    rows = [AuditOut.model_validate(r).model_dump(mode="json")
            for r in db.scalars(stmt.order_by(AuditLog.created_at.asc()).limit(EXPORT_LIMIT))]
    audit_service.record(db, A.AUDIT_EXPORTED, principal=principal, resource_type="audit_log", request=request,
                         metadata={"format": fmt, "rows": len(rows)})
    db.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if fmt == "json":
        payload = json.dumps(rows, ensure_ascii=False, indent=1)
        return StreamingResponse(iter([payload]), media_type="application/json",
                                 headers={"Content-Disposition": f'attachment; filename="audit-{stamp}.json"'})
    buf = io.StringIO()
    fields = ["created_at", "action", "actor_type", "actor_name", "user_id", "resource_type", "resource_id",
              "ip_address", "request_id", "user_agent", "metadata_json"]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        r["metadata_json"] = json.dumps(r["metadata_json"], ensure_ascii=False)
        writer.writerow(r)
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="audit-{stamp}.csv"'})


def _runner_out(r: RunnerNode) -> RunnerOut:
    out = RunnerOut.model_validate(r)
    out.online = (r.status != RunnerStatus.OFFLINE and r.last_heartbeat_at is not None
                  and datetime.now(timezone.utc) - r.last_heartbeat_at < timedelta(minutes=2))
    return out


@router.get("/runners", response_model=list[RunnerOut])
def list_runners(principal: AdminPrincipal, db: DbDep) -> list[RunnerOut]:
    return [_runner_out(r) for r in db.scalars(select(RunnerNode).order_by(RunnerNode.name))]


@router.patch("/runners/{runner_id}", response_model=RunnerOut)
def update_runner(runner_id: uuid.UUID, body: RunnerUpdate, principal: AdminPrincipal, db: DbDep) -> RunnerOut:
    runner = db.get(RunnerNode, runner_id)
    if runner is None:
        raise NotFound("RUNNER_NOT_FOUND", "Không tìm thấy runner")
    runner.status = body.status
    db.commit()
    return _runner_out(runner)


@router.get("/system")
def system_status(principal: AdminPrincipal, db: DbDep) -> dict:
    s = get_settings()
    ready = readiness()
    depth = queue_service.queue_depth()
    return {
        "app": {"name": s.app_name, "env": s.app_env, "version": get_version()},
        "readiness": ready,
        "queue_depth": depth,
        "counts": {
            "users": db.scalar(select(func.count()).select_from(User)),
            "active_users": db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))),
            "projects": db.scalar(select(func.count()).select_from(Project)),
            "builds_running": db.scalar(select(func.count()).select_from(Build)
                                        .where(Build.status == BuildStatus.RUNNING)),
            "builds_queued": db.scalar(select(func.count()).select_from(Build)
                                       .where(Build.status == BuildStatus.QUEUED)),
        },
        "storage": {
            "internal_package_bytes": int(db.scalar(select(func.coalesce(func.sum(PackageFile.size_bytes), 0))) or 0),
            "upstream_cache_bytes": int(db.scalar(select(func.coalesce(func.sum(UpstreamFile.size_bytes), 0))
                                                  .where(UpstreamFile.cached.is_(True))) or 0),
            "upstream_cached_files": db.scalar(select(func.count()).select_from(UpstreamFile)
                                               .where(UpstreamFile.cached.is_(True))),
        },
        "settings": {
            "internal_package_prefix": s.internal_package_prefix,
            "pypi_upstream": s.pypi_upstream_simple_url,
            "metadata_ttl_seconds": s.pypi_metadata_ttl_seconds,
            "build_timeout_seconds": s.build_timeout_seconds,
            "build_cpu_limit": s.build_cpu_limit,
            "build_memory_limit": s.build_memory_limit,
            "build_max_concurrency": s.build_max_concurrency,
            "registry_allow_anonymous": s.registry_allow_anonymous,
        },
    }


def get_version() -> str:
    from app import __version__

    return __version__
