"""Proxy/cache PyPI (spec 19–21, 38.5, 38.6): lấy index, rewrite link, tải + cache, hash, upstream sập, dependency confusion."""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from sqlalchemy import select, update

from app.db.models import AuditLog, UpstreamFile, UpstreamPackage
from app.services.pypi_proxy_service import parse_html_index, parse_json_index
from tests.conftest import login, make_user

UPSTREAM = "https://pypi.test/simple"
FILES = "https://files.pypi.test"
WHEEL = b"PK\x03\x04 fake requests wheel content " * 100
WHEEL_SHA = hashlib.sha256(WHEEL).hexdigest()
WHEEL_NAME = "requests-2.32.3-py3-none-any.whl"


def _json_index(sha: str = WHEEL_SHA) -> dict:
    return {"meta": {"api-version": "1.1"}, "name": "requests", "versions": ["2.32.3"], "files": [
        {"filename": WHEEL_NAME, "url": f"{FILES}/packages/aa/bb/{WHEEL_NAME}", "hashes": {"sha256": sha},
         "requires-python": ">=3.8", "yanked": False},
        {"filename": "requests-2.0.0.tar.gz", "url": f"{FILES}/packages/cc/requests-2.0.0.tar.gz",
         "hashes": {"sha256": "0" * 64}, "requires-python": None, "yanked": "broken"},
        {"filename": "evil-1.0-py3-none-any.whl", "url": "https://evil.example/evil-1.0-py3-none-any.whl",
         "hashes": {}},
    ]}


@pytest.fixture
def token(admin) -> dict:
    make_user("user1")
    raw = login("user1").post("/api/v1/me/tokens", json={"name": "pip", "scopes": ["read_package"]}).json()["token"]
    return {"Authorization": "Basic " + base64.b64encode(f"__token__:{raw}".encode()).decode()}


@pytest.fixture
def upstream():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mock:
        yield mock


def test_parsers():
    links = parse_json_index(_json_index(), f"{UPSTREAM}/requests/")
    assert links[0].sha256 == WHEEL_SHA and links[1].yanked is True
    html = (f'<a href="../../packages/aa/{WHEEL_NAME}#sha256={WHEEL_SHA}" data-requires-python="&gt;=3.8">x</a>'
            '<a href="https://files.pypi.test/b/requests-1.0.tar.gz" data-yanked="">y</a>')
    links = parse_html_index(html, f"{UPSTREAM}/requests/")
    assert links[0].filename == WHEEL_NAME and links[0].sha256 == WHEEL_SHA and links[0].requires_python == ">=3.8"
    assert links[0].url == f"https://pypi.test/packages/aa/{WHEEL_NAME}"
    assert links[1].yanked is True


def test_proxy_index_rewrites_links(anon, token, upstream, db):
    route = upstream.get(f"{UPSTREAM}/requests/").mock(
        return_value=httpx.Response(200, json=_json_index(), headers={"content-type": "application/vnd.pypi.simple.v1+json",
                                                                      "etag": '"v1"'}))
    r = anon.get("/simple/requests/", headers=token)
    assert r.status_code == 200, r.text
    assert f'href="/packages/upstream/requests/{WHEEL_NAME}#sha256={WHEEL_SHA}"' in r.text
    assert "files.pypi.test" not in r.text                     # link đã rewrite về registry nội bộ
    assert "evil" not in r.text                                # host ngoài allowlist bị loại
    assert 'data-yanked="broken"' not in r.text and "data-yanked" in r.text
    assert route.call_count == 1
    # Trong TTL → dùng cache metadata, không gọi upstream
    anon.get("/simple/requests/", headers=token)
    assert route.call_count == 1
    pkg = db.scalar(select(UpstreamPackage).where(UpstreamPackage.normalized_name == "requests"))
    assert pkg.upstream_etag == '"v1"'


