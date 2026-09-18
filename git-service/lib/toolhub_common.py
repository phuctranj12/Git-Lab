"""Tiện ích chung cho script git-service (chỉ stdlib — chạy bằng python3 hệ thống)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ENV_FILE = "/etc/toolhub/git-service.env"


def load_env() -> dict[str, str]:
    """sshd xoá env của process → đọc cấu hình từ file root:toolhub 0640 do entrypoint ghi."""
    cfg: dict[str, str] = {}
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    cfg[k.strip()] = v.strip()
    except OSError:
        pass
    for k in ("TOOLHUB_API_URL", "TOOLHUB_INTERNAL_SECRET", "TOOLHUB_SPOOL_DIR"):
        if os.environ.get(k):
            cfg.setdefault(k, os.environ[k])
    return cfg


def api(method: str, path: str, payload: dict | None = None, timeout: float = 5.0) -> tuple[int, dict]:
    cfg = load_env()
    url = cfg.get("TOOLHUB_API_URL", "http://backend:8000").rstrip("/") + "/api/v1/internal" + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {cfg.get('TOOLHUB_INTERNAL_SECRET', '')}",
        "Content-Type": "application/json",
        "User-Agent": "toolhub-git-service/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}


def err(msg: str) -> None:
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.stderr.flush()
