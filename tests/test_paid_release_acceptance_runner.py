from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Mapping
from urllib.request import ProxyHandler

import pytest

from scripts import run_paid_release_acceptance as acceptance_runner
from scripts.run_paid_release_acceptance import (
    ACTIVATION_CODE_ENV,
    AVATAR_ID,
    EXECUTION_ACK_ENV,
    EXECUTION_ACK_VALUE,
    VOICE_ID,
    AcceptanceError,
    HttpResponse,
    OutcomeUnknown,
    PaidReleaseAcceptanceRunner,
    RunnerConfig,
    UrlLibTransport,
    _NoRedirectHandler,
    _evidence_manifest_sha256,
    verify_report,
)


class FakeTransport:
    def __init__(
        self,
        *,
        balance: str = "5.00",
        proof: object | None = None,
        assets_shared: bool = True,
    ) -> None:
        self.balance = balance
        self.proof = proof
        self.assets_shared = assets_shared
        self.baseline: dict[str, object] | None = None
        self.calls: list[dict[str, object]] = []
        self.queued: list[HttpResponse | Exception] = []

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        if self.queued:
            result = self.queued.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        path = url.split("https://accept.example.test", 1)[-1]
        if path == "/health":
            return _response({"status": "ok", "release_version": "0.2.7"})
        if path == "/api/v1/auth/customer-login":
            return _response({"token": "private-token", "balance": self.balance})
        if path == "/api/v1/credits":
            return _response({"balance": self.balance, "transactions": []})
        if path == "/api/v1/provider/release-acceptance/start":
            request_payload = json.loads((body or b"{}").decode())
            unsigned = {
                **request_payload,
                "created_at": "2026-08-11T08:00:00+00:00",
                "starting_operation_rowid": 10,
                "starting_transaction_id": 0,
                "initial_balance_credits": self.balance,
            }
            self.baseline = {
                **unsigned,
                "baseline_sha256": hashlib.sha256(
                    json.dumps(
                        unsigned,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            }
            return _response(self.baseline)
        if path.startswith("/api/v1/provider/release-acceptance/baseline/"):
            return _response(self.baseline, 200 if self.baseline is not None else 404)
        if path == "/api/v1/provider/release-acceptance/capabilities":
            return _response(
                {"status": "ready", "enabled": True, "missing_configuration": []}
            )
        if path == "/api/v1/copywriting/capabilities":
            return _response(
                {
                    "enabled": True,
                    "mode": "production",
                    "missing_configuration": [],
                    "minimum_charge_credits": "0.01",
                }
            )
        if path == "/api/v1/provider/asr/capabilities":
            return _response(
                {
                    "enabled": True,
                    "live_ready": True,
                    "billing_authorized": True,
                    "provider_mode": "aliyun",
                    "provider_name": "aliyun_fun_asr",
                    "missing_configuration": [],
                    "is_mock": False,
                    "unit_price_cny_per_second": 0.00022,
                }
            )
        if path == "/api/v1/provider/video-editor/capabilities":
            return _response(
                {
                    "enabled": True,
                    "live_ready": True,
                    "provider_mode": "aliyun",
                    "provider_name": "aliyun_cloud_editor",
                    "missing_configuration": [],
                    "is_mock": False,
                    "price_version": "test-price-v1",
                    "quote_ttl_seconds": 900,
                }
            )
        if path == "/api/v1/provider/avatar/capabilities":
            return _response(
                {
                    "enabled": True,
                    "provider_name": "shuying_legacy_cloud",
                    "missing_configuration": [],
                }
            )
        if path == "/api/v1/provider/avatar/assets":
            return _response(
                [
                    {
                        "asset_id": AVATAR_ID,
                        "kind": "avatar",
                        "status": "ready",
                        "authorized": True,
                        "shared": self.assets_shared,
                    },
                    {
                        "asset_id": VOICE_ID,
                        "kind": "voice",
                        "status": "ready",
                        "authorized": True,
                        "shared": self.assets_shared,
                    },
                ]
            )
        if path.startswith("/api/v1/provider/avatar/quote?"):
            return _response(
                {
                    "reservation_credits": 0.17,
                    "price_per_minute_cny": 2.5,
                }
            )
        if path.startswith("/api/v1/provider/release-acceptance/proof/"):
            return _response(self.proof, 200 if self.proof is not None else 404)
        return _response({"ok": True})


def _response(payload: object, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload, ensure_ascii=False).encode(),
    )


def _runner(tmp_path: Path, transport: FakeTransport, *, execute: bool = False):
    return PaidReleaseAcceptanceRunner(
        RunnerConfig(
            base_url="https://accept.example.test",
            journal_path=tmp_path / "acceptance.json",
            expected_version="0.2.7",
            execute_paid=execute,
        ),
        transport=transport,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://accept.example.test",
        "https://localhost",
        "https://127.0.0.1:8443",
        "https://[::1]",
        "https://user:password@accept.example.test",
    ],
)
def test_runner_rejects_insecure_or_local_targets(tmp_path: Path, url: str) -> None:
    with pytest.raises(AcceptanceError):
        PaidReleaseAcceptanceRunner(
            RunnerConfig(base_url=url, journal_path=tmp_path / "journal.json"),
            transport=FakeTransport(),
        )


