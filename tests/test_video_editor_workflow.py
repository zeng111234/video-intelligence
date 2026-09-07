"""智能剪辑工作流的系统素材与字幕复核门禁测试。"""

from __future__ import annotations

import json
import io
import hashlib
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.models import (
    AvatarProviderStatus,
    AvatarTask,
    TaskStatus,
    VideoEditTask,
    VideoEditorBatch,
    VideoEditorBatchItem,
)
from src.repositories.mock import MockRepository
from src.repositories.sqlite import SQLiteRepository
from src.resources import asr_model_status
from src.services.video_editor_workflow import (
    VideoEditorWorkflowError,
    VideoEditorWorkflowService,
)
import src.services.video_editor_workflow as workflow_module
import src.services.style_presets as style_presets_module
import src.services.video_editor_cloud as cloud_module


def test_visual_gate_policy_is_template_adaptive() -> None:
    business = workflow_module._visual_gate_policy_for_template(
        "pain_point_solution"
    )
    tutorial = workflow_module._visual_gate_policy_for_template(
        "knowledge_howto"
    )
    story = workflow_module._visual_gate_policy_for_template("story_resonance")

    assert business["min_real_events"] == 1
    assert business["max_coverage_ratio"] == pytest.approx(0.20)
    assert tutorial["min_real_events"] == 0
    assert tutorial["max_coverage_ratio"] == pytest.approx(0.30)
    assert story["min_real_events"] == 0
    assert story["max_coverage_ratio"] == pytest.approx(0.18)
    assert tutorial["pip_required"] is False
    assert story["pip_required"] is False


def test_local_grammar_v2_is_a_roll_first_and_does_not_require_broll() -> None:
    policy = workflow_module._visual_gate_policy_for_template(
        "talking-head-local-grammar-v2",
        visual_density="local_grammar_v2",
    )

    assert policy["min_real_events"] == 0
    assert policy["max_real_events"] == 0
    assert policy["min_coverage_ratio"] == pytest.approx(0.0)
    assert policy["max_coverage_ratio"] == pytest.approx(0.0)
    assert policy["pip_required"] is False
    assert policy["full_required"] is False
    assert policy["min_camera_events"] == 1
    assert policy["min_symbol_events"] == 1


def test_grammar_symbols_stay_out_of_the_upper_right_corner() -> None:
    rendered = workflow_module.VideoEditorWorkflowService._local_rhythm_video_filter(
        duration_seconds=12.0,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.08,
        subtitle_filter="",
        motion_items=[
            {
                "start": 2.0,
                "end": 3.0,
                "style_id": "grammar_green_check",
            }
        ],
        source_width=720,
        source_height=1280,
    )

    assert "x='W-w-42'" in rendered
    assert "trunc(H*0.60-h/2)" in rendered
    assert "trunc(H*0.12-h/2)" not in rendered


def test_grammar_negative_mark_is_not_duplicated_by_editorial_sticker() -> None:
    events = workflow_module._clean_grammar_motion_events(
        [
            {
                "type": "semantic_symbol",
                "style_id": "grammar_red_x",
                "start": 10.0,
                "end": 11.5,
                "source_segment_index": 4,
            },
            {
                "type": "semantic_sticker",
                "style_id": "editorial_negative",
                "start": 10.0,
                "end": 11.5,
                "source_segment_index": 4,
            },
        ]
    )
    assert [item["style_id"] for item in events] == ["editorial_negative"]


def test_frozen_media_manifest_is_read_next_to_bundled_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    internal = tmp_path / "_internal"
    services = internal / "src" / "services"
    media = internal / "media"
    services.mkdir(parents=True)
    media.mkdir(parents=True)
    (media / "windows-media-tools.sha256").write_text(
        "a" * 64 + "  ffmpeg.exe\n" + "b" * 64 + "  ffprobe.exe\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        workflow_module, "__file__", str(services / "video_editor_workflow.py")
    )

    assert workflow_module._audited_media_tool_hashes() == {
        "ffmpeg.exe": "a" * 64,
        "ffprobe.exe": "b" * 64,
    }


def test_frozen_media_tools_resolve_from_the_installed_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    internal = tmp_path / "_internal"
    services = internal / "src" / "services"
    media = internal / "media"
    services.mkdir(parents=True)
    media.mkdir(parents=True)
    (media / "ffmpeg.exe").write_bytes(b"audited ffmpeg")
    (media / "ffprobe.exe").write_bytes(b"audited ffprobe")
    monkeypatch.setattr(
        workflow_module, "__file__", str(services / "video_editor_workflow.py")
    )
    monkeypatch.setattr(
        workflow_module,
        "_audited_media_tool_hashes",
        lambda: {"ffmpeg.exe": "ffmpeg-hash", "ffprobe.exe": "ffprobe-hash"},
    )
    monkeypatch.setattr(
        workflow_module,
        "_sha256_file",
        lambda path: "ffmpeg-hash" if path.name == "ffmpeg.exe" else "ffprobe-hash",
    )
    monkeypatch.delenv("VIDEO_EDITOR_MEDIA_BIN", raising=False)
    monkeypatch.delenv("VIDEO_EDITOR_FFMPEG_PATH", raising=False)
    monkeypatch.delenv("VIDEO_EDITOR_FFPROBE_PATH", raising=False)

    resolved = workflow_module._trusted_local_media_tools()

    assert Path(resolved["ffmpeg"]).parent == media
    assert Path(resolved["ffprobe"]).parent == media
    assert resolved["audited"] is True


def test_rich_adaptive_policy_requires_real_pip_and_full_visuals() -> None:
    policy = workflow_module._visual_gate_policy_for_template(
        "adaptive_talking_head_v1",
        visual_density="rich",
    )

    assert policy["min_real_events"] == 3
    assert policy["min_coverage_ratio"] == pytest.approx(0.45)
    assert policy["max_coverage_ratio"] == pytest.approx(0.65)
    assert policy["min_effective_coverage_ratio"] == pytest.approx(0.45)
    assert policy["max_effective_coverage_ratio"] == pytest.approx(0.65)
    assert policy["pip_required"] is True
    assert policy["full_required"] is True
    assert policy["min_pip_events"] == 1
    assert policy["min_full_events"] == 1


