"""Cost-capped, fail-closed acceptance runner for real paid provider paths.

The command is deliberately plan-only by default.  A paid run needs both the
``--execute-paid`` flag and a separate environment acknowledgement.  Evidence
is intentionally sparse: credentials, generated prose, private bucket names,
and signed URLs never reach the journal.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.credits import cny_to_credits  # noqa: E402
from src.services.avatar_billing import credits_for_seconds  # noqa: E402
from src.services.video_editor_cloud import create_cost_quote  # noqa: E402


MAX_ACCEPTANCE_BUDGET = Decimal("5.00")
EXECUTION_ACK_ENV = "VIDEOINSIGHT_AUTHORIZE_PAID_ACCEPTANCE"
EXECUTION_ACK_VALUE = "I_UNDERSTAND_MAX_5_CNY"
ACTIVATION_CODE_ENV = "VIDEOINSIGHT_ACCEPTANCE_ACTIVATION_CODE"
BASE_URL_ENV = "VIDEOINSIGHT_ACCEPTANCE_BASE_URL"
EXPECTED_VERSION_ENV = "VIDEOINSIGHT_ACCEPTANCE_EXPECTED_VERSION"
AVATAR_ID = "shuying-avatar-21920"
VOICE_ID = "shuying-voice-7869"
AVATAR_SCRIPT = "交付测试"
AVATAR_ACCEPTANCE_MAX_BILLED_SECONDS = 2
OPENING_SECONDS = Decimal("1.4")
MAIN_FIXTURE_SECONDS = Decimal("2.0")


class AcceptanceError(RuntimeError):
    """A definite, non-retryable acceptance failure."""


class OutcomeUnknown(AcceptanceError):
    """A mutation may have reached a paid supplier; never submit it again."""


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class Transport(Protocol):
    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse: ...


class UrlLibTransport:
    """Small redirect-free transport so retries can reuse the exact body."""

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=dict(headers), method=method)
        opener = build_opener(ProxyHandler({}), _NoRedirectHandler())
        try:
            with opener.open(request, timeout=timeout) as response:
                return HttpResponse(
                    status_code=int(response.status),
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except HTTPError as exc:
            return HttpResponse(
                status_code=int(exc.code),
                headers=dict(exc.headers.items()) if exc.headers else {},
                body=exc.read(),
            )


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


@dataclass(frozen=True)
class RunnerConfig:
    base_url: str
    journal_path: Path
    expected_version: str = ""
    execute_paid: bool = False
    budget: Decimal = MAX_ACCEPTANCE_BUDGET
    fixture_dir: Path | None = None
    poll_interval_seconds: float = 3.0
    poll_timeout_seconds: float = 900.0
    request_timeout_seconds: float = 45.0


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _money(value: Decimal | str | float | int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def _safe_json(response: HttpResponse) -> Any:
    try:
        return response.json()
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("服务返回了无法验证的响应。") from exc


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    try:
        temporary.write_text(encoded + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_base_url(raw: str) -> str:
    parsed = urlparse(raw.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise AcceptanceError("真实付费验收只允许使用 HTTPS 地址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AcceptanceError("验收地址不能包含凭据、查询参数或片段。")
    hostname = parsed.hostname.casefold()
    if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".localhost"):
        raise AcceptanceError("真实付费验收禁止连接本机地址。")
    return raw.rstrip("/")


def _multipart_body(
    *,
    boundary: str,
    fields: Mapping[str, str],
    file_name: str,
    file_type: str,
    file_bytes: bytes,
) -> bytes:
    chunks: list[bytes] = []
    marker = boundary.encode("ascii")
    for name in sorted(fields):
        chunks.extend(
            [
                b"--" + marker + b"\r\n",
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                fields[name].encode("utf-8"),
                b"\r\n",
            ]
        )
    safe_name = Path(file_name).name.replace('"', "")
    chunks.extend(
        [
            b"--" + marker + b"\r\n",
            f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'.encode(),
            f"Content-Type: {file_type}\r\n\r\n".encode("ascii"),
            file_bytes,
            b"\r\n--" + marker + b"--\r\n",
        ]
    )
    return b"".join(chunks)


class PaidReleaseAcceptanceRunner:
    def __init__(
        self, config: RunnerConfig, *, transport: Transport | None = None
    ) -> None:
        self.config = config
        self.base_url = _validate_base_url(config.base_url)
        self.transport = transport or UrlLibTransport()
        self.token = ""
        self._last_send_attempts = 0
        self._starting_transaction_id = 0
        self._journal = self._load_or_initialize_journal()
        baseline = self._journal.get("baseline")
        if isinstance(baseline, dict):
            self._starting_transaction_id = int(
                baseline.get("starting_transaction_id", 0)
            )

    def _load_or_initialize_journal(self) -> dict[str, Any]:
        if self.config.journal_path.exists():
            loaded = json.loads(self.config.journal_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or loaded.get("schema_version") != 1:
                raise AcceptanceError("验收记录格式无效。")
            if loaded.get("target_origin") != self.base_url:
                raise AcceptanceError("现有验收记录属于另一个服务器。")
            if loaded.get("expected_release_version") != self.config.expected_version:
                raise AcceptanceError("现有验收记录属于另一个发布版本。")
            expected_scope = (
                "paid_execution" if self.config.execute_paid else "plan_only"
            )
            if loaded.get("acceptance_scope") != expected_scope:
                raise AcceptanceError(
                    "计划检查与真实付费执行必须使用不同的验收记录文件。"
                )
            return loaded
        journal: dict[str, Any] = {
            "schema_version": 1,
            "run_id": uuid.uuid4().hex,
            "target_origin": self.base_url,
            "expected_release_version": self.config.expected_version,
            "mode": "execute_paid" if self.config.execute_paid else "plan_only",
            "started_at": _now(),
            "updated_at": _now(),
            # A new journal is not accepted until all plan gates pass.
            "status": "failed",
            "acceptance_scope": "paid_execution"
            if self.config.execute_paid
            else "plan_only",
            "paid_execution_performed": False,
            "budget_credits": str(self.config.budget),
            "operations": {},
            "capabilities": {},
        }
        _atomic_json(self.config.journal_path, journal)
        return journal

    def _save(self) -> None:
        self._journal["updated_at"] = _now()
        _atomic_json(self.config.journal_path, self._journal)

    def _headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["X-Customer-Token"] = self.token
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _send(
        self,
        method: str,
        path_or_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        mutation: bool = False,
    ) -> HttpResponse:
        url = (
            path_or_url
            if path_or_url.startswith("https://")
            else urljoin(self.base_url + "/", path_or_url.lstrip("/"))
        )
        original_headers = dict(headers or {})
        last_error: Exception | None = None
        for attempt in range(2):
            self._last_send_attempts = attempt + 1
            try:
                response = self.transport.send(
                    method,
                    url,
                    headers=original_headers,
                    body=body,
                    timeout=self.config.request_timeout_seconds,
                )
            except (OSError, TimeoutError, URLError) as exc:
                last_error = exc
                if attempt == 0:
                    continue
                if mutation:
                    raise OutcomeUnknown(
                        "付费操作连接中断，结果无法确认；禁止自动重复提交。"
                    ) from exc
                raise AcceptanceError("只读检查连接失败。") from exc
            if response.status_code in {502, 503} and attempt == 0:
                continue
            if response.status_code == 409:
                raise OutcomeUnknown(
                    "服务器报告操作仍在处理或结果未知；禁止更换请求标识。"
                )
            if response.status_code in {502, 503}:
                if mutation:
                    raise OutcomeUnknown("付费操作结果无法确认；禁止自动重复提交。")
                raise AcceptanceError("服务暂时不可用。")
            if not 200 <= response.status_code < 300:
                raise AcceptanceError(
                    f"服务拒绝验收请求（HTTP {response.status_code}）。"
                )
            return response
        raise AcceptanceError("请求未完成。") from last_error

    def _get_json(self, path: str) -> Any:
        return _safe_json(self._send("GET", path, headers=self._headers()))

    def _post_json(
        self,
        name: str,
        path: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> Any:
        body = _canonical_json(payload)
        operation = self._journal["operations"].get(name)
        body_hash = _sha256(body)
        if operation:
            if (
                operation.get("idempotency_key_hash")
                != _sha256(idempotency_key.encode())
                or operation.get("local_request_hash") != body_hash
            ):
                raise AcceptanceError("恢复记录与本次操作不一致。")
            if operation.get("state") in {"completed", "outcome_unknown", "submitted"}:
                raise OutcomeUnknown("该付费步骤已有提交记录，runner 不会再次提交。")
        self._journal["operations"][name] = {
            "state": "prepared",
            "idempotency_key_hash": _sha256(idempotency_key.encode()),
            "local_request_hash": body_hash,
            "request_hash": _sha256(path.encode() + b"\0" + body),
            "attempts": 0,
        }
        self._save()
        self._journal["operations"][name].update({"state": "submitted", "attempts": 1})
        self._save()
        try:
            response = self._send(
                "POST",
                path,
                headers={
                    **self._headers(idempotency_key=idempotency_key),
                    "Content-Type": "application/json",
                },
                body=body,
                mutation=True,
            )
        except OutcomeUnknown:
            self._journal["operations"][name]["state"] = "outcome_unknown"
            self._journal["operations"][name]["attempts"] = max(
                1, self._last_send_attempts
            )
            self._save()
            raise
        self._journal["operations"][name].update(
            {
                "state": "completed",
                "attempts": self._last_send_attempts,
                "response_hash": _sha256(response.body),
            }
        )
        self._save()
        return _safe_json(response)

    def _upload(
        self,
        name: str,
        path: str,
        *,
        fields: Mapping[str, str],
        media_path: Path,
        idempotency_key: str,
    ) -> Any:
        file_bytes = media_path.read_bytes()
        if not file_bytes or len(file_bytes) >= 5 * 1024 * 1024:
            raise AcceptanceError("验收媒体必须非空且小于 5MB。")
        content_hash = _sha256(file_bytes)
        fingerprint_source = "\0".join(
            [
                fields.get("task_id") or fields.get("batch_id") or "",
                fields["object_key"],
                fields["media_type"],
                content_hash,
            ]
        ).encode()
        operation_fingerprint = _sha256(fingerprint_source)
        boundary = "vi-acceptance-" + _sha256(idempotency_key.encode())[:24]
        body = _multipart_body(
            boundary=boundary,
            fields=fields,
            file_name=media_path.name,
            file_type=fields["media_type"],
            file_bytes=file_bytes,
        )
        operation = self._journal["operations"].get(name)
        body_hash = _sha256(body)
        if operation:
            if (
                operation.get("idempotency_key_hash")
                != _sha256(idempotency_key.encode())
                or operation.get("local_request_hash") != body_hash
            ):
                raise AcceptanceError("恢复记录与本次上传不一致。")
            if operation.get("state") in {
                "completed",
                "outcome_unknown",
                "submitted",
            }:
                raise OutcomeUnknown("该上传步骤已有提交记录，runner 不会再次上传。")
        self._journal["operations"][name] = {
            "state": "prepared",
            "idempotency_key_hash": _sha256(idempotency_key.encode()),
            "local_request_hash": body_hash,
            "request_hash": _sha256(
                path.encode() + b"\0" + operation_fingerprint.encode("ascii")
            ),
            "media_sha256": content_hash,
            "attempts": 0,
        }
        self._save()
        self._journal["operations"][name].update({"state": "submitted", "attempts": 1})
        self._save()
        try:
            response = self._send(
                "POST",
                path,
                headers={
                    **self._headers(idempotency_key=idempotency_key),
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "X-Content-SHA256": content_hash,
                    "X-Operation-Fingerprint": operation_fingerprint,
                },
                body=body,
                mutation=True,
            )
        except OutcomeUnknown:
            self._journal["operations"][name].update(
                {
                    "state": "outcome_unknown",
                    "attempts": max(1, self._last_send_attempts),
                }
            )
            self._save()
            raise
        self._journal["operations"][name].update(
            {
                "state": "completed",
                "attempts": self._last_send_attempts,
                "response_hash": _sha256(response.body),
            }
        )
        self._save()
        return _safe_json(response)

    def _login_and_balance(self) -> Decimal:
        activation_code = os.getenv(ACTIVATION_CODE_ENV, "").strip()
        if not activation_code:
            raise AcceptanceError(
                f"激活码只能通过环境变量 {ACTIVATION_CODE_ENV} 提供。"
            )
        response = self._send(
            "POST",
            "/api/v1/auth/customer-login",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            body=_canonical_json({"code": activation_code}),
        )
        login = _safe_json(response)
        self.token = str(login.get("token") or "")
        if not self.token:
            raise AcceptanceError("测试激活码登录未返回会话。")
        balance_payload = self._get_json("/api/v1/credits")
        balance = Decimal(str(balance_payload["balance"]))
        if balance < 0 or balance > MAX_ACCEPTANCE_BUDGET:
            raise AcceptanceError("专用验收账号起始余额必须在 0 到 5.00 积分之间。")
        self._journal["initial_balance_credits"] = str(balance)
        self._save()
        return balance

    def _validate_server_baseline(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise AcceptanceError("服务器验收起点格式无效。")
        expected = {
            "schema_version": 1,
            "run_id": self._journal["run_id"],
            "target_origin": self.base_url,
            "release_version": self._journal["release_version"],
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise AcceptanceError("服务器验收起点与本次运行不一致。")
        try:
            created_at = datetime.fromisoformat(str(payload["created_at"]))
            operation_rowid = int(payload["starting_operation_rowid"])
            transaction_id = int(payload["starting_transaction_id"])
            initial = Decimal(str(payload["initial_balance_credits"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise AcceptanceError("服务器验收起点字段无效。") from exc
        unsigned = {
            key: value for key, value in payload.items() if key != "baseline_sha256"
        }
        if (
            set(payload)
            != {
                "schema_version",
                "run_id",
                "target_origin",
                "release_version",
                "created_at",
                "starting_operation_rowid",
                "starting_transaction_id",
                "initial_balance_credits",
                "baseline_sha256",
            }
            or created_at.tzinfo is None
            or operation_rowid <= 0
            or transaction_id < 0
            or not initial.is_finite()
            or initial < 0
            or initial > MAX_ACCEPTANCE_BUDGET
            or payload.get("baseline_sha256") != _sha256(_canonical_json(unsigned))
        ):
            raise AcceptanceError("服务器验收起点完整性无效。")
        return dict(payload)

    def _start_server_baseline(self) -> Decimal:
        run_id = str(self._journal["run_id"])
        saved = self._journal.get("baseline")
        if isinstance(saved, dict):
            payload = self._get_json(
                f"/api/v1/provider/release-acceptance/baseline/{run_id}"
            )
        else:
            request_payload = {
                "schema_version": 1,
                "run_id": run_id,
                "target_origin": self.base_url,
                "release_version": self._journal["release_version"],
            }
            try:
                response = self._send(
                    "POST",
                    "/api/v1/provider/release-acceptance/start",
                    headers={
                        **self._headers(idempotency_key=f"acceptance-start-{run_id}"),
                        "Content-Type": "application/json",
                    },
                    body=_canonical_json(request_payload),
                    mutation=True,
                )
                payload = _safe_json(response)
            except OutcomeUnknown:
                payload = self._get_json(
                    f"/api/v1/provider/release-acceptance/baseline/{run_id}"
                )
        baseline = self._validate_server_baseline(payload)
        if saved is not None and baseline != saved:
            raise AcceptanceError("服务器验收起点与本地已保存记录不一致。")
        self._journal["baseline"] = baseline
        self._journal["initial_balance_credits"] = str(
            baseline["initial_balance_credits"]
        )
        self._starting_transaction_id = int(baseline["starting_transaction_id"])
        self._save()
        return Decimal(str(baseline["initial_balance_credits"]))

    def _check_release_version(self) -> None:
        if not self.config.expected_version.strip():
            raise AcceptanceError("必须指定要验收的准确发布版本。")
        health = self._get_json("/health")
        actual = str(health.get("release_version") or "")
        if actual != self.config.expected_version:
            raise AcceptanceError(
                f"服务器版本不一致：期望 {self.config.expected_version}，实际 {actual or '未报告'}。"
            )
        self._journal["release_version"] = actual
        self._save()

    def _build_plan(
        self, balance: Decimal, *, main_seconds: Decimal = MAIN_FIXTURE_SECONDS
    ) -> dict[str, Any]:
        acceptance_cap = self._get_json(
            "/api/v1/provider/release-acceptance/capabilities"
        )
        copy_cap = self._get_json("/api/v1/copywriting/capabilities")
        asr_cap = self._get_json("/api/v1/provider/asr/capabilities")
        editor_cap = self._get_json("/api/v1/provider/video-editor/capabilities")
        avatar_cap = self._get_json("/api/v1/provider/avatar/capabilities")
        assets = self._get_json("/api/v1/provider/avatar/assets")
        required_caps = [
            (
                "media_evidence",
                bool(acceptance_cap.get("enabled"))
                and acceptance_cap.get("status") == "ready"
                and not acceptance_cap.get("missing_configuration"),
            ),
            (
                "copywriting",
                bool(copy_cap.get("enabled"))
                and copy_cap.get("mode") == "production"
                and not copy_cap.get("missing_configuration"),
            ),
            (
                "asr",
                bool(asr_cap.get("enabled"))
                and bool(asr_cap.get("live_ready"))
                and bool(asr_cap.get("billing_authorized"))
                and asr_cap.get("provider_mode") == "aliyun"
                and asr_cap.get("provider_name") == "aliyun_fun_asr"
                and not asr_cap.get("is_mock", False)
                and not asr_cap.get("missing_configuration"),
            ),
            (
                "video_editor",
                bool(editor_cap.get("enabled"))
                and bool(editor_cap.get("live_ready"))
                and editor_cap.get("provider_mode") == "aliyun"
                and editor_cap.get("provider_name") == "aliyun_cloud_editor"
                and not editor_cap.get("is_mock", False)
                and not editor_cap.get("missing_configuration"),
            ),
            (
                "avatar",
                bool(avatar_cap.get("enabled"))
                and avatar_cap.get("provider_name") == "shuying_legacy_cloud"
                and not avatar_cap.get("missing_configuration"),
            ),
        ]
        unavailable = [name for name, ready in required_caps if not ready]
        if unavailable:
            raise AcceptanceError("正式能力未就绪：" + "、".join(unavailable))
        asset_index = {
            str(item.get("asset_id")): item for item in assets if isinstance(item, dict)
        }
        expected_assets = {AVATAR_ID: "avatar", VOICE_ID: "voice"}
        for asset_id, kind in expected_assets.items():
            item = asset_index.get(asset_id)
            if (
                not item
                or item.get("kind") != kind
                or item.get("status") != "ready"
                or not item.get("authorized")
                or item.get("shared") is not True
            ):
                raise AcceptanceError("大树1的共享形象或声音未就绪。")

        ai_credits = _money(
            Decimal(str(copy_cap.get("minimum_charge_credits", "0.01")))
        )
        asr_unit_price = Decimal(str(asr_cap["unit_price_cny_per_second"]))
        asr_credits = cny_to_credits(asr_unit_price * main_seconds)
        editor_quote = create_cost_quote(
            input_duration_seconds=main_seconds,
            output_duration_seconds=main_seconds + OPENING_SECONDS,
            output_profile="720p",
            price_version=str(editor_cap["price_version"]),
            ttl_seconds=int(editor_cap["quote_ttl_seconds"]),
        )
        editor_credits = cny_to_credits(editor_quote.estimated_total)
        avatar_quote = self._get_json(
            f"/api/v1/provider/avatar/quote?{urlencode({'characters': len(AVATAR_SCRIPT), 'speech_rate': 1.0})}"
        )
        avatar_reservation_seconds = int(avatar_quote["reservation_seconds"])
        avatar_estimated_billed_seconds = max(
            avatar_reservation_seconds, AVATAR_ACCEPTANCE_MAX_BILLED_SECONDS
        )
        avatar_price = Decimal(str(avatar_quote["price_per_minute_cny"]))
        avatar_credits = max(
            _money(avatar_quote["reservation_credits"]),
            credits_for_seconds(avatar_price, avatar_estimated_billed_seconds),
        )
        estimates = {
            "copywriting": ai_credits,
            "asr": asr_credits,
            "video_editor": editor_credits,
            "avatar": avatar_credits,
        }
        total = sum(estimates.values(), Decimal("0"))
        if (
            total > balance
            or total > self.config.budget
            or total > MAX_ACCEPTANCE_BUDGET
        ):
            raise AcceptanceError("预计总费用超过验收账号余额或 5.00 积分硬上限。")
        plan = {
            "estimated_credits": {
                name: str(value) for name, value in estimates.items()
            },
            "estimated_total_credits": str(total),
            "fixture": {
                "main_seconds": str(main_seconds),
                "opening_seconds": str(OPENING_SECONDS),
            },
            "avatar": {
                "provider": "shuying_legacy_cloud",
                "avatar_id": AVATAR_ID,
                "voice_id": VOICE_ID,
                "characters": len(AVATAR_SCRIPT),
                "estimated_billed_seconds": avatar_estimated_billed_seconds,
                "price_per_minute_cny": str(avatar_quote["price_per_minute_cny"]),
            },
            "video_editor_quote": editor_quote.model_dump(mode="json"),
        }
        self._journal["plan"] = plan
        self._journal["capabilities"] = {
            name: {"status": "passed", "estimated_credits": str(estimates[name])}
            for name in estimates
        }
        self._save()
        return plan

    def run(self) -> dict[str, Any]:
        try:
            if self.config.budget <= 0 or self.config.budget > MAX_ACCEPTANCE_BUDGET:
                raise AcceptanceError("验收预算必须大于 0 且不超过 5.00 积分。")
            if (
                self.config.execute_paid
                and os.getenv(EXECUTION_ACK_ENV, "") != EXECUTION_ACK_VALUE
            ):
                raise AcceptanceError(
                    f"真实付费执行还需要环境变量 {EXECUTION_ACK_ENV} 的明确授权。"
                )
            self._check_release_version()
            balance = self._login_and_balance()
            if self.config.execute_paid:
                balance = self._start_server_baseline()
            plan = self._build_plan(balance)
            if not self.config.execute_paid:
                self._journal["status"] = "passed"
                self._journal["finished_at"] = _now()
                self._save()
                return self._journal
            self._execute(plan)
            final_credits = self._get_json("/api/v1/credits")
            final_balance = Decimal(str(final_credits["balance"]))
            spent = balance - final_balance
            if spent < 0 or spent > self.config.budget or spent > MAX_ACCEPTANCE_BUDGET:
                raise AcceptanceError("实际扣费超过 5.00 积分硬上限，验收失败。")
            self._journal["final_balance_credits"] = str(final_balance)
            self._journal["actual_spent_credits"] = str(spent)
            self._validate_new_transactions(final_credits.get("transactions", []))
            _verify_operation_evidence(self._journal, str(self._journal["run_id"]))
            _verify_capability_evidence(self._journal)
            _verify_transaction_evidence(
                self._journal,
                str(self._journal["run_id"]),
                balance,
                final_balance,
                spent,
            )
            self._journal["evidence_manifest_sha256"] = _evidence_manifest_sha256(
                self._journal
            )
            self._journal["paid_execution_performed"] = True
            self._journal["server_proof"] = self._attest_server_proof()
            self._journal["status"] = "passed"
        except OutcomeUnknown as exc:
            self._journal["status"] = "outcome_unknown"
            self._journal["message"] = str(exc)
        except (AcceptanceError, KeyError, ValueError, TypeError) as exc:
            self._journal["status"] = "failed"
            self._journal["message"] = str(exc)[:300]
        self._journal["finished_at"] = _now()
        self._save()
        return self._journal

    def _attest_server_proof(self) -> dict[str, Any]:
        run_id = str(self._journal["run_id"])
        capabilities = self._journal["capabilities"]
        payload = {
            "schema_version": 1,
            "run_id": run_id,
            "target_origin": self.base_url,
            "release_version": self._journal["release_version"],
            "evidence_manifest_sha256": self._journal["evidence_manifest_sha256"],
            "provider": self._journal["plan"]["avatar"]["provider"],
            "avatar_id": self._journal["plan"]["avatar"]["avatar_id"],
            "voice_id": self._journal["plan"]["avatar"]["voice_id"],
            "baseline": self._journal["baseline"],
            "initial_balance_credits": self._journal["initial_balance_credits"],
            "final_balance_credits": self._journal["final_balance_credits"],
            "actual_spent_credits": self._journal["actual_spent_credits"],
            "operations": self._journal["operations"],
            "capabilities": {
                "copywriting": {
                    key: capabilities["copywriting"][key]
                    for key in ("status", "charged_credits", "result_sha256")
                },
                "asr": {
                    key: capabilities["asr"][key]
                    for key in ("status", "result_sha256", "provider_job_id_hash")
                },
                "video_editor": {
                    key: capabilities["video_editor"][key]
                    for key in (
                        "status",
                        "result_sha256",
                        "result_duration_seconds",
                        "opening_seconds",
                    )
                },
                "avatar": {
                    key: capabilities["avatar"][key]
                    for key in (
                        "status",
                        "result_sha256",
                        "duration_seconds",
                        "billed_seconds",
                        "charged_credits",
                    )
                },
            },
            "transactions": self._journal["new_transactions"],
        }
        body = _canonical_json(payload)
        path = "/api/v1/provider/release-acceptance/attest"
        try:
            response = self._send(
                "POST",
                path,
                headers={
                    **self._headers(idempotency_key=f"acceptance-attest-{run_id}"),
                    "Content-Type": "application/json",
                },
                body=body,
                mutation=True,
            )
            proof = _safe_json(response)
        except OutcomeUnknown:
            proof = self._get_json(
                "/api/v1/provider/release-acceptance/proof/"
                f"{run_id}?{urlencode({'evidence_manifest_sha256': self._journal['evidence_manifest_sha256']})}"
            )
        _verify_server_proof(
            proof,
            report=self._journal,
            expected_origin=self.base_url,
            expected_version=str(self._journal["release_version"]),
        )
        return proof

    def _validate_new_transactions(self, transactions: Any) -> None:
        if not isinstance(transactions, list):
            raise AcceptanceError("积分流水格式无效。")
        new_rows = [
            item
            for item in transactions
            if isinstance(item, dict)
            and int(item.get("id", -1)) > self._starting_transaction_id
        ]
        run_id = str(self._journal["run_id"])
        expected_refs = {
            ("copywriting", f"accept-copy-{run_id[:24]}"),
            (
                "transcription",
                f"transcript-{_sha256((run_id + 'asr').encode())[:10]}",
            ),
            (
                "video_editor",
                f"edit-batch-{_sha256((run_id + 'editor').encode())[:12]}",
            ),
            ("avatar_reserve", f"accept-avatar-{run_id[:24]}"),
        }
        observed = {
            (str(item.get("ref_type") or ""), str(item.get("ref_id") or ""))
            for item in new_rows
            if Decimal(str(item.get("amount", "0"))) < 0
        }
        missing = expected_refs - observed
        if missing:
            raise AcceptanceError("四项真实能力的扣费流水不完整。")
        self._journal["new_transactions"] = [
            {
                "id": int(item["id"]),
                "amount": str(item.get("amount", "0")),
                "balance_after": str(item.get("balance_after", "0")),
                "ref_type": str(item.get("ref_type") or ""),
                "ref_id": str(item.get("ref_id") or ""),
                "created_at": str(item.get("created_at") or ""),
            }
            for item in sorted(new_rows, key=lambda row: int(row["id"]))
        ]

    def _execute(self, plan: Mapping[str, Any]) -> None:
        main_path, opening_path = self._ensure_fixtures()
        main_media = _probe_media(main_path)
        opening_media = _probe_media(opening_path)
        main_seconds = Decimal(str(main_media["duration_seconds"]))
        opening_seconds = Decimal(str(opening_media["duration_seconds"]))
        if not Decimal("0.5") <= main_seconds <= Decimal("6"):
            raise AcceptanceError("主验收视频必须在 0.5 到 6 秒之间。")
        if (
            not main_media["has_audio"]
            or not opening_media["has_audio"]
            or {int(main_media["width"]), int(main_media["height"])} != {720, 1280}
            or {int(opening_media["width"]), int(opening_media["height"])}
            != {720, 1280}
        ):
            raise AcceptanceError("验收素材必须是 720x1280 且包含音频流。")
        if abs(opening_seconds - OPENING_SECONDS) > Decimal("0.03"):
            raise AcceptanceError("片头素材必须精确为 1.4 秒。")
        if abs(main_seconds - Decimal(str(plan["fixture"]["main_seconds"]))) > Decimal(
            "0.03"
        ):
            plan = self._build_plan(
                Decimal(str(self._journal["initial_balance_credits"])),
                main_seconds=main_seconds,
            )
        self._execute_copywriting()
        self._execute_asr(main_path, main_seconds)
        self._execute_video_editor(main_path, opening_path, main_seconds, plan)
        self._execute_avatar()

    def _execute_copywriting(self) -> None:
        payload = {
            "content_brief": "交付验收",
            "platform": "douyin",
            "target_audience": "",
            "selling_points": "",
            "call_to_action": "",
            "style_prompt": "",
            "target_length": 50,
            "tone": "professional",
            "variant_count": 1,
        }
        result = self._post_json(
            "copywriting_generate",
            "/api/v1/provider/copywriting/generate",
            payload,
            idempotency_key=f"accept-copy-{self._journal['run_id'][:24]}",
        )
        generated = result.get("result")
        usage = result.get("token_usage")
        generated_nonempty = (
            any(str(item).strip() for item in generated)
            if isinstance(generated, list)
            else bool(generated)
        )
        if not generated_nonempty or not isinstance(usage, dict):
            raise AcceptanceError("AI 文案没有返回可验证的结果或 Token 用量。")
        try:
            usage_values = [int(value) for value in usage.values()]
        except (TypeError, ValueError) as exc:
            raise AcceptanceError("AI 文案 Token 用量无效。") from exc
        if (
            not usage_values
            or any(value < 0 for value in usage_values)
            or sum(usage_values) <= 0
        ):
            raise AcceptanceError("AI 文案 Token 用量无效。")
        result_payload = _canonical_json(generated)
        self._journal["capabilities"]["copywriting"].update(
            {
                "status": "passed" if not result.get("is_mock") else "failed",
                "charged_credits": str(result.get("charged_credits", "0")),
                "result_sha256": _sha256(result_payload),
            }
        )
        if result.get("is_mock"):
            raise AcceptanceError("AI 文案返回了沙箱结果。")
        self._save()

    def _execute_asr(self, media_path: Path, duration: Decimal) -> None:
        task_id = (
            f"transcript-{_sha256((self._journal['run_id'] + 'asr').encode())[:10]}"
        )
        self._post_json(
            "asr_authorize",
            "/api/v1/provider/asr/authorize",
            {"task_id": task_id, "duration_seconds": float(duration)},
            idempotency_key=f"asr-charge-{task_id}",
        )
        object_key = f"asr-input/{task_id}/acceptance.mp4"
        asset = self._upload(
            "asr_upload",
            "/api/v1/provider/asr/upload",
            fields={
                "task_id": task_id,
                "object_key": object_key,
                "media_type": "video/mp4",
            },
            media_path=media_path,
            idempotency_key=f"asr-upload-{task_id}",
        )
        snapshot = self._post_json(
            "asr_submit",
            "/api/v1/provider/asr/submit",
            {"task_id": task_id, "asset": asset, "language": "zh"},
            idempotency_key=f"asr-submit-{task_id}",
        )
        snapshot = self._poll_provider_job(
            "/api/v1/provider/asr/jobs/{job_id}", snapshot
        )
        transcript = self._get_json(
            f"/api/v1/provider/asr/jobs/{snapshot['provider_job_id']}/result"
        )
        if (
            transcript.get("is_mock")
            or transcript.get("provider_name") != "aliyun_fun_asr"
            or not str(transcript.get("transcript") or "").strip()
            or not isinstance(transcript.get("segments"), list)
            or not transcript.get("segments")
        ):
            raise AcceptanceError("云转写没有返回正式、非空的阿里云结果。")
        evidence = _canonical_json(
            {
                "provider_name": transcript.get("provider_name"),
                "transcript": transcript.get("transcript"),
                "segments": transcript.get("segments", []),
                "spoken_ranges": transcript.get("spoken_ranges", []),
                "duration_seconds": transcript.get("duration_seconds"),
                "language": transcript.get("language", ""),
            }
        )
        self._journal["capabilities"]["asr"].update(
            {
                "status": "passed",
                "provider_job_id_hash": _sha256(
                    str(snapshot["provider_job_id"]).encode()
                ),
                "result_sha256": _sha256(evidence),
            }
        )
        self._save()

    def _execute_video_editor(
        self,
        main_path: Path,
        opening_path: Path,
        duration: Decimal,
        plan: Mapping[str, Any],
    ) -> None:
        batch_id = (
            f"edit-batch-{_sha256((self._journal['run_id'] + 'editor').encode())[:12]}"
        )
        quote = plan["video_editor_quote"]
        self._post_json(
            "video_authorize",
            "/api/v1/provider/video-editor/authorize",
            {
                "batch_id": batch_id,
                "quote": quote,
                "max_cost_cny": quote["estimated_max"],
            },
            idempotency_key=f"video-charge-{batch_id}",
        )
        main_key = f"video-editor-input/{batch_id}/input/main.mp4"
        main_asset = self._upload(
            "video_main_upload",
            "/api/v1/provider/video-editor/upload",
            fields={
                "batch_id": batch_id,
                "object_key": main_key,
                "media_type": "video/mp4",
            },
            media_path=main_path,
            idempotency_key=f"video-upload-{_sha256(main_key.encode())[:32]}",
        )
        opening_key = f"video-editor-input/{batch_id}/opening/opening.mp4"
        opening_asset = self._upload(
            "video_opening_upload",
            "/api/v1/provider/video-editor/upload",
            fields={
                "batch_id": batch_id,
                "object_key": opening_key,
                "media_type": "video/mp4",
            },
            media_path=opening_path,
            idempotency_key=f"video-upload-{_sha256(opening_key.encode())[:32]}",
        )
        asr_snapshot = self._post_json(
            "video_asr_submit",
            "/api/v1/provider/video-editor/asr/submit",
            {"batch_id": batch_id, "asset": main_asset, "language_hints": ["zh"]},
            idempotency_key=f"video-asr-{batch_id}",
        )
        asr_snapshot = self._poll_provider_job(
            "/api/v1/provider/video-editor/asr/jobs/{job_id}", asr_snapshot
        )
        transcript = self._get_json(
            f"/api/v1/provider/video-editor/asr/jobs/{asr_snapshot['provider_job_id']}/result"
        )
        if (
            transcript.get("is_mock")
            or transcript.get("provider_name") != "aliyun_fun_asr"
            or not str(transcript.get("transcript") or "").strip()
            or not transcript.get("segments")
        ):
            raise AcceptanceError("云剪辑转写没有返回正式、非空的阿里云结果。")
        edit_plan = self._post_json(
            "video_plan",
            "/api/v1/provider/video-editor/plan",
            {
                "batch_id": batch_id,
                "transcript": transcript.get("transcript", ""),
                "spoken_ranges": transcript.get("spoken_ranges", []),
                "duration_seconds": float(duration),
                "segments": transcript.get("segments", []),
            },
            idempotency_key=f"video-plan-{batch_id}",
        )
        if edit_plan.get("is_mock"):
            raise AcceptanceError("云剪辑方案返回了沙箱结果。")
        render_request = {
            "input_asset": main_asset,
            "output_object_key": f"video-editor-output/{batch_id}/output/720p.mp4",
            "output_profile": "720p",
            "edit_plan": edit_plan,
            "review_confirmed": True,
            "title": "交付验收",
            "opening_asset": opening_asset,
            "opening_duration_seconds": float(OPENING_SECONDS),
            "idempotency_key": f"video-render-{batch_id}",
        }
        render_snapshot = self._post_json(
            "video_render_submit",
            "/api/v1/provider/video-editor/render/submit",
            {"batch_id": batch_id, "render_request": render_request},
            idempotency_key=f"video-render-{batch_id}",
        )
        render_snapshot = self._poll_provider_job(
            "/api/v1/provider/video-editor/render/jobs/{job_id}", render_snapshot
        )
        if render_snapshot.get("is_mock") or not render_snapshot.get("can_publish"):
            raise AcceptanceError("云剪辑成片不是可发布的正式结果。")
        # The status may expose a provider URI; the signing endpoint accepts only
        # the deterministic customer-owned object key.
        object_key = str(render_request["output_object_key"])
        url_payload = self._get_json(
            f"/api/v1/provider/video-editor/outputs/{batch_id}/url?{urlencode({'object_key': object_key})}"
        )
        output_url = str(url_payload.get("url") or "")
        if urlparse(output_url).scheme != "https":
            raise AcceptanceError("云剪辑成片地址不是 HTTPS。")
        output = self._send("GET", output_url, headers={"Accept": "video/mp4"}).body
        output_path = self._temporary_media("editor-result.mp4", output)
        try:
            media = _probe_media(output_path)
            output_seconds = float(media["duration_seconds"])
            if not media["has_video"] or not media["has_audio"]:
                raise AcceptanceError("云剪辑成片缺少视频流或音频流。")
            if {int(media["width"]), int(media["height"])} != {720, 1280}:
                raise AcceptanceError("云剪辑成片不是预期的 720p 竖屏分辨率。")
        finally:
            output_path.unlink(missing_ok=True)
        self._journal["capabilities"]["video_editor"].update(
            {
                "status": "passed",
                "result_sha256": _sha256(output),
                "result_duration_seconds": round(output_seconds, 3),
                "opening_seconds": str(OPENING_SECONDS),
            }
        )
        self._save()

    def _execute_avatar(self) -> None:
        operation_key = f"accept-avatar-{self._journal['run_id'][:24]}"
        body = {
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
                "idempotency_key": operation_key,
            }
        }
        snapshot = self._post_json(
            "avatar_submit",
            "/api/v1/provider/avatar/submit",
            body,
            idempotency_key=f"avatar-submit-{operation_key}",
        )
        if str(snapshot.get("job_id", "")).startswith("voice-tts:"):
            pending_job_id = str(snapshot["job_id"])
            # Read-only checks come first.  The provider has no separate TTS
            # result endpoint, so one fixed resume operation is the only safe
            # transition to video submission.
            for _ in range(3):
                checked = self._get_json(
                    f"/api/v1/provider/avatar/jobs/find/{operation_key}"
                )
                if isinstance(checked, dict):
                    snapshot = checked
                if not str(snapshot.get("job_id", "")).startswith("voice-tts:"):
                    break
                time.sleep(self.config.poll_interval_seconds)
            if str(snapshot.get("job_id", "")).startswith("voice-tts:"):
                attempt_id = f"{self._journal['run_id'][:8]}-0"
                snapshot = self._post_json(
                    "avatar_resume",
                    "/api/v1/provider/avatar/resume",
                    {
                        **body,
                        "pending_job_id": pending_job_id,
                        "attempt_id": attempt_id,
                    },
                    idempotency_key=f"avatar-resume-{operation_key}-{attempt_id}",
                )
                if str(snapshot.get("job_id", "")).startswith("voice-tts:"):
                    raise OutcomeUnknown(
                        "数字人声音仍在处理中；验收只允许一次固定恢复请求。"
                    )
        snapshot = self._poll_avatar(snapshot)
        job_id = str(snapshot["job_id"])
        result = self._send(
            "GET",
            f"/api/v1/provider/avatar/jobs/{job_id}/result",
            headers=self._headers(),
        ).body
        result_path = self._temporary_media("avatar-result.mp4", result)
        try:
            media = _probe_media(result_path)
            duration = float(media["duration_seconds"])
            if not media["has_video"] or not media["has_audio"]:
                raise AcceptanceError("数字人成片缺少视频流或音频流。")
        finally:
            result_path.unlink(missing_ok=True)
        billed_seconds = math.ceil(duration)
        price = Decimal(str(self._journal["plan"]["avatar"]["price_per_minute_cny"]))
        expected = cny_to_credits(price * Decimal(billed_seconds) / Decimal(60))
        actual_seconds = int(snapshot.get("estimated_seconds") or 0)
        actual_credits = _money(snapshot.get("estimated_cost_cny") or 0)
        if actual_seconds != billed_seconds or actual_credits != expected:
            raise AcceptanceError("数字人成片按整秒结算与实际时长不一致。")
        self._journal["capabilities"]["avatar"].update(
            {
                "status": "passed",
                "result_sha256": _sha256(result),
                "duration_seconds": round(duration, 3),
                "billed_seconds": billed_seconds,
                "charged_credits": str(actual_credits),
            }
        )
        self._save()

    def _deadline(self) -> float:
        return time.monotonic() + self.config.poll_timeout_seconds

    def _poll_provider_job(
        self, template: str, snapshot: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        deadline = self._deadline()
        while True:
            status = str(snapshot.get("status", "")).casefold()
            if status == "succeeded":
                if snapshot.get("is_mock"):
                    raise AcceptanceError("供应商任务返回了沙箱结果。")
                return snapshot
            if status in {"failed", "cancelled"}:
                raise AcceptanceError("供应商任务明确失败。")
            if status in {"outcome_unknown", "unknown"}:
                raise OutcomeUnknown("供应商任务结果未知。")
            if time.monotonic() >= deadline:
                raise OutcomeUnknown("供应商任务在验收窗口内未完成。")
            time.sleep(self.config.poll_interval_seconds)
            job_id = str(snapshot.get("provider_job_id") or "")
            snapshot = self._get_json(template.format(job_id=job_id))

    def _poll_avatar(self, snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        deadline = self._deadline()
        while True:
            status = str(snapshot.get("status", "")).casefold()
            if status == "succeeded":
                return snapshot
            if status in {"failed", "cancelled"}:
                raise AcceptanceError("数字人任务明确失败。")
            if status in {"outcome_unknown", "unknown"}:
                raise OutcomeUnknown("数字人任务结果未知。")
            if time.monotonic() >= deadline:
                raise OutcomeUnknown("数字人任务在验收窗口内未完成。")
            time.sleep(self.config.poll_interval_seconds)
            snapshot = self._get_json(
                f"/api/v1/provider/avatar/jobs/{snapshot['job_id']}"
            )

    def _ensure_fixtures(self) -> tuple[Path, Path]:
        directory = self.config.fixture_dir or (
            Path(tempfile.gettempdir()) / "videoinsight-paid-acceptance"
        )
        directory.mkdir(parents=True, exist_ok=True)
        main = directory / "main-zh-speech-v1.mp4"
        opening = directory / "opening-1.4s-silent-aac-v1.mp4"
        if not main.exists():
            _generate_fixture(
                main, seconds=float(MAIN_FIXTURE_SECONDS), with_audio=True
            )
        if not opening.exists():
            _generate_fixture(opening, seconds=float(OPENING_SECONDS), with_audio=False)
        return main, opening

    def _temporary_media(self, name: str, payload: bytes) -> Path:
        if not payload:
            raise AcceptanceError("供应商返回了空成片。")
        directory = self.config.fixture_dir or Path(tempfile.gettempdir())
        path = directory / f"{self._journal['run_id'][:12]}-{name}"
        path.write_bytes(payload)
        return path


def _generate_fixture(path: Path, *, seconds: float, with_audio: bool) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AcceptanceError("未找到 FFmpeg，无法生成最低成本验收素材。")
    speech_path = path.with_suffix(".speech.wav")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x6d3df5:s=720x1280:r=25:d={seconds}",
    ]
    if with_audio:
        _generate_chinese_speech(speech_path)
        command.extend(["-i", str(speech_path), "-shortest"])
    else:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=mono:sample_rate=16000",
                "-t",
                str(seconds),
            ]
        )
    command.extend(["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
    command.extend(["-c:a", "aac", "-b:a", "32k"])
    command.extend(["-movflags", "+faststart", str(path)])
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=90, check=False
        )
        if completed.returncode != 0 or not path.exists():
            raise AcceptanceError("FFmpeg 无法生成验收素材。")
    finally:
        speech_path.unlink(missing_ok=True)


def _generate_chinese_speech(path: Path) -> None:
    if os.name != "nt":
        raise AcceptanceError(
            "非 Windows 环境必须通过 --fixture-dir 提供真实中文语音素材。"
        )
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise AcceptanceError("未找到 Windows PowerShell，无法生成中文语音验收素材。")
    escaped_path = str(path.resolve()).replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() |
    Where-Object {{ $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'zh-*' }} |
    Select-Object -First 1
if ($null -eq $voice) {{ exit 3 }}
$synth.SelectVoice($voice.VoiceInfo.Name)
$synth.SetOutputToWaveFile('{escaped_path}')
$synth.Speak('这是交付验收测试')
$synth.Dispose()
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0 or not path.is_file() or path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        raise AcceptanceError(
            "系统没有可用的中文语音，必须通过 --fixture-dir 提供真实中文语音素材。"
        )


def _probe_duration(path: Path) -> float:
    return float(_probe_media(path)["duration_seconds"])


def _probe_media(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise AcceptanceError("未找到 ffprobe，无法验证真实计费秒数。")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
        duration = float(payload["format"]["duration"])
        streams = payload.get("streams") or []
        video = next(item for item in streams if item.get("codec_type") == "video")
        has_audio = any(item.get("codec_type") == "audio" for item in streams)
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
    except (
        KeyError,
        StopIteration,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise AcceptanceError("无法读取验收成片时长。") from exc
    if completed.returncode != 0 or not math.isfinite(duration) or duration <= 0:
        raise AcceptanceError("验收成片时长无效。")
    return {
        "duration_seconds": duration,
        "has_video": True,
        "has_audio": has_audio,
        "width": width,
        "height": height,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VideoInsight 最低成本真实付费发布验收"
    )
    parser.add_argument("--base-url", default=os.getenv(BASE_URL_ENV, ""))
    parser.add_argument(
        "--expected-version", default=os.getenv(EXPECTED_VERSION_ENV, "")
    )
    parser.add_argument(
        "--journal", type=Path, default=Path("build/paid-release-acceptance.json")
    )
    parser.add_argument("--verify-report", type=Path)
    parser.add_argument("--fixture-dir", type=Path)
    parser.add_argument("--budget", type=Decimal, default=MAX_ACCEPTANCE_BUDGET)
    parser.add_argument("--execute-paid", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument("--poll-timeout", type=float, default=900.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
    args = _parse_args(argv)
    if not args.base_url:
        print(
            f"请通过 --base-url 或 {BASE_URL_ENV} 指定正式 HTTPS 地址。",
            file=sys.stderr,
        )
        return 2
    try:
        if args.verify_report:
            verification = verify_report(
                args.verify_report.resolve(),
                expected_origin=_validate_base_url(args.base_url),
                expected_version=args.expected_version,
            )
            print(
                json.dumps(verification, ensure_ascii=False, indent=2, sort_keys=True)
            )
            return 0
        runner = PaidReleaseAcceptanceRunner(
            RunnerConfig(
                base_url=args.base_url,
                journal_path=args.journal.resolve(),
                expected_version=args.expected_version,
                execute_paid=args.execute_paid,
                budget=args.budget,
                fixture_dir=args.fixture_dir.resolve() if args.fixture_dir else None,
                poll_interval_seconds=max(0.1, args.poll_interval),
                poll_timeout_seconds=max(1.0, args.poll_timeout),
            )
        )
        result = runner.run()
    except (AcceptanceError, json.JSONDecodeError, OSError) as exc:
        print(f"验收未启动：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "passed" else 1


def verify_report(
    path: Path,
    *,
    expected_origin: str,
    expected_version: str,
    maximum_age: timedelta = timedelta(hours=24),
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Fail closed unless a recent paid report proves all four live paths."""

    if not expected_version.strip():
        raise AcceptanceError("报告门禁必须指定准确发布版本。")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceError("无法读取真实付费验收报告。") from exc
    required = {
        "schema_version": 1,
        "status": "passed",
        "acceptance_scope": "paid_execution",
        "paid_execution_performed": True,
        "target_origin": expected_origin,
        "release_version": expected_version,
    }
    for key, value in required.items():
        if report.get(key) != value:
            raise AcceptanceError(f"真实付费验收报告字段无效：{key}。")
    try:
        finished_at = datetime.fromisoformat(str(report["finished_at"]))
    except (KeyError, ValueError) as exc:
        raise AcceptanceError("真实付费验收报告缺少完成时间。") from exc
    if finished_at.tzinfo is None:
        raise AcceptanceError("真实付费验收报告时间没有时区。")
    age = datetime.now().astimezone() - finished_at.astimezone()
    if age < timedelta(minutes=-5) or age > maximum_age:
        raise AcceptanceError("真实付费验收报告已过期或时间异常。")
    run_id = str(report.get("run_id") or "")
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise AcceptanceError("真实付费验收报告运行编号无效。")
    _verify_baseline_evidence(
        report,
        run_id=run_id,
        expected_origin=expected_origin,
        expected_version=expected_version,
    )
    _verify_operation_evidence(report, run_id)
    _verify_capability_evidence(report)
    try:
        budget = Decimal(str(report["budget_credits"]))
        spent = Decimal(str(report["actual_spent_credits"]))
        initial = Decimal(str(report["initial_balance_credits"]))
        final = Decimal(str(report["final_balance_credits"]))
    except (KeyError, ValueError) as exc:
        raise AcceptanceError("真实付费验收报告缺少费用证据。") from exc
    if (
        budget <= 0
        or budget > MAX_ACCEPTANCE_BUDGET
        or initial < 0
        or initial > MAX_ACCEPTANCE_BUDGET
        or spent < 0
        or spent > budget
        or spent > initial
        or initial - final != spent
    ):
        raise AcceptanceError("真实付费验收报告超出费用硬门禁。")
    _verify_transaction_evidence(report, run_id, initial, final, spent)
    if report.get("evidence_manifest_sha256") != _evidence_manifest_sha256(report):
        raise AcceptanceError("真实付费验收报告证据绑定无效。")
    if not isinstance(report.get("server_proof"), dict):
        raise AcceptanceError("真实付费验收报告缺少服务器在线证明。")
    activation_code = os.getenv(ACTIVATION_CODE_ENV, "").strip()
    if not activation_code:
        raise AcceptanceError(
            f"在线报告核验必须通过环境变量 {ACTIVATION_CODE_ENV} 提供同一客户激活码。"
        )
    live_transport = transport or UrlLibTransport()
    login_response = live_transport.send(
        "POST",
        urljoin(expected_origin + "/", "/api/v1/auth/customer-login".lstrip("/")),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        body=_canonical_json({"code": activation_code}),
        timeout=45,
    )
    if not 200 <= login_response.status_code < 300:
        raise AcceptanceError("在线报告核验无法登录验收服务器。")
    login = _safe_json(login_response)
    token = str(login.get("token") or "")
    if not token:
        raise AcceptanceError("在线报告核验未返回客户会话。")
    proof_response = live_transport.send(
        "GET",
        urljoin(
            expected_origin + "/",
            (
                f"/api/v1/provider/release-acceptance/proof/{run_id}?"
                + urlencode(
                    {"evidence_manifest_sha256": report["evidence_manifest_sha256"]}
                )
            ).lstrip("/"),
        ),
        headers={"Accept": "application/json", "X-Customer-Token": token},
        body=None,
        timeout=45,
    )
    if not 200 <= proof_response.status_code < 300:
        raise AcceptanceError("正式服务器不存在与该报告匹配的在线证明。")
    live_proof = _safe_json(proof_response)
    _verify_server_proof(
        live_proof,
        report=report,
        expected_origin=expected_origin,
        expected_version=expected_version,
    )
    return {
        "status": "passed",
        "release_version": expected_version,
        "target_origin": expected_origin,
        "finished_at": report["finished_at"],
        "actual_spent_credits": str(spent),
        "report_sha256": _sha256(path.read_bytes()),
    }


