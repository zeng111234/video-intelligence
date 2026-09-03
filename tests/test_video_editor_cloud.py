"""Cloud editor contract tests; no test makes a real provider call."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from src.adapters.video_editor_cloud import (
    AliyunEditPlanProvider,
    AliyunFunASRProvider,
    AliyunMPSRenderProvider,
    AliyunCloudObjectStore,
    CloudProviderError,
    build_cloud_providers,
)
from src.services.video_editor_cloud import (
    PRICE_VERSION,
    CloudAsset,
    CloudEditorConfiguration,
    CloudEditorError,
    CloudProviderMode,
    EditPlan,
    EditStepKind,
    OutputProfile,
    ProviderJobSnapshot,
    ProviderJobStatus,
    RenderRequest,
    TimeRange,
    build_business_talking_head_ass,
    build_business_talking_head_overlay_preview,
    build_business_talking_head_srt,
    build_business_talking_head_title_png,
    build_safe_edit_plan,
    build_visual_beats,
    build_smart_opening,
    create_cost_quote,
    get_cloud_capability,
    _caption_lexical_words,
    retime_segments_after_cuts,
    validated_caption_emphasis,
    validated_caption_groups,
    visual_style_spec,
    validate_cost_quote,
)


def _aliyun_config(**updates) -> CloudEditorConfiguration:
    config = CloudEditorConfiguration(
        provider_mode=CloudProviderMode.ALIYUN,
        workspace_id="workspace-test",
        dashscope_api_key="dashscope-secret",
        oss_bucket="private-video-bucket",
        oss_location="oss-cn-beijing",
        aliyun_region="cn-beijing",
        access_key_id="access-key-id",
        access_key_secret="access-key-secret",
        mps_pipeline_id="pipeline-id",
        mps_template_id_720p="template-720",
        mps_template_id_1080p="template-1080",
    )
    return config.model_copy(update=updates)


def _asset() -> CloudAsset:
    return CloudAsset(
        provider_name="aliyun_oss",
        bucket="private-video-bucket",
        object_key="input/demo.mp4",
        uri="oss://private-video-bucket/input/demo.mp4",
        media_type="video/mp4",
        size_bytes=1024,
        provider_locator=(
            "https://private-video-bucket.oss-cn-beijing.aliyuncs.com/"
            "input/demo.mp4?Signature=signed"
        ),
    )


def _render_request(*, confirmed: bool = True) -> RenderRequest:
    return RenderRequest(
        input_asset=_asset(),
        output_object_key="output/demo.mp4",
        output_profile=OutputProfile.HD_720P,
        edit_plan=build_safe_edit_plan(
            [{"start": 0, "end": 2}, {"start": 4, "end": 6}],
            6,
        ),
        review_confirmed=confirmed,
        idempotency_key="idem-001",
    )


def _bgm_asset() -> CloudAsset:
    return CloudAsset(
        provider_name="aliyun_oss",
        bucket="private-video-bucket",
        object_key="video-editor-input/demo/bgm/low-volume.m4a",
        uri="oss://private-video-bucket/video-editor-input/demo/bgm/low-volume.m4a",
        media_type="audio/mp4",
        size_bytes=1024,
        provider_locator=(
            "https://private-video-bucket.oss-cn-beijing.aliyuncs.com/"
            "video-editor-input/demo/bgm/low-volume.m4a?Signature=signed"
        ),
    )


def _opening_asset() -> CloudAsset:
    return CloudAsset(
        provider_name="aliyun_oss",
        bucket="private-video-bucket",
        object_key="video-editor-input/demo/opening/number-focus.mp4",
        uri=(
            "oss://private-video-bucket/video-editor-input/demo/opening/"
            "number-focus.mp4"
        ),
        media_type="video/mp4",
        size_bytes=2048,
        provider_locator=(
            "https://private-video-bucket.oss-cn-beijing.aliyuncs.com/"
            "video-editor-input/demo/opening/number-focus.mp4?Signature=signed"
        ),
    )


def test_configuration_defaults_to_honest_sandbox():
    config = CloudEditorConfiguration.from_env({})
    capability = get_cloud_capability(config)

    assert config.provider_mode == CloudProviderMode.SANDBOX
    assert capability.enabled is True
    assert capability.live_ready is False
    assert capability.is_mock is True
    assert capability.missing_configuration == []


def test_aliyun_capability_reports_missing_configuration_without_secrets():
    config = CloudEditorConfiguration.from_env(
        {"VIDEO_EDITOR_PROVIDER_MODE": "aliyun"},
    )
    capability = get_cloud_capability(config)

    assert capability.enabled is False
    assert capability.live_ready is False
    assert capability.is_mock is False
    assert "DASHSCOPE_API_KEY" in capability.missing_configuration
    assert "ALIBABA_CLOUD_ACCESS_KEY_SECRET" in capability.missing_configuration


def test_configuration_prefers_current_access_key_names_and_runtime_quote_settings():
    config = CloudEditorConfiguration.from_env(
        {
            "VIDEO_EDITOR_PROVIDER_MODE": "aliyun",
            "ALIBABA_CLOUD_ACCESS_KEY_ID": "current-id",
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "current-secret",
            "ALIYUN_ACCESS_KEY_ID": "legacy-id",
            "ALIYUN_ACCESS_KEY_SECRET": "legacy-secret",
            "ALIYUN_VIDEO_EDITOR_REGION": "cn-beijing",
            "VIDEO_EDITOR_PRICE_VERSION": "test-price-v2",
            "VIDEO_EDITOR_QUOTE_TTL_SECONDS": "300",
        },
    )

    assert config.access_key_id == "current-id"
    assert config.access_key_secret == "current-secret"
    assert config.aliyun_region == "cn-beijing"
    assert config.oss_location == "oss-cn-beijing"
    assert config.price_version == "test-price-v2"
    assert config.quote_ttl_seconds == 300


def test_cost_quote_matches_published_rates_and_expires_after_15_minutes():
    now = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
    quote = create_cost_quote(
        input_duration_seconds=60,
        output_duration_seconds=60,
        output_profile="720p",
        planning_input_tokens=3000,
        planning_output_tokens=1000,
        now=now,
    )

    costs = {item.component: item.estimated_cost_cny for item in quote.line_items}
    assert costs == {
        "speech_recognition": Decimal("0.013200"),
        "edit_planning": Decimal("0.001950"),
        "cloud_render": Decimal("0.032600"),
        "brand_title_overlay": Decimal("0.000100"),
    }
    assert quote.estimated_total == Decimal("0.047850")
    planning = next(
        item for item in quote.line_items if item.component == "edit_planning"
    )
    assert planning.rate_details == {
        "input_per_million_tokens": Decimal("0.15"),
        "output_per_million_tokens": Decimal("1.5"),
    }
    assert planning.unit_price_cny > 0
    assert quote.expires_at == now + timedelta(minutes=15)
    assert quote.price_version == PRICE_VERSION
    assert json.dumps(quote.model_dump(mode="json"), ensure_ascii=False)


def test_cost_quote_uses_1080p_mps_rate():
    quote = create_cost_quote(
        input_duration_seconds=60,
        output_duration_seconds=60,
        output_profile="1080p",
        planning_input_tokens=0,
        planning_output_tokens=0,
    )

    render = next(item for item in quote.line_items if item.component == "cloud_render")
    assert render.unit_price_cny == Decimal("0.0651")
    assert render.estimated_cost_cny == Decimal("0.065100")


def test_cost_quote_requires_matching_unexpired_price_version():
    now = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
    quote = create_cost_quote(input_duration_seconds=60, now=now)

    assert (
        validate_cost_quote(
            quote,
            quote_id=quote.quote_id,
            now=now + timedelta(minutes=14),
        )
        is quote
    )
    with pytest.raises(CloudEditorError, match="已过期"):
        validate_cost_quote(
            quote,
            quote_id=quote.quote_id,
            now=now + timedelta(minutes=15),
        )
    with pytest.raises(CloudEditorError, match="价格版本"):
        validate_cost_quote(
            quote,
            quote_id=quote.quote_id,
            now=now,
            expected_price_version="new-price",
        )


def test_safe_plan_only_cuts_long_internal_silence_with_edge_padding():
    plan = build_safe_edit_plan(
        [
            {"start": 0, "end": 2},
            {"start": 3.4, "end": 4},
            {"start": 6, "end": 8},
        ],
        8,
    )

    assert plan.remove_ranges == [TimeRange(start=4.45, end=5.55)]
    assert plan.kept_ranges == [
        TimeRange(start=0, end=4.45),
        TimeRange(start=5.55, end=8),
    ]
    assert plan.estimated_output_seconds == 6.9
    assert plan.trim_silence_enabled is True
    assert EditStepKind.TRIM_SILENCE in plan.enabled_steps
    for cut in plan.remove_ranges:
        assert all(
            not (cut.start < speech.end and speech.start < cut.end)
            for speech in plan.spoken_ranges
        )


def test_visual_beats_are_sparse_automatic_and_bound_to_caption_terms():
    segments = [
        {"start": 0, "end": 2, "text": "今天讲一个门店做法。"},
        {"start": 2, "end": 4, "text": "只需要49元就能参加活动。"},
        {"start": 10, "end": 12, "text": "我们把流程分成三步。"},
    ]

    beats = build_visual_beats(
        segments,
        [{"segment_index": 0, "term": "49元", "kind": "number"}],
    )

    assert [item.treatment for item in beats] == [
        "hook",
        "keyword_card",
        "punch_in",
    ]
    assert beats[0].start == 0
    assert beats[1].label == "49元"
    assert all(item.end <= 12 for item in beats)


def test_edit_plan_model_rejects_deleting_spoken_content():
    with pytest.raises(ValidationError, match="不能删除任何有人声"):
        EditPlan(
            duration_seconds=5,
            spoken_ranges=[{"start": 1, "end": 3}],
            remove_ranges=[{"start": 2, "end": 4}],
            enabled_steps=[EditStepKind.TRIM_SILENCE],
            trim_silence_enabled=True,
        )


def test_retime_subtitles_after_safe_rough_cut_and_reject_crossing_cue():
    retimed = retime_segments_after_cuts(
        [
            {"start": 0.1, "end": 2.0, "text": "第一句"},
            {"start": 4.0, "end": 5.0, "text": "第二句"},
        ],
        [TimeRange(start=2.45, end=3.55)],
    )

    assert retimed[1]["start"] == 2.9
    assert retimed[1]["end"] == 3.9
    with pytest.raises(CloudEditorError, match="跨越粗剪区间"):
        retime_segments_after_cuts(
            [{"start": 2.0, "end": 4.0, "text": "跨越切点"}],
            [TimeRange(start=2.45, end=3.55)],
        )


def test_safe_plan_disables_trimming_when_too_many_output_segments_remain():
    spoken = [{"start": index * 3.0, "end": index * 3.0 + 0.5} for index in range(102)]
    plan = build_safe_edit_plan(spoken, 304)

    assert plan.trim_silence_enabled is False
    assert plan.remove_ranges == []
    assert "安全裁剪区间过多" in plan.warnings[0]


def test_sandbox_bundle_never_calls_transports_or_creates_publishable_media(
    tmp_path: Path,
):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"not-a-real-video")

    def forbidden(*args, **kwargs):
        raise AssertionError("sandbox must not call a network transport")

    bundle = build_cloud_providers(
        CloudEditorConfiguration.from_env({}),
        json_transport=forbidden,
        upload_transport=forbidden,
    )
    asset = bundle.object_store.upload(video, "source.mp4", media_type="video/mp4")
    transcript_job = bundle.asr.submit(asset)
    transcript = bundle.asr.fetch_result(transcript_job)
    plan = bundle.edit_plan.create_plan(
        transcript.transcript,
        transcript.spoken_ranges,
        transcript.duration_seconds,
    )
    rendered = bundle.render.submit(
        RenderRequest(
            input_asset=asset,
            output_object_key="output.mp4",
            output_profile=OutputProfile.HD_720P,
            edit_plan=plan,
            review_confirmed=True,
            idempotency_key="sandbox-idem",
        ),
    )

    assert asset.is_mock is True
    assert transcript.is_mock is True
    assert rendered.is_mock is True
    assert rendered.output_uri is None
    assert rendered.can_publish is False


def test_missing_aliyun_credentials_prevent_transport_call():
    calls = []

    def spy(*args):
        calls.append(args)
        return {}

    config = CloudEditorConfiguration.from_env(
        {"VIDEO_EDITOR_PROVIDER_MODE": "aliyun"},
    )
    provider = AliyunFunASRProvider(config, transport=spy)

    with pytest.raises(CloudProviderError, match="尚未配置"):
        provider.submit(_asset())
    assert calls == []


def test_fun_asr_builds_documented_request_and_submit_is_not_retried():
    calls = []

    def failing_transport(*args):
        calls.append(args)
        raise ConnectionError("disconnected")

    provider = AliyunFunASRProvider(
        _aliyun_config(),
        transport=failing_transport,
    )
    url, headers, body = provider.build_submit_request(_asset())
    payload = json.loads(body)

    assert url.endswith("/api/v1/services/audio/asr/transcription")
    assert headers["X-DashScope-Async"] == "enable"
    assert payload["model"] == "fun-asr"
    assert payload["input"]["file_urls"] == [_asset().provider_locator]
    assert "provider_locator" not in _asset().model_dump(mode="json")
    with pytest.raises(CloudProviderError) as caught:
        provider.submit(_asset())
    assert caught.value.outcome_unknown is True
    assert len(calls) == 1


def test_fun_asr_query_retries_once_and_normalizes_downloaded_transcript():
    calls: list[str] = []

    def transport(method, url, headers, body, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise ConnectionError("temporary")
        if "/tasks/" in url:
            return {
                "output": {
                    "task_id": "asr-1",
                    "task_status": "SUCCEEDED",
                    "results": [
                        {
                            "subtask_status": "SUCCEEDED",
                            "transcription_url": (
                                "https://dashscope-result-bj.oss-cn-beijing."
                                "aliyuncs.com/result.json?Signature=secret"
                            ),
                        },
                    ],
                },
                "usage": {"duration": 9},
            }
        return {
            "properties": {"original_duration_in_milliseconds": 4000},
            "transcripts": [
                {
                    "text": "你好，世界。",
                    "sentences": [
                        {
                            "begin_time": 100,
                            "end_time": 1800,
                            "text": "你好，",
                            "speaker_id": 0,
                            "words": [
                                {
                                    "begin_time": 100,
                                    "end_time": 500,
                                    "text": "你",
                                    "confidence": 0.8,
                                },
                                {
                                    "begin_time": 800,
                                    "end_time": 1800,
                                    "text": "好",
                                    "confidence": 0.6,
                                },
                            ],
                        },
                        {
                            "begin_time": 2200,
                            "end_time": 3900,
                            "text": "世界。",
                            "speaker_id": 0,
                        },
                    ],
                },
            ],
        }

    provider = AliyunFunASRProvider(_aliyun_config(), transport=transport)
    snapshot = provider.query("asr-1")
    transcript = provider.fetch_result(snapshot)

    assert len(calls) == 3
    assert snapshot.status == ProviderJobStatus.SUCCEEDED
    assert "result_locator" not in snapshot.model_dump(mode="json")
    assert transcript.transcript == "你好，世界。"
    assert transcript.duration_seconds == 4
    assert transcript.segments[0].confidence == pytest.approx(0.7)
    assert transcript.segments[1].confidence is None
    assert transcript.spoken_ranges == [
        TimeRange(start=0.1, end=0.5),
        TimeRange(start=0.8, end=1.8),
        TimeRange(start=2.2, end=3.9),
    ]


def test_fun_asr_query_stops_after_one_retry():
    calls = 0

    def transport(*args):
        nonlocal calls
        calls += 1
        raise ConnectionError("offline")

    provider = AliyunFunASRProvider(_aliyun_config(), transport=transport)
    with pytest.raises(CloudProviderError) as caught:
        provider.query("asr-1")

    assert calls == 2
    assert caught.value.outcome_unknown is True


def test_fun_asr_failed_snapshot_keeps_top_level_reason():
    def transport(*_args):
        return {
            "output": {
                "task_id": "asr-no-words",
                "task_status": "FAILED",
                "code": "ASR_RESPONSE_HAVE_NO_WORDS",
                "message": "ASR_RESPONSE_HAVE_NO_WORDS",
                "results": [{"subtask_status": "FAILED"}],
            }
        }

    provider = AliyunFunASRProvider(_aliyun_config(), transport=transport)
    snapshot = provider.query("asr-no-words")

    assert snapshot.status == ProviderJobStatus.FAILED
    assert snapshot.detail["code"] == "ASR_RESPONSE_HAVE_NO_WORDS"
    assert snapshot.detail["message"] == "ASR_RESPONSE_HAVE_NO_WORDS"


def test_fun_asr_fetch_requeries_persisted_snapshot_and_retries_download_once():
    calls: list[str] = []

    def transport(method, url, headers, body, timeout):
        calls.append(url)
        if "/tasks/" in url:
            return {
                "output": {
                    "task_id": "asr-persisted",
                    "task_status": "SUCCEEDED",
                    "results": [
                        {
                            "subtask_status": "SUCCEEDED",
                            "transcription_url": (
                                "https://dashscope-result-bj.oss-cn-beijing."
                                "aliyuncs.com/result.json?Signature=secret"
                            ),
                        },
                    ],
                },
            }
        if calls.count(url) == 1:
            raise ConnectionError("temporary download failure")
        return {
            "properties": {"original_duration_in_milliseconds": 1000},
            "transcripts": [
                {
                    "text": "已恢复",
                    "sentences": [
                        {"begin_time": 0, "end_time": 900, "text": "已恢复"},
                    ],
                },
            ],
        }

    provider = AliyunFunASRProvider(_aliyun_config(), transport=transport)
    persisted = ProviderJobSnapshot(
        provider_name="aliyun_fun_asr",
        provider_job_id="asr-persisted",
        provider_stage="transcription_complete",
        status=ProviderJobStatus.SUCCEEDED,
    )

    transcript = provider.fetch_result(persisted)

    assert transcript.transcript == "已恢复"
    assert len(calls) == 3


def test_qwen_suggestions_cannot_inject_spoken_range_deletions():
    def transport(*args):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "title_candidates": ["安全标题"],
                                "explanation": "只处理停顿",
                                "bgm_category": "理性干货",
                                "bgm_energy": "克制",
                                "bgm_keywords": ["知识", "口播"],
                                "enabled_steps": [
                                    "trim_silence",
                                    "delete_spoken_content",
                                ],
                                "remove_ranges": [{"start": 0, "end": 6}],
                            },
                            ensure_ascii=False,
                        ),
                    },
                },
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        }

    provider = AliyunEditPlanProvider(_aliyun_config(), transport=transport)
    plan = provider.create_plan(
        "第一句。第二句。",
        [{"start": 0, "end": 2}, {"start": 4, "end": 6}],
        6,
    )

    assert plan.title_candidates == ["安全标题"]
    assert plan.bgm_category == "理性干货"
    assert plan.bgm_energy == "克制"
    assert plan.bgm_keywords == ["知识", "口播"]
    assert plan.remove_ranges == [TimeRange(start=2.45, end=3.55)]
    assert all(step.value != "delete_spoken_content" for step in plan.enabled_steps)
    assert plan.usage == {"prompt_tokens": 100, "completion_tokens": 20}


def test_qwen_bgm_profile_is_restricted_to_approved_values():
    def transport(*args):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "title_candidates": ["标题"],
                                "explanation": "测试",
                                "enabled_steps": ["bgm"],
                                "bgm_category": "任意外部分类",
                                "bgm_energy": "爆炸",
                                "bgm_keywords": [
                                    "科技",
                                    "未来",
                                    "第三个",
                                    "四",
                                    "五",
                                    "六",
                                    "七",
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                },
            ],
        }

    plan = AliyunEditPlanProvider(
        _aliyun_config(),
        transport=transport,
    ).create_plan("介绍一个知识点。", [{"start": 0, "end": 2}], 2)

    assert plan.bgm_category == "通用口播"
    assert plan.bgm_energy == "克制"
    assert plan.bgm_keywords == ["科技", "未来", "第三个", "四", "五", "六"]


def test_qwen_semantic_caption_groups_preserve_exact_asr_text():
    segments = [
        {
            "start": 0,
            "end": 4,
            "text": "80%的顾客还主动加了店里的私域。",
        },
        {
            "start": 4,
            "end": 8,
            "text": "附近5公里的居民基本都成了回头客。",
        },
    ]

    def transport(*args):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "title_candidates": ["顾客主动进私域的原因"],
                                "explanation": "按完整语义短语显示字幕",
                                "enabled_steps": ["subtitles", "title"],
                                "caption_groups": [
                                    {
                                        "segment_index": 0,
                                        "parts": [
                                            "80%的顾客",
                                            "还主动加了",
                                            "店里的私域",
                                        ],
                                    },
                                    {
                                        "segment_index": 1,
                                        "parts": [
                                            "附近5公里的居民",
                                            "基本都成了回头客",
                                        ],
                                    },
                                ],
                                "caption_emphasis": [
                                    {
                                        "segment_index": 0,
                                        "term": "80%",
                                        "kind": "number",
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    provider = AliyunEditPlanProvider(_aliyun_config(), transport=transport)
    plan = provider.create_plan(
        "".join(segment["text"] for segment in segments),
        [{"start": 0, "end": 4}, {"start": 4, "end": 8}],
        8,
        segments,
    )
    preview = build_business_talking_head_overlay_preview(
        segments,
        title=plan.title_candidates[0],
        output_profile="720p",
        caption_groups=plan.caption_groups,
        caption_emphasis=plan.caption_emphasis,
    )

    assert plan.caption_group_source == "qwen_semantic"
    assert [group.parts for group in plan.caption_groups] == [
        ["80%的顾客", "还主动加了", "店里的私域"],
        ["附近5公里的居民", "基本都成了回头客"],
    ]
    assert [cue["lines"][0] for cue in preview["cues"]] == [
        "80%的顾客",
        "还主动加了",
        "店里的私域",
        "附近5公里的居民",
        "基本都成了回头客",
    ]
    assert preview["caption_group_source"] == "qwen_semantic"
    assert [item.model_dump() for item in plan.caption_emphasis] == [
        {"segment_index": 0, "term": "80%", "kind": "number"}
    ]
    assert preview["cues"][0]["emphasis_range"] == {
        "line_index": 0,
        "start": 0,
        "end": 3,
    }
    assert preview["cues"][0]["emphasis_style"] == {
        "color": "#FFD166",
        "scale": 1.08,
        "animation": "scale_overshoot",
        "duration_ms": 200,
        "style_id": "adaptive_talking_head_v1",
    }


def test_qwen_caption_groups_reject_changed_text_and_mid_word_breaks():
    segments = [{"start": 0, "end": 4, "text": "顾客还主动加了店里的私域。"}]
    changed = validated_caption_groups(
        [{"segment_index": 0, "parts": ["顾客主动加了", "店里的私域"]}],
        segments,
        max_chars=11,
    )
    mid_word = validated_caption_groups(
        [{"segment_index": 0, "parts": ["顾客还主", "动加了店里的私域"]}],
        segments,
        max_chars=11,
    )

    assert changed == []
    assert mid_word == []


def test_caption_emphasis_is_sparse_exact_and_kept_inside_one_caption_part():
    segments = [
        {"start": 0, "end": 2, "text": "只需要49元就能参加活动。"},
        {"start": 2, "end": 4, "text": "附近5公里都可以使用。"},
        {"start": 4, "end": 6, "text": "这就是今天的核心结论。"},
        {"start": 6, "end": 8, "text": "千万不要错过最后一天。"},
    ]
    groups = [
        {"segment_index": 0, "parts": ["只需要49元", "就能参加活动"]},
        {"segment_index": 1, "parts": ["附近5公里", "都可以使用"]},
        {"segment_index": 2, "parts": ["这就是今天的", "核心结论"]},
        {"segment_index": 3, "parts": ["千万不要错过", "最后一天"]},
    ]
    emphasis = validated_caption_emphasis(
        [
            {"segment_index": 0, "term": "49元", "kind": "number"},
            {"segment_index": 1, "term": "5公里", "kind": "number"},
            {"segment_index": 2, "term": "核心结论", "kind": "benefit"},
        ],
        segments,
        caption_groups=groups,
    )

    assert [item.term for item in emphasis] == ["49元", "5公里"]
    assert (
        validated_caption_emphasis(
            [{"segment_index": 0, "term": "49元就能", "kind": "number"}],
            segments,
            caption_groups=groups,
        )
        == []
    )
    assert (
        validated_caption_emphasis(
            [{"segment_index": 0, "term": "免费", "kind": "benefit"}],
            segments,
            caption_groups=groups,
        )
        == []
    )


def test_qwen_request_contains_only_indexed_subtitle_text_for_semantic_grouping():
    provider = AliyunEditPlanProvider(_aliyun_config(), transport=lambda *args: {})
    _, _, body = provider.build_request(
        "这是完整文案。",
        [{"start": 0, "end": 2}],
        2,
        [{"start": 0, "end": 2, "text": "这是完整文案。", "confidence": 0.8}],
    )
    payload = json.loads(body)
    user_payload = json.loads(payload["messages"][1]["content"])

    assert user_payload["subtitle_segments"] == [
        {"segment_index": 0, "text": "这是完整文案。"}
    ]
    assert "caption_groups" in payload["messages"][0]["content"]
    assert "caption_emphasis" in payload["messages"][0]["content"]
    assert "opening_style_id" in payload["messages"][0]["content"]
    assert "suspense_reveal" in payload["messages"][0]["content"]
    assert "story_unfold" in payload["messages"][0]["content"]
    assert "number_focus" in payload["messages"][0]["content"]
    assert "逐字一致" in payload["messages"][0]["content"]


@pytest.mark.parametrize(
    ("transcript", "title", "expected_style", "expected_sound"),
    [
        ("充200送30活动今天开始", "充200送30", "number_focus", "soft_chime"),
        ("故事的序章从这里开始", "帷幕拉开", "story_unfold", "soft_page_turn"),
        ("很多人一直忽略这个真相", "你真的看懂了吗", "suspense_reveal", "soft_whoosh"),
    ],
)
def test_smart_opening_uses_only_approved_templates(
    transcript: str,
    title: str,
    expected_style: str,
    expected_sound: str,
):
    opening = build_smart_opening(transcript, [title])

    assert opening is not None
    assert opening.style_id == expected_style
    assert opening.sound_effect_id == expected_sound
    assert opening.duration_seconds == 1.4
    assert len(opening.hook_text) <= 14


def test_smart_opening_rejects_unapproved_preferred_style():
    opening = build_smart_opening(
        "这件事很多人都不知道",
        ["真相马上揭晓"],
        preferred_style="arbitrary_explosion_effect",
    )

    assert opening is not None
    assert opening.style_id == "suspense_reveal"


def test_smart_opening_selects_a_complete_clause_instead_of_cutting_mid_sentence():
    opening = build_smart_opening(
        "很多开连锁餐饮的老板，其实没细算过这笔账：光养一个获客员工就超过一万。",
        ["很多开连锁餐饮的老板，其实没细算过这笔账：光养一个专门做获客"],
    )

    assert opening is not None
    assert opening.hook_text == "其实没细算过这笔账"
    assert opening.hook_text != "很多开连锁餐饮的老板其实没细"[:14]


def test_smart_opening_skips_a_context_free_transition_title():
    opening = build_smart_opening(
        "但这个不一样 流水线成片不用粘胶 不用充电也能牢固使用",
        ["但这个不一样"],
    )

    assert opening is not None
    assert opening.hook_text == "流水线成片不用粘胶"
    assert opening.hook_text != "但这个不一样"


def test_ass_keyword_emphasis_uses_adaptive_light_pop_and_fade_in():
    segments = [{"start": 0, "end": 2, "text": "只需要49元就能参加活动。"}]
    ass = build_business_talking_head_ass(
        segments,
        title="活动说明",
        output_profile="720p",
        caption_groups=[{"segment_index": 0, "parts": ["只需要49元", "就能参加活动"]}],
        caption_emphasis=[{"segment_index": 0, "term": "49元", "kind": "number"}],
    ).decode("utf-8-sig")

    assert r"\fscx108\fscy92\frz4" in ass
    assert r"\t(0,200,\fscx108\fscy108\frz0\blur0)" in ass


def test_overlay_preview_automatically_marks_numeric_and_benefit_terms():
    preview = build_business_talking_head_overlay_preview(
        [
            {"start": 0, "end": 2, "text": "只要49元就能参加活动"},
            {"start": 2, "end": 4, "text": "顾客还能拿到现金奖励"},
        ],
        title="活动说明",
        output_profile="720p",
    )

    assert preview["cues"][0]["emphasis_style"] == {
        "color": "#FFD166",
        "scale": 1.08,
        "animation": "scale_overshoot",
        "duration_ms": 200,
        "style_id": "adaptive_talking_head_v1",
    }
    assert preview["cues"][0]["emphasis_range"] is not None
    assert all(cue["emphasis_range"] is None for cue in preview["cues"][1:])


def test_parallel_promotions_each_get_emphasis_and_use_asr_sentence_clock():
    preview = build_business_talking_head_overlay_preview(
        [
            {
                "start": 19.96,
                "end": 25.76,
                "text": "普通烧烤店搞充值活动，充100送10块，充200送30，早就过时了。",
            }
        ],
        title="充值活动",
        output_profile="720p",
        spoken_ranges=[
            {"start": 19.96, "end": 21.88},
            {"start": 22.12, "end": 23.24},
            {"start": 23.56, "end": 24.68},
            {"start": 24.96, "end": 25.76},
        ],
    )

    assert [cue["lines"][0] for cue in preview["cues"]] == [
        "普通烧烤店搞充值活动",
        "充100送10块",
        "充200送30",
        "早就过时了",
    ]
    assert [(cue["start"], cue["end"]) for cue in preview["cues"]] == [
        (19.96, 21.88),
        (22.12, 23.24),
        (23.56, 24.68),
        (24.96, 25.76),
    ]
    assert preview["cues"][1]["emphasis_range"] == {
        "line_index": 0,
        "start": 5,
        "end": 8,
    }
    assert preview["cues"][2]["emphasis_range"] is None


def test_overlay_preview_uses_word_timestamps_instead_of_sentence_averaging():
    preview = build_business_talking_head_overlay_preview(
        [
            {
                "start": 0,
                "end": 4,
                "text": "第一段内容，第二段内容",
                "words": [
                    {"start": 0, "end": 0.2, "text": "第"},
                    {"start": 0.2, "end": 0.4, "text": "一"},
                    {"start": 0.4, "end": 0.7, "text": "段"},
                    {"start": 0.7, "end": 1.1, "text": "内容"},
                    {"start": 1.1, "end": 1.2, "text": "，"},
                    {"start": 2.0, "end": 2.2, "text": "第"},
                    {"start": 2.2, "end": 2.4, "text": "二"},
                    {"start": 2.4, "end": 2.7, "text": "段"},
                    {"start": 2.7, "end": 3.8, "text": "内容"},
                ],
            }
        ],
        title="词级字幕",
        output_profile="720p",
        caption_groups=[{"segment_index": 0, "parts": ["第一段内容", "第二段内容"]}],
    )

    assert [(cue["start"], cue["end"]) for cue in preview["cues"]] == [
        (0.0, 1.2),
        (2.0, 3.8),
    ]


def test_word_clock_emits_kinetic_spans_and_ass_word_pop_effects():
    segments = [
        {
            "start": 0.0,
            "end": 2.4,
            "text": "今天跟你说四句话",
            "emphasis_kind": "number",
            "emphasis_terms": ["四句话"],
            "words": [
                {"start": 0.0, "end": 0.35, "text": "今天"},
                {"start": 0.35, "end": 0.8, "text": "跟你"},
                {"start": 0.8, "end": 1.15, "text": "说"},
                {"start": 1.15, "end": 1.75, "text": "四句话"},
            ],
        }
    ]

    preview = build_business_talking_head_overlay_preview(
        segments,
        title="词级动效",
        output_profile="720p",
        caption_emphasis=[
            {"segment_index": 0, "term": "四句话", "kind": "number"}
        ],
    )
    kinetic_cues = [cue for cue in preview["cues"] if cue["kinetic_words"]]

    assert kinetic_cues
    assert kinetic_cues[0]["kinetic_mode"] == "word_pop"
    assert len(kinetic_cues[0]["kinetic_words"]) >= 1
    assert all(
        word["end"] > word["start"]
        for word in kinetic_cues[0]["kinetic_words"]
    )
    assert {word["color"] for word in kinetic_cues[0]["kinetic_words"]} == {"#FFD166"}

    ass = build_business_talking_head_ass(
        segments,
        title="词级动效",
        output_profile="720p",
        overlay_preview=preview,
    ).decode("utf-8-sig")
    assert r"Style: Caption,Smiley Sans,52" in ass
    assert r"\c&H0066D1FF&\3c&H00101010&\bord4.0\shad1" in ass
    assert r"\fscx108\fscy92\frz4" in ass
    assert r"\fscx108\fscy108" in ass
    assert r"\bord5.0\shad1" in ass


def test_benefit_number_uses_burst_caption_motion_and_warm_benefit_color():
    segments = [
        {
            "start": 0.0,
            "end": 0.8,
            "text": "先看这个例子",
            "words": [
                {"start": 0.0, "end": 0.3, "text": "先看"},
                {"start": 0.3, "end": 0.8, "text": "这个例子"},
            ],
        },
        {
            "start": 0.8,
            "end": 3.2,
            "text": "充值100送10块",
            "emphasis_kind": "number",
            "emphasis_terms": ["10块"],
            "words": [
                {"start": 0.8, "end": 1.5, "text": "充值100"},
                {"start": 1.5, "end": 2.2, "text": "送10块"},
            ],
        }
    ]
    preview = build_business_talking_head_overlay_preview(
        segments,
        title="优惠动效",
        output_profile="720p",
        caption_emphasis=[
            {"segment_index": 1, "term": "10块", "kind": "number"}
        ],
    )

    kinetic_cue = next(cue for cue in preview["cues"] if cue["kinetic_words"])
    assert kinetic_cue["kinetic_style"] == "burst"
    assert {word["color"] for word in kinetic_cue["kinetic_words"]} == {"#FFD166"}

    ass = build_business_talking_head_ass(
        segments,
        title="优惠动效",
        output_profile="720p",
        overlay_preview=preview,
    ).decode("utf-8-sig")
    assert "\\fscx112\\fscy112\\frz2" in ass


def test_multiline_cue_uses_kinetic_motion_instead_of_legacy_emphasis_path():
    preview = {
        "title": {"lines": [], "start": 0, "end": 0},
        "cues": [
            {
                "start": 0.0,
                "end": 2.0,
                "lines": ["第一行重点", "第二行内容"],
                "kinetic_mode": "cue_pop",
                "kinetic_style": "slam",
                "kinetic_words": [],
                "emphasis_range": None,
                "emphasis_style": None,
            }
        ],
    }
    ass = build_business_talking_head_ass(
        [{"start": 0.0, "end": 2.0, "text": "第一行重点第二行内容"}],
        title="双行动效",
        output_profile="720p",
        overlay_preview=preview,
    ).decode("utf-8-sig")

    assert r"\fscx108\fscy92\frz4" in ass
    assert r"\N" in ass


def test_multiline_word_emphasis_keeps_kinetic_span_on_second_line():
    segments = [
        {
            "start": 0.0,
            "end": 2.4,
            "text": "普通说明这是重点结果",
            "emphasis_kind": "result",
            "emphasis_terms": ["重点结果"],
            "words": [
                {"start": 0.0, "end": 0.35, "text": "普通"},
                {"start": 0.35, "end": 0.75, "text": "说明"},
                {"start": 0.75, "end": 1.2, "text": "这是"},
                {"start": 1.2, "end": 1.8, "text": "重点结果"},
            ],
        }
    ]
    preview = {
        "title": {"lines": [], "start": 0, "end": 0},
        "style_fingerprint": {"word_motion": "selective_word_emphasis_v3"},
        "cues": [
            {
                "start": 0.0,
                "end": 2.4,
                "lines": ["普通说明这是", "重点结果"],
                "source_segment_index": 0,
                "emphasis_range": {"line_index": 1, "start": 0, "end": 4},
                "emphasis_style": {"color": "#FF8A7A", "duration_ms": 200},
            }
        ],
    }

    ass = build_business_talking_head_ass(
        segments,
        title="",
        output_profile="720p",
        overlay_preview=preview,
    ).decode("utf-8-sig")

    assert "\\N" in ass
    assert "\\fscx108\\fscy108" in ass
    second_line = ass.split("\\N", 1)[1]
    assert "\\t(" in second_line


def test_ass_backfills_kinetic_motion_for_legacy_cached_preview_cues():
    segments = [
        {"start": 0.0, "end": 1.2, "text": "第一段先抓住注意力"},
        {"start": 1.2, "end": 2.6, "text": "第二段说明核心方法"},
        {"start": 2.6, "end": 4.0, "text": "第三段给出结果"},
    ]
    # This is the shape of a pre-kinetic cached review snapshot: it has
    # captions and the old entrance field, but no kinetic metadata.
    preview = {
        "title": {"lines": [], "start": 0, "end": 0},
        "cues": [
            {
                "start": segment["start"],
                "end": segment["end"],
                "lines": [segment["text"]],
                "source_segment_index": index,
                "entry_motion": {"type": "fade_in_scale", "duration_ms": 140},
            }
            for index, segment in enumerate(segments)
        ],
    }

    ass = build_business_talking_head_ass(
        segments,
        title="",
        output_profile="720p",
        overlay_preview=preview,
    ).decode("utf-8-sig")
    caption_dialogues = [
        line for line in ass.splitlines() if ",Caption,," in line
    ]

    assert len(caption_dialogues) == len(segments)
    assert r"\t(" in caption_dialogues[0]
    assert all(r"\t(" in line for line in caption_dialogues)
    assert all(r"\fad(" in line for line in caption_dialogues)
    assert all(r"\fscx90\fscy90" in line for line in caption_dialogues[1:])


def test_ass_rebuilds_stale_v3_kinetic_spans_before_export():
    segments = [
        {
            "start": 0.0,
            "end": 1.8,
            "text": "结果提升80%",
            "emphasis_kind": "number",
            "words": [
                {"start": 0.0, "end": 0.7, "text": "结果提升"},
                {"start": 0.7, "end": 1.2, "text": "80%"},
            ],
        }
    ]
    preview = {
        "title": {"lines": [], "start": 0, "end": 0},
        "style_fingerprint": {"word_motion": "selective_word_emphasis_v3"},
        "cues": [
            {
                "start": 0.0,
                "end": 1.8,
                "lines": ["结果提升80%"],
                "source_segment_index": 0,
                "emphasis_range": {"line_index": 0, "start": 4, "end": 7},
                "emphasis_style": {"color": "#FFD166", "duration_ms": 200},
                "kinetic_mode": "word_pop",
                "kinetic_style": "stamp",
                "kinetic_words": [
                    {
                        "line_index": 0,
                        "start_offset": 4,
                        "end_offset": 7,
                        "start": 0.7,
                        "end": 1.2,
                        "text": "80%",
                        "color": "#55D6BE",
                    }
                ],
            }
        ],
    }

    ass = build_business_talking_head_ass(
        segments,
        title="",
        output_profile="720p",
        overlay_preview=preview,
    ).decode("utf-8-sig")

    assert r"\3c&H0066D1FF&" in ass
    assert r"\3c&H00BED655&" not in ass


def test_word_timestamps_keep_business_clause_together_when_character_split_would_fragment_it():
    text = "你公司的客户资源是掌握在业务员的手里呢，还是沉淀在咱们公司的数据库？"
    duration = 6.12
    character_duration = duration / len(text)
    words = [
        {
            "start": index * character_duration,
            "end": (index + 1) * character_duration,
            "text": character,
        }
        for index, character in enumerate(text)
    ]

    preview = build_business_talking_head_overlay_preview(
        [{"start": 0.0, "end": duration, "text": text, "words": words}],
        title="客户资源风险",
        output_profile="720p",
    )

    texts = ["".join(cue["lines"]) for cue in preview["cues"]]
    assert 2 <= len(texts) <= 4
    assert any("数据库" in text for text in texts)
    assert any("业务员" in text for text in texts)
    assert all(
        0.9 - 1e-6 <= cue["end"] - cue["start"] <= 2.4 + 1e-6
        for cue in preview["cues"]
    )


def test_mps_request_uses_selected_profile_and_requires_human_review():
    provider = AliyunMPSRenderProvider(
        _aliyun_config(),
        transport=lambda *args: {},
    )
    fixed_time = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
    request = _render_request().model_copy(
        update={
            "subtitle_object_key": "review/approved.ass",
            "title_watermark_object_key": "review/approved-title.png",
        },
    )
    _, headers, body = provider.build_submit_request(
        request,
        now=fixed_time,
        nonce="nonce-1",
    )
    form = parse_qs(body.decode("utf-8"))
    outputs = json.loads(form["Outputs"][0])

    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert form["Action"] == ["SubmitJobs"]
    assert outputs[0]["TemplateId"] == "template-720"
    assert outputs[0]["UserData"] == "idem-001"
    subtitle = outputs[0]["SubtitleConfig"]["ExtSubtitleList"][0]
    assert subtitle["Input"]["Object"] == "review%2Fapproved.ass"
    assert subtitle["CharEnc"] == "UTF-8"
    assert "FontName" not in subtitle
    watermark = outputs[0]["WaterMarks"][0]
    assert watermark["Type"] == "Image"
    assert watermark["InputFile"]["Object"] == "review%2Fapproved-title.png"
    assert watermark["ReferPos"] == "TopLeft"
    assert watermark["Width"] == "660"
    assert watermark["Dx"] == "30"
    assert watermark["Dy"] == "84"
    assert watermark["Timeline"] == {"Start": "0", "Duration": "2.5"}
    assert outputs[0]["Clip"]["ConfigToClipFirstPart"] is True
    assert outputs[0]["MergeList"][0]["Start"] == "3.550"
    assert outputs[0]["MergeList"][0]["MergeURL"].startswith(
        "http://private-video-bucket.oss-cn-beijing.aliyuncs.com/"
    )
    with pytest.raises(CloudProviderError, match="人工确认"):
        provider.build_submit_request(_render_request(confirmed=False))


def test_mps_request_prepends_opening_and_offsets_title_timeline():
    provider = AliyunMPSRenderProvider(
        _aliyun_config(),
        transport=lambda *args: {},
    )
    request = _render_request().model_copy(
        update={
            "opening_asset": _opening_asset(),
            "opening_duration_seconds": 1.4,
            "title_watermark_object_key": "review/approved-title.png",
        },
    )
    _, _, body = provider.build_submit_request(
        request,
        now=datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
        nonce="nonce-opening",
    )
    outputs = json.loads(parse_qs(body.decode("utf-8"))["Outputs"][0])

    assert outputs[0]["OpeningList"] == [
        {
            "OpenUrl": (
                "http://private-video-bucket.oss-cn-beijing.aliyuncs.com/"
                "video-editor-input/demo/opening/number-focus.mp4?Signature=signed"
            ),
            "Start": "0",
        }
    ]
    assert outputs[0]["WaterMarks"][0]["Timeline"]["Start"] == "1.400"


def test_mps_request_mixes_prepared_bgm_without_extending_video_duration():
    provider = AliyunMPSRenderProvider(
        _aliyun_config(),
        transport=lambda *args: {},
    )
    _, _, body = provider.build_submit_request(
        _render_request().model_copy(update={"bgm_asset": _bgm_asset()}),
        now=datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
        nonce="nonce-bgm",
    )
    outputs = json.loads(parse_qs(body.decode("utf-8"))["Outputs"][0])

    assert outputs[0]["Amix"] == [
        {
            "AmixURL": (
                "http://private-video-bucket.oss-cn-beijing.aliyuncs.com/"
                "video-editor-input/demo/bgm/low-volume.m4a?Signature=signed"
            ),
            "Map": "0:a:0",
            "MixDurMode": "first",
            "Start": "0",
        }
    ]


def test_mps_rejects_media_url_outside_configured_oss_bucket():
    provider = AliyunMPSRenderProvider(
        _aliyun_config(),
        transport=lambda *args: {},
    )
    untrusted = _opening_asset().model_copy(
        update={"provider_locator": "https://example.com/opening.mp4?token=secret"}
    )

    with pytest.raises(CloudProviderError, match="受信任地址"):
        provider.build_submit_request(
            _render_request().model_copy(update={"opening_asset": untrusted})
        )


def test_business_talking_head_ass_uses_portrait_canvas_safe_caption_area():
    spec = visual_style_spec("720p")
    ass = build_business_talking_head_ass(
        [
            {
                "start": 0.2,
                "end": 4.2,
                "text": "工厂没订单，再智能的设备也是一堆废铁。",
            },
        ],
        title="机器人也被裁员？真相令人深思",
        output_profile="720p",
    ).decode("utf-8-sig")

    assert spec["canvas"] == {
        "width": 720,
        "height": 1280,
        "pixel_aspect_ratio": "1:1",
    }
    assert "PlayResX: 720" in ass
    assert "PlayResY: 1280" in ass
    assert spec["style_id"] == (
        "business_talking_head_v9.1-smart-opening-clean-hook-speed-1.15"
    )
    assert spec["playback_rate"] == 1.15
    assert spec["title"]["max_lines"] == 1
    assert spec["title"]["max_chars_per_line"] == 12
    assert spec["title"]["font_family"] == "Source Han Serif CN Heavy"
    assert spec["title"]["render_mode"] == "png_watermark"
    assert spec["subtitle"]["max_lines"] == 1
    assert spec["subtitle"]["max_chars_per_line"] == 11
    assert spec["subtitle"]["font_size"] == 52
    assert spec["subtitle"]["font_family"] == "Smiley Sans"
    assert spec["subtitle"]["outline_width"] == 4
    assert spec["subtitle"]["shadow"] == 1
    assert "Style: Title,Source Han Serif CN Heavy,44" in ass
    assert "Style: Accent,Arial,1" in ass
    assert "Style: Caption,Smiley Sans,52" in ass
    assert "&H10101010,&H00000000,-1,0,0,0,100,100,0.02" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:02.50,Title" in ass
    assert r"\fscx108\fscy92\frz4" in ass
    assert r"\N" not in ass
    assert r"\\N" not in ass


def test_business_talking_head_ass_offsets_title_and_subtitles_for_opening():
    ass = build_business_talking_head_ass(
        [{"start": 0.2, "end": 2.2, "text": "最近广州出了一个活动"}],
        title="活动说明",
        output_profile="720p",
        time_offset_seconds=1.4,
    ).decode("utf-8-sig")

    assert "Dialogue: 0,0:00:01.40,0:00:03.90,Title" in ass
    assert "Dialogue: 0,0:00:01.60" in ass


def test_business_talking_head_title_png_uses_brand_font_and_profile_size():
    title_png = build_business_talking_head_title_png(
        "机器人也被裁员？真相令人深思",
        output_profile="720p",
    )

    assert title_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(title_png) > 10_000


def test_title_preview_uses_short_single_line_without_ellipsis():
    preview = build_business_talking_head_overlay_preview(
        [{"start": 0, "end": 2, "text": "最近广州冒出了一个挺特别的参与模式"}],
        title="最近广州冒出了一个挺特别的参与模式",
        output_profile="720p",
    )
    title_lines = preview["title"]["lines"]
    assert len(title_lines) == 1
    assert 6 <= len(title_lines[0]) <= 12
    assert "…" not in title_lines[0]


def test_overlay_preview_and_ass_use_short_single_line_captions_without_punctuation():
    segments = [
        {
            "start": 0,
            "end": 3,
            "text": "你发现没，机器人最近也被裁员了。",
            "emphasis_terms": ["被裁员"],
        }
    ]
    preview = build_business_talking_head_overlay_preview(
        segments,
        title="机器人也被裁员？真相令人深思",
        output_profile="720p",
    )
    ass = build_business_talking_head_ass(
        segments,
        title="机器人也被裁员？真相令人深思",
        output_profile="720p",
    ).decode("utf-8-sig")

    assert len(preview["title"]["lines"]) == 1
    assert 6 <= len(preview["title"]["lines"][0]) <= 12
    assert "…" not in preview["title"]["lines"][0]
    assert [cue["lines"] for cue in preview["cues"]] == [
        ["你发现没"],
        ["机器人最近也被裁员了"],
    ]
    assert all(len(cue["lines"]) == 1 for cue in preview["cues"])
    assert all(
        not any(mark in line for mark in "，。！？；：、,.!?;:")
        for cue in preview["cues"]
        for line in cue["lines"]
    )
    assert preview["cues"][1]["emphasis_range"] == {
        "line_index": 0,
        "start": 6,
        "end": 9,
    }
    assert r"\fscx108\fscy92\frz4" in ass


def test_caption_splits_are_contiguous_and_keep_numeric_punctuation():
    preview = build_business_talking_head_overlay_preview(
        [
            {
                "start": 1.25,
                "end": 5.75,
                "text": "今天12:30开播，转化率增长3.5%，大家别错过！",
            },
        ],
        title="直播提醒",
        output_profile="720p",
    )

    cues = preview["cues"]
    assert [cue["lines"] for cue in cues] == [
        ["今天12:30开播"],
        ["转化率增长3.5%"],
        ["大家别错过"],
    ]
    assert cues[0]["start"] == 1.25
    assert cues[-1]["end"] == 5.75
    assert all(left["end"] == right["start"] for left, right in zip(cues, cues[1:]))


def test_caption_splits_keep_number_and_unit_together():
    preview = build_business_talking_head_overlay_preview(
        [
            {
                "start": 0.0,
                "end": 4.0,
                "text": "只要49元就能到店，路上3.2公里，转化率80%。",
            },
        ],
        title="数字断句",
        output_profile="720p",
    )

    joined = "".join(line for cue in preview["cues"] for line in cue["lines"])
    assert joined == "只要49元就能到店路上3.2公里转化率80%"
    lines = [line for cue in preview["cues"] for line in cue["lines"]]
    assert all(
        not (
            re.search(r"\d(?:\.\d+)?$", left)
            and re.match(r"(?:元|公里|%)", right)
        )
        for left, right in zip(lines, lines[1:])
    )


def test_lexical_word_projection_keeps_numeric_suffix_when_provider_omits_it():
    projected = _caption_lexical_words(
        [
            {"text": "80", "start": 0.0, "end": 0.3},
            {"text": "的", "start": 0.3, "end": 0.6},
            {"text": "客户", "start": 0.6, "end": 1.2},
        ],
        text="80%的客户",
        segment_start=0.0,
        segment_end=1.2,
    )

    assert [item["text"] for item in projected] == ["80%", "的", "客户"]


def test_lexical_word_projection_keeps_numeric_suffix_after_clause_punctuation():
    projected = _caption_lexical_words(
        [
            {"text": "但如果你买的特别便宜", "start": 0.0, "end": 1.0},
            {"text": "1", "start": 1.0, "end": 1.1},
            {"text": "0", "start": 1.1, "end": 1.2},
            {"text": "0", "start": 1.2, "end": 1.3},
            {"text": "火烧", "start": 1.3, "end": 1.6},
        ],
        text="但如果你买的特别便宜，100%火烧",
        segment_start=0.0,
        segment_end=1.6,
    )

    assert "".join(item["text"] for item in projected) == (
        "但如果你买的特别便宜100%火烧"
    )


def test_caption_balances_long_phrases_without_one_or_two_character_orphans():
    preview = build_business_talking_head_overlay_preview(
        [
            {
                "start": 3.68,
                "end": 11.36,
                "text": (
                    "以前都说机器取代工人，结果现在工厂倒闭潮一来，"
                    "大量工业机器人被当废铁卖。"
                ),
            },
        ],
        title="机器人也会失业",
        output_profile="720p",
    )

    lines = [cue["lines"][0] for cue in preview["cues"]]
    assert lines == [
        "以前都说机器取代工人",
        "结果现在工厂倒闭潮一来",
        "大量工业机器人",
        "被当废铁卖",
    ]
    assert "铁卖" not in lines
    assert all(len(line) >= 4 for line in lines)


def test_sentence_only_long_caption_uses_short_estimated_phrase_cues():
    preview = build_business_talking_head_overlay_preview(
        [
            {
                "start": 0,
                "end": 14.8,
                "text": "客户资源到底掌握在业务员手里还是沉淀在公司数据库里面这是很多老板都忽略的风险",
            }
        ],
        title="客户资源风险",
        output_profile="720p",
    )

    assert preview["phrase_timing_source"] == "estimated_phrase_timestamps"
    assert 6 <= len(preview["cues"]) <= 8
    assert all(0.9 <= cue["end"] - cue["start"] <= 2.4 for cue in preview["cues"])
    assert all(len(cue["lines"]) == 1 for cue in preview["cues"])
    assert "客户资源" in "".join(cue["lines"][0] for cue in preview["cues"])


def test_sentence_only_business_copy_uses_semantic_groups_without_tail_fragments():
    preview = build_business_talking_head_overlay_preview(
        [
            {
                "start": 0.0,
                "end": 6.12,
                "text": "你公司的客户资源是掌握在业务员的手里呢，还是沉淀在咱们公司的数据库？",
            },
            {
                "start": 6.12,
                "end": 12.2,
                "text": "业务员呢，他一旦离职，你会发现聊天记录没了，客户的关系也就跟着断掉了。",
            },
            {
                "start": 12.92,
                "end": 20.52,
                "text": "企业真正需要的不只是业务员个人维护，而是一套呢可以沉淀用户、持续触达的营销机制。",
            },
            {
                "start": 20.8,
                "end": 35.68,
                "text": "数影霸屏呢，我们可以配合我们企业的整个客户的数据库，对合规客户呢，这些人群呢，进行持续的、精准的曝光，让客户认识的不只是某个业务员，更是你整个工厂、品牌、产品。",
            },
        ],
        title="客户资源风险",
        output_profile="720p",
    )

    cues = preview["cues"]
    texts = ["".join(cue["lines"]) for cue in cues]
    assert 14 <= len(cues) <= 21
    assert min(cue["end"] - cue["start"] for cue in cues) >= 0.9
    assert max(cue["end"] - cue["start"] for cue in cues) <= 2.4 + 1e-6
    assert "业务员呢" not in texts
    assert any("业务员他一旦离职" in text for text in texts)
    assert all(text not in {"的", "个"} for text in texts)
    assert any("工厂品牌产品" in text for text in texts)
    assert any("数据库" in text for text in texts)
    assert "只是某个业务员" not in texts
    assert (
        "让客户认识的" in texts
        or "让客户认识的不只是某个业务员" in texts
    )
    assert (
        "不只是某个业务员" in texts
        or "让客户认识的不只是某个业务员" in texts
    )


@pytest.mark.parametrize(
    "spoken_text",
    [
        "门店每天都要把新客沉淀下来",
        "客户关系不能只放在个人手机里",
        "先把会员制和优惠券讲清楚",
        "数据报表要能看出复购趋势",
        "员工离职以后客户还要有人接手",
        "小程序可以承接线上预约和核销",
        "不要把品牌和产品拆成孤立字幕",
        "从旧系统迁移到智慧门店需要步骤",
        "朋友圈传播要和到店行为连起来",
        "这套方法适合餐饮美容和便利店",
    ],
)
def test_unseen_chinese_copy_uses_generic_caption_boundaries_without_overfit(
    spoken_text: str,
):
    duration = max(3.0, len(spoken_text) * 0.24)
    preview = build_business_talking_head_overlay_preview(
        [{"start": 0.0, "end": duration, "text": spoken_text}],
        title="通用口播",
        output_profile="720p",
    )
    cues = preview["cues"]
    assert cues
    assert all(len(cue["lines"]) == 1 for cue in cues)
    assert all(
        not any(mark in "".join(cue["lines"]) for mark in "，。！？、,.!?；;：:")
        for cue in cues
    )
    assert all(
        0.9 <= float(cue["end"]) - float(cue["start"]) <= 2.4 + 1e-6
        for cue in cues
    )
    assert all("".join(cue["lines"]) not in {"的", "地", "个", "品牌", "产品"} for cue in cues)
    assert "".join("".join(cue["lines"]) for cue in cues) == spoken_text


@pytest.mark.parametrize(
    "category,spoken_text",
    [
        ("life", "周末去菜市场买菜，回家以后再做一锅热汤"),
        ("beauty", "做完护理以后先观察皮肤状态，再决定是否补水"),
        ("tutorial", "打开设置页面，先检查网络权限，再保存新的配置"),
    ],
)
def test_unfamiliar_life_beauty_tutorial_copy_uses_jieba_boundaries(
    category: str,
    spoken_text: str,
):
    duration = max(4.0, len(spoken_text) * 0.24)
    preview = build_business_talking_head_overlay_preview(
        [{"start": 0.0, "end": duration, "text": spoken_text}],
        title="",
        output_profile="720p",
    )
    cues = preview["cues"]
    expected = re.sub(r"[，。！？；：、,.!?;:]", "", spoken_text)
    rendered = "".join("".join(cue["lines"]) for cue in cues)
    assert rendered == expected
    assert all(len(cue["lines"]) == 1 for cue in cues)
    assert all(
        0.9 <= float(cue["end"]) - float(cue["start"]) <= 2.4 + 1e-6
        for cue in cues
    ), category
    assert all(
        not any(mark in "".join(cue["lines"]) for mark in "，。！？；：、,.!?;:")
        for cue in cues
    )


def test_caption_glossary_is_per_transcript_and_reaches_ass_rendering():
    spoken_text = "业务员离职后客户关系不应断开"
    words = [
        {"text": character, "start": index * 0.42, "end": (index + 1) * 0.42}
        for index, character in enumerate(spoken_text)
    ]
    glossary = ["业务员", "客户关系"]
    segments = [{"start": 0.0, "end": 4.2, "text": spoken_text, "words": words}]
    preview = build_business_talking_head_overlay_preview(
        segments,
        title="",
        output_profile="720p",
        caption_glossary=glossary,
    )
    cue_texts = ["".join(cue["lines"]) for cue in preview["cues"]]
    boundaries = []
    cursor = 0
    for text in cue_texts[:-1]:
        cursor += len(text)
        boundaries.append(cursor)
    for term in glossary:
        start = spoken_text.index(term)
        assert not any(start < boundary < start + len(term) for boundary in boundaries)
    ass = build_business_talking_head_ass(
        segments,
        title="",
        output_profile="720p",
        caption_glossary=glossary,
        overlay_preview=preview,
    ).decode("utf-8-sig")
    assert "业务员" in ass
    assert "客户关系" in ass


def test_caption_domain_terms_are_not_global_core_rules():
    source = Path("src/services/video_editor_cloud.py").read_text(encoding="utf-8")
    assert "_CAPTION_COMPOUND_WORDS" not in source
    assert '"客户资源"' not in source.split("_CAPTION_BREAK_AFTER_TOKENS", 1)[1].split(")", 1)[0]
    assert '"手里"' not in source.split("_CAPTION_BREAK_AFTER_TOKENS", 1)[1].split(")", 1)[0]
    assert '"持续"' not in source.split("_CAPTION_BREAK_BEFORE_TOKENS", 1)[1].split(")", 1)[0]


def test_caption_production_has_no_sample_sentence_overfit_branches():
    source = (Path("src/services/video_editor_cloud.py")).read_text(encoding="utf-8")
    forbidden_literals = (
        "客户资源是掌握在业务员的手里呢还是沉淀在咱们公司的数据库",
        "让客户认识的不只是某个业务员更是你整个工厂品牌产品",
        "企业真正需要的不只是业务员个人维护而是一套能沉淀用户持续触达的营销机制",
        "而且秒到账所有消费都是通过门店小程序完成的",
        "除了餐饮水果生鲜美容便利店都能找它",
    )
    assert all(literal not in source for literal in forbidden_literals)


def test_srt_uses_same_short_cues_as_ass_preview():
    srt = build_business_talking_head_srt(
        [{"start": 0, "end": 4.0, "text": "客户资源掌握在谁手里，决定公司风险。"}],
        output_profile="720p",
    ).decode("utf-8-sig")

    assert "00:00:00,000 -->" in srt
    assert "客户资源掌握" in srt
    assert "\n\n" in srt


def test_caption_keeps_basic_together_and_prefers_the_phrase_boundary():
    preview = build_business_talking_head_overlay_preview(
        [
            {
                "start": 4,
                "end": 10,
                "text": (
                    "街上有家烧烤店，才开一个月，附近5公里的居民基本都成了他的回头客。"
                ),
            },
        ],
        title="烧烤店的回头客秘密",
        output_profile="720p",
    )

    lines = [cue["lines"][0] for cue in preview["cues"]]
    assert lines == [
        "街上有家烧烤店",
        "才开一个月",
        "附近5公里的居民",
        "基本都成了他的回头客",
    ]
    assert all("居民基" not in line and not line.startswith("本都") for line in lines)


def test_caption_uses_chinese_word_boundaries_instead_of_splitting_active():
    preview = build_business_talking_head_overlay_preview(
        [
            {
                "start": 11.04,
                "end": 19.16,
                "text": (
                    "80%的顾客还主动加了店里的私域，生意好的不行，我也跑去试了几次。"
                ),
            },
        ],
        title="顾客为什么主动推荐",
        output_profile="720p",
    )

    lines = [cue["lines"][0] for cue in preview["cues"]]
    assert lines[:2] == [
        "80%的顾客",
        "还主动加了店里的私域",
    ]
    assert all(not line.endswith("主") and not line.startswith("动") for line in lines)


def test_caption_avoids_dangling_particles_prefixes_and_classifiers():
    preview = build_business_talking_head_overlay_preview(
        [
            {
                "start": 0,
                "end": 12,
                "text": (
                    "但重头戏是后面的共享店长活动。"
                    "立刻拿到6张无门槛优惠券。"
                    "这种口碑效果比花大钱打广告强多了。"
                    "如果你的店也想用这套系统搞活动。"
                ),
            },
        ],
        title="自然断句检查",
        output_profile="720p",
    )

    lines = [cue["lines"][0] for cue in preview["cues"]]
    joined = "".join(lines)
    assert "但重头戏是" in joined
    # This domain phrase is not a global break rule.  It remains contiguous;
    # reviewed media may protect it through transcript_glossary when needed.
    assert "后面的共享店长活动" in joined
    assert "立刻拿到" in joined
    assert "6张无门槛优惠券" in joined
    assert "这种口碑效果" in joined
    assert "比花大钱打广告强多了" in joined
    assert "如果你的店" in joined
    assert "也想用这套系统搞活动" in joined
    assert all(len(line) >= 3 for line in lines)


def test_oss_presigned_read_url_is_short_lived_and_not_serialized():
    store = AliyunCloudObjectStore(
        _aliyun_config(),
        transport=lambda *args: {},
    )
    now = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
    url = store.presign_get_url(
        "input/demo.mp4",
        expires_seconds=3600,
        now=now,
    )
    query = parse_qs(urlsplit(url).query)

    assert query["OSSAccessKeyId"] == ["access-key-id"]
    assert query["Expires"] == [str(int(now.timestamp()) + 3600)]
    assert query["Signature"][0]


def test_oss_upload_retries_one_connection_failure(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    calls = 0

    def transport(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CloudProviderError("temporary connection failure", kind="connection")
        return {}

    store = AliyunCloudObjectStore(
        _aliyun_config(),
        transport=transport,
    )

    asset = store.upload(
        source,
        "input/retry-once.mp4",
        media_type="video/mp4",
    )

    assert calls == 2
    assert asset.object_key == "input/retry-once.mp4"


def test_oss_upload_stops_after_one_retry(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    calls = 0

    def transport(*_args):
        nonlocal calls
        calls += 1
        raise CloudProviderError("connection down", kind="connection")

    store = AliyunCloudObjectStore(
        _aliyun_config(),
        transport=transport,
    )

    with pytest.raises(CloudProviderError) as caught:
        store.upload(
            source,
            "input/fail-after-retry.mp4",
            media_type="video/mp4",
        )

    assert calls == 2
    assert caught.value.outcome_unknown is True


def test_oss_upload_reuses_the_task_object_after_a_lost_success_response(
    tmp_path: Path,
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    calls = 0

    def transport(*_args):
        nonlocal calls
        calls += 1
        raise CloudProviderError(
            "OSS 上传 HTTP 409：FileAlreadyExists",
            kind="already_exists",
        )

    store = AliyunCloudObjectStore(
        _aliyun_config(),
        transport=transport,
    )

    asset = store.upload(
        source,
        "asr-input/transcript-fixed/source.mp4",
        media_type="video/mp4",
    )

    assert calls == 1
    assert asset.object_key == "asr-input/transcript-fixed/source.mp4"


def test_mps_submit_connection_failure_is_outcome_unknown_without_retry():
    calls = 0

    def transport(*args):
        nonlocal calls
        calls += 1
        raise ConnectionError("timeout")

    provider = AliyunMPSRenderProvider(
        _aliyun_config(),
        transport=transport,
    )
    with pytest.raises(CloudProviderError) as caught:
        provider.submit(_render_request())

    assert calls == 1
    assert caught.value.outcome_unknown is True


def test_mps_submit_explicit_job_rejection_is_known_validation_failure():
    provider = AliyunMPSRenderProvider(
        _aliyun_config(),
        transport=lambda *_args: {
            "JobResultList": {
                "JobResult": [
                    {
                        "Success": False,
                        "Code": "InvalidParameter.InvalidHTTPProtocol",
                        "Message": "OpenUrl protocol not supported",
                    }
                ]
            }
        },
    )

    with pytest.raises(CloudProviderError) as caught:
        provider.submit(_render_request())

    assert caught.value.kind == "validation"
    assert caught.value.outcome_unknown is False
