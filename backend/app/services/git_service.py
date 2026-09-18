"""Làm việc với bare repository bằng git binary chuẩn (spec 1.2: không tự viết lại Git)."""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import AppError, BadRequest, NotFound

ZERO_SHA = "0" * 40
_SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_REF_RE = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/@+-]{1,255}$")
_PATH_RE = re.compile(r"^(?!/)(?!.*(^|/)\.\.(/|$))[^\x00]{0,1024}$")
MAX_BLOB_BYTES = 1024 * 1024


class GitError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("GIT_ERROR", message, 500)


def repo_root() -> Path:
    return Path(get_settings().git_repository_root)


def relative_repo_path(group_slug: str, project_slug: str) -> str:
    return f"{group_slug}/{project_slug}.git"


def absolute_repo_path(relative: str) -> Path:
    root = repo_root().resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise BadRequest("INVALID_REPOSITORY", "Đường dẫn repository không hợp lệ")
    return path


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C.UTF-8"})
    return env


def run_git(repo: Path | None, *args: str, check: bool = True, timeout: int = 60, input_bytes: bytes | None = None
            ) -> subprocess.CompletedProcess[bytes]:
    cmd = [get_settings().git_binary, "-c", "safe.directory=*"]
    if repo is not None:
        cmd += ["--git-dir", str(repo)]
    cmd += list(args)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=_git_env(), input=input_bytes,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"Không chạy được git: {exc}") from exc
    if check and proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip()[:500] or "git lỗi")
    return proc


def validate_ref(ref: str) -> str:
    if not _REF_RE.match(ref):
        raise BadRequest("INVALID_REF", "Tên ref không hợp lệ")
    return ref


def validate_path(path: str) -> str:
    path = path.strip().strip("/")
    if not _PATH_RE.match(path):
        raise BadRequest("INVALID_PATH", "Đường dẫn không hợp lệ")
    return path


def is_sha(value: str) -> bool:
    return bool(_SHA_RE.match(value))


# ───────────── lifecycle ─────────────

def create_bare_repository(relative: str, project_id: uuid.UUID, default_branch: str) -> Path:
    path = absolute_repo_path(relative)
    if path.exists():
        raise AppError("REPOSITORY_EXISTS", "Repository đã tồn tại trên đĩa", 409)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_git(None, "init", "--bare", "--quiet", f"--initial-branch={default_branch}", str(path))
    s = get_settings()
    for key, value in (
        ("toolhub.projectid", str(project_id)),
        ("core.hooksPath", s.git_hook_dir),       # hook trung tâm của git-service (post-receive/pre-receive)
        ("receive.fsckObjects", "true"),
        ("receive.denyDeleteCurrent", "true"),
        ("transfer.hideRefs", "refs/toolhub"),
        ("uploadpack.allowFilter", "true"),
    ):
        run_git(path, "config", key, value)
    (path / "description").write_text(f"Tool Hub project {project_id}\n", encoding="utf-8")
    return path


def set_default_branch(relative: str, branch: str) -> None:
    run_git(absolute_repo_path(relative), "symbolic-ref", "HEAD", f"refs/heads/{validate_ref(branch)}")


def move_to_trash(relative: str) -> Path | None:
    path = absolute_repo_path(relative)
    if not path.exists():
        return None
    trash = repo_root() / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest = trash / f"{relative.replace('/', '__')}.{stamp}"
    shutil.move(str(path), str(dest))
    return dest


def remove_repository(relative: str) -> None:
    """Chỉ dùng để dọn khi tạo project lỗi giữa chừng."""
    path = absolute_repo_path(relative)
    if path.exists():
        def _onexc(func, p, _exc):  # object git là read-only (Windows không xoá được nếu không chmod)
            os.chmod(p, stat.S_IWRITE)
            func(p)

        shutil.rmtree(path, onexc=_onexc)


def repository_size(relative: str) -> int:
    path = absolute_repo_path(relative)
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def check_storage_writable() -> None:
    root = repo_root()
    root.mkdir(parents=True, exist_ok=True)
    probe = root / f".probe-{uuid.uuid4().hex}"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()


# ───────────── read ─────────────

def _repo(relative: str) -> Path:
    path = absolute_repo_path(relative)
    if not path.exists():
        raise NotFound("REPOSITORY_NOT_FOUND", "Repository không tồn tại")
    return path


def is_empty(relative: str) -> bool:
    proc = run_git(_repo(relative), "for-each-ref", "--count=1", "--format=%(refname)", "refs/heads", "refs/tags")
    return not proc.stdout.strip()


def resolve_commit(relative: str, ref: str) -> str | None:
    ref = validate_ref(ref)
    proc = run_git(_repo(relative), "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}",
                   check=False)
    sha = proc.stdout.decode().strip()
    return sha if proc.returncode == 0 and is_sha(sha) else None


@dataclass
class RefInfo:
    name: str
    kind: str  # branch | tag
    commit_sha: str
    committed_at: str | None
    subject: str | None