def test_transport_redirect_handler_never_forwards_a_request() -> None:
    handler = _NoRedirectHandler()

    assert (
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.test")
        is None
    )


def test_transport_ignores_malicious_https_proxy_for_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_handlers: list[object] = []

    class Response:
        status = 200
        headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok":true}'

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    def build(*handlers):
        captured_handlers.extend(handlers)
        return Opener()

    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid:8080")
    monkeypatch.setattr(acceptance_runner, "build_opener", build)

    response = UrlLibTransport().send(
        "POST",
        "https://accept.example.test/api/v1/auth/customer-login",
        headers={"Content-Type": "application/json"},
        body=b'{"code":"SECRET"}',
        timeout=1,
    )

    assert response.status_code == 200
    assert any(
        isinstance(handler, ProxyHandler) and handler.proxies == {}
        for handler in captured_handlers
    )


def test_default_mode_only_builds_a_cost_capped_plan_without_leaking_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ACTIVATION_CODE_ENV, "PRIVATE-ACTIVATION-CODE")
    transport = FakeTransport()

    result = _runner(tmp_path, transport).run()

    assert result["status"] == "passed"
    assert result["acceptance_scope"] == "plan_only"
    assert result["paid_execution_performed"] is False
    assert Decimal(result["plan"]["estimated_total_credits"]) <= Decimal("5.00")
    assert result["plan"]["avatar"]["avatar_id"] == AVATAR_ID
    assert result["plan"]["avatar"]["voice_id"] == VOICE_ID
    assert result["plan"]["fixture"]["opening_seconds"] == "1.4"
    assert all(call["method"] == "GET" for call in transport.calls[2:])
    encoded = (tmp_path / "acceptance.json").read_text(encoding="utf-8")
    assert "PRIVATE-ACTIVATION-CODE" not in encoded
    assert "private-token" not in encoded
    assert "交付测试" not in encoded


def test_execute_paid_requires_separate_environment_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ACTIVATION_CODE_ENV, "PRIVATE-ACTIVATION-CODE")
    monkeypatch.delenv(EXECUTION_ACK_ENV, raising=False)
    transport = FakeTransport()

    result = _runner(tmp_path, transport, execute=True).run()

    assert result["status"] == "failed"
    assert EXECUTION_ACK_ENV in result["message"]
    assert transport.calls == []


def test_balance_above_five_is_a_hard_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ACTIVATION_CODE_ENV, "PRIVATE-ACTIVATION-CODE")

    result = _runner(tmp_path, FakeTransport(balance="5.01")).run()

    assert result["status"] == "failed"
    assert "5.00" in result["message"]


def test_paid_run_starts_from_server_signed_baseline_and_requires_shared_dashu1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ACTIVATION_CODE_ENV, "PRIVATE-ACTIVATION-CODE")
    monkeypatch.setenv(EXECUTION_ACK_ENV, EXECUTION_ACK_VALUE)
    transport = FakeTransport()
    runner = _runner(tmp_path, transport, execute=True)
    runner._check_release_version()
    runner._login_and_balance()

    balance = runner._start_server_baseline()

    assert balance == Decimal("5.00")
    assert runner._journal["baseline"] == transport.baseline
    assert runner._journal["initial_balance_credits"] == "5.00"
    assert any(
        call["url"].endswith("/api/v1/provider/release-acceptance/start")
        for call in transport.calls
    )

    unshared = _runner(tmp_path / "unshared", FakeTransport(assets_shared=False))
    unshared._check_release_version()
    balance = unshared._login_and_balance()
    with pytest.raises(AcceptanceError, match="共享形象或声音"):
        unshared._build_plan(balance)


