"""API安全认证模块 - API-Key认证中间件。"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

# API Key 配置
API_KEY_HEADER_NAME = "X-API-Key"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_RUNTIME_ROOT = Path(
    os.getenv("VIDEOINSIGHT_RUNTIME_ROOT", str(_PROJECT_ROOT))
).expanduser().resolve()
API_KEY_FILE = _RUNTIME_ROOT / "data" / ".api_key"


def _generate_api_key() -> str:
    """生成安全的API Key。"""
    return f"vpro_{secrets.token_urlsafe(32)}"


def _hash_key(key: str) -> str:
    """对API Key进行哈希存储（安全考虑）。"""
    return hashlib.sha256(key.encode()).hexdigest()


_PASSWORD_HASH_PREFIX = "scrypt$"
_PASSWORD_SCRYPT_N = 2**14
_PASSWORD_SCRYPT_R = 8
_PASSWORD_SCRYPT_P = 1


def hash_password(password: str) -> str:
    """用慢速 scrypt 保存管理员密码，而不是快速哈希。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_PASSWORD_SCRYPT_N,
        r=_PASSWORD_SCRYPT_R,
        p=_PASSWORD_SCRYPT_P,
    )
    return (
        f"{_PASSWORD_HASH_PREFIX}{_PASSWORD_SCRYPT_N}${_PASSWORD_SCRYPT_R}$"
        f"{_PASSWORD_SCRYPT_P}${salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, stored_hash: str) -> tuple[bool, bool]:
    """返回 (密码有效, 是否需将旧 SHA-256 哈希升级为 scrypt)。"""
    if stored_hash.startswith(_PASSWORD_HASH_PREFIX):
        try:
            _, n, r, p, salt_hex, digest_hex = stored_hash.split("$", 5)
            digest = hashlib.scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=int(n),
                r=int(r),
                p=int(p),
            )
            return secrets.compare_digest(digest.hex(), digest_hex), False
        except (TypeError, ValueError):
            return False, False
    if re.fullmatch(r"[0-9a-f]{64}", stored_hash):
        legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return secrets.compare_digest(legacy, stored_hash), True
    return False, False


@lru_cache
def get_or_create_api_key() -> str:
    """获取或创建API Key。

    优先级：
    1. 环境变量 API_KEY
    2. 本地文件存储
    3. 自动生成并保存
    """
    import os

    # 1. 检查环境变量
    env_key = os.getenv("API_KEY", "").strip()
    if env_key:
        logger.info("使用环境变量中的 API Key")
        return env_key

    # 2. 检查本地文件
    if API_KEY_FILE.exists():
        try:
            stored_hash = API_KEY_FILE.read_text(encoding="utf-8").strip()
            if stored_hash:
                # 返回哈希值用于验证，但首次生成时返回原始key
                logger.info("从本地文件加载 API Key")
                return stored_hash
        except OSError:
            pass

    # 3. 自动生成新Key
    new_key = _generate_api_key()
    key_hash = _hash_key(new_key)

    # 保存哈希值到文件
    try:
        API_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        API_KEY_FILE.write_text(key_hash, encoding="utf-8")
        logger.info("已生成新的 API Key 并保存")
    except OSError as e:
        logger.warning("无法保存 API Key 到文件: %s", e)

    # 首次生成时返回原始key（只显示一次）
    return new_key


def get_stored_key_hash() -> str:
    """获取存储的Key哈希值。"""
    if API_KEY_FILE.exists():
        try:
            return API_KEY_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return ""


# FastAPI 安全方案
api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


