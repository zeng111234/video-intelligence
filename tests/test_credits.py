"""积分核心单元测试：换算、余额读写、原子扣费、余额不足、流水持久化。"""

from __future__ import annotations

from decimal import Decimal
from threading import Thread

import pytest

from src.models import AvatarTask
from src.repositories import MockRepository, SQLiteRepository
from src.services.credits import (
    CreditsService,
    InsufficientCreditsError,
    _current_owner,
    cny_to_credits,
)


@pytest.fixture(autouse=True)
def _use_customer_owner() -> None:
    """扣费测试默认以客户身份执行（管理员已免费，不产生扣费）；结束后还原上下文，避免污染其他测试。"""
    token = _current_owner.set("TEST-CUSTOMER")
    yield
    _current_owner.reset(token)


# ---------------------------------------------------------------------------
# 换算：1 元 = 1 积分，向上取整到 2 位小数
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cny", "expected"),
    [
        ("0.082", "0.09"),   # 计划示例：0.082 元 → 0.09 积分
        ("2.25", "2.25"),    # 数字人整值
        ("0.20", "0.20"),    # 转写上限定值
        ("0.00022", "0.01"),  # 极小金额向上取整到最小积分单位
        ("0.00187", "0.01"),  # 8.5 秒 × 0.00022
        ("0", "0"),
        ("-1", "0"),         # 非正金额按 0 处理
        ("0.09", "0.09"),
        ("0.095", "0.10"),   # 边界进一
    ],
)
def test_cny_to_credits_rounds_up_to_2dp(cny: str, expected: str) -> None:
    assert cny_to_credits(cny) == Decimal(expected)


def test_cny_to_credits_accepts_decimal_and_float() -> None:
    assert cny_to_credits(Decimal("1.234")) == Decimal("1.24")
    assert cny_to_credits(1.234) == Decimal("1.24")


# ---------------------------------------------------------------------------
# 余额与流水（SQLite 真实持久化）
# ---------------------------------------------------------------------------


@pytest.fixture()
def sqlite_service(tmp_path, monkeypatch) -> CreditsService:
    # 旧测试基于"初始余额 0"；默认赠送通过环境变量可关（默认 400 另行验证）
    monkeypatch.setenv("DEFAULT_CREDIT_BALANCE", "0")
    repository = SQLiteRepository(tmp_path / "credits.db")
    return CreditsService(repository)


def test_initial_balance_is_zero(sqlite_service: CreditsService) -> None:
    assert sqlite_service.get_balance() == Decimal("0")


def test_credit_increases_balance_and_records_transaction(
    sqlite_service: CreditsService,
) -> None:
    balance = sqlite_service.credit("10", "管理员充值", ref_type="admin", ref_id="op-1")
    assert balance == Decimal("10")
    assert sqlite_service.get_balance() == Decimal("10")

    txns = sqlite_service.list_transactions()
    assert len(txns) == 1
    assert txns[0]["amount"] == "10"
    assert txns[0]["balance_after"] == "10"
    assert txns[0]["reason"] == "管理员充值"
    assert txns[0]["ref_type"] == "admin"
    assert txns[0]["ref_id"] == "op-1"


def test_debit_reduces_balance_and_records_transaction(
    sqlite_service: CreditsService,
) -> None:
    sqlite_service.credit("10", "管理员充值")
    balance = sqlite_service.debit("3.5", "转写费用", ref_type="transcription", ref_id="t-1")
    assert balance == Decimal("6.5")
    assert sqlite_service.get_balance() == Decimal("6.5")

    txns = sqlite_service.list_transactions()
    assert len(txns) == 2
    assert txns[0]["amount"] == "-3.5"
    assert txns[0]["balance_after"] == "6.5"
    assert txns[0]["reason"] == "转写费用"


def test_debit_insufficient_balance_is_blocked(
    sqlite_service: CreditsService,
) -> None:
    sqlite_service.credit("1", "管理员充值")
    with pytest.raises(InsufficientCreditsError) as exc_info:
        sqlite_service.debit("2", "剪辑费用", ref_type="editing", ref_id="e-1")
    assert "积分不足" in exc_info.value.message
    # 余额不变且未新增流水（仅保留此前的充值记录）
    assert sqlite_service.get_balance() == Decimal("1")
    txns = sqlite_service.list_transactions()
    assert len(txns) == 1
    assert txns[0]["amount"] == "1"


