from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_PLACEHOLDER = "CHANGE_ME"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # ── App ──
    app_env: str = "development"
    app_name: str = "HAWEE Tool Hub"
    log_level: str = "INFO"

    web_base_url: str = "http://localhost:8080"
    package_base_url: str = "http://localhost:8081"
    git_ssh_host: str = "localhost"
    git_ssh_port: int = 2222
    git_ssh_user: str = "git"

    # ── Infra ──
    database_url: str = "postgresql+psycopg://toolhub:toolhub@localhost:5432/toolhub"
    redis_url: str = "redis://localhost:6379/0"

    # ── Secrets ──
    jwt_secret: str = DEV_PLACEHOLDER
    internal_service_secret: str = DEV_PLACEHOLDER
    runner_token: str = ""

    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    # ── MinIO ──
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "toolhub"
    minio_secret_key: str = "toolhub-secret"
    minio_secure: bool = False
    minio_bucket_packages: str = "toolhub-packages"
    minio_bucket_logs: str = "toolhub-build-logs"
    minio_bucket_artifacts: str = "toolhub-artifacts"
    minio_bucket_backups: str = "toolhub-backups"

    # ── Git ──
    git_repository_root: str = "/data/git/repositories"
    git_binary: str = "git"
    git_hook_dir: str = "/opt/toolhub/hooks"

    # ── Registry ──
    internal_package_prefix: str = "hawee-"
    pypi_upstream_simple_url: str = "https://pypi.org/simple"
    pypi_upstream_file_hosts: str = "files.pythonhosted.org"
    pypi_metadata_ttl_seconds: int = 900
    pypi_upstream_timeout_seconds: float = 20.0
    registry_allow_anonymous: bool = False
    package_max_upload_mb: int = 500
    upstream_cache_max_idle_days: int = 180
    upstream_cache_max_bytes: int = 200 * 1024**3

    # ── Build ──
    build_max_concurrency: int = 2
    build_timeout_seconds: int = 900
    build_memory_limit: str = "4g"
    build_cpu_limit: float = 2.0
    build_log_retention_days: int = 180
    build_stale_queue_minutes: int = 10

    # ── Rate limit ──
    login_rate_limit_per_minute: int = 5
    api_rate_limit_per_minute: int = 600
    registry_rate_limit_per_minute: int = 6000

    # ── Bootstrap ──
    first_admin_username: str = "admin"
    first_admin_email: str = "admin@hawee.local"
    first_admin_password: str = ""

    @field_validator("internal_package_prefix")
    @classmethod
    def _prefix_lower(cls, v: str) -> str:
        v = v.strip().lower()
        if not v.endswith("-"):
            raise ValueError("INTERNAL_PACKAGE_PREFIX phải kết thúc bằng '-'")
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod", "staging"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def upstream_file_host_list(self) -> list[str]:
        return [h.strip().lower() for h in self.pypi_upstream_file_hosts.split(",") if h.strip()]

    @property
    def package_max_upload_bytes(self) -> int:
        return self.package_max_upload_mb * 1024 * 1024

    def validate_for_runtime(self) -> list[str]:
        """Trả về danh sách lỗi cấu hình nghiêm trọng (rỗng = OK)."""
        problems: list[str] = []
        if self.is_production:
            for name in ("jwt_secret", "internal_service_secret"):
                value = getattr(self, name)
                if value == DEV_PLACEHOLDER or len(value) < 32:
                    problems.append(f"{name.upper()} phải được đặt (>= 32 ký tự) ở production")
            if self.runner_token and len(self.runner_token) < 32:
                problems.append("RUNNER_TOKEN phải >= 32 ký tự")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()

