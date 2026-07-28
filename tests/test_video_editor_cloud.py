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
    build_safe_edit_plan,
    create_cost_quote,
    get_cloud_capability,
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
    }
    assert quote.estimated_total == Decimal("0.047750")
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

    assert plan.remove_ranges == [TimeRange(start=4.35, end=5.65)]
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


def test_safe_plan_disables_trimming_when_more_than_100_ranges_remain():
    spoken = [{"start": index * 3.0, "end": index * 3.0 + 0.5} for index in range(102)]
    plan = build_safe_edit_plan(spoken, 304)

    assert plan.trim_silence_enabled is False
    assert plan.remove_ranges == []
    assert "超过 100 段" in plan.warnings[0]


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
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }

    provider = AliyunEditPlanProvider(_aliyun_config(), transport=transport)
    plan = provider.create_plan(
        "第一句。第二句。",
        [{"start": 0, "end": 2}, {"start": 4, "end": 6}],
        6,
    )

    assert plan.title_candidates == ["安全标题"]
    assert plan.remove_ranges == [TimeRange(start=2.35, end=3.65)]
    assert all(step.value != "delete_spoken_content" for step in plan.enabled_steps)


def test_mps_request_uses_selected_profile_and_requires_human_review():
    provider = AliyunMPSRenderProvider(
        _aliyun_config(),
        transport=lambda *args: {},
    )
    fixed_time = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
    _, headers, body = provider.build_submit_request(
        _render_request(),
        now=fixed_time,
        nonce="nonce-1",
    )
    form = parse_qs(body.decode("utf-8"))
    outputs = json.loads(form["Outputs"][0])

    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert form["Action"] == ["SubmitJobs"]
    assert outputs[0]["TemplateId"] == "template-720"
    assert outputs[0]["UserData"] == "idem-001"
    with pytest.raises(CloudProviderError, match="人工确认"):
        provider.build_submit_request(_render_request(confirmed=False))


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
