"""可复用 IP 配方与可恢复的批量生产控制服务。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.contracts import TaskRepository
from src.models import (
    PipelineRunStatus,
    PipelineStage,
    ProductionBatch,
    ProductionBatchItem,
    ProductionBatchItemStatus,
    ProductionBatchStatus,
    ProductionProfile,
    PublishPlatform,
)


class ProductionService:
    """保存 IP 配方、预检批次并为 worker 提供可恢复的调度状态。"""

    def __init__(
        self,
        repository: TaskRepository,
        storage_directory: str | Path,
        *,
        media_resolution_service=None,
        avatar_service=None,
        template_service=None,
        publish_service=None,
    ) -> None:
        self.repository = repository
        self.storage_directory = Path(storage_directory)
        self.storage_directory.mkdir(parents=True, exist_ok=True)
        self._profiles_path = self.storage_directory / "profiles.json"
        self._legacy_batches_path = self.storage_directory / "batches.json"
        self.media_resolution_service = media_resolution_service
        self.avatar_service = avatar_service
        self.template_service = template_service
        self.publish_service = publish_service
        self._import_legacy_batches_once()

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

    def list_profiles(self) -> list[ProductionProfile]:
        return sorted(
            self._load(self._profiles_path, ProductionProfile),
            key=lambda item: item.updated_at,
            reverse=True,
        )

    def get_profile(self, profile_id: str) -> ProductionProfile | None:
        return next((item for item in self.list_profiles() if item.profile_id == profile_id), None)

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
            edit_template_id=edit_template_id or None,
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
        candidate_ids: list[str],
        pipeline_service,
    ) -> ProductionBatch:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise ValueError("IP 配方不存在。")
        normalized_ids = list(dict.fromkeys(item.strip() for item in candidate_ids if item.strip()))
        if not normalized_ids:
            raise ValueError("请至少选择一条候选。")
        if len(normalized_ids) > 50:
            raise ValueError("单个批次最多包含 50 条候选。")
        getter = getattr(self.repository, "get_candidate", None)
        if getter is None:
            raise RuntimeError("当前仓库不支持候选生产计划。")

        batch = ProductionBatch(name=name.strip(), profile_id=profile.profile_id, profile_name=profile.name)
        items: list[ProductionBatchItem] = []
        for candidate_id in normalized_ids:
            candidate = getter(candidate_id)
            if candidate is None:
                raise ValueError(f"候选不存在：{candidate_id}")
            run = pipeline_service.create_run(
                keyword=candidate.title[:200],
                config={
                    "source": "production_batch_plan",
                    "batch_id": batch.batch_id,
                    "profile": profile.model_dump(mode="json"),
                    "candidate_id": candidate.video_id,
                    "next_action": "完成批次预检并启动后，后台将按队列执行。",
                },
            )
            run = run.model_copy(update={"candidate_video_id": candidate.video_id, "updated_at": datetime.now().astimezone()})
            run = pipeline_service._event(
                run,
                action="batch_plan_created",
                message="已加入批量生产计划，尚未调用任何生成或发布供应商。",
                details={"batch_id": batch.batch_id, "profile_id": profile.profile_id},
            )
            self.repository.save_pipeline_run(run)
            items.append(ProductionBatchItem(candidate_id=candidate.video_id, run_id=run.run_id))
        batch = batch.model_copy(update={"items": items})
        self.repository.save_production_batch(batch)
        return batch

    def preflight_batch(
        self,
        batch_id: str,
        *,
        rights_holder: str,
        rights_confirmed: bool,
        publish_platforms: list[str],
        concurrency: int = 1,
    ) -> dict[str, Any]:
        batch = self.get_batch(batch_id)
        if batch is None:
            raise ValueError("生产批次不存在。")
        if not 1 <= concurrency <= 5:
            raise ValueError("并发数只支持 1 到 5。")
        profile = self.get_profile(batch.profile_id)
        shared: list[str] = []
        platforms: list[PublishPlatform] = []
        if not rights_confirmed or not rights_holder.strip():
            shared.append("必须确认拥有媒体、文案、肖像和声音处理授权，并填写授权主体。")
        if profile is None:
            shared.append("IP 配方不存在。")
        else:
            if not all([profile.avatar_id, profile.voice_id, profile.edit_template_id]):
                shared.append("IP 配方必须绑定数字人形象、音色和剪辑模板。")
            if self.avatar_service is None:
                shared.append("数字人服务未配置。")
            elif profile.avatar_id and profile.voice_id:
                assets = {item.asset_id: item for item in self.avatar_service.list_assets()}
                for asset_id, label in ((profile.avatar_id, "数字人形象"), (profile.voice_id, "音色")):
                    asset = assets.get(asset_id)
                    if asset is None:
                        shared.append(f"IP 配方绑定的{label}不存在。")
                    elif not asset.authorized:
                        shared.append(f"IP 配方绑定的{label}未标记为已授权。")
            if self.template_service is None:
                shared.append("剪辑模板服务未配置。")
            elif profile.edit_template_id and self.template_service.get_template(profile.edit_template_id) is None:
                shared.append("IP 配方绑定的剪辑模板不存在。")
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

        items: list[dict[str, Any]] = []
        total_cost = 0.0
        budget_used = 0.0
        for item in batch.items:
            reasons = list(shared)
            candidate = getattr(self.repository, "get_candidate", lambda _: None)(item.candidate_id)
            if candidate is None:
                reasons.append("候选不存在或已被删除。")
            elif self.media_resolution_service is None:
                reasons.append("媒体解析服务未配置。")
            else:
                preview = self.media_resolution_service.preview(candidate)
                total_cost += float(preview.estimated_cost_cny or 0)
                budget_used = max(budget_used, float(preview.monthly_budget_used_cny or 0))
                if not preview.resolvable:
                    reasons.append(preview.block_reason or "候选不能进入媒体解析。")
            items.append({"run_id": item.run_id, "candidate_id": item.candidate_id, "ready": not reasons, "reasons": reasons})
        return {
            "batch_id": batch_id,
            "ready_count": sum(1 for item in items if item["ready"]),
            "blocked_count": sum(1 for item in items if not item["ready"]),
            "items": items,
            "estimated_cost_cny": round(total_cost, 2),
            "monthly_budget_used_cny": round(budget_used, 2),
            "platforms": platform_summary,
            "concurrency": concurrency,
        }

    def start_batch(self, batch_id: str, *, options: dict[str, Any], pipeline_service) -> ProductionBatch:
        batch = self.get_batch(batch_id)
        if batch is None:
            raise ValueError("生产批次不存在。")
        if batch.status in {
            ProductionBatchStatus.RUNNING,
            ProductionBatchStatus.AWAITING_REVIEW,
            ProductionBatchStatus.AWAITING_PUBLISH,
        }:
            return batch
        preflight = self.preflight_batch(batch_id, **options)
        profile = self.get_profile(batch.profile_id)
        assert profile is not None
        by_run = {item["run_id"]: item for item in preflight["items"]}
        now = datetime.now().astimezone()
        updated_items: list[ProductionBatchItem] = []
        execution_config = {
            "profile": profile.model_dump(mode="json"),
            "rights_holder": str(options["rights_holder"]).strip(),
            "rights_confirmed": True,
            "publish_platforms": list(options["publish_platforms"]),
            "concurrency": int(options.get("concurrency") or 1),
            "model_name": "large-v3-turbo",
        }
        for item in batch.items:
            result = by_run[item.run_id]
            run = self.repository.get_pipeline_run(item.run_id)
            if not result["ready"]:
                updated_items.append(item.model_copy(update={"status": ProductionBatchItemStatus.BLOCKED, "blocked_reasons": result["reasons"], "updated_at": now}))
                continue
            if run is None:
                updated_items.append(item.model_copy(update={"status": ProductionBatchItemStatus.BLOCKED, "blocked_reasons": ["流水线记录不存在。"], "updated_at": now}))
                continue
            config = {
                **run.config,
                "source": "production_batch",
                "workflow": "production_batch_candidate",
                "batch_id": batch.batch_id,
                "profile": execution_config["profile"],
                "rights_holder": execution_config["rights_holder"],
                "rights_confirmed": True,
                "publish_platforms": execution_config["publish_platforms"],
                "candidate_request": {
                    "rights_confirmed": True,
                    "rights_holder": execution_config["rights_holder"],
                    "model_name": execution_config["model_name"],
                    "target_length": 300,
                    "tone": "casual",
                    "target_audience": profile.target_audience,
                    "style_prompt": profile.script_style,
                    "variant_count": 2,
                },
            }
            run = run.model_copy(update={"config": config, "status": PipelineRunStatus.PENDING, "current_stage": None, "updated_at": now, "error_message": None})
            run = pipeline_service._event(run, action="batch_queued", message="批次预检通过，等待后台按队列执行。", details={"batch_id": batch.batch_id})
            self.repository.save_pipeline_run(run)
            updated_items.append(item.model_copy(update={"status": ProductionBatchItemStatus.QUEUED, "blocked_reasons": [], "updated_at": now}))
        updated = batch.model_copy(update={
            "items": updated_items,
            "status": ProductionBatchStatus.RUNNING if preflight["ready_count"] else ProductionBatchStatus.FAILED,
            "is_paused": False,
            "execution_config": execution_config,
            "estimated_cost_cny": preflight["estimated_cost_cny"],
            "monthly_budget_used_cny": preflight["monthly_budget_used_cny"],
            "started_at": batch.started_at or now,
            "updated_at": now,
        })
        self.repository.save_production_batch(updated)
        return updated

    def pause_batch(self, batch_id: str) -> ProductionBatch:
        batch = self._require_batch(batch_id)
        updated = batch.model_copy(update={"is_paused": True, "status": ProductionBatchStatus.PAUSED, "updated_at": datetime.now().astimezone()})
        self.repository.save_production_batch(updated)
        return updated

    def resume_batch(self, batch_id: str) -> ProductionBatch:
        batch = self._require_batch(batch_id)
        if not batch.execution_config:
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
            request = dict(run.config.get("candidate_request") or {})
            if not request:
                items.append(item.model_copy(update={"blocked_reasons": ["缺少可重试执行参数。"], "status": ProductionBatchItemStatus.BLOCKED, "updated_at": now}))
                continue
            queued = run.model_copy(update={"status": PipelineRunStatus.PENDING, "current_stage": None, "error_message": None, "updated_at": now})
            queued = pipeline_service._event(queued, action="batch_retry_queued", message="已加入失败重试队列。")
            self.repository.save_pipeline_run(queued)
            items.append(item.model_copy(update={"status": ProductionBatchItemStatus.QUEUED, "error_message": None, "updated_at": now}))
        updated = batch.model_copy(update={"items": items, "is_paused": False, "status": ProductionBatchStatus.RUNNING, "updated_at": now})
        self.repository.save_production_batch(updated)
        return updated

    def can_run(self, run_id: str) -> bool:
        """worker 在调用供应商前领取批次内唯一的并发槽位。"""
        run = self.repository.get_pipeline_run(run_id)
        if run is None:
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

    def publish_preflight(self, batch_id: str, *, run_ids: list[str], publish_platforms: list[str], pipeline_service) -> dict[str, Any]:
        if self.publish_service is None:
            raise ValueError("发布服务未配置。")
        batch = self._require_batch(batch_id)
        selected = set(run_ids) if run_ids else {item.run_id for item in batch.items}
        results: list[dict[str, Any]] = []
        for item in batch.items:
            if item.run_id not in selected:
                continue
            run = self.repository.get_pipeline_run(item.run_id)
            if run is None or run.current_stage != PipelineStage.PUBLISHING or run.status != PipelineRunStatus.PAUSED:
                results.append({"run_id": item.run_id, "blocked": True, "issues": ["该任务尚未生成可确认发布的成片。"]})
                continue
            video_path = self._video_path(run)
            try:
                targets = pipeline_service.build_publish_targets(
                    title=run.keyword[:100],
                    description=str(run.config.get("approved_script_text") or "")[:200],
                    tags=[run.keyword],
                    platforms=[PublishPlatform(value) for value in publish_platforms],
                )
                result = self.publish_service.preflight(video_path=video_path, targets=targets)
                results.append({"run_id": item.run_id, **result})
            except (ValueError, RuntimeError) as exc:
                results.append({"run_id": item.run_id, "blocked": True, "issues": [str(exc)]})
        return {"batch_id": batch_id, "items": results, "blocked": any(item["blocked"] for item in results)}

    def confirm_publish(
        self,
        batch_id: str,
        *,
        run_ids: list[str],
        publish_platforms: list[str],
        pipeline_service,
    ) -> ProductionBatch:
        preflight = self.publish_preflight(
            batch_id,
            run_ids=run_ids,
            publish_platforms=publish_platforms,
            pipeline_service=pipeline_service,
        )
        if preflight["blocked"]:
            raise ValueError("发布预检未通过，请先处理受阻项。")
        batch = self._require_batch(batch_id)
        selected = {item["run_id"] for item in preflight["items"]}
        now = datetime.now().astimezone()
        items: list[ProductionBatchItem] = []
        for item in batch.items:
            if item.run_id not in selected:
                items.append(item)
                continue
            run = self.repository.get_pipeline_run(item.run_id)
            if run is None:
                items.append(item)
                continue
            queued = run.model_copy(update={
                "status": PipelineRunStatus.PENDING,
                "current_stage": PipelineStage.PUBLISHING,
                "config": {**run.config, "publish_confirmed": True, "publish_platforms": publish_platforms},
                "updated_at": now,
                "error_message": None,
            })
            queued = pipeline_service._event(queued, action="publish_confirmed", stage=PipelineStage.PUBLISHING, message="已确认发布，等待后台创建发布任务。")
            self.repository.save_pipeline_run(queued)
            items.append(item.model_copy(update={"status": ProductionBatchItemStatus.QUEUED, "updated_at": now}))
        updated = batch.model_copy(update={"items": items, "is_paused": False, "status": ProductionBatchStatus.RUNNING, "updated_at": now})
        self.repository.save_production_batch(updated)
        return updated

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
            "paused": counts[ProductionBatchItemStatus.AWAITING_REVIEW.value] + counts[ProductionBatchItemStatus.AWAITING_PUBLISH.value],
            "succeeded": counts[ProductionBatchItemStatus.SUCCEEDED.value],
            "failed": counts[ProductionBatchItemStatus.FAILED.value],
        }

    def _save_item(self, batch: ProductionBatch, run_id: str, **changes: Any) -> ProductionBatch:
        now = datetime.now().astimezone()
        items = [item.model_copy(update={**changes, "updated_at": now}) if item.run_id == run_id else item for item in batch.items]
        updated = batch.model_copy(update={"items": items, "updated_at": now})
        self.repository.save_production_batch(updated)
        return updated

    @staticmethod
    def _item_from_run(item: ProductionBatchItem, run, now: datetime) -> ProductionBatchItem:
        if run is None:
            return item.model_copy(update={"status": ProductionBatchItemStatus.BLOCKED, "blocked_reasons": ["流水线记录不存在。"], "updated_at": now})
        stage = run.current_stage
        if run.status in {PipelineRunStatus.FAILED, PipelineRunStatus.PARTIAL}:
            status = ProductionBatchItemStatus.FAILED
        elif run.status == PipelineRunStatus.SUCCEEDED:
            status = ProductionBatchItemStatus.SUCCEEDED
        elif run.status == PipelineRunStatus.PAUSED and stage == PipelineStage.HUMAN_REVIEW:
            status = ProductionBatchItemStatus.AWAITING_REVIEW
        elif run.status == PipelineRunStatus.PAUSED and stage == PipelineStage.PUBLISHING:
            status = ProductionBatchItemStatus.AWAITING_PUBLISH
        elif run.status == PipelineRunStatus.RUNNING:
            status = ProductionBatchItemStatus.RUNNING
        elif item.status in {ProductionBatchItemStatus.BLOCKED, ProductionBatchItemStatus.PLANNED} and not str(run.config.get("workflow") or ""):
            status = item.status
        else:
            status = ProductionBatchItemStatus.QUEUED
        video_path = item.video_path
        if not video_path:
            for step in reversed(run.stages):
                if step.stage == PipelineStage.VIDEO_EDITING:
                    video_path = str(step.outputs.get("video_path") or step.outputs.get("result_path") or "") or None
                    break
        return item.model_copy(update={"status": status, "current_stage": stage, "error_message": run.error_message, "video_path": video_path, "updated_at": now})

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
    def _aggregate_status(items: list[ProductionBatchItem], *, paused: bool) -> ProductionBatchStatus:
        statuses = [item.status for item in items]
        if paused:
            return ProductionBatchStatus.PAUSED
        if any(status in {ProductionBatchItemStatus.RUNNING, ProductionBatchItemStatus.QUEUED} for status in statuses):
            return ProductionBatchStatus.RUNNING
        if any(status == ProductionBatchItemStatus.AWAITING_REVIEW for status in statuses):
            return ProductionBatchStatus.AWAITING_REVIEW
        if any(status == ProductionBatchItemStatus.AWAITING_PUBLISH for status in statuses):
            return ProductionBatchStatus.AWAITING_PUBLISH
        if statuses and all(status == ProductionBatchItemStatus.SUCCEEDED for status in statuses):
            return ProductionBatchStatus.SUCCEEDED
        if any(status == ProductionBatchItemStatus.SUCCEEDED for status in statuses):
            return ProductionBatchStatus.PARTIAL
        return ProductionBatchStatus.FAILED

    def _require_batch(self, batch_id: str) -> ProductionBatch:
        batch = self.get_batch(batch_id)
        if batch is None:
            raise ValueError("生产批次不存在。")
        return batch
