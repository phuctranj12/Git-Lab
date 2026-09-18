from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import IO

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.deps import Principal
from app.core.errors import BadRequest, Conflict, NotFound
from app.db.models import Build, Group, PackageFile, PackageVersion, Project
from app.db.models.enums import ActorType, BuildStatus, PackageFileType, PackageType
from app.services import access_service, audit_service, storage_service
from app.services.audit_service import A
from app.utils.naming import is_internal_package, normalize_package_name
from app.utils.pkgmeta import InvalidPackageFile, classify_filename, read_metadata
from app.utils.versions import is_valid_version, normalize_version, sort_key, versions_equal

log = logging.getLogger(__name__)

MAX_FILES_PER_RELEASE = 20


@dataclass
class _Staged:
    filename: str
    file_type: PackageFileType
    tmp: IO[bytes]
    size: int
    sha256: str
    requires_python: str | None
    metadata: dict


def internal_object_key(normalized: str, version: str, filename: str) -> str:
    return f"internal/{normalized}/{version}/{filename}"


def _stage(upload: UploadFile, max_bytes: int) -> tuple[IO[bytes], int, str]:
    tmp = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = upload.file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            tmp.close()
            raise BadRequest("PACKAGE_TOO_LARGE", f"{upload.filename} vượt giới hạn {max_bytes // 1024 // 1024} MB")
        digest.update(chunk)
        tmp.write(chunk)
    tmp.seek(0)
    return tmp, size, digest.hexdigest()


def publish(db: Session, principal: Principal, *, project_id: uuid.UUID, build_id: uuid.UUID, package_name: str,
            version: str, commit_sha: str, git_tag: str, files: list[UploadFile], request=None) -> PackageVersion:
    s = get_settings()
    build = db.scalar(select(Build).options(joinedload(Build.project).joinedload(Project.group))
                      .where(Build.id == build_id).with_for_update(of=Build))
    if build is None or build.project_id != project_id:
        raise NotFound("BUILD_NOT_FOUND", "Build không tồn tại hoặc không thuộc project")
    if build.status != BuildStatus.RUNNING:
        raise Conflict("BUILD_NOT_RUNNING", "Chỉ publish trong lúc build đang chạy (sau khi mọi bước đã PASS)")
    if not build.publish_package or not build.requested_version:
        raise BadRequest("PUBLISH_NOT_ALLOWED", "Build này không phải release build (tag vX.Y.Z)")
    project = build.project
    if not project.package_name:
        raise BadRequest("INVALID_PACKAGE_NAME", "Project chưa khai báo package_name")

    # ── Validate metadata khai báo (sau đó đối chiếu lại với chính file) ──
    normalized = normalize_package_name(package_name)
    if not is_internal_package(normalized, s.internal_package_prefix):
        raise BadRequest("INTERNAL_PREFIX_REQUIRED", f"Package phải có prefix '{s.internal_package_prefix}'")
    if normalized != project.package_name:
        raise BadRequest("INVALID_PACKAGE_NAME", f"Package '{normalized}' không thuộc project này "
                                                 f"(project khai báo '{project.package_name}')")
    if not is_valid_version(version) or not versions_equal(version, build.requested_version):
        raise BadRequest("VERSION_MISMATCH", f"Version {version} không khớp tag {build.ref_name}")
    if commit_sha != build.commit_sha or git_tag != build.ref_name:
        raise BadRequest("VALIDATION_ERROR", "commit_sha/git_tag không khớp build")
    version = normalize_version(version)
    exists = db.scalar(select(PackageVersion.id).where(PackageVersion.package_type == PackageType.PYPI,
                                                       PackageVersion.normalized_name == normalized,
                                                       PackageVersion.version == version))
    if exists is not None:
        raise Conflict("PACKAGE_VERSION_EXISTS", f"{normalized}=={version} đã tồn tại — không được ghi đè")
    if not files or len(files) > MAX_FILES_PER_RELEASE:
        raise BadRequest("VALIDATION_ERROR", f"Cần 1..{MAX_FILES_PER_RELEASE} file")

    staged: list[_Staged] = []
    try:
        seen: set[str] = set()
        for up in files:
            fname = (up.filename or "").strip()
            try:
                info = classify_filename(fname)
            except InvalidPackageFile as exc:
                raise BadRequest("INVALID_PACKAGE_FILE", str(exc)) from exc
            if fname in seen:
                raise BadRequest("INVALID_PACKAGE_FILE", f"Trùng file {fname}")
            seen.add(fname)
            if info.name != normalized or not versions_equal(info.version, version):
                raise BadRequest("INVALID_PACKAGE_FILE", f"{fname} không khớp {normalized}=={version}")
            tmp, size, sha = _stage(up, s.package_max_upload_bytes)
            try:
                meta = read_metadata(info.file_type, tmp)
            except InvalidPackageFile as exc:
                tmp.close()
                raise BadRequest("INVALID_PACKAGE_FILE", f"{fname}: {exc}") from exc
            if normalize_package_name(meta.name) != normalized or not versions_equal(meta.version, version):
                tmp.close()
                raise BadRequest("INVALID_PACKAGE_FILE",
                                 f"{fname}: metadata bên trong ({meta.name} {meta.version}) không khớp")
            staged.append(_Staged(fname, info.file_type, tmp, size, sha, meta.requires_python, meta.to_json()))
        if not any(st.file_type == PackageFileType.WHEEL for st in staged):
            raise BadRequest("INVALID_PACKAGE_FILE", "Release phải có ít nhất một file .whl")
        if db.scalar(select(func.count()).select_from(PackageFile).where(PackageFile.filename.in_(seen))):
            raise Conflict("PACKAGE_VERSION_EXISTS", "File package đã tồn tại")

        uploaded: list[str] = []
        bucket = s.minio_bucket_packages
        try:
            for st in staged:
                key = internal_object_key(normalized, version, st.filename)
                content_type = "application/zip" if st.file_type == PackageFileType.WHEEL else "application/gzip"
                storage_service.put_object(bucket, key, st.tmp, st.size, content_type)
                uploaded.append(key)
            wheel_meta = next(st.metadata for st in staged if st.file_type == PackageFileType.WHEEL)
            pv = PackageVersion(project_id=project.id, package_type=PackageType.PYPI, package_name=package_name.strip(),
                                normalized_name=normalized, version=version, build_id=build.id, commit_sha=commit_sha,
                                git_tag=git_tag, metadata_json=wheel_meta)
            pv.files = [PackageFile(filename=st.filename, file_type=st.file_type,
                                    object_key=internal_object_key(normalized, version, st.filename),
                                    size_bytes=st.size, sha256=st.sha256, python_requires=st.requires_python)
                        for st in staged]
            db.add(pv)
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            _cleanup(bucket, uploaded)
            raise Conflict("PACKAGE_VERSION_EXISTS", f"{normalized}=={version} đã tồn tại") from exc
        except Exception:
            db.rollback()
            _cleanup(bucket, uploaded)
            raise

        current = project.latest_release_version
        if current is None or sort_key(version) > sort_key(current):
            project.latest_release_version = version
            project.latest_release_build_id = build.id
        build.artifacts_json = [{"filename": st.filename, "size_bytes": st.size, "sha256": st.sha256,
                                 "bucket": bucket, "object_key": internal_object_key(normalized, version, st.filename),
                                 "published": True} for st in staged]
        audit_service.record(db, A.PACKAGE_PUBLISHED, principal=principal, actor_type=ActorType.SERVICE,
                             resource_type="package", resource_id=f"{normalized}=={version}", request=request,
                             metadata={"project_id": str(project.id), "build_id": str(build.id),
                                       "files": [{"filename": st.filename, "sha256": st.sha256} for st in staged]})
        db.commit()
        return pv
    finally:
        for st in staged:
            st.tmp.close()


