"""转写任务 API —— 支持演示模式、文件上传和链接转写。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from project.backend.app.core.deps import (
    get_copywriting_service,
    get_transcription_service,
)
from project.backend.app.core.config import ASRMode, ASR_MODE
from project.backend.app.schemas.requests import (
    TranscriptionCreateRequest,
    TranscriptionRevisionRequest,
)
from project.backend.app.schemas.responses import TranscriptionResponse
from src.models import (
    CopywritingTask,
    TaskKind,
    TaskStatus,
    TranscriptSegment,
    TranscriptionTask,
)
from src.services.transcription import TranscriptionError

router = APIRouter(prefix="/api/v1/transcriptions", tags=["transcriptions"])


class TranscriptionUrlRequest(BaseModel):
    """链接转写请求"""

    url: str
    rights_confirmed: bool = True
    rights_holder: str = "API用户"
    model_name: str = "large-v3-turbo"


class VoiceoverDraftRequest(BaseModel):
    """从已批准转写生成短数字人口播稿。"""

    target_seconds: int = 45
    speech_rate: float = 1.0
    platform: str = "douyin"
    target_audience: str = ""
    tone: str = "casual"
    variant_count: int = 2


class VoiceoverDraftResponse(BaseModel):
    copywriting_task_id: str
    source_task_id: str
    source_revision_id: str
    status: str
    provider_name: str
    model_name: str
    is_mock: bool
    target_seconds: int | None = None
    target_characters: int
    source_characters: int
    result_text: str | None = None
    result_variants: list[str] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class VoiceoverDraftUpdateRequest(BaseModel):
    result_text: str = Field(..., min_length=1)
    result_variants: list[str] = Field(default_factory=list)


def _to_response(task, service=None) -> TranscriptionResponse:
    source_segments = task.segments or []
    if service is not None and not task.is_mock:
        revisions = service.repository.list_transcript_revisions(task.task_id)
        if revisions:
            source_segments = revisions[-1].corrected_segments
    segments = [
        {
            "start": s.start,
            "end": s.end,
            "text": s.text,
            "confidence": s.confidence,
            "needs_review": s.needs_review,
            "reviewed": s.reviewed,
        }
        for s in source_segments
    ]
    return TranscriptionResponse(
        task_id=task.task_id,
        title=task.title,
        status=task.status.value,
        progress=task.progress,
        stage=task.stage,
        media_name=task.media_name,
        model_name=task.model_name,
        duration_seconds=task.duration_seconds,
        approved_revision_id=task.approved_revision_id,
        low_confidence_count=sum(
            1
            for segment in source_segments
            if segment.needs_review and not segment.reviewed
        ),
        segments=segments,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@router.post("", response_model=TranscriptionResponse)
def create_transcription(
    body: TranscriptionCreateRequest,
    service=Depends(get_transcription_service),
):
    """创建演示转写任务（不上传真实文件）。"""
    try:
        task = service.create_mock_task(
            media_name=body.media_name,
            media_type=body.media_type,
            rights_confirmed=body.rights_confirmed,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_response(task, service)


@router.post("/upload", response_model=TranscriptionResponse)
async def upload_and_transcribe(
    file: UploadFile = File(..., description="视频文件（MP4 / MOV）"),
    rights_confirmed: bool = Form(True, description="是否确认拥有媒体处理权"),
    rights_holder: str = Form("", description="权利主体"),
    model_name: str = Form("large-v3-turbo", description="识别模型名称"),
    language: str = Form("zh", description="识别语言：auto/zh/en/ja/ko"),
    hotwords: str = Form("", description="专有词提示"),
    service=Depends(get_transcription_service),
) -> TranscriptionResponse:
    """上传视频文件并执行真实转写。

    根据 ASR_MODE 配置，可能调用：
    - sandbox：返回演示数据（忽略文件内容）
    - local：本地 faster-whisper 模型
    - cloud：云端 ASR 供应商（阿里云等）
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空。")

    try:
        media_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"文件读取失败: {exc}")

    if not rights_holder.strip():
        rights_holder = "匿名用户"

    try:
        task = service.create_task(
            media_name=file.filename,
            media_type=file.content_type or "video/mp4",
            media_bytes=media_bytes,
            rights_confirmed=rights_confirmed,
            rights_holder=rights_holder,
            model_name=model_name,
            language=language,
            hotwords=hotwords or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return _to_response(task, service)


@router.post("/url", response_model=TranscriptionResponse)
async def create_transcription_by_url(
    body: TranscriptionUrlRequest,
    service=Depends(get_transcription_service),
):
    """通过视频直链创建转写任务。

    支持 MP4/MOV 格式的 HTTPS 直链。
    抖音、快手等平台分享链接需要先获取视频直链。
    """
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="链接不能为空。")

    try:
        from src.services.video_source import fetch_authorized_video

        # 下载视频
        video = fetch_authorized_video(body.url)

        # 创建真实转写任务
        task = service.create_task(
            media_name=video.name,
            media_type=video.media_type,
            media_bytes=video.content,
            rights_confirmed=body.rights_confirmed,
            rights_holder=body.rights_holder.strip() or "API用户",
            model_name=body.model_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_response(task, service)


@router.get("/config", response_model=dict[str, Any])
def get_asr_config() -> dict[str, Any]:
    """返回当前 ASR 配置信息，供前端展示。"""
    return {
        "asr_mode": ASR_MODE.value,
        "supports_upload": ASR_MODE in {ASRMode.LOCAL, ASRMode.CLOUD},
        "description": {
            ASRMode.SANDBOX: "演示模式 —— 返回模拟数据",
            ASRMode.LOCAL: "本地模型 —— 使用 faster-whisper 识别",
            ASRMode.CLOUD: "云端识别 —— 调用阿里云等供应商 API",
        }.get(ASR_MODE, "未知模式"),
    }


@router.get("", response_model=list[TranscriptionResponse])
def list_transcriptions(
    limit: int = 50,
    service=Depends(get_transcription_service),
):
    """从 SQLite 加载转写历史。"""
    safe_limit = max(1, min(limit, 100))
    tasks = [
        task
        for task in service.repository.list_tasks()
        if getattr(task, "kind", None) == TaskKind.TRANSCRIPTION
    ][:safe_limit]
    return [_to_response(task, service) for task in tasks]


@router.get("/{task_id}", response_model=TranscriptionResponse)
def get_transcription(
    task_id: str,
    service=Depends(get_transcription_service),
):
    """获取转写任务详情。"""
    task = service.repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="转写任务不存在。")
    return _to_response(task, service)


