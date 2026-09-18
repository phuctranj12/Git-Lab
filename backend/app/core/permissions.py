"""Luật phân quyền thuần (không đụng DB) — spec mục 8.

Mỗi user có một *cấp* hiệu lực trên project = max(role trong group, role override trong project),
cộng thêm VIEWER ngầm định nếu project INTERNAL. System admin luôn ở cấp cao nhất.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from app.db.models.enums import GroupRole, ProjectRole, Visibility


class Level(enum.IntEnum):
    NONE = 0
    VIEWER = 10
    DEVELOPER = 20
    MAINTAINER = 30
    OWNER = 40
    ADMIN = 100


ROLE_LEVEL: dict[str, Level] = {
    "VIEWER": Level.VIEWER,
    "DEVELOPER": Level.DEVELOPER,
    "MAINTAINER": Level.MAINTAINER,
    "OWNER": Level.OWNER,
}


class Perm(str, enum.Enum):
    PROJECT_READ = "project.read"
    REPO_READ = "repo.read"
    REPO_WRITE = "repo.write"
    PUSH_TAG = "repo.push_tag"
    BUILD_VIEW = "build.view"
    BUILD_LOG = "build.log"
    BUILD_RUN = "build.run"
    BUILD_CANCEL = "build.cancel"
    PACKAGE_READ = "package.read"
    PACKAGE_YANK = "package.yank"
    PROJECT_UPDATE = "project.update"
    PROJECT_MEMBERS = "project.members"
    PROJECT_TOKENS = "project.tokens"
    PROJECT_ARCHIVE = "project.archive"
    PROJECT_DELETE = "project.delete"


PROJECT_PERM_LEVEL: dict[Perm, Level] = {
    Perm.PROJECT_READ: Level.VIEWER,
    Perm.REPO_READ: Level.VIEWER,
    Perm.PACKAGE_READ: Level.VIEWER,
    Perm.BUILD_VIEW: Level.VIEWER,
    Perm.BUILD_LOG: Level.DEVELOPER,
    Perm.REPO_WRITE: Level.DEVELOPER,
    Perm.BUILD_RUN: Level.DEVELOPER,
    Perm.PUSH_TAG: Level.MAINTAINER,
    Perm.BUILD_CANCEL: Level.MAINTAINER,
    Perm.PACKAGE_YANK: Level.MAINTAINER,
    Perm.PROJECT_UPDATE: Level.MAINTAINER,
    Perm.PROJECT_MEMBERS: Level.MAINTAINER,
    Perm.PROJECT_TOKENS: Level.MAINTAINER,
    Perm.PROJECT_ARCHIVE: Level.OWNER,
    Perm.PROJECT_DELETE: Level.ADMIN,
}


class GroupPerm(str, enum.Enum):
    READ = "group.read"
    MANAGE = "group.manage"            # sửa group, thêm/xoá/đổi role thành viên
    CREATE_PROJECT = "group.create_project"


GROUP_PERM_LEVEL: dict[GroupPerm, Level] = {
    GroupPerm.READ: Level.VIEWER,
    GroupPerm.MANAGE: Level.OWNER,
    GroupPerm.CREATE_PROJECT: Level.OWNER,
}


# ── Token scopes (spec 7.7) ──
class Scope(str, enum.Enum):
    READ_API = "read_api"
    WRITE_API = "write_api"
    READ_REPOSITORY = "read_repository"
    WRITE_REPOSITORY = "write_repository"
    READ_PACKAGE = "read_package"
    WRITE_PACKAGE = "write_package"
    ADMIN = "admin"


ALL_SCOPES = frozenset(s.value for s in Scope)
# Scope bao hàm scope khác.
_IMPLIES: dict[str, set[str]] = {
    Scope.WRITE_API.value: {Scope.READ_API.value},
    Scope.WRITE_REPOSITORY.value: {Scope.READ_REPOSITORY.value},
    Scope.WRITE_PACKAGE.value: {Scope.READ_PACKAGE.value},
    Scope.ADMIN.value: set(ALL_SCOPES),
}


def expand_scopes(scopes: list[str] | set[str] | frozenset[str]) -> frozenset[str]:
    out: set[str] = set()
    for s in scopes:
        out.add(s)
        out |= _IMPLIES.get(s, set())
    return frozenset(out)


@dataclass(frozen=True)
class Membership:
    """Ảnh chụp quan hệ user ↔ project cần để quyết định quyền."""

    is_admin: bool = False
    group_role: GroupRole | None = None
    project_role: ProjectRole | None = None
    project_visibility: Visibility = Visibility.PRIVATE
    group_visibility: Visibility = Visibility.PRIVATE
    authenticated: bool = True
    # Token gắn project (SERVICE/RUNNER): được VIEWER trên project đó + các project cùng group.
    bound_project_viewer: bool = False
    extra: dict = field(default_factory=dict)


def project_level(m: Membership) -> Level:
    if not m.authenticated:
        return Level.NONE
    if m.is_admin:
        return Level.ADMIN
    level = Level.NONE
    if m.group_role is not None:
        level = max(level, ROLE_LEVEL[str(m.group_role.value)])
    if m.project_role is not None:
        level = max(level, ROLE_LEVEL[str(m.project_role.value)])
    if m.project_visibility == Visibility.INTERNAL and m.group_visibility == Visibility.INTERNAL:
        level = max(level, Level.VIEWER)
    if m.bound_project_viewer:
        level = max(level, Level.VIEWER)
    return level


def group_level(is_admin: bool, group_role: GroupRole | None, group_visibility: Visibility,
                authenticated: bool = True) -> Level:
    if not authenticated:
        return Level.NONE
    if is_admin:
        return Level.ADMIN
    level = ROLE_LEVEL[str(group_role.value)] if group_role is not None else Level.NONE
    if group_visibility == Visibility.INTERNAL:
        level = max(level, Level.VIEWER)
    return level


def can_project(level: Level, perm: Perm) -> bool:
    return level >= PROJECT_PERM_LEVEL[perm]


def can_group(level: Level, perm: GroupPerm) -> bool:
    return level >= GROUP_PERM_LEVEL[perm]


def can_push_ref(level: Level, ref: str, default_branch: str, protect_default_branch: bool) -> tuple[bool, str]:
    """Quyết định từng ref update khi push (pre-receive). Trả (allowed, lý do)."""
    if level < Level.DEVELOPER:
        return False, "Cần quyền Developer trở lên để push"
    if ref.startswith("refs/tags/"):
        if level < Level.MAINTAINER:
            return False, "Chỉ Maintainer trở lên được tạo/sửa tag (release)"
        return True, ""
    if ref.startswith("refs/heads/"):
        branch = ref[len("refs/heads/"):]
        if branch == default_branch and protect_default_branch and level < Level.MAINTAINER:
            return False, f"Nhánh '{branch}' được bảo vệ — chỉ Maintainer trở lên được push"
        return True, ""
    return False, f"Không cho phép push ref '{ref}'"


def max_grantable_project_role(level: Level) -> Level:
    """Maintainer chỉ cấp tới Maintainer; không ai tự nâng mình vượt cấp."""
    return min(level, Level.MAINTAINER)
