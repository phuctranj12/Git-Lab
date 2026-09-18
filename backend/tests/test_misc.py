"""Logging JSON (che secret), storage, bảo trì định kỳ, system admin, dọn cache upstream."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import JsonFormatter, request_id_var
from app.db.models import AccessToken, Build, RunnerNode, UpstreamFile, UpstreamPackage
from app.db.models.enums import BuildStatus, RunnerStatus, TokenType
from app.services import pypi_proxy_service, storage_service
from tests.conftest import login, runner_headers


def test_json_log_format_scrubs_secrets():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("x",), None)
    rec.extra_data = {"password": "p@ss", "nested": {"refresh_token": "abc", "ok": 1}, "items": [{"token": "t"}]}
    rec.action = "LOGIN"
    token = request_id_var.set("req-1")
    try:
        out = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(token)
    assert out["message"] == "hello x" and out["level"] == "INFO" and out["request_id"] == "req-1"
    assert out["action"] == "LOGIN"
    assert out["data"]["password"] == "***" and out["data"]["nested"]["refresh_token"] == "***"
    assert out["data"]["nested"]["ok"] == 1 and out["data"]["items"][0]["token"] == "***"
    assert "p@ss" not in json.dumps(out)


def test_storage_roundtrip():
    bucket = get_settings().minio_bucket_artifacts
    storage_service.put_bytes(bucket, "t/a.txt", b"hello")
    assert storage_service.object_exists(bucket, "t/a.txt")
    assert storage_service.get_bytes(bucket, "t/a.txt") == b"hello"
    assert b"".join(storage_service.iter_object(bucket, "t/a.txt")) == b"hello"
    storage_service.copy_object(bucket, "t/a.txt", bucket, "t/b.txt")
    assert storage_service.get_bytes(bucket, "t/b.txt") == b"hello"
    storage_service.remove_object(bucket, "t/a.txt")
    storage_service.remove_object(bucket, "t/b.txt")
    assert not storage_service.object_exists(bucket, "t/a.txt")
    assert storage_service.ensure_buckets() == []


def test_maintenance_job(world, admin, anon, db):
    now = datetime.now(timezone.utc)
    runner = RunnerNode(name="old-runner", status=RunnerStatus.ONLINE, last_heartbeat_at=now - timedelta(minutes=10))
    db.add(runner)
    db.add(AccessToken(name="build-x", token_prefix="x", token_hash="f" * 64, token_type=TokenType.RUNNER,
                       scopes=["read_package"], build_id=world["project"]["id"], expires_at=now - timedelta(days=8)))
    b = Build(project_id=world["project"]["id"], number=99, trigger_type="PUSH", ref_name="main", commit_sha="a" * 40,
              status=BuildStatus.RUNNING, started_at=now - timedelta(hours=3))
    db.add(b)
    db.commit()
    r = anon.post("/api/v1/internal/maintenance/run", headers=runner_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["runners_marked_offline"] == 1
    assert data["expired_tokens_removed"]["build"] == 1
    assert data["stale_builds"]["failed_stuck"] == 1
    db.expire_all()
    assert db.get(Build, b.id).error_code == "RUNNER_ERROR"
    assert anon.post("/api/v1/internal/maintenance/run").status_code == 401


def test_admin_system_and_runner_drain(admin, anon, world):
    anon.post("/api/v1/internal/runner/heartbeat", headers=runner_headers(), json={"name": "r1"})
    sys = admin.get("/api/v1/admin/system").json()
    assert sys["readiness"]["ready"] is True and sys["settings"]["internal_package_prefix"] == "hawee-"
    runner = admin.get("/api/v1/admin/runners").json()[0]
    r = admin.patch(f"/api/v1/admin/runners/{runner['id']}", json={"status": "DRAINING"})
    assert r.json()["status"] == "DRAINING"
    hb = anon.post("/api/v1/internal/runner/heartbeat", headers=runner_headers(), json={"name": "r1"}).json()
    assert hb["accept_jobs"] is False
    assert login("developer").get("/api/v1/admin/system").status_code == 403


def test_runner_draining_refuses_build(world, admin, anon):
    from tests.helpers import ZERO, WorkRepo, send_event, start_build
    from app.services import git_service

    p = world["project"]
    work = WorkRepo(git_service.absolute_repo_path("ai-tools/pdf-parser.git"))
    sha = work.commit()
    work.push("main")
    bid = send_event(admin, p["id"], "ai-tools/pdf-parser", [(ZERO, sha, "refs/heads/main")]).json()["builds"][0]["id"]
    anon.post("/api/v1/internal/runner/heartbeat", headers=runner_headers(), json={"name": "runner-test"})
    rid = admin.get("/api/v1/admin/runners").json()[0]["id"]
    admin.patch(f"/api/v1/admin/runners/{rid}", json={"status": "DRAINING"})
    r = start_build(admin, bid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "RUNNER_NOT_ACCEPTING"
    assert admin.get(f"/api/v1/builds/{bid}").json()["status"] == "QUEUED"


def test_upstream_cache_cleanup(db, monkeypatch):
    s = get_settings()
    now = datetime.now(timezone.utc)
    pkg = UpstreamPackage(normalized_name="oldpkg", last_checked_at=now, expires_at=now)
    db.add(pkg)
    db.flush()
    storage_service.put_bytes(s.minio_bucket_packages, "upstream/oldpkg/old-1.0-py3-none-any.whl", b"x" * 100)
    db.add_all([
        UpstreamFile(upstream_package_id=pkg.id, filename="old-1.0-py3-none-any.whl", upstream_url="https://x/o.whl",
                     cached=True, object_key="upstream/oldpkg/old-1.0-py3-none-any.whl", size_bytes=100,
                     last_accessed_at=now - timedelta(days=400)),
        UpstreamFile(upstream_package_id=pkg.id, filename="new-1.0-py3-none-any.whl", upstream_url="https://x/n.whl",
                     cached=True, object_key="upstream/oldpkg/new.whl", size_bytes=100, last_accessed_at=now),
    ])
    db.commit()
    monkeypatch.setattr(s, "upstream_cache_max_bytes", 1000)
    assert pypi_proxy_service.cleanup_cache(db)["removed"] == 0      # chưa vượt ngưỡng → không xoá
    monkeypatch.setattr(s, "upstream_cache_max_bytes", 150)
    result = pypi_proxy_service.cleanup_cache(db)
    assert result["removed"] == 1 and result["freed_bytes"] == 100
    files = {f.filename: f.cached for f in db.scalars(select(UpstreamFile))}
    assert files == {"old-1.0-py3-none-any.whl": False, "new-1.0-py3-none-any.whl": True}


def test_metrics_endpoint(anon):
    anon.get("/health/live")
    body = anon.get("/metrics").text
    assert "http_requests_total" in body and "build_queue_depth" in body
