"""Windows desktop preview launcher.

The packaged build runs FastAPI and the pre-built React application on one
localhost port.  Local crawling and media tools stay on the customer computer;
commercial authority and paid providers are reached through the company
control plane.  No permanent supplier secret is bundled or inherited.
"""

from __future__ import annotations

import ctypes
import json
import logging
import multiprocessing
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

DEMO_ACTIVATION_CODE = "DEMO-0815"
DEFAULT_DESKTOP_PORT = 1001
DESKTOP_PORT = DEFAULT_DESKTOP_PORT
APP_URL = f"http://127.0.0.1:{DESKTOP_PORT}/login"
HEALTH_URL = f"http://127.0.0.1:{DESKTOP_PORT}/health"

DESKTOP_BLOCKED_SECRET_KEYS = (
    "APP_SECRET_KEY",
    "API_KEY",
    "ADMIN_PASSWORD",
    "POSTGRES_PASSWORD",
    "VIDEOINSIGHT_WORKER_TOKEN",
    "DASHSCOPE_API_KEY",
    "ALIYUN_MODEL_STUDIO_WORKSPACE_ID",
    "ALIBABA_CLOUD_ACCESS_KEY_ID",
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    "ALIYUN_ACCESS_KEY_ID",
    "ALIYUN_ACCESS_KEY_SECRET",
    "ALIYUN_ASR_ACCESS_KEY_ID",
    "ALIYUN_ASR_ACCESS_KEY_SECRET",
    "ALIYUN_ASR_APP_KEY",
    "ONEAPI_API_KEY",
    "DOUYIN_CLIENT_KEY",
    "DOUYIN_CLIENT_SECRET",
    "COPYWRITING_API_KEY",
    "OPENAI_API_KEY",
    "AVATAR_API_KEY",
    "SHUYING_AVATAR_API_CODE",
    "AVATAR_SERVICE_TOKEN",
    "BAIDU_XILING_APP_ID",
    "BAIDU_XILING_APP_KEY",
    "PUBLISH_DOUYIN_CLIENT_KEY",
    "PUBLISH_DOUYIN_CLIENT_SECRET",
    "PUBLISH_DOUYIN_ACCESS_TOKEN",
    "PUBLISH_DOUYIN_REFRESH_TOKEN",
    "PUBLISH_KUAISHOU_CLIENT_KEY",
    "PUBLISH_KUAISHOU_CLIENT_SECRET",
    "PUBLISH_KUAISHOU_ACCESS_TOKEN",
    "PUBLISH_WECHAT_CHANNELS_CLIENT_KEY",
    "PUBLISH_WECHAT_CHANNELS_CLIENT_SECRET",
    "PUBLISH_WECHAT_CHANNELS_ACCESS_TOKEN",
    "PUBLISH_XIAOHONGSHU_CLIENT_KEY",
    "PUBLISH_XIAOHONGSHU_CLIENT_SECRET",
    "PUBLISH_XIAOHONGSHU_ACCESS_TOKEN",
)


def _application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def _runtime_root(root: Path) -> Path:
    configured = os.getenv("VIDEOINSIGHT_RUNTIME_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        local_app_data = os.getenv("LOCALAPPDATA", "").strip()
        if local_app_data:
            return (Path(local_app_data) / "VideoInsight").resolve()
    return root


def _load_control_plane_config(root: Path) -> dict[str, str | bool]:
    path = root / "config" / "desktop-control-plane.json"
    if not path.is_file() or path.stat().st_size > 16 * 1024:
        return {"enabled": False, "control_plane_url": ""}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"enabled": False, "control_plane_url": ""}
    if not isinstance(payload, dict):
        return {"enabled": False, "control_plane_url": ""}
    value = str(payload.get("control_plane_url") or "").strip().rstrip("/")
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    loopback = hostname in {"localhost", "127.0.0.1", "::1"}
    reserved = hostname in {"example.com", "example.net", "example.org"} or any(
        hostname.endswith(suffix)
        for suffix in (
            ".localhost",
            ".test",
            ".invalid",
            ".example",
            ".example.com",
            ".example.net",
            ".example.org",
        )
    )
    valid = bool(
        hostname
        and not reserved
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and parsed.path in {"", "/"}
        and (parsed.scheme == "https" or (parsed.scheme == "http" and loopback))
    )
    return {
        "enabled": bool(payload.get("enabled")) and valid,
        "control_plane_url": value if valid else "",
    }


