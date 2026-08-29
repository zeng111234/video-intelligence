"""Bounded smoke for the adaptive semantic stock-search path.

This deliberately runs one clean local product clip only.  It records the
structured visual requests and provider search log without printing keys or
starting any cloud task.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
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


BASE = ROOT / "work/auto-fine-cut-generalization-20260825/dynamic-search-smoke-20250825"
SOURCE = ROOT / "work/auto-fine-cut-generalization-20260825/5-samples-20250825/commercial-number-wechat/source.mp4"
ASR = ROOT / "work/auto-fine-cut-generalization-20260825/5-samples-20250825/commercial-number-wechat/asr.json"
SMOKE_SECONDS = 14.5


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_inputs() -> tuple[Path, Path]:
    BASE.mkdir(parents=True, exist_ok=True)
    clip = BASE / "smoke-source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(SOURCE),
            "-t", str(SMOKE_SECONDS), "-c", "copy", str(clip),
        ],
        check=True,
    )
    raw = json.loads(ASR.read_text(encoding="utf-8"))
    segments = []
    for segment in raw.get("segments") or []:
        start = float(segment.get("start") or 0)
        end = min(float(segment.get("end") or 0), SMOKE_SECONDS)
        if end <= start:
            continue
        item = dict(segment)
        item["end"] = round(end, 3)
        words = []
        for word in segment.get("words") or []:
            word_start = float(word.get("start") or 0)
            word_end = min(float(word.get("end") or 0), SMOKE_SECONDS)
            if word_end > word_start and word_start < SMOKE_SECONDS:
                words.append({**dict(word), "end": round(word_end, 3)})
        item["words"] = words
        segments.append(item)
    asr = BASE / "smoke-asr.json"
    asr.write_text(
        json.dumps({"duration_seconds": SMOKE_SECONDS, "segments": segments}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return clip, asr


def main() -> None:
    source_path, asr_path = prepare_inputs()
    asr = json.loads(asr_path.read_text(encoding="utf-8"))
    segments = [dict(segment) for segment in asr.get("segments") or []]
    now = datetime.now().astimezone()
    repo = get_repository()
    service = VideoEditorWorkflowService(repo, get_video_editing_service(), None, None)
    upload = service.upload_source(
        file_name=source_path.name,
        media_type="video/mp4",
        media_bytes=source_path.read_bytes(),
        rights_confirmed=True,
        rights_holder="local_dynamic_search_smoke",
    )
    source_identity = {
        "source_media_sha256": sha256(source_path),
        "transcript_sha256": sha256(asr_path),
        "transcript_timing_source": "word_timestamps",
        "transcript_path": str(asr_path.resolve()),
    }
    item = VideoEditorBatchItem(
        source_id=str(upload["source_id"]),
        title="车载支架产品演示冒烟",
        selected_title="车载支架产品演示冒烟",
        status="awaiting_subtitle_review",
        subtitle_segments=segments,
        review_snapshot={
            "confirmed": True,
            "approval_mode": "local_dynamic_search_smoke_word_timestamps",
            "source_range": {"start": 0.0, "end": SMOKE_SECONDS},
            "source_media_identity": source_identity,
        },
        review_confirmed_at=now,
        enabled_plan_step_ids=["smart_opening", "vertical_fit", "subtitles"],
        edit_plan={"remove_ranges": []},
        provider_payload={"requested_pipeline": "adaptive_fine_cut_v1"},
        updated_at=now,
    )
    batch = VideoEditorBatch(
        target_platform="douyin",
        subtitle_enabled=True,
        subtitle_model="local-dynamic-search-smoke-word-timestamps",
        bgm_enabled=False,
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
    repo.save_video_editor_batch(batch)
    payload = service.create_release_template_local_export(batch.batch_id, item.item_id)
    (BASE / "production-result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    completed = payload["items"][0]
    task_id = completed.get("edit_task_id")
    task = repo.get_task(task_id) if task_id else None
    outputs = dict(task.outputs or {}) if task else {}
    quality = json.loads(outputs.get("quality_report") or "{}")
    network_log = BASE / "provider-search-log-network-attempt.json"
    current_log = BASE / "provider-search-log.json"
    if current_log.is_file() and not network_log.is_file():
        network_log.write_text(current_log.read_text(encoding="utf-8"), encoding="utf-8")
    for key, filename in (
        ("edit_plan_json", "timeline.json"),
        ("shot_plan_json", "director-plan.json"),
        ("brolls_json", "output-added-visual.json"),
        ("source_media_identity_json", "source-identity.json"),
    ):
        value = outputs.get(key)
        if value:
            (BASE / filename).write_text(
                json.dumps(json.loads(value), ensure_ascii=False, indent=2), encoding="utf-8"
            )
    (BASE / "quality-report.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (BASE / "provider-search-log.json").write_text(
        json.dumps(quality.get("provider_search_log") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result_path = Path(str(task.result_path or "")) if task else Path()
    if result_path.is_file():
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(result_path)],
            check=False, stdout=(BASE / "ffprobe.json").open("w", encoding="utf-8"),
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(result_path), "-vf", "fps=1/2,scale=240:-1,tile=4x2:padding=4:margin=4", "-frames:v", "1", str(BASE / "contact-sheet.jpg")],
            check=False,
        )
    summary = {
        "task_id": task_id,
        "status": str(task.status) if task else "missing",
        "result_path": str(result_path.resolve()) if result_path.is_file() else None,
        "visual_request_count": len(quality.get("visual_requests") or []),
        "provider_log_count": len(quality.get("provider_search_log") or []),
        "provider_attempted_count": sum(bool(item.get("provider_attempted")) for item in quality.get("provider_search_log") or []),
        "output_added_visual": quality.get("output_added_visual"),
        "visual_release_passed": quality.get("visual_release_passed"),
        "subtitle_sync_passed": quality.get("subtitle_sync_passed"),
        "evidence_dir": str(BASE.resolve()),
    }
    (BASE / "smoke-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