def _verify_server_proof(
    proof: Any,
    *,
    report: Mapping[str, Any],
    expected_origin: str,
    expected_version: str,
) -> None:
    del expected_origin
    stored = report.get("server_proof")
    if stored is not None and proof != stored:
        raise AcceptanceError("在线证明与报告保存的服务器证明不一致。")
    if not isinstance(proof, dict):
        raise AcceptanceError("服务器在线证明格式无效。")
    expected = {
        "schema_version": 1,
        "release_version": expected_version,
        "run_id": report.get("run_id"),
        "evidence_manifest_sha256": report.get("evidence_manifest_sha256"),
        "provider": "shuying_legacy_cloud",
        "avatar_id": AVATAR_ID,
        "voice_id": VOICE_ID,
    }
    if any(proof.get(key) != value for key, value in expected.items()):
        raise AcceptanceError("服务器在线证明与报告版本、运行或固定资产不一致。")
    baseline = report.get("baseline")
    if proof.get("baseline") != baseline or not isinstance(baseline, dict):
        raise AcceptanceError("服务器在线证明未绑定服务器验收起点。")
    operation_window = proof.get("operation_window")
    operations = report.get("operations")
    if (
        not isinstance(operation_window, dict)
        or not isinstance(operations, dict)
        or operation_window.get("starting_operation_rowid")
        != baseline.get("starting_operation_rowid")
        or int(operation_window.get("attest_operation_rowid") or 0)
        <= int(baseline.get("starting_operation_rowid") or 0)
        or operation_window.get("operation_count") != len(operations) + 1
        or not _is_sha256(operation_window.get("digest_sha256"))
    ):
        raise AcceptanceError("服务器在线证明操作窗口无效。")
    try:
        validated = datetime.fromisoformat(str(proof["validated_at"]))
        expires = datetime.fromisoformat(str(proof["expires_at"]))
    except (KeyError, ValueError) as exc:
        raise AcceptanceError("服务器在线证明时间无效。") from exc
    now = datetime.now().astimezone()
    if (
        validated.tzinfo is None
        or expires.tzinfo is None
        or expires <= validated
        or expires - validated > timedelta(hours=24)
        or validated.astimezone() > now + timedelta(minutes=5)
        or expires.astimezone() < now
    ):
        raise AcceptanceError("服务器在线证明已过期或有效期无效。")
    if not isinstance(proof.get("ledger"), dict) or not _is_sha256(
        proof["ledger"].get("digest_sha256")
    ):
        raise AcceptanceError("服务器在线证明账本摘要无效。")
    capabilities = proof.get("capabilities")
    if not isinstance(capabilities, dict) or set(capabilities) != {
        "copywriting",
        "asr",
        "video_editor",
        "avatar",
    }:
        raise AcceptanceError("服务器在线证明能力集合无效。")
    report_capabilities = report.get("capabilities")
    if not isinstance(report_capabilities, dict):
        raise AcceptanceError("报告能力证据无效。")
    for name in ("copywriting", "asr", "video_editor", "avatar"):
        report_item = report_capabilities.get(name)
        if (
            not isinstance(report_item, dict)
            or not isinstance(capabilities.get(name), dict)
            or capabilities[name].get("status") != "passed"
            or capabilities[name].get("result_sha256")
            != report_item.get("result_sha256")
        ):
            raise AcceptanceError("服务器在线证明能力结果与报告不一致。")
    if (
        float(capabilities["video_editor"].get("duration_seconds") or 0)
        != float(report_capabilities["video_editor"]["result_duration_seconds"])
        or float(capabilities["avatar"].get("duration_seconds") or 0)
        != float(report_capabilities["avatar"]["duration_seconds"])
        or int(capabilities["avatar"].get("billed_seconds") or 0)
        != int(report_capabilities["avatar"]["billed_seconds"])
    ):
        raise AcceptanceError("服务器在线证明成片时长与报告不一致。")
    expected_transaction_ids = sorted(
        int(item["id"]) for item in report.get("new_transactions", [])
    )
    if proof["ledger"].get("transaction_ids") != expected_transaction_ids:
        raise AcceptanceError("服务器在线证明账本流水与报告不一致。")


