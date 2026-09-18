"""Đọc metadata từ chính file .whl / .tar.gz — không tin metadata client gửi (spec 29.5)."""
from __future__ import annotations

import re
import tarfile
import zipfile
from dataclasses import dataclass, field
from email.parser import HeaderParser
from pathlib import PurePosixPath
from typing import IO

from packaging.utils import InvalidSdistFilename, InvalidWheelFilename, parse_sdist_filename, parse_wheel_filename

from app.db.models.enums import PackageFileType

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*$")
MAX_METADATA_BYTES = 2 * 1024 * 1024


class InvalidPackageFile(ValueError):
    pass


@dataclass
class PackageMetadata:
    name: str
    version: str
    summary: str | None = None
    requires_python: str | None = None
    requires_dist: list[str] = field(default_factory=list)
    author: str | None = None
    license: str | None = None
    home_page: str | None = None
    description: str | None = None
    description_content_type: str | None = None

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "summary": self.summary,
            "requires_python": self.requires_python,
            "requires_dist": self.requires_dist,
            "author": self.author,
            "license": self.license,
            "home_page": self.home_page,
        }


@dataclass
class FilenameInfo:
    file_type: PackageFileType
    name: str  # canonical
    version: str


def classify_filename(filename: str) -> FilenameInfo:
    if "/" in filename or "\\" in filename or not _SAFE_FILENAME.match(filename) or len(filename) > 300:
        raise InvalidPackageFile(f"Tên file không hợp lệ: {filename!r}")
    if filename.endswith(".whl"):
        try:
            name, version, _build, _tags = parse_wheel_filename(filename)
        except InvalidWheelFilename as exc:
            raise InvalidPackageFile(str(exc)) from exc
        return FilenameInfo(PackageFileType.WHEEL, str(name), str(version))
    if filename.endswith(".tar.gz"):
        try:
            name, version = parse_sdist_filename(filename)
        except InvalidSdistFilename as exc:
            raise InvalidPackageFile(str(exc)) from exc
        return FilenameInfo(PackageFileType.SDIST, str(name), str(version))
    raise InvalidPackageFile("Chỉ chấp nhận .whl hoặc .tar.gz")


def _parse_metadata(raw: bytes) -> PackageMetadata:
    msg = HeaderParser().parsestr(raw.decode("utf-8", "replace"))
    name, version = msg.get("Name"), msg.get("Version")
    if not name or not version:
        raise InvalidPackageFile("METADATA thiếu Name/Version")
    body = msg.get_payload()
    description = msg.get("Description") or (body if isinstance(body, str) and body.strip() else None)
    return PackageMetadata(
        name=name.strip(),
        version=version.strip(),
        summary=msg.get("Summary"),
        requires_python=msg.get("Requires-Python"),
        requires_dist=[v for v in (msg.get_all("Requires-Dist") or [])],
        author=msg.get("Author") or msg.get("Author-email"),
        license=msg.get("License-Expression") or msg.get("License"),
        home_page=msg.get("Home-page"),
        description=description,
        description_content_type=msg.get("Description-Content-Type"),
    )


def read_wheel_metadata(fileobj: IO[bytes]) -> PackageMetadata:
    try:
        with zipfile.ZipFile(fileobj) as zf:
            candidates = [n for n in zf.namelist()
                          if n.count("/") == 1 and n.endswith(".dist-info/METADATA")]
            if len(candidates) != 1:
                raise InvalidPackageFile("Wheel phải có đúng một *.dist-info/METADATA")
            info = zf.getinfo(candidates[0])
            if info.file_size > MAX_METADATA_BYTES:
                raise InvalidPackageFile("METADATA quá lớn")
            return _parse_metadata(zf.read(info))
    except zipfile.BadZipFile as exc:
        raise InvalidPackageFile("File .whl không phải zip hợp lệ") from exc


def read_sdist_metadata(fileobj: IO[bytes]) -> PackageMetadata:
    try:
        with tarfile.open(fileobj=fileobj, mode="r:gz") as tf:
            for member in tf:
                p = PurePosixPath(member.name)
                if len(p.parts) == 2 and p.name == "PKG-INFO" and member.isfile():
                    if member.size > MAX_METADATA_BYTES:
                        raise InvalidPackageFile("PKG-INFO quá lớn")
                    f = tf.extractfile(member)
                    if f is None:
                        break
                    return _parse_metadata(f.read())
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise InvalidPackageFile("File .tar.gz không hợp lệ") from exc
    raise InvalidPackageFile("sdist thiếu <name>-<version>/PKG-INFO")


def read_metadata(file_type: PackageFileType, fileobj: IO[bytes]) -> PackageMetadata:
    fileobj.seek(0)
    try:
        if file_type == PackageFileType.WHEEL:
            return read_wheel_metadata(fileobj)
        return read_sdist_metadata(fileobj)
    finally:
        fileobj.seek(0)
