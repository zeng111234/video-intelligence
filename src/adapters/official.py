from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.models import (
    DataSource,
    EligibilityStatus,
    NormalizedCandidate,
    Platform,
    SourceCapability,
    SourcePage,
    SourceRequest,
    VideoMetricSnapshot,
)

TOKEN_URL = "https://open.douyin.com/oauth/client_token/"
VIDEO_SEARCH_URL = "https://open.douyin.com/dy_open_api/v2/search/video/"
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
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed official URLs
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OfficialApiError(
            f"抖音接口 HTTP {exc.code}：{detail[:300]}",
            code=exc.code,
            retryable=exc.code == 429 or exc.code >= 500,
        ) from exc


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


class DouyinHotBillboardAdapter(_DisabledOfficialAdapter):
    permission_name = "data.external.billboard_hot_video"


class DouyinKeywordAdapter:
    """Official `aweme.dy.video_search_v2` adapter; no scraping or media download."""

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
        for _ in range(2):
            try:
                return operation()
            except OfficialApiError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
            except (ConnectionError, TimeoutError, URLError) as exc:
                last_error = exc
        raise OfficialApiError(
            f"连接抖音开放平台失败，已自动重试一次：{last_error}"
        ) from last_error

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
                    source_url=link,
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