def test_paid_mutation_retries_once_with_identical_key_and_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ACTIVATION_CODE_ENV, "PRIVATE-ACTIVATION-CODE")
    monkeypatch.setenv(EXECUTION_ACK_ENV, EXECUTION_ACK_VALUE)
    transport = FakeTransport()
    transport.queued = [
        _response({"detail": "temporary"}, 503),
        _response({"ok": True}),
    ]
    runner = _runner(tmp_path, transport, execute=True)
    runner.token = "in-memory-token"

    result = runner._post_json(
        "one_paid_step",
        "/api/v1/provider/copywriting/generate",
        {"content_brief": "x"},
        idempotency_key="stable-idempotency-key",
    )

    assert result == {"ok": True}
    assert len(transport.calls) == 2
    assert transport.calls[0]["body"] == transport.calls[1]["body"]
    assert transport.calls[0]["headers"] == transport.calls[1]["headers"]
    journal = json.loads((tmp_path / "acceptance.json").read_text(encoding="utf-8"))
    assert journal["operations"]["one_paid_step"]["attempts"] == 2
    assert "stable-idempotency-key" not in json.dumps(journal)
    assert "in-memory-token" not in json.dumps(journal)


def test_409_is_outcome_unknown_and_the_journal_blocks_a_new_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ACTIVATION_CODE_ENV, "PRIVATE-ACTIVATION-CODE")
    transport = FakeTransport()
    transport.queued = [_response({"message": "pending"}, 409)]
    runner = _runner(tmp_path, transport, execute=True)
    runner.token = "in-memory-token"
    arguments = (
        "uncertain_step",
        "/api/v1/provider/avatar/submit",
        {"request": {"safe": True}},
    )

    with pytest.raises(OutcomeUnknown, match="禁止"):
        runner._post_json(*arguments, idempotency_key="stable-avatar-key")
    call_count = len(transport.calls)
    with pytest.raises(OutcomeUnknown, match="不会再次提交"):
        runner._post_json(*arguments, idempotency_key="stable-avatar-key")

    assert len(transport.calls) == call_count
    journal = json.loads((tmp_path / "acceptance.json").read_text(encoding="utf-8"))
    assert journal["operations"]["uncertain_step"]["state"] == "outcome_unknown"


class CrashBeforeResponseTransport(FakeTransport):
    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        self.calls.append({"method": method, "url": url})
        raise KeyboardInterrupt


@pytest.mark.parametrize("operation_kind", ["json", "upload"])
def test_restart_after_send_started_never_replays_a_paid_operation(
    tmp_path: Path, operation_kind: str
) -> None:
    crashed = _runner(tmp_path, CrashBeforeResponseTransport(), execute=True)
    crashed.token = "in-memory-token"
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"safe-offline-fixture")

    with pytest.raises(KeyboardInterrupt):
        if operation_kind == "json":
            crashed._post_json(
                "crash_step",
                "/api/v1/provider/copywriting/generate",
                {"content_brief": "x"},
                idempotency_key="fixed-crash-key",
            )
        else:
            crashed._upload(
                "crash_step",
                "/api/v1/provider/asr/upload",
                fields={
                    "task_id": "crash-task",
                    "object_key": "asr-input/crash-task/input.mp4",
                    "media_type": "video/mp4",
                },
                media_path=media,
                idempotency_key="fixed-crash-key",
            )

    saved = json.loads((tmp_path / "acceptance.json").read_text(encoding="utf-8"))
    assert saved["operations"]["crash_step"]["state"] == "submitted"
    assert saved["operations"]["crash_step"]["attempts"] == 1

    replacement_transport = FakeTransport()
    restarted = _runner(tmp_path, replacement_transport, execute=True)
    restarted.token = "in-memory-token"
    with pytest.raises(OutcomeUnknown, match="不会再次"):
        if operation_kind == "json":
            restarted._post_json(
                "crash_step",
                "/api/v1/provider/copywriting/generate",
                {"content_brief": "x"},
                idempotency_key="fixed-crash-key",
            )
        else:
            restarted._upload(
                "crash_step",
                "/api/v1/provider/asr/upload",
                fields={
                    "task_id": "crash-task",
                    "object_key": "asr-input/crash-task/input.mp4",
                    "media_type": "video/mp4",
                },
                media_path=media,
                idempotency_key="fixed-crash-key",
            )
    assert replacement_transport.calls == []


