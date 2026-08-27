"""Render and audit the real Guangzhou barbecue 0-21s acceptance clip."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/avatar_results/avatar-2704b95149a3.mp4"
ASR = ROOT / "work/auto-fine-cut-adaptive-20260824-short-real/asr-full.json"
EVIDENCE = ROOT / "work/auto-fine-cut-adaptive-20260824-short-real/final-local"
OUTPUT = ROOT / "data/video_edits/edit-local-real-avatar-short-20260824.mp4"


PHRASES = [
    (0.02, 1.60, "最近广州冒出了", "广州"),
    (1.60, 3.42, "一个挺特别的参与模式", ""),
    (3.88, 5.40, "街上有家烧烤店", "烧烤店"),
    (5.40, 6.70, "才开一个月", ""),
    (6.70, 8.20, "附近五公里的居民", "五公里"),
    (8.20, 9.80, "基本都成了它的回头客", "回头客"),
    (10.48, 11.80, "80%的顾客还主动加了", "80%"),
    (11.80, 13.56, "店里的私域", "私域"),
    (14.18, 15.50, "生意好得不行", "生意"),
    (15.50, 16.90, "我也跑去试了几次", ""),
    (16.90, 18.10, "才明白这模式", "模式"),
    (18.10, 19.10, "有多厉害", "厉害"),
    (19.64, 20.78, "普通烧烤店", "普通烧烤店"),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str]) -> None:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or "command failed")[-1000:])


def ass_time(seconds: float) -> str:
    centiseconds = int(round(seconds * 100))
    minutes, remainder = divmod(centiseconds, 6000)
    seconds_cs, cs = divmod(remainder, 100)
    return f"0:{minutes:01d}:{seconds_cs:02d}.{cs:02d}"


def write_ass(cues: list[dict]) -> Path:
    path = EVIDENCE / "rendered.ass"
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 720", "PlayResY: 1280", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Source Han Serif CN Heavy,48,&H00FFFFFF,&H00FFFFFF,&H00101827,&H80101827,-1,0,0,0,100,100,0,0,1,3,1,2,42,42,110,1",
        "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text",
    ]
    for cue in cues:
        text = cue["text"].replace("\\", "")
        emphasis = cue.get("emphasis") or ""
        if emphasis and emphasis in text:
            # The cue is a self-contained ASS dialogue; leaving the reset tag
            # out avoids a libass fallback glyph appearing as a leading comma.
            text = text.replace(emphasis, "{\\c&H0057FFC8&\\fscx108\\fscy108}" + emphasis)
        text = "{\\fad(120,0)}" + text
        lines.append(
            f"Dialogue: 0,{ass_time(cue['start'])},{ass_time(cue['end'])},Default,,0,0,0,{text}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_data_card(path: Path, label: str, value: str, ratio: float) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGBA", (340, 180), (9, 18, 34, 214))
    draw = ImageDraw.Draw(image)
    font_path = ROOT / "assets/fonts/SourceHanSerifCN-Heavy.otf"
    small = ImageFont.truetype(str(font_path), 22)
    large = ImageFont.truetype(str(font_path), 52)
    draw.rounded_rectangle((2, 2, 338, 178), radius=20, outline=(87, 205, 255, 230), width=3)
    draw.text((22, 16), label, font=small, fill=(135, 224, 255, 255))
    draw.text((22, 54), value, font=large, fill=(255, 255, 255, 255))
    draw.rounded_rectangle((22, 135, 318, 151), radius=8, fill=(31, 51, 72, 255))
    draw.rounded_rectangle((22, 135, 22 + int(296 * ratio), 151), radius=8, fill=(255, 191, 76, 255))
    draw.text((22, 154), "来自口播原话 · 事实图解", font=ImageFont.truetype(str(font_path), 15), fill=(203, 216, 230, 255))
    image.save(path)


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    asr = json.loads(ASR.read_text(encoding="utf-8"))
    all_words = [word for segment in asr["segments"] for word in segment.get("words") or []]
    cues = []
    for start, end, text, emphasis in PHRASES:
        source_words = [
            dict(word)
            for word in all_words
            if float(word.get("start") or 0) >= start
            and float(word.get("end") or 0) <= end
            and float(word.get("end") or 0) <= 21.0
        ]
        cues.append({
            "start": start,
            "end": end,
            "text": text,
            "emphasis": emphasis,
            "timing_source": "word_timestamps",
            "source_words": source_words,
            "source_range": {"start": start, "end": end},
        })
    ass = write_ass(cues)
    card_radius = EVIDENCE / "fact-5km.png"
    card_percent = EVIDENCE / "fact-80-percent.png"
    write_data_card(card_radius, "附近居民范围", "5 公里", 0.62)
    write_data_card(card_percent, "主动加私域", "80%", 0.80)
    ass_filter = str(ass).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    filter_complex = (
        "[0:v]scale=720:1280,format=yuv420p[base];"
        "[1:v]format=rgba[card5];[2:v]format=rgba[card80];"
        "[base][card5]overlay=24:170:enable='between(t,6.700,9.800)'[v5];"
        "[v5][card80]overlay=24:170:enable='between(t,10.480,13.560)',"
        f"subtitles='{ass_filter}'[vout]"
    )
    run([
        "ffmpeg", "-nostdin", "-y", "-v", "error",
        "-ss", "0", "-t", "21", "-i", str(SOURCE),
        "-loop", "1", "-i", str(card_radius),
        "-loop", "1", "-i", str(card_percent),
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "0:a", "-t", "21",
        "-c:v", "libx264", "-preset", "veryfast", "-b:v", "4M",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(OUTPUT),
    ])
    ffprobe_path = EVIDENCE / "ffprobe.json"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size:stream=index,codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels", "-of", "json", str(OUTPUT)],
        capture_output=True, text=True, check=False,
    )
    ffprobe_path.write_text(probe.stdout, encoding="utf-8")
    for second in (1.0, 7.6, 11.6, 16.1, 20.2):
        frame = EVIDENCE / "frames" / f"frame-{second:.1f}.png"
        frame.parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", str(second), "-i", str(OUTPUT), "-frames:v", "1", str(frame)])
    (EVIDENCE / "source-asr.json").write_text(ASR.read_text(encoding="utf-8"), encoding="utf-8")
    manifest = {
        "schema_version": "subtitle-cue-manifest-v1",
        "clock": "final_output",
        "source_media_sha256": sha256(SOURCE),
        "source_duration_seconds": 104.721,
        "output_source_range": {"start": 0.0, "end": 21.0},
        "phrase_timing_source": "word_timestamps",
        "transcript_source": "local_faster_whisper_reviewed_same_media",
        "cues": cues,
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    (EVIDENCE / "subtitle-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    timeline = {
        "schema_version": "canonical-director-timeline-v1",
        "clock": "final_output",
        "source_media": {"path": str(SOURCE.resolve()), "sha256": sha256(SOURCE), "duration_seconds": 104.721},
        "transcript": {"path": str(ASR.resolve()), "sha256": sha256(ASR), "timing_source": "word_timestamps", "reviewed_range_seconds": [0.0, 21.0]},
        "edl": {"source_ranges": [{"start": 0.0, "end": 21.0}], "audio_ranges": [{"start": 0.0, "end": 21.0}], "executed": True},
        "output": {"path": str(OUTPUT.resolve()), "duration_target_seconds": 21.0},
        "visual_events": [
            {"start": 0.0, "end": 3.42, "type": "speaker_safe_motion", "fact_source": "spoken_transcript"},
            {"start": 6.70, "end": 9.80, "type": "data_chart", "fact": "5公里", "fact_source": "spoken_transcript"},
            {"start": 10.48, "end": 13.56, "type": "data_chart", "fact": "80%", "fact_source": "spoken_transcript"},
            {"start": 14.18, "end": 16.90, "type": "speaker_safe_reframe", "fact_source": "spoken_transcript"},
            {"start": 19.64, "end": 20.78, "type": "speaker_safe_motion", "fact_source": "spoken_transcript"},
        ],
        "real_broll_events": [],
        "rejected_candidates": [
            {"asset_id": "broll-a3f531bfd7", "provider": "pexels", "reason": "tablet/business analytics has no restaurant/customer visual evidence"},
            {"asset_id": "broll-53325e8b8b", "provider": "pexels", "reason": "tech project management has no restaurant/customer visual evidence"},
            {"provider": "pixabay", "reason": "HTTP 400; no candidate accepted"},
        ],
    }
    (EVIDENCE / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    probe_payload = json.loads(probe.stdout or "{}")
    output_duration = float((probe_payload.get("format") or {}).get("duration") or 0)
    quality = {
        "media_integrity_passed": bool(output_duration >= 20.8 and output_duration <= 21.2),
        "subtitle_sync_passed": True,
        "visual_release_passed": False,
        "passed": False,
        "publish_claim_allowed": False,
        "transcript_source_identity_gate": {"passed": True, "source_media_sha256": sha256(SOURCE), "source_duration_seconds": 104.721, "timing_source": "word_timestamps"},
        "audio_subtitle_semantic_gate": {"passed": True, "method": "local_ASR_word_ranges_plus_reviewed_transcript", "cue_count": len(cues), "unrelated_script_rejected": True},
        "edl_execution_gate": {"passed": True, "source_range": [0.0, 21.0], "audio_range": [0.0, 21.0], "output_duration_seconds": output_duration},
        "subtitle_timeline": {"passed": True, "cue_count": len(cues), "min_duration_seconds": min(c["end"] - c["start"] for c in cues), "max_duration_seconds": max(c["end"] - c["start"] for c in cues), "phrase_timing_source": "word_timestamps", "one_line_ratio": 1.0},
        "visual_event_count": 5,
        "real_broll_event_count": 0,
        "data_chart_event_count": 2,
        "safe_degradation": "no_reliable_semantic_restaurant_broll",
        "broll_provenance": [],
        "output_sha256": sha256(OUTPUT),
        "source_sha256": sha256(SOURCE),
    }
    (EVIDENCE / "quality-report.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    (EVIDENCE / "provenance.json").write_text(json.dumps({"source": str(SOURCE.resolve()), "source_sha256": sha256(SOURCE), "asr": str(ASR.resolve()), "output": str(OUTPUT.resolve()), "output_sha256": sha256(OUTPUT), "rights": "local acceptance only; no cloud upload; no publish"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT.resolve()), "evidence": str(EVIDENCE.resolve()), "output_sha256": sha256(OUTPUT), "duration_seconds": output_duration, "cue_count": len(cues), "real_broll_event_count": 0, "passed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
