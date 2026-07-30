"""发布 API。"""

from __future__ import annotations

import os
import secrets
import json
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from shutil import copy2, copyfileobj
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from project.backend.app.core import config as backend_config
from project.backend.app.core import deps as backend_deps
from project.backend.app.core.config import PROJECT_ROOT
from project.backend.app.core.deps import get_publish_service, get_repository
from src.services.publish_accounts import PublishAccountError, publish_account_manager

router = APIRouter(prefix="/api/v1/publish", tags=["publish"])

PUBLISH_ASSET_DIR = PROJECT_ROOT / "data" / "publish_assets"

PUBLISH_CONFIG_FIELDS: dict[str, dict[str, str]] = {
    "douyin": {
        "mode": "PUBLISH_DOUYIN_MODE",
        "access_token": "PUBLISH_DOUYIN_ACCESS_TOKEN",
        "open_id": "PUBLISH_DOUYIN_OPEN_ID",
        "client_key": "PUBLISH_DOUYIN_CLIENT_KEY",
        "client_secret": "PUBLISH_DOUYIN_CLIENT_SECRET",
        "redirect_uri": "PUBLISH_DOUYIN_REDIRECT_URI",
        "refresh_token": "PUBLISH_DOUYIN_REFRESH_TOKEN",
        "token_expires_at": "PUBLISH_DOUYIN_TOKEN_EXPIRES_AT",
        "refresh_expires_at": "PUBLISH_DOUYIN_REFRESH_EXPIRES_AT",
    },
    "kuaishou": {
        "mode": "PUBLISH_KUAISHOU_MODE",
        "access_token": "PUBLISH_KUAISHOU_ACCESS_TOKEN",
        "open_id": "PUBLISH_KUAISHOU_OPEN_ID",
        "client_key": "PUBLISH_KUAISHOU_CLIENT_KEY",
        "client_secret": "PUBLISH_KUAISHOU_CLIENT_SECRET",
    },
    "xiaohongshu": {
        "mode": "PUBLISH_XIAOHONGSHU_MODE",
        "access_token": "PUBLISH_XIAOHONGSHU_ACCESS_TOKEN",
        "open_id": "PUBLISH_XIAOHONGSHU_OPEN_ID",
        "client_key": "PUBLISH_XIAOHONGSHU_CLIENT_KEY",
        "client_secret": "PUBLISH_XIAOHONGSHU_CLIENT_SECRET",
    },
    "wechat_channels": {
        "mode": "PUBLISH_WECHAT_CHANNELS_MODE",
        "access_token": "PUBLISH_WECHAT_CHANNELS_ACCESS_TOKEN",
        "open_id": "PUBLISH_WECHAT_CHANNELS_OPEN_ID",
        "client_key": "PUBLISH_WECHAT_CHANNELS_CLIENT_KEY",
        "client_secret": "PUBLISH_WECHAT_CHANNELS_CLIENT_SECRET",
    },
}

SECRET_CONFIG_FIELDS = {"access_token", "client_secret", "refresh_token"}
DOUYIN_AUTH_SCOPES = "user_info,video.create.bind"
DOUYIN_OAUTH_CONNECT_URL = "https://open.douyin.com/platform/oauth/connect/"
DOUYIN_OAUTH_TOKEN_URL = "https://open.douyin.com/oauth/access_token/"
_DOUYIN_PENDING_STATES: dict[str, datetime] = {}


class PublishRequest(BaseModel):
    video_path: str = Field(..., description="视频路径")
    platform: str = Field(..., description="平台标识")
    title: str = Field(..., min_length=1, description="标题")
    description: str = Field("", description="描述")
    tags: list[str] = Field(default_factory=list, description="标签")
    native_music_mode: str = Field(
        "auto_recommended",
        pattern="^(off|auto_recommended)$",
        description="平台原生配乐方式",
    )
    native_music_hint: str = Field("", max_length=80, description="配乐情绪提示")