class PendingVoiceTransport(FakeTransport):
    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body}
        )
        if url.endswith("/api/v1/provider/avatar/submit"):
            return _response({"job_id": "voice-tts:pending", "status": "processing"})
        if "/api/v1/provider/avatar/jobs/find/" in url:
            return _response({"job_id": "voice-tts:pending", "status": "processing"})
        if url.endswith("/api/v1/provider/avatar/resume"):
            return _response({"job_id": "voice-tts:pending", "status": "processing"})
        raise AssertionError(f"unexpected offline call: {method} {url}")


def test_pending_avatar_voice_uses_read_only_status_then_one_fixed_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "scripts.run_paid_release_acceptance.time.sleep", lambda _: None
    )
    transport = PendingVoiceTransport()
    runner = _runner(tmp_path, transport, execute=True)
    runner.token = "in-memory-token"

    with pytest.raises(OutcomeUnknown, match="只允许一次固定恢复请求"):
        runner._execute_avatar()

    posts = [call for call in transport.calls if call["method"] == "POST"]
    gets = [call for call in transport.calls if call["method"] == "GET"]
    assert len(posts) == 2
    assert posts[0]["url"].endswith("/avatar/submit")
    assert posts[1]["url"].endswith("/avatar/resume")
    assert len(gets) == 3
    assert set(runner._journal["operations"]) == {"avatar_submit", "avatar_resume"}
    resume = runner._journal["operations"]["avatar_resume"]
    assert resume["state"] == "completed"
    assert resume["attempts"] == 1


