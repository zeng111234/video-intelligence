from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from src.models import (
    DataSource,
    ImportErrorDetail,
    NormalizedCandidate,
    Platform,
    SourcePage,
    SourceRequest,
    VideoMetricSnapshot,
)

TARGET_CATEGORY = "B2B/AI企业服务获客数字人口播"
RECALL_KEYWORDS = [
    "数字人口播",
    "数字人营销",
    "AI获客",
    "企业服务",
    "AI工具",
    "SaaS",
    "私域",
    "线索",
    "询盘",
]

COLUMN_ALIASES = {
    "作品ID": "platform_item_id",
    "视频ID": "platform_item_id",
    "标题": "title",
    "作者ID": "author_id",
    "作者": "author_name",
    "平台": "platform",
    "二级赛道": "category",
    "发布时间": "published_at",
    "链接": "source_url",
    "采样时间": "sampled_at",
    "播放": "plays",
    "点赞": "likes",
    "评论": "comments",
    "分享": "shares",
    "收藏": "favorites",
    "粉丝": "followers",
    "置信度": "confidence",
    "证据": "evidence",
}
REQUIRED_COLUMNS = {
    "platform_item_id",
    "title",
    "author_name",
    "published_at",
    "source_url",
}
INTEGER_COLUMNS = ("plays", "likes", "comments", "shares", "favorites", "followers")


def _optional(value: Any) -> Any | None:
    return None if pd.isna(value) or value == "" else value


def _aware_datetime(value: Any) -> datetime:
    result = pd.to_datetime(value).to_pydatetime()
    if result.tzinfo is None:
        return result.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return result


def _item_id(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


class ManualImportAdapter:
    """Parse operator-provided CSV/Excel files into normalized candidates."""

    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self.content = content

    @staticmethod
    def template() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "platform_item_id",
                "title",
                "author_id",
                "author_name",
                "platform",
                "category",
                "published_at",
                "source_url",
                "sampled_at",
                *INTEGER_COLUMNS,
                "confidence",
                "evidence",
            ]
        )

    def _read(self) -> pd.DataFrame:
        suffix = Path(self.filename).suffix.lower()
        if suffix == ".csv":
            try:
                return pd.read_csv(BytesIO(self.content), encoding="utf-8-sig")
            except UnicodeDecodeError:
                return pd.read_csv(BytesIO(self.content), encoding="gb18030")
        if suffix == ".xlsx":
            return pd.read_excel(BytesIO(self.content))
        raise ValueError("仅支持 CSV 或 XLSX 文件。")

    def sync(self, request: SourceRequest) -> SourcePage:
        try:
            frame = self._read().rename(columns=COLUMN_ALIASES)
        except Exception as exc:
            return SourcePage(errors=[ImportErrorDetail(row=1, message=str(exc))])
        missing_columns = sorted(REQUIRED_COLUMNS - set(frame.columns))
        if missing_columns:
            return SourcePage(
                errors=[
                    ImportErrorDetail(
                        row=1,
                        field=field,
                        message=f"缺少必填列 {field}",
                    )
                    for field in missing_columns
                ]
            )

        items: list[NormalizedCandidate] = []
        errors: list[ImportErrorDetail] = []
        for index, row in frame.iterrows():
            row_number = int(index) + 2
            try:
                empty_required = [
                    field
                    for field in REQUIRED_COLUMNS
                    if _optional(row.get(field)) is None
                ]
                if empty_required:
                    raise ValueError(
                        f"必填字段为空：{', '.join(sorted(empty_required))}"
                    )
                item_id = _item_id(row["platform_item_id"])
                sampled_at = _aware_datetime(
                    _optional(row.get("sampled_at")) or datetime.now().astimezone()
                )
                metrics = {
                    column: (
                        int(value)
                        if (value := _optional(row.get(column))) is not None
                        else None
                    )
                    for column in INTEGER_COLUMNS
                }
                title = str(row["title"]).strip()
                matched_by = [
                    keyword
                    for keyword in (request.keywords or RECALL_KEYWORDS)
                    if keyword.casefold() in title.casefold()
                ]
                platform = Platform(
                    {
                        "抖音": "douyin",
                        "快手": "kuaishou",
                        "小红书": "xiaohongshu",
                    }.get(
                        str(_optional(row.get("platform")) or "douyin"),
                        str(_optional(row.get("platform")) or "douyin").lower(),
                    )
                )
                if platform != Platform.DOUYIN:
                    raise ValueError("首期导入只接受抖音候选。")
                category = str(
                    _optional(row.get("category"))
                    or request.category
                    or TARGET_CATEGORY
                )
                if category != TARGET_CATEGORY:
                    raise ValueError(f"首期二级赛道必须为：{TARGET_CATEGORY}")
                raw_confidence = _optional(row.get("confidence"))
                items.append(
                    NormalizedCandidate(
                        platform_item_id=item_id,
                        title=title,
                        author_id=str(
                            _optional(row.get("author_id")) or f"unknown-{item_id}"
                        ),
                        author_name=str(row["author_name"]).strip(),
                        platform=platform,
                        category=category,
                        published_at=_aware_datetime(row["published_at"]),
                        source_url=str(row["source_url"]).strip(),
                        source_type=DataSource.CSV,
                        metrics=VideoMetricSnapshot(
                            item_id=item_id,
                            sampled_at=sampled_at,
                            confidence=float(
                                0.70 if raw_confidence is None else raw_confidence
                            ),
                            **metrics,
                        ),
                        matched_by=matched_by,
                        evidence=str(
                            _optional(row.get("evidence")) or row["source_url"]
                        ),
                    )
                )
            except Exception as exc:
                errors.append(ImportErrorDetail(row=row_number, message=str(exc)))
        return SourcePage(items=items, errors=errors)

    @staticmethod
    def from_manual(
        *,
        platform_item_id: str,
        title: str,
        author_name: str,
        published_at: datetime,
        source_url: str,
        sampled_at: datetime,
        plays: int | None = None,
        likes: int | None = None,
        comments: int | None = None,
        shares: int | None = None,
        favorites: int | None = None,
        followers: int | None = None,
        evidence: str | None = None,
    ) -> SourcePage:
        try:
            matched_by = [
                keyword
                for keyword in RECALL_KEYWORDS
                if keyword.casefold() in title.casefold()
            ]
            item = NormalizedCandidate(
                platform_item_id=platform_item_id.strip(),
                title=title.strip(),
                author_id=f"manual-{platform_item_id.strip()}",
                author_name=author_name.strip(),
                platform=Platform.DOUYIN,
                category=TARGET_CATEGORY,
                published_at=published_at,
                source_url=source_url.strip(),
                source_type=DataSource.MANUAL,
                metrics=VideoMetricSnapshot(
                    item_id=platform_item_id.strip(),
                    sampled_at=sampled_at,
                    plays=plays,
                    likes=likes,
                    comments=comments,
                    shares=shares,
                    favorites=favorites,
                    followers=followers,
                    confidence=0.70,
                ),
                matched_by=matched_by,
                evidence=evidence or source_url.strip(),
            )
            return SourcePage(items=[item])
        except Exception as exc:
            return SourcePage(errors=[ImportErrorDetail(row=1, message=str(exc))])
