from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

_root = str(Path(__file__).resolve().parents[3])
if _root not in sys.path:
    sys.path.insert(0, _root)

from project.backend.app.api.v1 import crawler  # noqa: E402
from src.models import (  # noqa: E402
    CandidateCopyProbe,
    CandidateMatch,
    DataSource,
    HeatLevel,
    HeatResult,
    Platform,
    PlatformRunStatus,
    PlatformSearchRun,
    ProviderMode,
    SearchBatch,
    VideoCandidate,
    VideoMetricSnapshot,
)
from src.repositories import MockRepository  # noqa: E402


NOW = datetime(2026, 8, 1, 10, tzinfo=timezone.utc)


def _candidate(index: int) -> VideoCandidate:
    item_id = f"BVcopy{index:02d}"
    return VideoCandidate(
        video_id=item_id,
        platform_item_id=item_id,
        title=f"贴标机使用方法讲解 {index}",
        author_id=f"author-{index}",
        author_name="测试作者",
        platform=Platform.BILIBILI,
        category="贴标机",
        published_at=NOW,
        source_url=f"https://www.bilibili.com/video/{item_id}",
        source_type=DataSource.PUBLIC_RESEARCH,
        metrics=VideoMetricSnapshot(
            item_id=item_id,
            sampled_at=NOW,
            plays=1_000 - index,
            likes=100,
            confidence=1.0,
        ),
        heat=HeatResult(score=0, level=HeatLevel.INSUFFICIENT, confidence=1.0),
    )


def _low_heat_public_douyin_candidate() -> VideoCandidate:
    item_id = "douyin-public-copy-reference"
    return VideoCandidate(
        video_id=item_id,
        platform_item_id=item_id,
        title="贴标机怎么选，平面贴标机的选型避坑讲解",
        author_id="douyin-public-author",
        author_name="公开搜索作者",
        platform=Platform.DOUYIN,
        category="贴标机",
        published_at=NOW,
        source_url=f"https://www.douyin.com/video/{item_id}",
        source_type=DataSource.PUBLIC_RESEARCH,
        metrics=VideoMetricSnapshot(
            item_id=item_id,
            sampled_at=NOW,
            likes=6,
            confidence=0.55,
        ),
        evidence="douyin_public_search:关键词=贴标机怎么选;来源=browser_rendered;时长秒=35",
        heat=HeatResult(score=0, level=HeatLevel.INSUFFICIENT, confidence=0.55),
    )


def test_copy_search_matrix_keeps_intent_variants_bounded() -> None:
    matrix = crawler._copy_search_matrix("贴标机", ["贴标机教程", "贴标机教程"])

    assert matrix[:3] == ["贴标机", "贴标机教程", "贴标机讲解"]
    assert "贴标机应用案例" in matrix
    assert len(matrix) <= crawler.COPY_SEARCH_MATRIX_LIMIT


def test_public_douyin_evidence_keeps_its_source_label_and_duration() -> None:
    labels, duration_seconds, window_hours = crawler._hotspot_evidence_details(
        "douyin_public_search:关键词=贴标机;来源=browser_rendered;时长秒=48"
    )

    assert labels == ["抖音官网搜索"]
    assert duration_seconds == 48
    assert window_hours is None


