"""候选搜索 API。"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Depends

from project.backend.app.core.deps import get_candidate_service
from project.backend.app.schemas.requests import CandidateSearchRequest
from project.backend.app.schemas.responses import CandidateItem, CandidateListResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/candidates", tags=["candidates"])


@router.post("/search", response_model=CandidateListResponse)
def search_candidates(
    body: CandidateSearchRequest,
    service=Depends(get_candidate_service),
):
    """搜索视频候选。"""
    logger.info(f"候选搜索请求: keyword='{body.keyword}', limit={body.limit}")
    try:
        # 使用 CandidateService.search() 而非 repository.list_candidates()
        candidates = service.search(query=body.keyword)
        # 限制数量
        candidates = candidates[: body.limit]
        items = [
            CandidateItem(
                video_id=c.video_id,
                title=c.title,
                platform=c.platform.value,
                author_name=c.author_name,
                category=c.category,
                heat_score=c.heat.score,
                heat_level=c.heat.level.value,
                source_url=str(c.source_url) if c.source_url else None,
                published_at=c.published_at,
            )
            for c in candidates
        ]
        logger.info(f"候选搜索返回 {len(items)} 条结果")
        return CandidateListResponse(items=items, total=len(items))
    except Exception as e:
        logger.error(f"候选搜索失败: {e}", exc_info=True)
        raise
