"""Create private transcription tasks from one experimental Douyin share link."""

from __future__ import annotations

from dataclasses import dataclass

from src.adapters.douyin_parser import (
    DouyinParserError,
    LocalDouyinBrowserParserClient,
    parse_douyin_share_text,
)
from src.adapters.licensed import LicensedProviderError
from src.models import Platform, TranscriptionTask
from src.services.transcription import MAX_PROVIDER_MEDIA_BYTES, TranscriptionError
from src.services.video_source import VideoSourceError, fetch_authorized_video


@dataclass(frozen=True)
class LinkTranscriptionPreview:
    share_url: str
    work_id: str | None
    parser_enabled: bool
    parser_message: str | None
    oneapi_fallback_available: bool
    oneapi_estimated_cost_cny: float | None


class DouyinLinkTranscriptionService:
    def __init__(self, parser: LocalDouyinBrowserParserClient, provider, transcription_service) -> None:
        self.parser = parser
        self.provider = provider
        self.transcription_service = transcription_service

    def preview(self, share_text: str) -> LinkTranscriptionPreview:
        link = parse_douyin_share_text(share_text)
        parser_enabled, parser_message = self.parser.capabilities()
        price = self._fallback_price()
        return LinkTranscriptionPreview(
            share_url=link.share_url,
            work_id=link.work_id,
            parser_enabled=parser_enabled,
            parser_message=parser_message,
            oneapi_fallback_available=price is not None and link.work_id is not None,
            oneapi_estimated_cost_cny=price,
        )

    def transcribe_experimental(
        self,
        *,
        share_text: str,
        rights_holder: str,
        rights_confirmed: bool,
        model_name: str = "large-v3-turbo",
        candidate_id: str | None = None,
    ) -> TranscriptionTask:
        media = self.parser.resolve(share_text)
        return self._create_task(
            media_url=media.media_url,
            media_name=f"douyin-{media.work_id}.mp4",
            title=media.title,
            work_id=media.work_id,
            source_url=media.share_url,
            source_kind="douyin_local_browser",
            media_request_headers=media.media_request_headers,
            rights_holder=rights_holder,
            rights_confirmed=rights_confirmed,
            model_name=model_name,
            candidate_id=candidate_id,
        )

    def transcribe_oneapi_fallback(
        self,
        *,
        share_text: str,
        work_id: str,
        rights_holder: str,
        rights_confirmed: bool,
        idempotency_key: str,
        model_name: str = "large-v3-turbo",
    ) -> TranscriptionTask:
        link = parse_douyin_share_text(share_text)
        if not work_id.isdigit():
            raise DouyinParserError("缺少有效作品 ID，无法确认 OneAPI 回退。")
        try:
            result = self.provider.resolve_media_url(Platform.DOUYIN, work_id, idempotency_key)
        except LicensedProviderError as exc:
            raise DouyinParserError(str(exc), work_id=work_id) from exc
        return self._create_task(
            media_url=str(result.media_url),
            media_name=f"douyin-{work_id}.mp4",
            title=f"抖音作品 {work_id}",
            work_id=work_id,
            source_url=link.share_url,
            source_kind="douyin_oneapi_fallback",
            media_request_headers=None,
            rights_holder=rights_holder,
            rights_confirmed=rights_confirmed,
            model_name=model_name,
        )

    def _create_task(
        self,
        *,
        media_url: str,
        media_name: str,
        title: str,
        work_id: str,
        source_url: str,
        source_kind: str,
        media_request_headers: dict[str, str] | None,
        rights_holder: str,
        rights_confirmed: bool,
        model_name: str,
        candidate_id: str | None = None,
    ) -> TranscriptionTask:
        try:
            video = fetch_authorized_video(
                media_url,
                require_extension=False,
                max_bytes=MAX_PROVIDER_MEDIA_BYTES,
                fallback_name=media_name,
                request_headers=media_request_headers,
            )
            task = self.transcription_service.create_task(
                media_name=video.name,
                media_type=video.media_type,
                media_bytes=video.content,
                rights_confirmed=rights_confirmed,
                rights_holder=rights_holder,
                candidate_id=candidate_id or f"douyin-{work_id}",
                model_name=model_name,
                max_media_bytes=MAX_PROVIDER_MEDIA_BYTES,
                source_kind=source_kind,
                source_url=source_url,
            )
        except (VideoSourceError, TranscriptionError) as exc:
            raise DouyinParserError(getattr(exc, "user_message", str(exc)), work_id=work_id) from exc
        return task

    def _fallback_price(self) -> float | None:
        try:
            return self.provider.media_resolution_price(Platform.DOUYIN)
        except (AttributeError, LicensedProviderError):
            return None
