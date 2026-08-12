"""浏览器采集安全闸门测试：平台级租约（并发互斥 + 24h 滚动配额 + 冷却）。

覆盖主流四大平台（抖音/小红书/快手/B站）各自的真实采集闸门：
- 租约期间同平台并发请求被拒（running）
- 24 小时滚动窗口次数上限（默认 8 次）真正生效（daily_limit）
- 冷却期内被拒（cooldown）
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

from project.backend.app.api.v1.crawler import (  # noqa: E402
    BROWSER_COOLDOWN_SECONDS,
    BROWSER_MAX_REAL_RUNS_PER_WINDOW,
    BROWSER_SAFETY_PAUSE_SECONDS,
    _browser_provider_key,
    _browser_safety_status,
    _claim_browser_lease,
)
from src.models import Platform  # noqa: E402
from src.repositories.sqlite import SQLiteRepository  # noqa: E402


def _repo(tmp_path):
    return SQLiteRepository(tmp_path / "browser_safety.db")


def _release(repo, platform: Platform, lease_id: str, now: datetime, **kwargs):
    repo.release_provider_safety_lease(
        provider=_browser_provider_key(platform),
        run_id=lease_id,
        now=now,
        cooldown_seconds=kwargs.get("cooldown_seconds", 1),
        safety_pause_seconds=kwargs.get("safety_pause_seconds", 0),
        safety_reason=kwargs.get("safety_reason"),
    )


def test_default_daily_limit_is_eight():
    assert BROWSER_MAX_REAL_RUNS_PER_WINDOW == 8


def test_browser_lease_success_and_running_exclusion(tmp_path):
    """租约成功；未释放期间同平台并发抢占被拒；不同平台互不影响。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    lease = _claim_browser_lease(Platform.DOUYIN, repo, now)
    assert lease is not None
    # 同平台并发：被拒（互斥）
    assert (
        _claim_browser_lease(Platform.DOUYIN, repo, now + timedelta(seconds=1))
        is None
    )
    status = _browser_safety_status(Platform.DOUYIN, repo)
    assert status.state == "running"
    # 不同平台：不受影响
    assert _claim_browser_lease(Platform.BILIBILI, repo, now) is not None


def test_browser_lease_daily_limit_blocks_after_8_runs(tmp_path):
    """24 小时滚动窗口内第 9 次真实采集被拒（daily_limit）。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    for i in range(BROWSER_MAX_REAL_RUNS_PER_WINDOW):
        lease = _claim_browser_lease(Platform.KUAISHOU, repo, now)
        assert lease is not None, f"第 {i + 1} 次采集应成功"
        _release(repo, Platform.KUAISHOU, lease, now)
        now = now + timedelta(seconds=2)  # 跳过 1 秒冷却
    # 第 9 次：被拒
    assert _claim_browser_lease(Platform.KUAISHOU, repo, now) is None
    status = _browser_safety_status(Platform.KUAISHOU, repo)
    assert status.state == "daily_limit"
    assert f"{BROWSER_MAX_REAL_RUNS_PER_WINDOW} 次" in status.message


def test_browser_lease_cooldown_blocks_immediate_retry(tmp_path):
    """释放后冷却期内同平台不能立即再采。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    lease = _claim_browser_lease(Platform.XIAOHONGSHU, repo, now)
    assert lease is not None
    _release(
        repo,
        Platform.XIAOHONGSHU,
        lease,
        now,
        cooldown_seconds=BROWSER_COOLDOWN_SECONDS,
    )
    # 冷却期内：被拒
    assert (
        _claim_browser_lease(
            Platform.XIAOHONGSHU, repo, now + timedelta(seconds=5)
        )
        is None
    )
    status = _browser_safety_status(Platform.XIAOHONGSHU, repo)
    assert status.state == "cooldown"
    assert BROWSER_COOLDOWN_SECONDS == 3 * 60
    assert "60分钟" not in status.message
    assert f"{BROWSER_COOLDOWN_SECONDS // 60} 分钟" in status.message
    # 冷却过后：恢复
    assert (
        _claim_browser_lease(
            Platform.XIAOHONGSHU,
            repo,
            now + timedelta(seconds=BROWSER_COOLDOWN_SECONDS + 1),
        )
        is not None
    )


def test_browser_safety_pause_on_security_event(tmp_path):
    """安全事件（验证码/访问频繁）触发 24 小时暂停。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    lease = _claim_browser_lease(Platform.BILIBILI, repo, now)
    assert lease is not None
    _release(
        repo,
        Platform.BILIBILI,
        lease,
        now,
        cooldown_seconds=BROWSER_COOLDOWN_SECONDS,
        safety_pause_seconds=BROWSER_SAFETY_PAUSE_SECONDS,
        safety_reason="B站出现安全验证或访问频繁提示，已自动暂停真实采集 24 小时。",
    )
    status = _browser_safety_status(Platform.BILIBILI, repo)
    assert status.state == "safety_pause"
    assert (
        _claim_browser_lease(Platform.BILIBILI, repo, now + timedelta(minutes=5))
        is None
    )
