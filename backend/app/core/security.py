from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"

# Tiền tố token do Tool Hub phát hành, giúp secret scanner nhận diện token bị lộ.
TOKEN_PREFIX = "hth_"

_hasher = PasswordHasher()  # mặc định Argon2id

MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


# Hash giả để so sánh khi user không tồn tại — chống dò username qua thời gian phản hồi.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


# ── JWT access token (15 phút) ──

def create_access_token(user_id: uuid.UUID, *, is_admin: bool) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "typ": ACCESS_TOKEN_TYPE,
        "adm": is_admin,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.access_token_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Ném jwt.ExpiredSignatureError / jwt.InvalidTokenError. Pin cứng đúng 1 thuật toán."""
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALGORITHM])
    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("wrong token type")
    return payload


# ── Opaque tokens (refresh / PAT / service / runner) ──

def generate_opaque_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    """Token có entropy cao → SHA-256 là đủ (không cần Argon2) và tra cứu được theo hash."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def token_display_prefix(raw: str) -> str:
    return raw[:12]


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
