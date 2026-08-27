from __future__ import annotations

import asyncio
from concurrent.futures import Future

from project.backend.app.api.v1 import crawler
from src.models import (
    CrawlerKeywordQueue,
    CrawlerKeywordQueueItem,
    KeywordQueueItemStatus,
    KeywordQueueStatus,
    Platform,
    PlatformSearchRun,
    ProviderMode,
    SearchBatch,
)
from src.repositories.sqlite import SQLiteRepository


def test_platform_run_accepts_funnel_counts_over_return_limit() -> None:
    run = PlatformSearchRun(
        batch_id="batch-funnel-200",
        platform=Platform.DOUYIN,
        provider="douyin_public_browser_v2",
        mode=ProviderMode.LOCAL_BROWSER,
        idempotency_key="funnel-200-key",
        request_fingerprint="funnel-200-fingerprint",
        requested_count=30,
        returned_count=18,
        raw_item_count=200,
        parsed_item_count=200,
        irrelevant_count=182,
    )

    assert run.raw_item_count == 200
    assert run.irrelevant_count == 182


def test_keyword_queue_parser_deduplicates_and_limits() -> None:
    assert crawler._parse_keyword_queue("餐饮获客\n餐饮获客, 门店短视频；老板IP") == [
        "餐饮获客",
        "门店短视频",
        "老板IP",
    ]


def test_failed_batch_gets_a_non_empty_queue_diagnostic() -> None:
    failed = crawler.CrawlerBatchResponse(
        batch_id="batch-failed-empty-detail",
        keyword="美业源头工厂",
        published_window_days=0,
        count_per_platform=30,
        provider="free_multi_platform",
        mode="local_browser",
        status="failed",
        force_refresh=False,
        platform_runs=[
            crawler.CrawlerPlatformRunResponse(
                run_id="run-douyin-failed",
                platform="douyin",
                platform_label="抖音",
                provider="douyin_public_browser",
                mode="local_browser",
                status="failed",
                requested_count=30,
                returned_count=0,
                cache_hit=False,
                api_call_count=0,
                errors=[{"code": "public_search_verification", "message": "抖音需要你确认一次"}],
            ),
            crawler.CrawlerPlatformRunResponse(
                run_id="run-bilibili-failed",
                platform="bilibili",
                platform_label="B站",
                provider="bilibili_local_browser",
                mode="local_browser",
                status="failed",
                requested_count=30,
                returned_count=0,
                cache_hit=False,
                api_call_count=0,
            ),
        ],
    )

    message = crawler._crawler_batch_failure_message(failed)

    assert "抖音：抖音需要你确认一次" in message
    assert "B站暂未完成搜索" in message
    assert message
    failed_without_runs = failed.model_copy(update={"platform_runs": [], "error": None})
    assert crawler._crawler_batch_failure_message(failed_without_runs) == "本次找素材未完成，请检查平台状态后再试。"


def test_keyword_queue_persists_in_sqlite(tmp_path) -> None:
    repo = SQLiteRepository(tmp_path / "crawler-queue.db")
    queue = CrawlerKeywordQueue(
        items=[CrawlerKeywordQueueItem(keyword="门店短视频")],
        platforms=[Platform.BILIBILI],
    )
    repo.save_crawler_keyword_queue(queue)
    restored = repo.get_crawler_keyword_queue(queue.queue_id)
    assert restored is not None
    assert restored.items[0].keyword == "门店短视频"
    assert restored.platforms == [Platform.BILIBILI]


def test_keyword_queue_submission_persists_before_worker_runs(monkeypatch, tmp_path) -> None:
    repo = SQLiteRepository(tmp_path / "crawler-submit.db")
    started: list[str] = []
    monkeypatch.setattr(crawler, "_start_crawler_keyword_queue", started.append)

    response = crawler.create_crawler_keyword_queue(
        crawler.CrawlerKeywordQueueRequest(
            keywords="门店短视频",
            platforms=["bilibili"],
            count_per_platform=30,
        ),
        repo,
    )

    assert response.status == "queued"
    assert response.items[0].progress_stage == "queued"
    assert started == [response.queue_id]
    assert repo.get_crawler_keyword_queue(response.queue_id) is not None


