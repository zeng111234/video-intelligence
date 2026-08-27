"""Pure boundary for compiling the canonical director timeline to Aliyun IMS.

This module deliberately does not call Aliyun, upload OSS objects, or submit a
job.  The local FFmpeg renderer remains the active safe path.  Once a customer
has confirmed IMS subscription, OSS placement, and the displayed estimate, the
returned payload is the only shape that a future ``SubmitMediaProducingJob``
client should submit.  ``SubmitBatchMediaProducingJob`` is intentionally not a
精剪 path here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import quote, urlencode

import httpx


IMS_SUBMIT_ACTION = "SubmitMediaProducingJob"
IMS_TIMELINE_DOC = (
    "https://help.aliyun.com/zh/ims/developer-reference/"
    "api-ice-2020-11-09-submitmediaproducingjob"
)
IMS_TIMELINE_CONFIG_DOC = (
    "https://help.aliyun.com/zh/ims/developer-reference/timeline-configuration-description"
)
IMS_PRICING_DOC = "https://help.aliyun.com/zh/ims/video-clip"

_ORDINARY_CNY_PER_MINUTE = {"480p": 0.015, "720p": 0.03, "1080p": 0.06}
_ADVANCED_CNY_PER_MINUTE = {"1080p": 1.5}


class IMSRequestError(RuntimeError):
    """A redacted IMS request failure; request bodies never contain secrets."""


class AliyunIMSTimelineClient:
    """Minimal SubmitMediaProducingJob/GetMediaProducingJob boundary.

    The director remains the source of truth. This client only signs and
    submits the already compiled Timeline after the caller has completed the
    subscription, cost, OSS and idempotency checks.
    """

    def __init__(self, config: Any, *, timeout_seconds: float = 30.0) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds

    @property
    def _endpoint(self) -> str:
        return f"https://ice.{self.config.aliyun_region}.aliyuncs.com/"

    def _signed_body(
        self,
        action: str,
        parameters: Mapping[str, str],
        *,
        client_token: str | None = None,
    ) -> bytes:
        now = datetime.now(timezone.utc)
        params = {
            "AccessKeyId": self.config.access_key_id,
            "Action": action,
            "Format": "JSON",
            "RegionId": self.config.aliyun_region,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": uuid.uuid4().hex,
            "SignatureVersion": "1.0",
            "Timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Version": "2020-11-09",
            **parameters,
        }
        if client_token:
            params["ClientToken"] = client_token
        canonicalized = "&".join(
            f"{quote(str(key), safe='~')}={quote(str(value), safe='~')}"
            for key, value in sorted(params.items())
        )
        string_to_sign = f"POST&%2F&{quote(canonicalized, safe='~')}"
        signature = base64.b64encode(
            hmac.new(
                f"{self.config.access_key_secret}&".encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest(),
        ).decode("ascii")
        return urlencode({**params, "Signature": signature}).encode("utf-8")

    def build_submit_request(
        self,
        compiled: Mapping[str, Any],
        *,
        output_media_url: str,
        client_token: str,
        output_width: int = 720,
        output_height: int = 1280,
    ) -> tuple[str, dict[str, str], bytes]:
        if compiled.get("action") != IMS_SUBMIT_ACTION:
            raise IMSRequestError("Timeline action 不是 SubmitMediaProducingJob。")
        if not output_media_url.startswith("https://"):
            raise IMSRequestError("IMS 输出必须使用 OSS HTTPS 地址。")
        if not client_token or len(client_token) > 64 or not client_token.isascii():
            raise IMSRequestError("IMS ClientToken 不符合幂等要求。")
        timeline = json.dumps(
            compiled["timeline"], ensure_ascii=False, separators=(",", ":")
        )
        output_config = json.dumps(
            {
                "MediaURL": output_media_url,
                "Width": output_width,
                "Height": output_height,
            },
            separators=(",", ":"),
        )
        body = self._signed_body(
            IMS_SUBMIT_ACTION,
            {
                "Timeline": timeline,
                "OutputMediaTarget": "oss-object",
                "OutputMediaConfig": output_config,
                "Source": "OpenAPI",
            },
            client_token=client_token,
        )
        return self._endpoint, {"Content-Type": "application/x-www-form-urlencoded"}, body

    def _request(self, action: str, parameters: Mapping[str, str]) -> dict[str, Any]:
        body = self._signed_body(action, parameters)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = httpx.post(
                    self._endpoint,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    content=body,
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise IMSRequestError("IMS 返回格式不正确。")
                return payload
            except httpx.TransportError as exc:
                last_error = exc
                if attempt == 0:
                    continue
            except (httpx.HTTPStatusError, ValueError) as exc:
                detail = ""
                if isinstance(exc, httpx.HTTPStatusError):
                    detail = exc.response.text[:240].strip()
                raise IMSRequestError(f"IMS {action} 请求失败：{detail or '响应无效'}") from exc
        raise IMSRequestError(f"IMS {action} 连接失败，结果未确认。") from last_error

    def submit(self, compiled: Mapping[str, Any], *, output_media_url: str, client_token: str) -> dict[str, Any]:
        endpoint, headers, body = self.build_submit_request(
            compiled,
            output_media_url=output_media_url,
            client_token=client_token,
        )
        del endpoint, headers  # the request is sent through the bounded retry helper
        return self._request(
            IMS_SUBMIT_ACTION,
            {
                "Timeline": json.dumps(compiled["timeline"], ensure_ascii=False, separators=(",", ":")),
                "OutputMediaTarget": "oss-object",
                "OutputMediaConfig": json.dumps({"MediaURL": output_media_url, "Width": 720, "Height": 1280}, separators=(",", ":")),
                "Source": "OpenAPI",
                "ClientToken": client_token,
            },
        )

    def query(self, job_id: str) -> dict[str, Any]:
        if not job_id.strip():
            raise IMSRequestError("IMS JobId 不能为空。")
        return self._request("GetMediaProducingJob", {"JobId": job_id})


def _clip(
    media: Mapping[str, Any],
    *,
    timeline_start: float,
    timeline_end: float,
    mode: str = "full",
) -> dict[str, Any]:
    source_start = float(media.get("source_start") or media.get("in") or 0)
    source_end = float(media.get("source_end") or media.get("out") or 0)
    payload: dict[str, Any] = {
        "MediaURL": media.get("media_url") or media.get("source_url"),
        "Type": "Image" if str(media.get("media_kind") or "").lower() == "image" else "Video",
        "In": round(source_start, 3),
        "Out": round(source_end, 3),
        "TimelineIn": round(float(timeline_start), 3),
        "TimelineOut": round(float(timeline_end), 3),
    }
    if mode == "pip":
        geometry = media.get("geometry") or {}
        payload.update(
            {
                "X": geometry.get("left", 0.6667),
                "Y": geometry.get("top", 0.6148),
                "Width": geometry.get("width", 0.30),
                "Height": geometry.get("height", 0.17),
                "AdaptMode": "Contain",
            }
        )
    elif media.get("adapt_mode"):
        payload.update(
            {
                "AdaptMode": str(media.get("adapt_mode")),
                "Width": media.get("width", 720),
                "Height": media.get("height", 1280),
            }
        )
    if media.get("effects"):
        payload["Effects"] = list(media["effects"])
    return {key: value for key, value in payload.items() if value is not None}


def compile_director_timeline_to_ims(
    director_timeline: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile one canonical timeline without making a provider call."""

    shots = [
        item for item in director_timeline.get("shots") or [] if isinstance(item, Mapping)
    ]
    video_main: list[dict[str, Any]] = []
    video_overlay_full: list[dict[str, Any]] = []
    video_overlay: list[dict[str, Any]] = []
    for shot in shots:
        if not shot.get("media_url") and not shot.get("source_url"):
            continue
        clip = _clip(
            shot,
            timeline_start=float(shot.get("timeline_start") or shot.get("start") or 0),
            timeline_end=float(shot.get("timeline_end") or shot.get("end") or 0),
            mode=str(shot.get("mode") or shot.get("overlay_mode") or "full"),
        )
        mode = str(shot.get("mode") or shot.get("overlay_mode") or "full")
        if mode == "pip":
            video_overlay.append(clip)
        elif shot.get("main_track") is True or str(shot.get("intent") or "").casefold() == "speaker":
            video_main.append(clip)
        else:
            video_overlay_full.append(clip)

    subtitle_clips = [
        {
            "Text": str(item.get("text") or ""),
            "TimelineIn": round(float(item.get("start") or 0), 3),
            "TimelineOut": round(float(item.get("end") or 0), 3),
        }
        for item in director_timeline.get("subtitles") or []
        if isinstance(item, Mapping) and float(item.get("end") or 0) > float(item.get("start") or 0)
    ]
    subtitle_clips.extend(
        {
            "Type": "Text",
            "Content": str(item.get("text") or ""),
            "X": item.get("x", 0.08),
            "Y": item.get("y", 0.18),
            "FontSize": int(item.get("font_size") or 64),
            "FontColor": item.get("font_color", "#FFD447"),
            "TimelineIn": round(float(item.get("start") or 0), 3),
            "TimelineOut": round(float(item.get("end") or 0), 3),
        }
        for item in director_timeline.get("text_overlays") or []
        if isinstance(item, Mapping) and float(item.get("end") or 0) > float(item.get("start") or 0)
    )
    audio = director_timeline.get("bgm")
    bgm_segments = [
        item for item in director_timeline.get("bgm_segments") or [] if isinstance(item, Mapping)
    ]
    timeline: dict[str, Any] = {
        "VideoTracks": [],
        "SubtitleTracks": [{"SubtitleTrackClips": subtitle_clips}] if subtitle_clips else [],
        "AudioTracks": [],
        "EffectTracks": [],
    }
    if video_main:
        timeline["VideoTracks"].append({"MainTrack": True, "VideoTrackClips": video_main})
    if video_overlay_full:
        timeline["VideoTracks"].append({"MainTrack": False, "VideoTrackClips": video_overlay_full})
    if video_overlay:
        timeline["VideoTracks"].append({"MainTrack": False, "VideoTrackClips": video_overlay})
    if bgm_segments:
        timeline["AudioTracks"].append(
            {
                "AudioTrackClips": [
                    _clip(
                        item,
                        timeline_start=float(item.get("start") or 0),
                        timeline_end=float(item.get("end") or 0),
                    )
                    for item in bgm_segments
                    if (item.get("media_url") or item.get("source_url"))
                    and float(item.get("end") or 0) > float(item.get("start") or 0)
                ]
            }
        )
    elif isinstance(audio, Mapping) and (audio.get("media_url") or audio.get("source_url")):
        timeline["AudioTracks"].append(
            {
                "AudioTrackClips": [
                    _clip(
                        audio,
                        timeline_start=float(audio.get("start") or 0),
                        timeline_end=float(audio.get("end") or director_timeline.get("duration_seconds") or 0),
                    )
                ]
            }
        )
    return {
        "action": IMS_SUBMIT_ACTION,
        "timeline": timeline,
        "source_of_truth": "canonical_director_timeline",
        "renderer_only": True,
        "cloud_call_made": False,
        "forbidden_action": "SubmitBatchMediaProducingJob",
        "docs": {
            "submit": IMS_TIMELINE_DOC,
            "timeline": IMS_TIMELINE_CONFIG_DOC,
            "pricing": IMS_PRICING_DOC,
        },
    }


