from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from src.models import TaskStatus, TranscriptionTask
from src.repositories.mock import MockRepository
from src.services.transcription_worker import TranscriptionWorker


def _queued_cloud_task() -> TranscriptionTask:
    now = datetime.now().astimezone()
    return TranscriptionTask(
        task_id="cloud-worker-queued",
        title="待恢复转写",
        status=TaskStatus.QUEUED,
        progress=0,
        created_at=now,
        updated_at=now,
        media_name="source.mp4",
        media_type="video/mp4",
        rights_confirmed=True,
        provider_name="aliyun_fun_asr",
    )


def test_transcription_worker_preserves_queue_until_owner_session_is_ready():
    repository = MockRepository(tasks=[])
    queued = _queued_cloud_task()
    repository.save_task(queued)
    processed: list[str] = []
    service = SimpleNamespace(
        repository=repository,
        process_cloud_task=lambda task_id: processed.append(task_id),
    )
    worker = TranscriptionWorker(service, can_process=lambda: False)

    assert worker.tick_once() is None
    assert processed == []
    assert repository.get_task(queued.task_id).status == TaskStatus.QUEUED


def test_transcription_worker_processes_one_task_when_owner_session_is_ready():
    repository = MockRepository(tasks=[])
    queued = _queued_cloud_task()
    repository.save_task(queued)
    processed: list[str] = []
    service = SimpleNamespace(
        repository=repository,
        process_cloud_task=lambda task_id: processed.append(task_id) or queued,
    )
    worker = TranscriptionWorker(service, can_process=lambda: True)

    assert worker.tick_once() is queued
    assert processed == [queued.task_id]


def test_transcription_worker_does_not_claim_inline_task():
    repository = MockRepository(tasks=[])
    inline = _queued_cloud_task().model_copy(
        update={"outputs": {"processing_mode": "inline"}}
    )
    repository.save_task(inline)
    processed: list[str] = []
    service = SimpleNamespace(
        repository=repository,
        process_cloud_task=lambda task_id: processed.append(task_id),
    )

    worker = TranscriptionWorker(service, can_process=lambda: True)

    assert worker.tick_once() is None
    assert processed == []
