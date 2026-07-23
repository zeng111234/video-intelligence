"""面向 Web 工作台的智能剪辑工作流。

保持底层 ``VideoEditingService`` 的兼容接口不变，在其之上提供：
系统成片选择、可恢复的分析记录、字幕复核门禁与后台渲染任务。
"""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.models import (
    AvatarTask,
    PipelineStage,
    TaskStatus,
    VideoEditConfig,
    VideoEditStep,
    VideoEditStepKind,
    VideoEditTask,
)


_WORKFLOW_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-workflow")
_MAX_SUBTITLE_BYTES = 50 * 1024 * 1024
_MAX_SUBTITLE_SECONDS = 15 * 60


class VideoEditorWorkflowError(ValueError):
    """用户可理解的智能剪辑工作流错误。"""


class VideoEditorWorkflowService:
    """协调系统素材、转写复核与视频编辑服务。"""

    def __init__(self, repository, video_editing_service, transcription_service, copywriting_service) -> None:
        self.repository = repository
        self.video_editing_service = video_editing_service
        self.transcription_service = transcription_service
        self.copywriting_service = copywriting_service

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
            if task.status != TaskStatus.SUCCEEDED or task.is_mock or not path.is_file():
                continue
            key = str(path.resolve()).casefold()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            sources.append(
                self._source_payload(
                    source_id=f"avatar:{task.task_id}",
                    source_type="avatar",
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
            if task.status != TaskStatus.SUCCEEDED or task.is_mock or not path.is_file():
                raise VideoEditorWorkflowError("该数字人成片不可用或已被清理。")
            return self._source_payload(
                source_id=source_id,
                source_type=source_type,
                source_task_id=record_id,
                title=task.title,
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

        raise VideoEditorWorkflowError("不支持的系统素材来源。")

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
            task = self._update(task, status=TaskStatus.RUNNING, progress=10, stage="读取媒体信息")
            media = self._probe_media(Path(task.source_video_path))
            task = self._update(task, progress=35, stage="分析音频节奏")
            audio = self._analyze_audio(Path(task.source_video_path), media.get("duration_seconds"))
            recommendations, findings = self._recommend(media, audio, task.outputs)
            transcript_id = ""
            subtitle_error = ""

            if task.outputs.get("subtitle_enabled") == "true":
                task = self._update(task, progress=55, stage="生成字幕草稿")
                try:
                    transcript_id = self._create_transcription(task, media)
                except Exception as exc:
                    subtitle_error = str(exc)

            payload = {
                "media": media,
                "audio": audio,
                "findings": findings,
                "recommended_steps": recommendations,
                "subtitle_task_id": transcript_id or None,
                "subtitle_error": subtitle_error or None,
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
            self._update(task, status=TaskStatus.FAILED, stage="分析失败", error_message=str(exc))

    def _create_transcription(self, task: VideoEditTask, media: dict[str, Any]) -> str:
        path = Path(task.source_video_path)
        if path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
            raise VideoEditorWorkflowError("该成片格式暂不支持字幕识别。")
        if path.stat().st_size > _MAX_SUBTITLE_BYTES:
            raise VideoEditorWorkflowError("成片超过 50MB，暂不能在智能剪辑中生成字幕。")
        if float(media.get("duration_seconds") or 0) > _MAX_SUBTITLE_SECONDS:
            raise VideoEditorWorkflowError("成片超过 15 分钟，暂不能在智能剪辑中生成字幕。")
        transcript = self.transcription_service.create_task(
            media_name=path.name,
            media_type=mimetypes.guess_type(path.name)[0] or "video/mp4",
            media_bytes=path.read_bytes(),
            rights_confirmed=True,
            rights_holder="系统内已授权素材",
            model_name=task.outputs.get("subtitle_model", "large-v3-turbo"),
            language=task.outputs.get("language", "zh"),
        )
        if transcript.status != TaskStatus.SUCCEEDED:
            raise VideoEditorWorkflowError(transcript.error_message or "字幕识别失败。")
        return transcript.task_id

    @staticmethod
    def _probe_media(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise VideoEditorWorkflowError("源成片已不存在。")
        if not shutil.which("ffprobe"):
            raise VideoEditorWorkflowError("未检测到 FFprobe，无法分析视频。")
        cmd = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,width,height,r_frame_rate",
            "-of", "json", str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0:
            raise VideoEditorWorkflowError((result.stderr or "媒体分析失败。").strip()[:240])
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        has_audio = any(item.get("codec_type") == "audio" for item in streams)
        fps_text = str(video.get("r_frame_rate") or "0/1")
        try:
            num, den = fps_text.split("/", 1)
            fps = round(float(num) / max(float(den), 1), 2)
        except Exception:
            fps = 0.0
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        return {
            "duration_seconds": round(float((data.get("format") or {}).get("duration") or 0), 2),
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
        result: dict[str, Any] = {"available": True, "mean_volume_db": None, "silence_seconds": 0.0}
        try:
            volume = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True, timeout=90, check=False,
            )
            match = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", volume.stderr or "")
            if match:
                result["mean_volume_db"] = float(match.group(1))
        except Exception:
            pass
        try:
            silence = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "info", "-i", str(path), "-af", "silencedetect=noise=-30dB:d=0.5", "-f", "null", "-"],
                capture_output=True, text=True, timeout=90, check=False,
            )
            starts = [float(item) for item in re.findall(r"silence_start:\s*([\d.]+)", silence.stderr or "")]
            ends = [float(item) for item in re.findall(r"silence_end:\s*([\d.]+)", silence.stderr or "")]
            result["silence_seconds"] = round(sum(max(0, end - start) for start, end in zip(starts, ends)), 2)
            result["silence_intervals"] = [
                {"start": round(start, 2), "end": round(end, 2)}
                for start, end in zip(starts[:12], ends[:12])
            ]
        except Exception:
            result["silence_intervals"] = []
        return result

    @staticmethod
    def _recommend(media: dict[str, Any], audio: dict[str, Any], outputs: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
        steps: list[dict[str, Any]] = []
        findings: list[str] = []
        volume = audio.get("mean_volume_db")
        if isinstance(volume, (int, float)) and (volume < -18 or volume > -14):
            steps.append({"kind": "ai_volume_norm", "params": {"target_i": -16}, "enabled": True, "label": "统一口播响度"})
            findings.append(f"平均音量约 {volume:.1f} dB，建议标准化到短视频常用响度。")
        silence_seconds = float(audio.get("silence_seconds") or 0)
        duration = float(media.get("duration_seconds") or 0)
        if silence_seconds > 1 and duration > 0 and silence_seconds / duration >= 0.05:
            steps.append({"kind": "ai_silence_trim", "params": {"noise_threshold": -30, "min_duration": 0.5, "keep_padding": 0.1}, "enabled": True, "label": "裁掉明显静音"})
            findings.append(f"检测到约 {silence_seconds:.1f} 秒静音，可压缩口播节奏。")
        if media.get("orientation") != "vertical" or (media.get("width", 0) and media.get("height", 0) and media.get("height", 0) < 1280):
            steps.append({"kind": "resize", "params": {"resolution": "1080x1920"}, "enabled": True, "label": "适配竖屏平台"})
            findings.append("画幅或清晰度与竖屏发布预设不一致，建议等比适配。")
        if outputs.get("subtitle_enabled") == "true":
            findings.append("字幕会先进入人工复核；批准前不会烧录进成片。")
        if not steps:
            findings.append("当前素材未发现必须处理的音频或画幅问题，可按需添加增强步骤。")
        return steps, findings

    # ------------------------------------------------------------------
    # 内容建议与渲染
    # ------------------------------------------------------------------
    def generate_content_advice(self, analysis_id: str) -> dict[str, Any]:
        task = self._get_workflow_task(analysis_id, "analysis")
        transcript_id = task.outputs.get("transcription_task_id", "")
        if not transcript_id:
            raise VideoEditorWorkflowError("请先完成字幕识别和人工复核，再生成内容建议。")
        revision = self.transcription_service.get_approved_revision(transcript_id)
        if revision is None:
            raise VideoEditorWorkflowError("字幕尚未确认成稿，暂不能生成内容建议。")
        capabilities = self.copywriting_service.capabilities()
        if not capabilities.get("enabled", False):
            return {"enabled": False, "message": "未配置真实大模型，已保留本地剪辑建议。", "advice": []}
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
        return {"enabled": True, "message": "内容建议已生成，仅供人工参考，不会自动删改视频。", "advice": [advice]}

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
    ) -> VideoEditTask:
        analysis = self._get_workflow_task(analysis_id, "analysis")
        if analysis.status != TaskStatus.SUCCEEDED:
            raise VideoEditorWorkflowError("请等待智能分析完成后再开始剪辑。")
        if subtitle_enabled:
            transcript_id = analysis.outputs.get("transcription_task_id", "")
            if not transcript_id:
                raise VideoEditorWorkflowError("本次剪辑要求字幕，请先完成字幕识别和人工复核。")
            if self.transcription_service.get_approved_revision(transcript_id) is None:
                raise VideoEditorWorkflowError("字幕尚未完成复核确认，无法开始剪辑。")
        source = self.resolve_source(analysis.outputs.get("source_id", ""))
        validated_steps = [
            VideoEditStep(kind=VideoEditStepKind(item["kind"]), params=dict(item.get("params") or {}), order=index)
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
            source_avatar_task_id=source["source_task_id"] if source["source_type"] == "avatar" else None,
            stage="等待剪辑",
            is_mock=False,
            outputs={"workflow": "edit", "analysis_id": analysis_id, "source_id": source["source_id"], "subtitle_enabled": str(subtitle_enabled).lower()},
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
                revision = self.transcription_service.get_approved_revision(transcript_id)
                if revision is None:
                    raise VideoEditorWorkflowError("字幕尚未完成复核确认，无法开始剪辑。")
                temp_srt = self.video_editing_service.output_directory / f"{task.task_id}.approved.srt"
                temp_srt.write_bytes(self.transcription_service.export_srt(revision.corrected_segments))
                config = config.model_copy(
                    update={
                        "steps": [
                            *config.steps,
                            VideoEditStep(kind=VideoEditStepKind.SUBTITLE, params={"srt_path": str(temp_srt), "style": "default"}, order=len(config.steps)),
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
            result = result.model_copy(update={"outputs": {**result.outputs, **task.outputs}})
            self.repository.save_task(result)
            if result.status == TaskStatus.FAILED:
                self._update(task, status=TaskStatus.FAILED, progress=result.progress, stage=result.stage, error_message=result.error_message or "剪辑失败")
        except Exception as exc:
            self._update(task, status=TaskStatus.FAILED, stage="剪辑失败", error_message=str(exc))
        finally:
            if temp_srt is not None:
                temp_srt.unlink(missing_ok=True)

    def get_job(self, task_id: str) -> dict[str, Any]:
        task = self.get_edit_task(task_id)
        return self._job_payload(task)

    def get_edit_task(self, task_id: str) -> VideoEditTask:
        return self._get_workflow_task(task_id, "edit")

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        tasks = [task for task in self.repository.list_tasks() if isinstance(task, VideoEditTask) and task.outputs.get("workflow") == "edit"]
        return [self._job_payload(task) for task in tasks[:limit]]

    # ------------------------------------------------------------------
    # 序列化与状态更新
    # ------------------------------------------------------------------
    def _get_workflow_task(self, task_id: str, workflow: str) -> VideoEditTask:
        task = self.repository.get_task(task_id)
        if not isinstance(task, VideoEditTask) or task.outputs.get("workflow") != workflow:
            raise VideoEditorWorkflowError("智能剪辑任务不存在。")
        return task

    def _update(self, task: VideoEditTask, **changes: Any) -> VideoEditTask:
        updated = task.model_copy(update={**changes, "updated_at": datetime.now().astimezone()})
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
        media_url = f"/api/v1/video-editor/jobs/{task.task_id}/media" if task.result_path else None
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "progress": task.progress,
            "stage": task.stage,
            "error_message": task.error_message,
            "result_size_bytes": task.result_size_bytes,
            "media_url": media_url,
            "download_url": f"/api/v1/video-editor/jobs/{task.task_id}/download" if task.result_path else None,
            "source_id": task.outputs.get("source_id"),
            "analysis_id": task.outputs.get("analysis_id"),
        }
