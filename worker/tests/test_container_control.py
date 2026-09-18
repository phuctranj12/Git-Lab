"""Timeout / cancel / thu kết quả của container build — Docker client giả lập (không cần Docker daemon)."""
from __future__ import annotations

import io
import json
import tarfile

import pytest

from app.runners import python_runner
from app.runners.python_runner import BuildLog, SourceInfo, run_container


class FakeContainer:
    def __init__(self, exits_after: int | None, result: dict | None = None, dist: dict[str, bytes] | None = None):
        self.short_id = "abc123"
        self.status = "running"
        self._ticks = 0
        self._exits_after = exits_after
        self.killed = False
        self.removed = False
        self.put = None
        self._result = result
        self._dist = dist or {}

    def put_archive(self, path, data):
        self.put = (path, data)
        return True

    def start(self):
        pass

    def logs(self, stream=True, follow=True):
        yield b"==> building\n"

    def reload(self):
        self._ticks += 1
        if self.killed or (self._exits_after is not None and self._ticks >= self._exits_after):
            self.status = "exited"

    def kill(self):
        self.killed = True
        self.status = "exited"

    def wait(self, timeout=None):
        return {"StatusCode": 137 if self.killed else 0}

    def get_archive(self, path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            files = {"out/result.json": json.dumps(self._result or {}).encode()}
            files.update({f"out/dist/{k}": v for k, v in self._dist.items()})
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        return iter([buf.getvalue()]), {}

    def remove(self, force=False):
        self.removed = True


class FakeClient:
    def __init__(self, container: FakeContainer):
        self.container = container
        self.create_kwargs: dict = {}
        self.images = self
        self.containers = self

    def get(self, _image):
        return object()

    def create(self, image, **kwargs):
        self.create_kwargs = {"image": image, **kwargs}
        return self.container


class FakeApi:
    def __init__(self, cancel: bool = False):
        self.cancel = cancel
        self.logs: list[str] = []

    def append_log(self, _bid, text):
        self.logs.append(text)

    def control(self, _bid):
        return {"cancel": self.cancel}


INFO = {"build_id": "b-1", "build_token": "hth_tok", "memory_limit": "4g", "cpu_limit": 2, "timeout_seconds": 1,
        "publish_package": True}
SOURCE = SourceInfo(name="hawee-demo", version="0.1.0")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    s = python_runner.get_settings()
    object.__setattr__(s, "log_flush_seconds", 0.05)


def _run(monkeypatch, container: FakeContainer, api: FakeApi):
    client = FakeClient(container)
    monkeypatch.setattr(python_runner.docker, "from_env", lambda timeout=None: client)
    api_log = BuildLog(api, "b-1", ["hth_tok"])
    return run_container(api, INFO, b"tar", SOURCE, api_log), client, api_log


def test_timeout_kills_container(monkeypatch):
    container = FakeContainer(exits_after=None)
    res, client, _ = _run(monkeypatch, container, FakeApi())
    assert res.timed_out and container.killed and container.removed
    assert res.result is None


def test_cancel_kills_container(monkeypatch):
    container = FakeContainer(exits_after=None)
    res, _, _ = _run(monkeypatch, container, FakeApi(cancel=True))
    assert res.cancelled and container.killed and container.removed


def test_success_collects_result_and_hardened_container(monkeypatch):
    container = FakeContainer(exits_after=2, result={"status": "SUCCESS", "steps": []},
                              dist={"hawee_demo-0.1.0-py3-none-any.whl": b"PK", "../evil.whl": b"x"})
    res, client, blog = _run(monkeypatch, container, FakeApi())
    assert not res.timed_out and res.result["status"] == "SUCCESS"
    assert [n for n, _ in res.dist] == ["hawee_demo-0.1.0-py3-none-any.whl"]
    kw = client.create_kwargs
    assert kw["privileged"] is False and kw["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in kw["security_opt"]
    assert kw["user"] == "1000:1000" and kw["network"] == "toolhub-build"
    assert kw["mem_limit"] == "4g" and kw["nano_cpus"] == 2_000_000_000 and kw["pids_limit"] > 0
    assert "volumes" not in kw and "network_mode" not in kw           # không mount host / docker.sock
    assert "__token__:hth_tok@" in kw["environment"]["PIP_INDEX_URL"]
    assert "hth_tok" not in blog.text
    assert container.put[0] == "/workspace/src" and container.removed
