"""关键词爬虫 API —— 接入 SandboxLicensedSearchProvider 获取三平台沙箱数据。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.adapters.licensed import SandboxLicensedSearchProvider
from src.models import Platform

router = APIRouter(prefix="/api/v1/crawler", tags=["crawler"])

# 共享沙箱 provider 实例（确定性数据，线程安全）
_sandbox_provider = SandboxLicensedSearchProvider()

_PLATFORM_MAP: dict[str, Platform] = {
    "douyin": Platform.DOUYIN,
    "xiaohongshu": Platform.XIAOHONGSHU,
    "wechat_channels": Platform.WECHAT_CHANNELS,
}


# ---------------------------------------------------------------------------
# In-memory task store (production would use DB persistence)
# ---------------------------------------------------------------------------

class CrawlerTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CrawlerTask(BaseModel):
    task_id: str
    keyword: str
    status: CrawlerTaskStatus = CrawlerTaskStatus.PENDING
    platform: str = "douyin"
    max_results: int = 10
    result_count: int = 0
    results: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# In-memory store
_tasks: dict[str, CrawlerTask] = {}


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CrawlerCreateRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=200, description="抓取关键词")
    platform: str = Field("douyin", description="目标平台")
    max_results: int = Field(10, ge=1, le=100, description="最大结果数")


class CrawlerTaskResponse(BaseModel):
    task_id: str
    keyword: str
    status: str
    platform: str
    max_results: int
    result_count: int
    results: list[dict[str, Any]]
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CrawlerTaskListResponse(BaseModel):
    items: list[CrawlerTaskResponse]
    total: int


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.post("/tasks", response_model=CrawlerTaskResponse)
def create_crawler_task(body: CrawlerCreateRequest):
    """创建关键词爬虫任务，通过沙箱 provider 获取三平台演示数据。"""
    task_id = f"crawl-{uuid.uuid4().hex[:12]}"
    now = datetime.now()
    task = CrawlerTask(
        task_id=task_id,
        keyword=body.keyword.strip(),
        platform=body.platform,
        max_results=body.max_results,
        status=CrawlerTaskStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )
    _tasks[task_id] = task

    try:
        platform_enum = _PLATFORM_MAP.get(body.platform)
        if platform_enum is None:
            raise ValueError(f"不支持的平台: {body.platform}，可选: douyin, xiaohongshu, wechat_channels")

        published_after = datetime.now(timezone.utc) - timedelta(days=7)
        page = _sandbox_provider.search(
            platform=platform_enum,
            keyword=task.keyword,
            published_after=published_after,
            limit=min(task.max_results, 10),
            idempotency_key=task_id,
        )

        # 将 ProviderSearchItem 转为前端可展示的格式
        results: list[dict[str, Any]] = []
        for item in page.items:
            results.append({
                "title": item.title,
                "author": item.author_name,
                "likes": item.metrics.likes or 0,
                "platform": item.platform.value,
                "item_id": item.platform_item_id,
                "plays": item.metrics.plays,
                "comments": item.metrics.comments,
                "source_url": str(item.source_url) if item.source_url else None,
            })

        task.results = results
        task.result_count = len(results)
        task.status = CrawlerTaskStatus.SUCCEEDED
        task.updated_at = datetime.now()
    except Exception as exc:
        task.status = CrawlerTaskStatus.FAILED
        task.error_message = str(exc)
        task.updated_at = datetime.now()

    return _to_response(task)


@router.get("/tasks", response_model=CrawlerTaskListResponse)
def list_crawler_tasks():
    """列出所有爬虫任务。"""
    items = sorted(_tasks.values(), key=lambda t: t.created_at, reverse=True)
    return CrawlerTaskListResponse(
        items=[_to_response(t) for t in items],
        total=len(items),
    )


@router.get("/tasks/{task_id}", response_model=CrawlerTaskResponse)
def get_crawler_task(task_id: str):
    """获取爬虫任务详情。"""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return _to_response(task)


def _to_response(task: CrawlerTask) -> CrawlerTaskResponse:
    return CrawlerTaskResponse(
        task_id=task.task_id,
        keyword=task.keyword,
        status=task.status.value,
        platform=task.platform,
        max_results=task.max_results,
        result_count=task.result_count,
        results=task.results,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