def test_adaptive_pipeline_marker_cannot_fall_back_to_legacy_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clean-avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    repo.save_task(_avatar_task("adaptive-route-avatar", video, title="干净口播"))
    service = VideoEditorWorkflowService(
        repo,
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    monkeypatch.setattr(
        service,
        "_probe_media",
        lambda _path: {
            "duration_seconds": 12.0,
            "width": 720,
            "height": 1280,
            "fps": 30.0,
            "orientation": "vertical",
            "has_audio": True,
            "size_bytes": 5,
        },
    )
    now = datetime.now().astimezone()
    item = VideoEditorBatchItem(
        source_id="avatar:adaptive-route-avatar",
        title="干净口播",
        selected_title="干净口播",
        subtitle_segments=[
            {
                "start": 0.0,
                "end": 2.4,
                "text": "这是一段测试口播",
                "words": [
                    {"start": index * 0.3, "end": (index + 1) * 0.3, "text": char}
                    for index, char in enumerate("这是一段测试口播")
                ],
            }
        ],
        review_snapshot={"confirmed": True},
        provider_payload={"requested_pipeline": "adaptive_fine_cut_v1"},
        enabled_plan_step_ids=["smart_opening", "vertical_fit", "subtitles"],
        edit_plan={"remove_ranges": []},
        publish_allowed=False,
        updated_at=now,
    )
    batch = VideoEditorBatch(
        provider_mode="local",
        output_profile="720p",
        output_resolution="720x1280",
        output_fps=30,
        output_bitrate="1M",
        is_mock=False,
        items=[item],
        created_at=now,
        updated_at=now,
    )
    repo.save_video_editor_batch(batch)

    with pytest.raises(
        VideoEditorWorkflowError, match="VISUAL_PIPELINE_NOT_EXECUTED"
    ):
        service.create_local_preview_export(
            batch.batch_id,
            item.item_id,
            run_inline=True,
        )


def test_long_rich_adaptive_policy_uses_distributed_asset_clusters() -> None:
    policy = workflow_module._visual_gate_policy_for_template(
        "adaptive_talking_head_v1",
        visual_density="rich",
        duration_seconds=104.7,
    )

    assert policy["min_real_events"] == 4
    assert policy["max_real_events"] == 12
    assert policy["min_coverage_ratio"] == pytest.approx(0.45)
    assert policy["max_coverage_ratio"] == pytest.approx(0.65)
    assert policy["min_effective_coverage_ratio"] == pytest.approx(0.45)
    assert policy["max_effective_coverage_ratio"] == pytest.approx(0.65)
    assert policy["pip_required"] is True
    assert policy["full_required"] is True
    assert policy["min_pip_events"] == 2
    assert policy["min_full_events"] == 2


def test_long_semantic_binding_can_use_eight_distinct_cached_clusters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Long-form rich mode may fill its ceiling without blind rotation."""

    from PIL import Image

    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    image_path = tmp_path / "cache.png"
    image = Image.new("RGB", (128, 128), "#1677ff")
    for x in range(0, 128, 8):
        for y in range(0, 128, 8):
            image.putpixel((x, y), ((x * 2) % 255, (y * 2) % 255, 80))
    image.save(image_path)

    bindings = (
        "烧烤餐厅顾客",
        "客户会面关系",
        "客户数据库",
        "客户关系触达",
    ) * 2
    assets = [
        {
            "asset_id": f"cached-{index}",
            "_path": str(image_path),
            "media_type": "image/png",
            "duration_seconds": 2.0,
            "authorization_status": "confirmed",
            "publish_licensed": True,
            "asset_origin": "stock_video_asset",
            "source_provider": "pexels",
            "source_type": "local_cache",
            "semantic_binding": binding,
            "keywords": [],
        }
        for index, binding in enumerate(bindings)
    ]
    monkeypatch.setattr(service, "list_visual_assets", lambda kind=None: assets)
    monkeypatch.setenv("VIDEO_EDITOR_LOCAL_ACCEPTANCE_NO_PROVIDER", "1")
    shot_plan = {
        "timeline_duration_seconds": 104.7,
        "shots": [
            {
                "shot_id": f"shot-{index:02d}",
                "role": "A-roll",
                "duration_seconds": 2.0,
                "timeline_start": start,
                "source_start": start,
                "source_end": start + 2.0,
            }
            for index, start in enumerate((2, 16, 30, 44, 58, 72, 86, 100))
        ],
    }
    text_by_index = (
        "餐厅顾客完成复购",
        "客户会面沟通关系",
        "客户数据库持续管理",
        "会员优惠券和口碑传播",
    ) * 2
    segments = [
        {"start": start, "end": start + 2.0, "text": text}
        for start, text in zip((2, 16, 30, 44, 58, 72, 86, 100), text_by_index)
    ]

    result = service._auto_bind_release_broll_assets(
        shot_plan,
        transcript_segments=segments,
    )

    assert len(result) == 8
    assert len({item["asset_id"] for item in result.values()}) == 8
    assert all(int(item["match_score"]) > 0 for item in result.values())


def test_adaptive_cadence_uses_sparse_reframes_not_caption_refreshes() -> None:
    preview = {
        "cues": [
            {"start": 0.0, "end": 1.2, "emphasis_style": None},
            {"start": 1.2, "end": 2.4, "emphasis_style": {"scale": 1.08}},
            {"start": 2.4, "end": 3.6, "emphasis_style": None},
        ]
    }
    gate = workflow_module._visual_cadence_gate(
        duration_seconds=31.5,
        brolls=[
            {"start": 4.0, "end": 6.0},
            {"start": 11.0, "end": 13.0},
            {"start": 18.0, "end": 20.0},
            {"start": 25.0, "end": 27.0},
        ],
        vector_items=[],
        subtitle_preview=preview,
        has_hook=True,
        a_roll_shots=[],
        playback_rate=1.0,
    )
    assert gate["passed"] is True
    assert gate["meaningful_visual_beat_count"] <= 8
    assert gate["routine_subtitle_refresh_counted"] is False


def test_adaptive_reframe_events_count_as_camera_beats_once() -> None:
    gate = workflow_module._visual_cadence_gate(
        duration_seconds=19.7,
        brolls=[{"start": 3.84, "end": 6.68}],
        vector_items=[],
        subtitle_preview={"cues": []},
        has_hook=True,
        reframe_events=[
            {"start": 7.08, "end": 8.5},
            {"start": 10.56, "end": 12.0},
            {"start": 14.58, "end": 16.0},
            {"start": 19.26, "end": 20.7},
        ],
        playback_rate=1.0,
    )
    assert gate["passed"] is True
    assert gate["meaningful_visual_beat_count"] == 6
    assert gate["routine_subtitle_refresh_counted"] is False


def test_sparse_a_roll_degradation_does_not_fail_on_one_sentence_hold() -> None:
    gate = workflow_module._visual_cadence_gate(
        duration_seconds=96.9,
        brolls=[],
        vector_items=[],
        subtitle_preview={"cues": [{"start": float(index), "end": float(index + 1), "emphasis_style": {"scale": 1.08}} for index in range(5)]},
        has_hook=True,
        a_roll_shots=[],
        reframe_events=[],
        playback_rate=1.0,
        allow_safe_a_roll_degradation=True,
    )

    assert gate["passed"] is True
    assert gate["safe_degradation_allowed"] is True


def test_effective_visual_coverage_uses_non_overlapping_rendered_events() -> None:
    intervals = [
        {"start": 0.0, "end": 3.0},
        {"start": 2.0, "end": 5.0},
        {"start": 7.0, "end": 8.5},
    ]
    covered = workflow_module._interval_union_seconds(
        intervals,
        duration_seconds=10.0,
    )
    assert covered == pytest.approx(6.5)


def test_broad_semantic_opportunities_become_short_sparse_reframes() -> None:
    selected = workflow_module._select_sparse_reframe_events(
        [
            {"start": 10.0, "end": 25.0, "treatment": "keyword_card"},
            {"start": 12.0, "end": 18.0, "treatment": "punch_in"},
            {"start": 26.0, "end": 55.0, "treatment": "keyword_card"},
        ],
        duration_seconds=60.0,
    )
    assert [(item["start"], item["end"]) for item in selected] == [
        (10.0, 11.8),
        (26.0, 27.8),
    ]
    assert all(item["treatment"] == "safe_reframe" for item in selected)


class _TranscriptionStub:
    def __init__(self, approved=None):
        self.approved = approved

    def get_approved_revision(self, task_id: str):
        return self.approved


class _VideoEditingStub:
    def __init__(self, output_directory: Path):
        self.output_directory = output_directory


def _avatar_task(
    task_id: str,
    result_path: Path | None,
    *,
    is_mock: bool = False,
    title: str = "系统数字人成片",
    script_text: str = "测试口播",
) -> AvatarTask:
    now = datetime.now().astimezone()
    return AvatarTask(
        task_id=task_id,
        title=title,
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        script_text=script_text,
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


def test_avatar_filename_never_overrides_cached_copy_topic_or_preview_subtitles(
    tmp_path: Path,
):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    source_id = "avatar:avatar-copy-topic"
    repo.save_task(
        _avatar_task(
            "avatar-copy-topic",
            video,
            title="数字人视频4",
            script_text="社区推出了新的预约流程，用户可以按步骤完成办理。",
        )
    )
    service = VideoEditorWorkflowService(repo, None, _TranscriptionStub(), None)

    uncached_source = service.resolve_source(source_id)
    assert uncached_source["title"] == "社区推出了新的预约流程，用户可以按步骤完成办理"

    previous = VideoEditorBatch(
        provider_mode="aliyun",
        output_profile="720p",
        items=[
            VideoEditorBatchItem(
                source_id=source_id,
                title="数字人视频4",
                status="outcome_unknown",
                selected_title="3步预约，办理更省心？",
                title_candidates=[
                    "3步预约，办理更省心？",
                    "社区预约流程的办理方法",
                ],
                subtitle_segments=[
                    {"start": 0.4, "end": 2.8, "text": "社区推出了新的预约流程"}
                ],
                provider_payload={"media": {"duration_seconds": 60}},
            )
        ],
    )
    repo.save_video_editor_batch(previous)
    latest = VideoEditorBatch(
        provider_mode="aliyun",
        output_profile="720p",
        items=[
            VideoEditorBatchItem(
                source_id=source_id,
                title="数字人视频4",
                status="outcome_unknown",
                provider_stage="submission_outcome_unknown",
                error_message="OSS 上传连接失败。",
            )
        ],
    )
    repo.save_video_editor_batch(latest)

    payload = service.get_batch(latest.batch_id)
    item = payload["items"][0]

    assert item["selected_title"] == "3步预约，办理更省心？"
    assert item["title_candidates"][0] == "3步预约，办理更省心？"
    assert item["subtitle_segments"] == []
    assert item["preview_subtitle_segments"][0]["text"] == "社区推出了新的预约流程"
    assert item["subtitle_preview_source"] == "cached_asr"
    assert item["overlay_preview"]["title"]["lines"][0] != "数字人视频4"
    assert item["overlay_preview"]["cues"]


def test_unknown_cloud_item_can_reuse_approved_preview_for_free_local_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    source_id = "avatar:avatar-local-export"
    repo.save_task(
        _avatar_task(
            "avatar-local-export",
            video,
            title="数字人视频4",
        )
    )
    previous = VideoEditorBatch(
        provider_mode="aliyun",
        output_profile="720p",
        items=[
            VideoEditorBatchItem(
                source_id=source_id,
                title="数字人视频4",
                status="outcome_unknown",
                selected_title="3步预约，办理更省心？",
                subtitle_segments=[
                    {"start": 0.4, "end": 2.8, "text": "社区推出了新的预约流程"}
                ],
                review_snapshot={"confirmed": True},
                enabled_plan_step_ids=["vertical_fit", "subtitles", "title"],
                edit_plan={"remove_ranges": []},
            )
        ],
    )
    repo.save_video_editor_batch(previous)
    current = VideoEditorBatch(
        provider_mode="aliyun",
        output_profile="720p",
        output_resolution="720x1280",
        output_bitrate="2.5M",
        is_mock=False,
        items=[
            VideoEditorBatchItem(
                source_id=source_id,
                title="数字人视频4",
                status="outcome_unknown",
                provider_stage="submission_outcome_unknown",
                publish_allowed=False,
            )
        ],
    )
    repo.save_video_editor_batch(current)
    service = VideoEditorWorkflowService(
        repo,
        _VideoEditingStub(tmp_path / "outputs"),
        _TranscriptionStub(),
        None,
    )
    monkeypatch.setattr(service, "_sync_batch", lambda batch: batch)
    monkeypatch.setattr(
        service,
        "_probe_media",
        lambda _path: {
            "duration_seconds": 60.0,
            "width": 720,
            "height": 1280,
            "fps": 30.0,
            "orientation": "vertical",
            "has_audio": True,
            "size_bytes": 5,
        },
    )
    submitted: list[str] = []
    monkeypatch.setattr(
        workflow_module._WORKFLOW_EXECUTOR,
        "submit",
        lambda _runner, task_id: submitted.append(task_id),
    )

    payload = service.create_local_preview_export(
        current.batch_id,
        current.items[0].item_id,
    )

    item = payload["items"][0]
    assert item["status"] == "rendering"
    assert item["provider_stage"] == "local_export_rendering"
    assert item["provider_payload"]["local_export"]["cost_cny"] == "0"
    assert item["selected_title"] == "3步预约，办理更省心？"
    task = repo.get_task(item["edit_task_id"])
    assert isinstance(task, VideoEditTask)
    assert task.outputs["workflow"] == "local_preview_export"
    assert (
        task.outputs["style_version"]
        == "business_talking_head_v11.8-adaptive-reframe-no-text-cards-final-output-clock"
    )
    assert task.outputs["playback_rate"] == "1.15"
    assert "社区推出了新的预约流程" in task.outputs["subtitle_segments_json"]
    assert "smart_opening" in item["enabled_plan_step_ids"]
    opening = json.loads(task.outputs["smart_opening_json"])
    assert opening["style_id"] == "number_focus"
    assert opening["hook_text"] == "3步预约办理更省心"[:14]
    assert submitted == [task.task_id]


def test_local_rhythm_filter_uses_complete_source_timeline_with_safe_reframe():
    rendered = VideoEditorWorkflowService._local_rhythm_video_filter(
        duration_seconds=24.0,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.15,
        subtitle_filter="approved.ass",
        source_width=960,
        source_height=720,
    )

    assert "split=5[scene0][scene1][scene2][scene3][scene4]" in rendered
    assert "trim=start=0.000:end=4.800" in rendered
    assert "trim=start=19.200:end=24.000" in rendered
    assert "split=2[foreground0][background0]" in rendered
    assert "boxblur=18:2" in rendered
    assert "scale=720:540:force_original_aspect_ratio=decrease" in rendered
    assert "overlay=(W-w)/2:281" in rendered
    assert "pad=720:540" not in rendered
    assert "scale=820:1459:force_original_aspect_ratio=increase" not in rendered
    assert "xfade=transition=smoothleft:duration=0.240" in rendered
    assert "xfade=transition=fade:duration=0.240" in rendered
    assert "tpad=stop_mode=clone:stop_duration=0.240" in rendered
    assert "setpts=PTS/1.150" in rendered
    assert "subtitles='approved.ass'" in rendered


def test_local_rhythm_filter_can_use_clean_hard_cuts_for_grammar_routes():
    rendered = VideoEditorWorkflowService._local_rhythm_video_filter(
        duration_seconds=24.0,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.0,
        subtitle_filter="approved.ass",
        source_width=960,
        source_height=720,
        clean_hard_cuts=True,
    )

    assert "concat=n=5:v=1:a=0" in rendered
    assert "xfade=transition=" not in rendered


def test_local_rhythm_filter_fits_portrait_source_without_foreground_crop():
    rendered = VideoEditorWorkflowService._local_rhythm_video_filter(
        duration_seconds=8.0,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.15,
        subtitle_filter="approved.ass",
        source_width=720,
        source_height=1280,
    )

    assert "scale=w='trunc(720*(1+0.010*sin(PI*t/4.000))/2)*2':" in rendered
    assert "crop=720:1280:x='(iw-ow)/2+3*sin(PI*t/4.000)':y=0" in rendered


def test_adaptive_reframe_filter_uses_safe_camera_motion_without_cards():
    rendered = VideoEditorWorkflowService._local_rhythm_video_filter(
        duration_seconds=8.0,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.0,
        subtitle_filter="approved.ass",
        reframe_events=[{"start": 3.0, "end": 4.5, "treatment": "safe_reframe"}],
        source_width=720,
        source_height=1280,
    )

    assert "scale=w='trunc(720*(1+0.08*if(lt(t,3.000)" in rendered
    assert "crop=720:1280:(iw-ow)/2:0" in rendered
    assert "beatcard" not in rendered
    assert "subtitles='approved.ass'" in rendered


def test_subtitle_sound_effects_are_delayed_to_semantic_beats():
    rendered = workflow_module._subtitle_sound_effect_filters(
        [
            {"start": 2.4, "end": 3.4, "style_id": "number_slam"},
            {"start": 8.0, "end": 9.0, "style_id": "cta_burst"},
        ],
        playback_rate=1.0,
    )

    assert len(rendered) == 2
    assert "anoisesrc=color=brown" in rendered[0]
    assert "adelay=2400|2400" in rendered[0]
    assert "frequency=1040" in rendered[1]
    assert "adelay=8000|8000" in rendered[1]


def test_sparse_sfx_selector_covers_the_timeline_without_duplicate_segments():
    selected = workflow_module._select_sparse_sfx_items(
        [
            {
                "start": 18.0,
                "end": 19.0,
                "source_segment_index": 3,
                "semantic_role": "POSITIVE",
                "sfx_profile": "success_ping",
            }
        ],
        [
            {"start": 1.0, "end": 2.0, "source_segment_index": 0, "importance": 0.88},
            {"start": 8.0, "end": 9.0, "source_segment_index": 1, "importance": 0.90},
            {"start": 28.0, "end": 29.0, "source_segment_index": 4, "importance": 0.92},
            {"start": 42.0, "end": 43.0, "source_segment_index": 5, "importance": 0.84},
            {"start": 56.0, "end": 57.0, "source_segment_index": 6, "importance": 0.82},
        ],
        duration_seconds=60.0,
        target_count=6,
    )

    starts = [float(item["start"]) for item in selected]
    assert starts[0] == pytest.approx(1.0)
    assert starts[-1] >= 42.0
    assert all(right - left >= 2.8 for left, right in zip(starts, starts[1:]))
    assert len(
        [item.get("source_segment_index") for item in selected]
    ) == len({item.get("source_segment_index") for item in selected})


def test_local_sound_effect_library_is_bound_when_asset_exists(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(workflow_module, "_SFX_LIBRARY_DIR", tmp_path)
    path = tmp_path / "sfx-boom.wav"
    path.write_bytes(b"test-audio")
    path.with_suffix(".json").write_text(json.dumps({
        "authorization_status": "confirmed", "license_name": "project-generated",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    item = {"start": 2.4, "end": 3.4, "style_id": "number_slam"}
    asset = workflow_module._resolve_local_sound_effect(item, event_index=0)

    assert asset is not None
    assert asset.name == "sfx-boom.wav"

    rendered = workflow_module._subtitle_sound_effect_filters(
        [item], playback_rate=1.0, input_indices={0: 2}
    )
    assert len(rendered) == 1
    assert rendered[0].startswith("[2:a]aresample=48000")
    assert "adelay=2400|2400" in rendered[0]
    assert "anoisesrc" not in rendered[0]


def test_local_export_quality_report_requires_audio_and_expected_canvas():
    report = VideoEditorWorkflowService._local_export_quality_report(
        {
            "size_bytes": 100,
            "width": 720,
            "height": 1280,
            "duration_seconds": 10.1,
            "has_audio": True,
        },
        expected_width=720,
        expected_height=1280,
        expected_duration=10,
        source_has_audio=True,
        visual_beats=[{"start": 1, "end": 2}],
        source_media_identity={
            "provider": "aliyun",
            "model": "fun-asr",
            "media_sha256": "a" * 64,
            "transcript_sha256": "b" * 64,
            "transcript_timing_source": "sentence_timestamps",
            "word_timestamps_available": False,
        },
        subtitle_preview={
            "subtitle_style_id": "adaptive_talking_head_v1",
            "style_fingerprint": {
                "entry_motion": "fade_in_120ms",
                "emphasis_scale_range": [1.05, 1.10],
            },
            "cues": [],
        },
    )

    assert report["passed"] is True
    assert all(report["checks"].values())
    assert report["visual_beat_count"] == 1
    # P0-收口 2026-08-31: the new hard gates must publish their own block.
    assert report["transcript_source_identity"]["passed"] is True
    assert report["timing_source_truthful"]["passed"] is True
    assert report["subtitle_style_baseline"]["passed"] is True


@pytest.mark.parametrize(
    "with_broll,with_words",
    [(False, False), (True, False), (False, True)],
)
def test_sentence_level_local_export_keeps_subtitle_gate_with_or_without_pip(
    tmp_path: Path,
    with_broll: bool,
    with_words: bool,
):
    """A long sentence clock must render the same readable ASS timeline.

    This is deliberately an end-to-end local FFmpeg check.  It exercises the
    exact page path after review confirmation, while making no provider call.
    """

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required for the local export regression.")
    source = tmp_path / "sentence-level.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x243447:s=720x1280:r=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "16.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
    )

    repo = MockRepository(tasks=[])
    avatar = _avatar_task("avatar-sentence-level-local", source)
    repo.save_task(avatar)
    service = VideoEditorWorkflowService(
        repo,
        _VideoEditingStub(tmp_path / "outputs"),
        _TranscriptionStub(),
        None,
    )
    broll = None
    if with_broll:
        from PIL import Image

        image_bytes = io.BytesIO()
        Image.new("RGB", (480, 640), (33, 122, 92)).save(image_bytes, format="PNG")
        broll = service.upload_visual_asset(
            kind="broll",
            file_name="customer-relationship.png",
            media_type="image/png",
            media_bytes=image_bytes.getvalue(),
            rights_confirmed=True,
            rights_holder="generated_for_local_acceptance",
        )

    now = datetime.now().astimezone()
    segments = [
        {
            "start": 0.0,
            "end": 5.5,
            "text": "你公司的客户资源是掌握在业务员手里还是沉淀在公司数据库",
        },
        {
            "start": 5.5,
            "end": 11.5,
            "text": "业务员一旦离职聊天记录没了客户关系也跟着断掉了",
        },
        {
            "start": 11.5,
            "end": 16.3,
            "text": "企业真正需要的不是业务员个人维护而是一套能沉淀用户持续触达的营销机制",
        },
    ]
    if with_words:
        for segment in segments:
            text = str(segment["text"])
            duration = float(segment["end"]) - float(segment["start"])
            character_duration = duration / max(len(text), 1)
            segment["words"] = [
                {
                    "start": segment["start"] + index * character_duration,
                    "end": segment["start"] + (index + 1) * character_duration,
                    "text": character,
                }
                for index, character in enumerate(text)
            ]
    review_snapshot = {"confirmed": True}
    if broll:
        review_snapshot["broll"] = {
            "asset_id": broll["asset_id"],
            "start": 1.0,
            "end": 3.5,
            "mode": "pip",
        }
    item = VideoEditorBatchItem(
        source_id=f"avatar:{avatar.task_id}",
        title="客户资源风险",
        selected_title="客户资源风险",
        subtitle_segments=segments,
        review_snapshot=review_snapshot,
        review_confirmed_at=now,
        enabled_plan_step_ids=["vertical_fit", "subtitles", "title"],
        edit_plan={"remove_ranges": []},
        publish_allowed=False,
    )
    batch = VideoEditorBatch(
        provider_mode="local",
        output_profile="720p",
        output_resolution="720x1280",
        output_fps=30,
        output_bitrate="1M",
        is_mock=False,
        items=[item],
        created_at=now,
        updated_at=now,
    )
    repo.save_video_editor_batch(batch)

    payload = service.create_local_preview_export(
        batch.batch_id,
        item.item_id,
        run_inline=True,
    )
    rendered_item = payload["items"][0]
    task = repo.get_task(rendered_item["edit_task_id"])
    assert isinstance(task, VideoEditTask)
    assert task.status == TaskStatus.SUCCEEDED, task.error_message
    assert task.result_path and Path(task.result_path).is_file()
    quality = json.loads(task.outputs["quality_report"])
    assert quality["subtitle_timeline"]["passed"] is True
    assert quality["subtitle_timeline"]["experience_gate"]["passed"] is True
    assert quality["subtitle_timeline"]["phrase_cue_count"] >= 2
    assert quality["subtitle_timeline"]["phrase_cue_count"] <= 18
    assert quality["subtitle_word_gate_passed"] is with_words
    # Preview/word timing is separate from publish rights and visual gates.
    assert quality["publish_claim_allowed"] is False
    if with_broll:
        assert quality["broll_modes"]["pip"] == 1
    else:
        assert quality["broll_modes"]["pip"] == 0


def test_subtitle_word_timing_report_verifies_real_word_boundaries():
    from src.services.video_editor_cloud import build_business_talking_head_overlay_preview

    segment = {
        "start": 0.0,
        "end": 2.0,
        "text": "客户关系沉淀在公司数据库",
        "words": [
            {"start": 0.0, "end": 0.5, "text": "客户关系"},
            {"start": 0.5, "end": 1.1, "text": "沉淀在"},
            {"start": 1.1, "end": 2.0, "text": "公司数据库"},
        ],
    }
    preview = build_business_talking_head_overlay_preview(
        [segment],
        title="",
        output_profile="720p",
        caption_groups=[
            {"segment_index": 0, "parts": ["客户关系沉淀在", "公司数据库"]}
        ],
    )

    report = VideoEditorWorkflowService._subtitle_word_timing_quality(
        [segment], preview, fps=30.0
    )

    assert report["status"] == "verified"
    assert report["word_p95_ms"] == 0.0
    assert report["mapping_error_frames"] == 0.0
    assert report["checks"] == {
        "word_p95_le_150ms": True,
        "mapping_le_1_frame": True,
    }


def test_subtitle_word_timing_report_keeps_sentence_only_as_unverified():
    from src.services.video_editor_cloud import build_business_talking_head_overlay_preview

    segment = {
        "start": 0.0,
        "end": 2.0,
        "text": "客户关系沉淀在公司数据库",
    }
    preview = build_business_talking_head_overlay_preview(
        [segment], title="", output_profile="720p"
    )

    report = VideoEditorWorkflowService._subtitle_word_timing_quality(
        [segment], preview, fps=30.0
    )

    assert report["status"] == "unverified_sentence_level"
    assert report["verified"] is False
    assert report["word_p95_ms"] is None
    assert report["mapping_error_frames"] is None
    assert report["checks"] == {
        "word_p95_le_150ms": None,
        "mapping_le_1_frame": None,
    }


def test_short_reviewed_phrase_uses_exact_word_clock_with_explicit_dwell_exception():
    segment = {
        "start": 0.0,
        "end": 1.2,
        "text": "普通家用设备",
        "words": [
            {"start": 0.4, "end": 0.56, "text": "通"},
            {"start": 0.56, "end": 0.78, "text": "家用"},
            {"start": 0.78, "end": 1.14, "text": "设备"},
        ],
    }
    preview = {
        "phrase_timing_source": "word_timestamps",
        "cues": [
            {
                "source_segment_index": 0,
                "start": 0.0,
                "end": 1.2,
                "lines": ["普通家用设备"],
            }
        ],
    }

    snapped = VideoEditorWorkflowService._snap_preview_cues_to_reviewed_word_clock(
        [segment], preview
    )
    cue = snapped["cues"][0]
    report = VideoEditorWorkflowService._subtitle_word_timing_quality(
        [segment], snapped, fps=30.0
    )
    experience = VideoEditorWorkflowService._subtitle_experience_gate(
        [segment], snapped
    )

    assert cue["start"] == 0.4
    assert cue["end"] == 1.14
    assert cue["short_source_exception"] is True
    assert report["verified"] is True
    assert report["word_p95_ms"] == 0.0
    assert report["mapping_error_frames"] == 0.0
    assert experience["passed"] is True
    assert experience["checks"]["min_duration"] is True


def test_long_raw_word_pause_does_not_reinflate_readable_phrase_clock():
    segment = {
        "start": 0.0,
        "end": 3.0,
        "text": "普通家用设备搞充值活动",
        "words": [
            {"start": 0.0, "end": 0.25, "text": "普通"},
            {"start": 0.25, "end": 0.55, "text": "家用设备"},
            {"start": 0.55, "end": 1.0, "text": "搞"},
            {"start": 1.0, "end": 3.0, "text": "充值活动"},
        ],
    }
    preview = {
        "phrase_timing_source": "word_timestamps",
        "cues": [
            {
                "source_segment_index": 0,
                "start": 0.0,
                "end": 2.3,
                "lines": ["普通家用设备搞充值活动"],
            }
        ],
    }
    snapped = VideoEditorWorkflowService._snap_preview_cues_to_reviewed_word_clock(
        [segment], preview
    )
    cue = snapped["cues"][0]
    assert cue["end"] - cue["start"] <= 2.4
    assert cue["word_clock_mapping"] == "lexical_preview_clock_preserved_over_pause"


def test_production_export_builds_current_single_line_clean_caption_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    avatar = _avatar_task(
        "avatar-production-export",
        video,
        script_text="餐饮门店想做同城获客 别急着先砸钱投流 可以从老顾客和门店内容做起",
    )
    repo.save_task(avatar)
    service = VideoEditorWorkflowService(
        repo,
        _VideoEditingStub(tmp_path / "outputs"),
        _TranscriptionStub(),
        None,
    )
    monkeypatch.setattr(
        service,
        "_probe_media",
        lambda _path: {
            "duration_seconds": 8.0,
            "width": 1080,
            "height": 1920,
            "fps": 30.0,
            "orientation": "vertical",
            "has_audio": True,
            "size_bytes": 5,
        },
    )

    def complete(task_id: str) -> None:
        task = repo.get_task(task_id)
        assert isinstance(task, VideoEditTask)
        output = tmp_path / "outputs" / "smart.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"smart-video")
        repo.save_task(
            task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "result_path": str(output),
                    "result_size_bytes": output.stat().st_size,
                }
            )
        )

    monkeypatch.setattr(service, "_run_local_preview_export", complete)

    task = service.render_production_export(
        avatar_task=avatar,
        script_text=avatar.script_text,
        publish_title="很多餐饮老板都在头疼",
        subtitle_segments=[
            {"start": 0.8, "end": 2.9, "text": "餐饮门店想做同城获客"},
            {"start": 3.1, "end": 4.8, "text": "别急着先砸钱投流"},
            {
                "start": 5.0,
                "end": 7.8,
                "text": "可以从老顾客和门店内容做起",
            },
        ],
    )

    assert task.outputs["style_version"] == (
        "talking_head_release_v2.2-paced-pip-finish-safe-caption-director-timeline"
    )
    assert task.outputs["workflow"] == "local_preview_export"
    assert task.outputs["requested_pipeline"] == "adaptive_fine_cut_v1"
    assert json.loads(task.outputs["shot_plan_json"])["visual_density"] == "rich"
    assert task.outputs["publish_title"] == "餐饮门店同城获客"
    assert json.loads(task.outputs["subtitle_segments_json"])[0]["start"] == 0.8
    batch = repo.list_video_editor_batches(limit=1)[0]
    assert batch.items[0].review_snapshot["source"] == "approved_avatar_asr"
    preview = service._batch_payload(batch)["items"][0]["overlay_preview"]
    assert preview is not None
    assert all(len(cue["lines"]) == 1 for cue in preview["cues"])
    assert all(
        not any(mark in line for mark in "，。！？；：、,.!?;:")
        for cue in preview["cues"]
        for line in cue["lines"]
    )
    assert [cue["lines"][0] for cue in preview["cues"]] == [
        "餐饮门店想做同城获客",
        "别急着先砸钱投流",
        "可以从老顾客和门店内容做起",
    ]


def test_production_export_does_not_render_a_context_free_transition_as_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    video = tmp_path / "avatar-transition.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    avatar = _avatar_task(
        "avatar-transition",
        video,
        script_text="但这个不一样 流水线成片不用粘胶 不用充电也能牢固使用",
    )
    repo.save_task(avatar)
    service = VideoEditorWorkflowService(
        repo,
        _VideoEditingStub(tmp_path / "outputs"),
        _TranscriptionStub(),
        None,
    )
    monkeypatch.setattr(
        service,
        "_probe_media",
        lambda _path: {
            "duration_seconds": 8.0,
            "width": 1080,
            "height": 1920,
            "fps": 30.0,
            "orientation": "vertical",
            "has_audio": True,
            "size_bytes": 5,
        },
    )
    monkeypatch.setattr(service, "_run_local_preview_export", lambda _task_id: None)

    task = service.render_production_export(
        avatar_task=avatar,
        script_text=avatar.script_text,
        publish_title="但这个不一样",
    )

    assert task.outputs["publish_title"] == "流水线成片不用粘胶"


def test_approved_script_segments_keep_real_asr_pauses_without_asr_word_drift():
    script = "这套方法不保证爆单 但能帮助门店更稳定地测试效果 连续测试七天"

    segments = VideoEditorWorkflowService.approved_script_segments_from_asr(
        script,
        [
            {
                "start": 0.8,
                "end": 3.1,
                "text": "这套方法不保证爆单，但能帮助门店更稳定的测试效果。",
            },
            {"start": 3.5, "end": 4.8, "text": "连续测试7天。"},
        ],
    )

    assert segments[0]["start"] == 0.8
    assert segments[1]["start"] == 3.5
    assert "".join(segment["text"] for segment in segments) == re.sub(
        r"[\W_]+", "", script
    )
    assert "稳定地" in "".join(segment["text"] for segment in segments)
    assert "七天" in segments[-1]["text"]


def test_approved_script_segments_reject_unrelated_recognition():
    with pytest.raises(VideoEditorWorkflowError, match="差异过大"):
        VideoEditorWorkflowService.approved_script_segments_from_asr(
            "餐饮门店想做同城获客",
            [{"start": 0.0, "end": 2.0, "text": "今天天气非常不错"}],
        )


def test_subtitle_enabled_job_requires_an_approved_revision(tmp_path: Path):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    repo.save_task(_avatar_task("avatar-real", video))
    repo.save_task(_analysis_task(video))
    service = VideoEditorWorkflowService(
        repo, None, _TranscriptionStub(approved=None), None
    )

    with pytest.raises(VideoEditorWorkflowError, match="字幕尚未完成复核确认"):
        service.create_edit_job(
            analysis_id="analysis-test", steps=[], subtitle_enabled=True
        )


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


def test_generated_media_uses_a_larger_transcription_limit_than_manual_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    video = tmp_path / "long-avatar.mp4"
    video.write_bytes(b"video")
    captured: dict[str, object] = {}

    class CreatingTranscriptionStub(_TranscriptionStub):
        def create_task(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                status=TaskStatus.SUCCEEDED,
                task_id="transcript-long-avatar",
                error_message=None,
            )

    monkeypatch.setattr(workflow_module, "_MAX_SOURCE_UPLOAD_BYTES", 4)
    monkeypatch.setattr(workflow_module, "_MAX_GENERATED_SUBTITLE_BYTES", 8)
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        CreatingTranscriptionStub(),
        None,
    )

    with pytest.raises(VideoEditorWorkflowError, match="上传素材超过 50MB"):
        service.upload_source(
            file_name="manual.mp4",
            media_type="video/mp4",
            media_bytes=b"video",
            rights_confirmed=True,
            rights_holder="测试用户",
        )

    transcript_id = service._create_transcription(
        _analysis_task(video),
        {"duration_seconds": 1},
    )

    assert transcript_id == "transcript-long-avatar"
    assert captured["media_bytes"] == b"video"
    assert captured["max_media_bytes"] == 8
    assert captured["include_word_timestamps"] is True


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
    monkeypatch.setattr(
        workflow_module._WORKFLOW_EXECUTOR, "submit", lambda *args, **kwargs: None
    )

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


def test_broll_asset_accepts_authorized_image_and_renders_before_subtitles(
    tmp_path: Path,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    asset = service.upload_visual_asset(
        kind="broll",
        file_name="demo.png",
        media_type="image/png",
        media_bytes=b"\x89PNG\r\n\x1a\ndemo",
        rights_confirmed=True,
        rights_holder="测试公司",
    )

    assert asset["kind"] == "broll"
    assert asset["media_kind"] == "image"
    rendered = service._local_rhythm_video_filter(
        duration_seconds=8,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.15,
        subtitle_filter="approved.ass",
        broll={"start": 1.0, "end": 3.0, "mode": "pip"},
    )
    assert "overlay=202:768" in rendered
    assert "drawbox=x=1:y=1:w=iw-2:h=ih-2:color=white@0.55:t=2" in rendered
    assert "fade=t=in:st=0.870:d=0.20:alpha=1" in rendered
    assert "fade=t=out:st=2.409:d=0.20:alpha=1" in rendered
    assert "enable='between(t,0.870,2.609)'" in rendered
    assert rendered.index("[with_broll]") < rendered.index("subtitles='approved.ass'")


def test_cached_stock_broll_uses_visual_asset_namespace_and_resolves(
    tmp_path: Path,
):
    from src.services.stock_broll_provider import StockBrollProvider

    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    cache = service._visual_asset_directory()
    payload = {
        "videos": [
            {
                "id": 7,
                "url": "https://www.pexels.com/video/7/",
                "duration": 5,
                "video_files": [
                    {
                        "link": "https://cdn.example.test/7.mp4",
                        "width": 720,
                        "height": 1280,
                    }
                ],
            }
        ]
    }

    class Response:
        def __init__(self, data: bytes):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit=-1):
            return self.data

    def opener(request, timeout=0):
        if request.full_url.startswith("https://api.pexels.com/"):
            return Response(json.dumps(payload).encode())
        return Response(b"x" * 2048)

    def runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "format": {"duration": "5.0"},
                    "streams": [
                        {"codec_type": "video", "width": 720, "height": 1280}
                    ],
                }
            ),
            stderr="",
        )

    result = StockBrollProvider(
        cache,
        pexels_key="test-key",
        opener=opener,
        runner=runner,
    ).search_and_cache("客户关系")
    assert result.status == "ready"

    listed = service.list_visual_assets("broll")
    assert len(listed) == 1
    resolved = service.resolve_visual_asset(
        listed[0]["asset_id"], expected_kind="broll"
    )
    assert resolved["asset_origin"] == "stock_video_asset"
    assert resolved["publish_licensed"] is True


def test_image_broll_uses_motion_and_fade_before_subtitles(tmp_path: Path):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )

    rendered = service._local_rhythm_video_filter(
        duration_seconds=12,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.0,
        subtitle_filter="approved.ass",
        brolls=[
            {"start": 2.0, "end": 4.5, "mode": "full", "media_kind": "image"},
            {"start": 7.0, "end": 9.5, "mode": "pip", "media_kind": "image"},
        ],
    )

    assert "20*sin(2*PI*t/4.0)" in rendered
    assert "10*sin(2*PI*t/3.6)" in rendered
    assert "crop=317:230" in rendered
    assert "overlay=202:768" in rendered
    assert "fade=t=in:st=2.000:d=0.28:alpha=1" in rendered
    assert "fade=t=in:st=7.000:d=0.20:alpha=1" in rendered
    assert "fade=t=out:st=9.300:d=0.20:alpha=1" in rendered
    assert rendered.index("subtitles='approved.ass'") > rendered.index("[with_broll1]")


def test_release_auto_binding_ignores_unconfirmed_or_generated_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from PIL import Image

    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    image_path = tmp_path / "real.png"
    Image.new("RGB", (160, 240), (40, 120, 180)).save(image_path)
    asset = {
        "asset_id": "broll-confirmed01",
        "_path": str(image_path),
        "media_type": "image/png",
        "media_kind": "image",
        "authorization_status": "generated_for_local_acceptance",
        "asset_origin": "generated_image_asset",
    }
    monkeypatch.setattr(service, "list_visual_assets", lambda kind=None: [asset])
    monkeypatch.setattr(
        service,
        "_visual_asset_directory",
        lambda create=True: tmp_path,
    )
    shot_plan = {
        "title": "客户数据库",
        "shots": [
            {"shot_id": "shot-01", "role": "A-roll", "duration_seconds": 2, "timeline_start": 0},
            {"shot_id": "shot-02", "role": "A-roll", "duration_seconds": 2, "timeline_start": 2},
            {"shot_id": "shot-03", "role": "A-roll", "duration_seconds": 2, "timeline_start": 4},
            {"shot_id": "shot-04", "role": "A-roll", "duration_seconds": 2, "timeline_start": 6},
        ],
    }

    assert service._auto_bind_release_broll_assets(shot_plan) == {}


def test_release_local_acceptance_registers_three_generated_assets_and_maps_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from PIL import Image

    generated_dir = tmp_path / "generated-assets"
    generated_dir.mkdir()
    manifest_assets = []
    for index, binding in enumerate(("客户数据库", "客户关系沉淀", "工厂品牌产品")):
        path = generated_dir / f"asset-{index}.png"
        image = Image.new("RGB", (320, 480), (30 + index * 40, 90, 150))
        for band in range(12):
            image.paste(
                (30 + index * 40 + band * 3, 90 + band * 4, 150 - band * 3),
                (band * 26, 0, (band + 1) * 26, 480),
            )
        image.save(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_assets.append(
            {
                "asset_id": f"generated-{index}",
                "path": path.name,
                "sha256": digest,
                "semantic_binding": binding,
            }
        )
    manifest_path = generated_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "source_type": "built_in_image_generation",
                "rights_status": "generated_for_local_acceptance",
                "cloud_upload": False,
                "paid_stock_call": False,
                "assets": manifest_assets,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIDEOINSIGHT_GENERATED_ASSET_MANIFEST", str(manifest_path))
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )

    registered = service._register_local_generated_acceptance_assets()
    assert len(registered) == 3
    assert {item["asset_origin"] for item in registered} == {"generated_image_asset"}
    assert {item["authorization_status"] for item in registered} == {
        "generated_for_local_acceptance"
    }
    shot_plan = {
        "shots": [
            {
                "shot_id": f"shot-{index:02d}",
                "role": "A-roll",
                "duration_seconds": 2,
                "timeline_start": index * 2,
            }
            for index in range(10)
        ]
    }
    bindings = service._auto_bind_release_broll_assets(
        shot_plan,
        include_generated_images=True,
    )
    assert len(bindings) == 3
    assert len({item["asset_id"] for item in bindings.values()}) == 3
    assert [item["mode"] for item in bindings.values()] == ["pip", "full", "pip"]


def test_release_selection_prefers_three_publishable_cached_stock_videos(
    tmp_path: Path,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    explicit_generated = {
        "asset_id": "broll-local-only",
        "asset_origin": "generated_image_asset",
        "authorization_status": "generated_for_local_acceptance",
        "publish_licensed": False,
    }
    stock = [
        {
            "asset_id": f"broll-stock-{index}",
            "asset_origin": "stock_video_asset",
            "source_provider": "pexels",
            "authorization_status": "confirmed",
            "publish_licensed": True,
        }
        for index in range(3)
    ]
    generated = [
        {
            "asset_id": "broll-generated-fallback",
            "asset_origin": "generated_image_asset",
            "authorization_status": "generated_for_local_acceptance",
            "publish_licensed": False,
        }
    ]

    selected = service._select_release_broll_assets(
        explicit_generated,
        stock,
        generated,
    )

    assert [item["asset_id"] for item in selected] == [
        "broll-stock-0",
        "broll-stock-1",
        "broll-stock-2",
    ]
    assert [service._release_broll_mode(item, index) for index, item in enumerate(selected)] == [
        "full",
        "pip",
        "full",
    ]


def test_release_selection_puts_explicit_domestic_real_before_international_stock(
    tmp_path: Path,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    domestic = {
        "asset_id": "broll-domestic",
        "asset_origin": "local_uploaded_asset",
        "domestic_context": "domestic",
        "authorization_status": "confirmed",
        "publish_licensed": False,
    }
    stock = [
        {
            "asset_id": f"broll-foreign-{index}",
            "asset_origin": "stock_video_asset",
            "source_provider": "pexels",
            "domestic_context": "international",
            "authorization_status": "confirmed",
            "publish_licensed": True,
        }
        for index in range(3)
    ]
    selected = service._select_release_broll_assets(None, stock + [domestic], [])
    assert selected[0]["asset_id"] == "broll-domestic"


def test_release_selection_puts_explicit_domestic_generated_before_international_stock(
    tmp_path: Path,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    stock = [
        {
            "asset_id": f"broll-foreign-{index}",
            "asset_origin": "stock_video_asset",
            "source_provider": "pexels",
            "domestic_context": "international",
            "authorization_status": "confirmed",
            "publish_licensed": True,
        }
        for index in range(3)
    ]
    generated_domestic = {
        "asset_id": "broll-domestic-generated",
        "asset_origin": "generated_image_asset",
        "domestic_context": "domestic",
        "authorization_status": "generated_for_local_acceptance",
        "publish_licensed": False,
    }
    selected = service._select_release_broll_assets(
        None,
        stock,
        [generated_domestic],
    )
    assert selected[0]["asset_id"] == "broll-domestic-generated"
    assert selected[0]["publish_licensed"] is False


def test_release_broll_placement_caps_each_event_for_coverage_gate():
    placements = VideoEditorWorkflowService._shot_broll_placements(
        {
            "shots": [
                {
                    "shot_id": "shot-01",
                    "role": "B-roll",
                    "asset_id": "broll-aaaaaaaaaa",
                    "timeline_start": 2.0,
                    "timeline_end": 9.0,
                    "overlay_mode": "full",
                }
            ]
        }
    )
    assert placements == [
        {
            "shot_id": "shot-01",
            "asset_id": "broll-aaaaaaaaaa",
            "start": 2.0,
            "end": 5.6,
            "mode": "full",
        }
    ]


def test_long_form_broll_holds_semantic_asset_to_bounded_release_limit():
    placements = VideoEditorWorkflowService._shot_broll_placements(
        {
            "timeline_duration_seconds": 60.0,
            "shots": [
                {
                    "shot_id": "shot-long",
                    "role": "B-roll",
                    "asset_id": "broll-bbbbbbbbbb",
                    "timeline_start": 2.0,
                    "timeline_end": 4.0,
                    "overlay_mode": "pip",
                }
            ],
        }
    )
    assert placements[0]["start"] == 2.0
    assert placements[0]["end"] == 7.0


def test_rich_release_keeps_third_semantic_cluster_but_bounds_short_events():
    placements = [
        {"asset_id": f"broll-{index}", "start": start, "end": end, "mode": "full"}
        for index, (start, end) in enumerate(
            ((3.4, 6.22), (6.22, 9.32), (9.32, 12.32))
        )
    ]
    bounded = VideoEditorWorkflowService._bound_short_rich_release_brolls(placements)
    assert len(bounded) == 3
    assert [item["end"] for item in bounded] == [5.8, 8.62, 11.72]
    assert sum(item["end"] - item["start"] for item in bounded) == pytest.approx(7.2)
    assert all(item["coverage_trimmed_for_short_adaptive_rich"] for item in bounded)


def test_release_stock_search_uses_overlapping_spoken_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    queries: list[str] = []
    limits: list[int] = []

    class FakeProvider:
        def __init__(self, _cache):
            pass

        def search_and_cache(
            self, query, *, max_results, provider=None, force_fresh=False
        ):
            queries.append(query)
            limits.append(max_results)
            return SimpleNamespace(status="unavailable", items=[])

    monkeypatch.setattr(workflow_module, "StockBrollProvider", FakeProvider)
    monkeypatch.setattr(service, "list_visual_assets", lambda kind=None: [])
    shot_plan = {
        "title": "商业口播",
        "shots": [
            {"shot_id": "shot-01", "role": "A-roll", "duration_seconds": 2, "timeline_start": 0, "source_start": 0, "source_end": 2},
            {"shot_id": "shot-02", "role": "A-roll", "duration_seconds": 2, "timeline_start": 2, "source_start": 2, "source_end": 4},
            {"shot_id": "shot-03", "role": "A-roll", "duration_seconds": 2, "timeline_start": 4, "source_start": 4, "source_end": 6},
            {"shot_id": "shot-04", "role": "A-roll", "duration_seconds": 2, "timeline_start": 6, "source_start": 6, "source_end": 8},
            {"shot_id": "shot-05", "role": "A-roll", "duration_seconds": 2, "timeline_start": 8, "source_start": 8, "source_end": 10},
        ],
    }
    segments = [
        {"start": 2, "end": 4, "text": "客户数据库要沉淀"},
        {"start": 8, "end": 10, "text": "工厂品牌产品持续曝光"},
    ]

    assert service._auto_bind_release_broll_assets(
        shot_plan, transcript_segments=segments
    ) == {}
    assert queries[0].startswith("CRM dashboard customer database")
    assert "product in real setting" in queries
    assert all(limit == 8 for limit in limits)


def test_release_semantic_search_runs_even_when_authorized_cache_has_three_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from PIL import Image

    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    image = tmp_path / "cached.png"
    Image.new("RGB", (128, 128), "#1677ff").save(image)
    image_obj = Image.open(image)
    for x in range(0, 128, 16):
        for y in range(0, 128, 16):
            image_obj.putpixel((x, y), ((x * 2) % 255, (y * 2) % 255, 80))
    image_obj.save(image)
    queries: list[str] = []

    class FakeProvider:
        def __init__(self, _cache):
            pass

        def search_and_cache(self, query, *, max_results, force_fresh=False):
            queries.append(query)
            return SimpleNamespace(
                status="ready",
                attempts=1,
                items=[
                    {
                        "asset_id": "broll-pexels-new",
                        "name": "CRM customer database dashboard",
                        "original_name": "crm-customer-database-dashboard.mp4",
                        "source_provider": "pexels",
                        "source_type": "provider_cache",
                        "source_url": "https://www.pexels.com/video/123/",
                        "license_name": "Pexels License",
                        "license_url": "https://www.pexels.com/license/",
                        "authorization_status": "confirmed",
                        "publish_licensed": True,
                        "asset_origin": "stock_video_asset",
                        "cache_path": str(image),
                    }
                ],
            )

    monkeypatch.setattr(workflow_module, "StockBrollProvider", FakeProvider)
    cached = [
        {
            "asset_id": f"broll-cached-{index}",
            "_path": str(image),
            "media_type": "image/png",
            "authorization_status": "confirmed",
            "publish_licensed": True,
            "asset_origin": "stock_video_asset",
            "source_provider": "pexels",
        }
        for index in range(3)
    ]
    monkeypatch.setattr(service, "list_visual_assets", lambda kind=None: cached)
    shot_plan = {
        "shots": [
            {
                "shot_id": f"shot-{index:02d}",
                "role": "A-roll",
                "duration_seconds": 2,
                "timeline_start": index * 2,
                "source_start": index * 2,
                "source_end": index * 2 + 2,
            }
            for index in range(10)
        ]
    }
    bindings = service._auto_bind_release_broll_assets(
        shot_plan,
        transcript_segments=[
            {"start": 2, "end": 4, "text": "客户数据库持续沉淀"},
        ],
    )

    assert len(queries) == 1
    assert queries[0].startswith("CRM dashboard customer database")
    assert bindings["shot-01"]["source_provider"] == "pexels"
    assert bindings["shot-01"]["license_name"] == "Pexels License"
    assert bindings["shot-01"]["source_url"].endswith("/123/")


def test_release_searches_each_structured_visual_request_when_cache_has_no_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    from PIL import Image

    image = tmp_path / "provider-cache.png"
    Image.new("RGB", (128, 128), "#1677ff").save(image)
    calls: list[tuple[str, str | None, int]] = []

    class FakeProvider:
        def __init__(self, _cache):
            pass

        def search_and_cache(
            self, query, *, max_results, provider=None, force_fresh=False
        ):
            calls.append((query, provider, max_results))
            if provider == "pixabay":
                return SimpleNamespace(
                    provider="pixabay", status="unavailable", attempts=0, items=[]
                )
            is_process = "workflow" in query or "process" in query
            return SimpleNamespace(
                provider="pexels",
                status="ready",
                attempts=1,
                items=[
                    {
                        "asset_id": "broll-provider-process" if is_process else "broll-provider-crm",
                        "name": "workflow process demonstration"
                        if is_process
                        else "CRM customer database dashboard",
                        "source_provider": "pexels",
                        "source_type": "provider_cache",
                        "source_url": "https://www.pexels.com/video/provider/",
                        "license_name": "Pexels License",
                        "authorization_status": "confirmed",
                        "publish_licensed": True,
                        "asset_origin": "stock_video_asset",
                        "cache_path": str(image),
                    }
                ],
            )

    monkeypatch.setattr(workflow_module, "StockBrollProvider", FakeProvider)
    monkeypatch.setattr(service, "list_visual_assets", lambda kind=None: [])
    shot_plan = {
        "timeline_duration_seconds": 20.0,
        "shots": [
            {
                "shot_id": f"shot-{index:02d}",
                "role": "A-roll",
                "duration_seconds": 2.0,
                "timeline_start": float(index * 6 + 2),
                "source_start": float(index * 6 + 2),
                "source_end": float(index * 6 + 4),
            }
            for index in range(3)
        ],
    }
    bindings = service._auto_bind_release_broll_assets(
        shot_plan,
        transcript_segments=[
            {"start": 2.0, "end": 4.0, "text": "客户数据库持续沉淀"},
            {"start": 8.0, "end": 10.0, "text": "教程设置自动提醒步骤"},
        ],
    )

    assert set(bindings) == {"shot-00", "shot-01"}
    assert len({item[0] for item in calls}) == 2
    assert all(item[2] == 8 for item in calls)
    assert {item["source_provider"] for item in bindings.values()} == {"pexels"}
    assert {item["asset_id"] for item in bindings.values()} == {
        "broll-provider-crm",
        "broll-provider-process",
    }


def test_release_long_form_cached_visuals_are_temporally_spread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A long plan must not spend every semantic visual slot at its opening."""

    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    asset_path = tmp_path / "stock.mp4"
    asset_path.write_bytes(b"test-media")
    asset = {
        "asset_id": "broll-stock",
        "_path": str(asset_path),
        "media_type": "video/mp4",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "asset_origin": "stock_video_asset",
        "source_provider": "pexels",
    }
    monkeypatch.setattr(service, "list_visual_assets", lambda kind=None: [asset])
    monkeypatch.setattr(workflow_module, "local_broll_is_real", lambda *_args: True)
    monkeypatch.setattr(
        workflow_module,
        "match_local_visual_asset",
        lambda *_args, **_kwargs: {
            **asset,
            "match_score": 80,
            "match_reason": ["concrete_visual_intersection:scene"],
        },
    )
    monkeypatch.setattr(
        workflow_module,
        "query_visual_concepts",
        lambda _query: {"scene"},
    )
    monkeypatch.setattr(
        workflow_module,
        "StockBrollProvider",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    shots = [
        {
            "shot_id": f"shot-{index:02d}",
            "role": "A-roll",
            "duration_seconds": 2.5,
            "timeline_start": float(index * 5 + 1),
            "source_start": float(index * 5 + 1),
            "source_end": float(index * 5 + 3.5),
        }
        for index in range(32)
    ]
    shot_plan = {
        "timeline_duration_seconds": 104.0,
        "shots": shots,
    }
    semantic_texts = [
        "顾客扫码加入会员",
        "餐厅顾客就餐",
        "门店小程序操作",
        "会员关系持续触达",
    ]
    segments = [
        {
            "start": shot["source_start"],
            "end": shot["source_end"],
            "text": semantic_texts[index % len(semantic_texts)],
        }
        for index, shot in enumerate(shots)
    ]

    service._auto_bind_release_broll_assets(
        shot_plan,
        transcript_segments=segments,
    )

    selected_starts = sorted(
        float(request["start"])
        for request in shot_plan["visual_requests"]
    )
    assert len(selected_starts) == 12
    assert selected_starts[-1] >= 90.0
    assert all(
        right - left >= 7.5
        for left, right in zip(selected_starts, selected_starts[1:])
    )


def test_release_reuses_semantically_tagged_cache_before_stock_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from PIL import Image

    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    image = tmp_path / "cached.png"
    Image.new("RGB", (128, 128), "#1677ff").save(image)
    image_obj = Image.open(image)
    for x in range(0, 128, 16):
        for y in range(0, 128, 16):
            image_obj.putpixel((x, y), ((x * 2) % 255, (y * 2) % 255, 80))
    image_obj.save(image)

    class NoSearchProvider:
        def __init__(self, _cache):
            pass

        def search_and_cache(self, *_args, **_kwargs):
            raise AssertionError("semantic cache hit must not search")

    monkeypatch.setattr(workflow_module, "StockBrollProvider", NoSearchProvider)
    monkeypatch.setattr(
        service,
        "list_visual_assets",
        lambda kind=None: [
            {
                "asset_id": "broll-cached-db",
                "_path": str(image),
                "media_type": "image/png",
                "authorization_status": "confirmed",
                "publish_licensed": True,
                "asset_origin": "stock_video_asset",
                "source_provider": "pexels",
                "semantic_binding": "客户数据库",
                "keywords": ["数据库", "客户"],
            }
        ],
    )
    shot_plan = {
        "shots": [
            {
                "shot_id": "shot-01",
                "role": "A-roll",
                "duration_seconds": 2,
                "timeline_start": 2,
                "source_start": 2,
                "source_end": 4,
            },
            {
                "shot_id": "shot-02",
                "role": "A-roll",
                "duration_seconds": 2,
                "timeline_start": 4,
                "source_start": 4,
                "source_end": 6,
            },
        ]
    }

    bindings = service._auto_bind_release_broll_assets(
        shot_plan,
        transcript_segments=[{"start": 2, "end": 4, "text": "客户数据库持续沉淀"}],
    )

    assert bindings["shot-01"]["asset_id"] == "broll-cached-db"


def test_release_missing_stock_keys_keeps_safe_degradation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    monkeypatch.setattr(service, "list_visual_assets", lambda kind=None: [])
    shot_plan = {
        "shots": [
            {
                "shot_id": "shot-01",
                "role": "A-roll",
                "duration_seconds": 2,
                "timeline_start": 2,
                "source_start": 2,
                "source_end": 4,
            }
        ]
    }

    assert service._auto_bind_release_broll_assets(
        shot_plan,
        transcript_segments=[{"start": 2, "end": 4, "text": "无授权素材的主题"}],
    ) == {}


def test_visual_requests_are_structured_and_unrelated_gap_is_not_a_request():
    segments = [
        {"start": 2.0, "end": 4.0, "text": "客户数据库持续沉淀"},
        {"start": 8.0, "end": 10.0, "text": "工厂品牌产品持续曝光"},
    ]
    database = workflow_module._build_visual_request(
        {"source_start": 2.0, "source_end": 4.0}, segments
    )
    gap = workflow_module._build_visual_request(
        {"source_start": 4.0, "source_end": 6.0}, segments
    )
    product = workflow_module._build_visual_request(
        {"source_start": 8.0, "source_end": 10.0}, segments
    )
    assert database["visual_type"] == "data"
    assert database["search_queries"][:2] == [
        "CRM dashboard customer database",
        "business analytics dashboard",
    ]
    assert gap["visual_type"] == "abstract"
    assert gap["search_queries"] == []
    assert product["visual_type"] == "product"
    assert product["preferred_mode"] == "full"
    assert all(len(query.split()) <= 5 for query in product["search_queries"])


def test_visual_request_uses_concrete_factory_robot_scene_queries():
    request = workflow_module._build_visual_request(
        {
            "source_start": 0.0,
            "source_end": 3.0,
        },
        [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "工业机器人进入工厂流水线",
            }
        ],
    )

    assert request["visual_type"] == "scene"
    assert request["preferred_mode"] == "full"
    assert request["search_queries"] == [
        "industrial robot factory floor",
        "robot manufacturing automation",
        "factory production line robotics",
    ]
    assert "robot" in request["expected_subject"]


def test_visual_request_uses_generic_vehicle_scene_queries_without_product_dashboard_guess():
    request = workflow_module._build_visual_request(
        {"source_start": 4.0, "source_end": 7.0},
        [{"start": 4.0, "end": 7.0, "text": "买二手车要先研究新车行情和车况"}],
    )

    assert request["visual_type"] == "vehicle_scene"
    assert request["preferred_mode"] == "full"
    assert request["search_queries"] == [
        "used car inspection",
        "used car buyer checking vehicle",
        "second hand car dealership",
    ]


def test_default_local_filter_has_no_broll_overlay(tmp_path: Path):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    rendered = service._local_rhythm_video_filter(
        duration_seconds=8,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.15,
        subtitle_filter="approved.ass",
    )
    assert "overlay=W-w-28:90" not in rendered
    assert "subtitles='approved.ass'" in rendered


def test_local_render_thread_options_are_bounded_and_overridable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIDEO_EDITOR_LOCAL_THREADS", raising=False)
    monkeypatch.setattr(workflow_module.os, "cpu_count", lambda: 16)

    assert workflow_module._local_render_thread_options() == [
        "-threads",
        "8",
        "-filter_threads",
        "4",
        "-filter_complex_threads",
        "4",
    ]

    monkeypatch.setenv("VIDEO_EDITOR_LOCAL_THREADS", "2")
    assert workflow_module._local_render_thread_options() == [
        "-threads",
        "2",
        "-filter_threads",
        "2",
        "-filter_complex_threads",
        "2",
    ]

    monkeypatch.setenv("VIDEO_EDITOR_LOCAL_THREADS", "auto")
    assert workflow_module._local_render_thread_options() == [
        "-threads",
        "0",
        "-filter_threads",
        "0",
        "-filter_complex_threads",
        "0",
    ]


def test_primary_subtitle_graph_avoids_a_second_burn() -> None:
    assert workflow_module._subtitle_is_already_burned(
        {}, primary_subtitle_filter="approved.ass"
    )
    assert workflow_module._subtitle_is_already_burned(
        {"subtitles_burned_in": True}, primary_subtitle_filter=""
    )
    assert not workflow_module._subtitle_is_already_burned(
        {}, primary_subtitle_filter=""
    )


def test_release_filter_supports_multiple_pip_and_full_visual_events(
    tmp_path: Path,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    rendered = service._local_rhythm_video_filter(
        duration_seconds=8,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.0,
        subtitle_filter="approved.ass",
        brolls=[
            {"start": 1.0, "end": 2.5, "mode": "pip", "input_index": 2},
            {"start": 3.0, "end": 5.0, "mode": "full", "input_index": 3},
        ],
    )

    assert "[2:v]setpts=PTS-STARTPTS" in rendered
    assert "overlay=202:768" in rendered
    assert "[3:v]setpts=PTS-STARTPTS" in rendered
    assert "overlay=0:0" in rendered
    assert rendered.index("[with_broll0]") < rendered.index("[with_broll1]")
    assert rendered.index("subtitles='approved.ass'") > rendered.index("[with_broll1]")


def test_sparse_pip_has_editorial_entry_animation_without_card_border(
    tmp_path: Path,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    rendered = service._local_rhythm_video_filter(
        duration_seconds=8,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.0,
        subtitle_filter="approved.ass",
        brolls=[{"start": 1.0, "end": 3.0, "mode": "pip", "input_index": 2}],
        pip_layout="sparse",
    )

    assert "scale=w='trunc(187*(0.90+0.10*min(1,t/0.220))/2)*2'" in rendered
    assert "color=c=black@0.0:s=187x167" in rendered
    assert "fade=t=in:st=0:d=0.220:alpha=1" in rendered
    assert "setpts=PTS+1.000/TB[broll]" in rendered
    assert "drawbox=x=1:y=1" not in rendered


def test_pip_geometry_avoids_face_and_subtitle_and_skips_unsafe_canvas(
    tmp_path: Path,
):
    geometry = workflow_module._portrait_pip_geometry(720, 1280)
    assert geometry["safe"] is True
    assert geometry["bbox"]["width"] / 720 == pytest.approx(0.44, abs=0.01)
    assert geometry["intersects_face_safe_bbox"] is False
    assert geometry["intersects_subtitle_bbox"] is False

    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    rendered = service._local_rhythm_video_filter(
        duration_seconds=8,
        width=1000,
        height=1000,
        fps=30,
        playback_rate=1.0,
        subtitle_filter="approved.ass",
        brolls=[{"start": 1.0, "end": 3.0, "mode": "pip", "input_index": 2}],
    )
    assert "overlay=202:768" not in rendered
    assert "subtitles='approved.ass'" in rendered


def test_semantic_info_geometry_avoids_face_pip_and_subtitle_safe_areas():
    geometry = workflow_module._semantic_info_geometry(720, 1280)

    assert geometry["safe"] is False
    assert geometry["reason"] == "lower_left_safe_zone"
    assert geometry["intersects_face_safe_bbox"] is False
    assert geometry["intersects_pip_bbox"] is True
    assert geometry["intersects_subtitle_bbox"] is False
    assert geometry["bbox"]["left"] == 36
    assert geometry["bbox"]["right"] == 396


def test_release_filter_keeps_transparent_vector_track_before_subtitles(
    tmp_path: Path,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    rendered = service._local_rhythm_video_filter(
        duration_seconds=8,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.0,
        subtitle_filter="approved.ass",
        vectors=[
            {"start": 1.0, "end": 3.0, "mode": "pip", "input_index": 2},
        ],
        vector_input_index=2,
    )

    assert "rotate=0.035*sin(2*PI*t/2)" in rendered
    assert "color=0x00000000" in rendered
    assert "overlay=W-w-42:70" in rendered
    assert rendered.index("[with_vector0]") < rendered.index("subtitles='approved.ass'")


def test_release_filter_renders_semantic_motion_badges_before_subtitles(
    tmp_path: Path,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    rendered = service._local_rhythm_video_filter(
        duration_seconds=8,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.0,
        subtitle_filter="approved.ass",
        motion_items=[
            {
                "start": 1.0,
                "end": 2.8,
                "style_id": "number_slam",
                "input_index": 2,
            },
            {
                "start": 4.0,
                "end": 5.5,
                "style_id": "warning_shake",
                "input_index": 3,
            },
        ],
        motion_input_index=2,
    )

    assert "scale=w='trunc(518*(1.0+0.12*if(lt(t,0.20),1-t/0.20,0))/2)*2'" in rendered
    assert "overlay=x='(W-w)/2':y='trunc(H*0.76-h/2)'" in rendered
    assert "overlay=x='(W-w)/2+8*sin(2*PI*t/0.10)':y='trunc(H*0.30-h/2)'" in rendered
    assert rendered.index("[with_motion1]") < rendered.index("subtitles='approved.ass'")


def test_release_filter_renders_semantic_info_band_after_broll_before_subtitles(
    tmp_path: Path,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )
    rendered = service._local_rhythm_video_filter(
        duration_seconds=8,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.0,
        subtitle_filter="approved.ass",
        brolls=[{"start": 1.0, "end": 3.0, "mode": "pip", "input_index": 2}],
        semantic_layers=[
            {
                "start": 1.0,
                "end": 3.0,
                "renderer": "semantic_info_band",
                "input_index": 3,
            }
        ],
        semantic_input_index=3,
    )

    assert "[3:v]format=rgba,fade=t=in:st=0:d=0.24:alpha=1[semantic0]" in rendered
    assert "overlay=0:0" in rendered
    assert rendered.index("[with_semantic0]") < rendered.index("subtitles='approved.ass'")


def test_release_template_replaces_sticker_like_vectors_with_semantic_layer_by_default(
    tmp_path: Path,
):
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
    )

    vector_track, assets = service._ensure_release_creative_assets(
        "企业客户增长和销售管理",
        shot_plan={
            "shots": [
                {
                    "role": "A-roll",
                    "timeline_start": 2.0,
                    "duration_seconds": 2.5,
                    "timeline_end": 4.5,
                }
            ]
        },
    )

    assert assets == []
    assert vector_track["items"] == []
    assert vector_track["asset_count"] == 0
    assert vector_track["kind"] == "semantic_motion_layer"
    assert vector_track["source_policy"] == "safe_subject_motion_without_invented_facts"


def test_batch_waits_for_subtitle_then_confirms_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    repo.save_task(_avatar_task("avatar-real", video))
    transcript = _TranscriptionStub(approved=None)
    service = VideoEditorWorkflowService(
        repo, _VideoEditingStub(tmp_path / "edits"), transcript, None
    )

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
                "analysis_json": '{"recommended_steps": []}',
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
            outputs={
                "workflow": "edit",
                "source_id": "avatar:avatar-real",
                "analysis_id": kwargs["analysis_id"],
            },
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
    with pytest.raises(
        VideoEditorWorkflowError, match="请先在当前页保存并确认字幕成稿"
    ):
        service.continue_batch_item(created["batch_id"], item["item_id"])

    transcript.approved = object()
    rendering = service.continue_batch_item(created["batch_id"], item["item_id"])
    assert rendering["items"][0]["status"] == "rendering"

    current = repo.get_task("edit-batch")
    assert isinstance(current, VideoEditTask)
    result = current.model_copy(
        update={
            "status": TaskStatus.SUCCEEDED,
            "progress": 100,
            "stage": "剪辑完成",
            "result_path": str(video),
            "result_size_bytes": video.stat().st_size,
        }
    )
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


def test_local_title_candidates_keep_unfamiliar_script_topic():
    titles = VideoEditorWorkflowService._local_title_candidates(
        "本地上传 · 本地审核口播.mp4",
        "社区出现了一种便民服务模式用户可以按步骤完成预约",
        "douyin",
    )

    assert titles
    assert len(titles) <= 3
    assert "便民服务模式" in titles[0]


def test_grammar_hook_title_is_short_complete_and_transcript_grounded():
    hook = VideoEditorWorkflowService._grammar_hook_title(
        "本地上传 · 陌生视频.mp4",
        "最近上海出现了一种特别的社区服务模式，用户可以按步骤完成预约。",
    )

    assert 6 <= len(hook) <= 14
    assert "上海" in hook
    assert any(term in hook for term in ("社区服务", "服务模式"))
    assert "…" not in hook


def test_sparse_pip_geometry_alternates_safe_sides():
    right = workflow_module._sparse_pip_geometry(720, 1280, variant=0)
    left = workflow_module._sparse_pip_geometry(720, 1280, variant=1)

    assert right["safe"] and left["safe"]
    assert right["bbox"]["left"] > left["bbox"]["left"]
    assert right["bbox"]["right"] > left["bbox"]["right"]
    assert not right["intersects_face_safe_bbox"]
    assert not left["intersects_subtitle_bbox"]
    assert left["bbox"]["left"] / 720 < 0.02
    assert right["bbox"]["right"] / 720 > 0.98
    assert left["bbox"]["top"] / 1280 == pytest.approx(0.49, abs=0.01)


def test_caption_emphasis_preserves_a_late_semantic_payoff():
    cues = [
        {
            "start": float(index * 5),
            "end": float(index * 5 + 2),
            "lines": ["这是一段可读的口播"],
            "emphasis_range": {"line_index": 0, "start": 0, "end": 2},
            "emphasis_style": {"style_id": "test"},
            "semantic_role": "KEY_CLAIM",
            "semantic_importance": 0.8,
            "source_segment_index": index,
        }
        for index in range(19)
    ]
    cues[-1]["semantic_role"] = "CTA"
    cloud_module._apply_adaptive_caption_effects(cues)

    emphasized = [cue for cue in cues if cue.get("emphasis_range")]
    assert len(emphasized) <= 17
    assert emphasized[-1]["semantic_role"] == "CTA"


def test_authorized_bgm_is_added_to_batch_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
            outputs={
                "workflow": "edit",
                "source_id": "avatar:avatar-real",
                "analysis_id": kwargs["analysis_id"],
            },
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


def test_bgm_recommendation_defaults_business_talking_head_to_low_intrusion_tech_track(
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
    tech = service.upload_bgm(
        file_name="tech-business.mp3",
        media_type="audio/mpeg",
        media_bytes=b"tech-business",
        mood="科技·未来·舒缓·商务",
        rights_confirmed=True,
        rights_holder="本机验收授权",
        voiceover_category="科技未来",
        energy="平稳",
    )
    service.upload_bgm(
        file_name="business-forward.mp3",
        media_type="audio/mpeg",
        media_bytes=b"business-forward",
        mood="商业·增长·有推动感",
        rights_confirmed=True,
        rights_holder="本机验收授权",
        voiceover_category="商业表达",
        energy="有推动感",
    )

    selected, reason = service._recommend_bgm_asset(
        {
            "transcript": "客户资源沉淀在企业数据库，工厂和品牌需要持续触达。",
            "media": {"duration_seconds": 60},
        },
        "企业客户数据库营销机制",
    )

    assert selected is not None
    assert selected["asset_id"] == tech["asset_id"]
    assert selected["voiceover_category"] == "科技未来"
    assert "科技未来" in reason


def test_retired_bgm_is_hidden_but_stays_resolvable(
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
    retired = service.upload_bgm(
        file_name="old-library.mp3",
        media_type="audio/mpeg",
        media_bytes=b"old",
        mood="旧音乐",
        rights_confirmed=True,
        rights_holder="测试公司",
        voiceover_category="通用口播",
        energy="克制",
    )
    active = service.upload_bgm(
        file_name="new-library.mp3",
        media_type="audio/mpeg",
        media_bytes=b"new",
        mood="新音乐",
        rights_confirmed=True,
        rights_holder="测试公司",
        voiceover_category="通用口播",
        energy="克制",
    )
    metadata_path = service._bgm_directory() / f"{retired['asset_id']}.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["retired"] = True
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False),
        encoding="utf-8",
    )

    listed_ids = {item["asset_id"] for item in service.list_bgm_assets()}

    assert active["asset_id"] in listed_ids
    assert active["generated"] is False
    assert retired["asset_id"] not in listed_ids
    assert (
        service.resolve_bgm_asset(retired["asset_id"])["asset_id"]
        == retired["asset_id"]
    )


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


def test_auto_bgm_keeps_original_audio_when_library_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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


def test_full_review_transcript_corrects_homophones_without_changing_word_clock():
    raw = {
        "start": 26.12,
        "end": 55.02,
        "text": "这份方案的价质,花49块,就能半个学习账号,半完马上拿到一份资料,等于百送学习资格,但中头戏是后面的练习活动,立刻拿到六张无门槛又会劝,学员把劝发到群里或者同学",
        "reviewed_text": "这份方案的价值,花49元,就能办个学习账号,办完马上拿到一份资料,等于白送学习资格,但重头戏是后面的练习活动,立刻拿到六张无门槛优惠券,学员把券发到群里或者同学",
        "reviewed_text_corrections": [
            {"from": "价质", "to": "价值", "reason": "human_listening_review"},
            {"from": "49块", "to": "49元", "reason": "human_listening_review"},
            {"from": "半个学习账号", "to": "办个学习账号", "reason": "human_listening_review"},
            {"from": "半完马上", "to": "办完马上", "reason": "human_listening_review"},
            {"from": "等于百送", "to": "等于白送", "reason": "human_listening_review"},
            {"from": "中头戏", "to": "重头戏", "reason": "human_listening_review"},
            {"from": "又会劝", "to": "优惠券", "reason": "human_listening_review"},
            {"from": "把劝发到", "to": "把券发到", "reason": "human_listening_review"},
        ],
        "words": [
            {"start": 26.12, "end": 26.3, "word": "价质"},
            {"start": 26.3, "end": 26.5, "word": ","},
            {"start": 26.5, "end": 26.7, "word": "花"},
            {"start": 26.7, "end": 26.9, "word": "49"},
            {"start": 26.9, "end": 27.1, "word": "块"},
            {"start": 27.1, "end": 27.4, "word": "半个学习账号"},
            {"start": 27.4, "end": 27.8, "word": "半完马上"},
            {"start": 27.8, "end": 28.2, "word": "送一份资料"},
            {"start": 28.2, "end": 28.6, "word": "等于百送"},
            {"start": 28.6, "end": 29.0, "word": "学习资格"},
            {"start": 29.0, "end": 29.4, "word": "中头戏"},
            {"start": 29.4, "end": 30.0, "word": "立刻拿到六张无门槛又会劝"},
            {"start": 30.0, "end": 30.6, "word": "学员把劝发到"},
            {"start": 30.6, "end": 31.0, "word": "群里或者同学"},
        ],
    }
    reviewed, corrections = workflow_module._review_transcript_segments([raw])
    assert len(reviewed) == 1
    text = reviewed[0]["text"]
    assert "价值" in text
    assert "办个学习账号" in text
    assert "优惠券" in text
    assert "中头戏" not in text
    assert len(corrections) >= 6
    assert len(re.sub(r"[^\\w\\u4e00-\\u9fff]", "", raw["text"])) == len(
        re.sub(r"[^\\w\\u4e00-\\u9fff]", "", text)
    )


def test_full_transcript_builds_grounded_visual_intents_across_late_timeline():
    asr = json.loads(
        Path("work/auto-fine-cut-adaptive-20260824-short-real/asr-full.json").read_text(
            encoding="utf-8"
        )
    )
    reviewed, _ = workflow_module._review_transcript_segments(asr["segments"])
    intents = workflow_module._build_adaptive_visual_intents(reviewed)
    assert intents
    assert any(item["fact"] == "80%" for item in intents)
    assert any(item["visual_intent"] in {"data_chart", "concept_card", "network", "transition", "cta"} for item in intents)
    assert max(float(item["end"]) for item in intents) > 90
    assert all(item["grounded_in_text"] is True for item in intents)
    assert all(item["renderer"] == "data_visual_card" for item in intents)
    assert all(item["mode"] == "full" for item in intents)


def test_adaptive_visual_card_geometry_avoids_face_pip_and_subtitles():
    geometry = workflow_module._adaptive_visual_card_geometry(720, 1280)
    assert geometry["safe"] is True
    assert geometry["intersects_face_safe_bbox"] is False
    assert geometry["intersects_subtitle_bbox"] is False
    assert geometry["intersects_pip_bbox"] is False


def test_subtitle_audio_activity_gate_rejects_uncovered_speech_gap():
    cues = [{"start": 0.0, "end": 2.0}, {"start": 7.0, "end": 9.0}]
    active = [{"start": 0.0, "end": 9.0}]
    result = workflow_module.VideoEditorWorkflowService._subtitle_audio_activity_gate(
        cues, active
    )
    assert result["passed"] is False
    assert result["uncovered_ranges"] == [{"start": 2.0, "end": 7.0}]


def test_word_timestamps_bound_audio_activity_away_from_pause_noise():
    active = [{"start": 0.0, "end": 2.0}]
    word_ranges = [
        {"start": 0.0, "end": 0.8},
        {"start": 1.2, "end": 2.0},
    ]
    bounded = workflow_module.VideoEditorWorkflowService._intersect_audio_activity_with_spoken_ranges(
        active, word_ranges
    )
    result = workflow_module.VideoEditorWorkflowService._subtitle_audio_activity_gate(
        [{"start": 0.0, "end": 0.8}, {"start": 1.2, "end": 2.0}], bounded
    )
    assert result["passed"] is True


def test_v2_local_export_metadata_does_not_fall_back_to_legacy_template():
    preset = style_presets_module.get_style_preset("talking-head-local-grammar-v2")
    assert preset["phase"] == "phase_1_local_only"
    assert preset["external_visuals"] == {
        "allow_broll_video": False,
        "allow_ai_video": False,
        "allow_generated_images": False,
        "allow_network_search": False,
        "allow_bgm": False,
    }


def test_subtitle_text_integrity_gate_rejects_duplicate_or_missing_text():
    source = [{"text": "通过门店小程序完成的", "start": 0, "end": 2}]
    duplicate = {"cues": [{"lines": ["通过门店小程序完成的的"], "start": 0, "end": 2}]}
    result = workflow_module.VideoEditorWorkflowService._subtitle_text_integrity_gate(
        source, duplicate
    )
    assert result["passed"] is False
    assert result["duplicate_function_word_boundary"] is True


def test_generated_explainer_defaults_to_full_cutaway_not_pip():
    assert (
        VideoEditorWorkflowService._release_broll_mode(
            {
                "asset_origin": "generated_image_asset",
                "manifest_role": "pip_broll",
            },
            0,
        )
        == "full"
    )
    assert (
        VideoEditorWorkflowService._release_broll_mode(
            {"visual_intent": "speaker_pip"},
            0,
        )
        == "pip"
    )


def test_adaptive_visual_intents_are_generic_and_not_sample_answers():
    segments = [
        {"start": 1.0, "end": 3.0, "text": "订单转化率提升到80%"},
        {"start": 6.0, "end": 9.0, "text": "步骤流程然后执行支付"},
        {"start": 12.0, "end": 15.0, "text": "评论区告诉我你的问题"},
    ]
    intents = workflow_module._build_adaptive_visual_intents(segments)
    assert {item["visual_intent"] for item in intents} >= {
        "data_chart",
        "concept_card",
        "cta",
    }
    assert all(item["mode"] == "full" for item in intents)
    assert all(item["position"] == "full_cutaway" for item in intents)
    source = Path("src/services/video_editor_workflow.py").read_text(encoding="utf-8")
    assert "广州" not in source
    assert "客户数据库" not in source


def test_visual_card_rejects_scene_descriptions_and_empty_or_repeated_cards():
    scene = [
        {"start": 0.0, "end": 3.0, "text": "城市出现一种特别的服务模式"},
    ]
    assert workflow_module._build_adaptive_visual_intents(scene) == []

    empty_chart, empty_reason = workflow_module._sanitize_adaptive_visual_item(
        {
            "renderer": "data_visual_card",
            "visual_intent": "data_chart",
            "semantic_text": "特别的餐饮模式",
            "source_text": "今天介绍一种特别的餐饮模式",
        }
    )
    assert empty_chart is None
    assert empty_reason == "data_chart_without_grounded_fact"

    repeated_card, repeated_reason = workflow_module._sanitize_adaptive_visual_item(
        {
            "renderer": "data_visual_card",
            "visual_intent": "concept_card",
            "semantic_text": "小程序",
            "source_text": "小程序",
            "diagram_labels": ["小程序", "小程序"],
        }
    )
    assert repeated_card is None
    assert repeated_reason == "card_repeats_full_spoken_text"


def test_data_card_has_one_grounded_metric_and_no_unlabelled_bar_motif():
    item, reason = workflow_module._sanitize_adaptive_visual_item(
        {
            "renderer": "data_visual_card",
            "visual_intent": "data_chart",
            "semantic_text": "客户主动加了80%的私域",
            "fact": "80%",
            "source_text": "客户主动加了80%的私域",
        }
    )
    assert reason is None
    assert item["semantic_text"] == "80%"
    source = Path("src/services/video_editor_workflow.py").read_text(encoding="utf-8")
    assert "for index, ratio in enumerate((0.38, 0.62, 0.84))" not in source


def test_adaptive_visual_card_is_not_enabled_on_the_default_render_path(monkeypatch):
    monkeypatch.delenv("VIDEO_EDITOR_ENABLE_ADAPTIVE_VISUAL_CARD", raising=False)
    assert workflow_module._adaptive_visual_card_opted_in() is False

    monkeypatch.setenv("VIDEO_EDITOR_ENABLE_ADAPTIVE_VISUAL_CARD", "true")
    assert workflow_module._adaptive_visual_card_opted_in() is True


def test_reviewed_word_clock_uses_following_word_after_an_audio_pause():
    source_segments = [
        {
            "text": "甲乙丙丁",
            "start": 0.0,
            "end": 2.0,
            "words": [
                {"text": "甲乙", "start": 0.0, "end": 0.4},
                {"text": "丙丁", "start": 1.0, "end": 1.4},
            ],
        }
    ]
    preview = {
        "cues": [
            {
                "start": 0.0,
                "end": 0.4,
                "lines": ["甲乙"],
                "source_segment_index": 0,
            },
            {
                "start": 0.4,
                "end": 1.4,
                "lines": ["丙丁"],
                "source_segment_index": 0,
            },
        ]
    }

    snapped = workflow_module.VideoEditorWorkflowService._snap_preview_cues_to_reviewed_word_clock(
        source_segments,
        preview,
    )

    assert snapped["cues"][0]["start"] == pytest.approx(0.0)
    assert snapped["cues"][0]["end"] == pytest.approx(0.4)
    assert snapped["cues"][1]["start"] == pytest.approx(1.0)
    assert snapped["cues"][1]["end"] == pytest.approx(1.4)
    timing = workflow_module.VideoEditorWorkflowService._subtitle_word_timing_quality(
        source_segments,
        snapped,
        fps=30.0,
    )
    assert timing["word_p95_ms"] == pytest.approx(0.0)
    assert timing["mapping_error_frames"] == pytest.approx(0.0)


def test_caption_groups_remove_only_repeated_boundary_character():
    from src.services.video_editor_cloud import _normalize_preview_cue_texts

    segments = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "完成的老板不用费心",
            "words": [
                {"text": "完成的", "start": 0.0, "end": 0.8},
                {"text": "老板不用费心", "start": 0.8, "end": 1.8},
            ],
        }
    ]
    cues = [
        {"start": 0.0, "end": 0.8, "lines": ["完成的"], "source_segment_index": 0},
        {
            "start": 0.8,
            "end": 1.8,
            "lines": ["的老板不用费心"],
            "source_segment_index": 0,
        },
    ]
    normalized = _normalize_preview_cue_texts(cues, segments, max_chars=11)
    texts = ["".join(cue["lines"]) for cue in normalized]
    assert texts == ["完成的", "老板不用费心"]
    assert "".join(texts) == "完成的老板不用费心"


def test_word_alias_and_attached_punctuation_keep_exact_caption_clock():
    from src.services.video_editor_cloud import (
        _caption_cue_timings_from_words,
        _caption_lexical_words,
    )

    words = [
        {"start": 0.0, "end": 0.4, "word": "客户"},
        {"start": 0.4, "end": 0.5, "word": "，"},
        {"start": 0.8, "end": 1.2, "word": "数据库。"},
    ]
    lexical = _caption_lexical_words(
        words,
        text="客户数据库。",
        segment_start=0.0,
        segment_end=1.2,
    )
    assert "".join(item["text"] for item in lexical) == "客户数据库"
    assert _caption_cue_timings_from_words(
        ["客户", "数据库"],
        words,
        segment_start=0.0,
        segment_end=1.2,
    ) == [(0.0, 0.5), (0.8, 1.2)]


def test_lexical_clock_handles_mixed_token_granularity_without_splitting_compounds():
    from src.services.video_editor_cloud import _caption_lexical_words

    words = [
        {"start": 0.0, "end": 0.4, "word": "你的"},
        {"start": 0.4, "end": 0.6, "word": "店"},
        {"start": 0.6, "end": 0.8, "word": "系"},
        {"start": 0.8, "end": 1.0, "word": "统"},
    ]
    lexical = _caption_lexical_words(
        words,
        text="你的店系统",
        segment_start=0.0,
        segment_end=1.0,
    )

    assert [item["text"] for item in lexical] == ["你", "的", "店", "系统"]
    assert lexical[-1]["start"] == pytest.approx(0.6)
    assert lexical[-1]["end"] == pytest.approx(1.0)


def test_long_character_token_segment_uses_lexical_word_clock_partition():
    from src.services.video_editor_cloud import build_business_talking_head_overlay_preview

    text = "这是一个用于验证长段词序时钟的通用教程内容没有特殊答案并且保持完整顺序"
    segment = {"start": 0.0, "end": len(text) * 0.3, "text": text, "words": []}
    for index, character in enumerate(text):
        segment["words"].append(
            {
                "start": index * 0.3,
                "end": (index + 1) * 0.3,
                "text": character,
            }
        )

    preview = build_business_talking_head_overlay_preview(
        [segment], title="", output_profile="720p"
    )
    report = VideoEditorWorkflowService._subtitle_word_timing_quality(
        [segment], preview, fps=30.0
    )

    assert len(preview["cues"]) == 5
    assert all(
        cue["word_clock_mapping"] == "exact_or_reviewed_text_sequence_alignment"
        for cue in preview["cues"]
    )
    assert max(cue["end"] - cue["start"] for cue in preview["cues"]) <= 2.4
    assert report["unmatched_cue_count"] == 0


def test_adaptive_visual_intents_pin_fact_to_word_timestamps():
    """P1-1: data_chart cards must anchor on the word that carries the fact,
    not the trailing edge of a multi-second source segment.

    Regression: previously the card window was derived as
    ``segment_end - 2.8`` which placed the 80% / 49元 / 10% cards 12-24s
    after the speaker actually said the number, so viewers saw a "80%" card
    hovering over a different sentence.
    """
    segments = [
        {
            "start": 10.56,
            "end": 25.64,
            "text": "80%的顾客",
            "words": [
                {"word": "80", "start": 10.56, "end": 10.92},
                {"word": "%", "start": 10.92, "end": 11.16},
                {"word": "的", "start": 11.16, "end": 11.36},
                {"word": "顾客", "start": 11.36, "end": 11.68},
            ],
        },
        {
            "start": 26.12,
            "end": 55.02,
            "text": "49元就能办",
            "words": [
                {"word": "49", "start": 28.42, "end": 28.84},
                {"word": "元", "start": 28.84, "end": 29.00},
                {"word": "就能", "start": 29.00, "end": 29.30},
                {"word": "办", "start": 29.30, "end": 29.50},
            ],
        },
        {
            "start": 55.64,
            "end": 80.90,
            "text": "10%现金奖励",
            "words": [
                {"word": "10", "start": 57.46, "end": 57.70},
                {"word": "%", "start": 57.70, "end": 57.90},
                {"word": "现金", "start": 57.90, "end": 58.20},
                {"word": "奖励", "start": 58.20, "end": 58.50},
            ],
        },
    ]
    intents = workflow_module._build_adaptive_visual_intents(segments)
    by_fact = {item["fact"]: item for item in intents if item.get("fact") in {"80%", "49元", "10%"}}
    assert set(by_fact) == {"80%", "49元", "10%"}, by_fact
    # Each card must START within +/- 0.4s of the word that carries the fact.
    assert abs(by_fact["80%"]["start"] - 10.56) < 0.4, by_fact["80%"]
    assert abs(by_fact["49元"]["start"] - 28.42) < 0.4, by_fact["49元"]
    assert abs(by_fact["10%"]["start"] - 57.46) < 0.4, by_fact["10%"]
    # Each card must END no later than 1.6s after the fact word ends
    # (the renderer keeps the card visible long enough to read the number
    # plus a beat of context, but never more than 2.8s after the fact).
    assert by_fact["80%"]["end"] <= 10.92 + 1.6
    assert by_fact["49元"]["end"] <= 28.84 + 1.6
    assert by_fact["10%"]["end"] <= 57.70 + 1.6
    # And the old buggy window (segment_end - 2.8) must NOT be picked.
    for fact, item in by_fact.items():
        seg = next(s for s in segments if any(
            w.get("word") in fact.replace("%", "").replace("元", "").split()
            for w in s.get("words", [])
        ))
        old_start = seg["end"] - 2.8
        assert abs(item["start"] - old_start) > 0.5, (
            f"{fact} still using legacy segment_end-2.8s heuristic: {item['start']} vs {old_start}"
        )


# P0-收口 2026-08-31: transcript source identity / truthful gate / jieba
# estimation.  These tests are provider-agnostic — they fail if any
# future code path tries to claim word_timestamps without real words, or
# stops publishing the source provider / model / sha256.

def test_truthful_transcript_timing_source_refuses_to_lie_without_words():
    """The truthful helper MUST downgrade to ``sentence_timestamps`` when the
    underlying ASR only returned sentence-level segments, even if the
    caller asks for ``word_timestamps``."""
    segments = [{"text": "城市服务团队", "start": 0.0, "end": 3.0}]
    assert workflow_module._truthful_transcript_timing_source(segments) == "sentence_timestamps"
    with_words = [
        {"text": "城市服务团队", "start": 0.0, "end": 3.0,
         "words": [{"word": "城市", "start": 0.0, "end": 1.5}]}
    ]
    assert workflow_module._truthful_transcript_timing_source(with_words) == "word_timestamps"


def test_transcript_source_identity_publishes_provider_model_and_hash():
    identity = workflow_module._transcript_source_identity(
        [{"text": "abc", "start": 0.0, "end": 1.0}],
        provider="aliyun",
        model="fun-asr",
        source_media_sha256="DEADBEEF" * 8,
    )
    assert identity["provider"] == "aliyun"
    assert identity["model"] == "fun-asr"
    # P0-收口 2026-08-31: unified key name.
    assert identity["source_media_sha256"] == "deadbeef" * 8
    # No words in the segments, so truthful source MUST be sentence-only.
    assert identity["transcript_timing_source"] == "sentence_timestamps"
    assert identity["word_timestamps_available"] is False
    assert identity["estimated_phrase_timestamps"] is True
    assert isinstance(identity["transcript_sha256"], str)
    assert len(identity["transcript_sha256"]) == 64


def test_transcript_source_identity_gate_rejects_lying_word_timestamps():
    identity = {
        "provider": "aliyun",
        "model": "fun-asr",
        "source_media_sha256": "a" * 64,
        "transcript_sha256": "b" * 64,
        "transcript_timing_source": "word_timestamps",  # lies
        "word_timestamps_available": False,  # no real words
    }
    result = workflow_module._transcript_source_identity_gate(identity)
    assert result["passed"] is True, result  # identity fields are present

    truthful = workflow_module._timing_source_truthful_gate(identity)
    assert truthful["passed"] is False
    assert truthful["reason"] == "declared_word_timestamps_but_no_words"


def test_transcript_source_identity_gate_fails_when_provider_or_hash_missing():
    base = {
        "transcript_timing_source": "sentence_timestamps",
        "word_timestamps_available": False,
    }
    result = workflow_module._transcript_source_identity_gate(base)
    assert result["passed"] is False
    assert "provider_missing" in result["failures"]
    assert "model_missing" in result["failures"]
    # P0-收口 2026-08-31: unified key name in failures list.
    assert "source_media_sha256_missing" in result["failures"]
    assert "transcript_sha256_missing" in result["failures"]


def test_jieba_estimate_phrase_cues_never_escapes_segment_window():
    """jieba phrase estimation must keep every cue inside [start, end] and
    must mark each cue with ``estimated_phrase_timestamps: true`` so
    downstream gates can tell the synthetic words apart from real ones."""
    segments = [
        {"text": "课程介绍了一种清晰的学习方法", "start": 0.0, "end": 5.0},
    ]
    cues = workflow_module._estimate_phrase_cues_from_sentence_level(segments)
    assert cues, "jieba must produce at least one cue"
    for cue in cues:
        assert cue["start"] >= 0.0
        assert cue["end"] <= 5.0
        assert cue["estimated_phrase_timestamps"] is True
        assert cue["word_clock_mapping"] == "jieba_estimated_phrase_split"
        assert cue["source_segment_index"] == 0
    # No cue may be longer than the original sentence.
    assert max(c["end"] - c["start"] for c in cues) <= 5.0


def test_estimate_phrase_cues_handles_empty_or_malformed_segments():
    """jieba phrase estimation must never crash on empty / malformed input."""
    assert workflow_module._estimate_phrase_cues_from_sentence_level([]) == []
    malformed = [
        {"text": "", "start": 0.0, "end": 1.0},
        {"text": "x", "start": 2.0, "end": 1.0},  # end < start
        "not a mapping",
    ]
    cues = workflow_module._estimate_phrase_cues_from_sentence_level(malformed)
    # Only well-formed segments produce cues; we only assert no crash and
    # that any returned cue stays within its source window.
    for cue in cues:
        assert cue["end"] > cue["start"]


# P0-收口 2026-08-31: adaptive subtitle style baseline (C) + emphasis
# color classification.  The tests are renderer-agnostic — they pin the
# gate and the color function so any future drift in the stable ASS
# renderer will surface here.

def test_emphasis_color_for_classifies_by_intent():
    """Numbers get warm yellow, methods teal, conflicts warm red, default white."""
    color_number = workflow_module._emphasis_color_for("80%的顾客加了私域")
    assert color_number == workflow_module.SUBTITLE_EMPHASIS_COLOR_NUMBER
    color_method = workflow_module._emphasis_color_for("通过门店小程序下单")
    assert color_method == workflow_module.SUBTITLE_EMPHASIS_COLOR_METHOD
    color_conflict = workflow_module._emphasis_color_for("充一百送十块早就过时了")
    assert color_conflict == workflow_module.SUBTITLE_EMPHASIS_COLOR_CONFLICT
    color_default = workflow_module._emphasis_color_for("街上有家普通店铺")
    assert color_default == workflow_module.SUBTITLE_EMPHASIS_COLOR_DEFAULT
    # Number precedence over method (e.g. "49元就能办" must be yellow).
    color_num_method = workflow_module._emphasis_color_for("49元就能办尊贵会员")
    assert color_num_method == workflow_module.SUBTITLE_EMPHASIS_COLOR_NUMBER


def test_subtitle_style_baseline_gate_fails_when_scale_exceeds_110_percent():
    """The adaptive contract is 105-110%; the historical 1.05-1.12 fingerprint
    MUST fail the gate so the customer is told the renderer is out of contract."""
    preview = {
        "subtitle_style_id": "adaptive_talking_head_v1",
        "style_fingerprint": {
            "entry_motion": "fade_in_120ms",
            "emphasis_scale_range": [1.05, 1.12],  # historical drift
        },
        "cues": [],
    }
    result = workflow_module._subtitle_style_baseline_gate(preview)
    assert result["passed"] is False
    assert "emphasis_scale_out_of_baseline_range" in result["failures"]
    assert result["entry_motion_ms"] == 120
    assert result["preview_unified_style"] is True


def test_subtitle_style_baseline_gate_passes_on_baseline_compliant_preview():
    preview = {
        "subtitle_style_id": "adaptive_talking_head_v1",
        "style_fingerprint": {
            "entry_motion": "fade_in_120ms",
            "emphasis_scale_range": [1.05, 1.10],
        },
        "cues": [],
    }
    result = workflow_module._subtitle_style_baseline_gate(preview)
    assert result["passed"] is True
    assert result["failures"] == []


def test_subtitle_style_baseline_gate_accepts_current_white_base_renderer():
    preview = {
        "subtitle_style_id": "adaptive_white_base",
        "style_fingerprint": {
            "entry_motion": "fade_in_120ms",
            "emphasis_scale_range": [1.05, 1.10],
        },
        "cues": [],
    }
    result = workflow_module._subtitle_style_baseline_gate(preview)
    assert result["passed"] is True
    assert result["preview_unified_style"] is True


def test_subtitle_style_gate_allows_versioned_kinetic_v2_scale_range():
    preview = {
        "subtitle_style_id": "adaptive_talking_head_v1",
        "style_fingerprint": {
            "entry_motion": "fade_in_140ms",
            "word_motion": "douyin_kinetic_v2",
            "emphasis_scale_range": [1.05, 1.22],
        },
        "cues": [],
    }
    result = workflow_module._subtitle_style_baseline_gate(preview)
    assert result["passed"] is True
    assert result["failures"] == []


def test_subtitle_style_baseline_gate_rejects_too_many_emphasis_cues():
    preview = {
        "subtitle_style_id": "adaptive_talking_head_v1",
        "style_fingerprint": {
            "entry_motion": "fade_in_120ms",
            "emphasis_scale_range": [1.05, 1.10],
        },
        "cues": [
            {"emphasis_style": {"color": "#FFD166"}},
            {"emphasis_style": {"color": "#FFD166"}},
            {"emphasis_style": {"color": "#FFD166"}},
            {"emphasis_style": {"color": "#FFD166"}},
        ],
    }
    result = workflow_module._subtitle_style_baseline_gate(preview)
    assert result["passed"] is False
    assert "too_many_emphasis_cues" in result["failures"]


# P0-收口 2026-08-31: relevance gate (D §7) and cost-confirmation gate (D §8).
# The gates must be data-only — no network — so they can run before any
# provider call.  They must refuse a match_score of 0 even when the
# provider's library has 50 results; filler is not a feature.

# P0-收口 2026-08-31: unified source identity + TRANSCRIPT_SOURCE_MISMATCH
# gate.  These tests fail the build if anyone reintroduces a field-name
# drift between the ASR result, subtitle manifest, director plan and
# the final MP4.

def test_transcript_source_identity_uses_unified_source_media_sha256_key():
    identity = workflow_module._transcript_source_identity(
        [{"text": "abc", "start": 0.0, "end": 1.0}],
        provider="aliyun",
        model="fun-asr",
        source_media_sha256="A" * 64,
    )
    # Unified key MUST be present.
    assert identity["source_media_sha256"] == "a" * 64
    # Legacy key MUST NOT be present (no silent dual-write that drifts).
    assert "media_sha256" not in identity


def test_transcript_source_identity_gate_accepts_legacy_key_for_backcompat():
    identity = {
        "provider": "aliyun",
        "model": "fun-asr",
        # Legacy "media_sha256" only — gate must still recognise it so
        # older callers / pre-P0 batches keep working.
        "media_sha256": "C" * 64,
        "transcript_sha256": "D" * 64,
        "transcript_timing_source": "sentence_timestamps",
        "word_timestamps_available": False,
    }
    result = workflow_module._transcript_source_identity_gate(identity)
    assert result["passed"] is True
    assert result["source_media_sha256"] == "c" * 64


def test_transcript_source_mismatch_gate_fails_with_explicit_label():
    identity = {
        "source_media_sha256": "a" * 64,
        "transcript_sha256": "b" * 64,
    }
    ok = workflow_module._transcript_source_mismatch_gate(
        identity, computed_source_sha256="a" * 64
    )
    assert ok["passed"] is True
    assert ok["failures"] == []

    bad = workflow_module._transcript_source_mismatch_gate(
        identity, computed_source_sha256="C" * 64
    )
    assert bad["passed"] is False
    # The label MUST be the exact string the release quality report
    # surfaces to the customer.
    assert "TRANSCRIPT_SOURCE_MISMATCH" in bad["failures"]


def test_visual_match_score_rejection_gate_blocks_zero_and_conflict_terms():
    zero = workflow_module._visual_match_score_rejection_gate(
        {"match_score": 0, "matched_concepts": ["customer", "restaurant"]}
    )
    assert zero["passed"] is False
    assert "match_score_missing_or_zero" in zero["failures"]

    conflict = workflow_module._visual_match_score_rejection_gate(
        {
            "match_score": 0.5,
            "matched_concepts": ["lawyer", "office", "customer meeting"],
        }
    )
    assert conflict["passed"] is False
    assert "conflict_terms_present" in conflict["failures"]
    assert "lawyer" in conflict["conflict_terms"]

    low = workflow_module._visual_match_score_rejection_gate(
        {"match_score": 0.05, "matched_concepts": ["restaurant"]}
    )
    assert low["passed"] is False
    assert "match_score_below_threshold" in low["failures"]

    good = workflow_module._visual_match_score_rejection_gate(
        {"match_score": 0.6, "matched_concepts": ["restaurant customer", "grill"]}
    )
    assert good["passed"] is True
    assert good["failures"] == []


def test_cost_confirmation_required_gate_blocks_unconfirmed_generation():
    # No generated assets required → confirmation is not required.
    not_needed = workflow_module._cost_confirmation_required_gate(
        {"cost_confirmed": False},
        expected_min_generated_assets=0,
    )
    assert not_needed["passed"] is True

    # 2 generated assets must be confirmed.
    not_confirmed = workflow_module._cost_confirmation_required_gate(
        {"cost_confirmed": False, "cost_quote": 0.10},
        expected_min_generated_assets=2,
    )
    assert not_confirmed["passed"] is False
    assert "cost_not_confirmed" in not_confirmed["failures"]

    confirmed = workflow_module._cost_confirmation_required_gate(
        {"cost_confirmed": True, "cost_quote": 0.10},
        expected_min_generated_assets=2,
    )
    assert confirmed["passed"] is True


def test_local_export_quality_report_aggregates_relevance_and_cost_gates():
    """The two new gates must block the report when a low-score candidate
    slips in or when generated assets were not confirmed by the user."""
    base_kwargs = dict(
        expected_width=720,
        expected_height=1280,
        expected_duration=10,
        source_has_audio=True,
        visual_beats=[{"start": 1, "end": 2}],
        source_media_identity={
            "provider": "aliyun",
            "model": "fun-asr",
            "media_sha256": "a" * 64,
            "transcript_sha256": "b" * 64,
            "transcript_timing_source": "sentence_timestamps",
            "word_timestamps_available": False,
        },
        subtitle_preview={
            "subtitle_style_id": "adaptive_talking_head_v1",
            "style_fingerprint": {
                "entry_motion": "fade_in_120ms",
                "emphasis_scale_range": [1.05, 1.10],
            },
            "cues": [],
        },
    )
    output_media = {
        "size_bytes": 100, "width": 720, "height": 1280,
        "duration_seconds": 10.1, "has_audio": True,
    }
    # Relevance: zero-score candidate.
    fail_relevance = VideoEditorWorkflowService._local_export_quality_report(
        output_media,
        visual_candidates=[{"match_score": 0, "matched_concepts": ["junk"]}],
        **base_kwargs,
    )
    assert fail_relevance["passed"] is False
    assert fail_relevance["checks"]["visual_relevance"] is False
    # Cost: 1 generated asset, no confirmation.
    fail_cost = VideoEditorWorkflowService._local_export_quality_report(
        output_media,
        visual_candidates=[{
            "match_score": 0.6,
            "matched_concepts": ["fruit shop"],
            "asset_origin": "generated_image_asset",
        }],
        billing_confirmation={"cost_confirmed": False, "cost_quote": 0.05},
        **base_kwargs,
    )
    assert fail_cost["passed"] is False
    assert fail_cost["checks"]["cost_confirmation"] is False
    # Happy path: confirmed cost, high-score real B-roll.
    ok = VideoEditorWorkflowService._local_export_quality_report(
        output_media,
        visual_candidates=[{
            "match_score": 0.7,
            "matched_concepts": ["restaurant customer", "grill"],
            "asset_origin": "stock_video_asset",
        }],
        billing_confirmation={"cost_confirmed": True, "cost_quote": 0.05},
        **base_kwargs,
    )
    assert ok["passed"] is True
    assert ok["checks"]["visual_relevance"] is True
    assert ok["checks"]["cost_confirmation"] is True


# P0-收口 2026-08-31 / P1-素材链路 hard rules.  These are the three
# release-blocking rules from the P1 specification:
#   1. match_score < 0.20 is rejected (no filler padding for coverage).
#   2. Conflict terms in matched_concepts reject the candidate.
#   3. Generated images require explicit cost confirmation before
#      they are charged to the customer.
# A regression in any of the three surfaces here as a build failure.

def test_visual_match_score_below_0_2_threshold_rejected():
    """Anything below the 0.20 match-score floor is filler padding.
    A quality report containing such a candidate MUST fail so the
    release does not claim fake coverage.
    """
    result = workflow_module._visual_match_score_rejection_gate(
        {"match_score": 0.19, "matched_concepts": ["restaurant", "customer"]}
    )
    assert result["passed"] is False
    assert "match_score_below_threshold" in result["failures"]


def test_visual_match_score_conflict_term_drops_candidate():
    """Conflict terms override any positive match score.  A "lawyer"
    match for a "restaurant" query is not evidence of a loyalty programme.
    """
    result = workflow_module._visual_match_score_rejection_gate(
        {
            "match_score": 0.8,
            "matched_concepts": ["lawyer", "office", "client meeting"],
        }
    )
    assert result["passed"] is False
    assert "conflict_terms_present" in result["failures"]
    assert "lawyer" in result["conflict_terms"]


def test_cost_confirmation_required_gate_does_not_charge_unconfirmed_batch():
    """A generated-image batch without explicit user confirmation must
    NOT clear the gate, regardless of how cheap the quote is."""
    batch_size = 4
    result = workflow_module._cost_confirmation_required_gate(
        {
            "cost_confirmed": False,
            "cost_quote": 0.20,  # 4 images at 0.05 each
            "confirmed_at": None,
        },
        expected_min_generated_assets=batch_size,
    )
    assert result["passed"] is False
    assert "cost_not_confirmed" in result["failures"]
    # The gate must report the exact batch size the customer would have
    # been charged for, so the page can show the figure before
    # confirmation.
    assert result["expected_min_generated_assets"] == batch_size


def test_source_caption_overlay_keeps_generated_cues_only_for_full_broll():
    preview = {
        "cues": [
            {"start": 0.0, "end": 2.0, "lines": ["人物原画面"]},
            {"start": 4.0, "end": 6.0, "lines": ["全屏素材"]},
            {"start": 8.0, "end": 10.0, "lines": ["画中画"]},
        ],
        "subtitle_style_id": "adaptive_talking_head_v1",
    }
    result = VideoEditorWorkflowService._source_caption_overlay_preview(
        preview,
        source_caption_mode=workflow_module._SOURCE_CAPTION_MODE_PRESERVE,
        brolls=[
            {"start": 4.0, "end": 6.0, "mode": "full"},
            {"start": 8.0, "end": 10.0, "mode": "pip"},
        ],
        playback_rate=1.0,
    )

    assert [cue["lines"][0] for cue in result["cues"]] == ["全屏素材"]
    assert result["generated_caption_scope"] == "full_broll_only"
    assert result["generated_caption_intervals"] == [{"start": 4.0, "end": 6.0}]


def test_source_caption_overlay_replaces_baked_captions_for_full_timeline():
    preview = {
        "cues": [
            {"start": 0.0, "end": 2.0, "lines": ["开场强调"]},
            {"start": 4.0, "end": 6.0, "lines": ["重点结论"]},
        ],
        "subtitle_style_id": "adaptive_talking_head_v1",
    }

    result = VideoEditorWorkflowService._source_caption_overlay_preview(
        preview,
        source_caption_mode=workflow_module._SOURCE_CAPTION_MODE_REPLACE,
        brolls=[],
        playback_rate=1.0,
    )

    assert [cue["lines"][0] for cue in result["cues"]] == ["开场强调", "重点结论"]
    assert result["source_caption_mode"] == workflow_module._SOURCE_CAPTION_MODE_REPLACE
    assert result["generated_caption_scope"] == "full_timeline"
    assert result["generated_caption_intervals"] == [
        {"start": 0.0, "end": 2.0},
        {"start": 4.0, "end": 6.0},
    ]


def test_local_rhythm_filter_scrubs_detected_source_caption_band_before_ass():
    rendered = VideoEditorWorkflowService._local_rhythm_video_filter(
        duration_seconds=8.0,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.0,
        subtitle_filter="approved.ass",
        subtitle_force_style="MarginV=390",
        source_width=720,
        source_height=1280,
        scrub_source_captions=True,
        source_caption_detection={
            "band_center_ratio": 0.82,
            "band_height_ratio": 0.04,
        },
    )

    assert "[caption_source_band]crop=iw:" in rendered
    assert "boxblur=24:3[caption_blurred_band]" in rendered
    assert "[caption_clean_base][caption_blurred_band]overlay=0:" in rendered
    assert "[caption_scrubbed]subtitles='approved.ass':force_style='MarginV=390'" in rendered
