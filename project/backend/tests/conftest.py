"""project/backend/tests 共享配置：注入测试 API Key，并重置速率限制器。

背景：认证中间件要求每个请求携带 X-API-Key；早期编写的集成测试多为裸请求
（TestClient(app) 不带头），在激活码/管理员认证体系落地后全部 401。
这里通过给 TestClient 注入默认头统一修复，测试代码零改动。
"""

import os

import fastapi.testclient
import pytest

from project.backend.app.core.security import (
    auth_failure_limiter,
    auth_global_rate_limiter,
    auth_rate_limiter,
    rate_limiter,
)

TEST_API_KEY = "pytest-api-key"
TEST_API_HEADERS = {"X-API-Key": TEST_API_KEY}

os.environ.setdefault("API_KEY", TEST_API_KEY)
os.environ["APP_ENV"] = "development"
os.environ["ENABLE_DOCS"] = "true"

_original_init = fastapi.testclient.TestClient.__init__


def _patched_init(self, app, *args, **kwargs):  # type: ignore[no-untyped-def]
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("X-API-Key", TEST_API_KEY)
    kwargs["headers"] = headers
    _original_init(self, app, *args, **kwargs)


fastapi.testclient.TestClient.__init__ = _patched_init  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# 速率限制器重置：避免测试间状态泄漏导致 429 错误
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """每个测试前清空速率限制器状态，避免跨测试泄漏。"""
    rate_limiter.clear()
    auth_rate_limiter.clear()
    auth_global_rate_limiter.clear()
    auth_failure_limiter.clear()
    yield
    rate_limiter.clear()
    auth_rate_limiter.clear()
    auth_global_rate_limiter.clear()
    auth_failure_limiter.clear()
