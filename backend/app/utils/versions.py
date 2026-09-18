from __future__ import annotations

import re

from packaging.version import InvalidVersion, Version

TAG_RE = re.compile(r"^v(?P<version>\d+(?:\.\d+)*(?:[._-]?(?:a|b|rc|alpha|beta|pre|preview|c|post|dev)\.?\d*)*)$",
                    re.IGNORECASE)


def parse_release_tag(tag: str) -> str | None:
    """'v1.4.0' → '1.4.0'. Tag không đúng dạng vX.Y.Z → None (không tạo release build)."""
    m = TAG_RE.match(tag.strip())
    if not m:
        return None
    try:
        Version(m.group("version"))
    except InvalidVersion:
        return None
    return m.group("version")


def versions_equal(a: str, b: str) -> bool:
    """So khớp chặt theo dạng chuẩn PEP 440: '1.4.0' == '1.4.0', '1.4' != '1.4.0', '1.0.0RC1' == '1.0.0rc1'."""
    try:
        return str(Version(a)) == str(Version(b))
    except InvalidVersion:
        return False


def normalize_version(v: str) -> str:
    return str(Version(v))


def is_valid_version(v: str) -> bool:
    try:
        Version(v)
    except InvalidVersion:
        return False
    return True


def sort_key(v: str) -> tuple[int, Version | str]:
    try:
        return (1, Version(v))
    except InvalidVersion:
        return (0, v)
