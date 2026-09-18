from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.deps import Principal
from app.core.errors import BadRequest, Conflict, NotFound
from app.core.permissions import PROJECT_PERM_LEVEL, Level
from app.db.models import Group, PackageFile, PackageVersion, Project, User
from app.db.models.enums import ProjectLanguage, Visibility
from app.schemas import ProjectCreate, ProjectOut
from app.services import access_service, git_service
from app.utils.naming import is_internal_package, is_valid_package_name, is_valid_slug, normalize_package_name

log = logging.getLogger(__name__)


def clone_url(project: Project) -> str:
    s = get_settings()
    path = f"{project.group.slug}/{project.slug}.git"
    if s.git_ssh_port == 22:
        return f"{s.git_ssh_user}@{s.git_ssh_host}:{path}"
    return f"ssh://{s.git_ssh_user}@{s.git_ssh_host}:{s.git_ssh_port}/{path}"


def validate_package_name(name: str) -> str:
    """Trả tên chuẩn hoá. Package nội bộ BẮT BUỘC có prefix hawee- (spec 2.3, 21)."""
    name = name.strip()
    if not is_valid_package_name(name):
        raise BadRequest("INVALID_PACKAGE_NAME", "Tên package không hợp lệ theo PEP 508")
    prefix = get_settings().internal_package_prefix
    if not is_internal_package(name, prefix):
        raise BadRequest("INTERNAL_PREFIX_REQUIRED", f"Tên package nội bộ phải bắt đầu bằng '{prefix}'")
    return normalize_package_name(name)


def get_group_by_slug(db: Session, slug: str) -> Group:
    group = db.scalar(select(Group).where(Group.slug == slug.lower()))
    if group is None:
        raise NotFound("GROUP_NOT_FOUND", "Không tìm thấy group")
    return group


def get_project_by_path(db: Session, group_slug: str, project_slug: str) -> Project:
    project = db.scalar(select(Project).join(Group).options(joinedload(Project.group))
                        .where(Group.slug == group_slug.lower(), Project.slug == project_slug.lower()))
    if project is None:
        raise NotFound("PROJECT_NOT_FOUND", "Không tìm thấy project")
    return project


def create_project(db: Session, principal: Principal, group: Group, body: ProjectCreate) -> Project:
    slug = body.slug.strip().lower()
    if not is_valid_slug(slug):
        raise BadRequest("INVALID_SLUG", "Slug chỉ gồm a-z, 0-9, '-' (không bắt đầu/kết thúc bằng '-')")
    package_name: str | None = None
    if body.language == ProjectLanguage.PYTHON:
        if not body.package_name:
            raise BadRequest("INVALID_PACKAGE_NAME", "Project Python phải khai báo package_name (vd hawee-" + slug + ")")
        package_name = validate_package_name(body.package_name)
        clash = db.scalar(select(Project.id).where(Project.package_name == package_name, Project.archived.is_(False)))
        if clash is not None:
            raise Conflict("PACKAGE_NAME_TAKEN", f"Package '{package_name}' đã thuộc project khác")
        published_elsewhere = db.scalar(select(PackageVersion.project_id)
                                        .where(PackageVersion.normalized_name == package_name).limit(1))
        if published_elsewhere is not None:
            raise Conflict("PACKAGE_NAME_TAKEN", f"Package '{package_name}' đã từng được publish bởi project khác")
    elif body.package_name:
        raise BadRequest("VALIDATION_ERROR", "Chỉ project PYTHON mới có package_name ở V1")
    relative = git_service.relative_repo_path(group.slug, slug)
    project = Project(
        group_id=group.id, group=group, name=body.name.strip(), slug=slug, description=body.description,
        language=body.language, visibility=body.visibility, default_branch=git_service.validate_ref(body.default_branch),
        package_name=package_name, package_prefix_valid=package_name is not None, repo_path=relative,
        created_by=principal.user_id,
    )
    db.add(project)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("PROJECT_EXISTS", "Slug project đã tồn tại trong group") from exc
    git_service.create_bare_repository(relative, project.id, project.default_branch)
    return project


def latest_python_requires(db: Session, project: Project) -> str | None:
    if not project.latest_release_version or not project.package_name:
        return None
    return db.scalar(select(PackageFile.python_requires).join(PackageVersion)
                     .where(PackageVersion.normalized_name == project.package_name,
                            PackageVersion.version == project.latest_release_version,
                            PackageFile.python_requires.is_not(None)).limit(1))


def to_out(db: Session, project: Project, principal: Principal | None = None, level: Level | None = None) -> ProjectOut:
    if level is None:
        level = access_service.principal_project_level(db, principal, project) if principal else Level.NONE
    perms = [p.value for p, need in PROJECT_PERM_LEVEL.items() if level >= need]
    return ProjectOut(
        id=project.id, group_slug=project.group.slug, group_name=project.group.name, name=project.name,
        slug=project.slug, full_path=f"{project.group.slug}/{project.slug}", description=project.description,
        language=project.language, visibility=project.visibility, default_branch=project.default_branch,
        protect_default_branch=project.protect_default_branch, package_name=project.package_name,
        package_prefix_valid=project.package_prefix_valid, repository_size_bytes=project.repository_size_bytes,
        latest_commit_sha=project.latest_commit_sha, latest_release_version=project.latest_release_version,
        last_build_status=project.last_build_status, archived=project.archived, created_at=project.created_at,
        updated_at=project.updated_at, clone_url=clone_url(project),
        install_command=(f"pip install {project.package_name}" if project.package_name else None),
        python_requires=latest_python_requires(db, project),
        my_level=level.name, permissions=perms,
    )


def group_counts(db: Session, group_id) -> tuple[int, int]:
    from app.db.models import GroupMember

    projects = db.scalar(select(func.count()).select_from(Project).where(Project.group_id == group_id,
                                                                         Project.archived.is_(False))) or 0
    members = db.scalar(select(func.count()).select_from(GroupMember).where(GroupMember.group_id == group_id)) or 0
    return projects, members


def user_by_id(db: Session, user_id) -> User:
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise NotFound("USER_NOT_FOUND", "Không tìm thấy user đang hoạt động")
    return user


def effective_visibility(project: Project) -> Visibility:
    if project.visibility == Visibility.INTERNAL and project.group.visibility == Visibility.INTERNAL:
        return Visibility.INTERNAL
    return Visibility.PRIVATE
