"""Điều phối một build Python trong container tạm (spec 12–15).

Worker (tin cậy) làm: tải source đúng commit → kiểm tra pyproject (tên/prefix/version) → tạo container
giới hạn CPU/RAM/PID, không privileged, drop mọi capability, mạng chỉ tới registry → stream log →
timeout/cancel → lấy dist/ → publish (release) hoặc lưu artifact (validation) → báo kết quả.
"""
from __future__ import annotations

import io
import json
import logging
import re
import tarfile
import threading
import time
import tomllib
from dataclasses import dataclass, field

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from packaging.version import InvalidVersion, Version

from app.api_client import ApiClient, ApiError
from app.config import get_settings

log = logging.getLogger(__name__)

LABEL_BUILD = "toolhub.build"
LABEL_RUNNER = "toolhub.runner"
PUBLISH_ERROR_CODES = {"PACKAGE_VERSION_EXISTS", "VERSION_MISMATCH", "INVALID_PACKAGE_NAME",
                       "INTERNAL_PREFIX_REQUIRED", "INVALID_PACKAGE_FILE"}


class BuildError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def normalize_version(v: str) -> str:
    return str(Version(v))


# ───────────── log ─────────────

class BuildLog:
    """Gom log, che token, đẩy lên API theo lô (UI xem log trực tiếp)."""

    def __init__(self, api: ApiClient, build_id: str, secrets: list[str]) -> None:
        self.api = api
        self.build_id = build_id
        self.secrets = [s for s in secrets if s]
        self._parts: list[str] = []
        self._pending: list[str] = []
        self._size = 0
        self._lock = threading.Lock()
        self._truncated = False

    def _scrub(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "***")
        return text

    def write(self, text: str) -> None:
        text = self._scrub(text)
        with self._lock:
            if self._truncated:
                return
            limit = get_settings().max_log_bytes
            if self._size + len(text) > limit:
                text = text[: max(0, limit - self._size)] + "\n[... log bị cắt vì vượt giới hạn ...]\n"
                self._truncated = True
            self._size += len(text)
            self._parts.append(text)
            self._pending.append(text)

    def line(self, text: str) -> None:
        self.write(f"\033[1;35m[runner]\033[0m {text}\n")

    def flush(self) -> None:
        with self._lock:
            chunk = "".join(self._pending)
            self._pending.clear()
        if chunk:
            self.api.append_log(self.build_id, chunk)

    @property
    def text(self) -> str:
        with self._lock:
            return "".join(self._parts)


# ───────────── kiểm tra source (chạy ở worker, trước khi vào container) ─────────────

def _read_member(tf: tarfile.TarFile, name: str, limit: int = 1024 * 1024) -> bytes | None:
    try:
        member = tf.getmember(name)
    except KeyError:
        return None
    if not member.isfile() or member.size > limit:
        return None
    f = tf.extractfile(member)
    return f.read() if f else None


@dataclass
class SourceInfo:
    name: str
    version: str
    requires_python: str | None = None
    notes: list[str] = field(default_factory=list)


