from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from pydantic import HttpUrl
from typing import Any
from urllib.parse import urlparse

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
    "feedId": "feed_id",
    "视频号feedId": "feed_id",
    "finderUserName": "finder_user_name",
    "视频号finderUserName": "finder_user_name",
}
REQUIRED_COLUMNS = {
    "title",
    "author_name",
    "published_at",
}
INTEGER_COLUMNS = ("plays", "likes", "comments", "shares", "favorites", "followers")
PLATFORM_ALIASES = {
    "抖音": Platform.DOUYIN,
    "douyin": Platform.DOUYIN,
    "小红书": Platform.XIAOHONGSHU,
    "xiaohongshu": Platform.XIAOHONGSHU,
    "xhs": Platform.XIAOHONGSHU,
    "微信视频号": Platform.WECHAT_CHANNELS,
    "视频号": Platform.WECHAT_CHANNELS,
    "wechat_channels": Platform.WECHAT_CHANNELS,
    "wechat-channels": Platform.WECHAT_CHANNELS,
}
PLATFORM_DOMAINS = {
    Platform.DOUYIN: ("douyin.com", "iesdouyin.com"),
    Platform.XIAOHONGSHU: ("xiaohongshu.com", "xhslink.com"),
    Platform.WECHAT_CHANNELS: (
        "channels.weixin.qq.com",
        "finder.video.qq.com",
    ),
}


class ImportFieldError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _optional(value: Any) -> Any | None:
    return None if pd.isna(value) or value == "" else value


def _aware_datetime(value: Any, field: str) -> datetime:
    try:
        result = pd.to_datetime(value).to_pydatetime()
    except (TypeError, ValueError) as exc:
        raise ImportFieldError(field, f"{field} 不是有效日期时间。") from exc
    if result.tzinfo is None:
        return result.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return result