def test_keyword_queue_worker_passes_only_current_batch_dependencies(monkeypatch, tmp_path) -> None:
    repo = SQLiteRepository(tmp_path / "crawler-worker-wiring.db")
    queue = CrawlerKeywordQueue(
        items=[CrawlerKeywordQueueItem(keyword="抖音测试")],
        platforms=[Platform.DOUYIN],
        requested_count_per_platform=30,
    )
    repo.save_crawler_keyword_queue(queue)
    monkeypatch.setattr(crawler, "get_repository", lambda: repo)

    services = {
        "douyin_public_service": object(),
        "douyin_public_provider": object(),
        "bilibili_service": object(),
        "bilibili_provider": object(),
        "bilibili_metrics_provider": object(),
        "xiaohongshu_service": object(),
        "xiaohongshu_provider": object(),
        "kuaishou_service": object(),
        "kuaishou_provider": object(),
    }
    getter_names = {
        "douyin_public_service": "get_douyin_public_search_service",
        "douyin_public_provider": "get_douyin_public_browser_provider",
        "bilibili_service": "get_bilibili_browser_search_service",
        "bilibili_provider": "get_bilibili_browser_provider",
        "bilibili_metrics_provider": "get_bilibili_public_metrics_provider",
        "xiaohongshu_service": "get_xiaohongshu_browser_search_service",
        "xiaohongshu_provider": "get_xiaohongshu_browser_provider",
        "kuaishou_service": "get_kuaishou_browser_search_service",
        "kuaishou_provider": "get_kuaishou_browser_provider",
    }
    for key, getter_name in getter_names.items():
        monkeypatch.setattr(crawler, getter_name, lambda value=services[key]: value)

    called = False

    def fake_execute(
        body,
        bilibili_service,
        bilibili_provider,
        bilibili_metrics_provider,
        douyin_public_service,
        douyin_public_provider,
        xiaohongshu_service,
        xiaohongshu_provider,
        kuaishou_service,
        kuaishou_provider,
        repo,
        progress_callback=None,
    ):
        nonlocal called
        called = True
        assert body.keyword == "抖音测试"
        assert progress_callback is not None
        assert {
            "bilibili_service": bilibili_service,
            "bilibili_provider": bilibili_provider,
            "bilibili_metrics_provider": bilibili_metrics_provider,
            "douyin_public_service": douyin_public_service,
            "douyin_public_provider": douyin_public_provider,
            "xiaohongshu_service": xiaohongshu_service,
            "xiaohongshu_provider": xiaohongshu_provider,
            "kuaishou_service": kuaishou_service,
            "kuaishou_provider": kuaishou_provider,
        } == services
        # A worker callback may observe a provisional batch before the service
        # has persisted it. That ID must never leak into the durable queue.
        progress_callback(
            "platform_complete",
            "抖音已扫描 1 条，解析 1 条，保留 1 条。",
            SearchBatch(
                batch_id="batch-provisional-not-saved",
                keyword=body.keyword,
                provider="free_multi_platform",
                mode=ProviderMode.LOCAL_BROWSER,
                platforms=[Platform.DOUYIN],
            ),
        )
        repo.save_search_batch(
            SearchBatch(
                batch_id="batch-douyin-wire",
                keyword=body.keyword,
                provider="free_multi_platform",
                mode=ProviderMode.LOCAL_BROWSER,
                platforms=[Platform.DOUYIN],
            )
        )
        return crawler.CrawlerBatchResponse(
            batch_id="batch-douyin-wire",
            keyword=body.keyword,
            published_window_days=body.published_window_days,
            count_per_platform=body.count_per_platform,
            provider="free_multi_platform",
            mode="local_browser",
            status="succeeded",
            force_refresh=False,
            total_candidates=0,
        )

    monkeypatch.setattr(crawler, "_execute_free_multi_platform_batch", fake_execute)

    crawler._run_crawler_keyword_queue(queue.queue_id)

    restored = repo.get_crawler_keyword_queue(queue.queue_id)
    assert called is True
    assert restored is not None
    assert restored.status == KeywordQueueStatus.SUCCEEDED
    assert restored.items[0].status == KeywordQueueItemStatus.SUCCEEDED
    assert restored.items[0].batch_id == "batch-douyin-wire"
    assert restored.items[0].partial_batch_ids == ["batch-douyin-wire"]


