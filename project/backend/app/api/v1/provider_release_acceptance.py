"""Server-attested proof for the minimum real paid release acceptance run."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Literal
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request as URLRequest,
    build_opener,
)

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path as APIPath,
    Query,
    Request,
    Security,
)
from pydantic import BaseModel, ConfigDict, Field

from project.backend.app.core.config import DATABASE_PATH
from project.backend.app.core.control_plane_operations import (
    completed_operation_record,
    request_fingerprint,
)
from project.backend.app.core.media_probe import (
    get_media_duration_probe,
    media_probe_capability,
)
from project.backend.app.core.provider_jobs import provider_job_owned
from project.backend.app.core.repository import get_credits_service
from project.backend.app.core.security import require_customer_token
from project.backend.app.core.server_asr import get_server_asr_runtime
from project.backend.app.core.server_avatar import get_server_avatar_provider
from project.backend.app.core.server_video_editor import get_server_video_editor_runtime
from project.backend.app.release_version import get_release_version
from src.models import AvatarAssetKind, AvatarProviderStatus
from src.services.credits import CreditsService
from src.services.video_editor_cloud import ProviderJobStatus

router = APIRouter(
    prefix="/api/v1/provider/release-acceptance", tags=["provider-release-acceptance"]
)

PROVIDER = "shuying_legacy_cloud"
AVATAR_ID = "shuying-avatar-21920"
VOICE_ID = "shuying-voice-7869"
AVATAR_PROVIDER_ID = "21920"
VOICE_PROVIDER_ID = "7869"
AVATAR_SCRIPT = "交付测试"
START_PATH = "/api/v1/provider/release-acceptance/start"
ATTEST_PATH = "/api/v1/provider/release-acceptance/attest"
_SHA256 = r"^[a-f0-9]{64}$"
_RUN_ID = r"^[a-f0-9]{32}$"
_MAX_ACCEPTANCE_CREDITS = Decimal("5.00")
_MAX_ACCEPTANCE_MEDIA_BYTES = 32 * 1024 * 1024


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperationEvidence(_Strict):
    state: str
    attempts: int = Field(ge=1, le=2)
    idempotency_key_hash: str = Field(pattern=_SHA256)
    request_hash: str = Field(pattern=_SHA256)
    local_request_hash: str = Field(pattern=_SHA256)
    response_hash: str = Field(pattern=_SHA256)
    media_sha256: str | None = Field(default=None, pattern=_SHA256)


class CapabilityEvidence(_Strict):
    status: str
    result_sha256: str = Field(pattern=_SHA256)
    charged_credits: str | None = None
    provider_job_id_hash: str | None = Field(default=None, pattern=_SHA256)
    result_duration_seconds: float | None = Field(default=None, gt=0)
    opening_seconds: str | None = None
    duration_seconds: float | None = Field(default=None, gt=0)
    billed_seconds: int | None = Field(default=None, gt=0)


class LedgerEvidence(_Strict):
    id: int = Field(gt=0)
    amount: str
    balance_after: str
    ref_type: str
    ref_id: str
    created_at: str = ""


class RunBaseline(_Strict):
    schema_version: Literal[1]
    run_id: str = Field(pattern=_RUN_ID)
    target_origin: str = Field(min_length=9, max_length=500)
    release_version: str = Field(min_length=1, max_length=80)
    created_at: str = Field(min_length=20, max_length=80)
    starting_operation_rowid: int = Field(gt=0)
    starting_transaction_id: int = Field(ge=0)
    initial_balance_credits: str
    baseline_sha256: str = Field(pattern=_SHA256)


class StartBody(_Strict):
    schema_version: Literal[1]
    run_id: str = Field(pattern=_RUN_ID)
    target_origin: str = Field(min_length=9, max_length=500)
    release_version: str = Field(min_length=1, max_length=80)


class AttestBody(_Strict):
    schema_version: Literal[1]
    run_id: str = Field(pattern=_RUN_ID)
    target_origin: str = Field(min_length=9, max_length=500)
    release_version: str = Field(min_length=1, max_length=80)
    evidence_manifest_sha256: str = Field(pattern=_SHA256)
    provider: Literal["shuying_legacy_cloud"]
    avatar_id: Literal["shuying-avatar-21920"]
    voice_id: Literal["shuying-voice-7869"]
    baseline: RunBaseline
    initial_balance_credits: str
    final_balance_credits: str
    actual_spent_credits: str
    operations: dict[str, OperationEvidence]
    capabilities: dict[str, CapabilityEvidence]
    transactions: list[LedgerEvidence] = Field(min_length=1, max_length=1000)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _validate_release_context(*, target_origin: str, release_version: str) -> str:
    if release_version != get_release_version():
        raise HTTPException(status_code=409, detail="验收报告与服务器发布版本不一致。")
    configured_domain = os.getenv("CONTROL_PLANE_DOMAIN", "").strip().casefold()
    expected_origin = f"https://{configured_domain}" if configured_domain else ""
    if not expected_origin or target_origin.rstrip("/").casefold() != expected_origin:
        raise HTTPException(status_code=409, detail="验收报告不属于当前正式服务器。")
    target = urlparse(target_origin)
    if (
        target.scheme != "https"
        or not target.hostname
        or target.username
        or target.password
        or target.query
        or target.fragment
    ):
        raise HTTPException(status_code=409, detail="验收报告正式服务器地址无效。")
    return expected_origin


def _start_request(body: StartBody | AttestBody) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": body.run_id,
        "target_origin": body.target_origin,
        "release_version": body.release_version,
    }


def _baseline_hash(value: dict[str, Any]) -> str:
    return _sha256(_canonical(value))


def _validated_baseline(payload: Any) -> RunBaseline:
    try:
        baseline = RunBaseline.model_validate(payload)
        created_at = datetime.fromisoformat(baseline.created_at)
        initial = Decimal(baseline.initial_balance_credits)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="服务器验收起点记录无效。") from exc
    unsigned = baseline.model_dump(exclude={"baseline_sha256"})
    if (
        created_at.tzinfo is None
        or not initial.is_finite()
        or initial < 0
        or initial > _MAX_ACCEPTANCE_CREDITS
        or _baseline_hash(unsigned) != baseline.baseline_sha256
    ):
        raise HTTPException(status_code=409, detail="服务器验收起点记录无效。")
    return baseline


def _database_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(str(DATABASE_PATH), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _capture_server_baseline(
    *,
    owner: str,
    customer: str,
    body: StartBody,
    idempotency_key: str,
    expected_request_hash: str,
) -> RunBaseline:
    connection = _database_connection()
    try:
        connection.execute("BEGIN")
        operation = connection.execute(
            """
            SELECT rowid, request_hash, state, created_at
            FROM control_plane_operations
            WHERE owner = ? AND operation_type = ? AND idempotency_key = ?
            """,
            (owner, START_PATH, idempotency_key),
        ).fetchone()
        account = connection.execute(
            "SELECT balance FROM credit_accounts WHERE owner = ?", (customer,)
        ).fetchone()
        transaction = connection.execute(
            "SELECT COALESCE(MAX(id), 0) AS maximum_id "
            "FROM credit_transactions WHERE owner = ?",
            (customer,),
        ).fetchone()
        unsettled = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM control_plane_operations
            WHERE owner = ? AND state IN ('pending', 'unknown')
              AND NOT (operation_type = ? AND idempotency_key = ?)
            """,
            (owner, START_PATH, idempotency_key),
        ).fetchone()
    finally:
        connection.close()
    if (
        operation is None
        or str(operation["state"]) != "pending"
        or str(operation["request_hash"]) != expected_request_hash
        or account is None
        or transaction is None
        or unsettled is None
        or int(unsettled["total"]) != 0
    ):
        raise HTTPException(status_code=409, detail="服务器无法签发可信验收起点。")
    unsigned = {
        "schema_version": 1,
        "run_id": body.run_id,
        "target_origin": body.target_origin,
        "release_version": body.release_version,
        "created_at": str(operation["created_at"]),
        "starting_operation_rowid": int(operation["rowid"]),
        "starting_transaction_id": int(transaction["maximum_id"]),
        "initial_balance_credits": str(account["balance"]),
    }
    return _validated_baseline(
        {**unsigned, "baseline_sha256": _baseline_hash(unsigned)}
    )