def _is_sha256(value: Any) -> bool:
    return bool(re.fullmatch(r"[a-f0-9]{64}", str(value or "")))


def _verify_baseline_evidence(
    report: Mapping[str, Any],
    *,
    run_id: str,
    expected_origin: str,
    expected_version: str,
) -> None:
    baseline = report.get("baseline")
    if not isinstance(baseline, dict):
        raise AcceptanceError("真实付费验收报告缺少服务器起点。")
    if any(
        baseline.get(key) != value
        for key, value in {
            "schema_version": 1,
            "run_id": run_id,
            "target_origin": expected_origin,
            "release_version": expected_version,
        }.items()
    ):
        raise AcceptanceError("真实付费验收报告服务器起点绑定无效。")
    try:
        created_at = datetime.fromisoformat(str(baseline["created_at"]))
        start_rowid = int(baseline["starting_operation_rowid"])
        start_transaction = int(baseline["starting_transaction_id"])
        initial = Decimal(str(baseline["initial_balance_credits"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AcceptanceError("真实付费验收报告服务器起点字段无效。") from exc
    unsigned = {
        key: value for key, value in baseline.items() if key != "baseline_sha256"
    }
    if (
        created_at.tzinfo is None
        or start_rowid <= 0
        or start_transaction < 0
        or not initial.is_finite()
        or not 0 <= initial <= MAX_ACCEPTANCE_BUDGET
        or report.get("initial_balance_credits") != baseline["initial_balance_credits"]
        or baseline.get("baseline_sha256") != _sha256(_canonical_json(unsigned))
    ):
        raise AcceptanceError("真实付费验收报告服务器起点完整性无效。")
    transactions = report.get("new_transactions")
    if not isinstance(transactions, list) or any(
        int(item.get("id", 0)) <= start_transaction
        for item in transactions
        if isinstance(item, dict)
    ):
        raise AcceptanceError("真实付费流水不在服务器签发的验收窗口内。")


def _operation_keys(run_id: str) -> dict[str, str]:
    task_id = f"transcript-{_sha256((run_id + 'asr').encode())[:10]}"
    batch_id = f"edit-batch-{_sha256((run_id + 'editor').encode())[:12]}"
    main_key = f"video-editor-input/{batch_id}/input/main.mp4"
    opening_key = f"video-editor-input/{batch_id}/opening/opening.mp4"
    avatar_key = f"accept-avatar-{run_id[:24]}"
    return {
        "copywriting_generate": f"accept-copy-{run_id[:24]}",
        "asr_authorize": f"asr-charge-{task_id}",
        "asr_upload": f"asr-upload-{task_id}",
        "asr_submit": f"asr-submit-{task_id}",
        "video_authorize": f"video-charge-{batch_id}",
        "video_main_upload": f"video-upload-{_sha256(main_key.encode())[:32]}",
        "video_opening_upload": f"video-upload-{_sha256(opening_key.encode())[:32]}",
        "video_asr_submit": f"video-asr-{batch_id}",
        "video_plan": f"video-plan-{batch_id}",
        "video_render_submit": f"video-render-{batch_id}",
        "avatar_submit": f"avatar-submit-{avatar_key}",
    }


def _evidence_manifest_sha256(report: Mapping[str, Any]) -> str:
    """Bind a run to its exact operations, results, and ledger rows.

    This is an integrity binding, not a server signature.  It prevents a partial
    or independently copied evidence block from satisfying the local gate.
    """

    operations = report.get("operations")
    capabilities = report.get("capabilities")
    transactions = report.get("new_transactions")
    if not isinstance(operations, dict):
        raise AcceptanceError("真实付费验收报告缺少原子操作证据。")
    if not isinstance(capabilities, dict):
        raise AcceptanceError("真实付费验收报告缺少能力证据。")
    if not isinstance(transactions, list):
        raise AcceptanceError("真实付费验收报告缺少扣费流水。")
    plan_avatar = report.get("plan", {}).get("avatar", {})
    normalized_capabilities = {
        "copywriting": {
            key: capabilities["copywriting"][key]
            for key in ("status", "charged_credits", "result_sha256")
        },
        "asr": {
            key: capabilities["asr"][key]
            for key in ("status", "result_sha256", "provider_job_id_hash")
        },
        "video_editor": {
            key: capabilities["video_editor"][key]
            for key in (
                "status",
                "result_sha256",
                "result_duration_seconds",
                "opening_seconds",
            )
        },
        "avatar": {
            key: capabilities["avatar"][key]
            for key in (
                "status",
                "result_sha256",
                "duration_seconds",
                "billed_seconds",
                "charged_credits",
            )
        },
    }
    manifest = {
        "run_id": report.get("run_id"),
        "target_origin": report.get("target_origin"),
        "release_version": report.get("release_version"),
        "provider": plan_avatar.get("provider"),
        "avatar_id": plan_avatar.get("avatar_id"),
        "voice_id": plan_avatar.get("voice_id"),
        "baseline": report.get("baseline"),
        "operations": operations,
        "capabilities": normalized_capabilities,
        "new_transactions": transactions,
        "initial_balance_credits": report.get("initial_balance_credits"),
        "final_balance_credits": report.get("final_balance_credits"),
        "actual_spent_credits": report.get("actual_spent_credits"),
    }
    return _sha256(_canonical_json(manifest))


def _verify_operation_evidence(report: Mapping[str, Any], run_id: str) -> None:
    operations = report.get("operations")
    if not isinstance(operations, dict):
        raise AcceptanceError("真实付费验收报告缺少原子操作证据。")
    expected = _operation_keys(run_id)
    allowed_names = set(expected) | {"avatar_resume"}
    if not set(expected).issubset(operations) or set(operations) - allowed_names:
        raise AcceptanceError("真实付费验收报告原子操作集合无效。")
    if "avatar_resume" in operations:
        avatar_key = f"accept-avatar-{run_id[:24]}"
        attempt_id = f"{run_id[:8]}-0"
        expected["avatar_resume"] = f"avatar-resume-{avatar_key}-{attempt_id}"
    for name, key in expected.items():
        item = operations.get(name)
        if (
            not isinstance(item, dict)
            or item.get("state") != "completed"
            or not isinstance(item.get("attempts"), int)
            or not 1 <= int(item["attempts"]) <= 2
            or item.get("idempotency_key_hash") != _sha256(key.encode())
            or not _is_sha256(item.get("request_hash"))
            or not _is_sha256(item.get("local_request_hash"))
            or not _is_sha256(item.get("response_hash"))
        ):
            raise AcceptanceError(f"真实付费原子操作证据无效：{name}。")
        if name in {
            "asr_upload",
            "video_main_upload",
            "video_opening_upload",
        } and not _is_sha256(item.get("media_sha256")):
            raise AcceptanceError(f"真实付费上传证据无效：{name}。")


def _verify_capability_evidence(report: Mapping[str, Any]) -> None:
    capabilities = report.get("capabilities")
    if not isinstance(capabilities, dict) or set(capabilities) != {
        "copywriting",
        "asr",
        "video_editor",
        "avatar",
    }:
        raise AcceptanceError("四项真实付费能力证据集合无效。")
    if any(
        not isinstance(capabilities[name], dict)
        or capabilities[name].get("status") != "passed"
        for name in capabilities
    ):
        raise AcceptanceError("四项真实付费能力没有全部通过。")
    copywriting = capabilities["copywriting"]
    asr = capabilities["asr"]
    editor = capabilities["video_editor"]
    avatar = capabilities["avatar"]
    plan_avatar = report.get("plan", {}).get("avatar", {})
    if (
        not isinstance(plan_avatar, dict)
        or plan_avatar.get("provider") != "shuying_legacy_cloud"
        or plan_avatar.get("avatar_id") != AVATAR_ID
        or plan_avatar.get("voice_id") != VOICE_ID
    ):
        raise AcceptanceError("数字人验收计划没有绑定固定供应商、形象和声音。")
    try:
        charged_copywriting = Decimal(str(copywriting["charged_credits"]))
        editor_duration = Decimal(str(editor["result_duration_seconds"]))
        avatar_duration = float(avatar["duration_seconds"])
        avatar_billed_seconds = int(avatar["billed_seconds"])
        avatar_charged = Decimal(str(avatar["charged_credits"]))
        avatar_price = Decimal(str(report["plan"]["avatar"]["price_per_minute_cny"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AcceptanceError("四项真实付费能力费用或时长证据无效。") from exc
    if (
        not _is_sha256(copywriting.get("result_sha256"))
        or charged_copywriting <= 0
        or not _is_sha256(asr.get("result_sha256"))
        or not _is_sha256(asr.get("provider_job_id_hash"))
        or not _is_sha256(editor.get("result_sha256"))
        or editor_duration <= 0
        or str(editor.get("opening_seconds")) != str(OPENING_SECONDS)
        or not _is_sha256(avatar.get("result_sha256"))
        or not math.isfinite(avatar_duration)
        or avatar_duration <= 0
        or avatar_billed_seconds != math.ceil(avatar_duration)
        or avatar_charged
        != cny_to_credits(avatar_price * Decimal(avatar_billed_seconds) / Decimal(60))
    ):
        raise AcceptanceError("四项真实付费能力结果证据无效。")


def _verify_transaction_evidence(
    report: Mapping[str, Any],
    run_id: str,
    initial: Decimal,
    final: Decimal,
    spent: Decimal,
) -> None:
    rows = report.get("new_transactions")
    if not isinstance(rows, list) or not rows:
        raise AcceptanceError("真实付费验收报告缺少扣费流水。")
    task_id = f"transcript-{_sha256((run_id + 'asr').encode())[:10]}"
    batch_id = f"edit-batch-{_sha256((run_id + 'editor').encode())[:12]}"
    avatar_key = f"accept-avatar-{run_id[:24]}"
    expected_refs = {
        "copywriting": f"accept-copy-{run_id[:24]}",
        "transcription": task_id,
        "transcription_adjustment": task_id,
        "video_editor": batch_id,
        "video_editor_adjustment": batch_id,
        "avatar_reserve": avatar_key,
        "avatar_settlement": avatar_key,
    }
    required_debits = {"copywriting", "transcription", "video_editor", "avatar_reserve"}
    seen_base: set[str] = set()
    seen_ids: set[int] = set()
    running = initial
    net = Decimal("0")
    try:
        ordered = sorted(rows, key=lambda item: int(item["id"]))
        for item in ordered:
            if not isinstance(item, dict):
                raise ValueError
            row_id = int(item["id"])
            if row_id in seen_ids:
                raise ValueError
            seen_ids.add(row_id)
            ref_type = str(item.get("ref_type") or "")
            ref_id = str(item.get("ref_id") or "")
            amount = Decimal(str(item["amount"]))
            balance_after = Decimal(str(item["balance_after"]))
            if ref_type not in expected_refs or ref_id != expected_refs[ref_type]:
                raise ValueError
            if ref_type in required_debits:
                if amount >= 0 or ref_type in seen_base:
                    raise ValueError
                seen_base.add(ref_type)
            running += amount
            net += amount
            if running != balance_after:
                raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise AcceptanceError("真实付费验收报告包含额外或不一致的扣费流水。") from exc
    if seen_base != required_debits or net != -spent or running != final:
        raise AcceptanceError("真实付费验收报告流水净额与余额不一致。")


if __name__ == "__main__":
    raise SystemExit(main())
