"""Integrate the reviewed Tabler outline icons into the product icon library.

Only the icon slugs the integration guide actually recommends are integrated,
so the style engine can never rotate through a picture that has no agreed
meaning.  Tabler Icons are MIT licensed; the sidecar records the creator, the
licence and the source repository, and never claims project ownership.

Run with ``--apply`` to write; without it the script only reports the plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "assets/downloads/business-icons-20260910"
# The full 67-icon rasterised set; ``png/`` only holds the first 24.
PNG_DIR = SOURCE_DIR / "tabler-outline-png"
TARGET_DIR = ROOT / "assets" / "icons"

CREATOR = "Paweł Kuna / Tabler Icons contributors"
LICENSE_NAME = "MIT"
LICENSE_URL = "https://github.com/tabler/tabler-icons/blob/master/LICENSE"
SOURCE_PAGE = "https://github.com/tabler/tabler-icons"

# The integration guide's first batch.  Each entry maps the vendor slug to the
# editorial meanings it may serve; anything not listed here is deliberately not
# offered to the style engine.
CURATED: dict[str, tuple[str, ...]] = {
    "check": ("positive",),
    "circle-check": ("positive",),
    "clipboard-check": ("positive",),
    "x": ("negative",),
    "circle-x": ("negative",),
    "alert-triangle": ("negative",),
    "alert-circle": ("negative",),
    "map-pin": ("process",),
    "tag": ("number",),
    "percentage": ("number",),
    "coin": ("number",),
    "discount": ("offer_compare",),
    "chart-line": ("number",),
    "chart-bar": ("number",),
    "database": ("process",),
    "messages": ("cta",),
    "message": ("cta",),
    "phone-call": ("cta",),
    "mail": ("cta",),
    "users": ("process",),
    "users-group": ("process",),
    "user-check": ("positive",),
    "building-factory": ("process",),
    "building-store": ("process",),
    "arrow-right": ("cta",),
    "route": ("process",),
    "point": ("process",),
    "target": ("positive",),
    "rocket": ("positive",),
    "bulb": ("positive",),
    "star": ("positive",),
    "shopping-cart": ("offer_compare",),
    "package": ("process",),
    "lock": ("process",),
    "shield-check": ("positive",),
    "timeline": ("process",),
    "git-compare": ("offer_compare",),
    "report-analytics": ("number",),
    # Needed by the info-pill role map: a route mark for steps, a document mark
    # for contracts and lists, and a neutral mark when no role is available.
    "file-text": ("process",),
    "file-description": ("process",),
    "info-circle": ("process",),
    "help-circle": ("process",),
    "checklist": ("positive",),
    "list-check": ("positive",),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write files")
    args = parser.parse_args()

    written = 0
    missing: list[str] = []
    for slug, kinds in sorted(CURATED.items()):
        source = PNG_DIR / f"{slug}.png"
        if not source.is_file():
            missing.append(slug)
            continue
        if not args.apply:
            written += 1
            continue
        TARGET_DIR.mkdir(parents=True, exist_ok=True)
        stored_name = f"icon-tabler-{slug}.png"
        target = TARGET_DIR / stored_name
        shutil.copyfile(source, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        metadata = {
            "asset_id": f"icon-tabler-{slug}",
            "kind": "label_icon",
            "name": slug,
            "editorial_kinds": list(kinds),
            "stored_name": stored_name,
            "media_type": "image/png",
            "source_provider": "Tabler Icons",
            "source_type": "third_party_free_asset",
            "asset_origin": "third_party_mit_asset",
            "rights_holder": CREATOR,
            "attribution_required": True,
            "attribution_text": f"Icons: {CREATOR} — {SOURCE_PAGE} — MIT",
            "authorization_status": "confirmed",
            "rights_status": "mit",
            "publish_licensed": True,
            "license_name": LICENSE_NAME,
            "license_url": LICENSE_URL,
            "source_page": SOURCE_PAGE,
            "sha256": digest,
            "usage": "label_furniture_icon_15_to_25_percent_of_label_width",
        }
        (TARGET_DIR / f"{stored_name[:-4]}.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written += 1

    print(f"icons planned : {len(CURATED)}")
    print(f"files written : {written}" if args.apply else "files written : 0 (dry run)")
    if missing:
        print("missing source PNGs:")
        for item in missing:
            print(f"   - {item}")


if __name__ == "__main__":
    main()
