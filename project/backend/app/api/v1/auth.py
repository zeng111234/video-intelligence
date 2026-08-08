"""客户激活码登录与管理员多账号登录。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_repository
from project.backend.app.core.security import (
    get_admin_password,
    hash_password,
    issue_auth_token,
    revoke_auth_token,
    verify_password,
)
from src.models import AdminAccount
from src.repositories.sqlite import SQLiteRepository
from src.services.credits import CreditsService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class CustomerLoginRequest(BaseModel):
    code: str = Field(min_length=4, max_length=64)


class CustomerLoginResponse(BaseModel):
    token: str
    role: str = "customer"
    code: str
    name: str
    balance: str


class AdminLoginRequest(BaseModel):
    username: str = Field(default="admin", min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AdminLoginResponse(BaseModel):
    token: str
    role: str = "admin"
    username: str
    expires_in_seconds: int = 12 * 60 * 60


def _ensure_default_admin(repo: SQLiteRepository) -> None:
    """admin_accounts 为空时，用 ADMIN_PASSWORD 创建默认 admin 账号。"""
    if repo.list_admin_accounts():
        return
    default_password = get_admin_password()
    if not default_password:
        return
    from datetime import datetime

    now = datetime.now().astimezone()
    repo.create_admin_account(
        AdminAccount(
            username="admin",
            password_hash=hash_password(default_password),
            created_at=now,
            updated_at=now,
        )
    )


def admin_login_credentials(
    repo: SQLiteRepository, username: str, password: str
) -> str | None:
    """按 admin_accounts 表校验管理员账号密码；成功签发 admin token。"""
    _ensure_default_admin(repo)
    account = repo.get_admin_account(username)
    if account is None:
        return None
    valid, needs_upgrade = verify_password(password, account.password_hash)
    if not valid:
        return None
    if needs_upgrade:
        repo.set_admin_password(username, hash_password(password))
    return issue_auth_token("admin", username)


def _set_media_session_cookie(response: Response, name: str, token: str) -> None:
    """只为无自定义请求头的媒体 GET 提供会话，写操作仍使用 header token。"""
    import os

    response.set_cookie(
        key=name,
        value=token,
        max_age=12 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=os.getenv("APP_ENV", "development").lower() == "production",
        path="/api/v1/",
    )


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    """清理请求头/媒体 Cookie 中的会话，避免退出后预览仍然有效。"""
    token = (
        request.headers.get("X-Admin-Token")
        or request.headers.get("X-Customer-Token")
        or request.cookies.get("vi_admin_media_token")
        or request.cookies.get("vi_customer_media_token")
    )
    if token:
        revoke_auth_token(token)
    for name in ("vi_admin_media_token", "vi_customer_media_token"):
        response.delete_cookie(name, path="/api/v1/")
    return {"ok": True}


@router.post("/customer-login", response_model=CustomerLoginResponse)
def customer_login(
    body: CustomerLoginRequest,
    response: Response,
    repo: SQLiteRepository = Depends(get_repository),
):
    """客户用激活码登录：校验激活码，首次登录自动开立积分账户并赠送初始积分。"""
    code = body.code.strip().upper()
    customer = repo.get_customer_code(code)
    if customer is None:
        raise HTTPException(status_code=401, detail="激活码不存在，请检查后重试。")
    if not customer.enabled:
        raise HTTPException(status_code=403, detail="该激活码已被禁用，请联系管理员。")

    # 客户登录：确保账户开立（首次开立时自动赠送 initial_credits，不重复赠送）
    credits = CreditsService(repo)
    balance = credits.ensure_account(owner=code)
    token = issue_auth_token("customer", code)
    _set_media_session_cookie(response, "vi_customer_media_token", token)
    return CustomerLoginResponse(
        token=token,
        code=code,
        name=customer.name,
        balance=str(balance),
    )


@router.post("/admin-login", response_model=AdminLoginResponse)
def admin_login(
    body: AdminLoginRequest,
    response: Response,
    repo: SQLiteRepository = Depends(get_repository),
):
    """管理员用账号密码登录（多账号；首次自动用 ADMIN_PASSWORD 创建 admin）。"""
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="请输入管理员账号。")
    token = admin_login_credentials(repo, username, body.password)
    if token is None:
        raise HTTPException(status_code=401, detail="管理员账号或密码不正确。")
    _set_media_session_cookie(response, "vi_admin_media_token", token)
    return AdminLoginResponse(token=token, username=username)
