from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.adapters.video_editor_cloud import build_cloud_providers
from src.services.video_editor_cloud import (
    CloudEditorConfiguration,
    CloudProviderMode,
    ProviderJobSnapshot,
)

ASR_PRICE_VERSION = "aliyun-fun-asr-cn-beijing-2026-07-31"
ASR_UNIT_PRICE_CNY_PER_SECOND = Decimal("0.00022")
ASR_PER_TASK_CAP_CNY = Decimal("0.20")


class ASRAuthorization(BaseModel):
    confirmed: bool = False
    provider_name: str = "aliyun_fun_asr"
    price_version: str = ASR_PRICE_VERSION
    unit_price_cny_per_second: Decimal = ASR_UNIT_PRICE_CNY_PER_SECOND
    per_task_cap_cny: Decimal = ASR_PER_TASK_CAP_CNY
    confirmed_at: datetime | None = None


class ASRAuthorizationStore:
    """Persist the administrator's one-time ASR billing authorization."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> ASRAuthorization:
        if not self.path.exists():
            return ASRAuthorization()
        try:
            return ASRAuthorization.model_validate_json(
                self.path.read_text(encoding="utf-8"),
            )
        except (OSError, ValueError):
            return ASRAuthorization()

    def save(self, authorization: ASRAuthorization) -> ASRAuthorization:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            authorization.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
        return authorization


class AliyunFunASRRuntime:
    """Small ASR-only facade over the existing OSS and Fun-ASR adapters."""

    def __init__(
        self,
        *,
        authorization_store: ASRAuthorizationStore,
        configuration: CloudEditorConfiguration | None = None,
        providers: Any | None = None,
    ) -> None:
        self.authorization_store = authorization_store
        self.configuration = configuration or CloudEditorConfiguration.from_env()
        self.providers = providers or build_cloud_providers(self.configuration)

    @property
    def missing_configuration(self) -> list[str]:
        required = {
            "VIDEO_EDITOR_PROVIDER_MODE=aliyun": (
                self.configuration.provider_mode == CloudProviderMode.ALIYUN
            ),
            "ALIYUN_MODEL_STUDIO_WORKSPACE_ID": self.configuration.workspace_id,
            "DASHSCOPE_API_KEY": self.configuration.dashscope_api_key,
            "ALIYUN_OSS_BUCKET": self.configuration.oss_bucket,
            "ALIBABA_CLOUD_ACCESS_KEY_ID": self.configuration.access_key_id,
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": (
                self.configuration.access_key_secret
            ),
        }
        return [name for name, value in required.items() if not value]

    @property
    def live_ready(self) -> bool:
        return not self.missing_configuration

    def estimate_cost(self, duration_seconds: float) -> Decimal:
        # 单价以管理员定价为准（可调），未覆盖时用代码默认值
        from src.services.pricing import get_price

        unit_price = get_price("transcription_per_second_cny")
        return (Decimal(str(duration_seconds)) * unit_price).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

    def ensure_authorized(self, duration_seconds: float) -> Decimal:
        if self.missing_configuration:
            raise RuntimeError(
                "阿里云语音识别尚未配置完成，请联系管理员补充云服务配置。"
            )
        authorization = self.authorization_store.load()
        if (
            not authorization.confirmed
            or authorization.provider_name != "aliyun_fun_asr"
            or authorization.price_version != ASR_PRICE_VERSION
            or authorization.unit_price_cny_per_second
            != ASR_UNIT_PRICE_CNY_PER_SECOND
        ):
            raise RuntimeError(
                "阿里云语音识别费用尚未授权，或价格版本已经变化；"
                "请管理员在系统设置中重新确认。"
            )
        estimated = self.estimate_cost(duration_seconds)
        # 单条费用上限以管理员定价为准（可调），未覆盖时用代码默认值
        from src.services.pricing import get_price

        cap = get_price("transcription_max_per_item_cny")
        if estimated > cap:
            raise RuntimeError(
                f"本次语音识别预计费用 ¥{estimated:.4f}，"
                f"超过管理员设置的单条上限 ¥{cap:.2f}。"
            )
        return estimated

    def capability(self) -> dict[str, Any]:
        authorization = self.authorization_store.load()
        return {
            "provider_mode": "aliyun",
            "provider_name": "aliyun_fun_asr",
            "enabled": self.live_ready and authorization.confirmed,
            "live_ready": self.live_ready,
            "missing_configuration": self.missing_configuration,
            "is_mock": False,
            "billing_authorized": authorization.confirmed
            and authorization.price_version == ASR_PRICE_VERSION,
            "unit_price_cny_per_second": float(ASR_UNIT_PRICE_CNY_PER_SECOND),
            "per_task_cost_cap_cny": float(authorization.per_task_cap_cny),
            "price_version": ASR_PRICE_VERSION,
            "supports_local_fallback": False,
            "cost_exclusions": ["OSS 存储", "公网流量", "失败后的人工重试"],
        }

    def upload(
        self,
        media_path: str | Path,
        *,
        object_key: str,
        media_type: str,
    ):
        return self.providers.object_store.upload(
            media_path,
            object_key,
            media_type=media_type,
        )

    def submit(self, asset, *, language: str) -> ProviderJobSnapshot:
        language_hints = ("zh",) if language == "auto" else (language,)
        return self.providers.asr.submit(
            asset,
            language_hints=language_hints,
        )

    def query(self, provider_job_id: str) -> ProviderJobSnapshot:
        return self.providers.asr.query(provider_job_id)

    def fetch_result(self, snapshot: ProviderJobSnapshot):
        return self.providers.asr.fetch_result(snapshot)
