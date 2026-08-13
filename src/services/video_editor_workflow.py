"""面向 Web 工作台的智能剪辑工作流。

保持底层 ``VideoEditingService`` 的兼容接口不变，在其之上提供：
系统成片选择、可恢复的分析记录、字幕复核门禁与后台渲染任务。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlparse
from uuid import uuid4

from src.models import (
    AvatarTask,
    PipelineStage,
    TaskStatus,
    VideoEditConfig,
    VideoEditStep,
    VideoEditStepKind,
    VideoEditTask,
    VideoEditorBatch,
    VideoEditorBatchItem,
)
from src.services.credits import InsufficientCreditsError


_WORKFLOW_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="video-workflow"
)
_MAX_SOURCE_UPLOAD_BYTES = 50 * 1024 * 1024
_MAX_GENERATED_SUBTITLE_BYTES = 100 * 1024 * 1024
_MAX_SUBTITLE_SECONDS = 15 * 60
_MAX_BATCH_ITEMS = 10
_MAX_BGM_BYTES = 30 * 1024 * 1024
_BGM_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
_BGM_VOICEOVER_CATEGORIES = {
    "理性干货",
    "情绪共鸣",
    "故事叙事",
    "商业表达",
    "科技未来",
    "轻松日常",
    "励志成长",
    "悬念揭秘",
    "通用口播",
}
_BGM_ENERGY_LEVELS = {"克制", "平稳", "有推动感"}
_BGM_SOURCE_PROVIDERS = {"manual", "freepd", "pixabay", "light_factory", "bodian"}
_BGM_CONTENT_ID_RISKS = {"none", "registered", "unknown"}
_MAX_VISUAL_ASSET_BYTES = 10 * 1024 * 1024
_VISUAL_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_GENERIC_AVATAR_TITLE = re.compile(r"^数字人视频\d*$")
_LOCAL_PREVIEW_EXPORT_STYLE_VERSION = (
    "business_talking_head_v9.1-smart-opening-clean-hook-speed-1.15"
)
_LOCAL_PREVIEW_PLAYBACK_RATE = 1.15


class VideoEditorWorkflowError(ValueError):
    """用户可理解的智能剪辑工作流错误。"""


class VideoEditorWorkflowService:
    """协调系统素材、转写复核与视频编辑服务。"""

    def __init__(
        self,
        repository,
        video_editing_service,
        transcription_service,
        copywriting_service,
        *,
        cloud_configuration=None,
        cloud_providers=None,
    ) -> None:
        self.repository = repository
        self.video_editing_service = video_editing_service
        self.transcription_service = transcription_service
        self.copywriting_service = copywriting_service
        self._cloud_configuration_override = cloud_configuration
        self._cloud_providers_override = cloud_providers

    def _debit_credits(
        self,
        cost_cny: Decimal | float,
        *,
        reason: str,
        ref_type: str,
        ref_id: str,
    ) -> None:
        """按人民币费用扣积分；费用为 0/未知不扣，余额不足抛出 InsufficientCreditsError。"""
        from src.services.credits import (
            CreditsService,
            cny_to_credits,
        )

        credits = cny_to_credits(cost_cny)
        if credits <= 0:
            return
        CreditsService(self.repository).debit(
            credits,
            reason,
            ref_type=ref_type,
            ref_id=ref_id,
        )

    # ------------------------------------------------------------------
    # 系统素材
    # ------------------------------------------------------------------
    def list_sources(self) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        seen_paths: set[str] = set()

        for task in self.repository.list_tasks():
            if not isinstance(task, AvatarTask):
                continue
            path = Path(task.result_path or "")
            if (
                task.status != TaskStatus.SUCCEEDED
                or task.is_mock
                or not path.is_file()
            ):
                continue
            key = str(path.resolve()).casefold()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            source_id = f"avatar:{task.task_id}"
            sources.append(
                self._source_payload(
                    source_id=source_id,
                    source_type="avatar",
                    source_task_id=task.task_id,
                    title=self._semantic_source_title(
                        source_id,
                        task.title,
                        task.script_text,
                    ),
                    path=path,
                    created_at=task.created_at,
                )
            )

        for task in self.repository.list_tasks():
            if (
                not isinstance(task, VideoEditTask)
                or task.outputs.get("workflow") != "upload_source"
            ):
                continue
            path = Path(task.source_video_path)
            if (
                task.status != TaskStatus.SUCCEEDED
                or task.is_mock
                or not path.is_file()
            ):
                continue
            key = str(path.resolve()).casefold()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            sources.append(
                self._source_payload(
                    source_id=f"upload:{task.task_id}",
                    source_type="upload",
                    source_task_id=task.task_id,
                    title=task.title,
                    path=path,
                    created_at=task.created_at,
                )
            )

        list_runs = getattr(self.repository, "list_pipeline_runs", None)
        if callable(list_runs):
            for run in list_runs(limit=100):
                path = self._pipeline_video_path(run)
                if path is None:
                    continue
                key = str(path.resolve()).casefold()
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                sources.append(
                    self._source_payload(
                        source_id=f"pipeline:{run.run_id}",
                        source_type="pipeline",
                        source_task_id=run.run_id,
                        title=f"流水线成片 · {run.keyword}",
                        path=path,
                        created_at=run.updated_at,
                    )
                )

        return sorted(sources, key=lambda item: item["created_at"], reverse=True)

    def resolve_source(self, source_id: str) -> dict[str, Any]:
        source_type, sep, record_id = source_id.partition(":")
        if not sep or not record_id:
            raise VideoEditorWorkflowError("素材标识无效。")

        if source_type == "avatar":
            task = self.repository.get_task(record_id)
            if not isinstance(task, AvatarTask):
                raise VideoEditorWorkflowError("数字人成片不存在。")
            path = Path(task.result_path or "")
            if (
                task.status != TaskStatus.SUCCEEDED
                or task.is_mock
                or not path.is_file()
            ):
                raise VideoEditorWorkflowError("该数字人成片不可用或已被清理。")
            return self._source_payload(
                source_id=source_id,
                source_type=source_type,
                source_task_id=record_id,
                title=self._semantic_source_title(
                    source_id,
                    task.title,
                    task.script_text,
                ),
                path=path,
                created_at=task.created_at,
            )

        if source_type == "pipeline":
            list_runs = getattr(self.repository, "list_pipeline_runs", None)
            if not callable(list_runs):
                raise VideoEditorWorkflowError("当前环境未提供流水线素材。")
            for run in list_runs(limit=500):
                if run.run_id != record_id:
                    continue
                path = self._pipeline_video_path(run)
                if path is None:
                    raise VideoEditorWorkflowError("该流水线尚未生成可用成片。")
                return self._source_payload(
                    source_id=source_id,
                    source_type=source_type,
                    source_task_id=record_id,
                    title=f"流水线成片 · {run.keyword}",
                    path=path,
                    created_at=run.updated_at,
                )
            raise VideoEditorWorkflowError("流水线素材不存在。")

        if source_type == "upload":
            task = self.repository.get_task(record_id)
            if (
                not isinstance(task, VideoEditTask)
                or task.outputs.get("workflow") != "upload_source"
            ):
                raise VideoEditorWorkflowError("上传素材不存在。")
            path = Path(task.source_video_path)
            if (
                task.status != TaskStatus.SUCCEEDED
                or task.is_mock
                or not path.is_file()
            ):
                raise VideoEditorWorkflowError("该上传素材不可用或已被清理。")
            return self._source_payload(
                source_id=source_id,
                source_type=source_type,
                source_task_id=record_id,
                title=task.title,
                path=path,
                created_at=task.created_at,
            )

        raise VideoEditorWorkflowError("不支持的系统素材来源。")

    def _cached_source_context(self, source_id: str) -> dict[str, Any]:
        context: dict[str, Any] = {
            "selected_title": "",
            "title_candidates": [],
            "subtitle_segments": [],
            "duration_seconds": 0.0,
            "selected_bgm_id": None,
            "bgm_reason": None,
            "edit_plan": {},
            "enabled_plan_step_ids": [],
            "review_snapshot": {},
        }
        list_batches = getattr(self.repository, "list_video_editor_batches", None)
        if not callable(list_batches):
            return context
        for batch in list_batches(limit=100):
            for item in batch.items:
                if item.source_id != source_id:
                    continue
                if not context["selected_title"] and item.selected_title:
                    context["selected_title"] = item.selected_title
                if not context["title_candidates"] and item.title_candidates:
                    context["title_candidates"] = list(item.title_candidates)
                if not context["subtitle_segments"] and item.subtitle_segments:
                    context["subtitle_segments"] = [
                        dict(segment) for segment in item.subtitle_segments
                    ]
                if not context["selected_bgm_id"] and item.selected_bgm_id:
                    context["selected_bgm_id"] = item.selected_bgm_id
                    context["bgm_reason"] = item.bgm_reason
                if not context["edit_plan"] and item.edit_plan:
                    context["edit_plan"] = dict(item.edit_plan)
                if not context["enabled_plan_step_ids"] and item.enabled_plan_step_ids:
                    context["enabled_plan_step_ids"] = list(item.enabled_plan_step_ids)
                if not context["review_snapshot"] and item.review_snapshot:
                    context["review_snapshot"] = dict(item.review_snapshot)
                media = item.provider_payload.get("media") or {}
                if not context["duration_seconds"] and media.get("duration_seconds"):
                    context["duration_seconds"] = float(media["duration_seconds"])
            if (
                context["selected_title"]
                and context["title_candidates"]
                and context["subtitle_segments"]
                and context["duration_seconds"]
            ):
                break
        return context

    @staticmethod
    def _script_topic_title(script_text: str, fallback: str) -> str:
        script = re.sub(r"\s+", " ", script_text).strip()
        if not script:
            return fallback
        first_sentence = re.split(r"[。！？!?；;\n]|\s+", script, maxsplit=1)[0]
        subject = re.sub(
            r"^(最近|大家好|你知道吗|你发现没|今天(?:我们)?(?:来)?聊聊)\s*",
            "",
            first_sentence,
        ).strip(" ，,：:")
        if len(subject) > 9:
            subject = re.sub(r"(想做|想要|正在做)", "", subject, count=1)
        return (subject or first_sentence or fallback)[:40]

    def _semantic_source_title(
        self,
        source_id: str,
        stored_title: str,
        script_text: str = "",
    ) -> str:
        cached = self._cached_source_context(source_id)
        cached_title = str(cached.get("selected_title") or "").strip()
        if cached_title:
            return cached_title
        candidates = list(cached.get("title_candidates") or [])
        if candidates:
            return str(candidates[0]).strip()[:100]
        if not _GENERIC_AVATAR_TITLE.fullmatch(stored_title.strip()):
            return stored_title
        return self._script_topic_title(script_text, stored_title)

    def _avatar_script_text(self, source_id: str) -> str:
        source_type, _, record_id = source_id.partition(":")
        if source_type != "avatar" or not record_id:
            return ""
        task = self.repository.get_task(record_id)
        return task.script_text if isinstance(task, AvatarTask) else ""

    @staticmethod
    def _estimated_script_segments(
        script_text: str,
        duration_seconds: float,
    ) -> list[dict[str, Any]]:
        # The approved production script deliberately uses spaces to mark the
        # editor's human-reviewed spoken clauses.  Preserve those boundaries:
        # flattening them first makes captions jump across clauses (for example
        # "服务细节拍短视频标题写"), which reads like the legacy hard splitter.
        parts = [
            part.strip()
            for part in re.findall(
                r"[^。！？!?；;\s]+[。！？!?；;]?",
                script_text,
            )
            if part.strip()
        ]
        if not parts:
            return []
        total_characters = sum(len(part) for part in parts) or 1
        duration = duration_seconds or max(2.0, total_characters / 4.2)
        cursor = 0.0
        segments: list[dict[str, Any]] = []
        for index, part in enumerate(parts):
            end = (
                duration
                if index == len(parts) - 1
                else cursor + duration * len(part) / total_characters
            )
            segments.append(
                {
                    "start": round(cursor, 3),
                    "end": round(end, 3),
                    "text": part,
                }
            )
            cursor = end
        return segments

    @staticmethod
    def approved_script_segments_from_asr(
        script_text: str,
        asr_segments: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Project real ASR timing onto the exact approved spoken script.

        The avatar is generated from the approved script, while ASR may return
        harmless spelling variants (for example ``稳定的``/``稳定地`` or
        ``7``/``七``).  Keep ASR's real pause boundaries, but never replace the
        approved on-screen words with those recognition variants.
        """

        approved = re.sub(r"[\W_]+", "", script_text, flags=re.UNICODE)
        normalized_asr = [
            {
                "start": float(segment.get("start", 0)),
                "end": float(segment.get("end", 0)),
                "text": re.sub(
                    r"[\W_]+",
                    "",
                    str(segment.get("text") or ""),
                    flags=re.UNICODE,
                ),
            }
            for segment in asr_segments
            if str(segment.get("text") or "").strip()
        ]
        recognized = "".join(segment["text"] for segment in normalized_asr)
        if not approved or not recognized or not normalized_asr:
            raise VideoEditorWorkflowError("真实字幕时间轴为空，不能开始智能剪辑。")
        matcher = SequenceMatcher(None, recognized, approved, autojunk=False)
        if matcher.ratio() < 0.82:
            raise VideoEditorWorkflowError(
                "真实语音与已确认口播文案差异过大，已停止避免字幕错配。"
            )
        opcodes = matcher.get_opcodes()

        def project_boundary(position: int) -> int:
            for _tag, source_start, source_end, target_start, target_end in opcodes:
                if position > source_end:
                    continue
                if source_end <= source_start:
                    return target_start
                relative = (position - source_start) / (source_end - source_start)
                return max(
                    target_start,
                    min(
                        target_end,
                        round(target_start + relative * (target_end - target_start)),
                    ),
                )
            return len(approved)

        projected: list[dict[str, Any]] = []
        source_cursor = 0
        target_cursor = 0
        for index, segment in enumerate(normalized_asr):
            source_cursor += len(segment["text"])
            target_end = (
                len(approved)
                if index == len(normalized_asr) - 1
                else max(target_cursor, project_boundary(source_cursor))
            )
            text = approved[target_cursor:target_end]
            if text:
                projected.append(
                    {
                        "start": segment["start"],
                        "end": segment["end"],
                        "text": text,
                    }
                )
            target_cursor = target_end
        if target_cursor != len(approved):
            raise VideoEditorWorkflowError(
                "真实字幕时间轴未完整覆盖口播文案，已停止避免字幕错配。"
            )
        return projected

    def upload_source(
        self,
        *,
        file_name: str,
        media_type: str,
        media_bytes: bytes,
        rights_confirmed: bool,
        rights_holder: str,
    ) -> dict[str, Any]:
        """保存已授权本地视频，使其可被后续批次可靠引用。"""
        suffix = Path(file_name).suffix.lower()
        if suffix not in {".mp4", ".mov", ".m4v"}:
            raise VideoEditorWorkflowError("仅支持 MP4、MOV 或 M4V 素材。")
        if not rights_confirmed or not rights_holder.strip():
            raise VideoEditorWorkflowError("请确认拥有素材处理权并填写授权主体。")
        if not media_bytes:
            raise VideoEditorWorkflowError("上传文件为空。")
        if len(media_bytes) > _MAX_SOURCE_UPLOAD_BYTES:
            raise VideoEditorWorkflowError("上传素材超过 50MB 限制。")

        now = datetime.now().astimezone()
        task_id = f"upload-{uuid4().hex[:10]}"
        storage_dir = (
            self.video_editing_service.output_directory.parent / "video_uploads"
        )
        storage_dir.mkdir(parents=True, exist_ok=True)
        safe_name = (
            re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file_name).name) or f"source{suffix}"
        )
        path = storage_dir / f"{task_id}-{safe_name}"
        path.write_bytes(media_bytes)
        task = VideoEditTask(
            task_id=task_id,
            title=f"本地上传 · {file_name}",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            source_video_path=str(path),
            stage="素材已就绪",
            is_mock=False,
            outputs={
                "workflow": "upload_source",
                "rights_holder": rights_holder.strip(),
                "media_type": media_type or "video/mp4",
            },
        )
        self.repository.save_task(task)
        return self.resolve_source(f"upload:{task_id}")

    def upload_visual_asset(
        self,
        *,
        kind: str,
        file_name: str,
        media_type: str,
        media_bytes: bytes,
        rights_confirmed: bool,
        rights_holder: str,
    ) -> dict[str, Any]:
        """保存产品主图或背景图，供产品讲解成片可靠引用。"""
        if kind not in {"product", "background"}:
            raise VideoEditorWorkflowError("视觉素材类型只能是商品主图或背景图。")
        suffix = Path(file_name).suffix.lower()
        if suffix not in _VISUAL_SUFFIXES:
            raise VideoEditorWorkflowError("仅支持 PNG、JPG、JPEG 或 WebP 图片。")
        if not rights_confirmed or not rights_holder.strip():
            raise VideoEditorWorkflowError("请确认拥有图片处理权并填写授权主体。")
        if not media_bytes:
            raise VideoEditorWorkflowError("上传图片为空。")
        if len(media_bytes) > _MAX_VISUAL_ASSET_BYTES:
            raise VideoEditorWorkflowError("图片超过 10MB 限制。")
        if not self._is_supported_image(media_bytes, suffix):
            raise VideoEditorWorkflowError("图片内容与文件格式不匹配。")

        now = datetime.now().astimezone()
        asset_id = f"{kind}-{uuid4().hex[:10]}"
        safe_name = (
            re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file_name).name) or f"{kind}{suffix}"
        )
        directory = self._visual_asset_directory()
        path = directory / f"{asset_id}-{safe_name}"
        path.write_bytes(media_bytes)
        metadata = {
            "asset_id": asset_id,
            "kind": kind,
            "name": Path(file_name).stem or kind,
            "original_name": file_name,
            "stored_name": path.name,
            "media_type": mimetypes.guess_type(path.name)[0] or "image/png",
            "rights_holder": rights_holder.strip(),
            "rights_confirmed_at": now.isoformat(),
            "created_at": now.isoformat(),
        }
        self._visual_asset_metadata_path(asset_id).write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        return self._visual_asset_payload(metadata, path)

    def list_visual_assets(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind is not None and kind not in {"product", "background"}:
            raise VideoEditorWorkflowError("视觉素材类型无效。")
        directory = self._visual_asset_directory(create=False)
        if not directory.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for metadata_path in directory.glob("*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata.get("kind") not in {"product", "background"}:
                    continue
                if kind is not None and metadata["kind"] != kind:
                    continue
                media_path = directory / str(metadata["stored_name"])
                if not media_path.is_file():
                    continue
                items.append(self._visual_asset_payload(metadata, media_path))
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    def resolve_visual_asset(
        self, asset_id: str, *, expected_kind: str | None = None
    ) -> dict[str, Any]:
        metadata_path = self._visual_asset_metadata_path(asset_id)
        if not metadata_path.is_file():
            raise VideoEditorWorkflowError("视觉素材不存在或已被清理。")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("asset_id") != asset_id:
                raise VideoEditorWorkflowError("视觉素材标识无效。")
            if expected_kind is not None and metadata.get("kind") != expected_kind:
                raise VideoEditorWorkflowError("视觉素材类型不匹配。")
            media_path = self._visual_asset_directory(create=False) / str(
                metadata["stored_name"]
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise VideoEditorWorkflowError("视觉素材记录无法读取。") from exc
        if not media_path.is_file():
            raise VideoEditorWorkflowError("视觉素材文件不存在或已被清理。")
        return self._visual_asset_payload(metadata, media_path)

    def create_product_showcase_job(
        self,
        *,
        source_id: str,
        product_asset_id: str,
        background_asset_id: str | None = None,
        layout: str = "avatar_left_product_right",
    ) -> VideoEditTask:
        """创建不调用数字人供应商的产品讲解后期合成任务。"""
        if layout not in {"avatar_left_product_right", "product_canvas_avatar_pip"}:
            raise VideoEditorWorkflowError("不支持的产品讲解版式。")
        source = self.resolve_source(source_id)
        if source["source_type"] != "avatar":
            raise VideoEditorWorkflowError("产品讲解包装只能使用已完成的数字人成片。")
        product = self.resolve_visual_asset(product_asset_id, expected_kind="product")
        background = (
            self.resolve_visual_asset(background_asset_id, expected_kind="background")
            if background_asset_id
            else None
        )
        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id=f"showcase-{uuid4().hex[:10]}",
            title=f"产品讲解 · {source['title']}",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            source_video_path=source["_path"],
            edit_config=VideoEditConfig(
                steps=[
                    VideoEditStep(
                        kind=VideoEditStepKind.PRODUCT_SHOWCASE,
                        params={
                            "product_path": product["_path"],
                            "background_path": background["_path"]
                            if background
                            else None,
                            "layout": layout,
                        },
                        order=0,
                    )
                ]
            ),
            source_avatar_task_id=source["source_task_id"],
            stage="等待产品讲解合成",
            is_mock=False,
            outputs={
                "workflow": "product_showcase",
                "source_id": source_id,
                "product_asset_id": product_asset_id,
                "background_asset_id": background_asset_id or "",
                "layout": layout,
            },
        )
        self.repository.save_task(task)
        _WORKFLOW_EXECUTOR.submit(self._run_product_showcase, task.task_id)
        return task

    def _run_product_showcase(self, task_id: str) -> None:
        task = self._get_workflow_task(task_id, "product_showcase")
        try:
            self._update(
                task, status=TaskStatus.RUNNING, progress=10, stage="准备产品讲解合成"
            )
            result = self.video_editing_service.edit_video(
                source_video_path=task.source_video_path,
                edit_config=task.edit_config,
                source_avatar_task_id=task.source_avatar_task_id,
                task_id=task.task_id,
            )
            result = result.model_copy(
                update={
                    "title": task.title,
                    "outputs": {**result.outputs, **task.outputs},
                }
            )
            self.repository.save_task(result)
        except Exception as exc:
            self._update(
                task,
                status=TaskStatus.FAILED,
                stage="产品讲解合成失败",
                error_message=str(exc),
            )

    def list_bgm_assets(self) -> list[dict[str, Any]]:
        directory = self._bgm_directory()
        if not directory.is_dir():
            return []
        assets: list[dict[str, Any]] = []
        for metadata_path in directory.glob("bgm-*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata.get("retired"):
                    continue
                media_path = directory / metadata["stored_name"]
                if not media_path.is_file():
                    continue
                assets.append(self._bgm_payload(metadata, media_path))
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return sorted(assets, key=lambda item: item["created_at"], reverse=True)

    def upload_bgm(
        self,
        *,
        file_name: str,
        media_type: str,
        media_bytes: bytes,
        mood: str,
        rights_confirmed: bool,
        rights_holder: str,
        voiceover_category: str = "通用口播",
        energy: str = "克制",
        source_provider: str = "manual",
        source_url: str = "",
        license_url: str = "",
        content_id_risk: str = "unknown",
    ) -> dict[str, Any]:
        suffix = Path(file_name).suffix.lower()
        if suffix not in _BGM_SUFFIXES:
            raise VideoEditorWorkflowError(
                "背景音乐仅支持 MP3、WAV、M4A、AAC 或 FLAC。"
            )
        if not rights_confirmed or not rights_holder.strip():
            raise VideoEditorWorkflowError("请确认拥有音乐使用权并填写授权主体。")
        if not media_bytes:
            raise VideoEditorWorkflowError("上传的背景音乐为空。")
        if len(media_bytes) > _MAX_BGM_BYTES:
            raise VideoEditorWorkflowError("背景音乐超过 30MB 限制。")
        normalized_category = voiceover_category.strip() or "通用口播"
        if normalized_category not in _BGM_VOICEOVER_CATEGORIES:
            raise VideoEditorWorkflowError("请选择系统支持的口播音乐分类。")
        normalized_energy = energy.strip() or "克制"
        if normalized_energy not in _BGM_ENERGY_LEVELS:
            raise VideoEditorWorkflowError("请选择系统支持的音乐能量等级。")
        normalized_provider = source_provider.strip().lower() or "manual"
        if normalized_provider not in _BGM_SOURCE_PROVIDERS:
            raise VideoEditorWorkflowError("背景音乐来源类型无效。")
        normalized_content_id_risk = content_id_risk.strip().lower() or "unknown"
        if normalized_content_id_risk not in _BGM_CONTENT_ID_RISKS:
            raise VideoEditorWorkflowError("背景音乐版权识别风险标记无效。")
        normalized_source_url = source_url.strip()
        normalized_license_url = license_url.strip()
        for label, value in (
            ("素材来源链接", normalized_source_url),
            ("授权说明链接", normalized_license_url),
        ):
            if value and urlparse(value).scheme not in {"http", "https"}:
                raise VideoEditorWorkflowError(f"{label}必须是 http 或 https 地址。")
        if normalized_provider != "manual" and not normalized_source_url:
            raise VideoEditorWorkflowError("外部音乐必须保存原始素材页面链接。")

        asset_id = f"bgm-{uuid4().hex[:12]}"
        directory = self._bgm_directory()
        directory.mkdir(parents=True, exist_ok=True)
        media_path = directory / f"{asset_id}{suffix}"
        media_path.write_bytes(media_bytes)
        try:
            duration_seconds = self._probe_bgm_duration(media_path)
        except Exception:
            media_path.unlink(missing_ok=True)
            raise
        now = datetime.now().astimezone()
        normalized_media_type = (media_type or "").strip().lower()
        if normalized_media_type in {
            "",
            "application/octet-stream",
            "binary/octet-stream",
        }:
            normalized_media_type = mimetypes.guess_type(file_name)[0] or "audio/mpeg"
        metadata = {
            "asset_id": asset_id,
            "title": Path(file_name).stem[:100] or "本地背景音乐",
            "original_name": Path(file_name).name,
            "stored_name": media_path.name,
            "media_type": normalized_media_type,
            "mood": (mood or "通用").strip()[:30],
            "rights_holder": rights_holder.strip()[:100],
            "rights_confirmed_at": now.isoformat(),
            "created_at": now.isoformat(),
            "duration_seconds": duration_seconds,
            "voiceover_category": normalized_category,
            "energy": normalized_energy,
            "tags": [
                token
                for token in re.split(r"[\s,，、·/]+", (mood or "").strip())
                if token
            ][:12],
            "source_provider": normalized_provider,
            "source_url": normalized_source_url,
            "license_url": normalized_license_url,
            "content_id_risk": normalized_content_id_risk,
        }
        (directory / f"{asset_id}.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self._bgm_payload(metadata, media_path)

    def resolve_bgm_asset(self, asset_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"bgm-[a-f0-9]{12}", asset_id or ""):
            raise VideoEditorWorkflowError("背景音乐标识无效。")
        metadata_path = self._bgm_directory() / f"{asset_id}.json"
        if not metadata_path.is_file():
            raise VideoEditorWorkflowError("背景音乐不存在。")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            media_path = self._bgm_directory() / metadata["stored_name"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise VideoEditorWorkflowError("背景音乐记录已损坏。") from exc
        if not media_path.is_file():
            raise VideoEditorWorkflowError("背景音乐文件已不存在。")
        return {**self._bgm_payload(metadata, media_path), "_path": str(media_path)}

    def _bgm_directory(self) -> Path:
        return self.video_editing_service.output_directory.parent / "bgm_library"

    @staticmethod
    def _probe_bgm_duration(path: Path) -> float:
        if not shutil.which("ffprobe"):
            raise VideoEditorWorkflowError("未检测到 FFprobe，无法验证背景音乐。")
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise VideoEditorWorkflowError("背景音乐无法解析或文件已损坏。")
        data = json.loads(result.stdout or "{}")
        duration = float((data.get("format") or {}).get("duration") or 0)
        if duration <= 0:
            raise VideoEditorWorkflowError("背景音乐时长无效。")
        return round(duration, 2)

    @staticmethod
    def _prepare_bgm_for_cloud_mix(source: Path, *, volume: float) -> Path:
        """Create a low-volume audio-only asset before MPS mixes it with speech.

        MPS Amix has no per-external-track volume control.  Preparing the BGM on
        the server keeps the client lightweight and prevents a 1:1 mix from
        overwhelming a talking-head recording.
        """
        if not shutil.which("ffmpeg"):
            raise VideoEditorWorkflowError(
                "未检测到 FFmpeg，暂不能为云端成片准备低音量背景音乐。"
            )
        safe_volume = min(max(float(volume), 0.08), 0.35)
        handle = tempfile.NamedTemporaryFile(suffix=".m4a", delete=False)
        handle.close()
        prepared = Path(handle.name)
        result = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                "-vn",
                "-af",
                f"volume={safe_volume:.3f},afade=t=in:st=0:d=0.5",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(prepared),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if (
            result.returncode != 0
            or not prepared.is_file()
            or prepared.stat().st_size == 0
        ):
            prepared.unlink(missing_ok=True)
            raise VideoEditorWorkflowError("背景音乐预处理失败，请更换音乐后重试。")
        return prepared

    @staticmethod
    def _bgm_payload(metadata: dict[str, Any], media_path: Path) -> dict[str, Any]:
        mood = str(metadata.get("mood") or "通用")
        category = str(metadata.get("voiceover_category") or "").strip()
        if category not in _BGM_VOICEOVER_CATEGORIES:
            category = (
                "科技未来"
                if "科技" in mood or "未来" in mood
                else "理性干货"
                if "知识" in mood or "讲解" in mood
                else "故事叙事"
                if "叙事" in mood or "故事" in mood
                else "情绪共鸣"
                if "温柔" in mood or "治愈" in mood
                else "商业表达"
                if "商务" in mood or "品牌" in mood
                else "轻松日常"
                if "轻松" in mood or "欢快" in mood
                else "通用口播"
            )
        energy = str(metadata.get("energy") or "克制").strip()
        if energy not in _BGM_ENERGY_LEVELS:
            energy = "克制"
        tags = metadata.get("tags")
        normalized_tags = (
            [str(item).strip() for item in tags if str(item).strip()]
            if isinstance(tags, list)
            else [token for token in re.split(r"[\s,，、·/]+", mood) if token]
        )
        return {
            "asset_id": metadata["asset_id"],
            "title": metadata["title"],
            "original_name": metadata["original_name"],
            "media_type": metadata["media_type"],
            "mood": mood,
            "voiceover_category": category,
            "energy": energy,
            "tags": normalized_tags[:12],
            "rights_holder": metadata["rights_holder"],
            "rights_confirmed_at": metadata["rights_confirmed_at"],
            "created_at": metadata["created_at"],
            "duration_seconds": metadata["duration_seconds"],
            "size_bytes": media_path.stat().st_size,
            "media_url": f"/api/v1/video-editor/bgm/{metadata['asset_id']}/media",
            "source_provider": str(metadata.get("source_provider") or "manual"),
            "source_url": str(metadata.get("source_url") or ""),
            "license_url": str(metadata.get("license_url") or ""),
            "content_id_risk": str(metadata.get("content_id_risk") or "unknown"),
        }

    def _visual_asset_directory(self, *, create: bool = True) -> Path:
        directory = (
            self.video_editing_service.output_directory.parent / "creative_assets"
        )
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _visual_asset_metadata_path(self, asset_id: str) -> Path:
        if not re.fullmatch(r"(?:product|background)-[a-f0-9]{10}", asset_id):
            raise VideoEditorWorkflowError("视觉素材标识无效。")
        return self._visual_asset_directory() / f"{asset_id}.json"

    @staticmethod
    def _is_supported_image(media_bytes: bytes, suffix: str) -> bool:
        if suffix == ".png":
            return media_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        if suffix in {".jpg", ".jpeg"}:
            return media_bytes.startswith(b"\xff\xd8\xff")
        return (
            len(media_bytes) >= 12
            and media_bytes[:4] == b"RIFF"
            and media_bytes[8:12] == b"WEBP"
        )

    @staticmethod
    def _visual_asset_payload(
        metadata: dict[str, Any], media_path: Path
    ) -> dict[str, Any]:
        return {
            "asset_id": metadata["asset_id"],
            "kind": metadata["kind"],
            "name": metadata["name"],
            "original_name": metadata["original_name"],
            "media_type": metadata["media_type"],
            "rights_holder": metadata["rights_holder"],
            "rights_confirmed_at": metadata["rights_confirmed_at"],
            "created_at": metadata["created_at"],
            "size_bytes": media_path.stat().st_size,
            "media_url": f"/api/v1/video-editor/visual-assets/{metadata['asset_id']}/media",
            "_path": str(media_path),
        }

    @staticmethod
    def _source_payload(
        *,
        source_id: str,
        source_type: str,
        source_task_id: str,
        title: str,
        path: Path,
        created_at: datetime,
    ) -> dict[str, Any]:
        stat = path.stat()
        return {
            "source_id": source_id,
            "source_type": source_type,
            "source_task_id": source_task_id,
            "title": title,
            "file_name": path.name,
            "size_bytes": stat.st_size,
            "created_at": created_at.isoformat(),
            "media_url": f"/api/v1/video-editor/sources/{source_id}/media",
            "media_type": mimetypes.guess_type(path.name)[0] or "video/mp4",
            "_path": str(path),
        }

    def _pipeline_video_path(self, run) -> Path | None:
        if getattr(run, "edit_task_id", None):
            task = self.repository.get_task(run.edit_task_id)
            path = Path(getattr(task, "result_path", "") or "")
            if getattr(task, "status", None) == TaskStatus.SUCCEEDED and path.is_file():
                return path
        for stage in getattr(run, "stages", []) or []:
            if getattr(stage, "stage", None) != PipelineStage.VIDEO_EDITING:
                continue
            path = Path((getattr(stage, "outputs", {}) or {}).get("video_path", ""))
            if path.is_file():
                return path
        return None

    # ------------------------------------------------------------------
    # 分析
    # ------------------------------------------------------------------
    def create_analysis(
        self,
        *,
        source_id: str,
        target_platform: str = "douyin",
        subtitle_enabled: bool = True,
        subtitle_model: str = "large-v3-turbo",
        language: str = "zh",
    ) -> VideoEditTask:
        source = self.resolve_source(source_id)
        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id=f"analysis-{uuid4().hex[:10]}",
            title=f"智能分析 · {source['file_name']}",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            source_video_path=source["_path"],
            stage="等待分析",
            is_mock=False,
            outputs={
                "workflow": "analysis",
                "source_id": source_id,
                "target_platform": target_platform,
                "subtitle_enabled": str(subtitle_enabled).lower(),
                "subtitle_model": subtitle_model,
                "language": language,
            },
        )
        self.repository.save_task(task)
        _WORKFLOW_EXECUTOR.submit(self._run_analysis, task.task_id)
        return task

    def get_analysis(self, analysis_id: str) -> dict[str, Any]:
        task = self._get_workflow_task(analysis_id, "analysis")
        return self._analysis_payload(task)

    def _run_analysis(self, analysis_id: str) -> None:
        task = self._get_workflow_task(analysis_id, "analysis")
        try:
            task = self._update(
                task, status=TaskStatus.RUNNING, progress=10, stage="读取媒体信息"
            )
            media = self._probe_media(Path(task.source_video_path))
            task = self._update(task, progress=35, stage="分析音频节奏")
            audio = self._analyze_audio(
                Path(task.source_video_path), media.get("duration_seconds")
            )
            recommendations, findings = self._recommend(media, audio, task.outputs)
            transcript_id = ""
            subtitle_error = ""

            if task.outputs.get("subtitle_enabled") == "true":
                task = self._update(task, progress=55, stage="生成字幕草稿")
                try:
                    transcript_id = self._create_transcription(task, media)
                except Exception as exc:
                    subtitle_error = str(exc)

            source = self.resolve_source(task.outputs.get("source_id", ""))
            transcript_text = self._transcript_text(transcript_id)
            title_candidates = self._local_title_candidates(
                source["title"],
                transcript_text,
                task.outputs.get("target_platform", "douyin"),
            )
            payload = {
                "media": media,
                "audio": audio,
                "findings": findings,
                "recommended_steps": recommendations,
                "subtitle_task_id": transcript_id or None,
                "subtitle_error": subtitle_error or None,
                "title_candidates": title_candidates,
            }
            outputs = {
                **task.outputs,
                "analysis_json": json.dumps(payload, ensure_ascii=False),
                "transcription_task_id": transcript_id,
                "subtitle_error": subtitle_error,
            }
            self._update(
                task,
                status=TaskStatus.SUCCEEDED,
                progress=100,
                stage="分析完成" if not subtitle_error else "分析完成（字幕待处理）",
                outputs=outputs,
            )
        except Exception as exc:
            self._update(
                task, status=TaskStatus.FAILED, stage="分析失败", error_message=str(exc)
            )

    def _create_transcription(self, task: VideoEditTask, media: dict[str, Any]) -> str:
        path = Path(task.source_video_path)
        if path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
            raise VideoEditorWorkflowError("该成片格式暂不支持字幕识别。")
        if path.stat().st_size > _MAX_GENERATED_SUBTITLE_BYTES:
            raise VideoEditorWorkflowError(
                "成片超过 100MB，暂不能在智能剪辑中生成字幕。"
            )
        if float(media.get("duration_seconds") or 0) > _MAX_SUBTITLE_SECONDS:
            raise VideoEditorWorkflowError(
                "成片超过 15 分钟，暂不能在智能剪辑中生成字幕。"
            )
        transcript = self.transcription_service.create_task(
            media_name=path.name,
            media_type=mimetypes.guess_type(path.name)[0] or "video/mp4",
            media_bytes=path.read_bytes(),
            rights_confirmed=True,
            rights_holder="系统内已授权素材",
            model_name=task.outputs.get("subtitle_model", "large-v3-turbo"),
            language=task.outputs.get("language", "zh"),
            max_media_bytes=_MAX_GENERATED_SUBTITLE_BYTES,
        )
        if transcript.status != TaskStatus.SUCCEEDED:
            raise VideoEditorWorkflowError(transcript.error_message or "字幕识别失败。")
        return transcript.task_id

    def _transcript_text(self, transcript_id: str) -> str:
        if not transcript_id:
            return ""
        task = self.repository.get_task(transcript_id)
        return "".join(
            str(segment.text).strip()
            for segment in getattr(task, "segments", [])
            if str(segment.text).strip()
        )

    @staticmethod
    def _local_title_candidates(
        source_title: str, transcript_text: str, platform: str
    ) -> list[str]:
        clean_source = re.sub(
            r"^(本地上传|流水线成片|系统数字人成片)\s*[·：:-]?\s*", "", source_title
        ).strip()
        clean_source = Path(clean_source).stem
        first_sentence = re.split(
            r"[。！？!?；;\n]", transcript_text.strip(), maxsplit=1
        )[0]
        subject = re.sub(r"\s+", "", first_sentence or clean_source or "这条视频")[
            :22
        ].rstrip("，,。.")
        prefix = "视频号" if platform == "wechat_channels" else "短视频"
        candidates = [
            subject,
            f"看懂{subject}",
            f"别错过：{subject}",
            f"{prefix}重点：{subject}",
        ]
        result: list[str] = []
        for candidate in candidates:
            normalized = candidate[:40].strip(" ：:")
            if normalized and normalized not in result:
                result.append(normalized)
            if len(result) == 3:
                break
        return result

    @staticmethod
    def _probe_media(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise VideoEditorWorkflowError("源成片已不存在。")
        if not shutil.which("ffprobe"):
            raise VideoEditorWorkflowError("未检测到 FFprobe，无法分析视频。")
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode != 0:
            raise VideoEditorWorkflowError(
                (result.stderr or "媒体分析失败。").strip()[:240]
            )
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        video = next(
            (item for item in streams if item.get("codec_type") == "video"), {}
        )
        has_audio = any(item.get("codec_type") == "audio" for item in streams)
        fps_text = str(video.get("r_frame_rate") or "0/1")
        try:
            num, den = fps_text.split("/", 1)
            fps = round(float(num) / max(float(den), 1), 2)
        except Exception:
            fps = 0.0
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        return {
            "duration_seconds": round(
                float((data.get("format") or {}).get("duration") or 0), 2
            ),
            "width": width,
            "height": height,
            "fps": fps,
            "orientation": "vertical" if height >= width else "horizontal",
            "has_audio": has_audio,
            "size_bytes": path.stat().st_size,
        }

    @staticmethod
    def _analyze_audio(path: Path, duration: float | None) -> dict[str, Any]:
        if not shutil.which("ffmpeg"):
            return {"available": False}
        result: dict[str, Any] = {
            "available": True,
            "mean_volume_db": None,
            "silence_seconds": 0.0,
        }
        try:
            volume = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "info",
                    "-i",
                    str(path),
                    "-af",
                    "volumedetect",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            match = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", volume.stderr or "")
            if match:
                result["mean_volume_db"] = float(match.group(1))
        except Exception:
            pass
        try:
            silence = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "info",
                    "-i",
                    str(path),
                    "-af",
                    "silencedetect=noise=-30dB:d=0.5",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            starts = [
                float(item)
                for item in re.findall(
                    r"silence_start:\s*([\d.]+)", silence.stderr or ""
                )
            ]
            ends = [
                float(item)
                for item in re.findall(r"silence_end:\s*([\d.]+)", silence.stderr or "")
            ]
            result["silence_seconds"] = round(
                sum(max(0, end - start) for start, end in zip(starts, ends)), 2
            )
            result["silence_intervals"] = [
                {"start": round(start, 2), "end": round(end, 2)}
                for start, end in zip(starts[:12], ends[:12])
            ]
        except Exception:
            result["silence_intervals"] = []
        return result

    @staticmethod
    def _recommend(
        media: dict[str, Any], audio: dict[str, Any], outputs: dict[str, str]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        steps: list[dict[str, Any]] = []
        findings: list[str] = []
        volume = audio.get("mean_volume_db")
        if isinstance(volume, (int, float)) and (volume < -18 or volume > -14):
            steps.append(
                {
                    "kind": "ai_volume_norm",
                    "params": {"target_i": -16},
                    "enabled": True,
                    "label": "统一口播响度",
                }
            )
            findings.append(f"平均音量约 {volume:.1f} dB，建议标准化到短视频常用响度。")
        silence_seconds = float(audio.get("silence_seconds") or 0)
        duration = float(media.get("duration_seconds") or 0)
        if (
            silence_seconds >= 1.5
            and duration > 0
            and silence_seconds / duration >= 0.05
        ):
            steps.append(
                {
                    "kind": "ai_silence_trim",
                    "params": {
                        "noise_threshold": -30,
                        "min_duration": 1.5,
                        "keep_padding": 0.35,
                    },
                    "enabled": True,
                    "label": "压缩长停顿",
                }
            )
            findings.append(f"检测到约 {silence_seconds:.1f} 秒静音，可压缩口播节奏。")
        if media.get("orientation") != "vertical" or (
            media.get("width", 0)
            and media.get("height", 0)
            and media.get("height", 0) < 1280
        ):
            steps.append(
                {
                    "kind": "resize",
                    "params": {},
                    "enabled": True,
                    "label": "适配竖屏平台",
                }
            )
            findings.append("画幅或清晰度与竖屏发布预设不一致，建议等比适配。")
        if outputs.get("subtitle_enabled") == "true":
            findings.append("字幕会先进入人工复核；批准前不会烧录进成片。")
        if not steps:
            findings.append(
                "当前素材未发现必须处理的音频或画幅问题，可按需添加增强步骤。"
            )
        return steps, findings

    # ------------------------------------------------------------------
    # 内容建议与渲染
    # ------------------------------------------------------------------
    def generate_content_advice(self, analysis_id: str) -> dict[str, Any]:
        task = self._get_workflow_task(analysis_id, "analysis")
        transcript_id = task.outputs.get("transcription_task_id", "")
        if not transcript_id:
            raise VideoEditorWorkflowError(
                "请先完成字幕识别和人工复核，再生成内容建议。"
            )
        revision = self.transcription_service.get_approved_revision(transcript_id)
        if revision is None:
            raise VideoEditorWorkflowError("字幕尚未确认成稿，暂不能生成内容建议。")
        capabilities = self.copywriting_service.capabilities()
        if not capabilities.get("enabled", False):
            return {
                "enabled": False,
                "message": "未配置真实大模型，已保留本地剪辑建议。",
                "advice": [],
            }
        source_text = "\n".join(
            f"[{segment.start:.1f}-{segment.end:.1f}] {segment.text}"
            for segment in revision.corrected_segments
        )
        advice = self.copywriting_service.engine.rewrite(
            source_text,
            platform=task.outputs.get("target_platform", "douyin"),
            style_prompt="只输出三条简短的短视频节奏优化建议：开场钩子、重复信息、结尾行动引导。不得编造事实。",
            target_length=320,
            tone="professional",
            variant_count=1,
        )[0]
        outputs = {**task.outputs, "content_advice": advice}
        self._update(task, outputs=outputs, stage="内容建议已生成")
        return {
            "enabled": True,
            "message": "内容建议已生成，仅供人工参考，不会自动删改视频。",
            "advice": [advice],
        }

    def create_edit_job(
        self,
        *,
        analysis_id: str,
        steps: list[dict[str, Any]],
        output_format: str = "mp4",
        output_resolution: str = "1080x1920",
        output_fps: int = 30,
        output_bitrate: str = "4M",
        subtitle_enabled: bool = True,
        publish_title: str | None = None,
    ) -> VideoEditTask:
        analysis = self._get_workflow_task(analysis_id, "analysis")
        if analysis.status != TaskStatus.SUCCEEDED:
            raise VideoEditorWorkflowError("请等待智能分析完成后再开始剪辑。")
        if subtitle_enabled:
            transcript_id = analysis.outputs.get("transcription_task_id", "")
            if not transcript_id:
                raise VideoEditorWorkflowError(
                    "本次剪辑要求字幕，请先完成字幕识别和人工复核。"
                )
            if self.transcription_service.get_approved_revision(transcript_id) is None:
                raise VideoEditorWorkflowError("字幕尚未完成复核确认，无法开始剪辑。")
        source = self.resolve_source(analysis.outputs.get("source_id", ""))
        validated_steps = [
            VideoEditStep(
                kind=VideoEditStepKind(item["kind"]),
                params=dict(item.get("params") or {}),
                order=index,
            )
            for index, item in enumerate(steps)
            if item.get("enabled", True)
        ]
        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id=f"edit-{uuid4().hex[:10]}",
            title=f"智能剪辑 · {source['file_name']}",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            source_video_path=source["_path"],
            edit_config=VideoEditConfig(
                steps=validated_steps,
                output_format=output_format,
                output_resolution=output_resolution,
                output_fps=output_fps,
                output_bitrate=output_bitrate,
            ),
            source_task_id=analysis.outputs.get("transcription_task_id") or None,
            source_avatar_task_id=source["source_task_id"]
            if source["source_type"] == "avatar"
            else None,
            stage="等待剪辑",
            is_mock=False,
            outputs={
                "workflow": "edit",
                "analysis_id": analysis_id,
                "source_id": source["source_id"],
                "subtitle_enabled": str(subtitle_enabled).lower(),
                "publish_title": (publish_title or "").strip(),
            },
        )
        self.repository.save_task(task)
        _WORKFLOW_EXECUTOR.submit(self._run_edit, task.task_id)
        return task

    def _run_edit(self, task_id: str) -> None:
        task = self._get_workflow_task(task_id, "edit")
        temp_srt: Path | None = None
        try:
            config = task.edit_config
            if task.outputs.get("subtitle_enabled") == "true":
                transcript_id = task.source_task_id or ""
                revision = self.transcription_service.get_approved_revision(
                    transcript_id
                )
                if revision is None:
                    raise VideoEditorWorkflowError(
                        "字幕尚未完成复核确认，无法开始剪辑。"
                    )
                temp_srt = (
                    self.video_editing_service.output_directory
                    / f"{task.task_id}.approved.srt"
                )
                temp_srt.write_bytes(
                    self.transcription_service.export_srt(revision.corrected_segments)
                )
                config = config.model_copy(
                    update={
                        "steps": [
                            *config.steps,
                            VideoEditStep(
                                kind=VideoEditStepKind.SUBTITLE,
                                params={"srt_path": str(temp_srt), "style": "default"},
                                order=len(config.steps),
                            ),
                        ]
                    }
                )
            self._update(task, status=TaskStatus.RUNNING, progress=10, stage="准备剪辑")
            result = self.video_editing_service.edit_video(
                source_video_path=task.source_video_path,
                edit_config=config,
                source_task_id=task.source_task_id,
                source_avatar_task_id=task.source_avatar_task_id,
                task_id=task.task_id,
            )
            # 底层服务会写入结果输出；补回工作流标识，供页面持续轮询。
            result = result.model_copy(
                update={"outputs": {**result.outputs, **task.outputs}}
            )
            self.repository.save_task(result)
            if result.status == TaskStatus.FAILED:
                self._update(
                    task,
                    status=TaskStatus.FAILED,
                    progress=result.progress,
                    stage=result.stage,
                    error_message=result.error_message or "剪辑失败",
                )
        except Exception as exc:
            self._update(
                task, status=TaskStatus.FAILED, stage="剪辑失败", error_message=str(exc)
            )
        finally:
            if temp_srt is not None:
                temp_srt.unlink(missing_ok=True)

    def get_job(self, task_id: str) -> dict[str, Any]:
        task = self.get_edit_task(task_id)
        return self._job_payload(task)

    def get_edit_task(self, task_id: str) -> VideoEditTask:
        task = self.repository.get_task(task_id)
        if not isinstance(task, VideoEditTask) or task.outputs.get("workflow") not in {
            "edit",
            "product_showcase",
            "local_preview_export",
        }:
            raise VideoEditorWorkflowError("智能剪辑任务不存在。")
        return task

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        tasks = [
            task
            for task in self.repository.list_tasks()
            if isinstance(task, VideoEditTask)
            and task.outputs.get("workflow")
            in {"edit", "product_showcase", "local_preview_export"}
        ]
        return [self._job_payload(task) for task in tasks[:limit]]

    # ------------------------------------------------------------------
    # 云端轻量剪辑
    # ------------------------------------------------------------------
    def _cloud_runtime(self):
        from src.adapters.video_editor_cloud import build_cloud_providers
        from src.services.video_editor_cloud import CloudEditorConfiguration

        configuration = (
            self._cloud_configuration_override or CloudEditorConfiguration.from_env()
        )
        providers = self._cloud_providers_override or build_cloud_providers(
            configuration
        )
        return configuration, providers

    def cloud_capabilities(self) -> dict[str, Any]:
        from src.services.video_editor_cloud import get_cloud_capability

        configuration, providers = self._cloud_runtime()
        remote_capability = getattr(providers, "capability", None)
        if callable(remote_capability):
            return remote_capability()
        return get_cloud_capability(configuration).model_dump(mode="json")

    def create_cloud_preflight(
        self,
        *,
        source_id: str,
        output_profile: str,
        target_platform: str,
    ) -> dict[str, Any]:
        """生成并持久化无云调用的 15 分钟费用报价。"""
        from src.services.video_editor_cloud import (
            CloudEditorError,
            create_cost_quote,
            get_cloud_capability,
        )

        source = self.resolve_source(source_id)
        try:
            duration_seconds = float(
                self._probe_media(Path(source["_path"])).get("duration_seconds") or 0
            )
        except VideoEditorWorkflowError:
            raise
        except Exception as exc:
            raise VideoEditorWorkflowError(
                "无法读取素材时长，暂不能生成费用报价。"
            ) from exc
        if duration_seconds <= 0:
            raise VideoEditorWorkflowError("素材时长无效，暂不能生成费用报价。")

        configuration, providers = self._cloud_runtime()
        try:
            quote = create_cost_quote(
                input_duration_seconds=duration_seconds,
                # The recommended smart opening adds at most 1.4 seconds to the
                # first render. Quote it up front instead of hiding the cost.
                output_duration_seconds=duration_seconds + 1.4,
                output_profile=output_profile,
                price_version=configuration.price_version,
                ttl_seconds=configuration.quote_ttl_seconds,
            )
        except CloudEditorError as exc:
            raise VideoEditorWorkflowError(str(exc)) from exc
        quote_payload = quote.model_dump(mode="json")
        self.repository.save_video_editor_quote(
            quote_id=quote.quote_id,
            source_id=source_id,
            output_profile=quote.output_profile.value,
            target_platform=target_platform,
            expires_at=quote.expires_at.isoformat(),
            created_at=quote.issued_at.isoformat(),
            payload=quote_payload,
        )
        remote_capability = getattr(providers, "capability", None)
        capability = (
            remote_capability()
            if callable(remote_capability)
            else get_cloud_capability(configuration).model_dump(mode="json")
        )
        blocking_reasons = (
            [
                "生产云配置不完整，请先补齐缺失配置后重新预检。",
            ]
            if capability["provider_mode"] == "aliyun" and not capability["live_ready"]
            else []
        )
        return {
            **quote_payload,
            **capability,
            "blocking_reasons": blocking_reasons,
        }

    @staticmethod
    def _cloud_output_settings(output_profile: str) -> dict[str, Any]:
        profiles = {
            "720p": {
                "output_profile": "720p",
                "output_resolution": "720x1280",
                "output_fps": 30,
                "output_bitrate": "2.5M",
            },
            "1080p": {
                "output_profile": "1080p",
                "output_resolution": "1080x1920",
                "output_fps": 30,
                "output_bitrate": "5M",
            },
        }
        try:
            return profiles[output_profile]
        except KeyError as exc:
            raise VideoEditorWorkflowError("输出档位仅支持 720p 或 1080p。") from exc

    @staticmethod
    def _cloud_request_hash(payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_cloud_operation_response(payload: dict[str, Any]) -> dict[str, Any]:
        """幂等记录不持久化短期 OSS 签名地址。"""
        safe = json.loads(json.dumps(payload, ensure_ascii=False))
        for item in safe.get("items", []):
            if isinstance(item, dict):
                item["result_media_url"] = None
        return safe

    def create_cloud_batch(
        self,
        *,
        source_ids: list[str],
        target_platform: str,
        output_profile: str,
        quote_id: str,
        billing_confirmation: dict[str, Any],
        idempotency_key: str,
        bgm_enabled: bool = True,
        bgm_id: str | None = None,
        bgm_volume: float = 0.2,
    ) -> dict[str, Any]:
        """创建单素材云批次；付费边界前验证报价、上限和幂等键。"""
        from src.services.video_editor_cloud import (
            CloudEditorError,
            CostQuote,
            validate_cost_quote,
        )

        unique_source_ids = list(dict.fromkeys(source_ids))
        if len(unique_source_ids) != 1:
            raise VideoEditorWorkflowError("云端轻量剪辑每次只允许提交一条素材。")
        source_id = unique_source_ids[0]
        source = self.resolve_source(source_id)
        settings = self._cloud_output_settings(output_profile)
        if not idempotency_key.strip():
            raise VideoEditorWorkflowError("云端剪辑必须提供 Idempotency-Key。")

        stored_quote = self.repository.get_video_editor_quote(quote_id)
        if stored_quote is None:
            raise VideoEditorWorkflowError("费用报价不存在，请重新预检。")
        if (
            stored_quote["source_id"] != source_id
            or stored_quote["output_profile"] != output_profile
            or stored_quote["target_platform"] != target_platform
        ):
            raise VideoEditorWorkflowError("素材、平台或清晰度已变化，请重新确认费用。")

        configuration, providers = self._cloud_runtime()
        try:
            quote = CostQuote.model_validate(stored_quote["payload"])
            validate_cost_quote(
                quote,
                quote_id,
                expected_price_version=configuration.price_version,
            )
        except (CloudEditorError, ValueError) as exc:
            raise VideoEditorWorkflowError(str(exc)) from exc

        if not billing_confirmation.get("confirmed", False):
            raise VideoEditorWorkflowError("请先明确确认本次云服务预计费用。")
        try:
            max_cost = Decimal(str(billing_confirmation.get("max_cost_cny")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise VideoEditorWorkflowError("费用上限无效，请重新确认。") from exc
        if max_cost < quote.estimated_max:
            raise VideoEditorWorkflowError("确认的费用上限低于当前报价，请重新确认。")

        capability = self.cloud_capabilities()
        if capability["provider_mode"] == "aliyun" and not capability["live_ready"]:
            missing = "、".join(capability["missing_configuration"])
            raise VideoEditorWorkflowError(
                f"云端剪辑配置不完整：{missing}。不会自动降级到沙箱。"
            )
        if bgm_enabled and bgm_id:
            self.resolve_bgm_asset(bgm_id)

        operation_payload = {
            "source_id": source_id,
            "target_platform": target_platform,
            "output_profile": output_profile,
            "quote_id": quote_id,
            "max_cost_cny": str(max_cost),
            "bgm_enabled": bgm_enabled,
            "bgm_id": bgm_id,
            "bgm_volume": bgm_volume,
        }
        request_hash = self._cloud_request_hash(operation_payload)
        now = datetime.now().astimezone()
        claimed = self.repository.claim_video_editor_operation(
            idempotency_key=idempotency_key,
            operation_type="create_cloud_batch",
            request_hash=request_hash,
            created_at=now.isoformat(),
        )
        if not claimed:
            existing = self.repository.get_video_editor_operation(idempotency_key)
            if existing is None or existing["request_hash"] != request_hash:
                raise VideoEditorWorkflowError(
                    "该 Idempotency-Key 已用于不同请求，请更换后重试。"
                )
            if existing.get("resource_id"):
                return self.get_batch(existing["resource_id"])
            raise VideoEditorWorkflowError(
                "同一请求正在处理或结果待确认；系统不会重复提交付费任务。"
            )

        item = VideoEditorBatchItem(
            source_id=source_id,
            title=source["title"],
            status="analyzing",
            provider_stage="uploading",
            is_mock=bool(capability["is_mock"]),
            publish_allowed=False,
        )
        batch = VideoEditorBatch(
            target_platform=target_platform,
            subtitle_enabled=True,
            subtitle_model="fun-asr",
            bgm_enabled=bgm_enabled,
            bgm_id=bgm_id,
            bgm_volume=bgm_volume,
            output_format="mp4",
            output_resolution=settings["output_resolution"],
            output_fps=settings["output_fps"],
            output_bitrate=settings["output_bitrate"],
            provider_mode=capability["provider_mode"],
            output_profile=output_profile,
            quote_id=quote_id,
            cost_quote=quote.model_dump(mode="json"),
            billing_confirmation={
                "confirmed": True,
                "max_cost_cny": str(max_cost),
                "price_version": quote.price_version,
            },
            billing_confirmed_at=now,
            idempotency_key=idempotency_key,
            is_mock=bool(capability["is_mock"]),
            items=[item],
        )

        # 演示提供方只能生成本地体验方案，不调用付费供应商，也绝不能扣积分。
        # 服务器控制层模式下，正式提供方才先在公司服务器扣费再上传素材。
        if bool(capability["is_mock"]):
            pass
        elif bool(getattr(providers, "billing_centrally_managed", False)):
            authorize_cost = getattr(providers, "authorize_cost", None)
            if not callable(authorize_cost):
                self.repository.delete_video_editor_operation(idempotency_key)
                raise VideoEditorWorkflowError("公司云端计费服务暂不可用。")
            try:
                authorization = authorize_cost(
                    batch_id=batch.batch_id,
                    quote_payload=quote.model_dump(mode="json"),
                    max_cost_cny=str(max_cost),
                )
                batch = batch.model_copy(
                    update={
                        "billing_confirmation": {
                            **batch.billing_confirmation,
                            "authority": "company_control_plane",
                            "charged_credits": str(
                                authorization.get("charged_credits", "")
                            ),
                        }
                    }
                )
            except Exception as exc:
                if not bool(getattr(exc, "outcome_unknown", False)):
                    self.repository.delete_video_editor_operation(idempotency_key)
                    raise VideoEditorWorkflowError(str(exc)) from exc
                item = item.model_copy(
                    update={
                        "status": "outcome_unknown",
                        "provider_stage": "billing_outcome_unknown",
                        "error_message": str(exc),
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                batch = batch.model_copy(update={"items": [item]})
                self.repository.save_video_editor_batch(batch)
                payload = self._batch_payload(batch)
                self.repository.complete_video_editor_operation(
                    idempotency_key=idempotency_key,
                    state="outcome_unknown",
                    resource_id=batch.batch_id,
                    response=self._safe_cloud_operation_response(payload),
                    error_message=str(exc),
                    updated_at=datetime.now().astimezone().isoformat(),
                )
                return payload
        else:
            try:
                self._debit_credits(
                    quote.estimated_total,
                    reason="云端剪辑成片费用",
                    ref_type="video_editor",
                    ref_id=quote_id,
                )
            except InsufficientCreditsError as exc:
                self.repository.delete_video_editor_operation(idempotency_key)
                raise VideoEditorWorkflowError(exc.message) from exc

        self.repository.save_video_editor_batch(batch)
        try:
            batch = self._submit_cloud_analysis(batch, item)
            payload = self._batch_payload(batch)
            self.repository.complete_video_editor_operation(
                idempotency_key=idempotency_key,
                state="completed",
                resource_id=batch.batch_id,
                response=self._safe_cloud_operation_response(payload),
                updated_at=datetime.now().astimezone().isoformat(),
            )
            return payload
        except Exception as exc:
            # 创建记录已经落库。任何提交边界不明都保持可查询状态，绝不重提。
            current = self.repository.get_video_editor_batch(batch.batch_id) or batch
            current_item = current.items[0]
            if current_item.status not in {"failed", "outcome_unknown"}:
                current_item = current_item.model_copy(
                    update={
                        "status": "outcome_unknown",
                        "provider_stage": "submission_outcome_unknown",
                        "error_message": str(exc),
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                current = self._replace_batch_item(current, current_item)
            payload = self._batch_payload(current)
            self.repository.complete_video_editor_operation(
                idempotency_key=idempotency_key,
                state="outcome_unknown",
                resource_id=current.batch_id,
                response=self._safe_cloud_operation_response(payload),
                error_message=str(exc),
                updated_at=datetime.now().astimezone().isoformat(),
            )
            return payload

    def _save_cloud_job_snapshot(
        self,
        *,
        batch: VideoEditorBatch,
        item: VideoEditorBatchItem,
        job_key: str,
        snapshot,
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now().astimezone().isoformat()
        self.repository.save_video_editor_cloud_job(
            job_key=job_key,
            batch_id=batch.batch_id,
            item_id=item.item_id,
            provider_stage=snapshot.provider_stage,
            provider_name=snapshot.provider_name,
            provider_job_id=snapshot.provider_job_id,
            status=snapshot.status.value,
            usage=snapshot.usage,
            payload=payload or snapshot.model_dump(mode="json"),
            next_poll_at=None,
            created_at=now,
            updated_at=now,
        )

    def _submit_cloud_analysis(
        self,
        batch: VideoEditorBatch,
        item: VideoEditorBatchItem,
    ) -> VideoEditorBatch:
        configuration, providers = self._cloud_runtime()
        source = self.resolve_source(item.source_id)
        media = self._probe_media(Path(source["_path"]))
        duration_seconds = float(media.get("duration_seconds") or 0)
        if duration_seconds <= 0:
            raise VideoEditorWorkflowError("素材时长无效，无法开始云端分析。")

        object_key = (
            f"video-editor-input/{batch.batch_id}/input/"
            f"{Path(source['file_name']).name}"
        )
        asset = providers.object_store.upload(
            source["_path"],
            object_key,
            media_type=source["media_type"],
        )
        now = datetime.now().astimezone().isoformat()
        self.repository.save_video_editor_cloud_job(
            job_key=f"{batch.batch_id}:{item.item_id}:upload",
            batch_id=batch.batch_id,
            item_id=item.item_id,
            provider_stage="upload_complete",
            provider_name=asset.provider_name,
            provider_job_id=None,
            status="succeeded",
            usage={},
            payload={"asset": asset.model_dump(mode="json")},
            next_poll_at=None,
            created_at=now,
            updated_at=now,
        )
        updated = item.model_copy(
            update={
                "provider_stage": "submitting_transcription",
                "provider_payload": {
                    **item.provider_payload,
                    "input_asset": asset.model_dump(mode="json"),
                    "media": media,
                },
                "updated_at": datetime.now().astimezone(),
            }
        )
        batch = self._replace_batch_item(batch, updated)

        snapshot = providers.asr.submit(asset, language_hints=("zh",))
        self._save_cloud_job_snapshot(
            batch=batch,
            item=updated,
            job_key=f"{batch.batch_id}:{item.item_id}:asr",
            snapshot=snapshot,
        )
        updated = updated.model_copy(
            update={
                "provider_stage": snapshot.provider_stage,
                "provider_job_ids": {
                    **updated.provider_job_ids,
                    "asr": snapshot.provider_job_id,
                },
                "actual_usage": {
                    **updated.actual_usage,
                    "asr": snapshot.usage,
                },
                "is_mock": snapshot.is_mock,
                "updated_at": datetime.now().astimezone(),
            }
        )
        batch = self._replace_batch_item(batch, updated)
        if snapshot.status.value == "succeeded":
            return self._complete_cloud_analysis(batch, updated, snapshot)
        if snapshot.status.value == "failed":
            updated = updated.model_copy(
                update={
                    "status": "failed",
                    "error_message": str(
                        snapshot.detail.get("message") or "Fun-ASR 转写失败。"
                    ),
                    "updated_at": datetime.now().astimezone(),
                }
            )
            return self._replace_batch_item(batch, updated)
        return batch

    @staticmethod
    def _normalize_cloud_transcript(
        detail: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, float]]]:
        normalized = detail.get("normalized_result")
        source = normalized if isinstance(normalized, dict) else detail
        transcript = str(source.get("transcript") or source.get("text") or "").strip()
        raw_segments = source.get("segments") or source.get("sentences") or []
        segments: list[dict[str, Any]] = []
        spoken_ranges: list[dict[str, float]] = []
        if isinstance(raw_segments, list):
            for raw in raw_segments:
                if not isinstance(raw, dict):
                    continue
                raw_start = raw.get("start", raw.get("begin_time", 0))
                raw_end = raw.get("end", raw.get("end_time", 0))
                try:
                    start = float(raw_start or 0)
                    end = float(raw_end or 0)
                except (TypeError, ValueError):
                    continue
                # Fun-ASR sentence timestamps commonly use milliseconds.
                if start > 10_000 or end > 10_000:
                    start /= 1000
                    end /= 1000
                text = str(raw.get("text") or raw.get("sentence") or "").strip()
                if end <= start:
                    continue
                segment = {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "text": text,
                }
                segments.append(segment)
                spoken_ranges.append({"start": segment["start"], "end": segment["end"]})
        if not transcript:
            transcript = "".join(
                segment["text"] for segment in segments if segment["text"]
            )
        return transcript, segments, spoken_ranges

    @staticmethod
    def _cloud_plan_with_steps(plan) -> dict[str, Any]:
        payload = plan.model_dump(mode="json")
        removed_seconds = round(
            sum(item.end - item.start for item in plan.remove_ranges),
            3,
        )
        labels = {
            "smart_opening": (
                "AI 智能开场",
                "从已审核文案提取短钩子，自动匹配克制的开场动画和音效。",
            ),
            "trim_silence": (
                "安全粗剪长停顿",
                "只处理可靠无语音间隔；两端保留口型缓冲，正式成片按同一方案拼接。",
            ),
            "vertical_fit": ("适配 9:16", "按已选择的输出档位统一画幅、帧率和码率。"),
            "subtitles": (
                "烧录确认字幕",
                "只使用本次人工确认的一套字幕，不会再次识别。",
            ),
            "title": ("添加标题", "标题仅使用候选或人工输入，不改写人声内容。"),
            "bgm": ("添加授权配乐", "只有确认权利的音乐才会进入正式渲染。"),
            "audio_mix": ("平衡人声与音乐", "保留原始人声并限制背景音乐音量。"),
        }
        payload["steps"] = [
            {
                "step_id": kind.value,
                "kind": kind.value,
                "label": labels[kind.value][0],
                "reason": labels[kind.value][1],
                "enabled": True,
                "required": kind.value in {"vertical_fit", "subtitles"},
                "estimated_removed_seconds": (
                    removed_seconds if kind.value == "trim_silence" else 0
                ),
                "params": (
                    {
                        "intervals": [
                            item.model_dump(mode="json") for item in plan.remove_ranges
                        ],
                        "estimated_output_seconds": plan.estimated_output_seconds,
                    }
                    if kind.value == "trim_silence"
                    else {}
                ),
            }
            for kind in plan.enabled_steps
        ]
        return payload

    def _complete_cloud_analysis(
        self,
        batch: VideoEditorBatch,
        item: VideoEditorBatchItem,
        snapshot,
    ) -> VideoEditorBatch:
        from src.services.video_editor_cloud import EditStepKind

        _, providers = self._cloud_runtime()
        cloud_transcript = providers.asr.fetch_result(snapshot)
        transcript = cloud_transcript.transcript
        segments = [
            segment.model_dump(mode="json") for segment in cloud_transcript.segments
        ]
        spoken_ranges = [
            item.model_dump(mode="json") for item in cloud_transcript.spoken_ranges
        ]
        duration_seconds = float(
            (item.provider_payload.get("media") or {}).get("duration_seconds") or 0
        )
        if batch.provider_mode == "sandbox":
            transcript_for_plan = transcript or item.title
        else:
            transcript_for_plan = transcript
            if not transcript and not segments:
                raise VideoEditorWorkflowError(
                    "Fun-ASR 已完成，但尚未取得可复核的转写明细；不会进入渲染。"
                )
        plan = providers.edit_plan.create_plan(
            transcript_for_plan,
            spoken_ranges,
            duration_seconds,
            segments,
        )
        titles = list(plan.title_candidates) or [item.title[:40]]
        selected_bgm_id: str | None = None
        bgm_reason: str | None = None
        if batch.bgm_enabled:
            bgm, bgm_reason = self._recommend_bgm_asset(
                {
                    "transcript": transcript_for_plan,
                    "title_candidates": titles,
                    "media": item.provider_payload.get("media") or {},
                },
                item.title,
            )
            if bgm is not None:
                selected_bgm_id = bgm["asset_id"]
                if EditStepKind.BGM not in plan.enabled_steps:
                    plan = plan.model_copy(
                        update={
                            "enabled_steps": [
                                *plan.enabled_steps,
                                EditStepKind.BGM,
                            ]
                        }
                    )
        else:
            bgm_reason = "已关闭自动配乐，保持素材原声。"
        plan_payload = self._cloud_plan_with_steps(plan)
        updated = item.model_copy(
            update={
                "status": "awaiting_subtitle_review",
                "provider_stage": "awaiting_human_review",
                "subtitle_segments": segments,
                "edit_plan": plan_payload,
                "title_candidates": titles,
                "selected_title": item.selected_title or titles[0],
                "selected_bgm_id": selected_bgm_id,
                "bgm_reason": bgm_reason,
                "actual_usage": {
                    **item.actual_usage,
                    "planning": plan.usage,
                },
                "error_message": None,
                "updated_at": datetime.now().astimezone(),
            }
        )
        return self._replace_batch_item(batch, updated)

    # ------------------------------------------------------------------
    # 自动批次
    # ------------------------------------------------------------------
    def create_batch(
        self,
        *,
        source_ids: list[str],
        target_platform: str,
        subtitle_enabled: bool,
        subtitle_model: str,
        steps: list[dict[str, Any]],
        output_format: str,
        output_resolution: str,
        output_fps: int,
        output_bitrate: str,
        bgm_enabled: bool = True,
        bgm_id: str | None = None,
        bgm_volume: float = 0.24,
    ) -> dict[str, Any]:
        unique_source_ids = list(dict.fromkeys(source_ids))
        if not unique_source_ids:
            raise VideoEditorWorkflowError("请至少选择一条素材。")
        if len(unique_source_ids) > _MAX_BATCH_ITEMS:
            raise VideoEditorWorkflowError(f"单次最多处理 {_MAX_BATCH_ITEMS} 条素材。")

        selected_bgm_id = bgm_id
        if bgm_enabled and selected_bgm_id:
            self.resolve_bgm_asset(selected_bgm_id)

        items: list[VideoEditorBatchItem] = []
        for source_id in unique_source_ids:
            source = self.resolve_source(source_id)
            items.append(
                VideoEditorBatchItem(
                    source_id=source_id, title=source["title"], status="analyzing"
                )
            )
        batch = VideoEditorBatch(
            target_platform=target_platform,
            subtitle_enabled=subtitle_enabled,
            subtitle_model=subtitle_model,
            bgm_enabled=bgm_enabled,
            bgm_id=selected_bgm_id,
            bgm_volume=bgm_volume,
            steps=steps,
            output_format=output_format,
            output_resolution=output_resolution,
            output_fps=output_fps,
            output_bitrate=output_bitrate,
            items=items,
        )

        updated_items: list[VideoEditorBatchItem] = []
        for item in batch.items:
            analysis = self.create_analysis(
                source_id=item.source_id,
                target_platform=target_platform,
                subtitle_enabled=subtitle_enabled,
                subtitle_model=subtitle_model,
            )
            updated_items.append(
                item.model_copy(update={"analysis_id": analysis.task_id})
            )
        batch = batch.model_copy(update={"items": updated_items})
        self.repository.save_video_editor_batch(batch)
        return self._batch_payload(batch)

    def list_batches(self, limit: int = 20) -> list[dict[str, Any]]:
        batches = self.repository.list_video_editor_batches(limit=limit)
        return [self._batch_payload(self._sync_batch(batch)) for batch in batches]

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.repository.get_video_editor_batch(batch_id)
        if batch is None:
            raise VideoEditorWorkflowError("智能剪辑批次不存在。")
        return self._batch_payload(self._sync_batch(batch))

    def prepare_batch_item_download(
        self,
        batch_id: str,
        item_id: str,
    ) -> dict[str, str]:
        """为已完成的真实云成片生成一次性下载信息，不改变审核或发布状态。"""
        batch = self._require_batch(batch_id)
        item = next(
            (entry for entry in batch.items if entry.item_id == item_id),
            None,
        )
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        if batch.provider_mode != "aliyun" or batch.is_mock or item.is_mock:
            raise VideoEditorWorkflowError("体验任务没有真实成片可下载。")
        if item.status not in {
            "awaiting_output_confirmation",
            "ready_to_publish",
        }:
            raise VideoEditorWorkflowError("真实成片尚未生成，暂时不能下载。")
        try:
            media_url = self._cloud_preview_url(item)
        except Exception as exc:
            raise VideoEditorWorkflowError(
                "云成片下载地址暂时不可用，请检查云配置后重试。"
            ) from exc
        if not media_url:
            raise VideoEditorWorkflowError("真实成片文件不存在，暂时不能下载。")

        title = re.sub(
            r'[\\/:*?"<>|]+',
            "",
            item.selected_title or item.title or "剪辑成片",
        ).strip()
        return {
            "media_url": media_url,
            "filename": f"{title[:48] or '剪辑成片'}.mp4",
        }

    def create_local_preview_export(
        self,
        batch_id: str,
        item_id: str,
        *,
        run_inline: bool = False,
    ) -> dict[str, Any]:
        """把当前已审核的浏览器方案免费烧录为本机 MP4。"""
        batch = self._sync_batch(self._require_batch(batch_id))
        item = next(
            (entry for entry in batch.items if entry.item_id == item_id),
            None,
        )
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        if batch.is_mock or item.is_mock:
            raise VideoEditorWorkflowError("体验任务没有真实媒体，不能生成下载文件。")

        if item.edit_task_id:
            existing = self.repository.get_task(item.edit_task_id)
            if (
                isinstance(existing, VideoEditTask)
                and existing.outputs.get("workflow") == "local_preview_export"
                and existing.outputs.get("style_version")
                == _LOCAL_PREVIEW_EXPORT_STYLE_VERSION
                and existing.status
                in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.SUCCEEDED}
                and (
                    existing.status != TaskStatus.SUCCEEDED
                    or (existing.result_path and Path(existing.result_path).is_file())
                )
            ):
                return self._batch_payload(batch)

        cached = self._cached_source_context(item.source_id)
        segments = [
            dict(segment)
            for segment in (
                item.subtitle_segments or list(cached.get("subtitle_segments") or [])
            )
        ]
        cached_review = dict(cached.get("review_snapshot") or {})
        current_reviewed = bool(
            item.review_confirmed_at or item.review_snapshot.get("confirmed")
        )
        if not segments or not (current_reviewed or cached_review.get("confirmed")):
            raise VideoEditorWorkflowError(
                "当前只有估算字幕，没有可复用的人工确认字幕，暂不能生成成片。"
            )

        source = self.resolve_source(item.source_id)
        media = self._probe_media(Path(source["_path"]))
        segments = self._validated_review_segments(
            segments,
            duration_seconds=float(media["duration_seconds"]),
        )
        title = (
            item.selected_title
            or str(cached.get("selected_title") or "").strip()
            or item.title
        ).strip()
        if not title:
            raise VideoEditorWorkflowError("当前方案缺少标题，暂不能生成成片。")

        edit_plan = dict(item.edit_plan or cached.get("edit_plan") or {})
        if edit_plan.get("remove_ranges"):
            raise VideoEditorWorkflowError(
                "当前方案包含真实粗剪区间，本机免费导出暂不支持；请先关闭粗剪或使用云端出片。"
            )
        enabled_steps = list(
            item.enabled_plan_step_ids
            or cached.get("enabled_plan_step_ids")
            or ["vertical_fit", "subtitles", "title"]
        )
        from src.services.video_editor_cloud import build_smart_opening

        transcript = "".join(str(segment.get("text") or "") for segment in segments)
        existing_opening = edit_plan.get("smart_opening") or {}
        opening = build_smart_opening(
            transcript,
            [title],
            preferred_style=existing_opening.get("style_id"),
        )
        if opening is not None:
            edit_plan["smart_opening"] = opening.model_dump(mode="json")
            if "smart_opening" not in enabled_steps:
                enabled_steps.insert(0, "smart_opening")
        selected_bgm_id = (
            item.selected_bgm_id
            or str(cached.get("selected_bgm_id") or "").strip()
            or None
        )
        if "bgm" not in enabled_steps:
            selected_bgm_id = None

        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id=f"edit-local-{uuid4().hex[:10]}",
            title=f"本机导出 · {title}",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            source_video_path=source["_path"],
            edit_config=VideoEditConfig(
                output_format="mp4",
                output_resolution=batch.output_resolution,
                output_fps=batch.output_fps,
                output_bitrate=batch.output_bitrate,
            ),
            source_avatar_task_id=(
                source["source_task_id"] if source["source_type"] == "avatar" else None
            ),
            stage="等待本机免费导出",
            is_mock=False,
            outputs={
                "workflow": "local_preview_export",
                "batch_id": batch.batch_id,
                "item_id": item.item_id,
                "source_id": item.source_id,
                "publish_title": title,
                "output_profile": batch.output_profile or "720p",
                "style_version": _LOCAL_PREVIEW_EXPORT_STYLE_VERSION,
                "playback_rate": str(_LOCAL_PREVIEW_PLAYBACK_RATE),
                "subtitle_segments_json": json.dumps(
                    segments,
                    ensure_ascii=False,
                ),
                "edit_plan_json": json.dumps(edit_plan, ensure_ascii=False),
                "smart_opening_json": json.dumps(
                    edit_plan.get("smart_opening")
                    if "smart_opening" in enabled_steps
                    else {},
                    ensure_ascii=False,
                ),
                "bgm_id": selected_bgm_id or "",
            },
        )
        self.repository.save_task(task)
        updated = item.model_copy(
            update={
                "status": "rendering",
                "edit_task_id": task.task_id,
                "selected_title": title,
                "selected_bgm_id": selected_bgm_id,
                "bgm_reason": item.bgm_reason or cached.get("bgm_reason"),
                "edit_plan": edit_plan,
                "enabled_plan_step_ids": enabled_steps,
                "provider_stage": "local_export_rendering",
                "provider_payload": {
                    **item.provider_payload,
                    "local_export": {
                        "cost_cny": "0",
                        "reused_approved_subtitles": True,
                        "playback_rate": _LOCAL_PREVIEW_PLAYBACK_RATE,
                        "style_version": _LOCAL_PREVIEW_EXPORT_STYLE_VERSION,
                        "smart_opening": edit_plan.get("smart_opening"),
                    },
                },
                "publish_allowed": False,
                "error_message": None,
                "updated_at": now,
            }
        )
        batch = self._replace_batch_item(batch, updated)
        if run_inline:
            self._run_local_preview_export(task.task_id)
            return self._batch_payload(self._require_batch(batch.batch_id))
        _WORKFLOW_EXECUTOR.submit(self._run_local_preview_export, task.task_id)
        return self._batch_payload(batch)

    def render_production_export(
        self,
        *,
        avatar_task: AvatarTask,
        script_text: str,
        publish_title: str,
        subtitle_segments: Sequence[Mapping[str, Any]] | None = None,
    ) -> VideoEditTask:
        """Render an approved production avatar with the current local template.

        Production already paid for and downloaded the avatar before it reaches
        this boundary.  Reuse that exact media and script, and run the same
        reviewed local renderer used by the intelligent editor.  This keeps the
        automatic path on the single-line, punctuation-free business template
        instead of silently falling back to the legacy subtitle adapter.
        """

        if avatar_task.status != TaskStatus.SUCCEEDED or not avatar_task.result_path:
            raise VideoEditorWorkflowError("数字人成片尚未就绪，不能开始智能剪辑。")
        source_path = Path(avatar_task.result_path)
        if not source_path.is_file():
            raise VideoEditorWorkflowError("数字人成片文件不存在，不能开始智能剪辑。")
        script = script_text.strip()
        title = publish_title.strip()
        if not script:
            raise VideoEditorWorkflowError("已确认口播文案为空，不能开始智能剪辑。")
        if not title:
            raise VideoEditorWorkflowError("成片标题为空，不能开始智能剪辑。")

        # The production draft can still carry a stale creative-plan hook.
        # Anchor the on-screen title to the first approved spoken clause so it
        # is a complete sentence fragment and matches the actual video.
        title = self._script_topic_title(script, title)

        from src.services.video_editor_cloud import build_smart_opening

        approved_opening = build_smart_opening(script, [title])
        if approved_opening is not None:
            title = approved_opening.hook_text

        media = self._probe_media(source_path)
        timing_source = "approved_avatar_script_estimate"
        if subtitle_segments is not None:
            segments = self._validated_review_segments(
                [dict(segment) for segment in subtitle_segments],
                duration_seconds=float(media["duration_seconds"]),
            )
            approved_text = re.sub(r"[\W_]+", "", script, flags=re.UNICODE)
            timed_text = re.sub(
                r"[\W_]+",
                "",
                "".join(str(segment.get("text") or "") for segment in segments),
                flags=re.UNICODE,
            )
            if timed_text != approved_text:
                raise VideoEditorWorkflowError(
                    "真实字幕时间轴与已确认口播文案不一致，已停止避免音画错配。"
                )
            timing_source = "approved_avatar_asr"
        else:
            segments = self._estimated_script_segments(
                script,
                float(media["duration_seconds"]),
            )
        if not segments:
            raise VideoEditorWorkflowError("无法生成字幕时间轴，不能开始智能剪辑。")

        now = datetime.now().astimezone()
        source_id = f"avatar:{avatar_task.task_id}"
        item = VideoEditorBatchItem(
            source_id=source_id,
            title=title,
            status="outcome_unknown",
            selected_title=title,
            subtitle_segments=segments,
            review_snapshot={"confirmed": True, "source": timing_source},
            review_confirmed_at=now,
            enabled_plan_step_ids=["vertical_fit", "subtitles", "title"],
            edit_plan={"remove_ranges": []},
            provider_stage="production_local_export_ready",
            publish_allowed=False,
            updated_at=now,
        )
        batch = VideoEditorBatch(
            target_platform="douyin",
            subtitle_enabled=True,
            bgm_enabled=False,
            output_format="mp4",
            output_resolution="720x1280",
            output_fps=30,
            output_bitrate="2.5M",
            provider_mode="local",
            output_profile="720p",
            is_mock=False,
            items=[item],
            created_at=now,
            updated_at=now,
        )
        self.repository.save_video_editor_batch(batch)
        payload = self.create_local_preview_export(
            batch.batch_id,
            item.item_id,
            run_inline=True,
        )
        completed_item = payload["items"][0]
        edit_task = self.repository.get_task(completed_item.get("edit_task_id") or "")
        if not isinstance(edit_task, VideoEditTask):
            raise VideoEditorWorkflowError("智能剪辑没有生成可核验的任务记录。")
        return edit_task

    @staticmethod
    def _ffmpeg_filter_path(path: Path) -> str:
        return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

    @staticmethod
    def _render_smart_opening_clip(
        opening: dict[str, Any],
        *,
        output_profile: str,
        fps: int,
        output_path: Path,
        temp_dir: Path,
    ) -> float:
        """Render one short, full-frame opening from the approved template set."""

        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageFont
        except ImportError as exc:
            raise VideoEditorWorkflowError(
                "缺少开场动画排版组件 Pillow，暂不能生成开场。"
            ) from exc
        from src.services.video_editor_cloud import (
            BRAND_TITLE_FONT_PATH,
            SmartOpening,
            visual_style_spec,
        )

        approved = SmartOpening.model_validate(opening)
        spec = visual_style_spec(output_profile)
        width = int(spec["canvas"]["width"])
        height = int(spec["canvas"]["height"])
        duration = float(approved.duration_seconds)
        frame_count = max(2, round(duration * fps))
        frames_dir = temp_dir / "opening-frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        base = Image.new("RGB", (width, height), "#070A12")
        base_draw = ImageDraw.Draw(base)
        for y in range(height):
            ratio = y / max(1, height - 1)
            base_draw.line(
                (0, y, width, y),
                fill=(
                    7 + round(7 * ratio),
                    10 + round(9 * ratio),
                    18 + round(14 * ratio),
                ),
            )
        grid_gap = max(48, round(width * 0.09))
        grid_color = (69, 83, 112)
        for x in range(0, width, grid_gap):
            base_draw.line((x, 0, x, height), fill=grid_color, width=1)
        for y in range(0, height, grid_gap):
            base_draw.line((0, y, width, y), fill=grid_color, width=1)
        vignette = Image.new("L", (width, height), 0)
        vignette_draw = ImageDraw.Draw(vignette)
        vignette_draw.ellipse(
            (
                -round(width * 0.35),
                round(height * 0.18),
                round(width * 1.35),
                round(height * 0.82),
            ),
            fill=210,
        )
        vignette = vignette.filter(ImageFilter.GaussianBlur(round(width * 0.18)))
        light = Image.new("RGB", (width, height), "#17213A")
        base = Image.composite(light, base, vignette)

        font_size = round(width * (0.105 if len(approved.hook_text) <= 8 else 0.082))
        font = ImageFont.truetype(str(BRAND_TITLE_FONT_PATH), font_size)
        chars_per_line = 7 if len(approved.hook_text) > 9 else 9
        lines = [
            approved.hook_text[index : index + chars_per_line]
            for index in range(0, len(approved.hook_text), chars_per_line)
        ][:2]
        line_gap = round(font_size * 0.22)
        text_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_layer)
        boxes = [
            text_draw.textbbox(
                (0, 0), line, font=font, stroke_width=max(1, width // 360)
            )
            for line in lines
        ]
        block_height = sum(box[3] - box[1] for box in boxes) + line_gap * max(
            0, len(lines) - 1
        )
        y = round(height * 0.46 - block_height / 2)
        number_pattern = re.compile(r"\d+(?:\.\d+)?(?:%|％|元|块|折)?")
        for line, box in zip(lines, boxes, strict=False):
            line_width = box[2] - box[0]
            x = round((width - line_width) / 2)
            text_draw.text(
                (x, y),
                line,
                font=font,
                fill="#F8FAFC",
                stroke_width=max(1, width // 360),
                stroke_fill=(0, 0, 0, 150),
            )
            if approved.style_id == "number_focus":
                match = number_pattern.search(line)
                if match:
                    prefix_width = text_draw.textlength(
                        line[: match.start()], font=font
                    )
                    text_draw.text(
                        (x + prefix_width, y),
                        match.group(),
                        font=font,
                        fill="#FFE16A",
                        stroke_width=max(1, width // 360),
                        stroke_fill=(0, 0, 0, 150),
                    )
            y += box[3] - box[1] + line_gap

        for frame_index in range(frame_count):
            progress = frame_index / max(1, frame_count - 1)
            eased = 1 - (1 - min(progress / 0.72, 1)) ** 3
            fade_out = 1 if progress < 0.82 else max(0, (1 - progress) / 0.18)
            animated = text_layer
            if approved.style_id == "story_unfold":
                bounce = 1 + 0.06 * (1 - eased) * (1 if progress < 0.45 else -0.35)
                reveal = max(0.08, eased)
                resized = animated.resize(
                    (width, max(1, round(height * reveal * bounce))),
                    Image.Resampling.LANCZOS,
                )
                stage = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                stage.alpha_composite(
                    resized, (0, round((height - resized.height) / 2))
                )
                animated = stage
            else:
                start_scale = 1.42 if approved.style_id == "number_focus" else 1.28
                scale = start_scale - (start_scale - 1) * eased
                scaled = animated.resize(
                    (round(width * scale), round(height * scale)),
                    Image.Resampling.LANCZOS,
                )
                stage = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                shift_x = round(width * 0.08 * max(0, (progress - 0.82) / 0.18))
                stage.alpha_composite(
                    scaled,
                    (
                        round((width - scaled.width) / 2) + shift_x,
                        round((height - scaled.height) / 2),
                    ),
                )
                blur = round(
                    (1 - eased) * (12 if approved.style_id == "number_focus" else 8)
                )
                animated = (
                    stage.filter(ImageFilter.GaussianBlur(blur)) if blur else stage
                )
            if approved.style_id == "number_focus":
                glow = animated.filter(ImageFilter.GaussianBlur(max(2, width // 120)))
                glow.putalpha(
                    glow.getchannel("A").point(lambda value: round(value * 0.32))
                )
                frame = base.convert("RGBA")
                frame.alpha_composite(glow)
            else:
                frame = base.convert("RGBA")
            if fade_out < 1:
                animated = animated.copy()
                animated.putalpha(
                    animated.getchannel("A").point(
                        lambda value: round(value * fade_out)
                    )
                )
            frame.alpha_composite(animated)
            frame.convert("RGB").save(
                frames_dir / f"{frame_index:04d}.jpg",
                quality=90,
                optimize=True,
            )

        sound_filter = {
            "soft_whoosh": (
                f"anoisesrc=color=pink:sample_rate=48000:duration={duration:.3f}:amplitude=0.05,"
                "highpass=f=420,lowpass=f=4200,afade=t=in:st=0:d=0.06,"
                f"afade=t=out:st=0.55:d={max(0.2, duration - 0.55):.3f}"
            ),
            "soft_page_turn": (
                f"anoisesrc=color=white:sample_rate=48000:duration={duration:.3f}:amplitude=0.025,"
                "lowpass=f=2600,afade=t=in:st=0:d=0.04,"
                f"afade=t=out:st=0.32:d={max(0.2, duration - 0.32):.3f}"
            ),
            "soft_chime": (
                "sine=frequency=880:sample_rate=48000:duration=0.34,volume=0.045,"
                "afade=t=out:st=0.08:d=0.26,"
                f"apad=whole_dur={duration:.3f}"
            ),
        }[approved.sound_effect_id]
        command = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-framerate",
            str(fps),
            "-start_number",
            "0",
            "-i",
            str(frames_dir / "%04d.jpg"),
            "-f",
            "lavfi",
            "-i",
            sound_filter,
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=180, check=False
        )
        if (
            result.returncode != 0
            or not output_path.is_file()
            or not output_path.stat().st_size
        ):
            detail = (result.stderr or "开场动画没有生成文件。").strip()
            raise VideoEditorWorkflowError(f"智能开场生成失败：{detail[-500:]}")
        return duration

    @staticmethod
    def _prepend_opening_clip(
        opening_path: Path,
        body_path: Path,
        output_path: Path,
        *,
        bitrate: str,
    ) -> None:
        command = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-i",
            str(opening_path),
            "-i",
            str(body_path),
            "-filter_complex",
            "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout]",
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            bitrate,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            output_path.as_posix(),
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=15 * 60, check=False
        )
        if (
            result.returncode != 0
            or not output_path.is_file()
            or not output_path.stat().st_size
        ):
            detail = (result.stderr or "开场与正片拼接失败。").strip()
            raise VideoEditorWorkflowError(f"智能开场拼接失败：{detail[-500:]}")

    def _run_local_preview_export(self, task_id: str) -> None:
        task = self._get_workflow_task(task_id, "local_preview_export")
        output_dir = self.video_editing_service.output_directory
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{task.task_id}.mp4"
        temp_dir = Path(tempfile.mkdtemp(prefix=f"{task.task_id}-"))
        try:
            if not shutil.which("ffmpeg"):
                raise VideoEditorWorkflowError(
                    "未检测到 FFmpeg，无法在本机生成下载文件。"
                )
            source_path = Path(task.source_video_path)
            media = self._probe_media(source_path)
            if not media["has_audio"]:
                raise VideoEditorWorkflowError(
                    "当前原片没有可用人声轨道，暂不能按口播方案导出。"
                )
            profile = task.outputs.get("output_profile") or "720p"
            playback_rate = float(
                task.outputs.get("playback_rate") or _LOCAL_PREVIEW_PLAYBACK_RATE
            )
            segments = json.loads(task.outputs.get("subtitle_segments_json") or "[]")
            rendered_segments = [
                {
                    **segment,
                    "start": round(
                        float(segment.get("start", 0)) / playback_rate,
                        3,
                    ),
                    "end": round(
                        float(segment.get("end", 0)) / playback_rate,
                        3,
                    ),
                }
                for segment in segments
            ]
            edit_plan = json.loads(task.outputs.get("edit_plan_json") or "{}")
            smart_opening = json.loads(task.outputs.get("smart_opening_json") or "{}")
            rendered_spoken_ranges = [
                {
                    "start": round(
                        float(item.get("start", 0)) / playback_rate,
                        3,
                    ),
                    "end": round(
                        float(item.get("end", 0)) / playback_rate,
                        3,
                    ),
                }
                for item in edit_plan.get("spoken_ranges") or []
                if isinstance(item, dict)
            ]
            ass_path = temp_dir / "approved.ass"
            title_path = temp_dir / "title.png"
            ass_path.write_bytes(
                self._review_ass_bytes(
                    rendered_segments,
                    output_profile=profile,
                    caption_groups=edit_plan.get("caption_groups"),
                    caption_emphasis=edit_plan.get("caption_emphasis"),
                    spoken_ranges=rendered_spoken_ranges,
                )
            )
            title_path.write_bytes(
                self._review_title_png_bytes(
                    task.outputs.get("publish_title") or task.title,
                    output_profile=profile,
                )
            )

            from src.services.video_editor_cloud import visual_style_spec

            spec = visual_style_spec(profile)
            canvas = spec["canvas"]
            title_style = spec["title"]
            width = int(canvas["width"])
            height = int(canvas["height"])
            fps = task.edit_config.output_fps
            body_path = temp_dir / "body.mp4" if smart_opening else output_path
            subtitle_filter = self._ffmpeg_filter_path(ass_path)
            filter_parts = [
                (
                    f"[0:v]scale={width}:{height}:"
                    "force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:"
                    "color=0x101827,setsar=1,"
                    f"setpts=PTS/{playback_rate:.3f},fps={fps},"
                    f"subtitles='{subtitle_filter}'[captioned]"
                ),
                "[1:v]format=rgba[title]",
                (
                    "[captioned][title]overlay="
                    f"{int(title_style['safe_left'])}:"
                    f"{int(title_style['safe_top'])}:"
                    "enable='between(t,0,"
                    f"{float(title_style['visible_seconds']):.3f})':"
                    "eof_action=pass[vout]"
                ),
            ]
            command = [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-i",
                str(source_path),
                "-loop",
                "1",
                "-framerate",
                str(fps),
                "-i",
                str(title_path),
            ]
            bgm_id = task.outputs.get("bgm_id") or ""
            filter_parts.append(
                f"[0:a]atempo={playback_rate:.3f},aresample=async=1:first_pts=0[voice]"
            )
            if bgm_id:
                bgm = self.resolve_bgm_asset(bgm_id)
                command.extend(["-stream_loop", "-1", "-i", str(bgm["_path"])])
                filter_parts.extend(
                    [
                        (
                            "[2:a]volume=0.18,"
                            "afade=t=in:st=0:d=0.5,"
                            "aresample=async=1:first_pts=0[bgm]"
                        ),
                        (
                            "[voice][bgm]amix=inputs=2:duration=first:"
                            "dropout_transition=2:normalize=0[aout]"
                        ),
                    ]
                )
            command.extend(
                [
                    "-filter_complex",
                    ";".join(filter_parts),
                    "-map",
                    "[vout]",
                    "-map",
                    "[aout]" if bgm_id else "[voice]",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-b:v",
                    task.edit_config.output_bitrate,
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(body_path),
                ]
            )
            task = self._update(
                task,
                status=TaskStatus.RUNNING,
                progress=15,
                stage="正在写入标题、字幕和配乐",
            )
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=15 * 60,
                check=False,
            )
            if (
                result.returncode != 0
                or not body_path.is_file()
                or body_path.stat().st_size == 0
            ):
                detail = (result.stderr or "本机编码没有生成文件。").strip()
                raise VideoEditorWorkflowError(f"本机成片生成失败：{detail[-600:]}")
            opening_duration = 0.0
            if smart_opening:
                task = self._update(
                    task,
                    status=TaskStatus.RUNNING,
                    progress=72,
                    stage="正在生成智能开场并保持音画同步",
                )
                opening_path = temp_dir / "opening.mp4"
                opening_duration = self._render_smart_opening_clip(
                    smart_opening,
                    output_profile=profile,
                    fps=fps,
                    output_path=opening_path,
                    temp_dir=temp_dir,
                )
                self._prepend_opening_clip(
                    opening_path,
                    body_path,
                    output_path,
                    bitrate=task.edit_config.output_bitrate,
                )
            output_media = self._probe_media(output_path)
            if output_media["width"] != width or output_media["height"] != height:
                raise VideoEditorWorkflowError("本机成片分辨率校验失败。")
            expected_duration = (
                float(media["duration_seconds"]) / playback_rate + opening_duration
            )
            if abs(float(output_media["duration_seconds"]) - expected_duration) > 1:
                raise VideoEditorWorkflowError("本机成片语速与时长校验失败。")

            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "本机成片已生成，可直接下载",
                    "result_path": str(output_path),
                    "result_mime": "video/mp4",
                    "result_size_bytes": output_path.stat().st_size,
                    "error_message": None,
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self.repository.save_task(task)
            latest = self._require_batch(task.outputs["batch_id"])
            latest_item = next(
                entry
                for entry in latest.items
                if entry.item_id == task.outputs["item_id"]
            )
            completed = latest_item.model_copy(
                update={
                    "status": "awaiting_output_confirmation",
                    "provider_stage": "local_export_complete",
                    "publish_allowed": True,
                    "error_message": None,
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self._replace_batch_item(latest, completed)
        except Exception as exc:
            output_path.unlink(missing_ok=True)
            self._update(
                task,
                status=TaskStatus.FAILED,
                progress=0,
                stage="本机成片生成失败",
                error_message=str(exc),
            )
            try:
                latest = self._require_batch(task.outputs["batch_id"])
                latest_item = next(
                    entry
                    for entry in latest.items
                    if entry.item_id == task.outputs["item_id"]
                )
                failed = latest_item.model_copy(
                    update={
                        "status": "failed",
                        "provider_stage": "local_export_failed",
                        "publish_allowed": False,
                        "error_message": str(exc),
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                self._replace_batch_item(latest, failed)
            except Exception:
                pass
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _validated_review_segments(
        segments: list[dict[str, Any]],
        *,
        duration_seconds: float,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        previous_end = 0.0
        for raw in segments:
            try:
                start = round(float(raw.get("start", 0)), 3)
                end = round(float(raw.get("end", 0)), 3)
            except (TypeError, ValueError) as exc:
                raise VideoEditorWorkflowError("字幕时间戳格式无效。") from exc
            text = str(raw.get("text") or "").strip()
            if start < 0 or end <= start or end > duration_seconds:
                raise VideoEditorWorkflowError("字幕时间戳超出素材范围。")
            if start < previous_end:
                raise VideoEditorWorkflowError("字幕时间段不能互相重叠。")
            raw_terms = raw.get("emphasis_terms") or []
            if isinstance(raw_terms, (str, bytes)) or not isinstance(raw_terms, list):
                raise VideoEditorWorkflowError("字幕强调词格式无效。")
            if len(raw_terms) > 1:
                raise VideoEditorWorkflowError("每条字幕最多设置一个强调词。")
            normalized_segment = {"start": start, "end": end, "text": text}
            if raw_terms:
                emphasis = re.sub(r"\s+", "", str(raw_terms[0]))
                clean_text = re.sub(r"\s+", "", text)
                if not emphasis or len(emphasis) > 6 or emphasis not in clean_text:
                    raise VideoEditorWorkflowError(
                        "强调词必须是当前字幕中的连续原文，且不超过 6 个字。"
                    )
                normalized_segment["emphasis_terms"] = [emphasis]
                emphasis_kind = str(raw.get("emphasis_kind") or "keyword")
                if emphasis_kind not in {
                    "number",
                    "benefit",
                    "warning",
                    "keyword",
                }:
                    raise VideoEditorWorkflowError("字幕强调样式无效。")
                normalized_segment["emphasis_kind"] = emphasis_kind
            normalized.append(normalized_segment)
            previous_end = end
        return normalized

    @staticmethod
    def _srt_timestamp(seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, milliseconds = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"

    @staticmethod
    def _review_ass_bytes(
        segments: list[dict[str, Any]],
        *,
        output_profile: str,
        caption_groups: object = None,
        caption_emphasis: object = None,
        spoken_ranges: object = None,
        time_offset_seconds: float = 0,
    ) -> bytes:
        from src.services.video_editor_cloud import build_business_talking_head_ass

        return build_business_talking_head_ass(
            segments,
            title="",
            output_profile=output_profile,
            caption_groups=caption_groups,
            caption_emphasis=caption_emphasis,
            spoken_ranges=spoken_ranges,
            time_offset_seconds=time_offset_seconds,
        )

    @staticmethod
    def _review_title_png_bytes(
        title: str,
        *,
        output_profile: str,
    ) -> bytes:
        from src.services.video_editor_cloud import (
            build_business_talking_head_title_png,
        )

        return build_business_talking_head_title_png(
            title,
            output_profile=output_profile,
        )

    def review_cloud_batch_item(
        self,
        batch_id: str,
        item_id: str,
        *,
        subtitle_segments: list[dict[str, Any]],
        enabled_plan_step_ids: list[str],
        selected_title: str,
        selected_bgm_id: str | None,
        confirmed: bool,
        smart_opening_enabled: bool = True,
    ) -> dict[str, Any]:
        from src.services.video_editor_cloud import (
            CloudAsset,
            EditPlan,
            EditStepKind,
            RenderRequest,
            retime_segments_after_cuts,
            validated_caption_emphasis,
            validated_caption_groups,
            visual_style_spec,
        )

        batch = self._sync_batch(self._require_batch(batch_id))
        if batch.provider_mode not in {"sandbox", "aliyun"}:
            raise VideoEditorWorkflowError("该接口仅用于云端轻量剪辑批次。")
        item = next((entry for entry in batch.items if entry.item_id == item_id), None)
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        if item.status != "awaiting_subtitle_review":
            raise VideoEditorWorkflowError("该素材当前不在字幕与方案复核阶段。")
        if not confirmed:
            raise VideoEditorWorkflowError("请明确确认字幕与剪辑方案。")
        duration_seconds = float(
            (item.provider_payload.get("media") or {}).get("duration_seconds") or 0
        )
        if duration_seconds <= 0:
            raise VideoEditorWorkflowError("缺少素材时长，不能保存人工复核。")
        segments = self._validated_review_segments(
            subtitle_segments,
            duration_seconds=duration_seconds,
        )
        title = selected_title.strip()
        if not title:
            raise VideoEditorWorkflowError("请确认成片标题。")
        if len(title) > 100:
            raise VideoEditorWorkflowError("标题不能超过 100 个字符。")
        if selected_bgm_id:
            self.resolve_bgm_asset(selected_bgm_id)

        try:
            plan = EditPlan.model_validate(item.edit_plan)
            selected_steps = list(
                dict.fromkeys(EditStepKind(value) for value in enabled_plan_step_ids)
            )
        except (ValueError, TypeError) as exc:
            raise VideoEditorWorkflowError("剪辑方案步骤无效，请重新分析。") from exc
        allowed_steps = set(plan.enabled_steps)
        # Older tasks may have been analyzed before BGM was included in the
        # plan. A reviewed, locally authorized asset may be added explicitly;
        # arbitrary render parameters remain impossible.
        if selected_bgm_id:
            allowed_steps.add(EditStepKind.BGM)
        if smart_opening_enabled and plan.smart_opening is not None:
            allowed_steps.add(EditStepKind.SMART_OPENING)
            if EditStepKind.SMART_OPENING not in selected_steps:
                selected_steps.insert(0, EditStepKind.SMART_OPENING)
        else:
            selected_steps = [
                step for step in selected_steps if step != EditStepKind.SMART_OPENING
            ]
        if any(step not in allowed_steps for step in selected_steps):
            raise VideoEditorWorkflowError("不能启用服务端方案之外的剪辑步骤。")
        trim_enabled = EditStepKind.TRIM_SILENCE in selected_steps
        approved_caption_groups = validated_caption_groups(
            plan.caption_groups,
            segments,
            max_chars=11,
        )
        reviewed_caption_emphasis = validated_caption_emphasis(
            [
                {
                    "segment_index": index,
                    "term": (segment.get("emphasis_terms") or [""])[0],
                    "kind": segment.get("emphasis_kind") or "keyword",
                }
                for index, segment in enumerate(segments)
                if segment.get("emphasis_terms")
            ],
            segments,
            caption_groups=approved_caption_groups,
        )
        caption_warnings = list(plan.warnings)
        if plan.caption_groups and not approved_caption_groups:
            caption_warnings.append(
                "字幕经人工修改，原 AI 语义断句已失效，正式出片改用安全规则断句。"
            )
        reviewed_plan = plan.model_copy(
            update={
                "enabled_steps": selected_steps,
                "remove_ranges": plan.remove_ranges if trim_enabled else [],
                "trim_silence_enabled": trim_enabled,
                "caption_groups": approved_caption_groups,
                "caption_group_source": (
                    "qwen_semantic"
                    if approved_caption_groups
                    else "deterministic_fallback"
                ),
                "caption_emphasis": reviewed_caption_emphasis,
                "warnings": caption_warnings,
            }
        )
        reviewed_plan = EditPlan.model_validate(reviewed_plan.model_dump())
        try:
            render_segments = retime_segments_after_cuts(
                segments,
                reviewed_plan.remove_ranges,
            )
            render_spoken_ranges = retime_segments_after_cuts(
                [item.model_dump(mode="json") for item in reviewed_plan.spoken_ranges],
                reviewed_plan.remove_ranges,
            )
        except ValueError as exc:
            raise VideoEditorWorkflowError(str(exc)) from exc
        plan_payload = self._cloud_plan_with_steps(reviewed_plan)
        plan_hash = self._cloud_request_hash(plan_payload)
        subtitle_hash = hashlib.sha256(
            json.dumps(
                render_segments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        review_time = datetime.now().astimezone()
        review_snapshot = {
            "confirmed": True,
            "approval_mode": "manual",
            "confirmed_at": review_time.isoformat(),
            "plan_version": reviewed_plan.plan_version,
            "plan_hash": plan_hash,
            "subtitle_hash": subtitle_hash,
            "visual_style_id": visual_style_spec(batch.output_profile)["style_id"],
        }
        reviewed_item = item.model_copy(
            update={
                "status": "rendering",
                "provider_stage": "submitting_render",
                "subtitle_segments": segments,
                "edit_plan": plan_payload,
                "enabled_plan_step_ids": [step.value for step in selected_steps],
                "selected_title": title,
                "selected_bgm_id": selected_bgm_id,
                "review_snapshot": review_snapshot,
                "review_confirmed_at": review_time,
                "provider_payload": {
                    **item.provider_payload,
                    "render_manifest": {
                        "visual_style_id": visual_style_spec(batch.output_profile)[
                            "style_id"
                        ],
                        "subtitle_format": "ass",
                        "title_render_mode": "png_watermark",
                        "title_font": "Source Han Serif CN Heavy",
                        "title_burned_in": EditStepKind.TITLE in selected_steps,
                        "subtitles_burned_in": EditStepKind.SUBTITLES in selected_steps,
                        "rough_cut_burned_in": bool(reviewed_plan.remove_ranges),
                        "source_kept_ranges": [
                            item.model_dump(mode="json")
                            for item in reviewed_plan.kept_ranges
                        ],
                        "estimated_output_seconds": reviewed_plan.estimated_output_seconds,
                        "expected_resolution": self._cloud_output_settings(
                            batch.output_profile
                        )["output_resolution"],
                    },
                },
                "publish_allowed": False,
                "error_message": None,
                "updated_at": review_time,
            }
        )
        batch = self._replace_batch_item(batch, reviewed_item)

        render_key = f"video-editor-render:{batch.batch_id}:{item.item_id}:{plan_hash}"
        render_request_hash = self._cloud_request_hash(
            {
                "batch_id": batch.batch_id,
                "item_id": item.item_id,
                "plan_hash": plan_hash,
                "subtitle_hash": subtitle_hash,
                "title": title,
                "bgm_id": selected_bgm_id,
                "output_profile": batch.output_profile,
            }
        )
        claimed = self.repository.claim_video_editor_operation(
            idempotency_key=render_key,
            operation_type="submit_cloud_render",
            request_hash=render_request_hash,
            created_at=review_time.isoformat(),
        )
        if not claimed:
            operation = self.repository.get_video_editor_operation(render_key)
            if operation is None or operation["request_hash"] != render_request_hash:
                raise VideoEditorWorkflowError("渲染幂等记录冲突，请人工检查。")
            return self.get_batch(batch.batch_id)

        _, providers = self._cloud_runtime()
        input_asset = CloudAsset.model_validate(
            reviewed_item.provider_payload.get("input_asset") or {}
        )
        subtitle_object_key: str | None = None
        title_watermark_object_key: str | None = None
        temp_subtitle: Path | None = None
        temp_title: Path | None = None
        temp_merge_config: Path | None = None
        temp_bgm_mix: Path | None = None
        temp_opening_dir: Path | None = None
        opening_asset = None
        opening_duration = 0.0
        merge_config_asset = None
        bgm_asset = None
        try:
            if (
                EditStepKind.SMART_OPENING in selected_steps
                and reviewed_plan.smart_opening is not None
            ):
                temp_opening_dir = Path(
                    tempfile.mkdtemp(prefix=f"{batch.batch_id}-opening-")
                )
                temp_opening = temp_opening_dir / "opening.mp4"
                opening_duration = self._render_smart_opening_clip(
                    reviewed_plan.smart_opening.model_dump(mode="json"),
                    output_profile=batch.output_profile,
                    fps=batch.output_fps,
                    output_path=temp_opening,
                    temp_dir=temp_opening_dir,
                )
                opening_asset = providers.object_store.upload(
                    temp_opening,
                    (
                        f"video-editor-input/{batch.batch_id}/opening/"
                        f"{reviewed_plan.smart_opening.style_id}.mp4"
                    ),
                    media_type="video/mp4",
                )
            subtitle_bytes = self._review_ass_bytes(
                render_segments,
                output_profile=batch.output_profile,
                caption_groups=reviewed_plan.caption_groups,
                caption_emphasis=reviewed_plan.caption_emphasis,
                spoken_ranges=render_spoken_ranges,
                time_offset_seconds=opening_duration,
            )
            if subtitle_bytes and EditStepKind.SUBTITLES in selected_steps:
                handle = tempfile.NamedTemporaryFile(
                    suffix=".ass",
                    delete=False,
                )
                try:
                    handle.write(subtitle_bytes)
                    handle.flush()
                finally:
                    handle.close()
                temp_subtitle = Path(handle.name)
                subtitle_asset = providers.object_store.upload(
                    temp_subtitle,
                    f"video-editor-input/{batch.batch_id}/review/approved.ass",
                    media_type="text/x-ass",
                )
                subtitle_object_key = subtitle_asset.object_key

            if EditStepKind.TITLE in selected_steps:
                title_bytes = self._review_title_png_bytes(
                    title,
                    output_profile=batch.output_profile,
                )
                title_handle = tempfile.NamedTemporaryFile(
                    suffix=".png",
                    delete=False,
                )
                try:
                    title_handle.write(title_bytes)
                    title_handle.flush()
                finally:
                    title_handle.close()
                temp_title = Path(title_handle.name)
                title_asset = providers.object_store.upload(
                    temp_title,
                    (f"video-editor-input/{batch.batch_id}/review/approved-title.png"),
                    media_type="image/png",
                )
                title_watermark_object_key = title_asset.object_key

            if selected_bgm_id and EditStepKind.BGM in selected_steps:
                selected_bgm = self.resolve_bgm_asset(selected_bgm_id)
                temp_bgm_mix = self._prepare_bgm_for_cloud_mix(
                    Path(selected_bgm["_path"]),
                    volume=batch.bgm_volume,
                )
                bgm_asset = providers.object_store.upload(
                    temp_bgm_mix,
                    (
                        f"video-editor-input/{batch.batch_id}/bgm/"
                        f"{selected_bgm['asset_id']}-low-volume.m4a"
                    ),
                    media_type="audio/mp4",
                )

            if (
                reviewed_plan.trim_silence_enabled
                and len(reviewed_plan.kept_ranges) > 5
            ):
                merge_source_url = input_asset.provider_locator or input_asset.uri
                merge_payload = {
                    "MergeList": [
                        {
                            "MergeURL": merge_source_url,
                            "Start": f"{item.start:.3f}",
                            "Duration": f"{item.end - item.start:.3f}",
                        }
                        for item in reviewed_plan.kept_ranges[1:]
                    ]
                }
                handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
                try:
                    handle.write(
                        json.dumps(merge_payload, separators=(",", ":")).encode("utf-8")
                    )
                    handle.flush()
                finally:
                    handle.close()
                temp_merge_config = Path(handle.name)
                merge_config_asset = providers.object_store.upload(
                    temp_merge_config,
                    f"video-editor-input/{batch.batch_id}/review/rough-cut-merge.json",
                    media_type="application/json",
                )

            render_request = RenderRequest(
                input_asset=input_asset,
                output_object_key=(
                    f"video-editor-output/{batch.batch_id}/output/"
                    f"{batch.output_profile}.mp4"
                ),
                output_profile=batch.output_profile,
                edit_plan=reviewed_plan,
                review_confirmed=True,
                subtitle_object_key=subtitle_object_key,
                title_watermark_object_key=title_watermark_object_key,
                merge_config_asset=merge_config_asset,
                title=title,
                bgm_asset=bgm_asset,
                bgm_volume=batch.bgm_volume,
                opening_asset=opening_asset,
                opening_duration_seconds=opening_duration,
                idempotency_key=render_key,
            )
            snapshot = providers.render.submit(render_request)
            self._save_cloud_job_snapshot(
                batch=batch,
                item=reviewed_item,
                job_key=f"{batch.batch_id}:{item.item_id}:render",
                snapshot=snapshot,
            )
            is_real_output = bool(
                not snapshot.is_mock
                and snapshot.status.value == "succeeded"
                and snapshot.can_publish
                and snapshot.output_uri
            )
            next_status = (
                "configuration_required"
                if snapshot.is_mock
                else "awaiting_output_confirmation"
                if is_real_output
                else "failed"
                if snapshot.status.value == "failed"
                else "rendering"
            )
            updated = reviewed_item.model_copy(
                update={
                    "status": next_status,
                    "provider_stage": snapshot.provider_stage,
                    "provider_job_ids": {
                        **reviewed_item.provider_job_ids,
                        "render": snapshot.provider_job_id,
                    },
                    "actual_usage": {
                        **reviewed_item.actual_usage,
                        "render": snapshot.usage,
                    },
                    "provider_payload": {
                        **reviewed_item.provider_payload,
                        **(
                            {"output_uri": snapshot.output_uri}
                            if is_real_output
                            else {}
                        ),
                    },
                    "result_media_url": None,
                    "is_mock": snapshot.is_mock,
                    "publish_allowed": is_real_output,
                    "error_message": (
                        "沙箱模式未调用真实云服务、未生成成片，不能交接发布。"
                        if snapshot.is_mock
                        else str(snapshot.detail.get("message") or "") or None
                    ),
                    "updated_at": datetime.now().astimezone(),
                }
            )
            batch = self._replace_batch_item(batch, updated)
            payload = self._batch_payload(batch)
            self.repository.complete_video_editor_operation(
                idempotency_key=render_key,
                state="completed",
                resource_id=batch.batch_id,
                response=self._safe_cloud_operation_response(payload),
                updated_at=datetime.now().astimezone().isoformat(),
            )
            return payload
        except Exception as exc:
            updated = reviewed_item.model_copy(
                update={
                    "status": "outcome_unknown",
                    "provider_stage": "render_submission_outcome_unknown",
                    "error_message": (
                        "渲染提交结果不明，系统不会盲目重提；"
                        f"请按供应商任务记录查询。{str(exc)}"
                    ),
                    "updated_at": datetime.now().astimezone(),
                }
            )
            batch = self._replace_batch_item(batch, updated)
            payload = self._batch_payload(batch)
            self.repository.complete_video_editor_operation(
                idempotency_key=render_key,
                state="outcome_unknown",
                resource_id=batch.batch_id,
                response=self._safe_cloud_operation_response(payload),
                error_message=str(exc),
                updated_at=datetime.now().astimezone().isoformat(),
            )
            return payload
        finally:
            if temp_subtitle is not None:
                temp_subtitle.unlink(missing_ok=True)
            if temp_title is not None:
                temp_title.unlink(missing_ok=True)
            if temp_merge_config is not None:
                temp_merge_config.unlink(missing_ok=True)
            if temp_bgm_mix is not None:
                temp_bgm_mix.unlink(missing_ok=True)
            if temp_opening_dir is not None:
                shutil.rmtree(temp_opening_dir, ignore_errors=True)

    def continue_batch_item(self, batch_id: str, item_id: str) -> dict[str, Any]:
        batch = self._require_batch(batch_id)
        if batch.provider_mode in {"sandbox", "aliyun"}:
            raise VideoEditorWorkflowError(
                "云端轻量剪辑必须通过字幕与方案复核接口继续。"
            )
        batch = self._sync_batch(batch)
        item = next((entry for entry in batch.items if entry.item_id == item_id), None)
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        if item.status != "awaiting_subtitle_review":
            raise VideoEditorWorkflowError("该素材当前无需继续字幕复核。")
        if (
            not item.subtitle_task_id
            or self.transcription_service.get_approved_revision(item.subtitle_task_id)
            is None
        ):
            raise VideoEditorWorkflowError("请先在当前页保存并确认字幕成稿。")
        batch = self._start_batch_render(batch, item)
        return self._batch_payload(batch)

    def select_batch_item_title(
        self, batch_id: str, item_id: str, title: str
    ) -> dict[str, Any]:
        batch = self._sync_batch(self._require_batch(batch_id))
        item = next((entry for entry in batch.items if entry.item_id == item_id), None)
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        selected_title = title.strip()
        if not selected_title:
            raise VideoEditorWorkflowError("标题不能为空。")
        if len(selected_title) > 100:
            raise VideoEditorWorkflowError("标题不能超过 100 个字符。")
        updated = item.model_copy(
            update={
                "selected_title": selected_title,
                "updated_at": datetime.now().astimezone(),
            }
        )
        if item.edit_task_id:
            task = self.repository.get_task(item.edit_task_id)
            if isinstance(task, VideoEditTask):
                self.repository.save_task(
                    task.model_copy(
                        update={
                            "outputs": {
                                **task.outputs,
                                "publish_title": selected_title,
                            },
                            "updated_at": datetime.now().astimezone(),
                        }
                    )
                )
        return self._batch_payload(self._replace_batch_item(batch, updated))

    def retry_batch_item(self, batch_id: str, item_id: str) -> dict[str, Any]:
        batch = self._require_batch(batch_id)
        if batch.provider_mode in {"sandbox", "aliyun"}:
            item = next(
                (entry for entry in batch.items if entry.item_id == item_id), None
            )
            if item is None:
                raise VideoEditorWorkflowError("批次素材不存在。")
            if item.status not in {
                "failed",
                "outcome_unknown",
                "analyzing",
                "rendering",
            }:
                raise VideoEditorWorkflowError("该云任务当前无需查询重试。")
            synced = self._sync_cloud_batch(batch)
            updated = next(entry for entry in synced.items if entry.item_id == item_id)
            if updated.status in {"failed", "outcome_unknown"}:
                raise VideoEditorWorkflowError(
                    "已查询现有供应商任务，结果仍未恢复；系统未重复提交付费任务。"
                )
            return self._batch_payload(synced)
        item = next((entry for entry in batch.items if entry.item_id == item_id), None)
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        if item.status not in {"failed", "interrupted"}:
            raise VideoEditorWorkflowError("仅失败或中断的素材可以重试。")
        analysis = self.create_analysis(
            source_id=item.source_id,
            target_platform=batch.target_platform,
            subtitle_enabled=batch.subtitle_enabled,
            subtitle_model=batch.subtitle_model,
        )
        updated = item.model_copy(
            update={
                "status": "analyzing",
                "analysis_id": analysis.task_id,
                "subtitle_task_id": None,
                "edit_task_id": None,
                "title_candidates": [],
                "selected_title": None,
                "selected_bgm_id": None,
                "bgm_reason": None,
                "error_message": None,
                "updated_at": datetime.now().astimezone(),
            }
        )
        batch = self._replace_batch_item(batch, updated)
        return self._batch_payload(batch)

    def _cloud_preview_url(self, item: VideoEditorBatchItem) -> str | None:
        """为私有 OSS 输出生成短期 HTTPS 预览地址，不持久化签名 URL。"""
        raw_uri = str(item.provider_payload.get("output_uri") or "").strip()
        if not raw_uri:
            return None
        parsed = urlparse(raw_uri)
        if parsed.scheme == "https":
            hostname = (parsed.hostname or "").casefold()
            return (
                raw_uri
                if hostname == "aliyuncs.com" or hostname.endswith(".aliyuncs.com")
                else None
            )
        if parsed.scheme != "oss":
            return None
        configuration, providers = self._cloud_runtime()
        presign = getattr(providers.object_store, "presign_get_url", None)
        if not callable(presign):
            return None
        validates_remotely = getattr(
            providers.object_store,
            "validates_output_ownership_remotely",
            False,
        )
        if not validates_remotely and parsed.netloc != configuration.oss_bucket:
            return None
        object_key = unquote(parsed.path.lstrip("/"))
        return str(presign(object_key, expires_seconds=3600))

    def _materialize_cloud_edit_task(
        self,
        batch: VideoEditorBatch,
        item: VideoEditorBatchItem,
    ) -> VideoEditTask:
        """在最终确认后把真实云成片下载为现有发布页可导入的任务。"""
        task_id = f"edit-cloud-{item.item_id.removeprefix('edit-item-')}"
        existing = self.repository.get_task(task_id)
        if (
            isinstance(existing, VideoEditTask)
            and existing.status == TaskStatus.SUCCEEDED
            and existing.result_path
            and Path(existing.result_path).is_file()
        ):
            return existing

        preview_url = self._cloud_preview_url(item)
        if not preview_url:
            raise VideoEditorWorkflowError(
                "云成片尚未取得可用的 HTTPS 下载地址，暂不能交接发布。"
            )
        parsed = urlparse(preview_url)
        hostname = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or not (
            hostname == "aliyuncs.com" or hostname.endswith(".aliyuncs.com")
        ):
            raise VideoEditorWorkflowError("云成片下载地址不在允许的 OSS 域名内。")

        output_root = Path(self.video_editing_service.output_directory)
        output_directory = output_root / "cloud_results"
        output_directory.mkdir(parents=True, exist_ok=True)
        target = output_directory / f"{task_id}.mp4"
        partial = target.with_suffix(".mp4.part")
        max_bytes = 2 * 1024 * 1024 * 1024
        last_error: Exception | None = None
        for attempt in range(2):
            received = 0
            try:
                request = urllib.request.Request(
                    preview_url,
                    headers={"Accept": "video/mp4,video/*;q=0.9,*/*;q=0.1"},
                    method="GET",
                )
                with urllib.request.urlopen(request, timeout=60) as response:
                    raw_length = response.headers.get("Content-Length")
                    if raw_length and int(raw_length) > max_bytes:
                        raise VideoEditorWorkflowError(
                            "云成片超过 2GB 下载上限，暂不能交接发布。"
                        )
                    with partial.open("wb") as output_file:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > max_bytes:
                                raise VideoEditorWorkflowError(
                                    "云成片超过 2GB 下载上限，暂不能交接发布。"
                                )
                            output_file.write(chunk)
                if received <= 0:
                    raise VideoEditorWorkflowError("云成片下载结果为空。")
                with partial.open("rb") as downloaded:
                    header = downloaded.read(32)
                if b"ftyp" not in header:
                    raise VideoEditorWorkflowError(
                        "云端返回的文件不是可识别的 MP4/MOV 成片。"
                    )
                partial.replace(target)
                break
            except VideoEditorWorkflowError:
                partial.unlink(missing_ok=True)
                raise
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                ValueError,
            ) as exc:
                partial.unlink(missing_ok=True)
                last_error = exc
                if attempt == 1:
                    raise VideoEditorWorkflowError(
                        "云成片下载失败，已按规则最多重试一次。"
                    ) from exc
        if not target.is_file():
            raise VideoEditorWorkflowError(
                f"云成片下载失败：{str(last_error or '未知错误')}"
            )

        now = datetime.now().astimezone()
        source = self.resolve_source(item.source_id)
        task = VideoEditTask(
            task_id=task_id,
            title=f"云端轻量剪辑 · {item.selected_title or item.title}",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            source_video_path=source["_path"],
            edit_config=VideoEditConfig(
                steps=[],
                output_format="mp4",
                output_resolution=batch.output_resolution,
                output_fps=batch.output_fps,
                output_bitrate=batch.output_bitrate,
            ),
            result_path=str(target),
            result_mime="video/mp4",
            result_size_bytes=target.stat().st_size,
            stage="云成片已确认并准备交接",
            is_mock=False,
            outputs={
                "workflow": "edit",
                "provider_mode": batch.provider_mode,
                "provider_output_uri": str(
                    item.provider_payload.get("output_uri") or ""
                ),
                "batch_id": batch.batch_id,
                "item_id": item.item_id,
                "publish_title": item.selected_title or item.title,
            },
        )
        self.repository.save_task(task)
        return task

    def confirm_batch_results(
        self, batch_id: str, item_ids: list[str]
    ) -> dict[str, Any]:
        batch = self._sync_batch(self._require_batch(batch_id))
        selected = set(item_ids)
        if not selected:
            raise VideoEditorWorkflowError("请至少选择一条已生成成片。")
        updated_items: list[VideoEditorBatchItem] = []
        for item in batch.items:
            if item.item_id not in selected:
                updated_items.append(item)
                continue
            local_task = (
                self.repository.get_task(item.edit_task_id)
                if item.edit_task_id
                else None
            )
            has_local_result = bool(
                isinstance(local_task, VideoEditTask)
                and local_task.outputs.get("workflow") == "local_preview_export"
                and local_task.status == TaskStatus.SUCCEEDED
                and local_task.result_path
                and Path(local_task.result_path).is_file()
            )
            if (
                batch.provider_mode in {"sandbox", "aliyun"}
                and not has_local_result
                and (
                    batch.is_mock
                    or item.is_mock
                    or not item.publish_allowed
                    or not item.provider_payload.get("output_uri")
                )
            ):
                raise VideoEditorWorkflowError(
                    "当前没有真实且可发布的云端成片，不能交接发布。"
                )
            if item.status != "awaiting_output_confirmation":
                raise VideoEditorWorkflowError("只能确认已成功生成的成片。")
            edit_task_id = item.edit_task_id
            if batch.provider_mode == "aliyun" and not has_local_result:
                edit_task_id = self._materialize_cloud_edit_task(batch, item).task_id
            updated_items.append(
                item.model_copy(
                    update={
                        "status": "ready_to_publish",
                        "edit_task_id": edit_task_id,
                        "confirmed_at": datetime.now().astimezone(),
                        "updated_at": datetime.now().astimezone(),
                    }
                )
            )
        batch = batch.model_copy(
            update={"items": updated_items, "updated_at": datetime.now().astimezone()}
        )
        self.repository.save_video_editor_batch(batch)
        return self._batch_payload(batch)

    def _require_batch(self, batch_id: str) -> VideoEditorBatch:
        batch = self.repository.get_video_editor_batch(batch_id)
        if batch is None:
            raise VideoEditorWorkflowError("智能剪辑批次不存在。")
        return batch

    def _sync_cloud_batch(self, batch: VideoEditorBatch) -> VideoEditorBatch:
        from src.adapters.video_editor_cloud import CloudProviderError

        _, providers = self._cloud_runtime()
        changed = False
        items: list[VideoEditorBatchItem] = []
        for item in batch.items:
            updated = item
            if item.status == "analyzing" and item.provider_job_ids.get("asr"):
                try:
                    snapshot = providers.asr.query(item.provider_job_ids["asr"])
                    self._save_cloud_job_snapshot(
                        batch=batch,
                        item=item,
                        job_key=f"{batch.batch_id}:{item.item_id}:asr",
                        snapshot=snapshot,
                    )
                    if snapshot.status.value == "succeeded":
                        interim = self._replace_batch_item(
                            batch,
                            item,
                            save=False,
                        )
                        completed = self._complete_cloud_analysis(
                            interim,
                            item,
                            snapshot,
                        )
                        updated = next(
                            entry
                            for entry in completed.items
                            if entry.item_id == item.item_id
                        )
                    elif snapshot.status.value == "failed":
                        updated = item.model_copy(
                            update={
                                "status": "failed",
                                "provider_stage": snapshot.provider_stage,
                                "error_message": str(
                                    snapshot.detail.get("message")
                                    or "Fun-ASR 转写失败。"
                                ),
                            }
                        )
                    else:
                        updated = item.model_copy(
                            update={
                                "provider_stage": snapshot.provider_stage,
                                "actual_usage": {
                                    **item.actual_usage,
                                    "asr": snapshot.usage,
                                },
                                "error_message": None,
                            }
                        )
                except CloudProviderError:
                    updated = item.model_copy(
                        update={
                            "provider_stage": "transcription_query_failed",
                            "error_message": (
                                "查询 Fun-ASR 任务失败，已按规则最多重试一次；"
                                "稍后可继续查询，系统不会重新提交转写。"
                            ),
                        }
                    )
                except Exception as exc:
                    updated = item.model_copy(
                        update={
                            "status": "outcome_unknown",
                            "provider_stage": "planning_outcome_unknown",
                            "error_message": (
                                "转写已完成但规划结果不明，系统不会盲目重提。"
                                f"{str(exc)}"
                            ),
                        }
                    )
            elif item.status == "rendering" and item.provider_job_ids.get("render"):
                try:
                    snapshot = providers.render.query(item.provider_job_ids["render"])
                    self._save_cloud_job_snapshot(
                        batch=batch,
                        item=item,
                        job_key=f"{batch.batch_id}:{item.item_id}:render",
                        snapshot=snapshot,
                    )
                    is_real_output = bool(
                        not snapshot.is_mock
                        and snapshot.status.value == "succeeded"
                        and snapshot.can_publish
                        and snapshot.output_uri
                    )
                    if is_real_output:
                        updated = item.model_copy(
                            update={
                                "status": "awaiting_output_confirmation",
                                "provider_stage": snapshot.provider_stage,
                                "provider_payload": {
                                    **item.provider_payload,
                                    "output_uri": snapshot.output_uri,
                                },
                                "result_media_url": None,
                                "publish_allowed": True,
                                "actual_usage": {
                                    **item.actual_usage,
                                    "render": snapshot.usage,
                                },
                                "error_message": None,
                            }
                        )
                    elif snapshot.status.value == "failed":
                        updated = item.model_copy(
                            update={
                                "status": "failed",
                                "provider_stage": snapshot.provider_stage,
                                "error_message": str(
                                    snapshot.detail.get("message") or "MPS 渲染失败。"
                                ),
                            }
                        )
                    else:
                        updated = item.model_copy(
                            update={
                                "provider_stage": snapshot.provider_stage,
                                "error_message": None,
                            }
                        )
                except CloudProviderError:
                    updated = item.model_copy(
                        update={
                            "provider_stage": "render_query_failed",
                            "error_message": (
                                "查询 MPS 任务失败，已按规则最多重试一次；"
                                "稍后可继续查询，系统不会重新提交渲染。"
                            ),
                        }
                    )
            if updated != item:
                changed = True
                updated = updated.model_copy(
                    update={"updated_at": datetime.now().astimezone()}
                )
            items.append(updated)
        synced = batch.model_copy(
            update={
                "items": items,
                "updated_at": (
                    datetime.now().astimezone() if changed else batch.updated_at
                ),
            }
        )
        if changed:
            self.repository.save_video_editor_batch(synced)
        return synced

    def _sync_batch(self, batch: VideoEditorBatch) -> VideoEditorBatch:
        local_changed = False
        local_items: list[VideoEditorBatchItem] = []
        for item in batch.items:
            updated = item
            if item.edit_task_id and str(item.provider_stage or "").startswith(
                "local_export_"
            ):
                task = self.repository.get_task(item.edit_task_id)
                if (
                    isinstance(task, VideoEditTask)
                    and task.outputs.get("workflow") == "local_preview_export"
                ):
                    if task.status == TaskStatus.SUCCEEDED and task.result_path:
                        updated = item.model_copy(
                            update={
                                "status": "awaiting_output_confirmation",
                                "provider_stage": "local_export_complete",
                                "publish_allowed": True,
                                "error_message": None,
                            }
                        )
                    elif task.status == TaskStatus.FAILED:
                        updated = item.model_copy(
                            update={
                                "status": "failed",
                                "provider_stage": "local_export_failed",
                                "publish_allowed": False,
                                "error_message": task.error_message
                                or "本机成片生成失败。",
                            }
                        )
                    else:
                        updated = item.model_copy(
                            update={
                                "status": "rendering",
                                "provider_stage": "local_export_rendering",
                                "publish_allowed": False,
                            }
                        )
            if updated != item:
                local_changed = True
                updated = updated.model_copy(
                    update={"updated_at": datetime.now().astimezone()}
                )
            local_items.append(updated)
        if local_changed:
            batch = batch.model_copy(
                update={
                    "items": local_items,
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self.repository.save_video_editor_batch(batch)
        if batch.provider_mode in {"sandbox", "aliyun"}:
            return self._sync_cloud_batch(batch)
        changed = False
        items: list[VideoEditorBatchItem] = []
        for item in batch.items:
            updated = item
            if item.status == "analyzing" and item.analysis_id:
                analysis = self.repository.get_task(item.analysis_id)
                if isinstance(analysis, VideoEditTask):
                    if self._is_interrupted(analysis):
                        updated = item.model_copy(
                            update={
                                "status": "interrupted",
                                "error_message": "分析任务可能在服务重启时中断，请重试。",
                            }
                        )
                    elif analysis.status == TaskStatus.FAILED:
                        updated = item.model_copy(
                            update={
                                "status": "failed",
                                "error_message": analysis.error_message
                                or "素材分析失败。",
                            }
                        )
                    elif analysis.status == TaskStatus.SUCCEEDED:
                        transcript_id = (
                            analysis.outputs.get("transcription_task_id") or None
                        )
                        analysis_payload = self._analysis_payload(analysis)
                        title_candidates = list(
                            analysis_payload.get("title_candidates") or []
                        )
                        creative_updates = {
                            "title_candidates": title_candidates,
                            "selected_title": item.selected_title
                            or (
                                title_candidates[0] if title_candidates else item.title
                            ),
                        }
                        if batch.subtitle_enabled:
                            error = analysis.outputs.get("subtitle_error") or ""
                            updated = item.model_copy(
                                update={
                                    "status": "awaiting_subtitle_review"
                                    if transcript_id
                                    else "failed",
                                    "subtitle_task_id": transcript_id,
                                    "error_message": error
                                    or (None if transcript_id else "字幕生成失败。"),
                                    **creative_updates,
                                }
                            )
                        else:
                            updated = item.model_copy(
                                update={"status": "ready_to_render", **creative_updates}
                            )
            if updated.status == "ready_to_render":
                batch = self._replace_batch_item(batch, updated, save=False)
                batch = self._start_batch_render(batch, updated, save=False)
                updated = next(
                    entry for entry in batch.items if entry.item_id == item.item_id
                )
            elif updated.status == "rendering" and updated.edit_task_id:
                job = self.repository.get_task(updated.edit_task_id)
                if isinstance(job, VideoEditTask):
                    if self._is_interrupted(job):
                        updated = updated.model_copy(
                            update={
                                "status": "interrupted",
                                "error_message": "剪辑任务可能在服务重启时中断，请重试。",
                            }
                        )
                    elif job.status == TaskStatus.FAILED:
                        updated = updated.model_copy(
                            update={
                                "status": "failed",
                                "error_message": job.error_message or "剪辑失败。",
                            }
                        )
                    elif job.status == TaskStatus.SUCCEEDED:
                        updated = updated.model_copy(
                            update={"status": "awaiting_output_confirmation"}
                        )
            if updated != item:
                changed = True
            items.append(
                updated.model_copy(update={"updated_at": datetime.now().astimezone()})
                if updated != item
                else updated
            )
        synced = batch.model_copy(
            update={
                "items": items,
                "updated_at": datetime.now().astimezone()
                if changed
                else batch.updated_at,
            }
        )
        if changed:
            self.repository.save_video_editor_batch(synced)
        return synced

    def _start_batch_render(
        self, batch: VideoEditorBatch, item: VideoEditorBatchItem, *, save: bool = True
    ) -> VideoEditorBatch:
        if not item.analysis_id:
            raise VideoEditorWorkflowError("缺少素材分析结果，无法开始剪辑。")
        analysis = self.get_analysis(item.analysis_id)
        steps = batch.steps or list(analysis.get("recommended_steps") or [])
        normalized_steps: list[dict[str, Any]] = []
        for step in steps:
            kind = str(step.get("kind") or "")
            # 字幕只允许由已经人工确认的 revision 在 _run_edit 中烧录一次。
            if kind == "ai_subtitle" or (kind == "subtitle" and batch.subtitle_enabled):
                continue
            params = dict(step.get("params") or {})
            if kind == "resize":
                params["resolution"] = batch.output_resolution
            if kind == "ai_silence_trim":
                params["min_duration"] = max(
                    1.5,
                    float(params.get("min_duration") or 1.5),
                )
                params["keep_padding"] = max(
                    0.35,
                    float(params.get("keep_padding") or 0.35),
                )
            normalized_steps.append({**step, "params": params})
        steps = normalized_steps
        selected_bgm_id: str | None = None
        bgm_reason: str | None = None
        if batch.bgm_enabled:
            if batch.bgm_id:
                bgm = self.resolve_bgm_asset(batch.bgm_id)
                bgm_reason = f"使用你指定的授权音乐《{bgm['title']}》；自动做人声避让、响度和淡入淡出。"
            else:
                bgm, bgm_reason = self._recommend_bgm_asset(analysis, item.title)
            if bgm is not None:
                selected_bgm_id = bgm["asset_id"]
                auto_volume = self._auto_bgm_volume(analysis, batch.bgm_volume)
                steps = [
                    *steps,
                    {
                        "kind": "background_music",
                        "params": {
                            "bgm_path": bgm["_path"],
                            "bgm_volume": auto_volume,
                            "video_volume": 1.0,
                            "ducking": True,
                            "auto_adjusted": True,
                        },
                        "enabled": True,
                    },
                ]
        if not batch.bgm_enabled:
            bgm_reason = "已关闭自动配乐，保持素材原声。"
        job = self.create_edit_job(
            analysis_id=item.analysis_id,
            steps=steps,
            output_format=batch.output_format,
            output_resolution=batch.output_resolution,
            output_fps=batch.output_fps,
            output_bitrate=batch.output_bitrate,
            subtitle_enabled=batch.subtitle_enabled,
            publish_title=item.selected_title or item.title,
        )
        updated = item.model_copy(
            update={
                "status": "rendering",
                "edit_task_id": job.task_id,
                "selected_bgm_id": selected_bgm_id,
                "bgm_reason": bgm_reason,
                "error_message": None,
                "updated_at": datetime.now().astimezone(),
            }
        )
        return self._replace_batch_item(batch, updated, save=save)

    def _recommend_bgm_asset(
        self,
        analysis: dict[str, Any],
        source_title: str,
    ) -> tuple[dict[str, Any] | None, str]:
        assets = self.list_bgm_assets()
        if not assets:
            return (
                None,
                "本机授权音乐库为空，本条保持原声；上传一首有使用权的音乐后即可自动配乐。",
            )
        automatic_assets = [
            asset
            for asset in assets
            if str(asset.get("content_id_risk") or "unknown") != "registered"
        ]
        if not automatic_assets:
            return (
                None,
                "本机音乐都标记为可能触发平台版权识别，本条保持原声；仍可在复核时手动选择。",
            )
        assets = automatic_assets

        title_candidates = analysis.get("title_candidates") or []
        transcript = str(analysis.get("transcript") or "")
        text = " ".join(
            [source_title, transcript, *[str(value) for value in title_candidates]]
        ).casefold()
        technology_words = (
            "ai",
            "人工智能",
            "机器人",
            "科技",
            "软件",
            "数智",
            "智能",
            "系统",
            "设备",
        )
        business_words = (
            "客户",
            "工厂",
            "公司",
            "老板",
            "行业",
            "产品",
            "方案",
            "经营",
            "销售",
        )
        energetic_words = (
            "探店",
            "运动",
            "游戏",
            "促销",
            "开业",
            "挑战",
            "旅行",
            "展示",
            "节奏",
            "热血",
            "动感",
            "欢快",
        )
        calm_words = (
            "教程",
            "知识",
            "口播",
            "讲解",
            "访谈",
            "故事",
            "情感",
            "经验",
            "舒缓",
            "安静",
            "温柔",
        )
        edit_plan = analysis.get("edit_plan") or {}
        planned_category = (
            str(edit_plan.get("bgm_category") or "").strip()
            if isinstance(edit_plan, dict)
            else ""
        )
        desired = (
            planned_category
            if planned_category in _BGM_VOICEOVER_CATEGORIES
            else "科技未来"
            if any(word in text for word in technology_words)
            else "轻松日常"
            if any(word in text for word in energetic_words)
            else "故事叙事"
            if "故事" in text or "经历" in text or "后来" in text
            else "情绪共鸣"
            if any(word in text for word in ("情感", "难过", "焦虑", "治愈", "共鸣"))
            else "理性干货"
            if any(word in text for word in calm_words)
            else "商业表达"
            if any(word in text for word in business_words)
            else "通用口播"
        )

        category_tokens = {
            "理性干货": ("知识", "教程", "讲解", "理性", "平稳", "商务"),
            "情绪共鸣": ("情感", "共鸣", "温柔", "治愈", "钢琴", "舒缓"),
            "故事叙事": ("故事", "叙事", "回忆", "温暖", "安静"),
            "商业表达": ("商务", "品牌", "产品", "正向", "轻量"),
            "科技未来": ("科技", "未来", "智能", "电子", "数智"),
            "轻松日常": ("轻松", "日常", "欢快", "松弛", "轻快"),
            "励志成长": ("励志", "成长", "积极", "希望", "向上"),
            "悬念揭秘": ("悬念", "揭秘", "紧张", "神秘", "真相"),
            "通用口播": ("通用", "百搭", "平稳", "轻量"),
        }
        planned_keywords = (
            edit_plan.get("bgm_keywords") or [] if isinstance(edit_plan, dict) else []
        )
        normalized_keywords = [
            str(item).casefold() for item in planned_keywords if str(item).strip()
        ][:6]
        planned_energy = (
            str(edit_plan.get("bgm_energy") or "克制")
            if isinstance(edit_plan, dict)
            else "克制"
        )
        duration = float((analysis.get("media") or {}).get("duration_seconds") or 0)

        def score(asset: dict[str, Any]) -> tuple[int, int, str]:
            asset_category = str(asset.get("voiceover_category") or "")
            searchable = " ".join(
                [
                    str(asset.get("mood") or ""),
                    *[str(item) for item in asset.get("tags") or []],
                ]
            ).casefold()
            mood_score = 14 if asset_category == desired else 0
            mood_score += sum(
                3 for token in category_tokens[desired] if token in searchable
            )
            mood_score += sum(2 for token in normalized_keywords if token in searchable)
            if str(asset.get("energy") or "") == planned_energy:
                mood_score += 2
            asset_duration = float(asset.get("duration_seconds") or 0)
            covers_video = int(duration <= 0 or asset_duration >= duration)
            return mood_score, covers_video, str(asset.get("created_at") or "")

        selected = max(assets, key=score)
        reason = (
            f"AI 阅读转写文案与标题后归为“{desired}”，"
            f"自动选择本地授权音乐《{selected['title']}》并使用低音量铺底。"
        )
        return self.resolve_bgm_asset(selected["asset_id"]), reason

    @staticmethod
    def _auto_bgm_volume(analysis: dict[str, Any], preferred_volume: float) -> float:
        media = analysis.get("media") or {}
        audio = analysis.get("audio") or {}
        if not media.get("has_audio", False):
            return 0.38
        mean_volume = audio.get("mean_volume_db")
        if isinstance(mean_volume, (int, float)) and mean_volume > -14:
            return 0.16
        return round(min(max(preferred_volume, 0.18), 0.24), 2)

    def _replace_batch_item(
        self, batch: VideoEditorBatch, item: VideoEditorBatchItem, *, save: bool = True
    ) -> VideoEditorBatch:
        updated = batch.model_copy(
            update={
                "items": [
                    item if entry.item_id == item.item_id else entry
                    for entry in batch.items
                ],
                "updated_at": datetime.now().astimezone(),
            }
        )
        if save:
            self.repository.save_video_editor_batch(updated)
        return updated

    @staticmethod
    def _is_interrupted(task: VideoEditTask) -> bool:
        return task.status in {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
        } and datetime.now().astimezone() - task.updated_at > timedelta(minutes=5)

    def _batch_payload(self, batch: VideoEditorBatch) -> dict[str, Any]:
        visual_spec = None
        if (
            batch.provider_mode in {"sandbox", "aliyun", "local"}
            and batch.output_profile
        ):
            from src.services.video_editor_cloud import (
                build_business_talking_head_overlay_preview,
                visual_style_spec,
            )

            visual_spec = visual_style_spec(batch.output_profile)
        items: list[dict[str, Any]] = []
        for item in batch.items:
            cached_context = self._cached_source_context(item.source_id)
            script_text = self._avatar_script_text(item.source_id)
            effective_title = (
                item.selected_title
                or str(cached_context.get("selected_title") or "").strip()
                or self._semantic_source_title(
                    item.source_id,
                    item.title,
                    script_text,
                )
            )
            effective_title_candidates = (
                list(item.title_candidates)
                or list(cached_context.get("title_candidates") or [])
                or ([effective_title] if effective_title else [])
            )
            if item.subtitle_segments:
                preview_subtitle_segments = [
                    dict(segment) for segment in item.subtitle_segments
                ]
                subtitle_preview_source = "current_asr"
            elif cached_context.get("subtitle_segments"):
                preview_subtitle_segments = [
                    dict(segment) for segment in cached_context["subtitle_segments"]
                ]
                subtitle_preview_source = "cached_asr"
            else:
                preview_subtitle_segments = self._estimated_script_segments(
                    script_text,
                    float(cached_context.get("duration_seconds") or 0),
                )
                subtitle_preview_source = (
                    "script_estimate" if preview_subtitle_segments else "none"
                )
            analysis = (
                self.repository.get_task(item.analysis_id) if item.analysis_id else None
            )
            job = (
                self.repository.get_task(item.edit_task_id)
                if item.edit_task_id
                else None
            )
            result_media_url = item.result_media_url
            overlay_preview = None
            if visual_spec is not None:
                caption_groups = (
                    (item.edit_plan or {}).get("caption_groups")
                    if subtitle_preview_source == "current_asr"
                    else None
                )
                caption_emphasis = (
                    (item.edit_plan or {}).get("caption_emphasis")
                    if subtitle_preview_source == "current_asr"
                    else None
                )
                overlay_preview = build_business_talking_head_overlay_preview(
                    preview_subtitle_segments,
                    title=effective_title,
                    output_profile=batch.output_profile,
                    caption_groups=caption_groups,
                    caption_emphasis=caption_emphasis,
                    spoken_ranges=(item.edit_plan or {}).get("spoken_ranges"),
                )
            if (
                batch.provider_mode == "aliyun"
                and item.publish_allowed
                and item.provider_payload.get("output_uri")
            ):
                try:
                    result_media_url = self._cloud_preview_url(item)
                except Exception:
                    result_media_url = None
            items.append(
                {
                    "item_id": item.item_id,
                    "source_id": item.source_id,
                    "title": item.title,
                    "status": item.status,
                    "analysis_id": item.analysis_id,
                    "subtitle_task_id": item.subtitle_task_id,
                    "edit_task_id": item.edit_task_id,
                    "title_candidates": effective_title_candidates,
                    "selected_title": effective_title,
                    "selected_bgm_id": item.selected_bgm_id,
                    "bgm_reason": item.bgm_reason,
                    "provider_stage": item.provider_stage,
                    "provider_job_ids": item.provider_job_ids,
                    "provider_payload": item.provider_payload,
                    "render_manifest": item.provider_payload.get("render_manifest")
                    or None,
                    "actual_usage": item.actual_usage,
                    "edit_plan": item.edit_plan or None,
                    "enabled_plan_step_ids": item.enabled_plan_step_ids,
                    "subtitle_segments": item.subtitle_segments,
                    "preview_subtitle_segments": preview_subtitle_segments,
                    "subtitle_preview_source": subtitle_preview_source,
                    "overlay_preview": overlay_preview,
                    "review_snapshot": item.review_snapshot,
                    "review_confirmed_at": (
                        item.review_confirmed_at.isoformat()
                        if item.review_confirmed_at
                        else None
                    ),
                    "result_media_url": result_media_url,
                    "is_mock": item.is_mock,
                    "publish_allowed": item.publish_allowed,
                    "error_message": item.error_message,
                    "confirmed_at": item.confirmed_at.isoformat()
                    if item.confirmed_at
                    else None,
                    "analysis": self._analysis_payload(analysis)
                    if isinstance(analysis, VideoEditTask)
                    else None,
                    "job": self._job_payload(job)
                    if isinstance(job, VideoEditTask)
                    else None,
                }
            )
        status = (
            "ready_to_publish"
            if items and all(item["status"] == "ready_to_publish" for item in items)
            else "running"
        )
        if any(item["status"] == "configuration_required" for item in items):
            status = "configuration_required"
        elif any(item["status"] == "outcome_unknown" for item in items):
            status = "outcome_unknown"
        elif any(
            item["status"] in {"analyzing", "rendering", "ready_to_render"}
            for item in items
        ):
            status = "running"
        elif any(item["status"] == "awaiting_subtitle_review" for item in items):
            status = "awaiting_subtitle_review"
        elif any(item["status"] == "awaiting_output_confirmation" for item in items):
            status = "awaiting_output_confirmation"
        elif any(item["status"] in {"failed", "interrupted"} for item in items):
            status = "partial"
        return {
            "batch_id": batch.batch_id,
            "status": status,
            "target_platform": batch.target_platform,
            "subtitle_enabled": batch.subtitle_enabled,
            "subtitle_model": batch.subtitle_model,
            "bgm_enabled": batch.bgm_enabled,
            "bgm_id": batch.bgm_id,
            "bgm_volume": batch.bgm_volume,
            "provider_mode": batch.provider_mode,
            "output_profile": batch.output_profile,
            "output_resolution": batch.output_resolution,
            "output_fps": batch.output_fps,
            "output_bitrate": batch.output_bitrate,
            "visual_spec": visual_spec,
            "quote_id": batch.quote_id,
            "cost_quote": batch.cost_quote or None,
            "actual_usage": {
                item["item_id"]: item["actual_usage"]
                for item in items
                if item["actual_usage"]
            },
            "billing_confirmation": batch.billing_confirmation,
            "billing_confirmed_at": (
                batch.billing_confirmed_at.isoformat()
                if batch.billing_confirmed_at
                else None
            ),
            "idempotency_key": batch.idempotency_key,
            "is_mock": batch.is_mock,
            "bgm": (
                {
                    key: value
                    for key, value in self.resolve_bgm_asset(batch.bgm_id).items()
                    if key != "_path"
                }
                if batch.bgm_enabled and batch.bgm_id
                else None
            ),
            "items": items,
            "created_at": batch.created_at.isoformat(),
            "updated_at": batch.updated_at.isoformat(),
        }

    # ------------------------------------------------------------------
    # 序列化与状态更新
    # ------------------------------------------------------------------
    def _get_workflow_task(self, task_id: str, workflow: str) -> VideoEditTask:
        task = self.repository.get_task(task_id)
        if (
            not isinstance(task, VideoEditTask)
            or task.outputs.get("workflow") != workflow
        ):
            raise VideoEditorWorkflowError("智能剪辑任务不存在。")
        return task

    def _update(self, task: VideoEditTask, **changes: Any) -> VideoEditTask:
        updated = task.model_copy(
            update={**changes, "updated_at": datetime.now().astimezone()}
        )
        self.repository.save_task(updated)
        return updated

    @staticmethod
    def _analysis_payload(task: VideoEditTask) -> dict[str, Any]:
        try:
            analysis = json.loads(task.outputs.get("analysis_json", "{}"))
        except json.JSONDecodeError:
            analysis = {}
        return {
            "analysis_id": task.task_id,
            "status": task.status.value,
            "progress": task.progress,
            "stage": task.stage,
            "error_message": task.error_message,
            "source_id": task.outputs.get("source_id"),
            "target_platform": task.outputs.get("target_platform", "douyin"),
            "subtitle_enabled": task.outputs.get("subtitle_enabled") == "true",
            "subtitle_model": task.outputs.get("subtitle_model", "large-v3-turbo"),
            "content_advice": task.outputs.get("content_advice") or None,
            **analysis,
        }

    @staticmethod
    def _job_payload(task: VideoEditTask) -> dict[str, Any]:
        media_url = (
            f"/api/v1/video-editor/jobs/{task.task_id}/media"
            if task.result_path
            else None
        )
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "progress": task.progress,
            "stage": task.stage,
            "error_message": task.error_message,
            "result_size_bytes": task.result_size_bytes,
            "media_url": media_url,
            "download_url": f"/api/v1/video-editor/jobs/{task.task_id}/download"
            if task.result_path
            else None,
            "source_id": task.outputs.get("source_id"),
            "analysis_id": task.outputs.get("analysis_id"),
            "publish_title": task.outputs.get("publish_title") or None,
            "workflow": task.outputs.get("workflow"),
        }
