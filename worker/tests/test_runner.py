from __future__ import annotations

import io
import tarfile

import pytest

from app.runners.python_runner import BuildError, BuildLog, validate_source

INFO = {"internal_prefix": "hawee-", "package_name": "hawee-demo-tool", "publish_package": True,
        "requested_version": "0.1.0", "ref_name": "v0.1.0"}


def _archive(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _pyproject(name: str = "hawee-demo-tool", version: str = "0.1.0", extra: str = "") -> str:
    return (f'[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n\n'
            f'[project]\nname = "{name}"\nversion = "{version}"\n{extra}')


def _code(files: dict[str, str], info: dict | None = None) -> str:
    with pytest.raises(BuildError) as exc:
        validate_source(_archive(files), info or INFO)
    return exc.value.code


def test_valid_release():
    src = validate_source(_archive({"pyproject.toml": _pyproject(extra='requires-python = ">=3.10"\n'),
                                    "README.md": "# x"}), INFO)
    assert (src.name, src.version, src.requires_python) == ("hawee-demo-tool", "0.1.0", ">=3.10")


def test_version_mismatch():
    assert _code({"pyproject.toml": _pyproject(version="0.2.0"), "README.md": "x"}) == "VERSION_MISMATCH"


def test_validation_build_ignores_version():
    info = {**INFO, "publish_package": False, "requested_version": None}
    assert validate_source(_archive({"pyproject.toml": _pyproject(version="9.9.9"), "README.md": "x"}), info)


def test_prefix_and_name():
    assert _code({"pyproject.toml": _pyproject(name="demo-tool"), "README.md": "x"}) == "INTERNAL_PREFIX_REQUIRED"
    assert _code({"pyproject.toml": _pyproject(name="hawee-other"), "README.md": "x"}) == "INVALID_PACKAGE_NAME"
    # chuẩn hoá tên: Hawee_Demo.Tool == hawee-demo-tool
    assert validate_source(_archive({"pyproject.toml": _pyproject(name="Hawee_Demo.Tool"), "README.md": "x"}), INFO)


def test_invalid_pyproject():
    assert _code({"README.md": "x"}) == "INVALID_PYPROJECT"
    assert _code({"pyproject.toml": _pyproject()}) == "INVALID_PYPROJECT"               # thiếu README
    assert _code({"pyproject.toml": "not [toml", "README.md": "x"}) == "INVALID_PYPROJECT"
    assert _code({"pyproject.toml": '[project]\nname="hawee-demo-tool"\nversion="0.1.0"\n',
                  "README.md": "x"}) == "INVALID_PYPROJECT"                             # thiếu build-system
    dyn = _pyproject(version="0.1.0").replace('version = "0.1.0"', 'dynamic = ["version"]')
    assert _code({"pyproject.toml": dyn, "README.md": "x"}) == "INVALID_PYPROJECT"
    assert _code({"pyproject.toml": _pyproject(version="abc"), "README.md": "x"}) == "INVALID_PYPROJECT"


def test_tag_normalization():
    info = {**INFO, "requested_version": "1.0.0-RC1", "ref_name": "v1.0.0-RC1"}
    assert validate_source(_archive({"pyproject.toml": _pyproject(version="1.0.0rc1"), "README.md": "x"}), info)


def test_build_log_scrubs_secrets():
    class FakeApi:
        def __init__(self):
            self.sent = []

        def append_log(self, _bid, text):
            self.sent.append(text)

    api = FakeApi()
    blog = BuildLog(api, "b1", ["hth_SECRET_TOKEN"])
    blog.write("Looking in indexes: http://__token__:hth_SECRET_TOKEN@registry:8081/simple\n")
    blog.flush()
    assert "hth_SECRET_TOKEN" not in blog.text and "***" in api.sent[0]
    blog.flush()
    assert len(api.sent) == 1