def test_debit_exact_balance_is_allowed(sqlite_service: CreditsService) -> None:
    sqlite_service.credit("5", "管理员充值")
    balance = sqlite_service.debit("5", "数字人费用", ref_type="avatar", ref_id="a-1")
    assert balance == Decimal("0")
    assert sqlite_service.get_balance() == Decimal("0")


def test_balance_persists_across_reopen(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEFAULT_CREDIT_BALANCE", "0")
    database = tmp_path / "persist.db"
    first = CreditsService(SQLiteRepository(database))
    first.credit("20", "管理员充值")
    first.debit("7.5", "爬虫费用", ref_type="crawler", ref_id="c-1")

    reopened = CreditsService(SQLiteRepository(database))
    assert reopened.get_balance() == Decimal("12.5")
    txns = reopened.list_transactions()
    assert len(txns) == 2
    assert txns[0]["ref_type"] == "crawler"


def test_transactions_listed_newest_first_and_limited(
    sqlite_service: CreditsService,
) -> None:
    for i in range(5):
        sqlite_service.credit("1", f"充值 {i}")
    txns = sqlite_service.list_transactions(limit=3)
    assert len(txns) == 3
    assert txns[0]["reason"] == "充值 4"
    assert txns[-1]["reason"] == "充值 2"


# ---------------------------------------------------------------------------
# 原子扣费：并发扣费不超扣
# ---------------------------------------------------------------------------


def test_concurrent_debits_never_overdraw(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEFAULT_CREDIT_BALANCE", "0")
    repository = SQLiteRepository(tmp_path / "concurrent.db")
    service = CreditsService(repository)
    service.credit("10", "充值", owner="TEST-CUSTOMER")

    failures: list[Exception] = []
    successes: list[Decimal] = []

    def worker() -> None:
        try:
            successes.append(service.debit("1", "并发扣费", owner="TEST-CUSTOMER"))
        except InsufficientCreditsError:
            pass
        except Exception as exc:  # pragma: no cover - 仅防测试悬挂
            failures.append(exc)

    threads = [Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 10 次成功扣费后余额为 0，其余 10 次被阻止，且无负数
    assert not failures
    assert len(successes) == 10
    assert service.get_balance() == Decimal("0")
    assert all(balance >= 0 for balance in successes)
    # 流水恰好 11 条（1 充值 + 10 扣费）
    assert len(service.list_transactions("TEST-CUSTOMER")) == 11


# ---------------------------------------------------------------------------
# Mock 仓库同样可用
# ---------------------------------------------------------------------------


def test_mock_repository_credits() -> None:
    service = CreditsService(MockRepository())
    service.credit("10", "充值", owner="TEST-CUSTOMER")
    service.debit("4", "扣费", owner="TEST-CUSTOMER")
    assert service.get_balance("TEST-CUSTOMER") == Decimal("6")
    assert len(service.list_transactions("TEST-CUSTOMER")) == 2
    with pytest.raises(InsufficientCreditsError):
        service.debit("99999", "超额扣费", owner="TEST-CUSTOMER")
    assert service.get_balance("TEST-CUSTOMER") == Decimal("6")


def test_credit_rejects_non_positive(sqlite_service: CreditsService) -> None:
    with pytest.raises(ValueError):
        sqlite_service.credit("0", "非法充值")
    with pytest.raises(ValueError):
        sqlite_service.credit("-5", "非法充值")
    with pytest.raises(ValueError):
        sqlite_service.debit("0", "非法扣费")


# ---------------------------------------------------------------------------
# 收费点接入：转写 / 剪辑 / 数字人 / 爬虫 / 文案 扣积分
# ---------------------------------------------------------------------------


def test_transcription_cloud_task_debits_credits(tmp_path) -> None:
    from types import SimpleNamespace

    from src.services.cloud_transcription import ASR_PRICE_VERSION
    from src.services.transcription import TranscriptionService

    repository = MockRepository(candidates=[], tasks=[])
    CreditsService(repository).credit("5", "测试充值")
    initial = CreditsService(repository).get_balance()

    class FakeRuntime:
        def ensure_authorized(self, duration_seconds: float):
            return Decimal("0.2")  # 0.20 元 → 0.20 积分

        def capability(self):
            return {"price_version": ASR_PRICE_VERSION}

    def fake_probe(args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='{"streams": [{"codec_type": "audio"}], '
            '"format": {"duration": "8.5"}}',
        )

    service = TranscriptionService(
        repository,
        command_runner=fake_probe,
        cloud_runtime=FakeRuntime(),
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )
    service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=b"\x00\x00\x00\x18ftypisom-authorized-video",
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="fun-asr",
        async_processing=True,
    )
    credits = CreditsService(repository)
    assert credits.get_balance() == initial - Decimal("0.20")
    txns = credits.list_transactions()
    assert txns[0]["reason"] == "云端转写费用"
    assert txns[0]["ref_type"] == "transcription"


def test_transcription_cloud_task_blocked_when_insufficient(tmp_path) -> None:
    from types import SimpleNamespace

    from src.services.transcription import TranscriptionError, TranscriptionService

    repository = MockRepository(candidates=[], tasks=[])
    repository.adjust_credit_balance(amount=Decimal("-1000"), reason="清零")

    class FakeRuntime:
        def ensure_authorized(self, duration_seconds: float):
            return Decimal("0.2")

        def capability(self):
            return {"price_version": "v"}

    def fake_probe(args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='{"streams": [{"codec_type": "audio"}], '
            '"format": {"duration": "8.5"}}',
        )

    service = TranscriptionService(
        repository,
        command_runner=fake_probe,
        cloud_runtime=FakeRuntime(),
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )
    with pytest.raises(TranscriptionError) as exc_info:
        service.create_task(
            media_name="owned.mp4",
            media_type="video/mp4",
            media_bytes=b"\x00\x00\x00\x18ftypisom-authorized-video",
            rights_confirmed=True,
            rights_holder="测试公司",
            model_name="fun-asr",
            async_processing=True,
        )
    assert exc_info.value.code == "insufficient_credits"
    assert CreditsService(repository).get_balance() == Decimal("0")


def test_video_editor_sandbox_is_free_and_live_batch_debits_on_confirmation(
    tmp_path,
) -> None:
    from src.adapters.video_editor_cloud import build_cloud_providers
    from src.services.video_editor_cloud import (
        CloudEditorConfiguration,
        CloudProviderMode,
    )
    from src.services.video_editor_workflow import VideoEditorWorkflowService

    class VideoEditingStub:
        def __init__(self, output_directory):
            self.output_directory = output_directory

    class TranscriptionStub:
        def get_approved_revision(self, _task_id: str):
            return None

    repository = MockRepository(tasks=[])
    CreditsService(repository).credit("5", "测试充值")
    service = VideoEditorWorkflowService(
        repository,
        VideoEditingStub(tmp_path / "edits"),
        TranscriptionStub(),
        None,
        cloud_configuration=CloudEditorConfiguration(
            provider_mode=CloudProviderMode.SANDBOX,
        ),
        cloud_providers=build_cloud_providers(
            CloudEditorConfiguration(provider_mode=CloudProviderMode.SANDBOX),
        ),
    )
    service._probe_media = lambda _path: {  # type: ignore[method-assign]
        "duration_seconds": 60.0,
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "orientation": "vertical",
        "has_audio": True,
        "size_bytes": 1024,
    }
    source_id = service.upload_source(
        file_name="authorized.mp4",
        media_type="video/mp4",
        media_bytes=b"not-a-real-video",
        rights_confirmed=True,
        rights_holder="测试公司",
    )["source_id"]
    quote = service.create_cloud_preflight(
        source_id=source_id,
        output_profile="720p",
        target_platform="douyin",
    )
    initial = CreditsService(repository).get_balance()

    service.create_cloud_batch(
        source_ids=[source_id],
        target_platform="douyin",
        output_profile="720p",
        quote_id=quote["quote_id"],
        billing_confirmation={
            "confirmed": True,
            "max_cost_cny": float(quote["estimated_max"]),
        },
        idempotency_key="credit-batch-1",
    )
    credits = CreditsService(repository)
    assert credits.get_balance() == initial

    # 用不联网的正式提供方替身保留真实计费边界覆盖；沙箱本身必须免费。
    service.cloud_capabilities = lambda: {  # type: ignore[method-assign]
        "provider_mode": "aliyun",
        "provider_name": "live-billing-test-double",
        "enabled": True,
        "live_ready": True,
        "missing_configuration": [],
        "is_mock": False,
    }
    service._submit_cloud_analysis = lambda batch, _item: batch  # type: ignore[method-assign]
    service.create_cloud_batch(
        source_ids=[source_id],
        target_platform="douyin",
        output_profile="720p",
        quote_id=quote["quote_id"],
        billing_confirmation={
            "confirmed": True,
            "max_cost_cny": float(quote["estimated_max"]),
        },
        idempotency_key="credit-batch-live",
    )
    assert credits.get_balance() < initial
    txns = credits.list_transactions()
    assert txns[0]["reason"] == "云端剪辑成片费用"
    assert txns[0]["ref_type"] == "video_editor"


def test_video_editor_batch_blocked_when_insufficient_rolls_back_idempotency(
    tmp_path,
) -> None:
    from src.adapters.video_editor_cloud import build_cloud_providers
    from src.services.video_editor_cloud import (
        CloudEditorConfiguration,
        CloudProviderMode,
    )
    from src.services.video_editor_workflow import (
        VideoEditorWorkflowError,
        VideoEditorWorkflowService,
    )

    class VideoEditingStub:
        def __init__(self, output_directory):
            self.output_directory = output_directory

    class TranscriptionStub:
        def get_approved_revision(self, _task_id: str):
            return None

    repository = MockRepository(tasks=[])
    repository.adjust_credit_balance(amount=Decimal("-1000"), reason="清零")
    service = VideoEditorWorkflowService(
        repository,
        VideoEditingStub(tmp_path / "edits"),
        TranscriptionStub(),
        None,
        cloud_configuration=CloudEditorConfiguration(
            provider_mode=CloudProviderMode.SANDBOX,
        ),
        cloud_providers=build_cloud_providers(
            CloudEditorConfiguration(provider_mode=CloudProviderMode.SANDBOX),
        ),
    )
    service._probe_media = lambda _path: {  # type: ignore[method-assign]
        "duration_seconds": 60.0,
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "orientation": "vertical",
        "has_audio": True,
        "size_bytes": 1024,
    }
    source_id = service.upload_source(
        file_name="authorized.mp4",
        media_type="video/mp4",
        media_bytes=b"not-a-real-video",
        rights_confirmed=True,
        rights_holder="测试公司",
    )["source_id"]
    quote = service.create_cloud_preflight(
        source_id=source_id,
        output_profile="720p",
        target_platform="douyin",
    )
    service.cloud_capabilities = lambda: {  # type: ignore[method-assign]
        "provider_mode": "aliyun",
        "provider_name": "live-billing-test-double",
        "enabled": True,
        "live_ready": True,
        "missing_configuration": [],
        "is_mock": False,
    }
    service._submit_cloud_analysis = lambda batch, _item: batch  # type: ignore[method-assign]

    with pytest.raises(VideoEditorWorkflowError, match="积分不足"):
        service.create_cloud_batch(
            source_ids=[source_id],
            target_platform="douyin",
            output_profile="720p",
            quote_id=quote["quote_id"],
            billing_confirmation={
                "confirmed": True,
                "max_cost_cny": float(quote["estimated_max"]),
            },
            idempotency_key="credit-batch-blocked",
        )
    # 幂等锁已回滚，充值后可重试成功
    CreditsService(repository).credit("10", "充值")
    created = service.create_cloud_batch(
        source_ids=[source_id],
        target_platform="douyin",
        output_profile="720p",
        quote_id=quote["quote_id"],
        billing_confirmation={
            "confirmed": True,
            "max_cost_cny": float(quote["estimated_max"]),
        },
        idempotency_key="credit-batch-blocked",
    )
    assert created["batch_id"]


def test_avatar_submit_debits_credits() -> None:
    from src.models import (
        AvatarAsset,
        AvatarAssetKind,
        AvatarCapability,
        AvatarJobSnapshot,
        AvatarProviderStatus,
        AvatarSubmitRequest,
        ProviderMode,
    )
    from src.services.avatar import AvatarService

    class FakeProvider:
        def capabilities(self):
            return AvatarCapability(
                provider_name="fake-avatar",
                display_name="测试数字人",
                mode=ProviderMode.PRODUCTION,
                enabled=True,
                permission_status="authorized",
                max_script_chars=240,
                # 保留旧能力字段，验证提交计费不再沿用固定 45 秒报价。
                estimated_cost_cny=1.875,
                estimated_seconds=45,
            )

        def list_assets(self):
            return [
                AvatarAsset(
                    asset_id="avatar-1",
                    kind=AvatarAssetKind.AVATAR,
                    name="授权形象",
                    authorized=True,
                    status="ready",
                ),
                AvatarAsset(
                    asset_id="voice-1",
                    kind=AvatarAssetKind.VOICE,
                    name="授权音色",
                    authorized=True,
                    status="ready",
                ),
            ]

        def submit(self, request):
            return AvatarJobSnapshot(
                job_id="job-1",
                idempotency_key=request.idempotency_key,
                status=AvatarProviderStatus.QUEUED,
                progress=5,
                stage="供应商排队中",
                provider_job_id="job-1",
            )

    repository = MockRepository(candidates=[], tasks=[])
    service = AvatarService(repository, FakeProvider())
    CreditsService(repository).credit("5", "测试充值")
    initial = CreditsService(repository).get_balance()
    task = service.submit(
        AvatarSubmitRequest(
            script_text="测试文案",
            avatar_id="avatar-1",
            voice_id="voice-1",
            aspect_ratio="9:16",
            rights_holder="测试公司",
            script_rights_confirmed=True,
            avatar_rights_confirmed=True,
            voice_rights_confirmed=True,
            idempotency_key="avatar-credit-1",
        ),
        avatar_name="形象",
        voice_name="音色",
    )
    credits = CreditsService(repository)
    # 4 个字按自然中文口播约每秒 4 字预留 1 秒，最终仍按真实成片时长结算。
    assert credits.get_balance() == initial - Decimal("0.05")
    txns = credits.list_transactions()
    assert Decimal(str(txns[0]["amount"])) == Decimal("-0.05")
    assert txns[0]["reason"] == "数字人视频生成费用"
    assert txns[0]["ref_type"] == "avatar"
    assert task.task_id


def test_avatar_submit_blocked_when_insufficient() -> None:
    from src.models import (
        AvatarAsset,
        AvatarAssetKind,
        AvatarCapability,
        AvatarSubmitRequest,
        ProviderMode,
    )
    from src.services.avatar import AvatarService

    class FakeProvider:
        def capabilities(self):
            return AvatarCapability(
                provider_name="fake-avatar",
                display_name="测试数字人",
                mode=ProviderMode.PRODUCTION,
                enabled=True,
                permission_status="authorized",
                max_script_chars=240,
                estimated_cost_cny=1.875,
                estimated_seconds=45,
            )

        def list_assets(self):
            return [
                AvatarAsset(
                    asset_id="avatar-1",
                    kind=AvatarAssetKind.AVATAR,
                    name="授权形象",
                    authorized=True,
                    status="ready",
                ),
                AvatarAsset(
                    asset_id="voice-1",
                    kind=AvatarAssetKind.VOICE,
                    name="授权音色",
                    authorized=True,
                    status="ready",
                ),
            ]

        def submit(self, request):
            raise AssertionError("余额不足时不得提交")

    repository = MockRepository(candidates=[], tasks=[])
    repository.adjust_credit_balance(amount=Decimal("-1000"), reason="清零")
    service = AvatarService(repository, FakeProvider())
    with pytest.raises(ValueError, match="积分不足"):
        service.submit(
            AvatarSubmitRequest(
                script_text="测试文案",
                avatar_id="avatar-1",
                voice_id="voice-1",
                aspect_ratio="9:16",
                rights_holder="测试公司",
                script_rights_confirmed=True,
                avatar_rights_confirmed=True,
                voice_rights_confirmed=True,
                idempotency_key="avatar-credit-blocked",
            ),
            avatar_name="形象",
            voice_name="音色",
        )
    # 任务未落库，重试不受幂等拦截
    assert not any(
        isinstance(item, AvatarTask) for item in repository.list_tasks()
    )


def test_crawler_execute_does_not_debit_credits_per_platform() -> None:
    from src.adapters.licensed import SandboxLicensedSearchProvider
    from src.models import Platform
    from src.services.commercial_search import CommercialSearchService

    class ChargingProvider(SandboxLicensedSearchProvider):
        def capabilities(self):
            cap = super().capabilities()
            return cap.model_copy(
                update={"mode": "production", "provider_name": "oneapi-test"}
            )

    repository = MockRepository(candidates=[], tasks=[])
    CreditsService(repository).credit("5", "测试充值")
    service = CommercialSearchService(
        repository,
        _SourceStub(),
        _TrendStub(repository),
        ChargingProvider(),
    )
    initial = CreditsService(repository).get_balance()
    service._endpoint_prices = lambda: {Platform.DOUYIN: 0.03}
    service.execute(
        keyword="测试",
        published_window_days=0,
        count=5,
        platforms=(Platform.DOUYIN,),
        force_refresh=True,
        schedule_recrawls=False,
    )
    credits = CreditsService(repository)
    assert credits.get_balance() == initial
    txns = credits.list_transactions()
    assert not any(txn["ref_type"] == "crawler" for txn in txns)


def test_crawler_execute_runs_when_credit_balance_is_empty() -> None:
    from src.adapters.licensed import SandboxLicensedSearchProvider
    from src.models import Platform, PlatformRunStatus
    from src.services.commercial_search import CommercialSearchService

    class ChargingProvider(SandboxLicensedSearchProvider):
        def capabilities(self):
            cap = super().capabilities()
            return cap.model_copy(
                update={"mode": "production", "provider_name": "oneapi-test"}
            )

    repository = MockRepository(candidates=[], tasks=[])
    repository.adjust_credit_balance(amount=Decimal("-1000"), reason="清零")
    service = CommercialSearchService(
        repository,
        _SourceStub(),
        _TrendStub(repository),
        ChargingProvider(),
    )
    service._endpoint_prices = lambda: {Platform.DOUYIN: 0.03}
    batch = service.execute(
        keyword="测试",
        published_window_days=0,
        count=5,
        platforms=(Platform.DOUYIN,),
        force_refresh=True,
        schedule_recrawls=False,
    )
    runs = repository.list_platform_search_runs(batch.batch_id)
    assert runs[0].status != PlatformRunStatus.BLOCKED
    assert "积分不足" not in (runs[0].error or "")
    assert CreditsService(repository).get_balance() == Decimal("0")


def test_copywriting_generate_debits_actual_tokens_after_combining_costs(
    monkeypatch,
) -> None:
    from src.adapters.llm import SandboxCopywritingEngine
    from src.services.copywriting import CopywritingService
    import src.services.pricing as pricing

    class CostingEngine(SandboxCopywritingEngine):
        def capabilities(self):
            cap = super().capabilities()
            return {**cap, "mode": "production"}

        def generate(self, **kwargs):
            result = super().generate(**kwargs)
            self.last_usage = {
                "prompt_tokens": 1000,
                "completion_tokens": 1000,
                "total_tokens": 2000,
            }
            return result

    monkeypatch.setattr(
        pricing,
        "_PRICE_CACHE",
        {
            "copywriting_input_cny_per_1k_tokens": Decimal("0.0015"),
            "copywriting_output_cny_per_1k_tokens": Decimal("0.003"),
        },
    )
    repository = MockRepository(candidates=[], tasks=[])
    service = CopywritingService(repository, CostingEngine())
    CreditsService(repository).credit("5", "测试充值")
    initial = CreditsService(repository).get_balance()
    task = service.generate(content_brief="测试内容概要")
    credits = CreditsService(repository)
    # 输入 0.0015 + 输出 0.003 = 0.0045；整次合计后进一为 0.01。
    assert credits.get_balance() == initial - Decimal("0.01")
    assert task.charged_credits == 0.01
    txns = credits.list_transactions()
    assert txns[0]["reason"] == "AI 文案生成费用（按 Token）"
    assert txns[0]["ref_type"] == "copywriting"


def test_copywriting_generate_blocked_when_insufficient() -> None:
    from src.adapters.llm import SandboxCopywritingEngine
    from src.models import TaskStatus
    from src.services.copywriting import CopywritingService

    class CostingEngine(SandboxCopywritingEngine):
        def capabilities(self):
            cap = super().capabilities()
            return {**cap, "mode": "production"}

    repository = MockRepository(candidates=[], tasks=[])
    repository.adjust_credit_balance(amount=Decimal("-1000"), reason="清零")
    service = CopywritingService(repository, CostingEngine())
    with pytest.raises(ValueError, match="积分不足"):
        service.generate(content_brief="测试内容概要")
    # 任务被标记为失败并说明原因
    tasks = [t for t in repository.list_tasks() if getattr(t, "creation_mode", None) == "generate"]
    assert tasks
    assert tasks[0].status == TaskStatus.FAILED
    assert "积分不足" in (tasks[0].error_message or "")


def test_centrally_billed_copywriting_never_uses_local_balance() -> None:
    from src.adapters.llm import SandboxCopywritingEngine
    from src.services.copywriting import CopywritingService
    from src.services.credits import set_current_owner

    class CentrallyBilledEngine(SandboxCopywritingEngine):
        billing_centrally_managed = True
        last_charged_credits = 0.08

        def capabilities(self):
            return {**super().capabilities(), "mode": "production"}

    repository = MockRepository(candidates=[], tasks=[])
    service = CopywritingService(repository, CentrallyBilledEngine())
    set_current_owner("remote-customer")
    initial = CreditsService(repository).get_balance()
    try:
        task = service.generate(content_brief="测试服务器统一计费")
    finally:
        set_current_owner("admin")
    assert CreditsService(repository).get_balance("remote-customer") == initial
    assert task.charged_credits == 0.08


class _SourceStub:
    def repository(self):
        return None


class _TrendStub:
    def __init__(self, repository):
        self.repository = repository

    def track_results(self, *args, **kwargs):
        return None


# ---------------------------------------------------------------------------
# 管理员默认积分（可通过 DEFAULT_CREDIT_BALANCE 调整）
# ---------------------------------------------------------------------------


def test_new_admin_account_defaults_to_99999(tmp_path) -> None:
    """新管理员积分账户默认余额 99999，无需任何操作即可看到。"""
    repository = SQLiteRepository(tmp_path / "default99999.db")
    service = CreditsService(repository)
    assert service.get_balance("admin") == Decimal("99999")


def test_first_adjust_opens_account_with_gift_transaction(tmp_path) -> None:
    """首次充值/扣费时自动开户：余额 = 99999 + 操作金额，流水含赠送记录。"""
    repository = SQLiteRepository(tmp_path / "gift.db")
    service = CreditsService(repository)
    balance = service.credit("10", "管理员充值", owner="admin")
    assert balance == Decimal("100009")
    txns = service.list_transactions("admin")
    assert len(txns) == 2
    assert txns[1]["amount"] == "99999"
    assert txns[1]["reason"] == "新用户默认赠送"
    assert txns[0]["amount"] == "10"


def test_default_balance_env_override(tmp_path, monkeypatch) -> None:
    """DEFAULT_CREDIT_BALANCE 可调（如改为 100）。"""
    monkeypatch.setenv("DEFAULT_CREDIT_BALANCE", "100")
    repository = SQLiteRepository(tmp_path / "env100.db")
    service = CreditsService(repository)
    assert service.get_balance("admin") == Decimal("100")


def test_default_balance_does_not_rewrite_existing_account(tmp_path) -> None:
    """已有账户（非赠送开户）余额不被默认值覆盖。"""
    repository = SQLiteRepository(tmp_path / "existing.db")
    service = CreditsService(repository)
    service.credit("10", "管理员充值", owner="admin")  # 开户 + 充值 → 100009
    reopened = CreditsService(SQLiteRepository(tmp_path / "existing.db"))
    assert reopened.get_balance("admin") == Decimal("100009")
