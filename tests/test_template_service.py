"""TemplateService 测试。

覆盖：加载内置模板、CRUD 操作、分类过滤、模板应用。
使用临时目录隔离测试环境。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.adapters.video_editor import SandboxVideoEditor
from src.models import (
    EditTemplate,
    TaskStatus,
    TemplateStepDef,
    VideoEditConfig,
    VideoEditStep,
    VideoEditStepKind,
)
from src.repositories.mock import MockRepository
from src.services.template_service import TemplateService, TemplateServiceError
from src.services.video_editor import VideoEditingService


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _builtin_json_path() -> Path:
    """返回项目内置模板 JSON 路径。"""
    return Path(__file__).parent.parent / "data" / "templates" / "builtin.json"


def _write_builtin_json(tmp_dir: Path) -> None:
    """将内置模板 JSON 复制到临时目录。"""
    src = _builtin_json_path()
    dst = tmp_dir / "builtin.json"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _make_test_template(**overrides) -> EditTemplate:
    """创建测试用模板。"""
    defaults = {
        "template_id": "test-template",
        "name": "测试模板",
        "description": "用于测试",
        "category": "custom",
        "icon": "TestOutlined",
        "steps": [
            TemplateStepDef(
                kind="ai_volume_norm",
                params={"target_i": -16},
                label="音量标准化",
            ),
        ],
        "output_format": "mp4",
        "output_resolution": "1080x1920",
        "output_fps": 30,
        "output_bitrate": "4M",
        "is_builtin": False,
    }
    defaults.update(overrides)
    return EditTemplate(**defaults)


# ---------------------------------------------------------------------------
# 测试类
# ---------------------------------------------------------------------------


class TestLoadBuiltinTemplates:
    """测试加载内置模板。"""

    def test_load_builtin_templates_count(self):
        """内置模板应有 5 个。"""
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)
            templates = svc.list_templates()
            assert len(templates) == 5

    def test_load_builtin_templates_ids(self):
        """内置模板应包含指定的 template_id。"""
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)
            ids = {t.template_id for t in svc.list_templates()}
            expected = {
                "short_video_optimize",
                "auto_subtitle",
                "social_media_ready",
                "podcast_clean",
                "subtitle_and_polish",
            }
            assert ids == expected

    def test_builtin_templates_all_builtin_flag(self):
        """所有内置模板的 is_builtin 应为 True。"""
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)
            for t in svc.list_templates():
                assert t.is_builtin is True

    def test_builtin_templates_categories(self):
        """内置模板应包含不同分类。"""
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)
            categories = {t.category for t in svc.list_templates()}
            assert "optimization" in categories
            assert "subtitle" in categories
            assert "social_media" in categories
            assert "podcast" in categories

    def test_no_builtin_json_returns_empty(self):
        """没有 builtin.json 时应返回空列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            svc = TemplateService(tmp)
            assert svc.list_templates() == []


class TestFilterByCategory:
    """测试按分类过滤。"""

    def test_filter_by_existing_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)
            subtitle_templates = svc.list_templates(category="subtitle")
            assert len(subtitle_templates) == 2
            assert all(t.category == "subtitle" for t in subtitle_templates)

    def test_filter_by_nonexistent_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)
            result = svc.list_templates(category="nonexistent")
            assert result == []

    def test_filter_none_returns_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)
            all_templates = svc.list_templates(category=None)
            assert len(all_templates) == 5


class TestGetTemplate:
    """测试获取单个模板。"""

    def test_get_existing_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)
            template = svc.get_template("auto_subtitle")
            assert template is not None
            assert template.name == "自动字幕生成"
            assert len(template.steps) == 1
            assert template.steps[0].kind == "ai_subtitle"

    def test_get_nonexistent_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)
            assert svc.get_template("nonexistent") is None


class TestCreateTemplate:
    """测试创建自定义模板。"""

    def test_create_template_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)

            new_template = _make_test_template(name="我的模板")
            result = svc.create_template(new_template)

            assert result.template_id.startswith("custom-")
            assert result.name == "我的模板"
            assert result.is_builtin is False
            assert result.created_at is not None
            assert result.updated_at is not None

            # 验证可以获取
            fetched = svc.get_template(result.template_id)
            assert fetched is not None
            assert fetched.name == "我的模板"

    def test_create_template_duplicate_name_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)

            # 尝试创建与内置模板同名的模板
            duplicate = _make_test_template(name="自动字幕生成")
            with pytest.raises(TemplateServiceError, match="已存在"):
                svc.create_template(duplicate)

    def test_create_template_persists(self):
        """创建的模板应持久化到 custom.json。"""
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)

            svc.create_template(_make_test_template(name="持久化测试"))

            # 重新加载服务
            svc2 = TemplateService(tmp)
            templates = svc2.list_templates()
            assert any(t.name == "持久化测试" for t in templates)
            assert len(templates) == 6  # 5 内置 + 1 自定义


