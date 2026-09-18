from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BadRequest, PermissionDenied
from app.core.permissions import ALL_SCOPES, Scope
from app.core.security import generate_opaque_token, hash_token, token_display_prefix
from app.db.models import AccessToken, User
from app.db.models.enums import TokenType

# Service token của project chỉ được đọc (CI của project khác kéo package). Publish chỉ qua pipeline build.
SERVICE_TOKEN_SCOPES = frozenset({Scope.READ_PACKAGE.value, Scope.READ_API.value, Scope.READ_REPOSITORY.value})
MAX_PERSONAL_TOKENS = 50


def _validate_scopes(scopes: list[str], allowed: frozenset[str]) -> list[str]:
    cleaned = sorted(set(scopes))
    unknown = [s for s in cleaned if s not in ALL_SCOPES]
    if unknown:
        raise BadRequest("INVALID_SCOPE", f"Scope không hợp lệ: {', '.join(unknown)}")
    forbidden = [s for s in cleaned if s not in allowed]
    if forbidden:
        raise PermissionDenied(f"Không được cấp scope: {', '.join(forbidden)}", code="INSUFFICIENT_SCOPE")
    return cleaned


def _expiry(days: int | None) -> datetime | None:
    return datetime.now(timezone.utc) + timedelta(days=days) if days else None


def _new_token(**kwargs) -> tuple[AccessToken, str]:
    raw = generate_opaque_token()
    token = AccessToken(token_hash=hash_token(raw), token_prefix=token_display_prefix(raw), **kwargs)
    return token, raw


def create_personal_token(db: Session, user: User, name: str, scopes: list[str], expires_in_days: int | None
                          ) -> tuple[AccessToken, str]:
    allowed = ALL_SCOPES if user.is_system_admin else ALL_SCOPES - {Scope.ADMIN.value}
    cleaned = _validate_scopes(scopes, allowed)
    active = db.scalars(select(AccessToken.id).where(AccessToken.user_id == user.id,
                                                     AccessToken.token_type == TokenType.PERSONAL,
                                                     AccessToken.revoked_at.is_(None))).all()
    if len(active) >= MAX_PERSONAL_TOKENS:
        raise BadRequest("TOO_MANY_TOKENS", f"Tối đa {MAX_PERSONAL_TOKENS} token còn hiệu lực")
    token, raw = _new_token(user_id=user.id, name=name, token_type=TokenType.PERSONAL, scopes=cleaned,
                            expires_at=_expiry(expires_in_days), created_by=user.id)
    db.add(token)
    db.flush()
    return token, raw


def create_service_token(db: Session, project_id: uuid.UUID, creator: User, name: str, scopes: list[str],
                         expires_in_days: int | None) -> tuple[AccessToken, str]:
    cleaned = _validate_scopes(scopes, SERVICE_TOKEN_SCOPES)
    token, raw = _new_token(project_id=project_id, name=name, token_type=TokenType.SERVICE, scopes=cleaned,
                            expires_at=_expiry(expires_in_days), created_by=creator.id)
    db.add(token)
    db.flush()
    return token, raw


def create_build_token(db: Session, project_id: uuid.UUID, build_id: uuid.UUID, ttl_seconds: int
                       ) -> tuple[AccessToken, str]:
    """Token tạm cho container build: chỉ read_package, gắn project, hết hạn theo timeout build."""
    token, raw = _new_token(project_id=project_id, build_id=build_id, name=f"build-{build_id}",
                            token_type=TokenType.RUNNER, scopes=[Scope.READ_PACKAGE.value],
                            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds + 300))
    db.add(token)
    db.flush()
    return token, raw


def revoke_build_tokens(db: Session, build_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    for token in db.scalars(select(AccessToken).where(AccessToken.build_id == build_id,
                                                      AccessToken.revoked_at.is_(None))):
        token.revoked_at = now


def register_runner_token(db: Session, raw: str) -> AccessToken:
    """Đăng ký RUNNER_TOKEN (từ .env) vào DB dưới dạng hash — idempotent, dùng khi bootstrap."""
    h = hash_token(raw)
    existing = db.scalar(select(AccessToken).where(AccessToken.token_hash == h))
    if existing is not None:
        return existing
    token = AccessToken(token_hash=h, token_prefix=raw[:8] + "…", name="system-runner",
                        token_type=TokenType.RUNNER,
                        scopes=[Scope.WRITE_PACKAGE.value, Scope.READ_PACKAGE.value])
    db.add(token)
    db.flush()
    return token
