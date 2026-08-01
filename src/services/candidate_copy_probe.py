from __future__ import annotations

import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from src.adapters.platform_link_parser import PlatformLinkParserError
from src.models import CandidateCopyProbe, VideoCandidate
from src.resources import load_asr_model
from src.services.video_source import VideoSourceError, fetch_authorized_video

COPY_PROBE_VERSION = "copy_probe_v2"
# Three seconds misses many explainers that open with a logo or a product shot.
# This remains a bounded yes/no check, not a full transcription.
COPY_PROBE_SECONDS = 10
COPY_PROBE_MAX_BYTES = 4 * 1024 * 1024


def _has_detectable_copy(segments: Any) -> bool:
    """Keep only a yes/no verdict; recognised words must not leave this process."""
    for segment in segments:
        text = re.sub(r"\s+", "", str(getattr(segment, "text", "")))
        info = getattr(segment, "avg_logprob", None)
        if info is not None and float(info) < -1.2:
            continue
        if len(re.findall(r"[\u4e00-\u9fff]", text)) >= 2:
            return True
        if len(re.findall(r"[A-Za-z0-9]", text)) >= 3:
            return True
    return False


class CandidateCopyProbeService:
    """Safely classify a short opening sample without retaining a transcript."""

    def __init__(self, parser: Any) -> None:
        self._parser = parser

    def probe(self, candidate: VideoCandidate) -> CandidateCopyProbe:
        now = datetime.now().astimezone()
        window_label = f"前{COPY_PROBE_SECONDS}秒"
        if not candidate.source_url:
            return CandidateCopyProbe(
                candidate_id=candidate.video_id,
                status="failed",
                checked_at=now,
                message="没有原视频链接，无法检测文案。",
                version=COPY_PROBE_VERSION,
            )
        if candidate.platform.value == "xiaohongshu":
            return CandidateCopyProbe(
                candidate_id=candidate.video_id,
                status="unsupported",
                checked_at=now,
                message="小红书安全模式已开启，未检测文案。",
                version=COPY_PROBE_VERSION,
            )
        try:
            media = self._parser.resolve(str(candidate.source_url))
            direct = fetch_authorized_video(
                str(media.media_url),
                require_extension=False,
                fallback_name="sample.mp4",
                request_headers=getattr(media, "media_request_headers", None),
                max_bytes=COPY_PROBE_MAX_BYTES,
                max_elapsed_seconds=20,
                timeout_seconds=12,
                range_bytes=COPY_PROBE_MAX_BYTES,
            )
            with tempfile.TemporaryDirectory(prefix="copy-probe-") as directory:
                source = Path(directory) / "sample.mp4"
                audio = Path(directory) / "sample.wav"
                source.write_bytes(direct.content)
                completed = subprocess.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-t",
                        str(COPY_PROBE_SECONDS),
                        "-i",
                        str(source),
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-c:a",
                        "pcm_s16le",
                        "-y",
                        str(audio),
                    ],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if (
                    completed.returncode != 0
                    or not audio.exists()
                    or audio.stat().st_size < 1024
                ):
                    return CandidateCopyProbe(
                        candidate_id=candidate.video_id,
                        status="failed",
                        checked_at=now,
                        message=f"{window_label}音频无法解析，需手动确认。",
                        version=COPY_PROBE_VERSION,
                    )
                segments, _ = load_asr_model("base").transcribe(
                    str(audio),
                    language="zh",
                    beam_size=1,
                    vad_filter=True,
                    condition_on_previous_text=False,
                )
                status = "detected" if _has_detectable_copy(segments) else "no_text"
                message = (
                    f"{window_label}检测到文案。"
                    if status == "detected"
                    else f"{window_label}未识别到文案，不代表整段没有文案。"
                )
                return CandidateCopyProbe(
                    candidate_id=candidate.video_id,
                    status=status,
                    checked_at=now,
                    message=message,
                    version=COPY_PROBE_VERSION,
                )
        except (
            PlatformLinkParserError,
            VideoSourceError,
            subprocess.SubprocessError,
            OSError,
            RuntimeError,
            ValueError,
        ):
            return CandidateCopyProbe(
                candidate_id=candidate.video_id,
                status="failed",
                checked_at=now,
                message=f"{window_label}文案检测未完成，需手动确认。",
                version=COPY_PROBE_VERSION,
            )
