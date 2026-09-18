"""Integration: runner lifecycle, publish, simple API, download, SHA256, yank, không overwrite, RBAC package."""
from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

from sqlalchemy import select

from app.db.models import AuditLog, PackageVersion
from app.services import git_service
from tests.conftest import internal_headers, login, runner_headers
from tests.helpers import ZERO, WorkRepo, finish, make_sdist, make_wheel, publish, send_event, start_build


def _bare(project: dict) -> Path:
    return git_service.absolute_repo_path(f"{project['group_slug']}/{project['slug']}.git")


def _release(world, admin, version: str = "1.0.0", tag: str | None = None) -> dict:
    """push main + tag → trả về payload start của release build."""
    p = world["project"]
    work = world.setdefault("_work", WorkRepo(_bare(p)))
    sha = work.commit(version)
    work.push("main")
    tag = tag or f"v{version}"
    tag_sha = work.tag(tag)
    work.push(tag)
    builds = send_event(admin, p["id"], "ai-tools/pdf-parser", [(ZERO, sha, "refs/heads/main"),
                                                                 (ZERO, tag_sha, f"refs/tags/{tag}")]).json()["builds"]
    release = next(b for b in builds if b["trigger"] == "TAG")
    r = start_build(admin, release["id"])
    assert r.status_code == 200, r.text
    return r.json()


def _basic(token: str) -> dict:
    import base64

    return {"Authorization": "Basic " + base64.b64encode(f"__token__:{token}".encode()).decode()}


def _pat(client, scopes=("read_package",)) -> str:
    return client.post("/api/v1/me/tokens", json={"name": "pip", "scopes": list(scopes)}).json()["token"]


# ── runner lifecycle ──

def test_runner_endpoints_require_runner_token(world, admin, anon):
    assert anon.post("/api/v1/internal/runner/heartbeat", json={"name": "r1"}).status_code == 401
    pat = _pat(admin, ("write_package", "admin"))
    r = anon.post("/api/v1/internal/runner/heartbeat", json={"name": "r1"}, headers={"Authorization": f"Bearer {pat}"})
    assert r.status_code == 401  # PAT không phải RUNNER token
    r = anon.post("/api/v1/internal/runner/heartbeat", json={"name": "r1", "hostname": "h"}, headers=runner_headers())
    assert r.status_code == 200 and r.json()["accept_jobs"] is True
    runners = admin.get("/api/v1/admin/runners").json()
    assert runners[0]["name"] == "r1" and runners[0]["online"] is True


def test_build_lifecycle_source_log_finish(world, admin, anon):
    start = _release(world, admin)
    bid = start["build_id"]
    assert start["publish_package"] is True and start["requested_version"] == "1.0.0"
    assert start["package_name"] == "hawee-pdf-parser" and start["build_token"].startswith("hth_")
    # không start lại được
    assert start_build(admin, bid).status_code == 409
    # source = git archive đúng commit
    src = anon.get(f"/api/v1/internal/builds/{bid}/source", headers=runner_headers())
    assert src.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(src.content), mode="r:gz") as tf:
        names = tf.getnames()
        assert "pyproject.toml" in names
        assert b'version = "1.0.0"' in tf.extractfile("pyproject.toml").read()
    # build token: chỉ đọc package, hết hiệu lực khi build xong
    tok = {"Authorization": f"Bearer {start['build_token']}"}
    assert anon.get("/simple/", headers=tok).status_code == 200
    assert anon.get("/api/v1/auth/me", headers=tok).status_code == 403
    # live log
    anon.post(f"/api/v1/internal/builds/{bid}/log", headers=runner_headers(), json={"lines": "step 1\n"})
    anon.post(f"/api/v1/internal/builds/{bid}/log", headers=runner_headers(), json={"lines": "step 2\n"})
    dev = login("developer")
    log = dev.get(f"/api/v1/builds/{bid}/log").json()
    assert log["content"] == "step 1\nstep 2\n" and log["complete"] is False
    assert login("viewer").get(f"/api/v1/builds/{bid}/log").status_code == 403   # viewer không xem log
    r = finish(admin, bid, "FAILED", "TEST_FAILED", "full log\nFAILED\n")
    assert r.status_code == 200 and r.json()["status"] == "FAILED" and r.json()["error_code"] == "TEST_FAILED"
    log = dev.get(f"/api/v1/builds/{bid}/log").json()
    assert log["complete"] is True and "FAILED" in log["content"]
    assert anon.get("/simple/", headers=tok).status_code == 401  # token build đã bị thu hồi
    b = dev.get(f"/api/v1/builds/{bid}").json()
    assert b["duration_seconds"] is not None and b["runner_name"] == "runner-test"
    # finish lặp lại → idempotent
    assert finish(admin, bid, "SUCCESS").json()["status"] == "FAILED"


