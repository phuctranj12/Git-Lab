from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.db.models.enums import (
    BuildStatus,
    GroupRole,
    PackageFileType,
    ProjectLanguage,
    ProjectRole,
    RunnerStatus,
    TokenType,
    TriggerType,
    Visibility,
)

T = TypeVar("T")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def _email(v: str) -> str:
    """Email nội bộ: cho phép domain .local/.lan (email-validator chuẩn từ chối special-use TLD)."""
    v = v.strip().lower()
    if len(v) > 255 or not _EMAIL_RE.match(v):
        raise ValueError("Email không hợp lệ")
    return v


Email = Annotated[str, AfterValidator(_email)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class Message(BaseModel):
    message: str


# ───────────── users / auth ─────────────

class UserOut(ORM):
    id: uuid.UUID
    username: str
    email: str
    full_name: str | None
    is_active: bool
    is_system_admin: bool
    last_login_at: datetime | None
    created_at: datetime


class UserBrief(ORM):
    id: uuid.UUID
    username: str
    full_name: str | None
    email: str


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=255, description="username hoặc email")
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=8, max_length=1024)


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    email: Email
    full_name: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=8, max_length=1024)
    is_system_admin: bool = False


class UserUpdate(BaseModel):
    email: Email | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=1024)
    is_system_admin: bool | None = None
    is_active: bool | None = None


# ───────────── ssh keys / tokens ─────────────

class SshKeyCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    public_key: str = Field(min_length=20, max_length=16384)


class SshKeyOut(ORM):
    id: uuid.UUID
    title: str
    fingerprint: str
    key_type: str
    last_used_at: datetime | None
    created_at: datetime


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scopes: list[str] = Field(min_length=1)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class TokenOut(ORM):
    id: uuid.UUID
    name: str
    token_prefix: str
    token_type: TokenType
    scopes: list[str]
    project_id: uuid.UUID | None
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class TokenCreated(TokenOut):
    token: str = Field(description="Chỉ hiển thị MỘT lần")


# ───────────── groups ─────────────

class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    visibility: Visibility = Visibility.INTERNAL
    owner_id: uuid.UUID | None = Field(default=None, description="Owner ban đầu; mặc định là người tạo")


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    visibility: Visibility | None = None


class GroupOut(ORM):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    visibility: Visibility
    created_at: datetime
    project_count: int = 0
    member_count: int = 0
    my_role: GroupRole | None = None
    can_manage: bool = False


class MemberIn(BaseModel):
    user_id: uuid.UUID
    role: GroupRole


class MemberRoleIn(BaseModel):
    role: GroupRole


class GroupMemberOut(ORM):
    user: UserBrief
    role: GroupRole
    created_at: datetime


class ProjectMemberIn(BaseModel):
    user_id: uuid.UUID
    role: ProjectRole


class ProjectMemberOut(ORM):
    user: UserBrief
    role: str
    source: str  # "group" | "project"
    created_at: datetime | None = None


# ───────────── projects ─────────────

class ProjectCreate(BaseModel):
    group: str = Field(min_length=1, max_length=100, description="slug group")
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=10000)
    language: ProjectLanguage = ProjectLanguage.PYTHON
    visibility: Visibility = Visibility.INTERNAL
    default_branch: str = Field(default="main", min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._/-]+$")
    package_name: str | None = Field(default=None, max_length=255)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    visibility: Visibility | None = None
    default_branch: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._/-]+$")
    protect_default_branch: bool | None = None


class ProjectOut(ORM):
    id: uuid.UUID
    group_slug: str
    group_name: str
    name: str
    slug: str
    full_path: str
    description: str | None
    language: ProjectLanguage
    visibility: Visibility
    default_branch: str
    protect_default_branch: bool
    package_name: str | None
    package_prefix_valid: bool
    repository_size_bytes: int
    latest_commit_sha: str | None
    latest_release_version: str | None
    last_build_status: BuildStatus | None
    archived: bool
    created_at: datetime
    updated_at: datetime
    clone_url: str
    install_command: str | None
    python_requires: str | None = None
    my_level: str = "NONE"
    permissions: list[str] = []


# ───────────── builds ─────────────

class BuildOut(ORM):
    id: uuid.UUID
    project_id: uuid.UUID
    project_path: str | None = None
    number: int
    trigger_type: TriggerType
    ref_name: str
    commit_sha: str
    requested_version: str | None
    status: BuildStatus
    publish_package: bool
    runner_name: str | None = None
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: int | None
    error_code: str | None
    error_message: str | None
    artifacts: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    created_by_username: str | None = None
    created_at: datetime