def _load_server_baseline(
    *, owner: str, run_id: str, target_origin: str, release_version: str
) -> RunBaseline:
    key = f"acceptance-start-{run_id}"
    raw, payload, stored_request_hash = _load_operation(owner, START_PATH, key)
    del raw
    expected_request = {
        "schema_version": 1,
        "run_id": run_id,
        "target_origin": target_origin,
        "release_version": release_version,
    }
    if stored_request_hash != request_fingerprint(
        START_PATH, _canonical(expected_request)
    ):
        raise HTTPException(status_code=409, detail="服务器验收起点请求绑定无效。")
    baseline = _validated_baseline(payload)
    if (
        baseline.run_id != run_id
        or baseline.target_origin != target_origin
        or baseline.release_version != release_version
    ):
        raise HTTPException(status_code=409, detail="服务器验收起点与本次运行不一致。")
    return baseline


def _ids(run_id: str) -> tuple[str, str, str]:
    task_id = f"transcript-{_sha256((run_id + 'asr').encode())[:10]}"
    batch_id = f"edit-batch-{_sha256((run_id + 'editor').encode())[:12]}"
    avatar_key = f"accept-avatar-{run_id[:24]}"
    return task_id, batch_id, avatar_key


def _operation_specs(run_id: str, include_resume: bool) -> dict[str, tuple[str, str]]:
    task_id, batch_id, avatar_key = _ids(run_id)
    main_key = f"video-editor-input/{batch_id}/input/main.mp4"
    opening_key = f"video-editor-input/{batch_id}/opening/opening.mp4"
    specs = {
        "copywriting_generate": (
            "/api/v1/provider/copywriting/generate",
            f"accept-copy-{run_id[:24]}",
        ),
        "asr_authorize": ("/api/v1/provider/asr/authorize", f"asr-charge-{task_id}"),
        "asr_upload": ("/api/v1/provider/asr/upload", f"asr-upload-{task_id}"),
        "asr_submit": ("/api/v1/provider/asr/submit", f"asr-submit-{task_id}"),
        "video_authorize": (
            "/api/v1/provider/video-editor/authorize",
            f"video-charge-{batch_id}",
        ),
        "video_main_upload": (
            "/api/v1/provider/video-editor/upload",
            f"video-upload-{_sha256(main_key.encode())[:32]}",
        ),
        "video_opening_upload": (
            "/api/v1/provider/video-editor/upload",
            f"video-upload-{_sha256(opening_key.encode())[:32]}",
        ),
        "video_asr_submit": (
            "/api/v1/provider/video-editor/asr/submit",
            f"video-asr-{batch_id}",
        ),
        "video_plan": ("/api/v1/provider/video-editor/plan", f"video-plan-{batch_id}"),
        "video_render_submit": (
            "/api/v1/provider/video-editor/render/submit",
            f"video-render-{batch_id}",
        ),
        "avatar_submit": (
            "/api/v1/provider/avatar/submit",
            f"avatar-submit-{avatar_key}",
        ),
    }
    if include_resume:
        attempt_id = f"{run_id[:8]}-0"
        specs["avatar_resume"] = (
            "/api/v1/provider/avatar/resume",
            f"avatar-resume-{avatar_key}-{attempt_id}",
        )
    return specs