async def verify_api_key(request: Request) -> bool:
    """验证API Key。

    公开端点（如 /health, /docs）不需要验证。
    其他端点需要有效的 API Key。
    """
    # 公开端点白名单
    public_paths = {
        "/",
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
    }

    # 检查是否为公开端点
    if request.url.path in public_paths:
        return True

    # OPTIONS 请求不需要验证（CORS预检）
    if request.method == "OPTIONS":
        return True

    # 获取请求中的API Key
    api_key = request.headers.get(API_KEY_HEADER_NAME)
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": True,
                "code": 401,
                "message": "缺少API Key，请在请求头中添加 X-API-Key",
            },
        )

    # 验证API Key
    import os
    env_key = os.getenv("API_KEY", "").strip()
    if env_key:
        # 优先用环境变量验证
        if not secrets.compare_digest(api_key, env_key):
            logger.warning("API Key 验证失败（环境变量模式）: %s", request.url.path)
            raise HTTPException(
                status_code=403,
                detail={
                    "error": True,
                    "code": 403,
                    "message": "API Key 无效",
                },
            )
        return True

    stored_hash = get_stored_key_hash()
    if not stored_hash:
        # 首次启动，还没有生成Key，允许访问但记录警告
        logger.warning("API Key 尚未生成，允许本次请求")
        return True

    # 对比哈希值
    provided_hash = _hash_key(api_key)
    if not secrets.compare_digest(provided_hash, stored_hash):
        logger.warning("API Key 验证失败: %s", request.url.path)
        raise HTTPException(
            status_code=403,
            detail={
                "error": True,
                "code": 403,
                "message": "API Key 无效",
            },
        )

    return True


# 速率限制（简单实现）
class RateLimiter:
    """进程内滑动窗口限流器；线程安全，供单进程桌面与控制层使用。"""

    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, client_id: str) -> bool:
        """检查客户端是否在速率限制内。"""
        now = time.time()
        minute_ago = now - 60
        with self._lock:
            recent = [
                timestamp
                for timestamp in self.requests.get(client_id, [])
                if timestamp > minute_ago
            ]
            if len(recent) >= self.requests_per_minute:
                self.requests[client_id] = recent
                return False
            recent.append(now)
            self.requests[client_id] = recent
            return True

    def clear(self) -> None:
        with self._lock:
            self.requests.clear()


class AuthFailureLimiter:
    """按登录身份摘要锁定连续失败，避免针对某个客户持续猜码。"""

    def __init__(self, max_failures: int = 5, window_seconds: int = 15 * 60):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _subject_key(role: str, subject: str) -> str:
        normalized = f"{role.strip().casefold()}:{subject.strip().casefold()}"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def is_blocked(self, role: str, subject: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        key = self._subject_key(role, subject)
        with self._lock:
            recent = [
                timestamp
                for timestamp in self.failures.get(key, [])
                if timestamp > cutoff
            ]
            if recent:
                self.failures[key] = recent
            else:
                self.failures.pop(key, None)
            return len(recent) >= self.max_failures

    def record_failure(self, role: str, subject: str) -> None:
        now = time.time()
        cutoff = now - self.window_seconds
        key = self._subject_key(role, subject)
        with self._lock:
            recent = [
                timestamp
                for timestamp in self.failures.get(key, [])
                if timestamp > cutoff
            ]
            recent.append(now)
            self.failures[key] = recent

    def clear_subject(self, role: str, subject: str) -> None:
        key = self._subject_key(role, subject)
        with self._lock:
            self.failures.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self.failures.clear()


# 全局速率限制器实例
rate_limiter = RateLimiter(requests_per_minute=120)
auth_rate_limiter = RateLimiter(requests_per_minute=10)
auth_global_rate_limiter = RateLimiter(requests_per_minute=300)
auth_failure_limiter = AuthFailureLimiter(max_failures=5, window_seconds=15 * 60)


async def check_rate_limit(request: Request) -> bool:
    """检查请求速率限制。"""
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=429,
            detail={
                "error": True,
                "code": 429,
                "message": "请求过于频繁，请稍后再试",
            },
        )
    return True


async def check_auth_rate_limit(request: Request) -> bool:
    """登录接口使用独立限流，避免撞库消耗普通 API 配额。"""
    client_ip = request.client.host if request.client else "unknown"
    if not auth_global_rate_limiter.is_allowed("all-login-attempts"):
        raise HTTPException(
            status_code=429,
            detail={"error": True, "code": 429, "message": "登录请求较多，请稍后再试。"},
        )
    if not auth_rate_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=429,
            detail={"error": True, "code": 429, "message": "登录尝试过于频繁，请稍后再试。"},
        )
    return True


