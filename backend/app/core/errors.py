from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Lỗi nghiệp vụ có mã chuẩn (spec mục 42). Handler chuyển thành JSON {error:{code,message}}."""

    status_code: int = 400

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}
        self.headers = headers


class NotFound(AppError):
    def __init__(self, code: str = "NOT_FOUND", message: str = "Không tìm thấy") -> None:
        super().__init__(code, message, 404)


class PermissionDenied(AppError):
    def __init__(self, message: str = "Không có quyền thực hiện thao tác này", code: str = "PERMISSION_DENIED") -> None:
        super().__init__(code, message, 403)


class Unauthorized(AppError):
    def __init__(self, code: str = "INVALID_CREDENTIALS", message: str = "Chưa đăng nhập hoặc phiên đã hết hạn",
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(code, message, 401, headers=headers)


class Conflict(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, 409)


class BadRequest(AppError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(code, message, 400, details)


class TooManyRequests(AppError):
    def __init__(self, message: str = "Quá nhiều yêu cầu, thử lại sau", retry_after: int = 60) -> None:
        super().__init__("RATE_LIMITED", message, 429, headers={"Retry-After": str(retry_after)})


# Mã lỗi build (spec mục 42) — dùng chung với worker.
BUILD_ERROR_CODES = frozenset({
    "GIT_CLONE_FAILED",
    "INVALID_PYPROJECT",
    "INVALID_PACKAGE_NAME",
    "INTERNAL_PREFIX_REQUIRED",
    "VERSION_MISMATCH",
    "TEST_FAILED",
    "BUILD_FAILED",
    "TWINE_CHECK_FAILED",
    "INSTALL_TEST_FAILED",
    "PACKAGE_VERSION_EXISTS",
    "BUILD_TIMEOUT",
    "RUNNER_ERROR",
    "CANCELLED",
})
