"""Proxy/cache PyPI công khai (spec mục 19–20).

- Metadata (danh sách file) cache TTL 15 phút, dùng ETag; upstream lỗi → phục vụ bản cache cũ.
- File (.whl/.tar.gz) tải một lần, verify SHA256, lưu MinIO, lần sau phục vụ từ cache (immutable).
- Chỉ tải file từ host nằm trong allowlist (chống SSRF qua metadata upstream).
"""
from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import IO
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.core.metrics import UPSTREAM_CACHE_HITS, UPSTREAM_CACHE_MISSES, UPSTREAM_DOWNLOAD_BYTES
from app.db.models import UpstreamFile, UpstreamPackage
from app.db.models.enums import ActorType, UpstreamSource
from app.services import audit_service, storage_service
from app.services.audit_service import A
from app.utils.naming import is_internal_package
from app.utils.pkgmeta import InvalidPackageFile, classify_filename

log = logging.getLogger(__name__)

JSON_ACCEPT = "application/vnd.pypi.simple.v1+json, application/vnd.pypi.simple.v1+html;q=0.2, text/html;q=0.1"
_FILENAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*\.(whl|tar\.gz|zip|tar\.bz2|tgz|egg)$")
MAX_UPSTREAM_FILE_BYTES = 2 * 1024 * 1024 * 1024
ACCESS_TOUCH_INTERVAL = timedelta(hours=1)


class UpstreamUnavailable(AppError):
    def __init__(self, message: str = "Không kết nối được PyPI upstream") -> None:
        super().__init__("UPSTREAM_UNAVAILABLE", message, 502)


class DependencyConfusionBlocked(AppError):
    def __init__(self) -> None:
        super().__init__("PACKAGE_NOT_FOUND", "Package nội bộ không tồn tại (không tra PyPI)", 404)


@dataclass
class UpstreamLink:
    filename: str
    url: str
    sha256: str | None
    requires_python: str | None
    yanked: bool


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self.links.append(dict(attrs))


def _guard(normalized: str) -> None:
    """Luật bảo mật bắt buộc: hawee-* KHÔNG BAO GIỜ đi ra PyPI."""
    if is_internal_package(normalized, get_settings().internal_package_prefix):
        raise DependencyConfusionBlocked()


