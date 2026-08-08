"""管理员：客户激活码管理 + 管理员账号管理。"""
from __future__ import annotations

import secrets
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_repository
from project.backend.app.core.security import hash_password, require_admin_token, revoke_auth_tokens
from src.models import AdminAccount, CustomerCode
from src.repositories.sqlite import SQLiteRepository
from src.services.credits import CreditsService

router = APIRouter(prefix="/api/v1/admin", tags=["admin-customers"])

# 激活码字符集：去掉易混淆字符（0/O/1/I/L）
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


class GenerateCodesRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="客户名/备注")
    initial_credits: Decimal = Field(Decimal("400"), ge=0, description="初始赠送积分")
    count: int = Field(1, ge=1, le=50, description="生成数量")


class CustomerCodeResponse(BaseModel):
    code: str
    name: str
    enabled: bool
    initial_credits: str
    balance: str
    created_at: str


class CreateAdminRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(..., min_length=6, max_length=256)


class ResetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=6, max_length=256)


class AdminAccountResponse(BaseModel):
    username: str
    created_at: str


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _customer_response(
    code: CustomerCode, repo: SQLiteRepository
) -> CustomerCodeResponse:
    # 生成/查询时确保账户开立：余额立即可见（首次自动赠送 initial_credits）
    balance = CreditsService(repo).ensure_account(owner=code.code)
    return CustomerCodeResponse(
        code=code.code,
        name=code.name,
        enabled=code.enabled,
        initial_credits=str(code.initial_credits),
        balance=str(balance),
        created_at=code.created_at.isoformat(),
    )


@router.post("/codes/generate", response_model=list[CustomerCodeResponse])
def generate_codes(
    body: GenerateCodesRequest,
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
):
    """生成一批激活码（含初始积分）；返回生成的激活码与余额。"""
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


@router.post("/accounts", response_model=AdminAccountResponse)
def create_admin(
    body: CreateAdminRequest,
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
):
    """新增管理员账号。"""
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
    return AdminAccountResponse(username=username, created_at=now.isoformat())


@router.get("/accounts", response_model=list[AdminAccountResponse])
def list_admins(
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
):
    """管理员账号列表。"""
    return [
        AdminAccountResponse(username=a.username, created_at=a.created_at.isoformat())
        for a in repo.list_admin_accounts()
    ]


@router.post("/accounts/{username}/password", response_model=AdminAccountResponse)
def reset_admin_password(
    username: str,
    body: ResetPasswordRequest,
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
):
    """重置管理员密码。"""
    account = repo.get_admin_account(username)
    if account is None:
        raise HTTPException(status_code=404, detail="管理员账号不存在。")
    repo.set_admin_password(username, hash_password(body.password))
    revoke_auth_tokens("admin", username)
    return AdminAccountResponse(username=username, created_at=account.created_at.isoformat())


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