def check_auth_subject_allowed(role: str, subject: str) -> None:
    """在查询账号前检查摘要失败桶，不在内存或日志保存明文激活码。"""
    if auth_failure_limiter.is_blocked(role, subject):
        raise HTTPException(
            status_code=429,
            detail="登录尝试过于频繁，请 15 分钟后再试。",
        )


def record_auth_failure(role: str, subject: str) -> None:
    auth_failure_limiter.record_failure(role, subject)


def clear_auth_failures(role: str, subject: str) -> None:
    auth_failure_limiter.clear_subject(role, subject)


# ---------------------------------------------------------------------------
# 登录 token 体系：管理员与客户统一存储（token -> 角色+身份+过期时间）
# ---------------------------------------------------------------------------

ADMIN_PASSWORD_ENV = "ADMIN_PASSWORD"
ADMIN_TOKEN_TTL_SECONDS = 12 * 60 * 60  # 默认登录有效 12 小时
CUSTOMER_TOKEN_TTL_SECONDS = 12 * 60 * 60
_auth_tokens: dict[str, dict] = {}  # token -> {role, subject, expires_at}
_auth_tokens_lock = threading.Lock()


def _auth_session_store() -> str:
    """Return the configured session backend.

    Desktop/demo builds keep the existing in-memory behavior.  The company
    control plane sets AUTH_SESSION_STORE=sqlite so sessions survive process
    restarts without ever persisting plaintext bearer tokens.
    """

    return os.getenv("AUTH_SESSION_STORE", "memory").strip().casefold()


