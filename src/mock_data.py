from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import HttpUrl

from src.models import (
    DataSource,
    HeatLevel,
    HeatResult,
    Platform,
    TaskKind,
    TaskRecord,
    TaskStatus,
    TranscriptSegment,
    TranscriptionTask,
    VideoCandidate,
    VideoMetricSnapshot,
)

CHINA_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 7, 17, 16, 0, tzinfo=CHINA_TZ)


def build_mock_candidates(count: int = 24) -> list[VideoCandidate]:
    categories = ["美妆/防晒", "餐饮/探店", "科技/AI 工具", "家居/收纳"]
    candidates: list[VideoCandidate] = []
    for index in range(count):
        rank = index + 1
        score = max(58.0, 92.0 - index * 1.35)
        if score >= 85:
            level = HeatLevel.S
        elif score >= 75:
            level = HeatLevel.A
        elif score >= 65:
            level = HeatLevel.B
        else:
            level = HeatLevel.NORMAL
        platform = [Platform.DOUYIN, Platform.KUAISHOU, Platform.XIAOHONGSHU][index % 3]
        plays = (
            None
            if platform == Platform.XIAOHONGSHU and index % 2
            else 168_000 - index * 4_250
        )
        likes = 12_600 - index * 315
        confidence = 0.55 if index == count - 1 else round(0.96 - (index % 5) * 0.06, 2)
        if confidence < 0.6:
            level = HeatLevel.INSUFFICIENT
        video_id = f"mock-{rank:03d}"
        metrics = VideoMetricSnapshot(
            item_id=video_id,
            sampled_at=NOW - timedelta(minutes=index * 7),
            plays=plays,
            likes=max(likes, 800),
            comments=640 - index * 13,
            shares=None if index % 7 == 0 else max(920 - index * 18, 20),
            favorites=max(1_480 - index * 24, 50),
            followers=86_000 + index * 3_500,
            confidence=confidence,
        )
        candidates.append(
            VideoCandidate(
                video_id=video_id,
                title=f"{categories[index % len(categories)].split('/')[1]}内容创意案例 {rank}",
                author_id=f"author-{(index % 8) + 1:02d}",
                author_name=f"示例创作者 {(index % 8) + 1}",
                platform=platform,
                category=categories[index % len(categories)],
                published_at=NOW - timedelta(hours=2 + index * 2),
                source_url=HttpUrl(f"https://example.com/videos/{video_id}"),
                source_type=DataSource.MOCK,
                metrics=metrics,
                heat=HeatResult(
                    score=round(score * confidence, 1),
                    level=level,
                    confidence=confidence,
                    provisional=True,
                    model_version="mock-rule-v1",
                    reasons=[
                        f"同赛道互动质量位于 P{max(82, 99 - index)}",
                        "当前样本桶少于 100 条，等级为临时判断",
                    ],
                ),
            )
        )
    return candidates


def demo_segments() -> list[TranscriptSegment]:
    return [
        TranscriptSegment(
            start=0.0, end=4.2, text="很多人选防晒，只看包装上的倍数。", confidence=0.95
        ),
        TranscriptSegment(
            start=4.2,
            end=9.8,
            text="但真正决定使用体验的，是场景、肤质和补涂频率。",
            confidence=0.91,
        ),
        TranscriptSegment(
            start=9.8,
            end=14.6,
            text="先判断你是通勤、户外，还是长时间带妆。",
            confidence=0.67,
            needs_review=True,
        ),
        TranscriptSegment(
            start=14.6,
            end=20.0,
            text="再选择适合自己的产品，并按需补涂。",
            confidence=0.93,
        ),
    ]


def build_mock_tasks() -> list[TaskRecord]:
    return [
        TaskRecord(
            task_id="search-demo-001",
            kind=TaskKind.SEARCH,
            title="夏季防晒候选检索",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=NOW - timedelta(minutes=18),
            updated_at=NOW - timedelta(minutes=17),
            elapsed_seconds=8.4,
            outputs={"候选数": "24", "数据范围": "Mock + 手工导入"},
        ),
        TranscriptionTask(
            task_id="transcript-demo-001",
            title="授权口播样片.mp4",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=NOW - timedelta(minutes=12),
            updated_at=NOW - timedelta(minutes=11),
            elapsed_seconds=18.6,
            outputs={
                "transcript.txt": "ready",
                "transcript.json": "ready",
                "subtitles.srt": "ready",
            },
            media_name="授权口播样片.mp4",
            media_type="video/mp4",
            rights_confirmed=True,
            candidate_id="mock-001",
            segments=demo_segments(),
        ),
        TranscriptionTask(
            task_id="transcript-demo-002",
            title="新品介绍.mov",
            status=TaskStatus.RUNNING,
            progress=62,
            created_at=NOW - timedelta(minutes=4),
            updated_at=NOW - timedelta(minutes=1),
            media_name="新品介绍.mov",
            media_type="video/quicktime",
            rights_confirmed=True,
        ),
        TranscriptionTask(
            task_id="transcript-demo-003",
            title="门店活动.mp4",
            status=TaskStatus.FAILED,
            progress=24,
            created_at=NOW - timedelta(minutes=7),
            updated_at=NOW - timedelta(minutes=6),
            elapsed_seconds=5.2,
            error_message="演示错误：媒体格式探测失败。请检查文件后重试。",
            media_name="门店活动.mp4",
            media_type="video/mp4",
            rights_confirmed=True,
        ),
    ]
