"""Offline local ASR evidence exporter with real word timestamps.

The caller must provide a locally cached faster-whisper model.  This script
never downloads a model, calls a provider, uploads media, or edits the source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export local word-level ASR timestamps.")
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--model", required=True, help="Local faster-whisper model path")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hotwords", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.media.is_file():
        raise SystemExit(f"media not found: {args.media}")
    model_path = Path(args.model)
    if not model_path.is_dir():
        raise SystemExit(f"local model directory not found: {model_path}")

    from faster_whisper import WhisperModel

    model = WhisperModel(
        str(model_path),
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    )
    options = {
        "language": "zh",
        "vad_filter": True,
        "beam_size": 5,
        "word_timestamps": True,
    }
    if args.hotwords.strip():
        options["hotwords"] = args.hotwords.strip()
    segments, info = model.transcribe(str(args.media), **options)
    exported_segments = []
    word_count = 0
    for segment in segments:
        words = [
            {
                "start": round(float(word.start), 3),
                "end": round(float(word.end), 3),
                "word": str(word.word),
                "probability": round(float(getattr(word, "probability", 0.0)), 4),
            }
            for word in (segment.words or [])
        ]
        word_count += len(words)
        exported_segments.append(
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": str(segment.text).strip(),
                "words": words,
            }
        )
    payload = {
        "format": "local_word_timestamps_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(args.media.resolve()),
        "source_sha256": sha256(args.media),
        "model_path": str(model_path.resolve()),
        "model": model_path.name,
        "local_files_only": True,
        "word_timestamps": True,
        "language": str(info.language),
        "language_probability": round(float(info.language_probability), 4),
        "duration_seconds": round(float(info.duration), 3),
        "segment_count": len(exported_segments),
        "word_count": word_count,
        "segments": exported_segments,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "segment_count": len(exported_segments),
        "word_count": word_count,
        "duration_seconds": payload["duration_seconds"],
        "model": payload["model"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