def test_proxy_etag_revalidation(anon, token, upstream, db):
    route = upstream.get(f"{UPSTREAM}/requests/").mock(side_effect=[
        httpx.Response(200, json=_json_index(), headers={"content-type": "application/vnd.pypi.simple.v1+json",
                                                         "etag": '"v1"'}),
        httpx.Response(304),
    ])
    anon.get("/simple/requests/", headers=token)
    db.execute(update(UpstreamPackage).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db.commit()
    r = anon.get("/simple/requests/", headers=token)
    assert r.status_code == 200 and WHEEL_NAME in r.text
    assert route.calls[1].request.headers["if-none-match"] == '"v1"'


def test_proxy_download_cache_hit_and_upstream_down(anon, token, upstream, db):
    upstream.get(f"{UPSTREAM}/requests/").mock(return_value=httpx.Response(
        200, json=_json_index(), headers={"content-type": "application/vnd.pypi.simple.v1+json"}))
    file_route = upstream.get(f"{FILES}/packages/aa/bb/{WHEEL_NAME}").mock(return_value=httpx.Response(200, content=WHEEL))
    anon.get("/simple/requests/", headers=token)
    # 1) lần đầu: tải từ upstream, verify hash, lưu MinIO
    r = anon.get(f"/packages/upstream/requests/{WHEEL_NAME}", headers=token)
    assert r.status_code == 200 and r.content == WHEEL and r.headers["x-cache"] == "UPSTREAM"
    f = db.scalar(select(UpstreamFile).where(UpstreamFile.filename == WHEEL_NAME))
    assert f.cached and f.object_key == f"upstream/requests/{WHEEL_NAME}" and f.sha256 == WHEEL_SHA
    assert db.scalar(select(AuditLog).where(AuditLog.action == "UPSTREAM_PACKAGE_CACHED")) is not None
    # 2) lần sau: cache hit, không gọi upstream
    r = anon.get(f"/packages/upstream/requests/{WHEEL_NAME}", headers=token)
    assert r.headers["x-cache"] == "CACHE" and r.content == WHEEL and file_route.call_count == 1
    # 3) upstream sập hoàn toàn + metadata hết hạn → vẫn cài được từ cache
    db.execute(update(UpstreamPackage).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db.commit()
    upstream.routes.clear()
    upstream.route(host="pypi.test").mock(side_effect=httpx.ConnectError("down"))
    upstream.route(host="files.pypi.test").mock(side_effect=httpx.ConnectError("down"))
    r = anon.get("/simple/requests/", headers=token)
    assert r.status_code == 200 and WHEEL_NAME in r.text
    r = anon.get(f"/packages/upstream/requests/{WHEEL_NAME}", headers=token)
    assert r.status_code == 200 and r.content == WHEEL


def test_uncached_package_when_upstream_down(anon, token, upstream):
    upstream.route(host="pypi.test").mock(side_effect=httpx.ConnectError("down"))
    r = anon.get("/simple/numpy/", headers=token)
    assert r.status_code == 502 and r.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


def test_upstream_hash_mismatch_not_cached(anon, token, upstream, db):
    upstream.get(f"{UPSTREAM}/requests/").mock(return_value=httpx.Response(
        200, json=_json_index(sha="f" * 64), headers={"content-type": "application/vnd.pypi.simple.v1+json"}))
    upstream.get(f"{FILES}/packages/aa/bb/{WHEEL_NAME}").mock(return_value=httpx.Response(200, content=WHEEL))
    anon.get("/simple/requests/", headers=token)
    r = anon.get(f"/packages/upstream/requests/{WHEEL_NAME}", headers=token)
    assert r.status_code == 502 and r.json()["error"]["code"] == "UPSTREAM_HASH_MISMATCH"
    assert db.scalar(select(UpstreamFile).where(UpstreamFile.filename == WHEEL_NAME)).cached is False


def test_upstream_package_not_found(anon, token, upstream):
    upstream.get(f"{UPSTREAM}/khong-ton-tai-xyz/").mock(return_value=httpx.Response(404))
    r = anon.get("/simple/khong-ton-tai-xyz/", headers=token)
    assert r.status_code == 404 and r.json()["error"]["code"] == "PACKAGE_NOT_FOUND"


def test_html_upstream_supported(anon, token, upstream):
    html = f'<html><body><a href="{FILES}/packages/aa/bb/{WHEEL_NAME}#sha256={WHEEL_SHA}">{WHEEL_NAME}</a></body></html>'
    upstream.get(f"{UPSTREAM}/requests/").mock(return_value=httpx.Response(200, text=html,
                                                                           headers={"content-type": "text/html"}))
    r = anon.get("/simple/requests/", headers=token)
    assert f"/packages/upstream/requests/{WHEEL_NAME}#sha256={WHEEL_SHA}" in r.text


# ── Dependency confusion (test BẮT BUỘC — spec 38.6) ──

@pytest.mark.parametrize("name", ["hawee-package-khong-ton-tai", "Hawee_Package.Khong_Ton_Tai"])
def test_internal_prefix_never_hits_pypi(anon, token, upstream, name):
    catch_all = upstream.route().mock(return_value=httpx.Response(200, text="<a href='x'>x</a>"))
    r = anon.get(f"/simple/{name}/", headers=token, follow_redirects=True)
    assert r.status_code == 404
    r = anon.get("/packages/upstream/hawee-package-khong-ton-tai/hawee_package_khong_ton_tai-1.0-py3-none-any.whl",
                 headers=token)
    assert r.status_code == 404
    assert catch_all.call_count == 0, "KHÔNG được gọi pypi.org cho package hawee-*"


def test_anonymous_denied_by_default(anon, upstream):
    upstream.route().mock(return_value=httpx.Response(200))
    assert anon.get("/simple/requests/").status_code == 401
