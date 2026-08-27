"""Auditable, low-cost creative assets for the talking-head release template.

The library only uses an allow-listed official source.  Network access is an
optional enhancement: a timeout or a malformed download returns an empty
result and the caller keeps the safe talking-head render.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping


MATERIAL_SYMBOLS_METADATA_URL = "https://fonts.google.com/metadata/icons?incomplete=1&key=material_symbols"
MATERIAL_SYMBOLS_SVG_URL = "https://raw.githubusercontent.com/google/material-design-icons/master/symbols/web/{name}/materialsymbolsoutlined/{name}_24px.svg"
MATERIAL_SYMBOLS_FONT_URL = "https://raw.githubusercontent.com/google/material-design-icons/master/variablefont/MaterialSymbolsOutlined%5BFILL%2CGRAD%2Copsz%2Cwght%5D.ttf"
MATERIAL_SYMBOLS_LICENSE_URL = "https://raw.githubusercontent.com/google/material-design-icons/master/LICENSE"
MASHANZHENG_FONT_URL = "https://raw.githubusercontent.com/googlefonts/mashanzheng/main/fonts/ttf/MaShanZheng-Regular.ttf"
MASHANZHENG_LICENSE_URL = "https://raw.githubusercontent.com/googlefonts/mashanzheng/main/OFL.txt"

_THEME_RULES: dict[str, tuple[str, ...]] = {
    "technology": ("科技", "AI", "人工智能", "数据", "软件", "算法", "数字", "互联网", "芯片", "自动化", "未来"),
    "story": ("故事", "曾经", "后来", "有一天", "经历", "人物", "回忆", "终于", "转折"),
    "business": ("商业", "客户", "企业", "创业", "品牌", "销售", "管理", "老板", "公司", "市场", "增长"),
    "knowledge": ("方法", "步骤", "教程", "知识", "技巧", "原因", "如何", "第一", "第二", "第三", "解释"),
    "emotion": ("情绪", "治愈", "关系", "焦虑", "幸福", "喜欢", "失望", "勇气", "人生"),
}

_SYMBOLS_BY_THEME: dict[str, tuple[str, ...]] = {
    "technology": ("rocket_launch", "analytics", "memory", "auto_awesome"),
    "story": ("auto_stories", "history_edu", "person", "route"),
    "business": ("trending_up", "business_center", "target", "handshake"),
    "knowledge": ("lightbulb", "checklist", "school", "tips_and_updates"),
    "emotion": ("favorite", "psychology", "sentiment_satisfied", "warning"),
    "general": ("lightbulb", "insights", "auto_awesome"),
}


def classify_theme(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or ""))
    scores = {
        theme: sum(compact.lower().count(token.lower()) for token in tokens)
        for theme, tokens in _THEME_RULES.items()
    }
    best = max(scores, key=lambda key: (scores[key], ("technology", "business", "knowledge", "story", "emotion").index(key)))
    return best if scores[best] else "general"


def _asset_id(name: str) -> str:
    return f"vector-{hashlib.sha256(('material-symbols-outlined:' + name).encode()).hexdigest()[:10]}"


class CreativeAssetLibrary:
    """Download, render, and record open assets without hiding provenance."""

    def __init__(
        self,
        root: Path,
        *,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.font_root = root / "fonts"
        self.font_root.mkdir(parents=True, exist_ok=True)
        self.opener = opener or urllib.request.urlopen

    def _download(self, url: str, *, max_bytes: int) -> bytes:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": "VideoInsight/creative-asset-library"},
                )
                with self.opener(request, timeout=15) as response:
                    data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError("远程素材超过大小限制。")
                return data
            except (OSError, ValueError, urllib.error.URLError) as exc:
                last_error = exc
                if attempt == 1:
                    break
        raise RuntimeError(str(last_error or "远程素材下载失败。"))

    @staticmethod
    def _parse_metadata(data: bytes) -> dict[str, Any]:
        text = data.decode("utf-8-sig")
        text = text.lstrip(")]}'\n")
        payload = json.loads(text)
        if not isinstance(payload, dict) or not isinstance(payload.get("icons"), list):
            raise ValueError("Material Symbols 元数据格式无效。")
        return payload

    def _render_glyph(self, *, codepoint: int, font_path: Path, output: Path) -> None:
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype(str(font_path), 300)
        glyph = chr(codepoint)
        bbox = font.getbbox(glyph)
        if not bbox:
            raise ValueError("透明矢量图标没有有效字形。")
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        canvas = Image.new("RGBA", (640, 640), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        x = (640 - width) // 2 - bbox[0]
        y = (640 - height) // 2 - bbox[1]
        draw.text((x, y), glyph, font=font, fill=(115, 224, 255, 255))
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output, "PNG", optimize=True)

    def _ensure_material_font(self) -> Path:
        path = self.font_root / "MaterialSymbolsOutlined.ttf"
        if not path.is_file():
            data = self._download(MATERIAL_SYMBOLS_FONT_URL, max_bytes=20 * 1024 * 1024)
            if not (data.startswith(b"\x00\x01\x00\x00") or data.startswith(b"OTTO")):
                raise ValueError("Material Symbols 字体文件格式无效。")
            path.write_bytes(data)
            (self.font_root / "LICENSE-MaterialSymbols-Apache-2.0.txt").write_bytes(
                self._download(MATERIAL_SYMBOLS_LICENSE_URL, max_bytes=200_000)
            )
        return path

    def _ensure_ma_shan_zheng(self) -> Path:
        path = self.font_root / "MaShanZheng-Regular.ttf"
        if not path.is_file():
            data = self._download(MASHANZHENG_FONT_URL, max_bytes=12 * 1024 * 1024)
            if not data.startswith((b"\x00\x01\x00\x00", b"OTTO")):
                raise ValueError("马善政字体文件格式无效。")
            path.write_bytes(data)
            (self.font_root / "LICENSE-MaShanZheng-OFL.txt").write_bytes(
                self._download(MASHANZHENG_LICENSE_URL, max_bytes=200_000)
            )
        return path

    def ensure_font(self, theme: str) -> dict[str, Any]:
        theme = theme if theme in _THEME_RULES else "general"
        if theme == "story":
            try:
                path = self._ensure_ma_shan_zheng()
                return {"theme": theme, "font_family": "Ma Shan Zheng", "font_path": str(path), "source": "googlefonts/mashanzheng", "license": "OFL-1.1"}
            except Exception:
                pass
        if theme == "technology":
            path = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "SmileySans-Oblique.ttf"
            if path.is_file():
                return {"theme": theme, "font_family": "Smiley Sans", "font_path": str(path), "source": "atelier-anchor/smiley-sans", "license": "OFL-1.1"}
        path = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "SourceHanSerifCN-Heavy.otf"
        return {"theme": theme, "font_family": "Source Han Serif CN Heavy", "font_path": str(path), "source": "local-project-font", "license": "OFL-1.1"}

    def ensure_vector_assets(self, text: str, *, max_assets: int = 3) -> dict[str, Any]:
        theme = classify_theme(text)
        try:
            catalog = self._parse_metadata(
                self._download(MATERIAL_SYMBOLS_METADATA_URL, max_bytes=12 * 1024 * 1024)
            )
            catalog_by_name = {
                str(item.get("name")): item
                for item in catalog.get("icons") or []
                if isinstance(item, Mapping) and item.get("name")
            }
            font_path = self._ensure_material_font()
        except Exception as exc:
            return {"theme": theme, "items": [], "typography": self.ensure_font(theme), "degradation": f"透明矢量素材库暂不可用：{exc}"}
        items: list[dict[str, Any]] = []
        for name in _SYMBOLS_BY_THEME.get(theme, _SYMBOLS_BY_THEME["general"]):
            if len(items) >= max_assets:
                break
            asset_id = _asset_id(name)
            metadata_path = self.root / f"{asset_id}.json"
            png_path = self.root / f"{asset_id}.png"
            try:
                if metadata_path.is_file() and png_path.is_file():
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                else:
                    icon = catalog_by_name.get(name) or {}
                    codepoint = int(icon.get("codepoint") or 0)
                    if codepoint <= 0:
                        raise ValueError("官方图标目录没有对应字形码点。")
                    svg_url = MATERIAL_SYMBOLS_SVG_URL.format(name=name)
                    svg = self._download(svg_url, max_bytes=200_000)
                    if b"<svg" not in svg[:500].lower():
                        raise ValueError("透明矢量 SVG 格式无效。")
                    metadata_url = MATERIAL_SYMBOLS_METADATA_URL
                    source_svg = self.root / f"{asset_id}.svg"
                    source_svg.write_bytes(svg)
                    self._render_glyph(codepoint=codepoint, font_path=font_path, output=png_path)
                    # The source SVG is the audit asset and the rendered PNG is
                    # the FFmpeg-compatible transparent runtime asset.
                    metadata = {
                        "asset_id": asset_id,
                        "kind": "vector",
                        "name": name,
                        "original_name": f"{name}.svg",
                        "stored_name": png_path.name,
                        "media_type": "image/png",
                        "media_kind": "image",
                        "source_svg": source_svg.name,
                        "codepoint": codepoint,
                        "source_url": svg_url,
                        "catalog_url": metadata_url,
                        "license_name": "Apache-2.0",
                        "license_url": MATERIAL_SYMBOLS_LICENSE_URL,
                        "authorization_status": "confirmed",
                        "rights_holder": "Google Material Design Icons contributors",
                        "rights_confirmed_at": datetime.now().astimezone().isoformat(),
                        "created_at": datetime.now().astimezone().isoformat(),
                        "query": text[:160],
                        "theme": theme,
                        "sha256": hashlib.sha256(svg).hexdigest(),
                    }
                    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
                items.append({**metadata, "_path": str(png_path)})
            except Exception:
                continue
        return {
            "theme": theme,
            "items": items,
            "typography": self.ensure_font(theme),
            "degradation": None if items else "未下载到可用透明矢量素材，已安全降级为口播主体。",
        }
