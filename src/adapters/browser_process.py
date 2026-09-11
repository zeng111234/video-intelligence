"""本机浏览器进程的归属记录与回收（0.2.52）。

背景：启动 Chrome 时 ``subprocess.Popen`` 的返回值被直接丢弃，PID 从未记录。
后端被强杀或崩溃后，浏览器进程会一直留着并占住 profile 目录的锁，下次启动
只能看到"未登录/被占用"。此前唯一可行的补救是"按进程名批量结束 Chrome"，
而那会连带杀掉用户自己开着的浏览器——绝不能这么做。

因此这里记录"哪个 PID 用了哪个 --user-data-dir"，并按下面的原则回收：

  * 只回收 **PID 与 --user-data-dir 同时匹配** 的进程；
  * 绝不按进程名批量结束；
  * 关闭后等待 profile 锁释放，超时明确报错，不静默失败。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

# 记录文件放在 profile 目录里：它天然一一对应"哪个 profile 被哪个进程占着"。
BROWSER_PROCESS_RECORD_NAME = "browser-process.json"
DEFAULT_CLOSE_TIMEOUT_SECONDS = 20.0
_PROFILE_LOCK_NAMES = ("SingletonLock", "SingletonCookie", "SingletonSocket")


def _record_path(profile_dir: Path | str) -> Path:
    return Path(profile_dir) / BROWSER_PROCESS_RECORD_NAME


def _normalize_dir(value: Path | str) -> str:
    return str(value).replace("/", "\\").rstrip("\\").casefold()


# Chrome 在路径含空格时会加引号，因此必须同时接受带引号与不带引号两种写法。
_USER_DATA_DIR_PATTERN = re.compile(
    r"--user-data-dir=(?:\"([^\"]+)\"|(\S+))", re.IGNORECASE
)


def _extract_user_data_dir(command_line: str) -> str:
    match = _USER_DATA_DIR_PATTERN.search(command_line or "")
    if not match:
        return ""
    return _normalize_dir(match.group(1) or match.group(2) or "")


def record_browser_process(
    profile_dir: Path | str,
    pid: int,
    *,
    platform: str = "",
) -> None:
    """记录本次由我们启动的浏览器 PID 与用户目录。"""
    path = _record_path(profile_dir)
    payload = {
        "pid": int(pid),
        "user_data_dir": str(Path(profile_dir)),
        "platform": platform,
        "started_at": time.time(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        # 记录失败不能影响采集本身。
        return


def read_browser_process_record(profile_dir: Path | str) -> dict[str, Any] | None:
    path = _record_path(profile_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("pid"):
        return None
    return payload


def clear_browser_process_record(profile_dir: Path | str) -> None:
    try:
        _record_path(profile_dir).unlink(missing_ok=True)
    except OSError:
        return


def process_command_line(pid: int) -> str | None:
    """读取指定 PID 的命令行；进程已退出或读不到时返回 None。"""
    if os.name != "nt":
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - 固定参数，pid 为整数
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\")"
                ".CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = (completed.stdout or "").strip()
    return line or None


def process_is_owned_browser(pid: int, profile_dir: Path | str) -> bool:
    """PID 与 --user-data-dir 同时匹配，才认定这个进程是我们启动的浏览器。

    只看 PID 不够：PID 会被系统复用，可能指向用户后来开的别的程序。
    只看进程名更不行：那会连用户自己的 Chrome 一起关掉。
    """
    line = process_command_line(pid)
    if not line:
        return False
    extracted = _extract_user_data_dir(line)
    if not extracted:
        return False
    return extracted == _normalize_dir(profile_dir)


def _profile_lock_present(profile_dir: Path | str) -> bool:
    base = Path(profile_dir)
    return any((base / name).exists() for name in _PROFILE_LOCK_NAMES)


def close_owned_browser(
    pid: int,
    profile_dir: Path | str,
    *,
    timeout_seconds: float = DEFAULT_CLOSE_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """关闭我们启动的浏览器并等待 profile 锁释放。

    返回 (是否已关闭, 说明)。超时**明确报错**，不静默失败——否则下次启动会
    遇到一个锁住的 profile，而用户完全不知道发生过什么。
    """
    if not process_is_owned_browser(pid, profile_dir):
        # 不是我们启动的，或已经退出：不碰它。
        return True, "该进程不是本次启动的浏览器，未做处理。"

    try:
        subprocess.run(  # noqa: S603 - pid 为整数，参数固定
            ["taskkill", "/PID", str(int(pid)), "/T"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"关闭浏览器进程失败：{exc}"

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        if process_command_line(pid) is None and not _profile_lock_present(profile_dir):
            clear_browser_process_record(profile_dir)
            return True, "浏览器已关闭，profile 锁已释放。"
        time.sleep(0.5)

    still_running = process_command_line(pid) is not None
    lock_held = _profile_lock_present(profile_dir)
    clear_browser_process_record(profile_dir)
    reason = "进程仍在运行" if still_running else "profile 锁仍被占用"
    return False, (
        f"等待浏览器关闭超时（{timeout_seconds:.0f} 秒）：{reason}。"
        "请手动关闭该浏览器窗口后重试。"
    )


def reclaim_orphan_browser(
    profile_dir: Path | str,
    *,
    timeout_seconds: float = DEFAULT_CLOSE_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """启动时回收上一次后端留下的孤儿浏览器。

    只有记录里的 PID 与 user-data-dir 都匹配才回收；否则只清理记录，
    绝不按进程名批量结束 Chrome。
    """
    record = read_browser_process_record(profile_dir)
    if not record:
        return True, "没有上次启动留下的浏览器记录。"
    try:
        pid = int(record.get("pid") or 0)
    except (TypeError, ValueError):
        clear_browser_process_record(profile_dir)
        return True, "浏览器记录无效，已清理。"
    if pid <= 0:
        clear_browser_process_record(profile_dir)
        return True, "浏览器记录无效，已清理。"

    if not process_is_owned_browser(pid, profile_dir):
        # 进程已退出，或 PID 已被复用成别的程序——两种情况下都不该动手。
        clear_browser_process_record(profile_dir)
        return True, "上次的浏览器进程已退出，记录已清理。"
    return close_owned_browser(pid, profile_dir, timeout_seconds=timeout_seconds)
