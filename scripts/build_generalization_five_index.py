"""Build the human-review index for the five selected short samples."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "work/auto-fine-cut-generalization-20260825/5-samples-20250825"

SELECTED = [
    ("commercial-number-wechat", "商业数字/产品观点", "无复杂贴图；保留原生字幕，适合盲测"),
    ("product-intro-wechat", "商业经营/场景观点", "无复杂贴图；保留原生字幕，适合盲测"),
    ("abstract-viewpoint-wechat", "故事/普通观点", "宠物场景口播；无程序外复杂包装"),
    ("knowledge-white-shirt", "知识/经营观点", "无复杂贴图；保留原生字幕，适合盲测"),
    ("tutorial-original", "教程/流程", "淘汰为程序新增视觉证据：原片自带猫咪插片"),
]


def main() -> None:
    result = []
    for case_id, category, source_note in SELECTED:
        case_dir = BASE / case_id
        task = json.loads((case_dir / "task.json").read_text(encoding="utf-8"))
        quality = json.loads((case_dir / "quality-report.json").read_text(encoding="utf-8"))
        result_path = Path(str(task.get("result_path") or ""))
        result_sha256 = None
        if result_path.is_file():
            digest = hashlib.sha256()
            with result_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            result_sha256 = digest.hexdigest()
        output_added = quality.get("output_added_visual") or {}
        result.append(
            {
                "case_id": case_id,
                "category": category,
                "source_note": source_note,
                "route": (task.get("outputs") or {}).get("workflow"),
                "template_id": (task.get("outputs") or {}).get("template_id"),
                "source_path": str((case_dir / "source.mp4").resolve()),
                "result_path": task.get("result_path"),
                "result_sha256": result_sha256,
                "ffprobe": str((case_dir / "ffprobe.json").resolve()),
                "contact_sheet": str((case_dir / "contact-sheet.jpg").resolve()),
                "timeline": str((case_dir / "timeline.json").resolve()),
                "director_plan": str((case_dir / "director-plan.json").resolve()),
                "quality_report": str((case_dir / "quality-report.json").resolve()),
                "output_added_visual": output_added,
                "broll_provenance": quality.get("broll_provenance") or [],
                "subtitle_sync_passed": quality.get("subtitle_sync_passed"),
                "subtitle_timeline_passed": (quality.get("subtitle_timeline") or {}).get("passed"),
                "word_p95_ms": (quality.get("subtitle_timeline") or {}).get("word_p95_ms"),
                "mapping_error_frames": (quality.get("subtitle_timeline") or {}).get("mapping_error_frames"),
                "visual_release_passed": quality.get("visual_release_passed"),
                "publish_claim_allowed": quality.get("publish_claim_allowed"),
                "real_broll_event_count": quality.get("real_broll_event_count", 0),
                "broll_modes": quality.get("broll_modes") or {},
                "real_broll_coverage_ratio": quality.get("real_broll_coverage_ratio", 0),
                "failure_reasons": {
                    "quality_passed": quality.get("passed"),
                    "creative_passed": quality.get("creative_passed"),
                    "visual_gate": (quality.get("visual_gate_policy") or {}).get("passed"),
                    "subtitle": quality.get("subtitle_sync_passed"),
                },
            }
        )
    output = BASE / "five-sample-comparison-index.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"index": str(output.resolve()), "samples": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
