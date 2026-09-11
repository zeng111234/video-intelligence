"""任务列表 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from project.backend.app.core.deps import get_repository
from project.backend.app.schemas.responses import TaskItem, TaskListResponse

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskDeleteResponse(BaseModel):
    task_id: str
    deleted: bool


@router.get("", response_model=TaskListResponse)
def list_tasks(
    repo=Depends(get_repository),
):
    """列出所有任务。"""
    tasks = repo.list_tasks()
    items = [
        TaskItem(
            task_id=t.task_id,
            kind=t.kind.value,
            title=t.title,
            status=t.status.value,
            progress=t.progress,
            error_message=getattr(t, "error_message", None),
            created_at=t.created_at,
            finished_at=getattr(t, "finished_at", None)
            or (
                t.updated_at
                if getattr(t.status, "value", t.status)
                in {"succeeded", "failed", "cancelled"}
                else None
            ),
        )
        for t in tasks
    ]
    return TaskListResponse(items=items, total=len(items))


@router.get("/{task_id}", response_model=TaskItem)
def get_task(
    task_id: str,
    repo=Depends(get_repository),
):
    """查询单个任务详情。"""
    task = repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return TaskItem(
        task_id=task.task_id,
        kind=task.kind.value,
        title=task.title,
        status=task.status.value,
        progress=task.progress,
        error_message=getattr(task, "error_message", None),
        created_at=task.created_at,
        finished_at=getattr(task, "finished_at", None)
        or (
            task.updated_at
            if getattr(task.status, "value", task.status)
            in {"succeeded", "failed", "cancelled"}
            else None
        ),
    )


@router.delete("/{task_id}", response_model=TaskDeleteResponse)
def delete_task(
    task_id: str,
    repo=Depends(get_repository),
):
    if not repo.delete_task(task_id):
        raise HTTPException(status_code=404, detail=f"任务不存在或已删除: {task_id}")
    return TaskDeleteResponse(task_id=task_id, deleted=True)
