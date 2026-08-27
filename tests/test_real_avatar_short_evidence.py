from __future__ import annotations

from pathlib import Path

import scripts.render_real_avatar_short_evidence as evidence
from scripts.accept_real_avatar_short import build_review_segments
from src.services.video_editor_cloud import build_business_talking_head_overlay_preview


def test_rendered_ass_does_not_add_a_leading_comma_to_subtitle_text(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(evidence, "EVIDENCE", tmp_path)

    ass_path = evidence.write_ass(
        [
            {
                "start": 0.0,
                "end": 1.2,
                "text": "最近广州冒出了",
                "emphasis": "广州",
            }
        ]
    )

    dialogue = ass_path.read_text(encoding="utf-8").split("Dialogue: ", 1)[1]
    assert ",Default,,0,0,0,{\\fad(120,0)}" in dialogue
    assert ",Default,,0,0,0,,{\\fad(120,0)}" not in dialogue


def test_source_range_uses_reviewed_spoken_ranges_without_tail_fragment():
    segments = build_review_segments()
    playback_rate = 1.15
    rendered_segments = []
    for segment in segments:
        rendered_segments.append(
            {
                **segment,
                "start": segment["start"] / playback_rate,
                "end": segment["end"] / playback_rate,
                "words": [
                    {
                        **word,
                        "start": word["start"] / playback_rate,
                        "end": word["end"] / playback_rate,
                    }
                    for word in segment.get("words", [])
                ],
            }
        )

    preview = build_business_talking_head_overlay_preview(
        rendered_segments,
        title="",
        output_profile="720p",
        spoken_ranges=[
            {"start": item["start"], "end": item["end"]}
            for item in rendered_segments
        ],
    )

    durations = [cue["end"] - cue["start"] for cue in preview["cues"]]
    assert min(durations) >= 0.9
    assert all(cue["end"] > cue["start"] for cue in preview["cues"])
    assert not any(
        "员更是你" in "".join(cue.get("lines") or [])
        for cue in preview["cues"]
    )
