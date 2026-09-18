from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.core.deps import AdminPrincipal, CurrentPrincipal, DbDep
from app.core.errors import BadRequest, Conflict, NotFound
from app.core.permissions import GroupPerm, Level, can_group
from app.db.models import Group, GroupMember
from app.db.models.enums import GroupRole
from app.schemas import GroupCreate, GroupMemberOut, GroupOut, GroupUpdate, MemberIn, MemberRoleIn, UserBrief
from app.services import access_service, audit_service, project_service
from app.services.audit_service import A
from app.utils.naming import is_valid_slug

router = APIRouter(prefix="/groups", tags=["groups"])


def _group_out(db, group: Group, principal) -> GroupOut:
    projects, members = project_service.group_counts(db, group.id)
    my_role = None
    if principal.user_id is not None:
        my_role = db.scalar(select(GroupMember.role).where(GroupMember.group_id == group.id,
                                                           GroupMember.user_id == principal.user_id))
    level = access_service.principal_group_level(db, principal, group)
    out = GroupOut.model_validate(group)
    out.project_count, out.member_count, out.my_role = projects, members, my_role
    out.can_manage = can_group(level, GroupPerm.MANAGE)
    return out


@router.get("", response_model=list[GroupOut])
def list_groups(principal: CurrentPrincipal, db: DbDep, mine: bool = False) -> list[GroupOut]:
    stmt = access_service.visible_groups_filter(select(Group), principal)
    if mine and principal.user_id is not None:
        stmt = stmt.where(Group.id.in_(select(GroupMember.group_id).where(GroupMember.user_id == principal.user_id)))
    return [_group_out(db, g, principal) for g in db.scalars(stmt.order_by(Group.name))]


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupCreate, request: Request, principal: AdminPrincipal, db: DbDep) -> GroupOut:
    slug = body.slug.strip().lower()
    if not is_valid_slug(slug):
        raise BadRequest("INVALID_SLUG", "Slug chỉ gồm a-z, 0-9, '-' (không bắt đầu/kết thúc bằng '-')")
    group = Group(name=body.name.strip(), slug=slug, description=body.description, visibility=body.visibility,
                  created_by=principal.user_id)
    db.add(group)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("GROUP_EXISTS", "Slug group đã tồn tại") from exc
    owner_id = body.owner_id or principal.user_id
    if owner_id is not None:
        owner = project_service.user_by_id(db, owner_id)
        db.add(GroupMember(group_id=group.id, user_id=owner.id, role=GroupRole.OWNER))
    audit_service.record(db, A.CREATE_GROUP, principal=principal, resource_type="group", resource_id=group.id,
                         request=request, metadata={"slug": slug, "owner_id": str(owner_id) if owner_id else None})
    db.commit()
    return _group_out(db, group, principal)


@router.get("/{slug}", response_model=GroupOut)
def get_group(slug: str, principal: CurrentPrincipal, db: DbDep) -> GroupOut:
    group = project_service.get_group_by_slug(db, slug)
    access_service.require_group(db, principal, group, GroupPerm.READ)
    return _group_out(db, group, principal)


@router.patch("/{slug}", response_model=GroupOut)
def update_group(slug: str, body: GroupUpdate, request: Request, principal: CurrentPrincipal, db: DbDep) -> GroupOut:
    group = project_service.get_group_by_slug(db, slug)
    access_service.require_group(db, principal, group, GroupPerm.MANAGE)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(group, key, value)
    audit_service.record(db, A.UPDATE_GROUP, principal=principal, resource_type="group", resource_id=group.id,
                         request=request, metadata={k: str(v) for k, v in changes.items()})
    db.commit()
    return _group_out(db, group, principal)


# ───────────── members ─────────────

def _member_out(m: GroupMember) -> GroupMemberOut:
    return GroupMemberOut(user=UserBrief.model_validate(m.user), role=m.role, created_at=m.created_at)


