"""Xác thực request: cookie phiên (UI), Bearer (JWT hoặc token), Basic (pip)."""
from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, PermissionDenied, Unauthorized
from app.core.logging import user_id_var
from app.core.permissions import ALL_SCOPES, Scope, expand_scopes
from app.core.security import constant_time_equals, decode_access_token, hash_token
from app.db.models import AccessToken, User
from app.db.models.enums import ActorType, TokenType
from app.db.session import get_db

ACCESS_COOKIE = "toolhub_access"
REFRESH_COOKIE = "toolhub_refresh"
CSRF_COOKIE = "toolhub_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DbDep = Annotated[Session, Depends(get_db)]


@dataclass
class Principal:
    user: User | None
    token: AccessToken | None
    scopes: frozenset[str]
    via: str  # session | bearer_jwt | token | internal

    @property
    def user_id(self) -> uuid.UUID | None:
        return self.user.id if self.user else None

    @property
    def is_admin(self) -> bool:
        if self.user is None or not self.user.is_system_admin:
            return False
        # Quyền admin qua token chỉ khi token có scope admin.
        return self.via in {"session", "bearer_jwt"} or Scope.ADMIN.value in self.scopes

    @property
    def actor_type(self) -> ActorType:
        if self.token is not None and self.token.token_type != TokenType.PERSONAL:
            return ActorType.SERVICE
        return ActorType.USER if self.user else ActorType.SYSTEM

    @property
    def display_name(self) -> str:
        if self.token is not None and self.token.token_type != TokenType.PERSONAL:
            return f"token:{self.token.name}"
        return self.user.username if self.user else "system"

    @property
    def bound_project_id(self) -> uuid.UUID | None:
        if self.token is not None and self.token.token_type != TokenType.PERSONAL:
            return self.token.project_id
        return None

    def has_scope(self, scope: Scope | str) -> bool:
        return (scope.value if isinstance(scope, Scope) else scope) in self.scopes

    def require_scope(self, scope: Scope) -> None:
        if not self.has_scope(scope):
            raise AppError("INSUFFICIENT_SCOPE", f"Token thiếu scope '{scope.value}'", 403)


def _touch_token(db: Session, token: AccessToken) -> None:
    now = datetime.now(timezone.utc)
    if token.last_used_at is None or now - token.last_used_at > timedelta(minutes=5):
        token.last_used_at = now
        db.commit()


def authenticate_token(db: Session, raw: str, *, allowed_types: set[TokenType] | None = None) -> Principal:
    token = db.scalar(select(AccessToken).where(AccessToken.token_hash == hash_token(raw)))
    if token is None:
        raise Unauthorized("INVALID_CREDENTIALS", "Token không hợp lệ")
    now = datetime.now(timezone.utc)
    if token.revoked_at is not None:
        raise Unauthorized("TOKEN_REVOKED", "Token đã bị thu hồi")
    if token.expires_at is not None and token.expires_at <= now:
        raise Unauthorized("TOKEN_EXPIRED", "Token đã hết hạn")
    if allowed_types is not None and token.token_type not in allowed_types:
        raise Unauthorized("INVALID_CREDENTIALS", "Loại token không được phép ở đây")
    user = None
    if token.user_id is not None:
        user = db.get(User, token.user_id)
        if user is None or not user.is_active:
            raise Unauthorized("INVALID_CREDENTIALS", "Tài khoản đã bị vô hiệu hoá")
    _touch_token(db, token)
    return Principal(user=user, token=token, scopes=expand_scopes(token.scopes or []), via="token")


def _authenticate_jwt(db: Session, raw: str, via: str) -> Principal:
    try:
        payload = decode_access_token(raw)
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("TOKEN_EXPIRED", "Phiên đăng nhập đã hết hạn") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("INVALID_CREDENTIALS", "Token không hợp lệ") from exc
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise Unauthorized("INVALID_CREDENTIALS", "Token không hợp lệ") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("INVALID_CREDENTIALS", "Tài khoản không tồn tại hoặc đã bị vô hiệu hoá")
    return Principal(user=user, token=None, scopes=ALL_SCOPES, via=via)


