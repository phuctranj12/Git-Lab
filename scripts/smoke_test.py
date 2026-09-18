#!/usr/bin/env python3
"""HAWEE Tool Hub — smoke test / end-to-end trên stack ĐANG CHẠY (spec mục 38.3–38.6, 52, 53).

Luồng thật, không mock:
  health → login admin → tạo group/project/user tạm → SSH key → git clone/push qua SSH
  → validation build SUCCESS → git tag vX → release build SUCCESS → publish
  → venv sạch: pip install package nội bộ + import → pip install requests qua CÙNG index (proxy/cache)
  → RBAC (viewer push bị từ chối) → dependency confusion (hawee-* không có → 404) → audit → archive.
  --full thêm: tag lệch version → VERSION_MISMATCH, không overwrite version.

Chỉ dùng stdlib + git + ssh + ssh-keygen trên máy chạy. Ví dụ:
  python3 scripts/smoke_test.py --env-file deploy/.env
  python3 scripts/smoke_test.py --web http://10.0.0.5:8080 --packages http://10.0.0.5:8081 --admin admin --password ...
"""
from __future__ import annotations

import argparse
import base64
import http.cookiejar
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RESULTS: list[tuple[str, bool, str]] = []


def env_file_values(path: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if path and os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                v = v.split(" #")[0].strip().strip('"')
                out[k.strip()] = v
    return out


class Http:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def csrf(self) -> str | None:
        return next((c.value for c in self.jar if c.name == "toolhub_csrf"), None)

    def call(self, method: str, path: str, body: dict | None = None, headers: dict | None = None,
             raw: bool = False, expect: tuple[int, ...] = (200, 201, 204)):
        url = path if path.startswith("http") else self.base + path
        data = json.dumps(body).encode() if body is not None else None
        h = {"Accept": "application/json", **(headers or {})}
        if data is not None:
            h["Content-Type"] = "application/json"
        if method not in ("GET", "HEAD") and self.csrf():
            h["X-CSRF-Token"] = self.csrf()
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with self.opener.open(req, timeout=60) as resp:
                status, payload, rh = resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            status, payload, rh = exc.code, exc.read(), dict(exc.headers)
        if status not in expect:
            raise RuntimeError(f"{method} {path} → HTTP {status}: {payload[:400]!r}")
        if raw:
            return status, payload, rh
        return json.loads(payload) if payload and payload[:1] in (b"{", b"[") else payload


def step(name: str):
    def deco(fn):
        def wrapper(*a, **kw):
            t = time.monotonic()
            print(f"\n▶ {name} …", flush=True)
            try:
                result = fn(*a, **kw)
            except Exception as exc:  # noqa: BLE001
                RESULTS.append((name, False, str(exc)[:300]))
                print(f"  ✗ {name}: {exc}", flush=True)
                raise
            RESULTS.append((name, True, f"{time.monotonic() - t:.1f}s"))
            print(f"  ✓ {name} ({time.monotonic() - t:.1f}s)", flush=True)
            return result
        return wrapper
    return deco


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}… exit {proc.returncode}\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")
    return proc


def venv_python(venv: Path) -> str:
    win = venv / "Scripts" / "python.exe"
    return str(win if win.exists() else venv / "bin" / "python")


