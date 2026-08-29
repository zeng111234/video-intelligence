"""Local acceptance rerun with cached reviewed transcript plus one local gap pass.

This is an evidence helper, not a production rule.  It only combines already
cached transcript artifacts and a locally generated 5.4-second ASR artifact;
it never starts a provider job, uploads media, or edits the source file.
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


SOURCE_ID = "upload:upload-a88ae01012"
BASE_TRANSCRIPT = ROOT / "work/auto-fine-cut-adaptive-20260824-full/transcription.json"
WORD_TRANSCRIPT = ROOT / "work/auto-fine-cut-adaptive-20260824-short-real/asr-full.json"
GAP_TRANSCRIPT = ROOT / "work/auto-fine-cut-adaptive-20260825-full-r7/gap-asr.json"
EVIDENCE = ROOT / "work/auto-fine-cut-adaptive-20260825-full-r7"
BGM_ID = "bgm-0eed3a5909e5"
GAP_OFFSET_SECONDS = 80.5


def _words(payload: dict, *, offset: float = 0.0) -> list[dict]:
    result: list[dict] = []
    for segment in payload.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        for word in segment.get("words") or []:
            if not isinstance(word, dict):
                continue
            try:
                start = float(word.get("start") or 0) + offset
                end = float(word.get("end") or 0) + offset
            except (TypeError, ValueError):
                continue
            text = str(word.get("word") or word.get("text") or "").strip()
            if text and end > start:
                result.append({"start": round(start, 3), "end": round(end, 3), "word": text})
    return result


def build_reviewed_segments() -> list[dict]:
    base = json.loads(BASE_TRANSCRIPT.read_text(encoding="utf-8"))
    word_payload = json.loads(WORD_TRANSCRIPT.read_text(encoding="utf-8"))
    gap_payload = json.loads(GAP_TRANSCRIPT.read_text(encoding="utf-8"))
    raw_words = _words(word_payload) + _words(gap_payload, offset=GAP_OFFSET_SECONDS)
    raw_words.sort(key=lambda item: (item["start"], item["end"]))
    deduped: list[dict] = []
    for word in raw_words:
        if deduped and float(word["start"]) < float(deduped[-1]["end"]) - 0.005:
            # The local gap pass and the older full-file pass overlap around
            # the boundary.  Keep the first non-overlapping word clock.
            continue
        deduped.append(word)

    segments: list[dict] = []
    for segment in base.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or 0)
        text = str(segment.get("text") or "").strip()
        if not text or end <= start:
            continue
        attached_words: list[dict] = []
        for word in deduped:
            word_start = max(start, float(word["start"]))
            word_end = min(end, float(word["end"]))
            if word_end <= word_start:
                continue
            attached_words.append(
                {**dict(word), "start": round(word_start, 3), "end": round(word_end, 3)}
            )
        item = {
            "start": start,
            "end": end,
            "text": text,
            "reviewed_text": text,
            "quality_status": "accepted_cached_transcript_plus_local_gap_review",
        }
        if attached_words:
            item["words"] = attached_words
        segments.append(item)
    return segments


def main() -> None:
    os.environ["VIDEO_EDITOR_LOCAL_ACCEPTANCE_NO_PROVIDER"] = "1"
    segments = build_reviewed_segments()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "merged-reviewed-transcript.json").write_text(
        json.dumps(
            {
                "source": "cached_aliyun_sentence_transcript_plus_local_gap_whisper",
                "gap_range": {"start": 80.5, "end": 85.9},
                "segments": segments,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    now = datetime.now().astimezone()
    item = VideoEditorBatchItem(
        source_id=SOURCE_ID,
        title="本地审核口播",
        selected_title="本地审核口播",
        status="awaiting_subtitle_review",
        subtitle_segments=segments,
        review_snapshot={
            "confirmed": True,
            "approval_mode": "cached_transcript_plus_local_gap_review",
            "source_range": {"start": 0.0, "end": 104.72},
            "source_media_identity": {
                "timing_source": "word_timestamps_with_sentence_fallback_for_reviewed_gap",
                "transcript_path": str((EVIDENCE / "merged-reviewed-transcript.json").resolve()),
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
        subtitle_model="cached-reviewed-word-timestamps-plus-local-gap",
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
        repository, get_video_editing_service(), None, None
    )
    payload = service.create_release_template_local_export(
        batch.batch_id, item.item_id, local_bgm_id=BGM_ID
    )
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