def validate_source(archive: bytes, info: dict) -> SourceInfo:
    try:
        tf = tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz")
    except tarfile.TarError as exc:
        raise BuildError("GIT_CLONE_FAILED", f"Source archive hỏng: {exc}") from exc
    with tf:
        raw = _read_member(tf, "pyproject.toml")
        if raw is None:
            raise BuildError("INVALID_PYPROJECT", "Thiếu pyproject.toml ở thư mục gốc repository")
        readme = next((n for n in ("README.md", "README.rst", "README.txt", "README") if _read_member(tf, n) is not None),
                      None)
    if readme is None:
        raise BuildError("INVALID_PYPROJECT", "Thiếu README.md ở thư mục gốc (build contract spec mục 13)")
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise BuildError("INVALID_PYPROJECT", f"pyproject.toml không hợp lệ: {exc}") from exc
    project = data.get("project")
    if not isinstance(project, dict):
        raise BuildError("INVALID_PYPROJECT", "pyproject.toml thiếu bảng [project]")
    if "build-system" not in data:
        raise BuildError("INVALID_PYPROJECT", "pyproject.toml thiếu bảng [build-system]")
    name, version = project.get("name"), project.get("version")
    if "version" in (project.get("dynamic") or []) or not version:
        raise BuildError("INVALID_PYPROJECT", "version phải khai báo tĩnh trong [project] (không dùng dynamic)")
    if not isinstance(name, str) or not re.match(r"^([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9])$", name):
        raise BuildError("INVALID_PACKAGE_NAME", f"Tên package không hợp lệ: {name!r}")
    try:
        version = normalize_version(str(version))
    except InvalidVersion as exc:
        raise BuildError("INVALID_PYPROJECT", f"Version không hợp lệ theo PEP 440: {project.get('version')!r}") from exc
    prefix = info["internal_prefix"]
    if not normalize(name).startswith(normalize(prefix.rstrip("-")) + "-"):
        raise BuildError("INTERNAL_PREFIX_REQUIRED", f"Tên package '{name}' phải bắt đầu bằng '{prefix}'")
    expected = info.get("package_name")
    if expected and normalize(name) != normalize(expected):
        raise BuildError("INVALID_PACKAGE_NAME", f"pyproject name '{name}' khác package của project '{expected}'")
    requested = info.get("requested_version")
    if info.get("publish_package") and requested:
        try:
            requested_n = normalize_version(requested)
        except InvalidVersion as exc:
            raise BuildError("VERSION_MISMATCH", f"Tag {info['ref_name']} không phải version hợp lệ") from exc
        if requested_n != version:
            raise BuildError("VERSION_MISMATCH",
                             f"Tag {info['ref_name']} (={requested_n}) không khớp version {version} trong pyproject.toml")
    return SourceInfo(name=name, version=version, requires_python=project.get("requires-python"))


# ───────────── container ─────────────

@dataclass
class ContainerResult:
    exit_code: int | None
    result: dict | None
    dist: list[tuple[str, bytes]]
    timed_out: bool = False
    cancelled: bool = False


def _memory_bytes(limit: str) -> str:
    return limit.lower().replace("gb", "g").replace("mb", "m")


def _extract_out(container) -> tuple[dict | None, list[tuple[str, bytes]]]:
    try:
        stream, _ = container.get_archive("/workspace/out")
    except NotFound:
        return None, []
    buf = io.BytesIO(b"".join(stream))
    result, dist = None, []
    with tarfile.open(fileobj=buf, mode="r:") as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/")
            f = tf.extractfile(m)
            if f is None:
                continue
            if parts[-1] == "result.json" and len(parts) == 2:
                try:
                    result = json.loads(f.read().decode("utf-8"))
                except ValueError:
                    result = None
            elif len(parts) == 3 and parts[1] == "dist" and re.match(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*\.(whl|tar\.gz)$",
                                                                      parts[2]):
                dist.append((parts[2], f.read()))
    return result, dist


