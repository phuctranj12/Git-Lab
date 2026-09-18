from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import AdminPrincipal, CurrentPrincipal, DbDep
from app.core.errors import BadRequest, Conflict, NotFound
from app.core.security import hash_password
from app.db.models import User
from app.schemas import Page, UserBrief, UserCreate, UserOut, UserUpdate
from app.services import audit_service
from app.services.audit_service import A
from app.services.auth_service import revoke_all_refresh

router = APIRouter(prefix="/users", tags=["users"])


def _get_user(db, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("USER_NOT_FOUND", "Không tìm thấy user")
    return user


@router.get("", response_model=Page[UserOut])
def list_users(principal: AdminPrincipal, db: DbDep, q: str | None = None, include_inactive: bool = True,
               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)) -> Page[UserOut]:
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.username).like(like), func.lower(User.email).like(like),
                              func.lower(User.full_name).like(like)))
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(User.username).offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[UserOut.model_validate(u) for u in rows], total=total, page=page, page_size=page_size)


@router.get("/search", response_model=list[UserBrief])
def search_users(principal: CurrentPrincipal, db: DbDep, q: str = Query(min_length=1, max_length=100),
                 limit: int = Query(10, ge=1, le=50)) -> list[UserBrief]:
    """Tra cứu nhanh để thêm thành viên — chỉ trả thông tin cơ bản của user đang hoạt động."""
    like = f"%{q.lower()}%"
    rows = db.scalars(select(User).where(User.is_active.is_(True),
                                         or_(func.lower(User.username).like(like), func.lower(User.email).like(like),
                                             func.lower(User.full_name).like(like)))
                      .order_by(User.username).limit(limit)).all()
    return [UserBrief.model_validate(u) for u in rows]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, request: Request, principal: AdminPrincipal, db: DbDep) -> UserOut:
    user = User(username=body.username.lower(), email=str(body.email).lower(), full_name=body.full_name,
                password_hash=hash_password(body.password), is_system_admin=body.is_system_admin)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("USER_EXISTS", "Username hoặc email đã tồn tại") from exc
    audit_service.record(db, A.CREATE_USER, principal=principal, resource_type="user", resource_id=user.id,
                         request=request, metadata={"username": user.username, "is_system_admin": user.is_system_admin})
    db.commit()
    return UserOut.model_validate(user)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, principal: AdminPrincipal, db: DbDep) -> UserOut:
    return UserOut.model_validate(_get_user(db, user_id))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, request: Request, principal: AdminPrincipal,
                db: DbDep) -> UserOut:
    user = _get_user(db, user_id)
    changes: dict[str, object] = {}
    if body.email is not None:
        user.email = str(body.email).lower()
        changes["email"] = user.email
    if body.full_name is not None:
        user.full_name = body.full_name
        changes["full_name"] = body.full_name
    if body.password is not None:
        user.password_hash = hash_password(body.password)
        revoke_all_refresh(db, user.id)
        changes["password"] = "reset"
    if body.is_system_admin is not None and body.is_system_admin != user.is_system_admin:
        if user.id == principal.user_id and not body.is_system_admin:
            raise BadRequest("VALIDATION_ERROR", "Không thể tự bỏ quyền admin của chính mình")
        user.is_system_admin = body.is_system_admin
        changes["is_system_admin"] = body.is_system_admin
    action = A.UPDATE_USER
    if body.is_active is not None and body.is_active != user.is_active:
        _set_active(db, user, body.is_active, principal)
        action = A.ENABLE_USER if body.is_active else A.DISABLE_USER
        changes["is_active"] = body.is_active
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("USER_EXISTS", "Email đã được dùng") from exc
    audit_service.record(db, action, principal=principal, resource_type="user", resource_id=user.id,
                         request=request, metadata=changes)
    db.commit()
    return UserOut.model_validate(user)


def _set_active(db, user: User, active: bool, principal) -> None:
    if not active and user.id == principal.user_id:
        raise BadRequest("VALIDATION_ERROR", "Không thể tự vô hiệu hoá chính mình")
    user.is_active = active
    if not active:
        revoke_all_refresh(db, user.id)


@router.post("/{user_id}/disable", response_model=UserOut)
def disable_user(user_id: uuid.UUID, request: Request, principal: AdminPrincipal, db: DbDep) -> UserOut:
    user = _get_user(db, user_id)
    _set_active(db, user, False, principal)
    audit_service.record(db, A.DISABLE_USER, principal=principal, resource_type="user", resource_id=user.id,
                         request=request, metadata={"username": user.username})
    db.commit()
    return UserOut.model_validate(user)


@router.post("/{user_id}/enable", response_model=UserOut)
def enable_user(user_id: uuid.UUID, request: Request, principal: AdminPrincipal, db: DbDep) -> UserOut:
    user = _get_user(db, user_id)
    _set_active(db, user, True, principal)
    audit_service.record(db, A.ENABLE_USER, principal=principal, resource_type="user", resource_id=user.id,
                         request=request, metadata={"username": user.username})
    db.commit()
    return UserOut.model_validate(user)