def _allowed_host(url: str, index_url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    allowed = set(get_settings().upstream_file_host_list)
    allowed.add((urlsplit(index_url).hostname or "").lower())
    return urlsplit(url).scheme in {"https", "http"} and host in allowed


def parse_json_index(data: dict, index_url: str) -> list[UpstreamLink]:
    out = []
    for f in data.get("files", []):
        filename, url = f.get("filename"), f.get("url")
        if not filename or not url:
            continue
        yanked = f.get("yanked", False)
        out.append(UpstreamLink(filename=filename, url=urljoin(index_url, url),
                                sha256=(f.get("hashes") or {}).get("sha256"),
                                requires_python=f.get("requires-python"), yanked=bool(yanked)))
    return out


def parse_html_index(html: str, index_url: str) -> list[UpstreamLink]:
    parser = _AnchorParser()
    parser.feed(html)
    out = []
    for attrs in parser.links:
        href = attrs.get("href")
        if not href:
            continue
        url, _, fragment = href.partition("#")
        sha = None
        if fragment.startswith("sha256="):
            sha = fragment[len("sha256="):]
        filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        out.append(UpstreamLink(filename=filename, url=urljoin(index_url, url), sha256=sha,
                                requires_python=attrs.get("data-requires-python"),
                                yanked="data-yanked" in attrs))
    return out


def _index_url(normalized: str) -> str:
    return get_settings().pypi_upstream_simple_url.rstrip("/") + f"/{normalized}/"


def _client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(timeout=httpx.Timeout(s.pypi_upstream_timeout_seconds, connect=5.0), follow_redirects=True,
                        headers={"User-Agent": "hawee-tool-hub/1.0 (+internal registry proxy)"})


def _fetch_index(normalized: str, etag: str | None) -> tuple[int, list[UpstreamLink] | None, str | None, dict | None]:
    url = _index_url(normalized)
    headers = {"Accept": JSON_ACCEPT}
    if etag:
        headers["If-None-Match"] = etag
    try:
        with _client() as client:
            resp = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise UpstreamUnavailable(f"PyPI không phản hồi: {exc.__class__.__name__}") from exc
    if resp.status_code == 304:
        return 304, None, etag, None
    if resp.status_code == 404:
        return 404, None, None, None
    if resp.status_code >= 400:
        raise UpstreamUnavailable(f"PyPI trả lỗi HTTP {resp.status_code}")
    ctype = resp.headers.get("content-type", "")
    final_url = str(resp.url)
    if "json" in ctype:
        data = resp.json()
        links = parse_json_index(data, final_url)
        raw = {"format": "json", "meta": data.get("meta"), "versions": data.get("versions")}
    else:
        links = parse_html_index(resp.text, final_url)
        raw = {"format": "html"}
    links = [ln for ln in links if _FILENAME_OK.match(ln.filename) and _allowed_host(ln.url, final_url)]
    return 200, links, resp.headers.get("etag"), raw


def _file_version(filename: str) -> str | None:
    try:
        return classify_filename(filename).version[:100]
    except InvalidPackageFile:
        return None


def _store_links(db: Session, pkg: UpstreamPackage, links: list[UpstreamLink]) -> None:
    unique = {ln.filename: ln for ln in links}  # upstream đôi khi lặp tên file
    rows = [{"id": uuid.uuid4(), "upstream_package_id": pkg.id, "filename": ln.filename, "upstream_url": ln.url,
             "version": _file_version(ln.filename), "sha256": ln.sha256,
             "requires_python": (ln.requires_python or None) and ln.requires_python[:255],
             "yanked": ln.yanked, "cached": False} for ln in unique.values()]
    for start in range(0, len(rows), 1000):
        stmt = insert(UpstreamFile).values(rows[start:start + 1000])
        # Giữ trạng thái cache của file đã có; chỉ cập nhật URL/hash/yanked từ upstream.
        stmt = stmt.on_conflict_do_update(
            constraint="uq_upstream_files_package_filename",
            set_={"upstream_url": stmt.excluded.upstream_url, "requires_python": stmt.excluded.requires_python,
                  "yanked": stmt.excluded.yanked,
                  "sha256": func.coalesce(stmt.excluded.sha256, UpstreamFile.sha256)},
        )
        db.execute(stmt)


def get_index(db: Session, normalized: str) -> tuple[UpstreamPackage, list[UpstreamFile]]:
    """Danh sách file của package public (refresh khi hết TTL; upstream lỗi → dùng cache cũ)."""
    _guard(normalized)
    s = get_settings()
    now = datetime.now(timezone.utc)
    pkg = db.scalar(select(UpstreamPackage).where(UpstreamPackage.normalized_name == normalized))
    if pkg is None or pkg.expires_at <= now:
        try:
            status, links, etag, raw = _fetch_index(normalized, pkg.upstream_etag if pkg else None)
        except UpstreamUnavailable:
            if pkg is not None and _has_files(db, pkg):
                log.warning("upstream unavailable, serving stale index", extra={"extra_data": {"pkg": normalized}})
                return pkg, _files(db, pkg)
            raise
        if status == 404:
            if pkg is not None and _has_files(db, pkg):
                return pkg, _files(db, pkg)
            raise NotFound("PACKAGE_NOT_FOUND", f"Package '{normalized}' không tồn tại trên PyPI")
        if pkg is None:
            pkg = UpstreamPackage(normalized_name=normalized, source=UpstreamSource.PYPI, last_checked_at=now,
                                  expires_at=now)
            db.add(pkg)
            db.flush()
        pkg.last_checked_at = now
        pkg.expires_at = now + timedelta(seconds=s.pypi_metadata_ttl_seconds)
        if status == 200 and links is not None:
            pkg.upstream_etag = etag
            pkg.raw_metadata_json = raw
            _store_links(db, pkg, links)
        db.commit()
    return pkg, _files(db, pkg)


def _has_files(db: Session, pkg: UpstreamPackage) -> bool:
    return db.scalar(select(UpstreamFile.id).where(UpstreamFile.upstream_package_id == pkg.id).limit(1)) is not None


def _files(db: Session, pkg: UpstreamPackage) -> list[UpstreamFile]:
    return list(db.scalars(select(UpstreamFile).where(UpstreamFile.upstream_package_id == pkg.id)
                           .order_by(UpstreamFile.filename)))


def upstream_object_key(normalized: str, filename: str) -> str:
    return f"upstream/{normalized}/{filename}"


@dataclass
class ServedFile:
    filename: str
    size: int | None
    source: str  # cache | upstream
    stream: object  # iterator[bytes]


def _download_to_temp(url: str, expected_sha: str | None) -> tuple[IO[bytes], int, str]:
    tmp = tempfile.TemporaryFile()
    digest = hashlib.sha256()
    size = 0
    try:
        with _client() as client, client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise UpstreamUnavailable(f"PyPI trả lỗi HTTP {resp.status_code} khi tải file")
            for chunk in resp.iter_bytes(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPSTREAM_FILE_BYTES:
                    raise AppError("CACHE_STORAGE_ERROR", "File upstream quá lớn", 502)
                digest.update(chunk)
                tmp.write(chunk)
    except httpx.HTTPError as exc:
        tmp.close()
        raise UpstreamUnavailable(f"Lỗi tải file từ PyPI: {exc.__class__.__name__}") from exc
    except Exception:
        tmp.close()
        raise
    sha = digest.hexdigest()
    if expected_sha and sha.lower() != expected_sha.lower():
        tmp.close()
        raise AppError("UPSTREAM_HASH_MISMATCH", "SHA256 của file tải từ PyPI không khớp metadata", 502)
    tmp.seek(0)
    return tmp, size, sha


def _iter_tempfile(tmp: IO[bytes]):
    try:
        while True:
            chunk = tmp.read(256 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        tmp.close()


def serve_file(db: Session, normalized: str, filename: str) -> ServedFile:
    _guard(normalized)
    s = get_settings()
    pkg = db.scalar(select(UpstreamPackage).where(UpstreamPackage.normalized_name == normalized))
    f = None
    if pkg is not None:
        f = db.scalar(select(UpstreamFile).where(UpstreamFile.upstream_package_id == pkg.id,
                                                 UpstreamFile.filename == filename))
    if f is None:
        # Có thể client dùng link cũ / index chưa từng được lấy: refresh index rồi tìm lại.
        pkg, files = get_index(db, normalized)
        f = next((x for x in files if x.filename == filename), None)
        if f is None:
            raise NotFound("PACKAGE_NOT_FOUND", "Không tìm thấy file")
    now = datetime.now(timezone.utc)
    bucket = s.minio_bucket_packages
    if f.cached and f.object_key:
        if f.last_accessed_at is None or now - f.last_accessed_at > ACCESS_TOUCH_INTERVAL:
            f.last_accessed_at = now
            db.commit()
        UPSTREAM_CACHE_HITS.inc()
        return ServedFile(filename, f.size_bytes, "cache", storage_service.iter_object(bucket, f.object_key))
    if not _allowed_host(f.upstream_url, _index_url(normalized)):
        raise AppError("PACKAGE_ACCESS_DENIED", "Host upstream không nằm trong allowlist", 403)
    UPSTREAM_CACHE_MISSES.inc()
    tmp, size, sha = _download_to_temp(f.upstream_url, f.sha256)
    UPSTREAM_DOWNLOAD_BYTES.inc(size)
    key = upstream_object_key(normalized, filename)
    try:
        storage_service.put_object(bucket, key, tmp, size)
    except Exception as exc:  # noqa: BLE001
        log.exception("cannot store upstream file in cache")
        tmp.seek(0)
        if isinstance(exc, AppError):
            raise
        # Vẫn trả file cho client dù cache lỗi — không chặn pip.
        return ServedFile(filename, size, "upstream", _iter_tempfile(tmp))
    f.cached, f.cached_at, f.last_accessed_at = True, now, now
    f.object_key, f.size_bytes = key, size
    if not f.sha256:
        f.sha256 = sha
    audit_service.record(db, A.UPSTREAM_PACKAGE_CACHED, actor_type=ActorType.SYSTEM, resource_type="upstream_file",
                         resource_id=f"{normalized}/{filename}", metadata={"size": size, "sha256": sha})
    db.commit()
    tmp.seek(0)
    return ServedFile(filename, size, "upstream", _iter_tempfile(tmp))


def cleanup_cache(db: Session) -> dict:
    """Xoá binary upstream không truy cập > N ngày KHI tổng dung lượng vượt ngưỡng (spec 20)."""
    s = get_settings()
    total = db.scalar(select(func.coalesce(func.sum(UpstreamFile.size_bytes), 0))
                      .where(UpstreamFile.cached.is_(True))) or 0
    removed, freed = 0, 0
    if total <= s.upstream_cache_max_bytes:
        return {"total_bytes": int(total), "removed": 0, "freed_bytes": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=s.upstream_cache_max_idle_days)
    candidates = db.scalars(select(UpstreamFile).where(UpstreamFile.cached.is_(True),
                                                       UpstreamFile.last_accessed_at < cutoff)
                            .order_by(UpstreamFile.last_accessed_at.asc()).limit(5000))
    for f in candidates:
        if total - freed <= s.upstream_cache_max_bytes:
            break
        try:
            if f.object_key:
                storage_service.remove_object(s.minio_bucket_packages, f.object_key)
        except Exception:  # noqa: BLE001
            log.warning("cannot remove cached object %s", f.object_key)
            continue
        freed += f.size_bytes or 0
        removed += 1
        f.cached, f.object_key, f.cached_at = False, None, None
    db.commit()
    return {"total_bytes": int(total), "removed": removed, "freed_bytes": freed}
