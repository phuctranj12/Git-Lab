from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAt, Timestamps, UUIDPk
from app.db.models.enums import (
    ActorType,
    BuildStatus,
    GroupRole,
    PackageFileType,
    PackageType,
    ProjectLanguage,
    ProjectRole,
    RunnerStatus,
    TokenType,
    TriggerType,
    UpstreamSource,
    Visibility,
)


def _enum(cls: type, length: int = 20) -> Enum:
    # varchar thay vì native PG enum → thêm giá trị mới (npm/nuget…) không cần ALTER TYPE.
    return Enum(cls, native_enum=False, length=length, validate_strings=True,
                values_callable=lambda e: [m.value for m in e])


# ─────────────────────────────── users / auth ───────────────────────────────

class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    auth_provider: Mapped[str] = mapped_column(String(50), nullable=False, default="local", server_default="local")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    is_system_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ux_users_username", "username", unique=True),
        Index("ux_users_email", "email", unique=True),
    )


class RefreshToken(UUIDPk, CreatedAt, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_refresh_tokens_user_id", "user_id"),)


# ─────────────────────────────── groups ───────────────────────────────

class Group(UUIDPk, Timestamps, Base):
    __tablename__ = "groups"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[Visibility] = mapped_column(_enum(Visibility), nullable=False, default=Visibility.INTERNAL)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    members: Mapped[list[GroupMember]] = relationship(back_populates="group", cascade="all, delete-orphan")


class GroupMember(UUIDPk, CreatedAt, Base):
    __tablename__ = "group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[GroupRole] = mapped_column(_enum(GroupRole), nullable=False)

    group: Mapped[Group] = relationship(back_populates="members")
    user: Mapped[User] = relationship()

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_members_group_user"),
        Index("ix_group_members_user_id", "user_id"),
    )


# ─────────────────────────────── projects ───────────────────────────────

class Project(UUIDPk, Timestamps, Base):
    __tablename__ = "projects"

    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[ProjectLanguage] = mapped_column(_enum(ProjectLanguage), nullable=False, default=ProjectLanguage.PYTHON)
    visibility: Mapped[Visibility] = mapped_column(_enum(Visibility), nullable=False, default=Visibility.INTERNAL)
    default_branch: Mapped[str] = mapped_column(String(100), nullable=False, default="main", server_default="main")
    package_name: Mapped[str | None] = mapped_column(String(255))
    package_prefix_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    # True = chỉ Maintainer+ push được nhánh mặc định (spec 8.4 "push main nếu project policy cho phép").
    protect_default_branch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    repo_path: Mapped[str] = mapped_column(Text, nullable=False)
    repository_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    latest_commit_sha: Mapped[str | None] = mapped_column(String(64))
    latest_release_version: Mapped[str | None] = mapped_column(String(100))
    latest_release_build_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_build_status: Mapped[BuildStatus | None] = mapped_column(_enum(BuildStatus))
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    build_counter: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    group: Mapped[Group] = relationship()

    __table_args__ = (
        UniqueConstraint("group_id", "slug", name="uq_projects_group_slug"),
        Index("ux_projects_package_name_active", "package_name", unique=True,
              postgresql_where=text("archived = false AND package_name IS NOT NULL")),
    )


class ProjectMember(UUIDPk, CreatedAt, Base):
    __tablename__ = "project_members"

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[ProjectRole] = mapped_column(_enum(ProjectRole), nullable=False)

    user: Mapped[User] = relationship()

    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
        Index("ix_project_members_user_id", "user_id"),
    )


# ─────────────────────────────── ssh keys / tokens ───────────────────────────────

class SshKey(UUIDPk, CreatedAt, Base):
    __tablename__ = "ssh_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_type: Mapped[str] = mapped_column(String(50), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()

    __table_args__ = (
        # Fingerprint duy nhất trong số key còn hiệu lực (key đã thu hồi có thể thêm lại).
        Index("ux_ssh_keys_fingerprint_active", "fingerprint", unique=True, postgresql_where=text("revoked_at IS NULL")),
        Index("ix_ssh_keys_user_id", "user_id"),
    )


