from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import monotonic

from src.asr_quality import (
    character_error_rate,
    extract_transcript_text,
    hotword_recall,
    number_token_accuracy,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark a local ASR model.")
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--model", default="base")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--hotwords", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.media.is_file():
        raise SystemExit(f"Media file not found: {args.media}")

    from faster_whisper import WhisperModel

    started = monotonic()
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    load_seconds = monotonic() - started
    options = {"language": "zh", "vad_filter": True, "beam_size": 5}
    if args.hotwords:
        options["hotwords"] = args.hotwords
    transcription_started = monotonic()
    segments, info = model.transcribe(str(args.media), **options)
    segment_list = list(segments)
    transcription_seconds = monotonic() - transcription_started
    transcript = "\n".join(segment.text.strip() for segment in segment_list)

    result: dict[str, object] = {
        "model": args.model,
        "language": info.language,
        "media_duration_seconds": round(float(info.duration), 3),
        "model_load_seconds": round(load_seconds, 3),
        "transcription_seconds": round(transcription_seconds, 3),
        "realtime_factor": round(transcription_seconds / float(info.duration), 3),
        "segment_count": len(segment_list),
        "transcript": transcript,
    }
    if args.reference:
        reference = extract_transcript_text(args.reference.read_text("utf-8"))
        hotword_list = [
            word.strip()
            for word in args.hotwords.replace("，", ",").split(",")
            if word.strip()
        ]
        result["reference_path"] = str(args.reference)
        result["character_error_rate"] = round(
            character_error_rate(reference, transcript), 4
        )
        result["number_token_accuracy"] = number_token_accuracy(reference, transcript)
        result["hotword_recall"] = hotword_recall(reference, transcript, hotword_list)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
