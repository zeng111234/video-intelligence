"""积分账户服务：1 元 = 1 积分，向上取整到 2 位小数。

余额与流水持久化在 SQLite（credit_accounts / credit_transactions），
扣费通过仓库层 `adjust_credit_balance` 在单事务内完成（检查余额→扣减→记流水），
天然防并发超扣。
"""

from __future__ import annotations

from contextvars import ContextVar
from decimal import Decimal, ROUND_CEILING

CREDITS_PER_CNY = Decimal("1")

# 当前请求的积分账户归属（客户激活码或 'admin'）。由认证中间件在请求
# 开始时设置；Starlette 同步端点经 anyio 线程池执行时会复制调用方上下文，
# 因此服务层扣费可读取到当前客户。
_current_owner: ContextVar[str] = ContextVar("credit_owner", default="admin")


def set_current_owner(owner: str) -> None:
    """设置当前请求的积分账户归属（客户激活码或 'admin'）。"""
    _current_owner.set(owner)


def get_current_owner() -> str:
    """读取当前请求的积分账户归属。"""
    return _current_owner.get()


class InsufficientCreditsError(Exception):
    """积分不足，操作被阻止。"""

    def __init__(
        self,
        message: str = "积分不足，请先充值",
        *,
        balance: Decimal | None = None,
        required: Decimal | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.balance = balance
        self.required = required


def cny_to_credits(cny: Decimal | float | str) -> Decimal:
    """人民币金额 → 积分：1 元 = 1 积分，向上取整到 2 位小数。

    例如 0.082 元 → 0.09 积分；2.25 元 → 2.25 积分。
    """
    amount = Decimal(str(cny))
    if amount <= 0:
        return Decimal("0")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


class CreditsService:
    """积分账户读写与流水查询。"""

    def __init__(self, repository) -> None:
        self.repository = repository

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_balance(self, owner: str | None = None) -> Decimal:
        return self.repository.get_credit_balance(owner or get_current_owner())

    def ensure_account(self, owner: str | None = None) -> Decimal:
        """确保账户已开立（首次自动赠送初始积分），返回余额。"""
        return self.repository.ensure_credit_account(owner or get_current_owner())

    def list_transactions(self, owner: str | None = None, limit: int = 100) -> list[dict]:
        return self.repository.list_credit_transactions(
            owner=owner or get_current_owner(), limit=limit
        )

    # ------------------------------------------------------------------
    # 充值 / 扣费
    # ------------------------------------------------------------------

    def credit(
        self,
        amount: Decimal | float | str,
        reason: str,
        *,
        owner: str | None = None,
        ref_type: str | None = None,
        ref_id: str | None = None,
    ) -> Decimal:
        """加积分（管理员充值/赠送）。amount 必须为正。"""
        value = Decimal(str(amount))
        if value <= 0:
            raise ValueError("充值积分必须大于 0")
        return self.repository.adjust_credit_balance(
            amount=value,
            reason=reason,
            owner=owner or get_current_owner(),
            ref_type=ref_type,
            ref_id=ref_id,
        )

    def debit(
        self,
        amount: Decimal | float | str,
        reason: str,
        *,
        owner: str | None = None,
        ref_type: str | None = None,
        ref_id: str | None = None,
    ) -> Decimal:
        """扣积分（服务计费）。余额不足时抛出 InsufficientCreditsError。

        管理员（owner=admin）免费：直接返回当前余额，不扣费不记流水。
        客户（激活码）按价扣费。
        """
        value = Decimal(str(amount))
        if value <= 0:
            raise ValueError("扣费积分必须大于 0")
        effective_owner = owner or get_current_owner()
        if effective_owner == "admin":
            return self.repository.get_credit_balance("admin")
        try:
            return self.repository.adjust_credit_balance(
                amount=-value,
                reason=reason,
                owner=effective_owner,
                ref_type=ref_type,
                ref_id=ref_id,
            )
        except ValueError as exc:
            raise InsufficientCreditsError(
                balance=self.get_balance(effective_owner),
                required=value,
            ) from exc

    def can_afford(self, amount: Decimal | float | str, owner: str | None = None) -> bool:
        """余额是否足够支付指定积分。"""
        return self.get_balance(owner) >= Decimal(str(amount))
