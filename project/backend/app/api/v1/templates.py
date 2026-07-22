"""模板管理 API。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from project.backend.app.core.deps import get_video_editing_service

if TYPE_CHECKING:
    from src.services.template_service import TemplateService

router = APIRouter(prefix="/templates", tags=["templates"])


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class TemplateCreateRequest(BaseModel):
    """创建自定义模板请求。"""

    name: str = Field(..., min_length=1, max_length=100, description="模板名称")
    description: str = Field("", description="模板描述")
    category: str = Field("custom", description="模板分类")
    steps: list[dict[str, Any]] = Field(
        default_factory=list, description="剪辑步骤定义列表"
    )
    output_format: str = Field("mp4", description="输出格式")
    output_resolution: str = Field("1080x1920", description="输出分辨率")
    output_fps: int = Field(30, ge=15, le=60, description="输出帧率")
    output_bitrate: str = Field("4M", description="输出码率")


class TemplateUpdateRequest(BaseModel):
    """更新模板请求。"""

    name: str | None = Field(None, min_length=1, max_length=100, description="模板名称")
    description: str | None = Field(None, description="模板描述")
    category: str | None = Field(None, description="模板分类")
    steps: list[dict[str, Any]] | None = Field(None, description="剪辑步骤定义列表")
    output_format: str | None = Field(None, description="输出格式")
    output_resolution: str | None = Field(None, description="输出分辨率")
    output_fps: int | None = Field(None, ge=15, le=60, description="输出帧率")
    output_bitrate: str | None = Field(None, description="输出码率")


class TemplateApplyRequest(BaseModel):
    """执行模板请求。"""

    source_video_path: str = Field(..., description="源视频路径")


class TemplateResponse(BaseModel):
    """模板详情响应。"""

    template_id: str
    name: str
    description: str
    category: str
    icon: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
    output_format: str = "mp4"
    output_resolution: str = "1080x1920"
    output_fps: int = 30
    output_bitrate: str = "4M"
    is_builtin: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class TemplateListResponse(BaseModel):
    """模板列表响应。"""

    items: list[TemplateResponse]
    total: int


# ---------------------------------------------------------------------------
# 依赖获取（延迟导入，避免循环/并行导入问题）
# ---------------------------------------------------------------------------


def _get_template_service() -> TemplateService:
    from project.backend.app.core.deps import get_template_service

    return get_template_service()


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _template_to_response(tmpl: Any) -> TemplateResponse:
    """将领域模型转换为 API 响应模型。"""
    return TemplateResponse(
        template_id=getattr(tmpl, "template_id", ""),
        name=getattr(tmpl, "name", ""),
        description=getattr(tmpl, "description", ""),
        category=getattr(tmpl, "category", "custom"),
        icon=getattr(tmpl, "icon", ""),
        steps=[
            step.model_dump(mode="json") if hasattr(step, "model_dump") else step
            for step in getattr(tmpl, "steps", [])
        ],
        output_format=getattr(tmpl, "output_format", "mp4"),
        output_resolution=getattr(tmpl, "output_resolution", "1080x1920"),
        output_fps=getattr(tmpl, "output_fps", 30),
        output_bitrate=getattr(tmpl, "output_bitrate", "4M"),
        is_builtin=getattr(tmpl, "is_builtin", False),
        created_at=str(tmpl.created_at) if getattr(tmpl, "created_at", None) else None,
        updated_at=str(tmpl.updated_at) if getattr(tmpl, "updated_at", None) else None,
    )


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("", response_model=TemplateListResponse)
def list_templates(
    category: str | None = None,
    service: TemplateService = Depends(_get_template_service),
):
    """列出所有模板，支持按分类过滤。"""
    try:
        if category:
            templates = service.list_templates(category=category)
        else:
            templates = service.list_templates()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取模板列表失败: {exc}")

    items = [_template_to_response(t) for t in templates]
    return TemplateListResponse(items=items, total=len(items))


@router.get("/{template_id}", response_model=TemplateResponse)
def get_template(
    template_id: str,
    service: TemplateService = Depends(_get_template_service),
):
    """获取单个模板详情。"""
    try:
        tmpl = service.get_template(template_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"模板不存在: {exc}")

    if tmpl is None:
        raise HTTPException(status_code=404, detail=f"模板不存在: {template_id}")

    return _template_to_response(tmpl)


@router.post("", response_model=TemplateResponse, status_code=201)
def create_template(
    body: TemplateCreateRequest,
    service: TemplateService = Depends(_get_template_service),
):
    """创建自定义模板。"""
    try:
        from src.models import EditTemplate, TemplateStepDef

        tmpl = service.create_template(
            EditTemplate(
                template_id="",
                name=body.name,
                description=body.description,
                category=body.category,
                steps=[TemplateStepDef(**step) for step in body.steps],
                output_format=body.output_format,
                output_resolution=body.output_resolution,
                output_fps=body.output_fps,
                output_bitrate=body.output_bitrate,
                is_builtin=False,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"创建模板失败: {exc}")

    return _template_to_response(tmpl)


@router.put("/{template_id}", response_model=TemplateResponse)
def update_template(
    template_id: str,
    body: TemplateUpdateRequest,
    service: TemplateService = Depends(_get_template_service),
):
    """更新模板。"""
    # 先检查是否存在
    try:
        existing = service.get_template(template_id)
    except Exception:
        existing = None

    if existing is None:
        raise HTTPException(status_code=404, detail=f"模板不存在: {template_id}")

    # 禁止修改内置模板
    if getattr(existing, "is_builtin", False):
        raise HTTPException(status_code=403, detail="内置模板不允许修改。")

    # 构建更新字段（仅包含非 None 字段）
    update_fields: dict[str, Any] = {}
    for field_name in (
        "name",
        "description",
        "category",
        "steps",
        "output_format",
        "output_resolution",
        "output_fps",
        "output_bitrate",
    ):
        value = getattr(body, field_name, None)
        if value is not None:
            update_fields[field_name] = value

    if not update_fields:
        raise HTTPException(status_code=400, detail="未提供任何更新字段。")

    try:
        tmpl = service.update_template(template_id, update_fields)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"更新模板失败: {exc}")

    return _template_to_response(tmpl)


@router.delete("/{template_id}", status_code=204)
def delete_template(
    template_id: str,
    service: TemplateService = Depends(_get_template_service),
):
    """删除模板（内置模板返回 403）。"""
    try:
        existing = service.get_template(template_id)
    except Exception:
        existing = None

    if existing is None:
        raise HTTPException(status_code=404, detail=f"模板不存在: {template_id}")

    if getattr(existing, "is_builtin", False):
        raise HTTPException(status_code=403, detail="内置模板不允许删除。")

    try:
        service.delete_template(template_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"删除模板失败: {exc}")

    return None


@router.post("/{template_id}/apply")
def apply_template(
    template_id: str,
    body: TemplateApplyRequest,
    service: TemplateService = Depends(_get_template_service),
    video_editing_service=Depends(get_video_editing_service),
):
    """执行模板，对源视频应用模板中的剪辑步骤。

    返回 VideoEditTask 信息。
    """
    # 确认模板存在
    try:
        tmpl = service.get_template(template_id)
    except Exception:
        tmpl = None

    if tmpl is None:
        raise HTTPException(status_code=404, detail=f"模板不存在: {template_id}")

    try:
        task = service.apply_template(
            template_id=template_id,
            source_video_path=body.source_video_path,
            service=video_editing_service,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"执行模板失败: {exc}")

    # 返回任务信息（兼容 VideoEditTask 字段）
    return {
        "task_id": getattr(task, "task_id", ""),
        "status": str(getattr(task, "status", "unknown")),
        "result_path": getattr(task, "result_path", None),
        "result_size_bytes": getattr(task, "result_size_bytes", None),
        "error_message": getattr(task, "error_message", None),
    }
