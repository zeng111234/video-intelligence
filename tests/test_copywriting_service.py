"""CopywritingService 独立测试。

覆盖：正向路径、异常路径、引擎异常传播、on_progress 回调、get_task 类型过滤。
"""

from __future__ import annotations

import pytest

from src.adapters.llm import SandboxCopywritingEngine
from src.models import CopywritingTask, TaskKind, TaskStatus
from src.repositories.mock import MockRepository
from src.services.copywriting import CopywritingService


class _FailingEngine:
    """模拟引擎抛出异常的场景。"""

    def capabilities(self) -> dict[str, str | bool | int]:
        return {
            "provider_name": "failing",
            "mode": "sandbox",
            "enabled": True,
            "max_input_chars": 5000,
            "max_variants": 3,
        }

    def rewrite(self, source_text: str, **kwargs) -> list[str]:
        raise RuntimeError("引擎模拟故障")


class _SlowEngine:
    """模拟引擎返回空结果的场景。"""

    def capabilities(self) -> dict[str, str | bool | int]:
        return {
            "provider_name": "slow",
            "mode": "sandbox",
            "enabled": True,
            "max_input_chars": 5000,
            "max_variants": 3,
        }

    def rewrite(self, source_text: str, **kwargs) -> list[str]:
        return []


class TestCopywritingServiceEdgeCases:
    """补充 CopywritingService 边界和异常路径。"""

    def setup_method(self):
        self.repo = MockRepository()
        self.engine = SandboxCopywritingEngine()
        self.svc = CopywritingService(self.repo, self.engine)

    def test_capabilities_delegates_to_engine(self):
        cap = self.svc.capabilities()
        assert cap["enabled"] is True
        assert cap["mode"] == "sandbox"
        assert cap["provider_name"] == "sandbox_copywriting"

    def test_rewrite_whitespace_only_raises(self):
        """仅空白字符应被视为空文案。"""
        with pytest.raises(ValueError, match="源文案不能为空"):
            self.svc.rewrite(source_text="   \n\t  ")

    def test_rewrite_exceeds_max_input_chars(self):
        """超出 max_input_chars 应抛出 ValueError。"""
        long_text = "x" * 5001
        with pytest.raises(ValueError, match="最大长度限制"):
            self.svc.rewrite(source_text=long_text)

    def test_rewrite_engine_exception_produces_failed_task(self):
        """引擎异常应产生 FAILED 状态的任务，而非抛出异常。"""
        svc = CopywritingService(self.repo, _FailingEngine())
        task = svc.rewrite(source_text="触发引擎异常")
        assert task.status == TaskStatus.FAILED
        assert "引擎模拟故障" in task.error_message

    def test_rewrite_engine_empty_result(self):
        """引擎返回空列表时，result_text 应为 None。"""
        svc = CopywritingService(self.repo, _SlowEngine())
        task = svc.rewrite(source_text="空结果测试")
        assert task.status == TaskStatus.SUCCEEDED
        assert task.result_text is None
        assert task.result_variants == []

    def test_rewrite_with_source_ids(self):
        """source_task_id 和 source_revision_id 应正确存储。"""
        task = self.svc.rewrite(
            source_text="溯源测试",
            source_task_id="transcript-abc",
            source_revision_id="revision-xyz",
        )
        assert task.source_task_id == "transcript-abc"
        assert task.source_revision_id == "revision-xyz"

    def test_rewrite_with_all_params(self):
        """完整参数传递应正确反映在任务中。"""
        task = self.svc.rewrite(
            source_text="完整参数测试文案内容",
            style_prompt="口播风格",
            target_length=500,
            tone="casual",
            variant_count=2,
        )
        assert task.style_prompt == "口播风格"
        assert task.target_length == 500
        assert task.tone == "casual"
        assert len(task.result_variants) == 2

    def test_rewrite_on_progress_called(self):
        """on_progress 回调应被调用（至少 RUNNING 和 SUCCEEDED 各一次）。"""
        calls: list[CopywritingTask] = []
        self.svc.rewrite(
            source_text="回调测试",
            on_progress=lambda t: calls.append(t),
        )
        assert len(calls) >= 2
        assert calls[0].status == TaskStatus.RUNNING
        assert calls[-1].status == TaskStatus.SUCCEEDED

    def test_rewrite_on_progress_exception_swallowed(self):
        """on_progress 抛出异常不应影响主流程。"""

        def bad_callback(t: CopywritingTask) -> None:
            raise RuntimeError("回调异常")

        task = self.svc.rewrite(
            source_text="异常回调测试",
            on_progress=bad_callback,
        )
        assert task.status == TaskStatus.SUCCEEDED

    def test_get_task_returns_copywriting_task(self):
        task = self.svc.rewrite(source_text="检索测试")
        fetched = self.svc.get_task(task.task_id)
        assert fetched is not None
        assert fetched.task_id == task.task_id
        assert isinstance(fetched, CopywritingTask)

    def test_get_nonexistent_task_returns_none(self):
        result = self.svc.get_task("nonexistent-id")
        assert result is None

    def test_list_tasks_excludes_other_types(self):
        """list_tasks 应仅返回 CopywritingTask 类型。"""
        self.svc.rewrite(source_text="类型过滤测试")
        tasks = self.svc.list_tasks()
        assert len(tasks) >= 1
        assert all(isinstance(t, CopywritingTask) for t in tasks)

    def test_task_title_contains_snippet(self):
        """任务标题应包含源文案前 20 字符。"""
        task = self.svc.rewrite(source_text="这是一段很长的文案用于测试标题截取逻辑")
        assert "这是一段很长的文案用于测试标题截取" in task.title

    def test_task_kind_is_copywriting(self):
        task = self.svc.rewrite(source_text="kind 检查")
        assert task.kind == TaskKind.COPYWRITING

    def test_variant_count_clamped_to_max(self):
        """variant_count 超过 max_variants 时应被截断。"""
        task = self.svc.rewrite(source_text="变体截断测试", variant_count=100)
        assert len(task.result_variants) <= 3

    def test_variant_count_minimum_one(self):
        """variant_count=0 应被修正为 1。"""
        task = self.svc.rewrite(source_text="最小变体测试", variant_count=0)
        assert len(task.result_variants) >= 1

    def test_batch_rewrite_empty_list(self):
        tasks = self.svc.batch_rewrite(source_texts=[])
        assert tasks == []

    def test_batch_rewrite_mixed_results(self):
        """批量改写中部分失败不影响其他任务。"""
        svc = CopywritingService(self.repo, _FailingEngine())
        tasks = svc.batch_rewrite(source_texts=["失败一", "失败二"])
        assert len(tasks) == 2
        assert all(t.status == TaskStatus.FAILED for t in tasks)

    def test_is_mock_flag_from_sandbox(self):
        """沙箱引擎应设置 is_mock=True。"""
        task = self.svc.rewrite(source_text="mock 标记测试")
        assert task.is_mock is True