def run_container(api: ApiClient, info: dict, archive: bytes, source: SourceInfo, blog: BuildLog) -> ContainerResult:
    s = get_settings()
    build_id = str(info["build_id"])
    client = docker.from_env(timeout=120)
    try:
        client.images.get(s.builder_image)
    except ImageNotFound as exc:
        raise BuildError("RUNNER_ERROR", f"Không có builder image {s.builder_image} trên runner") from exc
    token = info["build_token"]
    index = s.build_registry_url.replace("://", f"://__token__:{token}@", 1)
    registry_host = re.sub(r"^https?://", "", s.build_registry_url).split("/")[0].split(":")[0]
    env = {
        "PIP_INDEX_URL": index,
        "PIP_TRUSTED_HOST": registry_host if s.build_registry_url.startswith("http://") else "",
        "EXPECTED_NAME": source.name,
        "EXPECTED_VERSION": source.version if info.get("publish_package") else "",
        "TOOLHUB_BUILD_ID": build_id,
        "HOME": "/tmp",
    }
    host_config_kwargs = {}
    if s.storage_opt_size:
        host_config_kwargs["storage_opt"] = {"size": s.storage_opt_size}
    container = client.containers.create(
        s.builder_image,
        name=f"toolhub-build-{build_id}",
        environment=env,
        network=s.build_network,
        mem_limit=_memory_bytes(info["memory_limit"]),
        memswap_limit=_memory_bytes(info["memory_limit"]),
        nano_cpus=int(float(info["cpu_limit"]) * 1e9),
        pids_limit=s.pids_limit,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        privileged=False,
        user="1000:1000",
        labels={LABEL_BUILD: build_id, LABEL_RUNNER: s.runner_name},
        detach=True,
        **host_config_kwargs,
    )
    try:
        if not container.put_archive("/workspace/src", archive):
            raise BuildError("RUNNER_ERROR", "Không đưa được source vào container")
        container.start()
        blog.line(f"container {container.short_id} — CPU {info['cpu_limit']} / RAM {info['memory_limit']} / "
                  f"timeout {info['timeout_seconds']}s / mạng {s.build_network}")

        def pump() -> None:
            try:
                for chunk in container.logs(stream=True, follow=True):
                    blog.write(chunk.decode("utf-8", "replace"))
            except (DockerException, OSError):
                pass

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        deadline = time.monotonic() + int(info["timeout_seconds"])
        last_control = 0.0
        timed_out = cancelled = False
        while True:
            time.sleep(s.log_flush_seconds)
            blog.flush()
            container.reload()
            if container.status in {"exited", "dead"}:
                break
            now = time.monotonic()
            if now > deadline:
                timed_out = True
                container.kill()
                break
            if now - last_control > 5:
                last_control = now
                if api.control(build_id).get("cancel"):
                    cancelled = True
                    container.kill()
                    break
        try:
            exit_code = container.wait(timeout=30).get("StatusCode")
        except Exception:  # noqa: BLE001
            exit_code = None
        reader.join(timeout=10)
        blog.flush()
        result, dist = (None, []) if (timed_out or cancelled) else _extract_out(container)
        return ContainerResult(exit_code, result, dist, timed_out, cancelled)
    finally:
        try:
            container.remove(force=True)
        except (APIError, NotFound):
            log.warning("cannot remove build container %s", build_id)


# ───────────── toàn bộ build ─────────────

