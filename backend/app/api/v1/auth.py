from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.core.config import get_settings
from app.core.deps import REFRESH_COOKIE, CurrentPrincipal, DbDep, require_user
from app.core.rate_limit import enforce
from app.schemas import ChangePasswordIn, LoginIn, Message, UserOut
from app.services import audit_service, auth_service
from app.services.audit_service import A

router = APIRouter(prefix="/auth", tags=["auth"])


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: DbDep) -> UserOut:
    enforce("login", _ip(request), get_settings().login_rate_limit_per_minute)
    user, issued = auth_service.login(db, request, body.login, body.password)
    auth_service.set_session_cookies(response, issued)
    return UserOut.model_validate(user)


@router.post("/refresh", response_model=UserOut)
def refresh(request: Request, response: Response, db: DbDep) -> UserOut:
    enforce("refresh", _ip(request), 60)
    try:
        user, issued = auth_service.refresh(db, request, request.cookies.get(REFRESH_COOKIE))
    except Exception:
        auth_service.clear_session_cookies(response)
        raise
    auth_service.set_session_cookies(response, issued)
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: DbDep) -> Response:
    auth_service.logout(db, request.cookies.get(REFRESH_COOKIE))
    response.status_code = status.HTTP_204_NO_CONTENT
    auth_service.clear_session_cookies(response)
    return response


@router.get("/me", response_model=UserOut)
def me(principal: CurrentPrincipal) -> UserOut:
    return UserOut.model_validate(require_user(principal))


@router.post("/change-password", response_model=Message)
def change_password(body: ChangePasswordIn, request: Request, response: Response, principal: CurrentPrincipal,
                    db: DbDep) -> Message:
    user = require_user(principal)
    auth_service.change_password(db, user, body.current_password, body.new_password)
    audit_service.record(db, A.CHANGE_PASSWORD, principal=principal, resource_type="user", resource_id=user.id,
                         request=request)
    db.commit()
    auth_service.clear_session_cookies(response)
    return Message(message="Đã đổi mật khẩu. Vui lòng đăng nhập lại.")