def test_keyword_queue_response_exposes_resume_safe_progress() -> None:
    queue = CrawlerKeywordQueue(
        items=[
            CrawlerKeywordQueueItem(
                keyword="已完成",
                status=KeywordQueueItemStatus.SUCCEEDED,
                batch_id="batch-1",
                partial_batch_ids=["batch-1", "batch-2"],
                progress_stage="completed",
                progress_message="已整理最终结果。",
                scanned_count=86,
                parsed_count=30,
                retained_count=12,
            ),
            CrawlerKeywordQueueItem(keyword="等待中"),
            CrawlerKeywordQueueItem(
                keyword="失败", status=KeywordQueueItemStatus.FAILED, error="规则可能失效"
            ),
        ],
        platforms=[Platform.DOUYIN],
        status=KeywordQueueStatus.PARTIAL,
    )
    response = crawler._crawler_queue_response(queue)
    assert (response.total, response.completed, response.queued, response.failed) == (3, 1, 1, 1)
    assert response.items[0].batch_id == "batch-1"
    assert response.items[0].partial_batch_ids == ["batch-1", "batch-2"]
    assert response.items[0].progress_message == "已整理最终结果。"
    assert (response.items[0].scanned_count, response.items[0].retained_count) == (86, 12)
    assert response.items[2].error == "规则可能失效"
    assert response.worker_active is False


def test_keyword_queue_ignores_orphan_and_cross_queue_batch_ids(tmp_path) -> None:
    repo = SQLiteRepository(tmp_path / "crawler-orphan-batches.db")
    queue = CrawlerKeywordQueue(
        queue_id="queue-orphan-check",
        items=[
            CrawlerKeywordQueueItem(
                keyword="ai获客",
                status=KeywordQueueItemStatus.PARTIAL,
                batch_id="batch-present",
                partial_batch_ids=["batch-present", "batch-missing", "batch-other"],
            )
        ],
        platforms=[Platform.BILIBILI],
        status=KeywordQueueStatus.PARTIAL,
    )
    repo.save_search_batch(
        SearchBatch(
            batch_id="batch-present",
            keyword="ai获客",
            provider="free_multi_platform",
            mode=ProviderMode.LOCAL_BROWSER,
            platforms=[Platform.BILIBILI],
            crawler_queue_id=queue.queue_id,
        )
    )
    repo.save_search_batch(
        SearchBatch(
            batch_id="batch-other",
            keyword="ai获客",
            provider="free_multi_platform",
            mode=ProviderMode.LOCAL_BROWSER,
            platforms=[Platform.BILIBILI],
            crawler_queue_id="another-queue",
        )
    )
    repo.save_crawler_keyword_queue(queue)

    response = crawler._crawler_queue_response(queue, repo)

    assert response.items[0].batch_id == "batch-present"
    assert response.items[0].partial_batch_ids == ["batch-present"]
    restored = repo.get_crawler_keyword_queue(queue.queue_id)
    assert restored is not None
    assert restored.items[0].partial_batch_ids == ["batch-present"]