def _avatar_submit_body(run_id: str) -> dict[str, Any]:
    _task_id, _batch_id, avatar_key = _ids(run_id)
    return {
        "request": {
            "script_text": AVATAR_SCRIPT,
            "video_name": "交付验收",
            "avatar_id": AVATAR_ID,
            "voice_id": VOICE_ID,
            "profile_id": "default",
            "speech_rate": 1.0,
            "aspect_ratio": "9:16",
            "resolution": "1080x1920",
            "background": "solid",
            "rights_holder": "VideoInsight交付验收",
            "script_rights_confirmed": True,
            "avatar_rights_confirmed": True,
            "voice_rights_confirmed": True,
            "idempotency_key": avatar_key,
        }
    }


def _avatar_resume_body(run_id: str, pending_job_id: str) -> dict[str, Any]:
    attempt_id = f"{run_id[:8]}-0"
    return {
        **_avatar_submit_body(run_id),
        "pending_job_id": pending_job_id,
        "attempt_id": attempt_id,
    }


def _verify_avatar_request_binding(
    *,
    run_id: str,
    operations: dict[str, OperationEvidence],
    responses: dict[str, dict[str, Any]],
    specs: dict[str, tuple[str, str]],
) -> None:
    submit_body = _avatar_submit_body(run_id)
    submit_path, _submit_key = specs["avatar_submit"]
    expected_submit_hash = request_fingerprint(submit_path, _canonical(submit_body))
    submit_evidence = operations["avatar_submit"]
    if (
        submit_evidence.request_hash != expected_submit_hash
        or submit_evidence.local_request_hash != _sha256(_canonical(submit_body))
    ):
        raise HTTPException(status_code=409, detail="数字人大树1固定请求绑定无效。")
    if "avatar_resume" not in operations:
        return
    pending_job_id = str(responses["avatar_submit"].get("job_id") or "")
    if not pending_job_id.startswith("voice-tts:"):
        raise HTTPException(
            status_code=409, detail="数字人固定恢复请求与起始任务不一致。"
        )
    resume_body = _avatar_resume_body(run_id, pending_job_id)
    resume_path, _resume_key = specs["avatar_resume"]
    resume_evidence = operations["avatar_resume"]
    if resume_evidence.request_hash != request_fingerprint(
        resume_path, _canonical(resume_body)
    ) or resume_evidence.local_request_hash != _sha256(_canonical(resume_body)):
        raise HTTPException(status_code=409, detail="数字人固定恢复请求绑定无效。")


def _require_ready_shared_avatar_assets(avatar_provider: Any) -> None:
    checker = getattr(avatar_provider, "has_ready_shared_asset", None)
    if not callable(checker) or not all(
        (
            bool(
                checker(
                    asset_id=AVATAR_ID,
                    kind=AvatarAssetKind.AVATAR,
                    provider_asset_id=AVATAR_PROVIDER_ID,
                )
            ),
            bool(
                checker(
                    asset_id=VOICE_ID,
                    kind=AvatarAssetKind.VOICE,
                    provider_asset_id=VOICE_PROVIDER_ID,
                )
            ),
        )
    ):
        raise HTTPException(
            status_code=409, detail="大树1共享形象或声音的授权就绪状态无效。"
        )


