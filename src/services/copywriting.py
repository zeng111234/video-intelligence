"""文案改写服务。

协调 CopywritingEngine 适配器与 TaskRepository，
支持单次改写、批量改写、变体生成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from difflib import SequenceMatcher
import json
import re
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

COMPLIANCE_RISK_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "收益或效果承诺",
        re.compile(r"(?:月入|日入|年入|赚[到]?)[0-9一二三四五六七八九十百千万wW]+(?:元|万|w|W)?|稳赚|躺赚|保本"),
        "已将收益或效果承诺改为个人经历或条件性表达。",
    ),
    (
        "绝对化保证",
        re.compile(r"百分之百|100%|一定(?:能|会|有效|成功|赚钱)|保证(?:有效|成功|成交|赚钱)|必定|绝不"),
        "已弱化绝对化、保证性表述。",
    ),
    (
        "医疗或金融效果断言",
        re.compile(r"根治|治愈|药到病除|立刻见效|秒见效|稳赚不赔|投资必赚"),
        "已移除医疗、金融等高风险效果断言。",
    ),
    (
        "虚假官方背书",
        re.compile(r"官方(?:认证|推荐|背书)|平台(?:认证|背书)"),
        "已删除无法核实的官方或平台背书表达。",
    ),
    (
        "平台敏感营销用语",
        re.compile(r"加(?:微信|微|V)|扫码加|二维码加|点击(?:下方)?链接|全网(?:最低|第一)|史上最|最强|顶级"),
        "已清理常见的导流、夸大或排名式营销用语。",
    ),
)

MAX_AUTOMATIC_COPY_ATTEMPTS = 3
COPYWRITING_INPUT_PRICE_KEY = "copywriting_input_cny_per_1k_tokens"
COPYWRITING_OUTPUT_PRICE_KEY = "copywriting_output_cny_per_1k_tokens"
MINIMUM_COPYWRITING_CHARGE = Decimal("0.01")


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

    def _ensure_minimum_credits(
        self,
        *,
        capability: dict[str, Any],
        on_progress: Callable[[CopywritingTask], None] | None = None,
        task: CopywritingTask | None = None,
    ) -> None:
        """真实模型调用前确认客户至少能支付最小计费单位。"""
        if capability.get("mode") != "production":
            return
        if bool(getattr(self.engine, "billing_centrally_managed", False)):
            return
        from src.services.credits import (
            CreditsService,
            get_current_owner,
        )

        if get_current_owner() == "admin":
            return
        if not CreditsService(self.repository).can_afford(MINIMUM_COPYWRITING_CHARGE):
            message = "积分不足，本次文案至少需要 0.01 积分，请先充值。"
            if task is not None:
                failed = task.model_copy(
                    update={
                        "status": TaskStatus.FAILED,
                        "stage": "积分不足",
                        "updated_at": datetime.now().astimezone(),
                        "error_message": message,
                    }
                )
                self._save(failed, on_progress)
            raise ValueError(message)

    @staticmethod
    def token_cost_cny(token_usage: dict[str, int]) -> Decimal:
        """按整次任务的实际输入/输出 Token 合计平台服务价。"""
        from src.services.pricing import get_price

        prompt_tokens = max(0, int(token_usage.get("prompt_tokens", 0)))
        if prompt_tokens == 0:
            prompt_tokens = max(
                0,
                int(token_usage.get("prompt_cache_hit_tokens", 0))
                + int(token_usage.get("prompt_cache_miss_tokens", 0)),
            )
        completion_tokens = max(0, int(token_usage.get("completion_tokens", 0)))
        return (
            Decimal(prompt_tokens)
            * get_price(COPYWRITING_INPUT_PRICE_KEY)
            / Decimal("1000")
            + Decimal(completion_tokens)
            * get_price(COPYWRITING_OUTPUT_PRICE_KEY)
            / Decimal("1000")
        )

    def _charge_token_usage(
        self,
        *,
        capability: dict[str, Any],
        task_id: str,
        token_usage: dict[str, int],
    ) -> float:
        """成功后按实际 Token 扣费，返回本次实际收取积分。"""
        if capability.get("mode") != "production":
            return 0.0
        if bool(getattr(self.engine, "billing_centrally_managed", False)):
            try:
                return max(0.0, float(getattr(self.engine, "last_charged_credits", 0)))
            except (TypeError, ValueError):
                return 0.0
        from src.services.credits import CreditsService, cny_to_credits, get_current_owner

        raw_cost = self.token_cost_cny(token_usage)
        charged = cny_to_credits(raw_cost)
        if charged <= 0 or get_current_owner() == "admin":
            return 0.0
        CreditsService(self.repository).debit(
            charged,
            "AI 文案生成费用（按 Token）",
            ref_type="copywriting",
            ref_id=task_id,
        )
        return float(charged)

    def capabilities(self) -> dict[str, Any]:
        from src.services.pricing import get_price

        capability = dict(self.engine.capabilities())
        capability.update(
            {
                "billing_label": "平台服务价",
                "input_price_credits_per_1k_tokens": str(
                    get_price(COPYWRITING_INPUT_PRICE_KEY)
                ),
                "output_price_credits_per_1k_tokens": str(
                    get_price(COPYWRITING_OUTPUT_PRICE_KEY)
                ),
                "minimum_charge_credits": str(MINIMUM_COPYWRITING_CHARGE),
                "billing_rounding": "整次任务合计后向上进位保留两位小数",
            }
        )
        return capability

    def select_best_spoken_script(
        self,
        *,
        candidates: list[dict[str, str]],
        target_audience: str = "",
        style_prompt: str = "",
    ) -> dict[str, str]:
        """Use the configured real model to choose one transcript for production."""
        capability = self.engine.capabilities()
        if not bool(capability.get("enabled")):
            raise RuntimeError("AI 文案服务不可用，无法自动选稿。")
        decision = self.engine.select_best_spoken_script(
            candidates=candidates,
            target_audience=target_audience,
            style_prompt=style_prompt,
        )
        valid_ids = {
            str(item.get("id") or "").strip()
            for item in candidates
            if str(item.get("id") or "").strip()
        }
        winner_id = str(decision.get("winner_id") or "").strip()
        if winner_id not in valid_ids:
            raise RuntimeError("AI 返回的胜出文案不在本次候选中。")
        return {
            "winner_id": winner_id,
            "reason": str(decision.get("reason") or "综合口播适配度最高。").strip()[:160],
        }

    def audit_spoken_script(
        self,
        *,
        script_text: str,
        target_audience: str = "",
        style_prompt: str = "",
    ) -> dict[str, Any]:
        """Run one explicit AI review for a manual-production spoken script."""
        text = script_text.strip()
        if not text:
            raise ValueError("口播文案为空，无法进行 AI 审核。")
        capability = self.engine.capabilities()
        if not bool(capability.get("enabled")):
            raise RuntimeError("AI 文案服务不可用，无法进行口播文案审核。")
        reviewed = self.engine.review_spoken_script(
            script_text=text,
            target_audience=target_audience,
            style_prompt=style_prompt,
        )
        raw_issues = reviewed.get("issues")
        issues: list[dict[str, str]] = []
        if isinstance(raw_issues, list):
            for item in raw_issues[:8]:
                if not isinstance(item, dict):
                    continue
                message = str(item.get("message") or "").strip()[:100]
                if not message:
                    continue
                severity = str(item.get("severity") or "warning").strip().lower()
                issues.append(
                    {
                        "severity": "block" if severity == "block" else "warning",
                        "category": str(item.get("category") or "文案建议").strip()[:40],
                        "message": message,
                    }
                )
        approved = bool(reviewed.get("approved")) and not any(
            item["severity"] == "block" for item in issues
        )
        return {
            "status": "mock" if bool(reviewed.get("is_mock")) else "completed",
            "approved": approved,
            "summary": str(reviewed.get("summary") or "请人工核对文案内容。").strip()[:160],
            "issues": issues,
        }

    @staticmethod
    def _risk_categories(texts: list[str]) -> list[str]:
        combined = "\n".join(texts)
        return [category for category, pattern, _ in COMPLIANCE_RISK_RULES if pattern.search(combined)]

    @staticmethod
    def _compliance_hint(categories: list[str]) -> str:
        if not categories:
            return ""
        return (
            "风险表达优化：保留可核实事实，但不要复述原敏感措辞；"
            f"重点处理：{'、'.join(categories)}。"
        )

    @staticmethod
    def _similarity_metrics(source_text: str, result_text: str) -> tuple[float, float]:
        """Return sequence similarity and output n-gram overlap for long-form copy."""
        source = re.sub(r"[\W_]+", "", source_text.lower())
        result = re.sub(r"[\W_]+", "", result_text.lower())
        if not source or not result:
            return 0.0, 0.0
        sequence_similarity = SequenceMatcher(None, source, result, autojunk=False).ratio()
        ngram_size = 8
        if min(len(source), len(result)) < ngram_size:
            return sequence_similarity, 0.0
        source_ngrams = {source[index : index + ngram_size] for index in range(len(source) - ngram_size + 1)}
        result_ngrams = {result[index : index + ngram_size] for index in range(len(result) - ngram_size + 1)}
        overlap = len(source_ngrams & result_ngrams) / max(1, len(result_ngrams))
        return sequence_similarity, overlap

    @classmethod
    def _too_similar(cls, source_text: str, results: list[str]) -> bool:
        normalized_source = re.sub(r"[\W_]+", "", source_text)
        if len(normalized_source) < 40:
            return False
        return any(
            sequence_similarity >= 0.82 or ngram_overlap >= 0.35
            for sequence_similarity, ngram_overlap in (
                cls._similarity_metrics(source_text, result) for result in results
            )
        )

    @staticmethod
    def _dedup_retry_hint() -> str:
        return (
            "去重验收未通过：上一版与原文的句序和连续表达过于相似。"
            "本次必须先打散原文顺序，再按受众痛点、关键差异、使用场景和结论重新组织；"
            "除型号、参数、金额、专有名词和必要事实外，不得沿用原句或只替换同义词。"
        )

    @staticmethod
    def _compliance_notes(
        categories: list[str],
        *,
        risk_retry_used: bool,
        dedup_retry_used: bool,
    ) -> list[str]:
        notes = ["已按自然口播节奏优化表达。"]
        for category, _, note in COMPLIANCE_RISK_RULES:
            if category in categories:
                notes.append(note)
        if risk_retry_used:
            notes.append("检测到风险表达后已自动重写，最终结果已通过检查。")
        if dedup_retry_used:
            notes.append("检测到输出与原文过于相似，已自动打散并重新组织表达。")
        return list(dict.fromkeys(notes))

    def _attention_terms(
        self,
        results: list[str],
        raw_terms: list[str] | None = None,
    ) -> list[str]:
        combined = "\n".join(results)
        if raw_terms is None:
            raw_terms = getattr(self.engine, "last_attention_terms", [])
        if not isinstance(raw_terms, list):
            return []
        terms: list[str] = []
        for item in raw_terms:
            term = str(item).strip()
            if 2 <= len(term) <= 40 and term in combined and term not in terms:
                terms.append(term)
        return terms

    def _run_with_compliance(
        self,
        *,
        source_texts: list[str],
        run: Callable[[str], list[str]],
        dedup_source: str = "",
        fallback_results: list[str] | None = None,
    ) -> tuple[list[str], str, list[str], bool, bool]:
        source_categories = self._risk_categories(source_texts)
        categories = list(source_categories)
        risk_retry_used = False
        dedup_retry_used = False
        retry_hint = self._compliance_hint(source_categories)
        last_results: list[str] = []

        def best_effort(
            results: list[str],
            message: str,
            *,
            retry_used: bool,
            generated_version: bool,
        ) -> tuple[list[str], str, list[str], bool, bool]:
            notes = ["流水线节点已完成自动处理。"]
            if risk_retry_used:
                notes.append("已自动尝试处理风险表达。")
            if dedup_retry_used:
                notes.append("已自动尝试降低与原文的表达重复。")
            notes.append(message)
            return (
                results,
                "best_effort",
                notes,
                generated_version and (risk_retry_used or dedup_retry_used),
                retry_used,
            )

        for attempt_index in range(MAX_AUTOMATIC_COPY_ATTEMPTS):
            try:
                results = run(retry_hint)
            except Exception:
                if last_results:
                    return best_effort(
                        last_results,
                        "后续自动优化未返回新版本，已使用最后一次生成结果。",
                        retry_used=attempt_index > 0,
                        generated_version=True,
                    )
                if fallback_results:
                    return best_effort(
                        fallback_results,
                        "模型本次未返回可用版本，已使用输入内容作为最终版本。",
                        retry_used=False,
                        generated_version=False,
                    )
                raise
            if not results:
                if last_results:
                    return best_effort(
                        last_results,
                        "后续自动优化未返回新版本，已使用最后一次生成结果。",
                        retry_used=attempt_index > 0,
                        generated_version=True,
                    )
                if fallback_results:
                    return best_effort(
                        fallback_results,
                        "模型本次未返回可用版本，已使用输入内容作为最终版本。",
                        retry_used=False,
                        generated_version=False,
                    )
                raise RuntimeError("LLM 未返回有效内容。")
            last_results = results

            output_categories = self._risk_categories(results)
            dedup_failed = bool(
                dedup_source and self._too_similar(dedup_source, results)
            )
            categories = list(dict.fromkeys(categories + output_categories))
            if not output_categories and not dedup_failed:
                return (
                    results,
                    "passed",
                    self._compliance_notes(
                        categories,
                        risk_retry_used=risk_retry_used,
                        dedup_retry_used=dedup_retry_used,
                    ),
                    bool(categories) or dedup_retry_used,
                    attempt_index > 0,
                )

            risk_retry_used = risk_retry_used or bool(output_categories)
            dedup_retry_used = dedup_retry_used or dedup_failed
            if attempt_index == MAX_AUTOMATIC_COPY_ATTEMPTS - 1:
                return best_effort(
                    results,
                    f"已完成 {MAX_AUTOMATIC_COPY_ATTEMPTS} 次自动优化，"
                    "为保持流水线连续，已使用最后一次生成结果。",
                    retry_used=True,
                    generated_version=True,
                )

            retry_parts = [
                self._compliance_hint(categories),
                self._dedup_retry_hint() if dedup_failed else "",
            ]
            retry_hint = "；".join(part for part in retry_parts if part)

        if fallback_results:
            return best_effort(
                fallback_results,
                "自动优化未返回新版本，已使用输入内容作为最终版本。",
                retry_used=False,
                generated_version=False,
            )
        raise RuntimeError("自动处理未完成。")

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
        reference_text: str = "",
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
        if reference_text.strip():
            brief_parts.append(
                "操作者提供的可见文案（只可概括和改写，不得逐句复述）："
                + reference_text.strip()[:1500]
            )
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
        self._ensure_minimum_credits(
            capability=cap,
            on_progress=on_progress,
            task=task,
        )
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
            token_usage = self._last_usage()
            charged_credits = self._charge_token_usage(
                capability=cap,
                task_id=task.task_id,
                token_usage=token_usage,
            )
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "文案已生成（需人工复核）",
                    "updated_at": datetime.now().astimezone(),
                    "token_usage": token_usage,
                    "charged_credits": charged_credits,
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

    def generate_publish_metadata(
        self,
        *,
        source_text: str,
        platforms: list[str] | None = None,
        source_task_id: str | None = None,
    ) -> tuple[CopywritingTask, dict[str, object] | None]:
        """Generate publish metadata and persist its source/result for audit."""
        if not source_text.strip():
            raise ValueError("原始文案不能为空。")
        cap = self.engine.capabilities()
        max_input = int(cap.get("max_input_chars", 5000))
        if len(source_text) > max_input:
            raise ValueError(f"原始文案超过最大长度限制（{max_input}字符）。")
        now = datetime.now().astimezone()
        task = CopywritingTask(
            task_id=f"copy-{uuid4().hex[:10]}",
            title=f"发布信息生成 · {source_text[:20]}...",
            status=TaskStatus.RUNNING,
            progress=10,
            created_at=now,
            updated_at=now,
            creation_mode="publish_metadata",
            source_text=source_text,
            source_task_id=source_task_id,
            platform=Platform.DOUYIN,
            provider_name=str(cap.get("provider_name", "unknown")),
            model_name=str(cap.get("model", "")),
            stage="正在生成发布信息",
            is_mock=bool(cap.get("mode") == "sandbox"),
        )
        self._save(task, None)
        self._ensure_minimum_credits(
            capability=cap,
            on_progress=None,
            task=task,
        )
        try:
            generator = getattr(self.engine, "generate_publish_metadata", None)
            if not callable(generator):
                raise RuntimeError("当前文案引擎不支持生成发布信息。")
            metadata = generator(source_text, platforms=platforms or [])
            if not isinstance(metadata, dict):
                raise RuntimeError("LLM 未返回有效的发布信息。")
            title = str(metadata.get("title") or "").strip()[:100]
            description = str(metadata.get("description") or "").strip()[:1000]
            raw_tags = metadata.get("tags")
            tags = (
                list(dict.fromkeys(str(item).strip().lstrip("#")[:30] for item in raw_tags if str(item).strip()))[:8]
                if isinstance(raw_tags, list)
                else []
            )
            if not title or not description:
                raise RuntimeError("LLM 未返回完整的标题和发布描述。")
            result = {"title": title, "description": description, "tags": tags}
            token_usage = self._last_usage()
            charged_credits = self._charge_token_usage(
                capability=cap,
                task_id=task.task_id,
                token_usage=token_usage,
            )
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "发布信息生成完成",
                    "updated_at": datetime.now().astimezone(),
                    "token_usage": token_usage,
                    "charged_credits": charged_credits,
                    "result_text": json.dumps(result, ensure_ascii=False),
                    "result_variants": [description],
                }
            )
            self._save(task, None)
            return task, result
        except Exception as exc:
            task = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "stage": "发布信息生成失败",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": str(exc),
                }
            )
            self._save(task, None)
            return task, None

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
        self._ensure_minimum_credits(
            capability=cap,
            on_progress=on_progress,
            task=task,
        )
        try:
            accumulated_usage: dict[str, int] = {}
            latest_attention_terms: list[str] = []

            def run(retry_hint: str) -> list[str]:
                goal = "；".join(item for item in [rewrite_goal.strip(), retry_hint] if item)
                results = self.engine.rewrite(
                    source_text,
                    platform=platform_enum.value,
                    target_audience=target_audience,
                    style_prompt=style_prompt,
                    target_length=target_length,
                    tone=tone,
                    rewrite_goal=goal,
                    variant_count=variant_count,
                )
                self._accumulate_last_usage(accumulated_usage)
                raw_attention_terms = getattr(self.engine, "last_attention_terms", [])
                latest_attention_terms.clear()
                if isinstance(raw_attention_terms, list):
                    latest_attention_terms.extend(str(item) for item in raw_attention_terms)
                return results

            results, compliance_status, compliance_notes, compliance_rewritten, compliance_retry_used = self._run_with_compliance(
                source_texts=[source_text],
                run=run,
                dedup_source=source_text,
                fallback_results=[source_text],
            )
            attention_terms = self._attention_terms(results, latest_attention_terms)
            if attention_terms:
                compliance_notes = [
                    *compliance_notes,
                    "疑似其他企业、品牌、机构或人物名称已在文案中高亮。",
                ]
            token_usage = accumulated_usage or self._last_usage()
            charged_credits = self._charge_token_usage(
                capability=cap,
                task_id=task.task_id,
                token_usage=token_usage,
            )
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "改写完成",
                    "updated_at": datetime.now().astimezone(),
                    "token_usage": token_usage,
                    "charged_credits": charged_credits,
                    "result_text": results[0] if results else None,
                    "result_variants": results,
                    "attention_terms": attention_terms,
                    "compliance_status": compliance_status,
                    "compliance_notes": compliance_notes,
                    "compliance_rewritten": compliance_rewritten,
                    "compliance_retry_used": compliance_retry_used,
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
                    "token_usage": accumulated_usage or self._last_usage(),
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
        self._ensure_minimum_credits(
            capability=cap,
            on_progress=on_progress,
            task=task,
        )
        try:
            accumulated_usage: dict[str, int] = {}
            latest_attention_terms: list[str] = []
            fallback_text = "\n".join(
                item
                for item in [
                    content_brief.strip(),
                    selling_points.strip(),
                    call_to_action.strip(),
                ]
                if item
            )

            def run(retry_hint: str) -> list[str]:
                prompt = "\n".join(item for item in [style_prompt.strip(), retry_hint] if item)
                results = self.engine.generate(
                    content_brief=content_brief,
                    platform=platform_enum.value,
                    target_audience=target_audience,
                    selling_points=selling_points,
                    call_to_action=call_to_action,
                    style_prompt=prompt,
                    target_length=target_length,
                    tone=tone,
                    variant_count=variant_count,
                )
                self._accumulate_last_usage(accumulated_usage)
                raw_attention_terms = getattr(self.engine, "last_attention_terms", [])
                latest_attention_terms.clear()
                if isinstance(raw_attention_terms, list):
                    latest_attention_terms.extend(str(item) for item in raw_attention_terms)
                return results

            results, compliance_status, compliance_notes, compliance_rewritten, compliance_retry_used = self._run_with_compliance(
                source_texts=[content_brief, selling_points, call_to_action],
                run=run,
                fallback_results=[fallback_text],
            )
            attention_terms = self._attention_terms(results, latest_attention_terms)
            if attention_terms:
                compliance_notes = [
                    *compliance_notes,
                    "疑似其他企业、品牌、机构或人物名称已在文案中高亮。",
                ]
            token_usage = accumulated_usage or self._last_usage()
            charged_credits = self._charge_token_usage(
                capability=cap,
                task_id=task.task_id,
                token_usage=token_usage,
            )
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "生成完成",
                    "updated_at": datetime.now().astimezone(),
                    "token_usage": token_usage,
                    "charged_credits": charged_credits,
                    "result_text": results[0],
                    "result_variants": results,
                    "attention_terms": attention_terms,
                    "compliance_status": compliance_status,
                    "compliance_notes": compliance_notes,
                    "compliance_rewritten": compliance_rewritten,
                    "compliance_retry_used": compliance_retry_used,
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
                    "token_usage": accumulated_usage or self._last_usage(),
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

    def _accumulate_last_usage(self, total: dict[str, int]) -> None:
        for key, value in self._last_usage().items():
            if isinstance(value, int):
                total[key] = total.get(key, 0) + value