def quote_ims_video_clip(
    *,
    duration_seconds: float,
    profile: str,
    advanced_template: bool = False,
) -> dict[str, Any]:
    """Return the documented video-editing estimate, excluding extra services."""

    normalized = str(profile or "720p").lower()
    table = _ADVANCED_CNY_PER_MINUTE if advanced_template else _ORDINARY_CNY_PER_MINUTE
    unit = table.get(normalized)
    if unit is None:
        return {
            "status": "blocked",
            "reason": "profile_price_not_in_local_documented_table",
            "provider_cost_cny": "暂无法确定",
            "pricing_doc": IMS_PRICING_DOC,
        }
    billable_minutes = max(1, math.ceil(max(float(duration_seconds), 0) / 60))
    return {
        "status": "estimate_only",
        "profile": normalized,
        "advanced_template": advanced_template,
        "billable_minutes": billable_minutes,
        "unit_price_cny": unit,
        "estimated_video_clip_cny": round(unit * billable_minutes, 4),
        "extra_costs": ["IMS subscription", "OSS", "traffic", "ASR or other intelligent tasks"],
        "pricing_doc": IMS_PRICING_DOC,
    }


def prepare_ims_submission(
    director_timeline: Mapping[str, Any],
    *,
    subscription_confirmed: bool,
    oss_media_ready: bool,
    cost_confirmed: bool,
) -> dict[str, Any]:
    """Return a blocked/ready boundary; never submit or upload."""

    compiled = compile_director_timeline_to_ims(director_timeline)
    missing = []
    if not subscription_confirmed:
        missing.append("ims_subscription_confirmation")
    if not oss_media_ready:
        missing.append("same_region_oss_media")
    if not cost_confirmed:
        missing.append("customer_cost_confirmation")
    return {
        **compiled,
        "status": "blocked_pending_confirmation" if missing else "ready_for_explicit_submit",
        "missing_confirmations": missing,
        "submit_allowed": not missing,
        "idempotency_required": True,
        "pause_point": "不满足订阅、OSS、费用三项前置条件时保持本地 FFmpeg 路径，不调用 IMS。",
    }