class PublishPreflightRequest(BaseModel):
    video_path: str = Field(..., description="视频路径")
    platforms: list[str] = Field(..., min_length=1, description="平台标识列表")
    title: str = Field(..., min_length=1, description="标题")
    description: str = Field("", description="描述")
    tags: list[str] = Field(default_factory=list, description="标签")
    account_ids: dict[str, str] = Field(
        default_factory=dict, description="平台对应的本机发布账号"
    )
    native_music_mode: str = Field(
        "auto_recommended",
        pattern="^(off|auto_recommended)$",
        description="平台原生配乐方式",
    )
    native_music_hint: str = Field("", max_length=80, description="配乐情绪提示")


class PublishBatchRequest(PublishPreflightRequest):
    confirmation_accepted: bool = Field(False, description="是否已确认预检结果")
    source_pipeline_run_id: str | None = None


class ManualPublishResultRequest(BaseModel):
    succeeded: bool | None = Field(
        ..., description="true=已发布，false=失败，null=结果不确定"
    )
    platform_url: str | None = None
    platform_video_id: str | None = None
    note: str = ""


class PublishTaskBatchDeleteRequest(BaseModel):
    task_ids: list[str] = Field(..., min_length=1, max_length=100)


class PublishVariableStatus(BaseModel):
    field: str
    key: str
    configured: bool
    masked_value: str
    secret: bool


class PublishPlatformConfig(BaseModel):
    platform: str
    display_name: str
    mode: str
    env_path: str
    variables: list[PublishVariableStatus]


class PublishConfigResponse(BaseModel):
    env_path: str
    platforms: list[PublishPlatformConfig]


class PublishPlatformConfigUpdate(BaseModel):
    mode: str = Field("manual", pattern="^(manual|official)$")
    access_token: str | None = None
    open_id: str | None = None
    client_key: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None


class PublishConnectionResponse(BaseModel):
    platform: str
    state: str
    ready: bool
    account: str | None = None
    expires_at: str | None = None
    message: str
    authorization_available: bool


class PublishConnectionStartResponse(BaseModel):
    platform: str
    authorization_url: str


class PublishAccountCreateRequest(BaseModel):
    platform: str = Field(
        "douyin",
        pattern="^(douyin|kuaishou|wechat_channels|xiaohongshu|bilibili)$",
    )
    name: str = Field(..., min_length=1, max_length=40)


class PublishAccountUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    auto_publish_authorized: bool | None = None


class ConfirmAutoPublishRequest(BaseModel):
    confirmation_accepted: bool = False


class PublishAccountResponse(BaseModel):
    account_id: str
    platform: str
    name: str
    status: str
    message: str
    auto_publish_authorized: bool = False
    last_verified_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class PublishResponse(BaseModel):
    task_id: str
    batch_id: str | None = None
    status: str
    publish_status: str
    platform: str
    title: str
    native_music_mode: str = "off"
    native_music_hint: str = ""
    selected_music_title: str | None = None
    stage: str
    provider_name: str
    platform_video_id: str | None = None
    platform_url: str | None = None
    is_mock: bool
    error_message: str | None = None
    action_required: str | None = None
    final_publish_started_at: str | None = None
    outcome_evidence: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class PublishBatchResponse(BaseModel):
    batch_id: str
    status: str
    total: int
    succeeded: int
    failed: int
    outcome_unknown: int
    pending: int
    created_at: str
    updated_at: str
    tasks: list[PublishResponse]


def _build_targets(body: PublishPreflightRequest):
    from src.models import PublishPlatform, PublishTarget

    targets = []
    for platform_key in body.platforms:
        try:
            platform = PublishPlatform(platform_key)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"不支持的平台: {platform_key}")
        targets.append(
            PublishTarget(
                platform=platform,
                title=body.title,
                description=body.description,
                tags=body.tags,
                account_id=body.account_ids.get(platform_key),
                native_music_mode=(
                    body.native_music_mode
                    if platform == PublishPlatform.DOUYIN
                    else "off"
                ),
                native_music_hint=(
                    body.native_music_hint
                    if platform == PublishPlatform.DOUYIN
                    else ""
                ),
                auto_publish_authorized=bool(
                    isinstance(body, PublishBatchRequest)
                    and body.confirmation_accepted
                    and platform == PublishPlatform.DOUYIN
                ),
            )
        )
    return targets


