from types import SimpleNamespace

import pytest

from src.adapters.douyin_parser import LocalDouyinBrowserParserClient
from src.adapters.platform_link_parser import (
    LocalPlatformLinkParserClient,
    PlatformLinkParserError,
    parse_platform_share_text,
)
from src.models import Platform


def _client() -> LocalPlatformLinkParserClient:
    ready_provider = SimpleNamespace(
        session_status=lambda: SimpleNamespace(
            ready_to_crawl=True,
            message="浏览器已连接。",
        )
    )
    return LocalPlatformLinkParserClient(
        douyin_parser=LocalDouyinBrowserParserClient(enabled=True),
        platform_providers={
            Platform.XIAOHONGSHU: ready_provider,
            Platform.KUAISHOU: ready_provider,
            Platform.BILIBILI: ready_provider,
        },
    )


@pytest.mark.parametrize(
    ("url", "platform", "work_id"),
    [
        (
            "https://www.xiaohongshu.com/explore/66abc123",
            Platform.XIAOHONGSHU,
            "66abc123",
        ),
        (
            "https://www.kuaishou.com/short-video/3xabc_123",
            Platform.KUAISHOU,
            "3xabc_123",
        ),
        (
            "https://www.bilibili.com/video/BV1Test2026",
            Platform.BILIBILI,
            "BV1Test2026",
        ),
        ("https://b23.tv/abcDEF", Platform.BILIBILI, None),
    ],
)
def test_platform_share_link_is_strictly_classified(url, platform, work_id):
    parsed = parse_platform_share_text(f"复制链接 {url}")

    assert parsed.platform == platform
    assert parsed.share_url == url
    assert parsed.work_id == work_id


def test_wechat_channels_link_keeps_the_manual_upload_boundary():
    with pytest.raises(PlatformLinkParserError, match="微信客户端内部"):
        parse_platform_share_text("https://channels.weixin.qq.com/platform")


def test_xiaohongshu_payload_extracts_visible_video_stream():
    captured: dict[str, str] = {}

    _client()._capture_media_payload(
        {
            "data": {
                "note": {
                    "title": "餐饮获客案例",
                    "video": {
                        "media": {
                            "stream": {
                                "h264": [
                                    {
                                        "master_url": (
                                            "https://sns-video.example/video.mp4"
                                        )
                                    }
                                ]
                            }
                        }
                    },
                }
            }
        },
        Platform.XIAOHONGSHU,
        captured,
    )

    assert captured["title"] == "餐饮获客案例"
    assert captured["media_url"] == "https://sns-video.example/video.mp4"


def test_kuaishou_payload_extracts_photo_url():
    captured: dict[str, str] = {}

    _client()._capture_media_payload(
        {
            "data": {
                "photo": {
                    "caption": "获客技巧",
                    "photoUrl": "https://video.kuaishou.example/play.mp4",
                }
            }
        },
        Platform.KUAISHOU,
        captured,
    )

    assert captured["title"] == "获客技巧"
    assert captured["media_url"] == "https://video.kuaishou.example/play.mp4"


def test_bilibili_payload_prefers_progressive_stream_then_dash_audio():
    captured: dict[str, str] = {}
    client = _client()

    client._capture_media_payload(
        {
            "data": {
                "durl": [{"url": "https://upos.example/progressive.mp4"}],
                "dash": {
                    "audio": [{"baseUrl": "https://upos.example/audio.m4s"}]
                },
            }
        },
        Platform.BILIBILI,
        captured,
    )

    assert captured["media_url"] == "https://upos.example/progressive.mp4"

    captured.clear()
    client._capture_media_payload(
        {
            "data": {
                "dash": {
                    "audio": [{"baseUrl": "https://upos.example/audio.m4s"}]
                }
            }
        },
        Platform.BILIBILI,
        captured,
    )

    assert captured["media_url"] == "https://upos.example/audio.m4s"


def test_platform_capability_requires_the_corresponding_browser_session():
    provider = SimpleNamespace(
        session_status=lambda: SimpleNamespace(
            ready_to_crawl=False,
            message="请先连接B站专用浏览器。",
        )
    )
    client = LocalPlatformLinkParserClient(
        douyin_parser=LocalDouyinBrowserParserClient(enabled=True),
        platform_providers={Platform.BILIBILI: provider},
    )

    enabled, message = client.capabilities_for(Platform.BILIBILI)

    assert enabled is False
    assert message == "请先连接B站专用浏览器。"
