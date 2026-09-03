from types import SimpleNamespace

import httpx
import pytest

from src.adapters.douyin_parser import LocalDouyinBrowserParserClient
from src.adapters.platform_link_parser import (
    LocalPlatformLinkParserClient,
    ParsedPlatformMedia,
    PlatformLinkParserError,
    _xiaohongshu_needs_search_recovery,
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


def test_xiaohongshu_link_parser_uses_the_connected_browser():
    assert _client().capabilities_for(Platform.XIAOHONGSHU) == (True, None)


def test_xiaohongshu_complete_link_is_used_without_search_recovery():
    class _Context:
        @property
        def pages(self):
            raise AssertionError("complete signed link must not scan browser pages")

    link = parse_platform_share_text(
        "https://www.xiaohongshu.com/explore/targetnote"
        "?xsec_token=live-token&xsec_source=pc_search"
    )

    target_url = _client()._xiaohongshu_context_url(_Context(), link)

    assert target_url == link.share_url


def test_xiaohongshu_signed_link_does_not_fall_back_to_keyword_search():
    link = parse_platform_share_text(
        "https://www.xiaohongshu.com/explore/targetnote"
        "?xsec_token=live-token&xsec_source=pc_search"
    )

    assert _xiaohongshu_needs_search_recovery(link) is False


@pytest.mark.parametrize(
    "platform",
    [Platform.XIAOHONGSHU, Platform.KUAISHOU, Platform.BILIBILI],
)
def test_link_resolution_restarts_one_closed_persisted_browser_profile(platform):
    class _Provider:
        anonymous_only = False

        def __init__(self):
            self.running = False
            self.start_count = 0

        def session_status(self):
            return SimpleNamespace(
                running=self.running,
                ready_to_crawl=self.running,
                message="已恢复" if self.running else "浏览器已关闭",
            )

        def start_login_browser(self):
            self.start_count += 1
            self.running = True
            return self.session_status()

    provider = _Provider()
    client = LocalPlatformLinkParserClient(
        douyin_parser=LocalDouyinBrowserParserClient(enabled=True),
        platform_providers={platform: provider},
    )

    assert client._ensure_session_for_resolution(platform) == (True, None)
    assert provider.start_count == 1


def test_xiaohongshu_bare_link_recovers_tokenized_href_from_search_page():
    class _Locator:
        def __init__(self, hrefs):
            self.hrefs = hrefs

        def evaluate_all(self, _script):
            return self.hrefs

    class _Page:
        def locator(self, selector):
            if "/explore/targetnote" in selector:
                return _Locator(
                    [
                        "https://www.xiaohongshu.com/explore/othernote?xsec_token=wrong",
                        "https://www.xiaohongshu.com/explore/targetnote?xsec_token=live-token&xsec_source=pc_search",
                    ]
                )
            return _Locator([])

    context = SimpleNamespace(pages=[_Page()])
    link = parse_platform_share_text(
        "https://www.xiaohongshu.com/explore/targetnote"
    )

    recovered = _client()._xiaohongshu_context_url(context, link)

    assert recovered.endswith(
        "/targetnote?xsec_token=live-token&xsec_source=pc_search"
    )


def test_xiaohongshu_context_recovery_does_not_substitute_another_note():
    class _Locator:
        @staticmethod
        def evaluate_all(_script):
            return [
                "https://www.xiaohongshu.com/explore/othernote?xsec_token=wrong"
            ]

    class _Page:
        @staticmethod
        def locator(_selector):
            return _Locator()

    link = parse_platform_share_text(
        "https://www.xiaohongshu.com/explore/targetnote"
    )

    recovered = _client()._xiaohongshu_context_url(
        SimpleNamespace(pages=[_Page()]), link
    )

    assert recovered == link.share_url


def test_xiaohongshu_force_recovery_does_not_reuse_detail_page_token():
    search_url = "https://www.xiaohongshu.com/search_result/?keyword=test"

    class _Anchor:
        @staticmethod
        def evaluate_all(_script):
            return [
                "https://www.xiaohongshu.com/explore/targetnote"
                "?xsec_token=fresh-token&xsec_source=pc_search"
            ]

    class _DetailPage:
        url = (
            "https://www.xiaohongshu.com/explore/targetnote"
            "?xsec_token=expired-token&xsec_source=pc_search"
        )

        @staticmethod
        def locator(_selector):
            raise AssertionError("detail page must not be used for token recovery")

    class _Card:
        def __init__(self, page):
            self.page = page

        @staticmethod
        def count():
            return 1

        def click(self, *, timeout):
            assert timeout == 5_000
            self.page.url = (
                "https://www.xiaohongshu.com/explore/targetnote"
                "?xsec_token=fresh-token&xsec_source=pc_search"
            )

    class _Cards:
        def __init__(self, page):
            self.page = page

        def filter(self, *, has):
            assert isinstance(has, _Anchor)
            return _Card(self.page)

    class _SearchPage:
        url = search_url

        def locator(self, selector):
            if selector == "section.note-item":
                return _Cards(self)
            return _Anchor()

        @staticmethod
        def wait_for_timeout(timeout):
            assert timeout == 800

        def go_back(self, *, wait_until, timeout):
            assert wait_until == "domcontentloaded"
            assert timeout == 5_000
            self.url = search_url

    link = parse_platform_share_text(
        "https://www.xiaohongshu.com/explore/targetnote"
        "?xsec_token=expired-token"
    )

    recovered = _client()._xiaohongshu_context_url(
        SimpleNamespace(pages=[_SearchPage(), _DetailPage()]),
        link,
        force_search_context=True,
    )

    assert "xsec_token=fresh-token" in recovered
    assert "expired-token" not in recovered


def test_xiaohongshu_existing_detail_page_is_reused_without_navigation():
    target_page = SimpleNamespace(
        url=(
            "https://www.xiaohongshu.com/explore/targetnote"
            "?xsec_token=current-token&xsec_source=pc_search"
        )
    )
    other_page = SimpleNamespace(
        url=(
            "https://www.xiaohongshu.com/explore/othernote"
            "?xsec_token=other-token&xsec_source=pc_search"
        )
    )
    search_page = SimpleNamespace(
        url="https://www.xiaohongshu.com/search_result/?keyword=test"
    )
    link = parse_platform_share_text(
        "https://www.xiaohongshu.com/explore/targetnote"
    )

    matched = _client()._existing_xiaohongshu_detail_page(
        SimpleNamespace(pages=[target_page, search_page, other_page]), link
    )

    assert matched is not None
    assert matched[0] is target_page
    assert matched[1].work_id == "targetnote"


def test_xiaohongshu_search_card_open_stays_on_generated_detail_page():
    class _Anchor:
        pass

    class _Card:
        def __init__(self, page):
            self.page = page

        @staticmethod
        def count():
            return 1

        def click(self, *, timeout):
            assert timeout == 5_000
            self.page.url = (
                "https://www.xiaohongshu.com/explore/targetnote"
                "?xsec_token=generated-token&xsec_source=pc_search"
            )

    class _Cards:
        def __init__(self, page):
            self.page = page

        def filter(self, *, has):
            assert isinstance(has, _Anchor)
            return _Card(self.page)

    class _SearchPage:
        def __init__(self):
            self.url = "https://www.xiaohongshu.com/search_result/?keyword=test"
            self.went_back = False

        def locator(self, selector):
            if selector == "section.note-item":
                return _Cards(self)
            return _Anchor()

        @staticmethod
        def wait_for_timeout(timeout):
            assert timeout == 800

        def go_back(self, **_kwargs):
            self.went_back = True

    page = _SearchPage()
    link = parse_platform_share_text(
        "https://www.xiaohongshu.com/explore/targetnote"
    )

    opened = _client()._open_xiaohongshu_search_result(
        SimpleNamespace(pages=[page]), link
    )

    assert opened is not None
    assert opened[0] is page
    assert opened[1].work_id == "targetnote"
    assert page.went_back is False
    assert "/explore/targetnote" in page.url


def test_xiaohongshu_next_card_restores_search_history_without_new_scan():
    search_url = "https://www.xiaohongshu.com/search_result/?keyword=test"

    class _Anchor:
        pass

    class _Card:
        def __init__(self, page):
            self.page = page

        @staticmethod
        def count():
            return 1

        def click(self, *, timeout):
            assert timeout == 5_000
            self.page.url = (
                "https://www.xiaohongshu.com/explore/targetnote"
                "?xsec_token=generated-token&xsec_source=pc_search"
            )

    class _Cards:
        def __init__(self, page):
            self.page = page

        def filter(self, *, has):
            assert isinstance(has, _Anchor)
            return _Card(self.page)

    class _DetailPage:
        def __init__(self):
            self.url = (
                "https://www.xiaohongshu.com/explore/previousnote"
                "?xsec_token=previous-token&xsec_source=pc_search"
            )
            self.history_restored = 0

        def go_back(self, *, wait_until, timeout):
            assert wait_until == "domcontentloaded"
            assert timeout == 5_000
            self.history_restored += 1
            self.url = search_url

        @staticmethod
        def wait_for_timeout(timeout):
            assert timeout in {250, 800}

        def locator(self, selector):
            if selector == "section.note-item":
                return _Cards(self)
            return _Anchor()

    class _Context:
        def __init__(self, page):
            self.pages = [page]

        @staticmethod
        def new_page():
            raise AssertionError("browser history should avoid a fresh search page")

    page = _DetailPage()
    link = parse_platform_share_text(
        "https://www.xiaohongshu.com/explore/targetnote"
    )

    opened = _client()._open_xiaohongshu_search_result(
        _Context(page), link, search_keyword="test"
    )

    assert opened is not None
    assert opened[0] is page
    assert opened[1].work_id == "targetnote"
    assert page.history_restored == 1
    assert "/explore/targetnote" in page.url


def test_xiaohongshu_opened_video_media_is_reused_by_transcription():
    class _Page:
        @staticmethod
        def evaluate(script):
            assert script == "navigator.userAgent"
            return "Test Browser"

        @staticmethod
        def title():
            return "目标作品 - 小红书"

    client = _client()
    link = parse_platform_share_text(
        "https://www.xiaohongshu.com/explore/targetnote"
        "?xsec_token=generated-token&xsec_source=pc_search"
    )
    client._cache_xiaohongshu_page_media(
        _Page(),
        link,
        {
            "media_url": "https://sns-video.example/target.mp4",
            "title": "目标作品",
        },
    )

    media = client.resolve("https://www.xiaohongshu.com/explore/targetnote")

    assert media.work_id == "targetnote"
    assert media.media_url == "https://sns-video.example/target.mp4"
    assert media.browser_user_agent == "Test Browser"


def test_xiaohongshu_media_is_read_through_authorized_browser_context():
    calls: list[dict[str, object]] = []

    class _Response:
        status = 206
        headers = {"content-type": "video/mp4", "content-length": "4"}

        @staticmethod
        def body():
            return b"video"

        @staticmethod
        def dispose():
            pass

    class _Request:
        def get(self, url, **kwargs):
            calls.append({"url": url, **kwargs})
            return _Response()

    context = SimpleNamespace(request=_Request())
    content, media_type = _client()._fetch_media_with_browser_context(
        context,
        "https://sns-video.example/target.mp4?sig=redacted",
        "https://www.xiaohongshu.com/explore/targetnote?xsec_token=redacted",
        "Test Browser",
    )

    assert content == b"video"
    assert media_type == "video/mp4"
    assert calls[0]["headers"] == {
        "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.1",
        "Referer": "https://www.xiaohongshu.com/explore/targetnote?xsec_token=redacted",
        "User-Agent": "Test Browser",
    }


def test_xiaohongshu_context_recovery_clicks_visible_card_for_platform_token():
    search_url = "https://www.xiaohongshu.com/search_result/?keyword=test"

    class _Anchor:
        @staticmethod
        def evaluate_all(_script):
            return ["https://www.xiaohongshu.com/explore/targetnote"]

    class _Card:
        @staticmethod
        def count():
            return 1

        def __init__(self, page):
            self.page = page

        def click(self, *, timeout):
            assert timeout == 5_000
            self.page.url = (
                "https://www.xiaohongshu.com/explore/targetnote"
                "?xsec_token=live-token&xsec_source=pc_search"
            )

    class _Cards:
        def __init__(self, page):
            self.page = page

        def filter(self, *, has):
            assert isinstance(has, _Anchor)
            return _Card(self.page)

    class _Page:
        def __init__(self):
            self.url = search_url

        def locator(self, selector):
            if selector == "section.note-item":
                return _Cards(self)
            return _Anchor()

        @staticmethod
        def wait_for_timeout(timeout):
            assert timeout == 800

        def go_back(self, *, wait_until, timeout):
            assert wait_until == "domcontentloaded"
            assert timeout == 5_000
            self.url = search_url

    page = _Page()
    link = parse_platform_share_text(
        "https://www.xiaohongshu.com/explore/targetnote"
    )

    recovered = _client()._xiaohongshu_context_url(
        SimpleNamespace(pages=[page]), link
    )

    assert "xsec_token=live-token" in recovered
    assert page.url == search_url


def test_xiaohongshu_context_recovery_starts_at_top_when_search_page_is_scrolled():
    search_url = "https://www.xiaohongshu.com/search_result/?keyword=test"

    class _Anchor:
        def __init__(self, page):
            self.page = page

        def evaluate_all(self, _script):
            if self.page.at_top:
                return ["https://www.xiaohongshu.com/explore/targetnote"]
            return []

    class _Card:
        def __init__(self, page):
            self.page = page

        @staticmethod
        def count():
            return 1

        def click(self, *, timeout):
            assert timeout == 5_000
            self.page.url = (
                "https://www.xiaohongshu.com/explore/targetnote"
                "?xsec_token=live-token"
            )

    class _Cards:
        def __init__(self, page):
            self.page = page

        def filter(self, *, has):
            assert isinstance(has, _Anchor)
            return _Card(self.page)

    class _Page:
        def __init__(self):
            self.url = search_url
            self.at_top = False
            self.scroll_rounds = 0

        def locator(self, selector):
            if selector == "section.note-item":
                return _Cards(self)
            return _Anchor(self)

        def evaluate(self, script):
            if "scrollTo" in script:
                self.at_top = True
            else:
                self.scroll_rounds += 1

        @staticmethod
        def wait_for_timeout(timeout):
            assert timeout in {350, 800}

        def go_back(self, *, wait_until, timeout):
            assert wait_until == "domcontentloaded"
            assert timeout == 5_000
            self.url = search_url

    page = _Page()
    link = parse_platform_share_text(
        "https://www.xiaohongshu.com/explore/targetnote"
    )

    recovered = _client()._xiaohongshu_context_url(
        SimpleNamespace(pages=[page]), link
    )

    assert "xsec_token=live-token" in recovered
    assert page.scroll_rounds == 0


def test_xiaohongshu_300031_is_actionable():
    class _Body:
        @staticmethod
        def inner_text(*, timeout):
            del timeout
            return ""

    class _Page:
        url = "https://www.xiaohongshu.com/404?error_code=300031"

        @staticmethod
        def locator(selector):
            assert selector == "body"
            return _Body()

    message = _client()._xiaohongshu_access_error(_Page())

    assert message is not None
    assert "当前无法浏览" in message
    assert "上传已获授权的视频" in message


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


def test_xiaohongshu_page_state_extracts_xhscdn_mp4_instead_of_blob():
    captured: dict[str, str] = {}

    class _Page:
        @staticmethod
        def evaluate(_script):
            return {
                "media_urls": [
                    "blob:https://www.xiaohongshu.com/opaque-player",
                    "https://sns-video-ak.xhscdn.com/stream/1/demo.mp4",
                ],
                "titles": ["页面标题"],
            }

    _client()._capture_xiaohongshu_page_state(_Page(), captured)

    assert captured == {
        "media_url": "https://sns-video-ak.xhscdn.com/stream/1/demo.mp4",
        "title": "页面标题",
    }


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


def test_bilibili_public_api_prefers_standard_https_backup_port():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/view"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "bvid": "BV1gZ8e64EYM",
                        "cid": 41215197494,
                        "title": "标准端口回退",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "dash": {
                        "audio": [
                            {
                                "baseUrl": "https://mcdn.bilivideo.cn:8082/audio.m4s",
                                "backupUrl": [
                                    "https://upos-sz-mirrorcos.bilivideo.com/audio.m4s"
                                ],
                            }
                        ]
                    }
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

    media = client.resolve("https://www.bilibili.com/video/BV1gZ8e64EYM/")

    assert media.media_url == "https://upos-sz-mirrorcos.bilivideo.com/audio.m4s"


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
