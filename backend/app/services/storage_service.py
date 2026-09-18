from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from typing import IO

from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings

log = logging.getLogger(__name__)

_client: Minio | None = None


def get_minio() -> Minio:
    global _client
    if _client is None:
        s = get_settings()
        _client = Minio(s.minio_endpoint, access_key=s.minio_access_key, secret_key=s.minio_secret_key,
                        secure=s.minio_secure)
    return _client


def reset_minio() -> None:
    global _client
    _client = None


def all_buckets() -> list[str]:
    s = get_settings()
    return [s.minio_bucket_packages, s.minio_bucket_logs, s.minio_bucket_artifacts, s.minio_bucket_backups]


def ensure_buckets() -> list[str]:
    created = []
    client = get_minio()
    for bucket in all_buckets():
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            created.append(bucket)
    return created


def put_object(bucket: str, key: str, data: IO[bytes], size: int, content_type: str = "application/octet-stream") -> None:
    data.seek(0)
    get_minio().put_object(bucket, key, data, size, content_type=content_type)


def put_bytes(bucket: str, key: str, payload: bytes, content_type: str = "application/octet-stream") -> None:
    put_object(bucket, key, io.BytesIO(payload), len(payload), content_type)


def object_exists(bucket: str, key: str) -> bool:
    try:
        get_minio().stat_object(bucket, key)
        return True
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject", "NotFound"}:
            return False
        raise


def get_bytes(bucket: str, key: str) -> bytes:
    resp = get_minio().get_object(bucket, key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def iter_object(bucket: str, key: str, chunk_size: int = 256 * 1024) -> Iterator[bytes]:
    """Stream object từ MinIO; đóng kết nối khi hết (kể cả client ngắt giữa chừng)."""
    resp = get_minio().get_object(bucket, key)
    try:
        yield from resp.stream(chunk_size)
    finally:
        resp.close()
        resp.release_conn()


def remove_object(bucket: str, key: str) -> None:
    get_minio().remove_object(bucket, key)


def copy_object(src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> None:
    from minio.commonconfig import CopySource

    get_minio().copy_object(dst_bucket, dst_key, CopySource(src_bucket, src_key))


def check_ready() -> None:
    get_minio().bucket_exists(get_settings().minio_bucket_packages)
