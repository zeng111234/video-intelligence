from src.services.director_preview_review import (
    apply_review_revisions,
    review_director_preview_once,
)


def test_preview_review_is_called_once_and_cannot_change_subtitle_fact():
    calls = []

    class FakeEngine:
        def review_director_preview(self, **kwargs):
            calls.append(kwargs)
            return {
                "verdict": "revise",
                "issues": [],
                "revisions": [
                    {
                        "event_id": "evt-1",
                        "action": "change_layout",
                        "value": "large_pip_right",
                    },
                    {
                        "event_id": "evt-1",
                        "action": "change_caption_treatment",
                        "value": "static",
                        "semantic_text": "伪造字幕",
                    },
                ],
            }

    engine = FakeEngine()
    review = review_director_preview_once(
        engine,
        preview_frames=[{"frame_id": "f1"}],
        events=[{"event_id": "evt-1", "type": "asset", "layout": "full_screen_broll"}],
        subtitle_text=[{"text": "原始字幕"}],
    )
    assert review["status"] == "completed"
    assert len(calls) == 1
    assert review_director_preview_once(
        engine,
        preview_frames=[],
        events=[],
        subtitle_text=[],
        already_called=True,
    )["status"] == "skipped"
    assert len(calls) == 1

    updated = apply_review_revisions(
        {
            "motion_events": [],
            "camera_events": [],
            "asset_events": [{
                "event_id": "evt-1",
                "layout": "full_screen_broll",
                "mode": "full",
                "semantic_text": "原始字幕",
            }],
        },
        review,
    )
    assert updated["asset_events"][0]["layout"] == "large_pip_right"
    assert updated["review_rejections"][0]["reason"] == "subtitle_fact_is_immutable"
