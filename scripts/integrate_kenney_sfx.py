"""Integrate the downloaded Kenney sound set into the product SFX contract.

Reads ``assets/downloads/editorial-20260910/catalog.json`` (the download
manifest), copies the curated candidates into ``assets/sounds/`` and writes the
per-file metadata that ``video_editor_workflow._pick_sfx_asset`` validates.

Attribution and licence are taken from the catalog, never asserted as
project-owned: these are Kenney CC0 files, so ``rights_holder`` is Kenney and
``source_url`` points at the original asset page.

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
CATALOG = DOWNLOAD_DIR / "catalog.json"
ZH_INDEX = DOWNLOAD_DIR / "AI素材索引.json"
SOURCE_DIR = DOWNLOAD_DIR
TARGET_DIR = ROOT / "assets/sounds"

# Chinese function name (from AI素材索引.json ``display_name_zh``) -> sound
# profile ids.  Routing by the reviewer's own functional label is more reliable
# than guessing from the English file stem, and it keeps this curation
# reviewable: every profile below names the sound it is built from.
PROFILE_LABELS: dict[str, tuple[str, ...]] = {
    # Small pools are reserved for one profile each so no two beats share a
    # sound; the large switch/hover pools absorb the high-traffic profiles.
    "number_slam": ("敲击提示",),
    "price_zoom_card": ("落下提示",),
    "benefit_burst": ("放大展开",),
    "warning_shake": ("错误警示",),
    "logic_arrow": ("拨弦提示",),
    "road_push": ("缩小收起",),
    "compare_split_accent": ("刮擦效果",),
    "cta_burst": ("确认成功",),
    "result_stamp": ("打开入场",),
    "knowledge_pop_card": ("玻璃音色",),
    "process_marker": ("短促节拍",),
    "data_highlight_card": ("玻璃音色",),
    "keyword_pop": ("__legacy__",),
    "pop_soft": ("__legacy__",),
    "tick_soft": ("点击提示",),
    "whoosh_soft": ("滚动切换",),
    "impact_soft": ("__legacy__",),
    "success_ping": ("选择提示",),
    "warning_tick": ("故障效果",),
}

# Per-profile cap so one profile cannot consume the whole shortlist.
PER_PROFILE_LIMIT = 3


# Existing project-owned files kept for profiles that must not change sound.
_LEGACY_BY_PROFILE = {
    "keyword_pop": "sfx-pop.wav",
    "pop_soft": "sfx-pop.wav",
    "impact_soft": "sfx-boom.wav",
}


def _load_index() -> tuple[list[dict], dict[str, dict]]:
    """Return the sound entries and a catalogue keyed by file stem.

    ``AI素材索引.json`` is the reviewer's functional naming pass; it carries the
    Chinese description, purpose tags and usage restrictions that the English
    file names do not.  Those fields are copied into the product sidecar so the
    semantics survive integration and stay auditable.
    """

    index = json.loads(ZH_INDEX.read_text(encoding="utf-8"))
    catalog = {Path(entry["file"]).stem: entry for entry in json.loads(
        CATALOG.read_text(encoding="utf-8")
    )}
    return [item for item in index if item.get("kind") == "sound_effect"], catalog


def _label_of(entry: dict) -> str:
    """Extract the functional label from ``音效_<用途>_<包名>_<编号>``."""

    parts = str(entry.get("display_name_zh") or "").split("_")
    return parts[1] if len(parts) >= 2 else ""


def _target_name(entry: dict) -> str:
    stem = Path(entry["file"]).stem
    return f"sfx-kenney-{stem.replace('kenney-interface-', '').replace('kenney-ui-', '')}.wav"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write files")
    args = parser.parse_args()

    sounds, catalog = _load_index()
    by_label: dict[str, list[dict]] = {}
    for entry in sounds:
        by_label.setdefault(_label_of(entry), []).append(entry)

    mapping: dict[str, tuple[str, ...]] = {}
    written = 0
    missing: list[str] = []

    for profile, labels in PROFILE_LABELS.items():
        names: list[str] = []
        if "__legacy__" in labels:
            legacy = _LEGACY_BY_PROFILE.get(profile, "")
            if legacy and (TARGET_DIR / legacy).is_file():
                names.append(legacy)
            mapping[profile] = tuple(names)
            continue
        for label in labels:
            pool = by_label.get(label) or []
            if not pool:
                missing.append(f"{profile}:{label} (no sound carries this label)")
                continue
            # Shortest first: a talking-head beat needs punctuation, not a
            # 0.7s flourish that runs into the next sentence.
            pool = sorted(pool, key=lambda item: item.get("duration_seconds") or 0)
            for entry in pool:
                if len(names) >= PER_PROFILE_LIMIT:
                    break
                stem = Path(entry["file"]).stem
                catalog_entry = catalog.get(stem)
                if catalog_entry is None:
                    missing.append(f"{profile}:{stem} (absent from catalog)")
                    continue
                src = SOURCE_DIR / entry["file"]
                if not src.is_file():
                    missing.append(f"{profile}:{stem} (file absent)")
                    continue
                dst_name = _target_name(entry)
                if dst_name not in names:
                    names.append(dst_name)
                if not args.apply:
                    continue
                dst = TARGET_DIR / dst_name
                if not dst.is_file():
                    shutil.copyfile(src, dst)
                digest = hashlib.sha256(dst.read_bytes()).hexdigest()
                if digest != catalog_entry["sha256"]:
                    raise SystemExit(f"checksum mismatch after copy: {dst_name}")
                metadata = {
                    "asset_id": f"sfx-{stem}",
                    "kind": "sound_effect",
                    "name": stem,
                    # Chinese semantics from the review pass: what this sound is
                    # for, and where it must not be used.
                    "display_name_zh": entry.get("display_name_zh"),
                    "description_zh": entry.get("description_zh"),
                    "tags_zh": entry.get("tags_zh") or [],
                    "avoid_when_zh": entry.get("avoid_when_zh"),
                    "review_status": entry.get("review_status"),
                    "stored_name": dst_name,
                    "media_type": "audio/wav",
                    "source_provider": "Kenney",
                    "source_type": "third_party_free_asset",
                    "asset_origin": "third_party_cc0_asset",
                    "rights_holder": catalog_entry["creator"],
                    "authorization_status": "confirmed",
                    "rights_status": "cc0_public_domain",
                    "publish_licensed": True,
                    "license_name": "CC0-1.0",
                    "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                    "source_url": catalog_entry["source_page"],
                    "sha256": digest,
                    "duration_seconds": catalog_entry["duration_seconds"],
                    "suggested_category": catalog_entry.get("suggested_category"),
                }
                (TARGET_DIR / f"{dst_name[:-4]}.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                written += 1
        if names:
            mapping[profile] = tuple(names)

    print("=== proposed _SFX_PROFILE_FILES ===")
    for profile, names in mapping.items():
        print(f'    "{profile}": {names!r},')
    print(f"\nprofiles mapped : {len(mapping)}")
    print(f"files written   : {written}" if args.apply else "files written   : 0 (dry run)")
    print(f"candidates spent: {sum(len(v) for v in mapping.values())}")
    if missing:
        print("\nmissing/unavailable:")
        for item in missing[:20]:
            print(f"   - {item}")


if __name__ == "__main__":
    main()
