"""批量下载透明背景矢量素材到口播矢量素材分类目录。

数据来源：Storyset（免费）、unDraw（免费商用）、OpenMoji（CC BY-SA 4.0）

用法：python scripts/download_vector_assets.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 强制不使用代理
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "data" / "creative_assets" / "口播矢量素材"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TIMEOUT = 20
MAX_RETRIES = 2
DELAY_BETWEEN = 0.5

# ── 平台配置 ──────────────────────────────────────────────
STORYSET_CATEGORIES = {
    "science": "https://www.storyset.com/science",
    "health": "https://www.storyset.com/health",
    "finance": "https://www.storyset.com/finance",
    "technology": "https://www.storyset.com/technology",
    "education": "https://www.storyset.com/education",
    "people": "https://www.storyset.com/people",
    "business": "https://www.storyset.com/business",
}

UNDRAW_ILLUSTRATIONS = [
    # 科普教育
    "scientist", "analysis", "data-trends", "chart", "science",
    "medicine", "research", "biology", "chemistry", "physics",
    "astronomy", "space", "rocket", "satellite", "telescope",
    "microscope", "lab", "experiment", "atom", "molecule",
    # 财金讲解
    "finance", "money", "investment", "bitcoin", "wallet",
    "payment", "credit-card", "bank", "savings", "growth",
    "chart-up", "analytics", "calculator", "coins", "stock",
    # 爱国/通用
    "flag", "celebration", "trophy", "team", "community",
    "leader", "hero", "military", "defense", "security",
    "landmark", "monument", "building", "city", "bridge",
    "train", "airplane", "ship", "satellite-dish", "tower",
]

# OpenMoji emoji codepoints（科学/金融/通用）
OPENMOJI_EMOJIS = {
    "科普教育/物理": [
        ("269B", "atom"),
        ("1F52D", "telescope"),
        ("1F4A1", "lightbulb"),
        ("26A1", "lightning"),
        ("1F30A", "wave"),
    ],
    "科普教育/化学": [
        ("2697", "alembic"),
        ("1F9EA", "test-tube"),
        ("1F52C", "microscope"),
        ("2622", "radioactive"),
        ("1F489", "syringe"),
    ],
    "科普教育/生物": [
        ("1F9EC", "dna"),
        ("1F41F", "fish"),
        ("1F420", "tropical-fish"),
        ("1F33F", "herb"),
        ("1F40D", "snake"),
    ],
    "科普教育/天文": [
        ("1F30D", "earth"),
        ("1F30E", "earth-americas"),
        ("1F30F", "earth-asia"),
        ("1F31F", "star"),
        ("2B50", "star-big"),
        ("1F680", "rocket"),
        ("1F319", "moon"),
        ("2600", "sun"),
        ("2604", "comet"),
        ("1F30C", "milky-way"),
    ],
    "科普教育/通用": [
        ("1F4D6", "book"),
        ("270D", "writing"),
        ("1F4DD", "memo"),
        ("1F4DA", "books"),
        ("1F393", "graduation-cap"),
    ],
    "财金讲解/数据图表": [
        ("1F4C8", "chart-up"),
        ("1F4C9", "chart-down"),
        ("1F4CA", "bar-chart"),
        ("1F4C0", "tv"),
        ("1F4B0", "money-bag"),
    ],
    "财金讲解/货币交易": [
        ("1F4B5", "dollar"),
        ("1F4B4", "yen"),
        ("1F4B6", "euro"),
        ("1F4B7", "pound"),
        ("1FA99", "coin"),
    ],
    "财金讲解/投资理财": [
        ("1F4B8", "money-wings"),
        ("1F3E6", "bank"),
        ("1F4B3", "credit-card"),
        ("1F4B1", "currency-exchange"),
        ("1F4C1", "folder"),
    ],
    "财金讲解/金融科技": [
        ("1F4F1", "mobile"),
        ("1F4BB", "laptop"),
        ("1F511", "key"),
        ("1F512", "lock"),
        ("1F4F7", "camera"),
    ],
    "财金讲解/人物场景": [
        ("1F464", "bust"),
        ("1F465", "busts"),
        ("1F9D1", "person"),
        ("1F468", "man"),
        ("1F469", "woman"),
    ],
    "爱国叙事/国家象征": [
        ("1F3F3", "flag"),
        ("1F3F4", "flag-black"),
        ("2B50", "star"),
        ("1F3EF", "shrine"),
        ("1F3F0", "castle"),
    ],
    "爱国叙事/军事元素": [
        ("1F396", "medal"),
        ("1F6E1", "shield"),
        ("2694", "swords"),
        ("1F6A8", "rotating-light"),
        ("1F6A9", "triangular-flag"),
    ],
    "爱国叙事/红色视觉": [
        ("2764", "heart"),
        ("1F493", "heartbeat"),
        ("1F495", "two-hearts"),
        ("1F5A4", "black-heart"),
        ("1F90D", "white-heart"),
    ],
    "爱国叙事/现代成就": [
        ("1F684", "bullet-train"),
        ("1F685", "train"),
        ("1F682", "locomotive"),
        ("2708", "airplane"),
        ("1F6F0", "satellite"),
    ],
    "通用/基础图标": [
        ("2705", "check"),
        ("274C", "cross"),
        ("26A0", "warning"),
        ("2139", "info"),
        ("2757", "exclamation"),
    ],
    "通用/背景元素": [
        ("2728", "sparkles"),
        ("1F31F", "glowing-star"),
        ("1F4AB", "dizzy"),
        ("1F308", "rainbow"),
        ("2744", "snowflake"),
    ],
}


def _opener():
    """创建不使用代理的 URL opener。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def download_url(url: str, max_bytes: int = 5 * 1024 * 1024) -> bytes | None:
    """下载单个 URL，返回 bytes 或 None。"""
    opener = _opener()
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=TIMEOUT) as resp:
                data = resp.read(max_bytes + 1)
                if len(data) > max_bytes:
                    return None
                return data
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
    return None


