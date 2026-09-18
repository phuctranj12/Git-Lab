"""Endpoint nội bộ — Nginx KHÔNG expose ra ngoài (spec 27). Auth: INTERNAL_SERVICE_SECRET hoặc runner token."""
from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import joinedload

from app.core.config import get_settings
from app.core.deps import DbDep, InternalService, RunnerPrincipal
from app.core.errors import BadRequest, NotFound
from app.core.permissions import Level, Perm, can_project, can_push_ref
from app.db.models import AccessToken, Build, PackageVersion, Project, RefreshToken, RunnerNode, SshKey, User
from app.db.models.enums import ActorType, BuildStatus, RunnerStatus, TokenType
from app.schemas import (
    BuildFinishIn,
    BuildLogIn,
    BuildOut,
    BuildStartIn,
    BuildStartOut,
    GitAuthorizeIn,
    GitAuthorizeOut,
    GitEventIn,
    GitPushAuthorizeIn,
    PackageVersionOut,
    RunnerHeartbeatIn,
)
from app.services import (
    access_service,
    audit_service,
    build_service,
    git_service,
    package_service,
    pypi_proxy_service,
    storage_service,
)
from app.services.audit_service import A

log = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)

_REPO_RE = re.compile(r"^/?(?P<group>[a-z0-9][a-z0-9-]{0,99})/(?P<project>[a-z0-9][a-z0-9-]{0,99})(\.git)?/?$")


# ───────────── git-service ─────────────

@router.get("/git/ssh-keys/lookup", dependencies=[InternalService])
def lookup_ssh_key(db: DbDep, fingerprint: str = Query(min_length=10, max_length=255)) -> dict:
    key = db.scalar(select(SshKey).options(joinedload(SshKey.user))
                    .where(SshKey.fingerprint == fingerprint, SshKey.revoked_at.is_(None)))
    if key is None or not key.user.is_active:
        raise NotFound("SSH_KEY_NOT_FOUND", "Không có key")
    return {"key_id": str(key.id), "key_type": key.key_type, "public_key": key.public_key,
            "username": key.user.username}


@router.post("/git/authorize", response_model=GitAuthorizeOut, dependencies=[InternalService])
def git_authorize(body: GitAuthorizeIn, db: DbDep) -> GitAuthorizeOut:
    key = db.scalar(select(SshKey).options(joinedload(SshKey.user))
                    .where(SshKey.id == body.key_id, SshKey.revoked_at.is_(None)))
    if key is None or not key.user.is_active:
        return GitAuthorizeOut(allowed=False, message="SSH key không hợp lệ hoặc tài khoản đã bị khoá")
    user = key.user
    m = _REPO_RE.match(body.repository.strip().lower())
    not_found = GitAuthorizeOut(allowed=False, message=f"Repository '{body.repository}' không tồn tại hoặc bạn không có quyền")
    if not m:
        return not_found
    project = db.scalar(select(Project).options(joinedload(Project.group))
                        .where(Project.repo_path == f"{m['group']}/{m['project']}.git"))
    if project is None:
        return not_found
    level = access_service.user_project_level(db, user.id, user.is_system_admin, project)
    if level < Level.VIEWER:
        return not_found
    key.last_used_at = datetime.now(timezone.utc)
    if body.action == "write":
        if project.archived:
            db.commit()
            return GitAuthorizeOut(allowed=False, message="Project đã archive — repository chỉ đọc")
        if not can_project(level, Perm.REPO_WRITE):
            audit_service.record(db, A.GIT_PUSH_DENIED, user_id=user.id, actor_type=ActorType.USER,
                                 actor_name=user.username, resource_type="project", resource_id=project.id,
                                 metadata={"reason": "insufficient_role", "level": level.name})
            db.commit()
            return GitAuthorizeOut(allowed=False, message="Bạn không có quyền push (cần Developer trở lên)")
    db.commit()
    return GitAuthorizeOut(allowed=True, user_id=user.id, username=user.username, project_id=project.id,
                           repo_path=str(git_service.absolute_repo_path(project.repo_path)))


