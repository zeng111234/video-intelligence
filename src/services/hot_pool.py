"""官方热榜池服务。

默认搜索路径：先同步官方热榜进候选池（保存快照，复用 import_page 仓储链路），
再按「标题 + hot_words」做本地关键词匹配。

- 关键词匹配 0 条时产出 ``result_state="官方热榜无匹配"``，并给出文案
  「官方热门池中没有匹配，不代表抖音搜索无视频」，与供应商异常明确区分。
- ``video.search`` 关键词垂搜保留为可选路径（见 discovery 服务），
  只覆盖最近 1 天公开视频。
- 热点词同步：消费 DouyinHotWordsAdapter.fetch_hot_words() 并持久化，
  供前端搜索建议读取。

服务层只依赖 contracts 中的协议（CrawlerAdapter / HotWordsProvider），
不直接 import 适配器实现，便于测试用 fake 替换。
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import uuid4

from src.contracts import CandidateRepository, CrawlerAdapter, HotWordsProvider
from src.models import (
    CandidateMatch,
    DataSource,
    DiscoveryResult,
    HotWordRecord,
    KeywordTrendResult,
    Platform,
    SamplingCheckpoint,
    SamplingStatus,
    SourceCapability,
    SourceRequest,
    SyncReport,
    VideoCandidate,
)
from src.services.discovery import RECRAWL_OFFSETS_BY_WINDOW
from src.services.keyword_trend import KeywordTrendService
from src.services.source import SourceService

HOT_POOL_NO_MATCH_STATE = "官方热榜无匹配"
HOT_POOL_MATCH_STATE = "官方热榜匹配"
HOT_POOL_NO_MATCH_NOTICE = "官方热门池中没有匹配，不代表抖音搜索无视频"
OFFICIAL_BILLBOARD_EVIDENCE_PREFIX = "official_billboard:"
RECRAWL_MISS_GRACE_MINUTES = 30
DEFAULT_PROVIDER_NAME = "official_hot_pool"


@dataclass(frozen=True)
class HotPoolSearchResult:
    """官方热榜池关键词匹配结果。"""

    request_id: str
    keyword: str
    provider_name: str
    pool_size: int
    matched: list[VideoCandidate] = field(default_factory=list)
    trends: list[KeywordTrendResult] = field(default_factory=list)
    result_state: str = HOT_POOL_NO_MATCH_STATE
    user_notice: str | None = None
    next_recrawl_at: datetime | None = None
    sync_report: SyncReport | None = None
    match_reasons: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HotWordsSyncResult:
    """官方热点词同步结果。"""

    provider_name: str
    saved_count: int
    words: list[HotWordRecord] = field(default_factory=list)
    error: str | None = None


def _candidate_hot_words(candidate: VideoCandidate) -> list[str]:
    """从候选 evidence（official_billboard: 前缀 JSON）中解析 hot_words。"""
    evidence = candidate.evidence or ""
    if not evidence.startswith(OFFICIAL_BILLBOARD_EVIDENCE_PREFIX):
        return []
    payload = evidence[len(OFFICIAL_BILLBOARD_EVIDENCE_PREFIX):]
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return []
    words = data.get("hot_words") or []
    return [str(word) for word in words if str(word).strip()]


class OfficialHotPoolService:
    def __init__(
        self,
        repository: CandidateRepository,
        source_service: SourceService,
        trend_service: KeywordTrendService | None = None,
        billboard_adapter: CrawlerAdapter | None = None,
        hot_words_adapter: HotWordsProvider | None = None,
        *,
        clock=None,
    ) -> None:
        self.repository = repository
        self.source_service = source_service
        self.trend_service = trend_service or KeywordTrendService(repository)
        self.billboard_adapter = billboard_adapter
        self.hot_words_adapter = hot_words_adapter
        self.clock = clock or (lambda: datetime.now().astimezone())

    # ------------------------------------------------------------------
    # 适配器解析
    # ------------------------------------------------------------------

    def _billboard(self, adapter: CrawlerAdapter | None) -> CrawlerAdapter:
        resolved = adapter or self.billboard_adapter
        if resolved is None:
            raise ValueError("未配置官方热榜适配器（DouyinHotBillboardAdapter）。")
        return resolved

    def _hot_words(self, adapter: HotWordsProvider | None) -> HotWordsProvider:
        resolved = adapter or self.hot_words_adapter
        if resolved is None:
            raise ValueError("未配置官方热点词适配器（DouyinHotWordsAdapter）。")
        return resolved

    def _provider_name(self, adapter: CrawlerAdapter | None) -> str:
        resolved = adapter or self.billboard_adapter
        if resolved is None:
            return DEFAULT_PROVIDER_NAME
        try:
            return resolved.capabilities().provider_name
        except Exception:
            return DEFAULT_PROVIDER_NAME

    # ------------------------------------------------------------------
    # 热榜同步
    # ------------------------------------------------------------------

    def sync_billboard(
        self,
        adapter: CrawlerAdapter | None = None,
        *,
        limit: int = 50,
        category: str = "官方热榜",
    ) -> SyncReport:
        """同步官方热榜进候选池并保存快照（复用 import_page 仓储链路）。"""
        billboard = self._billboard(adapter)
        capability = billboard.capabilities()
        if not capability.enabled:
            missing = "、".join(capability.missing_configuration) or "官方热榜权限"
            raise ValueError(f"官方热榜不可用：{missing}。")
        request = SourceRequest(
            source=DataSource.OFFICIAL,
            platform=Platform.DOUYIN,
            category=category,
            limit=limit,
            page_size=min(limit, capability.max_page_size),
        )
        page = billboard.sync(request)
        return self.source_service.import_page(page)

    # ------------------------------------------------------------------
    # 本地关键词匹配
    # ------------------------------------------------------------------

    def search_hot_pool(
        self,
        *,
        keyword: str,
        adapter: CrawlerAdapter | None = None,
        limit: int = 10,
        platform: Platform = Platform.DOUYIN,
        record: bool = True,
        related_terms: list[str] | None = None,
    ) -> HotPoolSearchResult:
        """官方热榜池 + 主关键词/相关赛道词的本地低成本匹配。"""
        keyword = keyword.strip()
        if not 2 <= len(keyword) <= 50:
            raise ValueError("关键词长度必须为 2 到 50 个字符。")
        if not 1 <= limit <= 100:
            raise ValueError("返回数量必须为 1 到 100 条。")

        started_at = self.clock()
        provider_name = self._provider_name(adapter)
        pool = self.repository.list_official_hot_pool(platform)
        terms = self._related_terms(keyword, related_terms)
        matched_rows = [
            (candidate, *match)
            for candidate in pool
            if (match := self._match_candidate(candidate, keyword, terms)) is not None
        ]
        matched_rows.sort(
            key=lambda row: (
                -row[1],
                -float(getattr(row[0].heat, "score", 0.0) or 0.0),
                row[0].official_rank or 10_000,
            )
        )
        matched = [row[0] for row in matched_rows[:limit]]
        match_reasons = {row[0].video_id: row[2] for row in matched_rows[:limit]}
        result_state = (
            HOT_POOL_MATCH_STATE if matched else HOT_POOL_NO_MATCH_STATE
        )
        user_notice = None if matched else HOT_POOL_NO_MATCH_NOTICE
        request_id = f"hotpool-{uuid4().hex[:12]}"
        finished_at = self.clock()
        if record:
            self.repository.save_discovery_result(
                DiscoveryResult(
                    request_id=request_id,
                    keyword=keyword,
                    provider_name=provider_name,
                    platform=platform,
                    requested_count=limit,
                    fetched_count=len(pool),
                    unique_count=len(matched),
                    result_state=result_state,
                    user_notice=user_notice,
                    api_call_count=0,
                    permission_status="local_pool",
                    started_at=started_at,
                    finished_at=finished_at,
                )
            )
        return HotPoolSearchResult(
            request_id=request_id,
            keyword=keyword,
            provider_name=provider_name,
            pool_size=len(pool),
            matched=matched,
            trends=self.repository.list_keyword_trend_results(
                keyword.casefold(), platform=platform
            ),
            result_state=result_state,
            user_notice=user_notice,
            next_recrawl_at=self._next_recrawl_at(keyword, matched),
            match_reasons=match_reasons,
        )

    @staticmethod
    def _normal_text(value: str) -> str:
        return "".join(
            character
            for character in unicodedata.normalize("NFKC", value).casefold()
            if not character.isspace()
            and not unicodedata.category(character).startswith(("P", "Z"))
        )

    @classmethod
    def _related_terms(
        cls, keyword: str, related_terms: list[str] | None
    ) -> list[str]:
        primary = cls._normal_text(keyword)
        normalized: list[str] = []
        for raw_term in related_terms or []:
            term = str(raw_term or "").strip()
            compact = cls._normal_text(term)
            if len(compact) < 2 or compact == primary or compact in normalized:
                continue
            normalized.append(compact)
            if len(normalized) >= 5:
                break
        return normalized

    @classmethod
    def _match_candidate(
        cls,
        candidate: VideoCandidate,
        keyword: str,
        related_terms: list[str],
    ) -> tuple[float, str] | None:
        """Return a deterministic relevance score and a user-facing reason.

        主关键词命中始终优先；相关赛道词仅作为用户明确给出的召回扩展，
        不调用模型、不把泛热词自动扩大为无关内容。
        """
        title = cls._normal_text(candidate.title)
        hot_words = [cls._normal_text(word) for word in _candidate_hot_words(candidate)]
        primary = cls._normal_text(keyword)
        if primary in title:
            return 1000.0, f"主关键词“{keyword.strip()}”命中标题"
        if any(primary in word for word in hot_words):
            return 800.0, f"主关键词“{keyword.strip()}”命中官方热点词"

        matched_terms: list[str] = []
        score = 0.0
        for term in related_terms:
            if term in title:
                score += 300.0
                matched_terms.append(term)
            elif any(term in word for word in hot_words):
                score += 200.0
                matched_terms.append(term)
        if not matched_terms:
            return None
        display_terms = "、".join(matched_terms[:2])
        return score, f"相关赛道词命中：{display_terms}"

    def _next_recrawl_at(
        self, keyword: str, matched: list[VideoCandidate]
    ) -> datetime | None:
        if not matched:
            return None
        matched_ids = {candidate.video_id for candidate in matched}
        pending = [
            checkpoint.due_at
            for checkpoint in self.repository.list_sampling_checkpoints(
                keyword.casefold()
            )
            if checkpoint.candidate_id in matched_ids
            and checkpoint.status == SamplingStatus.PENDING
        ]
        return min(pending) if pending else None

    # ------------------------------------------------------------------
    # 一键监测：同步热榜 → 匹配 → 保存快照 → 安排复爬 → 计算观察分
    # ------------------------------------------------------------------

    def monitor(
        self,
        *,
        keyword: str,
        adapter: CrawlerAdapter | None = None,
        limit: int = 10,
        publish_time: int = 1,
        related_terms: list[str] | None = None,
    ) -> HotPoolSearchResult:
        """官方热榜一键监测：同步热榜并处理单个关键词的匹配与复爬计划。"""
        billboard = self._billboard(adapter)
        report = self.sync_billboard(billboard, limit=max(limit, 50))
        return self._process_keyword(
            keyword=keyword,
            billboard=billboard,
            limit=limit,
            publish_time=publish_time,
            sync_report=report,
            related_terms=related_terms,
        )

    def execute_due_recrawls(
        self,
        adapter: CrawlerAdapter | None = None,
        *,
        max_groups: int = 5,
    ) -> list[HotPoolSearchResult]:
        """执行到期复爬：重新同步热榜后按关键词分组重匹配并刷新排行。"""
        billboard = self._billboard(adapter)
        provider_name = billboard.capabilities().provider_name
        now = self.clock()
        due_keywords: list[tuple[datetime, str, int]] = []
        seen: set[str] = set()
        for checkpoint in self.repository.list_sampling_checkpoints():
            if checkpoint.status != SamplingStatus.PENDING:
                continue
            if checkpoint.due_at > now:
                continue
            if checkpoint.provider_name != provider_name:
                continue
            if checkpoint.keyword in seen:
                continue
            seen.add(checkpoint.keyword)
            due_keywords.append(
                (checkpoint.due_at, checkpoint.keyword, checkpoint.published_window_days)
            )
        if not due_keywords:
            return []
        report = self.sync_billboard(billboard, limit=50)
        results: list[HotPoolSearchResult] = []
        for _due_at, keyword, window_days in sorted(due_keywords)[: max(1, max_groups)]:
            results.append(
                self._process_keyword(
                    keyword=keyword,
                    billboard=billboard,
                    limit=10,
                    publish_time=window_days if window_days in (1, 7) else 1,
                    sync_report=report,
                    related_terms=None,
                )
            )
        return results

    def _process_keyword(
        self,
        *,
        keyword: str,
        billboard: CrawlerAdapter,
        limit: int,
        publish_time: int,
        sync_report: SyncReport,
        related_terms: list[str] | None = None,
    ) -> HotPoolSearchResult:
        capability = billboard.capabilities()
        provider_name = capability.provider_name
        result = self.search_hot_pool(
            keyword=keyword,
            adapter=billboard,
            limit=limit,
            related_terms=related_terms,
        )
        if not result.matched:
            return HotPoolSearchResult(
                **{**result.__dict__, "sync_report": sync_report}
            )

        keyword_key = keyword.strip().casefold()
        now = self.clock()
        existing = self.repository.list_sampling_checkpoints(keyword_key)
        for fallback_rank, candidate in enumerate(result.matched, start=1):
            observed_at = candidate.metrics.sampled_at
            self.repository.save_candidate_match(
                CandidateMatch(
                    request_id=result.request_id,
                    video_id=candidate.video_id,
                    keyword=keyword_key,
                    cohort_key=f"{provider_name}:official_hot_pool:{keyword_key}",
                    platform=candidate.platform,
                    provider_name=provider_name,
                    platform_rank=min(10, candidate.official_rank or fallback_rank),
                    observed_at=observed_at,
                    publish_time=publish_time,
                    sort_type=0,
                    evidence=candidate.evidence,
                )
            )
            plan = [
                checkpoint
                for checkpoint in existing
                if checkpoint.candidate_id == candidate.video_id
                and checkpoint.provider_name == provider_name
            ]
            if not plan:
                for offset in RECRAWL_OFFSETS_BY_WINDOW[publish_time]:
                    checkpoint = SamplingCheckpoint(
                        checkpoint_id=f"sample-{uuid4().hex[:12]}",
                        keyword=keyword_key,
                        candidate_id=candidate.video_id,
                        request_id=result.request_id,
                        platform=candidate.platform,
                        provider_name=provider_name,
                        published_window_days=publish_time,
                        offset_hours=offset,
                        due_at=observed_at + timedelta(hours=offset),
                    )
                    self.repository.save_sampling_checkpoint(checkpoint)
                    existing.append(checkpoint)
            else:
                for checkpoint in plan:
                    if (
                        checkpoint.status == SamplingStatus.PENDING
                        and observed_at >= checkpoint.due_at
                    ):
                        self.repository.save_sampling_checkpoint(
                            checkpoint.model_copy(
                                update={
                                    "status": SamplingStatus.OBSERVED,
                                    "observed_at": observed_at,
                                }
                            )
                        )
        miss_cutoff = now - timedelta(minutes=RECRAWL_MISS_GRACE_MINUTES)
        matched_ids = {candidate.video_id for candidate in result.matched}
        for checkpoint in existing:
            if (
                checkpoint.status == SamplingStatus.PENDING
                and checkpoint.provider_name == provider_name
                and checkpoint.due_at < miss_cutoff
                and checkpoint.candidate_id not in matched_ids
            ):
                self.repository.save_sampling_checkpoint(
                    checkpoint.model_copy(update={"status": SamplingStatus.MISSED})
                )

        trends = self.trend_service.recompute(
            keyword_key,
            platform=Platform.DOUYIN,
            provider_name=provider_name,
            now=self.clock(),
        )
        return HotPoolSearchResult(
            request_id=result.request_id,
            keyword=result.keyword,
            provider_name=provider_name,
            pool_size=result.pool_size,
            matched=result.matched,
            trends=trends,
            result_state=result.result_state,
            user_notice=result.user_notice,
            next_recrawl_at=self._next_recrawl_at(keyword_key, result.matched),
            sync_report=sync_report,
        )

    # ------------------------------------------------------------------
    # 热点词同步与建议
    # ------------------------------------------------------------------

    def sync_hot_words(
        self, adapter: HotWordsProvider | None = None
    ) -> HotWordsSyncResult:
        """同步官方实时热点词并持久化（word、hot_value、fetched_at）。"""
        hot_words = self._hot_words(adapter)
        capability: SourceCapability = hot_words.capabilities()
        if not capability.enabled:
            missing = "、".join(capability.missing_configuration) or "实时热点词权限"
            return HotWordsSyncResult(
                provider_name=capability.provider_name,
                saved_count=0,
                error=f"官方热点词不可用：{missing}。",
            )
        try:
            entries = hot_words.fetch_hot_words()
        except Exception as exc:
            return HotWordsSyncResult(
                provider_name=capability.provider_name,
                saved_count=0,
                error=str(exc),
            )
        records: list[HotWordRecord] = []
        seen: set[str] = set()
        for entry in entries:
            word = str(entry.word or "").strip()
            if not word or word in seen:
                continue
            seen.add(word)
            records.append(
                HotWordRecord(
                    word=word,
                    hot_value=entry.hot_value,
                    fetched_at=entry.fetched_at,
                )
            )
        self.repository.save_hot_words(records)
        return HotWordsSyncResult(
            provider_name=capability.provider_name,
            saved_count=len(records),
            words=records,
        )

    def hot_word_suggestions(self, limit: int = 50) -> list[HotWordRecord]:
        """前端搜索框「官方热点词建议」读取入口。"""
        return self.repository.list_hot_words(limit)
