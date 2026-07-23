"""文案改写服务。

协调 CopywritingEngine 适配器与 TaskRepository，
支持单次改写、批量改写、变体生成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from src.contracts import CopywritingEngine, TaskRepository
from src.models import (
    CopyResult,
    CopySource,
    CopywritingTask,
    Platform,
    TaskStatus,
    VideoMetricSnapshot,
)


SUPPORTED_COPYWRITING_PLATFORMS = {
    Platform.DOUYIN,
    Platform.XIAOHONGSHU,
    Platform.WECHAT_CHANNELS,
}

METADATA_ORIGINAL_NOTE = (
    "本脚本基于标题/热点词/互动数据原创生成，不是原视频转写，"
    "使用前请人工复核。"
)


@dataclass(frozen=True)
class CopySourceOption:
    """三档文案来源的档位描述，供前端展示与选择。"""

    copy_source: str
    label: str
    estimated_cost_cny: float | None
    is_original_transcript: bool
    needs_manual_review: bool
    prerequisites: list[str] = field(default_factory=list)


class CopywritingService:
    def __init__(
        self,
        repository: TaskRepository,
        engine: CopywritingEngine,
    ) -> None:
        self.repository = repository
        self.engine = engine

    def capabilities(self) -> dict[str, Any]:
        return self.engine.capabilities()

    # ------------------------------------------------------------------
    # 三档文案来源
    # ------------------------------------------------------------------

    def copy_source_options(self) -> list[CopySourceOption]:
        """返回三档文案来源的档位描述。"""
        return [
            CopySourceOption(
                copy_source=CopySource.METADATA_ORIGINAL.value,
                label="平台信息生成文案",
                estimated_cost_cny=None,
                is_original_transcript=False,
                needs_manual_review=True,
                prerequisites=[],
            ),
            CopySourceOption(
                copy_source=CopySource.DOUBAO_MOBILE_TRANSCRIPT.value,
                label="手机豆包 0 元转写",
                estimated_cost_cny=0.0,
                is_original_transcript=True,
                needs_manual_review=False,
                prerequisites=[
                    "已登录的安卓设备与豆包 App",
                    "Appium/ADB 链路可用",
                    "遇到验证码或登录弹窗需人工处理",
                ],
            ),
            CopySourceOption(
                copy_source=CopySource.AUTHORIZED_ASR_TRANSCRIPT.value,
                label="授权 ASR 转写",
                estimated_cost_cny=None,
                is_original_transcript=True,
                needs_manual_review=False,
                prerequisites=["用户确认授权后走媒体解析 + ASR，按量计费"],
            ),
        ]

    def generate_metadata_original(
        self,
        *,
        title: str,
        hot_words: list[str] | None = None,
        metrics: VideoMetricSnapshot | None = None,
        platform: str = "douyin",
        target_audience: str = "",
        style_prompt: str = "",
        target_length: int = 300,
        on_progress: Callable[[CopywritingTask], None] | None = None,
    ) -> CopywritingTask:
        """基于标题/热点词/互动数据生成适合数字人口播的文案。

        结果不是原视频逐字转写，使用前仍需人工核对事实与表达。
        """
        if not title.strip():
            raise ValueError("标题不能为空。")
        platform_enum = self._validate_platform(platform)
        brief_parts = [f"参考视频标题：{title.strip()}"]
        if hot_words:
            brief_parts.append("关联热点词：" + "、".join(hot_words))
        if metrics is not None:
            interactions = []
            if metrics.likes is not None:
                interactions.append(f"点赞 {metrics.likes}")
            if metrics.comments is not None:
                interactions.append(f"评论 {metrics.comments}")
            if metrics.shares is not None:
                interactions.append(f"分享 {metrics.shares}")
            if metrics.favorites is not None:
                interactions.append(f"收藏 {metrics.favorites}")
            if interactions:
                brief_parts.append("互动数据：" + "，".join(interactions))
        brief_parts.append(
            "请仅依据以上信息生成适合数字人口播的短视频文案。"
            "不要复述或假装还原原视频的逐字内容。"
        )
        content_brief = "\n".join(brief_parts)
        digital_human_style_prompt = "\n".join(
            [
                "适合数字人口播：口语化、短句、有停顿感。",
                "时长控制在 30–45 秒；按一行一句输出，每句尽量 8–18 个汉字。",
                "开头直接抛问题、结果或反差，不要自我介绍。",
                "每 2–3 秒提供一个新信息点，最后只保留一个互动动作。",
                "不要使用开场钩子、主体、CTA 等栏目标题。",
                style_prompt,
            ]
        ).strip()

        cap = self.engine.capabilities()
        target_length = max(50, min(target_length, 800))
        now = datetime.now().astimezone()
        task = CopywritingTask(
            task_id=f"copy-{uuid4().hex[:10]}",
            title=f"数字人口播文案 · {title.strip()[:20]}",
            status=TaskStatus.RUNNING,
            progress=10,
            created_at=now,
            updated_at=now,
            creation_mode="metadata_original",
            content_brief=content_brief,
            platform=platform_enum,
            target_audience=target_audience,
            style_prompt=digital_human_style_prompt,
            target_length=target_length,
            provider_name=str(cap.get("provider_name", "unknown")),
            model_name=str(cap.get("model", "")),
            stage="正在生成文案",
            is_mock=bool(cap.get("mode") == "sandbox"),
            copy_source=CopySource.METADATA_ORIGINAL.value,
            is_original_transcript=False,
            needs_manual_review=True,
            estimated_cost_cny=None,
        )
        self._save(task, on_progress)
        try:
            results = self.engine.generate(
                content_brief=content_brief,
                platform=platform_enum.value,
                target_audience=target_audience,
                style_prompt=digital_human_style_prompt,
                target_length=target_length,
                tone="conversational",
                variant_count=1,
            )
            if not results:
                raise RuntimeError("LLM 未返回有效内容。")
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "文案已生成（需人工复核）",
                    "updated_at": datetime.now().astimezone(),
                    "token_usage": self._last_usage(),
                    "result_text": results[0],
                    "result_variants": results,
                }
            )
            self._save(task, on_progress)
            return task
        except Exception as exc:
            task = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "stage": "生成文案失败",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": str(exc),
                }
            )
            self._save(task, on_progress)
            return task

    @staticmethod
    def build_copy_result(
        *,
        copy_source: str | CopySource,
        text: str,
        candidate_id: str | None = None,
        estimated_cost_cny: float | None = None,
        source_basis: dict[str, Any] | None = None,
        notes: list[str] | None = None,
    ) -> CopyResult:
        """构造三档文案统一结果载荷。

        - metadata_original：非原版转写，必须人工复核。
        - doubao_mobile_transcript：费用恒 0（实际链路由后端/手机豆包服务负责）。
        - authorized_asr_transcript：授权后媒体解析 + ASR，暴露预计成本。
        """
        try:
            source = CopySource(copy_source)
        except ValueError as exc:
            raise ValueError(
                "文案来源只支持 metadata_original、doubao_mobile_transcript、"
                "authorized_asr_transcript。"
            ) from exc
        result_notes = list(notes or [])
        if source is CopySource.METADATA_ORIGINAL:
            is_original_transcript = False
            needs_manual_review = True
            cost = estimated_cost_cny
            result_notes.append(METADATA_ORIGINAL_NOTE)
        elif source is CopySource.DOUBAO_MOBILE_TRANSCRIPT:
            is_original_transcript = True
            needs_manual_review = False
            cost = 0.0  # 手机豆包链路费用恒 0
        else:
            is_original_transcript = True
            needs_manual_review = False
            cost = estimated_cost_cny
        return CopyResult(
            candidate_id=candidate_id,
            copy_source=source,
            text=text,
            is_original_transcript=is_original_transcript,
            needs_manual_review=needs_manual_review,
            estimated_cost_cny=cost,
            source_basis=source_basis or {},
            notes=result_notes,
        )

    def rewrite(
        self,
        *,
        source_text: str,
        platform: str = "douyin",
        target_audience: str = "",
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
        rewrite_goal: str = "",
        variant_count: int = 1,
        source_task_id: str | None = None,
        source_revision_id: str | None = None,
        on_progress: Callable[[CopywritingTask], None] | None = None,
    ) -> CopywritingTask:
        """创建文案改写任务并同步执行。"""
        if not source_text.strip():
            raise ValueError("源文案不能为空。")
        platform_enum = self._validate_platform(platform)
        cap = self.engine.capabilities()
        max_input = int(cap.get("max_input_chars", 5000))
        if len(source_text) > max_input:
            raise ValueError(f"源文案超过最大长度限制（{max_input}字符）。")
        max_variants = int(cap.get("max_variants", 3))
        variant_count = max(1, min(variant_count, max_variants))
        target_length = max(50, min(target_length, 800))

        now = datetime.now().astimezone()
        task = CopywritingTask(
            task_id=f"copy-{uuid4().hex[:10]}",
            title=f"文案改写 · {source_text[:20]}...",
            status=TaskStatus.RUNNING,
            progress=10,
            created_at=now,
            updated_at=now,
            creation_mode="rewrite",
            source_text=source_text,
            platform=platform_enum,
            target_audience=target_audience,
            style_prompt=style_prompt,
            target_length=target_length,
            tone=tone,
            rewrite_goal=rewrite_goal,
            provider_name=str(cap.get("provider_name", "unknown")),
            model_name=str(cap.get("model", "")),
            source_task_id=source_task_id,
            source_revision_id=source_revision_id,
            stage="正在改写",
            is_mock=bool(cap.get("mode") == "sandbox"),
        )
        self._save(task, on_progress)
        try:
            results = self.engine.rewrite(
                source_text,
                platform=platform_enum.value,
                target_audience=target_audience,
                style_prompt=style_prompt,
                target_length=target_length,
                tone=tone,
                rewrite_goal=rewrite_goal,
                variant_count=variant_count,
            )
            if not results:
                raise RuntimeError("LLM 未返回有效内容。")
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "改写完成",
                    "updated_at": datetime.now().astimezone(),
                    "token_usage": self._last_usage(),
                    "result_text": results[0] if results else None,
                    "result_variants": results,
                }
            )
            self._save(task, on_progress)
            return task
        except Exception as exc:
            task = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "stage": "改写失败",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": str(exc),
                }
            )
            self._save(task, on_progress)
            return task

    def generate(
        self,
        *,
        content_brief: str,
        platform: str = "douyin",
        target_audience: str = "",
        selling_points: str = "",
        call_to_action: str = "",
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
        variant_count: int = 1,
        on_progress: Callable[[CopywritingTask], None] | None = None,
    ) -> CopywritingTask:
        """创建从需求生成文案任务并同步执行。"""
        if not content_brief.strip():
            raise ValueError("内容概要不能为空。")
        platform_enum = self._validate_platform(platform)
        cap = self.engine.capabilities()
        max_input = int(cap.get("max_input_chars", 5000))
        if len(content_brief) > max_input:
            raise ValueError(f"内容概要超过最大长度限制（{max_input}字符）。")
        max_variants = int(cap.get("max_variants", 3))
        variant_count = max(1, min(variant_count, max_variants))
        target_length = max(50, min(target_length, 800))

        now = datetime.now().astimezone()
        task = CopywritingTask(
            task_id=f"copy-{uuid4().hex[:10]}",
            title=f"文案生成 · {content_brief[:20]}...",
            status=TaskStatus.RUNNING,
            progress=10,
            created_at=now,
            updated_at=now,
            creation_mode="generate",
            content_brief=content_brief,
            platform=platform_enum,
            target_audience=target_audience,
            selling_points=selling_points,
            call_to_action=call_to_action,
            style_prompt=style_prompt,
            target_length=target_length,
            tone=tone,
            provider_name=str(cap.get("provider_name", "unknown")),
            model_name=str(cap.get("model", "")),
            stage="正在生成",
            is_mock=bool(cap.get("mode") == "sandbox"),
        )
        self._save(task, on_progress)
        try:
            results = self.engine.generate(
                content_brief=content_brief,
                platform=platform_enum.value,
                target_audience=target_audience,
                selling_points=selling_points,
                call_to_action=call_to_action,
                style_prompt=style_prompt,
                target_length=target_length,
                tone=tone,
                variant_count=variant_count,
            )
            if not results:
                raise RuntimeError("LLM 未返回有效内容。")
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "生成完成",
                    "updated_at": datetime.now().astimezone(),
                    "token_usage": self._last_usage(),
                    "result_text": results[0],
                    "result_variants": results,
                }
            )
            self._save(task, on_progress)
            return task
        except Exception as exc:
            task = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "stage": "生成失败",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": str(exc),
                }
            )
            self._save(task, on_progress)
            return task

    def batch_rewrite(
        self,
        *,
        source_texts: list[str],
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
    ) -> list[CopywritingTask]:
        """批量文案改写。"""
        tasks: list[CopywritingTask] = []
        for text in source_texts:
            task = self.rewrite(
                source_text=text,
                style_prompt=style_prompt,
                target_length=target_length,
                tone=tone,
            )
            tasks.append(task)
        return tasks

    def list_tasks(self) -> list[CopywritingTask]:
        return [
            t for t in self.repository.list_tasks() if isinstance(t, CopywritingTask)
        ]

    def get_task(self, task_id: str) -> CopywritingTask | None:
        task = self.repository.get_task(task_id)
        return task if isinstance(task, CopywritingTask) else None

    def _save(
        self,
        task: CopywritingTask,
        on_progress: Callable[[CopywritingTask], None] | None,
    ) -> None:
        self.repository.save_task(task)
        if on_progress is not None:
            try:
                on_progress(task)
            except Exception:
                pass

    @staticmethod
    def _validate_platform(platform: str) -> Platform:
        try:
            platform_enum = Platform(platform)
        except ValueError as exc:
            raise ValueError(
                "文案平台只支持 douyin、xiaohongshu、wechat_channels。"
            ) from exc
        if platform_enum not in SUPPORTED_COPYWRITING_PLATFORMS:
            raise ValueError("文案平台只支持 douyin、xiaohongshu、wechat_channels。")
        return platform_enum

    def _last_usage(self) -> dict[str, int]:
        usage = getattr(self.engine, "last_usage", {})
        return dict(usage) if isinstance(usage, dict) else {}