def _paid_report(*, finished_at: str | None = None) -> dict[str, object]:
    run_id = "0123456789abcdef0123456789abcdef"
    task_id = f"transcript-{_hash(run_id + 'asr')[:10]}"
    batch_id = f"edit-batch-{_hash(run_id + 'editor')[:12]}"
    avatar_key = f"accept-avatar-{run_id[:24]}"
    main_key = f"video-editor-input/{batch_id}/input/main.mp4"
    opening_key = f"video-editor-input/{batch_id}/opening/opening.mp4"
    operation_keys = {
        "copywriting_generate": f"accept-copy-{run_id[:24]}",
        "asr_authorize": f"asr-charge-{task_id}",
        "asr_upload": f"asr-upload-{task_id}",
        "asr_submit": f"asr-submit-{task_id}",
        "video_authorize": f"video-charge-{batch_id}",
        "video_main_upload": f"video-upload-{_hash(main_key)[:32]}",
        "video_opening_upload": f"video-upload-{_hash(opening_key)[:32]}",
        "video_asr_submit": f"video-asr-{batch_id}",
        "video_plan": f"video-plan-{batch_id}",
        "video_render_submit": f"video-render-{batch_id}",
        "avatar_submit": f"avatar-submit-{avatar_key}",
    }
    operations = {
        name: {
            "state": "completed",
            "attempts": 1,
            "idempotency_key_hash": _hash(key),
            "request_hash": _hash(f"request:{name}"),
            "local_request_hash": _hash(f"local-request:{name}"),
            "response_hash": _hash(f"response:{name}"),
            **(
                {"media_sha256": _hash(f"media:{name}")}
                if name
                in {
                    "asr_upload",
                    "video_main_upload",
                    "video_opening_upload",
                }
                else {}
            ),
        }
        for name, key in operation_keys.items()
    }
    baseline_unsigned = {
        "schema_version": 1,
        "run_id": run_id,
        "target_origin": "https://accept.example.test",
        "release_version": "0.2.7",
        "created_at": "2026-08-11T08:00:00+00:00",
        "starting_operation_rowid": 10,
        "starting_transaction_id": 0,
        "initial_balance_credits": "5.00",
    }
    baseline = {
        **baseline_unsigned,
        "baseline_sha256": hashlib.sha256(
            json.dumps(
                baseline_unsigned,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    }
    report: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "passed",
        "acceptance_scope": "paid_execution",
        "paid_execution_performed": True,
        "target_origin": "https://accept.example.test",
        "release_version": "0.2.7",
        "finished_at": finished_at or datetime.now().astimezone().isoformat(),
        "budget_credits": "5.00",
        "initial_balance_credits": "5.00",
        "final_balance_credits": "4.80",
        "actual_spent_credits": "0.20",
        "baseline": baseline,
        "plan": {
            "avatar": {
                "provider": "shuying_legacy_cloud",
                "avatar_id": AVATAR_ID,
                "voice_id": VOICE_ID,
                "price_per_minute_cny": "2.5",
            }
        },
        "operations": operations,
        "capabilities": {
            "copywriting": {
                "status": "passed",
                "charged_credits": "0.01",
                "result_sha256": _hash("copy-result"),
            },
            "asr": {
                "status": "passed",
                "result_sha256": _hash("asr-result"),
                "provider_job_id_hash": _hash("asr-job"),
            },
            "video_editor": {
                "status": "passed",
                "result_sha256": _hash("editor-result"),
                "result_duration_seconds": 3.4,
                "opening_seconds": "1.4",
            },
            "avatar": {
                "status": "passed",
                "result_sha256": _hash("avatar-result"),
                "duration_seconds": 3.1,
                "billed_seconds": 4,
                "charged_credits": "0.17",
            },
        },
        "new_transactions": [
            {
                "id": 1,
                "ref_type": "copywriting",
                "ref_id": f"accept-copy-{run_id[:24]}",
                "amount": "-0.01",
                "balance_after": "4.99",
                "created_at": "2026-08-11T08:01:00+00:00",
            },
            {
                "id": 2,
                "ref_type": "transcription",
                "ref_id": task_id,
                "amount": "-0.01",
                "balance_after": "4.98",
                "created_at": "2026-08-11T08:02:00+00:00",
            },
            {
                "id": 3,
                "ref_type": "video_editor",
                "ref_id": batch_id,
                "amount": "-0.01",
                "balance_after": "4.97",
                "created_at": "2026-08-11T08:03:00+00:00",
            },
            {
                "id": 4,
                "ref_type": "avatar_reserve",
                "ref_id": avatar_key,
                "amount": "-0.17",
                "balance_after": "4.80",
                "created_at": "2026-08-11T08:04:00+00:00",
            },
        ],
    }
    report["evidence_manifest_sha256"] = _evidence_manifest_sha256(report)
    validated = datetime.now().astimezone()
    report["server_proof"] = {
        "schema_version": 1,
        "release_version": report["release_version"],
        "run_id": run_id,
        "evidence_manifest_sha256": report["evidence_manifest_sha256"],
        "validated_at": validated.isoformat(),
        "expires_at": (validated + timedelta(hours=24)).isoformat(),
        "provider": "shuying_legacy_cloud",
        "avatar_id": AVATAR_ID,
        "voice_id": VOICE_ID,
        "baseline": baseline,
        "operation_window": {
            "starting_operation_rowid": 10,
            "attest_operation_rowid": 30,
            "operation_count": len(operations) + 1,
            "digest_sha256": _hash("operation-window"),
        },
        "ledger": {"digest_sha256": _hash("ledger"), "transaction_ids": [1, 2, 3, 4]},
        "capabilities": {
            "copywriting": {
                "status": "passed",
                "result_sha256": report["capabilities"]["copywriting"]["result_sha256"],
            },
            "asr": {
                "status": "passed",
                "result_sha256": report["capabilities"]["asr"]["result_sha256"],
            },
            "video_editor": {
                "status": "passed",
                "result_sha256": report["capabilities"]["video_editor"][
                    "result_sha256"
                ],
                "duration_seconds": report["capabilities"]["video_editor"][
                    "result_duration_seconds"
                ],
            },
            "avatar": {
                "status": "passed",
                "result_sha256": report["capabilities"]["avatar"]["result_sha256"],
                "duration_seconds": report["capabilities"]["avatar"][
                    "duration_seconds"
                ],
                "billed_seconds": report["capabilities"]["avatar"]["billed_seconds"],
            },
        },
    }
    return report


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_verify_report_accepts_only_a_recent_real_paid_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ACTIVATION_CODE_ENV, "PRIVATE-ACTIVATION-CODE")
    path = tmp_path / "paid.json"
    report = _paid_report()
    path.write_text(json.dumps(report), encoding="utf-8")

    result = verify_report(
        path,
        expected_origin="https://accept.example.test",
        expected_version="0.2.7",
        transport=FakeTransport(proof=report["server_proof"]),
    )

    assert result["status"] == "passed"
    assert result["release_version"] == "0.2.7"
    assert len(result["report_sha256"]) == 64


