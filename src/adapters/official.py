from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from pydantic import HttpUrl
from urllib.request import Request, urlopen

from src.models import (
    DataSource,
    EligibilityStatus,
    ImportErrorDetail,
    NormalizedCandidate,
    Platform,
    SourceCapability,
    SourcePage,
    SourceRequest,
    VideoMetricSnapshot,
)
from utils.common.errors import (
    analyze_404_error,
    create_error_context,
    log_http_request_response,
)

TOKEN_URL = "https://open.douyin.com/oauth/client_token/"
VIDEO_SEARCH_URL = "https://open.douyin.com/dy_open_api/v2/search/video/"
HOT_VIDEO_BILLBOARD_URL = "https://open.douyin.com/data/extern/billboard/hot_video/"
HOT_WORDS_URL = "https://open.douyin.com/hotsearch/sentences/"
JsonTransport = Callable[[str, str, dict[str, str], bytes | None], dict[str, Any]]


class OfficialAdapterDisabledError(RuntimeError):
    pass


class OfficialApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _default_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
) -> dict[str, Any]:
    request = Request(url, data=body, headers=headers, method=method)
    
    # 记录请求详情
    log_http_request_response(
        url=url,
        method=method,
        request_headers=headers,
        request_body=body,
    )
    
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed official URLs
            response_body = response.read().decode("utf-8")
            
            # 记录响应详情
            log_http_request_response(
                url=url,
                method=method,
                request_headers=headers,
                request_body=body,
                response_status=response.status,
                response_body=response_body[:500],  # 截断过长的响应
            )
            
            return json.loads(response_body)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        
        # 增强404错误处理逻辑
        if exc.code == 404:
            # 分析404错误原因
            error_analysis = analyze_404_error(
                url=url,
                response_body=detail,
                headers=headers,
            )
            
            # 创建详细的错误上下文
            error_context = create_error_context(
                error=exc,
                request_url=url,
                request_method=method,
                request_headers=headers,
                request_body=body,
                response_status=exc.code,
                response_body=detail,
                additional_info={
                    "error_analysis": error_analysis,
                    "platform": "douyin",
                },
            )
            
            # 记录错误
            error_context.log_error()
            
            # 根据分析结果决定是否重试
            retryable = error_analysis.get("is_retryable", False)
            
            raise OfficialApiError(
                f"抖音接口 HTTP 404：资源未找到 - {detail[:300]}",
                code=exc.code,
                retryable=retryable,
            ) from exc
        
        # 记录其他HTTP错误
        log_http_request_response(
            url=url,
            method=method,
            request_headers=headers,
            request_body=body,
            response_status=exc.code,
            response_body=detail[:500],
            error=exc,
        )
        
        # 原有错误处理逻辑
        raise OfficialApiError(
            f"抖音接口 HTTP {exc.code}：{detail[:300]}",
            code=exc.code,
            retryable=exc.code == 429 or exc.code >= 500,
        ) from exc
    except (URLError, OSError) as exc:
        # 记录网络错误
        log_http_request_response(
            url=url,
            method=method,
            request_headers=headers,
            request_body=body,
            error=exc,
        )
        raise