def _configure_local_urls(port: int) -> None:
    global DESKTOP_PORT, APP_URL, HEALTH_URL
    DESKTOP_PORT = port
    APP_URL = f"http://127.0.0.1:{port}/login"
    HEALTH_URL = f"http://127.0.0.1:{port}/health"


def _resolve_desktop_port() -> int:
    configured = os.getenv("VIDEOINSIGHT_DESKTOP_PORT", "").strip()
    if configured:
        try:
            port = int(configured)
        except ValueError as exc:
            raise ValueError("桌面服务端口配置无效。") from exc
        if not 1024 <= port <= 65535:
            raise ValueError("桌面服务端口配置无效。")
        return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _configure_desktop_environment(
    root: Path, runtime_root: Path, port: int | None = None
) -> bool:
    runtime_root.mkdir(parents=True, exist_ok=True)
    os.chdir(runtime_root)
    bundled_media_tools = root / "media"
    os.environ["PATH"] = os.pathsep.join(
        (
            str(bundled_media_tools),
            str(root),
            os.environ.get("PATH", ""),
        )
    )
    control_plane = _load_control_plane_config(root)
    control_plane_enabled = bool(
        control_plane["enabled"] and control_plane["control_plane_url"]
    )
    settings = {
        "APP_ENV": "production",
        "ENABLE_DOCS": "false",
        "VIDEOINSIGHT_DESKTOP_CLIENT": "true",
        "VIDEOINSIGHT_DESKTOP_DEMO": "false" if control_plane_enabled else "true",
        "VIDEOINSIGHT_DEMO_OWNER": ""
        if control_plane_enabled
        else DEMO_ACTIVATION_CODE,
        "VIDEOINSIGHT_CONTROL_PLANE_ENABLED": (
            "true" if control_plane_enabled else "false"
        ),
        "VIDEOINSIGHT_CONTROL_PLANE_URL": str(control_plane["control_plane_url"]),
        "VIDEOINSIGHT_WORKER_TOKEN": secrets.token_urlsafe(32),
        "VIDEOINSIGHT_RUNTIME_ROOT": str(runtime_root),
        "VIDEOINSIGHT_FRONTEND_DIST": str(root / "project" / "frontend" / "dist"),
        "VIDEOINSIGHT_BACKEND_ORIGIN": f"http://127.0.0.1:{port or DESKTOP_PORT}",
        "ASR_MODE": "sandbox",
        "VIDEO_EDITOR_PROVIDER_MODE": "sandbox",
        "CRAWLER_PROVIDER_MODE": "sandbox",
        "COPYWRITING_MODE": "sandbox",
        "AVATAR_PROVIDER_MODE": "sandbox",
        "CRAWLER_ONEAPI_AUTO_ENABLED": "false",
        "DOUYIN_OFFICIAL_HOT_ENABLED": "false",
        "DOUYIN_HOT_WORDS_ENABLED": "false",
        "DEFAULT_CREDIT_BALANCE": "0",
    }
    for key, value in settings.items():
        os.environ[key] = value
    blocked_secret_keys = set(DESKTOP_BLOCKED_SECRET_KEYS)
    for key in tuple(os.environ):
        if key.upper() in blocked_secret_keys:
            os.environ.pop(key, None)
    return control_plane_enabled


def _configure_logging(root: Path) -> None:
    log_directory = root / "data" / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_directory / "desktop.log",
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _health_ready(timeout_seconds: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout_seconds) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read(2048))
            return (
                payload.get("status") == "ok"
                and payload.get("service") == "videoinsight-desktop-api"
                and payload.get("desktop_protocol") == "2"
                and payload.get("desktop_client") is True
                and payload.get("desktop_demo")
                == (os.getenv("VIDEOINSIGHT_CONTROL_PLANE_ENABLED", "false").casefold() != "true")
                and payload.get("control_plane_enabled")
                == (os.getenv("VIDEOINSIGHT_CONTROL_PLANE_ENABLED", "false").casefold() == "true")
            )
    except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _runtime_state_path(runtime_root: Path) -> Path:
    return runtime_root / "data" / "desktop-runtime.json"


def _runtime_pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A process we cannot inspect is safer to treat as live than delete.
        return True
    return True


