from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from time import monotonic
from typing import Any, Mapping
from urllib.error import URLError
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.retry import ExternalServiceError, RetryPolicy, retry_with_policy
from src.services.transcription import MAX_MEDIA_BYTES

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov"}
ALLOWED_CONTENT_TYPES = {
    "",
    "application/octet-stream",
    "audio/mp4",
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


def _validate_direct_video_url(
    url: str,
    resolver: Resolver,
    *,
    require_extension: bool = True,
) -> str:
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
    if require_extension and extension not in ALLOWED_VIDEO_EXTENSIONS:
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
    def __init__(self, resolver: Resolver, *, require_extension: bool):
        super().__init__()
        self._resolver = resolver
        self._require_extension = require_extension

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_direct_video_url(
            newurl,
            self._resolver,
            require_extension=self._require_extension,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_open_url(resolver: Resolver, *, require_extension: bool) -> OpenUrl:
    return build_opener(
        _ValidatingRedirectHandler(resolver, require_extension=require_extension)
    ).open


def fetch_authorized_video(
    url: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
    open_url: OpenUrl | None = None,
    timeout_seconds: float = 15,
    max_bytes: int = MAX_MEDIA_BYTES,
    max_elapsed_seconds: float = 60,
    require_extension: bool = True,
    fallback_name: str = "provider-video.mp4",
    request_headers: Mapping[str, str] | None = None,
    range_bytes: int | None = None,
) -> DirectVideo:
    """Read an authorized public direct video URL without persisting the URL."""

    normalized_url = _validate_direct_video_url(
        url,
        resolver,
        require_extension=require_extension,
    )
    max_size_label = (
        f"{max_bytes // (1024 * 1024)}MB"
        if max_bytes >= 1024 * 1024
        else f"{max_bytes} bytes"
    )
    max_elapsed_label = int(max_elapsed_seconds)
    opener = open_url or _default_open_url(
        resolver,
        require_extension=require_extension,
    )

    def fetch_once() -> DirectVideo:
        headers = {
            "Accept": "video/mp4,audio/mp4,video/quicktime,application/octet-stream",
            "User-Agent": "video-transcription-mvp/1.0",
        }
        if request_headers:
            for key in ("Referer", "User-Agent"):
                value = request_headers.get(key)
                if isinstance(value, str) and value.strip():
                    headers[key] = value.strip()
        if range_bytes is not None:
            headers["Range"] = f"bytes=0-{max(1, range_bytes) - 1}"
        request = Request(
            normalized_url,
            headers=headers,
        )
        with opener(request, timeout=timeout_seconds) as response:
            final_url = _validate_direct_video_url(
                response.geturl(),
                resolver,
                require_extension=require_extension,
            )
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
                    raise VideoSourceError(
                        f"视频文件超过 {max_size_label}，请压缩后再试。"
                    )

            chunks: list[bytes] = []
            total = 0
            started = monotonic()
            while True:
                if monotonic() - started > max_elapsed_seconds:
                    raise VideoSourceError(
                        f"视频文件下载超过 {max_elapsed_label} 秒，"
                        "请换短视频或手动上传 MP4/MOV。"
                    )
                chunk = response.read(min(1024 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise VideoSourceError(
                        f"视频文件超过 {max_size_label}，请压缩后再试。"
                    )
                if monotonic() - started > max_elapsed_seconds:
                    raise VideoSourceError(
                        f"视频文件下载超过 {max_elapsed_label} 秒，"
                        "请换短视频或手动上传 MP4/MOV。"
                    )

            content = b"".join(chunks)
            if not content:
                raise VideoSourceError("视频直链返回了空文件。")
            parsed = urlparse(final_url)
            name = PurePosixPath(unquote(parsed.path)).name or fallback_name
            extension = PurePosixPath(name).suffix.casefold()
            if extension not in ALLOWED_VIDEO_EXTENSIONS:
                name = fallback_name
                extension = ".mp4"
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
