from datetime import datetime

from src.models import (
    PipelineRun,
    PublishPlatform,
    PublishStatus,
    PublishTask,
    PublishTarget,
    TaskStatus,
)
from src.services.production import ProductionService
from src.services.publish_metadata import suggested_publish_draft, validated_publish_draft


def test_suggested_draft_never_inherits_source_title_or_hashtags():
    draft = suggested_publish_draft(
        approved_script="机器人也失业，最终谁输谁赢？市场才是关键。",
        creative_plan=None,
        profile_tags=["商业思维", "AI获客", "商业思维"],
    )

    assert draft["title"] == "机器人也失业，最终谁输谁赢"
    assert draft["tags"] == ["商业思维", "AI获客"]
    assert "石杨兵" not in " ".join([draft["title"], draft["description"], *draft["tags"]])


def test_publish_draft_collapses_duplicate_title_and_rejects_embedded_hashtags():
    draft = validated_publish_draft(
        title="机器人也失业，最终谁输谁赢？机器人也失业，最终谁输谁赢？",
        description="正文",
        tags=["商业思维", "#商业思维"],
    )

    assert draft["title"] == "机器人也失业，最终谁输谁赢？"
    assert draft["tags"] == ["商业思维"]


def test_suggested_draft_infers_complete_tags_when_profile_has_none():
    draft = suggested_publish_draft(
        approved_script="机器人也失业了。工人没有岗位，工厂没有订单，企业要重新理解市场和消费。",
        creative_plan=None,
        profile_tags=[],
    )

    assert draft["tags"] == ["机器人产业", "就业观察", "商业思维", "企业经营"]


def test_legacy_publish_task_uses_safe_script_metadata_in_workspace():
    now = datetime.now().astimezone()
    run = PipelineRun(keyword="旧任务")
    task = PublishTask(
        task_id="legacy-publish",
        title="旧发布任务",
        status=TaskStatus.PAUSED,
        progress=60,
        created_at=now,
        updated_at=now,
        video_path="result.mp4",
        target=PublishTarget(
            platform=PublishPlatform.DOUYIN,
            title="机器人也失业，如今到底谁输谁赢？ 机器人也失业，如今到底谁输谁赢？#商业思维#石杨兵",
            description="旧描述 #石杨兵",
            tags=["石杨兵"],
        ),
        publish_status=PublishStatus.MANUAL_READY,
        provider_name="douyin_local_browser",
    )

    draft = ProductionService._workspace_publish_draft(
        run=run,
        publish_tasks=[task],
        profile_tags=[],
        approved_script_text="你发现没，机器人最近也被裁员了。让人有活干，企业才有未来。",
    )

    assert draft is not None
    assert draft["title"] == "你发现没，机器人最近也被裁员了"
    assert draft["description"] == "你发现没，机器人最近也被裁员了。让人有活干，企业才有未来。"
    assert draft["tags"] == ["机器人产业", "就业观察", "企业经营"]
    assert "石杨兵" not in str(draft)
