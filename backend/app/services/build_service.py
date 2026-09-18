from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.errors import AppError, BadRequest, Conflict, NotFound
from app.core.metrics import BUILD_DURATION, BUILD_FAILURES, BUILDS_TOTAL
from app.core.redis import get_redis
from app.db.models import Build, GitPushEvent, Project, RunnerNode, User
from app.db.models.enums import FINISHED_BUILD_STATUSES, ActorType, BuildStatus, RunnerStatus, TriggerType
from app.schemas import BuildFinishIn, BuildOut, BuildStartOut, GitEventIn
from app.services import audit_service, git_service, queue_service, storage_service, token_service
from app.services.audit_service import A
from app.utils.versions import parse_release_tag

log = logging.getLogger(__name__)

LOG_KEY = "build:{id}:log"
CANCEL_KEY = "build:{id}:cancel"
LIVE_LOG_MAX_BYTES = 5 * 1024 * 1024
LIVE_LOG_TTL = 3 * 86400


# ───────────── tạo build ─────────────

def create_build(db: Session, project: Project, *, trigger: TriggerType, ref_name: str, commit_sha: str,
                 requested_version: str | None, publish: bool, created_by: uuid.UUID | None,
                 actor_type: ActorType = ActorType.USER, request=None) -> Build:
    locked = db.scalar(select(Project).where(Project.id == project.id).with_for_update())
    assert locked is not None
    locked.build_counter += 1
    build = Build(project_id=project.id, number=locked.build_counter, trigger_type=trigger, ref_name=ref_name,
                  commit_sha=commit_sha, requested_version=requested_version, status=BuildStatus.QUEUED,
                  publish_package=publish, created_by=created_by)
    db.add(build)
    db.flush()
    locked.last_build_status = BuildStatus.QUEUED
    audit_service.record(db, A.BUILD_CREATED, user_id=created_by, actor_type=actor_type, resource_type="build",
                         resource_id=build.id, request=request,
                         metadata={"project_id": str(project.id), "number": build.number, "trigger": trigger.value,
                                   "ref": ref_name, "commit": commit_sha, "publish": publish})
    return build


def handle_git_event(db: Session, event: GitEventIn) -> list[Build]:
    """post-receive → tạo build. Idempotent theo event_id (hook có thể gửi lại từ spool)."""
    project: Project | None = None
    if event.project_id is not None:
        project = db.get(Project, event.project_id)
    if project is None:
        rel = event.repository_path.strip("/")
        if not rel.endswith(".git"):
            rel += ".git"
        project = db.scalar(select(Project).where(Project.repo_path == rel))
    if project is None:
        raise NotFound("PROJECT_NOT_FOUND", "Không tìm thấy project cho repository")
    if db.get(GitPushEvent, event.event_id) is not None:
        return []
    db.add(GitPushEvent(id=event.event_id, project_id=project.id, user_id=event.user_id,
                        payload=event.model_dump(mode="json")))
    user_id = event.user_id if event.user_id and db.get(User, event.user_id) else None
    audit_service.record(db, A.GIT_PUSH, user_id=user_id, actor_type=ActorType.USER if user_id else ActorType.SYSTEM,
                         resource_type="project", resource_id=project.id,
                         metadata={"updates": [u.model_dump() for u in event.updates]})
    builds: list[Build] = []
    for upd in event.updates:
        if upd.new_sha == git_service.ZERO_SHA:
            continue  # xoá ref → không build
        if upd.ref.startswith("refs/heads/"):
            branch = upd.ref[len("refs/heads/"):]
            if branch == project.default_branch:
                project.latest_commit_sha = upd.new_sha
            if project.archived:
                continue
            builds.append(create_build(db, project, trigger=TriggerType.PUSH, ref_name=branch, commit_sha=upd.new_sha,
                                       requested_version=None, publish=False, created_by=user_id,
                                       actor_type=ActorType.SYSTEM))
        elif upd.ref.startswith("refs/tags/"):
            tag = upd.ref[len("refs/tags/"):]
            version = parse_release_tag(tag)
            if version is None or project.archived:
                continue  # tag không theo vX.Y.Z → không release
            commit = git_service.resolve_commit(project.repo_path, upd.new_sha) or upd.new_sha
            builds.append(create_build(db, project, trigger=TriggerType.TAG, ref_name=tag, commit_sha=commit,
                                       requested_version=version, publish=True, created_by=user_id,
                                       actor_type=ActorType.SYSTEM))
    try:
        project.repository_size_bytes = git_service.repository_size(project.repo_path)
    except OSError:
        pass
    return builds


