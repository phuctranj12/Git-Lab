"""Dữ liệu demo cho staging (idempotent): python -m app.seed  (make seed)

Tạo group `ai-tools` + 4 tool mẫu (repo rỗng) + 2 user demo. KHÔNG chạy ở production.
"""
from __future__ import annotations

import secrets
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import Group, GroupMember, Project, User
from app.db.models.enums import GroupRole, ProjectLanguage, Visibility
from app.db.session import session_factory
from app.services import git_service

TOOLS = [
    ("Bóc tách bản vẽ", "boc-tach-ban-ve", "Bóc tách khối lượng cáp/ống từ bản vẽ MEP PDF."),
    ("PDF Parser", "pdf-parser", "Trích xuất text/bảng từ PDF kỹ thuật."),
    ("Contract Parser", "contract-parser", "Phân tích điều khoản hợp đồng xây dựng."),
    ("DMS Client", "dms-client", "Client gọi hệ thống quản lý tài liệu nội bộ."),
]


def main() -> int:
    s = get_settings()
    if s.app_env.lower() == "production":
        print("Không seed dữ liệu demo ở production.", file=sys.stderr)
        return 1
    prefix = s.internal_package_prefix
    with session_factory()() as db:
        group = db.scalar(select(Group).where(Group.slug == "ai-tools"))
        if group is None:
            group = Group(name="AI Tools", slug="ai-tools", description="Nhóm AI — tool nội bộ",
                          visibility=Visibility.INTERNAL)
            db.add(group)
            db.flush()
        created_users = []
        for username, role in (("demo-dev", GroupRole.MAINTAINER), ("demo-viewer", GroupRole.VIEWER)):
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                password = secrets.token_urlsafe(10)
                user = User(username=username, email=f"{username}@hawee.local", full_name=username.replace("-", " ").title(),
                            password_hash=hash_password(password))
                db.add(user)
                db.flush()
                created_users.append((username, password))
            if db.scalar(select(GroupMember).where(GroupMember.group_id == group.id,
                                                   GroupMember.user_id == user.id)) is None:
                db.add(GroupMember(group_id=group.id, user_id=user.id, role=role))
        for name, slug, desc in TOOLS:
            if db.scalar(select(Project).where(Project.group_id == group.id, Project.slug == slug)) is not None:
                continue
            rel = git_service.relative_repo_path(group.slug, slug)
            project = Project(group_id=group.id, name=name, slug=slug, description=desc, language=ProjectLanguage.PYTHON,
                              visibility=Visibility.INTERNAL, package_name=f"{prefix}{slug}", package_prefix_valid=True,
                              repo_path=rel)
            db.add(project)
            db.flush()
            git_service.create_bare_repository(rel, project.id, project.default_branch)
        db.commit()
    print("Seed xong: group ai-tools + tool mẫu.")
    for username, password in created_users:
        print(f"  user {username} / {password}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
