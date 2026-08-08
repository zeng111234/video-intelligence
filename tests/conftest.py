"""共享测试配置:API Key 环境变量与默认请求头。"""


import pytest

TEST_API_KEY = "pytest-api-key"
TEST_API_HEADERS = {"X-API-Key": TEST_API_KEY}


@pytest.fixture(autouse=True)
def _set_test_api_key_env(monkeypatch):
    """后端 API Key 校验优先读环境变量;测试进程统一注入测试 Key。"""
    monkeypatch.setenv("API_KEY", TEST_API_KEY)
