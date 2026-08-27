import json
from pathlib import Path

from src.services.creative_asset_library import (
    CreativeAssetLibrary,
    MATERIAL_SYMBOLS_FONT_URL,
    MATERIAL_SYMBOLS_LICENSE_URL,
    MATERIAL_SYMBOLS_METADATA_URL,
    MATERIAL_SYMBOLS_SVG_URL,
    classify_theme,
)


class _Response:
    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.data


def test_theme_classifier_is_deterministic():
    assert classify_theme("这个 AI 数据系统如何提升效率") == "technology"
    assert classify_theme("曾经有一个客户，后来终于成功") == "story"
    assert classify_theme("企业客户增长和品牌销售") == "business"


def test_official_vector_download_is_recorded_with_license_and_source(tmp_path: Path, monkeypatch):
    metadata = b")]}'\n" + json.dumps({"icons": [{"name": "rocket_launch", "codepoint": 0xE8B8}]}).encode()

    def opener(request, timeout=0):
        url = request.full_url
        if url == MATERIAL_SYMBOLS_METADATA_URL:
            return _Response(metadata)
        if url == MATERIAL_SYMBOLS_FONT_URL:
            return _Response(b"\x00\x01\x00\x00fake-font")
        if url == MATERIAL_SYMBOLS_LICENSE_URL:
            return _Response(b"Apache License 2.0")
        if url == MATERIAL_SYMBOLS_SVG_URL.format(name="rocket_launch"):
            return _Response(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        raise AssertionError(f"unexpected URL: {url}")

    library = CreativeAssetLibrary(tmp_path, opener=opener)
    monkeypatch.setattr(
        library,
        "_render_glyph",
        lambda *, codepoint, font_path, output: output.write_bytes(b"\x89PNG\r\n\x1a\n"),
    )
    result = library.ensure_vector_assets("科技数据趋势", max_assets=1)

    assert result["theme"] == "technology"
    assert result["items"][0]["license_name"] == "Apache-2.0"
    assert result["items"][0]["source_url"].endswith("rocket_launch_24px.svg")
    assert (tmp_path / result["items"][0]["source_svg"]).is_file()
    metadata_path = tmp_path / f"{result['items'][0]['asset_id']}.json"
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["authorization_status"] == "confirmed"
