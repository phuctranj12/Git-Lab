from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.core.config import get_settings
from app.core.deps import CurrentPrincipal, DbDep
from app.core.errors import NotFound
from app.core.permissions import Perm
from app.db.models import Build, Group, Project
from app.db.models.enums import BuildStatus
from app.schemas import BuildOut, ManualBuildIn, Page
from app.services import access_service, build_service, project_service, storage_service

router = APIRouter(tags=["builds"])


def _load_build(db, build_id: uuid.UUID, principal, perm: Perm) -> Build:
    build = db.scalar(select(Build).options(joinedload(Build.project).joinedload(Project.group))
                      .where(Build.id == build_id))
    if build is None:
        raise NotFound("BUILD_NOT_FOUND", "Không tìm thấy build")
    try:
        access_service.require_project(db, principal, build.project, perm)
    except NotFound as exc:
        raise NotFound("BUILD_NOT_FOUND", "Không tìm thấy build") from exc
    return build


@router.get("/projects/{group}/{project}/builds", response_model=Page[BuildOut])
def list_project_builds(group: str, project: str, principal: CurrentPrincipal, db: DbDep,
                        status_filter: BuildStatus | None = Query(None, alias="status"),
                        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> Page[BuildOut]:
    p = project_service.get_project_by_path(db, group, project)
    access_service.require_project(db, principal, p, Perm.BUILD_VIEW)
    stmt = select(Build).where(Build.project_id == p.id)
    if status_filter is not None:
        stmt = stmt.where(Build.status == status_filter)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.options(joinedload(Build.project).joinedload(Project.group))
                      .order_by(Build.number.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[build_service.to_out(db, b) for b in rows], total=total, page=page, page_size=page_size)


@router.get("/builds", response_model=Page[BuildOut])
def list_builds(principal: CurrentPrincipal, db: DbDep, status_filter: BuildStatus | None = Query(None, alias="status"),
                mine: bool = False, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)
                ) -> Page[BuildOut]:
    """Build gần đây trên mọi project được xem (dashboard)."""
    stmt = access_service.visible_projects_filter(select(Build).join(Project).join(Group), principal)
    if status_filter is not None:
        stmt = stmt.where(Build.status == status_filter)
    if mine and principal.user_id is not None:
        stmt = stmt.where(Build.created_by == principal.user_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.options(joinedload(Build.project).joinedload(Project.group))
                      .order_by(Build.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[build_service.to_out(db, b) for b in rows], total=total, page=page, page_size=page_size)


@router.post("/projects/{group}/{project}/builds/manual", response_model=BuildOut,
             status_code=status.HTTP_201_CREATED)
def manual_build(group: str, project: str, body: ManualBuildIn, request: Request, principal: CurrentPrincipal,
                 db: DbDep) -> BuildOut:
    p = project_service.get_project_by_path(db, group, project)
    access_service.require_project(db, principal, p, Perm.BUILD_RUN)
    build = build_service.create_manual_build(db, p, principal.user_id, body.ref, request)
    db.commit()
    build_service.enqueue_after_commit([build])
    return build_service.to_out(db, build)


@router.get("/builds/{build_id}", response_model=BuildOut)
def get_build(build_id: uuid.UUID, principal: CurrentPrincipal, db: DbDep) -> BuildOut:
    return build_service.to_out(db, _load_build(db, build_id, principal, Perm.BUILD_VIEW))


@router.get("/builds/{build_id}/log")
def get_build_log(build_id: uuid.UUID, principal: CurrentPrincipal, db: DbDep, offset: int = Query(0, ge=0)) -> dict:
    build = _load_build(db, build_id, principal, Perm.BUILD_LOG)
    text, complete = build_service.read_log(build, offset)
    return {"offset": offset, "next_offset": offset + len(text), "content": text, "complete": complete,
            "status": build.status.value}


@router.post("/builds/{build_id}/cancel", response_model=BuildOut)
def cancel_build(build_id: uuid.UUID, request: Request, principal: CurrentPrincipal, db: DbDep) -> BuildOut:
    build = _load_build(db, build_id, principal, Perm.BUILD_CANCEL)
    build_service.cancel_build(db, build, principal.user_id, request)
    return build_service.to_out(db, build)


@router.get("/builds/{build_id}/artifacts/{filename}")
def download_artifact(build_id: uuid.UUID, filename: str, principal: CurrentPrincipal, db: DbDep) -> StreamingResponse:
    build = _load_build(db, build_id, principal, Perm.BUILD_LOG)
    art = next((a for a in (build.artifacts_json or []) if a.get("filename") == filename), None)
    if art is None:
        raise NotFound("ARTIFACT_NOT_FOUND", "Không tìm thấy artifact")
    bucket = art.get("bucket") or get_settings().minio_bucket_artifacts
    return StreamingResponse(storage_service.iter_object(bucket, art["object_key"]),
                             media_type="application/octet-stream",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})
