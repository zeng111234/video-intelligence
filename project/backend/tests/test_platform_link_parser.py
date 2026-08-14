from types import SimpleNamespace

import httpx
import pytest

from src.adapters.douyin_parser import LocalDouyinBrowserParserClient
from src.adapters.platform_link_parser import (
    LocalPlatformLinkParserClient,
    ParsedPlatformMedia,
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


def test_kuaishou_payload_only_accepts_the_requested_work():
    captured: dict[str, str] = {}

    _client()._capture_media_payload(
        {
            "data": {
                "feed": [
                    {
                        "photo": {
                            "id": "wrong-work",
                            "caption": "推荐流视频",
                            "photoUrl": "https://video.kuaishou.example/wrong.mp4",
                        }
                    },
                    {
                        "photo": {
                            "id": "target-work",
                            "caption": "贴标机",
                            "photoUrl": "https://video.kuaishou.example/target.mp4",
                        }
                    },
                ]
            }
        },
        Platform.KUAISHOU,
        captured,
        expected_work_id="target-work",
    )

    assert captured == {
        "media_url": "https://video.kuaishou.example/target.mp4",
        "work_id": "target-work",
        "title": "贴标机",
    }


def test_kuaishou_payload_rejects_media_when_requested_work_is_missing():
    captured: dict[str, str] = {}

    _client()._capture_media_payload(
        {
            "data": {
                "photo": {
                    "id": "wrong-work",
                    "caption": "推荐流视频",
                    "photoUrl": "https://video.kuaishou.example/wrong.mp4",
                }
            }
        },
        Platform.KUAISHOU,
        captured,
        expected_work_id="target-work",
    )

    assert captured == {}


def test_kuaishou_detail_page_accepts_the_exact_work_without_a_page_title():
    captured: dict[str, str] = {}

    accepted = _client()._capture_kuaishou_page_video(
        captured,
        expected_work_id="target-work",
        media_url="https://video.kuaishou.example/target.mp4",
        title="",
    )

    assert accepted is True
    assert captured == {
        "media_url": "https://video.kuaishou.example/target.mp4",
        "work_id": "target-work",
    }


def test_kuaishou_detail_page_waits_for_delayed_current_source():
    class _Video:
        def evaluate(self, _script):
            return "https://video.kuaishou.example/delayed-target.mp4"

    class _Videos:
        first = _Video()

        @staticmethod
        def count():
            return 1

    class _Page:
        def __init__(self):
            self.waited = False

        def wait_for_function(self, _script, *, timeout):
            assert timeout == 12_000
            self.waited = True

        @staticmethod
        def locator(selector):
            assert selector == "video"
            return _Videos()

    page = _Page()

    media_url = _client()._wait_for_kuaishou_page_video_source(
        page,
        timeout_ms=35_000,
    )

    assert page.waited is True
    assert media_url == "https://video.kuaishou.example/delayed-target.mp4"


@pytest.mark.parametrize(
    ("work_id", "media_url"),
    [
        (None, "https://video.kuaishou.example/target.mp4"),
        ("target-work", "http://video.kuaishou.example/target.mp4"),
        ("target-work", "https://video.kuaishou.example/target.m3u8"),
    ],
)
def test_kuaishou_detail_page_keeps_unsafe_streams_blocked(work_id, media_url):
    captured: dict[str, str] = {}

    accepted = _client()._capture_kuaishou_page_video(
        captured,
        expected_work_id=work_id,
        media_url=media_url,
        title="目标作品",
    )

    assert accepted is False
    assert captured == {}


def test_bilibili_payload_prefers_progressive_stream_then_dash_audio():
    captured: dict[str, str] = {}
    client = _client()

    client._capture_media_payload(
        {
            "data": {
                "durl": [{"url": "https://upos.example/progressive.mp4"}],
                "dash": {"audio": [{"baseUrl": "https://upos.example/audio.m4s"}]},
            }
        },
        Platform.BILIBILI,
        captured,
    )

    assert captured["media_url"] == "https://upos.example/progressive.mp4"

    captured.clear()
    client._capture_media_payload(
        {"data": {"dash": {"audio": [{"baseUrl": "https://upos.example/audio.m4s"}]}}},
        Platform.BILIBILI,
        captured,
    )

    assert captured["media_url"] == "https://upos.example/audio.m4s"


def test_bilibili_audio_is_not_replaced_by_a_video_only_page_stream():
    client = _client()

    assert client._may_capture_generic_video_response(Platform.BILIBILI) is False
    assert client._may_capture_generic_video_response(Platform.KUAISHOU) is False
    assert client._may_capture_generic_video_response(Platform.XIAOHONGSHU) is True


def test_bilibili_public_api_resolves_the_target_audio_stream():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/view"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "bvid": "BV1bZ3t64EvY",
                        "cid": 40448491583,
                        "title": "外贸获客讲解",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "dash": {"audio": [{"baseUrl": "https://upos.example/audio.m4s"}]}
                },
            },
        )

    client = LocalPlatformLinkParserClient(
        douyin_parser=LocalDouyinBrowserParserClient(enabled=True),
        platform_providers={},
        http_client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )

    media = client.resolve("https://www.bilibili.com/video/BV1bZ3t64EvY")

    assert media.work_id == "BV1bZ3t64EvY"
    assert media.title == "外贸获客讲解"
    assert media.media_url == "https://upos.example/audio.m4s"
    assert media.media_request_headers["Referer"].endswith("/BV1bZ3t64EvY")


def test_connected_browser_media_uses_the_real_browser_user_agent():
    media = ParsedPlatformMedia(
        platform=Platform.KUAISHOU,
        share_url="https://www.kuaishou.com/short-video/target-work",
        work_id="target-work",
        media_url="https://video.kuaishou.example/target.mp4",
        title="快手目标作品",
        browser_user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        ),
    )

    assert "Chrome/140.0.0.0" in media.media_request_headers["User-Agent"]


def test_browser_user_agent_rejects_header_injection():
    assert (
        LocalPlatformLinkParserClient._safe_browser_user_agent(
            "Chrome/140\r\nX-Test: 1"
        )
        is None
    )


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
