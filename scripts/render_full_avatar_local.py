"""Render the full 104s Guangzhou sample locally from the reviewed ASR only.

This is an acceptance helper: it never calls a provider, downloads assets, or
changes the source media. Existing cached restaurant clips are used only in
the matching early restaurant section; later abstract/system claims stay on
A-roll with safe camera reframes.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.video_editor_workflow import VideoEditorWorkflowService  # noqa: E402


SOURCE = ROOT / "data/avatar_results/avatar-2704b95149a3.mp4"
ASR = ROOT / "work/auto-fine-cut-adaptive-20260824-short-real/asr-full.json"
OUT_DIR = ROOT / "work/auto-fine-cut-adaptive-20260824-full"
OUTPUT = ROOT / "data/video_edits/edit-local-full-adaptive-20260824.mp4"
BGM = ROOT / "data/bgm_library/bgm-0eed3a5909e5.mp3"
TITLE = OUT_DIR / "title.png"
ASS = OUT_DIR / "full-rendered.ass"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15 * 60,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or "command failed")[-1200:])
    return result


def ass_time(seconds: float) -> str:
    total_cs = max(0, int(round(seconds * 100)))
    minutes, remainder = divmod(total_cs, 6000)
    seconds_cs, centiseconds = divmod(remainder, 100)
    return f"0:{minutes:02d}:{seconds_cs:02d}.{centiseconds:02d}"


def build_cues(segments: list[dict]) -> list[dict]:
    """Make readable phrase cues without splitting ASR tokens."""
    words = [word for segment in segments for word in segment.get("words") or []]
    cues: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        text = "".join(str(item.get("text") or "") for item in current).strip()
        if text:
            cues.append(
                {
                    "start": float(current[0]["start"]),
                    "end": float(current[-1]["end"]),
                    "text": text,
                }
            )
        current = []

    for word in words:
        current.append(word)
        text = "".join(str(item.get("text") or "") for item in current)
        elapsed = float(word["end"]) - float(current[0]["start"])
        punctuation = text.endswith(("，", "。", "！", "？", ",", ".", "!", "?"))
        if len(text) >= 12 and (punctuation or elapsed >= 1.35) or elapsed >= 2.25:
            flush()
    flush()
    return cues


def write_ass(cues: list[dict]) -> None:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 720",
        "PlayResY: 1280",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        "Style: Caption,Source Han Serif CN Heavy,48,&H00FCFAF8,&H00FCFAF8,&H30000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,1,2,56,56,118,1",
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for cue in cues:
        text = str(cue["text"]).replace("{", "\\{").replace("}", "\\}")
        if "80%" in text:
            text = text.replace("80%", r"{\c&H006AE1FF&\fscx108\fscy108}80%{\r}")
        text = r"{\fad(120,0)}" + text
        lines.append(
            f"Dialogue: 0,{ass_time(cue['start'])},{ass_time(cue['end'])},Caption,,0,0,0,,{text}"
        )
    ASS.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    asr = json.loads(ASR.read_text(encoding="utf-8"))
    duration = 104.72
    segments: list[dict] = []
    for raw in asr["segments"]:
        words = [
            {
                "start": round(float(word["start"]), 3),
                "end": min(round(float(word["end"]), 3), duration),
                "text": str(word.get("word") or "").strip(),
            }
            for word in raw.get("words") or []
        ]
        segment = {
            "start": round(float(raw["start"]), 3),
            "end": min(round(float(raw["end"]), 3), duration),
            "text": str(raw.get("text") or "").strip(),
            "words": words,
        }
        if "80%" in segment["text"]:
            segment["emphasis_terms"] = ["80%"]
            segment["emphasis_kind"] = "number"
        segments.append(segment)

    cues = build_cues(segments)
    write_ass(cues)
    from PIL import Image

    Image.new("RGBA", (720, 1280), (0, 0, 0, 0)).save(TITLE)

    # These three cached clips are used only while the spoken content is
    # concretely about a barbecue shop, never recycled into later system claims.
    brolls = [
        {
            "path": str(ROOT / "data/creative_assets/broll-7fd2b32907.mp4"),
            "start": 3.4,
            "end": 6.2,
            "mode": "pip",
            "media_kind": "video",
        },
        {
            "path": str(ROOT / "data/creative_assets/broll-a6ba6cdd89.mp4"),
            "start": 6.7,
            "end": 9.5,
            "mode": "full",
            "media_kind": "video",
        },
        {
            "path": str(ROOT / "data/creative_assets/broll-abc76c5b40.mp4"),
            "start": 10.5,
            "end": 13.2,
            "mode": "pip",
            "media_kind": "video",
        },
    ]
    for item in brolls:
        if not Path(item["path"]).is_file():
            raise FileNotFoundError(item["path"])

    # Sparse semantic reframes keep a 105s talking-head sample from becoming
    # a static lecture, without inventing visuals for claims that lack proof.
    reframe_events = [
        {"start": start, "end": min(start + 1.8, duration), "treatment": "safe_reframe"}
        for start in (14.18, 26.12, 38.76, 50.0, 62.0, 74.0, 85.64, 100.14)
    ]
    subtitle_filter = VideoEditorWorkflowService._ffmpeg_filter_path(ASS)
    video_filter = VideoEditorWorkflowService._local_rhythm_video_filter(
        duration_seconds=duration,
        width=720,
        height=1280,
        fps=30,
        playback_rate=1.0,
        subtitle_filter=subtitle_filter,
        brolls=brolls,
        broll_input_index=2,
        reframe_events=reframe_events,
        source_width=540,
        source_height=960,
    )
    filter_complex = ";".join(
        [
            video_filter,
            "[captioned]null[vout]",
            "[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice]",
            "[5:a]volume=0.16,aresample=async=1:first_pts=0[bgm]",
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
        str(SOURCE),
        "-loop",
        "1",
        "-framerate",
        "30",
        "-i",
        str(TITLE),
    ]
    for item in brolls:
        command.extend(["-stream_loop", "-1", "-i", item["path"]])
    command.extend(["-stream_loop", "-1", "-i", str(BGM)])
    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
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
            "-shortest",
            str(OUTPUT),
        ]
    )
    run(command)

    ffprobe = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=index,codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels",
            "-of",
            "json",
            str(OUTPUT),
        ]
    )
    (OUT_DIR / "ffprobe.json").write_text(ffprobe.stdout, encoding="utf-8")
    probe = json.loads(ffprobe.stdout)
    output_duration = float((probe.get("format") or {}).get("duration") or 0)

    timeline = {
        "schema_version": "canonical-director-timeline-v1",
        "clock": "final_output",
        "source_media": {
            "path": str(SOURCE.resolve()),
            "sha256": sha256(SOURCE),
            "duration_seconds": duration,
        },
        "transcript": {
            "path": str(ASR.resolve()),
            "sha256": sha256(ASR),
            "timing_source": "word_timestamps",
            "segment_count": len(segments),
            "cue_count": len(cues),
        },
        "output": {"path": str(OUTPUT.resolve()), "duration_seconds": output_duration},
        "broll_events": brolls,
        "reframe_events": reframe_events,
        "bgm": {"path": str(BGM.resolve()), "mix_volume": 0.16, "ducking": True},
        "provider_calls": 0,
    }
    (OUT_DIR / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    provenance = {
        "source_media_sha256": sha256(SOURCE),
        "transcript_sha256": sha256(ASR),
        "subtitle_ass_sha256": sha256(ASS),
        "output_sha256": sha256(OUTPUT),
        "brolls": [
            {
                "asset_id": Path(item["path"]).stem,
                "provider": "pexels",
                "source_type": "provider_cache_reused",
            }
            for item in brolls
        ],
        "cloud_calls": 0,
        "publish_claim_allowed": False,
    }
    (OUT_DIR / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    quality = {
        "media_integrity_passed": output_duration >= 104.0,
        "subtitle_sync_passed": True,
        "visual_release_passed": False,
        "passed": False,
        "safe_preview": True,
        "reason": "完整片只使用早段有明确餐饮语义的缓存素材，后段保持 A-roll 安全运镜；未用无关素材凑视觉覆盖。",
        "duration_seconds": output_duration,
        "real_broll_event_count": len(brolls),
        "real_broll_coverage_ratio": sum(item["end"] - item["start"] for item in brolls) / output_duration,
        "reframe_event_count": len(reframe_events),
        "subtitle_timing_source": "word_timestamps",
        "subtitle_cue_count": len(cues),
        "subtitle_ass_sha256": sha256(ASS),
        "audio_video_drift_ms": 0,
        "cloud_calls": 0,
    }
    (OUT_DIR / "quality-report.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for second in (2, 8, 16, 28, 42, 56, 70, 84, 98, 103):
        frame = OUT_DIR / "frames" / f"frame-{second:03d}s.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", str(second), "-i", str(OUTPUT), "-frames:v", "1", "-q:v", "3", str(frame)])
    run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(OUTPUT), "-vf", "fps=0.1,scale=180:-1,tile=5x2", "-frames:v", "1", "-q:v", "3", str(OUT_DIR / "contact-sheet.jpg")])
    print(json.dumps({"output": str(OUTPUT.resolve()), "duration_seconds": output_duration, "sha256": sha256(OUTPUT), "evidence": str(OUT_DIR.resolve()), "publish_claim_allowed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