def test_keyword_queue_persists_incremental_candidates_in_discovery_order(monkeypatch, tmp_path) -> None:
    repo = SQLiteRepository(tmp_path / "crawler-incremental-candidates.db")
    queue = CrawlerKeywordQueue(
        items=[CrawlerKeywordQueueItem(keyword="渐进候选")],
        platforms=[Platform.DOUYIN],
    )
    repo.save_crawler_keyword_queue(queue)
    monkeypatch.setattr(crawler, "get_repository", lambda: repo)

    getter_names = {
        "douyin_public_service": "get_douyin_public_search_service",
        "douyin_public_provider": "get_douyin_public_browser_provider",
        "bilibili_service": "get_bilibili_browser_search_service",
        "bilibili_provider": "get_bilibili_browser_provider",
        "bilibili_metrics_provider": "get_bilibili_public_metrics_provider",
        "xiaohongshu_service": "get_xiaohongshu_browser_search_service",
        "xiaohongshu_provider": "get_xiaohongshu_browser_provider",
        "kuaishou_service": "get_kuaishou_browser_search_service",
        "kuaishou_provider": "get_kuaishou_browser_provider",
    }
    for getter_name in getter_names.values():
        monkeypatch.setattr(crawler, getter_name, lambda: object())

    candidate_one = crawler.CrawlerCandidateResult(
        video_id="candidate-1",
        title="第一条",
        author_name="作者1",
        platform="douyin",
        platform_label="抖音",
    )
    candidate_two = crawler.CrawlerCandidateResult(
        video_id="candidate-2",
        title="第二条",
        author_name="作者2",
        platform="douyin",
        platform_label="抖音",
    )
    candidate_two_updated = candidate_two.model_copy(
        update={"video_id": "douyin-candidate-2", "title": "第二条补全"}
    )

    def fake_execute(
        body,
        bilibili_service,
        bilibili_provider,
        bilibili_metrics_provider,
        douyin_public_service,
        douyin_public_provider,
        xiaohongshu_service,
        xiaohongshu_provider,
        kuaishou_service,
        kuaishou_provider,
        repo,
        progress_callback=None,
    ):
        repo.save_search_batch(
            SearchBatch(
                batch_id="batch-incremental",
                keyword=body.keyword,
                provider="free_multi_platform",
                mode=ProviderMode.LOCAL_BROWSER,
                platforms=[Platform.DOUYIN],
            )
        )
        assert progress_callback is not None
        progress_callback("candidate_found", "已保留 1 条", None, candidate_one)
        progress_callback("candidate_found", "已保留 2 条", None, candidate_two)
        progress_callback("candidate_found", "已保留 2 条", None, candidate_two_updated)
        return crawler.CrawlerBatchResponse(
            batch_id="batch-incremental",
            keyword=body.keyword,
            published_window_days=body.published_window_days,
            count_per_platform=body.count_per_platform,
            provider="free_multi_platform",
            mode="local_browser",
            status="succeeded",
            force_refresh=False,
            total_candidates=2,
        )

    monkeypatch.setattr(crawler, "_execute_free_multi_platform_batch", fake_execute)

    crawler._run_crawler_keyword_queue(queue.queue_id)

    restored = repo.get_crawler_keyword_queue(queue.queue_id)
    assert restored is not None
    assert [item["video_id"] for item in restored.items[0].progress_candidates] == [
        "candidate-1",
        "candidate-2",
    ]
    assert restored.items[0].progress_candidates[1]["title"] == "第二条补全"
    assert restored.items[0].retained_count == 2


def test_stale_running_queue_is_marked_recoverable() -> None:
    queue = CrawlerKeywordQueue(
        items=[CrawlerKeywordQueueItem(keyword="中断关键词", status=KeywordQueueItemStatus.RUNNING)],
        platforms=[Platform.BILIBILI],
        status=KeywordQueueStatus.RUNNING,
    )

    response = crawler._crawler_queue_response(queue)

    assert response.worker_active is False


def test_item_result_does_not_overwrite_pause_state(tmp_path) -> None:
    repo = SQLiteRepository(tmp_path / "crawler-pause-race.db")
    queue = CrawlerKeywordQueue(
        items=[CrawlerKeywordQueueItem(keyword="正在搜索", status=KeywordQueueItemStatus.RUNNING)],
        platforms=[Platform.BILIBILI],
        status=KeywordQueueStatus.RUNNING,
    )
    repo.save_crawler_keyword_queue(queue)
    repo.save_crawler_keyword_queue(
        queue.model_copy(update={"status": KeywordQueueStatus.PAUSED})
    )

    crawler._save_crawler_queue_item_result(
        repo,
        queue.queue_id,
        queue,
        queue.items[0].model_copy(
            update={
                "status": KeywordQueueItemStatus.SUCCEEDED,
                "batch_id": "batch-after-pause",
            }
        ),
    )

    restored = repo.get_crawler_keyword_queue(queue.queue_id)
    assert restored is not None
    assert restored.status == KeywordQueueStatus.PAUSED
    assert restored.items[0].status == KeywordQueueItemStatus.SUCCEEDED


