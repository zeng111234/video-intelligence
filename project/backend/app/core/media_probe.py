"""Server-side media validation used before authoritative billing."""

from __future__ import annotations

import io
import json
import logging
import math
import os
import shutil
import stat
import subprocess
import struct
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

from project.backend.app.core.config import RUNTIME_ROOT


LOGGER = logging.getLogger(__name__)
MEDIA_PROBE_TEMP_ROOT = RUNTIME_ROOT / "data" / ".media-probe"
MAX_ACCEPTANCE_MEDIA_BYTES = 100 * 1024 * 1024


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


@dataclass(frozen=True)
class _Mp4Evidence:
    duration_seconds: float
    has_file_type: bool
    has_media_data: bool
    has_video_track: bool
    has_audio_track: bool


def _trusted_media_tool(name: str) -> str | None:
    candidate = shutil.which(name)
    if not candidate:
        return None
    try:
        resolved = Path(candidate).resolve(strict=True)
        metadata = resolved.stat()
    except OSError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        return None
    if os.name == "posix":
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            return None
        for directory in resolved.parents:
            try:
                directory_metadata = directory.stat()
            except OSError:
                return None
            if (
                not stat.S_ISDIR(directory_metadata.st_mode)
                or directory_metadata.st_uid != 0
                or directory_metadata.st_mode & 0o022
            ):
                return None
    return str(resolved)


def _media_probe_runtime_ready() -> bool:
    try:
        parent = os.lstat(MEDIA_PROBE_TEMP_ROOT.parent)
    except OSError:
        return False
    if (
        stat.S_ISLNK(parent.st_mode)
        or _is_reparse(parent)
        or not stat.S_ISDIR(parent.st_mode)
    ):
        return False
    if os.name == "posix" and (parent.st_uid != os.geteuid() or parent.st_mode & 0o077):
        return False
    try:
        root = os.lstat(MEDIA_PROBE_TEMP_ROOT)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if (
        stat.S_ISLNK(root.st_mode)
        or _is_reparse(root)
        or not stat.S_ISDIR(root.st_mode)
    ):
        return False
    return not (
        os.name == "posix" and (root.st_uid != os.geteuid() or root.st_mode & 0o077)
    )


def media_probe_capability() -> dict[str, object]:
    missing = [
        name for name in ("ffprobe", "ffmpeg") if _trusted_media_tool(name) is None
    ]
    if not _media_probe_runtime_ready():
        missing.append("media_probe_runtime")
    return {
        "status": "ready" if not missing else "unavailable",
        "enabled": not missing,
        "missing_configuration": missing,
    }


