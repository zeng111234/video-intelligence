from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
import shutil
import subprocess
import threading
from uuid import uuid4

from src.adapters.avatar import AvatarProviderError, SHUYING_VOICE_TTS_JOB_PREFIX
from src.contracts import AvatarProvider, TaskRepository
from src.models import (
    AvatarAsset,
    AvatarAssetKind,
    AvatarBillingQuote,
    AvatarCapability,
    AvatarJobSnapshot,
    AvatarProviderStatus,
    AvatarSubmitRequest,
    AvatarTask,
    ProviderErrorKind,
    TaskStatus,
)

MAX_RESULT_BYTES = 100 * 1024 * 1024
MAX_VIDEO_NAME_LENGTH = 100
AVATAR_NAME_LOCK = threading.Lock()
AVATAR_REFRESH_LOCK = threading.Lock()


class AvatarService:
    def __init__(
        self,
        repository: TaskRepository,
        provider: AvatarProvider,
        *,
        result_directory: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.result_directory = Path(
            result_directory
            if result_directory is not None
            else os.getenv(
                "AVATAR_RESULT_DIRECTORY", str(Path("data") / "avatar_results")
            )
        )

    def capabilities(self) -> AvatarCapability:
        capability = self.provider.capabilities()
        if bool(getattr(self.provider, "billing_centrally_managed", False)):
            return capability
        if capability.mode.value != "production":
            return capability
        seconds = int(capability.estimated_seconds or 0)
        if seconds <= 0:
            return capability
        from src.services.pricing import get_price

        customer_cost = float(get_price("avatar_per_minute_cny")) * seconds / 60
        return capability.model_copy(
            update={"estimated_cost_cny": round(customer_cost, 4)}
        )

    def list_assets(self) -> list[AvatarAsset]:
        capability = self.capabilities()
        if not capability.enabled:
            return []
        return [asset for asset in self.provider.list_assets() if asset.authorized]

    def billing_quote(self, *, script_text: str, speech_rate: float) -> AvatarBillingQuote:
        """返回本次任务的预留上限；最终费用仍以成片整秒结算。"""

        capability = self.capabilities()
        if not capability.enabled:
            raise ValueError("数字人供应商尚不可用，不能预估费用。")
        if capability.mode.value == "sandbox":
            from src.services.avatar_billing import build_avatar_billing_quote

            return build_avatar_billing_quote(
                script_text=script_text,
                speech_rate=speech_rate,
                price_per_minute_cny=Decimal("0"),
            )
        quote = getattr(self.provider, "quote", None)
        if callable(quote):
            try:
                return quote(script_text=script_text, speech_rate=speech_rate)
            except AvatarProviderError as exc:
                raise ValueError(str(exc)) from exc
        from src.services.avatar_billing import build_avatar_billing_quote
        from src.services.pricing import get_price

        return build_avatar_billing_quote(
            script_text=script_text,
            speech_rate=speech_rate,
            price_per_minute_cny=get_price("avatar_per_minute_cny"),
        )

    def _reserve_local_billing(
        self, *, request: AvatarSubmitRequest, quote: AvatarBillingQuote
    ) -> bool:
        if bool(getattr(self.provider, "billing_centrally_managed", False)):
            return False
        reserve = getattr(self.repository, "reserve_avatar_billing", None)
        if not callable(reserve):
            return False
        from src.services.credits import get_current_owner

        try:
            reserve(
                owner=get_current_owner(),
                idempotency_key=request.idempotency_key,
                price_per_minute_cny=Decimal(str(quote.price_per_minute_cny)),
                billing_unit_seconds=quote.billing_unit_seconds,
                reserved_seconds=quote.reservation_seconds,
                reserved_credits=Decimal(str(quote.reservation_credits)),
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return True

    def _release_local_billing(self, idempotency_key: str) -> None:
        release = getattr(self.repository, "release_avatar_billing", None)
        if not callable(release):
            return
        from src.services.credits import get_current_owner

        release(owner=get_current_owner(), idempotency_key=idempotency_key)

    def _apply_local_billing_snapshot(
        self, snapshot: AvatarJobSnapshot
    ) -> AvatarJobSnapshot:
        if bool(getattr(self.provider, "billing_centrally_managed", False)):
            return snapshot
        get_record = getattr(self.repository, "get_avatar_billing", None)
        bind = getattr(self.repository, "bind_avatar_billing_job", None)
        settle = getattr(self.repository, "settle_avatar_billing", None)
        if not callable(get_record):
            return snapshot
        from src.services.credits import get_current_owner

        owner = get_current_owner()
        record = get_record(
            owner=owner,
            idempotency_key=snapshot.idempotency_key or None,
            provider_job_id=None if snapshot.idempotency_key else snapshot.job_id,
        )
        if record is None:
            return snapshot
        if snapshot.idempotency_key and snapshot.job_id and callable(bind):
            bind(
                owner=owner,
                idempotency_key=snapshot.idempotency_key,
                provider_job_id=snapshot.job_id,
            )
        if (
            snapshot.status == AvatarProviderStatus.SUCCEEDED
            and int(snapshot.estimated_seconds or 0) > 0
            and callable(settle)
        ):
            record = settle(
                owner=owner,
                idempotency_key=str(record["idempotency_key"]),
                final_seconds=int(snapshot.estimated_seconds),
            )
            final_credits = Decimal(str(record["final_credits"]))
            return snapshot.model_copy(
                update={
                    "estimated_cost_cny": float(final_credits),
                    "estimated_seconds": int(record["final_seconds"]),
                    "stage": (
                        f"{snapshot.stage}；按 {record['final_seconds']} 秒结算 "
                        f"{final_credits} 积分"
                    ),
                }
            )
        return snapshot.model_copy(
            update={
                "estimated_cost_cny": float(Decimal(str(record["reserved_credits"]))),
                "estimated_seconds": int(record["reserved_seconds"]),
            }
        )

    def submit(
        self,
        request: AvatarSubmitRequest,
        *,
        avatar_name: str,
        voice_name: str,
    ) -> AvatarTask:
        capability = self.capabilities()
        if not capability.enabled:
            raise ValueError("数字人供应商尚不可用，不能提交生成任务。")
        quote = self.billing_quote(
            script_text=request.script_text,
            speech_rate=request.speech_rate,
        )
        if len(request.script_text) > capability.max_script_chars:
            raise ValueError(
                f"当前供应商单次最多支持 {capability.max_script_chars} 个字符。"
            )
        if request.aspect_ratio not in capability.supported_aspect_ratios:
            raise ValueError("当前供应商不支持所选画幅。")
        if capability.profiles:
            profiles = {profile.profile_id: profile for profile in capability.profiles}
            profile = profiles.get(request.profile_id)
            if profile is None:
                raise ValueError("所选数字人生成方案不存在。")
            if not profile.enabled:
                detail = "、".join(profile.missing_configuration)
                raise ValueError(f"所选数字人生成方案尚未就绪：{detail}")
        elif request.profile_id != "default":
            raise ValueError("当前供应商不支持所选数字人生成方案。")

        assets = [item for item in self.list_assets() if item.status == "ready"]
        avatar_ids = {
            item.asset_id for item in assets if item.kind == AvatarAssetKind.AVATAR
        }
        voice_ids = {
            item.asset_id for item in assets if item.kind == AvatarAssetKind.VOICE
        }
        if request.avatar_id not in avatar_ids or request.voice_id not in voice_ids:
            raise ValueError("所选数字人形象或音色当前不可用。")

        with AVATAR_NAME_LOCK:
            existing = next(
                (
                    item
                    for item in self.repository.list_tasks()
                    if isinstance(item, AvatarTask)
                    and item.idempotency_key == request.idempotency_key
                ),
                None,
            )
            if existing is not None:
                return existing
            video_name = self._allocate_video_name(request)
            request = request.model_copy(update={"video_name": video_name})
            now = datetime.now().astimezone()
            task = AvatarTask(
                task_id=f"avatar-{uuid4().hex[:12]}",
                title=video_name,
                status=TaskStatus.QUEUED,
                progress=0,
                created_at=now,
                updated_at=now,
                script_text=request.script_text,
                source_task_id=request.source_task_id,
                source_revision_id=request.source_revision_id,
                avatar_id=request.avatar_id,
                avatar_name=avatar_name,
                voice_id=request.voice_id,
                voice_name=voice_name,
                profile_id=request.profile_id,
                speech_rate=request.speech_rate,
                aspect_ratio=request.aspect_ratio,
                resolution=request.resolution,
                background=request.background,
                rights_holder=request.rights_holder,
                rights_confirmed_at=now,
                idempotency_key=request.idempotency_key,
                provider_name=capability.provider_name,
                stage="正在提交",
                estimated_cost_cny=quote.reservation_credits,
                estimated_seconds=quote.reservation_seconds,
                is_mock=capability.mode.value == "sandbox",
            )
            reserved_locally = self._reserve_local_billing(request=request, quote=quote)
            # 兼容测试仓库与旧插件；正式 SQLite 会走上面的可退款预留账本。
            if (
                quote.reservation_credits > 0
                and not reserved_locally
                and not bool(getattr(self.provider, "billing_centrally_managed", False))
            ):
                from src.services.credits import (
                    CreditsService,
                    InsufficientCreditsError,
                )

                credits = Decimal(str(quote.reservation_credits))
                if credits > 0:
                    try:
                        CreditsService(self.repository).debit(
                            credits,
                            "数字人视频生成费用",
                            ref_type="avatar",
                            ref_id=task.task_id,
                        )
                    except InsufficientCreditsError as exc:
                        raise ValueError(exc.message) from exc
            self.repository.save_task(task)
        try:
            snapshot = self.provider.submit(request)
        except AvatarProviderError as exc:
            if not exc.outcome_unknown:
                self._release_local_billing(request.idempotency_key)
            status = (
                AvatarProviderStatus.OUTCOME_UNKNOWN
                if exc.outcome_unknown
                else AvatarProviderStatus.FAILED
            )
            failed = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "provider_status": status,
                    "stage": (
                        "提交结果待核对"
                        if status == AvatarProviderStatus.OUTCOME_UNKNOWN
                        else "提交失败"
                    ),
                    "updated_at": datetime.now().astimezone(),
                    "error_message": str(exc),
                    "error_kind": (
                        ProviderErrorKind.OUTCOME_UNKNOWN
                        if exc.outcome_unknown
                        else exc.kind
                    ),
                    "retry_count": 1 if exc.outcome_unknown else 0,
                }
            )
            self.repository.save_task(failed)
            return failed

        snapshot = self._apply_local_billing_snapshot(snapshot)
        updated = self._apply_snapshot(task, snapshot)
        self.repository.save_task(updated)
        return updated

    def _allocate_video_name(self, request: AvatarSubmitRequest) -> str:
        explicit_name = (request.video_name or "").strip()
        base_name = explicit_name or (request.keyword or "").strip() or "数字人视频"
        base_name = base_name[:MAX_VIDEO_NAME_LENGTH]
        existing_titles = [
            item.title.strip()
            for item in self.repository.list_tasks()
            if isinstance(item, AvatarTask)
        ]
        normalized_base = base_name.casefold()
        exact_exists = any(title.casefold() == normalized_base for title in existing_titles)
        if explicit_name and not exact_exists:
            return base_name

        max_sequence = 0
        for title in existing_titles:
            normalized_title = title.casefold()
            if normalized_title == normalized_base:
                max_sequence = max(max_sequence, 1)
                continue
            if not normalized_title.startswith(normalized_base):
                continue
            suffix = normalized_title[len(normalized_base):]
            if suffix.isdigit():
                max_sequence = max(max_sequence, int(suffix))

        sequence = max(1 if not explicit_name else 2, max_sequence + 1)
        suffix = str(sequence)
        return f"{base_name[: MAX_VIDEO_NAME_LENGTH - len(suffix)]}{suffix}"

    def refresh_task(self, task_id: str) -> AvatarTask:
        with AVATAR_REFRESH_LOCK:
            return self._refresh_task_locked(task_id)

    def _refresh_task_locked(self, task_id: str) -> AvatarTask:
        task = self._get_avatar_task(task_id)
        if task.provider_status in {
            AvatarProviderStatus.FAILED,
            AvatarProviderStatus.CANCELLED,
        }:
            return task
        if (
            task.provider_status == AvatarProviderStatus.OUTCOME_UNKNOWN
            and task.backend_job_id
            and task.backend_job_id.startswith(SHUYING_VOICE_TTS_JOB_PREFIX)
        ):
            # The legacy /video endpoint has no idempotency key or lookup API.
            # Re-posting after a timeout could duplicate work or charges.
            return task
        try:
            if (
                task.provider_status == AvatarProviderStatus.OUTCOME_UNKNOWN
                and not task.backend_job_id
            ):
                snapshot = self.provider.find_job(task.idempotency_key)
                if snapshot is None:
                    raise AvatarProviderError(
                        "供应商仍未找到该幂等任务，请人工核对后再决定是否重新提交。",
                        kind=ProviderErrorKind.OUTCOME_UNKNOWN,
                    )
            elif (
                task.backend_job_id
                and task.backend_job_id.startswith(SHUYING_VOICE_TTS_JOB_PREFIX)
                and callable(getattr(self.provider, "resume_submit", None))
            ):
                snapshot = self.provider.resume_submit(  # type: ignore[attr-defined]
                    self._request_from_task(task),
                    task.backend_job_id,
                )
            else:
                snapshot = self.provider.get_job(task.backend_job_id or task.task_id)
        except AvatarProviderError as exc:
            if task.is_mock and exc.kind == ProviderErrorKind.VALIDATION:
                # Sandbox 状态只存在内存中；服务重启后可以安全收口演示任务，
                # 但必须明确没有真实成片，不能长期伪装成“处理中”。
                updated = task.model_copy(
                    update={
                        "status": TaskStatus.SUCCEEDED,
                        "provider_status": AvatarProviderStatus.SUCCEEDED,
                        "progress": 100,
                        "stage": "演示已完成；未调用真实服务，也未生成真实成片",
                        "updated_at": datetime.now().astimezone(),
                        "error_message": None,
                        "error_kind": None,
                    }
                )
                self.repository.save_task(updated)
                return updated
            updated = task.model_copy(
                update={
                    "updated_at": datetime.now().astimezone(),
                    "error_message": str(exc),
                    "error_kind": exc.kind,
                }
            )
            self.repository.save_task(updated)
            return updated

        snapshot = self._apply_local_billing_snapshot(snapshot)
        updated = self._apply_snapshot(task, snapshot)
        self.repository.save_task(updated)
        return updated

    @staticmethod
    def _request_from_task(task: AvatarTask) -> AvatarSubmitRequest:
        return AvatarSubmitRequest(
            script_text=task.script_text,
            video_name=task.title,
            source_task_id=task.source_task_id,
            source_revision_id=task.source_revision_id,
            avatar_id=task.avatar_id,
            voice_id=task.voice_id,
            profile_id=task.profile_id,
            speech_rate=task.speech_rate,
            aspect_ratio=task.aspect_ratio,
            resolution=task.resolution,
            background=task.background,
            rights_holder=task.rights_holder,
            script_rights_confirmed=True,
            avatar_rights_confirmed=True,
            voice_rights_confirmed=True,
            idempotency_key=task.idempotency_key,
        )

    @staticmethod
    def can_retry_video_submit(task: AvatarTask) -> bool:
        return bool(
            task.status == TaskStatus.FAILED
            and task.provider_status == AvatarProviderStatus.FAILED
            and task.backend_job_id
            and task.backend_job_id.startswith(SHUYING_VOICE_TTS_JOB_PREFIX)
            and task.provider_job_id is None
            and task.retry_count < 1
            and task.stage == "视频提交失败"
            and task.error_kind == ProviderErrorKind.SERVICE
            and "系统繁忙" in (task.error_message or "")
        )

    def retry_failed_video(self, task_id: str) -> AvatarTask:
        with AVATAR_REFRESH_LOCK:
            task = self._get_avatar_task(task_id)
            if not self.can_retry_video_submit(task):
                raise ValueError("该任务当前不能安全重试视频提交。")
            resume_submit = getattr(self.provider, "resume_submit", None)
            if not callable(resume_submit):
                raise ValueError("当前数字人供应商不支持恢复视频提交。")

            retrying = task.model_copy(
                update={
                    "status": TaskStatus.RUNNING,
                    "provider_status": AvatarProviderStatus.RUNNING,
                    "progress": 15,
                    "stage": "正在重试视频提交",
                    "retry_count": 1,
                    "updated_at": datetime.now().astimezone(),
                    "error_message": None,
                    "error_kind": None,
                }
            )
            self.repository.save_task(retrying)
            try:
                snapshot = resume_submit(
                    self._request_from_task(retrying),
                    retrying.backend_job_id,
                )
            except AvatarProviderError as exc:
                status = (
                    AvatarProviderStatus.OUTCOME_UNKNOWN
                    if exc.outcome_unknown
                    else AvatarProviderStatus.FAILED
                )
                snapshot = AvatarJobSnapshot(
                    job_id=retrying.backend_job_id or retrying.task_id,
                    idempotency_key=retrying.idempotency_key,
                    status=status,
                    progress=0 if exc.outcome_unknown else 100,
                    stage=(
                        "视频提交结果待核对"
                        if exc.outcome_unknown
                        else "视频提交重试失败"
                    ),
                    estimated_cost_cny=retrying.estimated_cost_cny,
                    estimated_seconds=retrying.estimated_seconds,
                    error_kind=(
                        ProviderErrorKind.OUTCOME_UNKNOWN
                        if exc.outcome_unknown
                        else exc.kind
                    ),
                    error_message=str(exc),
                )

            updated = self._apply_snapshot(retrying, snapshot)
            self.repository.save_task(updated)
            return updated

    def download_result(self, task_id: str) -> AvatarTask:
        task = self._get_avatar_task(task_id)
        if task.provider_status != AvatarProviderStatus.SUCCEEDED:
            raise ValueError("只有供应商已成功的任务才能下载结果。")
        if not task.backend_job_id:
            raise ValueError("任务缺少内部服务任务编号。")

        payload, mime_type = self.provider.download_result(task.backend_job_id)
        if not payload or len(payload) > MAX_RESULT_BYTES:
            raise ValueError("生成视频为空或超过 100 MB 安全限制。")
        if mime_type not in {"video/mp4", "application/octet-stream"}:
            raise ValueError(f"供应商返回了不支持的结果类型：{mime_type}")
        if len(payload) < 12 or b"ftyp" not in payload[4:12]:
            raise ValueError("供应商结果不是有效的 MP4 文件。")

        self.result_directory.mkdir(parents=True, exist_ok=True)
        final_path = (self.result_directory / f"{task.task_id}.mp4").resolve()
        expected_root = self.result_directory.resolve()
        if expected_root not in final_path.parents:
            raise ValueError("结果文件路径越界。")
        temporary_path = final_path.with_suffix(".mp4.part")
        temporary_path.write_bytes(payload)
        try:
            self._validate_with_ffprobe(temporary_path)
            temporary_path.replace(final_path)
        finally:
            temporary_path.unlink(missing_ok=True)

        updated = task.model_copy(
            update={
                "result_path": str(final_path),
                "result_mime": "video/mp4",
                "result_size_bytes": len(payload),
                "outputs": {**task.outputs, "video": str(final_path)},
                "updated_at": datetime.now().astimezone(),
                "stage": "结果已保存",
                "error_message": None,
                "error_kind": None,
            }
        )
        self.repository.save_task(updated)
        return updated

    def _get_avatar_task(self, task_id: str) -> AvatarTask:
        task = self.repository.get_task(task_id)
        if not isinstance(task, AvatarTask):
            raise ValueError("数字人任务不存在。")
        return task

    @staticmethod
    def _apply_snapshot(task: AvatarTask, snapshot: AvatarJobSnapshot) -> AvatarTask:
        status_map = {
            AvatarProviderStatus.QUEUED: TaskStatus.QUEUED,
            AvatarProviderStatus.SUBMITTED: TaskStatus.SUBMITTED,
            AvatarProviderStatus.RUNNING: TaskStatus.RUNNING,
            AvatarProviderStatus.SUCCEEDED: TaskStatus.SUCCEEDED,
            AvatarProviderStatus.FAILED: TaskStatus.FAILED,
            AvatarProviderStatus.CANCELLED: TaskStatus.CANCELLED,
            AvatarProviderStatus.OUTCOME_UNKNOWN: TaskStatus.OUTCOME_UNKNOWN,
        }
        now = datetime.now().astimezone()
        elapsed = (
            (now - task.created_at).total_seconds()
            if snapshot.status
            in {
                AvatarProviderStatus.SUCCEEDED,
                AvatarProviderStatus.FAILED,
                AvatarProviderStatus.CANCELLED,
                AvatarProviderStatus.OUTCOME_UNKNOWN,
            }
            else task.elapsed_seconds
        )
        return task.model_copy(
            update={
                "status": status_map[snapshot.status],
                "progress": snapshot.progress,
                "updated_at": now,
                "elapsed_seconds": elapsed,
                "backend_job_id": snapshot.job_id,
                "provider_job_id": snapshot.provider_job_id,
                "provider_status": snapshot.status,
                "stage": snapshot.stage,
                "estimated_cost_cny": snapshot.estimated_cost_cny,
                "estimated_seconds": snapshot.estimated_seconds,
                "result_mime": snapshot.result_mime,
                "result_size_bytes": snapshot.result_size_bytes,
                "error_message": snapshot.error_message,
                "error_kind": snapshot.error_kind,
            }
        )

    @staticmethod
    def _validate_with_ffprobe(path: Path) -> None:
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            raise RuntimeError("未找到 FFprobe，不能安全验证生成视频。")
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0 or "video" not in completed.stdout:
            raise ValueError("FFprobe 未检测到有效视频流。")
