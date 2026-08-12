"""Company-hosted digital-avatar provider and billing boundary."""

from __future__ import annotations

import hashlib
import mimetypes
import re
import tempfile
from decimal import Decimal
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from project.backend.app.core.provider_jobs import (
    provider_job_owned,
    register_provider_job,
)
from project.backend.app.core.avatar_media import (
    custom_voice_sample_path,
    validate_avatar_training_video,
    validate_voice_training_sample,
)
from project.backend.app.core.repository import get_credits_service
from project.backend.app.core.server_avatar import get_server_avatar_provider
from src.adapters.avatar import AvatarProviderError, ShuyingLegacyAvatarProvider
from src.models import (
    AvatarAsset,
    AvatarAssetKind,
    AvatarBillingQuote,
    AvatarCapability,
    AvatarJobSnapshot,
    AvatarProviderStatus,
    AvatarSubmitRequest,
    ProviderMode,
)
from src.services.credits import CreditsService, InsufficientCreditsError
from src.services.avatar_billing import build_avatar_billing_quote
from src.services.pricing import get_price

router = APIRouter(prefix="/api/v1/provider/avatar", tags=["provider-avatar"])

MAX_RESULT_BYTES = 100 * 1024 * 1024
MAX_FACE_BYTES = 500 * 1024 * 1024
MAX_VOICE_BYTES = 20 * 1024 * 1024
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_REQUEST_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,90}$")


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AvatarSubmitBody(_StrictRequest):
    request: AvatarSubmitRequest


class AvatarResumeBody(_StrictRequest):
    request: AvatarSubmitRequest
    pending_job_id: str
    attempt_id: str


def _principal(request: Request) -> tuple[str, str]:
    code = str(getattr(request.state, "customer_code", "")).strip()
    if code:
        return "customer", code
    username = str(getattr(request.state, "admin_username", "")).strip()
    if username:
        return "admin", username
    raise HTTPException(status_code=403, detail="需要客户或管理员身份。")


def _owner(request: Request) -> str:
    role, subject = _principal(request)
    return f"{role}:{subject}"


def _credit_owner(request: Request) -> str:
    role, subject = _principal(request)
    return subject if role == "customer" else "admin"


def _customer_capability(provider) -> AvatarCapability:
    capability = provider.capabilities()
    if capability.mode != ProviderMode.PRODUCTION:
        return capability.model_copy(
            update={
                "enabled": False,
                "permission_status": "production_required",
                "missing_configuration": ["公司数字人正式线路尚未配置"],
            }
        )
    # 数字人费用取决于本次文案和最终成片时长，能力接口不能再用固定 45 秒
    # 冒充任务报价。页面通过任务级 quote 获取预留上限。
    return capability.model_copy(
        update={"estimated_cost_cny": None, "estimated_seconds": None}
    )


def _require_live(provider) -> AvatarCapability:
    capability = _customer_capability(provider)
    if not capability.enabled:
        raise HTTPException(status_code=503, detail="公司数字人服务尚未配置完成。")
    if get_price("avatar_per_minute_cny") <= 0:
        raise HTTPException(status_code=503, detail="数字人生成价格尚未配置。")
    return capability


def _quote(script_text: str, speech_rate: float) -> AvatarBillingQuote:
    return build_avatar_billing_quote(
        script_text=script_text,
        speech_rate=speech_rate,
        price_per_minute_cny=get_price("avatar_per_minute_cny"),
    )


def _bind_billing_snapshot(
    *, snapshot: AvatarJobSnapshot, credits: CreditsService, request: Request
) -> None:
    if not snapshot.job_id:
        return
    credits.repository.bind_avatar_billing_job(
        owner=_credit_owner(request),
        idempotency_key=snapshot.idempotency_key,
        provider_job_id=snapshot.job_id,
    )


