from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.core.config import get_settings

router = APIRouter(tags=["meta"])


@router.get("/meta")
def meta() -> dict:
    """Thông tin công khai cho UI (không chứa secret)."""
    s = get_settings()
    return {
        "app_name": s.app_name,
        "version": __version__,
        "web_base_url": s.web_base_url,
        "package_index_url": s.package_base_url.rstrip("/") + "/simple",
        "internal_package_prefix": s.internal_package_prefix,
        "git_ssh_host": s.git_ssh_host,
        "git_ssh_port": s.git_ssh_port,
        "git_ssh_user": s.git_ssh_user,
        "registry_allow_anonymous": s.registry_allow_anonymous,
    }