class ManualBuildIn(BaseModel):
    ref: str | None = Field(default=None, max_length=255, description="branch hoặc tag; mặc định default branch")


# ───────────── packages ─────────────

class PackageFileOut(ORM):
    id: uuid.UUID
    filename: str
    file_type: PackageFileType
    size_bytes: int
    sha256: str
    python_requires: str | None
    uploaded_at: datetime
    download_url: str = ""


class PackageVersionOut(ORM):
    id: uuid.UUID
    package_name: str
    normalized_name: str
    version: str
    git_tag: str | None
    commit_sha: str | None
    build_id: uuid.UUID | None
    build_number: int | None = None
    is_yanked: bool
    yanked_reason: str | None
    yanked_at: datetime | None
    download_count: int
    metadata_json: dict[str, Any]
    created_at: datetime
    files: list[PackageFileOut] = []


class PackageSummary(BaseModel):
    package_name: str
    normalized_name: str
    project_path: str
    project_name: str
    description: str | None
    latest_version: str | None
    version_count: int
    download_count: int
    last_published_at: datetime | None


class YankIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    yank: bool = True


# ───────────── audit / runners ─────────────

class AuditOut(ORM):
    id: uuid.UUID
    user_id: uuid.UUID | None
    actor_type: str
    actor_name: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    ip_address: Any = None
    user_agent: str | None
    request_id: str | None
    metadata_json: dict[str, Any]
    created_at: datetime

    @field_validator("ip_address", mode="before")
    @classmethod
    def _ip(cls, v: Any) -> str | None:
        return str(v) if v is not None else None


class RunnerOut(ORM):
    id: uuid.UUID
    name: str
    hostname: str | None
    status: RunnerStatus
    labels: dict[str, Any]
    version: str | None
    last_heartbeat_at: datetime | None
    max_concurrent_jobs: int
    current_jobs: int
    online: bool = False


class RunnerUpdate(BaseModel):
    status: RunnerStatus


# ───────────── internal (git-service / runner) ─────────────

class GitAuthorizeIn(BaseModel):
    key_id: uuid.UUID
    action: str = Field(pattern=r"^(read|write)$")
    repository: str = Field(min_length=3, max_length=300)


class GitAuthorizeOut(BaseModel):
    allowed: bool
    message: str = ""
    user_id: uuid.UUID | None = None
    username: str | None = None
    project_id: uuid.UUID | None = None
    repo_path: str | None = None


class RefUpdate(BaseModel):
    old_sha: str = Field(min_length=40, max_length=64)
    new_sha: str = Field(min_length=40, max_length=64)
    ref: str = Field(min_length=1, max_length=500)


class GitPushAuthorizeIn(BaseModel):
    user_id: uuid.UUID
    project_id: uuid.UUID
    updates: list[RefUpdate]


class GitEventIn(BaseModel):
    event_id: uuid.UUID
    project_id: uuid.UUID | None = None
    repository_path: str
    user_id: uuid.UUID | None = None
    updates: list[RefUpdate]


class RunnerHeartbeatIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    hostname: str | None = Field(default=None, max_length=255)
    labels: dict[str, Any] = {}
    version: str | None = Field(default=None, max_length=50)
    max_concurrent_jobs: int = Field(default=2, ge=1, le=256)
    current_jobs: int = Field(default=0, ge=0)


class BuildStartIn(BaseModel):
    runner_name: str = Field(min_length=1, max_length=255)


class BuildStartOut(BaseModel):
    build_id: uuid.UUID
    project_id: uuid.UUID
    project_path: str
    package_name: str | None
    internal_prefix: str
    trigger_type: TriggerType
    ref_name: str
    commit_sha: str
    requested_version: str | None
    publish_package: bool
    build_token: str
    timeout_seconds: int
    cpu_limit: float
    memory_limit: str


class BuildLogIn(BaseModel):
    lines: str = Field(max_length=2_000_000)


class BuildStepIn(BaseModel):
    name: str
    status: str
    duration_seconds: float | None = None


class BuildFinishIn(BaseModel):
    status: BuildStatus
    error_code: str | None = None
    error_message: str | None = Field(default=None, max_length=10000)
    steps: list[BuildStepIn] = []
    log: str | None = Field(default=None, description="Toàn bộ log cuối cùng")


class BuildSourceMeta(BaseModel):
    commit_sha: str
