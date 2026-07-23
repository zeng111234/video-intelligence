"""官方热榜/热点词 API 契约测试。

使用 fake 适配器与 MockRepository，不发起真实网络请求：
- /api/v1/crawler/capabilities 返回官方热榜/热点词配置状态（含开关关闭语义）
- /api/v1/crawler/batches 支持 mode="official_hot"
- /api/v1/crawler/official-hot/monitor 无关键词与有关键词路径、到期复爬
- /api/v1/crawler/hotwords 热点词建议
- /api/v1/crawler/candidates/{id}/original-script 三档字段
- 手机豆包建任务费用恒 0，缺前置条件时失败原因明确
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 确保项目根目录在 Python 路径中
_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from project.backend.app.main import app  # noqa: E402
from project.backend.app.core import deps as backend_deps  # noqa: E402
from project.backend.app.api.v1 import crawler as crawler_module  # noqa: E402
from src.adapters.licensed import SandboxLicensedSearchProvider  # noqa: E402
from src.adapters.llm import SandboxCopywritingEngine  # noqa: E402
from src.models import (  # noqa: E402
    DataSource,
    EligibilityStatus,
    HotWordRecord,
    NormalizedCandidate,
    Platform,
    SourceCapability,
    SourcePage,
    SourceRequest,
    VideoMetricSnapshot,
)
from src.repositories import MockRepository  # noqa: E402
from src.services import HeatService, KeywordTrendService, SourceService  # noqa: E402
from src.services.commercial_search import CommercialSearchService  # noqa: E402
from src.services.copywriting import CopywritingService  # noqa: E402
from src.services.doubao_browser import DoubaoMobileAutomationService  # noqa: E402
from src.services.hot_pool import (  # noqa: E402
    HOT_POOL_NO_MATCH_NOTICE,
    HOT_POOL_NO_MATCH_STATE,
    OfficialHotPoolService,
)


@dataclass
class FakeHotWordEntry:
    word: str
    hot_value: int | None
    fetched_at: datetime
    raw: dict = field(default_factory=dict)


class FakeBillboardAdapter:
    """按 DouyinHotBillboardAdapter 公开契约实现的 fake（无网络）。"""

    def __init__(self, items: list[NormalizedCandidate], enabled: bool = True) -> None:
        self.items = items
        self.enabled = enabled
        self.sync_calls = 0

    def capabilities(self) -> SourceCapability:
        return SourceCapability(
            provider_name="douyin_hot_billboard",
            enabled=self.enabled,
            permission_status=(
                "verified_fixture" if self.enabled else "credentials_missing"
            ),
            max_page_size=50,
            missing_configuration=(
                [] if self.enabled else ["DOUYIN_CLIENT_KEY", "DOUYIN_CLIENT_SECRET"]
            ),
        )

    def sync(self, request: SourceRequest) -> SourcePage:
        self.sync_calls += 1
        if not self.enabled:
            raise RuntimeError("官方热榜权限未开通")
        return SourcePage(items=list(self.items))


class FakeHotWordsAdapter:
    def __init__(self, entries: list[FakeHotWordEntry], enabled: bool = True) -> None:
        self.entries = entries
        self.enabled = enabled
        self.fetch_calls = 0

    def capabilities(self) -> SourceCapability:
        return SourceCapability(
            provider_name="douyin_hot_words",
            enabled=self.enabled,
            permission_status=(
                "verified_fixture" if self.enabled else "credentials_missing"
            ),
            missing_configuration=(
                [] if self.enabled else ["DOUYIN_CLIENT_KEY", "DOUYIN_CLIENT_SECRET"]
            ),
        )

    def fetch_hot_words(self) -> list[FakeHotWordEntry]:
        self.fetch_calls += 1
        return list(self.entries)


NOW = datetime(2026, 7, 23, 10, tzinfo=timezone.utc)


def _billboard_item(
    item_id: str,
    *,
    rank: int,
    title: str,
    hot_words: list[str] | None = None,
    sampled_at: datetime = NOW,
) -> NormalizedCandidate:
    share_url = f"https://www.iesdouyin.com/share/video/{item_id}/"
    evidence_payload = {
        "rank": rank,
        "hot_words": hot_words or [],
        "hot_value": 1_000_000 - rank,
        "play_count": 50_000,
        "share_url": share_url,
    }
    return NormalizedCandidate(
        platform_item_id=item_id,
        title=title,
        author_id=f"author-{item_id}",
        author_name=f"作者{item_id}",
        platform=Platform.DOUYIN,
        category="官方热榜",
        published_at=sampled_at - timedelta(hours=6),
        source_url=share_url,
        source_type=DataSource.OFFICIAL,
        metrics=VideoMetricSnapshot(
            item_id=item_id,
            sampled_at=sampled_at,
            likes=100,
            comments=5,
            confidence=1.0,
        ),
        matched_by=hot_words or [],
        cohort_key="douyin_hot_billboard:官方热榜",
        eligibility_status=EligibilityStatus.AUTO_MATCHED,
        evidence="official_billboard:" + json.dumps(evidence_payload, ensure_ascii=False),
        official_hot=True,
        official_rank=rank,
        official_hot_value=float(1_000_000 - rank),
    )


def _default_items(sampled_at: datetime = NOW) -> list[NormalizedCandidate]:
    return [
        _billboard_item("a1", rank=1, title="今天天气不错", sampled_at=sampled_at),
        _billboard_item(
            "a2",
            rank=2,
            title="日常 vlog",
            hot_words=["AI数字人", "口播获客"],
            sampled_at=sampled_at,
        ),
    ]


def _hot_word_entries() -> list[FakeHotWordEntry]:
    return [
        FakeHotWordEntry(
            word="AI数字人",
            hot_value=123456,
            fetched_at=NOW,
            raw={"sentence": "AI数字人", "hot_value": 123456},
        ),
        FakeHotWordEntry(word="口播获客", hot_value=None, fetched_at=NOW, raw={}),
    ]


def _build_hot_pool_service(
    repository: MockRepository,
    billboard,
    hot_words=None,
    clock=None,
) -> OfficialHotPoolService:
    return OfficialHotPoolService(
        repository,
        SourceService(repository, HeatService()),
        KeywordTrendService(repository),
        billboard_adapter=billboard,
        hot_words_adapter=hot_words,
        clock=clock,
    )


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def official_env():
    """装配 fake 官方适配器 + MockRepository，并注入依赖覆盖。"""
    repository = MockRepository(candidates=[], tasks=[])
    billboard = FakeBillboardAdapter(_default_items())
    hot_words = FakeHotWordsAdapter(_hot_word_entries())
    service = _build_hot_pool_service(repository, billboard, hot_words)
    provider = SandboxLicensedSearchProvider()
    commercial = CommercialSearchService(
        repository,
        SourceService(repository, HeatService()),
        KeywordTrendService(repository),
        provider,
        active_platforms=(Platform.DOUYIN,),
    )
    overrides = {
        backend_deps.get_repository: lambda: repository,
        backend_deps.get_official_hot_pool_service: lambda: service,
        backend_deps.get_official_hot_billboard_adapter: lambda: billboard,
        backend_deps.get_official_hot_words_adapter: lambda: hot_words,
        backend_deps.get_licensed_search_provider: lambda: provider,
        backend_deps.get_commercial_search_service: lambda: commercial,
    }
    app.dependency_overrides.update(overrides)
    yield {
        "repository": repository,
        "billboard": billboard,
        "hot_words": hot_words,
        "service": service,
    }
    for key in overrides:
        app.dependency_overrides.pop(key, None)


# ---------------------------------------------------------------------------
# capabilities：官方热榜/热点词配置状态
# ---------------------------------------------------------------------------


class TestOfficialCapabilities:
    def test_capabilities_reports_official_adapters(self, client, official_env):
        resp = client.get("/api/v1/crawler/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        billboard = data["official_hot_billboard"]
        assert billboard["enabled"] is True
        assert billboard["provider_name"] == "douyin_hot_billboard"
        assert billboard["missing_configuration"] == []
        hot_words = data["official_hot_words"]
        assert hot_words["enabled"] is True
        assert hot_words["provider_name"] == "douyin_hot_words"
        assert hot_words["missing_configuration"] == []

    def test_capabilities_reports_missing_credentials(self, client, official_env):
        disabled_billboard = FakeBillboardAdapter([], enabled=False)
        app.dependency_overrides[backend_deps.get_official_hot_billboard_adapter] = (
            lambda: disabled_billboard
        )
        try:
            resp = client.get("/api/v1/crawler/capabilities")
        finally:
            app.dependency_overrides.pop(
                backend_deps.get_official_hot_billboard_adapter, None
            )
        assert resp.status_code == 200
        billboard = resp.json()["official_hot_billboard"]
        assert billboard["enabled"] is False
        assert "DOUYIN_CLIENT_KEY" in billboard["missing_configuration"]

    def test_capabilities_reports_flag_disabled(self, client, official_env):
        """开关为 false（适配器为 None）时报 enabled=False，且不构造适配器。"""
        app.dependency_overrides[backend_deps.get_official_hot_billboard_adapter] = (
            lambda: None
        )
        app.dependency_overrides[backend_deps.get_official_hot_words_adapter] = (
            lambda: None
        )
        try:
            resp = client.get("/api/v1/crawler/capabilities")
        finally:
            app.dependency_overrides.pop(
                backend_deps.get_official_hot_billboard_adapter, None
            )
            app.dependency_overrides.pop(
                backend_deps.get_official_hot_words_adapter, None
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["official_hot_billboard"]["enabled"] is False
        assert data["official_hot_billboard"]["missing_configuration"] == [
            "DOUYIN_OFFICIAL_HOT_ENABLED"
        ]
        assert data["official_hot_billboard"]["provider_name"] == "douyin_hot_billboard"
        assert data["official_hot_words"]["enabled"] is False
        assert data["official_hot_words"]["missing_configuration"] == [
            "DOUYIN_HOT_WORDS_ENABLED"
        ]


# ---------------------------------------------------------------------------
# batches：official_hot 模式
# ---------------------------------------------------------------------------


class TestOfficialHotBatches:
    def test_batch_official_hot_mode_matches(self, client, official_env):
        resp = client.post(
            "/api/v1/crawler/batches",
            json={
                "keyword": "天气",
                "published_window_days": 1,
                "count_per_platform": 5,
                "force_refresh": False,
                "mode": "official_hot",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "official_hot"
        assert data["status"] == "succeeded"
        assert data["total_estimated_cost_cny"] == 0.0
        assert data["total_api_calls"] == 0
        runs = data["platform_runs"]
        assert len(runs) == 1
        run = runs[0]
        assert run["platform"] == "douyin"
        assert run["mode"] == "official_hot"
        assert run["result_state"] == "官方热榜匹配"
        assert run["returned_count"] == 1
        candidate = run["candidates"][0]
        assert candidate["video_id"] == "douyin-a1"
        assert candidate["growth_stage"] == "观察样本"
        assert candidate["snapshot_count"] is not None
        # 分享/收藏未返回时保持 null，不进入 0
        assert candidate["share_count"] is None
        assert candidate["collect_count"] is None
        assert candidate["provider_hot_rank"] == 1

    def test_batch_official_hot_no_match_state(self, client, official_env):
        resp = client.post(
            "/api/v1/crawler/batches",
            json={
                "keyword": "冷门关键词",
                "published_window_days": 1,
                "count_per_platform": 5,
                "force_refresh": False,
                "mode": "official_hot",
            },
        )
        assert resp.status_code == 200
        run = resp.json()["platform_runs"][0]
        assert run["result_state"] == HOT_POOL_NO_MATCH_STATE
        assert run["payload_diagnostic"] == HOT_POOL_NO_MATCH_NOTICE
        assert run["candidates"] == []

    def test_batch_default_mode_still_works(self, client, official_env):
        resp = client.post(
            "/api/v1/crawler/batches",
            json={
                "keyword": "二手车",
                "published_window_days": 7,
                "count_per_platform": 2,
                "force_refresh": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["mode"] == "sandbox"

    def test_smart_mode_returns_free_candidates_without_fallback(self, client, official_env):
        repository = official_env["repository"]
        billboard = FakeBillboardAdapter(
            [
                _billboard_item("s1", rank=1, title="企业获客案例一"),
                _billboard_item("s2", rank=2, title="企业获客案例二"),
                _billboard_item("s3", rank=3, title="企业获客案例三"),
            ]
        )
        service = _build_hot_pool_service(repository, billboard, official_env["hot_words"])
        app.dependency_overrides[backend_deps.get_official_hot_pool_service] = (
            lambda: service
        )
        try:
            resp = client.post(
                "/api/v1/crawler/batches",
                json={
                    "keyword": "企业获客",
                    "published_window_days": 0,
                    "count_per_platform": 10,
                    "force_refresh": False,
                    "mode": "smart",
                },
            )
        finally:
            app.dependency_overrides[backend_deps.get_official_hot_pool_service] = (
                lambda: official_env["service"]
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "smart"
        assert data["free_candidate_count"] == 3
        assert data["paid_fallback_used"] is False
        assert data["total_api_calls"] == 0
        assert data["total_estimated_cost_cny"] == 0.0

    def test_batch_unknown_mode_rejected(self, client, official_env):
        resp = client.post(
            "/api/v1/crawler/batches",
            json={
                "keyword": "二手车",
                "published_window_days": 7,
                "count_per_platform": 2,
                "force_refresh": False,
                "mode": "crawler_x",
            },
        )
        assert resp.status_code == 400

    def test_delete_historical_batch_keeps_candidates(self, client, official_env):
        created = client.post(
            "/api/v1/crawler/batches",
            json={
                "keyword": "二手车",
                "published_window_days": 7,
                "count_per_platform": 2,
                "force_refresh": False,
            },
        )
        assert created.status_code == 200
        created_data = created.json()
        batch_id = created_data["batch_id"]
        candidate_id = created_data["platform_runs"][0]["candidates"][0]["video_id"]
        assert official_env["repository"].get_search_batch(batch_id) is not None

        deleted = client.delete(f"/api/v1/crawler/batches/{batch_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"batch_id": batch_id, "deleted": True}
        assert client.get(f"/api/v1/crawler/batches/{batch_id}").status_code == 404
        assert official_env["repository"].get_candidate(candidate_id) is not None


# ---------------------------------------------------------------------------
# official-hot/monitor：无关键词、有关键词、到期复爬
# ---------------------------------------------------------------------------


class TestOfficialHotMonitor:
    def test_monitor_without_keyword_syncs_billboard_and_hot_words(
        self, client, official_env
    ):
        resp = client.post("/api/v1/crawler/official-hot/monitor", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched_count"] == 2
        assert data["executed_recrawls"] == 0
        assert data["next_recrawl_at"] is None
        assert "热榜" in data["result_message"]
        assert "热点词" in data["result_message"]
        assert len(data["candidates"]) == 2
        # 全量同步后热点词已持久化，hotwords 端点可直接读取
        words_resp = client.get("/api/v1/crawler/hotwords")
        assert words_resp.status_code == 200
        words = words_resp.json()["words"]
        assert [item["word"] for item in words] == ["AI数字人", "口播获客"]
        assert words[0]["hot_value"] == 123456
        assert words[0]["fetched_at"]
        assert words[1]["hot_value"] is None

    def test_monitor_with_keyword_match(self, client, official_env):
        resp = client.post(
            "/api/v1/crawler/official-hot/monitor",
            json={"keyword": "数字人"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched_count"] == 1
        assert data["result_state"] == "官方热榜匹配"
        candidate = data["candidates"][0]
        assert candidate["video_id"] == "douyin-a2"
        assert candidate["growth_stage"] == "观察样本"
        assert data["next_recrawl_at"] is not None

    def test_monitor_with_keyword_no_match(self, client, official_env):
        resp = client.post(
            "/api/v1/crawler/official-hot/monitor",
            json={"keyword": "冷门关键词"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched_count"] == 0
        assert data["result_state"] == HOT_POOL_NO_MATCH_STATE
        assert data["result_message"] == HOT_POOL_NO_MATCH_NOTICE
        assert "不代表抖音搜索无视频" in data["result_message"]
        assert data["candidates"] == []

    def test_monitor_executes_due_recrawls_and_refreshes_ranking(
        self, client, official_env
    ):
        # 让候选快照采样时间早于 2h 复爬点，第二轮监测时复爬立即到期
        past = NOW - timedelta(hours=3)
        repository = official_env["repository"]
        billboard = FakeBillboardAdapter(_default_items(sampled_at=past))
        service = _build_hot_pool_service(
            repository, billboard, official_env["hot_words"], clock=lambda: NOW
        )
        app.dependency_overrides[backend_deps.get_official_hot_pool_service] = (
            lambda: service
        )
        app.dependency_overrides[backend_deps.get_official_hot_billboard_adapter] = (
            lambda: billboard
        )
        try:
            first = client.post(
                "/api/v1/crawler/official-hot/monitor",
                json={"keyword": "天气"},
            )
            assert first.status_code == 200
            assert first.json()["executed_recrawls"] == 0
            assert first.json()["matched_count"] == 1

            second = client.post(
                "/api/v1/crawler/official-hot/monitor",
                json={"keyword": "天气"},
            )
            assert second.status_code == 200
            data = second.json()
            assert data["executed_recrawls"] == 1
            assert data["matched_count"] == 1
            candidate = data["candidates"][0]
            assert candidate["growth_stage"] in {
                "观察样本",
                "增长确认中",
                "热门候选",
                "爆发候选",
            }
            # 排行（trend）已重算并回填到候选
            assert candidate["trend_score"] is not None
        finally:
            app.dependency_overrides.pop(
                backend_deps.get_official_hot_pool_service, None
            )
            app.dependency_overrides.pop(
                backend_deps.get_official_hot_billboard_adapter, None
            )

    def test_recrawls_due_endpoint_runs(self, client, official_env):
        resp = client.post("/api/v1/crawler/recrawls/due")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 0
        assert isinstance(data["executed_batches"], list)


# ---------------------------------------------------------------------------
# hotwords 端点
# ---------------------------------------------------------------------------


class TestHotWordsEndpoint:
    def test_hotwords_reads_saved_records(self, client, official_env):
        repository = official_env["repository"]
        repository.save_hot_words(
            [
                HotWordRecord(word="储能", hot_value=8888, fetched_at=NOW),
                HotWordRecord(word="露营", hot_value=None, fetched_at=NOW),
            ]
        )
        resp = client.get("/api/v1/crawler/hotwords")
        assert resp.status_code == 200
        words = resp.json()["words"]
        assert [item["word"] for item in words] == ["储能", "露营"]
        assert words[0]["hot_value"] == 8888
        assert words[1]["hot_value"] is None

    def test_hotwords_empty_without_adapter(self, client, official_env):
        """热点词开关关闭（适配器 None）时返回空列表与原因，不报 500。"""
        repository = MockRepository(candidates=[], tasks=[])
        service = _build_hot_pool_service(
            repository, FakeBillboardAdapter([]), hot_words=None
        )
        app.dependency_overrides[backend_deps.get_official_hot_pool_service] = (
            lambda: service
        )
        try:
            resp = client.get("/api/v1/crawler/hotwords")
        finally:
            app.dependency_overrides.pop(
                backend_deps.get_official_hot_pool_service, None
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["words"] == []
        assert data["error"]


# ---------------------------------------------------------------------------
# original-script 端点（三档字段）
# ---------------------------------------------------------------------------


class TestOriginalScript:
    @pytest.fixture()
    def script_env(self, official_env):
        repository = official_env["repository"]
        official_env["service"].sync_billboard()
        copywriting = CopywritingService(repository, SandboxCopywritingEngine())
        app.dependency_overrides[backend_deps.get_copywriting_service] = (
            lambda: copywriting
        )
        yield official_env
        app.dependency_overrides.pop(backend_deps.get_copywriting_service, None)

    def test_original_script_contract(self, client, script_env):
        resp = client.post("/api/v1/crawler/candidates/douyin-a2/original-script")
        assert resp.status_code == 200
        data = resp.json()
        assert data["copy_source"] == "metadata_original"
        assert data["is_original_transcript"] is False
        assert data["needs_manual_review"] is True
        assert isinstance(data["script"], str) and data["script"]

    def test_original_script_unknown_candidate(self, client, script_env):
        resp = client.post("/api/v1/crawler/candidates/unknown-id/original-script")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 手机豆包：费用恒 0 + 缺前置条件失败原因明确
# ---------------------------------------------------------------------------


class TestDoubaoMobile:
    @pytest.fixture()
    def mobile_env(self, official_env, monkeypatch: pytest.MonkeyPatch):
        repository = official_env["repository"]
        official_env["service"].sync_billboard()
        mobile_service = DoubaoMobileAutomationService(repository)
        app.dependency_overrides[backend_deps.get_doubao_mobile_service] = (
            lambda: mobile_service
        )
        # 强制前置条件全部缺失：无 adb、无包名、Appium 不可达
        monkeypatch.delenv("ADB_PATH", raising=False)
        monkeypatch.delenv("DOUBAO_ANDROID_PACKAGE", raising=False)
        monkeypatch.setenv("DOUBAO_MOBILE_APPIUM_URL", "http://127.0.0.1:9")
        monkeypatch.setattr(crawler_module.shutil, "which", lambda _name: None)

        def _appium_unreachable(*args, **kwargs):
            raise crawler_module.httpx.ConnectError("connection refused")

        monkeypatch.setattr(crawler_module.httpx, "get", _appium_unreachable)
        yield official_env
        app.dependency_overrides.pop(backend_deps.get_doubao_mobile_service, None)

    def test_mobile_capabilities_prerequisites(self, client, mobile_env):
        resp = client.get("/api/v1/crawler/doubao-mobile/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["estimated_cost_cny"] == 0.0
        prerequisites = data["prerequisites"]
        assert prerequisites["adb"] is False
        assert prerequisites["appium_url"] is False
        assert prerequisites["package"] is False
        assert prerequisites["device_ready"] is False
        assert any("DOUBAO_ANDROID_PACKAGE" in item for item in data["missing_configuration"])

    def test_create_mobile_job_zero_fee_and_explicit_failure(
        self, client, mobile_env
    ):
        resp = client.post(
            "/api/v1/crawler/doubao-mobile/jobs",
            json={"candidate_ids": ["douyin-a1"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        job = data["items"][0]
        # 费用恒 0
        assert job["fee_cny"] == 0.0
        # 缺前置条件：任务直接失败并写明具体缺失项
        assert job["status"] == "failed"
        assert "DOUBAO_ANDROID_PACKAGE" in job["error_message"]
        assert "ADB" in job["error_message"]
        assert "Appium" in job["error_message"]
