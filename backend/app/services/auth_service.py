"""Xác thực local (username/email + mật khẩu). Thiết kế provider-based để thêm LDAP/OIDC ở V2."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from fastapi import Request, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from app.core.errors import AppError, Unauthorized
from app.core.security import (
    burn_password_check,
    create_access_token,
    generate_csrf_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_password,
)
from app.db.models import RefreshToken, User
from app.services import audit_service
from app.services.audit_service import A

REFRESH_COOKIE_PATH = "/api/v1/auth"


class AuthProvider(Protocol):
    name: str

    def authenticate(self, db: Session, login: str, password: str) -> User | None: ...


class LocalAuthProvider:
    name = "local"

    def authenticate(self, db: Session, login: str, password: str) -> User | None:
        ident = login.strip().lower()
        user = db.scalar(select(User).where(or_(func.lower(User.username) == ident, func.lower(User.email) == ident)))
        if user is None or user.auth_provider != self.name:
            burn_password_check(password)
            return None
        if not verify_password(password, user.password_hash):
            return None
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        return user


PROVIDERS: list[AuthProvider] = [LocalAuthProvider()]


@dataclass
class IssuedSession:
    access_token: str
    refresh_token: str
    csrf_token: str


def issue_session(db: Session, user: User, request: Request) -> IssuedSession:
    s = get_settings()
    raw_refresh = generate_opaque_token()
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw_refresh),
        expires_at=datetime.now(timezone.utc) + timedelta(days=s.refresh_token_expire_days),
        ip_address=audit_service.client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:500],
    ))
    return IssuedSession(
        access_token=create_access_token(user.id, is_admin=user.is_system_admin),
        refresh_token=raw_refresh,
        csrf_token=generate_csrf_token(),
    )


def set_session_cookies(response: Response, issued: IssuedSession) -> None:
    s = get_settings()
    response.set_cookie(ACCESS_COOKIE, issued.access_token, max_age=s.access_token_expire_minutes * 60,
                        httponly=True, secure=s.cookie_secure, samesite="strict", path="/")
    response.set_cookie(REFRESH_COOKIE, issued.refresh_token, max_age=s.refresh_token_expire_days * 86400,
                        httponly=True, secure=s.cookie_secure, samesite="strict", path=REFRESH_COOKIE_PATH)
    # CSRF double-submit: JS đọc được cookie này và gửi lại qua header X-CSRF-Token.
    response.set_cookie(CSRF_COOKIE, issued.csrf_token, max_age=s.refresh_token_expire_days * 86400,
                        httponly=False, secure=s.cookie_secure, samesite="strict", path="/")


def clear_session_cookies(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=s.cookie_secure, httponly=True, samesite="strict")
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=s.cookie_secure, httponly=True,
                           samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=s.cookie_secure, samesite="strict")


def login(db: Session, request: Request, login_name: str, password: str) -> tuple[User, IssuedSession]:
    user: User | None = None
    for provider in PROVIDERS:
        user = provider.authenticate(db, login_name, password)
        if user is not None:
            break
    if user is None or not user.is_active:
        audit_service.record(db, A.LOGIN_FAILED, user_id=user.id if user else None, request=request,
                             actor_name=login_name[:100],
                             metadata={"reason": "disabled" if user else "invalid_credentials"})
        db.commit()
        raise Unauthorized("INVALID_CREDENTIALS", "Tên đăng nhập hoặc mật khẩu không đúng")
    user.last_login_at = datetime.now(timezone.utc)
    issued = issue_session(db, user, request)
    audit_service.record(db, A.LOGIN, user_id=user.id, actor_name=user.username, request=request,
                         resource_type="user", resource_id=user.id)
    db.commit()
    return user, issued


def revoke_all_refresh(db: Session, user_id) -> None:
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
               .values(revoked_at=datetime.now(timezone.utc)))


def refresh(db: Session, request: Request, raw_refresh: str | None) -> tuple[User, IssuedSession]:
    if not raw_refresh:
        raise Unauthorized("INVALID_CREDENTIALS", "Thiếu refresh token")
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_refresh))
                      .with_for_update())
    if token is None:
        raise Unauthorized("INVALID_CREDENTIALS", "Refresh token không hợp lệ")
    now = datetime.now(timezone.utc)
    if token.revoked_at is not None:
        # Refresh đã dùng rồi bị dùng lại → nghi bị đánh cắp: thu hồi toàn bộ phiên của user.
        revoke_all_refresh(db, token.user_id)
        db.commit()
        raise Unauthorized("TOKEN_REVOKED", "Phiên đăng nhập đã bị thu hồi, vui lòng đăng nhập lại")
    if token.expires_at <= now:
        raise Unauthorized("TOKEN_EXPIRED", "Phiên đăng nhập đã hết hạn")
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("INVALID_CREDENTIALS", "Tài khoản đã bị vô hiệu hoá")
    token.revoked_at = now
    issued = issue_session(db, user, request)
    db.commit()
    return user, issued


def logout(db: Session, raw_refresh: str | None) -> None:
    if not raw_refresh:
        return
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_refresh)))
    if token is not None and token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
        db.commit()


def change_password(db: Session, user: User, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        raise AppError("INVALID_CREDENTIALS", "Mật khẩu hiện tại không đúng", 400)
    if current == new:
        raise AppError("VALIDATION_ERROR", "Mật khẩu mới phải khác mật khẩu cũ", 400)
    user.password_hash = hash_password(new)
    revoke_all_refresh(db, user.id)
