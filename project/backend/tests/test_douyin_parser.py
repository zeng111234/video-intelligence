from src.adapters.douyin_parser import (
    LocalDouyinBrowserParserClient,
    ParsedDouyinMedia,
    parse_douyin_share_text,
)


def test_share_text_extracts_short_link_and_modal_id():
    short = parse_douyin_share_text("复制这条 https://v.douyin.com/abcDEF/ 到抖音")
    assert short.share_url == "https://v.douyin.com/abcDEF/"
    assert short.work_id is None

    work = parse_douyin_share_text("https://www.douyin.com/video/7351002003004005000?x=1")
    assert work.work_id == "7351002003004005000"

    search = parse_douyin_share_text(
        "https://www.douyin.com/jingxuan/search/%E5%8F%A3%E6%92%AD"
        "?modal_id=7583741167041367359&type=general"
    )
    assert LocalDouyinBrowserParserClient._target_url(search) == (
        "https://www.douyin.com/video/7583741167041367359"
    )


def test_parser_is_disabled_without_explicit_flag():
    client = LocalDouyinBrowserParserClient(enabled=False)
    available, message = client.capabilities()
    assert available is False
    assert "DOUYIN_LOCAL_BROWSER_ENABLED=false" in (message or "")


def test_parser_extracts_media_from_normal_detail_payload(monkeypatch):
    client = LocalDouyinBrowserParserClient(enabled=True)
    captured: dict[str, str] = {}
    client._capture_detail_payload({
        "aweme_detail": {"aweme_id": "7351002003004005000", "desc": "测试标题", "video": {"play_addr": {"url_list": ["https://media.example/video.mp4"]}}}
    }, captured)
    assert captured == {
        "work_id": "7351002003004005000",
        "title": "测试标题",
        "media_url": "https://media.example/video.mp4",
    }

    monkeypatch.setattr(client, "capabilities", lambda: (True, None))
    monkeypatch.setattr(client, "_resolve_once", lambda _link: ParsedDouyinMedia(
        "https://www.douyin.com/video/7351002003004005000",
        "7351002003004005000",
        "https://media.example/video.mp4",
        "测试标题",
    ))
    item = client.resolve("https://www.douyin.com/video/7351002003004005000")
    assert item.work_id == "7351002003004005000"
    assert item.title == "测试标题"
    assert item.media_url == "https://media.example/video.mp4"
    assert item.media_request_headers["Referer"] == "https://www.douyin.com/"


def test_router_payload_keeps_the_public_play_address_unchanged():
    captured: dict[str, str] = {}
    LocalDouyinBrowserParserClient._capture_router_payload(
        {
            "loaderData": {
                "video_(id)/page": {
                    "videoInfoRes": {
                        "item_list": [
                            {
                                "aweme_id": "7351002003004005000",
                                "desc": "公开页面标题",
                                "video": {
                                    "play_addr": {
                                        "url_list": [
                                            "https://media.example/aweme/v1/playwm/?video_id=1"
                                        ]
                                    }
                                },
                            }
                        ]
                    }
                }
            }
        },
        captured,
    )
    assert captured == {
        "work_id": "7351002003004005000",
        "title": "公开页面标题",
        "media_url": "https://media.example/aweme/v1/playwm/?video_id=1",
    }


def test_router_payload_only_accepts_the_modal_target():
    captured: dict[str, str] = {}
    LocalDouyinBrowserParserClient._capture_router_payload(
        {
            "loaderData": {
                "search/page": {
                    "videoInfoRes": {
                        "item_list": [
                            {
                                "aweme_id": "1111111111111111111",
                                "desc": "搜索页里的其他作品",
                                "video": {
                                    "play_addr": {
                                        "url_list": ["https://media.example/wrong.mp4"]
                                    }
                                },
                            },
                            {
                                "aweme_id": "7583741167041367359",
                                "desc": "用户点开的作品",
                                "video": {
                                    "play_addr": {
                                        "url_list": ["https://media.example/target.mp4"]
                                    }
                                },
                            },
                        ]
                    }
                }
            }
        },
        captured,
        expected_work_id="7583741167041367359",
    )

    assert captured == {
        "work_id": "7583741167041367359",
        "title": "用户点开的作品",
        "media_url": "https://media.example/target.mp4",
    }