def test_cancel_build(world, admin, anon):
    p = world["project"]
    work = WorkRepo(_bare(p))
    sha = work.commit()
    work.push("main")
    bid = send_event(admin, p["id"], "ai-tools/pdf-parser", [(ZERO, sha, "refs/heads/main")]).json()["builds"][0]["id"]
    assert login("developer").post(f"/api/v1/builds/{bid}/cancel").status_code == 403
    maint = login("maintainer")
    r = maint.post(f"/api/v1/builds/{bid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert start_build(admin, bid).status_code == 409
    # đang chạy → cờ cancel cho runner
    sha2 = work.commit("0.1.1")
    work.push("main")
    bid2 = send_event(admin, p["id"], "ai-tools/pdf-parser", [(sha, sha2, "refs/heads/main")]).json()["builds"][0]["id"]
    start_build(admin, bid2)
    maint.post(f"/api/v1/builds/{bid2}/cancel")
    ctl = anon.get(f"/api/v1/internal/builds/{bid2}/control", headers=runner_headers()).json()
    assert ctl["cancel"] is True and ctl["status"] == "RUNNING"


# ── publish ──

def test_publish_and_install_metadata(world, admin, anon, db):
    start = _release(world, admin)
    whl = make_wheel("hawee-pdf-parser", "1.0.0")
    sdist = make_sdist("hawee-pdf-parser", "1.0.0")
    r = publish(admin, start, [whl, sdist])
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["version"] == "1.0.0" and pv["git_tag"] == "v1.0.0" and len(pv["files"]) == 2
    wheel_file = next(f for f in pv["files"] if f["file_type"] == "WHEEL")
    assert wheel_file["sha256"] == hashlib.sha256(whl[1]).hexdigest()
    assert wheel_file["python_requires"] == ">=3.10"
    finish(admin, start["build_id"], "SUCCESS")
    proj = admin.get("/api/v1/projects/ai-tools/pdf-parser").json()
    assert proj["latest_release_version"] == "1.0.0" and proj["last_build_status"] == "SUCCESS"
    assert proj["python_requires"] == ">=3.10"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "PACKAGE_PUBLISHED")) is not None
    # build detail có artifact
    b = admin.get(f"/api/v1/builds/{start['build_id']}").json()
    assert {a["filename"] for a in b["artifacts"]} == {whl[0], sdist[0]}


def test_publish_rejects_bad_inputs(world, admin):
    start = _release(world, admin)
    whl = make_wheel("hawee-pdf-parser", "1.0.0")
    # version khác tag
    r = publish(admin, start, [make_wheel("hawee-pdf-parser", "1.0.1")], version="1.0.1")
    assert r.json()["error"]["code"] == "VERSION_MISMATCH"
    # package không thuộc project / thiếu prefix
    r = publish(admin, start, [make_wheel("requests", "1.0.0")], package_name="requests")
    assert r.json()["error"]["code"] == "INTERNAL_PREFIX_REQUIRED"
    r = publish(admin, start, [make_wheel("hawee-other", "1.0.0")], package_name="hawee-other")
    assert r.json()["error"]["code"] == "INVALID_PACKAGE_NAME"
    # metadata bên trong wheel không khớp (không tin tên file)
    r = publish(admin, start, [make_wheel("hawee-pdf-parser", "1.0.0", meta_version="9.9.9")])
    assert r.json()["error"]["code"] == "INVALID_PACKAGE_FILE"
    # sai loại file
    r = publish(admin, start, [("hawee_pdf_parser-1.0.0.exe", b"MZ")])
    assert r.json()["error"]["code"] == "INVALID_PACKAGE_FILE"
    # chỉ sdist, không có wheel
    r = publish(admin, start, [make_sdist("hawee-pdf-parser", "1.0.0")])
    assert r.json()["error"]["code"] == "INVALID_PACKAGE_FILE"
    # zip hỏng
    r = publish(admin, start, [("hawee_pdf_parser-1.0.0-py3-none-any.whl", b"not a zip")])
    assert r.json()["error"]["code"] == "INVALID_PACKAGE_FILE"
    assert publish(admin, start, [whl]).status_code == 200


