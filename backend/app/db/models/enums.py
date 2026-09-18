from __future__ import annotations

import enum


class StrEnum(str, enum.Enum):
    def __str__(self) -> str:  # pragma: no cover - tiện log
        return self.value


class Visibility(StrEnum):
    PRIVATE = "PRIVATE"
    INTERNAL = "INTERNAL"


class GroupRole(StrEnum):
    VIEWER = "VIEWER"
    DEVELOPER = "DEVELOPER"
    MAINTAINER = "MAINTAINER"
    OWNER = "OWNER"


class ProjectRole(StrEnum):
    VIEWER = "VIEWER"
    DEVELOPER = "DEVELOPER"
    MAINTAINER = "MAINTAINER"


class ProjectLanguage(StrEnum):
    PYTHON = "PYTHON"
    GENERIC = "GENERIC"


class TokenType(StrEnum):
    PERSONAL = "PERSONAL"
    SERVICE = "SERVICE"
    RUNNER = "RUNNER"


class TriggerType(StrEnum):
    PUSH = "PUSH"
    TAG = "TAG"
    MANUAL = "MANUAL"


class BuildStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PackageType(StrEnum):
    PYPI = "PYPI"  # npm / nuget / maven thêm sau — cột varchar nên không cần migrate enum


class PackageFileType(StrEnum):
    WHEEL = "WHEEL"
    SDIST = "SDIST"


class UpstreamSource(StrEnum):
    PYPI = "PYPI"


class ActorType(StrEnum):
    USER = "USER"
    SERVICE = "SERVICE"
    SYSTEM = "SYSTEM"


class RunnerStatus(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DRAINING = "DRAINING"


FINISHED_BUILD_STATUSES = frozenset({BuildStatus.SUCCESS, BuildStatus.FAILED, BuildStatus.CANCELLED})
