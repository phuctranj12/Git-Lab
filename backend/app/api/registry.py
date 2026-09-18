"""PyPI-compatible Simple API — một index URL duy nhất cho cả package nội bộ và public (spec 16–19).

GET /simple/                                         → danh sách package nội bộ user được thấy
GET /simple/{name}/                                  → hawee-* : chỉ DB nội bộ (KHÔNG fallback PyPI)
                                                       còn lại  : proxy/cache PyPI
GET /packages/internal/{name}/{version}/{filename}   → tải file nội bộ (MinIO)
GET /packages/upstream/{name}/{filename}             → tải file public (cache MinIO, miss → PyPI)
"""
from __future__ import annotations

import html
import json
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import update

from app.core.config import get_settings
from app.core.deps import DbDep, Principal, resolve_principal
from app.core.errors import AppError, NotFound, Unauthorized
from app.core.metrics import PACKAGE_DOWNLOADS
from app.core.permissions import Scope
from app.core.rate_limit import enforce
from app.db.models import PackageVersion
from app.services import audit_service, package_service, pypi_proxy_service, storage_service
from app.services.audit_service import A
from app.utils.naming import is_internal_package, normalize_package_name
from app.utils.versions import normalize_version

router = APIRouter(tags=["registry"], include_in_schema=True)

JSON_TYPE = "application/vnd.pypi.simple.v1+json"
HTML_TYPE = "application/vnd.pypi.simple.v1+html"
REALM_HEADER = {"WWW-Authenticate": 'Basic realm="HAWEE Tool Hub"'}


def _principal(request: Request, db) -> Principal | None:
    enforce("registry", request.client.host if request.client else "unknown",
            get_settings().registry_rate_limit_per_minute)
    try:
        principal = resolve_principal(request, db, allow_basic=True)
    except Unauthorized as exc:
        exc.headers = REALM_HEADER
        raise
    if principal is None:
        if get_settings().registry_allow_anonymous:
            return None
        raise Unauthorized("INVALID_CREDENTIALS", "Registry yêu cầu token (read_package)", headers=REALM_HEADER)
    if principal.via == "token" and not principal.has_scope(Scope.READ_PACKAGE):
        raise AppError("INSUFFICIENT_SCOPE", "Token thiếu scope 'read_package'", 403)
    return principal