def create_manual_build(db: Session, project: Project, user_id: uuid.UUID | None, ref: str | None, request=None
                        ) -> Build:
    if project.archived:
        raise BadRequest("PROJECT_ARCHIVED", "Project đã archive")
    ref = ref or project.default_branch
    commit = git_service.resolve_commit(project.repo_path, ref)
    if commit is None:
        raise BadRequest("REF_NOT_FOUND", f"Không tìm thấy ref '{ref}' (repository trống?)")
    tag_names = {r.name for r in git_service.list_refs(project.repo_path) if r.kind == "tag"}
    version = parse_release_tag(ref) if ref in tag_names else None
    # Manual build trên tag = build lại release; publish vẫn bị chặn nếu version đã tồn tại.
    return create_build(db, project, trigger=TriggerType.MANUAL, ref_name=ref, commit_sha=commit,
                        requested_version=version, publish=version is not None, created_by=user_id, request=request)


def enqueue_after_commit(builds: list[Build]) -> None:
    for b in builds:
        queue_service.enqueue_build(b.id)


# ───────────── vòng đời do runner gọi ─────────────

def _get_build(db: Session, build_id: uuid.UUID, lock: bool = False) -> Build:
    stmt = select(Build).options(joinedload(Build.project).joinedload(Project.group)).where(Build.id == build_id)
    if lock:
        stmt = stmt.with_for_update(of=Build)
    build = db.scalar(stmt)
    if build is None:
        raise NotFound("BUILD_NOT_FOUND", "Không tìm thấy build")
    return build


def _runner(db: Session, name: str) -> RunnerNode:
    runner = db.scalar(select(RunnerNode).where(RunnerNode.name == name))
    if runner is None:
        runner = RunnerNode(name=name, last_heartbeat_at=datetime.now(timezone.utc))
        db.add(runner)
        db.flush()
    return runner


def start_build(db: Session, build_id: uuid.UUID, runner_name: str) -> BuildStartOut:
    s = get_settings()
    build = _get_build(db, build_id, lock=True)
    if build.status != BuildStatus.QUEUED:
        raise Conflict("BUILD_NOT_QUEUED", f"Build đang ở trạng thái {build.status.value}")
    if _cancel_requested(build.id):
        raise Conflict("BUILD_NOT_QUEUED", "Build đã bị huỷ")
    runner = _runner(db, runner_name)
    if runner.status != RunnerStatus.ONLINE:
        raise AppError("RUNNER_NOT_ACCEPTING", f"Runner {runner_name} đang {runner.status.value}", 409)
    now = datetime.now(timezone.utc)
    build.status = BuildStatus.RUNNING
    build.started_at = now
    build.runner_id = runner.id
    build.project.last_build_status = BuildStatus.RUNNING
    _, raw = token_service.create_build_token(db, build.project_id, build.id, s.build_timeout_seconds)
    audit_service.record(db, A.BUILD_STARTED, actor_type=ActorType.SERVICE, actor_name=f"runner:{runner_name}",
                         resource_type="build", resource_id=build.id, metadata={"runner": runner_name})
    db.commit()
    return BuildStartOut(
        build_id=build.id, project_id=build.project_id,
        project_path=f"{build.project.group.slug}/{build.project.slug}", package_name=build.project.package_name,
        internal_prefix=s.internal_package_prefix, trigger_type=build.trigger_type, ref_name=build.ref_name,
        commit_sha=build.commit_sha, requested_version=build.requested_version,
        publish_package=build.publish_package, build_token=raw, timeout_seconds=s.build_timeout_seconds,
        cpu_limit=s.build_cpu_limit, memory_limit=s.build_memory_limit,
    )