def fetch_page(url: str) -> str | None:
    """获取网页文本内容。"""
    data = download_url(url, max_bytes=3 * 1024 * 1024)
    if data:
        return data.decode("utf-8", errors="ignore")
    return None


# ── Storyset 下载 ─────────────────────────────────────────
def get_storyset_images(category: str) -> list[dict[str, str]]:
    """从 Storyset 分类页面提取图片 URL。"""
    url = STORYSET_CATEGORIES.get(category)
    if not url:
        return []
    html = fetch_page(url)
    if not html:
        return []

    results = []
    # 提取 freepiklabs.com 的图片 URL
    pattern = r'https://stories\.freepiklabs\.com/storage/[^"\'<>\s]+\.(?:png|svg)'
    matches = list(set(re.findall(pattern, html, re.IGNORECASE)))
    for m in matches:
        results.append({"url": m, "source": "storyset", "category": category})
    return results


# ── unDraw 下载 ────────────────────────────────────────────
def get_undraw_images(query: str) -> list[dict[str, str]]:
    """从 unDraw 搜索页面提取 SVG URL。"""
    url = f"https://undraw.co/illustrations"
    html = fetch_page(url)
    if not html:
        return []

    results = []
    # 提取 cdn.undraw.co 的 SVG URL
    pattern = r'https://cdn\.undraw\.co/illustration/[^"\'<>\s]+\.svg'
    matches = list(set(re.findall(pattern, html, re.IGNORECASE)))
    for m in matches:
        if query.lower() in m.lower() or any(k in m.lower() for k in query.split("-")):
            results.append({"url": m, "source": "undraw", "query": query})
    return results


def get_all_undraw_images() -> list[dict[str, str]]:
    """获取 unDraw 所有可用的 SVG 插画。"""
    html = fetch_page("https://undraw.co/illustrations")
    if not html:
        return []

    pattern = r'https://cdn\.undraw\.co/illustration/[^"\'<>\s]+\.svg'
    matches = list(set(re.findall(pattern, html, re.IGNORECASE)))
    return [{"url": m, "source": "undraw"} for m in matches]


