"""账号管理系统

管理多个平台账号，支持：
- 账号 CRUD（添加/编辑/删除）
- Cookie 保存与加载
- 登录状态检测
- 多账号轮换

参考 MediaPublishPlatform 的账号管理设计。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Cookie 存储目录
COOKIE_DIR = Path("data/cookies")
COOKIE_DIR.mkdir(parents=True, exist_ok=True)


class AccountStatus(str, Enum):
    """账号状态"""
    ACTIVE = "active"        # 活跃
    EXPIRED = "expired"      # 过期
    ERROR = "error"          # 错误
    CHECKING = "checking"    # 检查中


@dataclass
class PlatformAccount:
    """平台账号"""
    account_id: str
    platform: str
    name: str  # 用户自定义名称
    username: str = ""  # 平台用户名
    avatar_url: str = ""

    # 状态
    status: AccountStatus = AccountStatus.ACTIVE
    last_check: datetime | None = None
    error_message: str = ""

    # Cookie
    cookie_path: str = ""
    cookies: list[dict] = field(default_factory=list)

    # 统计
    publish_count: int = 0
    last_publish: datetime | None = None

    # 元数据
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    notes: str = ""


class AccountManager:
    """账号管理器"""

    def __init__(self):
        self._accounts: dict[str, PlatformAccount] = {}
        self._load_accounts()

    def _load_accounts(self):
        """加载账号数据"""
        accounts_file = COOKIE_DIR / "accounts.json"
        if accounts_file.exists():
            try:
                data = json.loads(accounts_file.read_text(encoding="utf-8"))
                for item in data:
                    account = PlatformAccount(
                        account_id=item["account_id"],
                        platform=item["platform"],
                        name=item["name"],
                        username=item.get("username", ""),
                        status=AccountStatus(item.get("status", "active")),
                        cookie_path=item.get("cookie_path", ""),
                        publish_count=item.get("publish_count", 0),
                        notes=item.get("notes", ""),
                    )
                    self._accounts[account.account_id] = account
                logger.info(f"加载了 {len(self._accounts)} 个账号")
            except Exception as e:
                logger.error(f"加载账号数据失败: {e}")

    def _save_accounts(self):
        """保存账号数据"""
        accounts_file = COOKIE_DIR / "accounts.json"
        try:
            data = [
                {
                    "account_id": a.account_id,
                    "platform": a.platform,
                    "name": a.name,
                    "username": a.username,
                    "status": a.status.value,
                    "cookie_path": a.cookie_path,
                    "publish_count": a.publish_count,
                    "notes": a.notes,
                }
                for a in self._accounts.values()
            ]
            accounts_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("账号数据已保存")
        except Exception as e:
            logger.error(f"保存账号数据失败: {e}")

    def add_account(
        self,
        platform: str,
        name: str,
        username: str = "",
        cookies: list[dict] | None = None,
        notes: str = "",
    ) -> PlatformAccount:
        """添加账号"""
        account_id = f"acc-{uuid.uuid4().hex[:10]}"

        # 保存 Cookie
        cookie_path = ""
        if cookies:
            cookie_path = str(COOKIE_DIR / f"{account_id}.json")
            Path(cookie_path).write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")

        account = PlatformAccount(
            account_id=account_id,
            platform=platform,
            name=name,
            username=username,
            cookie_path=cookie_path,
            cookies=cookies or [],
            notes=notes,
        )

        self._accounts[account_id] = account
        self._save_accounts()
        logger.info(f"账号已添加: {account_id} ({platform}/{name})")
        return account

    def update_account(self, account_id: str, **kwargs) -> PlatformAccount | None:
        """更新账号"""
        account = self._accounts.get(account_id)
        if not account:
            return None

        for key, value in kwargs.items():
            if hasattr(account, key):
                setattr(account, key, value)

        account.updated_at = datetime.now()

        # 更新 Cookie 文件
        if "cookies" in kwargs:
            if not account.cookie_path:
                account.cookie_path = str(COOKIE_DIR / f"{account_id}.json")
            Path(account.cookie_path).write_text(
                json.dumps(kwargs["cookies"], ensure_ascii=False), encoding="utf-8"
            )

        self._save_accounts()
        return account

    def delete_account(self, account_id: str) -> bool:
        """删除账号"""
        account = self._accounts.get(account_id)
        if not account:
            return False

        # 删除 Cookie 文件
        if account.cookie_path and os.path.exists(account.cookie_path):
            os.remove(account.cookie_path)

        del self._accounts[account_id]
        self._save_accounts()
        logger.info(f"账号已删除: {account_id}")
        return True

    def get_account(self, account_id: str) -> PlatformAccount | None:
        """获取账号"""
        return self._accounts.get(account_id)

    def get_all_accounts(self) -> list[PlatformAccount]:
        """获取所有账号"""
        return list(self._accounts.values())

    def get_accounts_by_platform(self, platform: str) -> list[PlatformAccount]:
        """获取指定平台的账号"""
        return [a for a in self._accounts.values() if a.platform == platform]

    def get_active_accounts(self, platform: str | None = None) -> list[PlatformAccount]:
        """获取活跃账号"""
        accounts = self._accounts.values()
        if platform:
            accounts = [a for a in accounts if a.platform == platform]
        return [a for a in accounts if a.status == AccountStatus.ACTIVE]

    def get_next_account(self, platform: str) -> PlatformAccount | None:
        """获取下一个可用账号（轮换策略）"""
        active_accounts = self.get_active_accounts(platform)
        if not active_accounts:
            return None

        # 选择发布次数最少的账号
        return min(active_accounts, key=lambda a: a.publish_count)

    def update_status(self, account_id: str, status: AccountStatus, error_message: str = ""):
        """更新账号状态"""
        account = self._accounts.get(account_id)
        if not account:
            return

        account.status = status
        account.last_check = datetime.now()
        account.error_message = error_message
        account.updated_at = datetime.now()
        self._save_accounts()

    def increment_publish_count(self, account_id: str):
        """增加发布次数"""
        account = self._accounts.get(account_id)
        if account:
            account.publish_count += 1
            account.last_publish = datetime.now()
            account.updated_at = datetime.now()
            self._save_accounts()

    def load_cookies(self, account_id: str) -> list[dict]:
        """加载账号 Cookie"""
        account = self._accounts.get(account_id)
        if not account or not account.cookie_path:
            return []

        try:
            cookie_file = Path(account.cookie_path)
            if cookie_file.exists():
                return json.loads(cookie_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"加载 Cookie 失败: {e}")

        return []

    def export_cookies(self, account_id: str) -> str | None:
        """导出 Cookie（JSON 字符串）"""
        cookies = self.load_cookies(account_id)
        if cookies:
            return json.dumps(cookies, ensure_ascii=False, indent=2)
        return None

    def import_cookies(self, account_id: str, cookies_json: str) -> bool:
        """导入 Cookie"""
        try:
            cookies = json.loads(cookies_json)
            self.update_account(account_id, cookies=cookies)
            return True
        except Exception as e:
            logger.error(f"导入 Cookie 失败: {e}")
            return False


# 全局实例
account_manager = AccountManager()
