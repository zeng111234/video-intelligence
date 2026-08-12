"""管理员：客户激活码管理 + 管理员账号管理。"""

from __future__ import annotations

import secrets
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from pydantic import BaseModel, Field

from project.backend.app.core.repository import get_repository
from project.backend.app.core.security import (
    hash_password,
    require_admin_token,
    revoke_auth_tokens,
)
from src.models import AdminAccount, CustomerCode
from src.repositories.sqlite import SQLiteRepository
from src.services.credits import CreditsService

router = APIRouter(prefix="/api/v1/admin", tags=["admin-customers"])

# 激活码字符集：去掉易混淆字符（0/O/1/I/L）。16 个随机字符约 80 位熵，
# 用四段显示便于人工输入；既有 8 位激活码仍可继续登录。
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_RANDOM_LENGTH = 16
CODE_GROUP_LENGTH = 4


class GenerateCodesRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="客户名/备注")
    initial_credits: Decimal = Field(
        Decimal("9.9"), ge=0, description="套餐内含的首次可用积分"
    )
    valid_days: int | None = Field(7, ge=1, le=3650, description="首次激活后的可用天数")
    package_price_credits: Decimal = Field(
        Decimal("9.9"), ge=0, description="该使用套餐的售价积分"
    )
    count: int = Field(1, ge=1, le=50, description="生成数量")


class CustomerCodeResponse(BaseModel):
    code: str
    name: str
    enabled: bool
    initial_credits: str
    balance: str
    valid_days: int | None
    package_price_credits: str
    activated_at: str | None
    access_expires_at: str | None
    access_status: str
    created_at: str


class ExtendCodeAccessRequest(BaseModel):
    days: int = Field(..., ge=1, le=3650, description="增加的使用天数")
    package_price_credits: Decimal = Field(
        ..., ge=0, description="本次续期套餐售价积分"
    )


class CreateAdminRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(..., min_length=12, max_length=256)


class ResetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=12, max_length=256)


class AdminAccountResponse(BaseModel):
    username: str
    created_at: str
    is_current: bool = False
    can_reset_password: bool = False
    can_delete: bool = False


class DeleteAdminAccountResponse(BaseModel):
    username: str
    deleted: bool


def _current_admin(request: Request) -> str:
    username = str(getattr(request.state, "admin_username", "")).strip()
    if not username:
        raise HTTPException(status_code=403, detail="无法确认当前管理员账号。")
    return username


def _is_primary_admin(username: str) -> bool:
    return username.casefold() == "admin"


def _admin_response(
    account: AdminAccount, *, current_username: str
) -> AdminAccountResponse:
    is_current = account.username == current_username
    is_primary = _is_primary_admin(current_username)
    return AdminAccountResponse(
        username=account.username,
        created_at=account.created_at.isoformat(),
        is_current=is_current,
        can_reset_password=is_current or is_primary,
        can_delete=(
            is_primary and not is_current and not _is_primary_admin(account.username)
        ),
    )


def _generate_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_RANDOM_LENGTH))
    return "-".join(
        raw[index : index + CODE_GROUP_LENGTH]
        for index in range(0, len(raw), CODE_GROUP_LENGTH)
    )


def _customer_response(
    code: CustomerCode, repo: SQLiteRepository
) -> CustomerCodeResponse:
    # 生成/查询时确保账户开立：余额立即可见（首次自动赠送 initial_credits）
    balance = CreditsService(repo).ensure_account(owner=code.code)
    now = datetime.now().astimezone()
    if not code.enabled:
        access_status = "disabled"
    elif code.valid_days is None:
        access_status = "lifetime"
    elif code.activated_at is None:
        access_status = "unused"
    elif code.access_expires_at and code.access_expires_at <= now:
        access_status = "expired"
    else:
        access_status = "active"
    return CustomerCodeResponse(
        code=code.code,
        name=code.name,
        enabled=code.enabled,
        initial_credits=str(code.initial_credits),
        balance=str(balance),
        valid_days=code.valid_days,
        package_price_credits=str(code.package_price_credits),
        activated_at=code.activated_at.isoformat() if code.activated_at else None,
        access_expires_at=code.access_expires_at.isoformat()
        if code.access_expires_at
        else None,
        access_status=access_status,
        created_at=code.created_at.isoformat(),
    )


@router.post("/codes/generate", response_model=list[CustomerCodeResponse])
def generate_codes(
    body: GenerateCodesRequest,
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
):
    """生成一批带使用期和独立内容积分余额的激活码。"""
    now = datetime.now().astimezone()
    codes: list[CustomerCode] = []
    existing = {c.code for c in repo.list_customer_codes()}
    while len(codes) < body.count:
        candidate = _generate_code()
        if candidate in existing:
            continue
        existing.add(candidate)
        codes.append(
            CustomerCode(
                code=candidate,
                name=body.name.strip(),
                initial_credits=body.initial_credits,
                valid_days=body.valid_days,
                package_price_credits=body.package_price_credits,
                created_at=now,
                updated_at=now,
            )
        )
    repo.create_customer_codes(codes)
    return [_customer_response(code, repo) for code in codes]


@router.get("/codes", response_model=list[CustomerCodeResponse])
def list_codes(
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
):
    """客户激活码列表（含余额）。"""
    return [_customer_response(code, repo) for code in repo.list_customer_codes()]