def _looks_like_jwt(raw: str) -> bool:
    return raw.count(".") == 2 and raw.startswith("eyJ")


def _check_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not constant_time_equals(cookie, header):
        raise AppError("CSRF_FAILED", "Thiếu hoặc sai CSRF token", 403)


def parse_basic_auth(header: str) -> tuple[str, str] | None:
    try:
        decoded = base64.b64decode(header, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if ":" not in decoded:
        return None
    user, _, password = decoded.partition(":")
    return user, password


def resolve_principal(request: Request, db: Session, *, allow_basic: bool = False) -> Principal | None:
    """Trả Principal hoặc None nếu request không mang credential. Credential sai → 401."""
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    scheme = scheme.lower()
    value = value.strip()
    principal: Principal | None = None
    if scheme == "bearer" and value:
        principal = _authenticate_jwt(db, value, "bearer_jwt") if _looks_like_jwt(value) else authenticate_token(db, value)
    elif scheme == "basic" and value and allow_basic:
        creds = parse_basic_auth(value)
        if creds is None:
            raise Unauthorized("INVALID_CREDENTIALS", "Basic auth không hợp lệ")
        # pip: username tuỳ ý (vd __token__), password = access token. Không nhận mật khẩu đăng nhập.
        principal = authenticate_token(db, creds[1])
    else:
        cookie = request.cookies.get(ACCESS_COOKIE)
        if cookie:
            principal = _authenticate_jwt(db, cookie, "session")
            _check_csrf(request)
    if principal is not None and principal.user is not None:
        user_id_var.set(str(principal.user.id))
    if principal is not None:
        request.state.principal = principal
    return principal


def _api_principal(request: Request, db: DbDep) -> Principal:
    principal = resolve_principal(request, db)
    if principal is None:
        raise Unauthorized("INVALID_CREDENTIALS", "Chưa đăng nhập")
    if principal.via == "token":
        needed = Scope.READ_API if request.method in SAFE_METHODS else Scope.WRITE_API
        principal.require_scope(needed)
    return principal


def _admin_principal(principal: Annotated[Principal, Depends(_api_principal)]) -> Principal:
    if not principal.is_admin:
        raise PermissionDenied("Chỉ System Admin được thực hiện thao tác này")
    return principal


def _optional_principal(request: Request, db: DbDep) -> Principal | None:
    return resolve_principal(request, db)


CurrentPrincipal = Annotated[Principal, Depends(_api_principal)]
AdminPrincipal = Annotated[Principal, Depends(_admin_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(_optional_principal)]


def require_user(principal: Principal) -> User:
    if principal.user is None:
        raise PermissionDenied("Thao tác này cần tài khoản người dùng (không dùng service token)")
    return principal.user


# ── Internal service (git-service hook / auth wrapper) ──

def _internal_service(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    secret = get_settings().internal_service_secret
    if scheme.lower() != "bearer" or not value or not constant_time_equals(value.strip(), secret):
        raise Unauthorized("INVALID_CREDENTIALS", "Internal service token không hợp lệ")


def _runner_principal(request: Request, db: DbDep) -> Principal:
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise Unauthorized("INVALID_CREDENTIALS", "Thiếu runner token")
    principal = authenticate_token(db, value.strip(), allowed_types={TokenType.RUNNER})
    if principal.token is None or principal.token.project_id is not None:
        # Token RUNNER gắn project là token tạm cho container build — không được gọi API runner.
        raise PermissionDenied("Token này không phải runner service token")
    principal.require_scope(Scope.WRITE_PACKAGE)
    return principal


InternalService = Depends(_internal_service)
RunnerPrincipal = Annotated[Principal, Depends(_runner_principal)]