def _auth_database_path() -> Path:
    configured = os.getenv("AUTH_SESSION_DATABASE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    from project.backend.app.core.config import DATABASE_PATH

    return Path(DATABASE_PATH)


def _token_ttl_seconds(role: str) -> int:
    default = (
        CUSTOMER_TOKEN_TTL_SECONDS if role == "customer" else ADMIN_TOKEN_TTL_SECONDS
    )
    key = "CUSTOMER_TOKEN_TTL_SECONDS" if role == "customer" else "ADMIN_TOKEN_TTL_SECONDS"
    try:
        return max(300, min(int(os.getenv(key, str(default))), 7 * 24 * 60 * 60))
    except ValueError:
        return default


def _session_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(str(_auth_database_path()), timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _persistent_session_record(token: str) -> dict | None:
    token_hash = _hash_key(token)
    now = time.time()
    try:
        with _session_connection() as connection:
            row = connection.execute(
                """
                SELECT role, subject, expires_at
                FROM auth_sessions
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if float(row[2]) <= now:
                connection.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,)
                )
                return None
            return {"role": str(row[0]), "subject": str(row[1])}
    except sqlite3.OperationalError:
        logger.exception("持久登录会话存储不可用")
        return None


def issue_auth_token(
    role: str, subject: str, *, not_after: float | None = None
) -> str:
    """签发登录 token（role: admin/customer；subject: 用户名/激活码）。"""
    if role not in {"admin", "customer"} or not subject.strip():
        raise ValueError("登录会话角色或身份无效。")
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + _token_ttl_seconds(role)
    if not_after is not None:
        expires_at = min(expires_at, not_after)
    if expires_at <= now:
        raise ValueError("登录授权已到期。")
    if _auth_session_store() == "sqlite":
        with _session_connection() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            connection.execute(
                """
                INSERT INTO auth_sessions(token_hash, role, subject, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (_hash_key(token), role, subject.strip(), now, expires_at),
            )
        return token
    with _auth_tokens_lock:
        _auth_tokens[token] = {
            "role": role,
            "subject": subject,
            "expires_at": expires_at,
        }
        # 顺手清理过期 token，避免无限增长
        now = time.time()
        for old_token in [
            k for k, v in _auth_tokens.items() if v["expires_at"] <= now
        ]:
            _auth_tokens.pop(old_token, None)
    return token


def verify_auth_token(token: str) -> dict | None:
    """校验登录 token；有效返回 {role, subject}，否则返回 None。"""
    if not token:
        return None
    if _auth_session_store() == "sqlite":
        return _persistent_session_record(token)
    with _auth_tokens_lock:
        record = _auth_tokens.get(token)
        if record is None:
            return None
        if record["expires_at"] <= time.time():
            _auth_tokens.pop(token, None)
            return None
        return {"role": record["role"], "subject": record["subject"]}


def revoke_auth_token(token: str) -> None:
    """退出登录：使 token 立即失效。"""
    if _auth_session_store() == "sqlite":
        if not token:
            return
        try:
            with _session_connection() as connection:
                connection.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ?", (_hash_key(token),)
                )
        except sqlite3.OperationalError:
            logger.exception("注销持久登录会话失败")
        return
    with _auth_tokens_lock:
        _auth_tokens.pop(token, None)


def revoke_auth_tokens(role: str, subject: str) -> None:
    """禁用激活码或重置密码时，使该身份全部已签发会话立即失效。"""
    if _auth_session_store() == "sqlite":
        try:
            with _session_connection() as connection:
                connection.execute(
                    "DELETE FROM auth_sessions WHERE role = ? AND subject = ?",
                    (role, subject),
                )
        except sqlite3.OperationalError:
            logger.exception("批量注销持久登录会话失败")
        return
    with _auth_tokens_lock:
        for token, record in list(_auth_tokens.items()):
            if record["role"] == role and record["subject"] == subject:
                _auth_tokens.pop(token, None)


def get_admin_password() -> str:
    """读取管理员默认密码（环境变量 ADMIN_PASSWORD，用于首次初始化 admin 账号）。"""
    import os

    return os.getenv(ADMIN_PASSWORD_ENV, "").strip()


def admin_password_configured() -> bool:
    return bool(get_admin_password())


def verify_admin_token(token: str) -> bool:
    """校验管理员登录 token 是否有效且未过期。"""
    record = verify_auth_token(token)
    return bool(record and record["role"] == "admin")


admin_token_header = APIKeyHeader(
    name="X-Admin-Token", auto_error=False, description="管理员登录 token"
)


async def require_admin_token(
    admin_token: str | None = Security(admin_token_header),
) -> str:
    """FastAPI 依赖：要求携带有效的管理员登录 token。"""
    if not admin_token or not verify_admin_token(admin_token):
        raise HTTPException(
            status_code=401,
            detail={
                "error": True,
                "code": 401,
                "message": "需要管理员登录，请先输入管理密码。",
            },
        )
    record = verify_auth_token(admin_token)
    assert record is not None
    return str(record["subject"])


# ---------------------------------------------------------------------------
# 客户 token 验证
# ---------------------------------------------------------------------------

customer_token_header = APIKeyHeader(
    name="X-Customer-Token", auto_error=False, description="客户登录 token"
)


def verify_customer_token(token: str) -> str | None:
    """校验客户登录 token，有效返回客户激活码，否则返回 None。"""
    record = verify_auth_token(token)
    if record and record["role"] == "customer":
        return record["subject"]
    return None


async def require_customer_token(
    customer_token: str | None = Security(customer_token_header),
) -> str:
    """FastAPI 依赖：要求携带有效的客户登录 token，返回客户激活码。"""
    if not customer_token:
        raise HTTPException(
            status_code=401,
            detail={
                "error": True,
                "code": 401,
                "message": "需要客户登录，请先登录。",
            },
        )
    customer_code = verify_customer_token(customer_token)
    if not customer_code:
        raise HTTPException(
            status_code=401,
            detail={
                "error": True,
                "code": 401,
                "message": "客户登录已过期或无效，请重新登录。",
            },
        )
    return customer_code
