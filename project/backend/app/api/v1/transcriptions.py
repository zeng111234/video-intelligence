"""转写任务 API —— 支持演示模式、文件上传和链接转写。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, Security, UploadFile
from pydantic import BaseModel, Field

from project.backend.app.core.deps import (
    get_copywriting_service,
    get_repository,
    get_transcription_service,
)
from project.backend.app.core.config import ASRMode, ASR_MODE
from project.backend.app.core.security import require_admin_token
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
from src.services.cloud_transcription import (
    ASRAuthorization,
    ASR_PER_TASK_CAP_CNY,
    ASR_PRICE_VERSION,
    ASR_UNIT_PRICE_CNY_PER_SECOND,
)

router = APIRouter(prefix="/api/v1/transcriptions", tags=["transcriptions"])


class TranscriptionUrlRequest(BaseModel):
    """链接转写请求"""

    url: str
    rights_confirmed: bool = True
    rights_holder: str = "API用户"
    model_name: str = "large-v3-turbo"


class ManualTextImportRequest(BaseModel):
    """用户将外部工具转写结果粘贴回来，不调用该工具的自动化接口。"""

    text: str = Field(..., min_length=1, max_length=50_000)
    rights_confirmed: bool = False
    rights_holder: str = Field(..., min_length=1, max_length=80)
    media_name: str = Field("豆包人工转写", min_length=1, max_length=200)
    candidate_id: str | None = None
    source_url: str | None = None


class VoiceoverDraftRequest(BaseModel):
    """从已批准转写生成短数字人口播稿。"""

    # 默认将长转写压缩为一条约 60 秒的最终口播稿；保留请求字段以兼容
    # 已有客户端的显式时长设置。
    target_seconds: int = 60
    speech_rate: float = 1.0
    platform: str = "douyin"
    target_audience: str = ""
    tone: str = "casual"
    # 新主路径只生成一份最终文案；字段保留用于兼容旧客户端请求。
    variant_count: int = 1


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
    draft_stage: str = "deduplicate"
    parent_task_id: str | None = None
    needs_manual_review: bool = True


class VoiceoverDraftUpdateRequest(BaseModel):
    result_text: str = Field(..., min_length=1)
    result_variants: list[str] = Field(default_factory=list)


class ComplianceDraftRequest(BaseModel):
    parent_draft_id: str = Field(..., min_length=1)


class TranscriptionHistoryDeleteResponse(BaseModel):
    deleted_count: int


class ASRAuthorizationRequest(BaseModel):
    confirmed: bool
    per_task_cap_cny: Decimal = Field(
        default=ASR_PER_TASK_CAP_CNY,
        gt=0,
        le=ASR_PER_TASK_CAP_CNY,
    )


def _to_response(task, service=None) -> TranscriptionResponse:
    source_segments = task.segments or []
    if service is not None and not task.is_mock:
        revisions = service.repository.list_transcript_revisions(task.task_id)
        if revisions:
            source_segments = revisions[-1].corrected_segments
    segments = []
    normalized_legacy_cloud_segments = False
    for s in source_segments:
        legacy_cloud_without_confidence = (
            task.provider_name == "aliyun_fun_asr"
            and s.confidence is None
            and s.needs_review
            and s.quality_status == "pending"
            and s.quality_note == "阿里云识别结果，等待人工复核。"
        )
        normalized_legacy_cloud_segments |= legacy_cloud_without_confidence
        segments.append(
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "confidence": s.confidence,
                "needs_review": False
                if legacy_cloud_without_confidence
                else s.needs_review,
                "reviewed": s.reviewed,
                "quality_status": "completed"
                if legacy_cloud_without_confidence
                else s.quality_status,
                "quality_source": s.quality_source,
                "quality_note": (
                    "阿里云识别完成，未返回片段置信度。"
                    if legacy_cloud_without_confidence
                    else s.quality_note
                ),
                "alternatives": s.alternatives,
                # Keep provider-native word clocks in the review response.
                # The video editor uses these timestamps for phrase grouping
                # and subtitle sync; omitting them here silently downgraded
                # an otherwise word-timed ASR result to sentence timing.
                "words": [
                    {
                        "start": word.get("start"),
                        "end": word.get("end"),
                        "text": str(
                            word.get("text") or word.get("word") or ""
                        ),
                    }
                    for word in (s.words or [])
                    if isinstance(word, dict)
                    and word.get("start") is not None
                    and word.get("end") is not None
                    and str(word.get("text") or word.get("word") or "").strip()
                ],
            }
        )
    response_uncertain_count = sum(
        1 for segment in segments if segment["needs_review"] and not segment["reviewed"]
    )
    return TranscriptionResponse(
        task_id=task.task_id,
        title=task.title,
        status=task.status.value,
        progress=task.progress,
        stage=(
            "识别完成"
            if normalized_legacy_cloud_segments
            and response_uncertain_count == 0
            and task.stage == "待人工复核"
            else task.stage
        ),
        media_name=task.media_name,
        model_name=task.model_name,
        provider_name=task.provider_name,
        provider_job_id=task.provider_job_id,
        provider_status=task.provider_status,
        estimated_cost_cny=task.estimated_cost_cny,
        pricing_version=task.pricing_version,
        billing_authorized=task.billing_authorized,
        source_kind=task.source_kind,
        timing_available=task.timing_available,
        duration_seconds=task.duration_seconds,
        approved_revision_id=task.approved_revision_id,
        low_confidence_count=(
            task.uncertain_segment_count
            if task.auto_reviewed
            else response_uncertain_count
        ),
        is_mock=task.is_mock,
        auto_reviewed=task.auto_reviewed,
        uncertain_segment_count=(
            response_uncertain_count
            if task.provider_name == "aliyun_fun_asr"
            else task.uncertain_segment_count
        ),
        secondary_asr_count=task.secondary_asr_count,
        llm_review_count=task.llm_review_count,
        auto_review_error=task.auto_review_error,
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


@router.post("/upload", response_model=TranscriptionResponse, status_code=202)
async def upload_and_transcribe(
    file: UploadFile = File(..., description="视频文件（MP4 / MOV）"),
    rights_confirmed: bool = Form(True, description="是否确认拥有媒体处理权"),
    rights_holder: str = Form("", description="权利主体"),
    model_name: str = Form("large-v3-turbo", description="识别模型名称"),
    language: str = Form("zh", description="识别语言：auto/zh/en/ja/ko"),
    hotwords: str = Form("", description="专有词提示"),
    candidate_id: str = Form("", description="可选的候选素材编号"),
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
        if ASR_MODE == ASRMode.SANDBOX and service.cloud_runtime is None:
            task = service.create_mock_task(
                media_name=file.filename,
                media_type=file.content_type or "video/mp4",
                rights_confirmed=rights_confirmed,
                candidate_id=candidate_id.strip() or None,
            )
        else:
            task = service.create_task(
                media_name=file.filename,
                media_type=file.content_type or "video/mp4",
                media_bytes=media_bytes,
                rights_confirmed=rights_confirmed,
                rights_holder=rights_holder,
                candidate_id=candidate_id.strip() or None,
                model_name=model_name,
                language=language,
                hotwords=hotwords or None,
                async_processing=service.cloud_runtime is not None,
            )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return _to_response(task, service)


@router.post("/url", response_model=TranscriptionResponse, status_code=202)
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
            async_processing=service.cloud_runtime is not None,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_response(task, service)


@router.get("/config", response_model=dict[str, Any])
def get_asr_config(
    service=Depends(get_transcription_service),
    _admin: bool = Security(require_admin_token),
) -> dict[str, Any]:
    """返回当前 ASR 配置信息（仅管理员），供前端展示。"""
    if service.cloud_runtime is not None:
        capability = service.cloud_runtime.capability()
        return {
            "asr_mode": "cloud",
            "supports_upload": capability["enabled"],
            "description": "公司阿里云语音识别，不使用客户电脑 CPU。",
            **capability,
        }
    return {
        "asr_mode": ASR_MODE.value,
        "supports_upload": ASR_MODE in {ASRMode.LOCAL, ASRMode.CLOUD},
        "description": {
            ASRMode.SANDBOX: "演示模式 —— 返回模拟数据",
            ASRMode.LOCAL: "本地模型 —— 使用 faster-whisper 识别",
            ASRMode.CLOUD: "云端识别 —— 调用阿里云等供应商 API",
        }.get(ASR_MODE, "未知模式"),
    }


@router.get("/capabilities", response_model=dict[str, Any])
def get_customer_asr_capabilities(
    service=Depends(get_transcription_service),
) -> dict[str, Any]:
    """只返回客户页面需要的计费边界，不暴露供应商密钥或内部配置名。"""
    if service.cloud_runtime is not None:
        capability = service.cloud_runtime.capability()
        return {
            "mode": "cloud",
            "is_mock": False,
            "supports_upload": bool(capability.get("enabled")),
            "description": "公司云端语音识别，提交前需要确认费用。",
        }
    is_mock = ASR_MODE == ASRMode.SANDBOX
    return {
        "mode": ASR_MODE.value,
        "is_mock": is_mock,
        "supports_upload": ASR_MODE in {ASRMode.SANDBOX, ASRMode.LOCAL, ASRMode.CLOUD},
        "description": (
            "本地演示，不调用真实云服务、不扣积分。"
            if is_mock
            else "识别方式已由管理员配置。"
        ),
    }


@router.post("/authorization", response_model=dict[str, Any])
def authorize_cloud_asr(
    body: ASRAuthorizationRequest,
    service=Depends(get_transcription_service),
    _admin: bool = Security(require_admin_token),
):
    remote_authorize = getattr(service.cloud_runtime, "authorize_provider", None)
    if callable(remote_authorize):
        if not body.confirmed:
            raise HTTPException(status_code=400, detail="必须明确确认费用授权。")
        try:
            return remote_authorize(
                confirmed=True,
                per_task_cap_cny=body.per_task_cap_cny,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    if ASR_MODE != ASRMode.CLOUD or service.cloud_runtime is None:
        raise HTTPException(
            status_code=400,
            detail="请先由管理员将 ASR_MODE 切换为 cloud。",
        )
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="必须明确确认费用授权。")
    authorization = ASRAuthorization(
        confirmed=True,
        provider_name="aliyun_fun_asr",
        price_version=ASR_PRICE_VERSION,
        unit_price_cny_per_second=ASR_UNIT_PRICE_CNY_PER_SECOND,
        per_task_cap_cny=body.per_task_cap_cny,
        confirmed_at=datetime.now().astimezone(),
    )
    service.cloud_runtime.authorization_store.save(authorization)
    return service.cloud_runtime.capability()


@router.post("/{task_id}/reconnect", response_model=TranscriptionResponse)
def reconnect_cloud_transcription(
    task_id: str,
    service=Depends(get_transcription_service),
):
    task = service.repository.get_task(task_id)
    if not isinstance(task, TranscriptionTask):
        raise HTTPException(status_code=404, detail="转写任务不存在。")
    if not task.provider_job_id:
        raise HTTPException(
            status_code=400,
            detail="该任务没有可查询的阿里云任务 ID，不能安全重连。",
        )
    try:
        refreshed = service.process_cloud_task(task_id)
    except TranscriptionError:
        refreshed = service.repository.get_task(task_id)
    return _to_response(refreshed, service)


@router.post("/{task_id}/retry", response_model=TranscriptionResponse, status_code=202)
def retry_cloud_transcription(
    task_id: str,
    service=Depends(get_transcription_service),
):
    task = service.repository.get_task(task_id)
    if not isinstance(task, TranscriptionTask):
        raise HTTPException(status_code=404, detail="转写任务不存在。")
    if task.status != TaskStatus.FAILED:
        raise HTTPException(
            status_code=400,
            detail="只有阿里云已明确失败的任务才能重新识别；结果未知时不能重提。",
        )
    if task.retry_count >= 1:
        raise HTTPException(status_code=400, detail="该任务已经重试过一次。")
    retrying = task.model_copy(
        update={
            "status": TaskStatus.QUEUED,
            "stage": "等待重新识别",
            "progress": 5,
            "provider_job_id": None,
            "provider_status": "queued",
            "error_message": None,
            "retry_count": task.retry_count + 1,
            "updated_at": datetime.now().astimezone(),
        }
    )
    service.repository.save_task(retrying)
    return _to_response(retrying, service)


@router.get("", response_model=list[TranscriptionResponse])
def list_transcriptions(
    limit: int = 50,
    include_mock: bool = False,
    service=Depends(get_transcription_service),
):
    """从 SQLite 加载转写历史；默认不混入演示任务。"""
    safe_limit = max(1, min(limit, 100))
    tasks = [
        task
        for task in service.repository.list_tasks()
        if getattr(task, "kind", None) == TaskKind.TRANSCRIPTION
        and (include_mock or not getattr(task, "is_mock", False))
    ][:safe_limit]
    return [_to_response(task, service) for task in tasks]


@router.delete("/history", response_model=TranscriptionHistoryDeleteResponse)
def clear_transcription_history(repo=Depends(get_repository)):
    """删除右侧历史中的全部转写任务及其校对修订，不影响其他类型任务。"""
    task_ids = [
        task.task_id
        for task in repo.list_tasks()
        if isinstance(task, TranscriptionTask)
    ]
    for task_id in task_ids:
        repo.delete_task(task_id)
    return TranscriptionHistoryDeleteResponse(deleted_count=len(task_ids))


@router.post("/manual-text", response_model=TranscriptionResponse)
def import_manual_text(
    body: ManualTextImportRequest,
    service=Depends(get_transcription_service),
):
    """创建零成本人工回填任务，后续仍需人工复核后才可导出。"""
    try:
        task = service.import_manual_text(
            text=body.text,
            rights_confirmed=body.rights_confirmed,
            rights_holder=body.rights_holder,
            media_name=body.media_name,
            candidate_id=body.candidate_id,
            source_url=body.source_url,
        )
    except TranscriptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(task, service)


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
            quality_status=item.quality_status,
            quality_source=item.quality_source,
            quality_note=item.quality_note,
            alternatives=item.alternatives,
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
    existing_draft = next(
        (
            task
            for task in copywriting_service.list_tasks()
            if task.source_task_id == task_id
            and task.source_revision_id == revision.revision_id
            and task.status == TaskStatus.SUCCEEDED
            and task.outputs.get("draft_stage", "deduplicate") == "deduplicate"
        ),
        None,
    )
    if (
        existing_draft is not None
        and copywriting_service._spoken_character_count(
            existing_draft.result_text or ""
        )
        <= target_characters
    ):
        return _voiceover_to_response(existing_draft)

    rewrite_goal = (
        f"生成不超过约 {body.target_seconds} 秒、约 {target_characters} 字的数字人口播稿；内容不足时自然缩短，不要灌水。"
        "删除口头禅、寒暄、重复句和与主旨无关的绕话；合并重复观点。"
        "开头前3秒必须用原文可确认的信息重写一个有吸引力的钩子；"
        "如果原文没有强钩子，只能用问题、反差或痛点重组，不能编造事实。"
        "保留原文中可确认的核心观点、数字、专有名词和必要限定条件；"
        "不得把低置信或不确定内容补写成事实，不得虚构案例、效果或承诺。"
        "结尾补一个基于原文的自然行动引导或下一步建议；原文没有明确行动时使用中性总结，"
        "不得凭空添加购买、私信、关注、收益或效果承诺。"
        "只输出一份自然连续的纯口播正文，不要多个版本、标题、分镜说明、括号注释或 Markdown。"
    )
    task = copywriting_service.rewrite(
        source_text=source_text,
        platform=body.platform,
        target_audience=body.target_audience,
        style_prompt="清晰、自然、适合数字人口播",
        target_length=target_characters,
        tone=body.tone,
        rewrite_goal=rewrite_goal,
        variant_count=1,
        source_task_id=task_id,
        source_revision_id=revision.revision_id,
    )
    task = task.model_copy(
        update={
            "outputs": {
                **task.outputs,
                "voiceover_target_seconds": str(body.target_seconds),
                "voiceover_speech_rate": str(body.speech_rate),
                "draft_stage": "deduplicate",
                "needs_manual_review": "true",
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
        if task.source_task_id == task_id and task.outputs.get("draft_stage", "deduplicate") == "deduplicate"
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


@router.post("/{task_id}/compliance-drafts", response_model=VoiceoverDraftResponse)
def create_compliance_draft(
    task_id: str,
    body: ComplianceDraftRequest,
    transcription_service=Depends(get_transcription_service),
    copywriting_service=Depends(get_copywriting_service),
):
    """Create a clearly labelled compliance review draft from a saved de-duplicated draft."""
    if transcription_service.get_approved_revision(task_id) is None:
        raise HTTPException(status_code=400, detail="请先确认转写成稿。")
    parent = copywriting_service.get_task(body.parent_draft_id)
    if parent is None or parent.source_task_id != task_id or parent.outputs.get("draft_stage", "deduplicate") != "deduplicate":
        raise HTTPException(status_code=404, detail="去重口播稿不存在或不属于当前转写任务。")
    source_text = (parent.result_text or (parent.result_variants[0] if parent.result_variants else "")).strip()
    if not source_text:
        raise HTTPException(status_code=400, detail="请先保存有内容的去重口播稿。")
    capability = copywriting_service.capabilities()
    if not capability.get("enabled", False):
        raise HTTPException(status_code=503, detail="真实 LLM 文案服务未配置，暂不能生成合规优化稿。")
    task = copywriting_service.rewrite(
        source_text=source_text,
        platform="douyin",
        target_audience="",
        style_prompt="清晰、克制、适合短视频口播",
        target_length=parent.target_length,
        tone="casual",
        rewrite_goal=(
            "在不增加事实的前提下做合规表达优化：删除绝对化、夸大收益、保证性承诺、"
            "未经证实的比较、诱导性或可能侵害他人权利的模仿表达。保留必要限定条件；"
            "不虚构资质、案例、数据、效果或授权。输出纯口播正文，并标记为待人工复核稿。"
        ),
        variant_count=1,
        source_task_id=task_id,
        source_revision_id=parent.source_revision_id,
    )
    task = task.model_copy(update={"outputs": {**task.outputs, "draft_stage": "compliance", "parent_task_id": parent.task_id, "needs_manual_review": "true"}, "updated_at": datetime.now().astimezone()})
    copywriting_service.repository.save_task(task)
    return _voiceover_to_response(task, source_characters=len(source_text))


@router.get("/{task_id}/compliance-drafts", response_model=list[VoiceoverDraftResponse])
def list_compliance_drafts(
    task_id: str,
    limit: int = 50,
    transcription_service=Depends(get_transcription_service),
    copywriting_service=Depends(get_copywriting_service),
):
    if not isinstance(transcription_service.repository.get_task(task_id), TranscriptionTask):
        raise HTTPException(status_code=404, detail="转写任务不存在。")
    drafts = [task for task in copywriting_service.list_tasks() if task.source_task_id == task_id and task.outputs.get("draft_stage") == "compliance"]
    return [_voiceover_to_response(task) for task in drafts[:max(1, min(limit, 100))]]


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
        draft_stage=task.outputs.get("draft_stage", "deduplicate"),
        parent_task_id=task.outputs.get("parent_task_id"),
        needs_manual_review=task.outputs.get("needs_manual_review", "true").lower() != "false",
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
    if format in {"srt", "ass"} and any(
        segment.start is None or segment.end is None for segment in segments
    ):
        raise HTTPException(
            status_code=400,
            detail="人工导入文案没有时间轴，只支持 TXT/JSON 导出。",
        )
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
