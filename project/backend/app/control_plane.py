"""Company control-plane API for activation, credits and paid providers.

This application is deliberately separate from the desktop FastAPI process.
It does not start crawler, transcription, publishing or rendering workers.  A
reverse proxy is expected to terminate TLS; only opaque short-lived session
tokens cross the public boundary and supplier credentials remain in server
environment variables.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from database.migrations.runner import MigrationRunner
from project.backend.app.api.v1.auth import router as auth_router
from project.backend.app.api.v1.copywriting import router as copywriting_router
from project.backend.app.api.v1.credits import router as credits_router
from project.backend.app.api.v1.customer_admin import router as customer_admin_router
from project.backend.app.api.v1.provider_copywriting import (
    router as provider_copywriting_router,
)
from project.backend.app.api.v1.provider_asr import router as provider_asr_router
from project.backend.app.api.v1.provider_avatar import router as provider_avatar_router
from project.backend.app.api.v1.provider_release_acceptance import (
    router as provider_release_acceptance_router,
)
from project.backend.app.api.v1.provider_video_editor import (
    router as provider_video_editor_router,
)
from project.backend.app.core.config import DATABASE_PATH
from project.backend.app.core.control_plane_operations import (
    claim_operation,
    complete_operation,
    mark_operation_unknown,
    recover_pending_operations,
    request_fingerprint,
    valid_idempotency_key,
)
from project.backend.app.core.control_plane_runtime import (
    validate_control_plane_runtime,
)
from project.backend.app.core.security import (
    check_auth_rate_limit,
    verify_auth_token,
)
from project.backend.app.release_version import get_release_version
from src.models import AvatarAssetKind
from src.services.credits import set_current_owner

MAX_IDEMPOTENT_RESPONSE_BYTES = 2 * 1024 * 1024
RELEASE_VERSION = get_release_version()


def _allowed_hosts() -> list[str]:
    raw = os.getenv("CONTROL_PLANE_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
    return [host.strip() for host in raw.split(",") if host.strip()]


@asynccontextmanager
async def lifespan(_application: FastAPI):
    validate_control_plane_runtime()
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MigrationRunner(DATABASE_PATH).upgrade()
    recover_pending_operations()
    yield


app = FastAPI(
    title="VideoInsight 公司控制层",
    version=RELEASE_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts())


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def authenticate(request: Request, call_next):
    path = request.url.path
    if path in {"/", "/health", "/ready"}:
        return await call_next(request)
    if path.startswith("/api/v1/auth/"):
        if path.endswith(("customer-login", "admin-login")):
            try:
                await check_auth_rate_limit(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return await call_next(request)
    if not path.startswith("/api/v1/"):
        return JSONResponse(status_code=404, content={"message": "页面不存在。"})

    admin_token = request.headers.get("X-Admin-Token", "").strip()
    customer_token = request.headers.get("X-Customer-Token", "").strip()
    token = admin_token or customer_token
    record = verify_auth_token(token) if token else None
    if record is None:
        return JSONResponse(
            status_code=401,
            content={"message": "登录已过期，请重新登录。"},
        )
    if path.startswith("/api/v1/admin") and record["role"] != "admin":
        return JSONResponse(status_code=403, content={"message": "需要管理员权限。"})
    if admin_token and record["role"] != "admin":
        return JSONResponse(status_code=403, content={"message": "管理员身份无效。"})
    if customer_token and record["role"] != "customer":
        return JSONResponse(status_code=403, content={"message": "客户身份无效。"})

    if record["role"] == "admin":
        request.state.admin_username = record["subject"]
        set_current_owner("admin")
    else:
        request.state.customer_code = record["subject"]
        set_current_owner(str(record["subject"]))
    mutation_needs_idempotency = (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and path != "/api/v1/credits/login"
    )
    if mutation_needs_idempotency:
        return await _run_idempotent_paid_request(
            request,
            call_next,
            owner=f"{record['role']}:{record['subject']}",
        )
    return await call_next(request)


async def _run_idempotent_paid_request(request: Request, call_next, *, owner: str):
    key = request.headers.get("Idempotency-Key", "").strip()
    if not valid_idempotency_key(key):
        return JSONResponse(
            status_code=400,
            content={"message": "本次操作缺少安全请求标识，请刷新页面后重试。"},
        )
    is_streamed_provider_upload = request.url.path in {
        "/api/v1/provider/asr/upload",
        "/api/v1/provider/video-editor/upload",
        "/api/v1/provider/avatar/assets/train-avatar",
        "/api/v1/provider/avatar/assets/train-voice",
    }
    if is_streamed_provider_upload:
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > 513 * 1024 * 1024:
            return JSONResponse(
                status_code=413, content={"message": "上传素材不能超过 512MB。"}
            )
        operation_fingerprint = request.headers.get(
            "X-Operation-Fingerprint", ""
        ).strip()
        if not re.fullmatch(r"[a-f0-9]{64}", operation_fingerprint.casefold()):
            return JSONResponse(
                status_code=400, content={"message": "素材校验信息缺失。"}
            )
        body = operation_fingerprint.encode("ascii", errors="ignore")
    else:
        body = await request.body()
        if len(body) > 2 * 1024 * 1024:
            return JSONResponse(status_code=413, content={"message": "请求内容过大。"})
    operation_type = request.url.path
    fingerprint = request_fingerprint(operation_type, body)
    claimed, existing = claim_operation(
        owner=owner,
        operation_type=operation_type,
        idempotency_key=key,
        request_hash=fingerprint,
    )
    if not claimed:
        if existing is None or existing.request_hash != fingerprint:
            return JSONResponse(
                status_code=409,
                content={"message": "请求标识与原操作不一致，请刷新页面后重试。"},
            )
        if existing.state == "completed" and existing.response_status is not None:
            headers = {"X-Idempotent-Replay": "true"}
            if existing.response_content_type:
                headers["Content-Type"] = existing.response_content_type
            return Response(
                content=existing.response_body or b"",
                status_code=existing.response_status,
                headers=headers,
            )
        message = (
            "上次操作结果暂时无法确认，系统不会自动重复提交或重复扣费。"
            if existing.state == "unknown"
            else "本次操作正在处理中，请稍后查看结果。"
        )
        return JSONResponse(status_code=409, content={"message": message})

    try:
        response = await call_next(request)
        response_body = b"".join([chunk async for chunk in response.body_iterator])
    except Exception:
        mark_operation_unknown(
            owner=owner,
            operation_type=operation_type,
            idempotency_key=key,
        )
        raise

    if (
        response.status_code >= 500
        or len(response_body) > MAX_IDEMPOTENT_RESPONSE_BYTES
    ):
        mark_operation_unknown(
            owner=owner,
            operation_type=operation_type,
            idempotency_key=key,
        )
    else:
        complete_operation(
            owner=owner,
            operation_type=operation_type,
            idempotency_key=key,
            response_status=response.status_code,
            response_body=response_body,
            response_content_type=response.headers.get("content-type"),
        )
    return Response(
        content=response_body,
        status_code=response.status_code,
        headers=dict(response.headers),
        background=response.background,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "message": "请求参数校验失败。",
            "details": [
                {
                    "field": " -> ".join(str(part) for part in error.get("loc", [])),
                    "message": error.get("msg", ""),
                }
                for error in exc.errors()
            ],
        },
    )


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "VideoInsight control plane", "status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "release_version": RELEASE_VERSION}


@app.get("/ready")
def ready() -> dict[str, str]:
    try:
        with sqlite3.connect(str(DATABASE_PATH), timeout=3) as connection:
            connection.execute("SELECT 1").fetchone()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="数据库尚未就绪。") from exc
    return {"status": "ready"}


@app.get("/api/v1/admin/server-status")
def server_status() -> dict[str, object]:
    """Return configuration state only; never return supplier secret values."""

    from project.backend.app.core.config import (
        COPYWRITING_API_KEY,
        COPYWRITING_ESTIMATED_REQUEST_COST_CNY,
        COPYWRITING_MODE,
    )
    from project.backend.app.core.server_asr import get_server_asr_runtime
    from project.backend.app.core.server_avatar import get_server_avatar_provider
    from project.backend.app.core.server_video_editor import (
        get_server_video_editor_runtime,
    )
    from project.backend.app.api.v1.provider_avatar import _customer_capability
    from src.services.video_editor_cloud import get_cloud_capability

    def safe_capability(factory):
        try:
            return factory()
        except Exception:
            return {"enabled": False, "missing_configuration": ["服务配置读取失败"]}

    def redact_configuration_names(
        capability: dict[str, object], *, unavailable_message: str
    ) -> dict[str, object]:
        """Keep readiness useful without exposing secret/config variable names."""

        if capability.get("missing_configuration"):
            return {
                **capability,
                "missing_configuration": [unavailable_message],
            }
        return capability

    asr = redact_configuration_names(
        safe_capability(lambda: get_server_asr_runtime().capability()),
        unavailable_message="公司云端转写配置尚未完成",
    )
    video = redact_configuration_names(
        safe_capability(
            lambda: get_cloud_capability(
                get_server_video_editor_runtime().configuration
            ).model_dump(mode="json")
        ),
        unavailable_message="公司云端剪辑配置尚未完成",
    )

    def avatar_capability() -> dict[str, object]:
        provider = get_server_avatar_provider()
        capability = _customer_capability(provider).model_dump(mode="json")
        checker = getattr(provider, "has_ready_shared_asset", None)
        capability["required_shared_assets"] = {
            "avatar": {
                "asset_id": "shuying-avatar-21920",
                "provider_asset_id": "21920",
                "ready": bool(
                    callable(checker)
                    and checker(
                        asset_id="shuying-avatar-21920",
                        kind=AvatarAssetKind.AVATAR,
                        provider_asset_id="21920",
                    )
                ),
            },
            "voice": {
                "asset_id": "shuying-voice-7869",
                "provider_asset_id": "7869",
                "ready": bool(
                    callable(checker)
                    and checker(
                        asset_id="shuying-voice-7869",
                        kind=AvatarAssetKind.VOICE,
                        provider_asset_id="7869",
                    )
                ),
            },
        }
        return redact_configuration_names(
            capability,
            unavailable_message="公司数字人配置尚未完成",
        )

    avatar = safe_capability(avatar_capability)
    copy_production = str(COPYWRITING_MODE) == "production"
    return {
        "service": "ready",
        "crawler": {
            "location": "customer_desktop",
            "billable": False,
            "server_provider_disabled": True,
        },
        "copywriting": {
            "mode": str(COPYWRITING_MODE),
            "enabled": bool(
                copy_production
                and COPYWRITING_API_KEY
                and COPYWRITING_ESTIMATED_REQUEST_COST_CNY is not None
            ),
            "estimated_cost_configured": (
                COPYWRITING_ESTIMATED_REQUEST_COST_CNY is not None
            ),
        },
        "transcription": asr,
        "video_editor": video,
        "avatar": avatar,
    }


app.include_router(auth_router)
app.include_router(credits_router)
app.include_router(customer_admin_router)
app.include_router(copywriting_router)
app.include_router(provider_copywriting_router)
app.include_router(provider_asr_router)
app.include_router(provider_video_editor_router)
app.include_router(provider_avatar_router)
app.include_router(provider_release_acceptance_router)