def _item_id(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _integer(value: Any, field: str) -> int | None:
    optional = _optional(value)
    if optional is None:
        return None
    try:
        result = int(optional)
    except (TypeError, ValueError) as exc:
        raise ImportFieldError(field, f"{field} 必须是非负整数。") from exc
    if result < 0:
        raise ImportFieldError(field, f"{field} 必须是非负整数。")
    return result


def _platform(value: Any) -> Platform:
    raw = str(_optional(value) or "douyin").strip().casefold()
    platform = PLATFORM_ALIASES.get(raw)
    if platform is None:
        raise ImportFieldError("platform", "平台仅支持抖音、小红书或微信视频号。")
    return platform


def _source_reference(
    platform: Platform,
    source_url: Any,
    feed_id: Any,
    finder_user_name: Any,
) -> tuple[str | None, str | None, str | None]:
    url = str(_optional(source_url) or "").strip() or None
    normalized_feed_id = str(_optional(feed_id) or "").strip() or None
    normalized_finder = str(_optional(finder_user_name) or "").strip() or None
    if url:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme not in {"http", "https"} or not host:
            raise ImportFieldError("source_url", "链接必须是完整的 HTTP/HTTPS URL。")
        allowed = PLATFORM_DOMAINS[platform]
        if not any(host == domain or host.endswith(f".{domain}") for domain in allowed):
            from src.platforms import platform_label

            raise ImportFieldError(
                "source_url", f"链接域名与{platform_label(platform)}平台不匹配。"
            )
    elif platform == Platform.WECHAT_CHANNELS:
        if not normalized_feed_id:
            raise ImportFieldError("feed_id", "视频号缺少链接时必须填写 feedId。")
        if not normalized_finder:
            raise ImportFieldError(
                "finder_user_name",
                "视频号缺少链接时必须填写 finderUserName。",
            )
    else:
        raise ImportFieldError("source_url", "抖音和小红书必须填写公开来源链接。")
    return url, normalized_feed_id, normalized_finder


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
                "feed_id",
                "finder_user_name",
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
        for row_number, (_, row) in enumerate(frame.iterrows(), start=2):
            try:
                empty_required = [
                    field
                    for field in REQUIRED_COLUMNS
                    if _optional(row.get(field)) is None
                ]
                if empty_required:
                    field = sorted(empty_required)[0]
                    raise ImportFieldError(
                        field, f"必填字段为空：{', '.join(sorted(empty_required))}"
                    )
                platform = _platform(row.get("platform"))
                source_url, feed_id, finder_user_name = _source_reference(
                    platform,
                    row.get("source_url"),
                    row.get("feed_id"),
                    row.get("finder_user_name"),
                )
                raw_item_id = _optional(row.get("platform_item_id"))
                item_id = _item_id(raw_item_id or feed_id or "")
                if not item_id:
                    raise ImportFieldError(
                        "platform_item_id", "必须填写作品 ID；视频号可使用 feedId。"
                    )
                sampled_at = _aware_datetime(
                    _optional(row.get("sampled_at")) or datetime.now().astimezone(),
                    "sampled_at",
                )
                metrics = {
                    column: _integer(row.get(column), column)
                    for column in INTEGER_COLUMNS
                }
                title = str(row["title"]).strip()
                matched_by = [
                    keyword
                    for keyword in (request.keywords or RECALL_KEYWORDS)
                    if keyword.casefold() in title.casefold()
                ]
                category = str(
                    _optional(row.get("category"))
                    or request.category
                    or TARGET_CATEGORY
                )
                if category != TARGET_CATEGORY:
                    raise ImportFieldError(
                        "category", f"首期二级赛道必须为：{TARGET_CATEGORY}"
                    )
                raw_confidence = _optional(row.get("confidence"))
                try:
                    confidence = float(
                        0.70 if raw_confidence is None else raw_confidence
                    )
                except (TypeError, ValueError) as exc:
                    raise ImportFieldError(
                        "confidence", "confidence 必须是 0 到 1 的数字。"
                    ) from exc
                if not 0 <= confidence <= 1:
                    raise ImportFieldError(
                        "confidence", "confidence 必须是 0 到 1 的数字。"
                    )
                items.append(
                    NormalizedCandidate(
                        platform_item_id=item_id,
                        title=title,
                        author_id=str(_optional(row.get("author_id")) or ""),
                        author_name=str(row["author_name"]).strip(),
                        platform=platform,
                        category=category,
                        published_at=_aware_datetime(
                            row["published_at"], "published_at"
                        ),
                        source_url=HttpUrl(source_url) if source_url else None,
                        source_type=DataSource.CSV,
                        metrics=VideoMetricSnapshot(
                            item_id=item_id,
                            sampled_at=sampled_at,
                            confidence=confidence,
                            **metrics,
                        ),
                        matched_by=matched_by,
                        evidence=(
                            str(value)
                            if (value := _optional(row.get("evidence"))) is not None
                            else None
                        ),
                        feed_id=feed_id,
                        finder_user_name=finder_user_name,
                    )
                )
            except ImportFieldError as exc:
                errors.append(
                    ImportErrorDetail(
                        row=row_number,
                        field=exc.field,
                        message=str(exc),
                    )
                )
            except Exception as exc:
                errors.append(ImportErrorDetail(row=row_number, message=str(exc)))
        return SourcePage(items=items, errors=errors)

    @staticmethod
    def from_manual(
        *,
        platform_item_id: str,
        platform: Platform = Platform.DOUYIN,
        title: str,
        author_name: str,
        published_at: datetime,
        source_url: str | None,
        sampled_at: datetime,
        plays: int | None = None,
        likes: int | None = None,
        comments: int | None = None,
        shares: int | None = None,
        favorites: int | None = None,
        followers: int | None = None,
        evidence: str | None = None,
        feed_id: str | None = None,
        finder_user_name: str | None = None,
    ) -> SourcePage:
        try:
            source_url, feed_id, finder_user_name = _source_reference(
                platform, source_url, feed_id, finder_user_name
            )
            item_id = platform_item_id.strip() or feed_id or ""
            if not item_id:
                raise ImportFieldError(
                    "platform_item_id", "必须填写作品 ID；视频号可使用 feedId。"
                )
            matched_by = [
                keyword
                for keyword in RECALL_KEYWORDS
                if keyword.casefold() in title.casefold()
            ]
            item = NormalizedCandidate(
                platform_item_id=item_id,
                title=title.strip(),
                author_id="",
                author_name=author_name.strip(),
                platform=platform,
                category=TARGET_CATEGORY,
                published_at=published_at,
                source_url=HttpUrl(source_url) if source_url else None,
                source_type=DataSource.MANUAL,
                metrics=VideoMetricSnapshot(
                    item_id=item_id,
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
                evidence=evidence.strip() if evidence and evidence.strip() else None,
                feed_id=feed_id,
                finder_user_name=finder_user_name,
            )
            return SourcePage(items=[item])
        except ImportFieldError as exc:
            return SourcePage(
                errors=[ImportErrorDetail(row=1, field=exc.field, message=str(exc))]
            )
        except Exception as exc:
            return SourcePage(errors=[ImportErrorDetail(row=1, message=str(exc))])