@router.post("/git/authorize-push", dependencies=[InternalService])
def git_authorize_push(body: GitPushAuthorizeIn, db: DbDep) -> dict:
    """pre-receive: kiểm tra từng ref (tag cần Maintainer, nhánh mặc định có thể được bảo vệ)."""
    user = db.get(User, body.user_id)
    project = db.scalar(select(Project).options(joinedload(Project.group)).where(Project.id == body.project_id))
    if user is None or not user.is_active or project is None:
        return {"allowed": False, "message": "Không xác định được user/project", "denied": []}
    level = access_service.user_project_level(db, user.id, user.is_system_admin, project)
    denied = []
    for upd in body.updates:
        ok, reason = can_push_ref(level, upd.ref, project.default_branch, project.protect_default_branch)
        if ok and upd.ref.startswith("refs/tags/") and upd.old_sha != git_service.ZERO_SHA:
            tag = upd.ref[len("refs/tags/"):]
            published = db.scalar(select(PackageVersion.version).where(PackageVersion.project_id == project.id,
                                                                       PackageVersion.git_tag == tag).limit(1))
            if published is not None:
                ok, reason = False, f"Tag đã phát hành package {published} — không được sửa/xoá (release bất biến)"
        if ok and upd.new_sha == git_service.ZERO_SHA and upd.ref == f"refs/heads/{project.default_branch}":
            ok, reason = False, "Không được xoá nhánh mặc định"
        if not ok:
            denied.append({"ref": upd.ref, "reason": reason})
    if denied:
        audit_service.record(db, A.GIT_PUSH_DENIED, user_id=user.id, actor_type=ActorType.USER,
                             actor_name=user.username, resource_type="project", resource_id=project.id,
                             metadata={"denied": denied})
        db.commit()
    return {"allowed": not denied, "message": "; ".join(f"{d['ref']}: {d['reason']}" for d in denied),
            "denied": denied}


@router.post("/git/events", dependencies=[InternalService])
def git_events(body: GitEventIn, db: DbDep) -> dict:
    builds = build_service.handle_git_event(db, body)
    db.commit()
    build_service.enqueue_after_commit(builds)
    return {"builds": [{"id": str(b.id), "number": b.number, "trigger": b.trigger_type.value, "ref": b.ref_name}
                       for b in builds]}


# ───────────── runner ─────────────

@router.post("/runner/heartbeat")
def runner_heartbeat(body: RunnerHeartbeatIn, principal: RunnerPrincipal, db: DbDep) -> dict:
    runner = db.scalar(select(RunnerNode).where(RunnerNode.name == body.name))
    now = datetime.now(timezone.utc)
    if runner is None:
        runner = RunnerNode(name=body.name, status=RunnerStatus.ONLINE)
        db.add(runner)
    if runner.status == RunnerStatus.OFFLINE:
        runner.status = RunnerStatus.ONLINE
    runner.hostname, runner.labels, runner.version = body.hostname, body.labels, body.version
    runner.max_concurrent_jobs, runner.current_jobs = body.max_concurrent_jobs, body.current_jobs
    runner.last_heartbeat_at = now
    db.commit()
    return {"id": str(runner.id), "status": runner.status.value, "accept_jobs": runner.status == RunnerStatus.ONLINE}


@router.post("/builds/{build_id}/start", response_model=BuildStartOut)
def build_start(build_id: uuid.UUID, body: BuildStartIn, principal: RunnerPrincipal, db: DbDep) -> BuildStartOut:
    return build_service.start_build(db, build_id, body.runner_name)


@router.get("/builds/{build_id}/source")
def build_source(build_id: uuid.UUID, principal: RunnerPrincipal, db: DbDep) -> StreamingResponse:
    build = build_service.require_running(db, build_id)
    return StreamingResponse(git_service.stream_archive(build.project.repo_path, build.commit_sha),
                             media_type="application/gzip")


@router.post("/builds/{build_id}/log")
def build_log(build_id: uuid.UUID, body: BuildLogIn, principal: RunnerPrincipal) -> dict:
    build_service.append_log(build_id, body.lines)
    return {"ok": True}


@router.get("/builds/{build_id}/control")
def build_control(build_id: uuid.UUID, principal: RunnerPrincipal, db: DbDep) -> dict:
    return build_service.control(db, build_id)


