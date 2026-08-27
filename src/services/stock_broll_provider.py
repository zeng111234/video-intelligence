"""Low-cost Pexels/Pixabay B-roll adapter with auditable offline fallback.

The provider is deliberately video-only.  It never invents a license, never
calls an image-generation service, and returns an explicit unavailable result
when a key is missing.  Network failures are retried once at most; callers can
then keep the talking-head safe degradation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from src.services.local_visual_asset_matcher import visual_search_query

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency in minimal installs
    load_dotenv = None


PEXELS_API_URL = "https://api.pexels.com/videos/search"
PIXABAY_API_URL = "https://pixabay.com/api/videos/"
PEXELS_LICENSE_URL = "https://www.pexels.com/license/"
PIXABAY_LICENSE_URL = "https://pixabay.com/service/license-summary/"

@dataclass(frozen=True)
class StockBrollResult:
    status: str
    provider: str | None
    items: list[dict[str, Any]]
    reason: str | None = None
    attempts: int = 0
    candidate_count: int = 0
    candidate_summaries: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "provider": self.provider,
            "items": self.items,
            "reason": self.reason,
            "attempts": self.attempts,
            "candidate_count": self.candidate_count,
            "candidate_summaries": self.candidate_summaries,
        }


class StockBrollProvider:
    """Search and cache free stock videos without hiding provenance."""

    def __init__(
        self,
        cache_root: Path,
        *,
        pexels_key: str | None = None,
        pixabay_key: str | None = None,
        opener: Callable[..., Any] | None = None,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self.cache_root = cache_root
        self.cache_root.mkdir(parents=True, exist_ok=True)
        # Production callers use the default opener and may rely on the
        # project .env.  Tests/injected offline openers must remain isolated
        # from a developer machine's credentials and must never accidentally
        # make a real request.
        if load_dotenv is not None and opener is None:
            load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
        if opener is not None:
            # An injected opener is an explicit offline/test boundary. Do not
            # inherit developer-machine credentials from the environment and
            # accidentally turn a deterministic test into a network call.
            self.pexels_key = (pexels_key or "").strip()
            self.pixabay_key = (pixabay_key or "").strip()
        else:
            self.pexels_key = (pexels_key or os.getenv("PEXELS_API_KEY", "")).strip()
            self.pixabay_key = (pixabay_key or os.getenv("PIXABAY_API_KEY", "")).strip()
        self.opener = opener or urllib.request.urlopen
        self.runner = runner or subprocess.run

    def _redact_error(self, error: BaseException | str) -> str:
        """Keep provider diagnostics useful without exposing API credentials."""
        message = str(error or "stock provider request failed")
        for secret in (self.pexels_key, self.pixabay_key):
            if secret:
                message = message.replace(secret, "[已隐藏]")
        return message[:240] or "stock provider request failed"

    def search_and_cache(
        self,
        query: str,
        *,
        max_results: int = 3,
        provider: str | None = None,
        force_fresh: bool = False,
    ) -> StockBrollResult:
        """搜索并缓存免费 stock 视频。

        force_fresh=True：跳过 cache 命中，直接发 Pexels/Pixabay API。
        用于 P0-4 v2 触发条件：覆盖率缺口 / required windows / 长空档。
        """
        query = str(query or "").strip()
        if not query:
            return StockBrollResult("unavailable", None, [], "empty_query", 0)
        providers: list[tuple[str, str, str]] = []
        if self.pexels_key:
            providers.append(("pexels", self.pexels_key, PEXELS_API_URL))
        if self.pixabay_key:
            providers.append(("pixabay", self.pixabay_key, PIXABAY_API_URL))
        if provider:
            providers = [item for item in providers if item[0] == provider]
        cached = self._find_cached_query(
            query,
            provider=provider,
            max_results=max(1, min(int(max_results), 10)),
        )
        if not force_fresh and cached and len(cached) >= min(2, max(1, int(max_results))):
            return StockBrollResult(
                "ready",
                str(cached[0].get("source_provider") or cached[0].get("provider") or provider or ""),
                cached,
                "cache_reused",
                0,
                len(cached),
                [self._cached_candidate_summary(item, query) for item in cached],
            )
        if not providers:
            if cached:
                return StockBrollResult(
                    "ready",
                    str(cached[0].get("source_provider") or cached[0].get("provider") or provider or ""),
                    cached,
                    "cache_reused",
                    0,
                    len(cached),
                    [self._cached_candidate_summary(item, query) for item in cached],
                )
            return StockBrollResult("unavailable", None, [], "missing_api_key", 0)

        last_error = "provider_request_failed"
        metadata_limit = max(1, min(int(max_results), 10))
        combined_cached = list(cached)
        combined_summaries = [self._cached_candidate_summary(item, query) for item in cached]
        for provider, key, endpoint in providers:
            try:
                payload, attempts = self._request_json(provider, key, endpoint, query, metadata_limit)
                items, summaries = self._cache_results(
                    provider,
                    payload,
                    query=query,
                    max_results=metadata_limit,
                )
                combined_summaries.extend(summaries)
                existing_ids = {str(item.get("asset_id") or "") for item in combined_cached}
                items = [item for item in items if str(item.get("asset_id") or "") not in existing_ids]
                combined_cached.extend(items)
                if items:
                    return StockBrollResult(
                        "ready",
                        provider,
                        combined_cached,
                        None,
                        attempts,
                        len(combined_summaries),
                        combined_summaries,
                    )
                last_error = "no_video_results"
            except (
                OSError,
                ValueError,
                RuntimeError,
                urllib.error.URLError,
                json.JSONDecodeError,
            ) as exc:
                last_error = self._redact_error(exc)
        if combined_cached:
            return StockBrollResult(
                "ready",
                str(combined_cached[0].get("source_provider") or combined_cached[0].get("provider") or ""),
                combined_cached,
                "cache_reused",
                0,
                len(combined_summaries),
                combined_summaries,
            )
        return StockBrollResult("unavailable", None, [], last_error, 2, len(combined_summaries), combined_summaries)

    def _request_json(
        self,
        provider: str,
        key: str,
        endpoint: str,
        query: str,
        max_results: int,
    ) -> tuple[dict[str, Any], int]:
        params = {
            "query": visual_search_query(query),
            "per_page": max(1, min(int(max_results), 10)),
        }
        if provider == "pixabay":
            params["key"] = key
        url = f"{endpoint}?{urllib.parse.urlencode(params)}"
        headers = {"User-Agent": "VideoInsight/stock-broll"}
        if provider == "pexels":
            headers["Authorization"] = key
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                request = urllib.request.Request(url, headers=headers)
                with self.opener(request, timeout=15) as response:
                    data = response.read(2 * 1024 * 1024)
                payload = json.loads(data.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("stock provider response is not an object")
                return payload, attempt
            except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == 2:
                    break
        raise RuntimeError(self._redact_error(last_error or "stock provider request failed"))

    def _find_cached_query(
        self,
        query: str,
        *,
        provider: str | None = None,
        max_results: int = 2,
    ) -> list[dict[str, Any]]:
        """Reuse an auditable provider result before making another request."""
        normalized = visual_search_query(query).casefold()
        if not normalized:
            return []
        found: list[dict[str, Any]] = []
        for metadata_path in self.cache_root.glob("broll-*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(metadata, Mapping):
                continue
            item_provider = str(metadata.get("source_provider") or metadata.get("provider") or "")
            if provider and item_provider != provider:
                continue
            if str(metadata.get("authorization_status") or "") != "confirmed":
                continue
            if metadata.get("publish_licensed") is not True:
                continue
            cached_query = visual_search_query(
                str(metadata.get("search_query") or metadata.get("semantic_query") or "")
            ).casefold()
            if cached_query != normalized:
                continue
            cache_path = Path(str(metadata.get("cache_path") or ""))
            if not cache_path.is_file() or not local_broll_is_real(cache_path, metadata):
                continue
            found.append(dict(metadata))
        return found[: max(1, min(int(max_results), 10))]

    @staticmethod
    def _cached_candidate_summary(item: Mapping[str, Any], query: str) -> dict[str, Any]:
        return {
            "asset_id": item.get("asset_id"),
            "provider": item.get("source_provider") or item.get("provider"),
            "title": item.get("provider_title") or item.get("name"),
            "description": item.get("provider_description") or "",
            "tags": item.get("provider_tags") or item.get("keywords") or [],
            "source_url": item.get("source_url"),
            "duration_seconds": item.get("duration_seconds"),
            "aspect_ratio": item.get("aspect_ratio"),
            "score": 0,
            "accepted_for_download": True,
            "cache_reused": True,
            "query": query,
        }

    @staticmethod
    def _candidate_score(candidate: Mapping[str, Any], query: str) -> int:
        text = " ".join(
            str(candidate.get(key) or "")
            for key in (
                "title",
                "name",
                "description",
                "tags",
                "keywords",
                # Pexels frequently leaves `title` empty while its page URL
                # carries the only useful visible concept (for example
                # `phone-mounted-on-the-dashboard`).  This is retrieval
                # evidence used to rank candidates, not proof by itself in
                # the final semantic gate.
                "url",
                "pageURL",
            )
        ).casefold()
        terms = [term for term in re.findall(r"[a-z0-9]+", visual_search_query(query).casefold()) if len(term) >= 3]
        score = sum(12 for term in terms if term in text)
        phrase = visual_search_query(query).casefold()
        if phrase and phrase in text:
            score += 30
        try:
            duration = float(candidate.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration >= 2.0:
            score += 8
        elif duration > 0:
            score -= 12
        return score

    def _cache_results(
        self,
        provider: str,
        payload: Mapping[str, Any],
        *,
        query: str = "",
        max_results: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        candidates = payload.get("videos") if provider == "pexels" else payload.get("hits")
        if not isinstance(candidates, list):
            return [], []
        metadata_candidates: list[dict[str, Any]] = []
        for candidate in candidates[: max(1, min(int(max_results), 10))]:
            if not isinstance(candidate, Mapping):
                continue
            source_url, duration, width, height = self._video_candidate(provider, candidate)
            if not source_url:
                continue
            summary = {
                "provider": provider,
                "title": candidate.get("title") or candidate.get("name") or "",
                "description": candidate.get("description") or "",
                "tags": candidate.get("tags") or [],
                "source_url": str(candidate.get("url") or candidate.get("pageURL") or source_url),
                "duration_seconds": duration,
                "aspect_ratio": round(width / max(height, 1), 4) if width and height else None,
                "score": self._candidate_score(
                    {
                        "title": candidate.get("title") or candidate.get("name"),
                        "description": candidate.get("description"),
                        "tags": candidate.get("tags"),
                        "duration": duration,
                    },
                    query,
                ),
                "accepted_for_download": duration <= 0 or duration >= 2.0,
                "cache_reused": False,
                "query": query,
            }
            metadata_candidates.append({"candidate": candidate, "summary": summary})
        metadata_candidates.sort(key=lambda item: item["summary"]["score"], reverse=True)
        summaries = [item["summary"] for item in metadata_candidates]
        results: list[dict[str, Any]] = []
        download_limit = 1 if int(max_results) <= 1 else 2
        for entry in metadata_candidates[:download_limit]:
            candidate = entry["candidate"]
            if not isinstance(candidate, Mapping):
                continue
            source_url, duration, width, height = self._video_candidate(provider, candidate)
            if not source_url:
                continue
            if duration and duration < 2.0:
                continue
            media = self._download(source_url)
            digest = hashlib.sha256(media).hexdigest()
            suffix = Path(urllib.parse.urlparse(source_url).path).suffix.lower() or ".mp4"
            if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
                suffix = ".mp4"
            source_page = str(candidate.get("url") or candidate.get("pageURL") or source_url)
            source_slug = Path(urllib.parse.urlparse(source_page).path).stem.replace("-", " ")
            provider_title = str(
                candidate.get("title")
                or candidate.get("name")
                or source_slug
                or f"{provider} stock"
            ).strip()
            provider_description = str(
                candidate.get("description")
                or candidate.get("tags")
                or ""
            ).strip()
            # Cached provider media is a first-class visual asset in the
            # workflow, so use the same ID namespace accepted by
            # VideoEditorWorkflowService.resolve_visual_asset().
            asset_id = f"broll-{digest[:10]}"
            media_path = self.cache_root / f"{asset_id}{suffix}"
            media_path.write_bytes(media)
            probed = self._probe(media_path)
            item = {
                "asset_id": asset_id,
                "kind": "broll",
                "name": f"{provider} stock {asset_id[-6:]}",
                "original_name": f"{asset_id}{suffix}",
                "stored_name": media_path.name,
                "media_type": "video/mp4",
                "media_kind": "video",
                "provider": provider,
                "source_provider": provider,
                "source_url": source_page,
                "provider_title": provider_title,
                "provider_description": provider_description,
                "provider_tags": candidate.get("tags") or [],
                "license_name": "Pexels License" if provider == "pexels" else "Pixabay Content License",
                "license_url": PEXELS_LICENSE_URL if provider == "pexels" else PIXABAY_LICENSE_URL,
                "authorization_status": "confirmed",
                "rights_status": "provider_license",
                "publish_licensed": True,
                "asset_origin": "stock_video_asset",
                "source_type": "provider_cache",
                "rights_holder": provider.title(),
                "rights_confirmed_at": "provider-license",
                "created_at": datetime.now().astimezone().isoformat(),
                "sha256": digest,
                "duration_seconds": round(probed.get("duration_seconds") or duration or 0, 3),
                "width": int(probed.get("width") or width or 0),
                "height": int(probed.get("height") or height or 0),
                "aspect_ratio": round(
                    (int(probed.get("width") or width or 0) / max(int(probed.get("height") or height or 1), 1)), 4
                ),
                "cache_path": str(media_path),
                # Search intent is part of provenance, not a license claim.
                # It lets the next local run reuse a provider cache entry for
                # the same semantic shot before issuing another search.
                "semantic_query": query,
                "search_query": visual_search_query(query),
                "keywords": [query] if query else [],
                # Locale/search language is not evidence of a Chinese scene.
                "domestic_context": "international",
                "domestic_scene": "",
            }
            (self.cache_root / f"{asset_id}.json").write_text(
                json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            results.append(item)
            entry["summary"]["asset_id"] = asset_id
            entry["summary"]["downloaded"] = True
        return results, summaries

    @staticmethod
    def _video_candidate(provider: str, candidate: Mapping[str, Any]) -> tuple[str, float, int, int]:
        if provider == "pexels":
            files = candidate.get("video_files")
            files = files if isinstance(files, list) else []
            ranked = sorted(
                (item for item in files if isinstance(item, Mapping) and item.get("link")),
                key=lambda item: (abs(int(item.get("width") or 720) - 720), int(item.get("width") or 0)),
            )
            selected = ranked[0] if ranked else {}
            return str(selected.get("link") or ""), float(candidate.get("duration") or 0), int(selected.get("width") or 0), int(selected.get("height") or 0)
        videos = candidate.get("videos")
        videos = videos if isinstance(videos, Mapping) else {}
        selected = next((videos.get(name) for name in ("medium", "small", "large") if isinstance(videos.get(name), Mapping)), {})
        return str(selected.get("url") or ""), float(candidate.get("duration") or 0), int(selected.get("width") or 0), int(selected.get("height") or 0)

    def _download(self, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "VideoInsight/stock-broll"})
                with self.opener(request, timeout=30) as response:
                    data = response.read(80 * 1024 * 1024 + 1)
                if len(data) > 80 * 1024 * 1024:
                    raise ValueError("stock video exceeds cache limit")
                if len(data) < 1024:
                    raise ValueError("stock video download is empty")
                return data
            except (OSError, ValueError, urllib.error.URLError) as exc:
                last_error = exc
                if attempt == 2:
                    break
        raise RuntimeError(self._redact_error(last_error or "stock video download failed"))

    def _probe(self, path: Path) -> dict[str, Any]:
        result = self.runner(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("stock video cannot be probed")
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        return {
            "duration_seconds": float((payload.get("format") or {}).get("duration") or 0),
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
        }


def local_broll_is_real(path: Path, metadata: Mapping[str, Any]) -> bool:
    """Reject placeholders/icons while keeping genuine uploaded photos/videos."""
    media_type = str(metadata.get("media_type") or "")
    if media_type.startswith("video/"):
        return float(metadata.get("duration_seconds") or 0) >= 1.0
    if not media_type.startswith("image/") or not path.is_file():
        return False
    try:
        from PIL import Image

        image = Image.open(path).convert("RGB")
        original_width, original_height = image.size
        sample = image.resize((24, 24))
        colors = sample.getcolors(maxcolors=1024) or []
        return (
            len(colors) >= 8
            and original_width >= 64
            and original_height >= 64
        )
    except Exception:
        return False
