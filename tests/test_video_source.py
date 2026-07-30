from __future__ import annotations

import socket
from urllib.error import URLError

import pytest

from src.services.video_source import VideoSourceError, fetch_authorized_video


def public_resolver(host: str, port: int, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]


class FakeResponse:
    def __init__(
        self,
        content: bytes,
        *,
        final_url: str = "https://cdn.example.com/video.mp4",
        content_type: str = "video/mp4",
        content_length: str | None = None,
    ):
        self._content = content
        self._offset = 0
        self._final_url = final_url
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def geturl(self) -> str:
        return self._final_url

    def read(self, size: int) -> bytes:
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def test_direct_video_url_is_downloaded_with_safe_metadata() -> None:
    content = b"\x00\x00\x00\x18ftypisom-authorized-video"

    def open_url(request, timeout):
        assert request.full_url == "https://cdn.example.com/video.mp4?token=secret"
        assert timeout == 15
        return FakeResponse(
            content,
            final_url="https://cdn.example.com/video.mp4?token=secret",
        )

    video = fetch_authorized_video(
        "https://cdn.example.com/video.mp4?token=secret",
        resolver=public_resolver,
        open_url=open_url,
    )

    assert video.name == "video.mp4"
    assert video.media_type == "video/mp4"
    assert video.content == content


def test_bilibili_audio_mp4_stream_is_accepted_as_transcription_media() -> None:
    content = b"\x00\x00\x00\x18ftypisom-authorized-audio"

    video = fetch_authorized_video(
        "https://cdn.example.com/audio.m4s",
        resolver=public_resolver,
        open_url=lambda *args, **kwargs: FakeResponse(
            content,
            final_url="https://cdn.example.com/audio.m4s",
            content_type="audio/mp4",
        ),
        require_extension=False,
        fallback_name="bilibili-BV1TEST.mp4",
    )

    assert video.name == "bilibili-BV1TEST.mp4"
    assert video.media_type == "audio/mp4"
    assert video.content == content


def test_platform_share_page_is_rejected_before_network_access() -> None:
    with pytest.raises(VideoSourceError, match="平台分享页暂不支持"):
        fetch_authorized_video(
            "https://www.douyin.com/video/123456",
            resolver=lambda *args, **kwargs: pytest.fail("DNS should not be called"),
            open_url=lambda *args, **kwargs: pytest.fail(
                "network should not be called"
            ),
        )


def test_private_or_nonstandard_video_url_is_rejected() -> None:
    def private_resolver(host: str, port: int, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    with pytest.raises(VideoSourceError, match="本机、内网或保留地址"):
        fetch_authorized_video(
            "https://localhost/video.mp4",
            resolver=private_resolver,
        )
    with pytest.raises(VideoSourceError, match="非标准端口"):
        fetch_authorized_video(
            "https://cdn.example.com:8443/video.mp4",
            resolver=public_resolver,
        )


def test_final_redirect_target_is_validated_before_reading() -> None:
    def resolver(host: str, port: int, **kwargs):
        ip = "127.0.0.1" if host == "localhost" else "8.8.8.8"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    with pytest.raises(VideoSourceError, match="本机、内网或保留地址"):
        fetch_authorized_video(
            "https://cdn.example.com/video.mp4",
            resolver=resolver,
            open_url=lambda *args, **kwargs: FakeResponse(
                b"video",
                final_url="https://localhost/private.mp4",
            ),
        )


def test_direct_video_size_and_content_type_are_limited() -> None:
    with pytest.raises(VideoSourceError, match="超过 5 bytes"):
        fetch_authorized_video(
            "https://cdn.example.com/video.mp4",
            resolver=public_resolver,
            open_url=lambda *args, **kwargs: FakeResponse(
                b"123456",
                content_length="6",
            ),
            max_bytes=5,
        )

    with pytest.raises(VideoSourceError, match="不是可识别的视频"):
        fetch_authorized_video(
            "https://cdn.example.com/video.mp4",
            resolver=public_resolver,
            open_url=lambda *args, **kwargs: FakeResponse(
                b"<html>share page</html>",
                content_type="text/html",
            ),
        )


def test_direct_video_download_has_total_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter([0.0, 0.0, 61.0])
    monkeypatch.setattr(
        "src.services.video_source.monotonic",
        lambda: next(ticks),
    )

    with pytest.raises(VideoSourceError, match="下载超过 60 秒"):
        fetch_authorized_video(
            "https://cdn.example.com/video.mp4",
            resolver=public_resolver,
            open_url=lambda *args, **kwargs: FakeResponse(
                b"\x00\x00\x00\x18ftypisom-authorized-video",
            ),
        )


def test_direct_video_connection_failure_retries_only_once() -> None:
    attempts = 0

    def failing_open_url(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise URLError("connection unavailable")

    with pytest.raises(VideoSourceError, match="已自动重试一次"):
        fetch_authorized_video(
            "https://cdn.example.com/video.mp4",
            resolver=public_resolver,
            open_url=failing_open_url,
        )

    assert attempts == 2
