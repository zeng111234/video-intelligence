"""发布服务。

协调 Publisher 适配器与 TaskRepository，
支持单平台发布、多平台批量发布。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from collections.abc import Mapping

from src.contracts import Publisher, TaskRepository
from src.models import (
    PublishPlatform,
    PublishStatus,
    PublishTask,
    PublishTarget,
    TaskStatus,
)


class PublishService:
    def __init__(
        self,
        repository: TaskRepository,
        publishers: Mapping[str, Publisher],
    ) -> None:
        self.repository = repository
        self.publishers = publishers

    def available_platforms(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for pub in self.publishers.values():
            cap = pub.capabilities()
            result.append(
                {
                    "platform": pub.platform(),
                    "enabled": bool(cap.get("enabled", False)),
                    "display_name": str(cap.get("display_name", pub.platform())),
                    "mode": str(cap.get("mode", "disabled")),
                    "provider_name": str(cap.get("provider_name", pub.platform())),
                    "requires_account": bool(cap.get("requires_account", False)),
                    "setup_required": bool(cap.get("setup_required", False)),
                    "manual_only": bool(cap.get("manual_only", False)),
                    "manual_fallback": bool(cap.get("manual_fallback", True)),
                    "supports_scheduled": bool(cap.get("supports_scheduled", False)),
                    "supports_tags": bool(cap.get("supports_tags", False)),
                    "supports_cover": bool(cap.get("supports_cover", False)),
                    "missing_configuration": list(cap.get("missing_configuration", [])),
                }
            )
        return result

    def preflight(
        self,
        *,
        video_path: str,
        targets: list[PublishTarget],
    ) -> dict[str, Any]:
        """检查发布请求是否能创建任务，不执行发布动作。"""
        path = Path(video_path)
        suffix = path.suffix.lower()
        issues: list[str] = []
        if not video_path.strip():
            issues.append("请先选择或上传成片文件。")
        elif not path.is_file():
            issues.append("成片文件不存在或已被移动，请重新生成或恢复文件。")
        elif suffix and suffix not in {".mp4", ".mov", ".m4v"}:
            issues.append("建议使用 mp4、mov 或 m4v 成片文件。")

        platform_results: list[dict[str, Any]] = []
        for target in targets:
            platform_key = target.platform.value
            publisher = self.publishers.get(platform_key)
            if publisher is None:
                platform_results.append(
                    {
                        "platform": platform_key,
                        "can_create_task": False,
                        "mode": "missing",
                        "issue": f"未注册 {platform_key} 发布适配器。",
                    }
                )
                continue

            cap = publisher.capabilities()
            mode = str(cap.get("mode", "disabled"))
            enabled = bool(cap.get("enabled", False))
            manual_mode = mode in {"manual", "sandbox"}
            missing_config = list(cap.get("missing_configuration", []))
            can_create_task = enabled or manual_mode or bool(cap.get("manual_fallback"))
            issue = None
            if mode == "local_browser" and not target.account_id:
                can_create_task = False
                issue = "请先选择已扫码连接的发布账号。"
            if not can_create_task:
                issue = issue or "未配置官方发布权限，也没有启用人工兜底。"

            platform_results.append(
                {
                    "platform": platform_key,
                    "display_name": str(cap.get("display_name", platform_key)),
                    "provider_name": str(cap.get("provider_name", platform_key)),
                    "mode": mode,
                    "enabled": enabled,
                    "manual_required": manual_mode or not enabled,
                    "manual_only": bool(cap.get("manual_only", False)),
                    "can_create_task": can_create_task,
                    "issue": issue,
                    "issue_code": None,
                    "account_status": None,
                    "missing_configuration": missing_config,
                    "manual_steps": self._manual_steps(target.platform),
                }
            )

        blocked = bool(issues) or any(
            not item["can_create_task"] for item in platform_results
        )
        return {
            "blocked": blocked,
            "video_path": video_path,
            "issues": issues,
            "platforms": platform_results,
        }

    def publish(
        self,
        *,
        video_path: str,
        target: PublishTarget,
        source_pipeline_run_id: str | None = None,
        batch_id: str | None = None,
        on_progress: Callable[[PublishTask], None] | None = None,
    ) -> PublishTask:
        """发布视频到指定平台。"""
        video = Path(video_path)

        platform_key = target.platform.value
        publisher = self.publishers.get(platform_key)
        if publisher is None:
            raise ValueError(f"未找到 {target.platform.value} 的发布适配器。")

        publisher_mode = str(publisher.capabilities().get("mode", "disabled"))
        is_manual_mode = publisher_mode in {"sandbox", "manual"}

        # 人工发布包模式允许先记录本机路径；官方发布适配器必须检查文件存在。
        if not is_manual_mode and not video.exists():
            raise ValueError("视频文件不存在。")

        now = datetime.now().astimezone()
        task = PublishTask(
            task_id=f"pub-{uuid4().hex[:10]}",
            title=f"发布 · {target.title[:20]}",
            status=TaskStatus.RUNNING,
            progress=10,
            created_at=now,
            updated_at=now,
            video_path=str(video),
            batch_id=batch_id,
            target=target,
            publish_status=PublishStatus.UPLOADING,
            provider_name=str(publisher.capabilities().get("provider_name", "")),
            source_pipeline_run_id=source_pipeline_run_id,
            stage="正在上传",
            is_mock=bool(publisher.capabilities().get("mode") == "sandbox"),
        )
        self._save(task, on_progress)
        try:
            result = publisher.publish(str(video), target)
            # 更新初始任务为结果状态，而非保存新任务
            updated = task.model_copy(
                update={
                    "status": result.status,
                    "progress": result.progress,
                    "publish_status": result.publish_status,
                    "platform_video_id": result.platform_video_id,
                    "platform_url": result.platform_url,
                    "stage": result.stage,
                    "action_required": result.action_required,
                    "final_publish_started_at": result.final_publish_started_at,
                    "outcome_evidence": result.outcome_evidence,
                    "updated_at": datetime.now().astimezone(),
                    "is_mock": result.is_mock,
                    "outputs": {**task.outputs, **result.outputs},
                }
            )
            self._save(updated, on_progress)
            return updated
        except Exception as exc:
            failed = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "publish_status": PublishStatus.FAILED,
                    "stage": "发布失败",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": str(exc),
                }
            )
            self._save(failed, on_progress)
            return failed

    def multi_platform_publish(
        self,
        *,
        video_path: str,
        targets: list[PublishTarget],
        source_pipeline_run_id: str | None = None,
        batch_id: str | None = None,
    ) -> list[PublishTask]:
        """多平台批量发布。"""
        batch_id = batch_id or f"batch-{uuid4().hex[:10]}"
        tasks: list[PublishTask] = []
        for target in targets:
            task = self.publish(
                video_path=video_path,
                target=target,
                source_pipeline_run_id=source_pipeline_run_id,
                batch_id=batch_id,
            )
            tasks.append(task)
        return tasks

    def create_batch(
        self,
        *,
        video_path: str,
        targets: list[PublishTarget],
        source_pipeline_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one-at-a-time work; browser automation never runs in the API call."""
        batch_id = f"batch-{uuid4().hex[:10]}"
        tasks = [
            self.enqueue(
                video_path=video_path,
                target=target,
                source_pipeline_run_id=source_pipeline_run_id,
                batch_id=batch_id,
            )
            for target in targets
        ]
        return self.batch_summary(batch_id, tasks)

    def enqueue(
        self,
        *,
        video_path: str,
        target: PublishTarget,
        source_pipeline_run_id: str | None = None,
        batch_id: str | None = None,
    ) -> PublishTask:
        publisher = self.publishers.get(target.platform.value)
        if publisher is None:
            raise ValueError(f"未找到 {target.platform.value} 的发布适配器。")
        now = datetime.now().astimezone()
        task = PublishTask(
            task_id=f"pub-{uuid4().hex[:10]}",
            title=f"发布 · {target.title[:20]}",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            video_path=video_path,
            batch_id=batch_id,
            target=target,
            publish_status=PublishStatus.PENDING,
            provider_name=str(publisher.capabilities().get("provider_name", "")),
            source_pipeline_run_id=source_pipeline_run_id,
            stage="等待本机发布队列",
            is_mock=False,
        )
        self._save(task, None)
        return task

    def execute_queued_task(self, task_id: str) -> PublishTask | None:
        """Execute exactly one queued task.  Called only by PublishWorker."""
        task = self.get_task(task_id)
        if task is None or task.status != TaskStatus.QUEUED:
            return task
        publisher = self.publishers.get(task.target.platform.value)
        if publisher is None:
            failed = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "publish_status": PublishStatus.FAILED,
                    "stage": "发布适配器不存在",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": f"未找到 {task.target.platform.value} 的发布适配器。",
                }
            )
            self._save(failed, None)
            return failed
        running = task.model_copy(
            update={
                "status": TaskStatus.RUNNING,
                "progress": 10,
                "publish_status": PublishStatus.UPLOADING,
                "stage": "正在打开官方创作者窗口",
                "updated_at": datetime.now().astimezone(),
            }
        )
        self._save(running, None)
        try:
            result = publisher.publish(task.video_path, task.target)
            paused = result.status == TaskStatus.PAUSED or result.publish_status in {
                PublishStatus.MANUAL_READY,
                PublishStatus.ACTION_REQUIRED,
            }
            updated = running.model_copy(
                update={
                    "status": TaskStatus.PAUSED if paused else result.status,
                    "progress": result.progress,
                    "publish_status": result.publish_status,
                    "platform_video_id": result.platform_video_id,
                    "platform_url": result.platform_url,
                    "stage": result.stage,
                    "action_required": result.action_required,
                    "final_publish_started_at": result.final_publish_started_at,
                    "outcome_evidence": result.outcome_evidence,
                    "updated_at": datetime.now().astimezone(),
                    "is_mock": result.is_mock,
                    "outputs": {**running.outputs, **result.outputs},
                }
            )
            self._save(updated, None)
            return updated
        except Exception as exc:
            failed = running.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "publish_status": PublishStatus.FAILED,
                    "stage": "发布失败",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": str(exc),
                }
            )
            self._save(failed, None)
            return failed

    def list_batches(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[PublishTask]] = {}
        for task in self.list_tasks():
            grouped.setdefault(task.batch_id or task.task_id, []).append(task)
        return [
            self.batch_summary(batch_id, tasks)
            for batch_id, tasks in sorted(
                grouped.items(),
                key=lambda item: max(task.created_at for task in item[1]),
                reverse=True,
            )
        ]

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        tasks = [
            task
            for task in self.list_tasks()
            if (task.batch_id or task.task_id) == batch_id
        ]
        if not tasks:
            return None
        return self.batch_summary(batch_id, tasks)

    def batch_summary(self, batch_id: str, tasks: list[PublishTask]) -> dict[str, Any]:
        succeeded = sum(1 for task in tasks if task.status == TaskStatus.SUCCEEDED)
        failed = sum(1 for task in tasks if task.status == TaskStatus.FAILED)
        unknown = sum(1 for task in tasks if task.status == TaskStatus.OUTCOME_UNKNOWN)
        pending = len(tasks) - succeeded - failed - unknown
        if failed and succeeded:
            status = "partial"
        elif failed:
            status = "failed"
        elif unknown:
            status = "outcome_unknown"
        elif pending:
            status = "manual_ready"
        else:
            status = "succeeded"
        return {
            "batch_id": batch_id,
            "status": status,
            "total": len(tasks),
            "succeeded": succeeded,
            "failed": failed,
            "outcome_unknown": unknown,
            "pending": pending,
            "created_at": min(task.created_at for task in tasks).isoformat(),
            "updated_at": max(task.updated_at for task in tasks).isoformat(),
            "tasks": tasks,
        }

    def record_manual_result(
        self,
        *,
        task_id: str,
        succeeded: bool | None,
        platform_url: str | None = None,
        platform_video_id: str | None = None,
        note: str = "",
    ) -> PublishTask:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("发布任务不存在。")
        now = datetime.now().astimezone()
        if succeeded is True:
            updated = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "publish_status": PublishStatus.SUCCEEDED,
                    "platform_url": platform_url or task.platform_url,
                    "platform_video_id": platform_video_id or task.platform_video_id,
                    "stage": "人工确认已发布",
                    "updated_at": now,
                    "error_message": None,
                    "is_mock": False,
                    "outputs": {
                        **task.outputs,
                        "manual_confirmation_note": note,
                    },
                }
            )
        elif succeeded is False:
            updated = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "progress": task.progress,
                    "publish_status": PublishStatus.FAILED,
                    "stage": "人工确认发布失败",
                    "updated_at": now,
                    "error_message": note or "人工确认发布失败。",
                    "outputs": {
                        **task.outputs,
                        "manual_confirmation_note": note,
                    },
                }
            )
        else:
            updated = task.model_copy(
                update={
                    "status": TaskStatus.OUTCOME_UNKNOWN,
                    "publish_status": PublishStatus.OUTCOME_UNKNOWN,
                    "stage": "发布结果待确认",
                    "updated_at": now,
                    "error_message": note or "发布结果暂无法确认。",
                    "outputs": {
                        **task.outputs,
                        "manual_confirmation_note": note,
                    },
                }
            )
        self._save(updated, None)
        return updated

    def retry_task(self, task_id: str) -> PublishTask:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("发布任务不存在。")
        if task.final_publish_started_at or task.outputs.get("final_publish_clicked") == "true":
            raise ValueError("系统已经点击最终发布，结果可能已进入平台处理；请先人工核对平台后台，不能自动重试。")
        if task.retry_count >= 1:
            raise ValueError("该发布任务已重试过一次，请先人工核对平台后台状态。")
        updated = task.model_copy(
            update={
                "status": TaskStatus.QUEUED,
                "publish_status": PublishStatus.PENDING,
                "progress": 0,
                "stage": "已重新进入本机发布队列",
                "updated_at": datetime.now().astimezone(),
                "error_message": None,
                "action_required": None,
                "retry_count": task.retry_count + 1,
            }
        )
        self._save(updated, None)
        return updated

    def prepare_official_page(self, task_id: str) -> PublishTask:
        """Queue a reversible local-browser draft preparation.

        Preparing the official page is not a failed-job retry: the operator may
        save revised metadata and prepare the page again while the irreversible
        final publish click has not happened.
        """
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("发布任务不存在。")
        if task.final_publish_started_at or task.outputs.get("final_publish_clicked") == "true":
            raise ValueError("系统已经点击最终发布，请先到抖音后台核对结果。")
        if task.publish_status in {PublishStatus.SUCCEEDED, PublishStatus.OUTCOME_UNKNOWN}:
            raise ValueError("当前发布结果不能重新准备官方页，请先到抖音后台核对。")
        if task.provider_name != "douyin_local_browser":
            raise ValueError("当前任务不是抖音本机发布任务，不能准备抖音官方页。")
        if task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            return task
        updated = task.model_copy(
            update={
                "status": TaskStatus.QUEUED,
                "publish_status": PublishStatus.PENDING,
                "progress": 0,
                "stage": "正在准备抖音官方发布页",
                "updated_at": datetime.now().astimezone(),
                "error_message": None,
                "action_required": None,
            }
        )
        self._save(updated, None)
        return updated

    def confirm_auto_publish(self, task_id: str) -> PublishTask:
        """Authorize one Douyin task to use its prepared page and submit once."""
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("发布任务不存在。")
        if task.final_publish_started_at or task.outputs.get("final_publish_clicked") == "true":
            raise ValueError("系统已经点击最终发布，请先到抖音后台核对结果。")
        if task.publish_status in {
            PublishStatus.SUCCEEDED,
            PublishStatus.OUTCOME_UNKNOWN,
        }:
            raise ValueError("当前发布结果不能再次自动提交，请先到抖音后台核对。")
        if (
            task.provider_name != "douyin_local_browser"
            or task.target.platform != PublishPlatform.DOUYIN
        ):
            raise ValueError("当前任务不是抖音本机发布任务，不能自动提交。")
        if (
            task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}
            and task.target.auto_publish_authorized
        ):
            return task
        now = datetime.now().astimezone()
        updated = task.model_copy(
            update={
                "target": task.target.model_copy(
                    update={
                        "auto_publish_authorized": True,
                        "use_prepared_page": True,
                    }
                ),
                "status": TaskStatus.QUEUED,
                "publish_status": PublishStatus.PENDING,
                "progress": max(task.progress, 60),
                "stage": "等待上传完成后自动发布",
                "updated_at": now,
                "error_message": None,
                "action_required": None,
                "outputs": {
                    **task.outputs,
                    "task_auto_publish_authorized": "true",
                    "task_auto_publish_confirmed_at": now.isoformat(),
                },
            }
        )
        self._save(updated, None)
        return updated

    def resume_task(self, task_id: str) -> PublishTask:
        """Resume a user-gated task only while no final publish click happened."""
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("发布任务不存在。")
        if task.final_publish_started_at or task.outputs.get("final_publish_clicked") == "true":
            raise ValueError("系统已经点击最终发布，不能继续或重试；请先人工核对平台后台。")
        if task.status != TaskStatus.PAUSED or task.publish_status != PublishStatus.ACTION_REQUIRED:
            raise ValueError("当前任务不处于可继续的等待状态。")
        updated = task.model_copy(
            update={
                "status": TaskStatus.QUEUED,
                "publish_status": PublishStatus.PENDING,
                "progress": 0,
                "stage": "已恢复本机发布队列",
                "updated_at": datetime.now().astimezone(),
                "error_message": None,
                "action_required": None,
            }
        )
        self._save(updated, None)
        return updated

    def delete_task(self, task_id: str) -> str:
        """Delete a completed or user-paused publish record, never an active job."""
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("发布任务不存在。")
        if task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            raise ValueError("任务正在队列中或执行中，不能删除。")
        if not self.repository.delete_task(task_id):
            raise ValueError("发布任务不存在或已被删除。")
        return task_id

    def delete_tasks(self, task_ids: list[str]) -> list[str]:
        """Atomically validate a selected set before removing any task record."""
        unique_ids = list(dict.fromkeys(task_id for task_id in task_ids if task_id))
        if not unique_ids:
            raise ValueError("请至少选择一条发布任务。")
        tasks = [self.get_task(task_id) for task_id in unique_ids]
        missing = [task_id for task_id, task in zip(unique_ids, tasks) if task is None]
        if missing:
            raise ValueError("部分发布任务不存在或已被删除，请刷新列表。")
        active = [task.task_id for task in tasks if task and task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}]
        if active:
            raise ValueError("选中任务包含正在队列中或执行中的任务，不能删除。")
        deleted: list[str] = []
        for task_id in unique_ids:
            if not self.repository.delete_task(task_id):
                raise ValueError("部分发布任务删除失败，请刷新列表。")
            deleted.append(task_id)
        return deleted

    def list_tasks(self) -> list[PublishTask]:
        return [t for t in self.repository.list_tasks() if isinstance(t, PublishTask)]

    def get_task(self, task_id: str) -> PublishTask | None:
        task = self.repository.get_task(task_id)
        return task if isinstance(task, PublishTask) else None

    def _save(
        self,
        task: PublishTask,
        on_progress: Callable[[PublishTask], None] | None,
    ) -> None:
        self.repository.save_task(task)
        if on_progress is not None:
            try:
                on_progress(task)
            except Exception:
                pass

    @staticmethod
    def _manual_steps(platform: PublishPlatform) -> list[str]:
        labels = {
            PublishPlatform.DOUYIN: "抖音创作者服务中心",
            PublishPlatform.KUAISHOU: "快手创作者服务平台",
            PublishPlatform.WECHAT_CHANNELS: "微信视频号助手",
            PublishPlatform.XIAOHONGSHU: "小红书创作者中心",
            PublishPlatform.BILIBILI: "Bilibili 创作中心",
        }
        return [
            f"打开{labels[platform]}并登录已授权账号。",
            "上传本任务中的成片文件，复制标题、描述和话题标签。",
            "平台审核或发布完成后，回到本系统填写作品链接或确认失败原因。",
        ]