class _DouyinOfficialClient:
    """共享的抖音开放平台 client_token 获取/缓存/重试逻辑。"""

    provider_name = "douyin_official"

    def __init__(
        self,
        client_key: str | None = None,
        client_secret: str | None = None,
        *,
        transport: JsonTransport | None = None,
    ) -> None:
        self.client_key = (client_key or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.transport = transport or _default_transport
        self._token: str | None = None
        self._token_expires_at = 0.0

    def _missing_credentials(self) -> list[str]:
        missing = []
        if not self.client_key:
            missing.append("DOUYIN_CLIENT_KEY")
        if not self.client_secret:
            missing.append("DOUYIN_CLIENT_SECRET")
        return missing

    def _credential_capabilities(self) -> SourceCapability:
        missing = self._missing_credentials()
        return SourceCapability(
            provider_name=self.provider_name,
            enabled=not missing,
            metadata_only=True,
            permission_status=(
                "credentials_missing" if missing else "configured_permission_unverified"
            ),
            missing_configuration=missing,
        )

    def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        body = json.dumps(
            {
                "grant_type": "client_credential",
                "client_key": self.client_key,
                "client_secret": self.client_secret,
            }
        ).encode("utf-8")
        payload = self._run_once_with_retry(
            lambda: self.transport(
                "POST", TOKEN_URL, {"content-type": "application/json"}, body
            )
        )
        data = payload.get("data") or {}
        error_code = int(data.get("error_code", 0) or 0)
        token = data.get("access_token")
        if error_code or not token:
            raise OfficialApiError(
                f"获取 client_token 失败 [{error_code}]：{data.get('description') or payload.get('message') or '未知错误'}",
                code=error_code,
            )
        expires_in = max(60, int(data.get("expires_in", 7200)))
        self._token = str(token)
        self._token_expires_at = time.monotonic() + expires_in - 60
        return self._token

    @staticmethod
    def _run_once_with_retry(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        last_error: BaseException | None = None
        retryable = False
        for _ in range(2):
            try:
                return operation()
            except OfficialApiError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
                retryable = True
            except (ConnectionError, TimeoutError, URLError) as exc:
                last_error = exc
        raise OfficialApiError(
            f"连接抖音开放平台失败，已自动重试一次：{last_error}",
            retryable=retryable,
        ) from last_error

    def _authed_get(self, url: str, action: str) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            token = self._get_token()
            payload = self.transport(
                "GET",
                url,
                {"content-type": "application/json", "access-token": token},
                None,
            )
            err_no = int(payload.get("err_no", 0) or 0)
            if err_no:
                raise OfficialApiError(
                    f"{action}失败 [{err_no}]：{payload.get('err_msg', '未知错误')}",
                    code=err_no,
                    retryable=err_no in {28001005, 28001006},
                )
            return payload

        return self._run_once_with_retry(operation)

    @staticmethod
    def _check_inner_error(payload: dict[str, Any], action: str) -> dict[str, Any]:
        outer_data = payload.get("data") or {}
        inner_error = int(outer_data.get("error_code", 0) or 0)
        if inner_error:
            raise OfficialApiError(
                f"{action}返回错误 [{inner_error}]：{outer_data.get('description') or '未知错误'}",
                code=inner_error,
            )
        return outer_data


class _DisabledOfficialAdapter:
    permission_name = "官方数据权限"

    def capabilities(self) -> SourceCapability:
        return SourceCapability(
            provider_name=self.__class__.__name__,
            enabled=False,
            permission_status="permission_unavailable",
            missing_configuration=[self.permission_name],
        )

    def sync(self, request: SourceRequest) -> SourcePage:
        raise OfficialAdapterDisabledError(
            f"{self.permission_name}尚未配置；请使用 CSV、手工链接或公开元数据研究入口。"
        )


@dataclass(frozen=True)
class HotWordEntry:
    """抖音实时热点词条目（约每 2 小时刷新）。"""

    word: str
    hot_value: int | None
    fetched_at: datetime
    raw: dict[str, Any]


class DouyinHotBillboardAdapter(_DouyinOfficialClient):
    """官方热门视频榜适配器（scope: data.external.billboard_hot_video，无需用户授权）。

    榜单数据统计最近 24 小时，官方每天 10 点前产出。
    """

    provider_name = "douyin_hot_billboard"

    def capabilities(self) -> SourceCapability:
        return self._credential_capabilities()

    def sync(self, request: SourceRequest) -> SourcePage:
        if not self.capabilities().enabled:
            raise OfficialAdapterDisabledError(
                "尚未配置抖音 ClientKey/ClientSecret，官方热榜未发起平台请求。"
            )
        payload = self._authed_get(HOT_VIDEO_BILLBOARD_URL, "抖音官方热榜请求")
        return self._normalize_billboard(payload)

    @staticmethod
    def _normalize_billboard(payload: dict[str, Any]) -> SourcePage:
        outer_data = _DouyinOfficialClient._check_inner_error(payload, "抖音官方热榜")
        raw_list = outer_data.get("list") or []
        if not isinstance(raw_list, list):
            raw_list = []
        sampled_at = datetime.now().astimezone()
        items: list[NormalizedCandidate] = []
        errors: list[ImportErrorDetail] = []
        for index, raw in enumerate(raw_list):
            row = index + 1
            if not isinstance(raw, dict):
                errors.append(
                    ImportErrorDetail(row=row, message="榜单条目不是 JSON 对象，已跳过。")
                )
                continue
            title = str(raw.get("title") or "").strip()
            if not title:
                errors.append(
                    ImportErrorDetail(row=row, field="title", message="榜单条目缺少 title，已跳过。")
                )
                continue
            author_block = raw.get("author") if isinstance(raw.get("author"), dict) else {}
            nickname = str(
                raw.get("nickname") or author_block.get("nickname") or "作者信息不可用"
            )
            avatar = str(raw.get("avatar") or author_block.get("avatar") or "") or None
            author_hash = hashlib.sha256(nickname.encode()).hexdigest()[:16]
            share_url = str(raw.get("share_url") or "").strip()
            item_id = str(raw.get("item_id") or raw.get("group_id") or "").strip()
            if not item_id:
                item_id = f"billboard-{hashlib.sha256((share_url or title).encode()).hexdigest()[:16]}"
            try:
                rank = int(raw.get("rank") or row)
            except (TypeError, ValueError):
                rank = row
            rank = max(1, rank)

            def _to_int(key: str) -> int | None:
                value = raw.get(key)
                if value is None:
                    return None
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            digg_count = _to_int("digg_count")
            comment_count = _to_int("comment_count")
            play_count = _to_int("play_count")
            hot_value_raw = raw.get("hot_value")
            hot_value: float | None = None
            if hot_value_raw is not None:
                try:
                    hot_value = float(hot_value_raw)
                except (TypeError, ValueError):
                    hot_value = None

            hot_words_raw = raw.get("hot_words")
            if isinstance(hot_words_raw, list):
                hot_words = [str(word).strip() for word in hot_words_raw if str(word).strip()]
            elif isinstance(hot_words_raw, str) and hot_words_raw.strip():
                hot_words = [hot_words_raw.strip()]
            else:
                hot_words = []

            evidence_payload = {
                "rank": rank,
                "hot_words": hot_words,
                "hot_value": hot_value,
                "official_share_url": share_url or None,
                "play_count": play_count,
                "author_avatar": avatar,
            }
            evidence = "official_billboard:" + json.dumps(
                {k: v for k, v in evidence_payload.items() if v is not None},
                ensure_ascii=False,
            )
            source_url = (
                HttpUrl(share_url)
                if share_url.startswith(("https://", "http://"))
                else None
            )
            create_time = _to_int("create_time")
            published_at = (
                datetime.fromtimestamp(create_time, tz=timezone.utc).astimezone()
                if create_time
                else sampled_at
            )
            items.append(
                NormalizedCandidate(
                    platform_item_id=item_id,
                    title=title,
                    author_id=f"unavailable-{author_hash}",
                    author_name=nickname,
                    platform=Platform.DOUYIN,
                    category="官方热榜",
                    published_at=published_at,
                    source_url=source_url,
                    source_type=DataSource.OFFICIAL,
                    metrics=VideoMetricSnapshot(
                        item_id=item_id,
                        sampled_at=sampled_at,
                        plays=play_count,
                        likes=digg_count,
                        comments=comment_count,
                        confidence=1.0,
                    ),
                    matched_by=hot_words,
                    cohort_key="douyin:hot_billboard",
                    eligibility_status=EligibilityStatus.AUTO_MATCHED,
                    official_hot=True,
                    official_rank=rank,
                    official_hot_value=hot_value,
                    evidence=evidence,
                )
            )
        return SourcePage(items=items, has_more=False, errors=errors)


class DouyinHotWordsAdapter(_DouyinOfficialClient):
    """抖音实时热点词适配器（约每 2 小时刷新，无需用户授权）。

    只负责拉取并解析热点词；持久化由服务层消费 ``HotWordEntry`` 完成。
    """

    provider_name = "douyin_hot_words"

    def capabilities(self) -> SourceCapability:
        return self._credential_capabilities()

    def fetch_hot_words(self) -> list[HotWordEntry]:
        if not self.capabilities().enabled:
            raise OfficialAdapterDisabledError(
                "尚未配置抖音 ClientKey/ClientSecret，官方热点词未发起平台请求。"
            )
        payload = self._authed_get(HOT_WORDS_URL, "抖音热点词请求")
        outer_data = self._check_inner_error(payload, "抖音热点词")
        raw_list = outer_data.get("sentence_list") or outer_data.get("list") or []
        if not isinstance(raw_list, list):
            raw_list = []
        fetched_at = datetime.now().astimezone()
        entries: list[HotWordEntry] = []
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            word = str(raw.get("sentence") or raw.get("word") or "").strip()
            if not word:
                continue
            hot_value_raw = raw.get("hot_value")
            hot_value: int | None = None
            if hot_value_raw is not None:
                try:
                    hot_value = int(hot_value_raw)
                except (TypeError, ValueError):
                    hot_value = None
            entries.append(
                HotWordEntry(
                    word=word,
                    hot_value=hot_value,
                    fetched_at=fetched_at,
                    raw=raw,
                )
            )
        return entries


class DouyinKeywordAdapter(_DouyinOfficialClient):
    """Official `aweme.dy.video_search_v2` adapter; no scraping or media download."""

    def capabilities(self) -> SourceCapability:
        missing = []
        if not self.client_key:
            missing.append("DOUYIN_CLIENT_KEY")
        if not self.client_secret:
            missing.append("DOUYIN_CLIENT_SECRET")
        return SourceCapability(
            provider_name="douyin_video_search_v2",
            enabled=not missing,
            supports_keyword_search=True,
            metadata_only=True,
            permission_status=(
                "credentials_missing" if missing else "configured_permission_unverified"
            ),
            max_page_size=10,
            missing_configuration=missing,
        )

    def sync(self, request: SourceRequest) -> SourcePage:
        capability = self.capabilities()
        if not capability.enabled:
            raise OfficialAdapterDisabledError(
                "尚未配置抖音 ClientKey/ClientSecret，未发起平台请求。"
            )
        if len(request.keywords) != 1 or not request.keywords[0].strip():
            raise ValueError("抖音视频垂搜每次请求必须包含一个关键词。")
        if request.platform != Platform.DOUYIN:
            raise ValueError("抖音官方适配器只能处理抖音平台请求。")

        keyword = request.keywords[0].strip()
        query: dict[str, str | int] = {
            "keyword": keyword,
            "count": min(request.page_size, capability.max_page_size),
            "cursor": int(request.cursor or 0),
            "device_id": self._device_id(request.request_id),
            "publish_time": request.publish_time,
            "sort_type": request.sort_type,
        }
        if request.search_id:
            query["search_id"] = request.search_id
        url = f"{VIDEO_SEARCH_URL}?{urlencode(query)}"

        def operation() -> dict[str, Any]:
            token = self._get_token()
            payload = self.transport(
                "GET",
                url,
                {"content-type": "application/json", "access-token": token},
                None,
            )
            err_no = int(payload.get("err_no", 0) or 0)
            if err_no:
                raise OfficialApiError(
                    f"抖音视频搜索失败 [{err_no}]：{payload.get('err_msg', '未知错误')}",
                    code=err_no,
                    retryable=err_no in {28001005, 28001006},
                )
            return payload

        payload = self._run_once_with_retry(operation)
        return self._normalize_page(payload, request, keyword)

    @staticmethod
    def _device_id(request_id: str) -> int:
        return int(hashlib.sha256(request_id.encode()).hexdigest()[:12], 16)

    @staticmethod
    def _normalize_page(
        payload: dict[str, Any], request: SourceRequest, keyword: str
    ) -> SourcePage:
        outer_data = payload.get("data") or {}
        page_data = outer_data.get("data") or outer_data
        log_id = str(payload.get("log_id") or "")
        sampled_at = datetime.now().astimezone()
        cohort_key = f"douyin:keyword:{keyword.casefold()}"
        items: list[NormalizedCandidate] = []
        for raw in page_data.get("video_list") or []:
            item_id = str(raw.get("item_id") or "").strip()
            title = str(raw.get("title") or "").strip()
            link = str(raw.get("link") or "").strip()
            if not item_id or not title or not link:
                continue
            nickname = str(raw.get("nickname") or "作者信息不可用")
            author_hash = hashlib.sha256(nickname.encode()).hexdigest()[:16]
            statistics = raw.get("statistics") or {}
            create_time = int(raw.get("create_time") or 0)
            published_at = (
                datetime.fromtimestamp(create_time, tz=timezone.utc).astimezone()
                if create_time
                else sampled_at
            )
            items.append(
                NormalizedCandidate(
                    platform_item_id=item_id,
                    title=title,
                    author_id=f"unavailable-{author_hash}",
                    author_name=nickname,
                    platform=Platform.DOUYIN,
                    category=f"关键词/{keyword}",
                    published_at=published_at,
                    source_url=HttpUrl(link),
                    source_type=DataSource.OFFICIAL,
                    metrics=VideoMetricSnapshot(
                        item_id=item_id,
                        sampled_at=sampled_at,
                        likes=(
                            int(statistics["digg_count"])
                            if statistics.get("digg_count") is not None
                            else None
                        ),
                        confidence=1.0,
                    ),
                    matched_by=[keyword],
                    cohort_key=cohort_key,
                    eligibility_status=EligibilityStatus.AUTO_MATCHED,
                    evidence=f"douyin_log_id:{log_id}" if log_id else None,
                )
            )
        has_more = bool(page_data.get("has_more"))
        return SourcePage(
            items=items,
            cursor=str(page_data.get("cursor")) if has_more else None,
            search_id=str(page_data.get("search_id") or request.search_id or "")
            or None,
            has_more=has_more,
        )
