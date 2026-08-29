from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import HttpUrl

from src.adapters.licensed import LicensedProviderError
from src.models import (
    DataSource,
    EligibilityStatus,
    HeatLevel,
    HeatResult,
    MediaResolutionStatus,
    Platform,
    ProviderCapability,
    ProviderErrorKind,
    ProviderMediaResult,
    ProviderMode,
    VideoCandidate,
    VideoMetricSnapshot,
)
from src.repositories import MockRepository
from src.services.media_resolution import MediaResolutionError, MediaResolutionService
from src.services.transcription import MAX_PROVIDER_MEDIA_BYTES
from src.services.video_source import DirectVideo, VideoSourceError

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


class FixtureMediaProvider:
    provider_name = "fixture_oneapi"

    def __init__(self) -> None:
        self.calls: list[tuple[Platform, str, str]] = []
        self.error: LicensedProviderError | None = None
        self.warnings: list[str] = []

    def capabilities(self) -> ProviderCapability:
        return ProviderCapability(
            provider_name=self.provider_name,
            display_name="fixture",
            mode=ProviderMode.PRODUCTION,
            enabled=True,
            supported_platforms=[
                Platform.DOUYIN,
                Platform.XIAOHONGSHU,
                Platform.WECHAT_CHANNELS,
            ],
            permission_status="fixture",
        )

    def media_resolution_price(self, platform: Platform) -> float | None:
        return {
            Platform.DOUYIN: 0.04,
            Platform.XIAOHONGSHU: 0.12,
            Platform.WECHAT_CHANNELS: 0.15,
        }.get(platform)

    def resolve_media_url(
        self,
        platform: Platform,
        platform_item_id: str,
        idempotency_key: str,
    ) -> ProviderMediaResult:
        self.calls.append((platform, platform_item_id, idempotency_key))
        if self.error:
            raise self.error
        return ProviderMediaResult(
            platform=platform,
            provider=self.provider_name,
            platform_item_id=platform_item_id,
            media_url=HttpUrl("https://cdn.example.com/video.mp4"),
            observed_at=NOW,
            request_id="provider-media-1",
            api_call_count=1,
            billable_units=self.media_resolution_price(platform) or 0.0,
            warnings=self.warnings,
        )


def _candidate(
    *,
    platform: Platform = Platform.DOUYIN,
    platform_item_id: str = "real-item-1",
) -> VideoCandidate:
    return VideoCandidate(
        video_id=f"candidate-{platform.value}-{platform_item_id}",
        platform_item_id=platform_item_id,
        title="候选视频",
        author_id="author-1",
        author_name="作者",
        platform=platform,
        category="关键词/测试",
        published_at=NOW - timedelta(hours=1),
        source_url=None,
        source_type=DataSource.LICENSED_PROVIDER,
        eligibility_status=EligibilityStatus.AUTO_MATCHED,
        metrics=VideoMetricSnapshot(
            item_id=platform_item_id,
            sampled_at=NOW,
            likes=100,
            confidence=0.8,
        ),
        heat=HeatResult(score=50, level=HeatLevel.INSUFFICIENT, confidence=0.5),
    )


def _service(repository: MockRepository, provider: FixtureMediaProvider):
    return MediaResolutionService(
        repository,
        provider,
        clock=lambda: NOW,
    )


def test_proxy_candidate_is_blocked_before_provider_call() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureMediaProvider()
    candidate = _candidate(platform_item_id="proxy-missing-item-id-abc")
    repository.save_candidate(candidate)

    preview = _service(repository, provider).preview(candidate)

    assert preview.resolvable is False
    assert "代理 ID" in (preview.block_reason or "")
    assert provider.calls == []


def test_media_resolution_cost_counts_against_shared_monthly_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureMediaProvider()
    candidate = _candidate(
        platform=Platform.WECHAT_CHANNELS,
        platform_item_id="wechat-1",
    )
    repository.save_candidate(candidate)
    monkeypatch.setattr(
        "src.services.media_resolution.fetch_authorized_video",
        lambda *args, **kwargs: DirectVideo(
            name="video.mp4",
            media_type="video/mp4",
            content=b"0000ftypmp42",
        ),
    )

    resolved = _service(repository, provider).resolve_video(
        candidate,
        idempotency_key="idem-media-cost",
    )

    assert resolved.attempt.status == MediaResolutionStatus.SUCCEEDED
    assert resolved.attempt.billable_units == pytest.approx(0.15)
    assert repository.monthly_platform_query_cost(NOW.replace(day=1)) == pytest.approx(
        0.15
    )


