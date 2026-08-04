"""Small Windows-only helpers for the dedicated crawler browser."""

from __future__ import annotations

import ctypes
import subprocess


def _window_handles_for_debug_port(debug_port: int) -> list[int]:
    """Return top-level windows owned by the browser listening on ``debug_port``."""
    if not hasattr(ctypes, "windll"):
        return []
    try:
        listing = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        endpoint = f"127.0.0.1:{debug_port}"
        pid = next(
            (
                int(line.split()[-1])
                for line in (listing.stdout or "").splitlines()
                if endpoint in line and "LISTENING" in line.upper() and line.split()[-1].isdigit()
            ),
            None,
        )
        if pid is None:
            return []
        user32 = ctypes.windll.user32
        windows: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def visit(hwnd, _lparam):
            window_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid:
                windows.append(hwnd)
            return True

        user32.EnumWindows(visit, 0)
        return windows
    except (AttributeError, OSError, TypeError, ValueError):
        return []


def _main_browser_window(windows: list[int]) -> int:
    """Prefer the titled Chromium page over invisible helper windows."""
    user32 = ctypes.windll.user32
    return next(
        (hwnd for hwnd in windows if user32.GetWindowTextLengthW(hwnd) > 0),
        windows[0],
    )


def minimize_browser_window(debug_port: int) -> bool:
    """Keep routine crawler windows as one normal taskbar entry per platform."""
    return park_browser_window(debug_port)


def park_browser_window(debug_port: int) -> bool:
    """Leave one normal taskbar entry while hiding Chromium helper windows."""
    windows = _window_handles_for_debug_port(debug_port)
    if not windows:
        return False
    try:
        user32 = ctypes.windll.user32
        main_window = _main_browser_window(windows)
        for hwnd in windows:
            if hwnd != main_window:
                user32.ShowWindow(hwnd, 0)  # SW_HIDE helper/taskbar windows
        # Put an older off-screen window back on a normal work area before it
        # becomes the single minimized taskbar entry. It does not take focus.
        user32.SetWindowPos(main_window, 0, 80, 80, 1100, 800, 0x0014)
        user32.ShowWindow(main_window, 7)  # SW_SHOWMINNOACTIVE
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def reveal_browser_window(debug_port: int) -> bool:
    """Restore the Chrome window owning the local CDP port, if Windows exposes one."""
    windows = _window_handles_for_debug_port(debug_port)
    if not windows:
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = _main_browser_window(windows)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.ShowWindow(hwnd, 5)  # SW_SHOW: also restores a hidden window
        user32.SetWindowPos(hwnd, -1, 80, 80, 1100, 800, 0x0040)  # topmost once
        user32.SetWindowPos(hwnd, -2, 80, 80, 1100, 800, 0x0040)  # then normal z-order
        user32.SetForegroundWindow(hwnd)
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False