@router.post("/builds/{build_id}/artifacts")
def build_artifacts(build_id: uuid.UUID, principal: RunnerPrincipal, db: DbDep,
                    files: list[UploadFile] = File(...)) -> dict:
    """Artifact của validation build (không publish) → bucket toolhub-artifacts để tải về kiểm tra."""
    build = build_service.require_running(db, build_id)
    s = get_settings()
    stored = []
    for up in files[:20]:
        fname = (up.filename or "").strip()
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*\.(whl|tar\.gz)$", fname):
            raise BadRequest("INVALID_PACKAGE_FILE", f"Tên artifact không hợp lệ: {fname}")
        tmp = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
        digest, size = hashlib.sha256(), 0
        while chunk := up.file.read(1024 * 1024):
            size += len(chunk)
            if size > s.package_max_upload_bytes:
                raise BadRequest("PACKAGE_TOO_LARGE", "Artifact quá lớn")
            digest.update(chunk)
            tmp.write(chunk)
        key = f"builds/{build.project_id}/{build.id}/{fname}"
        storage_service.put_object(s.minio_bucket_artifacts, key, tmp, size)
        tmp.close()
        stored.append({"filename": fname, "size_bytes": size, "sha256": digest.hexdigest(),
                       "bucket": s.minio_bucket_artifacts, "object_key": key, "published": False})
    build_service.add_artifacts(db, build, stored)
    return {"artifacts": stored}


@router.post("/builds/{build_id}/finish", response_model=BuildOut)
def build_finish(build_id: uuid.UUID, body: BuildFinishIn, principal: RunnerPrincipal, db: DbDep) -> BuildOut:
    build = build_service.finish_build(db, build_id, body)
    return build_service.to_out(db, build)


@router.post("/packages/publish", response_model=PackageVersionOut)
def publish_package(request: Request, principal: RunnerPrincipal, db: DbDep,
                    project_id: uuid.UUID = Form(...), build_id: uuid.UUID = Form(...),
                    package_name: str = Form(..., max_length=255), version: str = Form(..., max_length=100),
                    commit_sha: str = Form(..., max_length=64), git_tag: str = Form(..., max_length=255),
                    files: list[UploadFile] = File(...)) -> PackageVersionOut:
    pv = package_service.publish(db, principal, project_id=project_id, build_id=build_id, package_name=package_name,
                                 version=version, commit_sha=commit_sha, git_tag=git_tag, files=files,
                                 request=request)
    return PackageVersionOut.model_validate(pv)


# ───────────── bảo trì định kỳ (worker beat gọi) ─────────────

@router.post("/maintenance/run")
def maintenance(principal: RunnerPrincipal, db: DbDep) -> dict:
    s = get_settings()
    now = datetime.now(timezone.utc)
    result: dict[str, object] = {"stale_builds": build_service.requeue_stale(db)}
    # Refresh token hết hạn/thu hồi > 30 ngày: xoá (chỉ là phiên đăng nhập).
    r = db.execute(delete(RefreshToken).where(RefreshToken.expires_at < now - timedelta(days=30)))
    # Token tạm của build đã hết hạn > 7 ngày: xoá (không mang giá trị audit).
    t = db.execute(delete(AccessToken).where(AccessToken.token_type == TokenType.RUNNER,
                                             AccessToken.build_id.is_not(None),
                                             AccessToken.expires_at < now - timedelta(days=7)))
    result["expired_tokens_removed"] = {"refresh": r.rowcount, "build": t.rowcount}
    # Runner mất heartbeat > 3 phút → OFFLINE.
    offline = 0
    for runner in db.scalars(select(RunnerNode).where(RunnerNode.status == RunnerStatus.ONLINE,
                                                      RunnerNode.last_heartbeat_at < now - timedelta(minutes=3))):
        runner.status = RunnerStatus.OFFLINE
        offline += 1
    result["runners_marked_offline"] = offline
    # Log build quá hạn lưu giữ.
    removed_logs = 0
    cutoff = now - timedelta(days=s.build_log_retention_days)
    for b in db.scalars(select(Build).where(Build.log_object_key.is_not(None), Build.created_at < cutoff).limit(500)):
        try:
            storage_service.remove_object(s.minio_bucket_logs, b.log_object_key)
        except Exception:  # noqa: BLE001
            continue
        b.log_object_key = None
        removed_logs += 1
    result["build_logs_removed"] = removed_logs
    db.commit()
    result["upstream_cache"] = pypi_proxy_service.cleanup_cache(db)
    result["queued_builds"] = db.scalar(select(Build.id).where(Build.status == BuildStatus.QUEUED).limit(1)) is not None
    log.info("maintenance done", extra={"extra_data": result})
    return result


@router.get("/ping", response_class=PlainTextResponse, dependencies=[InternalService])
def ping() -> str:
    return "pong"