def test_batch_response_only_promotes_detected_copy_to_primary_or_reserve() -> None:
    candidates = [_candidate(index) for index in range(1, 15)]
    repo = MockRepository(candidates=candidates, tasks=[])
    batch = SearchBatch(
        keyword="贴标机",
        requested_count_per_platform=30,
        provider="free_multi_platform",
        mode=ProviderMode.PUBLIC_WEB,
        platforms=[Platform.BILIBILI],
        copy_search_queries=["贴标机", "贴标机讲解"],
    )
    run = PlatformSearchRun(
        batch_id=batch.batch_id,
        platform=Platform.BILIBILI,
        provider="bilibili_local_browser",
        mode=ProviderMode.LOCAL_BROWSER,
        status=PlatformRunStatus.SUCCEEDED,
        requested_count=30,
        returned_count=len(candidates),
        idempotency_key="copy-quality-run",
        request_fingerprint="copy-quality-run",
        started_at=NOW,
        finished_at=NOW,
    )
    repo.save_search_batch(batch)
    repo.save_platform_search_run(run)
    for index, candidate in enumerate(candidates, start=1):
        repo.save_candidate_match(
            CandidateMatch(
                request_id=run.run_id,
                video_id=candidate.video_id,
                keyword="贴标机讲解",
                cohort_key="bilibili:贴标机讲解",
                platform=Platform.BILIBILI,
                platform_rank=index,
                observed_at=NOW,
            )
        )
        repo.save_candidate_copy_probe(
            CandidateCopyProbe(
                candidate_id=candidate.video_id,
                status="detected" if index <= 13 else "no_text",
                message="前3秒检测结果",
                checked_at=NOW,
            )
        )

    response = crawler._batch_to_response(batch, repo)
    visible = response.platform_runs[0].candidates
    by_id = {item.video_id: item for item in visible}

    assert response.copy_detected_count == 13
    assert response.copy_primary_count == 8
    assert response.copy_reserve_count == 4
    assert response.copy_probe_attempt_count == 14
    assert response.copy_queries_executed == 2
    assert [item.copy_pool_status for item in visible[:8]] == ["primary"] * 8
    assert [item.copy_pool_status for item in visible[8:12]] == ["reserve"] * 4
    assert by_id["BVcopy13"].copy_pool_status == "excluded"
    assert by_id["BVcopy14"].copy_pool_status == "excluded"
    assert "未识别到清晰文案" in (by_id["BVcopy14"].copy_rejection_reason or "")


def test_low_heat_public_douyin_reference_needs_detected_copy_before_visible() -> None:
    candidate = _low_heat_public_douyin_candidate()
    repo = MockRepository(candidates=[candidate], tasks=[])
    batch = SearchBatch(
        keyword="贴标机",
        requested_count_per_platform=20,
        provider="free_multi_platform",
        mode=ProviderMode.PUBLIC_WEB,
        platforms=[Platform.DOUYIN],
        copy_search_queries=["贴标机", "贴标机怎么选"],
    )
    run = PlatformSearchRun(
        batch_id=batch.batch_id,
        platform=Platform.DOUYIN,
        provider="douyin_public_browser_v2",
        mode=ProviderMode.LOCAL_BROWSER,
        status=PlatformRunStatus.SUCCEEDED,
        requested_count=20,
        returned_count=1,
        idempotency_key="public-copy-reference-run",
        request_fingerprint="public-copy-reference-run",
        started_at=NOW,
        finished_at=NOW,
    )
    repo.save_search_batch(batch)
    repo.save_platform_search_run(run)
    repo.save_candidate_match(
        CandidateMatch(
            request_id=run.run_id,
            video_id=candidate.video_id,
            keyword="贴标机怎么选",
            cohort_key="douyin-public:贴标机怎么选",
            platform=Platform.DOUYIN,
            provider_name="douyin_public_browser_v2",
            platform_rank=1,
            observed_at=NOW,
            evidence=candidate.evidence,
        )
    )

    assert not crawler._passes_main_board_heat_floor(candidate)
    assert [
        item[1].video_id for item in crawler._copy_probe_candidates(batch, repo)
    ] == [candidate.video_id]

    unprobed = crawler._batch_to_response(batch, repo)
    assert unprobed.platform_runs[0].candidates == []
    assert unprobed.platform_runs[0].result_state == "all_below_heat_floor"

    repo.save_candidate_copy_probe(
        CandidateCopyProbe(
            candidate_id=candidate.video_id,
            status="no_text",
            message="前3秒未识别到清晰文案。",
            checked_at=NOW,
        )
    )
    no_text = crawler._batch_to_response(batch, repo)
    assert no_text.platform_runs[0].candidates == []
    assert no_text.copy_detected_count == 0

    repo.save_candidate_copy_probe(
        CandidateCopyProbe(
            candidate_id=candidate.video_id,
            status="detected",
            message="前3秒检测到文案。",
            checked_at=NOW,
        )
    )
    detected = crawler._batch_to_response(batch, repo)
    visible = detected.platform_runs[0].candidates
    assert [item.video_id for item in visible] == [candidate.video_id]
    assert visible[0].copy_pool_status == "primary"
    assert crawler.COPY_REFERENCE_LOW_HEAT_WARNING in visible[0].data_quality_warnings
    assert detected.copy_detected_count == 1