class Smoke:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.web = Http(args.web)
        self.tag = secrets.token_hex(3)
        self.group = f"smoke-{self.tag}"
        self.slug = f"demo-tool-{self.tag}"
        self.package = f"hawee-demo-tool-{self.tag}"
        self.module = self.package.replace("-", "_")
        self.tmp = Path(tempfile.mkdtemp(prefix="toolhub-smoke-"))
        self.users: dict[str, dict] = {}
        self.clients: dict[str, Http] = {}
        self.keys: dict[str, Path] = {}
        self.project: dict = {}

    # ── helpers ──
    def login(self, client: Http, username: str, password: str) -> None:
        client.call("POST", "/api/v1/auth/login", {"login": username, "password": password})

    def git_env(self, who: str) -> dict:
        key = self.keys[who].as_posix()
        known = (self.tmp / "known_hosts").as_posix()
        return {"GIT_SSH_COMMAND": f'ssh -i "{key}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no '
                                   f'-o UserKnownHostsFile="{known}" -o BatchMode=yes',
                "GIT_AUTHOR_NAME": who, "GIT_AUTHOR_EMAIL": f"{who}@hawee.local",
                "GIT_COMMITTER_NAME": who, "GIT_COMMITTER_EMAIL": f"{who}@hawee.local", "GIT_TERMINAL_PROMPT": "0"}

    def wait_build(self, ref: str, min_number: int = 0, timeout: int = 900) -> dict:
        deadline = time.time() + timeout
        path = self.project["full_path"]
        while time.time() < deadline:
            page = self.web.call("GET", f"/api/v1/projects/{path}/builds?page_size=50")
            for b in page["items"]:
                if b["ref_name"] == ref and b["number"] > min_number and b["status"] in ("SUCCESS", "FAILED", "CANCELLED"):
                    return b
            time.sleep(3)
        raise RuntimeError(f"Build cho {ref} không xong sau {timeout}s")

    def build_log(self, build: dict) -> str:
        return self.web.call("GET", f"/api/v1/builds/{build['id']}/log")["content"]

    # ── steps ──
    @step("API health (PostgreSQL/Redis/MinIO/Git)")
    def health(self) -> None:
        ready = self.web.call("GET", "/health/ready")
        bad = [c["name"] for c in ready["checks"] if not c["ok"]]
        if bad:
            raise RuntimeError("not ready: " + ", ".join(bad))

    @step("Login admin")
    def login_admin(self) -> None:
        self.login(self.web, self.args.admin, self.args.password)
        me = self.web.call("GET", "/api/v1/auth/me")
        if not me["is_system_admin"]:
            raise RuntimeError("tài khoản không phải System Admin")

    @step("Tạo user/group/project tạm + SSH key")
    def setup(self) -> None:
        pw = "Smoke-" + secrets.token_urlsafe(12)
        for who, role in (("dev", "MAINTAINER"), ("viewer", "VIEWER")):
            username = f"smoke-{who}-{self.tag}"
            u = self.web.call("POST", "/api/v1/users", {"username": username, "email": f"{username}@hawee.local",
                                                          "password": pw, "full_name": f"Smoke {who}"})
            self.users[who] = {**u, "role": role}
            c = Http(self.args.web)
            self.login(c, username, pw)
            self.clients[who] = c
            key = self.tmp / f"id_{who}"
            run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key), "-C", username])
            self.keys[who] = key
            c.call("POST", "/api/v1/me/ssh-keys", {"title": "smoke", "public_key": Path(str(key) + ".pub").read_text()})
        self.web.call("POST", "/api/v1/groups", {"name": f"Smoke {self.tag}", "slug": self.group, "visibility": "PRIVATE"})
        for who in ("dev", "viewer"):
            self.web.call("POST", f"/api/v1/groups/{self.group}/members",
                          {"user_id": self.users[who]["id"], "role": self.users[who]["role"]})
        self.project = self.web.call("POST", "/api/v1/projects", {
            "group": self.group, "name": f"Demo tool {self.tag}", "slug": self.slug, "package_name": self.package,
            "visibility": "PRIVATE", "description": "Smoke test fixture"})
        print(f"    clone URL: {self.project['clone_url']}")

    @step("git clone + push main qua SSH")
    def push_main(self) -> None:
        work = self.tmp / "work"
        run(["git", "clone", "-q", self.project["clone_url"], str(work)], env=self.git_env("dev"))
        run(["git", "checkout", "-q", "-b", "main"], cwd=work, check=False)
        (work / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["setuptools>=68", "wheel"]\nbuild-backend = "setuptools.build_meta"\n\n'
            f'[project]\nname = "{self.package}"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n'
            'readme = "README.md"\ndescription = "Smoke test demo tool"\n', encoding="utf-8")
        (work / "README.md").write_text(f"# {self.package}\n\nDemo tool cho smoke test.\n", encoding="utf-8")
        src = work / "src" / self.module
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (work / "tests").mkdir()
        (work / "tests" / "test_add.py").write_text(
            f"from {self.module} import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
        run(["git", "add", "-A"], cwd=work)
        run(["git", "commit", "-q", "-m", "feat: demo tool"], cwd=work, env=self.git_env("dev"))
        out = run(["git", "push", "origin", "main"], cwd=work, env=self.git_env("dev"))
        print("    " + (out.stderr.strip().splitlines() or [""])[-1])
        self.work = work

    @step("Validation build (push main) SUCCESS")
    def validation_build(self) -> None:
        b = self.wait_build("main")
        if b["status"] != "SUCCESS" or b["publish_package"]:
            raise RuntimeError(f"build #{b['number']} {b['status']} {b['error_code']}: {b['error_message']}\n"
                               + self.build_log(b)[-3000:])
        self.main_build = b

    @step("Release build (tag v0.1.0) SUCCESS + publish")
    def release(self) -> None:
        run(["git", "tag", "v0.1.0"], cwd=self.work)
        run(["git", "push", "origin", "v0.1.0"], cwd=self.work, env=self.git_env("dev"))
        b = self.wait_build("v0.1.0")
        if b["status"] != "SUCCESS":
            raise RuntimeError(f"release #{b['number']} {b['status']} {b['error_code']}: {b['error_message']}\n"
                               + self.build_log(b)[-3000:])
        versions = self.web.call("GET", f"/api/v1/projects/{self.project['full_path']}/packages")
        v = next((x for x in versions if x["version"] == "0.1.0"), None)
        if not v or not any(f["file_type"] == "WHEEL" for f in v["files"]):
            raise RuntimeError("không thấy version 0.1.0 với file .whl")
        self.version = v

    @step("pip install package nội bộ trong venv sạch + import")
    def pip_install_internal(self) -> None:
        dev = self.clients["dev"]
        self.pat = dev.call("POST", "/api/v1/me/tokens", {"name": "smoke-pip", "scopes": ["read_package"],
                                                          "expires_in_days": 1})["token"]
        venv = self.tmp / "venv"
        run([sys.executable, "-m", "venv", str(venv)])
        self.py = venv_python(venv)
        index = self.args.packages.rstrip("/").replace("://", f"://__token__:{self.pat}@", 1) + "/simple"
        host = urllib.parse.urlsplit(self.args.packages).hostname
        self.pip = [self.py, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                    "--index-url", index, "--trusted-host", host]
        run([*self.pip, f"{self.package}==0.1.0"])
        out = run([self.py, "-c", f"from {self.module} import add; assert add(1, 2) == 3; print('import OK')"])
        print("    " + out.stdout.strip())

    @step("pip install requests qua CÙNG index (proxy PyPI)")
    def pip_install_public(self) -> None:
        run([*self.pip, "requests"])
        self.requests_version = run([self.py, "-c", "import requests; print(requests.__version__)"]).stdout.strip()
        print(f"    requests {self.requests_version}")

    @step("Cache upstream: lần tải sau lấy từ MinIO")
    def cache_hit(self) -> None:
        auth = {"Authorization": "Basic " + base64.b64encode(f"__token__:{self.pat}".encode()).decode()}
        pkg = Http(self.args.packages)
        wheel = f"requests-{self.requests_version}-py3-none-any.whl"
        html = pkg.call("GET", "/simple/requests/", headers=auth, raw=True)[1].decode()
        if f"/packages/upstream/requests/{wheel}" not in html:
            raise RuntimeError(f"index không có link rewrite cho {wheel}")
        _, body, headers = pkg.call("GET", f"/packages/upstream/requests/{wheel}", headers=auth, raw=True)
        cache = headers.get("X-Cache") or headers.get("x-cache")
        if cache != "CACHE":
            raise RuntimeError(f"{wheel} không phục vụ từ cache (X-Cache={cache})")
        print(f"    {wheel}: X-Cache=CACHE ({len(body) // 1024} KiB)")

    @step("Dependency confusion: hawee-* không tồn tại → 404, không ra PyPI")
    def dependency_confusion(self) -> None:
        auth = {"Authorization": "Basic " + base64.b64encode(f"__token__:{self.pat}".encode()).decode()}
        status, body, _ = Http(self.args.packages).call("GET", "/simple/hawee-package-khong-ton-tai/", headers=auth,
                                                         raw=True, expect=(404,))
        if b"PACKAGE_NOT_FOUND" not in body:
            raise RuntimeError(f"unexpected body {body[:200]!r}")

    @step("RBAC: viewer clone được, push bị từ chối")
    def rbac(self) -> None:
        vwork = self.tmp / "viewer"
        run(["git", "clone", "-q", self.project["clone_url"], str(vwork)], env=self.git_env("viewer"))
        (vwork / "hack.txt").write_text("x")
        run(["git", "add", "-A"], cwd=vwork)
        run(["git", "commit", "-q", "-m", "hack"], cwd=vwork, env=self.git_env("viewer"))
        proc = run(["git", "push", "origin", "main"], cwd=vwork, env=self.git_env("viewer"), check=False)
        if proc.returncode == 0:
            raise RuntimeError("viewer push ĐƯỢC CHẤP NHẬN — sai phân quyền!")
        print("    " + (proc.stderr.strip().splitlines() or ["denied"])[0][:120])
        viewer = self.clients["viewer"]
        viewer.call("POST", f"/api/v1/packages/{self.package}/0.1.0/yank", {"reason": "x"}, expect=(403,))

    @step("Version mismatch: tag v0.2.0 nhưng pyproject 0.1.0 → FAILED")
    def version_mismatch(self) -> None:
        run(["git", "tag", "v0.2.0"], cwd=self.work)
        run(["git", "push", "origin", "v0.2.0"], cwd=self.work, env=self.git_env("dev"))
        b = self.wait_build("v0.2.0")
        if b["status"] != "FAILED" or b["error_code"] != "VERSION_MISMATCH":
            raise RuntimeError(f"mong FAILED/VERSION_MISMATCH, nhận {b['status']}/{b['error_code']}")
        versions = self.web.call("GET", f"/api/v1/projects/{self.project['full_path']}/packages")
        if any(v["version"] == "0.2.0" for v in versions):
            raise RuntimeError("version 0.2.0 bị publish dù mismatch!")

    @step("Không overwrite: build lại tag v0.1.0 → PACKAGE_VERSION_EXISTS")
    def no_overwrite(self) -> None:
        last = self.wait_build("v0.1.0")
        self.clients["dev"].call("POST", f"/api/v1/projects/{self.project['full_path']}/builds/manual", {"ref": "v0.1.0"})
        b = self.wait_build("v0.1.0", min_number=last["number"])
        if b["error_code"] != "PACKAGE_VERSION_EXISTS":
            raise RuntimeError(f"mong PACKAGE_VERSION_EXISTS, nhận {b['status']}/{b['error_code']}")

    @step("Audit log có GIT_PUSH / BUILD_SUCCESS / PACKAGE_PUBLISHED")
    def audit(self) -> None:
        for action in ("GIT_PUSH", "BUILD_SUCCESS", "PACKAGE_PUBLISHED", "GIT_PUSH_DENIED"):
            page = self.web.call("GET", f"/api/v1/admin/audit-logs?action={action}&page_size=5")
            if page["total"] < 1:
                raise RuntimeError(f"thiếu audit {action}")

    @step("Dọn dẹp: archive project, vô hiệu user tạm")
    def cleanup(self) -> None:
        if self.project:
            self.web.call("POST", f"/api/v1/projects/{self.project['full_path']}/archive")
        for u in self.users.values():
            self.web.call("POST", f"/api/v1/users/{u['id']}/disable")

    def run_all(self) -> bool:
        ok = True
        steps = [self.health, self.login_admin, self.setup, self.push_main, self.validation_build, self.release,
                 self.pip_install_internal, self.pip_install_public, self.cache_hit, self.dependency_confusion,
                 self.rbac]
        if self.args.full:
            steps += [self.version_mismatch, self.no_overwrite]
        steps.append(self.audit)
        try:
            for s in steps:
                s()
        except Exception:  # noqa: BLE001
            ok = False
        finally:
            try:
                if self.web.csrf():
                    self.cleanup()
            except Exception:  # noqa: BLE001
                ok = False
            shutil.rmtree(self.tmp, ignore_errors=True)
        return ok


def summary(ok: bool) -> None:
    names = {r[0]: r[1] for r in RESULTS}

    def status(*keys: str) -> str:
        vals = [names.get(k) for k in keys]
        return "PASS" if vals and all(v is True for v in vals) else "FAIL"

    rows = [
        ("API", status("API health (PostgreSQL/Redis/MinIO/Git)", "Login admin")),
        ("PostgreSQL", status("API health (PostgreSQL/Redis/MinIO/Git)")),
        ("Redis", status("API health (PostgreSQL/Redis/MinIO/Git)")),
        ("MinIO", status("API health (PostgreSQL/Redis/MinIO/Git)")),
        ("Git push", status("git clone + push main qua SSH")),
        ("Build worker", status("Validation build (push main) SUCCESS", "Release build (tag v0.1.0) SUCCESS + publish")),
        ("Internal package", status("Release build (tag v0.1.0) SUCCESS + publish")),
        ("pip install", status("pip install package nội bộ trong venv sạch + import")),
        ("PyPI proxy", status("pip install requests qua CÙNG index (proxy PyPI)")),
        ("Cache", status("Cache upstream: lần tải sau lấy từ MinIO")),
        ("RBAC", status("RBAC: viewer clone được, push bị từ chối",
                        "Dependency confusion: hawee-* không tồn tại → 404, không ra PyPI")),
        ("Audit", status("Audit log có GIT_PUSH / BUILD_SUCCESS / PACKAGE_PUBLISHED")),
    ]
    line = "=" * 48
    print(f"\n{line}\nHAWEE TOOL HUB SMOKE TEST\n{line}")
    for name, st in rows:
        print(f"{name:<19}{st}")
    print(line)
    all_ok = ok and all(st == "PASS" for _, st in rows)
    print("READY FOR DEPLOYMENT" if all_ok else "NOT READY — xem lỗi ở trên")
    print(line)
    for name, passed, info in RESULTS:
        if not passed:
            print(f"✗ {name}: {info}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env-file", default=None)
    p.add_argument("--web", default=None, help="vd http://127.0.0.1:8080")
    p.add_argument("--packages", default=None, help="vd http://127.0.0.1:8081")
    p.add_argument("--admin", default=None)
    p.add_argument("--password", default=None)
    p.add_argument("--full", action="store_true", help="e2e đầy đủ (version mismatch, không overwrite)")
    args = p.parse_args()
    env = env_file_values(args.env_file)
    args.web = args.web or os.environ.get("SMOKE_WEB_URL") or f"http://127.0.0.1:{env.get('WEB_PORT', '8080')}"
    args.packages = (args.packages or os.environ.get("SMOKE_PACKAGES_URL")
                     or f"http://127.0.0.1:{env.get('PACKAGES_PORT', '8081')}")
    args.admin = args.admin or os.environ.get("SMOKE_ADMIN_USER") or env.get("FIRST_ADMIN_USERNAME", "admin")
    args.password = args.password or os.environ.get("SMOKE_ADMIN_PASSWORD") or env.get("FIRST_ADMIN_PASSWORD")
    if not args.password:
        print("Cần mật khẩu admin (--password hoặc SMOKE_ADMIN_PASSWORD)")
        return 2
    for tool in ("git", "ssh", "ssh-keygen"):
        if shutil.which(tool) is None:
            print(f"Thiếu công cụ: {tool}")
            return 2
    ok = Smoke(args).run_all()
    summary(ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
