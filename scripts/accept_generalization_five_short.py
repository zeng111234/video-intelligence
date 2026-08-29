"""Run five short, unseen local samples through the adaptive release route.

This is a bounded acceptance helper. It uses local media and the existing
VideoEditorWorkflowService; it does not publish, upload to a cloud provider,
or start a paid job. The source list is evidence input, not production logic.
"""

from __future__ import annotations

import hashlib
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


BASE = ROOT / "work/auto-fine-cut-generalization-20260825/5-samples-20250825"
MODEL = Path(
    os.getenv("WHISPER_MODEL_PATH", "large-v3-turbo")
).expanduser()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_case(service: VideoEditorWorkflowService, case_dir: Path) -> dict:
    source_path = case_dir / "source.mp4"
    asr_path = case_dir / "asr.json"
    asr = json.loads(asr_path.read_text(encoding="utf-8"))
    segments = [dict(segment) for segment in asr.get("segments") or []]
    if not segments:
        raise RuntimeError(f"no ASR segments: {case_dir}")
    now = datetime.now().astimezone()
    upload = service.upload_source(
        file_name=source_path.name,
        media_type="video/mp4",
        media_bytes=source_path.read_bytes(),
        rights_confirmed=True,
        rights_holder="local_unseen_short_acceptance",
    )
    source_id = str(upload["source_id"])
    source_identity = {
        "source_media_sha256": sha256(source_path),
        "transcript_sha256": sha256(asr_path),
        "transcript_timing_source": "word_timestamps",
        "transcript_path": str(asr_path.resolve()),
    }
    item = VideoEditorBatchItem(
        source_id=source_id,
        title=str(segments[0].get("text") or "未见口播短样"),
        selected_title=str(segments[0].get("text") or "未见口播短样"),
        status="awaiting_subtitle_review",
        subtitle_segments=segments,
        review_snapshot={
            "confirmed": True,
            "approval_mode": "local_unseen_short_word_timestamps",
            "source_range": {"start": 0.0, "end": float(asr.get("duration_seconds") or 20.0)},
            "source_media_identity": source_identity,
        },
        review_confirmed_at=now,
        enabled_plan_step_ids=["smart_opening", "vertical_fit", "subtitles"],
        edit_plan={"remove_ranges": []},
        updated_at=now,
    )
    batch = VideoEditorBatch(
        target_platform="douyin",
        subtitle_enabled=True,
        subtitle_model="local-unseen-word-timestamps",
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
    repo = get_repository()
    repo.save_video_editor_batch(batch)
    payload = service.create_release_template_local_export(batch.batch_id, item.item_id)
    payload_path = case_dir / "production-result.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    completed = payload["items"][0]
    return {
        "case_id": case_dir.name,
        "route": "create_release_template_local_export",
        "source_path": str(source_path.resolve()),
        "source_sha256": sha256(source_path),
        "asr_path": str(asr_path.resolve()),
        "batch_id": batch.batch_id,
        "item_id": item.item_id,
        "source_id": source_id,
        "status": completed.get("status"),
        "task_id": completed.get("edit_task_id"),
        "result_media_url": completed.get("result_media_url"),
        "result_path": completed.get("result_path"),
        "production_result": str(payload_path.resolve()),
        "model_path": str(MODEL),
    }


def main() -> None:
    default_cases = [
        "commercial-dashu",
        "business-dashu",
        "story-female",
        "tutorial-original",
        "abstract-dashu-clip",
    ]
    cases = sys.argv[1:] or default_cases
    service = VideoEditorWorkflowService(
        get_repository(), get_video_editing_service(), None, None
    )
    results = []
    for name in cases:
        case_dir = BASE / name
        print(f"START {name}", flush=True)
        try:
            results.append(run_case(service, case_dir))
            print(f"DONE {name}", flush=True)
        except Exception as exc:  # preserve the failure as evidence and continue
            results.append({"case_id": name, "status": "failed", "error": str(exc)})
            print(f"FAIL {name}: {exc}", flush=True)
    index = BASE / "five-sample-run-index.json"
    index.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"index": str(index.resolve()), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