def list_refs(relative: str) -> list[RefInfo]:
    fmt = "%(refname)%00%(objectname)%00%(*objectname)%00%(creatordate:iso-strict)%00%(contents:subject)"
    proc = run_git(_repo(relative), "for-each-ref", f"--format={fmt}", "--sort=-creatordate", "refs/heads", "refs/tags")
    out: list[RefInfo] = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split("\x00")
        if len(parts) < 5:
            continue
        refname, obj, peeled, date, subject = parts[:5]
        if refname.startswith("refs/heads/"):
            out.append(RefInfo(refname[11:], "branch", obj, date or None, subject or None))
        elif refname.startswith("refs/tags/"):
            out.append(RefInfo(refname[10:], "tag", peeled or obj, date or None, subject or None))
    return out


@dataclass
class CommitInfo:
    sha: str
    short_sha: str
    author_name: str
    author_email: str
    authored_at: str
    subject: str


def log(relative: str, ref: str, limit: int = 30, path: str | None = None) -> list[CommitInfo]:
    repo = _repo(relative)
    sha = resolve_commit(relative, ref)
    if sha is None:
        return []
    args = ["log", f"--max-count={max(1, min(limit, 200))}", "--format=%H%x00%h%x00%an%x00%ae%x00%aI%x00%s", sha]
    if path:
        args += ["--", validate_path(path)]
    proc = run_git(repo, *args)
    out = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        p = line.split("\x00")
        if len(p) == 6:
            out.append(CommitInfo(*p))
    return out


@dataclass
class TreeEntry:
    name: str
    path: str
    type: str  # blob | tree | commit
    size: int | None


def ls_tree(relative: str, ref: str, path: str = "") -> list[TreeEntry]:
    repo = _repo(relative)
    sha = resolve_commit(relative, ref)
    if sha is None:
        raise NotFound("REF_NOT_FOUND", "Không tìm thấy ref")
    path = validate_path(path)
    target = f"{sha}:{path}" if path else f"{sha}^{{tree}}"
    proc = run_git(repo, "ls-tree", "-l", "-z", target, check=False)
    if proc.returncode != 0:
        raise NotFound("PATH_NOT_FOUND", "Không tìm thấy đường dẫn")
    entries: list[TreeEntry] = []
    for rec in proc.stdout.decode("utf-8", "replace").split("\x00"):
        if not rec:
            continue
        meta, _, name = rec.partition("\t")
        fields = meta.split()
        if len(fields) < 4:
            continue
        size = int(fields[3]) if fields[3].isdigit() else None
        entries.append(TreeEntry(name=name, path=f"{path}/{name}" if path else name, type=fields[1], size=size))
    entries.sort(key=lambda e: (e.type != "tree", e.name.lower()))
    return entries


def read_blob(relative: str, ref: str, path: str, max_bytes: int = MAX_BLOB_BYTES) -> tuple[bytes, bool]:
    """Trả (nội dung, bị_cắt). Blob quá lớn chỉ trả max_bytes đầu."""
    repo = _repo(relative)
    sha = resolve_commit(relative, ref)
    if sha is None:
        raise NotFound("REF_NOT_FOUND", "Không tìm thấy ref")
    path = validate_path(path)
    spec = f"{sha}:{path}"
    size_proc = run_git(repo, "cat-file", "-s", spec, check=False)
    if size_proc.returncode != 0:
        raise NotFound("PATH_NOT_FOUND", "Không tìm thấy file")
    obj_type = run_git(repo, "cat-file", "-t", spec).stdout.decode().strip()
    if obj_type != "blob":
        raise BadRequest("NOT_A_FILE", "Đường dẫn không phải file")
    size = int(size_proc.stdout.decode().strip() or 0)
    data = run_git(repo, "cat-file", "blob", spec).stdout
    return data[:max_bytes], size > max_bytes


README_CANDIDATES = ("README.md", "README.rst", "README.txt", "README", "readme.md")


def read_readme(relative: str, ref: str) -> tuple[str, str] | None:
    try:
        entries = {e.name: e for e in ls_tree(relative, ref, "") if e.type == "blob"}
    except NotFound:
        return None
    for name in README_CANDIDATES:
        if name in entries:
            data, _ = read_blob(relative, ref, name, 512 * 1024)
            return name, data.decode("utf-8", "replace")
    return None


def stream_archive(relative: str, commit_sha: str) -> Iterator[bytes]:
    """`git archive` đúng commit → tar.gz stream (worker tải về để build, không cần quyền SSH)."""
    if not is_sha(commit_sha):
        raise BadRequest("INVALID_REF", "commit_sha không hợp lệ")
    repo = _repo(relative)
    cmd = [get_settings().git_binary, "-c", "safe.directory=*", "--git-dir", str(repo), "archive", "--format=tar.gz",
           commit_sha]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_git_env())
    assert proc.stdout is not None
    try:
        while True:
            chunk = proc.stdout.read(256 * 1024)
            if not chunk:
                break
            yield chunk
        rc = proc.wait(timeout=120)
        if rc != 0:
            err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            raise GitError(f"git archive lỗi: {err[:300]}")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()
