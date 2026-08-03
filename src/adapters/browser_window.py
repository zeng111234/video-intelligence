"""Small Windows-only helpers for the dedicated crawler browser."""

from __future__ import annotations

import ctypes
import subprocess


def minimize_browser_window(debug_port: int) -> bool:
    """Minimize the dedicated browser window without changing its login state."""
    if not hasattr(ctypes, "windll"):
        return False
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
            return False
        user32 = ctypes.windll.user32
        windows: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def visit(hwnd, _lparam):
            window_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid:
                windows.append(hwnd)
                return False
            return True

        user32.EnumWindows(visit, 0)
        if not windows:
            return False
        user32.ShowWindow(windows[0], 6)  # SW_MINIMIZE
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def reveal_browser_window(debug_port: int) -> bool:
    """Restore the Chrome window owning the local CDP port, if Windows exposes one."""
    if not hasattr(ctypes, "windll"):
        return False
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
            return False
        user32 = ctypes.windll.user32
        windows: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def visit(hwnd, _lparam):
            window_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid:
                windows.append(hwnd)
                return False
            return True

        user32.EnumWindows(visit, 0)
        if not windows:
            return False
        hwnd = windows[0]
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetWindowPos(hwnd, -1, 80, 80, 1100, 800, 0x0040)  # topmost once
        user32.SetWindowPos(hwnd, -2, 80, 80, 1100, 800, 0x0040)  # then normal z-order
        user32.SetForegroundWindow(hwnd)
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False