def test_legacy_short_no_text_probe_is_widened_once_without_rechecking_detection() -> (
    None
):
    candidates = [_candidate(index) for index in range(1, 3)]
    repo = MockRepository(candidates=candidates, tasks=[])
    batch = SearchBatch(
        keyword="贴标机",
        requested_count_per_platform=30,
        provider="free_multi_platform",
        mode=ProviderMode.PUBLIC_WEB,
        platforms=[Platform.BILIBILI],
    )
    run = PlatformSearchRun(
        batch_id=batch.batch_id,
        platform=Platform.BILIBILI,
        provider="bilibili_local_browser",
        mode=ProviderMode.LOCAL_BROWSER,
        status=PlatformRunStatus.SUCCEEDED,
        requested_count=30,
        returned_count=2,
        idempotency_key="copy-widen-run",
        request_fingerprint="copy-widen-run",
        started_at=NOW,
        finished_at=NOW,
    )
    repo.save_search_batch(batch)
    repo.save_platform_search_run(run)
    for index, candidate in enumerate(candidates, start=1):
        repo.save_candidate_match(
            CandidateMatch(
                request_id=run.run_id,
                video_id=candidate.video_id,
                keyword="贴标机",
                cohort_key="bilibili:贴标机",
                platform=Platform.BILIBILI,
                platform_rank=index,
                observed_at=NOW,
            )
        )
    repo.save_candidate_copy_probe(
        CandidateCopyProbe(
            candidate_id=candidates[0].video_id,
            status="no_text",
            message="前3秒未识别到文案。",
            checked_at=NOW,
            version=crawler.COPY_PROBE_LEGACY_VERSION,
        )
    )

    assert crawler._has_legacy_no_text_probe(batch, repo)
    repo.save_candidate_copy_probe(
        CandidateCopyProbe(
            candidate_id=candidates[1].video_id,
            status="detected",
            message="前3秒检测到文案。",
            checked_at=NOW,
            version=crawler.COPY_PROBE_LEGACY_VERSION,
        )
    )

    class ProbeService:
        def __init__(self) -> None:
            self.probed_ids: list[str] = []

        def probe(self, candidate: VideoCandidate) -> CandidateCopyProbe:
            self.probed_ids.append(candidate.video_id)
            return CandidateCopyProbe(
                candidate_id=candidate.video_id,
                status="detected",
                message="前10秒检测到文案。",
                checked_at=NOW,
                version=crawler.COPY_PROBE_VERSION,
            )

    service = ProbeService()
    response = crawler.recheck_crawler_batch_legacy_no_text_copy(
        batch.batch_id,
        repo=repo,
        service=service,
    )

    assert service.probed_ids == [candidates[0].video_id]
    assert response.copy_detected_count == 2
    assert response.copy_probe_recheckable_count == 0
    assert (
        repo.get_candidate_copy_probe(candidates[0].video_id).version
        == crawler.COPY_PROBE_VERSION
    )
    assert not crawler._has_legacy_no_text_probe(batch, repo)

    crawler.recheck_crawler_batch_legacy_no_text_copy(
        batch.batch_id,
        repo=repo,
        service=service,
    )
    assert service.probed_ids == [candidates[0].video_id]
