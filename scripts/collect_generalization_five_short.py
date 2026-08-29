"""Collect immutable evidence for the bounded five-sample run."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from project.backend.app.core.deps import get_repository  # noqa: E402


BASE = ROOT / "work/auto-fine-cut-generalization-20260825/5-samples-20250825"


def parse(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def main() -> None:
    repo = get_repository()
    index = json.loads((BASE / "five-sample-run-index.json").read_text(encoding="utf-8"))
    for entry in index:
        case_dir = BASE / str(entry["case_id"])
        task_id = entry.get("task_id")
        if not task_id:
            continue
        task = repo.get_task(task_id)
        if task is None:
            continue
        outputs = dict(task.outputs or {})
        (case_dir / "task.json").write_text(
            json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        quality = parse(outputs.get("quality_report")) or {}
        (case_dir / "quality-report.json").write_text(
            json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for key, filename in (
            ("edit_plan_json", "timeline.json"),
            ("shot_plan_json", "director-plan.json"),
            ("brolls_json", "output-added-visual.json"),
            ("source_media_identity_json", "source-identity.json"),
        ):
            value = parse(outputs.get(key))
            if value is not None:
                (case_dir / filename).write_text(
                    json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        result_path = Path(str(task.result_path or ""))
        if result_path.is_file():
            entry["result_path"] = str(result_path.resolve())
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(result_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            (case_dir / "ffprobe.json").write_text(probe.stdout, encoding="utf-8")
            contact = case_dir / "contact-sheet.jpg"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(result_path),
                    "-vf",
                    "fps=1/3,scale=240:-1,tile=7x1:padding=4:margin=4",
                    "-frames:v",
                    "1",
                    str(contact),
                ],
                check=False,
            )
        entry["task_status"] = str(task.status)
        entry["quality_report_path"] = str((case_dir / "quality-report.json").resolve())
        entry["output_added_visual"] = quality.get("output_added_visual", {})
        entry["subtitle_timeline"] = quality.get("subtitle_timeline", {})
    (BASE / "five-sample-run-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(index, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
