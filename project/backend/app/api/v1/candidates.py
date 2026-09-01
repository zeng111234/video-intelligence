"""候选搜索 API。"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Depends

from project.backend.app.core.deps import get_candidate_service
from project.backend.app.schemas.requests import CandidateSearchRequest
from project.backend.app.schemas.responses import (
    CandidateCategoryOption,
    CandidateItem,
    CandidateListResponse,
)
from src.models import Platform

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/candidates", tags=["candidates"])


@router.post("/search", response_model=CandidateListResponse)
def search_candidates(
    body: CandidateSearchRequest,
    service=Depends(get_candidate_service),
):
    """搜索视频候选。"""
    logger.info(f"候选搜索请求: keyword='{body.keyword}', page={body.page}, limit={body.limit}")
    try:
        platforms = []
        for value in body.platforms:
            try:
                platforms.append(Platform(value))
            except ValueError:
                continue
        base_candidates = service.search(
            query=body.keyword,
            platforms=platforms or None,
        )
        category_options = [
            CandidateCategoryOption(value=category, count=count)
            for category, count in sorted(
                {
                    category: sum(1 for candidate in base_candidates if candidate.category == category)
                    for category in {candidate.category for candidate in base_candidates}
                }.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        candidates = [
            candidate
            for candidate in base_candidates
            if not body.category
            or body.category == "全部赛道"
            or candidate.category == body.category
        ]
        total = len(candidates)
        start = (body.page - 1) * body.limit
        candidates = candidates[start : start + body.limit]
        items = [
            CandidateItem(
                video_id=c.video_id,
                platform_item_id=c.platform_item_id,
                title=c.title,
                platform=c.platform.value,
                author_name=c.author_name,
                category=c.category,
                heat_score=c.heat.score,
                heat_level=c.heat.level.value,
                source_url=str(c.source_url) if c.source_url else None,
                published_at=c.published_at,
                observed_at=c.metrics.sampled_at,
                publication_time_state=(
                    "sampled_fallback"
                    if any("采样时间" in warning for warning in c.data_quality_warnings)
                    else "platform"
                ),
                official_hot=c.official_hot,
                official_rank=c.official_rank,
                snapshot_count=c.heat.snapshot_count,
                growth_window_hours=c.heat.growth_window_hours,
                heat_reasons=c.heat.reasons,
            )
            for c in candidates
        ]
        logger.info(f"候选搜索返回 {len(items)} 条结果")
        return CandidateListResponse(
            items=items,
            total=total,
            category_options=category_options,
        )
    except Exception as e:
        logger.error(f"候选搜索失败: {e}", exc_info=True)
        raise
