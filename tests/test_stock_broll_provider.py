import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from src.services.stock_broll_provider import StockBrollProvider
from src.services.stock_broll_provider import local_broll_is_real


class _Response:
    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.data


def test_missing_keys_stays_offline_without_network(tmp_path: Path):
    def opener(*_args, **_kwargs):
        raise AssertionError("missing API key must not call the network")

    result = StockBrollProvider(tmp_path, opener=opener).search_and_cache("客户关系")
    assert result.status == "unavailable"
    assert result.reason == "missing_api_key"
    assert result.attempts == 0


def test_pexels_result_records_license_hash_duration_ratio_and_cache(tmp_path: Path):
    api_payload = {
        "videos": [
            {
                "id": 42,
                "url": "https://www.pexels.com/video/42/",
                "duration": 5,
                "video_files": [
                    {
                        "link": "https://cdn.example.test/42.mp4",
                        "width": 720,
                        "height": 1280,
                    }
                ],
            }
        ]
    }
    calls = []

    def opener(request, timeout=0):
        calls.append(request.full_url)
        if request.full_url.startswith("https://api.pexels.com/"):
            return _Response(json.dumps(api_payload).encode())
        return _Response(b"x" * 2048)

    def runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "format": {"duration": "5.0"},
                    "streams": [
                        {"codec_type": "video", "width": 720, "height": 1280}
                    ],
                }
            ),
            stderr="",
        )

    result = StockBrollProvider(
        tmp_path, pexels_key="test-key", opener=opener, runner=runner
    ).search_and_cache("客户关系")

    assert result.status == "ready"
    assert result.provider == "pexels"
    assert len(result.items) == 1
    item = result.items[0]
    assert item["asset_id"].startswith("broll-")
    assert len(item["asset_id"]) == len("broll-") + 10
    assert item["license_name"] == "Pexels License"
    assert item["asset_origin"] == "stock_video_asset"
    assert item["publish_licensed"] is True
    assert item["sha256"]
    assert item["duration_seconds"] == 5.0
    assert item["aspect_ratio"] == 0.5625
    assert item["semantic_query"] == "客户关系"
    assert item["keywords"] == ["客户关系"]
    assert item["domestic_context"] == "international"
    assert item["domestic_scene"] == ""
    assert Path(item["cache_path"]).is_file()
    assert Path(item["cache_path"]).with_suffix(".json").is_file()
    assert any("api.pexels.com" in url for url in calls)


