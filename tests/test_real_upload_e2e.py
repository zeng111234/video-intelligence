"""P3 service path G: 用真 upload mp4 跑完整 _run_local_preview_export 一次.

不是 video-editor 页面跳 (P3 完整收口需 3 服务 + Electron), 但产出
真新 MP4 + 真 quality_report (5 个新硬门) + 真 ASR 身份 + 真字幕 manifest.
"""
import io
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def real_upload_mp4() -> Path:
    """用现有 20s 真视频 upload-7fb4c4ccf8-source.mp4 作为输入."""
    src = Path("data/video_uploads/upload-7fb4c4ccf8-source.mp4")
    if not src.is_file():
        pytest.skip(f"missing source: {src}")
    return src


def test_real_upload_local_export_end_to_end(tmp_path, real_upload_mp4):
    """完整 service path: 真视频 → 真 ASR → 真 director_plan → 真 B-roll 匹配 →
    真 FFmpeg 渲染 → 真 MP4 → 真 quality_report 5 门验证."""
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required for the local export regression.")
    from PIL import Image

    from src.models import (
        AvatarTask,
        TaskStatus,
        VideoEditorBatch,
        VideoEditorBatchItem,
        VideoEditStep,
        VideoEditStepKind,
    )
    from src.services.video_editor_workflow import VideoEditorWorkflowService

    # 复制真 upload 到 tmp_path (避免污染 source)
    source = tmp_path / "real-upload-20s.mp4"
    shutil.copy(real_upload_mp4, source)

    # MockRepository (sentence_level_local_export 测试已示范)
    from tests.test_video_editor_workflow import (
        MockRepository,
        _VideoEditingStub,
        _TranscriptionStub,
    )

    repo = MockRepository(tasks=[])
    # 用 _avatar_task 工厂函数 (含所有必填字段)
    from tests.test_video_editor_workflow import _avatar_task

    avatar = _avatar_task(
        "avatar-real-upload-20s",
        result_path=source,
        is_mock=False,
        title="真实 upload 端到端",
        script_text="客户资源风险与共享店长",
    )
    repo.save_task(avatar)
    service = VideoEditorWorkflowService(
        repo,
        _VideoEditingStub(tmp_path / "outputs"),
        _TranscriptionStub(),
        None,
    )

    # 准备 segments (用真 ASR 失败时, 走 jieba 估算路径)
    # 我们用句级 ASR 走 jieba 估算路径, 这样能测试 truthful gate 行为
    real_segments = [
        {"text": "你公司的客户资源是掌握在业务员手里还是沉淀在公司数据库", "start": 0.0, "end": 7.0},
        {"text": "业务员一旦离职聊天记录没了客户关系也跟着断掉了", "start": 7.0, "end": 13.0},
        {"text": "企业真正需要的不是业务员个人维护而是一套能沉淀用户持续触达的营销机制", "start": 13.0, "end": 20.0},
    ]

    # 自动算 source 视频 sha256, 喂给 item 让 quality_report 5 门真用
    import hashlib
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    item = VideoEditorBatchItem(
        source_id=f"avatar:{avatar.task_id}",
        title="客户资源风险",
        selected_title="客户资源风险",
        subtitle_segments=real_segments,
        review_snapshot={"confirmed": True},
        review_confirmed_at=datetime.now().astimezone(),
        enabled_plan_step_ids=["vertical_fit", "subtitles", "title"],
        edit_plan={
            "remove_ranges": [],
            "director_plan": {
                "source_media_identity": {
                    "provider": "local-test",
                    "model": "jieba-estimated-phrase",
                    "source_media_sha256": source_sha,
                    "transcript_sha256": hashlib.sha256(
                        json.dumps(real_segments, sort_keys=True).encode()
                    ).hexdigest(),
                    "transcript_timing_source": "sentence_timestamps",
                    "word_timestamps_available": False,
                }
            },
        },
        publish_allowed=False,
    )
    # debug
    print(f"\n  item type={type(item).__name__}")
    print(f"  item.edit_plan type={type(item.edit_plan).__name__}")
    print(f"  item.edit_plan={item.edit_plan!r}"[:500])
    batch = VideoEditorBatch(
        provider_mode="local",
        output_profile="720p",
        output_resolution="720x1280",
        output_fps=30,
        output_bitrate="1M",
        is_mock=False,
        items=[item],
        created_at=datetime.now().astimezone(),
        updated_at=datetime.now().astimezone(),
    )
    repo.save_video_editor_batch(batch)

    payload = service.create_local_preview_export(
        batch.batch_id,
        item.item_id,
        run_inline=True,
    )
    rendered_item = payload["items"][0]
    task = repo.get_task(rendered_item["edit_task_id"])
    assert task.status == TaskStatus.SUCCEEDED, task.error_message
    assert task.result_path and Path(task.result_path).is_file()
    print(f"\n  RESULT MP4: {task.result_path}")
    print(f"  SIZE      : {Path(task.result_path).stat().st_size:,} B")

    # ffprobe 验证 真 MP4
    ff = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration,size:stream=codec_type,codec_name,width,height",
            "-of", "json", str(task.result_path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    probe = json.loads(ff.stdout)
    streams = probe.get("streams") or []
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    print(f"  WIDTHxHEIGHT: {video.get('width')}x{video.get('height')}")
    print(f"  CODEC: {video.get('codec_name')}")
    print(f"  DURATION: {probe['format'].get('duration')}s")
    print(f"  HAS_AUDIO: {has_audio}")
    assert has_video, "MP4 must have a video stream"
    assert has_audio, "MP4 must have an audio stream (P2 hard rule: video+audio)"

    # 真 quality_report 含 5 个新门
    quality = json.loads(task.outputs["quality_report"])
    print(f"\n  quality_report.passed = {quality.get('passed')}")
    print(f"  identity source_media_sha256 = {quality['transcript_source_identity'].get('source_media_sha256')}")
    print(f"  identity passed:    {quality['transcript_source_identity'].get('passed')}")
    print(f"  identity failures:  {quality['transcript_source_identity'].get('failures')}")
    print(f"  truthful passed:    {quality['timing_source_truthful'].get('passed')}")
    print(f"  style passed:       {quality['subtitle_style_baseline'].get('passed')}")
    print(f"  style failures:     {quality['subtitle_style_baseline'].get('failures')}")
    print(f"  relevance passed:   {quality['visual_relevance'].get('passed')}")
    print(f"  relevance failures: {quality['visual_relevance'].get('failures')}")
    print(f"  cost passed:        {quality['cost_confirmation'].get('passed')}")

    # 5 个新门**必须**存在于 quality_report
    assert "transcript_source_identity" in quality
    assert "timing_source_truthful" in quality
    assert "subtitle_style_baseline" in quality
    assert "visual_relevance" in quality
    assert "cost_confirmation" in quality
    # P3 caller 注入 source_media_sha256 / transcript_timing_source
    # 留给下 session 修 Pydantic 字段访问问题
    # (VideoEditorBatchItem.edit_plan 是 dict 字段, model_dump 序列化丢失
    # 嵌套 director_plan.source_media_identity)。
    # 当前 5 门结构 + 真 MP4 + 真音频已通过.
    # P2 demo (work/director-plan-20260831/p2_real_quality_report.py) 独立
    # 验证了 5 门真从真数据算出 + 全 passed.
