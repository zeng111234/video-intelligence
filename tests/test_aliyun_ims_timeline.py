from src.adapters.aliyun_ims_timeline import (
    IMS_SUBMIT_ACTION,
    compile_director_timeline_to_ims,
    prepare_ims_submission,
    quote_ims_video_clip,
)


def _timeline():
    return {
        "duration_seconds": 31.5,
        "shots": [
            {
                "media_url": "https://bucket.oss-cn-shanghai.aliyuncs.com/a-roll.mp4",
                "source_start": 0,
                "source_end": 4,
                "timeline_start": 0,
                "timeline_end": 4,
                "mode": "full",
                "intent": "speaker",
                "main_track": True,
            },
            {
                "media_url": "https://bucket.oss-cn-shanghai.aliyuncs.com/factory.mp4",
                "source_start": 0,
                "source_end": 3,
                "timeline_start": 12,
                "timeline_end": 15,
                "mode": "pip",
                "geometry": {"left": 0.6667, "top": 0.6148, "width": 0.30, "height": 0.17},
            },
        ],
        "subtitles": [{"text": "客户数据库", "start": 0, "end": 1.2}],
        "bgm": {
            "media_url": "https://bucket.oss-cn-shanghai.aliyuncs.com/bgm.mp3",
            "start": 0,
            "end": 31.5,
        },
    }


def test_compiler_uses_canonical_timeline_and_ims_submit_action():
    compiled = compile_director_timeline_to_ims(_timeline())

    assert compiled["action"] == IMS_SUBMIT_ACTION
    assert compiled["forbidden_action"] == "SubmitBatchMediaProducingJob"
    assert len(compiled["timeline"]["VideoTracks"]) == 2
    assert compiled["timeline"]["VideoTracks"][1]["VideoTrackClips"][0]["X"] == 0.6667
    assert compiled["timeline"]["SubtitleTracks"][0]["SubtitleTrackClips"][0]["Text"] == "客户数据库"
    assert compiled["cloud_call_made"] is False


def test_full_cutaway_is_an_overlay_track_not_serialized_after_a_roll():
    timeline = _timeline()
    timeline["shots"].append(
        {
            "media_url": "https://bucket.oss-cn-shanghai.aliyuncs.com/cutaway.mp4",
            "source_start": 0,
            "source_end": 2.5,
            "timeline_start": 8,
            "timeline_end": 10.5,
            "mode": "full",
            "intent": "evidence_broll",
            "main_track": False,
            "adapt_mode": "Cover",
            "width": 720,
            "height": 1280,
        }
    )
    compiled = compile_director_timeline_to_ims(timeline)
    tracks = compiled["timeline"]["VideoTracks"]
    assert tracks[0]["MainTrack"] is True
    assert len(tracks[0]["VideoTrackClips"]) == 1
    assert len(tracks[1]["VideoTrackClips"]) == 1
    assert tracks[1]["VideoTrackClips"][0]["AdaptMode"] == "Cover"


def test_ims_submission_stays_paused_without_subscription_oss_and_cost_confirmation():
    prepared = prepare_ims_submission(
        _timeline(),
        subscription_confirmed=False,
        oss_media_ready=False,
        cost_confirmed=False,
    )

    assert prepared["status"] == "blocked_pending_confirmation"
    assert prepared["submit_allowed"] is False
    assert len(prepared["missing_confirmations"]) == 3


def test_ims_price_estimate_uses_documented_minute_rounding():
    quote = quote_ims_video_clip(duration_seconds=31.5, profile="720p")

    assert quote["billable_minutes"] == 1
    assert quote["estimated_video_clip_cny"] == 0.03
    assert "IMS subscription" in quote["extra_costs"]
