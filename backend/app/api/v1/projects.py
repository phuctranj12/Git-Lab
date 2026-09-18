from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.core.deps import CurrentPrincipal, DbDep, require_user
from app.core.errors import BadRequest, Conflict, NotFound, PermissionDenied
from app.core.permissions import ROLE_LEVEL, GroupPerm, Level, Perm, max_grantable_project_role
from app.db.models import AccessToken, Group, GroupMember, PackageVersion, Project, ProjectMember
from app.db.models.enums import TokenType
from app.schemas import (
    Page,
    ProjectCreate,
    ProjectMemberIn,
    ProjectMemberOut,
    ProjectOut,
    ProjectUpdate,
    TokenCreate,
    TokenCreated,
    TokenOut,
    UserBrief,
)
from app.services import access_service, audit_service, git_service, project_service, token_service
from app.services.audit_service import A

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=Page[ProjectOut])
def list_projects(principal: CurrentPrincipal, db: DbDep, q: str | None = Query(None, max_length=200),
                  group: str | None = None, mine: bool = False, include_archived: bool = False,
                  language: str | None = None, sort: str = Query("updated", pattern="^(updated|name|created)$"),
                  page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100)) -> Page[ProjectOut]:
    stmt = access_service.visible_projects_filter(select(Project).join(Group), principal)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Project.name).like(like), func.lower(Project.slug).like(like),
                              func.lower(Project.package_name).like(like), func.lower(Project.description).like(like),
                              func.lower(Group.name).like(like), func.lower(Group.slug).like(like)))
    if group:
        stmt = stmt.where(Group.slug == group.lower())
    if language:
        stmt = stmt.where(Project.language == language.upper())
    if not include_archived:
        stmt = stmt.where(Project.archived.is_(False))
    if mine and principal.user_id is not None:
        uid = principal.user_id
        stmt = stmt.where(or_(Project.group_id.in_(select(GroupMember.group_id).where(GroupMember.user_id == uid)),
                              Project.id.in_(select(ProjectMember.project_id).where(ProjectMember.user_id == uid))))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = {"updated": Project.updated_at.desc(), "name": Project.name.asc(), "created": Project.created_at.desc()}
    rows = db.scalars(stmt.options(joinedload(Project.group)).order_by(order[sort])
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[project_service.to_out(db, p, principal) for p in rows], total=total, page=page,
                page_size=page_size)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, request: Request, principal: CurrentPrincipal, db: DbDep) -> ProjectOut:
    group = project_service.get_group_by_slug(db, body.group)
    access_service.require_group(db, principal, group, GroupPerm.CREATE_PROJECT)
    project = project_service.create_project(db, principal, group, body)
    try:
        audit_service.record(db, A.CREATE_PROJECT, principal=principal, resource_type="project",
                             resource_id=project.id, request=request,
                             metadata={"path": f"{group.slug}/{project.slug}", "package_name": project.package_name})
        db.commit()
    except Exception:
        db.rollback()
        git_service.remove_repository(project.repo_path)
        raise
    return project_service.to_out(db, project, principal)


def _load(db, group: str, project: str, principal, perm: Perm) -> tuple[Project, Level]:
    p = project_service.get_project_by_path(db, group, project)
    level = access_service.require_project(db, principal, p, perm)
    return p, level


@router.get("/{group}/{project}", response_model=ProjectOut)
def get_project(group: str, project: str, principal: CurrentPrincipal, db: DbDep) -> ProjectOut:
    p, level = _load(db, group, project, principal, Perm.PROJECT_READ)
    return project_service.to_out(db, p, principal, level)


@router.patch("/{group}/{project}", response_model=ProjectOut)
def update_project(group: str, project: str, body: ProjectUpdate, request: Request, principal: CurrentPrincipal,
                   db: DbDep) -> ProjectOut:
    p, level = _load(db, group, project, principal, Perm.PROJECT_UPDATE)
    if p.archived:
        raise BadRequest("PROJECT_ARCHIVED", "Project đã archive — bỏ archive trước khi sửa")
    changes = body.model_dump(exclude_unset=True)
    if "default_branch" in changes:
        git_service.validate_ref(changes["default_branch"])
        git_service.set_default_branch(p.repo_path, changes["default_branch"])
    for key, value in changes.items():
        setattr(p, key, value)
    audit_service.record(db, A.UPDATE_PROJECT, principal=principal, resource_type="project", resource_id=p.id,
                         request=request, metadata={k: str(v) for k, v in changes.items()})
    db.commit()
    return project_service.to_out(db, p, principal, level)


@router.post("/{group}/{project}/archive", response_model=ProjectOut)
def archive_project(group: str, project: str, request: Request, principal: CurrentPrincipal, db: DbDep) -> ProjectOut:
    p, level = _load(db, group, project, principal, Perm.PROJECT_ARCHIVE)
    p.archived = True
    audit_service.record(db, A.ARCHIVE_PROJECT, principal=principal, resource_type="project", resource_id=p.id,
                         request=request, metadata={"path": f"{group}/{project}"})
    db.commit()
    return project_service.to_out(db, p, principal, level)


