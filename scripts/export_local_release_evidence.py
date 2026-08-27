"""Export a local-only Video Editor acceptance bundle from an existing job.

The script talks only to the local API, reads the already completed job, and
does not create or submit any cloud task.  It deliberately preserves provider
license/source fields in the evidence bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any


def _get(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    return json.loads(result.stdout or "{}")


def _keyframes(path: Path, output: Path, duration: float) -> list[str]:
    seconds = sorted({0.0, round(duration * 0.34, 3), round(duration * 0.68, 3), max(0.0, round(duration - 0.5, 3))})
    files: list[str] = []
    for index, second in enumerate(seconds):
        target = output / f"keyframe-{index:02d}-{str(second).replace('.', '_')}s.png"
        result = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-ss",
                str(second),
                "-i",
                str(path),
                "-frames:v",
                "1",
                str(target),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode or not target.is_file():
            raise RuntimeError(result.stderr[-2000:])
        files.append(str(target))
    return files


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:2001")
    parser.add_argument("--batch", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    api_key = os.getenv("VIDEOINSIGHT_API_KEY", "")
    if not api_key:
        env_file = root / ".env"
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("API_KEY="):
                api_key = line.split("=", 1)[1].strip()
                break
    if not api_key:
        raise RuntimeError("API_KEY missing")
    base = args.base.rstrip("/")
    headers = {"X-API-Key": api_key}
    login = _post(f"{base}/api/v1/auth/customer-login", headers, {"code": "DEMO-0815"})
    headers["Authorization"] = f"Bearer {login['token']}"
    job = _get(f"{base}/api/v1/video-editor/jobs/{args.job}", headers)
    batch = _get(f"{base}/api/v1/video-editor/batches/{args.batch}", headers)
    item = next(item for item in batch.get("items", []) if item.get("item_id") == args.item)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    result_path = Path(str(job.get("result_path") or ""))
    if not result_path.is_file():
        # The public job payload normally exposes only media_url.  The local
        # repository path is stable and is already recorded in task metadata.
        candidate = root / "data" / "video_edits" / f"{args.job}.mp4"
        result_path = candidate
    if not result_path.is_file():
        raise RuntimeError(f"local MP4 not found: {result_path}")
    quality = job.get("quality_report") or {}
    probe = _probe(result_path)
    duration = float((probe.get("format") or {}).get("duration") or quality.get("duration_seconds") or 0)
    keyframe_paths = _keyframes(result_path, output, duration)
    edit_plan = item.get("edit_plan") or {}
    director_plan = edit_plan.get("director_plan") or {}
    shot_plan = edit_plan.get("shot_plan") or {}
    review_broll = (item.get("review_snapshot") or {}).get("broll")
    timeline_brolls = edit_plan.get("release_brolls") or (
        [review_broll] if isinstance(review_broll, dict) and review_broll else []
    )
    manifest_path = result_path.with_suffix(".subtitle-manifest.json")
    subtitle_manifest = {}
    if manifest_path.is_file():
        subtitle_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    playback_rate = float(subtitle_manifest.get("playback_rate") or 1.0)
    opening_duration = float(
        (quality.get("edl_execution_gate") or {}).get("opening_duration_seconds")
        or 0.0
    )
    final_output_brolls = []
    for broll in timeline_brolls:
        if not isinstance(broll, dict):
            continue
        source_start = float(broll.get("start") or 0.0)
        source_end = float(broll.get("end") or 0.0)
        final_output_brolls.append(
            {
                **broll,
                "source_start": source_start,
                "source_end": source_end,
                "final_output_start": round(
                    opening_duration + source_start / playback_rate, 3
                ),
                "final_output_end": round(
                    opening_duration + source_end / playback_rate, 3
                ),
            }
        )
    _write(output / "quality-report.json", quality)
    _write(
        output / "timeline.json",
        {
            "timeline_version": "local-release-timeline-v1",
            "clock": "final_output",
            "output_duration_seconds": duration,
            "source_id": job.get("source_id"),
            "source_media_identity": (
                quality.get("transcript_source_identity_gate") or {}
            ).get("source_media_identity") or {},
            "source_range": (
                quality.get("edl_execution_gate") or {}
            ).get("declared_source_range") or {},
            "shot_plan": shot_plan,
            "director_plan": director_plan,
            "release_brolls": timeline_brolls,
            "final_output_brolls": final_output_brolls,
            "clock_mapping": {
                "source_to_final": "final = opening_duration + source / playback_rate",
                "playback_rate": playback_rate,
                "opening_duration_seconds": opening_duration,
            },
            "subtitle_timeline": quality.get("subtitle_timeline"),
            "asset_matching": director_plan.get("asset_matching") or {},
        },
    )
    _write(
        output / "provenance.json",
        {
            "provenance_version": "local-release-provenance-v1",
            "cloud_calls": 0,
            "publish_action": "not_performed",
            "source_video": job.get("source_id"),
            "broll_provenance": quality.get("broll_provenance") or [],
            "generated_asset_policy": {
                "generated_for_local_acceptance": True,
                "publish_licensed": False,
                "cannot_satisfy_publish_rights_gate": True,
            },
        },
    )
    _write(
        output / "ffprobe.json",
        {
            "path": str(result_path),
            "sha256": _sha256(result_path),
            "probe": probe,
        },
    )
    subtitle_artifacts: dict[str, Any] = {}
    for suffix, label in ((".ass", "ass"), (".subtitle-manifest.json", "manifest")):
        sidecar = result_path.with_suffix(suffix)
        if sidecar.is_file():
            target = output / ("rendered.ass" if label == "ass" else "subtitle-manifest.json")
            shutil.copy2(sidecar, target)
            subtitle_artifacts[label] = {
                "path": str(target),
                "sha256": _sha256(sidecar),
            }
    _write(output / "subtitle-artifacts.json", subtitle_artifacts)
    _write(output / "keyframes.json", {"files": keyframe_paths})
    print(json.dumps({"output": str(result_path), "evidence": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
