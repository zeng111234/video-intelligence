"""Run an isolated, non-billable backend for local browser acceptance.

The launcher refuses normal project data directories, clears every permanent
supplier credential inherited from the developer machine, forces all paid
providers into sandbox mode, and seeds disposable customer/admin identities.
It is intentionally separate from the production launcher.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path


QA_CUSTOMER_CODE = "LOCALQA8"
QA_ADMIN_USERNAME = "qa_admin"
QA_ADMIN_PASSWORD = "LocalQAAdmin2026Pass"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=2101)
    return parser.parse_args()


def _safe_runtime_root(value: Path) -> Path:
    root = value.expanduser().resolve()
    if "videoinsight-local-acceptance-" not in root.name.casefold():
        raise SystemExit(
            "验收运行目录必须以 VideoInsight-local-acceptance- 开头，"
            "避免误用真实客户数据目录。"
        )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _configure_environment(runtime_root: Path) -> None:
    from scripts.desktop_launcher import DESKTOP_BLOCKED_SECRET_KEYS

    settings = {
        "APP_ENV": "development",
        "ENABLE_DOCS": "false",
        "VIDEOINSIGHT_RUNTIME_ROOT": str(runtime_root),
        "VIDEOINSIGHT_DESKTOP_CLIENT": "true",
        "VIDEOINSIGHT_DESKTOP_DEMO": "true",
        "VIDEOINSIGHT_DEMO_OWNER": QA_CUSTOMER_CODE,
        "VIDEOINSIGHT_CONTROL_PLANE_ENABLED": "false",
        "VIDEOINSIGHT_CONTROL_PLANE_URL": "",
        "VIDEOINSIGHT_WORKER_TOKEN": secrets.token_urlsafe(32),
        "VIDEOINSIGHT_NO_BROWSER": "true",
        "ADMIN_PASSWORD": QA_ADMIN_PASSWORD,
        "API_KEY": "local-acceptance-api-key",
        "DEFAULT_CREDIT_BALANCE": "0",
        "ASR_MODE": "sandbox",
        "VIDEO_EDITOR_PROVIDER_MODE": "sandbox",
        "CRAWLER_PROVIDER_MODE": "sandbox",
        "COPYWRITING_MODE": "sandbox",
        "AVATAR_PROVIDER_MODE": "sandbox",
        "CRAWLER_ONEAPI_AUTO_ENABLED": "false",
        "DOUYIN_OFFICIAL_HOT_ENABLED": "false",
        "DOUYIN_HOT_WORDS_ENABLED": "false",
        "DOUYIN_LOCAL_BROWSER_ENABLED": "false",
        "DOUYIN_BROWSER_DISCOVERY_ENABLED": "false",
        "XIAOHONGSHU_LOGIN_BROWSER_ENABLED": "false",
        "KUAISHOU_BROWSER_DISCOVERY_ENABLED": "false",
        "BILIBILI_BROWSER_DISCOVERY_ENABLED": "false",
    }
    os.environ.update(settings)
    for key in DESKTOP_BLOCKED_SECRET_KEYS:
        os.environ.pop(key, None)


def _seed_disposable_accounts() -> None:
    from database.migrations.runner import MigrationRunner
    from project.backend.app.core.config import DATABASE_PATH
    from project.backend.app.core.security import hash_password
    from src.models import AdminAccount, CustomerCode
    from src.repositories.sqlite import SQLiteRepository

    MigrationRunner(DATABASE_PATH).upgrade()
    repository = SQLiteRepository(DATABASE_PATH)
    now = datetime.now().astimezone()
    if repository.get_customer_code(QA_CUSTOMER_CODE) is None:
        repository.create_customer_codes(
            [
                CustomerCode(
                    code=QA_CUSTOMER_CODE,
                    name="本地整体验收",
                    initial_credits="400",
                    created_at=now,
                    updated_at=now,
                )
            ]
        )
    if repository.get_admin_account(QA_ADMIN_USERNAME) is None:
        repository.create_admin_account(
            AdminAccount(
                username=QA_ADMIN_USERNAME,
                password_hash=hash_password(QA_ADMIN_PASSWORD),
                created_at=now,
                updated_at=now,
            )
        )


def main() -> int:
    args = _parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit("验收端口必须在 1024 到 65535 之间。")
    runtime_root = _safe_runtime_root(args.runtime_root)
    _configure_environment(runtime_root)
    _seed_disposable_accounts()

    import uvicorn

    uvicorn.run(
        "project.backend.app.main:app",
        host="127.0.0.1",
        port=args.port,
        proxy_headers=False,
        access_log=False,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