@router.post("/{group}/{project}/unarchive", response_model=ProjectOut)
def unarchive_project(group: str, project: str, request: Request, principal: CurrentPrincipal,
                      db: DbDep) -> ProjectOut:
    p, level = _load(db, group, project, principal, Perm.PROJECT_ARCHIVE)
    p.archived = False
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("PACKAGE_NAME_TAKEN", "Package name đã được project khác dùng") from exc
    audit_service.record(db, A.UNARCHIVE_PROJECT, principal=principal, resource_type="project", resource_id=p.id,
                         request=request)
    db.commit()
    return project_service.to_out(db, p, principal, level)


@router.delete("/{group}/{project}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(group: str, project: str, request: Request, principal: CurrentPrincipal, db: DbDep) -> None:
    """Chỉ System Admin; project phải archive trước và chưa từng publish package (package không được xoá cứng)."""
    p, _ = _load(db, group, project, principal, Perm.PROJECT_DELETE)
    if not p.archived:
        raise BadRequest("PROJECT_NOT_ARCHIVED", "Phải archive project trước khi xoá")
    if db.scalar(select(func.count()).select_from(PackageVersion).where(PackageVersion.project_id == p.id)):
        raise BadRequest("PROJECT_HAS_PACKAGES", "Project đã publish package — chỉ được archive, không được xoá")
    trash = git_service.move_to_trash(p.repo_path)
    audit_service.record(db, A.DELETE_PROJECT, principal=principal, resource_type="project", resource_id=p.id,
                         request=request, metadata={"path": f"{group}/{project}", "repo_moved_to": str(trash)})
    db.delete(p)
    db.commit()


# ───────────── members (group + override theo project) ─────────────

@router.get("/{group}/{project}/members", response_model=list[ProjectMemberOut])
def list_project_members(group: str, project: str, principal: CurrentPrincipal, db: DbDep) -> list[ProjectMemberOut]:
    p, _ = _load(db, group, project, principal, Perm.PROJECT_READ)
    out: dict[uuid.UUID, ProjectMemberOut] = {}
    for m in db.scalars(select(GroupMember).options(joinedload(GroupMember.user))
                        .where(GroupMember.group_id == p.group_id)):
        out[m.user_id] = ProjectMemberOut(user=UserBrief.model_validate(m.user), role=m.role.value, source="group",
                                          created_at=m.created_at)
    for m in db.scalars(select(ProjectMember).options(joinedload(ProjectMember.user))
                        .where(ProjectMember.project_id == p.id)):
        existing = out.get(m.user_id)
        if existing is None or ROLE_LEVEL[m.role.value] > ROLE_LEVEL[existing.role]:
            out[m.user_id] = ProjectMemberOut(user=UserBrief.model_validate(m.user), role=m.role.value,
                                              source="project", created_at=m.created_at)
    return sorted(out.values(), key=lambda x: (-ROLE_LEVEL[x.role], x.user.username))


@router.post("/{group}/{project}/members", response_model=ProjectMemberOut, status_code=status.HTTP_201_CREATED)
def add_project_member(group: str, project: str, body: ProjectMemberIn, request: Request, principal: CurrentPrincipal,
                       db: DbDep) -> ProjectMemberOut:
    p, level = _load(db, group, project, principal, Perm.PROJECT_MEMBERS)
    if ROLE_LEVEL[body.role.value] > max_grantable_project_role(level):
        raise PermissionDenied("Không thể cấp role cao hơn quyền của bạn")
    user = project_service.user_by_id(db, body.user_id)
    member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == p.id, ProjectMember.user_id == user.id))
    if member is None:
        member = ProjectMember(project_id=p.id, user_id=user.id, role=body.role)
        db.add(member)
    else:
        member.role = body.role
    audit_service.record(db, A.ADD_PROJECT_MEMBER, principal=principal, resource_type="project", resource_id=p.id,
                         request=request, metadata={"user_id": str(user.id), "username": user.username,
                                                    "role": body.role.value})
    db.commit()
    return ProjectMemberOut(user=UserBrief.model_validate(user), role=body.role.value, source="project",
                            created_at=member.created_at)


@router.delete("/{group}/{project}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_project_member(group: str, project: str, user_id: uuid.UUID, request: Request,
                          principal: CurrentPrincipal, db: DbDep) -> None:
    p, _ = _load(db, group, project, principal, Perm.PROJECT_MEMBERS)
    member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == p.id, ProjectMember.user_id == user_id))
    if member is None:
        raise NotFound("MEMBER_NOT_FOUND", "User không có quyền riêng trên project (quyền đến từ group)")
    db.delete(member)
    audit_service.record(db, A.REMOVE_PROJECT_MEMBER, principal=principal, resource_type="project", resource_id=p.id,
                         request=request, metadata={"user_id": str(user_id)})
    db.commit()


# ───────────── project service tokens (CI) ─────────────

