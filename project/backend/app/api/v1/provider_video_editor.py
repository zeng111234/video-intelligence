"""Company-hosted paid provider boundary for cloud video editing."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from project.backend.app.core.control_plane_operations import completed_operation_record
from project.backend.app.core.media_probe import get_media_duration_probe
from project.backend.app.core.provider_jobs import (
    claim_provider_job,
    provider_job_owned,
    register_provider_job,
)
from project.backend.app.core.repository import get_credits_service
from project.backend.app.core.server_video_editor import (
    ServerVideoEditorRuntime,
    get_server_video_editor_runtime,
)
from src.adapters.video_editor_cloud import CloudProviderError
from src.services.credits import CreditsService, InsufficientCreditsError, cny_to_credits
from src.services.video_editor_cloud import (
    CloudAsset,
    CloudTranscript,
    CostQuote,
    EditPlan,
    ProviderJobSnapshot,
    ProviderJobStatus,
    RenderRequest,
    create_cost_quote,
    get_cloud_capability,
)

router = APIRouter(
    prefix="/api/v1/provider/video-editor",
    tags=["provider-video-editor"],
)

MAX_VIDEO_EDITOR_UPLOAD_BYTES = 512 * 1024 * 1024
_BATCH_PATTERN = re.compile(r"^edit-batch-[a-f0-9]{12}$")
_JOB_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthorizeRequest(_StrictRequest):
    batch_id: str = Field(pattern=r"^edit-batch-[a-f0-9]{12}$")
    quote: dict[str, Any]
    max_cost_cny: Decimal = Field(gt=0)


class AuthorizeResponse(BaseModel):
    batch_id: str
    charged_credits: str
    authoritative_quote: dict[str, Any]


class SubmitASRRequest(_StrictRequest):
    batch_id: str = Field(pattern=r"^edit-batch-[a-f0-9]{12}$")
    asset: dict[str, Any]
    language_hints: list[str] = Field(default_factory=lambda: ["zh"], max_length=5)


class PlanRequest(_StrictRequest):
    batch_id: str = Field(pattern=r"^edit-batch-[a-f0-9]{12}$")
    transcript: str = Field(max_length=50_000)
    spoken_ranges: list[dict[str, float]] = Field(default_factory=list, max_length=5000)
    duration_seconds: float = Field(gt=0, le=6 * 60 * 60)
    segments: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)


class RenderSubmitRequest(_StrictRequest):
    batch_id: str = Field(pattern=r"^edit-batch-[a-f0-9]{12}$")
    render_request: dict[str, Any]


class OutputUrlResponse(BaseModel):
    url: str


def _owner(request: Request) -> str:
    code = str(getattr(request.state, "customer_code", "")).strip()
    if not code:
        raise HTTPException(status_code=403, detail="需要客户身份。")
    return f"customer:{code}"


def _charge_record(request: Request, batch_id: str):
    return completed_operation_record(
        owner=_owner(request),
        operation_type="/api/v1/provider/video-editor/authorize",
        idempotency_key=f"video-charge-{batch_id}",
    )


def _require_charge(request: Request, batch_id: str):
    if provider_job_owned(
        provider_job_id=f"billing:void:video:{batch_id}",
        owner=_owner(request),
        kind="billing_marker",
    ):
        raise HTTPException(status_code=409, detail="本次剪辑预扣已退回，请重新创建任务。")
    record = _charge_record(request, batch_id)
    if record is None:
        raise HTTPException(status_code=409, detail="请先确认本次云端剪辑费用。")
    return record


def _refund_and_void_charge(
    *,
    request: Request,
    batch_id: str,
    reserved_credits: Decimal,
    credits: CreditsService,
    reason: str,
) -> None:
    owner = _owner(request)
    void_id = f"billing:void:video:{batch_id}"
    uploaded_id = f"billing:asset:video:{batch_id}"
    if provider_job_owned(
        provider_job_id=uploaded_id, owner=owner, kind="billing_marker"
    ):
        return
    registered = claim_provider_job(
        provider_job_id=void_id,
        owner=owner,
        kind="billing_marker",
    )
    if not registered:
        return
    if reserved_credits > 0:
        credits.credit(
            reserved_credits,
            reason,
            ref_type="video_editor_refund",
            ref_id=batch_id,
        )


def _refund_deterministic_render_failure(
    *,
    request: Request,
    batch_id: str,
    credits: CreditsService,
) -> None:
    owner = _owner(request)
    void_id = f"billing:void:video:{batch_id}"
    net_amount = sum(
        (
            Decimal(str(row["amount"]))
            for row in credits.list_transactions(limit=1000)
            if row.get("ref_id") == batch_id
            and row.get("ref_type")
            in {
                "video_editor",
                "video_editor_adjustment",
                "video_editor_refund",
            }
        ),
        Decimal("0"),
    )
    outstanding = max(Decimal("0"), -net_amount)
    if outstanding > 0:
        credits.credit(
            outstanding,
            "云端渲染确定未提交退款",
            ref_type="video_editor_refund",
            ref_id=batch_id,
        )
    # The control-plane middleware serializes this route's fixed idempotency key.
    # Persist the refund before the void marker so a crash cannot leave the charge
    # permanently voided without first returning the customer's money.
    claim_provider_job(
        provider_job_id=void_id,
        owner=owner,
        kind="billing_marker",
    )


def _quote_quantities(quote: CostQuote) -> tuple[Decimal, Decimal]:
    input_seconds = Decimal("0")
    output_seconds = Decimal("0")
    for item in quote.line_items:
        if item.component == "speech_recognition":
            input_seconds = item.quantity
        elif item.component == "cloud_render":
            output_seconds = item.quantity * Decimal("60")
    if input_seconds <= 0 or output_seconds <= 0:
        raise ValueError("费用报价缺少素材或成片时长。")
    return input_seconds, output_seconds


def _authoritative_quote(
    runtime: ServerVideoEditorRuntime,
    quote: CostQuote,
) -> CostQuote:
    if quote.price_version != runtime.configuration.price_version:
        raise ValueError("云端价格已更新，请重新查看费用。")
    input_seconds, output_seconds = _quote_quantities(quote)
    return create_cost_quote(
        input_duration_seconds=input_seconds,
        output_duration_seconds=output_seconds,
        output_profile=quote.output_profile,
        price_version=runtime.configuration.price_version,
        ttl_seconds=runtime.configuration.quote_ttl_seconds,
    )


def _signed_asset(runtime: ServerVideoEditorRuntime, asset: CloudAsset | None):
    if asset is None:
        return None
    presign = getattr(runtime.providers.object_store, "presign_get_url", None)
    if callable(presign):
        return asset.model_copy(
            update={"provider_locator": presign(asset.object_key)}
        )
    return asset


@router.get("/capabilities")
def capabilities(
    runtime: ServerVideoEditorRuntime = Depends(get_server_video_editor_runtime),
):
    return get_cloud_capability(runtime.configuration).model_dump(mode="json")


@router.get("/outputs/{batch_id}/url", response_model=OutputUrlResponse)
def get_output_url(
    batch_id: str,
    object_key: str,
    request: Request,
    runtime: ServerVideoEditorRuntime = Depends(get_server_video_editor_runtime),
) -> OutputUrlResponse:
    """Sign only an output owned by the charged customer batch."""
    if not _BATCH_PATTERN.fullmatch(batch_id):
        raise HTTPException(status_code=400, detail="剪辑批次编号无效。")
    _require_charge(request, batch_id)
    expected = re.compile(
        rf"^video-editor-output/{re.escape(batch_id)}/output/(?:720p|1080p)\.mp4$"
    )
    if not expected.fullmatch(object_key) or ".." in object_key:
        raise HTTPException(status_code=400, detail="云成片路径无效。")
    presign = getattr(runtime.providers.object_store, "presign_get_url", None)
    if not callable(presign):
        raise HTTPException(status_code=503, detail="云成片下载服务暂不可用。")
    try:
        return OutputUrlResponse(url=str(presign(object_key, expires_seconds=3600)))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="云成片下载地址生成失败。") from exc


@router.post("/authorize", response_model=AuthorizeResponse)
def authorize(
    body: AuthorizeRequest,
    request: Request,
    runtime: ServerVideoEditorRuntime = Depends(get_server_video_editor_runtime),
    credits: CreditsService = Depends(get_credits_service),
):
    if request.headers.get("Idempotency-Key") != f"video-charge-{body.batch_id}":
        raise HTTPException(status_code=400, detail="本次剪辑请求标识无效。")
    capability = get_cloud_capability(runtime.configuration)
    if not capability.live_ready:
        raise HTTPException(status_code=503, detail="公司云端剪辑服务尚未配置完成。")
    try:
        client_quote = CostQuote.model_validate(body.quote)
        quote = _authoritative_quote(runtime, client_quote)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.max_cost_cny < quote.estimated_max:
        raise HTTPException(status_code=400, detail="确认的费用上限低于服务器报价。")
    charged = cny_to_credits(quote.estimated_total)
    try:
        if charged > 0:
            credits.debit(
                charged,
                "云端剪辑成片费用",
                ref_type="video_editor",
                ref_id=body.batch_id,
            )
    except InsufficientCreditsError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return AuthorizeResponse(
        batch_id=body.batch_id,
        charged_credits=str(charged),
        authoritative_quote=quote.model_dump(mode="json"),
    )


@router.post("/upload", response_model=CloudAsset)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    batch_id: str = Form(...),
    object_key: str = Form(...),
    media_type: str = Form(...),
    runtime: ServerVideoEditorRuntime = Depends(get_server_video_editor_runtime),
    credits: CreditsService = Depends(get_credits_service),
    duration_probe=Depends(get_media_duration_probe),
):
    if not _BATCH_PATTERN.fullmatch(batch_id):
        raise HTTPException(status_code=400, detail="剪辑批次编号无效。")
    charge_record = _require_charge(request, batch_id)
    try:
        charge_payload = json.loads(charge_record.response_body or b"{}")
        reserved_credits = Decimal(str(charge_payload["charged_credits"]))
        original_quote = CostQuote.model_validate(
            charge_payload["authoritative_quote"]
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="本次云端剪辑费用记录无效。") from exc
    expected_prefix = f"video-editor-input/{batch_id}/"
    if not object_key.startswith(expected_prefix) or ".." in object_key:
        _refund_and_void_charge(
            request=request,
            batch_id=batch_id,
            reserved_credits=reserved_credits,
            credits=credits,
            reason="云端剪辑素材校验失败退款",
        )
        raise HTTPException(status_code=400, detail="云端素材路径无效。")
    expected_hash = request.headers.get("X-Content-SHA256", "").strip().casefold()
    if not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
        _refund_and_void_charge(
            request=request,
            batch_id=batch_id,
            reserved_credits=reserved_credits,
            credits=credits,
            reason="云端剪辑素材校验失败退款",
        )
        raise HTTPException(status_code=400, detail="素材校验信息缺失。")

    written = 0
    digest = hashlib.sha256()
    temporary_path: Path | None = None
    try:
        suffix = Path(file.filename or "asset.bin").suffix[:12]
        with tempfile.NamedTemporaryFile(
            prefix="videoinsight-edit-", suffix=suffix, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_VIDEO_EDITOR_UPLOAD_BYTES:
                    _refund_and_void_charge(
                        request=request,
                        batch_id=batch_id,
                        reserved_credits=reserved_credits,
                        credits=credits,
                        reason="云端剪辑素材过大退款",
                    )
                    raise HTTPException(status_code=413, detail="云端剪辑素材不能超过 512MB。")
                digest.update(chunk)
                temporary.write(chunk)
        if written == 0 or digest.hexdigest() != expected_hash:
            _refund_and_void_charge(
                request=request,
                batch_id=batch_id,
                reserved_credits=reserved_credits,
                credits=credits,
                reason="云端剪辑素材校验失败退款",
            )
            raise HTTPException(status_code=400, detail="素材上传不完整，请重新选择文件。")

        if "/input/" in object_key and media_type.startswith("video/"):
            try:
                actual_duration = Decimal(str(duration_probe(temporary_path)))
                actual_quote = create_cost_quote(
                    input_duration_seconds=actual_duration,
                    output_duration_seconds=actual_duration + Decimal("1.4"),
                    output_profile=original_quote.output_profile,
                    price_version=runtime.configuration.price_version,
                    ttl_seconds=runtime.configuration.quote_ttl_seconds,
                )
                actual_credits = cny_to_credits(actual_quote.estimated_total)
                difference = actual_credits - reserved_credits
                if difference > 0:
                    credits.debit(
                        difference,
                        "云端剪辑费用复核补扣",
                        ref_type="video_editor_adjustment",
                        ref_id=batch_id,
                    )
                elif difference < 0:
                    credits.credit(
                        abs(difference),
                        "云端剪辑费用复核退款",
                        ref_type="video_editor_adjustment",
                        ref_id=batch_id,
                    )
            except (InsufficientCreditsError, ValueError) as exc:
                _refund_and_void_charge(
                    request=request,
                    batch_id=batch_id,
                    reserved_credits=reserved_credits,
                    credits=credits,
                    reason="云端剪辑未提交退款",
                )
                raise HTTPException(
                    status_code=400,
                    detail="按素材真实时长复核后无法继续，本次未提交且已退回预扣积分。",
                ) from exc
        try:
            asset = runtime.providers.object_store.upload(
                temporary_path,
                object_key,
                media_type=media_type,
            )
            register_provider_job(
                provider_job_id=f"billing:asset:video:{batch_id}",
                owner=_owner(request),
                kind="billing_marker",
            )
            return asset
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="云端素材上传结果暂时无法确认，系统不会自动重复提交。",
            ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@router.post("/asr/submit", response_model=ProviderJobSnapshot)
def submit_asr(
    body: SubmitASRRequest,
    request: Request,
    runtime: ServerVideoEditorRuntime = Depends(get_server_video_editor_runtime),
):
    _require_charge(request, body.batch_id)
    if request.headers.get("Idempotency-Key") != f"video-asr-{body.batch_id}":
        raise HTTPException(status_code=400, detail="云端识别请求标识无效。")
    try:
        asset = _signed_asset(runtime, CloudAsset.model_validate(body.asset))
        if not asset.object_key.startswith(
            f"video-editor-input/{body.batch_id}/input/"
        ):
            raise HTTPException(status_code=400, detail="云端素材与剪辑批次不匹配。")
        snapshot = runtime.providers.asr.submit(
            asset,
            language_hints=tuple(body.language_hints),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="云端素材信息无效。") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="云端识别提交结果暂时无法确认，系统不会自动重复提交。",
        ) from exc
    registered = register_provider_job(
        provider_job_id=snapshot.provider_job_id,
        owner=_owner(request),
        kind="video_asr",
    )
    if not registered:
        raise HTTPException(status_code=502, detail="云端识别任务编号冲突，请联系管理员。")
    return snapshot


@router.get("/asr/jobs/{job_id}", response_model=ProviderJobSnapshot)
def query_asr(
    job_id: str,
    request: Request,
    runtime: ServerVideoEditorRuntime = Depends(get_server_video_editor_runtime),
):
    if not _JOB_PATTERN.fullmatch(job_id) or not provider_job_owned(
        provider_job_id=job_id, owner=_owner(request), kind="video_asr"
    ):
        raise HTTPException(status_code=404, detail="云端识别任务不存在。")
    try:
        return runtime.providers.asr.query(job_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="暂时无法查询云端识别状态。") from exc


@router.get("/asr/jobs/{job_id}/result", response_model=CloudTranscript)
def fetch_asr(
    job_id: str,
    request: Request,
    runtime: ServerVideoEditorRuntime = Depends(get_server_video_editor_runtime),
):
    if not _JOB_PATTERN.fullmatch(job_id) or not provider_job_owned(
        provider_job_id=job_id, owner=_owner(request), kind="video_asr"
    ):
        raise HTTPException(status_code=404, detail="云端识别任务不存在。")
    try:
        snapshot = runtime.providers.asr.query(job_id)
        if snapshot.status != ProviderJobStatus.SUCCEEDED:
            raise HTTPException(status_code=409, detail="云端识别尚未完成。")
        return runtime.providers.asr.fetch_result(snapshot)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="暂时无法读取云端识别结果。") from exc


@router.post("/plan", response_model=EditPlan)
def create_plan(
    body: PlanRequest,
    request: Request,
    runtime: ServerVideoEditorRuntime = Depends(get_server_video_editor_runtime),
):
    _require_charge(request, body.batch_id)
    if request.headers.get("Idempotency-Key") != f"video-plan-{body.batch_id}":
        raise HTTPException(status_code=400, detail="智能方案请求标识无效。")
    try:
        return runtime.providers.edit_plan.create_plan(
            body.transcript,
            body.spoken_ranges,
            body.duration_seconds,
            body.segments,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="智能剪辑方案结果暂时无法确认，系统不会自动重复提交。",
        ) from exc


@router.post("/render/submit", response_model=ProviderJobSnapshot)
def submit_render(
    body: RenderSubmitRequest,
    request: Request,
    runtime: ServerVideoEditorRuntime = Depends(get_server_video_editor_runtime),
    credits: CreditsService = Depends(get_credits_service),
):
    _require_charge(request, body.batch_id)
    if request.headers.get("Idempotency-Key") != f"video-render-{body.batch_id}":
        raise HTTPException(status_code=400, detail="云端渲染请求标识无效。")
    try:
        render_request = RenderRequest.model_validate(body.render_request)
        render_request = render_request.model_copy(
            update={
                "input_asset": _signed_asset(runtime, render_request.input_asset),
                "merge_config_asset": _signed_asset(
                    runtime, render_request.merge_config_asset
                ),
                "bgm_asset": _signed_asset(runtime, render_request.bgm_asset),
                "opening_asset": _signed_asset(runtime, render_request.opening_asset),
            }
        )
        snapshot = runtime.providers.render.submit(render_request)
    except ValueError as exc:
        _refund_deterministic_render_failure(
            request=request,
            batch_id=body.batch_id,
            credits=credits,
        )
        raise HTTPException(status_code=422, detail="云端渲染参数无效。") from exc
    except CloudProviderError as exc:
        deterministic_failure = not exc.outcome_unknown and exc.kind in {
            "authorization",
            "configuration",
            "rate_limit",
            "validation",
        }
        if deterministic_failure:
            _refund_deterministic_render_failure(
                request=request,
                batch_id=body.batch_id,
                credits=credits,
            )
            raise HTTPException(
                status_code=422,
                detail="云端渲染被供应商明确拒绝，本次未提交且已退回剪辑积分。",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail="云端渲染提交结果暂时无法确认，系统不会自动重复提交。",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="云端渲染提交结果暂时无法确认，系统不会自动重复提交。",
        ) from exc
    registered = register_provider_job(
        provider_job_id=snapshot.provider_job_id,
        owner=_owner(request),
        kind="video_render",
    )
    if not registered:
        raise HTTPException(status_code=502, detail="云端渲染任务编号冲突，请联系管理员。")
    return snapshot


@router.get("/render/jobs/{job_id}", response_model=ProviderJobSnapshot)
def query_render(
    job_id: str,
    request: Request,
    runtime: ServerVideoEditorRuntime = Depends(get_server_video_editor_runtime),
):
    if not _JOB_PATTERN.fullmatch(job_id) or not provider_job_owned(
        provider_job_id=job_id, owner=_owner(request), kind="video_render"
    ):
        raise HTTPException(status_code=404, detail="云端渲染任务不存在。")
    try:
        return runtime.providers.render.query(job_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="暂时无法查询云端渲染状态。") from exc