def _cleanup(bucket: str, keys: list[str]) -> None:
    for key in keys:
        try:
            storage_service.remove_object(bucket, key)
        except Exception:  # noqa: BLE001
            log.warning("cannot cleanup object %s", key)


# ───────────── truy vấn ─────────────

def project_for_package(db: Session, normalized: str) -> Project | None:
    project_id = db.scalar(select(PackageVersion.project_id).where(PackageVersion.normalized_name == normalized)
                           .limit(1))
    if project_id is None:
        project_id = db.scalar(select(Project.id).where(Project.package_name == normalized)
                               .order_by(Project.archived.asc()).limit(1))
    if project_id is None:
        return None
    return db.scalar(select(Project).options(joinedload(Project.group)).where(Project.id == project_id))


def list_versions(db: Session, normalized: str) -> list[PackageVersion]:
    rows = db.scalars(select(PackageVersion).options(joinedload(PackageVersion.files))
                      .where(PackageVersion.normalized_name == normalized)).unique().all()
    return sorted(rows, key=lambda v: sort_key(v.version), reverse=True)


def get_version(db: Session, normalized: str, version: str) -> PackageVersion:
    try:
        version = normalize_version(version)
    except Exception as exc:  # noqa: BLE001
        raise NotFound("PACKAGE_NOT_FOUND", "Không tìm thấy version") from exc
    pv = db.scalar(select(PackageVersion).options(joinedload(PackageVersion.files))
                   .where(PackageVersion.normalized_name == normalized, PackageVersion.version == version))
    if pv is None:
        raise NotFound("PACKAGE_NOT_FOUND", "Không tìm thấy version")
    return pv


def require_package_access(db: Session, principal: Principal | None, normalized: str) -> Project:
    """Package theo quyền project. Không có quyền → 404 (không lộ package private)."""
    project = project_for_package(db, normalized)
    if project is None:
        raise NotFound("PACKAGE_NOT_FOUND", "Không tìm thấy package")
    from app.core.permissions import Perm

    try:
        access_service.require_project(db, principal, project, Perm.PACKAGE_READ)
    except NotFound as exc:
        raise NotFound("PACKAGE_NOT_FOUND", "Không tìm thấy package") from exc
    return project


def visible_internal_package_names(db: Session, principal: Principal) -> list[str]:
    stmt = access_service.visible_projects_filter(
        select(PackageVersion.normalized_name).join(Project, Project.id == PackageVersion.project_id).join(Group),
        principal).distinct()
    return sorted(db.scalars(stmt).all())


def set_yanked(db: Session, pv: PackageVersion, yank: bool, reason: str, principal: Principal, request=None) -> None:
    if yank:
        pv.is_yanked, pv.yanked_reason = True, reason
        pv.yanked_at, pv.yanked_by = datetime.now(timezone.utc), principal.user_id
    else:
        pv.is_yanked, pv.yanked_reason, pv.yanked_at, pv.yanked_by = False, None, None, None
    audit_service.record(db, A.PACKAGE_YANKED if yank else A.PACKAGE_UNYANKED, principal=principal,
                         resource_type="package", resource_id=f"{pv.normalized_name}=={pv.version}", request=request,
                         metadata={"reason": reason})
    db.commit()
