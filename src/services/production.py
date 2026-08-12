"""可复用 IP 配方与可恢复的批量生产控制服务。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.adapters.douyin_parser import DouyinParserError
from src.contracts import TaskRepository
from src.services.publish_metadata import (
    publish_draft_fingerprint,
    suggested_publish_draft,
    validated_publish_draft,
)
from src.models import (
    AvatarTask,
    PipelineRunStatus,
    PipelineStage,
    CopywritingTask,
    ProductionBatch,
    ProductionBatchItem,
    ProductionBatchItemStatus,
    ProductionBatchStatus,
    ProductionProfile,
    ProductionWorkspaceConfiguration,
    PublishTask,
    PublishPlatform,
    PublishStatus,
    TaskStatus,
    TranscriptionTask,
)

DEFAULT_PRODUCTION_TEMPLATE_ID = "short_video_optimize"
BUNDLED_DEFAULT_PROFILE_NAME = "大树1"
BUNDLED_DEFAULT_AVATAR_ID = "shuying-avatar-21920"
BUNDLED_DEFAULT_VOICE_ID = "shuying-voice-7869"


class IdempotencyConflictError(ValueError):
    """同一个幂等键被用于不同请求。"""


class ProductionService:
    """保存 IP 配方、预检批次并为 worker 提供可恢复的调度状态。"""

    # 数字人服务通常应在三分钟内返回。超过这个时间只提示偏慢并继续
    # 查询原任务，绝不能为了“看起来卡住”而重复提交一次可能收费的生成。
    AVATAR_DELAY_WARNING_SECONDS = 180

    def __init__(
        self,
        repository: TaskRepository,
        storage_directory: str | Path,
        *,
        media_resolution_service=None,
        link_transcription_service=None,
        copywriting_service=None,
        avatar_service=None,
        template_service=None,
        publish_service=None,
        bootstrap_bundled_default_profile: bool = False,
    ) -> None:
        self.repository = repository
        self.storage_directory = Path(storage_directory)
        self.storage_directory.mkdir(parents=True, exist_ok=True)
        self._profiles_path = self.storage_directory / "profiles.json"
        self._workspace_configuration_path = (
            self.storage_directory / "workspace_configuration.json"
        )
        self._legacy_batches_path = self.storage_directory / "batches.json"
        self.media_resolution_service = media_resolution_service
        self.link_transcription_service = link_transcription_service
        self.copywriting_service = copywriting_service
        self.avatar_service = avatar_service
        self.template_service = template_service
        self.publish_service = publish_service
        self.bootstrap_bundled_default_profile = bootstrap_bundled_default_profile
        self._import_legacy_batches_once()
        self._bootstrap_bundled_default_profile()

    @staticmethod
    def _load(path: Path, model_type):
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [model_type.model_validate(item) for item in payload]

    @staticmethod
    def _save(path: Path, records: list) -> None:
        path.write_text(
            json.dumps(
                [record.model_dump(mode="json") for record in records],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _import_legacy_batches_once(self) -> None:
        """兼容旧计划文件；SQLite 已有记录时不覆盖。"""
        if not self._legacy_batches_path.exists():
            return
        for batch in self._load(self._legacy_batches_path, ProductionBatch):
            if self.repository.get_production_batch(batch.batch_id) is None:
                self.repository.save_production_batch(batch)

    def _bootstrap_bundled_default_profile(self) -> None:
        """正式桌面版首次启动时复用已购买资产，不覆盖任何客户配方。"""
        if self._profiles_path.exists() or not self.bootstrap_bundled_default_profile:
            return
        profile = ProductionProfile(
            name=BUNDLED_DEFAULT_PROFILE_NAME,
            description="已购买并授权的大树1形象与声音",
            platform="douyin",
            avatar_id=BUNDLED_DEFAULT_AVATAR_ID,
            voice_id=BUNDLED_DEFAULT_VOICE_ID,
            edit_template_id=DEFAULT_PRODUCTION_TEMPLATE_ID,
        )
        self._save(self._profiles_path, [profile])

    def list_profiles(self) -> list[ProductionProfile]:
        return sorted(
            self._load(self._profiles_path, ProductionProfile),
            key=lambda item: item.updated_at,
            reverse=True,
        )

    def get_profile(self, profile_id: str) -> ProductionProfile | None:
        return next((item for item in self.list_profiles() if item.profile_id == profile_id), None)

    def get_workspace_configuration(self) -> ProductionWorkspaceConfiguration | None:
        """读取客户首次设置；不存在时明确返回未设置，绝不默认授权。"""
        if not self._workspace_configuration_path.exists():
            return None
        payload = json.loads(
            self._workspace_configuration_path.read_text(encoding="utf-8")
        )
        return ProductionWorkspaceConfiguration.model_validate(payload)

    def configure_workspace(
        self,
        *,
        rights_holder: str,
        agreement_accepted: bool,
        default_profile_id: str | None = None,
        default_publish_platforms: list[str] | None = None,
        copywriting_estimated_cost_cny: float | None = None,
        avatar_estimated_cost_cny: float | None = None,
        bundled_compute: bool = True,
    ) -> ProductionWorkspaceConfiguration:
        """保存一次性基础设置，供客户工作台后续任务复用。"""
        if not agreement_accepted:
            raise ValueError("请先确认拥有本次创作所需的授权。")
        normalized_holder = rights_holder.strip()
        if not normalized_holder:
            raise ValueError("请填写授权主体。")
        if default_profile_id and self.get_profile(default_profile_id) is None:
            raise ValueError("默认 IP 配方不存在。")
        platforms = [
            item.strip()
            for item in (default_publish_platforms or ["douyin"])
            if item.strip()
        ]
        existing = self.get_workspace_configuration()
        configuration = ProductionWorkspaceConfiguration(
            rights_holder=normalized_holder,
            default_profile_id=default_profile_id or None,
            default_publish_platforms=platforms or ["douyin"],
            copywriting_estimated_cost_cny=(
                copywriting_estimated_cost_cny
                if copywriting_estimated_cost_cny is not None
                else (existing.copywriting_estimated_cost_cny if existing else None)
            ),
            avatar_estimated_cost_cny=(
                avatar_estimated_cost_cny
                if avatar_estimated_cost_cny is not None
                else (existing.avatar_estimated_cost_cny if existing else None)
            ),
            bundled_compute=bundled_compute,
        )
        self._workspace_configuration_path.write_text(
            json.dumps(
                configuration.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return configuration

    def resolve_workspace_execution_options(
        self, options: dict[str, Any]
    ) -> dict[str, Any]:
        """把已保存的首次授权应用到客户工作台，不影响高级接口显式传参。"""
        configuration = self.get_workspace_configuration()
        if configuration is None:
            return dict(options)
        resolved = dict(options)
        resolved["rights_holder"] = configuration.rights_holder
        resolved["rights_confirmed"] = True
        if not resolved.get("publish_platforms"):
            resolved["publish_platforms"] = list(
                configuration.default_publish_platforms
            )
        if configuration.bundled_compute:
            resolved["paid_actions_confirmed"] = True
        return resolved

    def create_profile(
        self,
        *,
        name: str,
        description: str = "",
        target_audience: str = "",
        platform: str = "douyin",
        script_style: str = "",
        avatar_id: str | None = None,
        voice_id: str | None = None,
        edit_template_id: str | None = None,
        tags: list[str] | None = None,
    ) -> ProductionProfile:
        profiles = self.list_profiles()
        normalized_name = name.strip()
        if any(item.name == normalized_name for item in profiles):
            raise ValueError("IP 配方名称已存在。")
        profile = ProductionProfile(
            name=normalized_name,
            description=description.strip(),
            target_audience=target_audience.strip(),
            platform=platform.strip() or "douyin",
            script_style=script_style.strip(),
            avatar_id=avatar_id or None,
            voice_id=voice_id or None,
            edit_template_id=edit_template_id or DEFAULT_PRODUCTION_TEMPLATE_ID,
            tags=[tag.strip() for tag in (tags or []) if tag.strip()],
        )
        self._save(self._profiles_path, [profile, *profiles])
        return profile

    def list_batches(self) -> list[ProductionBatch]:
        return self.repository.list_production_batches(limit=100)

    def get_batch(self, batch_id: str) -> ProductionBatch | None:
        return self.repository.get_production_batch(batch_id)

    def create_batch(
        self,
        *,
        name: str,
        profile_id: str,
        pipeline_service,
        candidate_ids: list[str] | None = None,
        source_items: list[dict[str, Any]] | None = None,
        idempotency_key: str = "",
        request_hash: str = "",
    ) -> ProductionBatch:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise ValueError("IP 配方不存在。")
        normalized_sources = self._normalize_source_items(candidate_ids or [], source_items or [])
        if not normalized_sources:
            raise ValueError("请至少添加一条候选、链接、选题或完整文案。")
        if len(normalized_sources) > 400:
            raise ValueError("单个批次最多包含 400 条内容。")
        getter = getattr(self.repository, "get_candidate", None)

        batch = ProductionBatch(
            name=name.strip(),
            profile_id=profile.profile_id,
            profile_name=profile.name,
            execution_config=self._with_idempotency_record(
                {},
                operation="create",
                key=idempotency_key,
                request_hash=request_hash,
            ),
        )
        claimed, existing, operation_resource_id = self._claim_operation(
            operation="create",
            key=idempotency_key,
            request_hash=request_hash,
            resource_id=batch.batch_id,
        )
        if not claimed:
            assert existing is not None
            return existing
        if operation_resource_id != batch.batch_id:
            batch = batch.model_copy(
                update={"batch_id": operation_resource_id}
            )
        items: list[ProductionBatchItem] = []
        runs = []
        try:
            for source in normalized_sources:
                source_type = source["source_type"]
                source_value = source["source_value"]
                candidate_role = source["candidate_role"]
                overrides = source["profile_overrides"]
                candidate = None
                if source_type == "candidate":
                    if getter is None:
                        raise RuntimeError("当前仓库不支持候选生产计划。")
                    candidate = getter(source_value)
                    if candidate is None:
                        raise ValueError(f"候选不存在：{source_value}")
                merged_profile = {**profile.model_dump(mode="json"), **overrides}
                title = source["display_title"] or (
                    candidate.title if candidate is not None else source_value[:80]
                )
                workflow = {
                    "candidate": "production_batch_candidate",
                    "share_link": "production_batch_share_link",
                    "brief": "production_batch_brief",
                    "script": "production_batch_script",
                }[source_type]
                run = pipeline_service.build_run(
                    keyword=title[:200],
                    config={
                        "source": "production_batch_plan",
                        "source_type": source_type,
                        "source_value": source_value,
                        "share_text": (
                            str(candidate.source_url or "")
                            if candidate is not None
                            else (source_value if source_type == "share_link" else "")
                        ),
                        "workflow": workflow,
                        "batch_id": batch.batch_id,
                        "profile": merged_profile,
                        "candidate_id": candidate.video_id if candidate is not None else "",
                        "candidate_role": candidate_role,
                        "candidate_platform": (
                            candidate.platform.value if candidate is not None else ""
                        ),
                        "next_action": "完成批次预检并启动后，后台将按队列执行。",
                    },
                )
                if candidate is not None:
                    run = run.model_copy(
                        update={
                            "candidate_video_id": candidate.video_id,
                            "updated_at": datetime.now().astimezone(),
                        }
                    )
                run = pipeline_service._event(
                    run,
                    action="batch_plan_created",
                    message="已加入批量生产计划，尚未调用任何生成或发布供应商。",
                    details={
                        "batch_id": batch.batch_id,
                        "profile_id": profile.profile_id,
                    },
                )
                runs.append(run)
                items.append(
                    ProductionBatchItem(
                        candidate_id=(
                            candidate.video_id if candidate is not None else ""
                        ),
                        run_id=run.run_id,
                        source_type=source_type,
                        source_value=source_value,
                        display_title=title,
                        candidate_role=candidate_role,
                        profile_overrides=overrides,
                    )
                )
            batch = batch.model_copy(update={"items": items})
            self._complete_operation(
                operation="create",
                key=idempotency_key,
                request_hash=request_hash,
                resource_id=batch.batch_id,
                batch=batch,
                runs=runs,
            )
            return batch
        except Exception as exc:
            for run in runs:
                self.repository.delete_pipeline_run(run.run_id)
            self._fail_operation(
                operation="create",
                key=idempotency_key,
                request_hash=request_hash,
                resource_id=batch.batch_id,
                error=exc,
            )
            raise

    @staticmethod
    def request_hash(payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _claim_operation(
        self,
        *,
        operation: str,
        key: str,
        request_hash: str,
        resource_id: str,
    ) -> tuple[bool, ProductionBatch | None, str]:
        if not key:
            return True, None, resource_id
        now_value = datetime.now().astimezone()
        now = now_value.isoformat()
        claimed = self.repository.claim_production_operation(
            operation_type=operation,
            idempotency_key=key,
            request_hash=request_hash,
            resource_id=resource_id,
            created_at=now,
        )
        if claimed:
            return True, None, resource_id
        record = self.repository.get_production_operation(
            operation_type=operation,
            idempotency_key=key,
        )
        same_resource = (
            operation == "create" or record.get("resource_id") == resource_id
        ) if record is not None else False
        if (
            record is None
            or record.get("request_hash") != request_hash
            or not same_resource
        ):
            raise IdempotencyConflictError(
                "该幂等键已用于不同请求，请刷新页面后重新操作。"
            )
        recorded_resource_id = str(record.get("resource_id") or resource_id)
        if record.get("state") == "failed":
            raise ValueError(
                str(record.get("error_message") or "此前相同请求执行失败，请使用新的幂等键重试。")
            )
        existing = self.get_batch(recorded_resource_id)
        if record.get("state") == "completed":
            if existing is None:
                raise IdempotencyConflictError(
                    "幂等记录已完成但原资源缺失，请联系管理员核对数据。"
                )
            return False, existing, recorded_resource_id
        updated_at_raw = str(record.get("updated_at") or "")
        try:
            updated_at = datetime.fromisoformat(updated_at_raw)
        except ValueError:
            updated_at = now_value
        if now_value - updated_at > timedelta(minutes=5):
            reclaimed = self.repository.reclaim_production_operation(
                operation_type=operation,
                idempotency_key=key,
                expected_updated_at=updated_at_raw,
                updated_at=now,
            )
            if reclaimed:
                return True, None, recorded_resource_id
        raise IdempotencyConflictError(
            "相同请求正在处理中；为避免重复扣费，当前不会并发重提。"
        )

    def _complete_operation(
        self,
        *,
        operation: str,
        key: str,
        request_hash: str,
        resource_id: str,
        batch: ProductionBatch,
        runs: list,
    ) -> None:
        if not key:
            for run in runs:
                self.repository.save_pipeline_run(run)
            self.repository.save_production_batch(batch)
            return
        self.repository.complete_production_operation(
            operation_type=operation,
            idempotency_key=key,
            request_hash=request_hash,
            state="completed",
            updated_at=datetime.now().astimezone().isoformat(),
            resource_id=resource_id,
            batch=batch,
            runs=runs,
        )

    def _fail_operation(
        self,
        *,
        operation: str,
        key: str,
        request_hash: str,
        resource_id: str,
        error: Exception,
    ) -> None:
        if not key:
            return
        self.repository.complete_production_operation(
            operation_type=operation,
            idempotency_key=key,
            request_hash=request_hash,
            state="failed",
            updated_at=datetime.now().astimezone().isoformat(),
            resource_id=resource_id,
            error_message=str(error),
        )

    @staticmethod
    def _with_idempotency_record(
        config: dict[str, Any],
        *,
        operation: str,
        key: str,
        request_hash: str,
    ) -> dict[str, Any]:
        if not key:
            return dict(config)
        records = list(config.get("_idempotency_records") or [])
        if not any(item.get("operation") == operation and item.get("key") == key for item in records):
            records.append(
                {
                    "operation": operation,
                    "key": key,
                    "request_hash": request_hash,
                }
            )
        return {**config, "_idempotency_records": records}

    @staticmethod
    def _normalize_source_items(candidate_ids: list[str], source_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """标准化混合来源并按来源和值去重，不在这里调用外部服务。"""
        raw: list[dict[str, Any]] = [
            {"source_type": "candidate", "source_value": value}
            for value in candidate_ids
        ] + source_items
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        allowed = {"candidate", "share_link", "brief", "script"}
        for item in raw:
            source_type = str(item.get("source_type") or "candidate").strip()
            value = str(item.get("source_value") or item.get("value") or "").strip()
            if source_type not in allowed:
                raise ValueError(f"不支持的批量来源：{source_type}")
            if not value:
                raise ValueError("批量来源内容不能为空。")
            if source_type == "share_link" and not value.lower().startswith(("http://", "https://")):
                raise ValueError("分享链接必须以 http:// 或 https:// 开头。")
            key = (source_type, value.casefold())
            if key in seen:
                continue
            seen.add(key)
            overrides = item.get("profile_overrides") or {}
            normalized.append({
                "source_type": source_type,
                "source_value": value,
                "display_title": str(item.get("display_title") or item.get("title") or "").strip(),
                "candidate_role": (
                    "reserve"
                    if source_type == "candidate"
                    and str(item.get("candidate_role") or "").casefold() == "reserve"
                    else "primary"
                ),
                "profile_overrides": {
                    key: str(value)
                    for key, value in overrides.items()
                    if key in {"avatar_id", "voice_id", "edit_template_id"} and str(value).strip()
                },
            })
        return normalized

    def preflight_batch(
        self,
        batch_id: str,
        *,
        rights_holder: str,
        rights_confirmed: bool,
        publish_platforms: list[str],
        concurrency: int = 1,
        max_total_cost_cny: float | None = None,
        paid_actions_confirmed: bool = False,
        automation_mode: str = "manual",
    ) -> dict[str, Any]:
        batch = self.get_batch(batch_id)
        if batch is None:
            raise ValueError("生产批次不存在。")
        if not 1 <= concurrency <= 5:
            raise ValueError("并发数只支持 1 到 5。")
        if max_total_cost_cny is not None and max_total_cost_cny < 0:
            raise ValueError("费用上限不能小于 0。")
        workspace_configuration = self.get_workspace_configuration()
        bundled_compute = bool(
            workspace_configuration is not None
            and workspace_configuration.bundled_compute
        )
        profile = self.get_profile(batch.profile_id)
        shared: list[str] = []
        platforms: list[PublishPlatform] = []
        assets_by_id: dict[str, Any] = {}
        avatar_cost = 0.0
        avatar_cost_known = True
        if not rights_confirmed or not rights_holder.strip():
            shared.append("必须确认拥有媒体、文案、肖像和声音处理授权，并填写授权主体。")
        if profile is None:
            shared.append("IP 配方不存在。")
        else:
            if not all([profile.avatar_id, profile.voice_id]):
                shared.append("IP 配方必须绑定数字人形象和音色。")
            if self.avatar_service is None:
                shared.append("数字人服务未配置。")
            elif profile.avatar_id and profile.voice_id:
                assets_by_id = {item.asset_id: item for item in self.avatar_service.list_assets()}
                for asset_id, label in ((profile.avatar_id, "数字人形象"), (profile.voice_id, "音色")):
                    asset = assets_by_id.get(asset_id)
                    if asset is None:
                        shared.append(f"IP 配方绑定的{label}不存在。")
                    elif not asset.authorized:
                        shared.append(f"IP 配方绑定的{label}未标记为已授权。")
                capabilities = getattr(self.avatar_service, "capabilities", None)
                if callable(capabilities):
                    capability = capabilities()
                    mode = str(getattr(capability, "mode", "") or "")
                    estimated = getattr(capability, "estimated_cost_cny", None)
                    if estimated is None and mode.casefold() not in {"sandbox", "providermode.sandbox"}:
                        if bundled_compute:
                            avatar_cost = 0.0
                        else:
                            configured_cost = (
                                workspace_configuration.avatar_estimated_cost_cny
                                if workspace_configuration is not None
                                else None
                            )
                            if configured_cost is None:
                                avatar_cost_known = False
                            else:
                                avatar_cost = float(configured_cost)
                    else:
                        avatar_cost = float(estimated or 0)
            if self.template_service is None:
                shared.append("系统通用智能优化暂不可用。")
            else:
                template_id = profile.edit_template_id or DEFAULT_PRODUCTION_TEMPLATE_ID
                if self.template_service.get_template(template_id) is None:
                    shared.append("系统通用智能优化配置异常。")
        for value in publish_platforms:
            try:
                platforms.append(PublishPlatform(value))
            except ValueError:
                shared.append(f"不支持的发布平台：{value}")
        if not platforms:
            shared.append("至少选择一个发布平台。")
        platform_summary: list[dict[str, Any]] = []
        if self.publish_service is None:
            shared.append("发布服务未配置。")
        else:
            available = {item["platform"]: item for item in self.publish_service.available_platforms()}
            for platform in platforms:
                capability = available.get(platform.value)
                if capability is None:
                    shared.append(f"未注册 {platform.value} 发布适配器。")
                else:
                    platform_summary.append(capability)
                    if not (capability["enabled"] or capability["manual_fallback"]):
                        shared.append(f"{capability['display_name']} 未启用且没有人工发布兜底。")

        copy_capability: dict[str, Any] | None = None
        copy_capability_error = ""
        if self.copywriting_service is None:
            copy_capability_error = "AI 文案服务未配置。"
        else:
            capabilities = getattr(self.copywriting_service, "capabilities", None)
            if not callable(capabilities):
                copy_capability_error = "AI 文案服务没有暴露能力与费用信息。"
            else:
                try:
                    copy_capability = dict(capabilities())
                except Exception:
                    copy_capability_error = "AI 文案服务能力检查失败。"

        prepared_items: list[dict[str, Any]] = []
        total_cost = 0.0
        budget_used = 0.0
        all_costs_known = True
        manual_script_audit = str(automation_mode or "manual").casefold() != "auto"
        for item in batch.items:
            reasons = list(shared)
            item_cost = avatar_cost
            item_cost_known = avatar_cost_known
            use_paid_fallback = False
            use_candidate_link_fallback = False
            rewrite_required = item.source_type in {"candidate", "share_link", "brief"}
            audit_required = manual_script_audit and item.source_type in {
                "candidate",
                "share_link",
                "brief",
                "script",
            }
            # 候选/分享链接最多预留一次整段转写 AI 校对，实际仅在云端
            # 返回低置信片段时调用；预检按上限报价，避免隐藏额外费用。
            transcript_review_reserved = item.source_type in {"candidate", "share_link"}
            copy_call_count = (
                int(rewrite_required)
                + int(audit_required)
                + int(transcript_review_reserved)
            )
            if copy_call_count:
                if copy_capability_error:
                    reasons.append(copy_capability_error)
                    item_cost_known = False
                elif copy_capability is None or not bool(
                    copy_capability.get("enabled")
                ):
                    missing = "、".join(
                        str(value)
                        for value in (
                            (copy_capability or {}).get(
                                "missing_configuration"
                            )
                            or []
                        )
                    )
                    reasons.append(
                        "AI 文案服务不可用"
                        + (f"，缺少配置：{missing}" if missing else "。")
                    )
                    item_cost_known = False
                else:
                    copy_mode = str(
                        copy_capability.get("mode") or ""
                    ).casefold()
                    copy_cost = copy_capability.get("estimated_cost_cny")
                    if copy_cost is None and copy_mode != "sandbox":
                        if bundled_compute:
                            item_cost += 0.0
                        else:
                            configured_cost = (
                                workspace_configuration.copywriting_estimated_cost_cny
                                if workspace_configuration is not None
                                else None
                            )
                            if configured_cost is None:
                                item_cost_known = False
                            else:
                                item_cost += float(configured_cost) * copy_call_count
                    else:
                        item_cost += float(copy_cost or 0) * copy_call_count
            if item.profile_overrides:
                if self.avatar_service is not None:
                    for key, label in (("avatar_id", "数字人形象"), ("voice_id", "音色")):
                        asset_id = item.profile_overrides.get(key)
                        if not asset_id:
                            continue
                        asset = assets_by_id.get(asset_id)
                        if asset is None:
                            reasons.append(f"单条覆盖的{label}不存在。")
                        elif not asset.authorized:
                            reasons.append(f"单条覆盖的{label}未标记为已授权。")
                if self.template_service is not None:
                    template_id = item.profile_overrides.get("edit_template_id")
                    if template_id and self.template_service.get_template(template_id) is None:
                        reasons.append("单条覆盖的剪辑模板不存在。")
            if item.source_type == "candidate":
                candidate = getattr(self.repository, "get_candidate", lambda _: None)(item.candidate_id)
                if candidate is None:
                    reasons.append("候选不存在或已被删除。")
                elif candidate.platform.value == "douyin" and self.media_resolution_service is None:
                    reasons.append("媒体解析服务未配置。")
                elif candidate.platform.value == "douyin":
                    preview = self.media_resolution_service.preview(candidate)
                    budget_used = max(budget_used, float(preview.monthly_budget_used_cny or 0))
                    if preview.resolvable:
                        if bundled_compute:
                            item_cost += 0.0
                        elif preview.estimated_cost_cny is None:
                            item_cost_known = False
                        else:
                            item_cost += float(preview.estimated_cost_cny)
                    elif candidate.source_url and self.link_transcription_service is not None:
                        try:
                            link_preview = self.link_transcription_service.preview(
                                str(candidate.source_url)
                            )
                        except DouyinParserError as exc:
                            reasons.append(exc.user_message)
                        else:
                            if link_preview.parser_enabled:
                                use_candidate_link_fallback = True
                            elif not link_preview.oneapi_fallback_available:
                                reasons.append(
                                    link_preview.parser_message
                                    or preview.block_reason
                                    or "候选当前不能提取可转写内容。"
                                )
                            elif link_preview.oneapi_estimated_cost_cny is None:
                                item_cost_known = False
                                reasons.append("候选链接的付费回退费用尚未配置。")
                            else:
                                item_cost += float(
                                    link_preview.oneapi_estimated_cost_cny
                                )
                                use_paid_fallback = True
                                use_candidate_link_fallback = True
                    else:
                        reasons.append(preview.block_reason or "候选不能进入媒体解析。")
                elif not str(candidate.source_url or "").lower().startswith(
                    ("http://", "https://")
                ):
                    reasons.append("该平台候选缺少可打开的原视频链接。")
                elif self.link_transcription_service is None:
                    item_cost_known = False
                    reasons.append("本机平台链接解析服务未配置。")
                else:
                    try:
                        link_preview = self.link_transcription_service.preview(
                            str(candidate.source_url)
                        )
                    except DouyinParserError as exc:
                        reasons.append(exc.user_message)
                    else:
                        if not link_preview.parser_enabled:
                            reasons.append(
                                link_preview.parser_message
                                or "本机浏览器暂时不能解析该平台链接。"
                            )
            elif item.source_type == "share_link":
                if not item.source_value.lower().startswith(("http://", "https://")):
                    reasons.append("分享链接格式无效。")
                elif self.link_transcription_service is None:
                    item_cost_known = False
                    reasons.append("分享链接解析服务未配置，无法确认费用和可用性。")
                else:
                    try:
                        link_preview = self.link_transcription_service.preview(
                            item.source_value
                        )
                    except DouyinParserError as exc:
                        reasons.append(exc.user_message)
                    else:
                        if not link_preview.parser_enabled:
                            if not link_preview.oneapi_fallback_available:
                                reasons.append(
                                    link_preview.parser_message
                                    or "本机解析不可用，且没有可确认费用的付费回退。"
                                )
                                item_cost_known = False
                            elif link_preview.oneapi_estimated_cost_cny is None:
                                item_cost_known = False
                            else:
                                item_cost += float(
                                    link_preview.oneapi_estimated_cost_cny
                                )
                                use_paid_fallback = True
            elif item.source_type not in {"share_link", "brief", "script"}:
                reasons.append("批次项来源不受支持。")
            total_cost += item_cost
            all_costs_known = all_costs_known and item_cost_known
            prepared_items.append({
                "run_id": item.run_id,
                "candidate_id": item.candidate_id,
                "source_type": item.source_type,
                "display_title": item.display_title or item.source_value,
                "candidate_role": item.candidate_role,
                "reasons": reasons,
                "estimated_cost_cny": round(item_cost, 2) if item_cost_known else None,
                "cost_known": item_cost_known,
                "use_paid_fallback": use_paid_fallback,
                "use_candidate_link_fallback": use_candidate_link_fallback,
                "copy_call_count": copy_call_count,
                "transcript_review_reserved": transcript_review_reserved,
                "manual_script_audit": audit_required,
            })
        cost_issues: list[str] = []
        if not all_costs_known:
            missing_costs: list[str] = []
            if not avatar_cost_known:
                missing_costs.append("数字人口播")
            if copy_capability is not None:
                copy_mode = str(copy_capability.get("mode") or "").casefold()
                if (
                    copy_mode != "sandbox"
                    and copy_capability.get("estimated_cost_cny") is None
                    and (
                        workspace_configuration is None
                        or workspace_configuration.copywriting_estimated_cost_cny is None
                    )
                ):
                    missing_costs.append("文案生成")
            suffix = "、".join(missing_costs) or "当前服务"
            cost_issues.append(
                f"{suffix}的单次费用尚未填写，暂不能启动。请先填写实际报价。"
            )
        if all_costs_known and max_total_cost_cny is not None and total_cost > max_total_cost_cny:
            cost_issues.append(
                f"预计总费用 {total_cost:.2f} 元超过本次上限 {max_total_cost_cny:.2f} 元。"
            )
        if total_cost > 0 and not paid_actions_confirmed:
            cost_issues.append("存在付费动作，必须确认预计费用后才能启动。")
        items = [
            {
                **item,
                "ready": not item["reasons"] and not cost_issues,
                "reasons": [*item["reasons"], *cost_issues],
            }
            for item in prepared_items
        ]
        return {
            "batch_id": batch_id,
            "ready_count": sum(1 for item in items if item["ready"]),
            "blocked_count": sum(1 for item in items if not item["ready"]),
            "items": items,
            "estimated_cost_cny": round(total_cost, 2) if all_costs_known else None,
            "cost_known": all_costs_known,
            "cost_blocked": bool(cost_issues),
            "cost_issues": cost_issues,
            "max_total_cost_cny": max_total_cost_cny,
            "paid_actions_confirmed": paid_actions_confirmed,
            "monthly_budget_used_cny": round(budget_used, 2),
            "platforms": platform_summary,
            "concurrency": concurrency,
        }

    def start_batch(
        self,
        batch_id: str,
        *,
        options: dict[str, Any],
        pipeline_service,
        idempotency_key: str = "",
        request_hash: str = "",
    ) -> ProductionBatch:
        batch = self.get_batch(batch_id)
        if batch is None:
            raise ValueError("生产批次不存在。")
        if not bool(options.get("rights_confirmed")) or not str(
            options.get("rights_holder") or ""
        ).strip():
            raise ValueError("请先完成一次基础设置并确认拥有创作所需授权。")
        claimed, existing, _ = self._claim_operation(
            operation="start",
            key=idempotency_key,
            request_hash=request_hash,
            resource_id=batch_id,
        )
        if not claimed:
            assert existing is not None
            return existing
        try:
            if batch.status in {
                ProductionBatchStatus.RUNNING,
                ProductionBatchStatus.AWAITING_REVIEW,
                ProductionBatchStatus.AWAITING_PUBLISH,
            }:
                self._complete_operation(
                    operation="start",
                    key=idempotency_key,
                    request_hash=request_hash,
                    resource_id=batch_id,
                    batch=batch,
                    runs=[],
                )
                return batch
            preflight = self.preflight_batch(batch_id, **options)
            if preflight["cost_blocked"]:
                raise ValueError("；".join(preflight["cost_issues"]))
            profile = self.get_profile(batch.profile_id)
            assert profile is not None
            by_run = {item["run_id"]: item for item in preflight["items"]}
            now = datetime.now().astimezone()
            updated_items: list[ProductionBatchItem] = []
            updated_runs = []
            execution_config = self._with_idempotency_record(
                {
                    **batch.execution_config,
                    "profile": profile.model_dump(mode="json"),
                    "rights_holder": str(options["rights_holder"]).strip(),
                    "rights_confirmed": bool(options["rights_confirmed"]),
                    "publish_platforms": list(options["publish_platforms"]),
                    "concurrency": int(options.get("concurrency") or 1),
                    "model_name": "large-v3-turbo",
                    "cost_known": preflight["cost_known"],
                    "max_total_cost_cny": options.get("max_total_cost_cny"),
                    "paid_actions_confirmed": bool(
                        options.get("paid_actions_confirmed")
                    ),
                    "automation_mode": (
                        "auto"
                        if str(options.get("automation_mode") or "").casefold()
                        == "auto"
                        else "manual"
                    ),
                    "auto_review_state": (
                        "pending"
                        if str(options.get("automation_mode") or "").casefold()
                        == "auto"
                        else "not_required"
                    ),
                    "auto_target_count": sum(
                        1
                        for item in batch.items
                        if item.candidate_role != "reserve"
                    ),
                    "auto_reserve_count": sum(
                        1
                        for item in batch.items
                        if item.candidate_role == "reserve"
                    ),
                    "auto_reserve_activated_count": 0,
                    "item_costs": {
                        item["run_id"]: {
                            "estimated_cost_cny": item["estimated_cost_cny"],
                            "known": item["cost_known"],
                        }
                        for item in preflight["items"]
                    },
                },
                operation="start",
                key=idempotency_key,
                request_hash=request_hash,
            )
            queued_count = 0
            for item in batch.items:
                result = by_run[item.run_id]
                run = self.repository.get_pipeline_run(item.run_id)
                if not result["ready"]:
                    updated_items.append(
                        item.model_copy(
                            update={
                                "status": ProductionBatchItemStatus.BLOCKED,
                                "blocked_reasons": result["reasons"],
                                "updated_at": now,
                            }
                        )
                    )
                    continue
                if run is None:
                    updated_items.append(
                        item.model_copy(
                            update={
                                "status": ProductionBatchItemStatus.BLOCKED,
                                "blocked_reasons": ["流水线记录不存在。"],
                                "updated_at": now,
                            }
                        )
                    )
                    continue
                item_profile = {
                    **execution_config["profile"],
                    **item.profile_overrides,
                }
                config = {
                    **run.config,
                    "source": "production_batch",
                    "batch_id": batch.batch_id,
                    "profile": item_profile,
                    "rights_holder": execution_config["rights_holder"],
                    "rights_confirmed": execution_config["rights_confirmed"],
                    "publish_platforms": execution_config["publish_platforms"],
                    "automation_mode": execution_config["automation_mode"],
                    "candidate_role": item.candidate_role,
                    "candidate_request": {
                        "rights_confirmed": execution_config["rights_confirmed"],
                        "rights_holder": execution_config["rights_holder"],
                        "model_name": execution_config["model_name"],
                        "target_length": 300,
                        "tone": "casual",
                        "target_audience": profile.target_audience,
                        "style_prompt": profile.script_style,
                        "variant_count": 2,
                    },
                    "use_paid_fallback": bool(
                        result.get("use_paid_fallback")
                    ),
                    "candidate_link_fallback": bool(
                        result.get("use_candidate_link_fallback")
                    ),
                }
                if (
                    execution_config["automation_mode"] == "auto"
                    and item.candidate_role == "reserve"
                ):
                    reserved = run.model_copy(
                        update={
                            "config": config,
                            "status": PipelineRunStatus.PAUSED,
                            "current_stage": None,
                            "updated_at": now,
                            "error_message": None,
                        }
                    )
                    reserved = pipeline_service._event(
                        reserved,
                        action="auto_reserve_prepared",
                        message="候补素材已完成预检；只有首批口播不可用时才会转写。",
                        details={"batch_id": batch.batch_id},
                    )
                    updated_runs.append(reserved)
                    updated_items.append(
                        item.model_copy(
                            update={
                                "status": ProductionBatchItemStatus.PLANNED,
                                "blocked_reasons": [],
                                "updated_at": now,
                            }
                        )
                    )
                    continue
                queued = run.model_copy(
                    update={
                        "config": config,
                        "status": PipelineRunStatus.PENDING,
                        "current_stage": None,
                        "updated_at": now,
                        "error_message": None,
                    }
                )
                queued = pipeline_service._event(
                    queued,
                    action="batch_queued",
                    message="批次预检通过，等待后台按队列执行。",
                    details={"batch_id": batch.batch_id},
                )
                updated_runs.append(queued)
                queued_count += 1
                updated_items.append(
                    item.model_copy(
                        update={
                            "status": ProductionBatchItemStatus.QUEUED,
                            "blocked_reasons": [],
                            "updated_at": now,
                        }
                    )
                )
            updated = batch.model_copy(
                update={
                    "items": updated_items,
                    "status": (
                        ProductionBatchStatus.RUNNING
                        if queued_count
                        else ProductionBatchStatus.FAILED
                    ),
                    "is_paused": False,
                    "execution_config": execution_config,
                    "estimated_cost_cny": float(
                        preflight["estimated_cost_cny"] or 0
                    ),
                    "monthly_budget_used_cny": preflight[
                        "monthly_budget_used_cny"
                    ],
                    "started_at": batch.started_at or now,
                    "updated_at": now,
                }
            )
            self._complete_operation(
                operation="start",
                key=idempotency_key,
                request_hash=request_hash,
                resource_id=batch_id,
                batch=updated,
                runs=updated_runs,
            )
            return updated
        except Exception as exc:
            self._fail_operation(
                operation="start",
                key=idempotency_key,
                request_hash=request_hash,
                resource_id=batch_id,
                error=exc,
            )
            raise

    def pause_batch(self, batch_id: str) -> ProductionBatch:
        batch = self._require_batch(batch_id)
        updated = batch.model_copy(update={"is_paused": True, "status": ProductionBatchStatus.PAUSED, "updated_at": datetime.now().astimezone()})
        self.repository.save_production_batch(updated)
        return updated

    def resume_batch(self, batch_id: str) -> ProductionBatch:
        batch = self._require_batch(batch_id)
        has_execution_config = any(
            not key.startswith("_") for key in batch.execution_config
        )
        if not has_execution_config:
            if all(
                item.status == ProductionBatchItemStatus.PLANNED
                for item in batch.items
            ):
                updated = batch.model_copy(
                    update={
                        "is_paused": False,
                        "status": ProductionBatchStatus.PLANNED,
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                self.repository.save_production_batch(updated)
                return updated
            raise ValueError("请先完成预检并启动批次。")
        updated = batch.model_copy(update={"is_paused": False, "status": ProductionBatchStatus.RUNNING, "updated_at": datetime.now().astimezone()})
        self.repository.save_production_batch(updated)
        return updated

    def retry_failed(self, batch_id: str, *, pipeline_service) -> ProductionBatch:
        batch = self._require_batch(batch_id)
        now = datetime.now().astimezone()
        items: list[ProductionBatchItem] = []
        for item in batch.items:
            run = self.repository.get_pipeline_run(item.run_id)
            if item.status != ProductionBatchItemStatus.FAILED or run is None:
                items.append(item)
                continue
            retry_stage, reason = self._safe_retry_stage(run)
            if retry_stage is None:
                items.append(
                    item.model_copy(
                        update={
                            "blocked_reasons": [reason or "当前阶段不能安全自动重试。"],
                            "status": ProductionBatchItemStatus.BLOCKED,
                            "updated_at": now,
                        }
                    )
                )
                continue
            retry_counts = dict(run.config.get("stage_retry_counts") or {})
            stage_key = retry_stage.value if retry_stage else "source"
            retry_number = int(retry_counts.get(stage_key) or 0)
            # Older workers accidentally reused the first avatar request's
            # idempotency key.  In that case the customer's first retry never
            # reached the provider.  Allow only that missing, already-counted
            # attempt to be resumed; a real retry task still keeps the limit.
            expected_retry_key = f"worker-avatar-{run.run_id}-retry-{retry_number}"
            missing_legacy_avatar_retry = (
                retry_stage == PipelineStage.AVATAR_GENERATION
                and retry_number > 0
                and not any(
                    getattr(task, "idempotency_key", "") == expected_retry_key
                    for task in self.repository.list_tasks()
                )
            )
            current_avatar_task = self.repository.get_task(run.avatar_task_id or "")
            provider_rejected_without_job = (
                retry_stage == PipelineStage.AVATAR_GENERATION
                and retry_number == 1
                and not missing_legacy_avatar_retry
                and current_avatar_task is not None
                and current_avatar_task.status == TaskStatus.FAILED
                and not getattr(current_avatar_task, "backend_job_id", None)
                and not getattr(current_avatar_task, "provider_job_id", None)
            )
            if (
                retry_number >= 1
                and not missing_legacy_avatar_retry
                and not provider_rejected_without_job
            ):
                items.append(
                    item.model_copy(
                        update={
                            "blocked_reasons": ["该阶段已安全重试过一次，请人工核对后重新创建任务。"],
                            "status": ProductionBatchItemStatus.BLOCKED,
                            "updated_at": now,
                        }
                    )
                )
                continue
            if retry_number == 0:
                retry_counts[stage_key] = 1
            elif provider_rejected_without_job:
                retry_counts[stage_key] = 2
            resume_existing_avatar = (
                run.current_stage == PipelineStage.VIDEO_EDITING
                and retry_stage == PipelineStage.AVATAR_GENERATION
                and bool(run.avatar_task_id)
            )
            retrying_transcription_upload = (
                retry_stage == PipelineStage.TRANSCRIPTION
            )
            retry_config = {
                **run.config,
                "stage_retry_counts": retry_counts,
            }
            if retrying_transcription_upload:
                transcription_task = self._transcription_task(run)
                retry_config.pop("transcription_task_id", None)
                retry_config.pop("outcome_unknown", None)
                retry_config.pop("recovery_blocked", None)
                retry_config.pop("recovery_reason", None)
                if transcription_task is not None:
                    retry_config["retry_source_transcription_task_id"] = (
                        transcription_task.task_id
                    )
            update = {
                "status": (
                    PipelineRunStatus.RUNNING
                    if resume_existing_avatar
                    else PipelineRunStatus.PENDING
                ),
                # Candidate media resolution is idempotent for this run.  Start
                # from that safe entry so the worker creates one new ASR task
                # instead of trying to resume an upload with no provider job.
                "current_stage": (
                    None if retrying_transcription_upload else retry_stage
                ),
                "error_message": None,
                "finished_at": None,
                "updated_at": now,
                "config": retry_config,
            }
            if (
                retry_stage == PipelineStage.AVATAR_GENERATION
                and not resume_existing_avatar
            ):
                update["avatar_task_id"] = None
            queued = run.model_copy(update=update)
            queued = pipeline_service._event(
                queued,
                action="batch_retry_queued",
                stage=retry_stage,
                message=(
                    "已确认重新提交转写；将复用已解析素材并只创建一个新的云端识别任务。"
                    if retrying_transcription_upload
                    else f"已从 {stage_key} 安全恢复，已成功的付费阶段不会重提。"
                ),
            )
            self.repository.save_pipeline_run(queued)
            items.append(item.model_copy(update={"status": ProductionBatchItemStatus.QUEUED, "error_message": None, "updated_at": now}))
        updated = batch.model_copy(update={"items": items, "is_paused": False, "status": ProductionBatchStatus.RUNNING, "updated_at": now})
        self.repository.save_production_batch(updated)
        return updated

    def _safe_retry_stage(self, run) -> tuple[PipelineStage | None, str]:
        if bool(run.config.get("publish_confirmed")):
            return None, "已经确认发布，不能从生产队列自动重提。"
        tasks = [self.repository.get_task(task_id) for task_id in run.publish_task_ids]
        if run.publish_task_ids:
            if any(
                task is not None
                and (
                    task.status == TaskStatus.OUTCOME_UNKNOWN
                    or getattr(task, "final_publish_started_at", None)
                    or task.outputs.get("final_publish_clicked") == "true"
                )
                for task in tasks
            ):
                return None, "发布结果未知或已点击最终发布，必须先人工核对平台后台。"
            return None, "发布任务已经创建，不能从生产队列自动重提；请在发布中心处理。"
        if run.current_stage == PipelineStage.VIDEO_EDITING and run.avatar_task_id:
            avatar_task = self.repository.get_task(run.avatar_task_id)
            if avatar_task is not None and avatar_task.status == TaskStatus.OUTCOME_UNKNOWN:
                return None, "数字人结果未知，不能自动重试以避免重复扣费。"
            return PipelineStage.AVATAR_GENERATION, ""
        if run.current_stage == PipelineStage.AVATAR_GENERATION:
            avatar_task = self.repository.get_task(run.avatar_task_id or "")
            if avatar_task is not None and avatar_task.status == TaskStatus.OUTCOME_UNKNOWN:
                return None, "数字人结果未知，不能自动重试以避免重复扣费。"
            return PipelineStage.AVATAR_GENERATION, ""
        if run.current_stage == PipelineStage.COPYWRITING:
            return None, "文案阶段失败需回到文案确认，不自动重复调用生成服务。"
        if run.current_stage == PipelineStage.TRANSCRIPTION:
            transcription_task = self._transcription_task(run)
            source_media_path = (
                Path(str(transcription_task.outputs.get("source_media_path") or ""))
                if transcription_task is not None
                else None
            )
            if (
                transcription_task is not None
                and transcription_task.status == TaskStatus.FAILED
                and not transcription_task.provider_job_id
                and transcription_task.provider_status == "failed"
                and source_media_path is not None
                and source_media_path.is_file()
                and bool(run.candidate_video_id)
            ):
                return PipelineStage.TRANSCRIPTION, ""
            return None, "转写任务可能已经提交或素材不可恢复，不能自动重提。"
        if run.current_stage == PipelineStage.MEDIA_RESOLUTION:
            return None, "媒体或转写阶段可能已经产生费用，不能自动重提。"
        if run.current_stage == PipelineStage.PUBLISHING:
            return None, "发布阶段不能由生产重试自动重提。"
        return None, "当前失败阶段没有可证明安全的恢复点。"

    def can_run(self, run_id: str) -> bool:
        """worker 在调用供应商前领取批次内唯一的并发槽位。"""
        run = self.repository.get_pipeline_run(run_id)
        if run is None:
            return False
        if run.status == PipelineRunStatus.PAUSED or bool(
            run.config.get("recovery_blocked")
        ):
            return False
        batch_id = str(run.config.get("batch_id") or "")
        batch = self.get_batch(batch_id)
        if batch is None or batch.is_paused or batch.status == ProductionBatchStatus.PAUSED:
            return False
        target = next((item for item in batch.items if item.run_id == run_id), None)
        if target is None or target.status not in {ProductionBatchItemStatus.QUEUED, ProductionBatchItemStatus.RUNNING}:
            return False
        first_queued = next(
            (item.run_id for item in batch.items if item.status == ProductionBatchItemStatus.QUEUED),
            None,
        )
        if target.status == ProductionBatchItemStatus.QUEUED and first_queued != run_id:
            return False
        active = sum(1 for item in batch.items if item.status == ProductionBatchItemStatus.RUNNING and item.run_id != run_id)
        if target.status == ProductionBatchItemStatus.QUEUED and active >= int(batch.execution_config.get("concurrency") or 1):
            return False
        if target.status == ProductionBatchItemStatus.QUEUED:
            self._save_item(batch, run_id, status=ProductionBatchItemStatus.RUNNING)
        return True

    def publish_preflight(
        self,
        batch_id: str,
        *,
        run_ids: list[str],
        targets: list[dict[str, Any]] | None,
        publish_platforms: list[str] | None,
        pipeline_service,
    ) -> dict[str, Any]:
        if self.publish_service is None:
            raise ValueError("发布服务未配置。")
        batch = self._require_batch(batch_id)
        selected = set(run_ids) if run_ids else {item.run_id for item in batch.items}
        requested_targets = self._normalize_publish_targets(targets, publish_platforms)
        known_run_ids = {item.run_id for item in batch.items}
        results: list[dict[str, Any]] = [
            {
                "run_id": run_id,
                "blocked": True,
                "issues": ["该任务不属于当前批次。"],
            }
            for run_id in sorted(selected - known_run_ids)
        ]
        for item in batch.items:
            if item.run_id not in selected:
                continue
            run = self.repository.get_pipeline_run(item.run_id)
            if run is None or run.current_stage != PipelineStage.PUBLISHING or run.status != PipelineRunStatus.PAUSED:
                results.append({"run_id": item.run_id, "blocked": True, "issues": ["该任务尚未生成可确认发布的成片。"]})
                continue
            if not bool(run.config.get("output_reviewed")):
                results.append({"run_id": item.run_id, "blocked": True, "issues": ["请先完成人工成片复核。"]})
                continue
            try:
                draft = self._approved_publish_draft(run)
            except ValueError as exc:
                results.append({"run_id": item.run_id, "blocked": True, "issues": [str(exc)]})
                continue
            if bool(run.config.get("publish_confirmed")) or run.publish_task_ids:
                results.append({"run_id": item.run_id, "blocked": True, "issues": ["该任务已经确认或创建发布任务，不能重复提交。"]})
                continue
            video_path = self._video_path(run)
            try:
                resolved, resolution_issues = self._resolve_publish_targets(requested_targets)
                publish_targets = pipeline_service.build_publish_targets(
                    title=draft["title"],
                    description=draft["description"],
                    tags=draft["tags"],
                    target_specs=resolved,
                )
                platform_results: list[dict[str, Any]] = []
                issues = list(resolution_issues)
                if not video_path.strip():
                    issues.append("找不到已生成的成片，不能创建发布任务。")
                elif not Path(video_path).is_file():
                    issues.append("成片文件不存在或已被移动，请重新生成或恢复文件后再发布。")
                for target, spec in zip(publish_targets, resolved):
                    if spec["mode"] == "manual":
                        platform_results.append(
                            {
                                "platform": target.platform.value,
                                "display_name": spec["display_name"],
                                "mode": "manual",
                                "enabled": True,
                                "manual_required": True,
                                "can_create_task": True,
                                "issue": None,
                                "account_status": None,
                            }
                        )
                        continue
                    check = self.publish_service.preflight(video_path=video_path, targets=[target])
                    platform_results.extend(check["platforms"])
                    issues.extend(check["issues"])
                blocked = bool(issues) or any(not value.get("can_create_task", False) for value in platform_results)
                results.append(
                    {
                        "run_id": item.run_id,
                        "blocked": blocked,
                        "issues": issues,
                        "platforms": platform_results,
                        "resolved_targets": resolved,
                        "video_path": video_path,
                    }
                )
            except (ValueError, RuntimeError) as exc:
                results.append({"run_id": item.run_id, "blocked": True, "issues": [str(exc)]})
        return {"batch_id": batch_id, "items": results, "blocked": any(item["blocked"] for item in results)}

    def confirm_publish(
        self,
        batch_id: str,
        *,
        run_ids: list[str],
        targets: list[dict[str, Any]] | None,
        publish_platforms: list[str] | None,
        pipeline_service,
        idempotency_key: str = "",
        request_hash: str = "",
    ) -> ProductionBatch:
        batch = self._require_batch(batch_id)
        claimed, existing, _ = self._claim_operation(
            operation="publish",
            key=idempotency_key,
            request_hash=request_hash,
            resource_id=batch_id,
        )
        if not claimed:
            assert existing is not None
            return existing
        try:
            preflight = self.publish_preflight(
                batch_id,
                run_ids=run_ids,
                targets=targets,
                publish_platforms=publish_platforms,
                pipeline_service=pipeline_service,
            )
            if preflight["blocked"] or not preflight["items"]:
                raise ValueError("发布预检未通过，请先处理受阻项。")
            selected = {item["run_id"] for item in preflight["items"]}
            resolved_by_run = {
                item["run_id"]: item.get("resolved_targets") or []
                for item in preflight["items"]
            }
            now = datetime.now().astimezone()
            items: list[ProductionBatchItem] = []
            updated_runs = []
            for item in batch.items:
                if item.run_id not in selected:
                    items.append(item)
                    continue
                run = self.repository.get_pipeline_run(item.run_id)
                if run is None:
                    items.append(item)
                    continue
                queued = run.model_copy(
                    update={
                        "status": PipelineRunStatus.PENDING,
                        "current_stage": PipelineStage.PUBLISHING,
                        "config": {
                            **run.config,
                            "publish_confirmed": True,
                            "publish_platforms": [
                                target["platform"]
                                for target in resolved_by_run[item.run_id]
                            ],
                            "publish_targets": resolved_by_run[item.run_id],
                        },
                        "updated_at": now,
                        "error_message": None,
                    }
                )
                queued = pipeline_service._event(
                    queued,
                    action="publish_confirmed",
                    stage=PipelineStage.PUBLISHING,
                    message="已确认发布，等待后台创建发布任务。",
                )
                updated_runs.append(queued)
                items.append(
                    item.model_copy(
                        update={
                            "status": ProductionBatchItemStatus.QUEUED,
                            "updated_at": now,
                        }
                    )
                )
            execution_config = self._with_idempotency_record(
                batch.execution_config,
                operation="publish",
                key=idempotency_key,
                request_hash=request_hash,
            )
            updated = batch.model_copy(
                update={
                    "items": items,
                    "is_paused": False,
                    "status": ProductionBatchStatus.RUNNING,
                    "execution_config": execution_config,
                    "updated_at": now,
                }
            )
            self._complete_operation(
                operation="publish",
                key=idempotency_key,
                request_hash=request_hash,
                resource_id=batch_id,
                batch=updated,
                runs=updated_runs,
            )
            return updated
        except Exception as exc:
            self._fail_operation(
                operation="publish",
                key=idempotency_key,
                request_hash=request_hash,
                resource_id=batch_id,
                error=exc,
            )
            raise

    @staticmethod
    def _normalize_publish_targets(
        targets: list[dict[str, Any]] | None,
        publish_platforms: list[str] | None,
    ) -> list[dict[str, Any]]:
        raw = targets or [
            {
                "platform": platform,
                "account_id": None,
                "use_manual_fallback": True,
            }
            for platform in (publish_platforms or ["douyin"])
        ]
        if not raw:
            raise ValueError("至少选择一个发布平台。")
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in raw:
            try:
                platform = PublishPlatform(str(item.get("platform") or ""))
            except ValueError as exc:
                raise ValueError(f"不支持的发布平台：{item.get('platform')}") from exc
            account_id = str(item.get("account_id") or "").strip()
            key = (platform.value, account_id)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                {
                    "platform": platform.value,
                    "account_id": account_id or None,
                    "use_manual_fallback": bool(item.get("use_manual_fallback", True)),
                    "auto_publish_authorized": bool(
                        item.get("auto_publish_authorized", False)
                    ),
                }
            )
        return normalized

    def _resolve_publish_targets(
        self,
        requested: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        available = {
            item["platform"]: item
            for item in self.publish_service.available_platforms()
        }
        resolved: list[dict[str, Any]] = []
        issues: list[str] = []
        for item in requested:
            platform = item["platform"]
            capability = available.get(platform)
            if capability is None:
                issues.append(f"未注册 {platform} 发布适配器。")
                continue
            account_id = item.get("account_id")
            allow_manual = bool(item.get("use_manual_fallback"))
            requires_account = bool(capability.get("requires_account"))
            mode = str(capability.get("mode") or "disabled")
            account_ready = False
            account_name = ""
            if account_id:
                try:
                    from src.services.publish_accounts import publish_account_manager

                    account = publish_account_manager.get(account_id, platform=platform)
                    account = publish_account_manager.status(account.account_id)
                    # A ready local-browser account is sufficient to prepare
                    # the official creator page.  Final platform submission is
                    # still guarded by the publisher when auto authorization
                    # is absent, so do not downgrade this safe prepare-only
                    # path to a manual package.
                    account_ready = account.status == "ready"
                    account_name = account.name
                except ValueError as exc:
                    if not allow_manual:
                        issues.append(str(exc))
            if requires_account and account_ready:
                resolved_mode = "real"
            elif mode in {"manual", "sandbox"}:
                resolved_mode = "manual"
            elif allow_manual and bool(capability.get("manual_fallback", False)):
                resolved_mode = "manual"
            elif requires_account:
                issues.append(
                    f"{capability['display_name']} 需要选择状态为 ready 的账号。"
                )
                continue
            elif bool(capability.get("enabled")):
                resolved_mode = "real"
            elif allow_manual and bool(capability.get("manual_fallback", False)):
                resolved_mode = "manual"
            else:
                issues.append(f"{capability['display_name']} 不可用且未允许人工发布兜底。")
                continue
            resolved.append(
                {
                    "platform": platform,
                    "account_id": account_id if resolved_mode == "real" else None,
                    "account_name": account_name if resolved_mode == "real" else "",
                    "mode": resolved_mode,
                    "display_name": capability.get("display_name", platform),
                    "provider_name": capability.get("provider_name", platform),
                    "use_manual_fallback": allow_manual,
                    "auto_publish_authorized": bool(
                        item.get("auto_publish_authorized", False)
                    )
                    if resolved_mode == "real"
                    else False,
                }
            )
        return resolved, issues

    def sync_batch(self, batch_id: str) -> ProductionBatch | None:
        batch = self.get_batch(batch_id)
        if batch is None:
            return None
        now = datetime.now().astimezone()
        items = [self._item_from_run(item, self.repository.get_pipeline_run(item.run_id), now) for item in batch.items]
        status = self._aggregate_status(items, paused=batch.is_paused)
        terminal = status in {ProductionBatchStatus.SUCCEEDED, ProductionBatchStatus.FAILED, ProductionBatchStatus.PARTIAL}
        updated = batch.model_copy(update={"items": items, "status": status, "updated_at": now, "finished_at": now if terminal else None})
        self.repository.save_production_batch(updated)
        return updated

    def _activate_auto_reserves(
        self,
        batch: ProductionBatch,
        *,
        pipeline_service,
        count: int,
    ) -> ProductionBatch:
        """Activate only the already preflighted reserve items that are needed."""
        now = datetime.now().astimezone()
        activated_ids: set[str] = set()
        for item in batch.items:
            if len(activated_ids) >= count:
                break
            if (
                item.candidate_role != "reserve"
                or item.status != ProductionBatchItemStatus.PLANNED
            ):
                continue
            run = self.repository.get_pipeline_run(item.run_id)
            if run is None:
                continue
            queued = run.model_copy(
                update={
                    "status": PipelineRunStatus.PENDING,
                    "current_stage": None,
                    "updated_at": now,
                    "error_message": None,
                }
            )
            queued = pipeline_service._event(
                queued,
                action="auto_reserve_activated",
                message="首批素材没有足够可用口播，已启用一条候补转写。",
                details={"batch_id": batch.batch_id},
            )
            self.repository.save_pipeline_run(queued)
            activated_ids.add(item.run_id)

        if not activated_ids:
            return batch
        config = dict(batch.execution_config or {})
        updated = batch.model_copy(
            update={
                "items": [
                    item.model_copy(
                        update={
                            "status": ProductionBatchItemStatus.QUEUED,
                            "updated_at": now,
                        }
                    )
                    if item.run_id in activated_ids
                    else item
                    for item in batch.items
                ],
                "status": ProductionBatchStatus.RUNNING,
                "execution_config": {
                    **config,
                    "auto_reserve_activated_count": int(
                        config.get("auto_reserve_activated_count") or 0
                    )
                    + len(activated_ids),
                    "auto_reserve_last_activated_at": now.isoformat(),
                },
                "finished_at": None,
                "updated_at": now,
            }
        )
        self.repository.save_production_batch(updated)
        return self.sync_batch(batch.batch_id) or updated

    def maybe_auto_review_batch(self, batch_id: str, *, pipeline_service) -> ProductionBatch | None:
        """When all selected sources are transcribed, choose one and continue it.

        The state is claimed before the model call.  A failed or interrupted
        claim is deliberately not replayed automatically because selection and
        rewrite can be billable.
        """
        batch = self.sync_batch(batch_id)
        if batch is None:
            return None
        config = dict(batch.execution_config or {})
        if str(config.get("automation_mode") or "") != "auto":
            return batch
        if str(config.get("auto_review_state") or "pending") != "pending":
            return batch

        ready: list[tuple[ProductionBatchItem, Any, TranscriptionTask, str]] = []
        still_processing = False
        for item in batch.items:
            if (
                item.candidate_role == "reserve"
                and item.status == ProductionBatchItemStatus.PLANNED
            ):
                continue
            run = self.repository.get_pipeline_run(item.run_id)
            if run is None:
                continue
            if (
                run.status == PipelineRunStatus.PAUSED
                and run.current_stage == PipelineStage.HUMAN_REVIEW
                and run.config.get("review_stage") == "transcript"
            ):
                task = self._transcription_task(run)
                text = (
                    "\n".join(
                        segment.text.strip()
                        for segment in task.segments
                        if segment.text.strip()
                    )
                    if task is not None
                    else ""
                )
                material_status, _ = (
                    pipeline_service.classify_spoken_material(task)
                    if task is not None
                    else ("visual_only", "")
                )
                if task is not None and text and material_status != "visual_only":
                    ready.append((item, run, task, text))
                continue
            if run.status not in {
                PipelineRunStatus.FAILED,
                PipelineRunStatus.PARTIAL,
                PipelineRunStatus.SUCCEEDED,
            }:
                still_processing = True
        if still_processing:
            return batch
        target_count = max(
            1,
            int(
                config.get("auto_target_count")
                or sum(1 for item in batch.items if item.candidate_role != "reserve")
                or 1
            ),
        )
        dormant_reserves = [
            item
            for item in batch.items
            if item.candidate_role == "reserve"
            and item.status == ProductionBatchItemStatus.PLANNED
        ]
        if len(ready) < target_count and dormant_reserves:
            return self._activate_auto_reserves(
                batch,
                pipeline_service=pipeline_service,
                count=min(target_count - len(ready), len(dormant_reserves)),
            )
        if not ready:
            failed = batch.model_copy(
                update={
                    "execution_config": {
                        **config,
                        "auto_review_state": "failed",
                        "auto_review_error": "本次没有识别到可用口播，素材已保留为画面参考，未生成文案。",
                    },
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self.repository.save_production_batch(failed)
            return self.sync_batch(batch_id)

        claimed_at = datetime.now().astimezone()
        claimed = batch.model_copy(
            update={
                "execution_config": {
                    **config,
                    "auto_review_state": "running",
                    "auto_review_started_at": claimed_at.isoformat(),
                },
                "updated_at": claimed_at,
            }
        )
        self.repository.save_production_batch(claimed)
        try:
            if self.copywriting_service is None:
                raise RuntimeError("AI 文案服务未配置，无法自动选稿。")
            profile = self.get_profile(batch.profile_id)
            decision = self.copywriting_service.select_best_spoken_script(
                candidates=[
                    {
                        "id": run.run_id,
                        "platform": str(run.config.get("candidate_platform") or ""),
                        "title": item.display_title or run.keyword,
                        "text": text,
                    }
                    for item, run, _, text in ready
                ],
                target_audience=profile.target_audience if profile is not None else "",
                style_prompt=profile.script_style if profile is not None else "",
            )
            winner_id = decision["winner_id"]
            reason = decision["reason"]
            winner = next(
                (entry for entry in ready if entry[1].run_id == winner_id),
                None,
            )
            if winner is None:
                raise RuntimeError("AI 选中的文案不在当前批次中。")
            _, winner_run, _, winner_text = winner
            rewritten = pipeline_service.review_transcript(
                run_id=winner_run.run_id,
                reviewer="AI 自动审核",
                approved_text=winner_text,
                note=f"从 {len(ready)} 条可用转写中择优：{reason}",
            )
            if (
                rewritten.status != PipelineRunStatus.PAUSED
                or rewritten.current_stage != PipelineStage.HUMAN_REVIEW
                or rewritten.config.get("review_stage") != "script"
                or not rewritten.copywriting_task_id
            ):
                raise RuntimeError(rewritten.error_message or "AI 降重改写未完成。")
            copy_task = self.repository.get_task(rewritten.copywriting_task_id)
            approved_script = (
                str(copy_task.result_text or "").strip()
                if isinstance(copy_task, CopywritingTask)
                else ""
            )
            if not approved_script:
                raise RuntimeError("AI 降重改写没有返回可用口播稿。")
            pipeline_service.review_candidate_script(
                run_id=winner_run.run_id,
                approved=True,
                reviewer="AI 自动审核",
                note=f"已从本批转写中择优并完成降重：{reason}",
                approved_text=approved_script,
            )
            finished_at = datetime.now().astimezone()
            for _, run, _, _ in ready:
                if run.run_id == winner_id:
                    continue
                skipped = run.model_copy(
                    update={
                        "status": PipelineRunStatus.SUCCEEDED,
                        "finished_at": finished_at,
                        "updated_at": finished_at,
                        "error_message": None,
                        "config": {
                            **run.config,
                            "auto_selection_status": "not_selected",
                            "auto_selection_reason": reason,
                        },
                    }
                )
                skipped = pipeline_service._event(
                    skipped,
                    action="auto_candidate_not_selected",
                    stage=PipelineStage.HUMAN_REVIEW,
                    message="该转写已参与 AI 对比，本次未进入数字人制作。",
                    details={"winner_run_id": winner_id, "reason": reason},
                )
                self.repository.save_pipeline_run(skipped)
            for item in batch.items:
                if (
                    item.candidate_role != "reserve"
                    or item.status != ProductionBatchItemStatus.PLANNED
                ):
                    continue
                run = self.repository.get_pipeline_run(item.run_id)
                if run is None:
                    continue
                unused = run.model_copy(
                    update={
                        "status": PipelineRunStatus.SUCCEEDED,
                        "finished_at": finished_at,
                        "updated_at": finished_at,
                        "error_message": None,
                        "config": {
                            **run.config,
                            "auto_selection_status": "reserve_not_needed",
                            "auto_selection_reason": "首批已有足够可用口播，未调用该候补。",
                        },
                    }
                )
                unused = pipeline_service._event(
                    unused,
                    action="auto_reserve_not_needed",
                    message="首批已有足够可用口播，该候补未调用转写。",
                    details={"winner_run_id": winner_id},
                )
                self.repository.save_pipeline_run(unused)
            current = self.get_batch(batch_id) or claimed
            completed = current.model_copy(
                update={
                    "execution_config": {
                        **dict(current.execution_config or {}),
                        "auto_review_state": "completed",
                        "auto_selected_run_id": winner_id,
                        "auto_selection_reason": reason,
                        "auto_review_finished_at": finished_at.isoformat(),
                    },
                    "updated_at": finished_at,
                }
            )
            self.repository.save_production_batch(completed)
        except Exception as exc:
            current = self.get_batch(batch_id) or claimed
            failed = current.model_copy(
                update={
                    "execution_config": {
                        **dict(current.execution_config or {}),
                        "auto_review_state": "failed",
                        "auto_review_error": str(exc),
                    },
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self.repository.save_production_batch(failed)
        return self.sync_batch(batch_id)

    def workspace(self, batch_id: str) -> dict[str, Any]:
        """聚合客户工作台所需状态，不在读取接口触发外部服务。"""
        batch = self.sync_batch(batch_id)
        if batch is None:
            raise ValueError("生产批次不存在。")
        now = datetime.now().astimezone()
        profile = self.get_profile(batch.profile_id)
        item_costs = dict(batch.execution_config.get("item_costs") or {})
        workspace_items: list[dict[str, Any]] = []
        for item in batch.items:
            run = self.repository.get_pipeline_run(item.run_id)
            video_path = self._video_path(run) if run is not None else ""
            transcript_task = self._transcription_task(run)
            copy_task = (
                self.repository.get_task(run.copywriting_task_id or "")
                if run is not None and run.copywriting_task_id
                else None
            )
            transcript_required = item.source_type in {"candidate", "share_link"}
            transcript_reviewed = bool(run and run.config.get("transcript_reviewed"))
            script_reviewed = bool(run and run.config.get("script_reviewed"))
            output_reviewed = bool(run and run.config.get("output_reviewed"))
            transcript_text = (
                str(run.config.get("approved_transcript_text") or "")
                if run is not None
                else ""
            )
            if not transcript_text and transcript_task is not None:
                transcript_text = "\n".join(
                    segment.text.strip()
                    for segment in transcript_task.segments
                    if segment.text.strip()
                )
            script_text = ""
            if isinstance(copy_task, CopywritingTask):
                script_text = str(copy_task.result_text or "")
            if not script_text and item.source_type == "script":
                script_text = item.source_value
            approved_script_text = (
                str(run.config.get("approved_script_text") or script_text)
                if run is not None
                else script_text
            )
            next_action, allowed_actions = self._workspace_actions(
                item,
                run,
                batch_paused=batch.is_paused,
            )
            retry_allowed = "retry" in allowed_actions
            publish_tasks = [
                self.repository.get_task(task_id)
                for task_id in (run.publish_task_ids if run is not None else [])
            ]
            publish_tasks = [
                task for task in publish_tasks if isinstance(task, PublishTask)
            ]
            cost = item_costs.get(item.run_id) or {}
            workspace_items.append(
                {
                    **item.model_dump(mode="json"),
                    "stage": self._workspace_stage(item, run),
                    "next_action": next_action,
                    "allowed_actions": allowed_actions,
                    "retry_allowed": retry_allowed,
                    "recovery": (
                        {
                            "kind": "transcription_upload_retry",
                            "estimated_cost_cny": transcript_task.estimated_cost_cny,
                            "currency": "CNY",
                            "attempts_used": int(
                                (run.config.get("stage_retry_counts") or {}).get(
                                    PipelineStage.TRANSCRIPTION.value
                                )
                                or 0
                            )
                            if run is not None
                            else 0,
                            "max_attempts": 1,
                        }
                        if retry_allowed
                        and run is not None
                        and self._safe_retry_stage(run)[0]
                        == PipelineStage.TRANSCRIPTION
                        and transcript_task is not None
                        else None
                    ),
                    # 浏览器不能直接播放服务端所在电脑的 Windows 路径；统一
                    # 返回受控的媒体接口，接口会验证文件存在后再提供 MP4。
                    "result_media_url": (
                        f"/api/v1/pipelines/{run.run_id}/media"
                        if run is not None and video_path and Path(video_path).is_file()
                        else None
                    ),
                    "processing": self._workspace_processing(run, now=now),
                    "reviews": {
                        "transcript": {
                            "required": transcript_required,
                            "reviewed": transcript_reviewed or not transcript_required,
                            "draft_text": transcript_text,
                            "low_confidence_count": (
                                sum(
                                    1
                                    for segment in transcript_task.segments
                                    if segment.confidence is not None
                                    and segment.confidence < 0.7
                                )
                                if transcript_task is not None
                                else 0
                            ),
                            "ai_corrected_count": (
                                sum(
                                    1
                                    for segment in transcript_task.segments
                                    if segment.quality_status == "llm_rewritten"
                                )
                                if transcript_task is not None
                                else 0
                            ),
                            "auto_reviewed": bool(
                                transcript_task and transcript_task.auto_reviewed
                            ),
                            "auto_review_error": (
                                transcript_task.auto_review_error
                                if transcript_task is not None
                                else None
                            ),
                            "uncertain_segment_count": (
                                transcript_task.uncertain_segment_count
                                if transcript_task is not None
                                else 0
                            ),
                            "low_confidence_segments": (
                                [
                                    {
                                        "start": segment.start,
                                        "end": segment.end,
                                        "text": segment.text,
                                        "confidence": segment.confidence,
                                        "quality_note": segment.quality_note,
                                    }
                                    for segment in transcript_task.segments
                                    if segment.needs_review and not segment.reviewed
                                ]
                                if transcript_task is not None
                                else []
                            ),
                        },
                        "script": {
                            "required": True,
                            "reviewed": script_reviewed,
                            "draft_text": approved_script_text,
                            "attention_terms": (
                                list(copy_task.attention_terms)
                                if isinstance(copy_task, CopywritingTask)
                                else []
                            ),
                            "compliance_status": (
                                copy_task.compliance_status
                                if isinstance(copy_task, CopywritingTask)
                                else None
                            ),
                            "compliance_notes": (
                                list(copy_task.compliance_notes)
                                if isinstance(copy_task, CopywritingTask)
                                else []
                            ),
                            "ai_audit": (
                                dict(audit)
                                if isinstance(
                                    audit := (run.config.get("script_ai_audit") if run is not None else None),
                                    dict,
                                )
                                else None
                            ),
                            "creative_plan": (
                                dict(plan)
                                if isinstance(
                                    plan := (run.config.get("creative_plan") if run is not None else None),
                                    dict,
                                )
                                and plan
                                else None
                            ),
                        },
                        "output": {
                            "required": True,
                            "reviewed": output_reviewed,
                        },
                    },
                    "cost": {
                        "estimated_cost_cny": cost.get("estimated_cost_cny"),
                        "known": bool(cost.get("known", False)),
                        "currency": "CNY",
                    },
                    "publish": {
                        "confirmed": bool(run and run.config.get("publish_confirmed")),
                        "status": self._publish_status(publish_tasks),
                        "stage": "；".join(
                            task.stage
                            for task in publish_tasks
                            if task.stage
                        ),
                        "action_required": next(
                            (
                                task.action_required
                                for task in publish_tasks
                                if task.action_required
                            ),
                            None,
                        ),
                        "prepared_task_ids": [
                            task.task_id
                            for task in publish_tasks
                            if task.provider_name == "douyin_local_browser"
                            and task.publish_status == PublishStatus.MANUAL_READY
                            and task.stage.startswith("已在账号")
                        ],
                        "targets": list(run.config.get("publish_targets") or [])
                        if run is not None
                        else [],
                        "task_ids": [task.task_id for task in publish_tasks],
                        "draft": self._workspace_publish_draft(
                            run=run,
                            publish_tasks=publish_tasks,
                            profile_tags=list(profile.tags) if profile is not None else [],
                            approved_script_text=approved_script_text,
                        ),
                    },
                }
            )
        active = next(
            (
                item
                for item in workspace_items
                if item["next_action"] not in {"view_result", "wait"}
            ),
            workspace_items[0] if workspace_items else None,
        )
        if active is None and workspace_items:
            active = workspace_items[0]
        all_publish_tasks = [
            self.repository.get_task(task_id)
            for item in batch.items
            for task_id in (
                self.repository.get_pipeline_run(item.run_id).publish_task_ids
                if self.repository.get_pipeline_run(item.run_id) is not None
                else []
            )
        ]
        all_publish_tasks = [
            task for task in all_publish_tasks if isinstance(task, PublishTask)
        ]
        batch_payload = batch.model_dump(mode="json")
        batch_payload["execution_config"] = {
            key: value
            for key, value in batch_payload.get("execution_config", {}).items()
            if not key.startswith("_")
        }
        workspace_configuration = self.get_workspace_configuration()
        bundled_compute = bool(
            workspace_configuration is not None
            and workspace_configuration.bundled_compute
        )
        cost_known = bundled_compute or bool(batch.execution_config.get("cost_known"))
        estimated_total_cny = (
            0.0
            if bundled_compute
            else (
                batch.estimated_cost_cny
                if bool(batch.execution_config.get("cost_known"))
                else None
            )
        )
        publish_confirmed = any(
            bool(
                (
                    self.repository.get_pipeline_run(item.run_id).config
                    if self.repository.get_pipeline_run(item.run_id) is not None
                    else {}
                ).get("publish_confirmed")
            )
            for item in batch.items
        )
        publish_status = self._publish_status(all_publish_tasks)
        workspace_status = (
            "outcome_unknown"
            if any(
                self._run_has_unknown_outcome(
                    self.repository.get_pipeline_run(item.run_id)
                )
                for item in batch.items
            )
            else batch.status.value
        )
        return {
            "batch": batch_payload,
            "profile": profile.model_dump(mode="json") if profile is not None else None,
            "status": workspace_status,
            "progress": self.batch_progress(batch),
            "current_run_id": active["run_id"] if active else None,
            "current_stage": active["stage"] if active else "source",
            "next_action": active["next_action"] if active else "completed",
            "allowed_actions": active["allowed_actions"] if active else [],
            "retry_allowed": bool(active and active["retry_allowed"]),
            "items": workspace_items,
            "automation": {
                "mode": str(
                    batch.execution_config.get("automation_mode") or "manual"
                ),
                "review_state": str(
                    batch.execution_config.get("auto_review_state")
                    or "not_required"
                ),
                "selected_run_id": batch.execution_config.get(
                    "auto_selected_run_id"
                ),
                "selection_reason": batch.execution_config.get(
                    "auto_selection_reason"
                ),
                "error": batch.execution_config.get("auto_review_error"),
                "target_count": int(
                    batch.execution_config.get("auto_target_count") or 0
                ),
                "reserve_count": int(
                    batch.execution_config.get("auto_reserve_count") or 0
                ),
                "reserve_activated_count": int(
                    batch.execution_config.get("auto_reserve_activated_count") or 0
                ),
            },
            "cost": {
                # estimated_cost_cny is the public workspace field used by the
                # customer client; keep estimated_total_cny as a compatibility
                # alias for early consumers of the aggregate endpoint.
                "estimated_cost_cny": estimated_total_cny,
                "estimated_total_cny": estimated_total_cny,
                "currency": "CNY",
                "known": cost_known,
                "blocked": not cost_known,
                "issues": (
                    ["存在预计费用未知的付费动作，不能启动。"]
                    if not cost_known
                    else []
                ),
                "max_total_cost_cny": batch.execution_config.get(
                    "max_total_cost_cny"
                ),
                "paid_actions_confirmed": bool(
                    batch.execution_config.get("paid_actions_confirmed")
                ),
            },
            "publish": {
                "confirmed": publish_confirmed,
                "status": publish_status,
                "task_ids": [task.task_id for task in all_publish_tasks],
                "targets": [
                    target
                    for item in workspace_items
                    for target in item["publish"]["targets"]
                ],
                "message": {
                    "manual_ready": (
                        "抖音官方发布页已准备，等待你最终确认"
                        if any(
                            task.provider_name == "douyin_local_browser"
                            and task.stage.startswith("已在账号")
                            for task in all_publish_tasks
                        )
                        else "手动发布包已生成"
                    ),
                    "outcome_unknown": "发布结果待人工核对",
                    "succeeded": "发布已由真实任务确认成功",
                    "failed": "发布任务失败",
                    "pending": "发布任务处理中",
                    "not_started": "尚未确认发布",
                }.get(publish_status, publish_status),
            },
        }

    def _run_has_unknown_outcome(self, run) -> bool:
        if run is None:
            return False
        if bool(run.config.get("outcome_unknown")):
            return True
        task_ids = [
            task_id
            for task_id in [run.avatar_task_id, *run.publish_task_ids]
            if task_id
        ]
        return any(
            task is not None and task.status == TaskStatus.OUTCOME_UNKNOWN
            for task in (self.repository.get_task(task_id) for task_id in task_ids)
        )

    def _transcription_task(self, run) -> TranscriptionTask | None:
        if run is None:
            return None
        task_id = str(run.config.get("transcription_task_id") or "")
        if not task_id:
            step = next(
                (
                    stage
                    for stage in run.stages
                    if stage.stage == PipelineStage.TRANSCRIPTION
                ),
                None,
            )
            task_id = step.task_id if step and step.task_id else ""
        task = self.repository.get_task(task_id) if task_id else None
        return task if isinstance(task, TranscriptionTask) else None

    def _workspace_processing(self, run, *, now: datetime) -> dict[str, Any] | None:
        """返回客户可理解的长耗时状态，且不触发任何供应商调用。"""
        if run is None or run.current_stage != PipelineStage.AVATAR_GENERATION:
            return None
        task = self.repository.get_task(run.avatar_task_id or "")
        if not isinstance(task, AvatarTask) or task.status not in {
            TaskStatus.QUEUED,
            TaskStatus.SUBMITTED,
            TaskStatus.RUNNING,
        }:
            return None

        stage_started_at = next(
            (
                stage.started_at
                for stage in reversed(run.stages)
                if stage.stage == PipelineStage.AVATAR_GENERATION
                and stage.started_at is not None
            ),
            None,
        )
        started_at = stage_started_at or task.created_at
        elapsed_seconds = max(0, int((now - started_at).total_seconds()))
        return {
            "stage": "avatar",
            "started_at": started_at.isoformat(),
            "elapsed_seconds": elapsed_seconds,
            "expected_seconds": self.AVATAR_DELAY_WARNING_SECONDS,
            "delayed": elapsed_seconds >= self.AVATAR_DELAY_WARNING_SECONDS,
            # 仅用于界面避免出现“暂停后会取消云端任务”的误导；不暴露供应商细节。
            "provider_job_received": bool(task.backend_job_id or task.provider_job_id),
        }

    def _workspace_actions(
        self,
        item: ProductionBatchItem,
        run,
        *,
        batch_paused: bool = False,
    ) -> tuple[str, list[str]]:
        if run is not None and run.status == PipelineRunStatus.PAUSED and (
            bool(run.config.get("outcome_unknown"))
            or bool(run.config.get("recovery_blocked"))
        ):
            return "manual_review", []
        if batch_paused:
            return "resume", ["resume"]
        if run is None:
            return "preflight", ["preflight"]
        if self._publish_tasks_succeeded(run):
            return "view_result", ["view_result"]
        if item.status == ProductionBatchItemStatus.PLANNED:
            return "preflight", ["preflight", "start"]
        if run.status == PipelineRunStatus.PAUSED and run.current_stage == PipelineStage.HUMAN_REVIEW:
            if run.config.get("review_stage") == "transcript":
                return "review_transcript", ["review_transcript", "pause"]
            return "review_script", ["review_script", "pause"]
        if run.status == PipelineRunStatus.PAUSED and run.current_stage == PipelineStage.PUBLISHING:
            if not bool(run.config.get("output_reviewed")):
                return "review_output", ["review_output", "pause"]
            if not bool(run.config.get("publish_draft_approved")) and not run.publish_task_ids:
                return "review_publish_draft", ["review_publish_draft", "pause"]
            if not bool(run.config.get("publish_confirmed")) and not run.publish_task_ids:
                return "publish", ["publish", "pause"]
            return "wait", ["wait"]
        if run.status in {PipelineRunStatus.FAILED, PipelineRunStatus.PARTIAL}:
            retry_stage, _ = self._safe_retry_stage(run)
            return ("retry", ["retry"]) if retry_stage is not None else ("wait", [])
        if run.status == PipelineRunStatus.SUCCEEDED:
            return "view_result", ["view_result"]
        if item.status == ProductionBatchItemStatus.BLOCKED:
            return "preflight", ["preflight"]
        return "wait", ["wait", "pause"]

    def _workspace_stage(self, item: ProductionBatchItem, run) -> str:
        if run is None or item.status == ProductionBatchItemStatus.PLANNED:
            return "source"
        if self._publish_tasks_succeeded(run):
            return "completed"
        if run.current_stage == PipelineStage.HUMAN_REVIEW:
            return "transcript" if run.config.get("review_stage") == "transcript" else "script"
        if run.current_stage == PipelineStage.TRANSCRIPTION:
            return "transcript"
        if run.current_stage == PipelineStage.COPYWRITING:
            return "script"
        if run.current_stage == PipelineStage.AVATAR_GENERATION:
            return "avatar"
        if run.current_stage == PipelineStage.VIDEO_EDITING:
            return "editing"
        if run.current_stage == PipelineStage.PUBLISHING:
            return "publish" if run.config.get("output_reviewed") else "output"
        if run.status == PipelineRunStatus.SUCCEEDED:
            return "completed"
        return "source"

    def _publish_tasks_succeeded(self, run) -> bool:
        task_ids = list(run.publish_task_ids) if run is not None else []
        if not task_ids:
            return False
        return all(
            isinstance(task, PublishTask) and task.status == TaskStatus.SUCCEEDED
            for task in (self.repository.get_task(task_id) for task_id in task_ids)
        )

    @staticmethod
    def _publish_status(tasks: list[PublishTask]) -> str:
        if not tasks:
            return "not_started"
        if any(task.status == TaskStatus.OUTCOME_UNKNOWN for task in tasks):
            return "outcome_unknown"
        if all(task.status == TaskStatus.SUCCEEDED for task in tasks):
            return "succeeded"
        if any(task.status == TaskStatus.FAILED for task in tasks):
            return "failed"
        if any(task.publish_status.value == "manual_ready" for task in tasks):
            return "manual_ready"
        return "pending"

    def batch_progress(self, batch: ProductionBatch) -> dict[str, int]:
        if all(item.status == ProductionBatchItemStatus.PLANNED for item in batch.items):
            return {
                "total": len(batch.items),
                "pending": len(batch.items),
                "running": 0,
                "paused": 0,
                "succeeded": 0,
                "failed": 0,
            }
        counts = {status.value: 0 for status in ProductionBatchItemStatus}
        for item in batch.items:
            counts[item.status.value] += 1
        # 兼容既有页面和 API 客户端的 pipeline 状态统计字段。
        return {
            "total": len(batch.items),
            **counts,
            "pending": counts[ProductionBatchItemStatus.PLANNED.value] + counts[ProductionBatchItemStatus.QUEUED.value],
            "running": counts[ProductionBatchItemStatus.RUNNING.value],
            "paused": counts[ProductionBatchItemStatus.AWAITING_REVIEW.value] + counts[ProductionBatchItemStatus.AWAITING_PUBLISH.value] + counts[ProductionBatchItemStatus.READY_TO_PUBLISH.value],
            "succeeded": counts[ProductionBatchItemStatus.SUCCEEDED.value],
            "failed": counts[ProductionBatchItemStatus.FAILED.value],
        }

    def _save_item(self, batch: ProductionBatch, run_id: str, **changes: Any) -> ProductionBatch:
        now = datetime.now().astimezone()
        items = [item.model_copy(update={**changes, "updated_at": now}) if item.run_id == run_id else item for item in batch.items]
        updated = batch.model_copy(update={"items": items, "updated_at": now})
        self.repository.save_production_batch(updated)
        return updated

    def _item_from_run(self, item: ProductionBatchItem, run, now: datetime) -> ProductionBatchItem:
        if run is None:
            return item.model_copy(update={"status": ProductionBatchItemStatus.BLOCKED, "blocked_reasons": ["流水线记录不存在。"], "updated_at": now})
        stage = run.current_stage
        publish_tasks = [
            self.repository.get_task(task_id)
            for task_id in run.publish_task_ids
        ]
        publish_completed = self._publish_tasks_succeeded(run)
        waiting_for_manual_publish = any(
            isinstance(task, PublishTask)
            and task.publish_status.value == "manual_ready"
            for task in publish_tasks
        )
        material_status = str(
            run.config.get("spoken_material_status")
            or item.spoken_material_status
            or "pending"
        )
        material_message = str(
            run.config.get("spoken_material_message")
            or item.spoken_material_message
            or ""
        )
        if (
            material_status == "visual_only"
            and str(run.config.get("automation_mode") or "") == "auto"
        ):
            status = ProductionBatchItemStatus.SKIPPED
        elif run.config.get("auto_selection_status") in {
            "not_selected",
            "reserve_not_needed",
        }:
            status = ProductionBatchItemStatus.SKIPPED
        elif publish_completed:
            status = ProductionBatchItemStatus.SUCCEEDED
        elif waiting_for_manual_publish:
            # Preparing a local browser page intentionally pauses the task so
            # the operator, not the system, performs the irreversible final
            # click.  Do not let the pipeline's partial terminal state make
            # this look like a failed production batch.
            status = ProductionBatchItemStatus.AWAITING_PUBLISH
        elif run.status in {PipelineRunStatus.FAILED, PipelineRunStatus.PARTIAL}:
            status = ProductionBatchItemStatus.FAILED
        elif run.status == PipelineRunStatus.SUCCEEDED:
            status = ProductionBatchItemStatus.SUCCEEDED
        elif run.status == PipelineRunStatus.PAUSED and (
            bool(run.config.get("outcome_unknown"))
            or bool(run.config.get("recovery_blocked"))
        ):
            status = ProductionBatchItemStatus.BLOCKED
        elif run.status == PipelineRunStatus.PAUSED and stage == PipelineStage.HUMAN_REVIEW:
            status = ProductionBatchItemStatus.AWAITING_REVIEW
        elif run.status == PipelineRunStatus.PAUSED and stage == PipelineStage.PUBLISHING:
            status = (
                ProductionBatchItemStatus.AWAITING_PUBLISH
                if bool(run.config.get("publish_confirmed")) or run.publish_task_ids
                else (
                    ProductionBatchItemStatus.READY_TO_PUBLISH
                    if bool(run.config.get("output_reviewed"))
                    else ProductionBatchItemStatus.AWAITING_PUBLISH
                )
            )
        elif run.status == PipelineRunStatus.RUNNING:
            status = ProductionBatchItemStatus.RUNNING
        elif item.status in {ProductionBatchItemStatus.BLOCKED, ProductionBatchItemStatus.PLANNED}:
            status = item.status
        else:
            status = ProductionBatchItemStatus.QUEUED
        video_path = item.video_path
        if not video_path:
            for step in reversed(run.stages):
                if step.stage == PipelineStage.VIDEO_EDITING:
                    video_path = str(step.outputs.get("video_path") or step.outputs.get("result_path") or "") or None
                    break
        return item.model_copy(
            update={
                "status": status,
                "current_stage": stage,
                "error_message": run.error_message,
                "video_path": video_path,
                "spoken_material_status": material_status,
                "spoken_material_message": material_message,
                "updated_at": now,
            }
        )

    def _video_path(self, run) -> str:
        task = self.repository.get_task(run.edit_task_id or "") if run.edit_task_id else None
        result_path = str(getattr(task, "result_path", "") or "")
        if result_path:
            return result_path
        for stage in reversed(run.stages):
            if stage.stage == PipelineStage.VIDEO_EDITING:
                return str(stage.outputs.get("video_path") or stage.outputs.get("result_path") or "")
        return ""

    @staticmethod
    def _approved_publish_draft(run) -> dict[str, Any]:
        raw = run.config.get("publish_draft")
        if not bool(run.config.get("publish_draft_approved")) or not isinstance(raw, dict):
            raise ValueError("请先确认标题、描述和标签，再准备发布。")
        draft = validated_publish_draft(
            title=str(raw.get("title") or ""),
            description=str(raw.get("description") or ""),
            tags=list(raw.get("tags") or []),
        )
        if publish_draft_fingerprint(draft) != str(run.config.get("publish_draft_fingerprint") or ""):
            raise ValueError("发布信息已变化，请重新确认标题、描述和标签。")
        return draft

    @staticmethod
    def _workspace_publish_draft(
        *,
        run,
        publish_tasks: list[PublishTask],
        profile_tags: list[str],
        approved_script_text: str,
    ) -> dict[str, Any] | None:
        if run is None:
            return None
        raw = run.config.get("publish_draft")
        if isinstance(raw, dict):
            return {
                **raw,
                "approved": bool(run.config.get("publish_draft_approved")),
                "warnings": [],
            }
        if publish_tasks:
            target = publish_tasks[0].target
            return suggested_publish_draft(
                approved_script=(
                    approved_script_text.strip()
                    or target.description.strip()
                    or target.title.strip()
                ),
                creative_plan=(
                    dict(plan)
                    if isinstance(plan := run.config.get("creative_plan"), dict)
                    else None
                ),
                profile_tags=profile_tags,
            )
        if not approved_script_text.strip():
            return {
                "title": "",
                "description": "",
                "tags": [],
                "approved": False,
                "warnings": [],
            }
        return suggested_publish_draft(
            approved_script=approved_script_text,
            creative_plan=(
                dict(plan)
                if isinstance(plan := run.config.get("creative_plan"), dict)
                else None
            ),
            profile_tags=profile_tags,
        )

    @staticmethod
    def _aggregate_status(items: list[ProductionBatchItem], *, paused: bool) -> ProductionBatchStatus:
        statuses = [item.status for item in items]
        if paused:
            return ProductionBatchStatus.PAUSED
        if statuses and all(
            status == ProductionBatchItemStatus.PLANNED for status in statuses
        ):
            return ProductionBatchStatus.PLANNED
        if any(status in {ProductionBatchItemStatus.RUNNING, ProductionBatchItemStatus.QUEUED} for status in statuses):
            return ProductionBatchStatus.RUNNING
        if any(status == ProductionBatchItemStatus.AWAITING_REVIEW for status in statuses):
            return ProductionBatchStatus.AWAITING_REVIEW
        if any(status in {ProductionBatchItemStatus.AWAITING_PUBLISH, ProductionBatchItemStatus.READY_TO_PUBLISH} for status in statuses):
            return ProductionBatchStatus.AWAITING_PUBLISH
        if any(status == ProductionBatchItemStatus.PLANNED for status in statuses):
            return ProductionBatchStatus.RUNNING
        completed_statuses = {
            ProductionBatchItemStatus.SUCCEEDED,
            ProductionBatchItemStatus.SKIPPED,
        }
        if (
            statuses
            and any(status == ProductionBatchItemStatus.SUCCEEDED for status in statuses)
            and all(status in completed_statuses for status in statuses)
        ):
            return ProductionBatchStatus.SUCCEEDED
        if any(status == ProductionBatchItemStatus.SUCCEEDED for status in statuses):
            return ProductionBatchStatus.PARTIAL
        return ProductionBatchStatus.FAILED

    def _require_batch(self, batch_id: str) -> ProductionBatch:
        batch = self.get_batch(batch_id)
        if batch is None:
            raise ValueError("生产批次不存在。")
        return batch
