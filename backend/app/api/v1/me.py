from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentPrincipal, DbDep, require_user
from app.core.errors import BadRequest, Conflict, NotFound
from app.db.models import AccessToken, SshKey
from app.db.models.enums import TokenType
from app.schemas import SshKeyCreate, SshKeyOut, TokenCreate, TokenCreated, TokenOut
from app.services import audit_service, token_service
from app.services.audit_service import A
from app.utils.ssh import InvalidSshKey, parse_public_key

router = APIRouter(prefix="/me", tags=["me"])


# ───────────── SSH keys ─────────────

@router.get("/ssh-keys", response_model=list[SshKeyOut])
def list_ssh_keys(principal: CurrentPrincipal, db: DbDep) -> list[SshKeyOut]:
    user = require_user(principal)
    rows = db.scalars(select(SshKey).where(SshKey.user_id == user.id, SshKey.revoked_at.is_(None))
                      .order_by(SshKey.created_at.desc())).all()
    return [SshKeyOut.model_validate(k) for k in rows]


@router.post("/ssh-keys", response_model=SshKeyOut, status_code=status.HTTP_201_CREATED)
def add_ssh_key(body: SshKeyCreate, request: Request, principal: CurrentPrincipal, db: DbDep) -> SshKeyOut:
    user = require_user(principal)
    try:
        parsed = parse_public_key(body.public_key)
    except InvalidSshKey as exc:
        raise BadRequest("INVALID_SSH_KEY", str(exc)) from exc
    key = SshKey(user_id=user.id, title=body.title.strip(), fingerprint=parsed.fingerprint,
                 public_key=parsed.canonical, key_type=parsed.key_type)
    db.add(key)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("SSH_KEY_EXISTS", "SSH key này đã được đăng ký") from exc
    audit_service.record(db, A.ADD_SSH_KEY, principal=principal, resource_type="ssh_key", resource_id=key.id,
                         request=request, metadata={"fingerprint": key.fingerprint, "title": key.title})
    db.commit()
    return SshKeyOut.model_validate(key)


@router.delete("/ssh-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_ssh_key(key_id: uuid.UUID, request: Request, principal: CurrentPrincipal, db: DbDep) -> None:
    user = require_user(principal)
    key = db.scalar(select(SshKey).where(SshKey.id == key_id, SshKey.user_id == user.id, SshKey.revoked_at.is_(None)))
    if key is None:
        raise NotFound("SSH_KEY_NOT_FOUND", "Không tìm thấy SSH key")
    key.revoked_at = datetime.now(timezone.utc)
    audit_service.record(db, A.REMOVE_SSH_KEY, principal=principal, resource_type="ssh_key", resource_id=key.id,
                         request=request, metadata={"fingerprint": key.fingerprint})
    db.commit()


# ───────────── Personal access tokens ─────────────

@router.get("/tokens", response_model=list[TokenOut])
def list_tokens(principal: CurrentPrincipal, db: DbDep, include_revoked: bool = False) -> list[TokenOut]:
    user = require_user(principal)
    stmt = select(AccessToken).where(AccessToken.user_id == user.id, AccessToken.token_type == TokenType.PERSONAL)
    if not include_revoked:
        stmt = stmt.where(AccessToken.revoked_at.is_(None))
    return [TokenOut.model_validate(t) for t in db.scalars(stmt.order_by(AccessToken.created_at.desc()))]


@router.post("/tokens", response_model=TokenCreated, status_code=status.HTTP_201_CREATED)
def create_token(body: TokenCreate, request: Request, principal: CurrentPrincipal, db: DbDep) -> TokenCreated:
    user = require_user(principal)
    if principal.via == "token":
        # Không cho token tự đẻ token khác (chống leo thang từ token bị lộ).
        raise BadRequest("VALIDATION_ERROR", "Hãy tạo token từ Web UI (phiên đăng nhập)")
    token, raw = token_service.create_personal_token(db, user, body.name.strip(), body.scopes, body.expires_in_days)
    audit_service.record(db, A.CREATE_TOKEN, principal=principal, resource_type="access_token", resource_id=token.id,
                         request=request, metadata={"name": token.name, "scopes": token.scopes, "type": "PERSONAL"})
    db.commit()
    return TokenCreated(**TokenOut.model_validate(token).model_dump(), token=raw)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(token_id: uuid.UUID, request: Request, principal: CurrentPrincipal, db: DbDep) -> None:
    user = require_user(principal)
    token = db.scalar(select(AccessToken).where(AccessToken.id == token_id, AccessToken.user_id == user.id,
                                                AccessToken.token_type == TokenType.PERSONAL))
    if token is None or token.revoked_at is not None:
        raise NotFound("TOKEN_NOT_FOUND", "Không tìm thấy token")
    token.revoked_at = datetime.now(timezone.utc)
    audit_service.record(db, A.REVOKE_TOKEN, principal=principal, resource_type="access_token", resource_id=token.id,
                         request=request, metadata={"name": token.name})
    db.commit()
