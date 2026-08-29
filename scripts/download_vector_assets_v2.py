"""批量下载透明背景矢量素材 V2 — 多平台扩展版。

数据来源：
- Lucide Icons (483 SVG)
- Heroicons (325 SVG)
- Feather Icons (287 SVG)
- DrawKit (657 SVG + 252 PNG)
- OpenDoodles (101 SVG + 36 PNG)
- ManyPixels (53 PNG)
- 3D Icons (9 PNG)
- Storyset (7 大类)
- unDraw (40 SVG)
- OpenMoji (emoji)

用法：python scripts/download_vector_assets_v2.py
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

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
DELAY = 0.3

# ── 分类关键词映射 ──────────────────────────────────────────
# 根据图标/素材名称中的关键词，自动分类到对应子目录
CATEGORY_RULES: dict[str, list[str]] = {
    # 科普教育
    "科普教育/物理": [
        "atom", "nuclear", "physics", "wave", "magnet", "energy", "light", "gravity",
        "force", "electric", "battery", "charge", "power", "voltage", "current",
        "magnetic", "field", "particle", "quantum", "photon", "laser", "optics",
        "prism", "spectrum", "frequency", "oscillat", "pendulum", "friction",
        "acceleration", "velocity", "momentum", "torque", "rotation",
    ],
    "科普教育/化学": [
        "chemistry", "beaker", "flask", "molecule", "lab", "experiment", "chemical",
        "test-tube", "alembic", "atom", "element", "compound", "reaction", "acid",
        "base", "salt", "solution", "solvent", "crystal", "polymer", "bond",
        "periodic", "electron", "proton", "neutron", "ion", "isotope",
    ],
    "科普教育/生物": [
        "biology", "dna", "cell", "microscope", "bacteria", "virus", "organism",
        "gene", "nature", "animal", "plant", "species", "evolution", "ecology",
        "enzyme", "protein", "membrane", "nucleus", "mitosis", "meiosis",
        "photosynthesis", "respiration", "metabolism", "anatomy", "organ",
        "tissue", "blood", "heart", "brain", "lung", "kidney", "liver",
        "stomach", "intestine", "nerve", "muscle", "bone", "skin",
    ],
    "科普教育/天文": [
        "space", "planet", "rocket", "star", "moon", "sun", "galaxy", "satellite",
        "orbit", "cosmos", "telescope", "meteor", "comet", "asteroid", "nebula",
        "constellation", "astronaut", "spacecraft", "space-station", "mars",
        "jupiter", "saturn", "venus", "mercury", "uranus", "neptune", "pluto",
        "eclipse", "solar", "lunar", "milky-way", "black-hole", "supernova",
        "universe", "telescope", "observatory", "planetarium",
    ],
    "科普教育/通用": [
        "science", "education", "book", "learn", "study", "school", "knowledge",
        "research", "discovery", "invention", "innovation", "experiment", "theory",
        "hypothesis", "formula", "equation", "math", "geometry", "algebra",
        "calculus", "statistics", "probability", "logic", "reasoning",
        "academic", "professor", "student", "teacher", "lecture", "seminar",
        "laboratory", "library", "university", "college", "campus",
    ],
    # 财金讲解
    "财金讲解/数据图表": [
        "chart", "graph", "analytics", "data", "trend", "statistic", "diagram",
        "analysis", "bar-chart", "pie-chart", "line-chart", "area-chart",
        "dashboard", "report", "metric", "kpi", "indicator", "gauge",
        "histogram", "scatter", "heatmap", "treemap", "funnel", "waterfall",
        "candlestick", "ohlc", "tick", "volume", "price", "index",
    ],
    "财金讲解/货币交易": [
        "money", "coin", "cash", "dollar", "currency", "banknote", "payment",
        "wallet", "purse", "bank", "atm", "withdraw", "deposit", "transfer",
        "exchange", "forex", "trade", "transaction", "receipt", "invoice",
        "bill", "check", "cheque", "payroll", "salary", "wage", "income",
        "revenue", "profit", "loss", "expense", "cost", "budget",
    ],
    "财金讲解/投资理财": [
        "invest", "savings", "growth", "profit", "fund", "stock", "portfolio",
        "retire", "pension", "insurance", "bond", "dividend", "yield",
        "interest", "compound", "asset", "liability", "equity", "capital",
        "venture", "startup", "ipo", "market", "exchange", "broker",
        "dealer", "trader", "investor", "shareholder", "stakeholder",
    ],
    "财金讲解/金融科技": [
        "fintech", "bitcoin", "crypto", "blockchain", "digital", "mobile-pay",
        "online", "e-commerce", "smart-contract", "defi", "nft", "token",
        "wallet", "ledger", "mining", "hash", "node", "network", "protocol",
        "api", "cloud", "saas", "paas", "iaas", "ai", "machine-learning",
        "automation", "robot", "chatbot", "voice-assistant",
    ],
    "财金讲解/人物场景": [
        "business", "office", "meeting", "corporate", "professional", "manager",
        "ceo", "cfo", "cto", "executive", "director", "supervisor", "team",
        "group", "collaboration", "partnership", "handshake", "deal", "contract",
        "negotiation", "presentation", "conference", "seminar", "workshop",
        "interview", "resume", "cv", "career", "job", "work", "occupation",
    ],
    # 爱国叙事
    "爱国叙事/国家象征": [
        "flag", "emblem", "national", "patriotic", "symbol", "monument",
        "landmark", "capitol", "parliament", "government", "president",
        "leader", "nation", "country", "state", "republic", "democracy",
        "constitution", "law", "justice", "liberty", "freedom", "independence",
        "sovereignty", "territory", "border", "map", "globe", "world",
    ],
    "爱国叙事/军事元素": [
        "military", "army", "soldier", "defense", "shield", "weapon", "tank",
        "jet", "aircraft", "warship", "submarine", "missile", "bomb", "gun",
        "rifle", "pistol", "sword", "armor", "helmet", "uniform", "medal",
        "badge", "rank", "general", "commander", "troop", "regiment",
        "battalion", "division", "corps", "army", "navy", "air-force",
        "marine", "coast-guard", "special-forces", "elite", "veteran",
    ],
    "爱国叙事/红色视觉": [
        "red", "ribbon", "heart", "star", "banner", "celebration", "festival",
        "party", "confetti", "balloon", "firework", "sparkle", "glow",
        "shine", "bright", "vibrant", "passion", "love", "romance",
        "valentine", "wedding", "anniversary", "birthday", "christmas",
        "new-year", "holiday", "vacation", "travel", "adventure",
    ],
    "爱国叙事/历史叙事": [
        "history", "historical", "heritage", "ancient", "classic", "vintage",
        "retro", "old", "antique", "archaeology", "fossil", "ruin", "temple",
        "pyramid", "castle", "palace", "fortress", "tower", "wall", "gate",
        "bridge", "arch", "column", "pillar", "statue", "sculpture",
        "artifact", "relic", "treasure", "gold", "silver", "bronze",
    ],
    "爱国叙事/现代成就": [
        "modern", "city", "skyscraper", "bridge", "train", "highway",
        "technology", "tower", "building", "architecture", "construction",
        "engineering", "infrastructure", "transportation", "communication",
        "internet", "network", "smart-city", "green-energy", "solar",
        "wind", "hydro", "nuclear", "power-plant", "factory", "industry",
        "manufacturing", "production", "assembly", "robot", "automation",
    ],
    # 通用
    "通用/基础图标": [
        "icon", "symbol", "sign", "mark", "check", "warning", "info",
        "arrow", "direction", "navigation", "menu", "button", "link",
        "search", "find", "filter", "sort", "order", "list", "grid",
        "table", "form", "input", "output", "save", "load", "file",
        "folder", "document", "page", "print", "share", "copy",
    ],
    "通用/背景元素": [
        "background", "pattern", "texture", "effect", "light", "bokeh",
        "particle", "gradient", "overlay", "frame", "border", "decoration",
        "ornament", "flourish", "swirl", "dot", "line", "circle", "square",
        "triangle", "diamond", "hexagon", "star", "heart", "cloud",
    ],
    "通用/特效素材": [
        "effect", "fx", "transition", "motion", "animation", "dynamic",
        "explosion", "burst", "splash", "wave", "ripple", "shock",
        "flash", "glitch", "noise", "static", "distort", "warp",
        "blur", "focus", "zoom", "pan", "rotate", "scale", "fade",
        "dissolve", "wipe", "slide", "push", "pull", "spin",
    ],
    "通用/装饰元素": [
        "decoration", "ornament", "flourish", "divider", "separator",
        "header", "footer", "sidebar", "corner", "edge", "margin",
        "padding", "spacing", "alignment", "layout", "composition",
        "frame", "border", "outline", "shadow", "glow", "highlight",
        "accent", "badge", "label", "tag", "sticker", "emoji",
    ],
}


def classify_by_name(name: str) -> str:
    """根据名称关键词分类。"""
    name_lower = name.lower().replace("_", "-").replace(".", "-")
    for category, keywords in CATEGORY_RULES.items():
        for kw in keywords:
            if kw in name_lower:
                return category
    return "通用/背景元素"


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def download_url(url: str, max_bytes: int = 5 * 1024 * 1024) -> bytes | None:
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
                time.sleep(0.5)
    return None


def fetch_page(url: str) -> str | None:
    data = download_url(url, max_bytes=5 * 1024 * 1024)
    return data.decode("utf-8", errors="ignore") if data else None


def save_asset(data: bytes, target_dir: Path, name: str, ext: str = "svg") -> bool:
    """保存素材文件，返回是否成功。"""
    if len(data) < 200:
        return False
    # 验证文件格式
    if ext == "svg" and b"<svg" not in data[:1000].lower():
        return False
    if ext == "png" and not data[:8].startswith(b"\x89PNG"):
        return False

    target_dir.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.md5(name.encode()).hexdigest()[:8]
    safe_name = re.sub(r'[^\w\-]', '_', name)[:40]
    filename = f"{safe_name}_{url_hash}.{ext}"
    filepath = target_dir / filename

    if filepath.exists():
        return False

    filepath.write_bytes(data)
    return True


# ── 平台下载器 ──────────────────────────────────────────────

def download_lucide_icons() -> int:
    """从 Lucide 下载 SVG 图标。"""
    print("[Lucide] 下载 SVG 图标...", end="", flush=True)
    # 获取图标列表
    html = fetch_page("https://unpkg.com/lucide-static@latest/icons/")
    if not html:
        print(" 无法访问")
        return 0

    # 提取图标名称
    pattern = r'href="/lucide-static@[^/]+/icons/([^"]+)\.svg"'
    names = list(set(re.findall(pattern, html)))
    print(f" 找到 {len(names)} 个图标", end="", flush=True)

    count = 0
    for name in names[:200]:  # 限制200个
        category = classify_by_name(name)
        target_dir = ASSET_ROOT / category
        if len(list(target_dir.glob("*.svg"))) >= 15:
            continue

        url = f"https://unpkg.com/lucide-static@latest/icons/{name}.svg"
        data = download_url(url)
        if data and save_asset(data, target_dir, name, "svg"):
            count += 1
            if count % 20 == 0:
                print(".", end="", flush=True)
        time.sleep(DELAY)

    print(f" 完成 (+{count})")
    return count


def download_heroicons() -> int:
    """从 Heroicons 下载 SVG 图标。"""
    print("[Heroicons] 下载 SVG 图标...", end="", flush=True)
    html = fetch_page("https://unpkg.com/heroicons@latest/24/outline/")
    if not html:
        print(" 无法访问")
        return 0

    pattern = r'href="/heroicons@[^/]+/24/outline/([^"]+)\.svg"'
    names = list(set(re.findall(pattern, html)))
    print(f" 找到 {len(names)} 个图标", end="", flush=True)

    count = 0
    for name in names[:200]:
        category = classify_by_name(name)
        target_dir = ASSET_ROOT / category
        if len(list(target_dir.glob("*.svg"))) >= 15:
            continue

        url = f"https://unpkg.com/heroicons@latest/24/outline/{name}.svg"
        data = download_url(url)
        if data and save_asset(data, target_dir, name, "svg"):
            count += 1
            if count % 20 == 0:
                print(".", end="", flush=True)
        time.sleep(DELAY)

    print(f" 完成 (+{count})")
    return count


def download_feather_icons() -> int:
    """从 Feather Icons 下载 SVG 图标。"""
    print("[Feather] 下载 SVG 图标...", end="", flush=True)
    html = fetch_page("https://unpkg.com/feather-icons@latest/dist/icons/")
    if not html:
        print(" 无法访问")
        return 0

    pattern = r'href="/feather-icons@[^/]+/dist/icons/([^"]+)\.svg"'
    names = list(set(re.findall(pattern, html)))
    print(f" 找到 {len(names)} 个图标", end="", flush=True)

    count = 0
    for name in names[:200]:
        category = classify_by_name(name)
        target_dir = ASSET_ROOT / category
        if len(list(target_dir.glob("*.svg"))) >= 15:
            continue

        url = f"https://unpkg.com/feather-icons@latest/dist/icons/{name}.svg"
        data = download_url(url)
        if data and save_asset(data, target_dir, name, "svg"):
            count += 1
            if count % 20 == 0:
                print(".", end="", flush=True)
        time.sleep(DELAY)

    print(f" 完成 (+{count})")
    return count


def download_drawkit() -> int:
    """从 DrawKit 下载 SVG/PNG 素材。"""
    print("[DrawKit] 下载插画...", end="", flush=True)
    html = fetch_page("https://www.drawkit.com/")
    if not html:
        print(" 无法访问")
        return 0

    # 提取所有 SVG 和 PNG URL
    svg_urls = list(set(re.findall(r'https://cdn\.prod\.website-files\.com/[^"\'<>\s]+\.svg', html)))
    png_urls = list(set(re.findall(r'https://cdn\.prod\.website-files\.com/[^"\'<>\s]+\.png', html)))
    all_urls = [(u, "svg") for u in svg_urls] + [(u, "png") for u in png_urls]
    print(f" 找到 {len(all_urls)} 个素材", end="", flush=True)

    count = 0
    for url, ext in all_urls[:100]:
        # 从URL提取名称
        name = url.split("/")[-1].split("?")[0].replace(f".{ext}", "")
        category = classify_by_name(name)
        target_dir = ASSET_ROOT / category
        if len(list(target_dir.glob(f"*.{ext}"))) >= 15:
            continue

        data = download_url(url)
        if data and save_asset(data, target_dir, name, ext):
            count += 1
            if count % 10 == 0:
                print(".", end="", flush=True)
        time.sleep(DELAY)

    print(f" 完成 (+{count})")
    return count


def download_opendoodles() -> int:
    """从 OpenDoodles 下载 SVG/PNG 素材。"""
    print("[OpenDoodles] 下载涂鸦插画...", end="", flush=True)
    html = fetch_page("https://www.opendoodles.com/")
    if not html:
        print(" 无法访问")
        return 0

    svg_urls = list(set(re.findall(r'https://[^"\'<>\s]+opendoodles[^"\'<>\s]+\.svg', html)))
    png_urls = list(set(re.findall(r'https://[^"\'<>\s]+opendoodles[^"\'<>\s]+\.png', html)))
    all_urls = [(u, "svg") for u in svg_urls] + [(u, "png") for u in png_urls]
    print(f" 找到 {len(all_urls)} 个素材", end="", flush=True)

    count = 0
    for url, ext in all_urls[:50]:
        name = url.split("/")[-1].split("?")[0].replace(f".{ext}", "")
        category = "通用/装饰元素"
        target_dir = ASSET_ROOT / category

        data = download_url(url)
        if data and save_asset(data, target_dir, f"doodle_{name}", ext):
            count += 1
        time.sleep(DELAY)

    print(f" 完成 (+{count})")
    return count


def download_manypixels() -> int:
    """从 ManyPixels 下载 PNG 素材。"""
    print("[ManyPixels] 下载插画...", end="", flush=True)
    html = fetch_page("https://www.manypixels.co/")
    if not html:
        print(" 无法访问")
        return 0

    png_urls = list(set(re.findall(r'https://cdn\.prod\.website-files\.com/[^"\'<>\s]+\.png', html)))
    print(f" 找到 {len(png_urls)} 个素材", end="", flush=True)

    count = 0
    for url in png_urls[:50]:
        name = url.split("/")[-1].split("?")[0].replace(".png", "")
        category = classify_by_name(name)
        target_dir = ASSET_ROOT / category

        data = download_url(url)
        if data and save_asset(data, target_dir, name, "png"):
            count += 1
        time.sleep(DELAY)

    print(f" 完成 (+{count})")
    return count


def download_3dicons() -> int:
    """从 3D Icons 下载 PNG 素材。"""
    print("[3D Icons] 下载 3D 图标...", end="", flush=True)
    html = fetch_page("https://3dicons.co/")
    if not html:
        print(" 无法访问")
        return 0

    png_urls = list(set(re.findall(r'https://[^"\'<>\s]+3dicons[^"\'<>\s]+\.png', html)))
    if not png_urls:
        # 尝试从 supabase 获取
        png_urls = list(set(re.findall(r'https://[^"\'<>\s]+supabase[^"\'<>\s]+\.png', html)))
    print(f" 找到 {len(png_urls)} 个素材", end="", flush=True)

    count = 0
    for url in png_urls[:30]:
        name = url.split("/")[-1].split("?")[0].replace(".png", "")
        category = classify_by_name(name)
        target_dir = ASSET_ROOT / category

        data = download_url(url)
        if data and save_asset(data, target_dir, f"3d_{name}", "png"):
            count += 1
        time.sleep(DELAY)

    print(f" 完成 (+{count})")
    return count


def download_storyset_all() -> int:
    """从 Storyset 所有分类下载素材。"""
    print("[Storyset] 下载所有分类插画...", end="", flush=True)
    categories = [
        "science", "health", "finance", "technology", "education",
        "people", "business", "history", "city", "nature",
        "transport", "work", "social", "cuate", "bro",
        "rafiki", "amico", "pana",
    ]

    count = 0
    for cat in categories:
        url = f"https://www.storyset.com/{cat}"
        html = fetch_page(url)
        if not html:
            continue

        pattern = r'https://stories\.freepiklabs\.com/storage/[^"\'<>\s]+\.(?:png|svg)'
        matches = list(set(re.findall(pattern, html, re.IGNORECASE)))

        for img_url in matches[:15]:
            ext = "svg" if img_url.endswith(".svg") else "png"
            name = img_url.split("/")[-1].split("?")[0].replace(f".{ext}", "")
            category = classify_by_name(name + "_" + cat)
            target_dir = ASSET_ROOT / category
            if len(list(target_dir.glob(f"*.{ext}"))) >= 15:
                continue

            data = download_url(img_url)
            if data and save_asset(data, target_dir, name, ext):
                count += 1
            time.sleep(DELAY)

        print(".", end="", flush=True)

    print(f" 完成 (+{count})")
    return count


def download_undraw_all() -> int:
    """从 unDraw 下载所有 SVG 插画。"""
    print("[unDraw] 下载所有 SVG 插画...", end="", flush=True)
    html = fetch_page("https://undraw.co/illustrations")
    if not html:
        print(" 无法访问")
        return 0

    pattern = r'https://cdn\.undraw\.co/illustration/[^"\'<>\s]+\.svg'
    matches = list(set(re.findall(pattern, html, re.IGNORECASE)))
    print(f" 找到 {len(matches)} 个素材", end="", flush=True)

    count = 0
    for url in matches:
        name = url.split("/")[-1].split("?")[0].replace(".svg", "")
        category = classify_by_name(name)
        target_dir = ASSET_ROOT / category
        if len(list(target_dir.glob("*.svg"))) >= 15:
            continue

        data = download_url(url)
        if data and save_asset(data, target_dir, name, "svg"):
            count += 1
        time.sleep(DELAY)

    print(f" 完成 (+{count})")
    return count


def download_openmoji_all() -> int:
    """从 OpenMoji 下载更多 emoji 素材。"""
    print("[OpenMoji] 下载 emoji 素材...", end="", flush=True)

    # 扩展 emoji 列表
    emojis = {
        # 科学
        "科普教育/物理": ["269B", "1F52D", "1F4A1", "26A1", "1F30A", "1F4AB", "2728", "1F300"],
        "科普教育/化学": ["2697", "1F9EA", "1F52C", "2622", "1F489", "1F48A", "2695", "1F321"],
        "科普教育/生物": ["1F9EC", "1F41F", "1F420", "1F33F", "1F40D", "1F419", "1F98B", "1F41E"],
        "科普教育/天文": ["1F30D", "1F30E", "1F30F", "1F31F", "2B50", "1F680", "1F319", "2600", "2604", "1F30C", "1F308", "1F320"],
        "科普教育/通用": ["1F4D6", "270D", "1F4DD", "1F4DA", "1F393", "1F4D0", "1F4CF", "1F4D1"],
        # 财金
        "财金讲解/数据图表": ["1F4C8", "1F4C9", "1F4CA", "1F4C0", "1F4B0", "1F4B9", "1F4C1", "1F4C2"],
        "财金讲解/货币交易": ["1F4B5", "1F4B4", "1F4B6", "1F4B7", "1FA99", "1F4B0", "1F4B2", "1F4B3"],
        "财金讲解/投资理财": ["1F4B8", "1F3E6", "1F4B3", "1F4B1", "1F4C1", "1F4BC", "1F4C5", "1F4C6"],
        "财金讲解/金融科技": ["1F4F1", "1F4BB", "1F511", "1F512", "1F4F7", "1F4E1", "1F4E0", "1F50C"],
        "财金讲解/人物场景": ["1F464", "1F465", "1F9D1", "1F468", "1F469", "1F46B", "1F46C", "1F46D"],
        # 爱国
        "爱国叙事/国家象征": ["1F3F3", "1F3F4", "2B50", "1F3EF", "1F3F0", "1F3DB", "1F3DC", "1F3DD"],
        "爱国叙事/军事元素": ["1F396", "1F6E1", "2694", "1F6A8", "1F6A9", "1F6E5", "1F4A3", "1F5E1"],
        "爱国叙事/红色视觉": ["2764", "1F493", "1F495", "1F5A4", "1F90D", "1F496", "1F497", "1F498"],
        "爱国叙事/现代成就": ["1F684", "1F685", "1F682", "2708", "1F6F0", "1F680", "1F3D7", "1F3D8"],
        # 通用
        "通用/基础图标": ["2705", "274C", "26A0", "2139", "2757", "2753", "2754", "1F514"],
        "通用/背景元素": ["2728", "1F31F", "1F4AB", "1F308", "2744", "2729", "272A", "272B"],
        "通用/特效素材": ["1F4A5", "1F4A8", "1F300", "1F301", "1F302", "1F303", "1F304", "1F305"],
        "通用/装饰元素": ["1F380", "1F381", "1F382", "1F383", "1F384", "1F385", "1F386", "1F387"],
    }

    count = 0
    for category, codepoints in emojis.items():
        target_dir = ASSET_ROOT / category
        target_dir.mkdir(parents=True, exist_ok=True)
        existing = len(list(target_dir.glob("*.png")))

        for cp in codepoints:
            if existing + count >= 15:
                break
            url = f"https://raw.githubusercontent.com/hfg-gmuend/openmoji/master/color/618x618/{cp}.png"
            fname = f"emoji_{cp}.png"
            fpath = target_dir / fname
            if fpath.exists():
                continue
            data = download_url(url)
            if data and len(data) > 500:
                fpath.write_bytes(data)
                count += 1
            time.sleep(DELAY)

    print(f" 完成 (+{count})")
    return count


# ── 主流程 ──────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("矢量贴图素材批量下载 V2 — 多平台扩展版")
    print(f"目标目录: {ASSET_ROOT}")
    print("=" * 60)

    start = time.time()
    total = 0

    # 图标库
    total += download_lucide_icons()
    total += download_heroicons()
    total += download_feather_icons()

    # 插画库
    total += download_drawkit()
    total += download_opendoodles()
    total += download_manypixels()
    total += download_3dicons()

    # 综合平台
    total += download_storyset_all()
    total += download_undraw_all()
    total += download_openmoji_all()

    elapsed = time.time() - start

    # 汇总统计
    print("\n" + "=" * 60)
    print("下载统计：")
    print("-" * 60)

    grand_total = 0
    for category_dir in sorted(ASSET_ROOT.iterdir()):
        if not category_dir.is_dir():
            continue
        for sub_dir in sorted(category_dir.iterdir()):
            if not sub_dir.is_dir():
                continue
            count = len([f for f in sub_dir.iterdir() if f.suffix in (".png", ".svg")])
            if count > 0:
                print(f"  {category_dir.name}/{sub_dir.name}: {count} 个")
                grand_total += count

    print("-" * 60)
    print(f"  本次新增: {total} 个")
    print(f"  素材总计: {grand_total} 个")
    print(f"  耗时: {elapsed:.1f} 秒")
    print("=" * 60)


if __name__ == "__main__":
    main()
