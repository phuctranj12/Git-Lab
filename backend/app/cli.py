"""CLI vận hành: python -m app.cli <lệnh>

  bootstrap       Lần đầu deploy / mỗi lần nâng cấp (idempotent) — spec mục 35.
  migrate         alembic upgrade head.
  create-user     Tạo user (dùng khi cần cứu hộ).
  reset-password  Đặt lại mật khẩu user.
  check           In kết quả readiness.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from pathlib import Path

from sqlalchemy import func, or_, select, text


def _print(step: str, ok: bool, detail: str = "") -> None:
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {step}{(' — ' + detail) if detail else ''}", flush=True)


def wait_for_db(timeout: int = 90) -> None:
    from app.db.session import get_engine

    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2)
    raise RuntimeError(f"PostgreSQL không sẵn sàng sau {timeout}s: {last}")


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
    command.upgrade(cfg, "head")


def bootstrap(args: argparse.Namespace) -> int:
    from app.core.config import get_settings
    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_factory
    from app.services import git_service, storage_service, token_service
    from app.services.health_service import readiness

    s = get_settings()
    problems = s.validate_for_runtime()
    if not s.runner_token:
        problems.append("RUNNER_TOKEN chưa đặt (worker không gọi được API)")
    _print("1. Validate env", not problems, "; ".join(problems))
    if problems:
        return 1

    wait_for_db()
    _print("2. PostgreSQL sẵn sàng", True)

    migrate()
    _print("3. Migration (alembic upgrade head)", True)

    created = storage_service.ensure_buckets()
    _print("4. MinIO buckets", True, f"tạo mới: {', '.join(created) or 'không (đã có đủ)'}")

    with session_factory()() as db:
        existing = db.scalar(select(User).where(or_(func.lower(User.username) == s.first_admin_username.lower(),
                                                    func.lower(User.email) == s.first_admin_email.lower())))
        if existing is None:
            password = s.first_admin_password or args.admin_password
            if not password:
                _print("5. Admin", False, "chưa có admin và FIRST_ADMIN_PASSWORD trống")
                return 1
            if len(password) < 8:
                _print("5. Admin", False, "FIRST_ADMIN_PASSWORD phải >= 8 ký tự")
                return 1
            db.add(User(username=s.first_admin_username.lower(), email=s.first_admin_email.lower(),
                        full_name="System Administrator", password_hash=hash_password(password),
                        is_system_admin=True))
            db.commit()
            _print("5. Admin", True, f"đã tạo '{s.first_admin_username}'")
        else:
            _print("5. Admin", True, f"đã tồn tại '{existing.username}' (không đổi mật khẩu)")

        token_service.register_runner_token(db, s.runner_token)
        db.commit()
        _print("6. System runner token", True, "đã đăng ký (hash) trong DB")

    git_service.check_storage_writable()
    _print("7. Git storage ghi được", True, s.git_repository_root)

    result = readiness()
    for c in result["checks"]:
        _print(f"8. Health: {c['name']}", c["ok"], c.get("error", ""))
    return 0 if result["ready"] else 1


def create_user(args: argparse.Namespace) -> int:
    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_factory

    password = args.password or getpass.getpass("Mật khẩu: ")
    with session_factory()() as db:
        db.add(User(username=args.username.lower(), email=args.email.lower(), full_name=args.full_name,
                    password_hash=hash_password(password), is_system_admin=args.admin))
        db.commit()
    print(f"Đã tạo user {args.username}")
    return 0


def reset_password(args: argparse.Namespace) -> int:
    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_factory
    from app.services.auth_service import revoke_all_refresh

    password = args.password or getpass.getpass("Mật khẩu mới: ")
    with session_factory()() as db:
        user = db.scalar(select(User).where(func.lower(User.username) == args.username.lower()))
        if user is None:
            print("Không tìm thấy user", file=sys.stderr)
            return 1
        user.password_hash = hash_password(password)
        user.is_active = True
        revoke_all_refresh(db, user.id)
        db.commit()
    print(f"Đã đặt lại mật khẩu cho {args.username}")
    return 0


def check(_: argparse.Namespace) -> int:
    from app.services.health_service import readiness

    result = readiness()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ready"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap")
    b.add_argument("--admin-password", default="")
    b.set_defaults(fn=bootstrap)
    sub.add_parser("migrate").set_defaults(fn=lambda _a: (wait_for_db(), migrate(), 0)[-1])
    c = sub.add_parser("create-user")
    c.add_argument("username")
    c.add_argument("email")
    c.add_argument("--full-name", default=None)
    c.add_argument("--password", default="")
    c.add_argument("--admin", action="store_true")
    c.set_defaults(fn=create_user)
    r = sub.add_parser("reset-password")
    r.add_argument("username")
    r.add_argument("--password", default="")
    r.set_defaults(fn=reset_password)
    sub.add_parser("check").set_defaults(fn=check)
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
