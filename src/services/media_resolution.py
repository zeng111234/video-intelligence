from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from src.adapters.licensed import LicensedProviderError
from src.contracts import CandidateRepository
from src.models import (
    MediaResolutionAttempt,
    MediaResolutionStatus,
    Platform,
    ProviderErrorKind,
    ProviderMode,
    TranscriptionTask,
    VideoCandidate,
)
from src.services.commercial_search import (
    DUPLICATE_GUARD_SECONDS,
    MONTHLY_HARD_LIMIT_COST_CNY,
)
from src.services.transcription import MAX_PROVIDER_MEDIA_BYTES
from src.services.video_source import (
    DirectVideo,
    VideoSourceError,
    fetch_authorized_video,
)


class MediaResolutionError(RuntimeError):
    def __init__(
        self,
        user_message: str,
        *,
        status_code: int = 400,
        attempt: MediaResolutionAttempt | None = None,
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.status_code = status_code
        self.attempt = attempt


@dataclass(frozen=True)
class CandidateMediaPreview:
    candidate_id: str
    resolvable: bool
    mode: str
    provider: str
    platform: Platform
    platform_item_id: str | None
    estimated_cost_cny: float | None
    monthly_budget_used_cny: float
    monthly_budget_limit_cny: float
    existing_task_id: str | None = None
    last_resolution_status: MediaResolutionStatus | None = None
    block_reason: str | None = None
    source: str = "provider"


@dataclass(frozen=True)
class ResolvedMedia:
    attempt: MediaResolutionAttempt
    video: DirectVideo


class MediaResolutionService:
    def __init__(
        self,
        repository: CandidateRepository,
        provider,
        *,
        clock=None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.clock = clock or (lambda: datetime.now().astimezone())

    def preview(self, candidate: VideoCandidate) -> CandidateMediaPreview:
        capability = self.provider.capabilities()
        latest = self.repository.find_latest_media_resolution_for_candidate(
            candidate.video_id
        )
        task_id = latest.task_id if latest and latest.task_id else None
        monthly_cost = self._monthly_budget_used()
        source = (
            "direct_url"
            if self._is_direct_video_url(candidate.source_url)
            else "provider"
        )
        estimated_cost = (
            0.0 if source == "direct_url" else self._price(candidate.platform)
        )
        block_reason = self._block_reason(
            candidate,
            estimated_cost=estimated_cost,
            monthly_cost=monthly_cost,
            source=source,
            latest=latest,
        )
        return CandidateMediaPreview(
            candidate_id=candidate.video_id,
            resolvable=block_reason is None,
            mode=capability.mode.value,
            provider=capability.provider_name,
            platform=candidate.platform,
            platform_item_id=candidate.platform_item_id,
            estimated_cost_cny=estimated_cost,
            monthly_budget_used_cny=monthly_cost,
            monthly_budget_limit_cny=MONTHLY_HARD_LIMIT_COST_CNY,
            existing_task_id=task_id,
            last_resolution_status=latest.status if latest else None,
            block_reason=block_reason,
            source=source,
        )

    def resolve_video(
        self,
        candidate: VideoCandidate,
        *,
        idempotency_key: str,
    ) -> ResolvedMedia:
        previous = self.repository.find_media_resolution_by_idempotency_key(
            idempotency_key
        )
        if previous:
            raise MediaResolutionError(
                "该幂等键已经使用过，请刷新后查看已有结果或重新确认。",
                status_code=409,
                attempt=previous,
            )
        if self.repository.has_unresolved_media_resolution(candidate.video_id):
            raise MediaResolutionError(
                "该候选存在结果未知的媒体解析请求，请先核对 OneAPI 使用记录再重试。",
                status_code=409,
            )

        preview = self.preview(candidate)
        if preview.block_reason:
            raise MediaResolutionError(preview.block_reason, status_code=400)

        now = self.clock()
        attempt = MediaResolutionAttempt(
            idempotency_key=idempotency_key,
            candidate_id=candidate.video_id,
            platform=candidate.platform,
            platform_item_id=candidate.platform_item_id or "",
            provider=preview.provider,
            status=MediaResolutionStatus.RUNNING,
            estimated_cost_cny=preview.estimated_cost_cny,
            created_at=now,
            updated_at=now,
        )
        current_attempt = attempt
        if not self.repository.claim_media_resolution_request(
            idempotency_key,
            attempt.resolution_id,
            now,
            ttl_seconds=DUPLICATE_GUARD_SECONDS,
        ):
            raise MediaResolutionError(
                "相同媒体解析请求刚刚执行过，请等待 60 秒，防止重复计费。",
                status_code=409,
            )
        self.repository.save_media_resolution_attempt(attempt)

        try:
            if preview.source == "direct_url" and candidate.source_url is not None:
                video = fetch_authorized_video(str(candidate.source_url))
                finished = attempt.model_copy(
                    update={
                        "status": MediaResolutionStatus.SUCCEEDED,
                        "api_call_count": 0,
                        "billable_units": 0.0,
                        "updated_at": self.clock(),
                    }
                )
            else:
                result = self.provider.resolve_media_url(
                    candidate.platform,
                    candidate.platform_item_id or "",
                    idempotency_key,
                )
                updated = attempt.model_copy(
                    update={
                        "api_call_count": result.api_call_count,
                        "billable_units": result.billable_units,
                        "provider_request_id": result.request_id,
                        "warnings": result.warnings,
                        "updated_at": self.clock(),
                    }
                )
                self.repository.save_media_resolution_attempt(updated)
                current_attempt = updated
                video = fetch_authorized_video(
                    str(result.media_url),
                    require_extension=False,
                    max_bytes=MAX_PROVIDER_MEDIA_BYTES,
                    fallback_name=(
                        f"{candidate.platform.value}-{candidate.platform_item_id}.mp4"
                    ),
                )
                finished = updated.model_copy(
                    update={
                        "status": MediaResolutionStatus.SUCCEEDED,
                        "updated_at": self.clock(),
                    }
                )
            self.repository.save_media_resolution_attempt(finished)
            self.repository.mark_media_resolution_request(
                idempotency_key,
                "succeeded",
                finished.updated_at,
            )
            return ResolvedMedia(attempt=finished, video=video)
        except LicensedProviderError as exc:
            failed = self._failed_attempt_from_provider_error(current_attempt, exc)
            self.repository.save_media_resolution_attempt(failed)
            self.repository.mark_media_resolution_request(
                idempotency_key,
                "outcome_unknown"
                if failed.status == MediaResolutionStatus.OUTCOME_UNKNOWN
                else "failed",
                failed.updated_at,
            )
            raise MediaResolutionError(
                str(exc),
                status_code=409
                if failed.status == MediaResolutionStatus.OUTCOME_UNKNOWN
                else 400,
                attempt=failed,
            ) from exc
        except VideoSourceError as exc:
            failed = current_attempt.model_copy(
                update={
                    "status": MediaResolutionStatus.FAILED,
                    "error_kind": ProviderErrorKind.VALIDATION,
                    "error_message": exc.user_message,
                    "updated_at": self.clock(),
                }
            )
            self.repository.save_media_resolution_attempt(failed)
            self.repository.mark_media_resolution_request(
                idempotency_key,
                "failed",
                failed.updated_at,
            )
            raise MediaResolutionError(
                exc.user_message,
                status_code=400,
                attempt=failed,
            ) from exc

    def attach_task(
        self,
        attempt: MediaResolutionAttempt,
        task: TranscriptionTask,
    ) -> MediaResolutionAttempt:
        updated = attempt.model_copy(
            update={"task_id": task.task_id, "updated_at": self.clock()}
        )
        self.repository.save_media_resolution_attempt(updated)
        return updated

    def _failed_attempt_from_provider_error(
        self,
        attempt: MediaResolutionAttempt,
        exc: LicensedProviderError,
    ) -> MediaResolutionAttempt:
        unknown = exc.outcome_unknown or exc.kind == ProviderErrorKind.OUTCOME_UNKNOWN
        status = (
            MediaResolutionStatus.OUTCOME_UNKNOWN
            if unknown
            else MediaResolutionStatus.FAILED
        )
        api_calls = (
            1 if self.provider.capabilities().mode == ProviderMode.PRODUCTION else 0
        )
        return attempt.model_copy(
            update={
                "status": status,
                "api_call_count": api_calls,
                "billable_units": attempt.estimated_cost_cny if unknown else None,
                "error_kind": exc.kind,
                "error_message": str(exc),
                "updated_at": self.clock(),
            }
        )

    def _block_reason(
        self,
        candidate: VideoCandidate,
        *,
        estimated_cost: float | None,
        monthly_cost: float,
        source: str,
        latest: MediaResolutionAttempt | None,
    ) -> str | None:
        capability = self.provider.capabilities()
        if candidate.platform == Platform.XIAOHONGSHU:
            return (
                "小红书未登录公开搜索只保存可见素材信息，不解析媒体直链或自动转写；"
                "请使用已获授权的本地文件。"
            )
        if source == "direct_url":
            return None
        if latest and latest.status == MediaResolutionStatus.OUTCOME_UNKNOWN:
            return "该候选存在结果未知的媒体解析请求，请先核对 OneAPI 使用记录再重试。"
        if (
            latest
            and latest.status == MediaResolutionStatus.FAILED
            and latest.api_call_count > 0
            and self._is_non_transcribable_media_error(latest.error_message)
            and self._same_media_strategy(latest)
        ):
            return (
                "供应商详情接口返回的媒体当前不能直接进入转写链路"
                "（格式、体积或可读性未通过校验），"
                "为避免重复计费，请手动补直链或上传视频。"
            )
        if capability.mode != ProviderMode.PRODUCTION or not capability.enabled:
            return "当前供应商不是可用 Production 模式，不能付费补媒体直链。"
        if candidate.platform_item_id is None or not candidate.platform_item_id.strip():
            return "该候选缺少真实作品 ID，不能补媒体直链。"
        if candidate.platform_item_id.startswith("proxy-"):
            return "该候选只有本地代理 ID，供应商无法按真实作品 ID 补媒体直链。"
        if not hasattr(self.provider, "resolve_media_url"):
            return "当前供应商适配器不支持补媒体直链。"
        if estimated_cost is None:
            return "当前平台没有明确媒体解析单价，未发起付费调用。"
        if monthly_cost + estimated_cost > MONTHLY_HARD_LIMIT_COST_CNY:
            return "已达到本地本月 ¥10 共享预算上限，未发起付费解析。"
        return None

    @staticmethod
    def _is_non_transcribable_media_error(message: str | None) -> bool:
        if not message:
            return False
        return any(
            marker in message
            for marker in (
                "不是可识别的视频文件",
                "未返回可用于转写的视频文件直链",
                "视频文件直链格式无效",
                "视频文件超过",
            )
        )

    @staticmethod
    def _same_media_strategy(attempt: MediaResolutionAttempt) -> bool:
        if attempt.platform != Platform.DOUYIN:
            return True
        return "douyin_detail_low_bitrate_media" in attempt.warnings

    def _price(self, platform: Platform) -> float | None:
        if hasattr(self.provider, "media_resolution_price"):
            price = self.provider.media_resolution_price(platform)
            return None if price is None else float(price)
        return None

    def _monthly_budget_used(self) -> float:
        month_start = self.clock().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return self.repository.monthly_platform_query_cost(month_start)

    @staticmethod
    def _is_direct_video_url(url) -> bool:
        if url is None:
            return False
        parsed = urlparse(str(url))
        return parsed.scheme == "https" and parsed.path.casefold().endswith(
            (".mp4", ".mov")
        )
