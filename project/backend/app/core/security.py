"""API安全认证模块 - API-Key认证中间件。"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

# API Key 配置
API_KEY_HEADER_NAME = "X-API-Key"
API_KEY_FILE = Path(__file__).resolve().parent.parent.parent.parent.parent / "data" / ".api_key"


def _generate_api_key() -> str:
    """生成安全的API Key。"""
    return f"vpro_{secrets.token_urlsafe(32)}"


def _hash_key(key: str) -> str:
    """对API Key进行哈希存储（安全考虑）。"""
    return hashlib.sha256(key.encode()).hexdigest()


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
        if api_key != env_key:
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
    if provided_hash != stored_hash:
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
    """简单的速率限制器。"""

    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.requests: dict[str, list[float]] = {}

    def is_allowed(self, client_id: str) -> bool:
        """检查客户端是否在速率限制内。"""
        now = time.time()
        minute_ago = now - 60

        if client_id not in self.requests:
            self.requests[client_id] = []

        # 清理过期记录
        self.requests[client_id] = [
            t for t in self.requests[client_id] if t > minute_ago
        ]

        # 检查是否超限
        if len(self.requests[client_id]) >= self.requests_per_minute:
            return False

        # 记录本次请求
        self.requests[client_id].append(now)
        return True


# 全局速率限制器实例
rate_limiter = RateLimiter(requests_per_minute=120)


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
