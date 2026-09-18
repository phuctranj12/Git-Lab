"""Unit tests: chuẩn hoá tên, prefix, version/tag, hash token/mật khẩu, JWT, SSH key, phân quyền."""
from __future__ import annotations

import base64
import os
import shutil
import struct
import subprocess
import tempfile
import uuid
from pathlib import Path

import jwt
import pytest

from app.core import security
from app.core.permissions import (
    Level,
    Membership,
    Perm,
    can_project,
    can_push_ref,
    expand_scopes,
    group_level,
    project_level,
)
from app.db.models.enums import GroupRole, ProjectRole, Visibility
from app.utils.naming import is_internal_package, is_valid_package_name, is_valid_slug, normalize_package_name, slugify
from app.utils.ssh import InvalidSshKey, parse_public_key
from app.utils.versions import normalize_version, parse_release_tag, versions_equal

pytestmark = pytest.mark.unit


# ── package normalization / prefix ──

@pytest.mark.parametrize("raw,expected", [
    ("Hawee_PDF.Parser", "hawee-pdf-parser"),
    ("hawee--pdf__parser", "hawee-pdf-parser"),
    ("NumPy", "numpy"),
    ("zope.interface", "zope-interface"),
    ("a_._-b", "a-b"),
])
def test_normalize(raw, expected):
    assert normalize_package_name(raw) == expected


@pytest.mark.parametrize("name,internal", [
    ("hawee-pdf-parser", True),
    ("Hawee_PDF", True),
    ("HAWEE.tool", True),
    ("hawee", False),
    ("haweetools", False),
    ("numpy", False),
    ("my-hawee-lib", False),
])
def test_internal_prefix(name, internal):
    assert is_internal_package(name, "hawee-") is internal


def test_valid_package_names():
    assert is_valid_package_name("hawee-demo-tool")
    assert not is_valid_package_name("-bad")
    assert not is_valid_package_name("bad name")
    assert not is_valid_package_name("hawee-tool-")


def test_slug():
    assert is_valid_slug("boc-tach-ban-ve")
    assert not is_valid_slug("Boc")
    assert not is_valid_slug("-x")
    assert not is_valid_slug("api")  # reserved
    assert slugify("Bóc tách bản vẽ Đẹp") == "boc-tach-ban-ve-dep"


# ── version / tag validation ──

@pytest.mark.parametrize("tag,version", [
    ("v1.4.0", "1.4.0"), ("v0.1.0", "0.1.0"), ("v2.0.0rc1", "2.0.0rc1"), ("v1.0.0.post1", "1.0.0.post1"),
    ("1.4.0", None), ("release-1", None), ("v1.x", None), ("vlatest", None),
])
def test_parse_release_tag(tag, version):
    assert parse_release_tag(tag) == version


def test_versions_equal_strict():
    assert versions_equal("1.4.0", "1.4.0")
    assert versions_equal("1.0.0RC1", "1.0.0rc1")
    assert not versions_equal("1.4", "1.4.0")
    assert not versions_equal("1.4.0", "1.4.1")
    assert not versions_equal("abc", "abc")
    assert normalize_version("1.0.0-RC1") == "1.0.0rc1"


# ── password / token hashing / JWT ──

def test_password_argon2id():
    h = security.hash_password("s3cret-pass")
    assert h.startswith("$argon2id$")
    assert security.verify_password("s3cret-pass", h)
    assert not security.verify_password("wrong", h)
    assert not security.verify_password("x", "not-a-hash")


def test_token_hashing():
    raw = security.generate_opaque_token()
    assert raw.startswith("hth_") and len(raw) > 40
    h = security.hash_token(raw)
    assert len(h) == 64 and h != raw
    assert security.hash_token(raw) == h
    assert security.token_display_prefix(raw) == raw[:12]
    assert security.generate_opaque_token() != raw


def test_jwt_roundtrip_and_algorithm_pinning():
    uid = uuid.uuid4()
    tok = security.create_access_token(uid, is_admin=False)
    assert security.decode_access_token(tok)["sub"] == str(uid)
    forged = jwt.encode({"sub": str(uid), "typ": "access", "exp": 9999999999}, "other-secret-other-secret-xx",
                        algorithm="HS256")
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_access_token(forged)
    none_alg = jwt.encode({"sub": str(uid), "typ": "access"}, None, algorithm="none")
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_access_token(none_alg)


# ── SSH key ──

def _ssh_keygen(tmp: Path, key_type: str, bits: int | None = None) -> tuple[str, str]:
    path = tmp / f"k_{key_type}"
    args = ["ssh-keygen", "-q", "-t", key_type, "-N", "", "-f", str(path), "-C", "dev@pc"]
    if bits:
        args += ["-b", str(bits)]
    subprocess.run(args, check=True, capture_output=True)
    pub = Path(str(path) + ".pub").read_text().strip()
    fp = subprocess.run(["ssh-keygen", "-lf", str(path) + ".pub", "-E", "sha256"], check=True,
                        capture_output=True, text=True).stdout.split()[1]
    return pub, fp


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen không có")
def test_ssh_fingerprint_matches_openssh():
    with tempfile.TemporaryDirectory() as d:
        pub, fp = _ssh_keygen(Path(d), "ed25519")
        parsed = parse_public_key(pub)
        assert parsed.fingerprint == fp
        assert parsed.key_type == "ssh-ed25519"
        assert parsed.comment == "dev@pc"
        pub_rsa, fp_rsa = _ssh_keygen(Path(d), "rsa", 3072)
        assert parse_public_key(pub_rsa).fingerprint == fp_rsa


