"""转写历史批量清空的 API 回归测试。"""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from project.backend.app.core import deps as backend_deps
from project.backend.app.main import app
from src.models import CopywritingTask, TaskStatus, TranscriptionTask
from src.repositories.mock import MockRepository


def test_clear_transcription_history_deletes_only_transcriptions() -> None:
    now = datetime.now().astimezone()
    repository = MockRepository(tasks=[])
    transcription = TranscriptionTask(
        task_id="asr-history-1",
        title="转写任务",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        media_name="history.mp4",
        media_type="video/mp4",
        rights_confirmed=True,
    )
    copywriting = CopywritingTask(
        task_id="copy-keep-1",
        title="保留的文案任务",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
    )
    repository.save_task(transcription)
    repository.save_task(copywriting)
    app.dependency_overrides[backend_deps.get_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            response = client.delete("/api/v1/transcriptions/history")
    finally:
        app.dependency_overrides.pop(backend_deps.get_repository, None)

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted_count": 1}
    assert repository.get_task(transcription.task_id) is None
    assert repository.get_task(copywriting.task_id) is not None
