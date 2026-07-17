from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime
from urllib.parse import urlparse
from urllib.error import URLError
from urllib.request import Request, urlopen

from src.models import (
    DataSource,
    ImportErrorDetail,
    NormalizedCandidate,
    Platform,
    SourcePage,
    SourceRequest,
    VideoMetricSnapshot,
)
from src.retry import run_with_single_retry

ALLOWED_HOST_SUFFIXES = ("douyin.com", "iesdouyin.com")


class PublicMetadataResearchAdapter:
    """Best-effort metadata reader for explicit public URLs; never downloads media."""

    def sync(self, request: SourceRequest) -> SourcePage:
        items: list[NormalizedCandidate] = []
        errors: list[ImportErrorDetail] = []
        for index, url_value in enumerate(request.urls, start=1):
            url = str(url_value)
            try:
                items.append(self._read_url(url, request))
            except Exception as exc:
                errors.append(
                    ImportErrorDetail(row=index, field="url", message=str(exc))
                )
        return SourcePage(items=items, errors=errors)

    def _read_url(self, url: str, request: SourceRequest) -> NormalizedCandidate:
        host = (urlparse(url).hostname or "").lower()
        if urlparse(url).scheme != "https":
            raise ValueError("研究适配器只接受 HTTPS 链接。")
        if not any(
            host == suffix or host.endswith(f".{suffix}")
            for suffix in ALLOWED_HOST_SUFFIXES
        ):
            raise ValueError("研究适配器只接受抖音公开分享链接。")

        def fetch() -> str:
            req = Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 metadata-research/1.0"},
            )
            with urlopen(req, timeout=10) as response:  # noqa: S310 - allowlisted hosts above
                final_host = (urlparse(response.geturl()).hostname or "").lower()
                if not any(
                    final_host == suffix or final_host.endswith(f".{suffix}")
                    for suffix in ALLOWED_HOST_SUFFIXES
                ):
                    raise ValueError("公开链接重定向到了未允许的域名。")
                return response.read(2_000_000).decode("utf-8", errors="replace")

        document = run_with_single_retry(
            fetch, retry_for=(ConnectionError, TimeoutError, URLError)
        )
        title_match = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
            document,
            flags=re.IGNORECASE,
        ) or re.search(
            r"<title>(.*?)</title>", document, flags=re.IGNORECASE | re.DOTALL
        )
        if not title_match:
            raise ValueError("公开页面没有可解析的标题，请改用手工/CSV 导入。")
        title = html.unescape(re.sub(r"\s+", " ", title_match.group(1))).strip()
        digit_ids = re.findall(r"\d{10,}", url)
        item_id = (
            digit_ids[-1]
            if digit_ids
            else hashlib.sha256(url.encode()).hexdigest()[:20]
        )
        matched_by = [
            keyword
            for keyword in request.keywords
            if keyword.casefold() in title.casefold()
        ]
        now = datetime.now().astimezone()
        return NormalizedCandidate(
            platform_item_id=item_id,
            title=title,
            author_id=f"unknown-{item_id}",
            author_name="待人工补充",
            platform=Platform.DOUYIN,
            category=request.category,
            published_at=now,
            source_url=url,
            source_type=DataSource.PUBLIC_RESEARCH,
            metrics=VideoMetricSnapshot(
                item_id=item_id, sampled_at=now, confidence=0.55
            ),
            matched_by=matched_by,
            evidence=url,
        )