# ── OpenMoji 下载 ──────────────────────────────────────────
def download_openmoji(codepoint: str) -> bytes | None:
    """下载单个 OpenMoji emoji（618x618 彩色 PNG）。"""
    url = f"https://raw.githubusercontent.com/hfg-gmuend/openmoji/master/color/618x618/{codepoint}.png"
    return download_url(url)


# ── 分类匹配 ──────────────────────────────────────────────
# 根据文件名关键词匹配到子目录
CATEGORY_KEYWORDS = {
    "科普教育/物理": ["atom", "physics", "wave", "magnet", "energy", "light", "gravity", "force", "electric"],
    "科普教育/化学": ["chemistry", "beaker", "flask", "molecule", "lab", "experiment", "chemical", "test-tube"],
    "科普教育/生物": ["biology", "dna", "cell", "microscope", "bacteria", "virus", "organism", "gene", "nature"],
    "科普教育/天文": ["space", "planet", "rocket", "star", "moon", "sun", "galaxy", "satellite", "orbit", "cosmos", "telescope"],
    "科普教育/通用": ["science", "education", "book", "learn", "study", "school", "knowledge", "research"],
    "财金讲解/数据图表": ["chart", "graph", "analytics", "data", "trend", "statistic", "diagram", "analysis"],
    "财金讲解/货币交易": ["money", "coin", "cash", "dollar", "currency", "banknote", "payment", "wallet"],
    "财金讲解/投资理财": ["invest", "savings", "growth", "profit", "fund", "stock", "portfolio", "retire"],
    "财金讲解/金融科技": ["fintech", "bitcoin", "crypto", "blockchain", "digital", "mobile-pay", "online"],
    "财金讲解/人物场景": ["business", "office", "meeting", "corporate", "professional", "manager", "ceo"],
    "爱国叙事/国家象征": ["flag", "emblem", "national", "patriotic", "symbol", "monument", "landmark"],
    "爱国叙事/军事元素": ["military", "army", "soldier", "defense", "shield", "weapon", "tank", "jet"],
    "爱国叙事/红色视觉": ["red", "ribbon", "heart", "star", "banner", "celebration", "festival"],
    "爱国叙事/历史叙事": ["history", "historical", "heritage", "ancient", "classic", "vintage"],
    "爱国叙事/现代成就": ["modern", "city", "skyscraper", "bridge", "train", "highway", "technology", "tower"],
    "通用/基础图标": ["icon", "symbol", "sign", "mark", "check", "warning", "info", "arrow"],
    "通用/背景元素": ["background", "pattern", "texture", "effect", "light", "bokeh", "particle", "gradient"],
}


def classify_image(url: str) -> str:
    """根据 URL 关键词将图片分类到子目录。"""
    url_lower = url.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in url_lower:
                return category
    return "通用/背景元素"


def get_asset_filename(url: str, category: str, index: int) -> str:
    """生成文件名。"""
    ext = "svg" if url.lower().endswith(".svg") else "png"
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    cat_name = category.split("/")[-1]
    return f"{cat_name}_{index:02d}_{url_hash}.{ext}"


# ── 主下载流程 ──────────────────────────────────────────────
def download_storyset_assets() -> dict[str, int]:
    """从 Storyset 下载素材。"""
    print("\n[1/3] 从 Storyset 下载素材...")
    stats: dict[str, int] = {}

    for cat_name, cat_url in STORYSET_CATEGORIES.items():
        print(f"  搜索 {cat_name}...", end="", flush=True)
        images = get_storyset_images(cat_name)
        if not images:
            print(" 未找到结果")
            continue

        # 限制每个分类最多10张
        images = images[:10]
        downloaded = 0

        for item in images:
            url = item["url"]
            target_dir_name = classify_image(url)
            target_dir = ASSET_ROOT / target_dir_name
            target_dir.mkdir(parents=True, exist_ok=True)

            existing = len([f for f in target_dir.iterdir() if f.suffix in (".png", ".svg")])
            if existing >= 8:
                continue

            filename = get_asset_filename(url, target_dir_name, existing + 1)
            filepath = target_dir / filename

            if filepath.exists():
                downloaded += 1
                continue

            data = download_url(url)
            if data and len(data) > 500:
                filepath.write_bytes(data)
                downloaded += 1
                print(".", end="", flush=True)

            time.sleep(DELAY_BETWEEN)

        stats[cat_name] = downloaded
        print(f" 完成 ({downloaded} 个)")

    return stats