@router.post("/{task_id}/revisions", response_model=dict[str, Any])
def save_transcription_revision(
    task_id: str,
    body: TranscriptionRevisionRequest,
    service=Depends(get_transcription_service),
):
    """保存校对版本；approve=true 时确认成稿。"""
    segments = [
        TranscriptSegment(
            start=item.start,
            end=item.end,
            text=item.text,
            confidence=item.confidence,
            needs_review=item.needs_review,
            reviewed=item.reviewed,
        )
        for item in body.segments
    ]
    try:
        revision = service.save_revision(
            task_id,
            segments,
            reviewer=body.reviewer,
            approve=body.approve,
        )
    except TranscriptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return revision.model_dump(mode="json")


@router.post(
    "/{task_id}/voiceover-drafts",
    response_model=VoiceoverDraftResponse,
)
def create_voiceover_draft(
    task_id: str,
    body: VoiceoverDraftRequest,
    transcription_service=Depends(get_transcription_service),
    copywriting_service=Depends(get_copywriting_service),
):
    """使用已确认成稿生成 15–60 秒的去重口播稿，不修改原始转写。"""
    if not 15 <= body.target_seconds <= 60:
        raise HTTPException(status_code=400, detail="目标时长必须在 15–60 秒之间。")
    if not 0.8 <= body.speech_rate <= 1.2:
        raise HTTPException(status_code=400, detail="语速必须在 0.8–1.2 倍之间。")
    if not 1 <= body.variant_count <= 3:
        raise HTTPException(status_code=400, detail="口播稿变体数量必须在 1–3 个之间。")

    revision = transcription_service.get_approved_revision(task_id)
    if revision is None:
        raise HTTPException(
            status_code=400,
            detail="请先完成低置信片段复核并确认成稿，再生成数字人口播稿。",
        )
    capability = copywriting_service.capabilities()
    if not capability.get("enabled", False):
        raise HTTPException(
            status_code=503,
            detail="真实 LLM 文案服务未配置，暂不能生成口播稿。",
        )

    source_text = _deduplicate_adjacent_segments(revision.corrected_segments)
    target_characters = max(
        50,
        min(800, round(body.target_seconds * 4 * body.speech_rate)),
    )
    rewrite_goal = (
        f"生成约 {body.target_seconds} 秒、约 {target_characters} 字的数字人口播稿。"
        "删除口头禅、寒暄、重复句和与主旨无关的绕话；合并重复观点。"
        "保留原文中可确认的核心观点、数字、专有名词和必要限定条件；"
        "不得把低置信或不确定内容补写成事实，不得虚构案例、效果或承诺。"
        "输出自然连续的纯口播正文，不要标题、分镜说明、括号注释或 Markdown。"
    )
    task = copywriting_service.rewrite(
        source_text=source_text,
        platform=body.platform,
        target_audience=body.target_audience,
        style_prompt="清晰、自然、适合数字人口播",
        target_length=target_characters,
        tone=body.tone,
        rewrite_goal=rewrite_goal,
        variant_count=body.variant_count,
        source_task_id=task_id,
        source_revision_id=revision.revision_id,
    )
    task = task.model_copy(
        update={
            "outputs": {
                **task.outputs,
                "voiceover_target_seconds": str(body.target_seconds),
                "voiceover_speech_rate": str(body.speech_rate),
            },
            "updated_at": datetime.now().astimezone(),
        }
    )
    copywriting_service.repository.save_task(task)
    return _voiceover_to_response(task, source_characters=len(source_text))


