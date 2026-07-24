"""Local browser profiles for user-authorized publishing accounts.

The registry deliberately stores metadata only.  Browser session material stays
inside Chromium's dedicated user-data directory and is never read, exported or
returned by the API.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PUBLISH_ACCOUNT_ROOT = PROJECT_ROOT / "data" / "publish_accounts"
PROFILE_ROOT = PROJECT_ROOT / "data" / "browser_profiles" / "publish"
REGISTRY_PATH = PUBLISH_ACCOUNT_ROOT / "accounts.json"
DOUYIN_CREATOR_HOME_URL = "https://creator.douyin.com/"


class PublishAccountError(ValueError):
    pass


@dataclass
class PublishAccount:
    account_id: str
    platform: str
    name: str
    profile_dir: str
    status: Literal["needs_login", "browser_open", "ready", "error"] = "needs_login"
    last_message: str = "尚未打开官方登录窗口。"
    debug_port: int | None = None
    browser_pid: int | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_public_dict(self) -> dict[str, str | int | None]:
        return {
            "account_id": self.account_id,
            "platform": self.platform,
            "name": self.name,
            "status": self.status,
            "message": self.last_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class PublishAccountManager:
    """Metadata registry and visible dedicated-browser launcher."""

    def __init__(self, *, registry_path: Path = REGISTRY_PATH, profile_root: Path = PROFILE_ROOT) -> None:
        self.registry_path = registry_path
        self.profile_root = profile_root
        self._accounts: dict[str, PublishAccount] = {}
        self._load()

    def _load(self) -> None:
        if not self.registry_path.exists():
            return
        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
            for item in raw if isinstance(raw, list) else []:
                account = PublishAccount(**item)
                self._accounts[account.account_id] = account
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A malformed local registry must not break the whole API.  The user
            # can still add a new account; existing profile folders stay intact.
            self._accounts = {}

    def _save(self) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(account) for account in self._accounts.values()]
        temporary = self.registry_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.registry_path)

    def list(self, platform: str | None = None) -> list[PublishAccount]:
        accounts = self._accounts.values()
        if platform:
            accounts = (item for item in accounts if item.platform == platform)
        return sorted(accounts, key=lambda item: (item.platform, item.name, item.created_at))

    def get(self, account_id: str, *, platform: str | None = None) -> PublishAccount:
        account = self._accounts.get(account_id)
        if account is None or (platform and account.platform != platform):
            raise PublishAccountError("发布账号不存在或不属于该平台。")
        return account

    def create(self, *, platform: str, name: str) -> PublishAccount:
        normalized_platform = platform.strip().casefold()
        normalized_name = name.strip()
        if normalized_platform != "douyin":
            raise PublishAccountError("本机扫码发布第一阶段只支持抖音。")
        if not normalized_name:
            raise PublishAccountError("请填写账号名称，例如“公司主号”。")
        if len(normalized_name) > 40:
            raise PublishAccountError("账号名称不能超过 40 个字符。")
        if any(item.platform == normalized_platform and item.name == normalized_name for item in self._accounts.values()):
            raise PublishAccountError("该平台已存在同名账号，请换一个名称。")
        now = datetime.now().astimezone().isoformat()
        account_id = f"pubacc-{uuid4().hex[:10]}"
        profile_dir = self.profile_root / normalized_platform / account_id
        account = PublishAccount(
            account_id=account_id,
            platform=normalized_platform,
            name=normalized_name,
            profile_dir=str(profile_dir),
            created_at=now,
            updated_at=now,
        )
        self._accounts[account_id] = account
        self._save()
        return account

    def rename(self, account_id: str, *, name: str) -> PublishAccount:
        account = self.get(account_id)
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name) > 40:
            raise PublishAccountError("账号名称需为 1 到 40 个字符。")
        account.name = normalized_name
        account.updated_at = datetime.now().astimezone().isoformat()
        self._save()
        return account

    def delete(self, account_id: str) -> None:
        account = self.get(account_id)
        profile_path = Path(account.profile_dir).resolve()
        allowed_root = self.profile_root.resolve()
        if allowed_root not in profile_path.parents:
            raise PublishAccountError("账号浏览器目录不合法，已拒绝清理。")
        self._close_account_browser(account)
        if profile_path.exists():
            try:
                shutil.rmtree(profile_path)
            except OSError as exc:
                raise PublishAccountError(
                    "专用浏览器仍在占用账号档案；请关闭该账号的抖音创作者窗口后再移除。"
                ) from exc
        del self._accounts[account_id]
        self._save()

    def status(self, account_id: str) -> PublishAccount:
        account = self.get(account_id)
        if account.debug_port and self._port_open(account.debug_port):
            pages = self._debug_pages(account.debug_port)
            creator_page = next(
                (page for page in pages if "creator.douyin.com" in str(page.get("url") or "")),
                None,
            )
            title = str((creator_page or {}).get("title") or "")
            if creator_page and "抖音创作者中心" in title:
                account.status = "ready"
                account.last_message = "已进入抖音创作者中心；可继续选择成片并准备发布。"
            else:
                account.status = "browser_open"
                account.last_message = "官方抖音窗口已打开；请在该窗口扫码或完成验证。"
        elif Path(account.profile_dir).exists():
            account.status = "ready"
            account.last_message = "已保存本机登录档案；开始发布时会打开官方抖音窗口确认登录。"
            account.debug_port = None
        else:
            account.status = "needs_login"
            account.last_message = "请打开官方扫码窗口完成首次登录。"
            account.debug_port = None
        account.updated_at = datetime.now().astimezone().isoformat()
        self._save()
        return account

    def open_login_browser(self, account_id: str) -> PublishAccount:
        account = self.get(account_id, platform="douyin")
        if account.debug_port and self._port_open(account.debug_port):
            return self.status(account_id)
        executable = self._browser_executable()
        if executable is None:
            raise PublishAccountError("未找到 Chrome 或 Edge，请安装浏览器后重试。")
        port = self._available_port()
        profile_path = Path(account.profile_dir)
        profile_path.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            [
                str(executable),
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_path}",
                "--no-first-run",
                "--no-default-browser-check",
                DOUYIN_CREATOR_HOME_URL,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        account.debug_port = port
        account.browser_pid = process.pid
        account.status = "browser_open"
        account.last_message = "已打开抖音官方创作者窗口，请扫码或完成平台验证。"
        account.updated_at = datetime.now().astimezone().isoformat()
        self._save()
        return account

    @staticmethod
    def _port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return True
        except OSError:
            return False

    @staticmethod
    def _debug_pages(port: int) -> list[dict]:
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1.5) as response:  # noqa: S310 - localhost only
                payload = json.loads(response.read().decode("utf-8"))
            return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        except (URLError, OSError, ValueError, json.JSONDecodeError):
            return []

    def _close_account_browser(self, account: PublishAccount) -> None:
        pids: set[int] = set()
        if account.browser_pid:
            pids.add(account.browser_pid)
        if account.debug_port:
            try:
                netstat = subprocess.run(
                    ["netstat", "-ano", "-p", "tcp"],
                    capture_output=True,
                    text=True,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                endpoint = f"127.0.0.1:{account.debug_port}"
                for line in netstat.stdout.splitlines():
                    if endpoint in line and "LISTENING" in line.upper():
                        candidate = line.split()[-1]
                        if candidate.isdigit():
                            pids.add(int(candidate))
            except OSError:
                pass
        for pid in pids:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        if pids:
            time.sleep(0.4)

    @staticmethod
    def _available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _browser_executable() -> Path | None:
        candidates = [
            shutil.which("chrome"),
            shutil.which("msedge"),
            r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
            r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
            r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        ]
        return next((Path(item) for item in candidates if item and Path(item).is_file()), None)


publish_account_manager = PublishAccountManager()
