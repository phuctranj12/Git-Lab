"""Integration: group, member, project + bare repo, git authorize (SSH wrapper), pre-receive, post-receive → build."""
from __future__ import annotations

import base64
import os
import struct
from pathlib import Path

from sqlalchemy import select

from app.db.models import AuditLog, Build
from app.services import git_service
from tests.conftest import internal_headers, login, make_user
from tests.helpers import ZERO, WorkRepo, send_event


def _bare(project: dict) -> Path:
    return git_service.absolute_repo_path(f"{project['group_slug']}/{project['slug']}.git")


def _add_key(client) -> str:
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + os.urandom(32)
    r = client.post("/api/v1/me/ssh-keys", json={"title": "k", "public_key": f"ssh-ed25519 {base64.b64encode(blob).decode()}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _authorize(client, key_id: str, action: str, repo: str = "ai-tools/pdf-parser.git") -> dict:
    r = client.post("/api/v1/internal/git/authorize", headers=internal_headers(),
                    json={"key_id": key_id, "action": action, "repository": repo})
    assert r.status_code == 200, r.text
    return r.json()


# ── group / project ──

def test_group_creation_admin_only(admin):
    make_user("eve")
    eve = login("eve")
    assert eve.post("/api/v1/groups", json={"name": "X", "slug": "x-team"}).status_code == 403
    r = admin.post("/api/v1/groups", json={"name": "AI Team", "slug": "ai-team"})
    assert r.status_code == 201 and r.json()["slug"] == "ai-team"
    assert admin.post("/api/v1/groups", json={"name": "AI", "slug": "ai-team"}).status_code == 409
    assert admin.post("/api/v1/groups", json={"name": "Bad", "slug": "Bad Slug"}).status_code == 400
    members = admin.get("/api/v1/groups/ai-team/members").json()
    assert members[0]["role"] == "OWNER" and members[0]["user"]["username"] == "admin"


def test_project_creation_creates_bare_repo(world, db):
    p = world["project"]
    assert p["package_name"] == "hawee-pdf-parser"
    assert p["clone_url"] == "ssh://git@git.test:2222/ai-tools/pdf-parser.git"
    assert p["install_command"] == "pip install hawee-pdf-parser"
    bare = _bare(p)
    assert (bare / "HEAD").exists() and (bare / "objects").is_dir()
    assert git_service.run_git(bare, "config", "toolhub.projectid").stdout.decode().strip() == p["id"]
    assert git_service.run_git(bare, "rev-parse", "--is-bare-repository").stdout.decode().strip() == "true"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "CREATE_PROJECT")) is not None


