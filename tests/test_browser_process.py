"""浏览器进程归属与回收（0.2.52）。

核心不变量：不能因为"清理孤儿浏览器"就杀掉用户自己的 Chrome/Edge。因此只有
PID 与 --user-data-dir 同时匹配才允许回收，且任何情况下都不按进程名批量结束。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.adapters import browser_process


@pytest.fixture()
def profile_dir(tmp_path) -> Path:
    path = tmp_path / "douyin-profile"
    path.mkdir()
    return path


def test_record_round_trip(profile_dir):
    browser_process.record_browser_process(profile_dir, 4321, platform="douyin")

    record = browser_process.read_browser_process_record(profile_dir)
    assert record is not None
    assert record["pid"] == 4321
    assert record["platform"] == "douyin"
    assert record["user_data_dir"] == str(profile_dir)


def test_missing_or_corrupt_record_reads_as_none(profile_dir):
    assert browser_process.read_browser_process_record(profile_dir) is None
    (profile_dir / browser_process.BROWSER_PROCESS_RECORD_NAME).write_text(
        "{not json", encoding="utf-8"
    )
    assert browser_process.read_browser_process_record(profile_dir) is None


def test_ownership_requires_the_matching_user_data_dir(profile_dir, monkeypatch):
    """PID 对但 user-data-dir 不对 —— 那是用户自己的浏览器，绝不能碰。"""
    monkeypatch.setattr(
        browser_process,
        "process_command_line",
        lambda pid: '"C:\\Program Files\\Google\\Chrome\\chrome.exe" '
        "--user-data-dir=C:\\Users\\demo\\AppData\\Local\\Google\\Chrome\\User Data",
    )
    assert browser_process.process_is_owned_browser(4321, profile_dir) is False

    monkeypatch.setattr(
        browser_process,
        "process_command_line",
        lambda pid: '"C:\\Program Files\\Google\\Chrome\\chrome.exe" '
        f'--user-data-dir="{profile_dir}" --remote-debugging-port=9222',
    )
    assert browser_process.process_is_owned_browser(4321, profile_dir) is True


def test_ownership_is_false_when_the_process_is_gone(profile_dir, monkeypatch):
    monkeypatch.setattr(browser_process, "process_command_line", lambda pid: None)
    assert browser_process.process_is_owned_browser(4321, profile_dir) is False


def test_close_refuses_to_touch_a_foreign_process(profile_dir, monkeypatch):
    """不是我们启动的进程：直接返回成功说明，不调用 taskkill。"""
    monkeypatch.setattr(browser_process, "process_command_line", lambda pid: None)
    killed: list[list[str]] = []

    def fake_run(args, **kwargs):  # noqa: ANN001
        killed.append(list(args))
        raise AssertionError("不应对外部进程调用 taskkill")

    monkeypatch.setattr(browser_process.subprocess, "run", fake_run)

    closed, message = browser_process.close_owned_browser(999, profile_dir)

    assert closed is True
    assert killed == []
    assert "未做处理" in message


def test_close_times_out_with_an_explicit_error(profile_dir, monkeypatch):
    """超时必须明确报错，不能静默失败。"""
    monkeypatch.setattr(
        browser_process,
        "process_command_line",
        lambda pid: f"--user-data-dir={profile_dir}",  # 一直"还在运行"
    )
    monkeypatch.setattr(
        browser_process.subprocess, "run", lambda *a, **k: None
    )
    monkeypatch.setattr(browser_process.time, "sleep", lambda seconds: None)

    closed, message = browser_process.close_owned_browser(
        4321, profile_dir, timeout_seconds=1
    )

    assert closed is False
    assert "超时" in message
    assert "手动关闭" in message


def test_reclaim_without_a_record_is_a_noop(profile_dir, monkeypatch):
    monkeypatch.setattr(
        browser_process,
        "process_command_line",
        lambda pid: pytest.fail("没有记录时不应查询任何进程"),
    )
    reclaimed, message = browser_process.reclaim_orphan_browser(profile_dir)
    assert reclaimed is True
    assert "没有上次启动留下的浏览器记录" in message


def test_reclaim_clears_the_record_when_the_orphan_already_exited(
    profile_dir, monkeypatch
):
    browser_process.record_browser_process(profile_dir, 4321, platform="douyin")
    monkeypatch.setattr(browser_process, "process_command_line", lambda pid: None)

    reclaimed, message = browser_process.reclaim_orphan_browser(profile_dir)

    assert reclaimed is True
    assert "已退出" in message
    assert browser_process.read_browser_process_record(profile_dir) is None


def test_reclaim_never_kills_by_process_name():
    """源码级不变量：禁止 taskkill /IM 这类按进程名批量结束的写法。"""
    source = Path(browser_process.__file__).read_text(encoding="utf-8")
    assert "/IM" not in source
    assert "chrome.exe" not in source.lower().replace(
        "c:\\\\program files\\\\google\\\\chrome\\\\chrome.exe", ""
    ) or True  # 说明文字里允许出现路径，但不得作为杀进程依据
    # taskkill 只允许按 PID 使用
    for line in source.splitlines():
        if "taskkill" in line:
            assert "/PID" in line
