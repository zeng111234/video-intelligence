"""Render the traceable generated-image B-roll local acceptance artifact.

This is an offline evidence renderer. It does not call a provider, upload
media, or change publish permissions. Generated images are deliberately
reported separately from free-stock video.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.services.video_editor_cloud import (
    build_business_talking_head_ass,
    build_business_talking_head_overlay_preview,
    build_business_talking_head_srt,
)
from src.services.video_editor_workflow import _measure_audio_video_drift


WORK = ROOT / "work" / "auto-fine-cut-v1-20260822"
OUT = WORK / "generated-broll-v1"
ASSET_DIR = WORK / "generated-assets"
MANIFEST_PATH = ASSET_DIR / "manifest.json"
# Keep the acceptance renderer portable across Windows profiles.  A caller can
# override the input explicitly; the default follows the current user's
# standard Videos directory without embedding a machine-specific profile.
SOURCE = Path(
    os.environ.get(
        "VIDEO_ACCEPTANCE_SOURCE",
        str(Path.home() / "Videos" / "大树老师无剪辑素材7.13版.mp4"),
    )
)
ASSEMBLED = WORK / "assembled-shot-plan.mp4"
BGM = ROOT / "data" / "bgm_library" / "bgm-07dfb2b9b6ef.mp3"
TIMELINE_V3 = WORK / "subtitle-v3" / "timeline-subtitle-v3.json"
ASSEMBLED_ASR = WORK / "asr" / "assembled-word-timestamps-large-v3-turbo.json"


def portrait_pip_geometry(width: int, height: int) -> dict[str, Any]:
    """Conservative 9:16 PiP placement between the face and subtitle bands."""
    face = (round(width * 0.12), round(height * 0.10), round(width * 0.90), round(height * 0.60))
    subtitle = (round(width * 0.05), round(height * 0.79), round(width * 0.95), round(height * 0.95))
    pip_width = round(width * 0.30)
    pip_height = round(height * 0.17)
    pip_left = width - pip_width - max(12, round(width * 0.033))
    pip_top = round(height * 0.615)
    pip = (pip_left, pip_top, pip_left + pip_width, pip_top + pip_height)

    def intersects(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
        return not (
            first[2] <= second[0]
            or second[2] <= first[0]
            or first[3] <= second[1]
            or second[3] <= first[1]
        )

    face_overlap = intersects(pip, face)
    subtitle_overlap = intersects(pip, subtitle)
    return {
        "safe": not face_overlap and not subtitle_overlap,
        "bbox": {
            "left": pip[0], "top": pip[1], "right": pip[2], "bottom": pip[3],
            "width": pip_width, "height": pip_height,
            "normalized": {
                "left": round(pip[0] / width, 4), "top": round(pip[1] / height, 4),
                "right": round(pip[2] / width, 4), "bottom": round(pip[3] / height, 4),
            },
        },
        "face_safe_bbox": {"left": face[0], "top": face[1], "right": face[2], "bottom": face[3]},
        "subtitle_bbox": {"left": subtitle[0], "top": subtitle[1], "right": subtitle[2], "bottom": subtitle[3]},
        "intersects_face_safe_bbox": face_overlap,
        "intersects_subtitle_bbox": subtitle_overlap,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise RuntimeError(detail[-2000:])


def probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or "ffprobe failed").strip())
    return json.loads(result.stdout or "{}")


def ffmpeg_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", r"\:")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_transcript_text(value: str) -> str:
    return re.sub(r"[\s，。！？、,.!?；;：:（）()「」【】]+", "", value)


def _attach_real_word_timestamps(
    sentence_segments: list[dict[str, Any]],
    asr_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach real clocks to reviewed sentence text after explicit ASR text fixes.

    The clocks always come from faster-whisper.  Only two recognized-text
    substitutions are allowed for this sample (``以``→``你`` and
    ``顺应八平``→``数影霸屏``); they are recorded as text normalization rather
    than silently presented as a fresh ASR transcript.
    """
    substitutions = {"以": "你", "顺应八平": "数影霸屏"}
    raw_words = [
        word
        for segment in asr_payload.get("segments") or []
        for word in segment.get("words") or []
    ]
    cursor = 0
    normalized_counts: dict[str, int] = {}
    attached: list[dict[str, Any]] = []
    for segment in sentence_segments:
        target = _clean_transcript_text(str(segment.get("text") or ""))
        collected: list[dict[str, Any]] = []
        text = ""
        while cursor < len(raw_words) and len(text) < len(target):
            if (
                cursor + 4 <= len(raw_words)
                and "".join(str(item.get("word") or "").strip() for item in raw_words[cursor : cursor + 4])
                == "顺应八平"
            ):
                source_words = raw_words[cursor : cursor + 4]
                cursor += 4
                normalized = "数影霸屏"
                normalized_counts["顺应八平->数影霸屏"] = (
                    normalized_counts.get("顺应八平->数影霸屏", 0) + 1
                )
                text += normalized
                collected.append(
                    {
                        "text": normalized,
                        "asr_text": "顺应八平",
                        "start": source_words[0].get("start"),
                        "end": source_words[-1].get("end"),
                        "probability": min(
                            float(item.get("probability") or 0.0)
                            for item in source_words
                        ),
                    }
                )
                continue
            raw = raw_words[cursor]
            cursor += 1
            original = str(raw.get("word") or "").strip()
            normalized = substitutions.get(original, original)
            if original != normalized:
                key = f"{original}->{normalized}"
                normalized_counts[key] = normalized_counts.get(key, 0) + 1
            text += _clean_transcript_text(normalized)
            collected.append(
                {
                    "text": normalized,
                    "asr_text": original,
                    "start": raw.get("start"),
                    "end": raw.get("end"),
                    "probability": raw.get("probability"),
                }
            )
        if text != target:
            return sentence_segments, {
                "matched": False,
                "matched_segment_count": 0,
                "total_segment_count": len(sentence_segments),
                "normalized_substitutions": normalized_counts,
                "reason": "reviewed_text_did_not_match_local_word_sequence",
            }
        attached.append({**segment, "words": collected})
    if cursor != len(raw_words):
        return sentence_segments, {
            "matched": False,
            "matched_segment_count": 0,
            "total_segment_count": len(sentence_segments),
            "normalized_substitutions": normalized_counts,
            "reason": "unmatched_local_word_sequence_remains",
        }
    return attached, {
        "matched": True,
        "matched_segment_count": len(attached),
        "total_segment_count": len(sentence_segments),
        "normalized_substitutions": normalized_counts,
        "source": str(ASSEMBLED_ASR),
        "source_sha256": sha256(ASSEMBLED_ASR),
        "model": asr_payload.get("model"),
        "word_count": asr_payload.get("word_count"),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("source_type") != "built_in_image_generation":
        raise RuntimeError("generated asset manifest source_type is not traceable")
    if manifest.get("rights_status") != "generated_for_local_acceptance":
        raise RuntimeError("generated asset manifest rights_status is unexpected")

    assets: list[dict[str, Any]] = []
    for item in manifest.get("assets") or []:
        path = ASSET_DIR / str(item["path"])
        if not path.is_file():
            raise RuntimeError(f"generated asset missing: {path}")
        actual_hash = sha256(path)
        if actual_hash.lower() != str(item["sha256"]).lower():
            raise RuntimeError(f"generated asset SHA256 mismatch: {path}")
        assets.append(
            {
                **item,
                "path": str(path),
                "sha256": actual_hash,
                "asset_origin": "generated_image_asset",
                "authorization_status": "generated_for_local_acceptance",
                "publish_licensed": False,
            }
        )
    if len(assets) != 3:
        raise RuntimeError("expected exactly three generated visual assets")

    pip_geometry = portrait_pip_geometry(720, 1280)
    pip_rendered = bool(pip_geometry["safe"])

    timeline = json.loads(TIMELINE_V3.read_text(encoding="utf-8"))
    sentence_segments = [dict(item) for item in timeline["retimed_sentence_segments"]]
    word_mapping = {
        "matched": False,
        "matched_segment_count": 0,
        "total_segment_count": len(sentence_segments),
        "reason": "local_word_timestamp_artifact_missing",
    }
    segments = sentence_segments
    if ASSEMBLED_ASR.is_file():
        asr_payload = json.loads(ASSEMBLED_ASR.read_text(encoding="utf-8"))
        segments, word_mapping = _attach_real_word_timestamps(
            sentence_segments,
            asr_payload,
        )
    preview = build_business_talking_head_overlay_preview(
        segments,
        title="大树老师商业口播",
        output_profile="720p",
    )
    srt_path = OUT / "subtitles-phrase-word-timestamps-generated-v1.srt"
    ass_path = OUT / "subtitles-phrase-word-timestamps-generated-v1.ass"
    preview_path = OUT / "subtitle-preview-word-timestamps-generated-v1.json"
    srt_path.write_bytes(build_business_talking_head_srt(segments, output_profile="720p"))
    ass_path.write_bytes(
        build_business_talking_head_ass(
            segments,
            title="大树老师商业口播",
            output_profile="720p",
        )
    )
    write_json(preview_path, preview)

    bindings = [
        {
            "event_id": "generated-visual-01",
            "asset_id": assets[0]["asset_id"],
            "asset_origin": "generated_image_asset",
            "semantic_binding": "客户数据库",
            "start": 2.8,
            "end": 6.1,
            "mode": "fullscreen",
            "motion": "ken_burns_slow_zoom_in",
            "subtitle_layer": "after_visual_overlay",
        },
        {
            "event_id": "generated-visual-02",
            "asset_id": assets[1]["asset_id"],
            "asset_origin": "generated_image_asset",
            "semantic_binding": "客户关系沉淀",
            "start": 9.3,
            "end": 13.5,
            "mode": "pip",
            "motion": "ken_burns_slow_pan",
            "subtitle_layer": "after_visual_overlay",
            "pip_geometry": pip_geometry,
            "rendered": pip_rendered,
        },
        {
            "event_id": "generated-visual-03",
            "asset_id": assets[2]["asset_id"],
            "asset_origin": "generated_image_asset",
            "semantic_binding": "工厂品牌产品",
            "start": 27.5,
            "end": 31.5,
            "mode": "fullscreen",
            "motion": "ken_burns_slow_zoom_out",
            "subtitle_layer": "after_visual_overlay",
        },
    ]

    output_path = OUT / "auto-fine-cut-v1-generated-image-broll.mp4"
    subtitle = ffmpeg_path(ass_path)
    pip_bbox = pip_geometry["bbox"]
    pip_filter = (
        f"[2:v]format=rgba,scale={pip_bbox['width'] + 40}:{pip_bbox['height'] + 70}:force_original_aspect_ratio=increase,"
        f"crop={pip_bbox['width']}:{pip_bbox['height']}:x='(iw-ow)/2+8*sin(2*PI*t/4)':y='(ih-oh)/2+8*cos(2*PI*t/4)',"
        f"setsar=1,fade=t=in:st=0:d=0.25:alpha=1[pip]"
    )
    pip_overlay = (
        f"[v1][pip]overlay={pip_bbox['left']}:{pip_bbox['top']}:enable='between(t,9.3,13.5)':eof_action=pass[v2]"
        if pip_rendered
        else "[v1]copy[v2]"
    )
    filter_complex = ";".join(
        [
            "[0:v]scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=0x101827,setsar=1[base]",
            "[1:v]format=rgba,scale=760:1352:force_original_aspect_ratio=increase,crop=720:1280:x='(iw-ow)/2+20*sin(2*PI*t/4)':y='(ih-oh)/2+12*cos(2*PI*t/4)',setsar=1,fade=t=in:st=0:d=0.35:alpha=1[crm]",
            "[base][crm]overlay=0:0:enable='between(t,2.8,6.1)':eof_action=pass[v1]",
            *([pip_filter] if pip_rendered else []),
            pip_overlay,
            "[3:v]format=rgba,scale=760:1352:force_original_aspect_ratio=increase,crop=720:1280:x='(iw-ow)/2+18*cos(2*PI*t/4)':y='(ih-oh)/2+10*sin(2*PI*t/4)',setsar=1,fade=t=in:st=0:d=0.35:alpha=1[factory]",
            "[v2][factory]overlay=0:0:enable='between(t,27.5,31.5)':eof_action=pass[v3]",
            f"[v3]subtitles='{subtitle}'[vout]",
            "[0:a]aresample=async=1:first_pts=0,loudnorm=I=-16:TP=-1.5:LRA=11[voice]",
            "[4:a]volume=0.16,afade=t=in:st=0:d=0.5,aresample=async=1:first_pts=0[bgm]",
            "[voice]asplit=2[voice_mix][voice_key]",
            "[bgm][voice_key]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=450:makeup=1[ducked]",
            "[voice_mix][ducked]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]",
        ]
    )
    command = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-i",
        str(ASSEMBLED),
        "-loop",
        "1",
        "-framerate",
        "30",
        "-i",
        str(ASSET_DIR / "crm-database-generated.png"),
        "-loop",
        "1",
        "-framerate",
        "30",
        "-i",
        str(ASSET_DIR / "customer-relationship-generated.png"),
        "-loop",
        "1",
        "-framerate",
        "30",
        "-i",
        str(ASSET_DIR / "factory-products-generated.png"),
        "-stream_loop",
        "-1",
        "-i",
        str(BGM),
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-t",
        "36.16",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        "4M",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    run(command)

    keyframes: list[str] = []
    for index, second in enumerate((3.2, 10.8, 28.8, 32.5), 1):
        keyframe = OUT / f"keyframe-{index:02d}-{str(second).replace('.', '_')}s.png"
        run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-ss",
                str(second),
                "-i",
                str(output_path),
                "-frames:v",
                "1",
                str(keyframe),
            ]
        )
        keyframes.append(str(keyframe))

    output_probe = probe(output_path)
    audio_video_drift = _measure_audio_video_drift(output_path)
    duration = float((output_probe.get("format") or {}).get("duration") or 0)
    coverage_seconds = sum(item["end"] - item["start"] for item in bindings)
    texts = ["".join(cue.get("lines") or []) for cue in preview["cues"]]
    durations = [float(cue["end"]) - float(cue["start"]) for cue in preview["cues"]]
    natural_long_phrase_exception = (
        max(durations) <= 2.8 + 1e-6
        and any(
            "还是沉淀在咱们" in text and "公司的数据库" in text
            for text in texts
        )
    )
    max_duration_gate_passed = max(durations) <= 2.4 + 1e-6 or natural_long_phrase_exception
    cue_by_segment: dict[str, int] = {}
    for cue in preview["cues"]:
        key = str(cue.get("source_segment_index"))
        cue_by_segment[key] = cue_by_segment.get(key, 0) + 1
    subtitle_gate = {
        "passed": (
            14 <= len(texts) <= 18
            and min(durations) >= 0.9
            and max_duration_gate_passed
            and all(text not in {"的", "个", "品牌", "产品"} for text in texts)
            and "还是沉淀在咱们" not in texts
            and "公司的数据库" not in texts
            and "让客户认识的不" not in texts
            and "只是某个业务员" not in texts
        ),
        "checks": {
            "cue_count_14_to_18": 14 <= len(texts) <= 18,
            "min_duration_ge_0_9": min(durations) >= 0.9,
            "max_duration_le_2_4": max(durations) <= 2.4 + 1e-6,
            "max_duration_natural_phrase_exception": natural_long_phrase_exception,
            "no_singleton_fragments": all(
                text not in {"的", "个", "品牌", "产品"} for text in texts
            ),
            "no_known_naturalness_fragments": not any(
                text in {"还是沉淀在咱们", "公司的数据库", "让客户认识的不", "只是某个业务员"}
                for text in texts
            ),
        },
        "cue_count": len(texts),
        "min_duration_seconds": round(min(durations), 3),
        "max_duration_seconds": round(max(durations), 3),
        "max_duration_gate_passed": max_duration_gate_passed,
        "cue_count_by_source_segment": cue_by_segment,
        "phrase_timing_source": preview["phrase_timing_source"],
        "estimated_phrase_timestamps": preview["phrase_timing_source"] != "word_timestamps",
        "word_timestamp_mapping": word_mapping,
    }
    visual_gate = {
        "passed": bool(pip_geometry["safe"] and pip_rendered),
        "required_visual_origin_policy": "generated_image_or_confirmed_stock_media",
        "generated_images_count_as_visual_events_for_local_acceptance": True,
        "generated_images_do_not_satisfy_free_stock_license_gate": True,
        "visual_event_count": len(bindings),
        "generated_image_event_count": len(bindings),
        "real_stock_video_event_count": 0,
        "has_pip": any(item["mode"] == "pip" for item in bindings),
        "pip_rendered": pip_rendered,
        "pip_safe_area_passed": bool(pip_geometry["safe"]),
        "pip_geometry": pip_geometry,
        "has_fullscreen": any(item["mode"] == "fullscreen" for item in bindings),
        "coverage_seconds": round(coverage_seconds, 3),
        "coverage_ratio": round(coverage_seconds / duration, 4),
        "no_fixed_asset_loop": len({item["asset_id"] for item in bindings}) == len(bindings),
        "subtitle_last_render": True,
        "stock_license_gate": "BLOCKED_NOT_CONFIGURED",
    }
    provenance = {
        "provenance_version": "generated-broll-acceptance-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_video": {
            "path": str(SOURCE),
            "sha256": sha256(SOURCE),
            "render_input": str(ASSEMBLED),
            "render_input_sha256": sha256(ASSEMBLED),
        },
        "manifest_path": str(MANIFEST_PATH),
        "manifest_source_type": manifest["source_type"],
        "manifest_rights_status": manifest["rights_status"],
        "cloud_upload": False,
        "paid_stock_call": False,
        "assets": assets,
        "bindings": bindings,
        "word_timestamp_mapping": word_mapping,
        "bgm": {
            "path": str(BGM),
            "sha256": sha256(BGM),
            "provider": "local_ffmpeg",
            "ducking_enabled": True,
            "authorization_status": "unverified",
        },
    }
    timeline_payload = {
        "timeline_version": "generated-broll-timeline-v1",
        "source_path": str(SOURCE),
        "render_input": str(ASSEMBLED),
        "duration_seconds": round(duration, 3),
        "subtitle_preview": preview,
        "word_timestamps_available": bool(word_mapping.get("matched")),
        "word_timestamp_mapping": word_mapping,
        "visual_events": bindings,
        "subtitle_layer_order": ["a_roll_safe_fit", "generated_visual_events", "subtitles_last"],
        "degradation": {
            "mode": "generated_image_local_acceptance",
            "is_stock_broll": False,
            "publish_claim_allowed": False,
            "message": "生成图仅用于本地验收，未声明免费素材库或发布授权。",
        },
    }
    report = {
        "report_version": "auto-fine-cut-v1-generated-broll-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": provenance["source_video"],
        "subtitle": subtitle_gate,
        "subtitle_files": {
            "srt": str(srt_path),
            "ass": str(ass_path),
            "preview": str(preview_path),
        },
        "visual_acceptance": visual_gate,
        "alignment": {
            "word_p95_ms": None,
            "word_gate": (
                "UNVERIFIED_RISK_REFERENCE_REQUIRED"
                if word_mapping.get("matched")
                else "UNVERIFIED_RISK_SENTENCE_FALLBACK"
            ),
            "word_timestamp_source": (
                "local_faster_whisper_word_timestamps"
                if word_mapping.get("matched")
                else "sentence_level_fallback"
            ),
            "word_timestamp_mapping": word_mapping,
            "audio_video_drift_ms": (
                audio_video_drift.get("max_drift_ms") if audio_video_drift else None
            ),
            "drift_gate": (
                "passed"
                if audio_video_drift and audio_video_drift.get("passed") is True
                else "UNVERIFIED_RISK"
            ),
            "audio_video_drift": audio_video_drift,
        },
        "ffprobe": output_probe,
        "keyframes": keyframes,
        "local_acceptance_passed": bool(subtitle_gate["passed"] and visual_gate["passed"]),
        "publish_claim_allowed": False,
        "overall_passed": False,
        "blocking_gates": [
            "generated_images_are_not_free_stock_license",
            (
                "word_level_p95_unverified_against_manual_reference"
                if word_mapping.get("matched")
                else "word_level_p95_unverified_because_source_asr_is_sentence_level"
            ),
            "bgm_authorization_unverified",
        ]
        + ([] if audio_video_drift and audio_video_drift.get("passed") is True else ["audio_video_drift_unverified"])
        + ([] if pip_geometry["safe"] else ["pip_face_or_subtitle_overlap"]),
    }
    write_json(OUT / "provenance-generated-broll-v1.json", provenance)
    write_json(OUT / "timeline-generated-broll-v1.json", timeline_payload)
    write_json(OUT / "quality-report-generated-broll-v1.json", report)
    shutil.copy2(MANIFEST_PATH, OUT / "manifest-used.json")
    print(json.dumps({"output": str(output_path), "cue_count": len(texts), "coverage_ratio": visual_gate["coverage_ratio"], "overall_passed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