@router.get("/{group}/{project}/tokens", response_model=list[TokenOut])
def list_project_tokens(group: str, project: str, principal: CurrentPrincipal, db: DbDep) -> list[TokenOut]:
    p, _ = _load(db, group, project, principal, Perm.PROJECT_TOKENS)
    rows = db.scalars(select(AccessToken).where(AccessToken.project_id == p.id,
                                                AccessToken.token_type == TokenType.SERVICE,
                                                AccessToken.revoked_at.is_(None))
                      .order_by(AccessToken.created_at.desc()))
    return [TokenOut.model_validate(t) for t in rows]


@router.post("/{group}/{project}/tokens", response_model=TokenCreated, status_code=status.HTTP_201_CREATED)
def create_project_token(group: str, project: str, body: TokenCreate, request: Request, principal: CurrentPrincipal,
                         db: DbDep) -> TokenCreated:
    p, _ = _load(db, group, project, principal, Perm.PROJECT_TOKENS)
    user = require_user(principal)
    token, raw = token_service.create_service_token(db, p.id, user, body.name.strip(), body.scopes,
                                                    body.expires_in_days)
    audit_service.record(db, A.CREATE_TOKEN, principal=principal, resource_type="access_token", resource_id=token.id,
                         request=request, metadata={"name": token.name, "scopes": token.scopes, "type": "SERVICE",
                                                    "project": f"{group}/{project}"})
    db.commit()
    return TokenCreated(**TokenOut.model_validate(token).model_dump(), token=raw)


@router.delete("/{group}/{project}/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_project_token(group: str, project: str, token_id: uuid.UUID, request: Request, principal: CurrentPrincipal,
                         db: DbDep) -> None:
    p, _ = _load(db, group, project, principal, Perm.PROJECT_TOKENS)
    token = db.scalar(select(AccessToken).where(AccessToken.id == token_id, AccessToken.project_id == p.id,
                                                AccessToken.token_type == TokenType.SERVICE,
                                                AccessToken.revoked_at.is_(None)))
    if token is None:
        raise NotFound("TOKEN_NOT_FOUND", "Không tìm thấy token")
    token.revoked_at = datetime.now(timezone.utc)
    audit_service.record(db, A.REVOKE_TOKEN, principal=principal, resource_type="access_token", resource_id=token.id,
                         request=request, metadata={"name": token.name, "project": f"{group}/{project}"})
    db.commit()


# ───────────── repository browsing ─────────────

@router.get("/{group}/{project}/repository/refs")
def repository_refs(group: str, project: str, principal: CurrentPrincipal, db: DbDep) -> dict:
    p, _ = _load(db, group, project, principal, Perm.REPO_READ)
    refs = git_service.list_refs(p.repo_path)
    return {
        "default_branch": p.default_branch,
        "empty": not refs,
        "branches": [r.__dict__ for r in refs if r.kind == "branch"],
        "tags": [r.__dict__ for r in refs if r.kind == "tag"],
    }


@router.get("/{group}/{project}/repository/commits")
def repository_commits(group: str, project: str, principal: CurrentPrincipal, db: DbDep, ref: str | None = None,
                       path: str | None = None, limit: int = Query(30, ge=1, le=200)) -> list[dict]:
    p, _ = _load(db, group, project, principal, Perm.REPO_READ)
    return [c.__dict__ for c in git_service.log(p.repo_path, ref or p.default_branch, limit, path)]


@router.get("/{group}/{project}/repository/tree")
def repository_tree(group: str, project: str, principal: CurrentPrincipal, db: DbDep, ref: str | None = None,
                    path: str = "") -> dict:
    p, _ = _load(db, group, project, principal, Perm.REPO_READ)
    ref = ref or p.default_branch
    if git_service.resolve_commit(p.repo_path, ref) is None:
        return {"ref": ref, "path": path, "entries": [], "empty": True}
    return {"ref": ref, "path": path, "empty": False,
            "entries": [e.__dict__ for e in git_service.ls_tree(p.repo_path, ref, path)]}


@router.get("/{group}/{project}/repository/blob")
def repository_blob(group: str, project: str, principal: CurrentPrincipal, db: DbDep, path: str,
                    ref: str | None = None) -> dict:
    p, _ = _load(db, group, project, principal, Perm.REPO_READ)
    data, truncated = git_service.read_blob(p.repo_path, ref or p.default_branch, path)
    binary = b"\x00" in data[:8000]
    return {"path": path, "ref": ref or p.default_branch, "truncated": truncated, "binary": binary,
            "size": len(data), "content": None if binary else data.decode("utf-8", "replace")}


@router.get("/{group}/{project}/readme")
def project_readme(group: str, project: str, principal: CurrentPrincipal, db: DbDep, ref: str | None = None) -> dict:
    p, _ = _load(db, group, project, principal, Perm.PROJECT_READ)
    ref = ref or p.default_branch
    if git_service.resolve_commit(p.repo_path, ref) is None:
        return {"filename": None, "content": None}
    found = git_service.read_readme(p.repo_path, ref)
    if found is None:
        return {"filename": None, "content": None}
    return {"filename": found[0], "content": found[1]}
