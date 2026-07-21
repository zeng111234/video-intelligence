"""系统管理 API。"""

from __future__ import annotations

import platform
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from project.backend.app.core.config import DATABASE_PATH
from project.backend.app.core.deps import get_repository
from project.backend.app.schemas.responses import AdminStatusResponse

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class DashboardStatsResponse(BaseModel):
    """Dashboard 统计数据响应。"""
    totalVideos: int = 0
    todayProduced: int = 0
    totalCandidates: int = 0
    todayCandidates: int = 0
    activeTasks: int = 0
    completedTasks: int = 0
    failedTasks: int = 0
    totalTasks: int = 0
    successRate: float = 0.0
    pipelineCount: int = 0


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


@router.get("/dashboard/stats", response_model=DashboardStatsResponse)
def dashboard_stats(
    repo=Depends(get_repository),
):
    """获取 Dashboard 统计数据，基于数据库真实数据。"""
    from datetime import date

    today = date.today().isoformat()

    # 候选素材统计
    candidates = repo.list_candidates()
    total_candidates = len(candidates)
    today_candidates = sum(
        1 for c in candidates
        if getattr(c, "published_at", None)
        and str(getattr(c, "published_at", "")).startswith(today)
    )

    # 任务统计
    tasks = repo.list_tasks()
    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.status.value == "succeeded")
    failed_tasks = sum(1 for t in tasks if t.status.value == "failed")
    active_tasks = sum(1 for t in tasks if t.status.value in ("running", "pending"))
    success_rate = round(completed_tasks / total_tasks * 100, 1) if total_tasks > 0 else 0.0

    # 流水线统计
    pipeline_runs = repo.list_pipeline_runs(limit=9999)
    pipeline_count = len(pipeline_runs)

    # 已生产视频数（从已完成的流水线汇总）
    total_videos = sum(
        len(run.stages) for run in pipeline_runs
        if run.status.value == "succeeded"
    ) if pipeline_runs else 0

    return DashboardStatsResponse(
        totalVideos=total_videos,
        todayProduced=0,
        totalCandidates=total_candidates,
        todayCandidates=today_candidates,
        activeTasks=active_tasks,
        completedTasks=completed_tasks,
        failedTasks=failed_tasks,
        totalTasks=total_tasks,
        successRate=success_rate,
        pipelineCount=pipeline_count,
    )