def _billing_snapshot(
    *, snapshot: AvatarJobSnapshot, credits: CreditsService, request: Request
) -> AvatarJobSnapshot:
    credit_owner = _credit_owner(request)
    record = credits.repository.get_avatar_billing(
        owner=credit_owner,
        idempotency_key=snapshot.idempotency_key or None,
        provider_job_id=None if snapshot.idempotency_key else snapshot.job_id,
    )
    if record is None:
        return snapshot
    if snapshot.idempotency_key and snapshot.job_id:
        credits.repository.bind_avatar_billing_job(
            owner=credit_owner,
            idempotency_key=snapshot.idempotency_key,
            provider_job_id=snapshot.job_id,
        )
    if snapshot.status == AvatarProviderStatus.SUCCEEDED:
        seconds = int(snapshot.estimated_seconds or 0)
        if seconds <= 0:
            return snapshot.model_copy(
                update={
                    "estimated_cost_cny": float(
                        Decimal(str(record["reserved_credits"]))
                    ),
                    "estimated_seconds": int(record["reserved_seconds"]),
                    "stage": f"{snapshot.stage}；成片时长待核对，预留积分尚未最终结算",
                }
            )
        try:
            record = credits.repository.settle_avatar_billing(
                owner=credit_owner,
                idempotency_key=str(record["idempotency_key"]),
                final_seconds=seconds,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        final_credits = Decimal(str(record["final_credits"]))
        return snapshot.model_copy(
            update={
                "estimated_cost_cny": float(final_credits),
                "estimated_seconds": int(record["final_seconds"]),
                "stage": (
                    f"{snapshot.stage}；按 {record['final_seconds']} 秒结算 {final_credits} 积分"
                ),
            }
        )
    return snapshot.model_copy(
        update={
            "estimated_cost_cny": float(Decimal(str(record["reserved_credits"]))),
            "estimated_seconds": int(record["reserved_seconds"]),
        }
    )


def _provider_error(exc: AvatarProviderError, fallback: str) -> HTTPException:
    status = (
        400
        if exc.kind.value == "validation"
        else 503
        if exc.kind.value == "authorization"
        else 502
    )
    message = str(exc).strip() or fallback
    if exc.outcome_unknown:
        message = f"{message} 结果暂时无法确认，系统不会自动重复提交。"
    return HTTPException(status_code=status, detail=message)


def _register_snapshot(snapshot: AvatarJobSnapshot, *, owner: str) -> None:
    identifiers = {snapshot.job_id, snapshot.provider_job_id or ""} - {""}
    for identifier in identifiers:
        if not _OPAQUE_ID.fullmatch(identifier) or not register_provider_job(
            provider_job_id=identifier,
            owner=owner,
            kind="avatar",
        ):
            raise HTTPException(
                status_code=502, detail="数字人任务编号冲突，请联系管理员。"
            )


def _visible_assets(provider, *, owner: str) -> list[AvatarAsset]:
    result: list[AvatarAsset] = []
    for asset in provider.list_assets():
        if not asset.authorized:
            continue
        if (
            asset.shared
            or asset.source_type in {"built_in", "public"}
            or provider_job_owned(
                provider_job_id=asset.asset_id,
                owner=owner,
                kind="avatar_asset",
            )
        ):
            result.append(asset)
    return result


@router.get("/capabilities", response_model=AvatarCapability)
def capabilities(provider=Depends(get_server_avatar_provider)):
    return _customer_capability(provider)


@router.get("/quote", response_model=AvatarBillingQuote)
def quote(
    characters: int = Query(..., ge=1, le=2000),
    speech_rate: float = Query(1.0, ge=0.8, le=1.2),
    provider=Depends(get_server_avatar_provider),
):
    _require_live(provider)
    return _quote("字" * characters, speech_rate)


@router.get("/assets", response_model=list[AvatarAsset])
def assets(request: Request, provider=Depends(get_server_avatar_provider)):
    _require_live(provider)
    try:
        return _visible_assets(provider, owner=_owner(request))
    except AvatarProviderError as exc:
        raise _provider_error(exc, "暂时无法读取数字人素材。") from exc


@router.post("/submit", response_model=AvatarJobSnapshot)
def submit(
    body: AvatarSubmitBody,
    request: Request,
    provider=Depends(get_server_avatar_provider),
    credits: CreditsService = Depends(get_credits_service),
):
    _require_live(provider)
    provider_request = body.request
    if not _REQUEST_KEY.fullmatch(provider_request.idempotency_key):
        raise HTTPException(status_code=400, detail="数字人请求标识无效。")
    expected_key = f"avatar-submit-{provider_request.idempotency_key}"
    if request.headers.get("Idempotency-Key") != expected_key:
        raise HTTPException(status_code=400, detail="数字人安全请求标识无效。")
    owner = _owner(request)
    alias = f"avatar-request:{provider_request.idempotency_key}"
    if not register_provider_job(
        provider_job_id=alias, owner=owner, kind="avatar_request"
    ):
        raise HTTPException(
            status_code=409, detail="数字人请求归属冲突，请联系管理员。"
        )
    try:
        visible = _visible_assets(provider, owner=owner)
        avatar_ids = {
            asset.asset_id
            for asset in visible
            if asset.kind == AvatarAssetKind.AVATAR and asset.status == "ready"
        }
        voice_ids = {
            asset.asset_id
            for asset in visible
            if asset.kind == AvatarAssetKind.VOICE and asset.status == "ready"
        }
        if (
            provider_request.avatar_id not in avatar_ids
            or provider_request.voice_id not in voice_ids
        ):
            raise HTTPException(status_code=400, detail="所选形象或声音当前不可用。")
        quote = _quote(provider_request.script_text, provider_request.speech_rate)
        credits.repository.reserve_avatar_billing(
            owner=_credit_owner(request),
            idempotency_key=provider_request.idempotency_key,
            price_per_minute_cny=Decimal(str(quote.price_per_minute_cny)),
            billing_unit_seconds=quote.billing_unit_seconds,
            reserved_seconds=quote.reservation_seconds,
            reserved_credits=Decimal(str(quote.reservation_credits)),
        )
        snapshot = provider.submit(provider_request)
        _register_snapshot(snapshot, owner=owner)
        _bind_billing_snapshot(snapshot=snapshot, credits=credits, request=request)
        return snapshot.model_copy(
            update={
                "estimated_cost_cny": quote.reservation_credits,
                "estimated_seconds": quote.reservation_seconds,
                "stage": f"{snapshot.stage}；已冻结 {quote.reservation_credits:.2f} 积分，完成后按实际整秒结算",
            }
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AvatarProviderError as exc:
        if not exc.outcome_unknown:
            credits.repository.release_avatar_billing(
                owner=_credit_owner(request),
                idempotency_key=provider_request.idempotency_key,
            )
        raise _provider_error(exc, "数字人任务提交失败。") from exc


@router.get("/jobs/find/{idempotency_key}", response_model=AvatarJobSnapshot | None)
def find_job(
    idempotency_key: str,
    request: Request,
    provider=Depends(get_server_avatar_provider),
    credits: CreditsService = Depends(get_credits_service),
):
    if not _REQUEST_KEY.fullmatch(idempotency_key):
        raise HTTPException(status_code=400, detail="数字人请求标识无效。")
    alias = f"avatar-request:{idempotency_key}"
    if not provider_job_owned(
        provider_job_id=alias, owner=_owner(request), kind="avatar_request"
    ):
        raise HTTPException(status_code=404, detail="数字人任务不存在。")
    try:
        snapshot = provider.find_job(idempotency_key)
        if snapshot is not None:
            _register_snapshot(snapshot, owner=_owner(request))
            snapshot = _billing_snapshot(
                snapshot=snapshot, credits=credits, request=request
            )
        return snapshot
    except AvatarProviderError as exc:
        raise _provider_error(exc, "暂时无法核对数字人任务。") from exc


@router.get("/jobs/{job_id}", response_model=AvatarJobSnapshot)
def get_job(
    job_id: str,
    request: Request,
    provider=Depends(get_server_avatar_provider),
    credits: CreditsService = Depends(get_credits_service),
):
    owner = _owner(request)
    if not _OPAQUE_ID.fullmatch(job_id) or not provider_job_owned(
        provider_job_id=job_id, owner=owner, kind="avatar"
    ):
        raise HTTPException(status_code=404, detail="数字人任务不存在。")
    try:
        snapshot = provider.get_job(job_id)
        _register_snapshot(snapshot, owner=owner)
        return _billing_snapshot(snapshot=snapshot, credits=credits, request=request)
    except AvatarProviderError as exc:
        raise _provider_error(exc, "暂时无法查询数字人任务。") from exc


@router.post("/resume", response_model=AvatarJobSnapshot)
def resume(
    body: AvatarResumeBody,
    request: Request,
    provider=Depends(get_server_avatar_provider),
    credits: CreditsService = Depends(get_credits_service),
):
    if not callable(getattr(provider, "resume_submit", None)):
        raise HTTPException(status_code=503, detail="当前数字人线路不支持恢复提交。")
    if not re.fullmatch(r"[a-f0-9-]{3,40}", body.attempt_id):
        raise HTTPException(status_code=400, detail="数字人恢复尝试编号无效。")
    expected_key = f"avatar-resume-{body.request.idempotency_key}-{body.attempt_id}"
    if request.headers.get("Idempotency-Key") != expected_key:
        raise HTTPException(status_code=400, detail="数字人恢复请求标识无效。")
    owner = _owner(request)
    if not provider_job_owned(
        provider_job_id=body.pending_job_id, owner=owner, kind="avatar"
    ):
        raise HTTPException(status_code=404, detail="待恢复任务不存在。")
    try:
        snapshot = provider.resume_submit(body.request, body.pending_job_id)
        _register_snapshot(snapshot, owner=owner)
        _bind_billing_snapshot(snapshot=snapshot, credits=credits, request=request)
        return _billing_snapshot(snapshot=snapshot, credits=credits, request=request)
    except AvatarProviderError as exc:
        raise _provider_error(exc, "数字人任务恢复失败。") from exc


@router.get("/jobs/{job_id}/result")
def download_result(
    job_id: str,
    request: Request,
    provider=Depends(get_server_avatar_provider),
    credits: CreditsService = Depends(get_credits_service),
):
    if not _OPAQUE_ID.fullmatch(job_id) or not provider_job_owned(
        provider_job_id=job_id, owner=_owner(request), kind="avatar"
    ):
        raise HTTPException(status_code=404, detail="数字人任务不存在。")
    try:
        snapshot = provider.get_job(job_id)
        _billing_snapshot(snapshot=snapshot, credits=credits, request=request)
        payload, media_type = provider.download_result(job_id)
    except AvatarProviderError as exc:
        raise _provider_error(exc, "暂时无法下载数字人成片。") from exc
    if not payload or len(payload) > MAX_RESULT_BYTES:
        raise HTTPException(status_code=502, detail="数字人成片为空或超过 100MB。")
    return Response(content=payload, media_type=media_type)


async def _stage_upload(
    file: UploadFile, *, maximum: int, prefix: str
) -> tuple[Path, str]:
    expected_hash = ""
    written = 0
    digest = hashlib.sha256()
    suffix = Path(file.filename or "asset.bin").suffix[:12]
    with tempfile.NamedTemporaryFile(
        prefix=prefix, suffix=suffix, delete=False
    ) as stream:
        path = Path(stream.name)
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > maximum:
                path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="训练素材超过大小限制。")
            digest.update(chunk)
            stream.write(chunk)
    expected_hash = digest.hexdigest()
    if written == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="训练素材为空。")
    return path, expected_hash


