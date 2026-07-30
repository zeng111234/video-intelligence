"""Cloud editor contract tests; no test makes a real provider call."""

from __future__ import annotations

import json
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
    build_business_talking_head_title_png,
    build_safe_edit_plan,
    create_cost_quote,
    get_cloud_capability,
    retime_segments_after_cuts,
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
                                },
                                {
                                    "begin_time": 800,
                                    "end_time": 1800,
                                    "text": "好",
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
                                "bgm_keywords": ["科技", "未来", "第三个", "四", "五", "六", "七"],
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
    assert subtitle["FontName"] == "YaHei"
    watermark = outputs[0]["WaterMarks"][0]
    assert watermark["Type"] == "Image"
    assert watermark["InputFile"]["Object"] == "review%2Fapproved-title.png"
    assert watermark["ReferPos"] == "TopLeft"
    assert watermark["Width"] == "520"
    assert watermark["Dx"] == "56"
    assert watermark["Dy"] == "84"
    assert watermark["Timeline"] == {"Start": "0", "Duration": "2.5"}
    assert outputs[0]["Clip"]["ConfigToClipFirstPart"] is True
    assert outputs[0]["MergeList"][0]["Start"] == "3.550"
    with pytest.raises(CloudProviderError, match="人工确认"):
        provider.build_submit_request(_render_request(confirmed=False))


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
            "AmixURL": _bgm_asset().provider_locator,
            "Map": "0:a:0",
            "MixDurMode": "first",
            "Start": "0",
        }
    ]


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
    assert spec["style_id"] == "business_talking_head_v7"
    assert spec["title"]["max_chars_per_line"] == 9
    assert spec["title"]["font_family"] == "Source Han Serif CN Heavy"
    assert spec["title"]["render_mode"] == "png_watermark"
    assert spec["subtitle"]["max_lines"] == 1
    assert spec["subtitle"]["max_chars_per_line"] == 10
    assert spec["subtitle"]["font_size"] == 46
    assert "Style: Title,YaHei,48" in ass
    assert "Style: Accent,Arial,1" in ass
    assert "Style: Caption,YaHei,46" in ass
    assert "&H8C000000,&H00000000,-1,0,0,0,100,100,0.18" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:02.50,Title" in ass
    assert r"\fad" not in ass
    assert r"\N" in ass
    assert r"\\N" not in ass


def test_business_talking_head_title_png_uses_brand_font_and_profile_size():
    title_png = build_business_talking_head_title_png(
        "机器人也被裁员？真相令人深思",
        output_profile="720p",
    )

    assert title_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(title_png) > 10_000


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

    assert preview["title"]["lines"] == ["机器人也被裁员？真", "相令人深思"]
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
    assert r"{\c&H006AE1FF&}被裁员{\c&H00F8FAFC&}" in ass


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
        "结果现在工厂",
        "倒闭潮一来",
        "大量工业机器人",
        "被当废铁卖",
    ]
    assert "铁卖" not in lines
    assert all(len(line) >= 4 for line in lines)


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
