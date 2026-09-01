"""Create the real 21-second avatar acceptance sample through the local page API.

This is an offline/local acceptance helper.  It uses the already logged-in
demo customer session, the same uploaded source, and a reviewed transcript
corrected only for obvious ASR homophones.  It never calls a cloud renderer.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

import requests


BASE = "http://localhost:2001/api/v1"
ASR_PATH = Path("work/auto-fine-cut-adaptive-20260824-short-real/asr-full.json")
STATE_PATH = Path("work/auto-fine-cut-adaptive-20260824-short-real/page-run-state.json")


def build_review_segments() -> list[dict]:
    payload = json.loads(ASR_PATH.read_text(encoding="utf-8"))
    words = [
        word
        for segment in payload.get("segments") or []
        for word in segment.get("words") or []
        if float(word.get("end") or 0) <= 21.2
    ]
    reviewed = [
        (0.02, 3.42, "最近广州冒出了一个挺特别的参与模式", []),
        (3.88, 6.70, "街上有家烧烤店才开一个月", []),
        (6.70, 9.80, "附近五公里的居民基本都成了它的回头客", []),
        (10.48, 13.56, "80%的顾客还主动加了店里的私域", ["80%"]),
        (14.18, 16.90, "生意好得不行，我也跑去试了几次", []),
        (16.90, 19.10, "才明白这模式有多厉害", []),
        (19.64, 20.78, "普通烧烤店", []),
    ]
    result: list[dict] = []
    for start, end, text, emphasis in reviewed:
        selected_words = [
            {
                "start": float(word["start"]),
                "end": float(word["end"]),
                "word": str(word.get("word") or ""),
                "probability": word.get("probability"),
            }
            for word in words
            if float(word.get("end") or 0) > start
            and float(word.get("start") or 0) < end
        ]
        # The reviewed phrase text remains human-corrected, while its source
        # clock must be bounded by the actual ASR words.  This also prevents
        # a word at a sentence boundary from being rejected merely because
        # the sentence-level ASR start was rounded a few frames later.
        effective_start = (
            min(float(word["start"]) for word in selected_words)
            if selected_words
            else start
        )
        effective_end = (
            max(float(word["end"]) for word in selected_words)
            if selected_words
            else end
        )
        result.append(
            {
                "start": effective_start,
                "end": effective_end,
                "text": text,
                "words": selected_words,
                "emphasis_terms": emphasis[:1],
            }
        )
    return result


def main() -> None:
    session = requests.Session()
    login = session.post(f"{BASE}/auth/customer-login", json={"code": "DEMO-0815"}, timeout=30)
    login.raise_for_status()
    session.headers.update({"X-Customer-Token": login.json()["token"]})
    source_id = "upload:upload-a88ae01012"
    created = session.post(
        f"{BASE}/video-editor/batches",
        json={
            "source_ids": [source_id],
            "target_platform": "douyin",
            "subtitle_enabled": True,
            "subtitle_model": "base",
            "steps": [],
            "output_format": "mp4",
            "output_resolution": "720x1280",
            "output_fps": 30,
            "output_bitrate": "4M",
            "bgm_enabled": False,
            "bgm_volume": 0.0,
        },
        headers={"Idempotency-Key": f"accept-real-avatar-short-{uuid4().hex}"},
        timeout=60,
    )
    created.raise_for_status()
    batch = created.json()
    batch_id = batch["batch_id"]
    item = batch["items"][0]
    for _ in range(36):
        time.sleep(3)
        batch = session.get(f"{BASE}/video-editor/batches/{batch_id}", timeout=30).json()
        item = batch["items"][0]
        if item.get("status") in {"awaiting_subtitle_review", "failed"}:
            break
    if item.get("status") != "awaiting_subtitle_review":
        raise RuntimeError(f"local analysis did not reach review: {item.get('status')}")
    segments = build_review_segments()
    review = session.post(
        f"{BASE}/video-editor/batches/{batch_id}/items/{item['item_id']}/review",
        json={
            "subtitle_segments": segments,
            "enabled_plan_step_ids": ["vertical_fit", "subtitles"],
            "selected_title": "广州烧烤店的回头客模式",
            "selected_bgm_id": None,
            "broll_placement": None,
            "local_only": True,
            "smart_opening_enabled": False,
            "confirmed": True,
            "source_range_start": 0.0,
            "source_range_end": 21.0,
        },
        timeout=60,
    )
    review.raise_for_status()
    reviewed_item = review.json()["items"][0]
    export = session.post(
        f"{BASE}/video-editor/batches/{batch_id}/items/{reviewed_item['item_id']}/local-export",
        timeout=30,
    )
    export.raise_for_status()
    states: list[dict] = []
    final = None
    for _ in range(36):
        time.sleep(5)
        current = session.get(f"{BASE}/video-editor/batches/{batch_id}", timeout=30).json()
        current_item = current["items"][0]
        state = {
            "status": current_item.get("status"),
            "provider_stage": current_item.get("provider_stage"),
            "error_message": current_item.get("error_message"),
            "edit_task_id": current_item.get("edit_task_id"),
        }
        states.append(state)
        final = current_item
        if state["status"] in {"awaiting_output_confirmation", "failed"}:
            break
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                "batch_id": batch_id,
                "item_id": item["item_id"],
                "review_segments": segments,
                "review_status": review.status_code,
                "export_status": export.status_code,
                "states": states,
                "final_item": {
                    "status": final.get("status") if final else None,
                    "provider_stage": final.get("provider_stage") if final else None,
                    "error_message": final.get("error_message") if final else None,
                    "edit_task_id": final.get("edit_task_id") if final else None,
                    "result_media_url": final.get("result_media_url") if final else None,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "batch_id": batch_id,
                "item_id": item["item_id"],
                "review_status": review.status_code,
                "export_status": export.status_code,
                "final_status": final.get("status") if final else None,
                "edit_task_id": final.get("edit_task_id") if final else None,
                "error": final.get("error_message") if final else None,
                "state_path": str(STATE_PATH),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