def download_undraw_assets() -> dict[str, int]:
    """从 unDraw 下载素材。"""
    print("\n[2/3] 从 unDraw 下载素材...")
    stats: dict[str, int] = {}

    # 获取所有 unDraw 插画
    all_images = get_all_undraw_images()
    if not all_images:
        print("  无法获取 unDraw 素材列表")
        return stats

    print(f"  找到 {len(all_images)} 个 unDraw 插画")

    # 按查询关键词分类下载
    for query in UNDRAW_ILLUSTRATIONS:
        matching = [img for img in all_images if query.lower() in img["url"].lower()]
        if not matching:
            continue

        for item in matching[:3]:  # 每个关键词最多3张
            url = item["url"]
            target_dir_name = classify_image(url + f"_{query}")
            target_dir = ASSET_ROOT / target_dir_name
            target_dir.mkdir(parents=True, exist_ok=True)

            existing = len([f for f in target_dir.iterdir() if f.suffix in (".png", ".svg")])
            if existing >= 8:
                continue

            filename = get_asset_filename(url, target_dir_name, existing + 1)
            filepath = target_dir / filename

            if filepath.exists():
                continue

            data = download_url(url)
            if data and len(data) > 500:
                filepath.write_bytes(data)
                cat_key = target_dir_name.split("/")[0]
                stats[cat_key] = stats.get(cat_key, 0) + 1
                print(".", end="", flush=True)

            time.sleep(DELAY_BETWEEN)

    print(f"\n  unDraw 下载完成")
    return stats


def download_openmoji_assets() -> dict[str, int]:
    """从 OpenMoji 下载 emoji 素材。"""
    print("\n[3/3] 从 OpenMoji 下载 emoji 素材...")
    stats: dict[str, int] = {}

    for category, emojis in OPENMOJI_EMOJIS.items():
        target_dir = ASSET_ROOT / category
        target_dir.mkdir(parents=True, exist_ok=True)

        existing = len([f for f in target_dir.iterdir() if f.suffix in (".png", ".svg")])
        if existing >= 8:
            stats[category] = existing
            continue

        downloaded = 0
        for codepoint, name in emojis:
            if existing + downloaded >= 8:
                break

            filename = f"{name}_{codepoint}.png"
            filepath = target_dir / filename

            if filepath.exists():
                downloaded += 1
                continue

            data = download_openmoji(codepoint)
            if data and len(data) > 500:
                filepath.write_bytes(data)
                downloaded += 1
                print(".", end="", flush=True)

            time.sleep(DELAY_BETWEEN)

        stats[category] = existing + downloaded
        if downloaded > 0:
            print(f"  {category}: +{downloaded} 个")

    return stats


def main() -> None:
    print("=" * 60)
    print("矢量贴图素材批量下载")
    print(f"目标目录: {ASSET_ROOT}")
    print("=" * 60)

    start = time.time()

    # 三个来源并行下载
    stats1 = download_storyset_assets()
    stats2 = download_undraw_assets()
    stats3 = download_openmoji_assets()

    elapsed = time.time() - start

    # 汇总统计
    print("\n" + "=" * 60)
    print("下载统计：")
    print("-" * 60)

    # 统计每个子目录的文件数
    total_files = 0
    for category_dir in sorted(ASSET_ROOT.iterdir()):
        if not category_dir.is_dir():
            continue
        for sub_dir in sorted(category_dir.iterdir()):
            if not sub_dir.is_dir():
                continue
            count = len([f for f in sub_dir.iterdir() if f.suffix in (".png", ".svg")])
            if count > 0:
                print(f"  {category_dir.name}/{sub_dir.name}: {count} 个")
                total_files += count

    print("-" * 60)
    print(f"  总计: {total_files} 个素材文件")
    print(f"  耗时: {elapsed:.1f} 秒")
    print("=" * 60)


if __name__ == "__main__":
    main()
