"""健康检查端点单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

# 确保项目根目录在 Python 路径中
_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from project.backend.app.main import app  # noqa: E402


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_non_sensitive_desktop_mode(monkeypatch):
    monkeypatch.setenv("VIDEOINSIGHT_DESKTOP_CLIENT", "true")
    monkeypatch.setenv("VIDEOINSIGHT_DESKTOP_DEMO", "true")
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_ENABLED", "false")
    response = TestClient(app).get("/health")
    payload = response.json()
    assert payload["desktop_client"] is True
    assert payload["desktop_demo"] is True
    assert payload["control_plane_enabled"] is False
    assert "token" not in payload
    assert "owner" not in payload


def test_health_exposes_only_active_task_counts(monkeypatch):
    """安装器要在覆盖安装前知道有没有任务在跑。

    这个接口免登录可达，因此只能暴露**数量**：不能出现任务 ID、关键词、
    文件路径或任何客户内容。
    """
    from src.models import TaskKind, TaskStatus
    from src.repositories import MockRepository

    # 演示仓库会预置示例任务（其中有一条 running），必须显式清空，
    # 否则计数会被预置数据干扰。
    repository = MockRepository(candidates=[], tasks=[])
    repository.save_task(
        _task("task-running", TaskStatus.RUNNING, TaskKind.TRANSCRIPTION)
    )
    repository.save_task(
        _task("task-queued", TaskStatus.QUEUED, TaskKind.VIDEO_EDITING)
    )
    # 已结束的任务不计入。
    repository.save_task(
        _task("task-done", TaskStatus.SUCCEEDED, TaskKind.COPYWRITING)
    )

    monkeypatch.setattr(
        "project.backend.app.core.repository.get_repository", lambda: repository
    )
    # 计数有 2 秒缓存，避免启动器轮询 /health 时反复全表扫描。
    from project.backend.app import main as main_module

    main_module._activity_snapshot_cache["value"] = None

    payload = TestClient(app).get("/health").json()

    assert payload["active_task_count"] == 2
    assert payload["active_tasks_by_kind"] == {
        "transcription": 1,
        "video_editing": 1,
    }
    # 只允许出现数量，不得泄露任务内容。
    serialized = str(payload)
    assert "task-running" not in serialized
    assert "task-queued" not in serialized
    assert "task-done" not in serialized


def test_health_still_works_when_task_counting_fails(monkeypatch):
    """统计失败不能把健康检查带崩——安装器依赖它判断服务是否就绪。"""

    def _boom():
        raise RuntimeError("repository unavailable")

    monkeypatch.setattr(
        "project.backend.app.core.repository.get_repository", _boom
    )
    from project.backend.app import main as main_module

    main_module._activity_snapshot_cache["value"] = None

    payload = TestClient(app).get("/health").json()

    assert payload["status"] == "ok"
    assert payload["desktop_protocol"] == "2"
    assert payload["active_task_count"] == 0


def _task(task_id: str, status, kind):
    from datetime import datetime

    from src.models import TaskRecord

    now = datetime.now().astimezone()
    return TaskRecord(
        task_id=task_id,
        kind=kind,
        status=status,
        progress=0,
        title=f"标题 {task_id}",
        created_at=now,
        updated_at=now,
    )