def require_running(db: Session, build_id: uuid.UUID) -> Build:
    build = _get_build(db, build_id)
    if build.status != BuildStatus.RUNNING:
        raise Conflict("BUILD_NOT_RUNNING", f"Build đang ở trạng thái {build.status.value}")
    return build


def append_log(build_id: uuid.UUID, text: str) -> None:
    if not text:
        return
    key = LOG_KEY.format(id=build_id)
    r = get_redis()
    try:
        if (r.strlen(key) or 0) > LIVE_LOG_MAX_BYTES:
            return
        pipe = r.pipeline()
        pipe.append(key, text)
        pipe.expire(key, LIVE_LOG_TTL)
        pipe.execute()
    except redis.RedisError:
        log.warning("cannot append live log", extra={"build_id": str(build_id)})


def finish_build(db: Session, build_id: uuid.UUID, body: BuildFinishIn) -> Build:
    if body.status not in FINISHED_BUILD_STATUSES:
        raise BadRequest("VALIDATION_ERROR", "status kết thúc phải là SUCCESS/FAILED/CANCELLED")
    build = _get_build(db, build_id, lock=True)
    if build.status in FINISHED_BUILD_STATUSES:
        return build  # idempotent: runner gửi lại
    now = datetime.now(timezone.utc)
    build.status = body.status
    build.finished_at = now
    started = build.started_at or build.created_at
    build.duration_seconds = max(0, int((now - started).total_seconds()))
    build.error_code = body.error_code if body.status != BuildStatus.SUCCESS else None
    build.error_message = body.error_message if body.status != BuildStatus.SUCCESS else None
    build.steps_json = [st.model_dump() for st in body.steps]
    full_log = body.log if body.log is not None else _live_log(build.id)
    if full_log:
        key = f"builds/{build.project_id}/{build.id}.log"
        try:
            storage_service.put_bytes(get_settings().minio_bucket_logs, key, full_log.encode("utf-8"),
                                      "text/plain; charset=utf-8")
            build.log_object_key = key
        except Exception:  # noqa: BLE001 — mất log không được làm hỏng trạng thái build
            log.exception("cannot store build log", extra={"build_id": str(build.id)})
    token_service.revoke_build_tokens(db, build.id)
    build.project.last_build_status = body.status
    action = {BuildStatus.SUCCESS: A.BUILD_SUCCESS, BuildStatus.FAILED: A.BUILD_FAILED,
              BuildStatus.CANCELLED: A.BUILD_CANCELLED}[body.status]
    audit_service.record(db, action, actor_type=ActorType.SERVICE, resource_type="build", resource_id=build.id,
                         metadata={"error_code": build.error_code, "duration": build.duration_seconds})
    db.commit()
    BUILDS_TOTAL.labels(status=body.status.value, trigger=build.trigger_type.value).inc()
    BUILD_DURATION.observe(build.duration_seconds or 0)
    if body.status == BuildStatus.FAILED:
        BUILD_FAILURES.labels(error_code=build.error_code or "UNKNOWN").inc()
    try:
        get_redis().delete(CANCEL_KEY.format(id=build.id))
    except redis.RedisError:
        pass
    return build


def _cancel_requested(build_id: uuid.UUID) -> bool:
    try:
        return bool(get_redis().exists(CANCEL_KEY.format(id=build_id)))
    except redis.RedisError:
        return False