def _clear_stale_runtime_state(runtime_root: Path) -> bool:
    """Remove only a runtime record whose recorded backend PID is gone."""
    destination = _runtime_state_path(runtime_root)
    if not destination.exists():
        return False
    try:
        payload = json.loads(destination.read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
    except (OSError, ValueError, json.JSONDecodeError):
        destination.unlink(missing_ok=True)
        logging.warning("已移除无效的 desktop-runtime.json")
        return True
    if _runtime_pid_is_alive(pid):
        return False
    destination.unlink(missing_ok=True)
    logging.warning("已移除已退出本地服务的运行时记录 pid=%s", pid)
    return True


def _clear_own_runtime_state(runtime_root: Path) -> bool:
    """Do not remove a newer backend's record when this process exits."""
    destination = _runtime_state_path(runtime_root)
    try:
        payload = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if int(payload.get("pid") or 0) != os.getpid():
        return False
    destination.unlink(missing_ok=True)
    logging.info("本地服务已清理运行时记录 pid=%s", os.getpid())
    return True


def _write_runtime_state(runtime_root: Path, port: int) -> None:
    data_directory = runtime_root / "data"
    data_directory.mkdir(parents=True, exist_ok=True)
    destination = _runtime_state_path(runtime_root)
    temporary = data_directory / f".desktop-runtime-{os.getpid()}.tmp"
    payload = {
        "schema_version": 1,
        "pid": os.getpid(),
        "port": port,
        "origin": f"http://127.0.0.1:{port}",
        "started_at": datetime.now().astimezone().isoformat(),
    }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, destination)


def _show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, message, "VideoInsight 启动失败", 0x10)


def _allow_local_demo() -> bool:
    """Keep the legacy demo account available only while running source code.

    A packaged customer build must fail closed when its company control-plane
    configuration is missing or invalid.  Otherwise deleting one JSON file
    would turn the commercial client back into a locally authoritative demo.
    """

    return not bool(getattr(sys, "frozen", False))


def _open_when_ready() -> None:
    for _ in range(120):
        if _health_ready():
            if os.getenv("VIDEOINSIGHT_NO_BROWSER", "").casefold() != "true":
                webbrowser.open(APP_URL)
            return
        time.sleep(0.25)
    _show_error("系统启动超时，请查看本机 VideoInsight 数据目录中的 desktop.log。")


def _ensure_demo_customer() -> None:
    from project.backend.app.core.config import DATABASE_PATH
    from src.models import CustomerCode
    from src.repositories.sqlite import SQLiteRepository

    repository = SQLiteRepository(DATABASE_PATH)
    if repository.get_customer_code(DEMO_ACTIVATION_CODE) is not None:
        return
    now = datetime.now().astimezone()
    repository.create_customer_codes(
        [
            CustomerCode(
                code=DEMO_ACTIVATION_CODE,
                name="8月封闭内测",
                initial_credits="99999",
                created_at=now,
                updated_at=now,
            )
        ]
    )


def main() -> int:
    multiprocessing.freeze_support()
    root = _application_root()
    runtime_root = _runtime_root(root)
    try:
        port = _resolve_desktop_port()
    except ValueError as exc:
        _show_error(str(exc))
        return 1
    _configure_local_urls(port)
    control_plane_enabled = _configure_desktop_environment(root, runtime_root, port)
    _configure_logging(runtime_root)
    _clear_stale_runtime_state(runtime_root)

    if _health_ready():
        if os.getenv("VIDEOINSIGHT_NO_BROWSER", "").casefold() != "true":
            webbrowser.open(APP_URL)
        return 0
    if _port_in_use(port):
        _show_error("本机服务端口刚被其他程序占用，请重新启动 VideoInsight。")
        return 1
    if not control_plane_enabled and not _allow_local_demo():
        logging.error("Packaged desktop control-plane configuration is unavailable")
        _show_error("公司服务配置缺失或无效，请联系服务人员重新安装正式版本。")
        return 1

    runtime_state_written = False
    try:
        if not control_plane_enabled:
            _ensure_demo_customer()
        from project.backend.app.desktop import app
        import uvicorn

        _write_runtime_state(runtime_root, port)
        runtime_state_written = True
        logging.info("本地服务启动 pid=%s port=%s", os.getpid(), port)
        threading.Thread(target=_open_when_ready, daemon=True).start()
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=port,
            proxy_headers=False,
            access_log=False,
            log_config=None,
        )
        logging.info("本地服务正常停止 pid=%s", os.getpid())
        return 0
    except Exception:
        logging.exception("Desktop application failed to start")
        _show_error("系统未能启动，请查看本机 VideoInsight 数据目录中的 desktop.log。")
        return 1
    finally:
        if runtime_state_written:
            _clear_own_runtime_state(runtime_root)


if __name__ == "__main__":
    raise SystemExit(main())