class TestUpdateTemplate:
    """测试更新自定义模板。"""

    def test_update_template_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)

            created = svc.create_template(_make_test_template(name="更新测试"))
            updated = svc.update_template(
                created.template_id,
                {"name": "更新后", "description": "新描述"},
            )

            assert updated.name == "更新后"
            assert updated.description == "新描述"
            assert updated.updated_at is not None

    def test_update_builtin_template_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)

            with pytest.raises(TemplateServiceError, match="内置模板不可修改"):
                svc.update_template("auto_subtitle", {"name": "新名称"})

    def test_update_nonexistent_template_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)

            with pytest.raises(TemplateServiceError, match="不存在"):
                svc.update_template("nonexistent", {"name": "新名称"})


class TestDeleteTemplate:
    """测试删除自定义模板。"""

    def test_delete_template_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)

            created = svc.create_template(_make_test_template(name="删除测试"))
            result = svc.delete_template(created.template_id)

            assert result is True
            assert svc.get_template(created.template_id) is None

    def test_delete_builtin_template_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)

            with pytest.raises(TemplateServiceError, match="内置模板不可删除"):
                svc.delete_template("auto_subtitle")

    def test_delete_nonexistent_template_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)

            with pytest.raises(TemplateServiceError, match="不存在"):
                svc.delete_template("nonexistent")

    def test_delete_persists(self):
        """删除操作应持久化。"""
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)

            created = svc.create_template(_make_test_template(name="持久删除"))
            svc.delete_template(created.template_id)

            # 重新加载
            svc2 = TemplateService(tmp)
            assert svc2.get_template(created.template_id) is None
            assert len(svc2.list_templates()) == 5


class TestApplyTemplate:
    """测试应用模板。"""

    def setup_method(self):
        self._temp_dir = tempfile.mkdtemp()
        self._temp_video = Path(self._temp_dir) / "test_video.mp4"
        self._temp_video.write_bytes(b"\x00" * 100)

    def teardown_method(self):
        self._temp_video.unlink(missing_ok=True)
        Path(self._temp_dir).rmdir()

    def test_apply_template_success(self):
        """应用内置模板应成功执行。"""
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)

            repo = MockRepository()
            editor = SandboxVideoEditor()
            video_svc = VideoEditingService(repo, editor)

            task = svc.apply_template(
                "auto_subtitle",
                str(self._temp_video),
                video_svc,
            )

            assert task.status == TaskStatus.SUCCEEDED
            assert task.result_path is not None

    def test_apply_nonexistent_template_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_builtin_json(Path(tmp))
            svc = TemplateService(tmp)

            repo = MockRepository()
            editor = SandboxVideoEditor()
            video_svc = VideoEditingService(repo, editor)

            with pytest.raises(TemplateServiceError, match="不存在"):
                svc.apply_template("nonexistent", str(self._temp_video), video_svc)


class TestBuiltinJsonIntegrity:
    """测试 builtin.json 文件完整性。"""

    def test_builtin_json_is_valid_json(self):
        path = _builtin_json_path()
        assert path.exists(), "builtin.json 不存在"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 5

    def test_builtin_json_required_fields(self):
        """每个内置模板应包含必要字段。"""
        path = _builtin_json_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        required_fields = {"template_id", "name", "steps"}
        for item in data:
            assert required_fields.issubset(item.keys()), f"模板缺少必要字段: {item.get('template_id')}"

    def test_builtin_json_steps_have_kind(self):
        """每个步骤应有 kind 字段。"""
        path = _builtin_json_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data:
            for step in item.get("steps", []):
                assert "kind" in step, f"模板 {item['template_id']} 的步骤缺少 kind"


class TestEditTemplateModel:
    """测试 EditTemplate 模型。"""

    def test_create_edit_template(self):
        template = EditTemplate(
            template_id="test",
            name="测试",
        )
        assert template.template_id == "test"
        assert template.name == "测试"
        assert template.category == "custom"
        assert template.is_builtin is True
        assert template.steps == []

    def test_edit_template_frozen(self):
        """EditTemplate 应为 frozen。"""
        template = EditTemplate(template_id="test", name="测试")
        with pytest.raises(Exception):
            template.name = "新名称"  # type: ignore

    def test_template_step_def(self):
        step = TemplateStepDef(
            kind="ai_volume_norm",
            params={"target_i": -16},
            label="音量标准化",
        )
        assert step.kind == "ai_volume_norm"
        assert step.params == {"target_i": -16}
        assert step.label == "音量标准化"

    def test_template_step_def_frozen(self):
        step = TemplateStepDef(kind="trim")
        with pytest.raises(Exception):
            step.kind = "new_kind"  # type: ignore