def test_provider_media_fetch_uses_larger_provider_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureMediaProvider()
    candidate = _candidate(platform=Platform.DOUYIN, platform_item_id="douyin-1")
    repository.save_candidate(candidate)
    seen: dict[str, int] = {}

    def fake_fetch_authorized_video(*args, **kwargs):
        seen["max_bytes"] = kwargs["max_bytes"]
        return DirectVideo(
            name="video.mp4",
            media_type="video/mp4",
            content=b"0000ftypmp42",
        )

    monkeypatch.setattr(
        "src.services.media_resolution.fetch_authorized_video",
        fake_fetch_authorized_video,
    )

    _service(repository, provider).resolve_video(
        candidate,
        idempotency_key="idem-provider-limit",
    )

    assert seen["max_bytes"] == MAX_PROVIDER_MEDIA_BYTES


def test_unknown_media_resolution_blocks_later_attempt() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureMediaProvider()
    provider.error = LicensedProviderError(
        "供应商超时，费用状态待核对",
        kind=ProviderErrorKind.OUTCOME_UNKNOWN,
        outcome_unknown=True,
    )
    candidate = _candidate()
    repository.save_candidate(candidate)
    service = _service(repository, provider)

    with pytest.raises(MediaResolutionError):
        service.resolve_video(candidate, idempotency_key="idem-media-unknown")

    provider.error = None
    with pytest.raises(MediaResolutionError) as caught:
        service.resolve_video(candidate, idempotency_key="idem-media-later")

    assert "结果未知" in caught.value.user_message
    assert len(provider.calls) == 1

    preview = service.preview(candidate)
    assert preview.resolvable is False
    assert "结果未知" in (preview.block_reason or "")


def test_xiaohongshu_direct_media_can_enter_transcription_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureMediaProvider()
    candidate = _candidate(platform=Platform.XIAOHONGSHU, platform_item_id="xhs-2")
    candidate = candidate.model_copy(
        update={"source_url": HttpUrl("https://cdn.example.com/xhs-source.mp4")}
    )
    repository.save_candidate(candidate)
    service = _service(repository, provider)
    monkeypatch.setattr(
        "src.services.media_resolution.fetch_authorized_video",
        lambda *args, **kwargs: DirectVideo(
            name="xhs-source.mp4",
            media_type="video/mp4",
            content=b"0000ftypmp42",
        ),
    )

    preview = service.preview(candidate)

    assert preview.resolvable is True
    assert preview.source == "direct_url"
    assert provider.calls == []

    resolved = service.resolve_video(
        candidate,
        idempotency_key="idem-media-xhs-direct",
    )

    assert resolved.attempt.status == MediaResolutionStatus.SUCCEEDED
    assert resolved.attempt.api_call_count == 0
    assert provider.calls == []
    assert repository.monthly_platform_query_cost(NOW.replace(day=1)) == pytest.approx(
        0.0
    )


def test_oversized_provider_media_blocks_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureMediaProvider()
    provider.warnings = ["douyin_detail_low_bitrate_media"]
    candidate = _candidate(platform=Platform.DOUYIN, platform_item_id="douyin-big")
    repository.save_candidate(candidate)
    monkeypatch.setattr(
        "src.services.media_resolution.fetch_authorized_video",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            VideoSourceError("视频文件超过 300MB，请压缩后再试。")
        ),
    )
    service = _service(repository, provider)

    with pytest.raises(MediaResolutionError):
        service.resolve_video(candidate, idempotency_key="idem-media-too-large")

    preview = service.preview(candidate)

    assert preview.resolvable is False
    assert "手动补直链或上传" in (preview.block_reason or "")
    assert len(provider.calls) == 1


def test_legacy_high_quality_oversized_failure_can_retry_with_new_douyin_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureMediaProvider()
    candidate = _candidate(platform=Platform.DOUYIN, platform_item_id="douyin-old-big")
    repository.save_candidate(candidate)
    monkeypatch.setattr(
        "src.services.media_resolution.fetch_authorized_video",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            VideoSourceError("视频文件超过 300MB，请压缩后再试。")
        ),
    )
    service = _service(repository, provider)

    with pytest.raises(MediaResolutionError):
        service.resolve_video(candidate, idempotency_key="idem-old-high-quality")

    preview = service.preview(candidate)

    assert preview.resolvable is True
    assert len(provider.calls) == 1
