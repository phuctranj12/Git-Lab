#!/usr/bin/env python3
"""Chạy BÊN TRONG container build tạm (ephemeral). Chỉ dùng stdlib + build/twine cài sẵn trong image.

Đầu vào:  /workspace/src           (source đúng commit, do worker đưa vào bằng put_archive)
          env EXPECTED_NAME, EXPECTED_VERSION (rỗng nếu validation build)
          env PIP_INDEX_URL        (registry nội bộ — mọi dependency đi qua proxy)
Đầu ra:   /workspace/out/result.json   {status, error_code, message, steps[]}
          /workspace/out/dist/*        (.whl, .tar.gz)

Thứ tự (spec 12): tests → python -m build → twine check → cài wheel trong venv sạch → import thử.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import zipfile
from email.parser import HeaderParser
from pathlib import Path

SRC_IN = Path("/workspace/src")
OUT = Path("/workspace/out")
WORK = Path("/tmp/work")
SRC = WORK / "src"
DIST = WORK / "dist"

steps: list[dict] = []


class StepFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def banner(text: str) -> None:
    print(f"\n\033[1;36m==> {text}\033[0m", flush=True)


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> int:
    print("$ " + " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, stdout=sys.stdout, stderr=subprocess.STDOUT)
    return proc.returncode


def step(name: str, fail_code: str):
    def deco(fn):
        def wrapper(*a, **kw):
            banner(name)
            t = time.monotonic()
            try:
                result = fn(*a, **kw)
            except StepFailed as exc:
                steps.append({"name": name, "status": "FAILED", "duration_seconds": round(time.monotonic() - t, 1)})
                raise
            except Exception as exc:  # noqa: BLE001
                steps.append({"name": name, "status": "FAILED", "duration_seconds": round(time.monotonic() - t, 1)})
                raise StepFailed(fail_code, f"{name}: {exc}") from exc
            steps.append({"name": name, "status": result or "SUCCESS",
                          "duration_seconds": round(time.monotonic() - t, 1)})
            return result
        return wrapper
    return deco


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@step("Chuẩn bị workspace", "RUNNER_ERROR")
def prepare() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    shutil.copytree(SRC_IN, SRC)
    DIST.mkdir(parents=True)
    print(f"python {sys.version.split()[0]} — index: {os.environ.get('PIP_INDEX_URL', '').split('@')[-1]}")


def _extras() -> list[str]:
    try:
        data = tomllib.loads((SRC / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    extras = (data.get("project") or {}).get("optional-dependencies") or {}
    return [e for e in ("test", "tests", "testing", "dev") if e in extras]


def _has_tests() -> bool:
    if (SRC / "tests").is_dir() or (SRC / "test").is_dir():
        return True
    return any(SRC.glob("test_*.py")) or any(SRC.glob("*_test.py"))


@step("Chạy test (pytest)", "TEST_FAILED")
def run_tests() -> str:
    if not _has_tests():
        print("Không có thư mục tests/ — bỏ qua bước test.")
        return "SKIPPED"
    venv = WORK / "testenv"
    if run([sys.executable, "-m", "venv", str(venv)]) != 0:
        raise StepFailed("RUNNER_ERROR", "Không tạo được venv test")
    py = str(venv / "bin" / "python")
    extras = _extras()
    target = f"{SRC}[{','.join(extras)}]" if extras else str(SRC)
    if run([py, "-m", "pip", "install", "--quiet", target, "pytest"]) != 0:
        raise StepFailed("TEST_FAILED", "Không cài được dependency để chạy test")
    rc = run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=SRC)
    if rc == 5:
        print("pytest: không có test nào được thu thập.")
        return "SKIPPED"
    if rc != 0:
        raise StepFailed("TEST_FAILED", f"pytest thất bại (exit {rc})")
    return "SUCCESS"


@step("Build package (python -m build)", "BUILD_FAILED")
def build() -> None:
    if run([sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(DIST), str(SRC)]) != 0:
        raise StepFailed("BUILD_FAILED", "python -m build thất bại")
    files = sorted(p.name for p in DIST.iterdir())
    print("Artifacts:", ", ".join(files))
    if not any(f.endswith(".whl") for f in files):
        raise StepFailed("BUILD_FAILED", "Không sinh ra file .whl")


@step("Kiểm tra metadata (twine check)", "TWINE_CHECK_FAILED")
def twine_check() -> None:
    if run([sys.executable, "-m", "twine", "check", *[str(p) for p in sorted(DIST.iterdir())]]) != 0:
        raise StepFailed("TWINE_CHECK_FAILED", "twine check thất bại")


def _wheel() -> Path:
    return next(p for p in DIST.iterdir() if p.suffix == ".whl")


def _wheel_meta(whl: Path) -> tuple[str, str, list[str]]:
    with zipfile.ZipFile(whl) as zf:
        meta_name = next(n for n in zf.namelist() if n.count("/") == 1 and n.endswith(".dist-info/METADATA"))
        msg = HeaderParser().parsestr(zf.read(meta_name).decode("utf-8", "replace"))
        tops = set()
        for n in zf.namelist():
            first = n.split("/", 1)[0]
            if first.endswith((".dist-info", ".data")):
                continue
            if "/" in n and n.split("/")[1] == "__init__.py":
                tops.add(first)
            elif n.endswith(".py") and "/" not in n:
                tops.add(n[:-3])
    return msg["Name"], msg["Version"], sorted(tops)


@step("Đối chiếu tên/version trong wheel", "INVALID_PACKAGE_NAME")
def verify_metadata() -> None:
    name, version, _ = _wheel_meta(_wheel())
    expected_name = os.environ.get("EXPECTED_NAME", "")
    expected_version = os.environ.get("EXPECTED_VERSION", "")
    print(f"wheel: {name} {version}")
    if expected_name and normalize(name) != normalize(expected_name):
        raise StepFailed("INVALID_PACKAGE_NAME", f"Wheel tên '{name}' ≠ project '{expected_name}'")
    if expected_version and version != expected_version:
        raise StepFailed("VERSION_MISMATCH", f"Wheel version {version} ≠ tag {expected_version}")


@step("Cài thử wheel trong môi trường sạch", "INSTALL_TEST_FAILED")
def install_test() -> None:
    whl = _wheel()
    venv = WORK / "installenv"
    if run([sys.executable, "-m", "venv", str(venv)]) != 0:
        raise StepFailed("RUNNER_ERROR", "Không tạo được venv")
    py = str(venv / "bin" / "python")
    if run([py, "-m", "pip", "install", "--quiet", str(whl)]) != 0:
        raise StepFailed("INSTALL_TEST_FAILED", "pip install wheel thất bại")
    if run([py, "-m", "pip", "check"]) != 0:
        raise StepFailed("INSTALL_TEST_FAILED", "pip check báo xung đột dependency")
    _, _, tops = _wheel_meta(whl)
    for mod in tops:
        if run([py, "-c", f"import {mod}; print('import {mod}: OK')"], cwd=Path("/")) != 0:
            raise StepFailed("INSTALL_TEST_FAILED", f"Không import được module '{mod}' sau khi cài")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    result = {"status": "SUCCESS", "error_code": None, "message": None}
    try:
        prepare()
        run_tests()
        build()
        twine_check()
        verify_metadata()
        install_test()
        shutil.copytree(DIST, OUT / "dist", dirs_exist_ok=True)
        banner("BUILD SUCCESS")
    except StepFailed as exc:
        result = {"status": "FAILED", "error_code": exc.code, "message": exc.message}
        print(f"\n\033[1;31mBUILD FAILED — {exc.code}: {exc.message}\033[0m", flush=True)
    result["steps"] = steps
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0 if result["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