@router.get("/{slug}/members", response_model=list[GroupMemberOut])
def list_members(slug: str, principal: CurrentPrincipal, db: DbDep) -> list[GroupMemberOut]:
    group = project_service.get_group_by_slug(db, slug)
    access_service.require_group(db, principal, group, GroupPerm.READ)
    rows = db.scalars(select(GroupMember).options(joinedload(GroupMember.user))
                      .where(GroupMember.group_id == group.id)).all()
    order = {GroupRole.OWNER: 0, GroupRole.MAINTAINER: 1, GroupRole.DEVELOPER: 2, GroupRole.VIEWER: 3}
    rows = sorted(rows, key=lambda m: (order[m.role], m.user.username))
    return [_member_out(m) for m in rows]


def _owner_count(db, group_id) -> int:
    return db.scalar(select(func.count()).select_from(GroupMember)
                     .where(GroupMember.group_id == group_id, GroupMember.role == GroupRole.OWNER)) or 0


@router.post("/{slug}/members", response_model=GroupMemberOut, status_code=status.HTTP_201_CREATED)
def add_member(slug: str, body: MemberIn, request: Request, principal: CurrentPrincipal, db: DbDep) -> GroupMemberOut:
    group = project_service.get_group_by_slug(db, slug)
    access_service.require_group(db, principal, group, GroupPerm.MANAGE)
    user = project_service.user_by_id(db, body.user_id)
    member = GroupMember(group_id=group.id, user_id=user.id, role=body.role)
    db.add(member)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("MEMBER_EXISTS", "User đã là thành viên group") from exc
    audit_service.record(db, A.ADD_GROUP_MEMBER, principal=principal, resource_type="group", resource_id=group.id,
                         request=request, metadata={"user_id": str(user.id), "username": user.username,
                                                    "role": body.role.value})
    db.commit()
    db.refresh(member)
    return _member_out(member)


def _get_member(db, group_id, user_id) -> GroupMember:
    member = db.scalar(select(GroupMember).options(joinedload(GroupMember.user))
                       .where(GroupMember.group_id == group_id, GroupMember.user_id == user_id))
    if member is None:
        raise NotFound("MEMBER_NOT_FOUND", "User không phải thành viên group")
    return member


@router.patch("/{slug}/members/{user_id}", response_model=GroupMemberOut)
def update_member(slug: str, user_id: uuid.UUID, body: MemberRoleIn, request: Request, principal: CurrentPrincipal,
                  db: DbDep) -> GroupMemberOut:
    group = project_service.get_group_by_slug(db, slug)
    access_service.require_group(db, principal, group, GroupPerm.MANAGE)
    member = _get_member(db, group.id, user_id)
    if member.role == GroupRole.OWNER and body.role != GroupRole.OWNER and _owner_count(db, group.id) <= 1:
        raise BadRequest("LAST_OWNER", "Group phải còn ít nhất một Owner")
    old = member.role
    member.role = body.role
    audit_service.record(db, A.UPDATE_GROUP_MEMBER, principal=principal, resource_type="group", resource_id=group.id,
                         request=request, metadata={"user_id": str(user_id), "from": old.value, "to": body.role.value})
    db.commit()
    return _member_out(member)


@router.delete("/{slug}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(slug: str, user_id: uuid.UUID, request: Request, principal: CurrentPrincipal, db: DbDep) -> None:
    group = project_service.get_group_by_slug(db, slug)
    level = access_service.principal_group_level(db, principal, group)
    self_leave = principal.user_id == user_id and level >= Level.VIEWER
    if not self_leave:
        access_service.require_group(db, principal, group, GroupPerm.MANAGE)
    member = _get_member(db, group.id, user_id)
    if member.role == GroupRole.OWNER and _owner_count(db, group.id) <= 1:
        raise BadRequest("LAST_OWNER", "Không thể xoá Owner cuối cùng của group")
    db.delete(member)
    audit_service.record(db, A.REMOVE_GROUP_MEMBER, principal=principal, resource_type="group", resource_id=group.id,
                         request=request, metadata={"user_id": str(user_id), "role": member.role.value})
    db.commit()