def control(db: Session, build_id: uuid.UUID) -> dict:
    build = _get_build(db, build_id)
    return {"status": build.status.value,
            "cancel": build.status == BuildStatus.CANCELLED or _cancel_requested(build_id)}


def cancel_build(db: Session, build: Build, user_id: uuid.UUID | None, request=None) -> Build:
    if build.status in FINISHED_BUILD_STATUSES:
        raise Conflict("BUILD_FINISHED", "Build đã kết thúc")
    try:
        get_redis().set(CANCEL_KEY.format(id=build.id), "1", ex=86400)
    except redis.RedisError as exc:
        raise AppError("QUEUE_UNAVAILABLE", "Không gửi được yêu cầu huỷ (Redis lỗi)", 503) from exc
    if build.status == BuildStatus.QUEUED:
        now = datetime.now(timezone.utc)
        build.status = BuildStatus.CANCELLED
        build.finished_at = now
        build.duration_seconds = 0
        build.error_code = "CANCELLED"
        build.error_message = "Huỷ trước khi chạy"
        build.project.last_build_status = BuildStatus.CANCELLED
    audit_service.record(db, A.BUILD_CANCELLED, user_id=user_id, resource_type="build", resource_id=build.id,
                         request=request, metadata={"status_at_cancel": build.status.value})
    db.commit()
    return build


def _live_log(build_id: uuid.UUID) -> str:
    try:
        return get_redis().get(LOG_KEY.format(id=build_id)) or ""
    except redis.RedisError:
        return ""


def read_log(build: Build, offset: int = 0) -> tuple[str, bool]:
    """Trả (log từ offset, đã_hoàn_tất)."""
    finished = build.status in FINISHED_BUILD_STATUSES
    if build.log_object_key:
        try:
            text = storage_service.get_bytes(get_settings().minio_bucket_logs, build.log_object_key).decode(
                "utf-8", "replace")
            return text[offset:], True
        except Exception:  # noqa: BLE001
            log.exception("cannot read build log", extra={"build_id": str(build.id)})
    return _live_log(build.id)[offset:], finished


def add_artifacts(db: Session, build: Build, artifacts: list[dict]) -> None:
    build.artifacts_json = [*(build.artifacts_json or []), *artifacts]
    db.commit()


# ───────────── bảo trì ─────────────

def requeue_stale(db: Session) -> dict:
    """QUEUED quá lâu (Redis mất message) → gửi lại; RUNNING quá hạn (runner chết) → FAILED RUNNER_ERROR."""
    s = get_settings()
    now = datetime.now(timezone.utc)
    requeued, failed = 0, 0
    for b in db.scalars(select(Build).where(Build.status == BuildStatus.QUEUED,
                                            Build.created_at < now - timedelta(minutes=s.build_stale_queue_minutes))):
        if queue_service.enqueue_build(b.id):
            requeued += 1
    deadline = now - timedelta(seconds=s.build_timeout_seconds + 600)
    for b in db.scalars(select(Build).where(Build.status == BuildStatus.RUNNING, Build.started_at < deadline)):
        finish_build(db, b.id, BuildFinishIn(status=BuildStatus.FAILED, error_code="RUNNER_ERROR",
                                             error_message="Runner không báo kết quả trong thời hạn (runner chết?)"))
        failed += 1
    return {"requeued": requeued, "failed_stuck": failed}


def to_out(db: Session, build: Build) -> BuildOut:
    out = BuildOut.model_validate(build)
    out.artifacts = build.artifacts_json or []
    out.steps = build.steps_json or []
    if build.project is not None and build.project.group is not None:
        out.project_path = f"{build.project.group.slug}/{build.project.slug}"
    if build.runner_id:
        runner = db.get(RunnerNode, build.runner_id)
        out.runner_name = runner.name if runner else None
    if build.created_by:
        user = db.get(User, build.created_by)
        out.created_by_username = user.username if user else None
    return out