def _verify_upload_hash(request: Request, actual_hash: str) -> None:
    expected = request.headers.get("X-Content-SHA256", "").strip().casefold()
    if not re.fullmatch(r"[a-f0-9]{64}", expected) or expected != actual_hash:
        raise HTTPException(status_code=400, detail="训练素材上传不完整。")


@router.post("/assets/train-avatar", response_model=AvatarAsset)
async def train_avatar(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    rights_confirmed: bool = Form(...),
    provider=Depends(get_server_avatar_provider),
    credits: CreditsService = Depends(get_credits_service),
):
    capability = _require_live(provider)
    if not capability.supports_cloud_avatar_training or not isinstance(
        provider, ShuyingLegacyAvatarProvider
    ):
        raise HTTPException(status_code=503, detail="公司云形象训练线路尚未配置。")
    if not rights_confirmed or not name.strip():
        raise HTTPException(status_code=400, detail="请填写名称并确认肖像授权。")
    path, digest = await _stage_upload(
        file, maximum=MAX_FACE_BYTES, prefix="videoinsight-face-"
    )
    try:
        _verify_upload_hash(request, digest)
        if request.headers.get("Idempotency-Key") != f"avatar-train-face-{digest[:32]}":
            raise HTTPException(status_code=400, detail="云形象训练请求标识无效。")
        try:
            validate_avatar_training_video(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        credits.debit(
            get_price("avatar_face_training_credits"),
            "云形象训练费用",
            ref_type="avatar_training",
            ref_id=digest[:24],
        )
        asset = provider.create_cloud_avatar(
            name=name.strip(),
            training_video_path=path,
            filename=file.filename or "training.mp4",
        )
        if not register_provider_job(
            provider_job_id=asset.asset_id, owner=_owner(request), kind="avatar_asset"
        ):
            raise HTTPException(status_code=502, detail="云形象素材编号冲突。")
        return asset
    except HTTPException:
        raise
    except InsufficientCreditsError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except AvatarProviderError as exc:
        raise _provider_error(exc, "云形象训练提交失败。") from exc
    finally:
        path.unlink(missing_ok=True)


@router.post("/assets/train-voice", response_model=AvatarAsset)
async def train_voice(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    rights_confirmed: bool = Form(...),
    provider=Depends(get_server_avatar_provider),
    credits: CreditsService = Depends(get_credits_service),
):
    capability = _require_live(provider)
    if not capability.supports_voice_sample_upload or not isinstance(
        provider, ShuyingLegacyAvatarProvider
    ):
        raise HTTPException(status_code=503, detail="公司声音训练线路尚未配置。")
    if not rights_confirmed or not name.strip():
        raise HTTPException(status_code=400, detail="请填写名称并确认声音授权。")
    path, digest = await _stage_upload(
        file, maximum=MAX_VOICE_BYTES, prefix="videoinsight-voice-"
    )
    try:
        _verify_upload_hash(request, digest)
        if (
            request.headers.get("Idempotency-Key")
            != f"avatar-train-voice-{digest[:32]}"
        ):
            raise HTTPException(status_code=400, detail="声音训练请求标识无效。")
        try:
            validate_voice_training_sample(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        credits.debit(
            get_price("avatar_voice_training_credits"),
            "声音训练费用",
            ref_type="avatar_training",
            ref_id=digest[:24],
        )
        if capability.supports_voice_cloning:
            asset = provider.create_voice_clone(
                name=name.strip(),
                sample_path=path,
                filename=file.filename or "voice-sample.mp3",
                mime_type=mimetypes.guess_type(file.filename or "")[0] or "audio/mpeg",
            )
        else:
            asset = provider.store_pending_voice_sample(
                name=name.strip(),
                sample_path=path,
                filename=file.filename or "voice-sample.mp3",
            )
        if not register_provider_job(
            provider_job_id=asset.asset_id, owner=_owner(request), kind="avatar_asset"
        ):
            raise HTTPException(status_code=502, detail="声音素材编号冲突。")
        return asset
    except HTTPException:
        raise
    except InsufficientCreditsError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except AvatarProviderError as exc:
        raise _provider_error(exc, "声音训练提交失败。") from exc
    finally:
        path.unlink(missing_ok=True)


@router.get("/assets/{asset_id}/voice-preview")
def voice_preview(
    asset_id: str,
    request: Request,
    provider=Depends(get_server_avatar_provider),
):
    visible = {
        asset.asset_id for asset in _visible_assets(provider, owner=_owner(request))
    }
    if asset_id not in visible:
        raise HTTPException(status_code=404, detail="声音素材不存在。")
    try:
        audio = provider.render_voice_preview(asset_id)
    except AvatarProviderError as exc:
        raise _provider_error(exc, "声音试听暂不可用。") from exc
    return Response(content=audio, media_type="audio/mpeg")


@router.post("/assets/{asset_id}/resume-voice-clone", response_model=AvatarAsset)
def resume_voice_clone(
    asset_id: str,
    request: Request,
    provider=Depends(get_server_avatar_provider),
):
    if not isinstance(provider, ShuyingLegacyAvatarProvider) or not provider_job_owned(
        provider_job_id=asset_id, owner=_owner(request), kind="avatar_asset"
    ):
        raise HTTPException(status_code=404, detail="待恢复声音素材不存在。")
    expected_key = (
        f"avatar-resume-asset-{hashlib.sha256(asset_id.encode()).hexdigest()[:32]}"
    )
    if request.headers.get("Idempotency-Key") != expected_key:
        raise HTTPException(status_code=400, detail="声音恢复请求标识无效。")
    try:
        asset = provider.resume_pending_voice_clone(asset_id)
        if not register_provider_job(
            provider_job_id=asset.asset_id, owner=_owner(request), kind="avatar_asset"
        ):
            raise HTTPException(status_code=502, detail="声音素材编号冲突。")
        return asset
    except HTTPException:
        raise
    except AvatarProviderError as exc:
        raise _provider_error(exc, "声音训练恢复失败。") from exc


@router.get("/assets/{asset_id}/media")
def asset_media(
    asset_id: str,
    request: Request,
    provider=Depends(get_server_avatar_provider),
):
    if not isinstance(provider, ShuyingLegacyAvatarProvider):
        raise HTTPException(status_code=404, detail="声音素材不存在。")
    owned = provider_job_owned(
        provider_job_id=asset_id, owner=_owner(request), kind="avatar_asset"
    )
    if not owned and not provider.is_shared_asset(asset_id):
        raise HTTPException(status_code=404, detail="声音素材不存在。")
    path = custom_voice_sample_path(provider.assets_manifest_path, asset_id)
    if path is None:
        raise HTTPException(status_code=404, detail="声音素材不存在。")
    return Response(
        content=path.read_bytes(),
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    )
