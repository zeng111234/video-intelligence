"""Integrate the reviewed OpenMoji sticker set into the product sticker library.

Source: ``assets/downloads/editorial-20260910/prep/png`` (512x512 RGBA, already
rasterised from the vendor SVGs) plus ``AI素材索引.json`` for the Chinese
function names.

Licensing is stricter than the sound set: OpenMoji is **CC BY-SA 4.0**, so every
sidecar must carry the creator, the licence name and a link to the licence, and
must never be labelled as project-owned.  The vector originals stay in the
download tree (per the download README); this script copies only the PNGs.

Run with ``--apply`` to write; without it the script only reports the plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_DIR = ROOT / "assets/downloads/editorial-20260910"
ZH_INDEX = DOWNLOAD_DIR / "AI素材索引.json"
PNG_DIR = DOWNLOAD_DIR / "prep/png"
TARGET_DIR = ROOT / "assets/stickers"

LICENSE_NAME = "CC-BY-SA-4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
CREATOR = "OpenMoji contributors"
SOURCE_PAGE = "https://openmoji.org/"

# Sticker slug -> editorial style it can serve.  Derived from the reviewer's
# Chinese function names in ``AI素材索引.json``.  A slug that is absent here is
# still integrated (with full metadata) but is not offered to the style engine,
# so the engine never reaches for a picture that does not match the sentence.
KIND_BY_SLUG: dict[str, str] = {
    # negation / risk: must be the strongest and most distinct set
    "cancel": "negative",
    "decline": "negative",
    "warning": "negative",
    "attention": "negative",
    "thumbs-down": "negative",
    # confirmation / result
    "confirm": "positive",
    "thumbs-up": "positive",
    "target": "positive",
    "handshake": "positive",
    "celebration": "positive",
    "star": "positive",
    "sparkles": "positive",
    "idea": "positive",
    "rocket": "positive",
    "fire": "positive",
    # numbers / money
    "label": "number",
    "money-bag": "number",
    "banknotes": "number",
    "growth": "number",
    "chart": "number",
    # process / object / place
    "factory": "process",
    "store": "process",
    "location": "process",
    "link": "process",
    "repeat": "process",
    "search": "process",
    "key": "process",
    "lock": "process",
    "laptop": "process",
    "folder": "process",
    "package": "process",
    "calendar": "process",
    "hourglass": "process",
    "clipboard": "process",
    "memo": "process",
    "customers": "process",
    # action / contact
    "phone": "cta",
    "telephone": "cta",
    "chat": "cta",
    "bell": "cta",
    "announcement": "cta",
    "right-arrow": "cta",
    # offers
    "gift": "offer_compare",
    "shopping-cart": "offer_compare",
    "payment-card": "offer_compare",
    "email": "cta",
    "question": "process",
    "alarm": "process",
}


def _load_index() -> dict[str, dict]:
    entries = json.loads(ZH_INDEX.read_text(encoding="utf-8"))
    return {
        Path(entry["file"]).stem: entry
        for entry in entries
        if entry.get("kind") == "sticker_svg"
    }


def _target_name(stem: str, label_zh: str) -> str:
    return f"sticker-openmoji-{stem}.png"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write files")
    args = parser.parse_args()

    index = _load_index()
    written = 0
    missing: list[str] = []
    plan: list[tuple[str, str, str]] = []

    for slug, entry in sorted(index.items()):
        png = PNG_DIR / f"{slug}.png"
        if not png.is_file():
            missing.append(f"{slug} (no rasterised PNG)")
            continue
        label_zh = str(entry.get("display_name_zh") or "").strip()
        target_name = _target_name(slug, label_zh)
        kind = KIND_BY_SLUG.get(slug, "positive")
        plan.append((slug, kind, target_name))
        if not args.apply:
            continue
        TARGET_DIR.mkdir(parents=True, exist_ok=True)
        target = TARGET_DIR / target_name
        shutil.copyfile(png, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        metadata = {
            "asset_id": f"sticker-openmoji-{slug}",
            "kind": "sticker",
            "name": slug,
            "editorial_kind": kind,
            "display_name_zh": label_zh,
            "description_zh": entry.get("description_zh"),
            "tags_zh": entry.get("tags_zh") or [],
            "avoid_when_zh": entry.get("avoid_when_zh"),
            "review_status": entry.get("review_status"),
            "stored_name": target_name,
            "media_type": "image/png",
            "width": 512,
            "height": 512,
            "source_provider": "OpenMoji",
            "source_type": "third_party_free_asset",
            "asset_origin": "third_party_cc_by_sa_asset",
            "rights_holder": CREATOR,
            "attribution_required": True,
            "attribution_text": f"Stickers: {CREATOR} — {SOURCE_PAGE} — {LICENSE_NAME}",
            "authorization_status": "confirmed",
            "rights_status": "cc_by_sa_4_0",
            "publish_licensed": True,
            "license_name": LICENSE_NAME,
            "license_url": LICENSE_URL,
            "source_url": SOURCE_PAGE,
            "sha256": digest,
        }
        (TARGET_DIR / f"{target_name[:-4]}.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written += 1

    print(f"stickers planned : {len(plan)}")
    print(f"files written    : {written}" if args.apply else "files written    : 0 (dry run)")
    kinds: dict[str, int] = {}
    for _slug, kind, _name in plan:
        kinds[kind] = kinds.get(kind, 0) + 1
    print("by editorial kind:")
    for kind, count in sorted(kinds.items()):
        print(f"   {kind:<14} {count}")
    if missing:
        print("\nmissing:")
        for item in missing:
            print(f"   - {item}")


if __name__ == "__main__":
    main()

