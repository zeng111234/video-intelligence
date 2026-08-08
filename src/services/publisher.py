"""发布服务。

协调 Publisher 适配器与 TaskRepository，
支持单平台发布、多平台批量发布。
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from collections.abc import Mapping

from src.contracts import Publisher, TaskRepository
from src.models import (
    PublishPlatform,
    PublishSafetyState,
    PublishStatus,
    PublishTask,
    PublishTarget,
    TaskStatus,
)

# 发布防封保护（可在项目根 .env 调整）:
# - PUBLISH_DAILY_LIMIT: 每个平台账号每天最多发布条数
# - PUBLISH_MIN_INTERVAL_MINUTES: 同平台两条发布之间的最小间隔
PUBLISH_DAILY_LIMIT = int(os.getenv("PUBLISH_DAILY_LIMIT", "5"))
PUBLISH_MIN_INTERVAL_MINUTES = int(os.getenv("PUBLISH_MIN_INTERVAL_MINUTES", "15"))

# 发布行为风控提示标记：一旦出现即视为平台风控信号，账号自动暂停发布
PUBLISH_RISK_MARKERS = (
    "发布频繁",
    "操作频繁",
    "访问频繁",
    "内容违规",
    "行为异常",
    "频繁发布",
    "频率过快",
)
# 连续失败达到该次数后自动暂停账号发布
PUBLISH_MAX_CONSECUTIVE_FAILURES = 2
# 风控/连续失败暂停时长
PUBLISH_SAFETY_PAUSE_SECONDS = 24 * 60 * 60


class PublishService:
    def __init__(
        self,
        repository: TaskRepository,
        publishers: Mapping[str, Publisher],
    ) -> None:
        self.repository = repository
        self.publishers = publishers
        # 账号级发布互斥锁：同一平台同一账号同时只能有一个发布在跑，
        # 防止同步发布与队列并发抢同一官方页面。
        self._publish_locks: dict[tuple[str, str], threading.Lock] = {}
        self._publish_locks_guard = threading.Lock()

    def _publish_lock(self, platform: PublishPlatform, account_id: str | None) -> threading.Lock:
        key = (platform.value, self._account_key(platform, account_id))
        with self._publish_locks_guard:
            lock = self._publish_locks.get(key)
            if lock is None:
                lock = self._publish_locks[key] = threading.Lock()
            return lock

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
        platform_key = target.platform.value
        publisher = self.publishers.get(platform_key)
        if publisher is None:
            raise ValueError(f"未找到 {target.platform.value} 的发布适配器。")

        # 账号级互斥：该账号正在被其他发布占用时直接拒绝，
        # 防止同步发布与队列并发抢同一官方页面。
        lock = self._publish_lock(target.platform, target.account_id)
        if not lock.acquire(blocking=False):
            raise ValueError(
                "该平台账号已有发布任务正在执行，请稍后再试（防并发保护）。"
            )
        try:
            return self._publish_locked(
                video_path=video_path,
                target=target,
                source_pipeline_run_id=source_pipeline_run_id,
                batch_id=batch_id,
                on_progress=on_progress,
            )
        finally:
            lock.release()

    def _publish_locked(
        self,
        *,
        video_path: str,
        target: PublishTarget,
        source_pipeline_run_id: str | None = None,
        batch_id: str | None = None,
        on_progress: Callable[[PublishTask], None] | None = None,
    ) -> PublishTask:
        """持锁状态下的实际发布流程（原 publish 主体）。"""
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

    def _publish_usage(self, platform: PublishPlatform) -> tuple[int, datetime | None]:
        """该平台今天已发布条数与最近一次发布完成时间。

        以 final_publish_started_at（最终发布点击时间）为准；
        未真正点击发布的准备/暂停任务不计入。
        """
        now = datetime.now().astimezone()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        count = 0
        last_at: datetime | None = None
        for task in self.list_tasks():
            if task.target.platform != platform:
                continue
            final_at = task.final_publish_started_at
            if final_at is None:
                continue
            if final_at >= today_start:
                count += 1
            if last_at is None or final_at > last_at:
                last_at = final_at
        return count, last_at

    def publish_safety_status(self) -> list[dict[str, Any]]:
        """各平台/账号的发布安全状态：今日已发、剩余条数、下次可发、暂停信息。"""
        now = datetime.now().astimezone()
        result: list[dict[str, Any]] = []
        for platform in PublishPlatform:
            count_today, last_at = self._publish_usage(platform)
            account_keys = {
                self._account_key(platform, task.target.account_id)
                for task in self.list_tasks()
                if task.target.platform == platform
            }
            keys = sorted(account_keys) or ["default"]
            for key in keys:
                state = self.repository.get_publish_safety_state(platform.value, key)
                blocked = bool(
                    state and state.blocked_until and state.blocked_until > now
                )
                result.append(
                    {
                        "platform": platform.value,
                        "account_id": key,
                        "today_published": count_today,
                        "daily_limit": PUBLISH_DAILY_LIMIT,
                        "remaining_today": max(0, PUBLISH_DAILY_LIMIT - count_today),
                        "next_allowed_at": (
                            (last_at + timedelta(minutes=PUBLISH_MIN_INTERVAL_MINUTES)).isoformat()
                            if last_at
                            else None
                        ),
                        "blocked": blocked,
                        "blocked_until": (
                            state.blocked_until.isoformat()
                            if state and state.blocked_until
                            else None
                        ),
                        "blocked_reason": state.blocked_reason if state else None,
                    }
                )
        return result

    def resume_publish_account(
        self, platform: PublishPlatform, account_id: str | None = None
    ) -> dict[str, Any]:
        """手动恢复被暂停的发布账号（清暂停与失败计数）。"""
        key = self._account_key(platform, account_id)
        state = self.repository.get_publish_safety_state(platform.value, key)
        now = datetime.now().astimezone()
        active_block = bool(
            state and state.blocked_until and state.blocked_until > now
        )
        if state is not None and (
            active_block or state.consecutive_failures > 0
        ):
            self.repository.update_publish_safety_state(
                state.model_copy(
                    update={
                        "blocked_until": None,
                        "blocked_reason": None,
                        "consecutive_failures": 0,
                        "updated_at": now,
                    }
                )
            )
        return {
            "platform": platform.value,
            "account_id": key,
            "resumed": active_block,
            "message": "已恢复该账号发布。" if active_block else "该账号当前未处于暂停状态。",
        }

    def _account_key(self, platform: PublishPlatform, account_id: str | None) -> str:
        """账号粒度键：未指定账号时使用 default。"""
        return (account_id or "default").strip() or "default"

    def _record_publish_failure(
        self, platform: PublishPlatform, account_id: str | None, error_text: str
    ) -> None:
        """记录一次发布失败：连续失败达到上限或命中风控标记时自动暂停账号。"""
        key = self._account_key(platform, account_id)
        state = self.repository.get_publish_safety_state(platform.value, key)
        if state is None:
            state = PublishSafetyState(platform=platform.value, account_id=key)
        now = datetime.now().astimezone()
        failures = state.consecutive_failures + 1
        is_risk = any(marker in error_text for marker in PUBLISH_RISK_MARKERS)
        if is_risk:
            updated = state.model_copy(
                update={
                    "blocked_until": now + timedelta(seconds=PUBLISH_SAFETY_PAUSE_SECONDS),
                    "blocked_reason": (
                        "平台提示发布频繁或操作频繁，已自动暂停该账号发布 24 小时，"
                        "避免账号被限流。可在发布账号管理页手动恢复。"
                    ),
                    "consecutive_failures": failures,
                    "updated_at": now,
                }
            )
        elif failures >= PUBLISH_MAX_CONSECUTIVE_FAILURES:
            updated = state.model_copy(
                update={
                    "blocked_until": now + timedelta(seconds=PUBLISH_SAFETY_PAUSE_SECONDS),
                    "blocked_reason": (
                        f"连续 {failures} 次发布失败，已自动暂停该账号发布 24 小时，"
                        "避免异常操作被平台盯上。请检查发布环境后到发布账号管理页恢复。"
                    ),
                    "consecutive_failures": failures,
                    "updated_at": now,
                }
            )
        else:
            updated = state.model_copy(
                update={
                    "consecutive_failures": failures,
                    "updated_at": now,
                }
            )
        self.repository.update_publish_safety_state(updated)

    def _record_publish_success(
        self, platform: PublishPlatform, account_id: str | None
    ) -> None:
        """发布成功：清零连续失败计数（保留已触发的暂停状态）。"""
        key = self._account_key(platform, account_id)
        state = self.repository.get_publish_safety_state(platform.value, key)
        if state is None or state.consecutive_failures == 0:
            return
        self.repository.update_publish_safety_state(
            state.model_copy(
                update={
                    "consecutive_failures": 0,
                    "updated_at": datetime.now().astimezone(),
                }
            )
        )

    def execute_queued_task(self, task_id: str) -> PublishTask | None:
        """Execute exactly one queued task.  Called only by PublishWorker."""
        task = self.get_task(task_id)
        if task is None or task.status != TaskStatus.QUEUED:
            return task
        # 发布防封闸门：每日条数上限 + 条间最小间隔。
        # 超上限的任务明确失败并提示；间隔不足的任务保持排队自动等待。
        count_today, last_at = self._publish_usage(task.target.platform)
        if count_today >= PUBLISH_DAILY_LIMIT:
            blocked = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "publish_status": PublishStatus.FAILED,
                    "stage": "今日发布次数已用完",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": (
                        f"该平台今天已发布 {count_today} 条，达到每日上限 "
                        f"{PUBLISH_DAILY_LIMIT} 条（防封保护）。请明天再发布，"
                        "或在项目根 .env 调整 PUBLISH_DAILY_LIMIT。"
                    ),
                }
            )
            self._save(blocked, None)
            return blocked
        if last_at is not None:
            elapsed_minutes = (
                datetime.now().astimezone() - last_at
            ).total_seconds() / 60
            if elapsed_minutes < PUBLISH_MIN_INTERVAL_MINUTES:
                # 间隔不足：保持排队，下一轮 tick 自动继续等待
                return None
        # 账号防封暂停检查：风控提示或连续失败触发后，暂停期间任务明确失败
        account_key = self._account_key(task.target.platform, task.target.account_id)
        safety = self.repository.get_publish_safety_state(
            task.target.platform.value, account_key
        )
        if safety and safety.blocked_until and safety.blocked_until > datetime.now().astimezone():
            blocked = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "publish_status": PublishStatus.FAILED,
                    "stage": "账号已暂停发布",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": (
                        f"该发布账号已暂停：{safety.blocked_reason or '风控保护'}（"
                        f"暂停至 {safety.blocked_until.strftime('%m-%d %H:%M')}）。"
                        "可在发布账号管理页手动恢复。"
                    ),
                }
            )
            self._save(blocked, None)
            return blocked
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
        # 账号级互斥：该账号正在被同步发布或其他任务占用时，保持排队下一轮再试
        lock = self._publish_lock(task.target.platform, task.target.account_id)
        if not lock.acquire(blocking=False):
            return None
        try:
            return self._execute_queued_locked(task, running=None)
        finally:
            lock.release()

    def _execute_queued_locked(
        self, task: PublishTask, running: PublishTask | None
    ) -> PublishTask | None:
        """持锁状态下执行队列任务（原 execute_queued_task 主体）。"""
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
            self._record_publish_success(task.target.platform, task.target.account_id)
            return updated
        except Exception as exc:
            error_text = str(exc)
            self._record_publish_failure(
                task.target.platform, task.target.account_id, error_text
            )
            failed = running.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "publish_status": PublishStatus.FAILED,
                    "stage": "发布失败",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": error_text,
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