def test_project_package_prefix_enforced(world):
    owner = world["owner"]
    r = owner.post("/api/v1/projects", json={"group": "ai-tools", "name": "Req", "slug": "req",
                                             "package_name": "requests"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INTERNAL_PREFIX_REQUIRED"
    r = owner.post("/api/v1/projects", json={"group": "ai-tools", "name": "No pkg", "slug": "no-pkg"})
    assert r.status_code == 400
    r = owner.post("/api/v1/projects", json={"group": "ai-tools", "name": "Dup", "slug": "dup",
                                             "package_name": "Hawee_PDF.Parser"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PACKAGE_NAME_TAKEN"
    r = owner.post("/api/v1/projects", json={"group": "ai-tools", "name": "Generic", "slug": "scripts",
                                             "language": "GENERIC"})
    assert r.status_code == 201 and r.json()["package_name"] is None
    assert owner.post("/api/v1/projects", json={"group": "ai-tools", "name": "PDF 2", "slug": "pdf-parser",
                                                "package_name": "hawee-pdf-2"}).status_code == 409


def test_project_create_requires_group_owner(world):
    dev = login("developer")
    r = dev.post("/api/v1/projects", json={"group": "ai-tools", "name": "Z", "slug": "zz", "package_name": "hawee-zz"})
    assert r.status_code == 403


def test_private_project_hidden_from_outsider(world):
    out = login("outsider")
    assert out.get("/api/v1/projects/ai-tools/pdf-parser").status_code == 404
    assert out.get("/api/v1/projects").json()["total"] == 0
    viewer = login("viewer")
    r = viewer.get("/api/v1/projects/ai-tools/pdf-parser")
    assert r.status_code == 200 and r.json()["my_level"] == "VIEWER"


def test_internal_project_visible_to_all(admin):
    make_user("frank")
    admin.post("/api/v1/groups", json={"name": "Open", "slug": "open", "visibility": "INTERNAL"})
    admin.post("/api/v1/projects", json={"group": "open", "name": "Lib", "slug": "lib", "package_name": "hawee-lib",
                                         "visibility": "INTERNAL"})
    frank = login("frank")
    r = frank.get("/api/v1/projects", params={"q": "lib"})
    assert r.json()["total"] == 1
    assert frank.get("/api/v1/projects/open/lib").json()["my_level"] == "VIEWER"
    assert frank.patch("/api/v1/projects/open/lib", json={"description": "x"}).status_code == 403


def test_catalogue_search(world):
    owner = world["owner"]
    owner.patch("/api/v1/projects/ai-tools/pdf-parser", json={"description": "Trích xuất bảng từ PDF"})
    for q in ("pdf", "hawee-pdf", "bảng", "ai tools", "AI-TOOLS"):
        assert owner.get("/api/v1/projects", params={"q": q}).json()["total"] == 1, q
    assert owner.get("/api/v1/projects", params={"q": "khongco"}).json()["total"] == 0


def test_group_member_management(world, admin):
    owner = world["owner"]
    dev_id = str(world["users"]["developer"].id)
    r = owner.patch(f"/api/v1/groups/ai-tools/members/{dev_id}", json={"role": "MAINTAINER"})
    assert r.status_code == 200 and r.json()["role"] == "MAINTAINER"
    maint = login("maintainer")
    assert maint.patch(f"/api/v1/groups/ai-tools/members/{dev_id}", json={"role": "VIEWER"}).status_code == 403
    owner_id = str(world["users"]["owner"].id)
    r = owner.delete(f"/api/v1/groups/ai-tools/members/{owner_id}")
    assert r.status_code == 400 and r.json()["error"]["code"] == "LAST_OWNER"
    assert owner.delete(f"/api/v1/groups/ai-tools/members/{dev_id}").status_code == 204
    assert login("developer").get("/api/v1/projects/ai-tools/pdf-parser").status_code == 404


def test_project_member_override(world):
    owner = world["owner"]
    out_id = str(world["users"]["outsider"].id)
    r = owner.post("/api/v1/projects/ai-tools/pdf-parser/members", json={"user_id": out_id, "role": "DEVELOPER"})
    assert r.status_code == 201
    out = login("outsider")
    assert out.get("/api/v1/projects/ai-tools/pdf-parser").json()["my_level"] == "DEVELOPER"
    members = owner.get("/api/v1/projects/ai-tools/pdf-parser/members").json()
    assert any(m["user"]["username"] == "outsider" and m["source"] == "project" for m in members)
    dev = login("developer")
    assert dev.post("/api/v1/projects/ai-tools/pdf-parser/members",
                    json={"user_id": out_id, "role": "MAINTAINER"}).status_code == 403


def test_archive_and_delete(world, admin):
    owner = world["owner"]
    dev = login("developer")
    assert dev.post("/api/v1/projects/ai-tools/pdf-parser/archive").status_code == 403
    assert owner.delete("/api/v1/projects/ai-tools/pdf-parser").status_code == 403
    assert admin.delete("/api/v1/projects/ai-tools/pdf-parser").status_code == 400  # chưa archive
    assert owner.post("/api/v1/projects/ai-tools/pdf-parser/archive").json()["archived"] is True
    bare = _bare(world["project"])
    assert admin.delete("/api/v1/projects/ai-tools/pdf-parser").status_code == 204
    assert not bare.exists()
    assert admin.get("/api/v1/projects/ai-tools/pdf-parser").status_code == 404


# ── git authorization (SSH forced command) ──

def test_ssh_key_lookup_and_authorize_by_role(world, anon):
    keys = {role: _add_key(login(role)) for role in ("viewer", "developer", "outsider", "maintainer")}
    r = anon.get("/api/v1/internal/git/ssh-keys/lookup", params={"fingerprint": "SHA256:nope"},
                 headers=internal_headers())
    assert r.status_code == 404
    assert anon.get("/api/v1/internal/git/ssh-keys/lookup", params={"fingerprint": "SHA256:nope"}).status_code == 401

    assert _authorize(anon, keys["viewer"], "read")["allowed"] is True
    assert _authorize(anon, keys["viewer"], "write")["allowed"] is False           # viewer: push denied
    ok = _authorize(anon, keys["developer"], "write")                              # developer: push allowed
    assert ok["allowed"] and ok["repo_path"].endswith("pdf-parser.git") and ok["user_id"]
    assert _authorize(anon, keys["outsider"], "read")["allowed"] is False           # private
    assert _authorize(anon, keys["developer"], "read", "/ai-tools/pdf-parser")["allowed"] is True
    assert _authorize(anon, keys["developer"], "read", "../etc/passwd")["allowed"] is False
    assert _authorize(anon, keys["developer"], "read", "ai-tools/nope.git")["allowed"] is False


def test_authorize_push_ref_rules(world, anon):
    pid = world["project"]["id"]
    sha = "a" * 40

    def push(role: str, ref: str, old: str = ZERO, new: str = sha):
        r = anon.post("/api/v1/internal/git/authorize-push", headers=internal_headers(), json={
            "user_id": str(world["users"][role].id), "project_id": pid,
            "updates": [{"old_sha": old, "new_sha": new, "ref": ref}]})
        return r.json()["allowed"]

    assert push("developer", "refs/heads/main") is True
    assert push("developer", "refs/heads/feature/x") is True
    assert push("developer", "refs/tags/v1.0.0") is False
    assert push("maintainer", "refs/tags/v1.0.0") is True
    assert push("viewer", "refs/heads/main") is False
    assert push("maintainer", "refs/heads/main", old=sha, new=ZERO) is False  # xoá nhánh mặc định
    world["owner"].patch("/api/v1/projects/ai-tools/pdf-parser", json={"protect_default_branch": True})
    assert push("developer", "refs/heads/main") is False
    assert push("maintainer", "refs/heads/main") is True


# ── post-receive → build ──

def test_push_main_creates_validation_build_and_tag_creates_release(world, admin, db):
    p = world["project"]
    work = WorkRepo(_bare(p))
    sha = work.commit("0.1.0")
    work.push("main")
    r = send_event(admin, p["id"], "ai-tools/pdf-parser", [(ZERO, sha, "refs/heads/main")],
                   user_id=str(world["users"]["developer"].id))
    assert r.status_code == 200, r.text
    builds = r.json()["builds"]
    assert len(builds) == 1 and builds[0]["trigger"] == "PUSH"
    b = db.get(Build, builds[0]["id"])
    assert b.publish_package is False and b.status.value == "QUEUED" and b.commit_sha == sha and b.number == 1

    tag_sha = work.tag("v0.1.0")
    work.push("v0.1.0")
    r = send_event(admin, p["id"], "ai-tools/pdf-parser", [(ZERO, tag_sha, "refs/tags/v0.1.0")])
    rel = r.json()["builds"][0]
    b = db.get(Build, rel["id"])
    assert rel["trigger"] == "TAG" and b.publish_package is True and b.requested_version == "0.1.0"
    assert b.number == 2
    proj = admin.get("/api/v1/projects/ai-tools/pdf-parser").json()
    assert proj["latest_commit_sha"] == sha and proj["repository_size_bytes"] > 0
    assert proj["last_build_status"] == "QUEUED"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "GIT_PUSH")) is not None


def test_non_release_tag_and_branch_delete_ignored(world, admin):
    p = world["project"]
    r = send_event(admin, p["id"], "ai-tools/pdf-parser", [(ZERO, "b" * 40, "refs/tags/nightly"),
                                                            ("b" * 40, ZERO, "refs/heads/old")])
    assert r.json()["builds"] == []


def test_event_idempotent(world, admin):
    p = world["project"]
    work = WorkRepo(_bare(p))
    sha = work.commit()
    work.push("main")
    eid = "3f1c6f8e-8a8e-4f7c-9a55-2f5c1b3b7d11"
    first = send_event(admin, p["id"], "ai-tools/pdf-parser", [(ZERO, sha, "refs/heads/main")], event_id=eid)
    second = send_event(admin, p["id"], "ai-tools/pdf-parser", [(ZERO, sha, "refs/heads/main")], event_id=eid)
    assert len(first.json()["builds"]) == 1 and second.json()["builds"] == []


def test_event_requires_internal_secret(world, anon):
    r = anon.post("/api/v1/internal/git/events", json={"event_id": "3f1c6f8e-8a8e-4f7c-9a55-2f5c1b3b7d12",
                                                        "repository_path": "x/y", "updates": []})
    assert r.status_code == 401


def test_repository_browsing(world):
    p = world["project"]
    work = WorkRepo(_bare(p))
    work.commit("0.2.0", "feat: first")
    work.push("main")
    dev = login("developer")
    refs = dev.get("/api/v1/projects/ai-tools/pdf-parser/repository/refs").json()
    assert refs["branches"][0]["name"] == "main"
    tree = dev.get("/api/v1/projects/ai-tools/pdf-parser/repository/tree").json()
    names = [e["name"] for e in tree["entries"]]
    assert names[0] == "src" and "pyproject.toml" in names
    blob = dev.get("/api/v1/projects/ai-tools/pdf-parser/repository/blob", params={"path": "pyproject.toml"}).json()
    assert 'version = "0.2.0"' in blob["content"]
    readme = dev.get("/api/v1/projects/ai-tools/pdf-parser/readme").json()
    assert readme["filename"] == "README.md" and "Tool nội bộ" in readme["content"]
    commits = dev.get("/api/v1/projects/ai-tools/pdf-parser/repository/commits").json()
    assert commits[0]["subject"] == "feat: first"
    assert dev.get("/api/v1/projects/ai-tools/pdf-parser/repository/blob",
                   params={"path": "../../etc/passwd"}).status_code in (400, 404)


def test_manual_build(world):
    p = world["project"]
    work = WorkRepo(_bare(p))
    work.commit()
    work.push("main")
    viewer = login("viewer")
    assert viewer.post("/api/v1/projects/ai-tools/pdf-parser/builds/manual", json={}).status_code == 403
    dev = login("developer")
    r = dev.post("/api/v1/projects/ai-tools/pdf-parser/builds/manual", json={})
    assert r.status_code == 201 and r.json()["trigger_type"] == "MANUAL" and r.json()["publish_package"] is False
    assert dev.post("/api/v1/projects/ai-tools/pdf-parser/builds/manual", json={"ref": "nope"}).status_code == 400