def _account_response(account) -> PublishAccountResponse:
    return PublishAccountResponse(**account.to_public_dict())


def _validate_local_browser_accounts(
    preflight: dict[str, Any], targets: list[Any]
) -> dict[str, Any]:
    """Fail closed when a browser-publisher account was deleted or expired.

    The UI can keep an old selected account during a long-lived browser session,
    so account identity and profile state must be checked again on the server.
    """
    for target, platform_result in zip(targets, preflight["platforms"]):
        if platform_result.get("mode") != "local_browser":
            continue

        account_id = target.account_id
        if not account_id:
            platform_result.update(
                can_create_task=False,
                issue="请先连接并选择该平台的账号。",
                issue_code="account_missing",
                account_status="missing",
            )
            continue

        try:
            account = publish_account_manager.status(account_id)
            if account.platform != target.platform.value:
                raise PublishAccountError("发布账号不存在或不属于该平台。")
        except PublishAccountError:
            platform_result.update(
                can_create_task=False,
                issue="该账号记录已失效，请重新添加并扫码连接。",
                issue_code="account_missing",
                account_status="missing",
            )
            continue

        platform_result["account_status"] = account.status
        if account.status != "ready":
            platform_result.update(
                can_create_task=False,
                issue="账号尚未实际核验登录，请在官方窗口完成扫码后点击“我已扫码，核验”。",
                issue_code="account_not_ready",
            )

    preflight["blocked"] = bool(preflight["issues"]) or any(
        not item["can_create_task"] for item in preflight["platforms"]
    )
    return preflight


