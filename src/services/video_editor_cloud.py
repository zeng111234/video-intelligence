"""Cloud video-editor contracts, pricing, and deterministic safety rules.

This module intentionally has no FastAPI or vendor SDK dependency.  The API
layer can serialize the Pydantic models directly, while provider adapters stay
replaceable and testable without making paid calls.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


PRICE_VERSION = "aliyun-cn-mainland-2026-07-28"
QUOTE_TTL_SECONDS = 15 * 60
FUN_ASR_CNY_PER_SECOND = Decimal("0.00022")
QWEN_FLASH_INPUT_CNY_PER_MILLION_TOKENS = Decimal("0.15")
QWEN_FLASH_OUTPUT_CNY_PER_MILLION_TOKENS = Decimal("1.5")
MPS_CNY_PER_OUTPUT_MINUTE = {
    "720p": Decimal("0.0326"),
    "1080p": Decimal("0.0651"),
}
MIN_SILENCE_SECONDS = 1.5
SILENCE_EDGE_PADDING_SECONDS = 0.35
MAX_REMOVE_RANGES = 100
_COST_PRECISION = Decimal("0.000001")


class CloudEditorError(ValueError):
    """A user-displayable cloud editor validation error."""


class CloudProviderMode(StrEnum):
    SANDBOX = "sandbox"
    ALIYUN = "aliyun"


class OutputProfile(StrEnum):
    HD_720P = "720p"
    FULL_HD_1080P = "1080p"


class ProviderJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class EditStepKind(StrEnum):
    TRIM_SILENCE = "trim_silence"
    VERTICAL_FIT = "vertical_fit"
    SUBTITLES = "subtitles"
    TITLE = "title"
    BGM = "bgm"
    AUDIO_MIX = "audio_mix"


class TimeRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def _validate_order(self) -> TimeRange:
        if self.end <= self.start:
            raise ValueError("时间区间的 end 必须大于 start。")
        return self


class EditPlan(BaseModel):
    """Server-validated plan; remove ranges can never overlap spoken content."""

    model_config = ConfigDict(frozen=True)

    plan_version: str = "safe-light-edit-v1"
    duration_seconds: float = Field(gt=0)
    spoken_ranges: list[TimeRange] = Field(default_factory=list)
    remove_ranges: list[TimeRange] = Field(
        default_factory=list,
        max_length=MAX_REMOVE_RANGES,
    )
    enabled_steps: list[EditStepKind] = Field(default_factory=list)
    trim_silence_enabled: bool = False
    title_candidates: list[str] = Field(default_factory=list, max_length=5)
    explanation: str = ""
    warnings: list[str] = Field(default_factory=list)
    provider_name: str = "deterministic_rules"
    is_mock: bool = False
    usage: dict[str, int | float | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_safe_ranges(self) -> EditPlan:
        spoken = sorted(self.spoken_ranges, key=lambda item: (item.start, item.end))
        removed = sorted(self.remove_ranges, key=lambda item: (item.start, item.end))

        for label, ranges in (("语音", spoken), ("删除", removed)):
            previous_end = 0.0
            for index, item in enumerate(ranges):
                if item.end > self.duration_seconds:
                    raise ValueError(f"{label}区间超出视频时长。")
                if index and item.start < previous_end:
                    raise ValueError(f"{label}区间不能互相重叠。")
                previous_end = item.end

        for cut in removed:
            if any(
                cut.start < speech.end and speech.start < cut.end for speech in spoken
            ):
                raise ValueError("自动剪辑不能删除任何有人声的区间。")

        if bool(removed) != self.trim_silence_enabled:
            raise ValueError("trim_silence_enabled 必须与删除区间保持一致。")
        if (
            self.trim_silence_enabled
            and EditStepKind.TRIM_SILENCE not in self.enabled_steps
        ):
            raise ValueError("存在删除区间时必须启用 trim_silence 步骤。")
        return self


class QuoteLineItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    component: str
    provider: str
    quantity: Decimal = Field(ge=0)
    unit: str
    unit_price_cny: Decimal = Field(ge=0)
    estimated_cost_cny: Decimal = Field(ge=0)
    rate_details: dict[str, Decimal] = Field(default_factory=dict)


class CostQuote(BaseModel):
    model_config = ConfigDict(frozen=True)

    quote_id: str
    issued_at: datetime
    expires_at: datetime
    ttl_seconds: int = QUOTE_TTL_SECONDS
    price_version: str = PRICE_VERSION
    currency: str = "CNY"
    output_profile: OutputProfile
    line_items: list[QuoteLineItem]
    estimated_total: Decimal = Field(ge=0)
    estimated_max: Decimal = Field(ge=0)
    exclusions: list[str] = Field(
        default_factory=lambda: ["OSS 存储", "公网下行流量", "失败重试"],
    )


class CloudCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_mode: CloudProviderMode
    provider_name: str
    enabled: bool
    live_ready: bool
    missing_configuration: list[str] = Field(default_factory=list)
    is_mock: bool
    price_version: str = PRICE_VERSION
    quote_ttl_seconds: int = QUOTE_TTL_SECONDS
    supported_output_profiles: list[OutputProfile] = Field(
        default_factory=lambda: [
            OutputProfile.HD_720P,
            OutputProfile.FULL_HD_1080P,
        ],
    )


class CloudAsset(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_name: str
    bucket: str | None = None
    object_key: str
    uri: str
    media_type: str
    size_bytes: int = Field(ge=0)
    is_mock: bool = False
    provider_locator: str | None = Field(default=None, exclude=True, repr=False)


class ProviderJobSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_name: str
    provider_job_id: str
    provider_stage: str
    status: ProviderJobStatus
    is_mock: bool = False
    output_uri: str | None = None
    can_publish: bool = False
    usage: dict[str, int | float | str] = Field(default_factory=dict)
    detail: dict[str, Any] = Field(default_factory=dict)
    result_locator: str | None = Field(default=None, exclude=True, repr=False)


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str
    speaker_id: int | None = None

    @model_validator(mode="after")
    def _validate_order(self) -> TranscriptSegment:
        if self.end <= self.start:
            raise ValueError("转写分段的 end 必须大于 start。")
        return self


class CloudTranscript(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_name: str
    transcript: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    spoken_ranges: list[TimeRange] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0)
    language: str = ""
    is_mock: bool = False
    usage: dict[str, int | float | str] = Field(default_factory=dict)


class RenderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_asset: CloudAsset
    output_object_key: str = Field(min_length=1)
    output_profile: OutputProfile
    edit_plan: EditPlan
    review_confirmed: bool = False
    subtitle_object_key: str | None = None
    title: str = ""
    bgm_asset: CloudAsset | None = None
    bgm_volume: float = Field(default=0.2, ge=0, le=1)
    idempotency_key: str = Field(min_length=1)


class CloudObjectStore(Protocol):
    def upload(
        self,
        path: str | Path,
        object_key: str,
        *,
        media_type: str,
    ) -> CloudAsset: ...


class CloudASRProvider(Protocol):
    def submit(
        self,
        asset: CloudAsset,
        *,
        language_hints: Sequence[str] = ("zh",),
    ) -> ProviderJobSnapshot: ...

    def query(self, provider_job_id: str) -> ProviderJobSnapshot: ...

    def fetch_result(self, snapshot: ProviderJobSnapshot) -> CloudTranscript: ...


class EditPlanProvider(Protocol):
    def create_plan(
        self,
        transcript: str,
        spoken_ranges: Sequence[TimeRange | Mapping[str, float]],
        duration_seconds: float,
    ) -> EditPlan: ...


class CloudRenderProvider(Protocol):
    def submit(self, request: RenderRequest) -> ProviderJobSnapshot: ...

    def query(self, provider_job_id: str) -> ProviderJobSnapshot: ...


class CloudEditorConfiguration(BaseModel):
    """Environment-backed provider configuration without secret disclosure."""

    model_config = ConfigDict(frozen=True)

    provider_mode: CloudProviderMode = CloudProviderMode.SANDBOX
    workspace_id: str = ""
    dashscope_api_key: str = Field(default="", repr=False)
    oss_bucket: str = ""
    oss_location: str = "oss-cn-beijing"
    aliyun_region: str = "cn-beijing"
    access_key_id: str = Field(default="", repr=False)
    access_key_secret: str = Field(default="", repr=False)
    mps_pipeline_id: str = ""
    mps_template_id_720p: str = ""
    mps_template_id_1080p: str = ""
    price_version: str = PRICE_VERSION
    quote_ttl_seconds: int = Field(default=QUOTE_TTL_SECONDS, gt=0)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> CloudEditorConfiguration:
        source = os.environ if env is None else env
        raw_mode = (
            str(source.get("VIDEO_EDITOR_PROVIDER_MODE", "sandbox")).strip().casefold()
        )
        try:
            provider_mode = CloudProviderMode(raw_mode or "sandbox")
        except ValueError as exc:
            raise CloudEditorError(
                "VIDEO_EDITOR_PROVIDER_MODE 仅支持 sandbox 或 aliyun。",
            ) from exc
        raw_ttl = str(
            source.get("VIDEO_EDITOR_QUOTE_TTL_SECONDS", QUOTE_TTL_SECONDS),
        ).strip()
        try:
            quote_ttl_seconds = int(raw_ttl)
        except ValueError:
            quote_ttl_seconds = QUOTE_TTL_SECONDS
        quote_ttl_seconds = max(60, quote_ttl_seconds)
        aliyun_region = (
            str(
                source.get(
                    "ALIYUN_VIDEO_EDITOR_REGION",
                    source.get("ALIYUN_REGION", "cn-beijing"),
                ),
            ).strip()
            or "cn-beijing"
        )
        default_oss_location = (
            aliyun_region
            if aliyun_region.startswith("oss-")
            else f"oss-{aliyun_region}"
        )
        return cls(
            provider_mode=provider_mode,
            workspace_id=str(
                source.get("ALIYUN_MODEL_STUDIO_WORKSPACE_ID", ""),
            ).strip(),
            dashscope_api_key=str(source.get("DASHSCOPE_API_KEY", "")).strip(),
            oss_bucket=str(source.get("ALIYUN_OSS_BUCKET", "")).strip(),
            oss_location=str(
                source.get("ALIYUN_OSS_LOCATION", default_oss_location),
            ).strip()
            or default_oss_location,
            aliyun_region=aliyun_region.removeprefix("oss-"),
            access_key_id=str(
                source.get(
                    "ALIBABA_CLOUD_ACCESS_KEY_ID",
                    source.get("ALIYUN_ACCESS_KEY_ID", ""),
                ),
            ).strip(),
            access_key_secret=str(
                source.get(
                    "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
                    source.get("ALIYUN_ACCESS_KEY_SECRET", ""),
                ),
            ).strip(),
            mps_pipeline_id=str(source.get("ALIYUN_MPS_PIPELINE_ID", "")).strip(),
            mps_template_id_720p=str(
                source.get("ALIYUN_MPS_TEMPLATE_ID_720P", ""),
            ).strip(),
            mps_template_id_1080p=str(
                source.get("ALIYUN_MPS_TEMPLATE_ID_1080P", ""),
            ).strip(),
            price_version=str(
                source.get("VIDEO_EDITOR_PRICE_VERSION", PRICE_VERSION),
            ).strip()
            or PRICE_VERSION,
            quote_ttl_seconds=quote_ttl_seconds,
        )

    @property
    def missing_configuration(self) -> list[str]:
        if self.provider_mode == CloudProviderMode.SANDBOX:
            return []
        required = {
            "ALIYUN_MODEL_STUDIO_WORKSPACE_ID": self.workspace_id,
            "DASHSCOPE_API_KEY": self.dashscope_api_key,
            "ALIYUN_OSS_BUCKET": self.oss_bucket,
            "ALIBABA_CLOUD_ACCESS_KEY_ID": self.access_key_id,
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": self.access_key_secret,
            "ALIYUN_MPS_PIPELINE_ID": self.mps_pipeline_id,
            "ALIYUN_MPS_TEMPLATE_ID_720P": self.mps_template_id_720p,
            "ALIYUN_MPS_TEMPLATE_ID_1080P": self.mps_template_id_1080p,
        }
        return [name for name, value in required.items() if not value]

    def mps_template_id(self, profile: OutputProfile) -> str:
        return (
            self.mps_template_id_720p
            if profile == OutputProfile.HD_720P
            else self.mps_template_id_1080p
        )


def get_cloud_capability(config: CloudEditorConfiguration) -> CloudCapability:
    missing = config.missing_configuration
    sandbox = config.provider_mode == CloudProviderMode.SANDBOX
    return CloudCapability(
        provider_mode=config.provider_mode,
        provider_name="sandbox_cloud_editor" if sandbox else "aliyun_cloud_editor",
        enabled=sandbox or not missing,
        live_ready=not sandbox and not missing,
        missing_configuration=missing,
        is_mock=sandbox,
        price_version=config.price_version,
        quote_ttl_seconds=config.quote_ttl_seconds,
    )


def _decimal(value: int | float | str | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(_COST_PRECISION, rounding=ROUND_HALF_UP)


def create_cost_quote(
    *,
    input_duration_seconds: int | float | Decimal,
    output_duration_seconds: int | float | Decimal | None = None,
    output_profile: OutputProfile | str = OutputProfile.HD_720P,
    planning_input_tokens: int = 3000,
    planning_output_tokens: int = 1000,
    now: datetime | None = None,
    price_version: str = PRICE_VERSION,
    ttl_seconds: int = QUOTE_TTL_SECONDS,
) -> CostQuote:
    input_seconds = _decimal(input_duration_seconds)
    output_seconds = (
        input_seconds
        if output_duration_seconds is None
        else _decimal(output_duration_seconds)
    )
    if input_seconds <= 0 or output_seconds <= 0:
        raise CloudEditorError("输入与预计输出时长必须大于 0 秒。")
    if planning_input_tokens < 0 or planning_output_tokens < 0:
        raise CloudEditorError("规划 Token 估算不能为负数。")
    if ttl_seconds <= 0:
        raise CloudEditorError("费用报价有效期必须大于 0 秒。")
    try:
        profile = OutputProfile(output_profile)
    except ValueError as exc:
        raise CloudEditorError("输出档位仅支持 720p 或 1080p。") from exc

    input_token_quantity = Decimal(planning_input_tokens)
    output_token_quantity = Decimal(planning_output_tokens)
    asr_cost = _money(input_seconds * FUN_ASR_CNY_PER_SECOND)
    planning_input_cost = (
        input_token_quantity
        / Decimal(1_000_000)
        * QWEN_FLASH_INPUT_CNY_PER_MILLION_TOKENS
    )
    planning_output_cost = (
        output_token_quantity
        / Decimal(1_000_000)
        * QWEN_FLASH_OUTPUT_CNY_PER_MILLION_TOKENS
    )
    planning_cost = _money(planning_input_cost + planning_output_cost)
    planning_token_total = input_token_quantity + output_token_quantity
    blended_planning_rate = (
        planning_cost / planning_token_total if planning_token_total else Decimal("0")
    )
    output_minutes = output_seconds / Decimal(60)
    render_rate = MPS_CNY_PER_OUTPUT_MINUTE[profile.value]
    render_cost = _money(output_minutes * render_rate)

    line_items = [
        QuoteLineItem(
            component="speech_recognition",
            provider="Fun-ASR",
            quantity=input_seconds,
            unit="input_second",
            unit_price_cny=FUN_ASR_CNY_PER_SECOND,
            estimated_cost_cny=asr_cost,
        ),
        QuoteLineItem(
            component="edit_planning",
            provider="qwen-flash",
            quantity=planning_token_total,
            unit="estimated_token",
            unit_price_cny=blended_planning_rate,
            estimated_cost_cny=planning_cost,
            rate_details={
                "input_per_million_tokens": (QWEN_FLASH_INPUT_CNY_PER_MILLION_TOKENS),
                "output_per_million_tokens": (QWEN_FLASH_OUTPUT_CNY_PER_MILLION_TOKENS),
            },
        ),
        QuoteLineItem(
            component="cloud_render",
            provider="MPS H.264",
            quantity=output_minutes,
            unit="output_minute",
            unit_price_cny=render_rate,
            estimated_cost_cny=render_cost,
        ),
    ]
    total = _money(sum((item.estimated_cost_cny for item in line_items), Decimal("0")))
    issued_at = now or datetime.now(timezone.utc)
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    return CostQuote(
        quote_id=f"veq_{uuid4().hex}",
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=ttl_seconds),
        ttl_seconds=ttl_seconds,
        price_version=price_version,
        output_profile=profile,
        line_items=line_items,
        estimated_total=total,
        estimated_max=total,
    )


def validate_cost_quote(
    quote: CostQuote,
    quote_id: str,
    *,
    now: datetime | None = None,
    expected_price_version: str = PRICE_VERSION,
) -> CostQuote:
    if quote.quote_id != quote_id:
        raise CloudEditorError("费用报价与本次确认不匹配，请重新预检。")
    if quote.price_version != expected_price_version:
        raise CloudEditorError("计费价格版本已变化，请重新确认费用。")
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    if checked_at >= quote.expires_at:
        raise CloudEditorError("费用报价已过期，请重新预检并确认。")
    return quote


def _as_time_range(value: TimeRange | Mapping[str, float]) -> TimeRange:
    return value if isinstance(value, TimeRange) else TimeRange.model_validate(value)


def _merge_ranges(ranges: Sequence[TimeRange]) -> list[TimeRange]:
    merged: list[TimeRange] = []
    for item in sorted(ranges, key=lambda value: (value.start, value.end)):
        if merged and item.start <= merged[-1].end:
            merged[-1] = TimeRange(
                start=merged[-1].start,
                end=max(merged[-1].end, item.end),
            )
        else:
            merged.append(item)
    return merged


def build_safe_edit_plan(
    spoken_ranges: Sequence[TimeRange | Mapping[str, float]],
    duration_seconds: float,
    *,
    title_candidates: Sequence[str] | None = None,
    explanation: str = "",
    enabled_steps: Sequence[EditStepKind | str] | None = None,
    provider_name: str = "deterministic_rules",
    is_mock: bool = False,
    usage: Mapping[str, int | float | str] | None = None,
) -> EditPlan:
    if duration_seconds <= 0:
        raise CloudEditorError("视频时长必须大于 0 秒。")
    spoken = _merge_ranges([_as_time_range(item) for item in spoken_ranges])
    if any(item.end > duration_seconds for item in spoken):
        raise CloudEditorError("语音区间不能超出视频时长。")

    cuts: list[TimeRange] = []
    for left, right in zip(spoken, spoken[1:], strict=False):
        gap_seconds = right.start - left.end
        if gap_seconds < MIN_SILENCE_SECONDS:
            continue
        cut_start = left.end + SILENCE_EDGE_PADDING_SECONDS
        cut_end = right.start - SILENCE_EDGE_PADDING_SECONDS
        if cut_end > cut_start:
            cuts.append(TimeRange(start=cut_start, end=cut_end))
    cuts = _merge_ranges(cuts)

    warnings: list[str] = []
    if not spoken:
        warnings.append("未检测到可靠语音区间，未自动裁剪。")
    if len(cuts) > MAX_REMOVE_RANGES:
        cuts = []
        warnings.append("安全裁剪区间合并后仍超过 100 段，已关闭自动裁停顿。")

    requested_steps = (
        [
            EditStepKind.TRIM_SILENCE,
            EditStepKind.VERTICAL_FIT,
            EditStepKind.SUBTITLES,
            EditStepKind.TITLE,
            EditStepKind.BGM,
            EditStepKind.AUDIO_MIX,
        ]
        if enabled_steps is None
        else [EditStepKind(item) for item in enabled_steps]
    )
    unique_steps = list(dict.fromkeys(requested_steps))
    if not cuts:
        unique_steps = [
            step for step in unique_steps if step != EditStepKind.TRIM_SILENCE
        ]
    elif EditStepKind.TRIM_SILENCE not in unique_steps:
        unique_steps.insert(0, EditStepKind.TRIM_SILENCE)

    titles = [
        str(item).strip() for item in (title_candidates or []) if str(item).strip()
    ]
    return EditPlan(
        duration_seconds=duration_seconds,
        spoken_ranges=spoken,
        remove_ranges=cuts,
        enabled_steps=unique_steps,
        trim_silence_enabled=bool(cuts),
        title_candidates=titles[:5],
        explanation=explanation.strip(),
        warnings=warnings,
        provider_name=provider_name,
        is_mock=is_mock,
        usage=dict(usage or {}),
    )
