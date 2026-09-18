"""Worker chỉ nói chuyện với API qua HTTP (runner token) — có thể chạy trên host build riêng (spec 14, 44)."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class ApiClient:
    def __init__(self) -> None:
        s = get_settings()
        self._client = httpx.Client(base_url=f"{s.api_base_url}/api/v1/internal",
                                    headers={"Authorization": f"Bearer {s.runner_token}"},
                                    timeout=httpx.Timeout(60.0, connect=10.0))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _request(self, method: str, url: str, *, retries: int = 3, **kwargs) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                last = exc
                time.sleep(min(2 ** attempt, 10))
                continue
            if resp.status_code >= 500 and attempt < retries - 1:
                time.sleep(min(2 ** attempt, 10))
                continue
            if resp.status_code >= 400:
                try:
                    err = resp.json().get("error", {})
                except ValueError:
                    err = {}
                raise ApiError(resp.status_code, err.get("code", "HTTP_ERROR"), err.get("message", resp.text[:300]))
            return resp
        raise ApiError(0, "API_UNREACHABLE", f"Không gọi được API: {last}")

    def heartbeat(self, payload: dict[str, Any]) -> dict:
        return self._request("POST", "/runner/heartbeat", json=payload, retries=1).json()

    def start(self, build_id: str, runner_name: str) -> dict:
        return self._request("POST", f"/builds/{build_id}/start", json={"runner_name": runner_name}).json()

    def download_source(self, build_id: str) -> bytes:
        return self._request("GET", f"/builds/{build_id}/source").content

    def append_log(self, build_id: str, text: str) -> None:
        try:
            self._request("POST", f"/builds/{build_id}/log", json={"lines": text}, retries=1)
        except ApiError as exc:
            log.warning("cannot push live log: %s", exc)

    def control(self, build_id: str) -> dict:
        try:
            return self._request("GET", f"/builds/{build_id}/control", retries=1).json()
        except ApiError:
            return {"cancel": False}

    def upload_artifacts(self, build_id: str, files: list[tuple[str, bytes]]) -> dict:
        return self._request("POST", f"/builds/{build_id}/artifacts",
                             files=[("files", (n, c, "application/octet-stream")) for n, c in files]).json()

    def publish(self, data: dict[str, str], files: list[tuple[str, bytes]]) -> dict:
        return self._request("POST", "/packages/publish", data=data, retries=1,
                             files=[("files", (n, c, "application/octet-stream")) for n, c in files]).json()

    def finish(self, build_id: str, payload: dict[str, Any]) -> dict:
        return self._request("POST", f"/builds/{build_id}/finish", json=payload, retries=5).json()

    def maintenance(self) -> dict:
        return self._request("POST", "/maintenance/run", retries=1).json()