@router.get(
    "/{task_id}/voiceover-drafts",
    response_model=list[VoiceoverDraftResponse],
)
def list_voiceover_drafts(
    task_id: str,
    limit: int = 50,
    transcription_service=Depends(get_transcription_service),
    copywriting_service=Depends(get_copywriting_service),
):
    """列出当前转写任务下的口播稿版本。"""
    transcription_task = transcription_service.repository.get_task(task_id)
    if not isinstance(transcription_task, TranscriptionTask):
        raise HTTPException(status_code=404, detail="转写任务不存在。")
    safe_limit = max(1, min(limit, 100))
    drafts = [
        task
        for task in copywriting_service.list_tasks()
        if task.source_task_id == task_id
    ][:safe_limit]
    return [_voiceover_to_response(task) for task in drafts]


@router.patch(
    "/{task_id}/voiceover-drafts/{draft_id}",
    response_model=VoiceoverDraftResponse,
)
def update_voiceover_draft(
    task_id: str,
    draft_id: str,
    body: VoiceoverDraftUpdateRequest,
    transcription_service=Depends(get_transcription_service),
    copywriting_service=Depends(get_copywriting_service),
):
    """保存人工编辑后的口播稿，且只允许修改当前转写任务所属草稿。"""
    transcription_task = transcription_service.repository.get_task(task_id)
    if not isinstance(transcription_task, TranscriptionTask):
        raise HTTPException(status_code=404, detail="转写任务不存在。")

    task = copywriting_service.get_task(draft_id)
    if task is None or task.source_task_id != task_id:
        raise HTTPException(
            status_code=404, detail="口播稿不存在或不属于当前转写任务。"
        )

    variants = [item for item in body.result_variants if item.strip()]
    if not variants:
        variants = [body.result_text]
    variants[0] = body.result_text
    updated = task.model_copy(
        update={
            "status": TaskStatus.SUCCEEDED,
            "progress": 100,
            "stage": "人工编辑已保存",
            "result_text": variants[0],
            "result_variants": variants,
            "updated_at": datetime.now().astimezone(),
        }
    )
    copywriting_service.repository.save_task(updated)
    return _voiceover_to_response(updated)


def _voiceover_to_response(
    task: CopywritingTask,
    *,
    source_characters: int | None = None,
) -> VoiceoverDraftResponse:
    target_seconds = _int_output(task, "voiceover_target_seconds")
    return VoiceoverDraftResponse(
        copywriting_task_id=task.task_id,
        source_task_id=task.source_task_id or "",
        source_revision_id=task.source_revision_id or "",
        status=task.status.value,
        provider_name=task.provider_name,
        model_name=task.model_name,
        is_mock=task.is_mock,
        target_seconds=target_seconds,
        target_characters=task.target_length,
        source_characters=source_characters
        if source_characters is not None
        else len(task.source_text),
        result_text=task.result_text,
        result_variants=task.result_variants,
        token_usage=task.token_usage,
        error_message=task.error_message,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


def _int_output(task: CopywritingTask, key: str) -> int | None:
    try:
        raw = task.outputs.get(key)
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _deduplicate_adjacent_segments(segments: list[TranscriptSegment]) -> str:
    """只移除相邻完全重复片段；语义去重交给 LLM，原修订保持不变。"""
    lines: list[str] = []
    previous = ""
    for segment in segments:
        text = " ".join(segment.text.split()).strip()
        if not text or text == previous:
            continue
        lines.append(text)
        previous = text
    return "\n".join(lines)


@router.get("/{task_id}/export")
def export_transcription(
    task_id: str,
    format: str = "txt",
    service=Depends(get_transcription_service),
):
    """导出 TXT/JSON/SRT/ASS。真实转写必须先确认成稿，演示任务可直接导出。"""
    if format not in {"txt", "json", "srt", "ass"}:
        raise HTTPException(status_code=400, detail="导出格式只支持 txt/json/srt/ass。")
    task = service.repository.get_task(task_id)
    if not isinstance(task, TranscriptionTask):
        raise HTTPException(status_code=404, detail="转写任务不存在。")
    if task.is_mock:
        segments = task.segments
    else:
        revision = service.get_approved_revision(task_id)
        if revision is None:
            raise HTTPException(
                status_code=400, detail="真实转写需确认成稿后才能导出。"
            )
        segments = revision.corrected_segments
    exporter = {
        "txt": service.export_txt,
        "json": service.export_json,
        "srt": service.export_srt,
        "ass": service.export_ass,
    }[format]
    content = exporter(segments)
    media_type = "application/json" if format == "json" else "text/plain"
    filename = f"{task_id}.{format}"
    return Response(
        content=content,
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