def _verify_operation_window(
    *,
    owner: str,
    baseline: RunBaseline,
    attest_key: str,
    specs: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    connection = _database_connection()
    try:
        end = connection.execute(
            """
            SELECT rowid, created_at
            FROM control_plane_operations
            WHERE owner = ? AND operation_type = ? AND idempotency_key = ?
            """,
            (owner, ATTEST_PATH, attest_key),
        ).fetchone()
        if end is None:
            raise HTTPException(status_code=409, detail="服务器验收结束边界不存在。")
        rows = connection.execute(
            """
            SELECT rowid, operation_type, idempotency_key, request_hash,
                   state, created_at
            FROM control_plane_operations
            WHERE owner = ? AND rowid > ? AND rowid <= ?
            ORDER BY rowid ASC
            """,
            (owner, baseline.starting_operation_rowid, int(end["rowid"])),
        ).fetchall()
    finally:
        connection.close()
    expected = set(specs.values()) | {(ATTEST_PATH, attest_key)}
    actual = {(str(row["operation_type"]), str(row["idempotency_key"])) for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise HTTPException(
            status_code=409, detail="验收窗口内存在额外训练、发布或付费操作。"
        )
    normalized: list[dict[str, Any]] = []
    for row in rows:
        operation_type = str(row["operation_type"])
        state = str(row["state"])
        required_state = "pending" if operation_type == ATTEST_PATH else "completed"
        if state != required_state:
            raise HTTPException(status_code=409, detail="验收窗口内原子操作状态无效。")
        normalized.append(
            {
                "rowid": int(row["rowid"]),
                "operation_type": operation_type,
                "idempotency_key": str(row["idempotency_key"]),
                "request_hash": str(row["request_hash"]),
                "state": state,
                "created_at": str(row["created_at"]),
            }
        )
    return {
        "starting_operation_rowid": baseline.starting_operation_rowid,
        "attest_operation_rowid": int(end["rowid"]),
        "operation_count": len(normalized),
        "digest_sha256": _sha256(_canonical(normalized)),
    }


def _load_operation(
    owner: str, path: str, key: str
) -> tuple[bytes, dict[str, Any], str]:
    record = completed_operation_record(
        owner=owner, operation_type=path, idempotency_key=key
    )
    if (
        record is None
        or record.response_status is None
        or not 200 <= record.response_status < 300
    ):
        raise HTTPException(status_code=409, detail="真实付费原子操作记录不完整。")
    raw = record.response_body or b""
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409, detail="真实付费原子操作响应无效。"
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=409, detail="真实付费原子操作响应无效。")
    return raw, payload, record.request_hash


def _connection_failure(exc: Exception) -> bool:
    kind = getattr(exc, "kind", None)
    return (
        isinstance(exc, (OSError, TimeoutError))
        or str(getattr(kind, "value", kind)).casefold() == "connection"
    )


def _readonly(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except Exception as exc:
        if not _connection_failure(exc):
            raise HTTPException(
                status_code=502, detail="供应商最终状态复核失败。"
            ) from exc
    try:
        return call()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="供应商最终状态复核失败。") from exc


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _download_https(url: str, *, expected_host: str) -> bytes:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.hostname.casefold() != expected_host.casefold()
        or parsed.username
        or parsed.password
    ):
        raise ValueError("result URL is not HTTPS")
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    with opener.open(
        URLRequest(url, headers={"Accept": "video/mp4"}), timeout=45
    ) as response:  # noqa: S310
        payload = response.read(_MAX_ACCEPTANCE_MEDIA_BYTES + 1)
    if len(payload) > _MAX_ACCEPTANCE_MEDIA_BYTES:
        raise ValueError("result exceeds acceptance size limit")
    return payload


def _validated_media_duration(
    duration_probe: Callable[[bytes], float], payload: bytes, *, label: str
) -> float:
    try:
        duration = float(duration_probe(payload))
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail=f"{label}不是可播放的完整音视频。"
        ) from exc
    if not math.isfinite(duration) or duration <= 0:
        raise HTTPException(status_code=409, detail=f"{label}不是可播放的完整音视频。")
    return duration


def _manifest_hash(body: AttestBody) -> str:
    return _sha256(
        _canonical(
            {
                "run_id": body.run_id,
                "target_origin": body.target_origin,
                "release_version": body.release_version,
                "provider": body.provider,
                "avatar_id": body.avatar_id,
                "voice_id": body.voice_id,
                "baseline": body.baseline.model_dump(),
                "operations": {
                    key: value.model_dump(exclude_none=True)
                    for key, value in body.operations.items()
                },
                "capabilities": {
                    key: value.model_dump(exclude_none=True)
                    for key, value in body.capabilities.items()
                },
                "new_transactions": [item.model_dump() for item in body.transactions],
                "initial_balance_credits": body.initial_balance_credits,
                "final_balance_credits": body.final_balance_credits,
                "actual_spent_credits": body.actual_spent_credits,
            }
        )
    )


