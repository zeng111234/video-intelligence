"""发布后人工确认数据的记录与可解释复盘。"""
from __future__ import annotations

import json
from pathlib import Path

from src.models import PublishFeedback, PublishTask, TaskStatus


class FeedbackService:
    def __init__(self, repository, storage_directory: str | Path) -> None:
        self.repository = repository
        self.path = Path(storage_directory) / "publish_feedback.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def list_feedback(self) -> list[PublishFeedback]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return sorted([PublishFeedback.model_validate(item) for item in data], key=lambda item: item.recorded_at, reverse=True)

    def record(self, *, publish_task_id: str, views: int, likes: int, comments: int, leads: int, recorded_by: str, note: str = "") -> PublishFeedback:
        task = self.repository.get_task(publish_task_id)
        if not isinstance(task, PublishTask):
            raise ValueError("发布任务不存在。")
        if task.status != TaskStatus.SUCCEEDED or task.is_mock:
            raise ValueError("只能为人工确认已发布的真实发布任务回填反馈。")
        if any(item.publish_task_id == publish_task_id for item in self.list_feedback()):
            raise ValueError("该发布任务已回填反馈；请避免重复计入复盘。")
        feedback = PublishFeedback(publish_task_id=publish_task_id, pipeline_run_id=task.source_pipeline_run_id, platform=task.target.platform.value, views=views, likes=likes, comments=comments, leads=leads, recorded_by=recorded_by.strip(), note=note.strip())
        records = [feedback, *self.list_feedback()]
        self.path.write_text(json.dumps([item.model_dump(mode="json") for item in records], ensure_ascii=False, indent=2), encoding="utf-8")
        return feedback

    def recommendations(self) -> dict:
        records = self.list_feedback()
        if not records:
            return {"sample_size": 0, "message": "暂无已确认发布后的反馈数据，不能给出优化结论。", "recommendations": []}
        by_platform: dict[str, list[PublishFeedback]] = {}
        for item in records:
            by_platform.setdefault(item.platform, []).append(item)
        rates = {platform: sum((item.likes + item.comments) / max(item.views, 1) for item in items) / len(items) for platform, items in by_platform.items()}
        best_platform = max(rates, key=rates.get)
        recommendations = [f"已确认样本中，{best_platform} 的平均互动率最高（{rates[best_platform] * 100:.1f}%）。"]
        if len(records) < 3:
            recommendations.append("样本少于 3 条，建议继续回填同口径数据，暂不据此调整预算或内容方向。")
        if sum(item.leads for item in records) == 0:
            recommendations.append("当前确认样本尚无线索回填；优先检查 CTA、承接页和人工跟进链路。")
        return {"sample_size": len(records), "message": "建议仅基于人工回填的已确认数据生成。", "recommendations": recommendations, "platform_engagement_rates": {key: round(value, 4) for key, value in rates.items()}}
