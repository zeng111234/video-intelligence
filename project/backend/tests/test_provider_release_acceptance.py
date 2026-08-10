from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.request import ProxyHandler

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from project.backend.app.api.v1 import provider_release_acceptance as acceptance
from project.backend.app.core.control_plane_operations import OperationRecord
from project.backend.app.release_version import get_release_version
from src.models import AvatarJobSnapshot, AvatarProviderStatus
from src.services.video_editor_cloud import (
    CloudTranscript,
    ProviderJobSnapshot,
    ProviderJobStatus,
    TranscriptSegment,
)


def _baseline(
    run_id: str,
    *,
    initial: str = "5.00",
    starting_transaction_id: int = 0,
    starting_operation_rowid: int = 30,
) -> acceptance.RunBaseline:
    unsigned = {
        "schema_version": 1,
        "run_id": run_id,
        "target_origin": "https://xmt.syszr.cn",
        "release_version": "0.2.7",
        "created_at": "2026-08-10T13:59:59+00:00",
        "starting_operation_rowid": starting_operation_rowid,
        "starting_transaction_id": starting_transaction_id,
        "initial_balance_credits": initial,
    }
    return acceptance.RunBaseline(
        **unsigned, baseline_sha256=acceptance._baseline_hash(unsigned)
    )


def _proof(run_id: str, manifest: str) -> dict[str, object]:
    now = datetime.now().astimezone()
    return {
        "schema_version": 1,
        "release_version": get_release_version(),
        "run_id": run_id,
        "evidence_manifest_sha256": manifest,
        "validated_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=24)).isoformat(),
        "provider": acceptance.PROVIDER,
        "avatar_id": acceptance.AVATAR_ID,
        "voice_id": acceptance.VOICE_ID,
    }


def test_proof_reads_only_persisted_attestation_and_keeps_fixed_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "1" * 32
    manifest = "2" * 64
    payload = _proof(run_id, manifest)
    observed: dict[str, str] = {}

    def completed(**kwargs):
        observed.update(kwargs)
        return OperationRecord(
            state="completed",
            request_hash="3" * 64,
            response_status=200,
            response_body=json.dumps(payload).encode(),
            response_content_type="application/json",
        )

    monkeypatch.setattr(acceptance, "completed_operation_record", completed)
    monkeypatch.setattr(
        acceptance,
        "get_server_asr_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("supplier must not be queried")),
    )

    result = acceptance.proof(
        run_id=run_id,
        evidence_manifest_sha256=manifest,
        customer="CUSTOMER-A",
    )

    assert result == payload
    assert observed == {
        "owner": "customer:CUSTOMER-A",
        "operation_type": acceptance.ATTEST_PATH,
        "idempotency_key": f"acceptance-attest-{run_id}",
    }
    assert result["provider"] == "shuying_legacy_cloud"
    assert result["avatar_id"] == "shuying-avatar-21920"
    assert result["voice_id"] == "shuying-voice-7869"


