"""Company-hosted ASR gateway used by the Windows desktop client."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Security, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from project.backend.app.core.control_plane_operations import completed_operation_record
from project.backend.app.core.media_probe import get_media_duration_probe
from project.backend.app.core.provider_jobs import (
    claim_provider_job,
    provider_job_owned,
    register_provider_job,
)
from project.backend.app.core.repository import get_credits_service
from project.backend.app.core.security import require_admin_token
from project.backend.app.core.server_asr import get_server_asr_runtime
from src.services.cloud_transcription import (
    ASRAuthorization,
    ASR_PER_TASK_CAP_CNY,
    ASR_PRICE_VERSION,
    ASR_UNIT_PRICE_CNY_PER_SECOND,
)
from src.services.credits import CreditsService, InsufficientCreditsError, cny_to_credits
from src.services.video_editor_cloud import (
    CloudAsset,
    CloudTranscript,
    ProviderJobSnapshot,
    ProviderJobStatus,
)

router = APIRouter(prefix="/api/v1/provider/asr", tags=["provider-asr"])

MAX_ASR_UPLOAD_BYTES = 512 * 1024 * 1024
_TASK_ID_PATTERN = re.compile(r"^transcript-[a-f0-9]{10}$")


def _server_asr_enabled() -> bool:
    return os.getenv("ASR_MODE", "sandbox").strip().casefold() == "cloud"


def _require_server_asr_enabled() -> None:
    if not _server_asr_enabled():
        raise HTTPException(
            status_code=503,
            detail="公司云端转写尚未启用，请联系管理员。",
        )


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ASRChargeRequest(_StrictRequest):
    task_id: str = Field(pattern=r"^transcript-[a-f0-9]{10}$")
    duration_seconds: float = Field(gt=0, le=6 * 60 * 60)


class ASRChargeResponse(BaseModel):
    task_id: str
    estimated_cost_cny: str
    charged_credits: str
    price_version: str


class ASRSubmitRequest(_StrictRequest):
    task_id: str = Field(pattern=r"^transcript-[a-f0-9]{10}$")
    asset: dict[str, Any]
    language: str = Field(default="zh", pattern=r"^(auto|zh|en|ja|ko)$")


class ASRAuthorizationRequest(_StrictRequest):
    confirmed: bool
    per_task_cap_cny: Decimal = Field(default=ASR_PER_TASK_CAP_CNY, gt=0)


def _owner(request: Request) -> str:
    code = str(getattr(request.state, "customer_code", "")).strip()
    if not code:
        raise HTTPException(status_code=403, detail="需要客户身份。")
    return f"customer:{code}"


def _require_charge(request: Request, task_id: str):
    if provider_job_owned(
        provider_job_id=f"billing:void:asr:{task_id}",
        owner=_owner(request),
        kind="billing_marker",
    ):
        raise HTTPException(status_code=409, detail="本次转写预扣已退回，请重新创建任务。")
    record = _charge_record(request, task_id)
    if record is None:
        raise HTTPException(status_code=409, detail="请先确认本次转写费用。")
    return record


def _charge_record(request: Request, task_id: str):
    return completed_operation_record(
        owner=_owner(request),
        operation_type="/api/v1/provider/asr/authorize",
        idempotency_key=f"asr-charge-{task_id}",
    )


def _refund_and_void_charge(
    *,
    request: Request,
    task_id: str,
    reserved_credits: Decimal,
    credits: CreditsService,
    reason: str,
) -> None:
    owner = _owner(request)
    void_id = f"billing:void:asr:{task_id}"
    uploaded_id = f"billing:asset:asr:{task_id}"
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
            ref_type="transcription_refund",
            ref_id=task_id,
        )


@router.get("/capabilities")
def capabilities(runtime=Depends(get_server_asr_runtime)) -> dict[str, Any]:
    capability = dict(runtime.capability())
    if _server_asr_enabled():
        return capability
    missing = list(capability.get("missing_configuration") or [])
    if "ASR_MODE=cloud" not in missing:
        missing.insert(0, "ASR_MODE=cloud")
    capability.update(
        {
            "enabled": False,
            "live_ready": False,
            "billing_authorized": False,
            "missing_configuration": missing,
        }
    )
    return capability


@router.post("/admin/authorization")
def authorize_provider(
    body: ASRAuthorizationRequest,
    runtime=Depends(get_server_asr_runtime),
    _admin: bool = Security(require_admin_token),
) -> dict[str, Any]:
    _require_server_asr_enabled()
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="必须明确确认供应商费用。")
    authorization = ASRAuthorization(
        confirmed=True,
        provider_name="aliyun_fun_asr",
        price_version=ASR_PRICE_VERSION,
        unit_price_cny_per_second=ASR_UNIT_PRICE_CNY_PER_SECOND,
        per_task_cap_cny=body.per_task_cap_cny,
        confirmed_at=datetime.now().astimezone(),
    )
    runtime.authorization_store.save(authorization)
    return runtime.capability()


@router.post("/authorize", response_model=ASRChargeResponse)
def authorize_task(
    body: ASRChargeRequest,
    request: Request,
    runtime=Depends(get_server_asr_runtime),
    credits: CreditsService = Depends(get_credits_service),
) -> ASRChargeResponse:
    _require_server_asr_enabled()
    expected_key = f"asr-charge-{body.task_id}"
    if request.headers.get("Idempotency-Key") != expected_key:
        raise HTTPException(status_code=400, detail="本次转写请求标识无效。")
    try:
        estimated = runtime.ensure_authorized(body.duration_seconds)
        charged = cny_to_credits(estimated)
        if charged > 0:
            credits.debit(
                charged,
                "云端转写费用",
                ref_type="transcription",
                ref_id=body.task_id,
            )
    except InsufficientCreditsError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ASRChargeResponse(
        task_id=body.task_id,
        estimated_cost_cny=str(estimated),
        charged_credits=str(charged),
        price_version=ASR_PRICE_VERSION,
    )


@router.post("/upload", response_model=CloudAsset)
async def upload_asset(
    request: Request,
    file: UploadFile = File(...),
    task_id: str = Form(...),
    object_key: str = Form(...),
    media_type: str = Form(...),
    runtime=Depends(get_server_asr_runtime),
    credits: CreditsService = Depends(get_credits_service),
    duration_probe=Depends(get_media_duration_probe),
) -> CloudAsset:
    _require_server_asr_enabled()
    if not _TASK_ID_PATTERN.fullmatch(task_id):
        raise HTTPException(status_code=400, detail="转写任务编号无效。")
    charge_record = _require_charge(request, task_id)
    try:
        charge_payload = json.loads(charge_record.response_body or b"{}")
        reserved_credits = Decimal(str(charge_payload["charged_credits"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="本次转写费用记录无效。") from exc
    expected_prefix = f"asr-input/{task_id}/"
    if not object_key.startswith(expected_prefix) or ".." in object_key:
        _refund_and_void_charge(
            request=request,
            task_id=task_id,
            reserved_credits=reserved_credits,
            credits=credits,
            reason="云端转写素材校验失败退款",
        )
        raise HTTPException(status_code=400, detail="云端素材路径无效。")
    expected_hash = request.headers.get("X-Content-SHA256", "").strip().casefold()
    if not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
        _refund_and_void_charge(
            request=request,
            task_id=task_id,
            reserved_credits=reserved_credits,
            credits=credits,
            reason="云端转写素材校验失败退款",
        )
        raise HTTPException(status_code=400, detail="素材校验信息缺失。")

    suffix = Path(file.filename or "input.bin").suffix[:12]
    written = 0
    digest = hashlib.sha256()
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="videoinsight-asr-", suffix=suffix, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_ASR_UPLOAD_BYTES:
                    _refund_and_void_charge(
                        request=request,
                        task_id=task_id,
                        reserved_credits=reserved_credits,
                        credits=credits,
                        reason="云端转写素材过大退款",
                    )
                    raise HTTPException(status_code=413, detail="转写素材不能超过 512MB。")
                digest.update(chunk)
                temporary.write(chunk)
        if written == 0 or digest.hexdigest() != expected_hash:
            _refund_and_void_charge(
                request=request,
                task_id=task_id,
                reserved_credits=reserved_credits,
                credits=credits,
                reason="云端转写素材校验失败退款",
            )
            raise HTTPException(status_code=400, detail="素材上传不完整，请重新选择文件。")
        try:
            actual_duration = duration_probe(temporary_path)
            authoritative_cost = runtime.ensure_authorized(actual_duration)
            authoritative_credits = cny_to_credits(authoritative_cost)
            difference = authoritative_credits - reserved_credits
            if difference > 0:
                credits.debit(
                    difference,
                    "云端转写费用复核补扣",
                    ref_type="transcription_adjustment",
                    ref_id=task_id,
                )
            elif difference < 0:
                credits.credit(
                    abs(difference),
                    "云端转写费用复核退款",
                    ref_type="transcription_adjustment",
                    ref_id=task_id,
                )
        except InsufficientCreditsError as exc:
            _refund_and_void_charge(
                request=request,
                task_id=task_id,
                reserved_credits=reserved_credits,
                credits=credits,
                reason="云端转写未提交退款",
            )
            raise HTTPException(
                status_code=400,
                detail="按素材真实时长复核后积分不足，本次未提交且已退回预扣积分。",
            ) from exc
        except (RuntimeError, ValueError) as exc:
            _refund_and_void_charge(
                request=request,
                task_id=task_id,
                reserved_credits=reserved_credits,
                credits=credits,
                reason="云端转写素材无效退款",
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            asset = runtime.upload(
                temporary_path,
                object_key=object_key,
                media_type=media_type,
            )
            register_provider_job(
                provider_job_id=f"billing:asset:asr:{task_id}",
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


@router.post("/submit", response_model=ProviderJobSnapshot)
def submit_task(
    body: ASRSubmitRequest,
    request: Request,
    runtime=Depends(get_server_asr_runtime),
) -> ProviderJobSnapshot:
    _require_server_asr_enabled()
    _require_charge(request, body.task_id)
    if request.headers.get("Idempotency-Key") != f"asr-submit-{body.task_id}":
        raise HTTPException(status_code=400, detail="云端提交请求标识无效。")
    try:
        asset = CloudAsset.model_validate(body.asset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="云端素材信息无效。") from exc
    if not asset.object_key.startswith(f"asr-input/{body.task_id}/"):
        raise HTTPException(status_code=400, detail="云端素材与转写任务不匹配。")
    presign = getattr(runtime.providers.object_store, "presign_get_url", None)
    if callable(presign):
        asset = asset.model_copy(
            update={"provider_locator": presign(asset.object_key)}
        )
    try:
        snapshot = runtime.submit(asset, language=body.language)
        registered = register_provider_job(
            provider_job_id=snapshot.provider_job_id,
            owner=_owner(request),
            kind="asr",
        )
        if not registered:
            raise HTTPException(status_code=502, detail="云端任务编号冲突，请联系管理员。")
        return snapshot
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="云端识别提交结果暂时无法确认，系统不会自动重复提交。",
        ) from exc


@router.get("/jobs/{provider_job_id}", response_model=ProviderJobSnapshot)
def query_task(
    provider_job_id: str,
    request: Request,
    runtime=Depends(get_server_asr_runtime),
):
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", provider_job_id):
        raise HTTPException(status_code=400, detail="云端任务编号无效。")
    if not provider_job_owned(
        provider_job_id=provider_job_id,
        owner=_owner(request),
        kind="asr",
    ):
        raise HTTPException(status_code=404, detail="云端识别任务不存在。")
    try:
        return runtime.query(provider_job_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="暂时无法查询云端识别状态。") from exc


@router.get("/jobs/{provider_job_id}/result", response_model=CloudTranscript)
def fetch_result(
    provider_job_id: str,
    request: Request,
    runtime=Depends(get_server_asr_runtime),
):
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", provider_job_id):
        raise HTTPException(status_code=400, detail="云端任务编号无效。")
    if not provider_job_owned(
        provider_job_id=provider_job_id,
        owner=_owner(request),
        kind="asr",
    ):
        raise HTTPException(status_code=404, detail="云端识别任务不存在。")
    try:
        snapshot = runtime.query(provider_job_id)
        if snapshot.status != ProviderJobStatus.SUCCEEDED:
            raise HTTPException(status_code=409, detail="云端识别尚未完成。")
        return runtime.fetch_result(snapshot)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="暂时无法读取云端识别结果。") from exc
