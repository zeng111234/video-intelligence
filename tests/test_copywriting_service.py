"""CopywritingService 独立测试。

覆盖：正向路径、异常路径、引擎异常传播、on_progress 回调、get_task 类型过滤。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from project.backend.app.api.v1.transcriptions import (
    VoiceoverDraftRequest,
    create_voiceover_draft,
)
from src.adapters.llm import SandboxCopywritingEngine
from src.models import CopywritingTask, TaskKind, TaskStatus, TranscriptSegment
from src.repositories.mock import MockRepository
from src.services.copywriting import CopywritingService


def test_audit_spoken_script_keeps_sandbox_result_truthful():
    service = CopywritingService(MockRepository(), SandboxCopywritingEngine())

    result = service.audit_spoken_script(script_text="这是一段待审核的口播稿。")

    assert result["status"] == "mock"
    assert result["approved"] is False
    assert "未执行真实 AI 文案审核" in result["summary"]


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

    def generate(self, **kwargs) -> list[str]:
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

    def generate(self, **kwargs) -> list[str]:
        return []


class _ComplianceRetryEngine:
    """首次返回高风险表达，第二次返回可复核表达。"""

    def __init__(self, *, remains_risky: bool = False) -> None:
        self.remains_risky = remains_risky
        self.rewrite_calls: list[dict] = []

    def capabilities(self) -> dict[str, str | bool | int]:
        return {
            "provider_name": "compliance-test",
            "mode": "sandbox",
            "enabled": True,
            "max_input_chars": 5000,
            "max_variants": 3,
        }

    def rewrite(self, source_text: str, **kwargs) -> list[str]:
        self.rewrite_calls.append(kwargs)
        if len(self.rewrite_calls) == 1 or self.remains_risky:
            return ["月入5万，保证有效。"]
        return ["这是个人经历，实际效果会因条件不同而不同。"]

    def generate(self, **kwargs) -> list[str]:
        return self.rewrite(kwargs.get("content_brief", ""), **kwargs)


class _SimilarityRetryEngine:
    def __init__(self, *, remains_similar: bool = False) -> None:
        self.remains_similar = remains_similar
        self.rewrite_calls: list[dict] = []
        self.last_usage: dict[str, int] = {}

    def capabilities(self) -> dict[str, str | bool | int]:
        return {
            "provider_name": "similarity-test",
            "mode": "sandbox",
            "enabled": True,
            "max_input_chars": 5000,
            "max_variants": 1,
        }

    def rewrite(self, source_text: str, **kwargs) -> list[str]:
        self.rewrite_calls.append(kwargs)
        self.last_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if len(self.rewrite_calls) == 1 or self.remains_similar:
            return [source_text]
        return [
            "预算在两百元左右，又想兼顾赛车和射击游戏，可以先看摇杆阻尼是否可调。"
            "这款产品提供四套预设和三模连接，GT13 霍尔摇杆可在 30 到 80GF 之间调节，"
            "不同游戏可以直接切换更合适的手感。"
        ]

    def generate(self, **kwargs) -> list[str]:
        return self.rewrite(kwargs.get("content_brief", ""), **kwargs)


class _AttentionEngine:
    last_attention_terms = ["竞品科技", "未出现在结果里的名称"]

    def capabilities(self) -> dict[str, str | bool | int]:
        return {
            "provider_name": "attention-test",
            "mode": "sandbox",
            "enabled": True,
            "max_input_chars": 5000,
            "max_variants": 1,
        }

    def rewrite(self, source_text: str, **kwargs) -> list[str]:
        return ["竞品科技发布了这款工具，主要面向内容团队。"]

    def generate(self, **kwargs) -> list[str]:
        return ["竞品科技发布了这款工具，主要面向内容团队。"]


class _RetryThenFailEngine:
    def __init__(self) -> None:
        self.rewrite_calls = 0
        self.last_attention_terms: list[str] = []

    def capabilities(self) -> dict[str, str | bool | int]:
        return {
            "provider_name": "retry-then-fail",
            "mode": "sandbox",
            "enabled": True,
            "max_input_chars": 5000,
            "max_variants": 1,
        }

    def rewrite(self, source_text: str, **kwargs) -> list[str]:
        self.rewrite_calls += 1
        if self.rewrite_calls == 1:
            self.last_attention_terms = ["竞品科技"]
            return ["竞品科技保证有效，月入5万。"]
        self.last_attention_terms = []
        raise RuntimeError("后续模型调用失败")

    def generate(self, **kwargs) -> list[str]:
        return self.rewrite(kwargs.get("content_brief", ""))


class _OverlongVoiceoverEngine:
    def __init__(self, *, remains_overlong: bool = False) -> None:
        self.rewrite_calls: list[dict] = []
        self.source_texts: list[str] = []
        self.remains_overlong = remains_overlong

    def capabilities(self) -> dict[str, str | bool | int]:
        return {
            "provider_name": "overlong-voiceover",
            "mode": "sandbox",
            "enabled": True,
            "max_input_chars": 5000,
            "max_variants": 1,
        }

    def rewrite(self, source_text: str, **kwargs) -> list[str]:
        self.rewrite_calls.append(kwargs)
        self.source_texts.append(source_text)
        if len(self.rewrite_calls) == 1:
            return ["开头钩子。" + "重复说明。" * 20 + "结尾行动句。"]
        if self.remains_overlong:
            return ["开头钩子。" + "仍然重复。" * 20 + "结尾行动句。"]
        return ["开头钩子。核心事实。结尾行动句。"]

    def generate(self, **kwargs) -> list[str]:
        return self.rewrite(kwargs.get("content_brief", ""), **kwargs)


class _CompactVoiceoverEngine:
    def __init__(self) -> None:
        self.rewrite_calls = 0

    def capabilities(self) -> dict[str, str | bool | int]:
        return {
            "provider_name": "compact-voiceover",
            "mode": "sandbox",
            "enabled": True,
            "max_input_chars": 5000,
            "max_variants": 1,
        }

    def rewrite(self, source_text: str, **kwargs) -> list[str]:
        self.rewrite_calls += 1
        return ["新钩子。保留核心事实。结尾行动句。"]

    def generate(self, **kwargs) -> list[str]:
        return self.rewrite(kwargs.get("content_brief", ""), **kwargs)


class _ApprovedRevisionService:
    def get_approved_revision(self, task_id: str):
        return SimpleNamespace(
            revision_id="revision-approved",
            corrected_segments=[TranscriptSegment(text="原始事实。" * 100)],
        )


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

    def test_rewrite_engine_exception_falls_back_to_source(self):
        """引擎异常时使用原文，保证流水线节点成功。"""
        svc = CopywritingService(self.repo, _FailingEngine())
        task = svc.rewrite(source_text="触发引擎异常")
        assert task.status == TaskStatus.SUCCEEDED
        assert task.result_text == "触发引擎异常"
        assert task.compliance_status == "best_effort"
        assert task.error_message is None
        assert any("使用输入内容" in note for note in task.compliance_notes)

    def test_rewrite_engine_empty_result_falls_back_to_source(self):
        """引擎返回空列表时使用原文，保证流水线节点成功。"""
        svc = CopywritingService(self.repo, _SlowEngine())
        task = svc.rewrite(source_text="空结果测试")
        assert task.status == TaskStatus.SUCCEEDED
        assert task.result_text == "空结果测试"
        assert task.result_variants == ["空结果测试"]
        assert task.compliance_status == "best_effort"
        assert task.error_message is None

    def test_generate_engine_exception_falls_back_to_combined_input(self):
        task = CopywritingService(self.repo, _FailingEngine()).generate(
            content_brief="介绍这款工具",
            selling_points="降低内容成本",
            call_to_action="欢迎了解",
        )

        assert task.status == TaskStatus.SUCCEEDED
        assert task.result_text == "介绍这款工具\n降低内容成本\n欢迎了解"
        assert task.compliance_status == "best_effort"
        assert task.error_message is None

    def test_rewrite_automatically_retries_when_risk_expression_remains(self):
        engine = _ComplianceRetryEngine()
        task = CopywritingService(self.repo, engine).rewrite(source_text="分享经验")
        assert task.status == TaskStatus.SUCCEEDED
        assert task.compliance_status == "passed"
        assert task.compliance_rewritten is True
        assert task.compliance_retry_used is True
        assert len(engine.rewrite_calls) == 2
        assert any("已自动重写" in note for note in task.compliance_notes)

    def test_rewrite_persists_and_forwards_customer_skill_and_target_length(self):
        engine = _ComplianceRetryEngine()
        task = CopywritingService(self.repo, engine).rewrite(
            source_text="分享经验",
            skill_prompt="先讲结论，再给三个步骤；语气像老板聊天。",
            target_length=150,
        )

        assert task.skill_prompt == "先讲结论，再给三个步骤；语气像老板聊天。"
        assert task.target_length == 150
        assert engine.rewrite_calls[0]["style_prompt"] == ""
        assert engine.rewrite_calls[0]["skill_prompt"] == task.skill_prompt

    def test_generate_retries_when_model_ignores_target_length(self):
        engine = _OverlongVoiceoverEngine()

        task = CopywritingService(self.repo, engine).generate(
            content_brief="一段需要生成口播的内容。",
            target_length=50,
        )

        assert task.status == TaskStatus.SUCCEEDED
        assert len(engine.rewrite_calls) == 2
        assert engine.rewrite_calls[0]["skill_prompt"] == ""
        assert CopywritingService._spoken_character_count(task.result_text or "") <= 50
        assert task.result_text == "开头钩子。核心事实。结尾行动句。"
        assert any("二次语义压缩达到目标字数" in note for note in task.compliance_notes)

    def test_rewrite_uses_final_version_after_three_risk_attempts(self):
        engine = _ComplianceRetryEngine(remains_risky=True)
        task = CopywritingService(self.repo, engine).rewrite(source_text="分享经验")
        assert task.status == TaskStatus.SUCCEEDED
        assert task.result_text == "月入5万，保证有效。"
        assert task.compliance_status == "best_effort"
        assert len(engine.rewrite_calls) == 3
        assert any("使用最后一次生成结果" in note for note in task.compliance_notes)
        assert task.error_message is None

    def test_rewrite_retries_once_when_output_is_too_similar(self):
        source = (
            "这款手柄采用经典模具，按键位置保持不变，支持四套预设和三模连接。"
            "GT13霍尔摇杆支持30到80GF阻尼调节，售价约200元，适合多种游戏。"
        )
        engine = _SimilarityRetryEngine()

        task = CopywritingService(self.repo, engine).rewrite(source_text=source)

        assert task.compliance_status == "passed"
        assert task.compliance_retry_used is True
        assert len(engine.rewrite_calls) == 2
        assert "去重验收未通过" in engine.rewrite_calls[1]["rewrite_goal"]
        assert any("与原文过于相似" in note for note in task.compliance_notes)
        assert task.token_usage == {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}

    def test_rewrite_uses_final_version_when_three_results_are_still_too_similar(self):
        source = (
            "这款手柄采用经典模具，按键位置保持不变，支持四套预设和三模连接。"
            "GT13霍尔摇杆支持30到80GF阻尼调节，售价约200元，适合多种游戏。"
        )
        engine = _SimilarityRetryEngine(remains_similar=True)

        task = CopywritingService(self.repo, engine).rewrite(source_text=source)

        assert task.status == TaskStatus.SUCCEEDED
        assert task.result_text == source
        assert task.compliance_status == "best_effort"
        assert len(engine.rewrite_calls) == 3
        assert any("使用最后一次生成结果" in note for note in task.compliance_notes)
        assert task.token_usage == {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45}

    def test_rewrite_uses_last_version_when_a_later_retry_errors(self):
        engine = _RetryThenFailEngine()

        task = CopywritingService(self.repo, engine).rewrite(source_text="介绍产品")

        assert task.status == TaskStatus.SUCCEEDED
        assert task.result_text == "竞品科技保证有效，月入5万。"
        assert task.compliance_status == "best_effort"
        assert task.attention_terms == ["竞品科技"]
        assert engine.rewrite_calls == 2
        assert any("最后一次生成结果" in note for note in task.compliance_notes)

    def test_rewrite_keeps_only_attention_terms_present_in_final_copy(self):
        task = CopywritingService(self.repo, _AttentionEngine()).rewrite(
            source_text="介绍一下这个工具"
        )

        assert task.status == TaskStatus.SUCCEEDED
        assert task.compliance_status == "passed"
        assert task.attention_terms == ["竞品科技"]
        assert any("已在文案中高亮" in note for note in task.compliance_notes)

    def test_rewrite_with_source_ids(self):
        """source_task_id 和 source_revision_id 应正确存储。"""
        task = self.svc.rewrite(
            source_text="溯源测试",
            source_task_id="transcript-abc",
            source_revision_id="revision-xyz",
        )
        assert task.source_task_id == "transcript-abc"
        assert task.source_revision_id == "revision-xyz"

    def test_rewrite_persists_voiceover_goal(self):
        task = self.svc.rewrite(
            source_text="需要压缩的长文案",
            rewrite_goal="压缩为 45 秒并删除重复观点",
        )
        assert task.rewrite_goal == "压缩为 45 秒并删除重复观点"

    def test_rewrite_retries_when_the_model_ignores_the_target_length(self):
        engine = _OverlongVoiceoverEngine()

        task = CopywritingService(self.repo, engine).rewrite(
            source_text="一段很长的授权转写。",
            target_length=50,
        )

        assert task.status == TaskStatus.SUCCEEDED
        assert len(engine.rewrite_calls) == 2
        assert "硬性长度要求" in engine.rewrite_calls[0]["rewrite_goal"]
        assert "长度验收未通过" in engine.rewrite_calls[1]["rewrite_goal"]
        assert engine.source_texts[1].startswith("开头钩子。")
        assert CopywritingService._spoken_character_count(task.result_text or "") <= 50
        assert task.result_text == "开头钩子。核心事实。结尾行动句。"
        assert any("二次语义压缩" in note for note in task.compliance_notes)

    def test_rewrite_fails_instead_of_truncating_when_semantic_compression_stays_overlong(self):
        task = CopywritingService(
            self.repo,
            _OverlongVoiceoverEngine(remains_overlong=True),
        ).rewrite(
            source_text="一段很长的授权转写。",
            target_length=50,
        )

        assert task.status == TaskStatus.FAILED
        assert task.result_text is None
        assert "未生成可交给数字人的文案" in (task.error_message or "")

    def test_voiceover_draft_regenerates_an_old_overlong_cached_result(self):
        now = datetime.now().astimezone()
        old_draft = CopywritingTask(
            task_id="copy-overlong-cache",
            title="旧口播稿",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            source_task_id="transcription-1",
            source_revision_id="revision-approved",
            result_text="旧稿。" * 100,
            result_variants=["旧稿。" * 100],
            outputs={"draft_stage": "deduplicate"},
        )
        self.repo.save_task(old_draft)
        engine = _CompactVoiceoverEngine()
        service = CopywritingService(self.repo, engine)

        response = create_voiceover_draft(
            "transcription-1",
            VoiceoverDraftRequest(target_seconds=15),
            transcription_service=_ApprovedRevisionService(),
            copywriting_service=service,
        )

        assert engine.rewrite_calls == 1
        assert response.copywriting_task_id != old_draft.task_id
        assert response.result_text == "新钩子。保留核心事实。结尾行动句。"

    def test_rewrite_with_all_params(self):
        """完整参数传递应正确反映在任务中。"""
        task = self.svc.rewrite(
            source_text="完整参数测试文案内容",
            platform="xiaohongshu",
            target_audience="企业主",
            style_prompt="口播风格",
            target_length=500,
            tone="casual",
            variant_count=2,
        )
        assert task.platform.value == "xiaohongshu"
        assert task.target_audience == "企业主"
        assert task.style_prompt == "口播风格"
        assert task.target_length == 500
        assert task.tone == "casual"
        assert len(task.result_variants) == 2

    def test_generate_success(self):
        task = self.svc.generate(
            content_brief="介绍 AI 短视频获客工具",
            platform="wechat_channels",
            target_audience="市场负责人",
            selling_points="降低内容制作成本",
            call_to_action="私信领取方案",
            variant_count=2,
        )
        assert task.status == TaskStatus.SUCCEEDED
        assert task.creation_mode == "generate"
        assert task.platform.value == "wechat_channels"
        assert task.target_audience == "市场负责人"
        assert task.selling_points == "降低内容制作成本"
        assert task.call_to_action == "私信领取方案"
        assert len(task.result_variants) == 2

    def test_generate_empty_brief_raises(self):
        with pytest.raises(ValueError, match="内容概要不能为空"):
            self.svc.generate(content_brief=" ")

    def test_unsupported_platform_raises(self):
        with pytest.raises(ValueError, match="只支持"):
            self.svc.rewrite(source_text="平台测试", platform="kuaishou")

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

    def test_batch_rewrite_model_errors_keep_every_pipeline_item(self):
        """批量改写遇到模型异常时，每项都回退输入并继续。"""
        svc = CopywritingService(self.repo, _FailingEngine())
        tasks = svc.batch_rewrite(source_texts=["失败一", "失败二"])
        assert len(tasks) == 2
        assert all(t.status == TaskStatus.SUCCEEDED for t in tasks)
        assert [task.result_text for task in tasks] == ["失败一", "失败二"]
        assert all(task.compliance_status == "best_effort" for task in tasks)

    def test_is_mock_flag_from_sandbox(self):
        """沙箱引擎应设置 is_mock=True。"""
        task = self.svc.rewrite(source_text="mock 标记测试")
        assert task.is_mock is True
