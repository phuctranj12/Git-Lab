from __future__ import annotations

import io
import os
import subprocess
import tarfile
import tempfile
import uuid
import zipfile
from pathlib import Path

from tests.conftest import internal_headers, runner_headers

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev@hawee.local",
           "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": "dev@hawee.local", "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=GIT_ENV)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def pyproject(name: str, version: str) -> str:
    return f"""[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "{version}"
requires-python = ">=3.10"
readme = "README.md"
"""


class WorkRepo:
    """Working copy trỏ tới bare repo của project (push qua đường dẫn file — không đi qua SSH)."""

    def __init__(self, bare: Path, package: str = "hawee-pdf-parser") -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="work-"))
        self.package = package
        git(self.dir, "init", "-q", "-b", "main")
        git(self.dir, "remote", "add", "origin", str(bare))

    def commit(self, version: str = "0.1.0", message: str = "update", name: str | None = None) -> str:
        mod = (name or self.package).replace("-", "_")
        (self.dir / "pyproject.toml").write_text(pyproject(name or self.package, version), encoding="utf-8")
        (self.dir / "README.md").write_text(f"# {self.package}\n\nTool nội bộ.\n", encoding="utf-8")
        pkg = self.dir / "src" / mod
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text(f"__version__ = '{version}'\n\ndef add(a, b):\n    return a + b\n")
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-q", "-m", message, "--allow-empty")
        return git(self.dir, "rev-parse", "HEAD")

    def push(self, *refs: str) -> None:
        git(self.dir, "push", "-q", "origin", *refs)

    def tag(self, tag: str) -> str:
        git(self.dir, "tag", tag)
        return git(self.dir, "rev-parse", f"{tag}^{{commit}}")


def send_event(client, project_id: str, repo: str, updates: list[tuple[str, str, str]], user_id: str | None = None,
               event_id: str | None = None):
    return client.post("/api/v1/internal/git/events", headers=internal_headers(), json={
        "event_id": event_id or str(uuid.uuid4()), "project_id": project_id, "repository_path": repo,
        "user_id": user_id,
        "updates": [{"old_sha": o, "new_sha": n, "ref": r} for o, n, r in updates],
    })


ZERO = "0" * 40


# ── package files giả (đúng cấu trúc wheel / sdist) ──

def make_wheel(name: str, version: str, requires_python: str = ">=3.10", meta_name: str | None = None,
               meta_version: str | None = None) -> tuple[str, bytes]:
    dist = name.replace("-", "_")
    filename = f"{dist}-{version}-py3-none-any.whl"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{dist}/__init__.py", "def add(a, b):\n    return a + b\n")
        zf.writestr(f"{dist}-{version}.dist-info/METADATA",
                    f"Metadata-Version: 2.1\nName: {meta_name or name}\nVersion: {meta_version or version}\n"
                    f"Summary: Demo tool\nRequires-Python: {requires_python}\n\nLong description\n")
        zf.writestr(f"{dist}-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        zf.writestr(f"{dist}-{version}.dist-info/RECORD", "")
    return filename, buf.getvalue()


def make_sdist(name: str, version: str) -> tuple[str, bytes]:
    dist = name.replace("-", "_")
    filename = f"{dist}-{version}.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode()
        info = tarfile.TarInfo(f"{dist}-{version}/PKG-INFO")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return filename, buf.getvalue()


def start_build(client, build_id: str, runner: str = "runner-test"):
    return client.post(f"/api/v1/internal/builds/{build_id}/start", headers=runner_headers(),
                       json={"runner_name": runner})


def publish(client, start: dict, files: list[tuple[str, bytes]], version: str | None = None,
            package_name: str | None = None):
    return client.post("/api/v1/internal/packages/publish", headers=runner_headers(), data={
        "project_id": start["project_id"], "build_id": start["build_id"],
        "package_name": package_name or start["package_name"], "version": version or start["requested_version"],
        "commit_sha": start["commit_sha"], "git_tag": start["ref_name"],
    }, files=[("files", (fn, content, "application/octet-stream")) for fn, content in files])


def finish(client, build_id: str, status: str = "SUCCESS", error_code: str | None = None, log: str = "ok\n"):
    return client.post(f"/api/v1/internal/builds/{build_id}/finish", headers=runner_headers(),
                       json={"status": status, "error_code": error_code, "log": log,
                             "steps": [{"name": "build", "status": status}]})