@contextmanager
def _controlled_media_file(payload: bytes) -> Iterator[tuple[str, tuple[int, ...]]]:
    """Pin a private runtime file and never consult TMPDIR or a reparse leaf."""
    parent = MEDIA_PROBE_TEMP_ROOT.parent
    if os.name == "posix":
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise ValueError("platform cannot securely create media probe files")
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
        parent_descriptor: int | None = None
        root_descriptor: int | None = None
        work_descriptor: int | None = None
        media_descriptor: int | None = None
        work_name = f"probe-{uuid.uuid4().hex}"
        file_name = "evidence.mp4"
        try:
            parent_descriptor = os.open(parent, directory_flags)
            parent_metadata = os.fstat(parent_descriptor)
            if (
                not stat.S_ISDIR(parent_metadata.st_mode)
                or parent_metadata.st_uid != os.geteuid()
                or parent_metadata.st_mode & 0o077
            ):
                raise ValueError("media probe runtime parent is not private")
            try:
                os.mkdir(
                    MEDIA_PROBE_TEMP_ROOT.name,
                    mode=0o700,
                    dir_fd=parent_descriptor,
                )
            except FileExistsError:
                pass
            root_descriptor = os.open(
                MEDIA_PROBE_TEMP_ROOT.name,
                directory_flags,
                dir_fd=parent_descriptor,
            )
            root_metadata = os.fstat(root_descriptor)
            if (
                not stat.S_ISDIR(root_metadata.st_mode)
                or root_metadata.st_uid != os.geteuid()
                or root_metadata.st_mode & 0o077
            ):
                raise ValueError("media probe runtime directory is not private")
            os.mkdir(work_name, mode=0o700, dir_fd=root_descriptor)
            work_descriptor = os.open(
                work_name, directory_flags, dir_fd=root_descriptor
            )
            file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow
            write_descriptor = os.open(
                file_name, file_flags, 0o600, dir_fd=work_descriptor
            )
            try:
                view = memoryview(payload)
                while view:
                    written = os.write(write_descriptor, view)
                    if written <= 0:
                        raise OSError("short write while staging media evidence")
                    view = view[written:]
                os.fsync(write_descriptor)
            finally:
                os.close(write_descriptor)
            media_descriptor = os.open(
                file_name, os.O_RDONLY | no_follow, dir_fd=work_descriptor
            )
            metadata = os.fstat(media_descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != len(payload):
                raise ValueError("media probe evidence file is invalid")
            os.unlink(file_name, dir_fd=work_descriptor)
            yield f"/proc/self/fd/{media_descriptor}", (media_descriptor,)
        finally:
            if media_descriptor is not None:
                os.close(media_descriptor)
            if work_descriptor is not None:
                try:
                    os.unlink(file_name, dir_fd=work_descriptor)
                except FileNotFoundError:
                    pass
                except OSError:
                    LOGGER.warning("media probe temporary file cleanup failed")
                os.close(work_descriptor)
            if root_descriptor is not None:
                try:
                    os.rmdir(work_name, dir_fd=root_descriptor)
                except FileNotFoundError:
                    pass
                except OSError:
                    LOGGER.warning("media probe temporary directory cleanup failed")
                os.close(root_descriptor)
            if parent_descriptor is not None:
                os.close(parent_descriptor)
        return

    try:
        parent_metadata = os.lstat(parent)
    except OSError as exc:
        raise ValueError("media probe runtime parent is unavailable") from exc
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or _is_reparse(parent_metadata)
        or not stat.S_ISDIR(parent_metadata.st_mode)
    ):
        raise ValueError("media probe runtime parent is not a directory")
    MEDIA_PROBE_TEMP_ROOT.mkdir(mode=0o700, exist_ok=True)
    root_metadata = os.lstat(MEDIA_PROBE_TEMP_ROOT)
    if (
        stat.S_ISLNK(root_metadata.st_mode)
        or _is_reparse(root_metadata)
        or not stat.S_ISDIR(root_metadata.st_mode)
    ):
        raise ValueError("media probe runtime directory is unsafe")
    work = MEDIA_PROBE_TEMP_ROOT / f"probe-{uuid.uuid4().hex}"
    media = work / "evidence.mp4"
    try:
        work.mkdir(mode=0o700)
        descriptor = os.open(media, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        metadata = os.lstat(media)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != len(payload):
            raise ValueError("media probe evidence file is invalid")
        yield str(media), ()
    finally:
        try:
            media.unlink(missing_ok=True)
            work.rmdir()
        except OSError:
            LOGGER.warning("media probe temporary cleanup failed")


def _read_box_header(source, *, end: int) -> tuple[bytes, int] | None:
    start = source.tell()
    if start >= end:
        return None
    header = source.read(8)
    if len(header) != 8:
        raise ValueError("MP4 box header is truncated")
    size, kind = struct.unpack(">I4s", header)
    header_size = 8
    if size == 1:
        extended = source.read(8)
        if len(extended) != 8:
            raise ValueError("MP4 extended box header is truncated")
        size = struct.unpack(">Q", extended)[0]
        header_size = 16
    elif size == 0:
        size = end - start
    if size < header_size or start + size > end:
        raise ValueError("MP4 box size is invalid")
    return kind, start + size


def _movie_duration(stream: BinaryIO, *, box_end: int) -> float:
    payload = stream.read(min(32, box_end - stream.tell()))
    if len(payload) < 20:
        raise ValueError("MP4 movie header is truncated")
    version = payload[0]
    if version == 0:
        timescale = struct.unpack(">I", payload[12:16])[0]
        duration_ticks = struct.unpack(">I", payload[16:20])[0]
    elif version == 1 and len(payload) >= 32:
        timescale = struct.unpack(">I", payload[20:24])[0]
        duration_ticks = struct.unpack(">Q", payload[24:32])[0]
    else:
        raise ValueError("MP4 movie header version is unsupported")
    if timescale <= 0 or duration_ticks <= 0:
        raise ValueError("MP4 movie duration is invalid")
    return duration_ticks / timescale


def _media_handler(stream: BinaryIO, *, media_end: int) -> bytes | None:
    handler: bytes | None = None
    while stream.tell() < media_end:
        child = _read_box_header(stream, end=media_end)
        if child is None:
            break
        child_kind, child_end = child
        if child_kind == b"hdlr":
            payload = stream.read(min(12, child_end - stream.tell()))
            if len(payload) < 12:
                raise ValueError("MP4 media handler is truncated")
            handler = payload[8:12]
        stream.seek(child_end)
    return handler


def _track_handler(stream: BinaryIO, *, track_end: int) -> bytes | None:
    handler: bytes | None = None
    while stream.tell() < track_end:
        child = _read_box_header(stream, end=track_end)
        if child is None:
            break
        child_kind, child_end = child
        if child_kind == b"mdia":
            handler = _media_handler(stream, media_end=child_end)
        stream.seek(child_end)
    return handler


def _movie_evidence(
    stream: BinaryIO, *, movie_end: int
) -> tuple[float | None, bool, bool]:
    duration: float | None = None
    has_video = False
    has_audio = False
    while stream.tell() < movie_end:
        child = _read_box_header(stream, end=movie_end)
        if child is None:
            break
        child_kind, child_end = child
        if child_kind == b"mvhd":
            duration = _movie_duration(stream, box_end=child_end)
        elif child_kind == b"trak":
            handler = _track_handler(stream, track_end=child_end)
            has_video = has_video or handler == b"vide"
            has_audio = has_audio or handler == b"soun"
        stream.seek(child_end)
    return duration, has_video, has_audio


def _probe_mp4_stream(stream: BinaryIO, *, file_size: int) -> _Mp4Evidence:
    if file_size < 16:
        raise ValueError("MP4 file is too small")
    duration: float | None = None
    has_file_type = False
    has_media_data = False
    has_video = False
    has_audio = False
    while stream.tell() < file_size:
        header = _read_box_header(stream, end=file_size)
        if header is None:
            break
        kind, box_end = header
        payload_start = stream.tell()
        if kind == b"ftyp":
            has_file_type = box_end - payload_start >= 8
        elif kind == b"mdat":
            has_media_data = box_end - payload_start > 0
        elif kind == b"moov":
            movie_duration, movie_video, movie_audio = _movie_evidence(
                stream, movie_end=box_end
            )
            duration = movie_duration if movie_duration is not None else duration
            has_video = has_video or movie_video
            has_audio = has_audio or movie_audio
        stream.seek(box_end)
    if duration is None:
        raise ValueError("MP4 movie header is missing")
    return _Mp4Evidence(
        duration_seconds=duration,
        has_file_type=has_file_type,
        has_media_data=has_media_data,
        has_video_track=has_video,
        has_audio_track=has_audio,
    )


def _probe_decodable_acceptance_media(payload: bytes) -> float:
    if len(payload) > MAX_ACCEPTANCE_MEDIA_BYTES:
        raise ValueError("acceptance media exceeds 100 MB")
    evidence = _probe_mp4_stream(io.BytesIO(payload), file_size=len(payload))
    if not (
        evidence.has_file_type
        and evidence.has_media_data
        and evidence.has_video_track
        and evidence.has_audio_track
    ):
        raise ValueError("acceptance media is missing MP4 audio/video structure")
    ffprobe = _trusted_media_tool("ffprobe")
    ffmpeg = _trusted_media_tool("ffmpeg")
    if ffprobe is None or ffmpeg is None:
        raise ValueError("trusted ffprobe and ffmpeg are required")
    with _controlled_media_file(payload) as (source, pass_fds):
        common = ["-v", "error", "-protocol_whitelist", "file,crypto,data"]
        probe = subprocess.run(
            [
                ffprobe,
                *common,
                "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name",
                "-of",
                "json",
                source,
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=30,
            check=True,
            pass_fds=pass_fds,
        )
        try:
            details = json.loads(probe.stdout)
            format_details = details["format"]
            format_name = str(format_details["format_name"])
            duration = float(format_details["duration"])
            streams = details["streams"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("ffprobe returned invalid media evidence") from exc
        if (
            not math.isfinite(duration)
            or not 0 < duration <= 6 * 60 * 60
            or "mp4" not in {item.strip() for item in format_name.split(",")}
            or not isinstance(streams, list)
            or not any(
                item.get("codec_type") == "video" and item.get("codec_name")
                for item in streams
                if isinstance(item, dict)
            )
            or not any(
                item.get("codec_type") == "audio" and item.get("codec_name")
                for item in streams
                if isinstance(item, dict)
            )
        ):
            raise ValueError("ffprobe did not find decodable MP4 audio and video")
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                *common,
                "-i",
                source,
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-frames:v",
                "1",
                "-frames:a",
                "1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=True,
            pass_fds=pass_fds,
        )
    return duration


def _probe_mp4_duration(source: Path) -> float:
    """Read the movie header without requiring a system FFmpeg install."""

    file_size = source.stat().st_size
    with source.open("rb") as stream:
        return _probe_mp4_stream(stream, file_size=file_size).duration_seconds


def probe_media_duration(path: str | Path | bytes | bytearray | memoryview) -> float:
    if isinstance(path, (bytes, bytearray, memoryview)):
        payload = bytes(path)
        try:
            duration = _probe_decodable_acceptance_media(payload)
        except (OSError, ValueError, struct.error, subprocess.SubprocessError) as exc:
            raise ValueError("无法读取素材时长，请确认文件完整。") from exc
        if not 0 < duration <= 6 * 60 * 60:
            raise ValueError("素材时长无效或超过 6 小时限制。")
        return duration

    source = Path(path)
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        duration = float(completed.stdout.strip())
    except FileNotFoundError:
        try:
            duration = _probe_mp4_duration(source)
        except (OSError, ValueError, struct.error) as exc:
            raise ValueError("无法读取素材时长，请确认文件完整。") from exc
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ValueError("无法读取素材时长，请确认文件完整。") from exc
    if not 0 < duration <= 6 * 60 * 60:
        raise ValueError("素材时长无效或超过 6 小时限制。")
    return duration


def get_media_duration_probe():
    return probe_media_duration
