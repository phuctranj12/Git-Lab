from __future__ import annotations

import ipaddress
import logging
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.core.logging import request_id_var
from app.db.models import AuditLog
from app.db.models.enums import ActorType

if TYPE_CHECKING:
    from fastapi import Request

    from app.core.deps import Principal

log = logging.getLogger("audit")


class A:
    """Danh sách action audit bắt buộc (spec 7.13) + bổ sung."""

    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    CREATE_USER = "CREATE_USER"
    UPDATE_USER = "UPDATE_USER"
    DISABLE_USER = "DISABLE_USER"
    ENABLE_USER = "ENABLE_USER"
    CHANGE_PASSWORD = "CHANGE_PASSWORD"
    CREATE_GROUP = "CREATE_GROUP"
    UPDATE_GROUP = "UPDATE_GROUP"
    ADD_GROUP_MEMBER = "ADD_GROUP_MEMBER"
    UPDATE_GROUP_MEMBER = "UPDATE_GROUP_MEMBER"
    REMOVE_GROUP_MEMBER = "REMOVE_GROUP_MEMBER"
    CREATE_PROJECT = "CREATE_PROJECT"
    UPDATE_PROJECT = "UPDATE_PROJECT"
    DELETE_PROJECT = "DELETE_PROJECT"
    ARCHIVE_PROJECT = "ARCHIVE_PROJECT"
    UNARCHIVE_PROJECT = "UNARCHIVE_PROJECT"
    ADD_PROJECT_MEMBER = "ADD_PROJECT_MEMBER"
    REMOVE_PROJECT_MEMBER = "REMOVE_PROJECT_MEMBER"
    ADD_SSH_KEY = "ADD_SSH_KEY"
    REMOVE_SSH_KEY = "REMOVE_SSH_KEY"
    CREATE_TOKEN = "CREATE_TOKEN"
    REVOKE_TOKEN = "REVOKE_TOKEN"
    GIT_PUSH = "GIT_PUSH"
    GIT_PUSH_DENIED = "GIT_PUSH_DENIED"
    BUILD_CREATED = "BUILD_CREATED"
    BUILD_STARTED = "BUILD_STARTED"
    BUILD_FAILED = "BUILD_FAILED"
    BUILD_SUCCESS = "BUILD_SUCCESS"
    BUILD_CANCELLED = "BUILD_CANCELLED"
    PACKAGE_PUBLISHED = "PACKAGE_PUBLISHED"
    PACKAGE_YANKED = "PACKAGE_YANKED"
    PACKAGE_UNYANKED = "PACKAGE_UNYANKED"
    PACKAGE_DOWNLOADED = "PACKAGE_DOWNLOADED"
    UPSTREAM_PACKAGE_CACHED = "UPSTREAM_PACKAGE_CACHED"
    AUDIT_EXPORTED = "AUDIT_EXPORTED"


def client_ip(request: Request | None) -> str | None:
    """IP hợp lệ của client (None nếu không phải IP — vd TestClient/unix socket)."""
    if request is None or request.client is None:
        return None
    host = request.client.host
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host


def record(
    db: Session,
    action: str,
    *,
    principal: Principal | None = None,
    actor_type: ActorType | None = None,
    actor_name: str | None = None,
    user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: Any = None,
    request: Request | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Thêm bản ghi audit vào session hiện tại — commit cùng transaction nghiệp vụ."""
    if principal is not None:
        user_id = user_id or principal.user_id
        actor_type = actor_type or principal.actor_type
        actor_name = actor_name or principal.display_name
    entry = AuditLog(
        user_id=user_id,
        actor_type=actor_type or ActorType.SYSTEM,
        actor_name=actor_name,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent", "")[:500] if request is not None else None),
        request_id=request_id_var.get(),
        metadata_json=metadata or {},
    )
    db.add(entry)
    log.info(action, extra={"action": action, "extra_data": {"resource_type": resource_type,
                                                             "resource_id": entry.resource_id}})
    return entry
