"""智能剪辑工作流的系统素材与字幕复核门禁测试。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.models import AvatarTask, AvatarProviderStatus, TaskStatus, VideoEditTask, VideoEditorBatch
from src.repositories.mock import MockRepository
from src.repositories.sqlite import SQLiteRepository
from src.resources import asr_model_status
from src.services.video_editor_workflow import (
    VideoEditorWorkflowError,
    VideoEditorWorkflowService,
)
import src.services.video_editor_workflow as workflow_module


class _TranscriptionStub:
    def __init__(self, approved=None):
        self.approved = approved

    def get_approved_revision(self, task_id: str):
        return self.approved


class _VideoEditingStub:
    def __init__(self, output_directory: Path):
        self.output_directory = output_directory


def _avatar_task(task_id: str, result_path: Path | None, *, is_mock: bool = False) -> AvatarTask:
    now = datetime.now().astimezone()
    return AvatarTask(
        task_id=task_id,
        title="系统数字人成片",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        script_text="测试口播",
        avatar_id="avatar-1",
        avatar_name="测试数字人",
        voice_id="voice-1",
        voice_name="测试声音",
        rights_holder="测试用户",
        rights_confirmed_at=now,
        idempotency_key=f"key-{task_id}",
        provider_name="local",
        provider_status=AvatarProviderStatus.SUCCEEDED,
        result_path=str(result_path) if result_path else None,
        is_mock=is_mock,
    )


def _analysis_task(source_path: Path) -> VideoEditTask:
    now = datetime.now().astimezone()
    return VideoEditTask(
        task_id="analysis-test",
        title="智能分析",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        source_video_path=str(source_path),
        is_mock=False,
        outputs={
            "workflow": "analysis",
            "source_id": "avatar:avatar-real",
            "transcription_task_id": "transcript-test",
        },
    )


def test_list_sources_only_exposes_real_existing_system_media(tmp_path: Path):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    repo.save_task(_avatar_task("avatar-real", video))
    repo.save_task(_avatar_task("avatar-mock", video, is_mock=True))
    repo.save_task(_avatar_task("avatar-missing", tmp_path / "missing.mp4"))
    service = VideoEditorWorkflowService(repo, None, _TranscriptionStub(), None)

    sources = service.list_sources()

    assert [item["source_id"] for item in sources] == ["avatar:avatar-real"]
    assert sources[0]["source_type"] == "avatar"
    assert sources[0]["media_url"].endswith("/avatar:avatar-real/media")


def test_subtitle_enabled_job_requires_an_approved_revision(tmp_path: Path):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    repo.save_task(_avatar_task("avatar-real", video))
    repo.save_task(_analysis_task(video))
    service = VideoEditorWorkflowService(repo, None, _TranscriptionStub(approved=None), None)

    with pytest.raises(VideoEditorWorkflowError, match="字幕尚未完成复核确认"):
        service.create_edit_job(analysis_id="analysis-test", steps=[], subtitle_enabled=True)


def test_upload_source_is_persisted_and_listed(tmp_path: Path):
    repo = MockRepository(tasks=[])
    service = VideoEditorWorkflowService(
        repo,
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )

    source = service.upload_source(
        file_name="demo.mp4",
        media_type="video/mp4",
        media_bytes=b"fake-mp4",
        rights_confirmed=True,
        rights_holder="测试用户",
    )

    assert source["source_id"].startswith("upload:")
    assert source["source_type"] == "upload"
    assert Path(source["_path"]).is_file()
    assert service.list_sources()[0]["source_id"] == source["source_id"]


def test_product_showcase_uses_authorized_visual_assets_without_restarting_avatar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    repo.save_task(_avatar_task("avatar-real", video))
    service = VideoEditorWorkflowService(
        repo,
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    product = service.upload_visual_asset(
        kind="product",
        file_name="product.png",
        media_type="image/png",
        media_bytes=b"\x89PNG\r\n\x1a\nproduct",
        rights_confirmed=True,
        rights_holder="测试公司",
    )
    background = service.upload_visual_asset(
        kind="background",
        file_name="background.webp",
        media_type="image/webp",
        media_bytes=b"RIFF\x00\x00\x00\x00WEBPbackground",
        rights_confirmed=True,
        rights_holder="测试公司",
    )
    monkeypatch.setattr(workflow_module._WORKFLOW_EXECUTOR, "submit", lambda *args, **kwargs: None)

    task = service.create_product_showcase_job(
        source_id="avatar:avatar-real",
        product_asset_id=product["asset_id"],
        background_asset_id=background["asset_id"],
        layout="product_canvas_avatar_pip",
    )

    assert task.outputs["workflow"] == "product_showcase"
    assert task.source_avatar_task_id == "avatar-real"
    assert task.edit_config.steps[0].kind.value == "product_showcase"
    assert task.edit_config.steps[0].params["product_path"] == product["_path"]
    assert task.edit_config.steps[0].params["background_path"] == background["_path"]
    assert service.get_edit_task(task.task_id).task_id == task.task_id


def test_visual_asset_rejects_a_file_with_an_incorrect_image_signature(tmp_path: Path):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )

    with pytest.raises(VideoEditorWorkflowError, match="图片内容与文件格式不匹配"):
        service.upload_visual_asset(
            kind="product",
            file_name="not-an-image.png",
            media_type="image/png",
            media_bytes=b"not-an-image",
            rights_confirmed=True,
            rights_holder="测试公司",
        )


def test_batch_waits_for_subtitle_then_confirms_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    repo.save_task(_avatar_task("avatar-real", video))
    transcript = _TranscriptionStub(approved=None)
    service = VideoEditorWorkflowService(repo, _VideoEditingStub(tmp_path / "edits"), transcript, None)

    def fake_create_analysis(**kwargs):
        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id="analysis-batch",
            title="分析",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            source_video_path=str(video),
            is_mock=False,
            outputs={
                "workflow": "analysis",
                "source_id": kwargs["source_id"],
                "transcription_task_id": "transcript-batch",
                "analysis_json": "{\"recommended_steps\": []}",
            },
        )
        repo.save_task(task)
        return task

    def fake_create_edit_job(**kwargs):
        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id="edit-batch",
            title="剪辑",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            source_video_path=str(video),
            is_mock=False,
            outputs={"workflow": "edit", "source_id": "avatar:avatar-real", "analysis_id": kwargs["analysis_id"]},
        )
        repo.save_task(task)
        return task

    monkeypatch.setattr(service, "create_analysis", fake_create_analysis)
    monkeypatch.setattr(service, "create_edit_job", fake_create_edit_job)
    created = service.create_batch(
        source_ids=["avatar:avatar-real"],
        target_platform="douyin",
        subtitle_enabled=True,
        subtitle_model="large-v3-turbo",
        steps=[],
        output_format="mp4",
        output_resolution="1080x1920",
        output_fps=30,
        output_bitrate="4M",
    )
    item = created["items"][0]

    waiting = service.get_batch(created["batch_id"])
    assert waiting["items"][0]["status"] == "awaiting_subtitle_review"
    with pytest.raises(VideoEditorWorkflowError, match="请先在当前页保存并确认字幕成稿"):
        service.continue_batch_item(created["batch_id"], item["item_id"])

    transcript.approved = object()
    rendering = service.continue_batch_item(created["batch_id"], item["item_id"])
    assert rendering["items"][0]["status"] == "rendering"

    current = repo.get_task("edit-batch")
    assert isinstance(current, VideoEditTask)
    result = current.model_copy(update={"status": TaskStatus.SUCCEEDED, "progress": 100, "stage": "剪辑完成", "result_path": str(video), "result_size_bytes": video.stat().st_size})
    repo.save_task(result)
    ready = service.get_batch(created["batch_id"])
    assert ready["items"][0]["status"] == "awaiting_output_confirmation"

    confirmed = service.confirm_batch_results(created["batch_id"], [item["item_id"]])
    assert confirmed["items"][0]["status"] == "ready_to_publish"


def test_sqlite_repository_persists_editor_batches(tmp_path: Path):
    database_path = tmp_path / "video.db"
    created = VideoEditorBatch(items=[])

    SQLiteRepository(database_path).save_video_editor_batch(created)
    restored = SQLiteRepository(database_path).get_video_editor_batch(created.batch_id)

    assert restored == created


def test_local_title_candidates_are_zero_config_and_grounded():
    titles = VideoEditorWorkflowService._local_title_candidates(
        "本地上传 · 台球教学.mp4",
        "普通人练习台球时，先把站姿稳定下来。然后再调整出杆动作。",
        "douyin",
    )

    assert titles == [
        "普通人练习台球时，先把站姿稳定下来",
        "看懂普通人练习台球时，先把站姿稳定下来",
        "别错过：普通人练习台球时，先把站姿稳定下来",
    ]


def test_authorized_bgm_is_added_to_batch_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    repo.save_task(_avatar_task("avatar-real", video))
    service = VideoEditorWorkflowService(
        repo,
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    monkeypatch.setattr(service, "_probe_bgm_duration", lambda _: 12.0)
    bgm = service.upload_bgm(
        file_name="calm.mp3",
        media_type="audio/mpeg",
        media_bytes=b"licensed-audio",
        mood="舒缓",
        rights_confirmed=True,
        rights_holder="测试公司",
    )

    def fake_create_analysis(**kwargs):
        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id="analysis-bgm",
            title="分析",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            source_video_path=str(video),
            is_mock=False,
            outputs={
                "workflow": "analysis",
                "source_id": kwargs["source_id"],
                "analysis_json": (
                    '{"recommended_steps":[],"title_candidates":["台球教程"],'
                    '"media":{"duration_seconds":10,"has_audio":true},'
                    '"audio":{"mean_volume_db":-16}}'
                ),
            },
        )
        repo.save_task(task)
        return task

    captured: dict = {}

    def fake_create_edit_job(**kwargs):
        captured.update(kwargs)
        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id="edit-bgm",
            title="剪辑",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            source_video_path=str(video),
            is_mock=False,
            outputs={"workflow": "edit", "source_id": "avatar:avatar-real", "analysis_id": kwargs["analysis_id"]},
        )
        repo.save_task(task)
        return task

    monkeypatch.setattr(service, "create_analysis", fake_create_analysis)
    monkeypatch.setattr(service, "create_edit_job", fake_create_edit_job)
    created = service.create_batch(
        source_ids=["avatar:avatar-real"],
        target_platform="douyin",
        subtitle_enabled=False,
        subtitle_model="base",
        steps=[],
        output_format="mp4",
        output_resolution="720x1280",
        output_fps=25,
        output_bitrate="2M",
        bgm_enabled=True,
        bgm_id=None,
        bgm_volume=0.24,
    )
    running = service.get_batch(created["batch_id"])

    assert running["items"][0]["status"] == "rendering"
    assert running["items"][0]["selected_title"] == "台球教程"
    assert captured["publish_title"] == "台球教程"
    assert captured["steps"][-1]["kind"] == "background_music"
    assert captured["steps"][-1]["params"]["ducking"] is True
    assert captured["steps"][-1]["params"]["auto_adjusted"] is True
    assert captured["steps"][-1]["params"]["bgm_volume"] == 0.24
    assert running["items"][0]["selected_bgm_id"] == bgm["asset_id"]
    assert "AI 阅读转写文案与标题" in running["items"][0]["bgm_reason"]

    service.select_batch_item_title(
        created["batch_id"],
        running["items"][0]["item_id"],
        "人工选择的新标题",
    )
    edit_task = repo.get_task("edit-bgm")
    assert isinstance(edit_task, VideoEditTask)
    assert edit_task.outputs["publish_title"] == "人工选择的新标题"


def test_bgm_recommendation_prefers_ai_voiceover_category(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    monkeypatch.setattr(service, "_probe_bgm_duration", lambda _: 120.0)
    knowledge = service.upload_bgm(
        file_name="knowledge.mp3",
        media_type="audio/mpeg",
        media_bytes=b"knowledge",
        mood="知识·讲解·平稳",
        rights_confirmed=True,
        rights_holder="测试公司",
        voiceover_category="理性干货",
        energy="克制",
    )
    service.upload_bgm(
        file_name="emotion.mp3",
        media_type="audio/mpeg",
        media_bytes=b"emotion",
        mood="温柔·治愈·钢琴",
        rights_confirmed=True,
        rights_holder="测试公司",
        voiceover_category="情绪共鸣",
        energy="平稳",
    )

    selected, reason = service._recommend_bgm_asset(
        {
            "transcript": "今天解释一个机器人的工作原理。",
            "media": {"duration_seconds": 60},
            "edit_plan": {
                "bgm_category": "理性干货",
                "bgm_energy": "克制",
                "bgm_keywords": ["知识", "讲解"],
            },
        },
        "机器人原理",
    )

    assert selected is not None
    assert selected["asset_id"] == knowledge["asset_id"]
    assert "理性干货" in reason


def test_bgm_recommendation_does_not_auto_select_content_id_registered_track(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    monkeypatch.setattr(service, "_probe_bgm_duration", lambda _: 120.0)
    service.upload_bgm(
        file_name="pixabay-risk.mp3",
        media_type="audio/mpeg",
        media_bytes=b"pixabay",
        mood="故事·叙事",
        rights_confirmed=True,
        rights_holder="作者 via Pixabay",
        voiceover_category="故事叙事",
        source_provider="pixabay",
        source_url="https://pixabay.com/music/example/",
        license_url="https://pixabay.com/service/license-summary/",
        content_id_risk="registered",
    )

    selected, reason = service._recommend_bgm_asset(
        {"transcript": "讲一个故事。", "media": {"duration_seconds": 30}},
        "故事",
    )

    assert selected is None
    assert "版权识别" in reason


def test_auto_bgm_keeps_original_audio_when_library_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    repo.save_task(_avatar_task("avatar-real", video))
    service = VideoEditorWorkflowService(
        repo,
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )

    def fake_create_analysis(**kwargs):
        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id="analysis-no-bgm",
            title="分析",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            source_video_path=str(video),
            is_mock=False,
            outputs={
                "workflow": "analysis",
                "source_id": kwargs["source_id"],
                "analysis_json": '{"recommended_steps":[],"title_candidates":["本地标题"],"media":{"has_audio":true}}',
            },
        )
        repo.save_task(task)
        return task

    captured: dict = {}

    def fake_create_edit_job(**kwargs):
        captured.update(kwargs)
        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id="edit-no-bgm",
            title="剪辑",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            source_video_path=str(video),
            is_mock=False,
            outputs={"workflow": "edit"},
        )
        repo.save_task(task)
        return task

    monkeypatch.setattr(service, "create_analysis", fake_create_analysis)
    monkeypatch.setattr(service, "create_edit_job", fake_create_edit_job)
    created = service.create_batch(
        source_ids=["avatar:avatar-real"],
        target_platform="douyin",
        subtitle_enabled=False,
        subtitle_model="base",
        steps=[],
        output_format="mp4",
        output_resolution="720x1280",
        output_fps=25,
        output_bitrate="2M",
        bgm_enabled=True,
    )
    running = service.get_batch(created["batch_id"])

    assert running["items"][0]["status"] == "rendering"
    assert running["items"][0]["selected_bgm_id"] is None
    assert "保持原声" in running["items"][0]["bgm_reason"]
    assert all(step["kind"] != "background_music" for step in captured["steps"])


def test_asr_model_status_detects_complete_snapshot(tmp_path: Path):
    snapshot = tmp_path / "models--Systran--faster-whisper-base" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (snapshot / name).write_bytes(b"x")

    status = asr_model_status("base", cache_root=tmp_path)

    assert status["installed"] is True
    assert status["device"] == "cpu"
    assert status["compute_type"] == "int8"