def test_version_cannot_be_overwritten(world, admin):
    start = _release(world, admin)
    assert publish(admin, start, [make_wheel("hawee-pdf-parser", "1.0.0")]).status_code == 200
    finish(admin, start["build_id"])
    # xoá tag đã publish → pre-receive chặn (release bất biến)
    r = admin.post("/api/v1/internal/git/authorize-push", headers=internal_headers(), json={
        "user_id": str(world["users"]["maintainer"].id), "project_id": world["project"]["id"],
        "updates": [{"old_sha": start["commit_sha"], "new_sha": ZERO, "ref": "refs/tags/v1.0.0"}]})
    assert r.json()["allowed"] is False
    # Build lại (manual trên tag) → publish cùng version bị từ chối
    maint = login("maintainer")
    rebuild = maint.post("/api/v1/projects/ai-tools/pdf-parser/builds/manual", json={"ref": "v1.0.0"}).json()
    assert rebuild["publish_package"] is True
    s2 = start_build(admin, rebuild["id"]).json()
    r = publish(admin, s2, [make_wheel("hawee-pdf-parser", "1.0.0")])
    assert r.status_code == 409 and r.json()["error"]["code"] == "PACKAGE_VERSION_EXISTS"


def test_publish_only_for_release_builds(world, admin):
    p = world["project"]
    work = WorkRepo(_bare(p))
    sha = work.commit("1.0.0")
    work.push("main")
    bid = send_event(admin, p["id"], "ai-tools/pdf-parser", [(ZERO, sha, "refs/heads/main")]).json()["builds"][0]["id"]
    s = start_build(admin, bid).json()
    r = publish(admin, {**s, "requested_version": "1.0.0"}, [make_wheel("hawee-pdf-parser", "1.0.0")])
    assert r.json()["error"]["code"] == "PUBLISH_NOT_ALLOWED"
    # artifact của validation build vẫn lưu được
    fn, content = make_wheel("hawee-pdf-parser", "1.0.0")
    r = admin.post(f"/api/v1/internal/builds/{bid}/artifacts", headers=runner_headers(),
                   files=[("files", (fn, content, "application/octet-stream"))])
    assert r.status_code == 200
    dl = login("developer").get(f"/api/v1/builds/{bid}/artifacts/{fn}")
    assert dl.status_code == 200 and dl.content == content


# ── simple API ──

