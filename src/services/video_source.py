from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.error import URLError
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.retry import ExternalServiceError, RetryPolicy, retry_with_policy
from src.services.transcription import MAX_MEDIA_BYTES

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov"}
ALLOWED_CONTENT_TYPES = {
    "",
    "application/octet-stream",
    "video/mp4",
    "video/quicktime",
}


class VideoSourceError(RuntimeError):
    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


@dataclass(frozen=True)
class DirectVideo:
    name: str
    media_type: str
    content: bytes


Resolver = Callable[..., list[tuple[Any, ...]]]
OpenUrl = Callable[..., Any]


def _validate_direct_video_url(url: str, resolver: Resolver) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise VideoSourceError("视频直链必须是完整的 HTTPS 地址。")
    try:
        port = parsed.port
    except ValueError as exc:
        raise VideoSourceError("视频直链端口格式无效。") from exc
    if parsed.username or parsed.password or port not in {None, 443}:
        raise VideoSourceError("视频直链不能包含账号信息或非标准端口。")

    extension = PurePosixPath(unquote(parsed.path)).suffix.casefold()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise VideoSourceError(
            "当前只支持直接指向 MP4/MOV 文件的 HTTPS 视频直链；"
            "平台分享页暂不支持，请改为上传视频文件。"
        )

    try:
        addresses = resolver(
            parsed.hostname,
            443,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise VideoSourceError("无法解析视频直链地址，请检查链接后再试。") from exc
    if not addresses:
        raise VideoSourceError("无法解析视频直链地址，请检查链接后再试。")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except (IndexError, TypeError, ValueError) as exc:
            raise VideoSourceError("视频直链地址解析结果无效。") from exc
        if not ip.is_global:
            raise VideoSourceError("视频直链不能指向本机、内网或保留地址。")
    return parsed.geturl()


class _ValidatingRedirectHandler(HTTPRedirectHandler):
    def __init__(self, resolver: Resolver):
        super().__init__()
        self._resolver = resolver

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_direct_video_url(newurl, self._resolver)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_open_url(resolver: Resolver) -> OpenUrl:
    return build_opener(_ValidatingRedirectHandler(resolver)).open


def fetch_authorized_video(
    url: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
    open_url: OpenUrl | None = None,
    timeout_seconds: float = 15,
    max_bytes: int = MAX_MEDIA_BYTES,
) -> DirectVideo:
    """Read an authorized public direct video URL without persisting the URL."""

    normalized_url = _validate_direct_video_url(url, resolver)
    opener = open_url or _default_open_url(resolver)

    def fetch_once() -> DirectVideo:
        request = Request(
            normalized_url,
            headers={
                "Accept": "video/mp4,video/quicktime,application/octet-stream",
                "User-Agent": "video-transcription-mvp/1.0",
            },
        )
        with opener(request, timeout=timeout_seconds) as response:
            final_url = _validate_direct_video_url(response.geturl(), resolver)
            content_type = response.headers.get("Content-Type", "")
            media_type = content_type.split(";", 1)[0].strip().casefold()
            if media_type not in ALLOWED_CONTENT_TYPES:
                raise VideoSourceError(
                    "该地址返回的不是可识别的视频文件，请改为上传 MP4/MOV。"
                )

            raw_length = response.headers.get("Content-Length")
            if raw_length:
                try:
                    content_length = int(raw_length)
                except ValueError:
                    content_length = 0
                if content_length > max_bytes:
                    raise VideoSourceError("视频文件超过 50MB，请压缩后再试。")

            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(1024 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise VideoSourceError("视频文件超过 50MB，请压缩后再试。")

            content = b"".join(chunks)
            if not content:
                raise VideoSourceError("视频直链返回了空文件。")
            parsed = urlparse(final_url)
            name = PurePosixPath(unquote(parsed.path)).name
            extension = PurePosixPath(name).suffix.casefold()
            fallback_type = "video/mp4" if extension == ".mp4" else "video/quicktime"
            return DirectVideo(
                name=name,
                media_type=media_type or fallback_type,
                content=content,
            )

    try:
        return retry_with_policy(
            fetch_once,
            policy=RetryPolicy(max_attempts=2, base_delay=0.5, max_delay=5.0),
            retry_for=(ConnectionError, TimeoutError, URLError, OSError),
        )
    except ExternalServiceError as exc:
        raise VideoSourceError(
            "视频直链读取失败，已自动重试一次，请检查链接后再试。"
        ) from exc
