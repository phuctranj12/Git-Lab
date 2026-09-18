from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from app.core.deps import CurrentPrincipal, DbDep
from app.db.models import Build, Group, GroupMember, PackageVersion, Project, ProjectMember
from app.db.models.enums import BuildStatus
from app.services import access_service, build_service, project_service

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard(principal: CurrentPrincipal, db: DbDep) -> dict:
    """Spec 26.2: My Projects, My Groups, Recent Builds, Recent Releases, Failed Builds, Popular Packages."""
    uid = principal.user_id
    my_projects: list = []
    my_groups: list = []
    if uid is not None:
        stmt = (select(Project).join(Group).options(joinedload(Project.group))
                .where(Project.archived.is_(False),
                       or_(Project.group_id.in_(select(GroupMember.group_id).where(GroupMember.user_id == uid)),
                           Project.id.in_(select(ProjectMember.project_id).where(ProjectMember.user_id == uid))))
                .order_by(Project.updated_at.desc()).limit(8))
        my_projects = [project_service.to_out(db, p, principal) for p in db.scalars(stmt)]
        groups = db.execute(select(Group, GroupMember.role).join(GroupMember, GroupMember.group_id == Group.id)
                            .where(GroupMember.user_id == uid).order_by(Group.name)).all()
        my_groups = [{"slug": g.slug, "name": g.name, "role": role.value,
                      "project_count": project_service.group_counts(db, g.id)[0]} for g, role in groups]

    visible_builds = access_service.visible_projects_filter(
        select(Build).join(Project).join(Group).options(joinedload(Build.project).joinedload(Project.group)),
        principal)
    recent = db.scalars(visible_builds.order_by(Build.created_at.desc()).limit(10)).all()
    failed = db.scalars(visible_builds.where(Build.status == BuildStatus.FAILED)
                        .order_by(Build.created_at.desc()).limit(5)).all()

    visible_versions = access_service.visible_projects_filter(
        select(PackageVersion, Project).join(Project, Project.id == PackageVersion.project_id).join(Group), principal)
    releases = db.execute(visible_versions.order_by(PackageVersion.created_at.desc()).limit(8)).all()

    popular_stmt = access_service.visible_projects_filter(
        select(PackageVersion.normalized_name, func.sum(PackageVersion.download_count).label("downloads"),
               func.max(Project.latest_release_version), func.max(Project.name))
        .join(Project, Project.id == PackageVersion.project_id).join(Group), principal)
    popular = db.execute(popular_stmt.group_by(PackageVersion.normalized_name)
                         .order_by(func.sum(PackageVersion.download_count).desc()).limit(8)).all()

    return {
        "my_projects": my_projects,
        "my_groups": my_groups,
        "recent_builds": [build_service.to_out(db, b) for b in recent],
        "failed_builds": [build_service.to_out(db, b) for b in failed],
        "recent_releases": [{"package_name": pv.normalized_name, "version": pv.version, "is_yanked": pv.is_yanked,
                             "project_path": f"{p.group.slug}/{p.slug}" if p.group else None,
                             "project_name": p.name, "created_at": pv.created_at} for pv, p in releases],
        "popular_packages": [{"package_name": n, "downloads": int(d or 0), "latest_version": v, "project_name": pn}
                             for n, d, v, pn in popular],
    }
