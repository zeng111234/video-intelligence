"""Audit saved crawler acceptance evidence without opening a platform page.

This is deliberately an evidence auditor, not a crawler runner.  It never
changes SQLite safety state, starts a browser, or retries a platform request.
An ``incomplete`` result is expected until a fresh low-frequency Bilibili run
proves the remaining page-level gates.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


REQUIRED_CSV_COLUMNS = {
    "关键词",
    "平台",
    "标题",
    "作者",
    "链接",
    "时间",
    "互动",
    "热度",
    "相关性",
    "质量说明",
}
FORBIDDEN_CSV_TERMS = {"cookie", "token", "密码", "代理", "浏览器配置"}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def audit(root: Path) -> dict[str, object]:
    evidence = root / "work" / "crawler-acceptance-20260824"
    v5 = _read_json(evidence / "cold-bilibili-api-v5.json")
    v6 = _read_json(evidence / "v6-safety-cap-api.json")
    run = v5["platform_run"]
    timings = run.get("stage_timings_ms") or {}
    funnel_total = (
        int(run.get("direct_match", 0))
        + int(run.get("relevance_filtered", 0))
        + int(run.get("duration_filtered", 0))
        + int(run.get("invalid_fields", 0))
    )

    csv_path = evidence / "cold-bilibili-export.csv"
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        rows = list(reader)
    csv_text = csv_path.read_text(encoding="utf-8-sig").casefold()

    checks = {
        "cold_scan_under_60_seconds": int(timings.get("total_ms", 0)) <= 60_000,
        "funnel_adds_up": funnel_total == int(run.get("parsed", 0)),
        "cold_cache_is_false": run.get("cache_hit") is False,
        "safety_pause_is_recorded": (
            v6.get("status") == "failed"
            and not v6.get("platform_runs")
            and "安全" in str(v6.get("safety_interpretation", ""))
        ),
        "csv_columns_are_complete": REQUIRED_CSV_COLUMNS <= columns,
        "csv_has_no_sensitive_fields": not any(
            term in csv_text for term in FORBIDDEN_CSV_TERMS
        ),
        "page_screenshot_exists": (
            evidence / "crawler-optimized-live.png"
        ).exists(),
    }
    remaining = {
        "final_30_candidates": int(run.get("retained", 0)) >= 30,
        # v5 records a backend response stage, not a browser-rendered partial
        # result. Keep this false until a page-level observation is saved.
        "first_candidate_visible_in_page_under_12_seconds": False,
        "fresh_v6_cold_run": v6.get("status") != "failed",
    }
    return {
        "status": "verified_partial" if all(checks.values()) else "evidence_invalid",
        "checks": checks,
        "remaining_gates": remaining,
        "v5": {
            "raw_discovered": run.get("raw_discovered"),
            "parsed": run.get("parsed"),
            "direct_match": run.get("direct_match"),
            "retained": run.get("retained"),
            "adapter_total_ms": timings.get("total_ms"),
            "csv_rows": len(rows),
        },
    }


def main() -> int:
    result = audit(Path(__file__).resolve().parents[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "verified_partial" else 1


if __name__ == "__main__":
    sys.exit(main())