def test_ssh_rejects_bad_input():
    with pytest.raises(InvalidSshKey):
        parse_public_key("-----BEGIN OPENSSH PRIVATE KEY-----\nabc")
    with pytest.raises(InvalidSshKey):
        parse_public_key("ssh-dss AAAAB3NzaC1kc3MAAACBAP")
    with pytest.raises(InvalidSshKey):
        parse_public_key("ssh-ed25519 !!!notbase64")
    blob = struct.pack(">I", 7) + b"ssh-rsa" + struct.pack(">I", 32) + os.urandom(32)
    with pytest.raises(InvalidSshKey):
        parse_public_key("ssh-ed25519 " + base64.b64encode(blob).decode())
    weak = (struct.pack(">I", 7) + b"ssh-rsa" + struct.pack(">I", 3) + b"\x01\x00\x01"
            + struct.pack(">I", 129) + b"\x00" + b"\xff" * 128)
    with pytest.raises(InvalidSshKey, match="quá yếu"):
        parse_public_key("ssh-rsa " + base64.b64encode(weak).decode())


# ── permission resolution ──

def _m(**kw) -> Membership:
    base = dict(project_visibility=Visibility.PRIVATE, group_visibility=Visibility.PRIVATE)
    base.update(kw)
    return Membership(**base)


def test_project_level_resolution():
    assert project_level(_m()) == Level.NONE
    assert project_level(_m(is_admin=True)) == Level.ADMIN
    assert project_level(_m(group_role=GroupRole.DEVELOPER)) == Level.DEVELOPER
    assert project_level(_m(group_role=GroupRole.VIEWER, project_role=ProjectRole.MAINTAINER)) == Level.MAINTAINER
    assert project_level(_m(project_visibility=Visibility.INTERNAL)) == Level.NONE
    assert project_level(_m(project_visibility=Visibility.INTERNAL,
                            group_visibility=Visibility.INTERNAL)) == Level.VIEWER
    assert project_level(_m(authenticated=False, is_admin=True)) == Level.NONE
    assert project_level(_m(bound_project_viewer=True)) == Level.VIEWER
    assert group_level(False, None, Visibility.INTERNAL) == Level.VIEWER
    assert group_level(False, GroupRole.OWNER, Visibility.PRIVATE) == Level.OWNER


@pytest.mark.parametrize("level,perm,allowed", [
    (Level.VIEWER, Perm.REPO_READ, True),
    (Level.VIEWER, Perm.REPO_WRITE, False),
    (Level.VIEWER, Perm.BUILD_LOG, False),
    (Level.DEVELOPER, Perm.REPO_WRITE, True),
    (Level.DEVELOPER, Perm.BUILD_RUN, True),
    (Level.DEVELOPER, Perm.PACKAGE_YANK, False),
    (Level.DEVELOPER, Perm.PROJECT_ARCHIVE, False),
    (Level.DEVELOPER, Perm.PROJECT_DELETE, False),
    (Level.MAINTAINER, Perm.PACKAGE_YANK, True),
    (Level.MAINTAINER, Perm.PROJECT_TOKENS, True),
    (Level.MAINTAINER, Perm.PROJECT_ARCHIVE, False),
    (Level.OWNER, Perm.PROJECT_ARCHIVE, True),
    (Level.OWNER, Perm.PROJECT_DELETE, False),
    (Level.ADMIN, Perm.PROJECT_DELETE, True),
])
def test_permission_matrix(level, perm, allowed):
    assert can_project(level, perm) is allowed


def test_push_ref_rules():
    assert can_push_ref(Level.VIEWER, "refs/heads/main", "main", False)[0] is False
    assert can_push_ref(Level.DEVELOPER, "refs/heads/main", "main", False)[0] is True
    assert can_push_ref(Level.DEVELOPER, "refs/heads/main", "main", True)[0] is False
    assert can_push_ref(Level.DEVELOPER, "refs/heads/feature/x", "main", True)[0] is True
    assert can_push_ref(Level.DEVELOPER, "refs/tags/v1.0.0", "main", False)[0] is False
    assert can_push_ref(Level.MAINTAINER, "refs/tags/v1.0.0", "main", False)[0] is True
    assert can_push_ref(Level.MAINTAINER, "refs/notes/x", "main", False)[0] is False


def test_scope_expansion():
    assert "read_package" in expand_scopes(["write_package"])
    assert "read_api" in expand_scopes(["write_api"])
    assert expand_scopes(["admin"]) >= {"read_api", "write_package", "write_repository"}
    assert "write_package" not in expand_scopes(["read_package"])
