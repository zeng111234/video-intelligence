"""补充下载 Lucide、Feather、Heroicons 图标库。"""

from __future__ import annotations
import hashlib
import os
import re
import time
import urllib.request
from pathlib import Path

os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "data" / "creative_assets" / "口播矢量素材"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

CATEGORY_KEYWORDS = {
    "科普教育/物理": ["atom","battery","bolt","charge","electric","energy","field","force","gravity","laser","light","magnet","nuclear","particle","photon","power","quantum","spark","volt","wave","zap","plug","cable","switch","circuit"],
    "科普教育/化学": ["beaker","flask","molecule","lab","experiment","chemical","test-tube","alembic","acid","base","crystal","element","reaction","atom","bond","compound"],
    "科普教育/生物": ["biology","dna","cell","microscope","bacteria","virus","gene","nature","animal","plant","heart","brain","bone","blood","organ","protein","leaf","tree","flower","fish","bird","bug","insect"],
    "科普教育/天文": ["space","planet","rocket","star","moon","sun","galaxy","satellite","orbit","cosmos","telescope","meteor","comet","asteroid","nebula","astronaut","mars","eclipse","universe","telescope","observatory"],
    "科普教育/通用": ["science","education","book","learn","study","school","knowledge","research","discovery","invention","formula","math","geometry","logic","academic","professor","student","teacher","library","university","graduation","pencil","ruler","compass","protractor"],
    "财金讲解/数据图表": ["chart","graph","analytics","data","trend","statistic","diagram","analysis","dashboard","report","metric","gauge","bar-chart","pie-chart","line-chart","area-chart","histogram","scatter"],
    "财金讲解/货币交易": ["money","coin","cash","dollar","currency","banknote","payment","wallet","purse","bank","atm","withdraw","deposit","transfer","exchange","forex","trade","transaction","receipt","invoice","bill","check","payroll","salary","income","revenue","profit","loss","expense","cost","budget","credit-card","banknotes"],
    "财金讲解/投资理财": ["invest","savings","growth","profit","fund","stock","portfolio","retire","pension","insurance","bond","dividend","yield","interest","asset","liability","equity","capital","venture","startup","market","broker","trader","investor","shareholder","percent","trending-up","trending-down"],
    "财金讲解/金融科技": ["fintech","bitcoin","crypto","blockchain","digital","mobile","online","ecommerce","token","ledger","mining","hash","node","network","api","cloud","ai","robot","automation","server","database","code","terminal","wifi","signal","bluetooth","usb","cpu","hard-drive","monitor","smartphone"],
    "财金讲解/人物场景": ["business","office","meeting","corporate","professional","manager","team","group","collaboration","partnership","handshake","deal","contract","negotiation","presentation","conference","interview","career","job","work","briefcase","user","users","person","people","contact","address-book"],
    "爱国叙事/国家象征": ["flag","emblem","national","patriotic","symbol","monument","landmark","capitol","government","president","leader","nation","country","state","republic","constitution","law","justice","liberty","freedom","independence","shield","scale","balance"],
    "爱国叙事/军事元素": ["military","army","soldier","defense","shield","weapon","tank","jet","aircraft","missile","bomb","gun","rifle","sword","armor","helmet","medal","badge","troop","veteran","crosshair","target","radar","lock","key","siren","alert","warning"],
    "爱国叙事/红色视觉": ["red","ribbon","heart","star","banner","celebration","festival","party","confetti","balloon","firework","sparkle","glow","shine","bright","passion","love","rose","fire","flame","sun","sunrise","sunset"],
    "爱国叙事/历史叙事": ["history","heritage","ancient","classic","vintage","retro","antique","archaeology","fossil","ruin","temple","pyramid","castle","palace","fortress","tower","statue","sculpture","artifact","treasure","crown","throne","scroll","book-open","compass","map","globe","anchor"],
    "爱国叙事/现代成就": ["modern","city","skyscraper","bridge","train","highway","technology","building","architecture","construction","engineering","infrastructure","transportation","factory","industry","manufacturing","car","truck","bus","airplane","ship","boat","helicopter","camera","video","radio","broadcast","antenna","signal"],
    "通用/特效素材": ["effect","animation","motion","dynamic","explosion","burst","splash","ripple","flash","glitch","blur","zoom","fade","dissolve","wipe","slide","spin","rotate","shuffle","refresh","reload","sync","loop","repeat","rewind","fast-forward","skip","play","pause","stop"],
    "通用/装饰元素": ["decoration","ornament","flourish","divider","separator","frame","border","outline","shadow","glow","highlight","accent","badge","label","tag","sticker","pattern","texture","grid","layout","columns","rows","sidebar","panel","card","module","block","section"],
    "通用/基础图标": ["icon","symbol","sign","mark","check","warning","info","arrow","direction","navigation","menu","button","link","search","find","filter","sort","order","list","grid","table","form","input","output","save","load","file","folder","document","page","print","share","copy","cut","paste","undo","redo","settings","cog","gear","tool","wrench","hammer","screwdriver","scissors"],
}


