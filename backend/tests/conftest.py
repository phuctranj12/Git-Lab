"""Hạ tầng test: PostgreSQL/Redis/MinIO thật (deploy/docker-compose.test.yml), PyPI giả lập bằng respx."""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path

import pytest

_GIT_ROOT = Path(tempfile.mkdtemp(prefix="toolhub-git-"))

os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL",
                                   "postgresql+psycopg://toolhub:toolhub@127.0.0.1:55432/toolhub_test"),
    "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:56379/15"),
    "MINIO_ENDPOINT": os.environ.get("TEST_MINIO_ENDPOINT", "127.0.0.1:59000"),
    "MINIO_ACCESS_KEY": "toolhub",
    "MINIO_SECRET_KEY": "toolhub-secret",
    "MINIO_BUCKET_PACKAGES": "test-packages",
    "MINIO_BUCKET_LOGS": "test-build-logs",
    "MINIO_BUCKET_ARTIFACTS": "test-artifacts",
    "MINIO_BUCKET_BACKUPS": "test-backups",
    "JWT_SECRET": "test-jwt-secret-0123456789abcdef0123456789",
    "INTERNAL_SERVICE_SECRET": "test-internal-secret-0123456789abcdef0123",
    "RUNNER_TOKEN": "test-runner-token-0123456789abcdef0123456789",
    "GIT_REPOSITORY_ROOT": str(_GIT_ROOT),
    "PYPI_UPSTREAM_SIMPLE_URL": "https://pypi.test/simple",
    "PYPI_UPSTREAM_FILE_HOSTS": "files.pypi.test",
    "LOGIN_RATE_LIMIT_PER_MINUTE": "1000",
    "API_RATE_LIMIT_PER_MINUTE": "100000",
    "REGISTRY_RATE_LIMIT_PER_MINUTE": "100000",
    "GIT_SSH_HOST": "git.test",
    "GIT_SSH_PORT": "2222",
    "LOG_LEVEL": "WARNING",
})

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.cli import migrate  # noqa: E402
from app.core.redis import get_redis  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import User  # noqa: E402
from app.db.session import get_engine, session_factory  # noqa: E402
from app.services import storage_service, token_service  # noqa: E402

TABLES = ["package_files", "package_versions", "upstream_files", "upstream_packages", "git_push_events", "builds",
          "access_tokens", "ssh_keys", "project_members", "projects", "group_members", "groups", "refresh_tokens",
          "audit_logs", "runner_nodes", "users"]

PASSWORD = "Passw0rd!Strong"
RUNNER_TOKEN = os.environ["RUNNER_TOKEN"]
INTERNAL_SECRET = os.environ["INTERNAL_SERVICE_SECRET"]


def _force_rmtree(path: Path) -> None:
    def _onexc(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onexc=_onexc)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    migrate()
    storage_service.ensure_buckets()
    yield
    shutil.rmtree(_GIT_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean():
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))
    get_redis().flushdb()
    for child in _GIT_ROOT.iterdir():
        _force_rmtree(child)
    with session_factory()() as db:
        token_service.register_runner_token(db, RUNNER_TOKEN)
        db.commit()
    yield


@pytest.fixture
def db():
    with session_factory()() as session:
        yield session


def make_user(username: str, *, admin: bool = False, active: bool = True) -> User:
    with session_factory()() as s:
        user = User(username=username, email=f"{username}@hawee.local", full_name=username.title(),
                    password_hash=hash_password(PASSWORD), is_system_admin=admin, is_active=active)
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


def login(username: str) -> TestClient:
    from app.main import app

    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/api/v1/auth/login", json={"login": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    client.headers["X-CSRF-Token"] = client.cookies.get("toolhub_csrf")
    return client


@pytest.fixture
def anon() -> TestClient:
    from app.main import app

    return TestClient(app)


@pytest.fixture
def admin_user() -> User:
    return make_user("admin", admin=True)


@pytest.fixture
def admin(admin_user) -> TestClient:
    return login("admin")


def internal_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {INTERNAL_SECRET}"}


def runner_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RUNNER_TOKEN}"}


@pytest.fixture
def world(admin):
    """Group 'ai-tools' + project python 'pdf-parser' + user theo từng role."""
    users = {role: make_user(role) for role in ("owner", "maintainer", "developer", "viewer", "outsider")}
    r = admin.post("/api/v1/groups", json={"name": "AI Tools", "slug": "ai-tools", "visibility": "PRIVATE",
                                           "owner_id": str(users["owner"].id)})
    assert r.status_code == 201, r.text
    for role in ("maintainer", "developer", "viewer"):
        r = admin.post("/api/v1/groups/ai-tools/members", json={"user_id": str(users[role].id), "role": role.upper()})
        assert r.status_code == 201, r.text
    owner = login("owner")
    r = owner.post("/api/v1/projects", json={"group": "ai-tools", "name": "PDF Parser", "slug": "pdf-parser",
                                             "package_name": "hawee-pdf-parser", "visibility": "PRIVATE"})
    assert r.status_code == 201, r.text
    return {"users": users, "project": r.json(), "owner": owner}


def new_uuid() -> str:
    return str(uuid.uuid4())
