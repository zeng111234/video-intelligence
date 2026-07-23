from datetime import datetime

import pytest

from src.models import PublishPlatform, PublishStatus, PublishTarget, PublishTask, TaskStatus
from src.repositories.mock import MockRepository
from src.services.feedback import FeedbackService


def _published_task(*, task_id: str = "pub-feedback", is_mock: bool = False) -> PublishTask:
    now = datetime.now().astimezone()
    return PublishTask(
        task_id=task_id, title="已发布作品", status=TaskStatus.SUCCEEDED, progress=100,
        created_at=now, updated_at=now, video_path="C:/video.mp4", target=PublishTarget(platform=PublishPlatform.DOUYIN, title="测试"),
        publish_status=PublishStatus.SUCCEEDED, is_mock=is_mock, source_pipeline_run_id="pipeline-feedback",
    )


def test_feedback_requires_confirmed_real_publish_and_generates_cautious_review(tmp_path):
    repository = MockRepository()
    repository.save_task(_published_task())
    service = FeedbackService(repository, tmp_path)
    item = service.record(publish_task_id="pub-feedback", views=1000, likes=50, comments=10, leads=0, recorded_by="运营")
    assert item.platform == "douyin"
    review = service.recommendations()
    assert review["sample_size"] == 1
    assert "样本少于 3 条" in " ".join(review["recommendations"])
    with pytest.raises(ValueError, match="已回填"):
        service.record(publish_task_id="pub-feedback", views=1, likes=0, comments=0, leads=0, recorded_by="运营")


def test_feedback_rejects_mock_publish(tmp_path):
    repository = MockRepository()
    repository.save_task(_published_task(is_mock=True))
    with pytest.raises(ValueError, match="真实发布任务"):
        FeedbackService(repository, tmp_path).record(publish_task_id="pub-feedback", views=1, likes=0, comments=0, leads=0, recorded_by="运营")
