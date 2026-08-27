"""浏览器采集安全闸门测试：平台级租约（并发互斥 + 可选配额）。

覆盖主流四大平台（抖音/小红书/快手/B站）各自的真实采集闸门：
- 租约期间同平台并发请求被拒（running）
- 默认不设置 24 小时滚动次数上限；管理员正整数配置时才生效
- 默认无额外冷却；显式安全暂停仍会阻断（safety_pause）
- 安全事件（验证码/访问频繁）触发 24 小时暂停（safety_pause）
- 不同平台互不影响
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from project.backend.app.api.v1 import crawler as crawler_module  # noqa: E402
from src.models import Platform  # noqa: E402
from src.repositories.sqlite import SQLiteRepository  # noqa: E402


def _repo(tmp_path):
    return SQLiteRepository(tmp_path / "browser_safety.db")


def _release(repo, platform: Platform, lease_id: str, now: datetime, **kwargs):
    repo.release_provider_safety_lease(
        provider=crawler_module._browser_provider_key(platform),
        run_id=lease_id,
        now=now,
        cooldown_seconds=kwargs.get("cooldown_seconds", 1),
        safety_pause_seconds=kwargs.get("safety_pause_seconds", 0),
        safety_reason=kwargs.get("safety_reason"),
    )


def test_default_daily_limit_is_disabled():
    assert crawler_module.BROWSER_MAX_REAL_RUNS_PER_WINDOW is None


def test_admin_limit_parser_accepts_only_positive_values(monkeypatch):
    monkeypatch.setenv("TEST_BROWSER_LIMIT", "12")
    assert crawler_module._optional_positive_env_int("TEST_BROWSER_LIMIT") == 12
    monkeypatch.setenv("TEST_BROWSER_LIMIT", "0")
    assert crawler_module._optional_positive_env_int("TEST_BROWSER_LIMIT") is None
    monkeypatch.setenv("TEST_BROWSER_LIMIT", "not-a-number")
    assert crawler_module._optional_positive_env_int("TEST_BROWSER_LIMIT") is None


def test_browser_lease_success_and_running_exclusion(tmp_path):
    """租约成功；未释放期间同平台并发抢占被拒；不同平台互不影响。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    lease = crawler_module._claim_browser_lease(Platform.DOUYIN, repo, now)
    assert lease is not None
    # 同平台并发：被拒（互斥）
    assert (
        crawler_module._claim_browser_lease(Platform.DOUYIN, repo, now + timedelta(seconds=1))
        is None
    )
    status = crawler_module._browser_safety_status(Platform.DOUYIN, repo)
    assert status.state == "running"
    # 不同平台：不受影响
    assert crawler_module._claim_browser_lease(Platform.BILIBILI, repo, now) is not None


def test_browser_lease_daily_limit_blocks_after_explicit_limit(tmp_path, monkeypatch):
    """管理员显式设置正整数时，滚动窗口次数上限仍生效。"""
    monkeypatch.setattr(crawler_module, "BROWSER_MAX_REAL_RUNS_PER_WINDOW", 2)
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    for i in range(2):
        lease = crawler_module._claim_browser_lease(Platform.KUAISHOU, repo, now)
        assert lease is not None, f"第 {i + 1} 次采集应成功"
        _release(repo, Platform.KUAISHOU, lease, now)
        now = now + timedelta(seconds=2)  # 跳过 1 秒冷却
    assert crawler_module._claim_browser_lease(Platform.KUAISHOU, repo, now) is None
    status = crawler_module._browser_safety_status(Platform.KUAISHOU, repo)
    assert status.state == "daily_limit"
    assert "2 次" in status.message


def test_historical_eight_runs_do_not_block_when_default_limit_is_disabled(tmp_path):
    """已有 SQLite 计数在默认关闭次数上限后仍可继续使用。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    for _ in range(8):
        lease = crawler_module._claim_browser_lease(Platform.BILIBILI, repo, now)
        assert lease is not None
        _release(repo, Platform.BILIBILI, lease, now)
        now += timedelta(seconds=2)
    status = crawler_module._browser_safety_status(Platform.BILIBILI, repo)
    assert status.state != "daily_limit"
    assert status.real_runs_in_window == 8
    assert status.real_run_limit is None
    assert "24小时最多" not in status.message
    assert crawler_module._claim_browser_lease(Platform.BILIBILI, repo, now) is not None


def test_browser_lease_has_no_default_cooldown(tmp_path):
    """默认释放后可立即换词再采；同一时刻互斥仍由租约保证。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    lease = crawler_module._claim_browser_lease(Platform.XIAOHONGSHU, repo, now)
    assert lease is not None
    _release(
        repo,
        Platform.XIAOHONGSHU,
        lease,
        now,
        cooldown_seconds=crawler_module.BROWSER_COOLDOWN_SECONDS,
    )
    ready_status = crawler_module._browser_safety_status(Platform.XIAOHONGSHU, repo)
    assert ready_status.state == "ready"
    assert "短暂冷却" not in ready_status.message
    assert "不额外冷却" in ready_status.message
    # 默认无额外冷却：短暂间隔后可立即重新取得租约。
    assert (
        crawler_module._claim_browser_lease(
            Platform.XIAOHONGSHU, repo, now + timedelta(seconds=5)
        )
        is not None
    )
    status = crawler_module._browser_safety_status(Platform.XIAOHONGSHU, repo)
    assert status.state == "running"
    assert crawler_module.BROWSER_COOLDOWN_SECONDS == 0


def test_browser_safety_pause_on_security_event(tmp_path):
    """安全事件（验证码/访问频繁）触发 24 小时暂停。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    lease = crawler_module._claim_browser_lease(Platform.BILIBILI, repo, now)
    assert lease is not None
    _release(
        repo,
        Platform.BILIBILI,
        lease,
        now,
        cooldown_seconds=crawler_module.BROWSER_COOLDOWN_SECONDS,
        safety_pause_seconds=crawler_module.BROWSER_SAFETY_PAUSE_SECONDS,
        safety_reason="B站出现安全验证或访问频繁提示，已自动暂停真实采集 24 小时。",
    )
    status = crawler_module._browser_safety_status(Platform.BILIBILI, repo)
    assert status.state == "safety_pause"
    assert (
        crawler_module._claim_browser_lease(Platform.BILIBILI, repo, now + timedelta(minutes=5))
        is None
    )