class AccessToken(UUIDPk, CreatedAt, Base):
    __tablename__ = "access_tokens"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    build_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_type: Mapped[TokenType] = mapped_column(_enum(TokenType), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (
        Index("ix_access_tokens_user_id", "user_id"),
        Index("ix_access_tokens_project_id", "project_id"),
    )


# ─────────────────────────────── builds ───────────────────────────────

class Build(UUIDPk, CreatedAt, Base):
    __tablename__ = "builds"

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_type: Mapped[TriggerType] = mapped_column(_enum(TriggerType), nullable=False)
    ref_name: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_version: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[BuildStatus] = mapped_column(_enum(BuildStatus), nullable=False, default=BuildStatus.QUEUED)
    publish_package: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    runner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("runner_nodes.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    log_object_key: Mapped[str | None] = mapped_column(Text)
    artifacts_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    steps_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    project: Mapped[Project] = relationship()

    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_builds_project_number"),
        Index("ix_builds_project_created", "project_id", text("created_at DESC")),
        Index("ix_builds_status", "status"),
        Index("ix_builds_commit_sha", "commit_sha"),
    )


class GitPushEvent(CreatedAt, Base):
    """Chống xử lý trùng khi hook gửi lại event từ spool."""

    __tablename__ = "git_push_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


# ─────────────────────────────── packages ───────────────────────────────

class PackageVersion(UUIDPk, CreatedAt, Base):
    __tablename__ = "package_versions"

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    package_type: Mapped[PackageType] = mapped_column(_enum(PackageType), nullable=False, default=PackageType.PYPI,
                                                      server_default=PackageType.PYPI.value)
    package_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    build_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("builds.id", ondelete="SET NULL"))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    git_tag: Mapped[str | None] = mapped_column(String(255))
    is_yanked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    yanked_reason: Mapped[str | None] = mapped_column(Text)
    yanked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    yanked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    download_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    files: Mapped[list[PackageFile]] = relationship(back_populates="package_version", cascade="all, delete-orphan",
                                                    order_by="PackageFile.filename")
    project: Mapped[Project] = relationship()

    __table_args__ = (
        UniqueConstraint("package_type", "normalized_name", "version", name="uq_package_versions_type_name_version"),
        Index("ix_package_versions_normalized_name", "normalized_name"),
        Index("ix_package_versions_project_id", "project_id"),
    )


class PackageFile(UUIDPk, Base):
    __tablename__ = "package_files"

    package_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("package_versions.id", ondelete="CASCADE"),
                                                          nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[PackageFileType] = mapped_column(_enum(PackageFileType), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    python_requires: Mapped[str | None] = mapped_column(String(100))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    package_version: Mapped[PackageVersion] = relationship(back_populates="files")

    __table_args__ = (
        Index("ux_package_files_filename", "filename", unique=True),
    )


class UpstreamPackage(UUIDPk, Base):
    __tablename__ = "upstream_packages"

    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    source: Mapped[UpstreamSource] = mapped_column(_enum(UpstreamSource), nullable=False, default=UpstreamSource.PYPI)
    upstream_etag: Mapped[str | None] = mapped_column(String(255))
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class UpstreamFile(UUIDPk, Base):
    __tablename__ = "upstream_files"

    upstream_package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("upstream_packages.id", ondelete="CASCADE"),
                                                           nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    version: Mapped[str | None] = mapped_column(String(100))
    upstream_url: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    requires_python: Mapped[str | None] = mapped_column(String(255))
    yanked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    upstream_package: Mapped[UpstreamPackage] = relationship()

    __table_args__ = (
        UniqueConstraint("upstream_package_id", "filename", name="uq_upstream_files_package_filename"),
        Index("ix_upstream_files_cached_access", "cached", "last_accessed_at"),
    )


# ─────────────────────────────── audit / runners ───────────────────────────────

class AuditLog(UUIDPk, CreatedAt, Base):
    __tablename__ = "audit_logs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # không FK: audit phải sống sót khi user bị xoá
    actor_type: Mapped[ActorType] = mapped_column(_enum(ActorType), nullable=False)
    actor_name: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_audit_logs_created_at", text("created_at DESC")),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )


class RunnerNode(UUIDPk, CreatedAt, Base):
    __tablename__ = "runner_nodes"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    hostname: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[RunnerStatus] = mapped_column(_enum(RunnerStatus), nullable=False, default=RunnerStatus.ONLINE)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[str | None] = mapped_column(String(50))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    current_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


__all__ = [
    "AccessToken", "AuditLog", "Build", "GitPushEvent", "Group", "GroupMember", "PackageFile", "PackageVersion",
    "Project", "ProjectMember", "RefreshToken", "RunnerNode", "SshKey", "UpstreamFile", "UpstreamPackage", "User",
]
