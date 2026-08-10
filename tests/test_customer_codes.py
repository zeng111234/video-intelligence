"""客户激活码 + 多管理员 + 多账户积分测试。

验证：
- 旧版单账户表自动迁移为 owner='admin'
- 客户激活码 CRUD 与启用/禁用
- 管理员多账号 CRUD 与密码更新
- 积分多账户隔离（客户之间、客户与管理员互不影响）
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from src.models import AdminAccount, CustomerCode
from src.repositories.sqlite import SQLiteRepository
from src.services.credits import CreditsService


def test_legacy_single_account_migrates_to_admin(tmp_path):
    """旧版单账户表（id=1, 余额 400）打开后迁移为 owner='admin'。"""
    db_path = tmp_path / "legacy-credits.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE credit_accounts (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            balance TEXT NOT NULL DEFAULT '0',
            updated_at TEXT NOT NULL
        );
        INSERT INTO credit_accounts(id, balance, updated_at)
        VALUES (1, '400', '2026-08-07T10:00:00+08:00');
        """
    )
    conn.commit()
    conn.close()

    repo = SQLiteRepository(db_path)
    assert repo.get_credit_balance("admin") == Decimal("400")
    # 新客户账户独立，不受 admin 余额影响
    assert repo.get_credit_balance("CUSTOMER1") == Decimal("0")


def test_customer_accounts_are_isolated(tmp_path):
    """客户 A/B 积分互不影响，且与管理员账户隔离。"""
    repo = SQLiteRepository(tmp_path / "multi-owner.db")
    service = CreditsService(repo)
    service.credit("100", "客户A充值", owner="CUSTOMER-A")
    service.credit("50", "客户B充值", owner="CUSTOMER-B")
    service.credit("10", "管理员充值", owner="admin")
    assert service.get_balance("CUSTOMER-A") == Decimal("100")
    assert service.get_balance("CUSTOMER-B") == Decimal("50")
    assert service.get_balance("admin") == Decimal("100009")  # 99999 默认 + 10
    # 客户 A 扣费不影响 B
    service.debit("30", "数字人费用", owner="CUSTOMER-A")
    assert service.get_balance("CUSTOMER-A") == Decimal("70")
    assert service.get_balance("CUSTOMER-B") == Decimal("50")
    # 流水按客户过滤
    assert len(service.list_transactions("CUSTOMER-A")) == 2
    assert len(service.list_transactions("CUSTOMER-B")) == 1


def test_customer_code_crud(tmp_path):
    """激活码创建、查询、列表、禁用。"""
    repo = SQLiteRepository(tmp_path / "codes.db")
    now = datetime.now().astimezone()
    repo.create_customer_codes(
        [
            CustomerCode(
                code="ABC12345",
                name="张三",
                initial_credits=Decimal("400"),
                created_at=now,
                updated_at=now,
            ),
            CustomerCode(
                code="XYZ67890",
                name="李四",
                initial_credits=Decimal("800"),
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    found = repo.get_customer_code("ABC12345")
    assert found is not None
    assert found.name == "张三"
    assert found.initial_credits == Decimal("400")
    assert found.enabled is True
    assert repo.get_customer_code("NOT-EXIST") is None
    assert len(repo.list_customer_codes()) == 2

    repo.set_customer_code_enabled("ABC12345", False)
    assert repo.get_customer_code("ABC12345").enabled is False

    repo.delete_customer_code("XYZ67890")
    assert repo.get_customer_code("XYZ67890") is None


def test_admin_accounts_crud(tmp_path):
    """多管理员账号：创建、查询、列表、改密码。"""
    repo = SQLiteRepository(tmp_path / "admins.db")
    now = datetime.now().astimezone()
    repo.create_admin_account(
        AdminAccount(
            username="admin",
            password_hash="hash-1",
            created_at=now,
            updated_at=now,
        )
    )
    repo.create_admin_account(
        AdminAccount(
            username="boss",
            password_hash="hash-2",
            created_at=now,
            updated_at=now,
        )
    )
    assert repo.get_admin_account("admin").password_hash == "hash-1"
    assert repo.get_admin_account("nobody") is None
    assert {a.username for a in repo.list_admin_accounts()} == {"admin", "boss"}
    repo.set_admin_password("boss", "hash-3")
    assert repo.get_admin_account("boss").password_hash == "hash-3"
