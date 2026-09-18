from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from app.core.config import get_settings
from app.core.deps import CurrentPrincipal, DbDep
from app.core.errors import NotFound
from app.core.metrics import PACKAGE_DOWNLOADS
from app.core.permissions import Perm
from app.db.models import Build, Group, PackageVersion, Project
from app.schemas import PackageFileOut, PackageSummary, PackageVersionOut, YankIn
from app.services import access_service, audit_service, package_service, project_service, storage_service
from app.services.audit_service import A
from app.utils.naming import normalize_package_name
from app.utils.versions import sort_key

router = APIRouter(tags=["packages"])


def _version_out(db, pv: PackageVersion) -> PackageVersionOut:
    out = PackageVersionOut.model_validate(pv)
    out.files = []
    for f in pv.files:
        fo = PackageFileOut.model_validate(f)
        fo.download_url = f"/api/v1/packages/{pv.normalized_name}/{pv.version}/files/{f.filename}"
        out.files.append(fo)
    if pv.build_id:
        out.build_number = db.scalar(select(Build.number).where(Build.id == pv.build_id))
    return out


@router.get("/packages", response_model=list[PackageSummary])
def list_packages(principal: CurrentPrincipal, db: DbDep, q: str | None = Query(None, max_length=200),
                  sort: str = Query("popular", pattern="^(popular|recent|name)$"),
                  limit: int = Query(50, ge=1, le=200)) -> list[PackageSummary]:
    stmt = (select(PackageVersion.normalized_name, func.max(PackageVersion.package_name), Project.id,
                   func.count(PackageVersion.id), func.sum(PackageVersion.download_count),
                   func.max(PackageVersion.created_at))
            .join(Project, Project.id == PackageVersion.project_id).join(Group))
    stmt = access_service.visible_projects_filter(stmt, principal)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(PackageVersion.normalized_name.like(like), func.lower(Project.name).like(like),
                              func.lower(Project.description).like(like)))
    stmt = stmt.group_by(PackageVersion.normalized_name, Project.id)
    order = {"popular": func.sum(PackageVersion.download_count).desc(),
             "recent": func.max(PackageVersion.created_at).desc(), "name": PackageVersion.normalized_name.asc()}
    rows = db.execute(stmt.order_by(order[sort]).limit(limit)).all()
    projects = {p.id: p for p in db.scalars(select(Project).options(joinedload(Project.group))
                                            .where(Project.id.in_([r[2] for r in rows])))}
    out = []
    for normalized, display, project_id, count, downloads, last in rows:
        p = projects[project_id]
        out.append(PackageSummary(package_name=display, normalized_name=normalized,
                                  project_path=f"{p.group.slug}/{p.slug}", project_name=p.name,
                                  description=p.description, latest_version=p.latest_release_version,
                                  version_count=count, download_count=int(downloads or 0), last_published_at=last))
    return out


@router.get("/packages/{name}")
def get_package(name: str, principal: CurrentPrincipal, db: DbDep) -> dict:
    normalized = normalize_package_name(name)
    project = package_service.require_package_access(db, principal, normalized)
    versions = package_service.list_versions(db, normalized)
    return {"package_name": normalized, "project": project_service.to_out(db, project, principal),
            "versions": [_version_out(db, v) for v in versions]}


@router.get("/packages/{name}/{version}", response_model=PackageVersionOut)
def get_package_version(name: str, version: str, principal: CurrentPrincipal, db: DbDep) -> PackageVersionOut:
    normalized = normalize_package_name(name)
    package_service.require_package_access(db, principal, normalized)
    return _version_out(db, package_service.get_version(db, normalized, version))


@router.post("/packages/{name}/{version}/yank", response_model=PackageVersionOut)
def yank_package(name: str, version: str, body: YankIn, request: Request, principal: CurrentPrincipal,
                 db: DbDep) -> PackageVersionOut:
    normalized = normalize_package_name(name)
    project = package_service.require_package_access(db, principal, normalized)
    access_service.require_project(db, principal, project, Perm.PACKAGE_YANK)
    pv = package_service.get_version(db, normalized, version)
    package_service.set_yanked(db, pv, body.yank, body.reason, principal, request)
    return _version_out(db, pv)


@router.get("/packages/{name}/{version}/files/{filename}")
def download_package_file(name: str, version: str, filename: str, request: Request, principal: CurrentPrincipal,
                          db: DbDep) -> StreamingResponse:
    normalized = normalize_package_name(name)
    package_service.require_package_access(db, principal, normalized)
    pv = package_service.get_version(db, normalized, version)
    f = next((x for x in pv.files if x.filename == filename), None)
    if f is None:
        raise NotFound("PACKAGE_NOT_FOUND", "Không tìm thấy file")
    pv.download_count += 1
    audit_service.record(db, A.PACKAGE_DOWNLOADED, principal=principal, resource_type="package",
                         resource_id=f"{normalized}=={pv.version}", request=request,
                         metadata={"filename": filename, "via": "web"})
    db.commit()
    PACKAGE_DOWNLOADS.labels(source="internal").inc()
    return StreamingResponse(storage_service.iter_object(get_settings().minio_bucket_packages, f.object_key),
                             media_type="application/octet-stream",
                             headers={"Content-Length": str(f.size_bytes),
                                      "Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/projects/{group}/{project}/packages", response_model=list[PackageVersionOut])
def project_packages(group: str, project: str, principal: CurrentPrincipal, db: DbDep) -> list[PackageVersionOut]:
    p = project_service.get_project_by_path(db, group, project)
    access_service.require_project(db, principal, p, Perm.PACKAGE_READ)
    rows = db.scalars(select(PackageVersion).options(joinedload(PackageVersion.files))
                      .where(PackageVersion.project_id == p.id)).unique().all()
    rows = sorted(rows, key=lambda v: sort_key(v.version), reverse=True)
    return [_version_out(db, v) for v in rows]
