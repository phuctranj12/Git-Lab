from __future__ import annotations

import re

from packaging.utils import canonicalize_name

_NORMALIZE_RE = re.compile(r"[-_.]+")
# PEP 508 project name (ASCII letters/digits, - _ . ở giữa).
_VALID_PROJECT_NAME = re.compile(r"^([A-Z0-9]|[A-Z0-9][A-Z0-9._-]*[A-Z0-9])$", re.IGNORECASE)
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")

RESERVED_SLUGS = frozenset({"api", "admin", "simple", "packages", "health", "metrics", "internal", "static", "assets",
                            "new", "login", "logout", "settings", "me"})


def normalize_package_name(name: str) -> str:
    """PEP 503: lowercase, gộp chuỗi [-_.] thành '-'. Ví dụ Hawee_PDF.Parser → hawee-pdf-parser."""
    return _NORMALIZE_RE.sub("-", name).lower()


def is_valid_package_name(name: str) -> bool:
    return bool(_VALID_PROJECT_NAME.match(name))


def is_internal_package(name: str, prefix: str) -> bool:
    """Luật dependency-confusion: mọi tên (đã chuẩn hoá) bắt đầu bằng prefix là package nội bộ."""
    return normalize_package_name(name).startswith(normalize_package_name(prefix.rstrip("-")) + "-")


def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug)) and slug not in RESERVED_SLUGS


def slugify(text: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:100].strip("-")


def canonical(name: str) -> str:
    return str(canonicalize_name(name))


def wheel_dist_name(normalized_name: str) -> str:
    """Tên file wheel dùng '_' thay cho '-' (PEP 427)."""
    return normalized_name.replace("-", "_")