def test_simple_api_internal_package(world, admin, anon):
    start = _release(world, admin)
    whl = make_wheel("hawee-pdf-parser", "1.0.0")
    publish(admin, start, [whl, make_sdist("hawee-pdf-parser", "1.0.0")])
    finish(admin, start["build_id"])
    dev = login("developer")
    tok = _pat(dev)
    # Không auth → 401 + WWW-Authenticate (pip sẽ hỏi credential)
    r = anon.get("/simple/hawee-pdf-parser/")
    assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
    r = anon.get("/simple/hawee-pdf-parser/", headers=_basic(tok))
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    sha = hashlib.sha256(whl[1]).hexdigest()
    assert f'/packages/internal/hawee-pdf-parser/1.0.0/{whl[0]}#sha256={sha}' in r.text
    assert 'data-requires-python="&gt;=3.10"' in r.text
    # PEP 503 normalize + redirect
    r = anon.get("/simple/Hawee_PDF.Parser/", headers=_basic(tok), follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/simple/hawee-pdf-parser/"
    # PEP 691 JSON
    r = anon.get("/simple/hawee-pdf-parser/", headers={**_basic(tok), "Accept": "application/vnd.pypi.simple.v1+json"})
    data = r.json()
    assert data["meta"]["api-version"] == "1.0" and {f["filename"] for f in data["files"]} >= {whl[0]}
    # root list
    r = anon.get("/simple/", headers=_basic(tok))
    assert 'href="/simple/hawee-pdf-parser/"' in r.text
    # tải file: đúng nội dung + SHA256, đếm download + audit
    r = anon.get(f"/packages/internal/hawee-pdf-parser/1.0.0/{whl[0]}", headers=_basic(tok))
    assert r.status_code == 200 and r.content == whl[1]
    assert hashlib.sha256(r.content).hexdigest() == sha
    pkg = dev.get("/api/v1/packages/hawee-pdf-parser").json()
    assert pkg["versions"][0]["download_count"] == 1


def test_private_package_hidden_and_scope_required(world, admin, anon):
    start = _release(world, admin)
    publish(admin, start, [make_wheel("hawee-pdf-parser", "1.0.0")])
    finish(admin, start["build_id"])
    outsider_tok = _pat(login("outsider"))
    r = anon.get("/simple/hawee-pdf-parser/", headers=_basic(outsider_tok))
    assert r.status_code == 404                                   # không lộ package private
    assert "hawee-pdf-parser" not in anon.get("/simple/", headers=_basic(outsider_tok)).text
    api_only = _pat(login("developer"), ("read_api",))
    r = anon.get("/simple/hawee-pdf-parser/", headers=_basic(api_only))
    assert r.status_code == 403 and r.json()["error"]["code"] == "INSUFFICIENT_SCOPE"
    # password đăng nhập KHÔNG dùng được cho pip
    import base64

    r = anon.get("/simple/", headers={"Authorization": "Basic " + base64.b64encode(b"developer:Passw0rd!Strong").decode()})
    assert r.status_code == 401


def test_service_token_for_ci(world, admin, anon):
    start = _release(world, admin)
    publish(admin, start, [make_wheel("hawee-pdf-parser", "1.0.0")])
    finish(admin, start["build_id"])
    maint = login("maintainer")
    dev = login("developer")
    assert dev.post("/api/v1/projects/ai-tools/pdf-parser/tokens",
                    json={"name": "ci", "scopes": ["read_package"]}).status_code == 403
    r = maint.post("/api/v1/projects/ai-tools/pdf-parser/tokens", json={"name": "ci", "scopes": ["write_package"]})
    assert r.status_code == 403  # service token không được quyền ghi package
    tok = maint.post("/api/v1/projects/ai-tools/pdf-parser/tokens",
                     json={"name": "ci", "scopes": ["read_package"]}).json()["token"]
    assert anon.get("/simple/hawee-pdf-parser/", headers=_basic(tok)).status_code == 200


def test_yank(world, admin, anon):
    start = _release(world, admin)
    publish(admin, start, [make_wheel("hawee-pdf-parser", "1.0.0")])
    finish(admin, start["build_id"])
    dev = login("developer")
    assert dev.post("/api/v1/packages/hawee-pdf-parser/1.0.0/yank", json={"reason": "bug"}).status_code == 403
    maint = login("maintainer")
    r = maint.post("/api/v1/packages/hawee-pdf-parser/1.0.0/yank", json={"reason": "lỗi nghiêm trọng"})
    assert r.status_code == 200 and r.json()["is_yanked"] is True
    tok = _pat(dev)
    html = anon.get("/simple/hawee-pdf-parser/", headers=_basic(tok)).text
    assert 'data-yanked="lỗi nghiêm trọng"' in html
    # file vẫn tải được (pip vẫn cài được khi pin đúng version)
    assert anon.get("/packages/internal/hawee-pdf-parser/1.0.0/hawee_pdf_parser-1.0.0-py3-none-any.whl",
                    headers=_basic(tok)).status_code == 200
    assert maint.post("/api/v1/packages/hawee-pdf-parser/1.0.0/yank",
                      json={"reason": "fixed", "yank": False}).json()["is_yanked"] is False


def test_latest_release_tracks_highest_version(world, admin, db):
    s1 = _release(world, admin, "1.2.0")
    publish(admin, s1, [make_wheel("hawee-pdf-parser", "1.2.0")])
    finish(admin, s1["build_id"])
    s2 = _release(world, admin, "1.1.5")  # hotfix nhánh cũ
    publish(admin, s2, [make_wheel("hawee-pdf-parser", "1.1.5")])
    finish(admin, s2["build_id"])
    proj = admin.get("/api/v1/projects/ai-tools/pdf-parser").json()
    assert proj["latest_release_version"] == "1.2.0"
    versions = [v["version"] for v in admin.get("/api/v1/projects/ai-tools/pdf-parser/packages").json()]
    assert versions == ["1.2.0", "1.1.5"]
    assert db.scalar(select(PackageVersion).where(PackageVersion.version == "1.1.5")).git_tag == "v1.1.5"


def test_dashboard_and_catalogue(world, admin):
    start = _release(world, admin)
    publish(admin, start, [make_wheel("hawee-pdf-parser", "1.0.0")])
    finish(admin, start["build_id"])
    dev = login("developer")
    d = dev.get("/api/v1/dashboard").json()
    assert d["my_projects"][0]["package_name"] == "hawee-pdf-parser"
    assert d["my_groups"][0]["slug"] == "ai-tools"
    assert d["recent_releases"][0]["version"] == "1.0.0"
    assert len(d["recent_builds"]) == 2
    pkgs = dev.get("/api/v1/packages", params={"q": "pdf"}).json()
    assert pkgs[0]["package_name"] == "hawee-pdf-parser" and pkgs[0]["latest_version"] == "1.0.0"
    assert login("outsider").get("/api/v1/packages").json() == []
