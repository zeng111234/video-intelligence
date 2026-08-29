"""Run the single, explicitly approved IMS Timeline short-sample acceptance.

This script reads local configuration without printing secrets, uploads only the
three required inputs, submits one idempotent SubmitMediaProducingJob request,
polls it, and saves redacted evidence. It is intentionally not a product
fallback path and never invokes SubmitBatchMediaProducingJob.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.aliyun_ims_timeline import (  # noqa: E402
    AliyunIMSTimelineClient,
    compile_director_timeline_to_ims,
)
from src.adapters.video_editor_cloud import AliyunCloudObjectStore  # noqa: E402
from src.services.video_editor_cloud import CloudEditorConfiguration  # noqa: E402


EVIDENCE = ROOT / "work" / "ims-cloud-short-acceptance-20260826" / "retry-01"
SOURCE = ROOT / "data" / "video_uploads" / "upload-33f128b9dc-smoke-source.mp4"
BROLL = ROOT / "data" / "creative_assets" / "broll-eda83700b0.mp4"
BGM = ROOT / "data" / "bgm_library" / "bgm-a40b291dc5b1.mp3"
ASR = ROOT / "work" / "auto-fine-cut-generalization-20260825" / "dynamic-search-smoke-20250825" / "smoke-asr.json"
SOURCE_IDENTITY = ROOT / "work" / "auto-fine-cut-generalization-20260825" / "dynamic-search-smoke-20250825" / "source-identity.json"


def load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(name: str, value: Any) -> None:
    (EVIDENCE / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def plain_oss_url(config: CloudEditorConfiguration, object_key: str) -> str:
    return f"https://{config.oss_bucket}.{config.oss_location}.aliyuncs.com/{object_key}"


def main() -> int:
    load_dotenv()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for path in (SOURCE, BROLL, BGM, ASR, SOURCE_IDENTITY):
        if not path.is_file():
            write_json("failure.json", {"stage": "preflight", "reason": f"missing_local_file:{path.name}"})
            return 2

    config = CloudEditorConfiguration.from_env()
    if config.provider_mode.value != "aliyun" or config.missing_configuration:
        write_json(
            "failure.json",
            {
                "stage": "preflight",
                "reason": "aliyun_configuration_incomplete",
                "provider_mode": config.provider_mode.value,
                "missing_configuration": config.missing_configuration,
            },
        )
        return 2

    source_identity = json.loads(SOURCE_IDENTITY.read_text(encoding="utf-8"))
    asr = json.loads(ASR.read_text(encoding="utf-8"))
    source_offset = 1.46
    output_duration = 13.04
    output_key = "videoinsight/ims-cloud-short-acceptance-20260826/retry-01/output.mp4"
    token = "ims-short-acceptance-20260826"
    object_prefix = "videoinsight/ims-cloud-short-acceptance-20260826"
    store = AliyunCloudObjectStore(config)
    timings: dict[str, float] = {}

    def upload(path: Path, suffix: str, media_type: str) -> Any:
        started = time.perf_counter()
        asset = store.upload(path, f"{object_prefix}/{suffix}", media_type=media_type)
        timings[f"upload_{suffix}"] = round(time.perf_counter() - started, 3)
        return asset

    reuse_uploaded = "--reuse-uploaded" in sys.argv[1:]
    if reuse_uploaded:
        timings["uploads_reused"] = 0.0
    else:
        try:
            upload(SOURCE, "source.mp4", "video/mp4")
            upload(BROLL, "broll-pexels-phone-mount.mp4", "video/mp4")
            upload(BGM, "bgm-local-acceptance.mp3", "audio/mpeg")
        except Exception as exc:  # noqa: BLE001 - evidence must preserve one bounded failure
            write_json("failure.json", {"stage": "oss_upload", "error": str(exc)[:400], "timings": timings})
            return 3

    subtitles: list[dict[str, Any]] = []
    for segment in asr.get("segments") or []:
        start = max(0.0, round(float(segment.get("start") or 0) - source_offset, 3))
        end = min(output_duration, round(float(segment.get("end") or 0) - source_offset, 3))
        if end > start:
            subtitles.append({"text": str(segment.get("text") or ""), "start": start, "end": end})

    spoken = [(item["start"], item["end"]) for item in subtitles]
    bgm_segments: list[dict[str, Any]] = []
    bgm_url = plain_oss_url(config, f"{object_prefix}/bgm-local-acceptance.mp3")
    cursor = 0.0
    for start, end in spoken:
        if start > cursor:
            bgm_segments.append({"media_url": bgm_url, "source_start": cursor, "source_end": start, "start": cursor, "end": start, "effects": [{"Type": "Volume", "Gain": 0.24}]})
        bgm_segments.append({"media_url": bgm_url, "source_start": start, "source_end": end, "start": start, "end": end, "effects": [{"Type": "Volume", "Gain": 0.12}]})
        cursor = end
    if cursor < output_duration:
        bgm_segments.append({"media_url": bgm_url, "source_start": cursor, "source_end": output_duration, "start": cursor, "end": output_duration, "effects": [{"Type": "Volume", "Gain": 0.24}]})

    director_timeline = {
        "duration_seconds": output_duration,
        "shots": [
            {
                "media_url": plain_oss_url(config, f"{object_prefix}/source.mp4"),
                "source_start": source_offset,
                "source_end": 14.5,
                "timeline_start": 0.0,
                "timeline_end": output_duration,
                "mode": "full",
                "intent": "speaker",
                "main_track": True,
            },
            {
                "media_url": plain_oss_url(config, f"{object_prefix}/broll-pexels-phone-mount.mp4"),
                "source_start": 0.0,
                "source_end": 2.6,
                "timeline_start": 3.24,
                "timeline_end": 5.84,
                "mode": "full",
                "adapt_mode": "Cover",
                "width": 720,
                "height": 1280,
                "intent": "evidence_broll",
                "main_track": False,
                "asset_id": "broll-eda83700b0",
            },
        ],
        "subtitles": subtitles,
        "text_overlays": [
            {"text": "锁紧", "start": 3.24, "end": 4.60, "x": 0.12, "y": 0.16, "font_size": 72, "font_color": "#FFD447"}
        ],
        "bgm_segments": bgm_segments,
        "source_identity": source_identity,
    }
    compiled = compile_director_timeline_to_ims(director_timeline)
    timeline_path = EVIDENCE / "canonical-director-timeline.json"
    timeline_path.write_text(json.dumps(director_timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    write_json("ims-timeline-compiled.json", compiled)
    write_json(
        "provenance.json",
        {
            "source_of_truth": "canonical_director_timeline",
            "renderer": "aliyun_ims_SubmitMediaProducingJob",
            "source_media_sha256": sha256(SOURCE),
            "source_identity": source_identity,
            "assets": [
                {"path": str(SOURCE), "sha256": sha256(SOURCE), "role": "a_roll"},
                {"path": str(BROLL), "sha256": sha256(BROLL), "role": "dynamic_search_pexels_full", "source_url": "https://www.pexels.com/video/person-driving-a-vehicle-using-google-maps-on-a-smartphone-mounted-on-the-dashboard-3006846/", "license": "Pexels License"},
                {"path": str(BGM), "sha256": sha256(BGM), "role": "user_supplied_bgm_local_acceptance", "content_id_risk": "unknown"},
            ],
            "no_submit_batch_api": True,
            "timings": timings,
        },
    )
    output_url = f"https://{config.oss_bucket}.{config.oss_location}.aliyuncs.com/{output_key}"
    client = AliyunIMSTimelineClient(config)
    endpoint, headers, body = client.build_submit_request(compiled, output_media_url=output_url, client_token=token)
    write_json(
        "submit-payload-summary.json",
        {
            "action": "SubmitMediaProducingJob",
            "endpoint_host": endpoint.split("//", 1)[-1].split("/", 1)[0],
            "region": config.aliyun_region,
            "output_media_target": "oss-object",
            "output_object": output_key,
            "output_profile": "720x1280",
            "timeline_sha256": sha256(EVIDENCE / "ims-timeline-compiled.json"),
            "request_body_bytes": len(body),
            "header_names": sorted(headers),
            "client_token": token,
            "estimated_base_video_clip_cny": 0.03,
            "estimated_total_cost": "暂无法确定",
        },
    )

    started = time.perf_counter()
    try:
        response = client.submit(compiled, output_media_url=output_url, client_token=token)
    except Exception as exc:  # noqa: BLE001
        write_json("failure.json", {"stage": "ims_submit", "error": str(exc)[:400], "timings": timings})
        return 4
    timings["submit_seconds"] = round(time.perf_counter() - started, 3)
    job = {key: response.get(key) for key in ("RequestId", "ProjectId", "JobId", "MediaId", "VodMediaId") if response.get(key)}
    write_json("submit-response-redacted.json", job)
    job_id = str(response.get("JobId") or "")
    if not job_id:
        write_json("failure.json", {"stage": "ims_submit", "error": "response_missing_job_id", "response_keys": sorted(response), "timings": timings})
        return 5

    poll_started = time.perf_counter()
    poll_history: list[dict[str, Any]] = []
    latest: dict[str, Any] = {}
    for _ in range(30):
        latest = client.query(job_id)
        detail = latest.get("MediaProducingJob") if isinstance(latest.get("MediaProducingJob"), dict) else {}
        poll_history.append({key: detail.get(key) for key in ("JobId", "Status", "Progress", "Code", "Message", "MediaURL", "MediaId", "Duration") if detail.get(key) is not None})
        status = str(detail.get("Status") or "")
        if status in {"Success", "Failed"}:
            break
        time.sleep(5)
    timings["poll_seconds"] = round(time.perf_counter() - poll_started, 3)
    write_json("poll-history.json", poll_history)
    detail = latest.get("MediaProducingJob") if isinstance(latest.get("MediaProducingJob"), dict) else {}
    if str(detail.get("Status") or "") != "Success":
        write_json("failure.json", {"stage": "ims_render", "job_id": job_id, "latest": {key: detail.get(key) for key in ("Status", "Code", "Message", "Progress")}, "timings": timings})
        return 6

    download_started = time.perf_counter()
    output = EVIDENCE / "ims-short-output.mp4"
    download_url = store.presign_get_url(output_key)
    import httpx

    with httpx.Client(timeout=120, follow_redirects=False) as http:
        result = http.get(download_url)
        result.raise_for_status()
        output.write_bytes(result.content)
    timings["download_seconds"] = round(time.perf_counter() - download_started, 3)
    write_json(
        "cloud-result.json",
        {
            "provider": "aliyun_ims",
            "action": "SubmitMediaProducingJob",
            "job_id": job_id,
            "media_id": detail.get("MediaId") or response.get("MediaId"),
            "status": detail.get("Status"),
            "media_url_present": bool(detail.get("MediaURL")),
            "downloaded_file": str(output),
            "sha256": sha256(output),
            "timings": timings,
        },
    )
    print(json.dumps({"status": "success", "job_id": job_id, "output": str(output), "sha256": sha256(output), "timings": timings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