def _task_response(task) -> PublishResponse:
    return PublishResponse(
        task_id=task.task_id,
        batch_id=task.batch_id,
        status=task.status.value,
        publish_status=task.publish_status.value,
        platform=task.target.platform.value,
        title=task.target.title,
        native_music_mode=task.target.native_music_mode,
        native_music_hint=task.target.native_music_hint,
        selected_music_title=task.target.selected_music_title,
        stage=task.stage,
        provider_name=task.provider_name,
        platform_video_id=task.platform_video_id,
        platform_url=task.platform_url,
        is_mock=task.is_mock,
        error_message=task.error_message,
        action_required=task.action_required,
        final_publish_started_at=task.final_publish_started_at.isoformat() if task.final_publish_started_at else None,
        outcome_evidence=task.outcome_evidence,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


def _batch_response(summary: dict[str, Any]) -> PublishBatchResponse:
    return PublishBatchResponse(
        batch_id=summary["batch_id"],
        status=summary["status"],
        total=summary["total"],
        succeeded=summary["succeeded"],
        failed=summary["failed"],
        outcome_unknown=summary["outcome_unknown"],
        pending=summary["pending"],
        created_at=summary["created_at"],
        updated_at=summary["updated_at"],
        tasks=[_task_response(task) for task in summary["tasks"]],
    )


def _mask_value(value: str, *, secret: bool) -> str:
    if not value:
        return ""
    if not secret and len(value) <= 16:
        return value
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def _env_value(key: str) -> str:
    return os.getenv(
        key, backend_config._parse_env_file(backend_config.ENV_PATH).get(key, "")
    ).strip()


def _platform_config(platform: str) -> PublishPlatformConfig:
    fields = PUBLISH_CONFIG_FIELDS[platform]
    mode = _env_value(fields["mode"]) or "manual"
    variables = []
    for field, key in fields.items():
        value = _env_value(key)
        is_secret = field in SECRET_CONFIG_FIELDS
        variables.append(
            PublishVariableStatus(
                field=field,
                key=key,
                configured=bool(value),
                masked_value=_mask_value(value, secret=is_secret),
                secret=is_secret,
            )
        )
    return PublishPlatformConfig(
        platform=platform,
        display_name={
            "douyin": "抖音",
            "kuaishou": "快手",
            "xiaohongshu": "小红书",
            "wechat_channels": "视频号",
        }[platform],
        mode=mode if mode in {"manual", "official"} else "manual",
        env_path=str(backend_config.ENV_PATH),
        variables=variables,
    )


def _format_env_line(key: str, value: str) -> str:
    normalized = value.strip()
    if any(ch.isspace() for ch in normalized) or "#" in normalized:
        normalized = '"' + normalized.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{key}={normalized}"


def _update_env_file(updates: dict[str, str]) -> None:
    env_path = backend_config.ENV_PATH
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = (
        env_path.read_text(encoding="utf-8-sig").splitlines()
        if env_path.exists()
        else []
    )
    remaining = dict(updates)
    output_lines: list[str] = []
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            output_lines.append(raw_line)
            continue
        key = raw_line.split("=", 1)[0].strip()
        if key in remaining:
            output_lines.append(_format_env_line(key, remaining.pop(key)))
        else:
            output_lines.append(raw_line)
    if remaining:
        if output_lines and output_lines[-1].strip():
            output_lines.append("")
        output_lines.append("# Publish platform configuration")
        for key, value in remaining.items():
            output_lines.append(_format_env_line(key, value))
    env_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
    for key, value in updates.items():
        os.environ[key] = value
    backend_deps.get_publishers.cache_clear()
    backend_deps.get_publish_service.cache_clear()


def _config_updates(platform: str, body: PublishPlatformConfigUpdate) -> dict[str, str]:
    if platform not in PUBLISH_CONFIG_FIELDS:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")
    fields = PUBLISH_CONFIG_FIELDS[platform]
    updates = {fields["mode"]: body.mode}
    for field in (
        "access_token",
        "open_id",
        "client_key",
        "client_secret",
        "redirect_uri",
    ):
        if field not in fields:
            continue
        value = getattr(body, field)
        if value is not None and value.strip():
            updates[fields[field]] = value.strip()
    return updates


def _parse_timestamp(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _douyin_connection() -> PublishConnectionResponse:
    fields = PUBLISH_CONFIG_FIELDS["douyin"]
    client_key = _env_value(fields["client_key"])
    client_secret = _env_value(fields["client_secret"])
    redirect_uri = _env_value(fields["redirect_uri"])
    access_token = _env_value(fields["access_token"])
    open_id = _env_value(fields["open_id"])
    expires_at = _parse_timestamp(_env_value(fields["token_expires_at"]))
    auth_ready = bool(client_key and client_secret and redirect_uri)

    if not auth_ready:
        return PublishConnectionResponse(
            platform="douyin",
            state="not_configured",
            ready=False,
            message="管理员需先配置抖音网站应用、Client Key、Client Secret 和 HTTPS 授权回调地址。",
            authorization_available=False,
        )
    if not access_token or not open_id:
        return PublishConnectionResponse(
            platform="douyin",
            state="disconnected",
            ready=False,
            message="应用已就绪，请扫码连接抖音账号。",
            authorization_available=True,
        )
    if expires_at and expires_at <= datetime.now(timezone.utc):
        return PublishConnectionResponse(
            platform="douyin",
            state="expired",
            ready=False,
            account=_mask_value(open_id, secret=True),
            expires_at=expires_at.isoformat(),
            message="抖音授权已过期，请重新扫码连接。",
            authorization_available=True,
        )
    return PublishConnectionResponse(
        platform="douyin",
        state="connected",
        ready=True,
        account=_mask_value(open_id, secret=True),
        expires_at=expires_at.isoformat() if expires_at else None,
        message="抖音账号已连接。授权凭证仅保存在本机服务端，不会回显到页面。",
        authorization_available=True,
    )


def _make_douyin_authorization_url() -> str:
    fields = PUBLISH_CONFIG_FIELDS["douyin"]
    client_key = _env_value(fields["client_key"])
    redirect_uri = _env_value(fields["redirect_uri"])
    if not client_key or not redirect_uri:
        raise HTTPException(status_code=400, detail="抖音网站应用尚未完成管理员接入。")
    if not redirect_uri.startswith("https://"):
        raise HTTPException(
            status_code=400,
            detail="抖音授权回调地址必须使用已配置的 HTTPS 地址。",
        )
    now = datetime.now(timezone.utc)
    _DOUYIN_PENDING_STATES.clear()
    state = secrets.token_urlsafe(24)
    _DOUYIN_PENDING_STATES[state] = now + timedelta(minutes=10)
    query = urlencode(
        {
            "client_key": client_key,
            "response_type": "code",
            "scope": DOUYIN_AUTH_SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{DOUYIN_OAUTH_CONNECT_URL}?{query}"


def _exchange_douyin_code(code: str) -> dict[str, Any]:
    fields = PUBLISH_CONFIG_FIELDS["douyin"]
    response = httpx.post(
        DOUYIN_OAUTH_TOKEN_URL,
        data={
            "client_key": _env_value(fields["client_key"]),
            "client_secret": _env_value(fields["client_secret"]),
            "code": code,
            "grant_type": "authorization_code",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15.0,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502, detail="抖音授权响应无效，请重新扫码。"
        ) from exc
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if response.is_error or not isinstance(data, dict) or not data.get("access_token"):
        raise HTTPException(
            status_code=502, detail="抖音未返回有效授权，请重新扫码或检查应用权限。"
        )
    return data


def _save_douyin_tokens(token_data: dict[str, Any]) -> None:
    fields = PUBLISH_CONFIG_FIELDS["douyin"]
    now = datetime.now(timezone.utc)

    def expires_at(seconds: Any) -> str:
        try:
            return (now + timedelta(seconds=max(0, int(seconds)))).isoformat()
        except (TypeError, ValueError):
            return ""

    updates = {
        fields["access_token"]: str(token_data["access_token"]).strip(),
        fields["open_id"]: str(token_data.get("open_id", "")).strip(),
        fields["refresh_token"]: str(token_data.get("refresh_token", "")).strip(),
        fields["token_expires_at"]: expires_at(token_data.get("expires_in")),
        fields["refresh_expires_at"]: expires_at(token_data.get("refresh_expires_in")),
    }
    _update_env_file(updates)


def _connection_callback_page(message: str, *, succeeded: bool) -> HTMLResponse:
    event = json.dumps(
        {
            "type": "publish-connection",
            "platform": "douyin",
            "succeeded": succeeded,
        }
    )
    title = "抖音账号已连接" if succeeded else "抖音连接未完成"
    return HTMLResponse(
        content=(
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            f"<title>{title}</title><body><p>{escape(message)}</p>"
            f"<script>window.opener&&window.opener.postMessage({event}, '*');"
            "window.setTimeout(()=>window.close(), 1600);</script></body></html>"
        )
    )


@router.get("/config", response_model=PublishConfigResponse)
def get_publish_config():
    """读取发布配置状态，不回显完整密钥。"""
    return PublishConfigResponse(
        env_path=str(backend_config.ENV_PATH),
        platforms=[
            _platform_config(platform)
            for platform in (
                "douyin",
                "kuaishou",
                "wechat_channels",
                "xiaohongshu",
            )
        ],
    )


@router.put("/config/{platform}", response_model=PublishPlatformConfig)
def update_publish_config(
    platform: str,
    body: PublishPlatformConfigUpdate,
):
    """保存单个平台发布配置到根目录 .env。"""
    updates = _config_updates(platform, body)
    _update_env_file(updates)
    return _platform_config(platform)


@router.get("/connections/douyin", response_model=PublishConnectionResponse)
def get_douyin_connection():
    """返回抖音授权状态，绝不返回用户 token。"""
    return _douyin_connection()


@router.post(
    "/connections/douyin/start",
    response_model=PublishConnectionStartResponse,
)
def start_douyin_connection():
    """Stop the obsolete OAuth route used by stale browser pages.

    Local browser publishing must never send a normal publisher into the
    developer-platform OAuth flow, which requires an app key and callback.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "旧版抖音开发者平台授权已停用。请刷新“多平台发布”页面，"
            "先添加账号，再点击“打开官方扫码窗口”。"
        ),
    )


@router.get("/connections/douyin/callback", response_class=HTMLResponse)
def complete_douyin_connection(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
):
    """接收抖音 OAuth 回调，交换并仅在服务端保存授权凭证。"""
    expires_at = _DOUYIN_PENDING_STATES.pop(state or "", None)
    if not state or expires_at is None or expires_at < datetime.now(timezone.utc):
        return _connection_callback_page(
            "授权请求已失效，请回到系统重新扫码。", succeeded=False
        )
    if error:
        detail = error_description or error
        return _connection_callback_page(f"抖音未完成授权：{detail}", succeeded=False)
    if not code:
        return _connection_callback_page(
            "抖音未返回授权码，请重新扫码。", succeeded=False
        )
    try:
        token_data = _exchange_douyin_code(code)
        if not str(token_data.get("open_id", "")).strip():
            raise HTTPException(
                status_code=502, detail="抖音未返回账号标识，请重新扫码。"
            )
        _save_douyin_tokens(token_data)
    except HTTPException as exc:
        return _connection_callback_page(str(exc.detail), succeeded=False)
    return _connection_callback_page("抖音账号已连接，可关闭此页面。", succeeded=True)


@router.post("/connections/douyin/disconnect", response_model=PublishConnectionResponse)
def disconnect_douyin_connection():
    """仅清除本机保存的抖音用户授权，不会操作平台账号。"""
    fields = PUBLISH_CONFIG_FIELDS["douyin"]
    _update_env_file(
        {
            fields["access_token"]: "",
            fields["open_id"]: "",
            fields["refresh_token"]: "",
            fields["token_expires_at"]: "",
            fields["refresh_expires_at"]: "",
        }
    )
    return _douyin_connection()


@router.get("/platforms")
def list_platforms(
    service=Depends(get_publish_service),
):
    """列出可用发布平台。"""
    return {"platforms": service.available_platforms()}


@router.get("/accounts", response_model=list[PublishAccountResponse])
def list_publish_accounts(platform: str | None = None):
    accounts = []
    for account in publish_account_manager.list(platform):
        # Refreshing here prevents a removed dedicated profile from being shown
        # as a reusable login after the page reloads.
        accounts.append(
            _account_response(publish_account_manager.status(account.account_id))
        )
    return accounts


@router.post("/accounts", response_model=PublishAccountResponse)
def create_publish_account(body: PublishAccountCreateRequest):
    try:
        return _account_response(
            publish_account_manager.create(platform=body.platform, name=body.name)
        )
    except PublishAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/accounts/{account_id}/status", response_model=PublishAccountResponse)
def get_publish_account_status(account_id: str):
    try:
        return _account_response(publish_account_manager.status(account_id))
    except PublishAccountError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/accounts/{account_id}/connect", response_model=PublishAccountResponse)
def connect_publish_account(account_id: str):
    try:
        return _account_response(publish_account_manager.open_login_browser(account_id))
    except PublishAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/accounts/{account_id}/verify", response_model=PublishAccountResponse)
def verify_publish_account(account_id: str):
    """Verify the currently visible official creator page, never profile files."""
    try:
        return _account_response(publish_account_manager.verify_session(account_id))
    except PublishAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/accounts/{account_id}", response_model=PublishAccountResponse)
def update_publish_account(account_id: str, body: PublishAccountUpdateRequest):
    try:
        account = publish_account_manager.get(account_id)
        if body.name is not None:
            account = publish_account_manager.rename(account_id, name=body.name)
        if body.auto_publish_authorized is not None:
            account = publish_account_manager.set_auto_publish_authorized(
                account.account_id, authorized=body.auto_publish_authorized
            )
        return _account_response(account)
    except PublishAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/accounts/{account_id}", status_code=204)
def delete_publish_account(account_id: str):
    try:
        publish_account_manager.delete(account_id)
    except PublishAccountError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/assets")
def list_assets():
    """列出已上传到本机的待发布成片。"""
    PUBLISH_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(
        PUBLISH_ASSET_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if not path.is_file():
            continue
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "size_bytes": stat.st_size,
                "updated_at": stat.st_mtime,
            }
        )
    return {"items": items, "total": len(items)}


@router.post("/assets/upload")
def upload_asset(
    file: UploadFile = File(...),
    rights_confirmed: bool = Form(True),
):
    """上传本机成片，用于人工或官方发布任务。"""
    if not rights_confirmed:
        raise HTTPException(status_code=400, detail="请先确认拥有该成片的发布权。")
    filename = Path(file.filename or "video.mp4").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v"}:
        raise HTTPException(status_code=400, detail="仅支持 mp4、mov、m4v 成片文件。")

    PUBLISH_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    target = PUBLISH_ASSET_DIR / filename
    if target.exists():
        target = (
            PUBLISH_ASSET_DIR
            / f"{target.stem}-{len(list(PUBLISH_ASSET_DIR.glob(target.stem + '*')))}{target.suffix}"
        )
    try:
        with target.open("wb") as out_file:
            copyfileobj(file.file, out_file)
    finally:
        file.file.close()
    return {
        "name": target.name,
        "path": str(target),
        "size_bytes": target.stat().st_size,
    }


@router.post("/assets/from-edit/{task_id}")
def import_edited_asset(
    task_id: str,
    repository=Depends(get_repository),
):
    """登记系统生成的真实剪辑成片，供发布页继续使用。"""
    from src.models import TaskStatus, VideoEditTask

    task = repository.get_task(task_id)
    if not isinstance(task, VideoEditTask):
        raise HTTPException(status_code=404, detail="未找到智能剪辑任务。")
    if task.outputs.get("workflow") != "edit":
        raise HTTPException(
            status_code=400, detail="该任务不是智能剪辑工作流生成的成片。"
        )
    if task.status != TaskStatus.SUCCEEDED or task.is_mock:
        raise HTTPException(status_code=400, detail="仅可交接已成功生成的真实成片。")
    if not task.result_path:
        raise HTTPException(status_code=400, detail="剪辑任务没有可发布的成片。")

    source = Path(task.result_path).resolve()
    if not source.is_file():
        raise HTTPException(
            status_code=404, detail="剪辑成片文件不存在，请重新执行任务。"
        )
    if source.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        raise HTTPException(
            status_code=400, detail="仅支持交接 mp4、mov、m4v 成片文件。"
        )

    PUBLISH_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    target = PUBLISH_ASSET_DIR / f"ai-edit-{task.task_id}{source.suffix.lower()}"
    if not target.exists():
        copy2(source, target)
    music_hint = ""
    batch_id = str(task.outputs.get("batch_id") or "").strip()
    item_id = str(task.outputs.get("item_id") or "").strip()
    if batch_id and item_id:
        batch = repository.get_video_editor_batch(batch_id)
        if batch is not None:
            item = next(
                (entry for entry in batch.items if entry.item_id == item_id),
                None,
            )
            if item is not None and item.edit_plan:
                plan = dict(item.edit_plan)
                hint_parts = [
                    str(plan.get("bgm_category") or "").strip(),
                    str(plan.get("bgm_energy") or "").strip(),
                    *[
                        str(keyword).strip()
                        for keyword in list(plan.get("bgm_keywords") or [])[:3]
                    ],
                ]
                music_hint = " ".join(part for part in hint_parts if part)[:80]
    stat = target.stat()
    return {
        "name": target.name,
        "path": str(target),
        "size_bytes": stat.st_size,
        "updated_at": stat.st_mtime,
        "recommended_title": task.outputs.get("publish_title") or None,
        "recommended_music_hint": music_hint or None,
    }


@router.post("/preflight")
def preflight_publish(
    body: PublishPreflightRequest,
    service=Depends(get_publish_service),
):
    """发布前检查，不创建任务。"""
    targets = _build_targets(body)
    result = service.preflight(video_path=body.video_path, targets=targets)
    return _validate_local_browser_accounts(result, targets)


@router.post("/batches", response_model=PublishBatchResponse)
def create_publish_batch(
    body: PublishBatchRequest,
    service=Depends(get_publish_service),
):
    """创建多平台发布批次。"""
    targets = _build_targets(body)
    preflight = _validate_local_browser_accounts(
        service.preflight(video_path=body.video_path, targets=targets), targets
    )
    if preflight["blocked"]:
        raise HTTPException(
            status_code=400, detail={"message": "发布预检未通过", **preflight}
        )
    if not body.confirmation_accepted:
        raise HTTPException(status_code=400, detail="请先完成发布预检并确认。")
    summary = service.create_batch(
        video_path=body.video_path,
        targets=targets,
        source_pipeline_run_id=body.source_pipeline_run_id,
    )
    return _batch_response(summary)


@router.get("/batches")
def list_publish_batches(
    service=Depends(get_publish_service),
):
    """列出发布批次。"""
    batches = [_batch_response(summary) for summary in service.list_batches()]
    return {"items": batches, "total": len(batches)}


@router.get("/batches/{batch_id}", response_model=PublishBatchResponse)
def get_publish_batch(
    batch_id: str,
    service=Depends(get_publish_service),
):
    summary = service.get_batch(batch_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="发布批次不存在。")
    return _batch_response(summary)


@router.post("/tasks/{task_id}/manual-result", response_model=PublishResponse)
def record_manual_result(
    task_id: str,
    body: ManualPublishResultRequest,
    service=Depends(get_publish_service),
):
    try:
        task = service.record_manual_result(
            task_id=task_id,
            succeeded=body.succeeded,
            platform_url=body.platform_url,
            platform_video_id=body.platform_video_id,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _task_response(task)


@router.post("/tasks/{task_id}/retry", response_model=PublishResponse)
def retry_publish_task(
    task_id: str,
    service=Depends(get_publish_service),
):
    try:
        task = service.retry_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _task_response(task)


@router.post("/tasks/{task_id}/prepare-official-page", response_model=PublishResponse)
def prepare_publish_official_page(
    task_id: str,
    service=Depends(get_publish_service),
):
    try:
        task = service.prepare_official_page(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _task_response(task)


@router.post("/tasks/{task_id}/confirm-auto-publish", response_model=PublishResponse)
def confirm_auto_publish_task(
    task_id: str,
    body: ConfirmAutoPublishRequest,
    service=Depends(get_publish_service),
):
    if not body.confirmation_accepted:
        raise HTTPException(status_code=400, detail="请先确认本次自动发布。")
    try:
        task = service.confirm_auto_publish(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _task_response(task)


@router.post("/tasks/{task_id}/resume", response_model=PublishResponse)
def resume_publish_task(
    task_id: str,
    service=Depends(get_publish_service),
):
    try:
        task = service.resume_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _task_response(task)


@router.delete("/tasks/{task_id}")
def delete_publish_task(
    task_id: str,
    service=Depends(get_publish_service),
):
    try:
        deleted_task_id = service.delete_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task_id": deleted_task_id, "deleted": True}


@router.post("/tasks/delete-batch")
def delete_publish_tasks(
    body: PublishTaskBatchDeleteRequest,
    service=Depends(get_publish_service),
):
    try:
        deleted_task_ids = service.delete_tasks(body.task_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"deleted_task_ids": deleted_task_ids, "deleted": len(deleted_task_ids)}


@router.post("", response_model=PublishResponse)
def publish_video(
    body: PublishRequest,
    service=Depends(get_publish_service),
):
    """兼容旧单平台发布接口。"""
    from src.models import PublishPlatform, PublishTarget

    try:
        platform = PublishPlatform(body.platform)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {body.platform}")

    target = PublishTarget(
        platform=platform,
        title=body.title,
        description=body.description,
        tags=body.tags,
        native_music_mode=(
            body.native_music_mode
            if platform == PublishPlatform.DOUYIN
            else "off"
        ),
        native_music_hint=(
            body.native_music_hint
            if platform == PublishPlatform.DOUYIN
            else ""
        ),
    )
    try:
        task = service.publish(video_path=body.video_path, target=target)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _task_response(task)
