"""Run the full Guangzhou sample through the production local renderer.

This helper only seeds the already reviewed local transcript into the same
VideoEditorWorkflowService used by the page. It never starts ASR, a provider,
upload, payment, or publishing.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from project.backend.app.core.deps import (  # noqa: E402
    get_repository,
    get_video_editing_service,
)
from src.models import VideoEditorBatch, VideoEditorBatchItem  # noqa: E402
from src.services.video_editor_workflow import (  # noqa: E402
    VideoEditorWorkflowService,
)


ASR = ROOT / "work/auto-fine-cut-adaptive-20260824-short-real/asr-full.json"
EVIDENCE = Path(
    os.getenv("VIDEO_EDITOR_ACCEPTANCE_EVIDENCE_DIR", "").strip()
    or (ROOT / "work/auto-fine-cut-generalization-20250826-v5-rerun/long-avatar-r2")
)
SOURCE_ID = "upload:upload-a88ae01012"
BGM_ID = "bgm-0eed3a5909e5"


def main() -> None:
    # Keep the historical offline default, while allowing an explicit caller
    # opt-in to the existing free stock adapters for acceptance runs.
    os.environ.setdefault("VIDEO_EDITOR_LOCAL_ACCEPTANCE_NO_PROVIDER", "1")
    asr = json.loads(ASR.read_text(encoding="utf-8"))
    segments = [dict(item) for item in asr.get("segments") or []]
    now = datetime.now().astimezone()
    item = VideoEditorBatchItem(
        source_id=SOURCE_ID,
        title="广州烧烤店的回头客模式",
        selected_title="广州烧烤店的回头客模式",
        status="awaiting_subtitle_review",
        subtitle_segments=segments,
        review_snapshot={
            "confirmed": True,
            "approval_mode": "local_reviewed_transcript",
            "source_range": {"start": 0.0, "end": 104.72},
            "source_media_identity": {
                "timing_source": "word_timestamps",
                "transcript_path": str(ASR.resolve()),
            },
        },
        review_confirmed_at=now,
        enabled_plan_step_ids=["vertical_fit", "subtitles", "title", "bgm"],
        edit_plan={"remove_ranges": []},
        selected_bgm_id=BGM_ID,
        updated_at=now,
    )
    batch = VideoEditorBatch(
        target_platform="douyin",
        subtitle_enabled=True,
        subtitle_model="local-reviewed-word-timestamps",
        bgm_enabled=True,
        bgm_id=BGM_ID,
        output_format="mp4",
        output_resolution="720x1280",
        output_fps=30,
        output_bitrate="4M",
        provider_mode="local",
        output_profile="720p",
        is_mock=False,
        items=[item],
        created_at=now,
        updated_at=now,
    )
    repository = get_repository()
    repository.save_video_editor_batch(batch)
    service = VideoEditorWorkflowService(
        repository,
        get_video_editing_service(),
        None,
        None,
    )
    payload = service.create_release_template_local_export(
        batch.batch_id,
        item.item_id,
        local_bgm_id=BGM_ID,
    )
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "production-local-run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    completed = payload["items"][0]
    print(
        json.dumps(
            {
                "batch_id": batch.batch_id,
                "item_id": item.item_id,
                "status": completed.get("status"),
                "edit_task_id": completed.get("edit_task_id"),
                "result_media_url": completed.get("result_media_url"),
                "evidence": str(EVIDENCE.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