def test_provider_failure_is_safe_after_one_retry(tmp_path: Path):
    calls = 0

    def opener(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise OSError("offline")

    result = StockBrollProvider(
        tmp_path, pexels_key="test-key", opener=opener
    ).search_and_cache("客户关系")
    assert result.status == "unavailable"
    assert result.attempts == 2
    assert calls == 2


def test_provider_failure_reason_redacts_api_key(tmp_path: Path):
    secret = "pexels-secret-value"

    def opener(*_args, **_kwargs):
        raise OSError(f"request failed for Authorization={secret}")

    result = StockBrollProvider(
        tmp_path, pexels_key=secret, opener=opener
    ).search_and_cache("客户关系")

    assert result.status == "unavailable"
    assert result.attempts == 2
    assert secret not in (result.reason or "")
    assert "[已隐藏]" in (result.reason or "")


def test_matching_provider_cache_is_reused_without_network(tmp_path: Path):
    cached_media = tmp_path / "broll-cached.mp4"
    cached_media.write_bytes(b"cached-media")
    metadata = {
        "asset_id": "broll-cached",
        "kind": "broll",
        "cache_path": str(cached_media),
        "media_type": "video/mp4",
        "duration_seconds": 4.2,
        "source_provider": "pexels",
        "search_query": "phone mount in car",
        "authorization_status": "confirmed",
        "publish_licensed": True,
    }
    (tmp_path / "broll-cached.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    def opener(*_args, **_kwargs):
        raise AssertionError("matching provider cache must not call the network")

    result = StockBrollProvider(
        tmp_path, pexels_key="test-key", opener=opener
    ).search_and_cache("phone mount in car", max_results=1)
    assert result.status == "ready"
    assert result.reason == "cache_reused"
    assert result.attempts == 0
    assert result.items[0]["asset_id"] == "broll-cached"


def test_provider_ranks_metadata_and_downloads_only_top_two(tmp_path: Path):
    payload = {
        "videos": [
            {
                "id": 1,
                "title": "Wildlife lake landscape",
                "url": "https://www.pexels.com/video/1/",
                "duration": 4,
                "video_files": [{"link": "https://cdn.example.test/lake.mp4", "width": 720, "height": 1280}],
            },
            {
                "id": 2,
                "title": "Hands installing phone mount in car",
                "url": "https://www.pexels.com/video/2/",
                "duration": 4,
                "video_files": [{"link": "https://cdn.example.test/mount.mp4", "width": 720, "height": 1280}],
            },
            {
                "id": 3,
                "title": "Phone mount dashboard close up",
                "url": "https://www.pexels.com/video/3/",
                "duration": 4,
                "video_files": [{"link": "https://cdn.example.test/dashboard.mp4", "width": 720, "height": 1280}],
            },
        ]
    }
    calls: list[str] = []

    def opener(request, timeout=0):
        calls.append(request.full_url)
        if request.full_url.startswith("https://api.pexels.com/"):
            return _Response(json.dumps(payload).encode())
        return _Response((request.full_url.encode() * 80)[:2048])

    def runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "format": {"duration": "4.0"},
                    "streams": [{"codec_type": "video", "width": 720, "height": 1280}],
                }
            ),
            stderr="",
        )

    result = StockBrollProvider(
        tmp_path,
        pexels_key="test-key",
        opener=opener,
        runner=runner,
    ).search_and_cache("phone mount in car", max_results=8)
    assert result.status == "ready"
    assert result.candidate_count == 3
    assert len(result.items) == 2
    assert result.candidate_summaries[0]["score"] >= result.candidate_summaries[1]["score"]
    assert sum("cdn.example.test" in url for url in calls) == 2


def test_provider_ranking_uses_page_url_when_title_is_empty(tmp_path: Path):
    payload = {
        "videos": [
            {
                "id": 1,
                "title": "",
                "url": "https://www.pexels.com/video/phone-mounted-on-dashboard-1/",
                "duration": 4,
                "video_files": [{"link": "https://cdn.example.test/one.mp4", "width": 720, "height": 1280}],
            },
            {
                "id": 2,
                "title": "",
                "url": "https://www.pexels.com/video/office-person-2/",
                "duration": 4,
                "video_files": [{"link": "https://cdn.example.test/two.mp4", "width": 720, "height": 1280}],
            },
        ]
    }
    calls: list[str] = []

    def opener(request, timeout=0):
        calls.append(request.full_url)
        if request.full_url.startswith("https://api.pexels.com/"):
            return _Response(json.dumps(payload).encode())
        return _Response((request.full_url.encode() * 80)[:2048])

    def runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "format": {"duration": "4.0"},
                    "streams": [{"codec_type": "video", "width": 720, "height": 1280}],
                }
            ),
            stderr="",
        )

    result = StockBrollProvider(
        tmp_path,
        pexels_key="test-key",
        opener=opener,
        runner=runner,
    ).search_and_cache("phone mount in car", max_results=2)

    assert result.status == "ready"
    assert result.candidate_summaries[0]["source_url"].endswith("phone-mounted-on-dashboard-1/")


def test_real_color_image_is_accepted_but_solid_placeholder_is_rejected(tmp_path: Path):
    real = tmp_path / "real.png"
    Image.new("RGB", (128, 96), "#1677ff").save(real)
    # Add enough distinct pixels to make this a real visual rather than a flat card.
    image = Image.open(real)
    for x in range(0, 128, 8):
        for y in range(0, 96, 8):
            image.putpixel((x, y), ((x * 2) % 255, (y * 2) % 255, 80))
    image.save(real)
    solid = tmp_path / "solid.png"
    Image.new("RGB", (128, 96), "#ff0000").save(solid)

    metadata = {"media_type": "image/png"}
    assert local_broll_is_real(real, metadata)
    assert not local_broll_is_real(solid, metadata)