def _accept_q(accept: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in accept.split(","):
        media, *params = [p.strip() for p in part.split(";")]
        q = 1.0
        for p in params:
            if p.startswith("q="):
                try:
                    q = float(p[2:])
                except ValueError:
                    q = 0.0
        if media:
            out[media.lower()] = q
    return out


def _wants_json(request: Request) -> bool:
    """PEP 691: trả JSON khi client ưu tiên JSON hơn HTML (pip mới), còn lại trả HTML PEP 503."""
    q = _accept_q(request.headers.get("accept", ""))
    json_q = q.get(JSON_TYPE, 0.0)
    html_q = max(q.get(HTML_TYPE, 0.0), q.get("text/html", 0.0), q.get("*/*", 0.0))
    return json_q > 0 and json_q >= html_q


def _render(request: Request, name: str, files: list[dict]) -> Response:
    if _wants_json(request):
        body = {"meta": {"api-version": "1.0"}, "name": name, "files": [
            {"filename": f["filename"], "url": f["url"], "hashes": {"sha256": f["sha256"]} if f["sha256"] else {},
             "requires-python": f["requires_python"], "yanked": f["yanked"] or False}
            for f in files]}
        return Response(json.dumps(body), media_type=JSON_TYPE)
    lines = ["<!DOCTYPE html>", "<html>", "<head>", '<meta name="pypi:repository-version" content="1.0">',
             f"<title>Links for {html.escape(name)}</title>", "</head>", "<body>",
             f"<h1>Links for {html.escape(name)}</h1>"]
    for f in files:
        href = f["url"] + (f"#sha256={f['sha256']}" if f["sha256"] else "")
        attrs = [f'href="{html.escape(href, quote=True)}"']
        if f["requires_python"]:
            attrs.append(f'data-requires-python="{html.escape(f["requires_python"], quote=True)}"')
        if f["yanked"]:
            reason = f["yanked"] if isinstance(f["yanked"], str) else ""
            attrs.append(f'data-yanked="{html.escape(reason, quote=True)}"')
        lines.append(f"<a {' '.join(attrs)}>{html.escape(f['filename'])}</a><br>")
    lines += ["</body>", "</html>"]
    return HTMLResponse("\n".join(lines), media_type="text/html")


@router.get("/simple/", response_class=HTMLResponse)
def simple_root(request: Request, db: DbDep) -> Response:
    principal = _principal(request, db)
    names = package_service.visible_internal_package_names(db, principal) if principal else []
    if _wants_json(request):
        return Response(json.dumps({"meta": {"api-version": "1.0"}, "projects": [{"name": n} for n in names]}),
                        media_type=JSON_TYPE)
    links = "\n".join(f'<a href="/simple/{quote(n)}/">{html.escape(n)}</a><br>' for n in names)
    return HTMLResponse("<!DOCTYPE html>\n<html><head><meta name=\"pypi:repository-version\" content=\"1.0\">"
                        f"<title>HAWEE Tool Hub — Simple index</title></head><body>\n{links}\n</body></html>")


@router.get("/simple/{name}", include_in_schema=False)
def simple_no_slash(name: str) -> RedirectResponse:
    return RedirectResponse(f"/simple/{normalize_package_name(name)}/", status_code=301)


@router.get("/simple/{name}/")
def simple_project(name: str, request: Request, db: DbDep) -> Response:
    normalized = normalize_package_name(name)
    if normalized != name:
        return RedirectResponse(f"/simple/{normalized}/", status_code=301)
    principal = _principal(request, db)
    prefix = get_settings().internal_package_prefix
    if is_internal_package(normalized, prefix):
        # Package nội bộ: CHỈ tra DB nội bộ; không tồn tại/không có quyền → 404, tuyệt đối không gọi PyPI.
        package_service.require_package_access(db, principal, normalized)
        files = []
        for pv in package_service.list_versions(db, normalized):
            for f in pv.files:
                files.append({"filename": f.filename,
                              "url": f"/packages/internal/{quote(normalized)}/{quote(pv.version)}/{quote(f.filename)}",
                              "sha256": f.sha256, "requires_python": f.python_requires,
                              "yanked": (pv.yanked_reason or True) if pv.is_yanked else False})
        if not files:
            raise NotFound("PACKAGE_NOT_FOUND", "Package chưa có version nào")
        return _render(request, normalized, files)
    _, upstream_files = pypi_proxy_service.get_index(db, normalized)
    files = [{"filename": f.filename, "url": f"/packages/upstream/{quote(normalized)}/{quote(f.filename)}",
              "sha256": f.sha256, "requires_python": f.requires_python, "yanked": f.yanked}
             for f in upstream_files]
    return _render(request, normalized, files)


@router.get("/packages/internal/{name}/{version}/{filename}")
def download_internal(name: str, version: str, filename: str, request: Request, db: DbDep) -> StreamingResponse:
    principal = _principal(request, db)
    normalized = normalize_package_name(name)
    package_service.require_package_access(db, principal, normalized)
    pv = package_service.get_version(db, normalized, version)
    f = next((x for x in pv.files if x.filename == filename), None)
    if f is None:
        raise NotFound("PACKAGE_NOT_FOUND", "Không tìm thấy file")
    db.execute(update(PackageVersion).where(PackageVersion.id == pv.id)
               .values(download_count=PackageVersion.download_count + 1))
    audit_service.record(db, A.PACKAGE_DOWNLOADED, principal=principal, resource_type="package",
                         resource_id=f"{normalized}=={normalize_version(version)}", request=request,
                         metadata={"filename": filename})
    db.commit()
    PACKAGE_DOWNLOADS.labels(source="internal").inc()
    return StreamingResponse(
        storage_service.iter_object(get_settings().minio_bucket_packages, f.object_key),
        media_type="application/octet-stream",
        headers={"Content-Length": str(f.size_bytes), "Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "private, max-age=31536000, immutable", "X-Checksum-Sha256": f.sha256},
    )


@router.get("/packages/upstream/{name}/{filename}")
def download_upstream(name: str, filename: str, request: Request, db: DbDep) -> StreamingResponse:
    _principal(request, db)
    normalized = normalize_package_name(name)
    served = pypi_proxy_service.serve_file(db, normalized, filename)
    PACKAGE_DOWNLOADS.labels(source=f"upstream_{served.source}").inc()
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "X-Cache": served.source.upper()}
    if served.size is not None:
        headers["Content-Length"] = str(served.size)
    return StreamingResponse(served.stream, media_type="application/octet-stream", headers=headers)