def execute(build_id: str) -> dict:
    s = get_settings()
    with ApiClient() as api:
        try:
            info = api.start(build_id, s.runner_name)
        except ApiError as exc:
            if exc.code == "RUNNER_NOT_ACCEPTING":
                # Runner đang DRAINING/OFFLINE (admin đặt) → trả build về hàng đợi cho runner khác.
                from app.tasks.build_python import build as build_task

                build_task.apply_async(args=[build_id], countdown=30)
                log.info("runner not accepting jobs, requeued %s", build_id)
                return {"requeued": True}
            if exc.status in (404, 409):
                log.info("skip build %s: %s", build_id, exc.message)
                return {"skipped": True, "reason": exc.message}
            raise
        blog = BuildLog(api, build_id, [info["build_token"], s.runner_token])
        status, code, message, steps = "FAILED", "RUNNER_ERROR", None, []
        t0 = time.monotonic()
        try:
            kind = "RELEASE" if info["publish_package"] else "VALIDATION"
            blog.line(f"Build {kind} — {info['project_path']} @ {info['ref_name']} ({info['commit_sha'][:10]}) "
                      f"trên runner {s.runner_name}")
            try:
                archive = api.download_source(build_id)
            except ApiError as exc:
                raise BuildError("GIT_CLONE_FAILED", f"Không lấy được source commit {info['commit_sha']}: "
                                                     f"{exc.message}") from exc
            blog.line(f"Đã lấy source đúng commit ({len(archive) // 1024} KiB)")
            source = validate_source(archive, info)
            blog.line(f"pyproject: {source.name} {source.version} (requires-python {source.requires_python or '-'})")
            if info["publish_package"]:
                blog.line(f"Tag {info['ref_name']} khớp version {source.version} ✓")
            blog.flush()
            res = run_container(api, info, archive, source, blog)
            if res.cancelled:
                raise BuildError("CANCELLED", "Build bị huỷ theo yêu cầu")
            if res.timed_out:
                raise BuildError("BUILD_TIMEOUT", f"Vượt thời gian tối đa {info['timeout_seconds']} giây")
            if res.result is None:
                raise BuildError("RUNNER_ERROR", f"Container kết thúc bất thường (exit {res.exit_code}) — "
                                                 "có thể do hết RAM (OOM) hoặc lỗi runner")
            steps = res.result.get("steps", [])
            if res.result.get("status") != "SUCCESS":
                raise BuildError(res.result.get("error_code") or "BUILD_FAILED", res.result.get("message") or "")
            if not res.dist:
                raise BuildError("BUILD_FAILED", "Không có file dist sau build")
            if info["publish_package"]:
                blog.line("Publish lên registry nội bộ: " + ", ".join(n for n, _ in res.dist))
                try:
                    pv = api.publish({"project_id": str(info["project_id"]), "build_id": build_id,
                                      "package_name": source.name, "version": source.version,
                                      "commit_sha": info["commit_sha"], "git_tag": info["ref_name"]}, res.dist)
                except ApiError as exc:
                    raise BuildError(exc.code if exc.code in PUBLISH_ERROR_CODES else "RUNNER_ERROR",
                                     f"Publish thất bại: {exc.message}") from exc
                blog.line(f"Đã publish {pv['package_name']}=={pv['version']} ✓")
                steps.append({"name": "Publish package", "status": "SUCCESS"})
            else:
                api.upload_artifacts(build_id, res.dist)
                blog.line("Validation build — KHÔNG publish; đã lưu artifact để tải kiểm tra")
            status, code, message = "SUCCESS", None, None
        except BuildError as exc:
            status = "CANCELLED" if exc.code == "CANCELLED" else "FAILED"
            code, message = exc.code, exc.message
            blog.line(f"{'CANCELLED' if status == 'CANCELLED' else 'FAILED'} — {exc.code}: {exc.message}")
        except (DockerException, ApiError) as exc:
            code, message = "RUNNER_ERROR", f"{exc.__class__.__name__}: {exc}"
            blog.line(f"FAILED — RUNNER_ERROR: {message}")
            log.exception("runner error on build %s", build_id)
        except Exception as exc:  # noqa: BLE001
            code, message = "RUNNER_ERROR", f"{exc.__class__.__name__}: {exc}"
            blog.line(f"FAILED — RUNNER_ERROR: {message}")
            log.exception("unexpected error on build %s", build_id)
        blog.line(f"Kết thúc: {status} sau {time.monotonic() - t0:.1f}s")
        blog.flush()
        api.finish(build_id, {"status": status, "error_code": code, "error_message": message,
                              "steps": steps, "log": blog.text})
        return {"status": status, "error_code": code}


def cleanup_orphans(max_age_seconds: int = 7200) -> int:
    """Xoá container build mồ côi (worker chết giữa chừng)."""
    s = get_settings()
    client = docker.from_env(timeout=60)
    removed = 0
    for c in client.containers.list(all=True, filters={"label": f"{LABEL_RUNNER}={s.runner_name}"}):
        created = c.attrs.get("Created", "")
        try:
            from datetime import datetime, timezone

            age = (datetime.now(timezone.utc) - datetime.fromisoformat(created[:26].rstrip("Z") + "+00:00")).total_seconds()
        except ValueError:
            age = max_age_seconds + 1
        if age > max_age_seconds:
            c.remove(force=True)
            removed += 1
    return removed


def running_jobs() -> int:
    s = get_settings()
    try:
        return len(docker.from_env(timeout=10).containers.list(filters={"label": f"{LABEL_RUNNER}={s.runner_name}"}))
    except DockerException:
        return 0
