"""模板服务。

管理视频编辑模板，支持内置模板和自定义模板的 CRUD 操作。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.models import (
    EditTemplate,
    TemplateStepDef,
    VideoEditConfig,
    VideoEditStep,
    VideoEditStepKind,
    VideoEditTask,
)


class TemplateServiceError(RuntimeError):
    """模板操作失败。"""

    def __init__(self, message: str, *, code: str = "template_error") -> None:
        super().__init__(message)
        self.code = code


class TemplateService:
    """模板服务。

    负责加载、管理和应用视频编辑模板。
    """

    def __init__(
        self,
        templates_dir: str,
        *,
        builtin_templates_dir: str | None = None,
    ) -> None:
        """初始化模板服务。

        Args:
            templates_dir: 模板存储目录路径
        """
        self.templates_dir = Path(templates_dir)
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.builtin_templates_dir = Path(
            builtin_templates_dir or templates_dir
        )

        self._builtin_templates: list[EditTemplate] = []
        self._custom_templates: list[EditTemplate] = []
        self._load_all_templates()

    def _load_all_templates(self) -> None:
        """加载所有模板。"""
        self._builtin_templates = self._load_builtin_templates()
        self._custom_templates = self._load_custom_templates()

    def _load_builtin_templates(self) -> list[EditTemplate]:
        """从 JSON 加载内置模板。

        Returns:
            内置模板列表
        """
        builtin_path = self.builtin_templates_dir / "builtin.json"
        if not builtin_path.exists():
            return []

        try:
            data = json.loads(builtin_path.read_text(encoding="utf-8"))
            templates: list[EditTemplate] = []
            for item in data:
                steps = [
                    TemplateStepDef(
                        kind=s["kind"],
                        params=s.get("params", {}),
                        label=s.get("label", ""),
                    )
                    for s in item.get("steps", [])
                ]
                templates.append(
                    EditTemplate(
                        template_id=item["template_id"],
                        name=item["name"],
                        description=item.get("description", ""),
                        category=item.get("category", "custom"),
                        icon=item.get("icon", "RocketOutlined"),
                        steps=steps,
                        output_format=item.get("output_format", "mp4"),
                        output_resolution=item.get("output_resolution", "1080x1920"),
                        output_fps=item.get("output_fps", 30),
                        output_bitrate=item.get("output_bitrate", "4M"),
                        is_builtin=True,
                    )
                )
            return templates
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise TemplateServiceError(
                f"内置模板文件格式错误：{exc}",
                code="builtin_load_failed",
            ) from exc

    def _load_custom_templates(self) -> list[EditTemplate]:
        """从 JSON 加载自定义模板。

        Returns:
            自定义模板列表
        """
        custom_path = self.templates_dir / "custom.json"
        if not custom_path.exists():
            return []

        try:
            data = json.loads(custom_path.read_text(encoding="utf-8"))
            templates: list[EditTemplate] = []
            for item in data:
                steps = [
                    TemplateStepDef(
                        kind=s["kind"],
                        params=s.get("params", {}),
                        label=s.get("label", ""),
                    )
                    for s in item.get("steps", [])
                ]
                created_at = None
                updated_at = None
                if item.get("created_at"):
                    created_at = datetime.fromisoformat(item["created_at"])
                if item.get("updated_at"):
                    updated_at = datetime.fromisoformat(item["updated_at"])

                templates.append(
                    EditTemplate(
                        template_id=item["template_id"],
                        name=item["name"],
                        description=item.get("description", ""),
                        category=item.get("category", "custom"),
                        icon=item.get("icon", "RocketOutlined"),
                        steps=steps,
                        output_format=item.get("output_format", "mp4"),
                        output_resolution=item.get("output_resolution", "1080x1920"),
                        output_fps=item.get("output_fps", 30),
                        output_bitrate=item.get("output_bitrate", "4M"),
                        is_builtin=False,
                        created_at=created_at,
                        updated_at=updated_at,
                    )
                )
            return templates
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise TemplateServiceError(
                f"自定义模板文件格式错误：{exc}",
                code="custom_load_failed",
            ) from exc

    def list_templates(self, category: str | None = None) -> list[EditTemplate]:
        """列出所有模板。

        Args:
            category: 按分类过滤（可选）

        Returns:
            模板列表
        """
        all_templates = self._builtin_templates + self._custom_templates
        if category is not None:
            all_templates = [t for t in all_templates if t.category == category]
        return all_templates

    def get_template(self, template_id: str) -> EditTemplate | None:
        """获取单个模板。

        Args:
            template_id: 模板 ID

        Returns:
            模板对象，不存在则返回 None
        """
        for template in self._builtin_templates + self._custom_templates:
            if template.template_id == template_id:
                return template
        return None

    def create_template(self, template: EditTemplate) -> EditTemplate:
        """创建自定义模板。

        Args:
            template: 模板对象（template_id 会被忽略，自动生成）

        Returns:
            创建后的模板对象

        Raises:
            TemplateServiceError: 模板名称已存在
        """
        # 检查名称是否重复
        all_templates = self._builtin_templates + self._custom_templates
        if any(t.name == template.name for t in all_templates):
            raise TemplateServiceError(
                f"模板名称 '{template.name}' 已存在。",
                code="duplicate_name",
            )

        now = datetime.now().astimezone()
        new_template = EditTemplate(
            template_id=f"custom-{uuid4().hex[:10]}",
            name=template.name,
            description=template.description,
            category=template.category,
            icon=template.icon,
            steps=template.steps,
            output_format=template.output_format,
            output_resolution=template.output_resolution,
            output_fps=template.output_fps,
            output_bitrate=template.output_bitrate,
            is_builtin=False,
            created_at=now,
            updated_at=now,
        )

        self._custom_templates.append(new_template)
        self._save_custom_templates()
        return new_template

    def update_template(self, template_id: str, updates: dict[str, Any]) -> EditTemplate:
        """更新自定义模板。

        Args:
            template_id: 模板 ID
            updates: 更新字段

        Returns:
            更新后的模板对象

        Raises:
            TemplateServiceError: 模板不存在或是内置模板
        """
        # 查找模板
        target = None
        for template in self._custom_templates:
            if template.template_id == template_id:
                target = template
                break

        if target is None:
            # 检查是否是内置模板
            for template in self._builtin_templates:
                if template.template_id == template_id:
                    raise TemplateServiceError(
                        "内置模板不可修改。",
                        code="builtin_readonly",
                    )
            raise TemplateServiceError(
                f"模板 '{template_id}' 不存在。",
                code="not_found",
            )

        # 构建更新后的模板
        now = datetime.now().astimezone()
        update_data = {**updates, "updated_at": now}

        # 处理 steps 字段
        if "steps" in update_data and isinstance(update_data["steps"], list):
            steps = []
            for s in update_data["steps"]:
                if isinstance(s, dict):
                    steps.append(
                        TemplateStepDef(
                            kind=s["kind"],
                            params=s.get("params", {}),
                            label=s.get("label", ""),
                        )
                    )
                elif isinstance(s, TemplateStepDef):
                    steps.append(s)
            update_data["steps"] = steps

        updated_template = target.model_copy(update=update_data)

        # 替换列表中的模板
        self._custom_templates = [
            updated_template if t.template_id == template_id else t
            for t in self._custom_templates
        ]
        self._save_custom_templates()
        return updated_template

    def delete_template(self, template_id: str) -> bool:
        """删除自定义模板。

        Args:
            template_id: 模板 ID

        Returns:
            True 如果删除成功

        Raises:
            TemplateServiceError: 模板不存在或是内置模板
        """
        # 检查是否是内置模板
        for template in self._builtin_templates:
            if template.template_id == template_id:
                raise TemplateServiceError(
                    "内置模板不可删除。",
                    code="builtin_readonly",
                )

        # 查找并删除
        original_count = len(self._custom_templates)
        self._custom_templates = [
            t for t in self._custom_templates if t.template_id != template_id
        ]

        if len(self._custom_templates) == original_count:
            raise TemplateServiceError(
                f"模板 '{template_id}' 不存在。",
                code="not_found",
            )

        self._save_custom_templates()
        return True

    def apply_template(
        self,
        template_id: str,
        source_video_path: str,
        service: Any,  # VideoEditingService
    ) -> VideoEditTask:
        """应用模板到视频。

        Args:
            template_id: 模板 ID
            source_video_path: 源视频路径
            service: VideoEditingService 实例

        Returns:
            视频编辑任务

        Raises:
            TemplateServiceError: 模板不存在
        """
        template = self.get_template(template_id)
        if template is None:
            raise TemplateServiceError(
                f"模板 '{template_id}' 不存在。",
                code="not_found",
            )

        # 将模板步骤转换为 VideoEditConfig
        steps: list[VideoEditStep] = []
        for i, step_def in enumerate(template.steps):
            # 验证 kind 是否有效
            try:
                kind = VideoEditStepKind(step_def.kind)
            except ValueError:
                raise TemplateServiceError(
                    f"模板包含不支持的步骤类型 '{step_def.kind}'。",
                    code="invalid_step_kind",
                )

            steps.append(
                VideoEditStep(
                    kind=kind,
                    params=step_def.params,
                    order=i,
                )
            )

        config = VideoEditConfig(
            steps=steps,
            output_format=template.output_format,
            output_resolution=template.output_resolution,
            output_fps=template.output_fps,
            output_bitrate=template.output_bitrate,
        )

        # 调用 VideoEditingService 执行
        return service.edit_video(
            source_video_path=source_video_path,
            edit_config=config,
        )

    def _save_custom_templates(self) -> None:
        """保存自定义模板到 JSON 文件。"""
        custom_path = self.templates_dir / "custom.json"
        data = []
        for template in self._custom_templates:
            item: dict[str, Any] = {
                "template_id": template.template_id,
                "name": template.name,
                "description": template.description,
                "category": template.category,
                "icon": template.icon,
                "steps": [
                    {
                        "kind": s.kind,
                        "params": s.params,
                        "label": s.label,
                    }
                    for s in template.steps
                ],
                "output_format": template.output_format,
                "output_resolution": template.output_resolution,
                "output_fps": template.output_fps,
                "output_bitrate": template.output_bitrate,
                "is_builtin": False,
            }
            if template.created_at:
                item["created_at"] = template.created_at.isoformat()
            if template.updated_at:
                item["updated_at"] = template.updated_at.isoformat()
            data.append(item)

        custom_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
