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
from datetime import datetime, timedelta
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
    VideoEditorBatch,
    VideoEditorBatchItem,
)


_WORKFLOW_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-workflow")
_MAX_SUBTITLE_BYTES = 50 * 1024 * 1024
_MAX_SUBTITLE_SECONDS = 15 * 60
_MAX_BATCH_ITEMS = 10
_MAX_BGM_BYTES = 30 * 1024 * 1024
_BGM_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac"}


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

        for task in self.repository.list_tasks():
            if not isinstance(task, VideoEditTask) or task.outputs.get("workflow") != "upload_source":
                continue
            path = Path(task.source_video_path)
            if task.status != TaskStatus.SUCCEEDED or task.is_mock or not path.is_file():
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

        if source_type == "upload":
            task = self.repository.get_task(record_id)
            if not isinstance(task, VideoEditTask) or task.outputs.get("workflow") != "upload_source":
                raise VideoEditorWorkflowError("上传素材不存在。")
            path = Path(task.source_video_path)
            if task.status != TaskStatus.SUCCEEDED or task.is_mock or not path.is_file():
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
        if len(media_bytes) > _MAX_SUBTITLE_BYTES:
            raise VideoEditorWorkflowError("上传素材超过 50MB 限制。")

        now = datetime.now().astimezone()
        task_id = f"upload-{uuid4().hex[:10]}"
        storage_dir = self.video_editing_service.output_directory.parent / "video_uploads"
        storage_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file_name).name) or f"source{suffix}"
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

    def list_bgm_assets(self) -> list[dict[str, Any]]:
        directory = self._bgm_directory()
        if not directory.is_dir():
            return []
        assets: list[dict[str, Any]] = []
        for metadata_path in directory.glob("bgm-*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
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
    ) -> dict[str, Any]:
        suffix = Path(file_name).suffix.lower()
        if suffix not in _BGM_SUFFIXES:
            raise VideoEditorWorkflowError("背景音乐仅支持 MP3、WAV、M4A、AAC 或 FLAC。")
        if not rights_confirmed or not rights_holder.strip():
            raise VideoEditorWorkflowError("请确认拥有音乐使用权并填写授权主体。")
        if not media_bytes:
            raise VideoEditorWorkflowError("上传的背景音乐为空。")
        if len(media_bytes) > _MAX_BGM_BYTES:
            raise VideoEditorWorkflowError("背景音乐超过 30MB 限制。")

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
        metadata = {
            "asset_id": asset_id,
            "title": Path(file_name).stem[:100] or "本地背景音乐",
            "original_name": Path(file_name).name,
            "stored_name": media_path.name,
            "media_type": media_type or mimetypes.guess_type(file_name)[0] or "audio/mpeg",
            "mood": (mood or "通用").strip()[:30],
            "rights_holder": rights_holder.strip()[:100],
            "rights_confirmed_at": now.isoformat(),
            "created_at": now.isoformat(),
            "duration_seconds": duration_seconds,
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
    def _bgm_payload(metadata: dict[str, Any], media_path: Path) -> dict[str, Any]:
        return {
            "asset_id": metadata["asset_id"],
            "title": metadata["title"],
            "original_name": metadata["original_name"],
            "media_type": metadata["media_type"],
            "mood": metadata["mood"],
            "rights_holder": metadata["rights_holder"],
            "rights_confirmed_at": metadata["rights_confirmed_at"],
            "created_at": metadata["created_at"],
            "duration_seconds": metadata["duration_seconds"],
            "size_bytes": media_path.stat().st_size,
            "media_url": f"/api/v1/video-editor/bgm/{metadata['asset_id']}/media",
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

    def _transcript_text(self, transcript_id: str) -> str:
        if not transcript_id:
            return ""
        task = self.repository.get_task(transcript_id)
        return "".join(str(segment.text).strip() for segment in getattr(task, "segments", []) if str(segment.text).strip())

    @staticmethod
    def _local_title_candidates(source_title: str, transcript_text: str, platform: str) -> list[str]:
        clean_source = re.sub(r"^(本地上传|流水线成片|系统数字人成片)\s*[·：:-]?\s*", "", source_title).strip()
        clean_source = Path(clean_source).stem
        first_sentence = re.split(r"[。！？!?；;\n]", transcript_text.strip(), maxsplit=1)[0]
        subject = re.sub(r"\s+", "", first_sentence or clean_source or "这条视频")[:22].rstrip("，,。.")
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
        publish_title: str | None = None,
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
        bgm_enabled: bool = False,
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
            items.append(VideoEditorBatchItem(source_id=source_id, title=source["title"], status="analyzing"))
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
            updated_items.append(item.model_copy(update={"analysis_id": analysis.task_id}))
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

    def continue_batch_item(self, batch_id: str, item_id: str) -> dict[str, Any]:
        batch = self._require_batch(batch_id)
        batch = self._sync_batch(batch)
        item = next((entry for entry in batch.items if entry.item_id == item_id), None)
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        if item.status != "awaiting_subtitle_review":
            raise VideoEditorWorkflowError("该素材当前无需继续字幕复核。")
        if not item.subtitle_task_id or self.transcription_service.get_approved_revision(item.subtitle_task_id) is None:
            raise VideoEditorWorkflowError("请先在当前页保存并确认字幕成稿。")
        batch = self._start_batch_render(batch, item)
        return self._batch_payload(batch)

    def select_batch_item_title(self, batch_id: str, item_id: str, title: str) -> dict[str, Any]:
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
                            "outputs": {**task.outputs, "publish_title": selected_title},
                            "updated_at": datetime.now().astimezone(),
                        }
                    )
                )
        return self._batch_payload(self._replace_batch_item(batch, updated))

    def retry_batch_item(self, batch_id: str, item_id: str) -> dict[str, Any]:
        batch = self._require_batch(batch_id)
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

    def confirm_batch_results(self, batch_id: str, item_ids: list[str]) -> dict[str, Any]:
        batch = self._sync_batch(self._require_batch(batch_id))
        selected = set(item_ids)
        if not selected:
            raise VideoEditorWorkflowError("请至少选择一条已生成成片。")
        updated_items: list[VideoEditorBatchItem] = []
        for item in batch.items:
            if item.item_id not in selected:
                updated_items.append(item)
                continue
            if item.status != "awaiting_output_confirmation":
                raise VideoEditorWorkflowError("只能确认已成功生成的成片。")
            updated_items.append(item.model_copy(update={"status": "ready_to_publish", "confirmed_at": datetime.now().astimezone(), "updated_at": datetime.now().astimezone()}))
        batch = batch.model_copy(update={"items": updated_items, "updated_at": datetime.now().astimezone()})
        self.repository.save_video_editor_batch(batch)
        return self._batch_payload(batch)

    def _require_batch(self, batch_id: str) -> VideoEditorBatch:
        batch = self.repository.get_video_editor_batch(batch_id)
        if batch is None:
            raise VideoEditorWorkflowError("智能剪辑批次不存在。")
        return batch

    def _sync_batch(self, batch: VideoEditorBatch) -> VideoEditorBatch:
        changed = False
        items: list[VideoEditorBatchItem] = []
        for item in batch.items:
            updated = item
            if item.status == "analyzing" and item.analysis_id:
                analysis = self.repository.get_task(item.analysis_id)
                if isinstance(analysis, VideoEditTask):
                    if self._is_interrupted(analysis):
                        updated = item.model_copy(update={"status": "interrupted", "error_message": "分析任务可能在服务重启时中断，请重试。"})
                    elif analysis.status == TaskStatus.FAILED:
                        updated = item.model_copy(update={"status": "failed", "error_message": analysis.error_message or "素材分析失败。"})
                    elif analysis.status == TaskStatus.SUCCEEDED:
                        transcript_id = analysis.outputs.get("transcription_task_id") or None
                        analysis_payload = self._analysis_payload(analysis)
                        title_candidates = list(analysis_payload.get("title_candidates") or [])
                        creative_updates = {
                            "title_candidates": title_candidates,
                            "selected_title": item.selected_title or (title_candidates[0] if title_candidates else item.title),
                        }
                        if batch.subtitle_enabled:
                            error = analysis.outputs.get("subtitle_error") or ""
                            updated = item.model_copy(
                                update={
                                    "status": "awaiting_subtitle_review" if transcript_id else "failed",
                                    "subtitle_task_id": transcript_id,
                                    "error_message": error or (None if transcript_id else "字幕生成失败。"),
                                    **creative_updates,
                                }
                            )
                        else:
                            updated = item.model_copy(update={"status": "ready_to_render", **creative_updates})
            if updated.status == "ready_to_render":
                batch = self._replace_batch_item(batch, updated, save=False)
                batch = self._start_batch_render(batch, updated, save=False)
                updated = next(entry for entry in batch.items if entry.item_id == item.item_id)
            elif updated.status == "rendering" and updated.edit_task_id:
                job = self.repository.get_task(updated.edit_task_id)
                if isinstance(job, VideoEditTask):
                    if self._is_interrupted(job):
                        updated = updated.model_copy(update={"status": "interrupted", "error_message": "剪辑任务可能在服务重启时中断，请重试。"})
                    elif job.status == TaskStatus.FAILED:
                        updated = updated.model_copy(update={"status": "failed", "error_message": job.error_message or "剪辑失败。"})
                    elif job.status == TaskStatus.SUCCEEDED:
                        updated = updated.model_copy(update={"status": "awaiting_output_confirmation"})
            if updated != item:
                changed = True
            items.append(updated.model_copy(update={"updated_at": datetime.now().astimezone()}) if updated != item else updated)
        synced = batch.model_copy(update={"items": items, "updated_at": datetime.now().astimezone() if changed else batch.updated_at})
        if changed:
            self.repository.save_video_editor_batch(synced)
        return synced

    def _start_batch_render(self, batch: VideoEditorBatch, item: VideoEditorBatchItem, *, save: bool = True) -> VideoEditorBatch:
        if not item.analysis_id:
            raise VideoEditorWorkflowError("缺少素材分析结果，无法开始剪辑。")
        analysis = self.get_analysis(item.analysis_id)
        steps = batch.steps or list(analysis.get("recommended_steps") or [])
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
            return None, "本机授权音乐库为空，本条保持原声；上传一首有使用权的音乐后即可自动配乐。"

        title_candidates = analysis.get("title_candidates") or []
        text = " ".join([source_title, *[str(value) for value in title_candidates]]).casefold()
        energetic_words = ("探店", "运动", "游戏", "促销", "开业", "挑战", "旅行", "展示", "节奏", "热血", "动感", "欢快")
        calm_words = ("教程", "知识", "口播", "讲解", "访谈", "故事", "情感", "经验", "舒缓", "安静", "温柔")
        desired = "动感" if any(word in text for word in energetic_words) else "舒缓" if any(word in text for word in calm_words) else "通用"

        mood_tokens = {
            "动感": ("动感", "节奏", "欢快", "热血", "活力", "电子", "运动"),
            "舒缓": ("舒缓", "安静", "温柔", "治愈", "叙事", "轻松", "钢琴"),
            "通用": ("通用", "百搭", "轻快"),
        }
        duration = float((analysis.get("media") or {}).get("duration_seconds") or 0)

        def score(asset: dict[str, Any]) -> tuple[int, int, str]:
            mood = str(asset.get("mood") or "").casefold()
            mood_score = sum(4 for token in mood_tokens[desired] if token in mood)
            mood_score += sum(1 for token in mood_tokens["通用"] if token in mood)
            asset_duration = float(asset.get("duration_seconds") or 0)
            covers_video = int(duration <= 0 or asset_duration >= duration)
            return mood_score, covers_video, str(asset.get("created_at") or "")

        selected = max(assets, key=score)
        reason = f"根据内容判断为“{desired}”氛围，自动选择授权音乐《{selected['title']}》并随人声压低音量。"
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

    def _replace_batch_item(self, batch: VideoEditorBatch, item: VideoEditorBatchItem, *, save: bool = True) -> VideoEditorBatch:
        updated = batch.model_copy(
            update={
                "items": [item if entry.item_id == item.item_id else entry for entry in batch.items],
                "updated_at": datetime.now().astimezone(),
            }
        )
        if save:
            self.repository.save_video_editor_batch(updated)
        return updated

    @staticmethod
    def _is_interrupted(task: VideoEditTask) -> bool:
        return task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING} and datetime.now().astimezone() - task.updated_at > timedelta(minutes=5)

    def _batch_payload(self, batch: VideoEditorBatch) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for item in batch.items:
            analysis = self.repository.get_task(item.analysis_id) if item.analysis_id else None
            job = self.repository.get_task(item.edit_task_id) if item.edit_task_id else None
            items.append(
                {
                    "item_id": item.item_id,
                    "source_id": item.source_id,
                    "title": item.title,
                    "status": item.status,
                    "analysis_id": item.analysis_id,
                    "subtitle_task_id": item.subtitle_task_id,
                    "edit_task_id": item.edit_task_id,
                    "title_candidates": item.title_candidates,
                    "selected_title": item.selected_title,
                    "selected_bgm_id": item.selected_bgm_id,
                    "bgm_reason": item.bgm_reason,
                    "error_message": item.error_message,
                    "confirmed_at": item.confirmed_at.isoformat() if item.confirmed_at else None,
                    "analysis": self._analysis_payload(analysis) if isinstance(analysis, VideoEditTask) else None,
                    "job": self._job_payload(job) if isinstance(job, VideoEditTask) else None,
                }
            )
        status = "ready_to_publish" if items and all(item["status"] == "ready_to_publish" for item in items) else "running"
        if any(item["status"] in {"analyzing", "rendering", "ready_to_render"} for item in items):
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
            "bgm": (
                {key: value for key, value in self.resolve_bgm_asset(batch.bgm_id).items() if key != "_path"}
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
            "publish_title": task.outputs.get("publish_title") or None,
        }
