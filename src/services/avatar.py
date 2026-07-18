from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from src.adapters.avatar import AvatarProviderError
from src.contracts import AvatarProvider, TaskRepository
from src.models import (
    AvatarAsset,
    AvatarAssetKind,
    AvatarCapability,
    AvatarJobSnapshot,
    AvatarProviderStatus,
    AvatarSubmitRequest,
    AvatarTask,
    ProviderErrorKind,
    TaskStatus,
)

MAX_RESULT_BYTES = 100 * 1024 * 1024


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
            or os.getenv(
                "AVATAR_RESULT_DIRECTORY", str(Path("data") / "avatar_results")
            )
        )

    def capabilities(self) -> AvatarCapability:
        return self.provider.capabilities()

    def list_assets(self) -> list[AvatarAsset]:
        capability = self.capabilities()
        if not capability.enabled:
            return []
        return [asset for asset in self.provider.list_assets() if asset.authorized]

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
        if len(request.script_text) > capability.max_script_chars:
            raise ValueError(
                f"当前供应商单次最多支持 {capability.max_script_chars} 个字符。"
            )
        if request.aspect_ratio not in capability.supported_aspect_ratios:
            raise ValueError("当前供应商不支持所选画幅。")

        assets = self.list_assets()
        avatar_ids = {
            item.asset_id for item in assets if item.kind == AvatarAssetKind.AVATAR
        }
        voice_ids = {
            item.asset_id for item in assets if item.kind == AvatarAssetKind.VOICE
        }
        if request.avatar_id not in avatar_ids or request.voice_id not in voice_ids:
            raise ValueError("所选数字人形象或音色当前不可用。")

        now = datetime.now().astimezone()
        task = AvatarTask(
            task_id=f"avatar-{uuid4().hex[:12]}",
            title=f"数字人视频 · {avatar_name}",
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
            speech_rate=request.speech_rate,
            aspect_ratio=request.aspect_ratio,
            resolution=request.resolution,
            background=request.background,
            rights_holder=request.rights_holder,
            rights_confirmed_at=now,
            idempotency_key=request.idempotency_key,
            provider_name=capability.provider_name,
            stage="正在提交",
            estimated_cost_cny=capability.estimated_cost_cny,
            estimated_seconds=capability.estimated_seconds,
            is_mock=False,
        )
        self.repository.save_task(task)
        try:
            snapshot = self.provider.submit(request)
        except AvatarProviderError as exc:
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

        updated = self._apply_snapshot(task, snapshot)
        self.repository.save_task(updated)
        return updated

    def refresh_task(self, task_id: str) -> AvatarTask:
        task = self._get_avatar_task(task_id)
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
            else:
                snapshot = self.provider.get_job(task.backend_job_id or task.task_id)
        except AvatarProviderError as exc:
            updated = task.model_copy(
                update={
                    "updated_at": datetime.now().astimezone(),
                    "error_message": str(exc),
                    "error_kind": exc.kind,
                }
            )
            self.repository.save_task(updated)
            return updated

        updated = self._apply_snapshot(task, snapshot)
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
            AvatarProviderStatus.RUNNING: TaskStatus.RUNNING,
            AvatarProviderStatus.SUCCEEDED: TaskStatus.SUCCEEDED,
            AvatarProviderStatus.FAILED: TaskStatus.FAILED,
            AvatarProviderStatus.OUTCOME_UNKNOWN: TaskStatus.FAILED,
        }
        now = datetime.now().astimezone()
        elapsed = (
            (now - task.created_at).total_seconds()
            if snapshot.status
            in {AvatarProviderStatus.SUCCEEDED, AvatarProviderStatus.FAILED}
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
