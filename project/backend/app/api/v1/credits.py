"""积分账户管理 API：查询余额与流水、管理员加/减积分、充值请求审批。"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_credits_service, get_repository
from project.backend.app.core.security import (
    admin_password_configured,
    require_admin_token,
    require_customer_token,
)
from project.backend.app.api.v1.auth import admin_login_credentials
from src.repositories.sqlite import SQLiteRepository
from src.services.credits import CreditsService, InsufficientCreditsError

router = APIRouter(prefix="/api/v1/credits", tags=["credits"])


class CreditsAdjustRequest(BaseModel):
    amount: Decimal = Field(..., description="积分变动量，正数=充值，负数=扣减")
    reason: str = Field(..., min_length=1, max_length=200, description="变动原因")
    owner: str = Field("admin", description="积分账户：admin 或客户激活码")
    ref_type: str | None = Field(None, max_length=50, description="关联业务类型")
    ref_id: str | None = Field(None, max_length=100, description="关联业务 ID")


class AdminLoginRequest(BaseModel):
    username: str = Field("admin", min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=200)


class CreditsTransactionResponse(BaseModel):
    id: int
    amount: str
    balance_after: str
    reason: str
    ref_type: str | None = None
    ref_id: str | None = None
    created_at: str


class CreditsBalanceResponse(BaseModel):
    balance: str
    transactions: list[CreditsTransactionResponse]


class AdminLoginResponse(BaseModel):
    token: str
    expires_in_seconds: int


# ---- 充值请求相关 ----

class RechargeRequestCreate(BaseModel):
    amount: Decimal = Field(..., gt=0, description="充值金额（积分）")
    reason: str = Field("", max_length=200, description="充值原因")


class RechargeRequestReview(BaseModel):
    status: str = Field(..., pattern="^(approved|rejected)$", description="审批结果")
    review_note: str = Field("", max_length=200, description="审批备注")


class RechargeRequestResponse(BaseModel):
    id: int
    customer_code: str
    amount: str
    reason: str | None
    status: str
    created_at: str
    updated_at: str
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None


@router.post("/login", response_model=AdminLoginResponse)
def admin_login(
    body: AdminLoginRequest,
    repo: SQLiteRepository = Depends(get_repository),
) -> AdminLoginResponse:
    """管理员登录：账号+密码（兼容旧单密码模式，username 默认 admin）。"""
    if not admin_password_configured() and not repo.list_admin_accounts():
        raise HTTPException(
            status_code=503,
            detail="管理员密码尚未设置，请在 .env 中配置 ADMIN_PASSWORD。",
        )
    username = body.username.strip() or "admin"
    token = admin_login_credentials(repo, username, body.password)
    if token is None:
        raise HTTPException(status_code=401, detail="管理密码不正确。")
    return AdminLoginResponse(
        token=token,
        expires_in_seconds=12 * 60 * 60,
    )


@router.get("", response_model=CreditsBalanceResponse)
def get_credits_balance(
    credits: CreditsService = Depends(get_credits_service),
) -> CreditsBalanceResponse:
    """查询当前积分余额与最近流水（客户看自己的，管理员看 admin 账户）。"""
    return CreditsBalanceResponse(
        balance=str(credits.get_balance()),
        transactions=[
            CreditsTransactionResponse(**row)
            for row in credits.list_transactions(limit=100)
        ],
    )


@router.post("/adjust", response_model=CreditsBalanceResponse)
def adjust_credits(
    body: CreditsAdjustRequest,
    credits: CreditsService = Depends(get_credits_service),
    _admin: bool = Security(require_admin_token),
) -> CreditsBalanceResponse:
    """管理员加/减积分（需要管理员登录 token）；可指定 owner 给客户充值。"""
    owner = body.owner.strip() or "admin"
    try:
        if body.amount > 0:
            credits.credit(
                body.amount,
                body.reason,
                owner=owner,
                ref_type=body.ref_type,
                ref_id=body.ref_id,
            )
        else:
            credits.debit(
                abs(body.amount),
                body.reason,
                owner=owner,
                ref_type=body.ref_type,
                ref_id=body.ref_id,
            )
    except InsufficientCreditsError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CreditsBalanceResponse(
        balance=str(credits.get_balance(owner)),
        transactions=[
            CreditsTransactionResponse(**row)
            for row in credits.list_transactions(owner, limit=100)
        ],
    )


# ---- 充值请求（客户提交，管理员审批） ----

@router.post("/recharge-request", response_model=RechargeRequestResponse)
def create_recharge_request(
    body: RechargeRequestCreate,
    repo: SQLiteRepository = Depends(get_repository),
    customer_code: str = Security(require_customer_token),
) -> RechargeRequestResponse:
    """客户提交充值请求（无需管理员权限）。"""
    result = repo.create_recharge_request(
        customer_code=customer_code,
        amount=body.amount,
        reason=body.reason,
    )
    return RechargeRequestResponse(**result)


@router.get("/recharge-requests", response_model=list[RechargeRequestResponse])
def list_recharge_requests(
    status: str | None = None,
    customer_code: str | None = None,
    repo: SQLiteRepository = Depends(get_repository),
    _admin: bool = Security(require_admin_token),
) -> list[RechargeRequestResponse]:
    """管理员查看充值请求列表。"""
    rows = repo.list_recharge_requests(status=status, customer_code=customer_code)
    return [RechargeRequestResponse(**row) for row in rows]


@router.get("/recharge-requests/mine", response_model=list[RechargeRequestResponse])
def list_my_recharge_requests(
    repo: SQLiteRepository = Depends(get_repository),
    customer_code: str = Security(require_customer_token),
) -> list[RechargeRequestResponse]:
    """客户查看自己的充值请求。"""
    rows = repo.list_recharge_requests(customer_code=customer_code)
    return [RechargeRequestResponse(**row) for row in rows]


@router.post("/recharge-requests/{request_id}/review", response_model=RechargeRequestResponse)
def review_recharge_request(
    request_id: int,
    body: RechargeRequestReview,
    repo: SQLiteRepository = Depends(get_repository),
    admin_user: str = Security(require_admin_token),
) -> RechargeRequestResponse:
    """管理员审批充值请求（批准后自动充值）。"""
    existing = repo.get_recharge_request(request_id)
    if not existing:
        raise HTTPException(status_code=404, detail="充值请求不存在")
    updated = repo.review_recharge_request_and_credit(
        request_id=request_id,
        status=body.status,
        reviewed_by=admin_user,
        review_note=body.review_note,
    )
    if not updated:
        raise HTTPException(status_code=400, detail="该请求已处理")
    return RechargeRequestResponse(**updated)