@router.post("/codes/{code}/toggle", response_model=CustomerCodeResponse)
def toggle_code(
    code: str,
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
):
    """启用/禁用激活码（禁用后客户无法登录）。"""
    customer = repo.get_customer_code(code)
    if customer is None:
        raise HTTPException(status_code=404, detail="激活码不存在。")
    repo.set_customer_code_enabled(code, not customer.enabled)
    if customer.enabled:
        revoke_auth_tokens("customer", customer.code)
    updated = repo.get_customer_code(code)
    return _customer_response(updated, repo)


@router.post("/codes/{code}/extend", response_model=CustomerCodeResponse)
def extend_code_access(
    code: str,
    body: ExtendCodeAccessRequest,
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
):
    """管理员确认收款后延长使用期；不会自动增加或扣除积分。"""
    customer = repo.get_customer_code(code)
    if customer is None:
        raise HTTPException(status_code=404, detail="激活码不存在。")
    if customer.valid_days is None:
        raise HTTPException(status_code=400, detail="长期激活码无需续期。")
    updated = repo.extend_customer_code_access(
        customer.code,
        body.days,
        body.package_price_credits,
        datetime.now().astimezone(),
    )
    assert updated is not None
    revoke_auth_tokens("customer", customer.code)
    return _customer_response(updated, repo)


@router.post("/accounts", response_model=AdminAccountResponse)
def create_admin(
    body: CreateAdminRequest,
    request: Request,
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
):
    """新增管理员账号。"""
    if not _is_primary_admin(_current_admin(request)):
        raise HTTPException(status_code=403, detail="只有主管理员可以新增管理员账号。")
    username = body.username.strip()
    if repo.get_admin_account(username) is not None:
        raise HTTPException(status_code=400, detail="该管理员账号已存在。")
    now = datetime.now().astimezone()
    repo.create_admin_account(
        AdminAccount(
            username=username,
            password_hash=hash_password(body.password),
            created_at=now,
            updated_at=now,
        )
    )
    account = repo.get_admin_account(username)
    assert account is not None
    return _admin_response(account, current_username=_current_admin(request))


@router.get("/accounts", response_model=list[AdminAccountResponse])
def list_admins(
    request: Request,
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
):
    """管理员账号列表。"""
    current_username = _current_admin(request)
    return [
        _admin_response(a, current_username=current_username)
        for a in repo.list_admin_accounts()
    ]


@router.post("/accounts/{username}/password", response_model=AdminAccountResponse)
def reset_admin_password(
    username: str,
    body: ResetPasswordRequest,
    request: Request,
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
):
    """重置管理员密码。"""
    current_username = _current_admin(request)
    if username != current_username and not _is_primary_admin(current_username):
        raise HTTPException(status_code=403, detail="普通管理员只能修改自己的密码。")
    account = repo.get_admin_account(username)
    if account is None:
        raise HTTPException(status_code=404, detail="管理员账号不存在。")
    repo.set_admin_password(username, hash_password(body.password))
    revoke_auth_tokens("admin", username)
    return _admin_response(account, current_username=current_username)


@router.delete("/accounts/{username}", response_model=DeleteAdminAccountResponse)
def delete_admin(
    username: str,
    request: Request,
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
):
    """主管理员删除不再使用的其他管理员账号。"""
    current_username = _current_admin(request)
    if not _is_primary_admin(current_username):
        raise HTTPException(status_code=403, detail="只有主管理员可以删除管理员账号。")
    if username == current_username or _is_primary_admin(username):
        raise HTTPException(status_code=400, detail="主管理员账号不能删除。")
    if len(repo.list_admin_accounts()) <= 1:
        raise HTTPException(status_code=400, detail="系统必须至少保留一个管理员账号。")
    if repo.get_admin_account(username) is None:
        raise HTTPException(status_code=404, detail="管理员账号不存在。")
    if not repo.delete_admin_account(username):
        raise HTTPException(status_code=409, detail="账号状态已变化，请刷新后重试。")
    revoke_auth_tokens("admin", username)
    return DeleteAdminAccountResponse(username=username, deleted=True)


# ---------------------------------------------------------------------------
# 定价设置：管理员可调全部收费价格，改价即时生效
# ---------------------------------------------------------------------------


class PricingItemResponse(BaseModel):
    key: str
    label: str
    default: str
    value: str
    overridden: bool
    updated_at: str | None = None


class UpdatePricingRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=64)
    value: str = Field(..., min_length=1, max_length=32)


@router.get("/pricing", response_model=list[PricingItemResponse])
def list_pricing(
    _admin: bool = Security(require_admin_token),
):
    """返回全部可调价格（默认值 + 当前生效值）。"""
    from src.services.pricing import list_prices

    return [PricingItemResponse(**item) for item in list_prices()]


@router.put("/pricing", response_model=PricingItemResponse)
def update_pricing(
    body: UpdatePricingRequest,
    _admin: bool = Security(require_admin_token),
):
    """调整一项价格，改完即时生效（客户按新价扣费）。"""
    from src.services.pricing import list_prices, set_price

    try:
        set_price(body.key, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    item = next((p for p in list_prices() if p["key"] == body.key), None)
    if item is None:
        raise HTTPException(status_code=404, detail="未知定价项。")
    return PricingItemResponse(**item)