def test_item_progress_persists_without_overwriting_pause_state(tmp_path) -> None:
    repo = SQLiteRepository(tmp_path / "crawler-progress.db")
    queue = CrawlerKeywordQueue(
        items=[CrawlerKeywordQueueItem(keyword="正在扫描", status=KeywordQueueItemStatus.RUNNING)],
        platforms=[Platform.BILIBILI],
        status=KeywordQueueStatus.RUNNING,
    )
    repo.save_crawler_keyword_queue(queue)

    crawler._save_crawler_queue_item_progress(
        repo,
        queue.queue_id,
        queue.items[0].item_id,
        progress_stage="platform_complete",
        progress_message="B站已扫描 86 条，解析 30 条，保留 12 条。",
        batch_id="batch-progress",
        partial_batch_ids=["batch-progress"],
        scanned_count=86,
        parsed_count=30,
        retained_count=12,
    )

    restored = repo.get_crawler_keyword_queue(queue.queue_id)
    assert restored is not None
    item = restored.items[0]
    assert item.progress_stage == "platform_complete"
    assert item.partial_batch_ids == ["batch-progress"]
    assert (item.scanned_count, item.parsed_count, item.retained_count) == (86, 30, 12)


def test_resume_keeps_active_item_running_to_avoid_duplicate_batch(monkeypatch, tmp_path) -> None:
    repo = SQLiteRepository(tmp_path / "crawler-resume.db")
    queue = CrawlerKeywordQueue(
        items=[
            CrawlerKeywordQueueItem(
                keyword="当前关键词", status=KeywordQueueItemStatus.RUNNING
            ),
            CrawlerKeywordQueueItem(
                keyword="上次失败", status=KeywordQueueItemStatus.FAILED, error="冷却中"
            ),
        ],
        platforms=[Platform.BILIBILI],
        status=KeywordQueueStatus.PAUSED,
    )
    repo.save_crawler_keyword_queue(queue)
    active_future = Future()
    monkeypatch.setattr(
        crawler, "_CRAWLER_QUEUE_FUTURES", {queue.queue_id: active_future}
    )

    resumed = crawler.resume_crawler_keyword_queue(queue.queue_id, repo)

    assert resumed.status == KeywordQueueStatus.QUEUED.value
    assert resumed.items[0].status == KeywordQueueItemStatus.RUNNING.value
    assert resumed.items[1].status == KeywordQueueItemStatus.QUEUED.value
    assert resumed.items[1].error is None


def test_csv_export_has_fixed_public_fields(monkeypatch) -> None:
    from project.backend.app.api.v1.crawler import (
        CrawlerBatchResponse,
        CrawlerCandidateResult,
        CrawlerPlatformRunResponse,
    )

    class Repo:
        def get_search_batch(self, _batch_id):
            return object()

    batch = CrawlerBatchResponse(
        batch_id="batch-1",
        keyword="门店短视频",
        published_window_days=0,
        count_per_platform=30,
        provider="test",
        mode="smart",
        status="succeeded",
        force_refresh=False,
        platform_runs=[
            CrawlerPlatformRunResponse(
                run_id="run-1",
                platform="bilibili",
                platform_label="B站",
                provider="test",
                mode="public_web",
                status="succeeded",
                requested_count=1,
                returned_count=1,
                cache_hit=True,
                api_call_count=0,
                candidates=[
                    CrawlerCandidateResult(
                        video_id="BV1",
                        title="公开素材",
                        author_name="作者",
                        platform="bilibili",
                        platform_label="B站",
                        source_url="https://www.bilibili.com/video/BV1",
                        relevance_reason="标题命中关键词",
                    )
                ],
            )
        ],
    )
    monkeypatch.setattr(crawler, "_batch_to_response", lambda *_args, **_kwargs: batch)
    response = crawler.export_crawler_batch_csv("batch-1", Repo())

    async def collect() -> bytes:
        return b"".join([chunk async for chunk in response.body_iterator])

    payload = asyncio.run(collect()).decode("utf-8-sig")
    assert "关键词,平台,标题,作者,链接,时间,互动,热度,相关性,质量说明" in payload
    assert "门店短视频,B站,公开素材,作者" in payload
    assert "Cookie" not in payload
    assert "Token" not in payload
