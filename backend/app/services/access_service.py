from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.core.deps import Principal
from app.core.errors import NotFound, PermissionDenied
from app.core.permissions import (
    GroupPerm,
    Level,
    Membership,
    Perm,
    can_group,
    can_project,
    group_level,
    project_level,
)
from app.db.models import Group, GroupMember, Project, ProjectMember
from app.db.models.enums import GroupRole, ProjectRole, Visibility


def _group_role(db: Session, group_id: uuid.UUID, user_id: uuid.UUID | None) -> GroupRole | None:
    if user_id is None:
        return None
    return db.scalar(select(GroupMember.role).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id))


def _project_role(db: Session, project_id: uuid.UUID, user_id: uuid.UUID | None) -> ProjectRole | None:
    if user_id is None:
        return None
    return db.scalar(select(ProjectMember.role).where(ProjectMember.project_id == project_id,
                                                      ProjectMember.user_id == user_id))


def _bound_viewer(db: Session, principal: Principal, project: Project) -> bool:
    bound = principal.bound_project_id
    if bound is None:
        return False
    if bound == project.id:
        return True
    bound_group = db.scalar(select(Project.group_id).where(Project.id == bound))
    return bound_group == project.group_id


def user_project_level(db: Session, user_id: uuid.UUID, is_admin: bool, project: Project) -> Level:
    group = project.group
    return project_level(Membership(
        is_admin=is_admin,
        group_role=_group_role(db, project.group_id, user_id),
        project_role=_project_role(db, project.id, user_id),
        project_visibility=project.visibility,
        group_visibility=group.visibility,
    ))


def principal_project_level(db: Session, principal: Principal | None, project: Project) -> Level:
    if principal is None:
        return Level.NONE
    user_id = principal.user_id
    return project_level(Membership(
        is_admin=principal.is_admin,
        group_role=_group_role(db, project.group_id, user_id),
        project_role=_project_role(db, project.id, user_id),
        project_visibility=project.visibility,
        group_visibility=project.group.visibility,
        bound_project_viewer=_bound_viewer(db, principal, project),
    ))


def principal_group_level(db: Session, principal: Principal | None, group: Group) -> Level:
    if principal is None:
        return Level.NONE
    return group_level(principal.is_admin, _group_role(db, group.id, principal.user_id), group.visibility)


def require_project(db: Session, principal: Principal | None, project: Project, perm: Perm) -> Level:
    level = principal_project_level(db, principal, project)
    if level < Level.VIEWER:
        # Không lộ sự tồn tại của project private.
        raise NotFound("PROJECT_NOT_FOUND", "Không tìm thấy project")
    if not can_project(level, perm):
        raise PermissionDenied()
    return level


def require_group(db: Session, principal: Principal | None, group: Group, perm: GroupPerm) -> Level:
    level = principal_group_level(db, principal, group)
    if level < Level.VIEWER:
        raise NotFound("GROUP_NOT_FOUND", "Không tìm thấy group")
    if not can_group(level, perm):
        raise PermissionDenied()
    return level


def visible_projects_filter(stmt: Select, principal: Principal) -> Select:
    """Thêm điều kiện WHERE để chỉ lấy project principal được xem (danh sách/catalogue/registry)."""
    if principal.is_admin:
        return stmt
    conds = [(Project.visibility == Visibility.INTERNAL) & (Group.visibility == Visibility.INTERNAL)]
    uid = principal.user_id
    if uid is not None:
        conds.append(Project.group_id.in_(select(GroupMember.group_id).where(GroupMember.user_id == uid)))
        conds.append(Project.id.in_(select(ProjectMember.project_id).where(ProjectMember.user_id == uid)))
    bound = principal.bound_project_id
    if bound is not None:
        conds.append(Project.id == bound)
        conds.append(Project.group_id.in_(select(Project.group_id).where(Project.id == bound)))
    return stmt.where(or_(*conds))


def visible_groups_filter(stmt: Select, principal: Principal) -> Select:
    if principal.is_admin:
        return stmt
    conds = [Group.visibility == Visibility.INTERNAL]
    if principal.user_id is not None:
        conds.append(Group.id.in_(select(GroupMember.group_id).where(GroupMember.user_id == principal.user_id)))
    return stmt.where(or_(*conds))