@pytest.mark.parametrize("mutation", ["missing", "run", "version", "manifest"])
def test_proof_fails_closed_for_missing_or_mismatched_binding(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    run_id = "4" * 32
    manifest = "5" * 64
    payload = _proof(run_id, manifest)
    if mutation == "run":
        payload["run_id"] = "6" * 32
    elif mutation == "version":
        payload["release_version"] = "different-release"
    elif mutation == "manifest":
        payload["evidence_manifest_sha256"] = "7" * 64

    monkeypatch.setattr(
        acceptance,
        "completed_operation_record",
        lambda **_kwargs: (
            None
            if mutation == "missing"
            else OperationRecord(
                state="completed",
                request_hash="8" * 64,
                response_status=200,
                response_body=json.dumps(payload).encode(),
                response_content_type="application/json",
            )
        ),
    )

    with pytest.raises(HTTPException) as caught:
        acceptance.proof(
            run_id=run_id,
            evidence_manifest_sha256=manifest,
            customer="CUSTOMER-A",
        )

    assert caught.value.status_code in {404, 409}


def test_server_media_download_ignores_https_proxy_for_presigned_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers: list[object] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"server-media"

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    def build(*items):
        handlers.extend(items)
        return Opener()

    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid:8080")
    monkeypatch.setattr(acceptance, "build_opener", build)

    assert (
        acceptance._download_https(
            "https://bucket.oss-cn-hangzhou.aliyuncs.com/result.mp4?signature=secret",
            expected_host="bucket.oss-cn-hangzhou.aliyuncs.com",
        )
        == b"server-media"
    )
    assert any(
        isinstance(handler, ProxyHandler) and handler.proxies == {}
        for handler in handlers
    )


def test_attest_recomputes_and_verifies_the_complete_live_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the full attest branch without contacting a supplier."""

    run_id = "a" * 32
    task_id, batch_id, avatar_key = acceptance._ids(run_id)
    responses: dict[str, dict[str, object]] = {
        name: {} for name in acceptance._operation_specs(run_id, False)
    }
    generated = ["这是正式验收文案"]
    responses["copywriting_generate"] = {
        "result": generated,
        "token_usage": {"input_tokens": 4, "output_tokens": 8},
        "charged_credits": "0.01",
        "is_mock": False,
    }
    responses["asr_submit"] = {"provider_job_id": "asr-job-1"}
    responses["video_asr_submit"] = {"provider_job_id": "video-asr-job-1"}
    responses["video_plan"] = {"is_mock": False}
    responses["video_render_submit"] = {"provider_job_id": "render-job-1"}
    responses["avatar_submit"] = {"job_id": "avatar-job-1"}

    operations: dict[str, acceptance.OperationEvidence] = {}
    raw_responses: dict[tuple[str, str], tuple[bytes, str]] = {}
    for name, (path, key) in acceptance._operation_specs(run_id, False).items():
        raw = acceptance._canonical(responses[name])
        request_body = (
            acceptance._avatar_submit_body(run_id)
            if name == "avatar_submit"
            else {"test_operation": name}
        )
        stored_request_hash = acceptance.request_fingerprint(
            path, acceptance._canonical(request_body)
        )
        raw_responses[(path, key)] = (raw, stored_request_hash)
        operations[name] = acceptance.OperationEvidence(
            state="completed",
            attempts=1,
            idempotency_key_hash=acceptance._sha256(key.encode()),
            request_hash=stored_request_hash,
            local_request_hash=acceptance._sha256(acceptance._canonical(request_body)),
            response_hash=acceptance._sha256(raw),
            media_sha256=(
                acceptance._sha256(f"media:{name}".encode())
                if name in {"asr_upload", "video_main_upload", "video_opening_upload"}
                else None
            ),
        )

    asr_transcript = CloudTranscript(
        provider_name="aliyun_fun_asr",
        transcript="正式转写结果",
        segments=[TranscriptSegment(start=0, end=2, text="正式转写结果")],
        duration_seconds=2,
        is_mock=False,
    )
    asr_result_hash = acceptance._sha256(
        acceptance._canonical(
            asr_transcript.model_dump(
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
    editor_bytes = b"verified-editor-video"
    avatar_bytes = b"verified-avatar-video"
    capabilities = {
        "copywriting": acceptance.CapabilityEvidence(
            status="passed",
            charged_credits="0.01",
            result_sha256=acceptance._sha256(acceptance._canonical(generated)),
        ),
        "asr": acceptance.CapabilityEvidence(
            status="passed",
            result_sha256=asr_result_hash,
            provider_job_id_hash=acceptance._sha256(b"asr-job-1"),
        ),
        "video_editor": acceptance.CapabilityEvidence(
            status="passed",
            result_sha256=acceptance._sha256(editor_bytes),
            result_duration_seconds=3.4,
            opening_seconds="1.4",
        ),
        "avatar": acceptance.CapabilityEvidence(
            status="passed",
            result_sha256=acceptance._sha256(avatar_bytes),
            duration_seconds=1.067,
            billed_seconds=2,
            charged_credits="0.09",
        ),
    }
    transactions = [
        acceptance.LedgerEvidence(
            id=41,
            amount="-0.10",
            balance_after="4.90",
            ref_type="copywriting",
            ref_id=f"accept-copy-{run_id[:24]}",
            created_at="2026-08-10T14:00:00+00:00",
        )
    ]
    body = acceptance.AttestBody(
        schema_version=1,
        run_id=run_id,
        target_origin="https://xmt.syszr.cn",
        release_version="0.2.7",
        evidence_manifest_sha256="0" * 64,
        provider=acceptance.PROVIDER,
        avatar_id=acceptance.AVATAR_ID,
        voice_id=acceptance.VOICE_ID,
        baseline=_baseline(run_id),
        initial_balance_credits="5.00",
        final_balance_credits="4.90",
        actual_spent_credits="0.10",
        operations=operations,
        capabilities=capabilities,
        transactions=transactions,
    )
    body = body.model_copy(
        update={"evidence_manifest_sha256": acceptance._manifest_hash(body)}
    )

    monkeypatch.setenv("CONTROL_PLANE_DOMAIN", "xmt.syszr.cn")
    monkeypatch.setattr(acceptance, "get_release_version", lambda: "0.2.7")
    monkeypatch.setattr(
        acceptance,
        "_load_server_baseline",
        lambda **_kwargs: body.baseline,
    )
    monkeypatch.setattr(
        acceptance,
        "_verify_operation_window",
        lambda **_kwargs: {
            "starting_operation_rowid": 30,
            "attest_operation_rowid": 50,
            "operation_count": len(operations) + 1,
            "digest_sha256": acceptance._sha256(b"operation-window"),
        },
    )
    monkeypatch.setattr(
        acceptance,
        "_load_operation",
        lambda _owner, path, key: (
            raw_responses[(path, key)][0],
            json.loads(raw_responses[(path, key)][0]),
            raw_responses[(path, key)][1],
        ),
    )
    monkeypatch.setattr(acceptance, "provider_job_owned", lambda **_kwargs: True)
    monkeypatch.setattr(
        acceptance,
        "_verify_ledger",
        lambda *_args: (
            acceptance._sha256(b"ledger"),
            [41],
            {
                "copywriting": acceptance.Decimal("-0.01"),
                "asr": acceptance.Decimal("-0.01"),
                "video_editor": acceptance.Decimal("-0.01"),
                "avatar": acceptance.Decimal("-0.09"),
            },
        ),
    )
    monkeypatch.setattr(
        acceptance, "_download_https", lambda *_args, **_kwargs: editor_bytes
    )

    succeeded = ProviderJobSnapshot(
        provider_name="aliyun",
        provider_job_id="job",
        provider_stage="completed",
        status=ProviderJobStatus.SUCCEEDED,
        is_mock=False,
        can_publish=True,
    )
    asr_runtime = SimpleNamespace(
        query=lambda _job: succeeded,
        fetch_result=lambda _snapshot: asr_transcript,
    )
    video_runtime = SimpleNamespace(
        configuration=SimpleNamespace(
            oss_bucket="verified-bucket", oss_location="oss-cn-hangzhou"
        ),
        providers=SimpleNamespace(
            asr=SimpleNamespace(
                query=lambda _job: succeeded,
                fetch_result=lambda _snapshot: asr_transcript,
            ),
            render=SimpleNamespace(query=lambda _job: succeeded),
            object_store=SimpleNamespace(
                presign_get_url=lambda _key: (
                    "https://verified-bucket.oss-cn-hangzhou.aliyuncs.com/result.mp4"
                )
            ),
        ),
    )
    avatar_provider = SimpleNamespace(
        has_ready_shared_asset=lambda **kwargs: (
            (kwargs["asset_id"], kwargs["provider_asset_id"])
            in {
                (acceptance.AVATAR_ID, acceptance.AVATAR_PROVIDER_ID),
                (acceptance.VOICE_ID, acceptance.VOICE_PROVIDER_ID),
            }
        ),
        get_job=lambda _job: AvatarJobSnapshot(
            job_id="avatar-job-1",
            idempotency_key=avatar_key,
            status=AvatarProviderStatus.SUCCEEDED,
        ),
        download_result=lambda _job: (avatar_bytes, "video/mp4"),
    )
    credits = SimpleNamespace(
        repository=SimpleNamespace(
            get_avatar_billing=lambda **_kwargs: {
                "state": "settled",
                "final_seconds": 2,
                "final_credits": "0.09",
            }
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": acceptance.ATTEST_PATH,
            "headers": [(b"idempotency-key", f"acceptance-attest-{run_id}".encode())],
        }
    )
    probed_media: list[bytes] = []

    def probe_media(payload: bytes) -> float:
        probed_media.append(payload)
        return 3.4 if payload == editor_bytes else 1.067

    proof = acceptance.attest(
        body=body,
        request=request,
        customer="CUSTOMER-A",
        credits=credits,
        asr_runtime=asr_runtime,
        video_runtime=video_runtime,
        avatar_provider=avatar_provider,
        duration_probe=probe_media,
    )

    assert proof["evidence_manifest_sha256"] == body.evidence_manifest_sha256
    assert proof["run_id"] == run_id
    assert proof["ledger"]["transaction_ids"] == [41]
    assert proof["capabilities"]["avatar"]["billed_seconds"] == 2
    assert proof["baseline"] == body.baseline.model_dump()
    assert proof["operation_window"]["operation_count"] == len(operations) + 1
    assert probed_media == [editor_bytes, avatar_bytes]
    assert (task_id, batch_id) == acceptance._ids(run_id)[:2]


def _ledger_body(
    run_id: str, rows: list[acceptance.LedgerEvidence], *, initial: str, final: str
) -> acceptance.AttestBody:
    return acceptance.AttestBody(
        schema_version=1,
        run_id=run_id,
        target_origin="https://xmt.syszr.cn",
        release_version="0.2.7",
        evidence_manifest_sha256="0" * 64,
        provider=acceptance.PROVIDER,
        avatar_id=acceptance.AVATAR_ID,
        voice_id=acceptance.VOICE_ID,
        baseline=_baseline(run_id, initial=initial),
        initial_balance_credits=initial,
        final_balance_credits=final,
        actual_spent_credits=str(
            acceptance.Decimal(initial) - acceptance.Decimal(final)
        ),
        operations={},
        capabilities={},
        transactions=rows,
    )


def _ledger_rows(run_id: str, amounts: tuple[str, str, str, str]):
    task_id, batch_id, avatar_key = acceptance._ids(run_id)
    refs = (
        ("copywriting", f"accept-copy-{run_id[:24]}"),
        ("transcription", task_id),
        ("video_editor", batch_id),
        ("avatar_reserve", avatar_key),
    )
    running = acceptance.Decimal("5.00")
    rows: list[acceptance.LedgerEvidence] = []
    for index, ((ref_type, ref_id), amount) in enumerate(
        zip(refs, amounts, strict=True), start=1
    ):
        running += acceptance.Decimal(amount)
        rows.append(
            acceptance.LedgerEvidence(
                id=index,
                amount=amount,
                balance_after=str(running),
                ref_type=ref_type,
                ref_id=ref_id,
                created_at=f"2026-08-10T14:00:0{index}+00:00",
            )
        )
    return rows, running


def test_server_ledger_requires_all_four_real_debits_within_five_credit_cap() -> None:
    run_id = "b" * 32
    rows, final = _ledger_rows(run_id, ("-0.01", "-0.01", "-0.01", "-0.09"))
    body = _ledger_body(run_id, rows, initial="5.00", final=str(final))
    credits = SimpleNamespace(
        list_transactions=lambda _customer, limit: [row.model_dump() for row in rows],
        get_balance=lambda _customer: final,
    )

    _digest, transaction_ids, group_net = acceptance._verify_ledger(
        body, credits, "CUSTOMER-A", body.baseline
    )

    assert transaction_ids == [1, 2, 3, 4]
    assert group_net == {
        "copywriting": acceptance.Decimal("-0.01"),
        "asr": acceptance.Decimal("-0.01"),
        "video_editor": acceptance.Decimal("-0.01"),
        "avatar": acceptance.Decimal("-0.09"),
    }


@pytest.mark.parametrize("mutation", ["missing_capability", "over_budget"])
def test_server_ledger_rejects_incomplete_or_over_budget_paid_evidence(
    mutation: str,
) -> None:
    run_id = "c" * 32
    amounts = (
        ("-0.01", "-0.01", "-0.01", "-0.09")
        if mutation == "missing_capability"
        else ("-1.00", "-1.00", "-1.00", "-2.01")
    )
    rows, final = _ledger_rows(run_id, amounts)
    if mutation == "missing_capability":
        rows = rows[:-1]
        final = acceptance.Decimal(rows[-1].balance_after)
    body = _ledger_body(run_id, rows, initial="5.00", final=str(final))
    credits = SimpleNamespace(
        list_transactions=lambda _customer, limit: [row.model_dump() for row in rows],
        get_balance=lambda _customer: final,
    )

    with pytest.raises(HTTPException, match="账本净额"):
        acceptance._verify_ledger(body, credits, "CUSTOMER-A", body.baseline)


def test_server_ledger_uses_signed_baseline_and_exact_server_timestamps() -> None:
    run_id = "d" * 32
    rows, final = _ledger_rows(run_id, ("-0.01", "-0.01", "-0.01", "-0.09"))
    rows = [row.model_copy(update={"id": row.id + 10}) for row in rows]
    baseline = _baseline(run_id, starting_transaction_id=10)
    body = _ledger_body(run_id, rows, initial="5.00", final=str(final)).model_copy(
        update={"baseline": baseline}
    )
    stored = [row.model_dump() for row in rows]
    credits = SimpleNamespace(
        list_transactions=lambda _customer, limit: stored,
        get_balance=lambda _customer: final,
    )

    acceptance._verify_ledger(body, credits, "CUSTOMER-A", baseline)

    forged_rows = list(rows)
    forged_rows[0] = forged_rows[0].model_copy(
        update={"created_at": "2099-01-01T00:00:00+00:00"}
    )
    forged = body.model_copy(update={"transactions": forged_rows})
    with pytest.raises(HTTPException, match="账本与验收报告"):
        acceptance._verify_ledger(forged, credits, "CUSTOMER-A", baseline)

    hidden_publish = {
        "id": 15,
        "amount": "-0.01",
        "balance_after": "4.87",
        "ref_type": "publish",
        "ref_id": "hidden-publish",
        "created_at": "2026-08-10T14:01:00+00:00",
    }
    hidden_credits = SimpleNamespace(
        list_transactions=lambda _customer, limit: [hidden_publish, *stored],
        get_balance=lambda _customer: acceptance.Decimal("4.87"),
    )
    with pytest.raises(HTTPException, match="账本与验收报告"):
        acceptance._verify_ledger(body, hidden_credits, "CUSTOMER-A", baseline)


def test_avatar_attestation_binds_exact_dashu1_request_and_shared_assets() -> None:
    run_id = "e" * 32
    specs = acceptance._operation_specs(run_id, False)
    path, key = specs["avatar_submit"]
    request_body = acceptance._avatar_submit_body(run_id)
    evidence = acceptance.OperationEvidence(
        state="completed",
        attempts=1,
        idempotency_key_hash=acceptance._sha256(key.encode()),
        request_hash=acceptance.request_fingerprint(
            path, acceptance._canonical(request_body)
        ),
        local_request_hash=acceptance._sha256(acceptance._canonical(request_body)),
        response_hash=acceptance._sha256(b"response"),
    )

    acceptance._verify_avatar_request_binding(
        run_id=run_id,
        operations={"avatar_submit": evidence},
        responses={"avatar_submit": {"job_id": "avatar-job"}},
        specs=specs,
    )
    tampered_body = acceptance._avatar_submit_body(run_id)
    tampered_body["request"]["avatar_id"] = "shuying-avatar-another"
    tampered = evidence.model_copy(
        update={
            "request_hash": acceptance.request_fingerprint(
                path, acceptance._canonical(tampered_body)
            ),
            "local_request_hash": acceptance._sha256(
                acceptance._canonical(tampered_body)
            ),
        }
    )
    with pytest.raises(HTTPException, match="大树1固定请求"):
        acceptance._verify_avatar_request_binding(
            run_id=run_id,
            operations={"avatar_submit": tampered},
            responses={"avatar_submit": {"job_id": "avatar-job"}},
            specs=specs,
        )

    observed: list[tuple[str, str]] = []

    def checker(**kwargs):
        observed.append((kwargs["asset_id"], kwargs["provider_asset_id"]))
        return kwargs["asset_id"] == acceptance.AVATAR_ID

    with pytest.raises(HTTPException, match="共享形象或声音"):
        acceptance._require_ready_shared_avatar_assets(
            SimpleNamespace(has_ready_shared_asset=checker)
        )
    assert observed == [
        (acceptance.AVATAR_ID, acceptance.AVATAR_PROVIDER_ID),
        (acceptance.VOICE_ID, acceptance.VOICE_PROVIDER_ID),
    ]


def test_operation_window_rejects_hidden_training_or_publish(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "control-plane.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE control_plane_operations (
            owner TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    owner = "customer:CUSTOMER-A"
    run_id = "f" * 32
    connection.execute(
        "INSERT INTO control_plane_operations VALUES (?, ?, ?, ?, ?, ?)",
        (
            owner,
            acceptance.START_PATH,
            f"acceptance-start-{run_id}",
            "1" * 64,
            "completed",
            "2026-08-10T14:00:00+00:00",
        ),
    )
    specs = acceptance._operation_specs(run_id, False)
    for path, key in specs.values():
        connection.execute(
            "INSERT INTO control_plane_operations VALUES (?, ?, ?, ?, ?, ?)",
            (owner, path, key, "2" * 64, "completed", "2026-08-10T14:01:00+00:00"),
        )
    connection.execute(
        "INSERT INTO control_plane_operations VALUES (?, ?, ?, ?, ?, ?)",
        (
            owner,
            "/api/v1/provider/avatar/assets/train-avatar",
            "hidden-training-key",
            "3" * 64,
            "completed",
            "2026-08-10T14:02:00+00:00",
        ),
    )
    attest_key = f"acceptance-attest-{run_id}"
    connection.execute(
        "INSERT INTO control_plane_operations VALUES (?, ?, ?, ?, ?, ?)",
        (
            owner,
            acceptance.ATTEST_PATH,
            attest_key,
            "4" * 64,
            "pending",
            "2026-08-10T14:03:00+00:00",
        ),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(acceptance, "DATABASE_PATH", database)

    with pytest.raises(HTTPException, match="额外训练、发布或付费"):
        acceptance._verify_operation_window(
            owner=owner,
            baseline=_baseline(run_id, starting_operation_rowid=1),
            attest_key=attest_key,
            specs=specs,
        )


def test_server_baseline_is_issued_from_atomic_database_boundary(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "baseline.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE control_plane_operations (
            owner TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE credit_accounts (owner TEXT PRIMARY KEY, balance TEXT NOT NULL);
        CREATE TABLE credit_transactions (
            id INTEGER PRIMARY KEY,
            owner TEXT NOT NULL
        );
        """
    )
    run_id = "9" * 32
    owner = "customer:CUSTOMER-A"
    key = f"acceptance-start-{run_id}"
    body = acceptance.StartBody(
        schema_version=1,
        run_id=run_id,
        target_origin="https://xmt.syszr.cn",
        release_version="0.2.7",
    )
    request_hash = acceptance.request_fingerprint(
        acceptance.START_PATH, acceptance._canonical(acceptance._start_request(body))
    )
    connection.execute(
        "INSERT INTO control_plane_operations VALUES (?, ?, ?, ?, ?, ?)",
        (
            owner,
            acceptance.START_PATH,
            key,
            request_hash,
            "pending",
            "2026-08-11T08:00:00+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO credit_accounts VALUES (?, ?)", ("CUSTOMER-A", "5.00")
    )
    connection.execute(
        "INSERT INTO credit_transactions VALUES (?, ?)", (17, "CUSTOMER-A")
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(acceptance, "DATABASE_PATH", database)

    baseline = acceptance._capture_server_baseline(
        owner=owner,
        customer="CUSTOMER-A",
        body=body,
        idempotency_key=key,
        expected_request_hash=request_hash,
    )

    assert baseline.starting_operation_rowid == 1
    assert baseline.starting_transaction_id == 17
    assert baseline.initial_balance_credits == "5.00"
    assert baseline.created_at == "2026-08-11T08:00:00+00:00"
    assert baseline.baseline_sha256 == acceptance._baseline_hash(
        baseline.model_dump(exclude={"baseline_sha256"})
    )

    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO control_plane_operations VALUES (?, ?, ?, ?, ?, ?)",
        (
            owner,
            "/api/v1/provider/avatar/assets/train-avatar",
            "preexisting-pending-training",
            "8" * 64,
            "unknown",
            "2026-08-11T07:59:00+00:00",
        ),
    )
    connection.commit()
    connection.close()
    with pytest.raises(HTTPException, match="无法签发可信验收起点"):
        acceptance._capture_server_baseline(
            owner=owner,
            customer="CUSTOMER-A",
            body=body,
            idempotency_key=key,
            expected_request_hash=request_hash,
        )