def _verify_ledger(
    body: AttestBody,
    credits: CreditsService,
    customer: str,
    baseline: RunBaseline,
) -> tuple[str, list[int], dict[str, Decimal]]:
    task_id, batch_id, avatar_key = _ids(body.run_id)
    refs = {
        "copywriting": f"accept-copy-{body.run_id[:24]}",
        "transcription": task_id,
        "transcription_adjustment": task_id,
        "video_editor": batch_id,
        "video_editor_adjustment": batch_id,
        "avatar_reserve": avatar_key,
        "avatar_settlement": avatar_key,
    }
    reported = [item.model_dump() for item in body.transactions]
    stored = credits.list_transactions(customer, limit=1000)
    after_baseline = [
        row
        for row in stored
        if int(row.get("id") or 0) > baseline.starting_transaction_id
    ]
    if len(stored) >= 1000 and all(
        int(row.get("id") or 0) > baseline.starting_transaction_id for row in stored
    ):
        raise HTTPException(
            status_code=409, detail="验收窗口内账本流水过多，拒绝不完整证据。"
        )

    def normalize(row: dict[str, Any]) -> dict[str, str]:
        return {
            key: str(row.get(key) or "")
            for key in (
                "id",
                "amount",
                "balance_after",
                "ref_type",
                "ref_id",
                "created_at",
            )
        }

    if sorted(map(normalize, after_baseline), key=lambda row: int(row["id"])) != sorted(
        map(normalize, reported), key=lambda row: int(row["id"])
    ):
        raise HTTPException(status_code=409, detail="服务器账本与验收报告不一致。")
    forbidden = {
        "avatar_training",
        "transcription_refund",
        "video_editor_refund",
        "publish",
    }
    for row in after_baseline:
        ref_type = str(row.get("ref_type") or "")
        try:
            amount = Decimal(str(row["amount"]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=409, detail="服务器账本包含无效金额。"
            ) from exc
        if not amount.is_finite():
            raise HTTPException(status_code=409, detail="服务器账本包含无效金额。")
        if (
            ref_type in forbidden
            or refs.get(ref_type) != str(row.get("ref_id") or "")
            or amount == 0
        ):
            raise HTTPException(
                status_code=409, detail="验收账号包含额外扣费、退款、训练或发布流水。"
            )
    try:
        initial = Decimal(baseline.initial_balance_credits)
        reported_initial = Decimal(body.initial_balance_credits)
        final = Decimal(body.final_balance_credits)
        spent = Decimal(body.actual_spent_credits)
        if (
            not initial.is_finite()
            or not reported_initial.is_finite()
            or not final.is_finite()
            or not spent.is_finite()
            or initial < 0
            or initial > _MAX_ACCEPTANCE_CREDITS
            or reported_initial != initial
            or final < 0
            or spent <= 0
            or spent > _MAX_ACCEPTANCE_CREDITS
        ):
            raise ValueError
        ordered = sorted(after_baseline, key=lambda row: int(row["id"]))
        running = initial
        net = Decimal("0")
        required_debits = {
            "copywriting",
            "transcription",
            "video_editor",
            "avatar_reserve",
        }
        seen_debits: set[str] = set()
        groups = {
            "copywriting": "copywriting",
            "transcription": "asr",
            "transcription_adjustment": "asr",
            "video_editor": "video_editor",
            "video_editor_adjustment": "video_editor",
            "avatar_reserve": "avatar",
            "avatar_settlement": "avatar",
        }
        group_net = {
            "copywriting": Decimal("0"),
            "asr": Decimal("0"),
            "video_editor": Decimal("0"),
            "avatar": Decimal("0"),
        }
        for row in ordered:
            ref_type = str(row.get("ref_type") or "")
            amount = Decimal(str(row["amount"]))
            balance_after = Decimal(str(row["balance_after"]))
            if not amount.is_finite() or not balance_after.is_finite():
                raise ValueError
            if ref_type in required_debits:
                if amount >= 0 or ref_type in seen_debits:
                    raise ValueError
                seen_debits.add(ref_type)
            running += amount
            net += amount
            group_net[groups[ref_type]] += amount
            if running != balance_after:
                raise ValueError
        if (
            running != final
            or initial - final != spent
            or net != -spent
            or seen_debits != required_debits
            or any(amount >= 0 for amount in group_net.values())
            or credits.get_balance(customer) != final
        ):
            raise ValueError
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        raise HTTPException(
            status_code=409, detail="服务器账本净额与余额不一致。"
        ) from exc
    digest = _sha256(
        _canonical(
            sorted(map(normalize, after_baseline), key=lambda row: int(row["id"]))
        )
    )
    return digest, sorted(int(row["id"]) for row in after_baseline), group_net


@router.get("/capabilities")
def capabilities(
    _customer: str = Security(require_customer_token),
) -> dict[str, object]:
    return media_probe_capability()


@router.post("/start")
def start(
    body: StartBody,
    request: Request,
    customer: str = Security(require_customer_token),
) -> dict[str, Any]:
    key = f"acceptance-start-{body.run_id}"
    if request.headers.get("Idempotency-Key") != key:
        raise HTTPException(status_code=400, detail="验收起点请求标识无效。")
    _validate_release_context(
        target_origin=body.target_origin, release_version=body.release_version
    )
    expected_request_hash = request_fingerprint(
        START_PATH, _canonical(_start_request(body))
    )
    baseline = _capture_server_baseline(
        owner=f"customer:{customer}",
        customer=customer,
        body=body,
        idempotency_key=key,
        expected_request_hash=expected_request_hash,
    )
    return baseline.model_dump()


@router.get("/baseline/{run_id}")
def baseline(
    run_id: str = APIPath(pattern=_RUN_ID),
    customer: str = Security(require_customer_token),
) -> dict[str, Any]:
    configured_domain = os.getenv("CONTROL_PLANE_DOMAIN", "").strip().casefold()
    target_origin = f"https://{configured_domain}" if configured_domain else ""
    _validate_release_context(
        target_origin=target_origin, release_version=get_release_version()
    )
    return _load_server_baseline(
        owner=f"customer:{customer}",
        run_id=run_id,
        target_origin=target_origin,
        release_version=get_release_version(),
    ).model_dump()


@router.post("/attest")
def attest(
    body: AttestBody,
    request: Request,
    customer: str = Security(require_customer_token),
    credits: CreditsService = Depends(get_credits_service),
    asr_runtime=Depends(get_server_asr_runtime),
    video_runtime=Depends(get_server_video_editor_runtime),
    avatar_provider=Depends(get_server_avatar_provider),
    duration_probe=Depends(get_media_duration_probe),
) -> dict[str, Any]:
    if request.headers.get("Idempotency-Key") != f"acceptance-attest-{body.run_id}":
        raise HTTPException(status_code=400, detail="在线验收证明请求标识无效。")
    _validate_release_context(
        target_origin=body.target_origin, release_version=body.release_version
    )
    if (body.provider, body.avatar_id, body.voice_id) != (
        PROVIDER,
        AVATAR_ID,
        VOICE_ID,
    ):
        raise HTTPException(status_code=409, detail="验收使用的数字人固定资产无效。")
    owner = f"customer:{customer}"
    server_baseline = _load_server_baseline(
        owner=owner,
        run_id=body.run_id,
        target_origin=body.target_origin,
        release_version=body.release_version,
    )
    if body.baseline != server_baseline:
        raise HTTPException(
            status_code=409, detail="验收报告与服务器签发的起点不一致。"
        )
    if _manifest_hash(body) != body.evidence_manifest_sha256:
        raise HTTPException(
            status_code=409, detail="服务器重算的验收 manifest 不一致。"
        )
    if set(body.capabilities) != {
        "copywriting",
        "asr",
        "video_editor",
        "avatar",
    } or any(item.status != "passed" for item in body.capabilities.values()):
        raise HTTPException(status_code=409, detail="四项真实付费能力证据不完整。")
    include_resume = "avatar_resume" in body.operations
    specs = _operation_specs(body.run_id, include_resume)
    if set(body.operations) != set(specs):
        raise HTTPException(status_code=409, detail="真实付费原子操作集合无效。")
    responses: dict[str, dict[str, Any]] = {}
    for name, (path, key) in specs.items():
        evidence = body.operations[name]
        if evidence.state != "completed" or evidence.idempotency_key_hash != _sha256(
            key.encode()
        ):
            raise HTTPException(status_code=409, detail="真实付费原子操作证据无效。")
        raw, responses[name], stored_request_hash = _load_operation(owner, path, key)
        if evidence.request_hash != stored_request_hash:
            raise HTTPException(
                status_code=409, detail="真实付费原子操作请求绑定无效。"
            )
        if evidence.response_hash != _sha256(raw):
            raise HTTPException(
                status_code=409, detail="真实付费原子操作响应绑定无效。"
            )

    _verify_avatar_request_binding(
        run_id=body.run_id,
        operations=body.operations,
        responses=responses,
        specs=specs,
    )

    operation_window = _verify_operation_window(
        owner=owner,
        baseline=server_baseline,
        attest_key=f"acceptance-attest-{body.run_id}",
        specs=specs,
    )

    copy = responses["copywriting_generate"]
    usage = copy.get("token_usage")
    generated = copy.get("result")
    try:
        token_counts = [int(value) for value in (usage or {}).values()]
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="AI 文案用量证据无效。") from exc
    try:
        copy_charged = Decimal(str(copy.get("charged_credits")))
        reported_copy_charged = Decimal(
            str(body.capabilities["copywriting"].charged_credits)
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="AI 文案扣费证据无效。") from exc
    if (
        copy.get("is_mock")
        or not generated
        or not isinstance(usage, dict)
        or not token_counts
        or any(value < 0 for value in token_counts)
        or sum(token_counts) <= 0
        or not copy_charged.is_finite()
        or copy_charged <= 0
        or copy_charged != reported_copy_charged
    ):
        raise HTTPException(status_code=409, detail="AI 文案不是可验证的正式结果。")
    if _sha256(_canonical(generated)) != body.capabilities["copywriting"].result_sha256:
        raise HTTPException(status_code=409, detail="AI 文案结果绑定无效。")

    task_id, batch_id, avatar_key = _ids(body.run_id)
    asr_job = str(responses["asr_submit"].get("provider_job_id") or "")
    if not asr_job or not provider_job_owned(
        provider_job_id=asr_job, owner=owner, kind="asr"
    ):
        raise HTTPException(status_code=409, detail="云转写任务归属无效。")
    asr_snapshot = _readonly(lambda: asr_runtime.query(asr_job))
    if asr_snapshot.status != ProviderJobStatus.SUCCEEDED or asr_snapshot.is_mock:
        raise HTTPException(status_code=409, detail="云转写供应商终态无效。")
    transcript = _readonly(lambda: asr_runtime.fetch_result(asr_snapshot))
    asr_hash = _sha256(
        _canonical(
            transcript.model_dump(
                mode="json",
                include={
                    "provider_name",
                    "transcript",
                    "segments",
                    "spoken_ranges",
                    "duration_seconds",
                    "language",
                },
            )
        )
    )
    if (
        transcript.is_mock
        or transcript.provider_name != "aliyun_fun_asr"
        or not transcript.transcript.strip()
        or not transcript.segments
        or asr_hash != body.capabilities["asr"].result_sha256
        or _sha256(asr_job.encode()) != body.capabilities["asr"].provider_job_id_hash
    ):
        raise HTTPException(status_code=409, detail="云转写正式结果绑定无效。")

    video_asr_job = str(responses["video_asr_submit"].get("provider_job_id") or "")
    if not video_asr_job or not provider_job_owned(
        provider_job_id=video_asr_job, owner=owner, kind="video_asr"
    ):
        raise HTTPException(status_code=409, detail="云剪辑转写任务归属无效。")
    video_asr_snapshot = _readonly(
        lambda: video_runtime.providers.asr.query(video_asr_job)
    )
    video_transcript = _readonly(
        lambda: video_runtime.providers.asr.fetch_result(video_asr_snapshot)
    )
    if (
        video_asr_snapshot.status != ProviderJobStatus.SUCCEEDED
        or video_asr_snapshot.is_mock
        or video_transcript.is_mock
        or video_transcript.provider_name != "aliyun_fun_asr"
        or not video_transcript.transcript.strip()
        or not video_transcript.segments
    ):
        raise HTTPException(status_code=409, detail="云剪辑转写供应商终态无效。")
    if responses["video_plan"].get("is_mock"):
        raise HTTPException(status_code=409, detail="云剪辑方案不是正式结果。")
    render_job = str(responses["video_render_submit"].get("provider_job_id") or "")
    if not render_job or not provider_job_owned(
        provider_job_id=render_job, owner=owner, kind="video_render"
    ):
        raise HTTPException(status_code=409, detail="云剪辑渲染任务归属无效。")
    render = _readonly(lambda: video_runtime.providers.render.query(render_job))
    if (
        render.status != ProviderJobStatus.SUCCEEDED
        or render.is_mock
        or not render.can_publish
    ):
        raise HTTPException(status_code=409, detail="云剪辑供应商终态不可发布。")
    presign = getattr(video_runtime.providers.object_store, "presign_get_url", None)
    if not callable(presign):
        raise HTTPException(status_code=502, detail="无法只读复核云剪辑结果。")
    expected_oss_host = (
        f"{video_runtime.configuration.oss_bucket}."
        f"{video_runtime.configuration.oss_location}.aliyuncs.com"
    )
    editor_bytes = _readonly(
        lambda: _download_https(
            str(presign(f"video-editor-output/{batch_id}/output/720p.mp4")),
            expected_host=expected_oss_host,
        )
    )
    if (
        not editor_bytes
        or _sha256(editor_bytes) != body.capabilities["video_editor"].result_sha256
    ):
        raise HTTPException(status_code=409, detail="云剪辑成片结果绑定无效。")
    editor_seconds = _validated_media_duration(
        duration_probe, editor_bytes, label="云剪辑成片"
    )
    if (
        not math.isfinite(editor_seconds)
        or abs(
            editor_seconds
            - float(body.capabilities["video_editor"].result_duration_seconds or 0)
        )
        > 0.03
    ):
        raise HTTPException(status_code=409, detail="云剪辑成片时长绑定无效。")

    _require_ready_shared_avatar_assets(avatar_provider)

    avatar_response = responses.get("avatar_resume") or responses["avatar_submit"]
    avatar_job = str(avatar_response.get("job_id") or "")
    if not avatar_job or not provider_job_owned(
        provider_job_id=avatar_job, owner=owner, kind="avatar"
    ):
        raise HTTPException(status_code=409, detail="数字人任务归属无效。")
    avatar = _readonly(lambda: avatar_provider.get_job(avatar_job))
    if avatar.status != AvatarProviderStatus.SUCCEEDED:
        raise HTTPException(status_code=409, detail="数字人供应商终态无效。")
    avatar_bytes, avatar_media_type = _readonly(
        lambda: avatar_provider.download_result(avatar_job)
    )
    avatar_seconds = _validated_media_duration(
        duration_probe, avatar_bytes, label="数字人成片"
    )
    billing = credits.repository.get_avatar_billing(
        owner=customer, idempotency_key=avatar_key
    )
    capability_avatar = body.capabilities["avatar"]
    try:
        avatar_charged = Decimal(str(capability_avatar.charged_credits))
        settled_avatar_credits = Decimal(str((billing or {}).get("final_credits")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="数字人最终扣费证据无效。") from exc
    if (
        not avatar_bytes
        or not str(avatar_media_type).startswith("video/")
        or _sha256(avatar_bytes) != capability_avatar.result_sha256
        or not billing
        or billing.get("state") != "settled"
        or int(billing.get("final_seconds") or 0) <= 0
        or int(billing["final_seconds"]) != capability_avatar.billed_seconds
        or not avatar_charged.is_finite()
        or avatar_charged <= 0
        or avatar_charged != settled_avatar_credits
        or abs(avatar_seconds - float(capability_avatar.duration_seconds or 0)) > 0.03
        or math.ceil(avatar_seconds) != int(billing["final_seconds"])
    ):
        raise HTTPException(status_code=409, detail="数字人成片或最终结算绑定无效。")

    ledger_digest, transaction_ids, group_net = _verify_ledger(
        body, credits, customer, server_baseline
    )
    if (
        -group_net["copywriting"] != copy_charged
        or -group_net["avatar"] != avatar_charged
    ):
        raise HTTPException(status_code=409, detail="能力扣费与服务器账本不一致。")
    validated_at = datetime.now().astimezone()
    expires_at = validated_at + timedelta(hours=24)
    return {
        "schema_version": 1,
        "release_version": body.release_version,
        "run_id": body.run_id,
        "evidence_manifest_sha256": body.evidence_manifest_sha256,
        "validated_at": validated_at.isoformat(timespec="seconds"),
        "expires_at": expires_at.isoformat(timespec="seconds"),
        "provider": PROVIDER,
        "avatar_id": AVATAR_ID,
        "voice_id": VOICE_ID,
        "baseline": server_baseline.model_dump(),
        "operation_window": operation_window,
        "ledger": {"digest_sha256": ledger_digest, "transaction_ids": transaction_ids},
        "capabilities": {
            "copywriting": {
                "status": "passed",
                "result_sha256": body.capabilities["copywriting"].result_sha256,
            },
            "asr": {"status": "passed", "result_sha256": asr_hash},
            "video_editor": {
                "status": "passed",
                "result_sha256": body.capabilities["video_editor"].result_sha256,
                "duration_seconds": round(editor_seconds, 3),
            },
            "avatar": {
                "status": "passed",
                "result_sha256": capability_avatar.result_sha256,
                "duration_seconds": round(avatar_seconds, 3),
                "billed_seconds": capability_avatar.billed_seconds,
            },
        },
    }


@router.get("/proof/{run_id}")
def proof(
    run_id: str = APIPath(pattern=_RUN_ID),
    evidence_manifest_sha256: str = Query(pattern=_SHA256),
    customer: str = Security(require_customer_token),
) -> dict[str, Any]:
    record = completed_operation_record(
        owner=f"customer:{customer}",
        operation_type=ATTEST_PATH,
        idempotency_key=f"acceptance-attest-{run_id}",
    )
    if record is None:
        raise HTTPException(status_code=404, detail="在线验收证明不存在。")
    try:
        payload = json.loads(record.response_body or b"{}")
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="在线验收证明记录无效。") from exc
    if (
        payload.get("run_id") != run_id
        or payload.get("release_version") != get_release_version()
        or payload.get("evidence_manifest_sha256") != evidence_manifest_sha256
        or expires_at.tzinfo is None
        or datetime.now().astimezone() > expires_at.astimezone()
    ):
        raise HTTPException(
            status_code=409, detail="在线验收证明与当前报告、版本或有效期不一致。"
        )
    return payload
