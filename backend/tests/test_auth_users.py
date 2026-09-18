"""Integration: login, refresh rotation, CSRF, rate limit, user admin, SSH key, token, audit."""
from __future__ import annotations

import base64
import os
import struct

from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.models import AccessToken, AuditLog
from tests.conftest import PASSWORD, login, make_user


def _actions(db) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.created_at)))


def _ed25519_pub(comment: str = "dev@pc") -> str:
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + os.urandom(32)
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}"


def test_login_success_sets_httponly_cookies_and_audits(anon, admin_user, db):
    r = anon.post("/api/v1/auth/login", json={"login": "admin", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["username"] == "admin"
    set_cookie = r.headers.get_list("set-cookie")
    access = next(c for c in set_cookie if c.startswith("toolhub_access="))
    refresh = next(c for c in set_cookie if c.startswith("toolhub_refresh="))
    assert "HttpOnly" in access and "SameSite=strict" in access
    assert "Path=/api/v1/auth" in refresh and "HttpOnly" in refresh
    assert "password" not in r.text.lower()
    # email cũng đăng nhập được
    assert anon.post("/api/v1/auth/login", json={"login": "ADMIN@hawee.local", "password": PASSWORD}).status_code == 200
    assert _actions(db).count("LOGIN") == 2


def test_login_failed_is_audited_and_generic(anon, admin_user, db):
    r = anon.post("/api/v1/auth/login", json={"login": "admin", "password": "wrong-password"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    r2 = anon.post("/api/v1/auth/login", json={"login": "nobody", "password": "wrong-password"})
    assert r2.json()["error"]["message"] == r.json()["error"]["message"]
    assert _actions(db).count("LOGIN_FAILED") == 2


def test_login_rate_limit(anon, admin_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_rate_limit_per_minute", 5)
    codes = [anon.post("/api/v1/auth/login", json={"login": "admin", "password": "bad"}).status_code for _ in range(7)]
    assert codes[:5] == [401] * 5
    assert codes[5] == 429 and codes[6] == 429


def test_me_requires_auth(anon):
    r = anon.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_csrf_required_for_cookie_writes(admin):
    csrf = admin.headers.pop("X-CSRF-Token")
    r = admin.post("/api/v1/me/tokens", json={"name": "x", "scopes": ["read_package"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"
    admin.headers["X-CSRF-Token"] = csrf
    assert admin.post("/api/v1/me/tokens", json={"name": "x", "scopes": ["read_package"]}).status_code == 201


def test_refresh_rotation_and_reuse_detection(admin):
    old_refresh = admin.cookies.get("toolhub_refresh", path="/api/v1/auth")
    r = admin.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    new_refresh = admin.cookies.get("toolhub_refresh", path="/api/v1/auth")
    assert new_refresh and new_refresh != old_refresh
    # Dùng lại refresh cũ → thu hồi toàn bộ phiên (kể cả refresh mới)
    admin.cookies.set("toolhub_refresh", old_refresh, path="/api/v1/auth")
    r = admin.post("/api/v1/auth/refresh")
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_REVOKED"
    admin.cookies.set("toolhub_refresh", new_refresh, path="/api/v1/auth")
    assert admin.post("/api/v1/auth/refresh").status_code == 401


def test_logout_revokes_refresh(admin):
    refresh = admin.cookies.get("toolhub_refresh", path="/api/v1/auth")
    assert admin.post("/api/v1/auth/logout").status_code == 204
    admin.cookies.set("toolhub_refresh", refresh, path="/api/v1/auth")
    assert admin.post("/api/v1/auth/refresh").status_code == 401


def test_admin_user_management(admin, db):
    r = admin.post("/api/v1/users", json={"username": "phuc", "email": "Phuc@Hawee.com.vn", "password": "longpassword",
                                          "full_name": "Phúc"})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert r.json()["email"] == "phuc@hawee.com.vn"
    assert admin.post("/api/v1/users", json={"username": "phuc", "email": "other@x.vn",
                                             "password": "longpassword"}).status_code == 409
    r = admin.post(f"/api/v1/users/{uid}/disable")
    assert r.status_code == 200 and r.json()["is_active"] is False
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    assert c.post("/api/v1/auth/login", json={"login": "phuc", "password": "longpassword"}).status_code == 401
    admin.post(f"/api/v1/users/{uid}/enable")
    assert c.post("/api/v1/auth/login", json={"login": "phuc", "password": "longpassword"}).status_code == 200
    acts = _actions(db)
    assert "CREATE_USER" in acts and "DISABLE_USER" in acts


def test_disabled_user_session_rejected(admin):
    make_user("bob")
    bob = login("bob")
    assert bob.get("/api/v1/auth/me").status_code == 200
    bob_id = bob.get("/api/v1/auth/me").json()["id"]
    admin.post(f"/api/v1/users/{bob_id}/disable")
    assert bob.get("/api/v1/auth/me").status_code == 401


def test_non_admin_cannot_manage_users(admin):
    make_user("carol")
    carol = login("carol")
    assert carol.get("/api/v1/users").status_code == 403
    assert carol.post("/api/v1/users", json={"username": "x1", "email": "x1@x.vn",
                                             "password": "longpassword"}).status_code == 403
    assert carol.get("/api/v1/users/search", params={"q": "adm"}).status_code == 200


def test_ssh_key_lifecycle(admin, db):
    pub = _ed25519_pub()
    r = admin.post("/api/v1/me/ssh-keys", json={"title": "laptop", "public_key": pub})
    assert r.status_code == 201, r.text
    key = r.json()
    assert key["fingerprint"].startswith("SHA256:")
    assert "public_key" not in key
    assert admin.post("/api/v1/me/ssh-keys", json={"title": "dup", "public_key": pub}).status_code == 409
    bad = admin.post("/api/v1/me/ssh-keys", json={"title": "bad",
                                                  "public_key": "-----BEGIN OPENSSH PRIVATE KEY----- xxxxxxxxxxxx"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_SSH_KEY"
    assert len(admin.get("/api/v1/me/ssh-keys").json()) == 1
    assert admin.delete(f"/api/v1/me/ssh-keys/{key['id']}").status_code == 204
    assert admin.get("/api/v1/me/ssh-keys").json() == []
    # thu hồi xong thêm lại được
    assert admin.post("/api/v1/me/ssh-keys", json={"title": "again", "public_key": pub}).status_code == 201
    acts = _actions(db)
    assert "ADD_SSH_KEY" in acts and "REMOVE_SSH_KEY" in acts


def test_personal_token_shown_once_and_hashed(admin, db, anon):
    r = admin.post("/api/v1/me/tokens", json={"name": "pip", "scopes": ["read_package"], "expires_in_days": 30})
    assert r.status_code == 201, r.text
    raw = r.json()["token"]
    assert raw.startswith("hth_")
    listed = admin.get("/api/v1/me/tokens").json()
    assert "token" not in listed[0]
    stored = db.scalar(select(AccessToken).where(AccessToken.name == "pip"))
    assert stored.token_hash != raw and raw not in stored.token_hash
    row = db.execute(text("SELECT count(*) FROM access_tokens WHERE token_hash = :t OR token_prefix = :t"),
                     {"t": raw}).scalar()
    assert row == 0
    # token read_package không gọi được API (thiếu read_api)
    r = anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "INSUFFICIENT_SCOPE"
    # token có read_api đọc được, nhưng không ghi được
    api = admin.post("/api/v1/me/tokens", json={"name": "cli", "scopes": ["read_api"]}).json()["token"]
    h = {"Authorization": f"Bearer {api}"}
    assert anon.get("/api/v1/auth/me", headers=h).status_code == 200
    assert anon.post("/api/v1/me/ssh-keys", headers=h,
                     json={"title": "x", "public_key": _ed25519_pub()}).status_code == 403
    # revoke
    assert admin.delete(f"/api/v1/me/tokens/{stored.id}").status_code == 204
    r = anon.get("/simple/", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_REVOKED"
    assert "CREATE_TOKEN" in _actions(db) and "REVOKE_TOKEN" in _actions(db)


def test_non_admin_cannot_mint_admin_scope(admin):
    make_user("dave")
    dave = login("dave")
    r = dave.post("/api/v1/me/tokens", json={"name": "x", "scopes": ["admin"]})
    assert r.status_code == 403
    assert dave.post("/api/v1/me/tokens", json={"name": "x", "scopes": ["bogus"]}).status_code == 400


def test_admin_token_needs_admin_scope(admin, anon):
    raw = admin.post("/api/v1/me/tokens", json={"name": "ro", "scopes": ["read_api"]}).json()["token"]
    assert anon.get("/api/v1/users", headers={"Authorization": f"Bearer {raw}"}).status_code == 403
    raw2 = admin.post("/api/v1/me/tokens", json={"name": "adm", "scopes": ["admin"]}).json()["token"]
    assert anon.get("/api/v1/users", headers={"Authorization": f"Bearer {raw2}"}).status_code == 200


def test_expired_token_rejected(admin, anon, db):
    from datetime import datetime, timedelta, timezone

    raw = admin.post("/api/v1/me/tokens", json={"name": "old", "scopes": ["read_api"]}).json()["token"]
    tok = db.scalar(select(AccessToken).where(AccessToken.name == "old"))
    tok.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    r = anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_EXPIRED"


def test_audit_is_append_only(admin, db):
    import pytest
    from sqlalchemy.exc import DBAPIError

    row = db.scalar(select(AuditLog).limit(1))
    assert row is not None
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE audit_logs SET action = 'X'"))
        db.commit()
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM audit_logs"))
        db.commit()
    db.rollback()


def test_audit_export(admin):
    r = admin.get("/api/v1/admin/audit-logs", params={"action": "LOGIN"})
    assert r.status_code == 200 and r.json()["total"] >= 1
    csv_resp = admin.get("/api/v1/admin/audit-logs/export", params={"format": "csv"})
    assert csv_resp.status_code == 200 and "LOGIN" in csv_resp.text
    js = admin.get("/api/v1/admin/audit-logs/export", params={"format": "json"})
    assert js.status_code == 200 and isinstance(js.json(), list)


def test_request_id_and_error_format(anon):
    r = anon.get("/api/v1/auth/me", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"
    assert r.json()["error"]["request_id"] == "abc-123"


def test_health(anon):
    assert anon.get("/health/live").json()["status"] == "ok"
    r = anon.get("/health/ready")
    assert r.status_code == 200, r.text
    assert {c["name"] for c in r.json()["checks"]} == {"postgresql", "redis", "minio", "git_storage"}
    assert anon.get("/metrics").status_code == 200