def test_verify_report_rejects_locally_rebound_report_without_live_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ACTIVATION_CODE_ENV, "PRIVATE-ACTIVATION-CODE")
    report = _paid_report()
    report["capabilities"]["copywriting"]["result_sha256"] = _hash("forged")
    report["evidence_manifest_sha256"] = _evidence_manifest_sha256(report)
    report["server_proof"]["evidence_manifest_sha256"] = report[
        "evidence_manifest_sha256"
    ]
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(AcceptanceError, match="不存在"):
        verify_report(
            path,
            expected_origin="https://accept.example.test",
            expected_version="0.2.7",
            transport=FakeTransport(),
        )


def test_verify_report_rejects_stale_evidence(tmp_path: Path) -> None:
    stale = datetime.now().astimezone() - timedelta(hours=25)
    payload = _paid_report(finished_at=stale.isoformat())
    path = tmp_path / "stale.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AcceptanceError, match="过期"):
        verify_report(
            path,
            expected_origin="https://accept.example.test",
            expected_version="0.2.7",
        )


def test_verify_report_rejects_plan_only_evidence(tmp_path: Path) -> None:
    payload = _paid_report()
    payload["acceptance_scope"] = "plan_only"
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AcceptanceError):
        verify_report(
            path,
            expected_origin="https://accept.example.test",
            expected_version="0.2.7",
        )


def test_verify_report_rejects_a_handwritten_pass_without_bound_evidence(
    tmp_path: Path,
) -> None:
    fake = {
        "schema_version": 1,
        "run_id": "0" * 32,
        "status": "passed",
        "acceptance_scope": "paid_execution",
        "paid_execution_performed": True,
        "target_origin": "https://accept.example.test",
        "release_version": "0.2.7",
        "finished_at": datetime.now().astimezone().isoformat(),
        "budget_credits": "5.00",
        "initial_balance_credits": "5.00",
        "final_balance_credits": "4.99",
        "actual_spent_credits": "0.01",
        "capabilities": {
            name: {"status": "passed"}
            for name in ("copywriting", "asr", "video_editor", "avatar")
        },
        "new_transactions": [],
    }
    path = tmp_path / "handwritten.json"
    path.write_text(json.dumps(fake), encoding="utf-8")

    with pytest.raises(AcceptanceError):
        verify_report(
            path,
            expected_origin="https://accept.example.test",
            expected_version="0.2.7",
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("wrong_ref", "额外或不一致"),
        ("extra_publish_debit", "额外或不一致"),
        ("operation_not_completed", "原子操作"),
        ("invalid_result_hash", "能力结果"),
        ("ledger_net_mismatch", "硬门禁|流水净额"),
    ],
)
def test_verify_report_rejects_tampered_paid_evidence_even_with_rebound_manifest(
    tmp_path: Path, mutation: str, message: str
) -> None:
    report = copy.deepcopy(_paid_report())
    if mutation == "wrong_ref":
        report["new_transactions"][0]["ref_id"] = "another-customer-ref"
    elif mutation == "extra_publish_debit":
        report["new_transactions"].append(
            {
                "id": 5,
                "ref_type": "publish",
                "ref_id": "publish-other-customer",
                "amount": "-0.01",
                "balance_after": "4.79",
            }
        )
        report["final_balance_credits"] = "4.79"
        report["actual_spent_credits"] = "0.21"
    elif mutation == "operation_not_completed":
        report["operations"]["avatar_submit"]["state"] = "submitted"
    elif mutation == "invalid_result_hash":
        report["capabilities"]["avatar"]["result_sha256"] = "not-a-hash"
    elif mutation == "ledger_net_mismatch":
        report["final_balance_credits"] = "4.81"
    report["evidence_manifest_sha256"] = _evidence_manifest_sha256(report)
    path = tmp_path / f"tampered-{mutation}.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(AcceptanceError, match=message):
        verify_report(
            path,
            expected_origin="https://accept.example.test",
            expected_version="0.2.7",
        )
