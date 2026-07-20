"""系统管理 API。"""

from __future__ import annotations

import platform
import sqlite3
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from project.backend.app.core.config import DATABASE_PATH
from project.backend.app.core.deps import get_repository
from project.backend.app.schemas.responses import AdminStatusResponse

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _get_db_migration_version(db_path) -> int | None:
    """读取 SQLite user_version 作为迁移版本号。"""
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        return version
    except Exception:
        return None


def _get_migration_details(db_path) -> dict:
    """获取迁移框架的详细状态。"""
    try:
        from database.migrations.runner import MigrationRunner

        runner = MigrationRunner(db_path)
        status = runner.status()
        return {
            "current_version": status.current_version,
            "target_version": status.target_version,
            "pending_count": len(status.pending),
            "applied_count": len(status.applied),
            "pending": [
                {"version": m.version, "description": m.description}
                for m in status.pending
            ],
            "applied": [
                {"version": m.version, "description": m.description}
                for m in status.applied
            ],
            "is_up_to_date": len(status.pending) == 0,
        }
    except Exception:
        return {}


@router.get("/status", response_model=AdminStatusResponse)
def admin_status(
    repo=Depends(get_repository),
):
    """获取系统状态。"""
    candidate_count = len(repo.list_candidates())
    task_count = len(repo.list_tasks())
    pipeline_runs = repo.list_pipeline_runs(limit=9999)
    migration_version = _get_db_migration_version(DATABASE_PATH)
    migration_details = _get_migration_details(DATABASE_PATH)

    return AdminStatusResponse(
        status="ok",
        repository_type=type(repo).__name__,
        database_path=str(DATABASE_PATH) if DATABASE_PATH.exists() else None,
        candidate_count=candidate_count,
        task_count=task_count,
        pipeline_count=len(pipeline_runs),
        migration_version=migration_version,
        migration_details=migration_details,
        python_version=sys.version.split()[0],
        platform_info=platform.system(),
    )