def classify(name: str) -> str:
    n = name.lower().replace("_", "-")
    for cat, kws in CATEGORY_KEYWORDS.items():
        for kw in kws:
            if kw in n:
                return cat
    return "通用/基础图标"


def download_svg(url: str, target_dir: Path, name: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with opener.open(req, timeout=10) as resp:
            data = resp.read(50000)
        if not data or b"<svg" not in data[:500].lower():
            return False
        target_dir.mkdir(parents=True, exist_ok=True)
        h = hashlib.md5(name.encode()).hexdigest()[:8]
        fpath = target_dir / f"{name}_{h}.svg"
        if fpath.exists():
            return False
        fpath.write_bytes(data)
        return True
    except Exception:
        return False


def main():
    print("=" * 60)
    print("补充下载图标库：Lucide / Feather / Heroicons")
    print("=" * 60)
    total = 0

    # ── Lucide ──
    print("\n[Lucide] 从 CDN 下载...", end="", flush=True)
    try:
        req = urllib.request.Request("https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/", headers={"User-Agent": UA})
        with opener.open(req, timeout=15) as resp:
            html = resp.read(3000000).decode("utf-8", errors="ignore")
        names = list(set(re.findall(r"/lucide-static@[^/]+/icons/([a-z0-9-]+)\.svg", html)))
        print(f" 找到 {len(names)} 个", end="", flush=True)
    except Exception as e:
        names = []
        print(f" 失败: {e}")

    c = 0
    for name in names[:400]:
        cat = classify(name)
        target = ASSET_ROOT / cat
        if len(list(target.glob("*.svg"))) >= 18:
            continue
        url = f"https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/{name}.svg"
        if download_svg(url, target, name):
            c += 1
            if c % 50 == 0:
                print(".", end="", flush=True)
        time.sleep(0.12)
    print(f" +{c}")
    total += c

    # ── Feather ──
    print("\n[Feather] 从 unpkg 下载...", end="", flush=True)
    try:
        req = urllib.request.Request("https://unpkg.com/browse/feather-icons@latest/dist/icons/", headers={"User-Agent": UA})
        with opener.open(req, timeout=15) as resp:
            html = resp.read(3000000).decode("utf-8", errors="ignore")
        names = list(set(re.findall(r"/feather-icons@[^/]+/dist/icons/([a-z0-9-]+)\.svg", html)))
        print(f" 找到 {len(names)} 个", end="", flush=True)
    except Exception as e:
        names = []
        print(f" 失败: {e}")

    c = 0
    for name in names[:300]:
        cat = classify(name)
        target = ASSET_ROOT / cat
        if len(list(target.glob("*.svg"))) >= 18:
            continue
        url = f"https://unpkg.com/feather-icons@latest/dist/icons/{name}.svg"
        if download_svg(url, target, name):
            c += 1
            if c % 30 == 0:
                print(".", end="", flush=True)
        time.sleep(0.12)
    print(f" +{c}")
    total += c

    # ── Heroicons ──
    print("\n[Heroicons] 从 GitHub 下载...", end="", flush=True)
    heroicons = [
        "arrow-path","arrow-trending-up","arrow-trending-down","arrow-up","arrow-down",
        "arrow-left","arrow-right","arrows-pointing-in","arrows-pointing-out",
        "banknotes","bell","bookmark","briefcase","building-library","building-office",
        "calculator","calendar","camera","chart-bar","chart-pie","check-circle",
        "clock","cloud","code-bracket","cog","command-line","computer-desktop",
        "currency-dollar","device-phone-mobile","document","envelope","eye",
        "face-smile","film","finger-print","fire","flag","folder","gift",
        "globe-alt","hand-thumb-up","heart","home","identification","inbox",
        "information-circle","key","language","lifebuoy","light-bulb","link",
        "lock-closed","magnifying-glass","map","megaphone","microphone",
        "musical-note","newspaper","no-symbol","paint-brush","paper-airplane",
        "paper-clip","pencil","phone","photo","play","presentation-chart-bar",
        "printer","puzzle-piece","question-mark-circle","queue-list","radio",
        "rocket-launch","scale","scissors","server","share","shield-check",
        "shopping-bag","shopping-cart","signal","sparkles","speaker-wave",
        "star","stop","sun","swatch","table-cells","tag","ticket","trophy",
        "truck","tv","user","user-group","video-camera","wallet","wifi",
        "window","wrench","wrench-screwdriver","academic-cap","adjustments-horizontal",
        "archive-box","arrow-top-right-on-square","at-symbol","beaker",
        "bug-ant","building-storefront","chat-bubble-bottom-center",
        "chevron-down","chevron-up","chevron-left","chevron-right",
        "circle-stack","clipboard","cloud-arrow-up","cog-6-tooth",
        "currency-rupee","currency-yen","currency-pound","currency-euro",
        "exclamation-triangle","eye-slash","face-frown","globe-americas",
        "hand-raised","hand-thumb-down","hashtag","language","lifebuoy",
        "link","list-bullet","lock-open","minus","moon","musical-note",
        "paint-brush","paper-airplane","pencil-square","plus","power",
        "question-mark-circle","rss","signal-slash","sparkles","speaker-x-mark",
        "square-2-stack","squares-2x2","squares-plus","text","thumb-up",
        "trash","trophy","truck","tv","user-circle","video-camera-slash",
        "view-columns","viewfinder-circle","volume-up","wallet","wrench",
        "x-mark","academic-cap","adjustments","archive","arrow-circle-down",
        "arrow-circle-left","arrow-circle-right","arrow-circle-up",
        "arrow-sm-left","arrow-sm-right","arrow-sm-up","arrow-sm-down",
    ]

    c = 0
    for name in heroicons:
        cat = classify(name)
        target = ASSET_ROOT / cat
        if len(list(target.glob("*.svg"))) >= 18:
            continue
        url = f"https://raw.githubusercontent.com/tailwindlabs/heroicons/master/optimized/24/outline/{name}.svg"
        if download_svg(url, target, name):
            c += 1
            if c % 30 == 0:
                print(".", end="", flush=True)
        time.sleep(0.12)
    print(f" +{c}")
    total += c

    # ── 最终统计 ──
    print("\n" + "=" * 60)
    print("最终统计：")
    print("-" * 60)
    grand = 0
    for cat_dir in sorted(ASSET_ROOT.iterdir()):
        if not cat_dir.is_dir():
            continue
        for sub in sorted(cat_dir.iterdir()):
            if not sub.is_dir():
                continue
            cnt = len([f for f in sub.iterdir() if f.suffix in (".png", ".svg")])
            if cnt > 0:
                print(f"  {cat_dir.name}/{sub.name}: {cnt}")
                grand += cnt
    print("-" * 60)
    print(f"  本次新增: {total}")
    print(f"  素材总计: {grand}")
    print("=" * 60)


if __name__ == "__main__":
    main()
