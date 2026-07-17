from __future__ import annotations

from datetime import datetime

from src.contracts import CandidateRepository, CrawlerAdapter
from src.models import (
    CandidateMatch,
    DataSource,
    DiscoveryResult,
    ImportErrorDetail,
    NormalizedCandidate,
    SourcePage,
    SourceRequest,
)
from src.services.source import SourceService


class KeywordDiscoveryService:
    def __init__(
        self,
        repository: CandidateRepository,
        source_service: SourceService,
    ) -> None:
        self.repository = repository
        self.source_service = source_service

    def discover(
        self,
        *,
        keyword: str,
        adapter: CrawlerAdapter,
        publish_time: int = 1,
    ) -> DiscoveryResult:
        keyword = keyword.strip()
        if not 2 <= len(keyword) <= 50:
            raise ValueError("关键词长度必须为 2 到 50 个字符。")
        if publish_time not in {1, 7}:
            raise ValueError("发布时间范围只支持近 24 小时或近 7 天。")

        started_at = datetime.now().astimezone()
        capability = adapter.capabilities()
        request = SourceRequest(
            source=DataSource.OFFICIAL,
            keywords=[keyword],
            category=f"关键词/{keyword}",
            limit=10,
            page_size=min(10, capability.max_page_size),
            publish_time=publish_time,
            sort_type=0,
        )
        if not capability.enabled:
            result = DiscoveryResult(
                request_id=request.request_id,
                keyword=keyword,
                provider_name=capability.provider_name,
                requested_count=10,
                permission_status=capability.permission_status,
                publish_time=publish_time,
                sort_type=0,
                api_call_count=0,
                started_at=started_at,
                finished_at=datetime.now().astimezone(),
                errors=[
                    ImportErrorDetail(
                        row=1,
                        field="provider",
                        message=(
                            "缺少配置："
                            + ", ".join(capability.missing_configuration)
                            + "；未发起平台请求。"
                        ),
                    )
                ],
            )
            self.repository.save_discovery_result(result)
            return result

        unique: dict[tuple[str, str], NormalizedCandidate] = {}
        duplicate_count = 0
        errors: list[ImportErrorDetail] = []
        try:
            page = adapter.sync(request)
        except Exception as exc:
            page = SourcePage()
            errors.append(ImportErrorDetail(row=1, field="provider", message=str(exc)))
        rank_by_key: dict[tuple[str, str], int] = {}
        for platform_rank, item in enumerate(page.items[:10], start=1):
            key = (item.platform.value, item.platform_item_id)
            if key in unique:
                duplicate_count += 1
                continue
            unique[key] = item
            rank_by_key[key] = platform_rank
        errors.extend(page.errors)
        items = list(unique.values())[:10]
        report = None
        if items:
            report = self.source_service.import_page(
                SourcePage(items=items, errors=errors)
            )
        result = DiscoveryResult(
            request_id=request.request_id,
            keyword=keyword,
            provider_name=capability.provider_name,
            requested_count=10,
            fetched_count=len(page.items),
            unique_count=len(items),
            duplicate_count=duplicate_count,
            exhausted=not page.has_more,
            partial=bool(errors) or len(items) < 10,
            permission_status=capability.permission_status,
            publish_time=publish_time,
            sort_type=0,
            api_call_count=1,
            started_at=started_at,
            finished_at=datetime.now().astimezone(),
            import_report=report,
            errors=errors,
        )
        self.repository.save_discovery_result(result)
        if items:
            candidate_ids = {
                (
                    candidate.platform.value,
                    candidate.platform_item_id,
                ): candidate.video_id
                for candidate in self.repository.list_candidates()
            }
            keyword_key = keyword.casefold()
            cohort_key = f"douyin:keyword:{keyword_key}"
            for item in items:
                video_id = candidate_ids.get(
                    (item.platform.value, item.platform_item_id)
                )
                if video_id:
                    self.repository.save_candidate_match(
                        CandidateMatch(
                            request_id=request.request_id,
                            video_id=video_id,
                            keyword=keyword_key,
                            cohort_key=cohort_key,
                            platform_rank=rank_by_key[
                                (item.platform.value, item.platform_item_id)
                            ],
                            observed_at=item.metrics.sampled_at,
                            publish_time=publish_time,
                            sort_type=0,
                            evidence=item.evidence,
                        )
                    )
        return result
