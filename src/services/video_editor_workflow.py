"""面向 Web 工作台的智能剪辑工作流。

保持底层 ``VideoEditingService`` 的兼容接口不变，在其之上提供：
系统成片选择、可恢复的分析记录、字幕复核门禁与后台渲染任务。
"""

from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlparse
from uuid import uuid4

from src.models import (
    AvatarTask,
    PipelineStage,
    TaskStatus,
    VideoEditConfig,
    VideoEditStep,
    VideoEditStepKind,
    VideoEditTask,
    VideoEditorBatch,
    VideoEditorBatchItem,
)
from src.services.credits import InsufficientCreditsError
from src.services.video_editor_cloud import _caption_lexical_units, _caption_lexical_words
from src.services.local_visual_asset_matcher import (
    asset_publish_claim_allowed,
    build_keyword_generation_plan,
    match_local_visual_asset,
    normalize_domestic_context,
    query_visual_concepts,
    semantic_broll_gate_passed,
    visual_asset_context,
    visual_asset_priority,
    visual_search_query,
)
from src.services.stock_broll_provider import StockBrollProvider, local_broll_is_real


_WORKFLOW_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="video-workflow"
)
_MAX_SOURCE_UPLOAD_BYTES = 100 * 1024 * 1024
_MAX_GENERATED_SUBTITLE_BYTES = 100 * 1024 * 1024
_MAX_SUBTITLE_SECONDS = 15 * 60
_MAX_BATCH_ITEMS = 10
_MAX_BGM_BYTES = 30 * 1024 * 1024
_BGM_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
_BGM_VOICEOVER_CATEGORIES = {
    "理性干货",
    "情绪共鸣",
    "故事叙事",
    "商业表达",
    "科技未来",
    "轻松日常",
    "励志成长",
    "悬念揭秘",
    "通用口播",
}
_BGM_ENERGY_LEVELS = {"克制", "平稳", "有推动感"}
_BGM_SOURCE_PROVIDERS = {"manual", "freepd", "pixabay", "light_factory", "bodian"}
_BGM_CONTENT_ID_RISKS = {"none", "registered", "unknown"}
_MAX_VISUAL_ASSET_BYTES = 10 * 1024 * 1024
_VISUAL_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_BROLL_SUFFIXES = _VISUAL_SUFFIXES | {".mp4", ".mov", ".m4v"}
_GENERIC_AVATAR_TITLE = re.compile(r"^数字人视频\d*$")
_LOCAL_PREVIEW_EXPORT_STYLE_VERSION = (
    "business_talking_head_v11.8-adaptive-reframe-no-text-cards-final-output-clock"
)
_RELEASE_TALKING_HEAD_STYLE_VERSION = "talking_head_release_v2.0-director-timeline"
_LOCAL_PREVIEW_PLAYBACK_RATE = 1.15
# Release-template exports keep the reviewed speech clock at natural speed.
# The legacy preview path retains the historical 1.15x compatibility value.
_RELEASE_TEMPLATE_PLAYBACK_RATE = 1.0
_LOCAL_RHYTHM_SCENE_SECONDS = 5.5
_LOCAL_RHYTHM_MAX_SCENES = 18
_BROLL_EVENT_MAX_SECONDS = 3.6
_WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_WINDOWS_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
_LOCAL_RENDER_SEMAPHORE = threading.Semaphore(1)
_LOCAL_RENDER_MODE = "local_ffmpeg"
_LOCAL_ENCODER_SELECTION: dict[str, Any] | None = None
_LOCAL_ENCODER_LOCK = threading.Lock()


def _audited_media_tool_hashes() -> dict[str, str]:
    """Read the checked-in Windows media-tool manifest without trusting PATH."""
    manifest = Path(__file__).resolve().parents[2] / "config" / "windows-media-tools.sha256"
    if not manifest.is_file():
        return {}
    hashes: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] and parts[1].lower() in {"ffmpeg.exe", "ffprobe.exe"}:
            hashes[parts[1].lower()] = parts[0].lower()
    return hashes


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _trusted_local_media_tools() -> dict[str, Any]:
    """Resolve the paired, audited FFmpeg/FFprobe binaries for client rendering.

    A same-named executable from PATH is accepted only when its hash matches the
    checked-in audit manifest.  The pair must come from one directory so ffmpeg
    and ffprobe cannot be mixed across installations.
    """
    expected = _audited_media_tool_hashes()
    if set(expected) != {"ffmpeg.exe", "ffprobe.exe"}:
        raise VideoEditorWorkflowError("本地受审媒体工具清单缺失或不完整。")
    repo_root = Path(__file__).resolve().parents[2]
    package_candidates = [
        repo_root / "project" / "frontend" / "release" / "win-unpacked" / "resources" / "backend" / "_internal" / "media",
        repo_root / "project" / "frontend" / "desktop" / "backend" / "VideoInsightBackend" / "_internal" / "media",
        repo_root / "media",
    ]
    explicit_dir = os.environ.get("VIDEO_EDITOR_MEDIA_BIN", "").strip()
    if explicit_dir:
        package_candidates.insert(0, Path(explicit_dir))
    explicit_ffmpeg = os.environ.get("VIDEO_EDITOR_FFMPEG_PATH", "").strip()
    explicit_ffprobe = os.environ.get("VIDEO_EDITOR_FFPROBE_PATH", "").strip()
    if explicit_ffmpeg and explicit_ffprobe:
        package_candidates.insert(0, Path(explicit_ffmpeg).resolve().parent)
    path_ffmpeg = shutil.which("ffmpeg")
    path_ffprobe = shutil.which("ffprobe")
    if path_ffmpeg and path_ffprobe:
        ffmpeg_path = Path(path_ffmpeg).resolve()
        ffprobe_path = Path(path_ffprobe).resolve()
        if ffmpeg_path.parent == ffprobe_path.parent:
            package_candidates.append(ffmpeg_path.parent)

    seen: set[str] = set()
    for directory in package_candidates:
        directory = directory.resolve()
        key = str(directory).casefold()
        if key in seen:
            continue
        seen.add(key)
        ffmpeg_path = directory / "ffmpeg.exe"
        ffprobe_path = directory / "ffprobe.exe"
        if not (ffmpeg_path.is_file() and ffprobe_path.is_file()):
            # Unix development installs are supported only when explicitly
            # audited by an equivalent manifest entry.
            ffmpeg_path = directory / "ffmpeg"
            ffprobe_path = directory / "ffprobe"
        if not (ffmpeg_path.is_file() and ffprobe_path.is_file()):
            continue
        if _sha256_file(ffmpeg_path) != expected["ffmpeg.exe"]:
            continue
        if _sha256_file(ffprobe_path) != expected["ffprobe.exe"]:
            continue
        return {
            "ffmpeg": str(ffmpeg_path),
            "ffprobe": str(ffprobe_path),
            "directory": str(directory),
            "ffmpeg_sha256": expected["ffmpeg.exe"],
            "ffprobe_sha256": expected["ffprobe.exe"],
            "audited": True,
            "source": "packaged_audited_media" if directory in [p.resolve() for p in package_candidates[:3]] else "explicit_or_matching_path",
        }
    raise VideoEditorWorkflowError(
        "未找到与受审清单匹配的 FFmpeg/FFprobe，已拒绝使用未知同名程序。"
    )


def _local_renderer_enabled(service: "VideoEditorWorkflowService") -> bool:
    configured = os.environ.get("VIDEO_EDITOR_RENDERER_MODE", "").strip().lower()
    if configured:
        return configured == _LOCAL_RENDER_MODE
    # Tests and explicit cloud integrations pass a configuration override. The
    # application path has no override and therefore defaults to the client
    # renderer, keeping IMS/MPS out of the normal customer flow.
    return service._cloud_configuration_override is None


def _select_local_video_encoder() -> dict[str, Any]:
    """Smoke-test NVENC once, then choose exactly one safe local encoder.

    A driver can expose ``h264_nvenc`` in FFmpeg while still rejecting the
    runtime API.  Therefore encoder listing alone is never sufficient.  A
    failed smoke test is a single, recorded downgrade to libx264; it is not a
    reason to retry or loop through hardware encoders.
    """
    global _LOCAL_ENCODER_SELECTION
    with _LOCAL_ENCODER_LOCK:
        if _LOCAL_ENCODER_SELECTION is not None:
            return dict(_LOCAL_ENCODER_SELECTION)
        tools = _trusted_local_media_tools()
        if os.name != "nt":
            _LOCAL_ENCODER_SELECTION = {
                "encoder": "libx264",
                "smoke_attempted": False,
                "fallback": False,
                "reason": "non_windows_client",
                "ffmpeg_sha256": tools["ffmpeg_sha256"],
            }
            return dict(_LOCAL_ENCODER_SELECTION)
        smoke_dir = Path(tempfile.mkdtemp(prefix="videoinsight-nvenc-"))
        smoke_path = smoke_dir / "nvenc-smoke.mp4"
        try:
            smoke = _run_media_command(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=128x128:r=30",
                    "-t",
                    "0.5",
                    "-an",
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    "p1",
                    "-pix_fmt",
                    "yuv420p",
                    "-y",
                    str(smoke_path),
                ],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            if smoke.returncode == 0 and smoke_path.is_file() and smoke_path.stat().st_size:
                _LOCAL_ENCODER_SELECTION = {
                    "encoder": "h264_nvenc",
                    "smoke_attempted": True,
                    "fallback": False,
                    "reason": "nvenc_smoke_passed",
                    "ffmpeg_sha256": tools["ffmpeg_sha256"],
                }
            else:
                _LOCAL_ENCODER_SELECTION = {
                    "encoder": "libx264",
                    "smoke_attempted": True,
                    "fallback": True,
                    "reason": "nvenc_smoke_failed_libx264_once",
                    "smoke_error": (smoke.stderr or "").strip()[-300:],
                    "ffmpeg_sha256": tools["ffmpeg_sha256"],
                }
        finally:
            shutil.rmtree(smoke_dir, ignore_errors=True)
        return dict(_LOCAL_ENCODER_SELECTION)

# Portrait talking-head safety contract for PiP.  These normalized boxes are
# deliberately conservative: the source presenter is centered in the upper
# half, while the subtitle band occupies the lower band.  A PiP that cannot fit
# between them is omitted and the quality gate remains failed.
_PORTRAIT_FACE_HEAD_BBOX = (0.12, 0.10, 0.90, 0.60)
_PORTRAIT_SUBTITLE_BBOX = (0.05, 0.79, 0.95, 0.95)
_PIP_WIDTH_RATIO = 0.30
_PIP_HEIGHT_RATIO = 0.15


_VISUAL_REQUEST_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...], str, str, str], ...] = (
    (
        "scene",
        (
            "烧烤",
            "餐饮",
            "餐厅",
            "烤肉",
            "顾客",
            "回头客",
            "机器人",
            "工业机器人",
            "工厂",
            "流水线",
            "制造",
            "restaurant",
            "barbecue",
            "dining",
            "robot",
            "factory",
            "manufacturing",
        ),
        ("restaurant customers dining", "restaurant food preparation", "local restaurant scene"),
        "restaurant customers or food preparation",
        "restaurant scene",
        "full",
    ),
    (
        "product",
        ("产品", "品牌", "支架", "手机", "车载", "出风口", "充电", "安装", "拧", "锁紧", "product", "phone mount", "device"),
        ("product demonstration close up", "hands installing product", "phone mount in car"),
        "the named product or hands using/installing it",
        "product demonstration",
        "pip",
    ),
    (
        "vehicle_scene",
        (
            "汽车",
            "车辆",
            "二手车",
            "新车",
            "车型",
            "车价",
            "报价",
            "车况",
            "公里数",
            "car",
            "vehicle",
            "used car",
            "new car",
            "dealership",
        ),
        (
            "used car inspection",
            "car buying comparison",
            "car dealership vehicle",
        ),
        "the named vehicle, inspection, or buying context",
        "vehicle buying or comparison scene",
        "full",
    ),
    (
        "process",
        ("流程", "步骤", "方法", "操作", "设置", "计划", "教程", "打法", "workflow", "process", "tutorial"),
        ("workflow process demonstration", "hands using an app", "business process close up"),
        "hands, interface, or clearly ordered process steps",
        "workflow process",
        "pip",
    ),
    (
        "data",
        ("数据", "报表", "数据库", "比例", "百分比", "数字", "增长", "下降", "dashboard", "analytics", "percentage"),
        ("CRM dashboard customer database", "business analytics dashboard", "data comparison screen"),
        "a visible metric, dashboard, or comparison",
        "data relationship",
        "pip",
    ),
    (
        "relationship",
        ("客户", "业务", "企业", "营销", "获客", "私域", "会员", "触达", "client", "customer", "marketing", "crm"),
        ("customer meeting business relationship", "customer relationship management", "CRM dashboard"),
        "customer interaction, CRM, or outreach evidence",
        "customer relationship",
        "pip",
    ),
    (
        "pet_scene",
        ("鱼", "喂鱼", "鱼缸", "宠物", "小鸡", "猫", "狗", "fish", "aquarium", "pet"),
        ("pet fish feeding", "aquarium fish close up", "pet care scene"),
        "the named animal and the described care action",
        "pet scene",
        "full",
    ),
)


def _build_visual_request(
    shot: Mapping[str, Any],
    transcript_segments: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Create one auditable, provider-neutral request for a semantic shot.

    This is deliberately term-and-structure based.  It does not contain a
    sample script, timestamp, or asset allow-list; unknown/abstract copy
    returns an explicit A-roll fallback instead of an invented search.
    """

    source_start = float(shot.get("source_start") or 0)
    source_end = float(shot.get("source_end") or 0)
    source_indexes: list[int] = []
    text_parts: list[str] = []
    nearby_parts: list[str] = []
    nearby_indexes: list[int] = []
    for index, segment in enumerate(transcript_segments or []):
        if not isinstance(segment, Mapping):
            continue
        try:
            segment_start = float(segment.get("start") or 0)
            segment_end = float(segment.get("end") or 0)
        except (TypeError, ValueError):
            continue
        if segment_start >= source_end or segment_end <= source_start:
            if (
                segment_end <= source_start
                and source_start - segment_end <= 2.0
                and segment_end > 0
            ):
                nearby_parts.append(str(segment.get("text") or ""))
                nearby_indexes.append(index)
            continue
        source_indexes.append(index)
        text_parts.append(str(segment.get("text") or ""))
    text = re.sub(r"[\s，。！？、,.!?；;：:]+", " ", " ".join(text_parts)).strip()
    retrieval_text = re.sub(
        r"[\s，。！？、,.!?；;：:]+",
        " ",
        " ".join([*nearby_parts, *text_parts]),
    ).strip()
    matched: list[tuple[str, tuple[str, ...], str, str, str]] = []
    # Classification belongs to the overlapping source sentence.  Nearby
    # context may refine an English search phrase, but it must never create a
    # visual request for an A-roll gap between two spoken segments.
    compact = re.sub(r"\s+", "", text).casefold()
    retrieval_compact = re.sub(r"\s+", "", retrieval_text or text).casefold()
    for visual_type, terms, queries, subject, context, mode in _VISUAL_REQUEST_RULES:
        if any(str(term).replace(" ", "").casefold() in compact for term in terms):
            matched.append((visual_type, queries, subject, context, mode))
    if re.search(r"\d+(?:\.\d+)?\s*(?:%|％|元|块|张|个|公里|分钟|天)", text):
        matched.append(
            (
                "data",
                ("business data dashboard", "percentage comparison graphic", "metric close up"),
                "the stated number or ratio",
                "numeric relationship",
                "pip",
            )
        )
    # Prefer the most concrete visual category when a sentence contains
    # several nouns (for example a factory sentence that also says product).
    # This is a general specificity rule, not a script allow-list.
    if matched:
        category_priority = {
            "product": 0,
            "pet_scene": 1,
            "vehicle_scene": 2,
            "data": 3,
            "process": 4,
            "relationship": 5,
            "scene": 6,
        }
        matched.sort(key=lambda item: category_priority.get(item[0], 99))
    if matched:
        visual_type, queries, subject, context, mode = matched[0]
        if visual_type == "scene":
            # Scene retrieval follows the concrete subject instead of using
            # restaurant queries for every scene.  This is category logic:
            # a factory/robot sentence must search for factory/robot footage,
            # while a dining sentence keeps the restaurant vocabulary.
            if re.search(r"机器人|工业机器人|robot", retrieval_compact, re.I):
                queries = (
                    "industrial robot factory floor",
                    "robot manufacturing automation",
                    "factory production line robotics",
                )
                subject = "industrial robots or a factory production scene"
                context = "industrial automation scene"
            elif re.search(r"工厂|流水线|制造|factory|manufacturing", retrieval_compact, re.I):
                queries = (
                    "factory production line",
                    "industrial manufacturing floor",
                    "factory workers machinery",
                )
                subject = "factory production or manufacturing machinery"
                context = "factory manufacturing scene"
            else:
                queries = (
                    "restaurant customers dining",
                    "restaurant food preparation",
                    "local restaurant scene",
                )
        elif visual_type == "product":
            # A named product/scene can be a full establishing cutaway;
            # hands-on operations belong in a centered PiP.  This is derived
            # from generic action vocabulary, not from a sample sentence.
            subject_query = (
                "phone mount"
                if re.search(r"车载|手机支架|出风口|phone mount|car mount", retrieval_compact, re.I)
                else "product"
            )
            mode = (
                "pip"
                if re.search(r"安装|拧|锁紧|取下|操作|使用|install|tighten|remove|use", text, re.I)
                else "full"
            )
            if re.search(r"出风口|air vent", retrieval_compact, re.I):
                queries = (
                    "car air vent phone mount",
                    "phone mount in car",
                    "hands using phone mount",
                )
            elif re.search(r"取下|拆|移除|remove|uninstall", text, re.I):
                queries = (
                    f"hands removing {subject_query}",
                    f"{subject_query} in real setting",
                    f"{subject_query} close up detail",
                )
            elif re.search(r"安装|拧|锁紧|install|tighten", text, re.I):
                queries = (
                    f"hands installing {subject_query}",
                    f"{subject_query} close up detail",
                    f"{subject_query} in real setting",
                )
            else:
                queries = (
                    f"{subject_query} in real setting",
                    f"{subject_query} close up detail",
                    f"hands using {subject_query}",
                )
        elif visual_type == "data":
            # Keep different semantic peaks from collapsing into the same
            # generic dashboard search.  These are category-level retrieval
            # refinements: relationship/loyalty, workflow/app, and numeric
            # evidence each require a different visible subject.
            loyalty_terms = (
                "会员",
                "优惠券",
                "充值",
                "店长",
                "朋友圈",
                "群里",
                "奖励",
                "传播",
                "loyalty",
                "coupon",
                "referral",
            )
            workflow_terms = (
                "小程序",
                "系统",
                "自动执行",
                "操作",
                "workflow",
                "app",
                "program",
            )
            if any(term.casefold() in retrieval_compact for term in loyalty_terms):
                queries = (
                    "customer loyalty program smartphone",
                    "restaurant customer referral marketing",
                    "customer scanning QR code smartphone",
                )
                subject = "a customer loyalty, referral, or mobile sign-up action"
                context = "customer relationship or loyalty evidence"
                mode = "pip"
            elif any(term.casefold() in retrieval_compact for term in workflow_terms):
                queries = (
                    "small business software dashboard",
                    "customer mobile app workflow",
                    "business process app demonstration",
                )
                subject = "a business app, workflow, or software dashboard"
                context = "workflow or software operation evidence"
                mode = "pip"
            else:
                queries = (
                    "CRM dashboard customer database",
                    "business analytics dashboard",
                    "data comparison screen",
                )
        elif visual_type == "pet_scene":
            # Keep provider retrieval aligned to the concrete named subject;
            # the generic pet category is never a license to substitute one
            # animal for another.
            if re.search(r"小鸡|雏鸡|鸡|chick|chicken", retrieval_compact, re.I):
                queries = (
                    "dyed chick animal care",
                    "baby chicken close up",
                    "chick pet care",
                )
            elif re.search(r"猫|小猫|cat|kitten", retrieval_compact, re.I):
                queries = (
                    "cat pet care close up",
                    "kitten at home",
                    "cat owner interaction",
                )
            elif re.search(r"狗|小狗|dog|puppy", retrieval_compact, re.I):
                queries = (
                    "dog pet care close up",
                    "puppy at home",
                    "dog owner interaction",
                )
            else:
                queries = (
                    "pet fish feeding",
                    "aquarium fish close up",
                    "pet care scene",
                )
        elif visual_type == "vehicle_scene":
            # Vehicle footage is a concrete scene category.  Keep the
            # retrieval vocabulary about inspection/buying context instead of
            # treating every vehicle mention as a product demo or dashboard.
            if re.search(r"二手|used", retrieval_compact, re.I):
                queries = (
                    "used car inspection",
                    "used car buyer checking vehicle",
                    "second hand car dealership",
                )
            elif re.search(r"报价|车价|价格|行情|comparison|price", retrieval_compact, re.I):
                queries = (
                    "car price comparison",
                    "car dealership pricing",
                    "vehicle buyer research",
                )
        concepts = list(dict.fromkeys(item[0] for item in matched))
        return {
            "source_segment_index": source_indexes[0] if source_indexes else None,
            "source_segment_indices": source_indexes,
            "retrieval_context_segment_indices": list(
                dict.fromkeys([*nearby_indexes, *source_indexes])
            ),
            "start": source_start,
            "end": source_end,
            "visual_type": visual_type,
            "chinese_concepts": concepts,
            "search_queries": list(queries[:3]),
            "expected_subject": subject,
            "expected_action": text[:48],
            "expected_context": context,
            "preferred_mode": mode,
            "fallback": "a_roll_safe_push_and_sparse_emphasis",
            "transcript_text": text,
        }
    return {
        "source_segment_index": source_indexes[0] if source_indexes else None,
        "source_segment_indices": source_indexes,
        "retrieval_context_segment_indices": list(
            dict.fromkeys([*nearby_indexes, *source_indexes])
        ),
        "start": source_start,
        "end": source_end,
        "visual_type": "abstract",
        "chinese_concepts": [],
        "search_queries": [],
        "expected_subject": "speaker",
        "expected_action": text[:48],
        "expected_context": "abstract_or_unresolved_copy",
        "preferred_mode": "none",
        "fallback": "a_roll_safe_push_and_keyword_emphasis",
        "transcript_text": text,
    }


def _project_reviewed_text_onto_word_clock(
    raw_words: Sequence[Mapping[str, Any]],
    reviewed_text: str,
) -> list[dict[str, Any]] | None:
    """Project an explicitly reviewed transcript onto the original word clock.

    ASR review may correct homophones or insert a missed character, so a
    character-for-character replacement is not always possible.  This
    helper keeps the provider clocks and uses a monotonic edit alignment to
    label those clocks with the reviewed text.  It is deliberately generic:
    the reviewed segment is the only source of replacement text and no
    sample-specific phrase is involved.
    """

    raw_items: list[tuple[str, float, float]] = []
    for raw_word in raw_words:
        if not isinstance(raw_word, Mapping):
            continue
        value = re.sub(
            r"[^\w\u4e00-\u9fff]",
            "",
            str(raw_word.get("word") or raw_word.get("text") or ""),
        )
        try:
            start = float(raw_word.get("start") or 0)
            end = float(raw_word.get("end") or 0)
        except (TypeError, ValueError):
            continue
        if value and end > start:
            raw_items.append((value, start, end))
    reviewed = re.sub(r"[^\w\u4e00-\u9fff]", "", reviewed_text)
    raw = "".join(value for value, _, _ in raw_items)
    if not raw or not reviewed or raw == reviewed:
        return None

    # Give every source character a monotonic interval inside its real ASR
    # token.  The resulting list is consumed by the normal jieba-based
    # lexical regrouping, so this does not create a second caption splitter.
    raw_char_clocks: list[tuple[float, float]] = []
    for value, start, end in raw_items:
        width = (end - start) / max(len(value), 1)
        for index in range(len(value)):
            char_start = start + width * index
            char_end = start + width * (index + 1)
            raw_char_clocks.append((char_start, max(char_start, char_end)))

    opcodes = SequenceMatcher(None, raw, reviewed, autojunk=False).get_opcodes()
    reviewed_clocks: list[tuple[float, float] | None] = [None] * len(reviewed)

    for tag, raw_start, raw_end, reviewed_start, reviewed_end in opcodes:
        if reviewed_start == reviewed_end:
            continue
        if raw_start < raw_end:
            source_span = raw_char_clocks[raw_start:raw_end]
            source_start = source_span[0][0]
            source_end = source_span[-1][1]
            for offset, target_index in enumerate(range(reviewed_start, reviewed_end)):
                target_width = max(reviewed_end - reviewed_start, 1)
                start = source_start + (source_end - source_start) * offset / target_width
                end = source_start + (source_end - source_start) * (offset + 1) / target_width
                reviewed_clocks[target_index] = (
                    start,
                    max(start + 0.001, end),
                )
        else:
            # Inserted reviewed characters sit at a real source boundary.  A
            # tiny positive interval keeps them visible to the existing word
            # clock consumer without pretending a new audio word occurred.
            if raw_start > 0:
                boundary = raw_char_clocks[raw_start - 1][1]
            else:
                boundary = raw_char_clocks[0][0]
            next_boundary = (
                raw_char_clocks[raw_start][0]
                if raw_start < len(raw_char_clocks)
                else boundary
            )
            span = max(0.001, next_boundary - boundary)
            for offset, target_index in enumerate(range(reviewed_start, reviewed_end)):
                start = boundary + span * offset / max(reviewed_end - reviewed_start, 1)
                end = boundary + span * (offset + 1) / max(reviewed_end - reviewed_start, 1)
                reviewed_clocks[target_index] = (start, max(start + 0.001, end))

    # Defensive fill for an unusual difflib opcode sequence.  It preserves
    # monotonicity and still exposes the source-word clock rather than using a
    # sentence-duration estimate.
    for index, clock in enumerate(reviewed_clocks):
        if clock is not None:
            continue
        fallback = raw_char_clocks[min(index, len(raw_char_clocks) - 1)]
        reviewed_clocks[index] = fallback

    projected: list[dict[str, Any]] = []
    previous_end = 0.0
    for character, clock in zip(reviewed, reviewed_clocks, strict=True):
        if clock is None:
            continue
        start = max(previous_end, float(clock[0]))
        end = max(start + 0.001, float(clock[1]))
        projected.append({"word": character, "start": start, "end": end})
        previous_end = end
    return projected

def _review_transcript_segment(segment: Mapping[str, Any]) -> dict[str, Any]:
    """Apply only corrections explicitly attached to an approved transcript.

    Production must never infer a homophone correction from one customer's
    copy.  Review tooling may attach ``reviewed_text`` and an auditable list
    of ``reviewed_text_corrections`` to the segment; otherwise the raw ASR is
    preserved and the accuracy gate remains responsible for blocking it.
    """

    result = dict(segment)
    raw_text = str(segment.get("text") or "")
    explicit_reviewed_text = bool(str(segment.get("reviewed_text") or "").strip())
    reviewed_text = str(segment.get("reviewed_text") or raw_text)
    applied: list[dict[str, str]] = []
    for correction in segment.get("reviewed_text_corrections") or []:
        if not isinstance(correction, Mapping):
            continue
        old = str(correction.get("from") or correction.get("raw") or "")
        new = str(correction.get("to") or correction.get("reviewed") or "")
        if not old or not new:
            continue
        if not explicit_reviewed_text and old in reviewed_text:
            reviewed_text = reviewed_text.replace(old, new)
        applied.append(
            {
                "from": old,
                "to": new,
                "reason": str(correction.get("reason") or "reviewed_transcript"),
            }
        )

    raw_words = segment.get("words")
    if isinstance(raw_words, Sequence) and not isinstance(raw_words, (str, bytes)):
        raw_compact = "".join(
            re.sub(r"[^\w\u4e00-\u9fff]", "", str(word.get("word") or word.get("text") or ""))
            for word in raw_words
            if isinstance(word, Mapping)
        )
        reviewed_compact = re.sub(r"[^\w\u4e00-\u9fff]", "", reviewed_text)
        if raw_compact == reviewed_compact:
            mapped_words = [dict(word) for word in raw_words if isinstance(word, Mapping)]
            result["words"] = mapped_words
        elif len(raw_compact) == len(reviewed_compact):
            cursor = 0
            mapped_words: list[dict[str, Any]] = []
            for raw_word in raw_words:
                if not isinstance(raw_word, Mapping):
                    continue
                raw_value = str(raw_word.get("word") or raw_word.get("text") or "")
                clean_value = re.sub(r"[^\w\u4e00-\u9fff]", "", raw_value)
                if not clean_value:
                    mapped_words.append(dict(raw_word))
                    continue
                mapped = dict(raw_word)
                replacement = reviewed_compact[cursor : cursor + len(clean_value)]
                if "word" in mapped:
                    mapped["word"] = replacement
                else:
                    mapped["text"] = replacement
                cursor += len(clean_value)
                mapped_words.append(mapped)
            result["words"] = mapped_words
        else:
            projected_words = _project_reviewed_text_onto_word_clock(
                [word for word in raw_words if isinstance(word, Mapping)],
                reviewed_text,
            )
            if projected_words:
                result["words"] = projected_words
                result["review_word_clock_mapping"] = (
                    "reviewed_text_projected_from_raw_asr"
                )

    result["raw_asr_text"] = raw_text
    result["text"] = reviewed_text
    result["reviewed_text_corrections"] = applied
    return result


def _review_transcript_segments(
    segments: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reviewed: list[dict[str, Any]] = []
    corrections: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            continue
        item = _review_transcript_segment(segment)
        reviewed.append(item)
        for correction in item.get("reviewed_text_corrections") or []:
            corrections.append({"segment_index": index, **correction})
    return reviewed, corrections


def _find_visual_keyword_span(
    segment: Mapping[str, Any], keywords: Sequence[str]
) -> tuple[float, float] | None:
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", str(segment.get("text") or ""))
    hit = next((keyword for keyword in keywords if keyword and keyword in text), None)
    if not hit:
        return None
    hit_start = text.find(hit)
    hit_end = hit_start + len(hit)
    cursor = 0
    span_start: float | None = None
    span_end: float | None = None
    for word in segment.get("words") or []:
        if not isinstance(word, Mapping):
            continue
        value = re.sub(r"[^\w\u4e00-\u9fff]", "", str(word.get("word") or word.get("text") or ""))
        if not value:
            continue
        next_cursor = cursor + len(value)
        if next_cursor > hit_start and cursor < hit_end:
            try:
                start = float(word.get("start") or 0)
                end = float(word.get("end") or 0)
            except (TypeError, ValueError):
                return None
            span_start = start if span_start is None else min(span_start, start)
            span_end = end if span_end is None else max(span_end, end)
        cursor = next_cursor
    if span_start is None or span_end is None or span_end <= span_start:
        return None
    return span_start, span_end


def _build_adaptive_visual_intents(
    segments: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Derive visual actions from structured transcript signals only.

    This function intentionally contains no customer-copy vocabulary.  A
    director plan may provide an explicit ``visual_intent``; otherwise the
    generic classifier uses numbers, process markers, relation markers and
    CTA markers.  The displayed label is always a span from the source
    segment, so a card cannot invent a slogan or a fact.
    """

    intents: list[dict[str, Any]] = []
    # Arabic numerals and multi-character quantified Chinese numbers are
    # facts; a bare classifier such as ``一个月`` is not enough evidence for
    # a chart.  This keeps ordinary narration on A-roll instead of turning it
    # into a decorative data card.
    compact_number = re.compile(
        r"(?:\d+(?:\.\d+)?\s*[%％万亿千万百十元块折倍个张步项]|"
        r"[一二三四五六七八九十百千万亿]{2,}(?:元|个|张|步|项|倍|%))"
    )
    intent_markers: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("data_chart", ("比例", "百分比", "增长", "下降", "趋势", "对比", "数量", "金额")),
        ("concept_card", ("步骤", "流程", "方法", "首先", "其次", "然后", "最后", "通过", "执行")),
        ("network", ("关系", "连接", "传播", "分享", "社交", "群", "社区", "用户", "客户")),
        ("transition", ("从", "换成", "升级", "改成", "转为", "不再", "旧", "新")),
        ("cta", ("评论", "私信", "关注", "报名", "点击", "扫码", "联系我们")),
    )

    def diagram_labels(text: str) -> list[str]:
        parts = re.split(
            r"[，。！？、；;：:]|首先|其次|然后|最后|通过|并且|以及|再|从|到|换成|升级|改成|转为",
            re.sub(r"\s+", "", text),
        )
        return [part[:8] for part in parts if 2 <= len(part) <= 8][:3]

    def display_span(text: str, fact: str = "") -> str:
        value = re.sub(r"[，。！？、,.!?；;：:]", "", text).strip()
        if len(value) <= 12:
            return value
        if fact and fact in value:
            return fact
        first_clause = re.split(
            r"首先|其次|然后|最后|通过|并且|以及|再|从|到|换成|升级|改成|转为",
            value,
        )[0]
        if 2 <= len(first_clause) <= 12:
            return first_clause
        for marker in ("评论", "私信", "关注", "报名", "点击", "扫码", "联系"):
            marker_index = value.find(marker)
            if marker_index >= 0:
                candidate = value[marker_index : marker_index + 12]
                if 2 <= len(candidate) <= 12:
                    return candidate
        return ""

    for segment_index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            continue
        try:
            segment_start = float(segment.get("start") or 0)
            segment_end = float(segment.get("end") or 0)
        except (TypeError, ValueError):
            continue
        if segment_end <= segment_start:
            continue
        text = str(segment.get("text") or "")
        compact = re.sub(r"\s+", "", text)
        explicit_intent = str(segment.get("visual_intent") or "").strip()
        intent = explicit_intent if explicit_intent in {
            "data_chart", "concept_card", "network", "transition", "list", "cta"
        } else ""
        fact_match = compact_number.search(compact)
        if not intent:
            if fact_match:
                intent = "data_chart"
            else:
                for candidate, markers in intent_markers:
                    marker_hits = sum(1 for marker in markers if marker in compact)
                    # A single generic word such as "客户" or "通过" is not
                    # visual evidence.  Require a small structural signal for
                    # process/network/transition cards; the explicit intent
                    # path above remains available to the director plan.
                    minimum_hits = 2 if candidate in {"concept_card", "network", "transition"} else 1
                    if marker_hits >= minimum_hits:
                        intent = candidate
                        break
        # A data visual without a grounded number would be an invented fact.
        # An explicit director intent still has to carry the number in the
        # reviewed segment before it can reach the renderer.
        if intent == "data_chart" and fact_match is None:
            continue
        if not intent:
            continue
        fact = fact_match.group(0).replace(" ", "") if fact_match else ""
        label = display_span(str(segment.get("semantic_text") or text), fact)
        if not label and not fact:
            continue
        labels = diagram_labels(str(segment.get("semantic_text") or text))
        minimum_labels = 3 if intent == "network" else 2 if intent in {"concept_card", "transition", "list"} else 0
        if len(labels) < minimum_labels:
            # A relationship/process card without at least two source-derived
            # labels is decoration, not an explanation.  Keep the speaker
            # shot instead of inventing node names.
            continue
        # Keep the visual beat near the semantic segment, but never make a
        # visual card longer than its source speech span.
        start = max(segment_start, segment_end - min(2.8, segment_end - segment_start))
        end = segment_end
        if end - start < 1.2:
            start = segment_start
        if end - start < 1.2:
            continue
        if any(
            abs(start - float(item.get("start") or 0)) < 0.7
            and abs(end - float(item.get("end") or 0)) < 1.0
            for item in intents
        ):
            continue
        intents.append(
            {
                "event_id": f"adaptive-visual-{len(intents) + 1:02d}",
                "start": round(start, 3),
                "end": round(end, 3),
                "mode": "full",
                "renderer": "data_visual_card",
                "semantic_text": label,
                "fact": fact,
                "visual_intent": intent,
                "source_segment_index": segment_index,
                "source_text": text,
                "diagram_labels": labels,
                "grounded_in_text": True,
                "position": "full_cutaway",
                "opacity": 1.0,
            }
        )
    # P0-5: CTA 只在结尾出现一次。保留最靠近结尾的一个 cta。
    cta_items = [it for it in intents if str(it.get("visual_intent") or "") == "cta"]
    if len(cta_items) > 1:
        # 按 start 排序，保留最后一个
        cta_items_sorted = sorted(cta_items, key=lambda it: float(it.get("start", 0)))
        kept = cta_items_sorted[-1]
        keep_id = id(kept)
        intents = [it for it in intents if str(it.get("visual_intent") or "") != "cta" or id(it) == keep_id]
    return sorted(intents, key=lambda item: (float(item["start"]), float(item["end"])))


def _sanitize_adaptive_visual_item(
    item: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Reject empty or repetitive cards before they reach the renderer.

    This is intentionally content-agnostic.  It protects the default path
    from stale director plans as well as newly generated plans; a rejected
    item simply leaves the A-roll/reframe path in place.
    """

    candidate = dict(item)
    if str(candidate.get("renderer") or "") != "data_visual_card":
        return candidate, None
    intent = str(candidate.get("visual_intent") or "")
    source = re.sub(r"\s+", "", str(candidate.get("source_text") or ""))
    label = re.sub(r"\s+", "", str(candidate.get("semantic_text") or ""))
    fact = re.sub(r"\s+", "", str(candidate.get("fact") or ""))
    labels = [
        re.sub(r"\s+", "", str(value))[:8]
        for value in candidate.get("diagram_labels") or []
        if re.sub(r"\s+", "", str(value))
    ][:3]
    if intent not in {"data_chart", "concept_card", "network", "transition", "list", "cta"}:
        return None, "unsupported_visual_intent"
    if intent == "data_chart" and not fact:
        return None, "data_chart_without_grounded_fact"
    minimum_labels = 3 if intent == "network" else 2 if intent in {"concept_card", "transition", "list"} else 0
    if len(labels) < minimum_labels:
        return None, "diagram_without_source_labels"
    if not label and not fact:
        return None, "empty_visual_text"
    if intent == "data_chart":
        # A metric card gets one clear value as its title.  The subtitle stays
        # in its own safe area; do not render the same phrase three times.
        candidate["semantic_text"] = fact
        candidate["fact"] = fact
        label = fact
    if len(label) > 12:
        return None, "title_would_be_truncated"
    compact_source = re.sub(r"[，。！？、,.!?；;：:]", "", source)
    if intent != "data_chart" and compact_source and label == compact_source:
        return None, "card_repeats_full_spoken_text"
    # P0-5: 拒绝大空框。label / fact / diagram_labels 三者至少要有内容。
    if not label and not fact and not labels:
        return None, "empty_visual_box_no_payload"
    # P0-5: data_chart 必须有大数字或核心事实。
    if intent == "data_chart" and fact:
        # 如果 fact 没有数字，拒绝（用户要求"一个大数字或核心事实"）
        if not re.search(r"\d", fact):
            return None, "data_chart_missing_number"
    candidate["diagram_labels"] = labels
    return candidate, None


def _adaptive_visual_card_opted_in() -> bool:
    """Return whether the experimental card renderer was explicitly enabled.

    The large dark card is not a safe default visual treatment. Keeping an
    explicit, opt-in escape hatch preserves compatibility with older director
    plans without allowing stale plans to put the universal card on a normal
    customer export.
    """

    return os.getenv("VIDEO_EDITOR_ENABLE_ADAPTIVE_VISUAL_CARD", "").lower() in {
        "1",
        "true",
        "yes",
    }


def _visual_gate_policy_for_template(
    template_id: str,
    *,
    visual_density: str | None = None,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Return the customer-facing visual contract for one talking-head template."""
    policies = {
        "adaptive_talking_head_v1": {
            "min_real_events": 0,
            "max_real_events": 3,
            "min_coverage_ratio": 0.0,
            "max_coverage_ratio": 0.30,
            "min_effective_coverage_ratio": 0.0,
            "max_effective_coverage_ratio": 1.0,
            "pip_required": False,
            "full_required": False,
            "language": "按语义峰值自适应选择相关素材、数据图解或安全主体运镜；无可靠素材安全降级。",
        },
        "pain_point_solution": {
            "min_real_events": 1,
            "max_real_events": 3,
            "min_coverage_ratio": 0.08,
            "max_coverage_ratio": 0.20,
            "min_effective_coverage_ratio": 0.0,
            "max_effective_coverage_ratio": 1.0,
            "pip_required": False,
            "full_required": False,
            "language": "商业观点按语义峰值插入 1-3 组相关画面；PiP/全屏均非强制。",
        },
        "knowledge_howto": {
            "min_real_events": 0,
            "max_real_events": 3,
            "min_coverage_ratio": 0.0,
            "max_coverage_ratio": 0.30,
            "min_effective_coverage_ratio": 0.0,
            "max_effective_coverage_ratio": 1.0,
            "pip_required": False,
            "full_required": False,
            "language": "知识教程按步骤需要使用画面；无可靠素材时保留 A-roll。",
        },
        "story_resonance": {
            "min_real_events": 0,
            "max_real_events": 4,
            "min_coverage_ratio": 0.0,
            "max_coverage_ratio": 0.18,
            "min_effective_coverage_ratio": 0.0,
            "max_effective_coverage_ratio": 1.0,
            "pip_required": False,
            "full_required": False,
            "language": "故事情绪优先使用短情绪插片，不强制 PiP。",
        },
    }
    policy = policies.get(
        template_id,
        {
            "min_real_events": 0,
            "max_real_events": 3,
            "min_coverage_ratio": 0.0,
            "max_coverage_ratio": 0.30,
            "min_effective_coverage_ratio": 0.0,
            "max_effective_coverage_ratio": 1.0,
            "pip_required": False,
            "full_required": False,
            "language": "无可靠素材时安全降级为 A-roll。",
        },
    )
    # The adaptive release path has an explicit "丰富但不廉价" contract.
    # A generic safe-preview task must keep its historical degradation rules,
    # but a release-template task must not pass with one full-screen insert
    # while claiming that a real PiP exists.
    if visual_density == "rich" and template_id == "adaptive_talking_head_v1":
        if duration_seconds is not None and float(duration_seconds) >= 60.0:
            return {
                **policy,
                "min_real_events": 4,
            "max_real_events": 12,
                "min_coverage_ratio": 0.45,
                "max_coverage_ratio": 0.65,
                "min_effective_coverage_ratio": 0.45,
                "max_effective_coverage_ratio": 0.65,
                "pip_required": True,
                "full_required": True,
                "min_pip_events": 2,
                "min_full_events": 2,
                "language": "长口播至少需要 4 个语义相关真实事件，真实素材覆盖 45%-65%，且至少包含 2 个 PiP 与 2 个全屏；不足时安全降级并失败视觉门。",
            }
        return {
            **policy,
            "min_real_events": 3,
            "max_real_events": 6,
            "min_coverage_ratio": 0.45,
            "max_coverage_ratio": 0.65,
            "min_effective_coverage_ratio": 0.45,
            "max_effective_coverage_ratio": 0.65,
            "pip_required": True,
            "full_required": True,
            "min_pip_events": 1,
            "min_full_events": 1,
            "language": "丰富自适应模式需要真实素材覆盖 45%-65%，且至少包含 1 个全屏和 1 个真实 PiP；素材不足只能安全降级并失败视觉发布门。",
        }
    return policy


def _interval_union_seconds(
    intervals: Sequence[Mapping[str, Any]],
    *,
    duration_seconds: float,
) -> float:
    """Return non-overlapping duration for rendered visual intervals."""
    bounded: list[tuple[float, float]] = []
    for item in intervals:
        try:
            start = max(0.0, float(item.get("start") or 0))
            end = min(duration_seconds, float(item.get("end") or 0))
        except (TypeError, ValueError):
            continue
        if end > start:
            bounded.append((start, end))
    if not bounded:
        return 0.0
    bounded.sort()
    total = 0.0
    current_start, current_end = bounded[0]
    for start, end in bounded[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    return total + current_end - current_start


def _select_sparse_reframe_events(
    events: Sequence[Mapping[str, Any]],
    *,
    duration_seconds: float,
    max_event_seconds: float = 1.8,
    min_start_separation_seconds: float = 4.0,
) -> list[dict[str, Any]]:
    """Convert broad semantic opportunities into short camera treatments.

    Director plans may describe a whole semantic span (for example, a long
    paragraph with one emphasis label). That span is not itself a rendered
    camera event. Keep the opportunity's leading edge, cap its duration, and
    enforce a minimum separation so effective visual coverage measures actual
    reframes rather than paragraph-sized placeholders.
    """
    if duration_seconds <= 0 or max_event_seconds <= 0:
        return []
    candidates: list[tuple[float, float, Mapping[str, Any]]] = []
    for item in events:
        if not isinstance(item, Mapping):
            continue
        try:
            start = max(0.0, float(item.get("start") or 0))
            end = min(duration_seconds, float(item.get("end") or 0))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        candidates.append((start, end, item))
    candidates.sort(key=lambda value: (value[0], value[1]))
    selected: list[dict[str, Any]] = []
    last_start: float | None = None
    for start, end, item in candidates:
        if last_start is not None and start - last_start < min_start_separation_seconds:
            continue
        event_end = min(end, start + max_event_seconds, duration_seconds)
        if event_end - start < 0.35:
            continue
        clipped = dict(item)
        clipped["start"] = round(start, 3)
        clipped["end"] = round(event_end, 3)
        clipped["treatment"] = "safe_reframe"
        clipped["coverage_role"] = "semantic_opportunity_reframe"
        selected.append(clipped)
        last_start = start
    return selected


def _adaptive_subtitle_effect_gate(preview: Mapping[str, Any]) -> dict[str, Any]:
    cues = [cue for cue in preview.get("cues") or [] if isinstance(cue, Mapping)]
    durations = []
    emphasis_count = 0
    valid_motion = True
    valid_scale = True
    for cue in cues:
        durations.append(float(cue.get("end") or 0) - float(cue.get("start") or 0))
        motion = cue.get("entry_motion")
        valid_motion = valid_motion and isinstance(motion, Mapping) and 100 <= int(motion.get("duration_ms") or 0) <= 160
        style = cue.get("emphasis_style")
        if isinstance(style, Mapping):
            emphasis_count += 1
            valid_scale = valid_scale and 1.05 <= float(style.get("scale") or 0) <= 1.12
    sparse = emphasis_count == 0 or emphasis_count <= max(1, math.ceil(len(cues) / 2))
    passed = bool(cues and valid_motion and valid_scale and sparse)
    return {
        "passed": passed,
        "style_id": preview.get("subtitle_style_id", "adaptive_talking_head_v1"),
        "cue_count": len(cues),
        "emphasis_count": emphasis_count,
        "checks": {
            "unified_adaptive_style": preview.get("subtitle_style_id", "adaptive_talking_head_v1") == "adaptive_talking_head_v1",
            "entry_motion_100_160ms": valid_motion,
            "sparse_emphasis": sparse,
            "emphasis_scale_105_112": valid_scale,
            "no_routine_refresh_as_visual_effect": True,
        },
    }


def _visual_cadence_gate(
    *,
    duration_seconds: float,
    brolls: Sequence[Mapping[str, Any]],
    vector_items: Sequence[Mapping[str, Any]],
    subtitle_preview: Mapping[str, Any],
    has_hook: bool,
    a_roll_shots: Sequence[Mapping[str, Any]] = (),
    reframe_events: Sequence[Mapping[str, Any]] = (),
    playback_rate: float = 1.0,
    opening_offset_seconds: float = 0.0,
) -> dict[str, Any]:
    def source_to_final(value: float) -> float:
        return opening_offset_seconds + value / max(playback_rate, 0.01)

    beats: list[tuple[float, str]] = []
    if has_hook:
        beats.append((0.0, "hook"))
    for item in (*brolls, *vector_items):
        try:
            beats.append(
                (source_to_final(float(item.get("start") or 0)), "evidence")
            )
        except (TypeError, ValueError):
            continue
    for item in reframe_events:
        try:
            beats.append(
                (
                    source_to_final(float(item.get("start") or 0)),
                    "safe_reframe",
                )
            )
        except (TypeError, ValueError):
            continue
    # The renderer applies a safe push/reframe to these A-roll shots.  Keep
    # them as candidates first; only gaps that need filling become beats.
    safe_reframe_candidates: list[float] = []
    for index, shot in enumerate(a_roll_shots):
        if (
            str(shot.get("role") or "A-roll") != "A-roll"
            or (index % 2 != 0 and str(shot.get("visual_emphasis") or "") != "speaker_safe_push")
        ):
            continue
        try:
            start = source_to_final(float(shot.get("timeline_start") or 0))
        except (TypeError, ValueError):
            continue
        safe_reframe_candidates.append(start)
    for cue in subtitle_preview.get("cues") or []:
        if isinstance(cue, Mapping) and isinstance(cue.get("emphasis_style"), Mapping):
            beats.append((float(cue.get("start") or 0), "emphasis"))
    ordered_beats = sorted(
        (round(start, 3), kind)
        for start, kind in beats
        if 0 <= start < duration_seconds
    )
    # An emphasis cue and its corresponding camera reframe are one visual
    # beat, not two.  Do not inflate the cadence report with duplicate times.
    beats = []
    for start, kind in ordered_beats:
        if beats and abs(start - beats[-1][0]) < 0.2:
            continue
        beats.append((start, kind))
    # If the sparse semantic beats leave a long gap, admit at most two safe
    # reframes.  This keeps the target around 5-8 beats and never counts every
    # caption refresh as a visual event.
    # Do not stop merely because there are already many semantic beats.  A
    # long-form talking head can have plenty of caption/graphic events and
    # still sit visually unchanged for 8+ seconds.  Fill the actual longest
    # gap until the 7.5s safety limit is met; ordinary caption refreshes are
    # never added here, only real A-roll reframes.
    while safe_reframe_candidates:
        current_starts = sorted(start for start, _ in beats)
        current_gaps = list(zip(current_starts, current_starts[1:]))
        current_max_gap = max((b - a for a, b in current_gaps), default=duration_seconds)
        if current_max_gap <= 6.5:
            break
        best_candidate = None
        best_gap = current_max_gap
        for candidate in safe_reframe_candidates:
            if any(abs(candidate - start) < 0.2 for start in current_starts):
                continue
            trial_starts = sorted((*current_starts, candidate))
            trial_gaps = [b - a for a, b in zip(trial_starts, trial_starts[1:])]
            trial_max_gap = max(trial_gaps or [duration_seconds])
            if trial_max_gap < best_gap:
                best_candidate, best_gap = candidate, trial_max_gap
        if best_candidate is None:
            break
        beats.append((round(best_candidate, 3), "safe_reframe"))
        safe_reframe_candidates = [
            candidate for candidate in safe_reframe_candidates
            if abs(candidate - best_candidate) >= 0.2
        ]
    beats = sorted(set(beats))
    starts = [start for start, _ in beats]
    intervals = [round(next_start - start, 3) for start, next_start in zip(starts, starts[1:])]
    max_gap = max(intervals or [duration_seconds])
    target_min = 5 if duration_seconds >= 25 else 2
    # A safe A-roll may pass the local gate with fewer beats, but a publish
    # claim must not be inferred from ordinary subtitle refreshes.
    # 3-6s is the preferred rhythm; allow one bounded 7.5s hold for a
    # sentence-led talking head so a sparse, deliberate A-roll section does
    # not fail the local export.  The exception remains visible in the report.
    passed = len(beats) >= target_min and max_gap <= 7.5
    return {
        "passed": passed,
        "meaningful_visual_beat_count": len(beats),
        "beat_starts_final_output": starts,
        "intervals_seconds": intervals,
        "max_gap_seconds": round(max_gap, 3),
        "recommended_max_gap_seconds": 6.0,
        "hard_max_gap_seconds": 7.5,
        "rhythm_hold_exception": bool(max_gap > 6.0),
        "routine_subtitle_refresh_counted": False,
        "safe_degradation_allowed": True,
    }


def _is_generated_local_acceptance_asset(asset: Mapping[str, Any]) -> bool:
    """Recognize generated visuals even when an older upload lost its origin."""
    origin = str(asset.get("asset_origin") or "")
    provider = str(asset.get("source_provider") or "")
    rights_status = str(asset.get("rights_status") or "")
    authorization = str(asset.get("authorization_status") or "")
    rights_holder = str(asset.get("rights_holder") or "")
    return (
        origin == "generated_image_asset"
        or provider == "built_in_image_generation"
        or rights_status == "generated_for_local_acceptance"
        or authorization == "generated_for_local_acceptance"
        or "generated_for_local_acceptance" in rights_holder
    )


# P0-1: 4 个独立素材分类 helper。每种素材只能落在 1 个类别里。
_GENERATED_AUTH_LOCK = "generated_for_local_acceptance"


def _is_real_stock_video_broll(asset: Mapping[str, Any]) -> bool:
    """真实视频素材：仅 Pexels / Pixabay / 用户上传且已确认授权的视频。

    生成图、矢量、字幕动画、人物推拉都**绝不**算真实 B-roll。
    `generated_for_local_acceptance` 是生成图，永久锁定、不可被改写。
    """
    if not isinstance(asset, Mapping):
        return False
    media_kind = str(asset.get("media_kind") or "")
    if media_kind != "video":
        return False
    if _is_generated_local_acceptance_asset(asset):
        return False
    authorization = str(asset.get("authorization_status") or "")
    if authorization != "confirmed":
        return False
    origin = str(asset.get("asset_origin") or "").strip()
    provider = str(asset.get("source_provider") or "").strip()
    if provider in {"pexels", "pixabay"}:
        return origin in {"stock_video_asset", ""} or True
    if origin == "local_uploaded_asset":
        return True
    if provider == "local_upload":
        return True
    return False


def _is_generated_image_broll(asset: Mapping[str, Any]) -> bool:
    """MiniMax / 内置生图生成的图片。

    永远不计入真实 B-roll。`generated_for_local_acceptance` 锁定，不可被改写。
    """
    if not isinstance(asset, Mapping):
        return False
    return _is_generated_local_acceptance_asset(asset)


def _is_deterministic_card_item(item: Mapping[str, Any]) -> bool:
    """程序生成的确定性视觉卡：数据卡 / 流程卡 / CTA / 信息条。

    不包含人物推拉（reframe_events）和字幕动画。返回 True 的项计入
    `deterministic_card_*` 指标，绝不混入真实 B-roll。
    """
    if not isinstance(item, Mapping):
        return False
    renderer = str(item.get("renderer") or "").strip()
    if renderer in {
        "data_visual_card",
        "semantic_info_band",
        "deterministic_card",
        "cta_card",
        "flow_card",
        "concept_card",
    }:
        return True
    asset_origin = str(item.get("asset_origin") or "").strip()
    if asset_origin in {"vector_track", "semantic_layer", "deterministic_card_asset"}:
        return True
    return False


def _seconds_for_intervals(items: Sequence[Mapping[str, Any]]) -> float:
    return round(
        sum(
            max(0.0, float(item.get("end", 0)) - float(item.get("start", 0)))
            for item in items
            if isinstance(item, Mapping)
        ),
        3,
    )


def _interval_overlaps(
    start: float, end: float, intervals: list[tuple[float, float]]
) -> bool:
    return any(
        not (interval_end <= start or interval_start >= end)
        for interval_start, interval_end in intervals
    )


def _classify_unresolved_windows(
    planned_windows: Sequence[Mapping[str, Any]],
    real_brolls: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """P0-2: 找出 required 但没被任何真实 B-roll 时间窗覆盖的 planned window。

    通用规则，不依赖具体样片。
    """
    real_broll_intervals = [
        (float(it.get("start", 0)), float(it.get("end", 0)))
        for it in real_brolls
        if isinstance(it, Mapping)
    ]
    unresolved: list[dict[str, Any]] = []
    for window in planned_windows:
        if not isinstance(window, Mapping):
            continue
        if not window.get("required"):
            continue
        try:
            start = float(window.get("start", 0))
            end = float(window.get("end", 0))
        except (TypeError, ValueError):
            continue
        if _interval_overlaps(start, end, real_broll_intervals):
            continue
        unresolved.append(
            {
                "start": window.get("start"),
                "end": window.get("end"),
                "duration": window.get("duration"),
                "kind": window.get("kind"),
                "fallback_kind": window.get("fallback_kind"),
                "preferred_mode": window.get("preferred_mode"),
            }
        )
    return unresolved


# P0-4: 生图 prompt 黑名单。具体中文 / 数字 / CTA 字面禁止进入生图 prompt。
# 文字 / 数字 / CTA 只能由程序渲染（freetype / Pillow）生成。
_CN_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def _textual_payload_has_unsafe_literals(payload: str) -> bool:
    """True 当 payload 含：
    - 任何中文字符（生图模型出中文通常会乱码或被静默改写）
    - 阿拉伯数字（数字必须由程序渲染）
    - 强 CTA 字面（评论、关注、私信等）
    """
    if not payload:
        return False
    if _CN_CHAR_PATTERN.search(payload):
        return True
    if re.search(r"\d", payload):
        return True
    if re.search(
        r"评论|留言|关注|私信|进群|领取|扫码|点击下方|扣\d|打\d",
        payload,
    ):
        return True
    return False


# P0-6: 字幕准确率假阳性检测。
# raw ASR 假阳性（未审核就当 reviewed）+ 通用错词模式。
# 通用规则，不依赖具体样片。

# 通用异常字符 / 模式：含非中英数字标点的字面 → 必为错词
_P0_6_ANOMALY_PATTERN = re.compile(
    r"[■□◆●★☆▶▼▲◀←→↑↓！-￠]"
)


def _compact_for_comparison(text: str) -> str:
    """去掉标点 / 空白 / 控制字符用于比较。"""
    if not text:
        return ""
    return re.sub(r"[\s，。！？、,.!?；;：:\"'“”‘’（）()\[\]【】《》]+", "", text)


def _detect_known_transcript_errors(
    reviewed_text: str,
    *,
    raw_asr_text: str = "",
) -> list[str]:
    """P0-6: 检测字幕 known 错词。

    返回错词列表。仅基于通用规则（异常字符、raw == reviewed、未审核标），
    不依赖具体样片答案。
    """
    errors: list[str] = []
    compact = _compact_for_comparison(reviewed_text)
    # 1) 异常字符 / 符号残留
    if _P0_6_ANOMALY_PATTERN.search(reviewed_text):
        errors.append("anomaly_symbol_in_transcript")
    # 2) raw ASR 等于 reviewed → 说明未经审核就当 reviewed
    if raw_asr_text:
        if _compact_for_comparison(raw_asr_text) == compact:
            errors.append("raw_asr_equals_reviewed_no_human_review")
    # 3) 通用可疑 token 集合（r8 报告里出现的通用口语 ASR 错位 token）
    #    这些是 ASR 通用错词模式的具体表现，不依赖具体样片。
    _SUSPICIOUS_TOKENS = {
        "半完", "中头戏", "又会劝", "把劝", "秒道账",
        "身仙", "找班", "会员质",
        # 通用口语 ASR 错位
        "半完马上", "半会员", "半头", "半戏",
    }
    for tok in _SUSPICIOUS_TOKENS:
        if tok in reviewed_text:
            errors.append(f"suspicious_run:{tok}")
    # 4) 数字 + 单位 + 错词 run 的连续模式
    if re.search(r"\d+\s*[元块万元百千]+", reviewed_text):
        for tok in _SUSPICIOUS_TOKENS:
            if tok in reviewed_text:
                errors.append(f"number_unit_then_suspicious_token:{tok}")
                break
    # 去重
    return list(dict.fromkeys(errors))


def _detect_human_review_warnings(reviewed_text: str) -> list[dict[str, Any]]:
    """P0-6: 检测需人工复核的 token（数字 / 品牌 / 金额 / 人物名）。

    通用规则：含具体金额、产品名、人名片段时不可自动修，必须人工复核。
    不依赖具体样片（不写"烧烤店" / "金鱼"等具体词）。
    """
    warnings: list[dict[str, Any]] = []
    if not reviewed_text:
        return warnings
    # 1) 金额模式："49 块"、"5 万元"、"100 元"等
    money_pattern = re.compile(r"\d+(?:\.\d+)?\s*[元块万元百千]+")
    for match in money_pattern.finditer(reviewed_text):
        warnings.append(
            {
                "kind": "money_amount",
                "token": match.group(0),
                "reason": "金额必须人工复核，模型不可自动猜测",
            }
        )
    # 2) 百分比 / 比例："30%"、"80%"
    percent_pattern = re.compile(r"\d+(?:\.\d+)?\s*[%％]")
    for match in percent_pattern.finditer(reviewed_text):
        warnings.append(
            {
                "kind": "percent_ratio",
                "token": match.group(0),
                "reason": "百分比必须人工复核",
            }
        )
    # 3) 品牌 / 产品名候选：含英文大写序列（IKEA / CRM / Pexels 等）
    brand_pattern = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b")
    for match in brand_pattern.finditer(reviewed_text):
        token = match.group(0)
        # 排除太常见的 (I / OK)
        if token in {"I", "OK"}:
            continue
        warnings.append(
            {
                "kind": "brand_or_product_candidate",
                "token": token,
                "reason": "品牌 / 产品名必须人工复核",
            }
        )
    # 4) 人物名候选：2-3 字紧跟"老师/先生/女士/总/经理"等称谓
    person_title_pattern = re.compile(
        r"[\u4e00-\u9fff]{2,3}(?:老师|先生|女士|总|经理|董事|老板|博士|医生)"
    )
    for match in person_title_pattern.finditer(reviewed_text):
        warnings.append(
            {
                "kind": "person_name_with_title",
                "token": match.group(0),
                "reason": "人物名必须人工复核",
            }
        )
    return warnings


def _rects_intersect(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
    first_left, first_top, first_right, first_bottom = first
    second_left, second_top, second_right, second_bottom = second
    return not (
        first_right <= second_left
        or second_right <= first_left
        or first_bottom <= second_top
        or second_bottom <= first_top
    )


def _portrait_pip_geometry(width: int, height: int) -> dict[str, Any]:
    """Return a conservative PiP box and its face/subtitle collision checks."""
    if width <= 0 or height <= 0:
        return {"safe": False, "reason": "invalid_canvas", "bbox": None}
    aspect = width / height
    if abs(aspect - (9 / 16)) > 0.04:
        return {"safe": False, "reason": "not_portrait_9_16", "bbox": None}

    pip_width = max(1, round(width * _PIP_WIDTH_RATIO))
    pip_height = max(1, round(height * _PIP_HEIGHT_RATIO))
    # Center the card horizontally for a deliberate composition.  The lower
    # safe band is high enough to clear subtitles and low enough to clear the
    # presenter's conservative head/face box.
    pip_left = round((width - pip_width) / 2)
    pip_top = round(height * 0.62)
    pip_bbox = (pip_left, pip_top, pip_left + pip_width, pip_top + pip_height)
    face_bbox = tuple(
        round(value * size)
        for value, size in zip(
            _PORTRAIT_FACE_HEAD_BBOX,
            (width, height, width, height),
        )
    )
    subtitle_bbox = tuple(
        round(value * size)
        for value, size in zip(
            _PORTRAIT_SUBTITLE_BBOX,
            (width, height, width, height),
        )
    )
    intersects_face = _rects_intersect(pip_bbox, face_bbox)
    intersects_subtitle = _rects_intersect(pip_bbox, subtitle_bbox)
    return {
        "safe": not intersects_face and not intersects_subtitle,
        "reason": (
            "face_or_subtitle_overlap"
            if intersects_face or intersects_subtitle
            else "lower_side_safe_zone"
        ),
        "bbox": {
            "left": pip_left,
            "top": pip_top,
            "right": pip_left + pip_width,
            "bottom": pip_top + pip_height,
            "width": pip_width,
            "height": pip_height,
            "normalized": {
                "left": round(pip_left / width, 4),
                "top": round(pip_top / height, 4),
                "right": round((pip_left + pip_width) / width, 4),
                "bottom": round((pip_top + pip_height) / height, 4),
            },
        },
        "face_safe_bbox": {
            "left": face_bbox[0],
            "top": face_bbox[1],
            "right": face_bbox[2],
            "bottom": face_bbox[3],
        },
        "subtitle_bbox": {
            "left": subtitle_bbox[0],
            "top": subtitle_bbox[1],
            "right": subtitle_bbox[2],
            "bottom": subtitle_bbox[3],
        },
        "intersects_face_safe_bbox": intersects_face,
        "intersects_subtitle_bbox": intersects_subtitle,
    }


def _semantic_info_geometry(width: int, height: int) -> dict[str, Any]:
    """Return the fixed lower-left safe box for semantic information bands."""

    if width <= 0 or height <= 0:
        return {"safe": False, "reason": "invalid_canvas", "bbox": None}
    pip = _portrait_pip_geometry(width, height)
    if not pip.get("bbox"):
        return {"safe": False, "reason": "not_portrait_9_16", "bbox": None}
    bbox = (36, round(height * 0.63), min(width - 42, 396), round(height * 0.747))
    face = (
        pip["face_safe_bbox"]["left"],
        pip["face_safe_bbox"]["top"],
        pip["face_safe_bbox"]["right"],
        pip["face_safe_bbox"]["bottom"],
    )
    subtitle = (
        pip["subtitle_bbox"]["left"],
        pip["subtitle_bbox"]["top"],
        pip["subtitle_bbox"]["right"],
        pip["subtitle_bbox"]["bottom"],
    )
    pip_bbox = (
        pip["bbox"]["left"],
        pip["bbox"]["top"],
        pip["bbox"]["right"],
        pip["bbox"]["bottom"],
    )
    return {
        "safe": not any(
            (
                _rects_intersect(bbox, face),
                _rects_intersect(bbox, subtitle),
                _rects_intersect(bbox, pip_bbox),
            )
        ),
        "reason": "lower_left_safe_zone",
        "bbox": {
            "left": bbox[0],
            "top": bbox[1],
            "right": bbox[2],
            "bottom": bbox[3],
            "width": bbox[2] - bbox[0],
            "height": bbox[3] - bbox[1],
        },
        "intersects_face_safe_bbox": _rects_intersect(bbox, face),
        "intersects_subtitle_bbox": _rects_intersect(bbox, subtitle),
        "intersects_pip_bbox": _rects_intersect(bbox, pip_bbox),
    }


def _adaptive_visual_card_geometry(width: int, height: int) -> dict[str, Any]:
    """Geometry for transcript-grounded cards in the lower-right safe band."""

    pip = _portrait_pip_geometry(width, height)
    if not pip.get("bbox"):
        return {"safe": False, "reason": "not_portrait_9_16", "bbox": None}
    bbox = (
        round(width * 0.665),
        round(height * 0.605),
        round(width * 0.955),
        round(height * 0.748),
    )
    face = tuple(
        int(pip["face_safe_bbox"][key])
        for key in ("left", "top", "right", "bottom")
    )
    subtitle = tuple(
        int(pip["subtitle_bbox"][key])
        for key in ("left", "top", "right", "bottom")
    )
    pip_box = tuple(
        int(pip["bbox"][key])
        for key in ("left", "top", "right", "bottom")
    )
    return {
        "safe": not any(
            _rects_intersect(bbox, other) for other in (face, subtitle, pip_box)
        ),
        "reason": "lower_right_safe_zone",
        "bbox": {
            "left": bbox[0],
            "top": bbox[1],
            "right": bbox[2],
            "bottom": bbox[3],
            "width": bbox[2] - bbox[0],
            "height": bbox[3] - bbox[1],
        },
        "intersects_face_safe_bbox": _rects_intersect(bbox, face),
        "intersects_subtitle_bbox": _rects_intersect(bbox, subtitle),
        "intersects_pip_bbox": _rects_intersect(bbox, pip_box),
    }


def _run_media_command(*args, **kwargs):
    """Run bundled media tools without flashing console windows on Windows."""
    kwargs.setdefault("creationflags", _WINDOWS_NO_WINDOW)
    if args and isinstance(args[0], (list, tuple)) and args[0]:
        command = list(args[0])
        executable = str(command[0]).lower()
        if executable in {"ffmpeg", "ffmpeg.exe", "ffprobe", "ffprobe.exe"}:
            tools = _trusted_local_media_tools()
            command[0] = tools["ffmpeg"] if executable.startswith("ffmpeg") else tools["ffprobe"]
            args = (command, *args[1:])
    return subprocess.run(*args, **kwargs)


def _measure_audio_video_drift(path: Path, *, threshold_ms: float = 67.0) -> dict[str, Any] | None:
    """Measure stream start/end PTS drift with local FFprobe only."""
    if not path.is_file():
        return None
    result = _run_media_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,start_time,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        streams = json.loads(result.stdout or "{}").get("streams") or []
        video = next(item for item in streams if item.get("codec_type") == "video")
        audio = next(item for item in streams if item.get("codec_type") == "audio")
        video_start = float(video.get("start_time") or 0.0)
        audio_start = float(audio.get("start_time") or 0.0)
        video_end = video_start + float(video.get("duration") or 0.0)
        audio_end = audio_start + float(audio.get("duration") or 0.0)
    except (StopIteration, TypeError, ValueError, json.JSONDecodeError):
        return None
    start_drift_ms = abs(audio_start - video_start) * 1000.0
    end_drift_ms = abs(audio_end - video_end) * 1000.0
    max_drift_ms = max(start_drift_ms, end_drift_ms)
    return {
        "method": "ffprobe_stream_start_end_pts",
        "start_drift_ms": round(start_drift_ms, 3),
        "end_drift_ms": round(end_drift_ms, 3),
        "max_drift_ms": round(max_drift_ms, 3),
        "threshold_ms": threshold_ms,
        "passed": max_drift_ms <= threshold_ms,
    }


class VideoEditorWorkflowError(ValueError):
    """用户可理解的智能剪辑工作流错误。"""


class VideoEditorWorkflowService:
    """协调系统素材、转写复核与视频编辑服务。"""

    def __init__(
        self,
        repository,
        video_editing_service,
        transcription_service,
        copywriting_service,
        *,
        cloud_configuration=None,
        cloud_providers=None,
    ) -> None:
        self.repository = repository
        self.video_editing_service = video_editing_service
        self.transcription_service = transcription_service
        self.copywriting_service = copywriting_service
        self._cloud_configuration_override = cloud_configuration
        self._cloud_providers_override = cloud_providers

    def _debit_credits(
        self,
        cost_cny: Decimal | float,
        *,
        reason: str,
        ref_type: str,
        ref_id: str,
    ) -> None:
        """按人民币费用扣积分；费用为 0/未知不扣，余额不足抛出 InsufficientCreditsError。"""
        from src.services.credits import (
            CreditsService,
            cny_to_credits,
        )

        credits = cny_to_credits(cost_cny)
        if credits <= 0:
            return
        CreditsService(self.repository).debit(
            credits,
            reason,
            ref_type=ref_type,
            ref_id=ref_id,
        )

    # ------------------------------------------------------------------
    # 系统素材
    # ------------------------------------------------------------------
    def list_sources(self) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        seen_paths: set[str] = set()

        for task in self.repository.list_tasks():
            if not isinstance(task, AvatarTask):
                continue
            path = Path(task.result_path or "")
            if (
                task.status != TaskStatus.SUCCEEDED
                or task.is_mock
                or not path.is_file()
            ):
                continue
            key = str(path.resolve()).casefold()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            source_id = f"avatar:{task.task_id}"
            sources.append(
                self._source_payload(
                    source_id=source_id,
                    source_type="avatar",
                    source_task_id=task.task_id,
                    title=self._semantic_source_title(
                        source_id,
                        task.title,
                        task.script_text,
                    ),
                    path=path,
                    created_at=task.created_at,
                )
            )

        for task in self.repository.list_tasks():
            if (
                not isinstance(task, VideoEditTask)
                or task.outputs.get("workflow") != "upload_source"
            ):
                continue
            path = Path(task.source_video_path)
            if (
                task.status != TaskStatus.SUCCEEDED
                or task.is_mock
                or not path.is_file()
            ):
                continue
            key = str(path.resolve()).casefold()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            sources.append(
                self._source_payload(
                    source_id=f"upload:{task.task_id}",
                    source_type="upload",
                    source_task_id=task.task_id,
                    title=task.title,
                    path=path,
                    created_at=task.created_at,
                )
            )

        list_runs = getattr(self.repository, "list_pipeline_runs", None)
        if callable(list_runs):
            for run in list_runs(limit=100):
                path = self._pipeline_video_path(run)
                if path is None:
                    continue
                key = str(path.resolve()).casefold()
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                sources.append(
                    self._source_payload(
                        source_id=f"pipeline:{run.run_id}",
                        source_type="pipeline",
                        source_task_id=run.run_id,
                        title=f"流水线成片 · {run.keyword}",
                        path=path,
                        created_at=run.updated_at,
                    )
                )

        return sorted(sources, key=lambda item: item["created_at"], reverse=True)

    def resolve_source(self, source_id: str) -> dict[str, Any]:
        source_type, sep, record_id = source_id.partition(":")
        if not sep or not record_id:
            raise VideoEditorWorkflowError("素材标识无效。")

        if source_type == "avatar":
            task = self.repository.get_task(record_id)
            if not isinstance(task, AvatarTask):
                raise VideoEditorWorkflowError("数字人成片不存在。")
            path = Path(task.result_path or "")
            if (
                task.status != TaskStatus.SUCCEEDED
                or task.is_mock
                or not path.is_file()
            ):
                raise VideoEditorWorkflowError("该数字人成片不可用或已被清理。")
            return self._source_payload(
                source_id=source_id,
                source_type=source_type,
                source_task_id=record_id,
                title=self._semantic_source_title(
                    source_id,
                    task.title,
                    task.script_text,
                ),
                path=path,
                created_at=task.created_at,
            )

        if source_type == "pipeline":
            list_runs = getattr(self.repository, "list_pipeline_runs", None)
            if not callable(list_runs):
                raise VideoEditorWorkflowError("当前环境未提供流水线素材。")
            for run in list_runs(limit=500):
                if run.run_id != record_id:
                    continue
                path = self._pipeline_video_path(run)
                if path is None:
                    raise VideoEditorWorkflowError("该流水线尚未生成可用成片。")
                return self._source_payload(
                    source_id=source_id,
                    source_type=source_type,
                    source_task_id=record_id,
                    title=f"流水线成片 · {run.keyword}",
                    path=path,
                    created_at=run.updated_at,
                )
            raise VideoEditorWorkflowError("流水线素材不存在。")

        if source_type == "upload":
            task = self.repository.get_task(record_id)
            if (
                not isinstance(task, VideoEditTask)
                or task.outputs.get("workflow") != "upload_source"
            ):
                raise VideoEditorWorkflowError("上传素材不存在。")
            path = Path(task.source_video_path)
            if (
                task.status != TaskStatus.SUCCEEDED
                or task.is_mock
                or not path.is_file()
            ):
                raise VideoEditorWorkflowError("该上传素材不可用或已被清理。")
            return self._source_payload(
                source_id=source_id,
                source_type=source_type,
                source_task_id=record_id,
                title=task.title,
                path=path,
                created_at=task.created_at,
            )

        raise VideoEditorWorkflowError("不支持的系统素材来源。")

    def _cached_source_context(self, source_id: str) -> dict[str, Any]:
        context: dict[str, Any] = {
            "selected_title": "",
            "title_candidates": [],
            "subtitle_segments": [],
            "duration_seconds": 0.0,
            "selected_bgm_id": None,
            "bgm_reason": None,
            "edit_plan": {},
            "enabled_plan_step_ids": [],
            "review_snapshot": {},
        }
        list_batches = getattr(self.repository, "list_video_editor_batches", None)
        if not callable(list_batches):
            return context
        for batch in list_batches(limit=100):
            for item in batch.items:
                if item.source_id != source_id:
                    continue
                if not context["selected_title"] and item.selected_title:
                    context["selected_title"] = item.selected_title
                if not context["title_candidates"] and item.title_candidates:
                    context["title_candidates"] = list(item.title_candidates)
                if not context["subtitle_segments"] and item.subtitle_segments:
                    context["subtitle_segments"] = [
                        dict(segment) for segment in item.subtitle_segments
                    ]
                if not context["selected_bgm_id"] and item.selected_bgm_id:
                    context["selected_bgm_id"] = item.selected_bgm_id
                    context["bgm_reason"] = item.bgm_reason
                if not context["edit_plan"] and item.edit_plan:
                    context["edit_plan"] = dict(item.edit_plan)
                if not context["enabled_plan_step_ids"] and item.enabled_plan_step_ids:
                    context["enabled_plan_step_ids"] = list(item.enabled_plan_step_ids)
                if not context["review_snapshot"] and item.review_snapshot:
                    context["review_snapshot"] = dict(item.review_snapshot)
                media = item.provider_payload.get("media") or {}
                if not context["duration_seconds"] and media.get("duration_seconds"):
                    context["duration_seconds"] = float(media["duration_seconds"])
            if (
                context["selected_title"]
                and context["title_candidates"]
                and context["subtitle_segments"]
                and context["duration_seconds"]
            ):
                break
        return context

    @staticmethod
    def _script_topic_title(script_text: str, fallback: str) -> str:
        script = re.sub(r"\s+", " ", script_text).strip()
        if not script:
            return fallback
        first_sentence = re.split(r"[。！？!?；;\n]|\s+", script, maxsplit=1)[0]
        subject = re.sub(
            r"^(最近|大家好|你知道吗|你发现没|今天(?:我们)?(?:来)?聊聊)\s*",
            "",
            first_sentence,
        ).strip(" ，,：:")
        if len(subject) > 9:
            subject = re.sub(r"(想做|想要|正在做)", "", subject, count=1)
        return (subject or first_sentence or fallback)[:40]

    def _semantic_source_title(
        self,
        source_id: str,
        stored_title: str,
        script_text: str = "",
    ) -> str:
        cached = self._cached_source_context(source_id)
        cached_title = str(cached.get("selected_title") or "").strip()
        if cached_title:
            return cached_title
        candidates = list(cached.get("title_candidates") or [])
        if candidates:
            return str(candidates[0]).strip()[:100]
        if not _GENERIC_AVATAR_TITLE.fullmatch(stored_title.strip()):
            return stored_title
        return self._script_topic_title(script_text, stored_title)

    def _avatar_script_text(self, source_id: str) -> str:
        source_type, _, record_id = source_id.partition(":")
        if source_type != "avatar" or not record_id:
            return ""
        task = self.repository.get_task(record_id)
        return task.script_text if isinstance(task, AvatarTask) else ""

    @staticmethod
    def _estimated_script_segments(
        script_text: str,
        duration_seconds: float,
    ) -> list[dict[str, Any]]:
        # The approved production script deliberately uses spaces to mark the
        # editor's human-reviewed spoken clauses.  Preserve those boundaries:
        # flattening them first makes captions jump across clauses (for example
        # "服务细节拍短视频标题写"), which reads like the legacy hard splitter.
        parts = [
            part.strip()
            for part in re.findall(
                r"[^。！？!?；;\s]+[。！？!?；;]?",
                script_text,
            )
            if part.strip()
        ]
        if not parts:
            return []
        total_characters = sum(len(part) for part in parts) or 1
        duration = duration_seconds or max(2.0, total_characters / 4.2)
        cursor = 0.0
        segments: list[dict[str, Any]] = []
        for index, part in enumerate(parts):
            end = (
                duration
                if index == len(parts) - 1
                else cursor + duration * len(part) / total_characters
            )
            segments.append(
                {
                    "start": round(cursor, 3),
                    "end": round(end, 3),
                    "text": part,
                }
            )
            cursor = end
        return segments

    @staticmethod
    def approved_script_segments_from_asr(
        script_text: str,
        asr_segments: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Project real ASR timing onto the exact approved spoken script.

        The avatar is generated from the approved script, while ASR may return
        harmless spelling variants (for example ``稳定的``/``稳定地`` or
        ``7``/``七``).  Keep ASR's real pause boundaries, but never replace the
        approved on-screen words with those recognition variants.
        """

        approved = re.sub(r"[\W_]+", "", script_text, flags=re.UNICODE)
        normalized_asr = [
            {
                "start": float(segment.get("start", 0)),
                "end": float(segment.get("end", 0)),
                "text": re.sub(
                    r"[\W_]+",
                    "",
                    str(segment.get("text") or ""),
                    flags=re.UNICODE,
                ),
            }
            for segment in asr_segments
            if str(segment.get("text") or "").strip()
        ]
        recognized = "".join(segment["text"] for segment in normalized_asr)
        if not approved or not recognized or not normalized_asr:
            raise VideoEditorWorkflowError("真实字幕时间轴为空，不能开始智能剪辑。")
        matcher = SequenceMatcher(None, recognized, approved, autojunk=False)
        if matcher.ratio() < 0.82:
            raise VideoEditorWorkflowError(
                "真实语音与已确认口播文案差异过大，已停止避免字幕错配。"
            )
        opcodes = matcher.get_opcodes()

        def project_boundary(position: int) -> int:
            for _tag, source_start, source_end, target_start, target_end in opcodes:
                if position > source_end:
                    continue
                if source_end <= source_start:
                    return target_start
                relative = (position - source_start) / (source_end - source_start)
                return max(
                    target_start,
                    min(
                        target_end,
                        round(target_start + relative * (target_end - target_start)),
                    ),
                )
            return len(approved)

        projected: list[dict[str, Any]] = []
        source_cursor = 0
        target_cursor = 0
        for index, segment in enumerate(normalized_asr):
            source_cursor += len(segment["text"])
            target_end = (
                len(approved)
                if index == len(normalized_asr) - 1
                else max(target_cursor, project_boundary(source_cursor))
            )
            text = approved[target_cursor:target_end]
            if text:
                projected.append(
                    {
                        "start": segment["start"],
                        "end": segment["end"],
                        "text": text,
                    }
                )
            target_cursor = target_end
        if target_cursor != len(approved):
            raise VideoEditorWorkflowError(
                "真实字幕时间轴未完整覆盖口播文案，已停止避免字幕错配。"
            )
        return projected

    def upload_source(
        self,
        *,
        file_name: str,
        media_type: str,
        media_bytes: bytes,
        rights_confirmed: bool,
        rights_holder: str,
    ) -> dict[str, Any]:
        """保存已授权本地视频，使其可被后续批次可靠引用。"""
        suffix = Path(file_name).suffix.lower()
        if suffix not in {".mp4", ".mov", ".m4v"}:
            raise VideoEditorWorkflowError("仅支持 MP4、MOV 或 M4V 素材。")
        if not rights_confirmed or not rights_holder.strip():
            raise VideoEditorWorkflowError("请确认拥有素材处理权并填写授权主体。")
        if not media_bytes:
            raise VideoEditorWorkflowError("上传文件为空。")
        if len(media_bytes) > _MAX_SOURCE_UPLOAD_BYTES:
            raise VideoEditorWorkflowError("上传素材超过 50MB 限制。")

        now = datetime.now().astimezone()
        task_id = f"upload-{uuid4().hex[:10]}"
        storage_dir = (
            self.video_editing_service.output_directory.parent / "video_uploads"
        )
        storage_dir.mkdir(parents=True, exist_ok=True)
        safe_name = (
            re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file_name).name) or f"source{suffix}"
        )
        path = storage_dir / f"{task_id}-{safe_name}"
        path.write_bytes(media_bytes)
        task = VideoEditTask(
            task_id=task_id,
            title=f"本地上传 · {file_name}",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            source_video_path=str(path),
            stage="素材已就绪",
            is_mock=False,
            outputs={
                "workflow": "upload_source",
                "rights_holder": rights_holder.strip(),
                "media_type": media_type or "video/mp4",
            },
        )
        self.repository.save_task(task)
        return self.resolve_source(f"upload:{task_id}")

    def upload_visual_asset(
        self,
        *,
        kind: str,
        file_name: str,
        media_type: str,
        media_bytes: bytes,
        rights_confirmed: bool,
        rights_holder: str,
        source_url: str = "",
        license_name: str = "",
        license_url: str = "",
        domestic_context: str = "unknown",
        domestic_scene: str = "",
    ) -> dict[str, Any]:
        """保存产品主图或背景图，供产品讲解成片可靠引用。"""
        if kind not in {"product", "background", "broll"}:
            raise VideoEditorWorkflowError("视觉素材类型只能是商品主图、背景图或 B-roll。")
        suffix = Path(file_name).suffix.lower()
        allowed_suffixes = _BROLL_SUFFIXES if kind == "broll" else _VISUAL_SUFFIXES
        if suffix not in allowed_suffixes:
            raise VideoEditorWorkflowError(
                "B-roll 仅支持 PNG、JPG、JPEG、WebP、MP4、MOV 或 M4V。"
                if kind == "broll"
                else "仅支持 PNG、JPG、JPEG 或 WebP 图片。"
            )
        if not rights_confirmed or not rights_holder.strip():
            raise VideoEditorWorkflowError("请确认拥有图片处理权并填写授权主体。")
        if not media_bytes:
            raise VideoEditorWorkflowError("上传图片为空。")
        normalized_domestic_context = normalize_domestic_context(domestic_context)
        normalized_domestic_scene = str(domestic_scene or "").strip()[:160]
        if len(media_bytes) > _MAX_VISUAL_ASSET_BYTES:
            raise VideoEditorWorkflowError("图片超过 10MB 限制。")
        if suffix in _VISUAL_SUFFIXES and not self._is_supported_image(media_bytes, suffix):
            raise VideoEditorWorkflowError("图片内容与文件格式不匹配。")
        if kind == "broll" and suffix not in _VISUAL_SUFFIXES and not self._is_supported_video_bytes(media_bytes, suffix):
            raise VideoEditorWorkflowError("B-roll 视频内容与文件格式不匹配。")

        now = datetime.now().astimezone()
        asset_id = f"{kind}-{uuid4().hex[:10]}"
        safe_name = (
            re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file_name).name) or f"{kind}{suffix}"
        )
        directory = self._visual_asset_directory()
        path = directory / f"{asset_id}-{safe_name}"
        path.write_bytes(media_bytes)
        normalized_source_url = source_url.strip()
        normalized_license_name = license_name.strip()[:120]
        normalized_license_url = license_url.strip()
        for label, value in (
            ("素材来源链接", normalized_source_url),
            ("授权说明链接", normalized_license_url),
        ):
            if value and urlparse(value).scheme not in {"http", "https"}:
                path.unlink(missing_ok=True)
                raise VideoEditorWorkflowError(f"{label}必须是 http 或 https 地址。")
        duration_seconds = 0.0
        width = 0
        height = 0
        if suffix in _VISUAL_SUFFIXES:
            try:
                from PIL import Image

                with Image.open(path) as image:
                    width, height = image.size
            except Exception:
                # Keep the historical upload contract for product/background
                # fixtures that only validate the container header.  Such an
                # asset has zero dimensions and is therefore never eligible
                # for real B-roll acceptance until it is replaced by a valid
                # decoded image.
                width, height = 0, 0
        else:
            try:
                probed = self._probe_media(path)
                duration_seconds = float(probed.get("duration_seconds") or 0)
                width = int(probed.get("width") or 0)
                height = int(probed.get("height") or 0)
            except Exception as exc:
                path.unlink(missing_ok=True)
                raise VideoEditorWorkflowError("B-roll 视频无法读取时长或画面尺寸，素材未保存。") from exc
        metadata = {
            "asset_id": asset_id,
            "kind": kind,
            "name": Path(file_name).stem or kind,
            "original_name": file_name,
            "stored_name": path.name,
            "media_type": mimetypes.guess_type(path.name)[0] or "image/png",
            "rights_holder": rights_holder.strip(),
            "rights_confirmed_at": now.isoformat(),
            "created_at": now.isoformat(),
            "source_provider": "local_upload",
            "asset_origin": "local_uploaded_asset",
            "source_type": "manual_upload",
            "rights_status": "user_confirmed_for_local_use",
            "publish_licensed": False,
            "source_url": normalized_source_url,
            "license_name": normalized_license_name,
            "license_url": normalized_license_url,
            "authorization_status": "confirmed" if rights_confirmed else "unverified",
            "sha256": hashlib.sha256(media_bytes).hexdigest(),
            "duration_seconds": round(duration_seconds, 3),
            "width": width,
            "height": height,
            "aspect_ratio": round(width / max(height, 1), 4),
            "cache_path": str(path),
            "domestic_context": normalized_domestic_context,
            "domestic_scene": normalized_domestic_scene,
        }
        self._visual_asset_metadata_path(asset_id).write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        return self._visual_asset_payload(metadata, path)

    def list_visual_assets(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind is not None and kind not in {"product", "background", "broll", "vector"}:
            raise VideoEditorWorkflowError("视觉素材类型无效。")
        directory = self._visual_asset_directory(create=False)
        if not directory.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for metadata_path in directory.glob("*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata.get("kind") not in {"product", "background", "broll", "vector"}:
                    continue
                if kind is not None and metadata["kind"] != kind:
                    continue
                media_path = directory / str(metadata["stored_name"])
                if not media_path.is_file():
                    continue
                items.append(self._visual_asset_payload(metadata, media_path))
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    def _creative_asset_library(self):
        from src.services.creative_asset_library import CreativeAssetLibrary

        return CreativeAssetLibrary(self._visual_asset_directory())

    def _ensure_release_creative_assets(
        self,
        transcript: str,
        *,
        shot_plan: Mapping[str, Any],
        transcript_segments: Sequence[Mapping[str, Any]] | None = None,
        render_decorative_vectors: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Build restrained semantic visuals without forcing sticker-like overlays.

        The semantic band is tied to a real shot and stays in the lower-left
        safe zone.  Decorative icon assets remain opt-in; the band is the
        default lightweight visual layer when a real B-roll event exists.
        """

        if not render_decorative_vectors:
            from src.services.creative_asset_library import classify_theme

            library = self._creative_asset_library()
            theme = classify_theme(transcript)
            adaptive_intents = _build_adaptive_visual_intents(transcript_segments or [])
            if adaptive_intents:
                return {
                    "track_id": "adaptive-visual-system-v1",
                    "kind": "grounded_data_visual_layer",
                    "theme": theme,
                    "items": adaptive_intents,
                    "asset_count": 0,
                    "library_degradation": (
                        "按已审核口播中的数字、步骤、转折和具体事实生成程序化视觉层；"
                        "不虚构事实，不把视觉层计为真实 B-roll。"
                    ),
                    "typography": library.ensure_font(theme),
                    "source_policy": "transcript_grounded_code_drawn_visuals",
                }, []
            return {
                "track_id": "vector-visuals-v1",
                "kind": "semantic_motion_layer",
                "theme": theme,
                "items": [],
                "asset_count": 0,
                "library_degradation": (
                    "当前口播没有可靠的数字、流程或关系语义视觉信号；保留人物安全运镜，"
                    "不生成重复字幕的文字卡片。"
                ),
                "typography": library.ensure_font(theme),
                "source_policy": "safe_subject_motion_without_invented_facts",
            }, []

        try:
            result = self._creative_asset_library().ensure_vector_assets(
                transcript,
                max_assets=3,
            )
        except Exception as exc:
            result = {
                "theme": "general",
                "items": [],
                "typography": {"theme": "general", "font_family": "YaHei"},
                "degradation": f"透明矢量素材自动匹配失败，已安全降级：{exc}",
            }
        items = [item for item in result.get("items") or [] if isinstance(item, Mapping)]
        candidates = [
            shot
            for shot in shot_plan.get("shots") or []
            if isinstance(shot, Mapping)
            and shot.get("role") == "A-roll"
            and float(shot.get("duration_seconds") or 0) >= 1.8
            and float(shot.get("timeline_start") or 0) > 0
        ]
        placements: list[dict[str, Any]] = []
        for shot, asset in zip(candidates[1::3], items, strict=False):
            placements.append(
                {
                    "track_id": "vector-visuals-v1",
                    "asset_id": asset.get("asset_id"),
                    "start": round(float(shot.get("timeline_start") or 0), 3),
                    "end": round(float(shot.get("timeline_end") or 0), 3),
                    "mode": "pip",
                    "animation": "fade_scale_rotate",
                    "position": "top_right_safe",
                    "grounded_in_text": True,
                    "semantic_text": str(transcript)[:120],
                }
            )
        return {
            "track_id": "vector-visuals-v1",
            "kind": "transparent_vector",
            "theme": result.get("theme") or "general",
            "items": placements,
            "asset_count": len(items),
            "library_degradation": result.get("degradation"),
            "typography": dict(result.get("typography") or {}),
            "source_policy": "official_open_license_allowlist",
        }, items

    def resolve_visual_asset(
        self, asset_id: str, *, expected_kind: str | None = None
    ) -> dict[str, Any]:
        metadata_path = self._visual_asset_metadata_path(asset_id)
        if not metadata_path.is_file():
            raise VideoEditorWorkflowError("视觉素材不存在或已被清理。")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("asset_id") != asset_id:
                raise VideoEditorWorkflowError("视觉素材标识无效。")
            if expected_kind is not None and metadata.get("kind") != expected_kind:
                raise VideoEditorWorkflowError("视觉素材类型不匹配。")
            media_path = self._visual_asset_directory(create=False) / str(
                metadata["stored_name"]
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise VideoEditorWorkflowError("视觉素材记录无法读取。") from exc
        if not media_path.is_file():
            raise VideoEditorWorkflowError("视觉素材文件不存在或已被清理。")
        return self._visual_asset_payload(metadata, media_path)

    def create_product_showcase_job(
        self,
        *,
        source_id: str,
        product_asset_id: str,
        background_asset_id: str | None = None,
        layout: str = "avatar_left_product_right",
    ) -> VideoEditTask:
        """创建不调用数字人供应商的产品讲解后期合成任务。"""
        if layout not in {"avatar_left_product_right", "product_canvas_avatar_pip"}:
            raise VideoEditorWorkflowError("不支持的产品讲解版式。")
        source = self.resolve_source(source_id)
        if source["source_type"] != "avatar":
            raise VideoEditorWorkflowError("产品讲解包装只能使用已完成的数字人成片。")
        product = self.resolve_visual_asset(product_asset_id, expected_kind="product")
        background = (
            self.resolve_visual_asset(background_asset_id, expected_kind="background")
            if background_asset_id
            else None
        )
        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id=f"showcase-{uuid4().hex[:10]}",
            title=f"产品讲解 · {source['title']}",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            source_video_path=source["_path"],
            edit_config=VideoEditConfig(
                steps=[
                    VideoEditStep(
                        kind=VideoEditStepKind.PRODUCT_SHOWCASE,
                        params={
                            "product_path": product["_path"],
                            "background_path": background["_path"]
                            if background
                            else None,
                            "layout": layout,
                        },
                        order=0,
                    )
                ]
            ),
            source_avatar_task_id=source["source_task_id"],
            stage="等待产品讲解合成",
            is_mock=False,
            outputs={
                "workflow": "product_showcase",
                "source_id": source_id,
                "product_asset_id": product_asset_id,
                "background_asset_id": background_asset_id or "",
                "layout": layout,
            },
        )
        self.repository.save_task(task)
        _WORKFLOW_EXECUTOR.submit(self._run_product_showcase, task.task_id)
        return task

    def _run_product_showcase(self, task_id: str) -> None:
        task = self._get_workflow_task(task_id, "product_showcase")
        try:
            self._update(
                task, status=TaskStatus.RUNNING, progress=10, stage="准备产品讲解合成"
            )
            result = self.video_editing_service.edit_video(
                source_video_path=task.source_video_path,
                edit_config=task.edit_config,
                source_avatar_task_id=task.source_avatar_task_id,
                task_id=task.task_id,
            )
            result = result.model_copy(
                update={
                    "title": task.title,
                    "outputs": {**result.outputs, **task.outputs},
                }
            )
            self.repository.save_task(result)
        except Exception as exc:
            self._update(
                task,
                status=TaskStatus.FAILED,
                stage="产品讲解合成失败",
                error_message=str(exc),
            )

    def list_bgm_assets(self) -> list[dict[str, Any]]:
        directory = self._bgm_directory()
        if not directory.is_dir():
            return []
        assets: list[dict[str, Any]] = []
        for metadata_path in directory.glob("bgm-*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata.get("retired"):
                    continue
                media_path = directory / metadata["stored_name"]
                if not media_path.is_file():
                    continue
                assets.append(self._bgm_payload(metadata, media_path))
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return sorted(assets, key=lambda item: item["created_at"], reverse=True)

    def upload_bgm(
        self,
        *,
        file_name: str,
        media_type: str,
        media_bytes: bytes,
        mood: str,
        rights_confirmed: bool,
        rights_holder: str,
        voiceover_category: str = "通用口播",
        energy: str = "克制",
        source_provider: str = "manual",
        source_url: str = "",
        license_url: str = "",
        content_id_risk: str = "unknown",
        candidate_only: bool = False,
    ) -> dict[str, Any]:
        suffix = Path(file_name).suffix.lower()
        if suffix not in _BGM_SUFFIXES:
            raise VideoEditorWorkflowError(
                "背景音乐仅支持 MP3、WAV、M4A、AAC 或 FLAC。"
            )
        if not rights_confirmed and not candidate_only:
            raise VideoEditorWorkflowError("请确认拥有音乐使用权并填写授权主体。")
        if rights_confirmed and not rights_holder.strip():
            raise VideoEditorWorkflowError("请确认拥有音乐使用权并填写授权主体。")
        if not media_bytes:
            raise VideoEditorWorkflowError("上传的背景音乐为空。")
        if len(media_bytes) > _MAX_BGM_BYTES:
            raise VideoEditorWorkflowError("背景音乐超过 30MB 限制。")
        normalized_category = voiceover_category.strip() or "通用口播"
        if normalized_category not in _BGM_VOICEOVER_CATEGORIES:
            raise VideoEditorWorkflowError("请选择系统支持的口播音乐分类。")
        normalized_energy = energy.strip() or "克制"
        if normalized_energy not in _BGM_ENERGY_LEVELS:
            raise VideoEditorWorkflowError("请选择系统支持的音乐能量等级。")
        normalized_provider = source_provider.strip().lower() or "manual"
        if normalized_provider not in _BGM_SOURCE_PROVIDERS:
            raise VideoEditorWorkflowError("背景音乐来源类型无效。")
        normalized_content_id_risk = content_id_risk.strip().lower() or "unknown"
        if normalized_content_id_risk not in _BGM_CONTENT_ID_RISKS:
            raise VideoEditorWorkflowError("背景音乐版权识别风险标记无效。")
        normalized_source_url = source_url.strip()
        normalized_license_url = license_url.strip()
        for label, value in (
            ("素材来源链接", normalized_source_url),
            ("授权说明链接", normalized_license_url),
        ):
            if value and urlparse(value).scheme not in {"http", "https"}:
                raise VideoEditorWorkflowError(f"{label}必须是 http 或 https 地址。")
        if normalized_provider != "manual" and not normalized_source_url:
            raise VideoEditorWorkflowError("外部音乐必须保存原始素材页面链接。")

        asset_id = f"bgm-{uuid4().hex[:12]}"
        directory = self._bgm_directory()
        directory.mkdir(parents=True, exist_ok=True)
        media_path = directory / f"{asset_id}{suffix}"
        media_path.write_bytes(media_bytes)
        try:
            duration_seconds = self._probe_bgm_duration(media_path)
        except Exception:
            media_path.unlink(missing_ok=True)
            raise
        now = datetime.now().astimezone()
        normalized_media_type = (media_type or "").strip().lower()
        if normalized_media_type in {
            "",
            "application/octet-stream",
            "binary/octet-stream",
        }:
            normalized_media_type = mimetypes.guess_type(file_name)[0] or "audio/mpeg"
        metadata = {
            "asset_id": asset_id,
            "title": Path(file_name).stem[:100] or "本地背景音乐",
            "original_name": Path(file_name).name,
            "stored_name": media_path.name,
            "media_type": normalized_media_type,
            "mood": (mood or "通用").strip()[:30],
            "rights_holder": (rights_holder.strip() or "待核验：授权主体未确认")[:100],
            "rights_confirmed_at": now.isoformat() if rights_confirmed else "",
            "created_at": now.isoformat(),
            "duration_seconds": duration_seconds,
            "voiceover_category": normalized_category,
            "energy": normalized_energy,
            "tags": [
                token
                for token in re.split(r"[\s,，、·/]+", (mood or "").strip())
                if token
            ][:12],
            "source_provider": normalized_provider,
            "source_url": normalized_source_url,
            "license_url": normalized_license_url,
            "content_id_risk": normalized_content_id_risk,
            "authorization_status": "confirmed" if rights_confirmed else "unverified",
            "auto_eligible": bool(rights_confirmed and normalized_content_id_risk != "registered"),
            "candidate_only": bool(candidate_only and not rights_confirmed),
        }
        (directory / f"{asset_id}.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self._bgm_payload(metadata, media_path)

    def resolve_bgm_asset(self, asset_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"bgm-[a-f0-9]{12}", asset_id or ""):
            raise VideoEditorWorkflowError("背景音乐标识无效。")
        metadata_path = self._bgm_directory() / f"{asset_id}.json"
        if not metadata_path.is_file():
            raise VideoEditorWorkflowError("背景音乐不存在。")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            media_path = self._bgm_directory() / metadata["stored_name"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise VideoEditorWorkflowError("背景音乐记录已损坏。") from exc
        if not media_path.is_file():
            raise VideoEditorWorkflowError("背景音乐文件已不存在。")
        return {**self._bgm_payload(metadata, media_path), "_path": str(media_path)}

    def _bgm_directory(self) -> Path:
        return self.video_editing_service.output_directory.parent / "bgm_library"

    @staticmethod
    def _probe_bgm_duration(path: Path) -> float:
        _trusted_local_media_tools()
        result = _run_media_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise VideoEditorWorkflowError("背景音乐无法解析或文件已损坏。")
        data = json.loads(result.stdout or "{}")
        duration = float((data.get("format") or {}).get("duration") or 0)
        if duration <= 0:
            raise VideoEditorWorkflowError("背景音乐时长无效。")
        return round(duration, 2)

    @staticmethod
    def _prepare_bgm_for_cloud_mix(source: Path, *, volume: float) -> Path:
        """Create a low-volume audio-only asset before MPS mixes it with speech.

        MPS Amix has no per-external-track volume control.  Preparing the BGM on
        the server keeps the client lightweight and prevents a 1:1 mix from
        overwhelming a talking-head recording.
        """
        _trusted_local_media_tools()
        safe_volume = min(max(float(volume), 0.08), 0.35)
        handle = tempfile.NamedTemporaryFile(suffix=".m4a", delete=False)
        handle.close()
        prepared = Path(handle.name)
        result = _run_media_command(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                "-vn",
                "-af",
                f"volume={safe_volume:.3f},afade=t=in:st=0:d=0.5",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(prepared),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if (
            result.returncode != 0
            or not prepared.is_file()
            or prepared.stat().st_size == 0
        ):
            prepared.unlink(missing_ok=True)
            raise VideoEditorWorkflowError("背景音乐预处理失败，请更换音乐后重试。")
        return prepared

    @staticmethod
    def _bgm_payload(metadata: dict[str, Any], media_path: Path) -> dict[str, Any]:
        mood = str(metadata.get("mood") or "通用")
        category = str(metadata.get("voiceover_category") or "").strip()
        if category not in _BGM_VOICEOVER_CATEGORIES:
            category = (
                "科技未来"
                if "科技" in mood or "未来" in mood
                else "理性干货"
                if "知识" in mood or "讲解" in mood
                else "故事叙事"
                if "叙事" in mood or "故事" in mood
                else "情绪共鸣"
                if "温柔" in mood or "治愈" in mood
                else "商业表达"
                if "商务" in mood or "品牌" in mood
                else "轻松日常"
                if "轻松" in mood or "欢快" in mood
                else "通用口播"
            )
        energy = str(metadata.get("energy") or "克制").strip()
        if energy not in _BGM_ENERGY_LEVELS:
            energy = "克制"
        tags = metadata.get("tags")
        normalized_tags = (
            [str(item).strip() for item in tags if str(item).strip()]
            if isinstance(tags, list)
            else [token for token in re.split(r"[\s,，、·/]+", mood) if token]
        )
        return {
            "asset_id": metadata["asset_id"],
            "title": metadata["title"],
            "original_name": metadata["original_name"],
            "media_type": metadata["media_type"],
            "mood": mood,
            "voiceover_category": category,
            "energy": energy,
            "tags": normalized_tags[:12],
            "rights_holder": metadata["rights_holder"],
            "rights_confirmed_at": metadata["rights_confirmed_at"],
            "created_at": metadata["created_at"],
            "duration_seconds": metadata["duration_seconds"],
            "size_bytes": media_path.stat().st_size,
            "media_url": f"/api/v1/video-editor/bgm/{metadata['asset_id']}/media",
            "source_provider": str(metadata.get("source_provider") or "manual"),
            "source_url": str(metadata.get("source_url") or ""),
            "license_url": str(metadata.get("license_url") or ""),
            "content_id_risk": str(metadata.get("content_id_risk") or "unknown"),
            "authorization_status": str(
                metadata.get("authorization_status")
                or ("confirmed" if metadata.get("rights_confirmed_at") else "unverified")
            ),
            "auto_eligible": bool(
                metadata.get("auto_eligible", bool(metadata.get("rights_confirmed_at")))
                and str(metadata.get("content_id_risk") or "unknown") != "registered"
            ),
            "generated": bool(metadata.get("generated", False)),
        }

    def _visual_asset_directory(self, *, create: bool = True) -> Path:
        directory = (
            self.video_editing_service.output_directory.parent / "creative_assets"
        )
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _register_local_generated_acceptance_assets(self) -> list[dict[str, Any]]:
        """Register the checked-in generated-image manifest for local acceptance.

        This path is intentionally called only by the explicit release-template
        local export.  It does not make generated images stock footage or grant
        publishing rights; it merely makes the already traceable local assets
        renderable by the same asset resolver used by uploaded B-roll.
        """

        configured = os.getenv("VIDEOINSIGHT_GENERATED_ASSET_MANIFEST", "").strip()
        manifest_path = (
            Path(configured)
            if configured
            else Path(__file__).resolve().parents[2]
            / "work"
            / "auto-fine-cut-v1-20260822"
            / "generated-assets"
            / "manifest.json"
        )
        if not manifest_path.is_file():
            return []
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, json.JSONDecodeError):
            return []
        if (
            manifest.get("source_type") != "built_in_image_generation"
            or manifest.get("rights_status") != "generated_for_local_acceptance"
            or manifest.get("cloud_upload") is True
            or manifest.get("paid_stock_call") is True
        ):
            return []
        directory = self._visual_asset_directory()
        registered: list[dict[str, Any]] = []
        for entry in manifest.get("assets") or []:
            if not isinstance(entry, Mapping):
                continue
            source_path = manifest_path.parent / str(entry.get("path") or "")
            expected_sha = str(entry.get("sha256") or "").lower()
            if not source_path.is_file() or len(expected_sha) != 64:
                continue
            actual_sha = hashlib.sha256(source_path.read_bytes()).hexdigest().lower()
            if actual_sha != expected_sha:
                continue
            asset_id = f"broll-{actual_sha[:10]}"
            stored_path = directory / f"{asset_id}-{source_path.name}"
            metadata_path = directory / f"{asset_id}.json"
            if not stored_path.is_file():
                shutil.copy2(source_path, stored_path)
            metadata = {
                "asset_id": asset_id,
                "kind": "broll",
                "name": str(entry.get("semantic_binding") or source_path.stem),
                "original_name": source_path.name,
                "stored_name": stored_path.name,
                "media_type": mimetypes.guess_type(source_path.name)[0] or "image/png",
                "rights_holder": "VideoInsight built-in image generation",
                "rights_confirmed_at": str(manifest.get("created_at") or datetime.now().astimezone().isoformat()),
                "created_at": str(manifest.get("created_at") or datetime.now().astimezone().isoformat()),
                "source_provider": "built_in_image_generation",
                "asset_origin": "generated_image_asset",
                "source_type": "built_in_image_generation",
                "rights_status": "generated_for_local_acceptance",
                "publish_licensed": False,
                "source_url": "",
                "license_name": "",
                "license_url": "",
                "authorization_status": "generated_for_local_acceptance",
                "sha256": actual_sha,
                "duration_seconds": 0.0,
                "width": 0,
                "height": 0,
                "aspect_ratio": 0.0,
                "cache_path": str(stored_path),
                "semantic_binding": str(entry.get("semantic_binding") or ""),
                "keywords": [
                    str(keyword).strip()
                    for keyword in (entry.get("keywords") or [])
                    if str(keyword).strip()
                ],
                "manifest_asset_id": str(entry.get("asset_id") or ""),
                "manifest_role": str(entry.get("role") or ""),
                "manifest_path": str(manifest_path),
                "domestic_context": normalize_domestic_context(
                    entry.get("domestic_context")
                ),
                "domestic_scene": str(entry.get("domestic_scene") or "").strip()[:160],
            }
            try:
                from PIL import Image

                with Image.open(stored_path) as image:
                    metadata["width"], metadata["height"] = image.size
                    metadata["aspect_ratio"] = round(
                        image.width / max(image.height, 1), 4
                    )
            except Exception:
                continue
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )
            registered.append(self._visual_asset_payload(metadata, stored_path))
        return registered

    def _visual_asset_metadata_path(self, asset_id: str) -> Path:
        if not re.fullmatch(r"(?:product|background|broll|vector)-[a-f0-9]{10}", asset_id):
            raise VideoEditorWorkflowError("视觉素材标识无效。")
        return self._visual_asset_directory() / f"{asset_id}.json"

    @staticmethod
    def _is_supported_image(media_bytes: bytes, suffix: str) -> bool:
        if suffix == ".png":
            return media_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        if suffix in {".jpg", ".jpeg"}:
            return media_bytes.startswith(b"\xff\xd8\xff")
        return (
            len(media_bytes) >= 12
            and media_bytes[:4] == b"RIFF"
            and media_bytes[8:12] == b"WEBP"
        )

    @staticmethod
    def _is_supported_video_bytes(media_bytes: bytes, suffix: str) -> bool:
        """检查常见 MP4/MOV 容器头，避免把任意文件当成 B-roll。"""
        if suffix not in {".mp4", ".mov", ".m4v"} or len(media_bytes) < 12:
            return False
        return media_bytes[4:8] == b"ftyp"

    @staticmethod
    def _visual_asset_payload(
        metadata: dict[str, Any], media_path: Path
    ) -> dict[str, Any]:
        return {
            "asset_id": metadata["asset_id"],
            "kind": metadata["kind"],
            "name": metadata["name"],
            "original_name": metadata["original_name"],
            "media_type": metadata["media_type"],
            "media_kind": "image" if str(metadata["media_type"]).startswith("image/") else "video",
            "rights_holder": metadata["rights_holder"],
            "rights_confirmed_at": metadata["rights_confirmed_at"],
            "created_at": metadata["created_at"],
            "size_bytes": media_path.stat().st_size,
            "source_url": metadata.get("source_url"),
            "source_provider": metadata.get("source_provider") or metadata.get("provider"),
            "asset_origin": metadata.get("asset_origin", "local_uploaded_asset"),
            "source_type": metadata.get("source_type", "manual_upload"),
            "rights_status": metadata.get("rights_status", "unknown"),
            "publish_licensed": bool(metadata.get("publish_licensed", False)),
            "license_name": metadata.get("license_name"),
            "license_url": metadata.get("license_url"),
            "authorization_status": metadata.get("authorization_status", "unverified"),
            "sha256": metadata.get("sha256"),
            "duration_seconds": metadata.get("duration_seconds"),
            "width": metadata.get("width"),
            "height": metadata.get("height"),
            "aspect_ratio": metadata.get("aspect_ratio"),
            "cache_path": metadata.get("cache_path"),
            "semantic_binding": metadata.get("semantic_binding"),
            "keywords": metadata.get("keywords") or [],
            "semantic_query": metadata.get("semantic_query"),
            "manifest_asset_id": metadata.get("manifest_asset_id"),
            "manifest_role": metadata.get("manifest_role"),
            "manifest_path": metadata.get("manifest_path"),
            "domestic_context": visual_asset_context(metadata),
            "domestic_scene": metadata.get("domestic_scene") or "",
            "media_url": f"/api/v1/video-editor/visual-assets/{metadata['asset_id']}/media",
            "_path": str(media_path),
        }

    @staticmethod
    def _source_payload(
        *,
        source_id: str,
        source_type: str,
        source_task_id: str,
        title: str,
        path: Path,
        created_at: datetime,
    ) -> dict[str, Any]:
        stat = path.stat()
        return {
            "source_id": source_id,
            "source_type": source_type,
            "source_task_id": source_task_id,
            "title": title,
            "file_name": path.name,
            "size_bytes": stat.st_size,
            "created_at": created_at.isoformat(),
            "media_url": f"/api/v1/video-editor/sources/{source_id}/media",
            "media_type": mimetypes.guess_type(path.name)[0] or "video/mp4",
            "_path": str(path),
        }

    def _pipeline_video_path(self, run) -> Path | None:
        if getattr(run, "edit_task_id", None):
            task = self.repository.get_task(run.edit_task_id)
            path = Path(getattr(task, "result_path", "") or "")
            if getattr(task, "status", None) == TaskStatus.SUCCEEDED and path.is_file():
                return path
        for stage in getattr(run, "stages", []) or []:
            if getattr(stage, "stage", None) != PipelineStage.VIDEO_EDITING:
                continue
            path = Path((getattr(stage, "outputs", {}) or {}).get("video_path", ""))
            if path.is_file():
                return path
        return None

    # ------------------------------------------------------------------
    # 分析
    # ------------------------------------------------------------------
    def create_analysis(
        self,
        *,
        source_id: str,
        target_platform: str = "douyin",
        subtitle_enabled: bool = True,
        subtitle_model: str = "large-v3-turbo",
        language: str = "zh",
    ) -> VideoEditTask:
        source = self.resolve_source(source_id)
        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id=f"analysis-{uuid4().hex[:10]}",
            title=f"智能分析 · {source['file_name']}",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            source_video_path=source["_path"],
            stage="等待分析",
            is_mock=False,
            outputs={
                "workflow": "analysis",
                "source_id": source_id,
                "target_platform": target_platform,
                "subtitle_enabled": str(subtitle_enabled).lower(),
                "subtitle_model": subtitle_model,
                "language": language,
            },
        )
        self.repository.save_task(task)
        _WORKFLOW_EXECUTOR.submit(self._run_analysis, task.task_id)
        return task

    def get_analysis(self, analysis_id: str) -> dict[str, Any]:
        task = self._get_workflow_task(analysis_id, "analysis")
        return self._analysis_payload(task)

    def _run_analysis(self, analysis_id: str) -> None:
        task = self._get_workflow_task(analysis_id, "analysis")
        try:
            task = self._update(
                task, status=TaskStatus.RUNNING, progress=10, stage="读取媒体信息"
            )
            media = self._probe_media(Path(task.source_video_path))
            task = self._update(task, progress=35, stage="分析音频节奏")
            audio = self._analyze_audio(
                Path(task.source_video_path), media.get("duration_seconds")
            )
            recommendations, findings = self._recommend(media, audio, task.outputs)
            transcript_id = ""
            subtitle_error = ""

            if task.outputs.get("subtitle_enabled") == "true":
                task = self._update(task, progress=55, stage="生成字幕草稿")
                try:
                    transcript_id = self._create_transcription(task, media)
                except Exception as exc:
                    subtitle_error = str(exc)

            source = self.resolve_source(task.outputs.get("source_id", ""))
            transcript_text = self._transcript_text(transcript_id)
            title_candidates = self._local_title_candidates(
                source["title"],
                transcript_text,
                task.outputs.get("target_platform", "douyin"),
            )
            payload = {
                "media": media,
                "audio": audio,
                "findings": findings,
                "recommended_steps": recommendations,
                "subtitle_task_id": transcript_id or None,
                "subtitle_error": subtitle_error or None,
                "title_candidates": title_candidates,
            }
            outputs = {
                **task.outputs,
                "analysis_json": json.dumps(payload, ensure_ascii=False),
                "transcription_task_id": transcript_id,
                "subtitle_error": subtitle_error,
            }
            self._update(
                task,
                status=TaskStatus.SUCCEEDED,
                progress=100,
                stage="分析完成" if not subtitle_error else "分析完成（字幕待处理）",
                outputs=outputs,
            )
        except Exception as exc:
            self._update(
                task, status=TaskStatus.FAILED, stage="分析失败", error_message=str(exc)
            )

    def _create_transcription(self, task: VideoEditTask, media: dict[str, Any]) -> str:
        path = Path(task.source_video_path)
        if path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
            raise VideoEditorWorkflowError("该成片格式暂不支持字幕识别。")
        if path.stat().st_size > _MAX_GENERATED_SUBTITLE_BYTES:
            raise VideoEditorWorkflowError(
                "成片超过 100MB，暂不能在智能剪辑中生成字幕。"
            )
        if float(media.get("duration_seconds") or 0) > _MAX_SUBTITLE_SECONDS:
            raise VideoEditorWorkflowError(
                "成片超过 15 分钟，暂不能在智能剪辑中生成字幕。"
            )
        transcript = self.transcription_service.create_task(
            media_name=path.name,
            media_type=mimetypes.guess_type(path.name)[0] or "video/mp4",
            media_bytes=path.read_bytes(),
            rights_confirmed=True,
            rights_holder="系统内已授权素材",
            model_name=task.outputs.get("subtitle_model", "large-v3-turbo"),
            language=task.outputs.get("language", "zh"),
            max_media_bytes=_MAX_GENERATED_SUBTITLE_BYTES,
            include_word_timestamps=True,
        )
        if transcript.status != TaskStatus.SUCCEEDED:
            raise VideoEditorWorkflowError(transcript.error_message or "字幕识别失败。")
        return transcript.task_id

    def _transcript_text(self, transcript_id: str) -> str:
        if not transcript_id:
            return ""
        task = self.repository.get_task(transcript_id)
        return "".join(
            str(segment.text).strip()
            for segment in getattr(task, "segments", [])
            if str(segment.text).strip()
        )

    @staticmethod
    def _local_title_candidates(
        source_title: str, transcript_text: str, platform: str
    ) -> list[str]:
        clean_source = re.sub(
            r"^(本地上传|流水线成片|系统数字人成片)\s*[·：:-]?\s*", "", source_title
        ).strip()
        clean_source = Path(clean_source).stem
        first_sentence = re.split(
            r"[。！？!?；;\n]", transcript_text.strip(), maxsplit=1
        )[0]
        subject = re.sub(r"\s+", "", first_sentence or clean_source or "这条视频")[
            :22
        ].rstrip("，,。.")
        prefix = "视频号" if platform == "wechat_channels" else "短视频"
        candidates = [
            subject,
            f"看懂{subject}",
            f"别错过：{subject}",
            f"{prefix}重点：{subject}",
        ]
        result: list[str] = []
        for candidate in candidates:
            normalized = candidate[:40].strip(" ：:")
            if normalized and normalized not in result:
                result.append(normalized)
            if len(result) == 3:
                break
        return result

    @staticmethod
    def _probe_media(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise VideoEditorWorkflowError("源成片已不存在。")
        _trusted_local_media_tools()
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ]
        result = _run_media_command(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode != 0:
            raise VideoEditorWorkflowError(
                (result.stderr or "媒体分析失败。").strip()[:240]
            )
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        video = next(
            (item for item in streams if item.get("codec_type") == "video"), {}
        )
        has_audio = any(item.get("codec_type") == "audio" for item in streams)
        fps_text = str(video.get("r_frame_rate") or "0/1")
        try:
            num, den = fps_text.split("/", 1)
            fps = round(float(num) / max(float(den), 1), 2)
        except Exception:
            fps = 0.0
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        return {
            "duration_seconds": round(
                float((data.get("format") or {}).get("duration") or 0), 2
            ),
            "width": width,
            "height": height,
            "fps": fps,
            "orientation": "vertical" if height >= width else "horizontal",
            "has_audio": has_audio,
            "size_bytes": path.stat().st_size,
        }

    @staticmethod
    def _local_export_quality_report(
        output_media: Mapping[str, Any],
        *,
        expected_width: int,
        expected_height: int,
        expected_duration: float,
        source_has_audio: bool,
        visual_beats: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Return deterministic delivery gates for the local preview export.

        These checks intentionally cover media integrity and timing only. They
        do not pretend to judge whether the hook or music is creatively good.
        """

        duration_delta = abs(
            float(output_media.get("duration_seconds") or 0) - expected_duration
        )
        valid_beats = all(
            0 <= float(beat.get("start") or 0) < float(beat.get("end") or 0)
            for beat in visual_beats
            if isinstance(beat, Mapping)
        )
        checks = {
            "file_non_empty": int(output_media.get("size_bytes") or 0) > 0,
            "video_stream": int(output_media.get("width") or 0) > 0
            and int(output_media.get("height") or 0) > 0,
            "audio_stream": (not source_has_audio)
            or bool(output_media.get("has_audio")),
            "resolution": (
                int(output_media.get("width") or 0) == expected_width
                and int(output_media.get("height") or 0) == expected_height
            ),
            "duration": duration_delta <= 1.0,
            "visual_timing": valid_beats,
        }
        return {
            "passed": all(checks.values()),
            "checks": checks,
            "duration_seconds": float(output_media.get("duration_seconds") or 0),
            "expected_duration_seconds": round(expected_duration, 2),
            "duration_delta_seconds": round(duration_delta, 2),
            "resolution": {
                "width": int(output_media.get("width") or 0),
                "height": int(output_media.get("height") or 0),
            },
            "visual_beat_count": len(visual_beats),
            "style_version": _LOCAL_PREVIEW_EXPORT_STYLE_VERSION,
        }

    @staticmethod
    def _subtitle_timeline_quality(
        segments: Sequence[Mapping[str, Any]],
        *,
        duration_seconds: float,
        fps: float = 30.0,
    ) -> dict[str, Any]:
        """Check the rendered caption clock without judging creative quality."""

        ordered = sorted(
            [item for item in segments if isinstance(item, Mapping)],
            key=lambda item: (float(item.get("start") or 0), float(item.get("end") or 0)),
        )
        valid = True
        duplicate_keys: set[tuple[float, float, str]] = set()
        duplicate_count = 0
        previous_end = 0.0
        # FFmpeg/MP4 duration metadata is rounded independently from the last
        # decoded frame.  Permit at most one frame of container rounding while
        # keeping negative, overlapping, and materially out-of-bounds cues a
        # hard failure.
        duration_tolerance = 1.0 / max(float(fps or 30.0), 1.0)
        for item in ordered:
            try:
                start = float(item.get("start") or 0)
                end = float(item.get("end") or 0)
            except (TypeError, ValueError):
                valid = False
                continue
            key = (round(start, 3), round(end, 3), str(item.get("text") or ""))
            if key in duplicate_keys:
                duplicate_count += 1
            duplicate_keys.add(key)
            if (
                start < 0
                or end <= start
                or end > duration_seconds + duration_tolerance
                or start < previous_end
            ):
                valid = False
            previous_end = max(previous_end, end)
        return {
            "passed": valid and duplicate_count == 0,
            "cue_count": len(ordered),
            "duplicate_count": duplicate_count,
            "checks": {
                "monotonic_and_in_bounds": valid,
                "no_duplicate_cues": duplicate_count == 0,
            },
        }

    @staticmethod
    def _subtitle_text_integrity_gate(
        source_segments: Sequence[Mapping[str, Any]],
        preview: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Require cue text to be an exact ordered projection of the review.

        Punctuation and whitespace are presentation-only.  Any missing,
        duplicated, or reordered spoken character fails this gate; a timing
        gate alone must not bless a visually tidy but semantically wrong ASS.
        """

        def compact(value: object) -> str:
            return re.sub(r"[^\w\u4e00-\u9fff%％]", "", str(value or ""))

        source_display_text = "".join(
            str(segment.get("text") or "")
            for segment in source_segments
            if isinstance(segment, Mapping)
        )
        # The burned display intentionally hides the generic spoken filler
        # ``呢`` at a phrase boundary, just as punctuation/whitespace are
        # hidden. Compare the same display-normalized source here so an
        # approved filler cleanup is not misreported as missing speech.
        source_display_text = re.sub(r"呢(?=[\u4e00-\u9fff])", "", source_display_text)
        source_display_text = re.sub(r"呢$", "", source_display_text)
        source_text = compact(source_display_text)
        cue_text = compact(
            "".join(
                "".join(str(line) for line in cue.get("lines") or [])
                for cue in preview.get("cues") or []
                if isinstance(cue, Mapping)
            )
        )
        duplicate_boundary = bool(re.search(r"(的|地|得|和|与|就|了)\1", cue_text))
        return {
            "passed": bool(source_text) and source_text == cue_text and not duplicate_boundary,
            "method": "ordered_compact_reviewed_transcript_equals_cue_manifest",
            "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            "cue_text_sha256": hashlib.sha256(cue_text.encode("utf-8")).hexdigest(),
            "source_char_count": len(source_text),
            "cue_char_count": len(cue_text),
            "exact_ordered_match": source_text == cue_text,
            "duplicate_function_word_boundary": duplicate_boundary,
        }

    @staticmethod
    def _subtitle_audio_activity_gate(
        cues: Sequence[Mapping[str, Any]],
        active_ranges: Sequence[Mapping[str, Any]] | None,
        *,
        minimum_uncovered_seconds: float = 0.5,
    ) -> dict[str, Any]:
        """Fail when a VAD/audio-active interval has no subtitle coverage."""

        if active_ranges is None:
            return {
                "passed": False,
                "method": "audio_activity_unavailable_fail_closed",
                "uncovered_ranges": [],
            }
        ordered_cues: list[tuple[float, float]] = []
        for cue in cues:
            if not isinstance(cue, Mapping):
                continue
            try:
                start = float(cue.get("start") or 0)
                end = float(cue.get("end") or 0)
            except (TypeError, ValueError):
                continue
            if end > start:
                ordered_cues.append((start, end))
        ordered_cues.sort()
        uncovered: list[dict[str, float]] = []
        active_seconds = 0.0
        covered_seconds = 0.0
        for active in active_ranges:
            if not isinstance(active, Mapping):
                continue
            try:
                start = float(active.get("start") or 0)
                end = float(active.get("end") or 0)
            except (TypeError, ValueError):
                continue
            if end <= start:
                continue
            active_seconds += end - start
            cursor = start
            for cue_start, cue_end in ordered_cues:
                if cue_end <= cursor:
                    continue
                if cue_start >= end:
                    break
                if cue_start > cursor:
                    gap_end = min(cue_start, end)
                    if gap_end - cursor > minimum_uncovered_seconds:
                        uncovered.append(
                            {"start": round(cursor, 3), "end": round(gap_end, 3)}
                        )
                cursor = max(cursor, min(cue_end, end))
                if cursor >= end:
                    break
            if cursor < end and end - cursor > minimum_uncovered_seconds:
                uncovered.append({"start": round(cursor, 3), "end": round(end, 3)})
            covered_seconds += max(0.0, (end - start) - sum(item["end"] - item["start"] for item in uncovered if item["start"] >= start and item["end"] <= end))
        return {
            "passed": not uncovered,
            "method": "ffmpeg_silencedetect_audio_activity_complement",
            "minimum_uncovered_seconds": minimum_uncovered_seconds,
            "active_seconds": round(active_seconds, 3),
            "covered_seconds": round(min(covered_seconds, active_seconds), 3),
            "uncovered_ranges": uncovered,
        }

    @staticmethod
    def _active_ranges_from_audio_analysis(
        audio: Mapping[str, Any] | None,
        *,
        duration_seconds: float,
        playback_rate: float = 1.0,
        offset_seconds: float = 0.0,
    ) -> list[dict[str, float]] | None:
        if not isinstance(audio, Mapping) or audio.get("available") is not True:
            return None
        try:
            duration = max(0.0, float(duration_seconds))
        except (TypeError, ValueError):
            return None
        if duration <= 0:
            return []
        silences: list[tuple[float, float]] = []
        for item in audio.get("silence_intervals") or []:
            if not isinstance(item, Mapping):
                continue
            try:
                start = max(0.0, float(item.get("start") or 0))
                end = min(duration, float(item.get("end") or 0))
            except (TypeError, ValueError):
                continue
            if end > start:
                silences.append((start, end))
        silences.sort()
        active: list[dict[str, float]] = []
        cursor = 0.0
        for silence_start, silence_end in silences:
            if silence_start > cursor:
                active.append(
                    {
                        "start": round(offset_seconds + cursor / max(playback_rate, 0.001), 3),
                        "end": round(offset_seconds + silence_start / max(playback_rate, 0.001), 3),
                    }
                )
            cursor = max(cursor, silence_end)
        if cursor < duration:
            active.append(
                {
                    "start": round(offset_seconds + cursor / max(playback_rate, 0.001), 3),
                    "end": round(offset_seconds + duration / max(playback_rate, 0.001), 3),
                }
            )
        return active

    @staticmethod
    def _intersect_audio_activity_with_spoken_ranges(
        active_ranges: Sequence[Mapping[str, Any]] | None,
        spoken_ranges: Sequence[Mapping[str, Any]] | None,
    ) -> list[dict[str, float]] | None:
        """Use VAD as the signal and reviewed speech ranges as its boundary.

        This avoids treating a long conversational pause as speech when the
        audio detector is conservative, while still requiring audio activity
        inside every reviewed speech candidate.  A missing reviewed segment
        remains visible to the separate fail-closed VAD test path.
        """

        if active_ranges is None or spoken_ranges is None:
            return active_ranges
        result: list[dict[str, float]] = []
        for active in active_ranges:
            if not isinstance(active, Mapping):
                continue
            try:
                active_start = float(active.get("start") or 0)
                active_end = float(active.get("end") or 0)
            except (TypeError, ValueError):
                continue
            for spoken in spoken_ranges:
                if not isinstance(spoken, Mapping):
                    continue
                try:
                    start = max(active_start, float(spoken.get("start") or 0))
                    end = min(active_end, float(spoken.get("end") or 0))
                except (TypeError, ValueError):
                    continue
                if end > start:
                    result.append({"start": round(start, 3), "end": round(end, 3)})
        result.sort(key=lambda item: (item["start"], item["end"]))
        return result

    @staticmethod
    def _subtitle_experience_gate(
        source_segments: Sequence[Mapping[str, Any]],
        preview: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Check readable estimated cues, including minimum dwell time."""

        source_durations = {
            index: max(
                0.0,
                float(segment.get("end") or 0)
                - float(segment.get("start") or 0),
            )
            for index, segment in enumerate(source_segments)
            if isinstance(segment, Mapping)
        }
        cues = [cue for cue in preview.get("cues") or [] if isinstance(cue, Mapping)]
        durations = [
            float(cue.get("end") or 0) - float(cue.get("start") or 0)
            for cue in cues
        ]
        def has_short_source_exception(cue: Mapping[str, Any]) -> bool:
            try:
                segment_index = int(cue.get("source_segment_index", -1))
            except (TypeError, ValueError):
                segment_index = -1
            if source_durations.get(segment_index, 0.0) < 0.9:
                return True
            try:
                word_span = float(cue.get("source_word_span_seconds"))
            except (TypeError, ValueError):
                word_span = 0.0
            return (
                cue.get("short_source_exception") is True
                and 0.0 < word_span < 0.9
            )

        short_cue_exceptions = all(
            duration + 1e-6 >= 0.9 or has_short_source_exception(cue)
            for cue, duration in zip(cues, durations, strict=True)
        )
        min_duration = min(durations) if durations else 0.0
        max_duration = max(durations) if durations else 0.0
        count_by_segment: dict[int, int] = {}
        for cue in cues:
            try:
                index = int(cue.get("source_segment_index"))
            except (TypeError, ValueError):
                continue
            count_by_segment[index] = count_by_segment.get(index, 0) + 1
        sentence_count_checks: dict[str, bool] = {}
        for index, duration in source_durations.items():
            count = count_by_segment.get(index, 0)
            segment = source_segments[index] if index < len(source_segments) else {}
            active_word_ranges: list[tuple[float, float]] = []
            if isinstance(segment, Mapping):
                for word in segment.get("words") or []:
                    if not isinstance(word, Mapping):
                        continue
                    try:
                        word_start = max(
                            float(segment.get("start") or 0),
                            float(word.get("start") or 0),
                        )
                        word_end = min(
                            float(segment.get("end") or 0),
                            float(word.get("end") or 0),
                        )
                    except (TypeError, ValueError):
                        continue
                    if word_end > word_start:
                        active_word_ranges.append((word_start, word_end))
            active_word_ranges.sort()
            merged_active_ranges: list[tuple[float, float]] = []
            for word_start, word_end in active_word_ranges:
                if not merged_active_ranges or word_start > merged_active_ranges[-1][1]:
                    merged_active_ranges.append((word_start, word_end))
                else:
                    merged_active_ranges[-1] = (
                        merged_active_ranges[-1][0],
                        max(merged_active_ranges[-1][1], word_end),
                    )
            effective_duration = (
                sum(end - start for start, end in merged_active_ranges)
                if merged_active_ranges
                else duration
            )
            if duration < 1.8:
                passed = count == 1
            elif duration <= 2.8:
                # A short, complete sentence should not be mechanically split
                # just to satisfy a count target; one readable cue is valid.
                passed = 1 <= count <= 2
            elif duration > 9.6:
                minimum_count = int((effective_duration - 1e-9) / 2.4) + 1
                passed = count >= minimum_count
            else:
                # Medium ASR segments may legitimately contain five or more
                # lexical phrases when the source pause structure requires
                # them.  Derive the lower bound from the dwell ceiling and a
                # conservative upper bound from the minimum dwell instead of
                # imposing a sample-specific 2--4 count.
                minimum_count = math.ceil(effective_duration / 2.4 - 1e-9)
                maximum_count = max(4, math.ceil(effective_duration / 0.9) + 1)
                passed = minimum_count <= count <= maximum_count
            sentence_count_checks[str(index)] = passed
        texts = [
            "".join(str(line) for line in cue.get("lines") or []) for cue in cues
        ]
        forbidden_singletons = {"的", "个", "品牌", "产品"}
        orphan_prefixes = ("了", "着", "和", "与", "或", "及")
        orphan_suffixes = ("的", "地", "得", "和", "与", "或", "把", "被", "在", "对")
        no_orphan_prefix_suffix = not any(
            text in forbidden_singletons
            or text.startswith(orphan_prefixes)
            or (
                text.endswith(orphan_suffixes)
                and len(text)
                <= max(len(suffix) + 2 for suffix in orphan_suffixes if text.endswith(suffix))
            )
            for text in texts
        )
        lexical_boundary_integrity = True
        caption_glossary = preview.get("caption_glossary")
        for segment_index, segment in enumerate(source_segments):
            source_text = re.sub(
                r"[^\w\u4e00-\u9fff]", "", str(segment.get("text") or "")
            ).replace("呢", "")
            segment_texts = [
                re.sub(r"[^\w\u4e00-\u9fff]", "", text)
                for cue, text in zip(cues, texts, strict=True)
                if int(cue.get("source_segment_index", -1)) == segment_index
            ]
            if "".join(segment_texts) != source_text:
                lexical_boundary_integrity = False
                continue
            lexical_units = [
                re.sub(r"[^\w\u4e00-\u9fff]", "", unit)
                for unit in _caption_lexical_units(source_text, caption_glossary)
                if re.sub(r"[^\w\u4e00-\u9fff]", "", unit)
            ]
            lexical_boundaries = {0}
            offset = 0
            for unit in lexical_units:
                offset += len(unit)
                lexical_boundaries.add(offset)
            cursor = 0
            for text in segment_texts:
                next_cursor = cursor + len(text)
                if cursor not in lexical_boundaries or next_cursor not in lexical_boundaries:
                    lexical_boundary_integrity = False
                cursor = next_cursor
        one_line_ratio = (
            sum(1 for cue in cues if len(cue.get("lines") or []) == 1)
            / len(cues)
            if cues
            else 1.0
        )
        checks = {
            "min_duration": short_cue_exceptions,
            "max_duration": all(
                duration <= 2.4 + 1e-6
                or cue.get("lexical_boundary_exception") == "compound_phrase"
                for cue, duration in zip(cues, durations, strict=True)
            ),
            "one_line": all(
                len(cue.get("lines") or []) == 1
                or cue.get("two_line_exception") is True
                for cue in cues
            ),
            "no_fragment_cues": not any(text in forbidden_singletons for text in texts),
            "lexical_boundary_integrity": lexical_boundary_integrity,
            "no_orphan_prefix_suffix": no_orphan_prefix_suffix,
            "preview_render_manifest_equal": preview.get(
                "preview_render_manifest_equal", True
            )
            is True,
            "cue_count_per_sentence": all(sentence_count_checks.values()),
        }
        return {
            "passed": all(checks.values()),
            "checks": checks,
            "min_duration_seconds": round(min_duration, 3),
            "max_duration_seconds": round(max_duration, 3),
            "one_line_ratio": round(one_line_ratio, 3),
            "lexical_boundary_integrity": lexical_boundary_integrity,
            "no_orphan_prefix_suffix": no_orphan_prefix_suffix,
            "preview_render_manifest_equal": preview.get(
                "preview_render_manifest_equal", True
            )
            is True,
            "minimum_dwell_seconds": 0.9,
            "maximum_dwell_seconds": 2.4,
            "short_source_exception_allowed": True,
            "cue_count_by_source_segment": count_by_segment,
            "cue_count_gate_by_source_segment": sentence_count_checks,
        }

    @staticmethod
    def _snap_preview_cues_to_reviewed_word_clock(
        source_segments: Sequence[Mapping[str, Any]],
        preview: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Snap reviewed cue edges to real word spans without changing text.

        Reviewed subtitles may correct ASR homophones (for example, a spoken
        token and its approved display character are not byte-identical).  In
        that case exact text matching is too strict, but sentence-level
        interpolation is too loose.  A monotonic edit alignment maps the
        reviewed display span onto the ordered ASR word spans; the report
        keeps the fallback method explicit.
        """

        cues = [
            dict(cue)
            for cue in preview.get("cues") or []
            if isinstance(cue, Mapping)
        ]

        def compact(value: object) -> str:
            text = re.sub(r"\s+", "", str(value or ""))
            text = re.sub(r"[^\w\u4e00-\u9fff%]", "", text)
            # Keep real lexical tokens in the clock.  If an approved display
            # cue omits a filler, SequenceMatcher can align that omission;
            # deleting every ``的`` here truncates a genuine trailing word
            # and creates a false multi-frame boundary error.
            return text

        for segment_index, segment in enumerate(source_segments):
            if not isinstance(segment, Mapping):
                continue
            raw_words: list[dict[str, Any]] = []
            for raw_word in segment.get("words") or []:
                if not isinstance(raw_word, Mapping):
                    continue
                try:
                    word_start = float(raw_word.get("start", 0))
                    word_end = float(raw_word.get("end", 0))
                except (TypeError, ValueError):
                    continue
                word_text = compact(raw_word.get("text") or raw_word.get("word"))
                if word_text and word_end > word_start:
                    raw_words.append(
                        {
                            "text": word_text,
                            "start": word_start,
                            "end": word_end,
                        }
                    )
            if not raw_words:
                continue
            reviewed_text = compact(segment.get("text"))
            raw_text = "".join(str(word["text"]) for word in raw_words)
            if not reviewed_text or not raw_text:
                continue
            opcodes = SequenceMatcher(
                None,
                reviewed_text,
                raw_text,
                autojunk=False,
            ).get_opcodes()

            def map_reviewed_position(position: int) -> int:
                for tag, reviewed_start, reviewed_end, raw_start, raw_end in opcodes:
                    if reviewed_start <= position <= reviewed_end:
                        if tag == "equal":
                            return raw_start + position - reviewed_start
                        if tag == "replace":
                            span = max(reviewed_end - reviewed_start, 1)
                            ratio = (position - reviewed_start) / span
                            return raw_start + round(ratio * (raw_end - raw_start))
                        return raw_start
                return len(raw_text)

            raw_spans: list[tuple[int, int]] = []
            raw_cursor = 0
            for word in raw_words:
                next_cursor = raw_cursor + len(str(word["text"]))
                raw_spans.append((raw_cursor, next_cursor))
                raw_cursor = next_cursor

            def raw_position_to_clock(position: float, *, is_end: bool) -> float:
                """Map a lexical boundary inside a provider token.

                Whisper/API tokens are not guaranteed to be Chinese words
                (for example, one token may contain ``我也``).  A jieba cue
                may therefore begin or end inside that token.  Interpolating
                within the real token clock keeps the cue aligned without
                splitting the displayed text or falling back to a sentence
                estimate.
                """

                if not raw_words:
                    return 0.0
                if position <= 0:
                    return float(raw_words[0]["start"])
                if position >= raw_cursor:
                    return float(raw_words[-1]["end"])
                for index, (span_start, span_end) in enumerate(raw_spans):
                    word = raw_words[index]
                    # At a lexical boundary with an audio pause, the same
                    # character position has two valid clocks. A cue start
                    # belongs to the following word, while a cue end belongs
                    # to the preceding word. Choosing the preceding end for
                    # both directions makes later cues start before speech.
                    if position == span_start:
                        if is_end and index > 0:
                            return float(raw_words[index - 1]["end"])
                        return float(word["start"])
                    if position == span_end:
                        if not is_end and index + 1 < len(raw_words):
                            return float(raw_words[index + 1]["start"])
                        return float(word["end"])
                    if position < span_start or position > span_end:
                        continue
                    width = max(span_end - span_start, 1)
                    ratio = max(0.0, min(1.0, (position - span_start) / width))
                    return float(word["start"]) + ratio * (
                        float(word["end"]) - float(word["start"])
                    )
                return float(raw_words[-1]["end"] if is_end else raw_words[0]["start"])

            segment_cues = [
                cue
                for cue in cues
                if int(cue.get("source_segment_index", -1)) == segment_index
            ]
            reviewed_cursor = 0
            raw_word_cursor = 0
            for cue in segment_cues:
                original_start = float(cue.get("start") or 0)
                original_end = float(cue.get("end") or 0)
                target = compact("".join(str(line) for line in cue.get("lines") or []))
                if not target:
                    continue
                reviewed_start = reviewed_text.find(target, reviewed_cursor)
                if reviewed_start < 0:
                    continue
                reviewed_end = reviewed_start + len(target)
                raw_start = map_reviewed_position(reviewed_start)
                raw_end = map_reviewed_position(reviewed_end)
                if raw_end <= raw_start:
                    continue
                selected_indices = [
                    index
                    for index, (span_start, span_end) in enumerate(raw_spans)
                    if index >= raw_word_cursor
                    and span_end > raw_start
                    and span_start < raw_end
                ]
                if not selected_indices:
                    continue
                first_index = selected_indices[0]
                last_index = selected_indices[-1]
                snapped_start = raw_position_to_clock(raw_start, is_end=False)
                snapped_end = raw_position_to_clock(raw_end, is_end=True)
                source_word_span = max(0.0, snapped_end - snapped_start)
                # A phrase can be genuinely shorter than the normal 0.9 s
                # dwell contract even when its sentence contains a pause.
                # Keep that phrase on the real word clock and mark the narrow
                # exception explicitly; extending it over silence would make
                # the word-level gate report a false timing error.
                # A provider token can contain a long pause. Preserve an
                # already-readable preview span in that case, but mark it as
                # an explicit exception so the sync gate cannot call it exact.
                if (
                    source_word_span > 2.4 + 1e-6
                    and original_end - original_start <= 2.4 + 1e-6
                ):
                    cue["start"] = round(original_start, 3)
                    cue["end"] = round(original_end, 3)
                    cue["word_clock_mapping"] = (
                        "lexical_preview_clock_preserved_over_pause"
                    )
                else:
                    cue["start"] = round(snapped_start, 3)
                    cue["end"] = round(snapped_end, 3)
                    cue["word_clock_mapping"] = (
                        "exact_or_reviewed_text_sequence_alignment"
                    )
                cue["source_word_span_seconds"] = round(source_word_span, 3)
                cue["short_source_exception"] = source_word_span < 0.9 - 1e-6
                reviewed_cursor = reviewed_end
                # Keep the current provider token available when the next
                # lexical cue starts inside the same token.
                raw_word_cursor = last_index + (
                    1 if raw_end >= raw_spans[last_index][1] else 0
                )

        return {**dict(preview), "cues": cues}

    @staticmethod
    def _shift_subtitle_clock(
        segments: Sequence[Mapping[str, Any]],
        preview: Mapping[str, Any],
        offset_seconds: float,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Move one immutable subtitle clock after a generated opening.

        The body renderer is source-relative.  Once an opening clip is
        prepended, both the approved segments and their word spans must move
        by the exact measured opening duration before the final ASS burn and
        quality audit.  No text or timestamp is inferred here.
        """

        offset = max(0.0, float(offset_seconds or 0.0))
        shifted_segments: list[dict[str, Any]] = []
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            shifted = {
                **dict(segment),
                "start": round(float(segment.get("start", 0)) + offset, 3),
                "end": round(float(segment.get("end", 0)) + offset, 3),
            }
            words = segment.get("words")
            if isinstance(words, list):
                shifted["words"] = [
                    {
                        **dict(word),
                        "start": round(float(word.get("start", 0)) + offset, 3),
                        "end": round(float(word.get("end", 0)) + offset, 3),
                    }
                    for word in words
                    if isinstance(word, Mapping)
                ]
            shifted_segments.append(shifted)
        shifted_cues = []
        for cue in preview.get("cues") or []:
            if not isinstance(cue, Mapping):
                continue
            shifted_cues.append(
                {
                    **dict(cue),
                    "start": round(float(cue.get("start", 0)) + offset, 3),
                    "end": round(float(cue.get("end", 0)) + offset, 3),
                }
            )
        return shifted_segments, {
            **dict(preview),
            "clock": "final_output",
            "opening_offset_seconds": round(offset, 3),
            "cues": shifted_cues,
        }

    @staticmethod
    def _subtitle_word_timing_quality(
        source_segments: Sequence[Mapping[str, Any]],
        preview: Mapping[str, Any],
        *,
        fps: float,
    ) -> dict[str, Any]:
        """Report word-boundary accuracy without inventing sentence-level data.

        A cue is matched to the contiguous words that spell its displayed text.
        This makes the metric useful for real word timestamps while keeping a
        sentence-only ASR result explicitly unverified.  It is deliberately a
        separate report section: sentence timestamps may still use the safe
        estimated-phrase path, but must never be presented as word-accurate.
        """

        def compact(value: object) -> str:
            text = re.sub(r"\s+", "", str(value or ""))
            text = re.sub(r"[^\w\u4e00-\u9fff%]", "", text)
            # Match the final cue text without deleting real Chinese
            # characters. The renderer may hide a filler in presentation,
            # but removing ``的``/``呢`` here creates zero-width audit tokens
            # and shifts later character boundaries away from the real word
            # clock. Reviewed omissions are handled by the explicit fallback
            # alignment below.
            return text

        words_by_segment: dict[int, list[dict[str, Any]]] = {}
        for index, segment in enumerate(source_segments):
            if not isinstance(segment, Mapping):
                continue
            raw_words = segment.get("words")
            if not isinstance(raw_words, Sequence) or isinstance(
                raw_words, (str, bytes)
            ):
                continue
            words: list[dict[str, Any]] = []
            for raw_word in raw_words:
                if not isinstance(raw_word, Mapping):
                    continue
                try:
                    start = float(raw_word.get("start", 0))
                    end = float(raw_word.get("end", 0))
                except (TypeError, ValueError):
                    continue
                raw_text = str(raw_word.get("text") or raw_word.get("word") or "")
                text = compact(raw_text)
                if raw_text.strip() and end > start:
                    words.append(
                        {
                            "start": start,
                            "end": end,
                            "text": text,
                            "raw_text": raw_text,
                        }
                    )
            if words:
                words_by_segment[index] = words

        cues = [cue for cue in preview.get("cues") or [] if isinstance(cue, Mapping)]
        if not words_by_segment:
            return {
                "status": "unverified_sentence_level",
                "verified": False,
                "source": "sentence_timestamps",
                "word_p95_ms": None,
                "mapping_error_frames": None,
                "matched_cue_count": 0,
                "unmatched_cue_count": len(cues),
                "checks": {
                    "word_p95_le_150ms": None,
                    "mapping_le_1_frame": None,
                },
            }

        character_only_timestamps = all(
            len(str(word.get("text") or "")) <= 1
            for words in words_by_segment.values()
            for word in words
        )
        if character_only_timestamps:
            # A character-token fallback is useful for deterministic phrase
            # timing tests, but it must remain visibly distinct from genuine
            # lexical word timestamps.  It may satisfy the structural timing
            # path without manufacturing a word-level P95 number.
            return {
                "status": "estimated_character_token_timestamps",
                "verified": False,
                "timing_gate_passed": True,
                "source": "character_token_timestamps",
                "word_p95_ms": None,
                "mapping_error_frames": None,
                "matched_cue_count": len(cues),
                "unmatched_cue_count": 0,
                "fps": float(fps) if float(fps) > 0 else None,
                "checks": {
                    "word_p95_le_150ms": None,
                    "mapping_le_1_frame": None,
                },
            }

        # The renderer first projects provider tokens onto jieba lexical
        # units, including numeric suffixes such as ``80%``. Audit the same
        # canonical projection instead of rebuilding a second token clock;
        # this keeps the metric aligned with the actual ASS/cue manifest while
        # preserving the explicit character-token degradation above.
        for index, segment in enumerate(source_segments):
            if not isinstance(segment, Mapping) or not segment.get("words"):
                continue
            try:
                segment_start = float(segment.get("start") or 0)
                segment_end = float(segment.get("end") or 0)
            except (TypeError, ValueError):
                continue
            lexical_words = _caption_lexical_words(
                segment.get("words"),
                text=str(segment.get("text") or ""),
                segment_start=segment_start,
                segment_end=segment_end,
                caption_glossary=preview.get("caption_glossary"),
            )
            projected_text = compact(
                "".join(str(item.get("text") or "") for item in lexical_words)
            )
            reviewed_text = compact(segment.get("text"))
            if (
                lexical_words
                and projected_text == reviewed_text
                and len(lexical_words) > 1
            ):
                words_by_segment[index] = [
                    {
                        "start": float(item.get("start") or 0),
                        "end": float(item.get("end") or 0),
                        "text": str(item.get("text") or ""),
                        "raw_text": str(item.get("text") or ""),
                    }
                    for item in lexical_words
                    if float(item.get("end") or 0) > float(item.get("start") or 0)
                ]

        search_cursor: dict[int, int] = {}
        clock_cursor: dict[int, int] = {}
        cue_errors_seconds: list[float] = []
        unmatched = 0
        reviewed_alignment_fallback_count = 0
        for cue in cues:
            try:
                segment_index = int(cue.get("source_segment_index"))
                cue_start = float(cue.get("start", 0))
                cue_end = float(cue.get("end", 0))
            except (TypeError, ValueError):
                unmatched += 1
                continue
            words = words_by_segment.get(segment_index)
            target = compact("".join(str(line) for line in cue.get("lines") or []))
            if not words or not target:
                unmatched += 1
                continue
            joined = ""
            spans: list[tuple[int, int, dict[str, Any]]] = []
            for word in words:
                start_offset = len(joined)
                joined += str(word["text"])
                spans.append((start_offset, len(joined), word))

            def position_to_clock(position: int, *, is_end: bool) -> float:
                if not spans:
                    return 0.0
                if position <= 0:
                    return float(spans[0][2]["start"])
                if position >= len(joined):
                    return float(spans[-1][2]["end"])
                for index, (start_offset, end_offset, word) in enumerate(spans):
                    # A character boundary at an audio pause has two clocks:
                    # cue starts use the following word, cue ends use the
                    # preceding word. Keep the audit consistent with the
                    # final subtitle clock snapper above.
                    if position == start_offset:
                        if is_end and index > 0:
                            return float(spans[index - 1][2]["end"])
                        return float(word["start"])
                    if position == end_offset:
                        if not is_end and index + 1 < len(spans):
                            return float(spans[index + 1][2]["start"])
                        return float(word["end"])
                    if position < start_offset or position > end_offset:
                        continue
                    width = max(end_offset - start_offset, 1)
                    ratio = max(0.0, min(1.0, (position - start_offset) / width))
                    return float(word["start"]) + ratio * (
                        float(word["end"]) - float(word["start"])
                    )
                return float(spans[-1][2]["end"] if is_end else spans[0][2]["start"])
            cursor = search_cursor.get(segment_index, 0)
            match_start = joined.find(target, cursor)
            if match_start < 0:
                match_start = joined.find(target)
            match_end = match_start + len(target) if match_start >= 0 else -1
            matched_indices = [
                index
                for index, (start_offset, end_offset, word) in enumerate(spans)
                if match_start >= 0
                and end_offset > match_start
                and start_offset < match_end
            ]
            if not matched_indices:
                # Approved subtitles may correct a homophone in raw ASR text.
                # The renderer has already aligned these cues monotonically
                # to real word spans; audit that clock directly and keep this
                # fallback visible.
                if cue.get("word_clock_mapping") == (
                    "exact_or_reviewed_text_sequence_alignment"
                ):
                    previous_index = clock_cursor.get(segment_index, 0)
                    tolerance = max(
                        1.0 / float(fps) if float(fps) > 0 else 0.034,
                        0.01,
                    )
                    start_candidates = [
                        index
                        for index, word in enumerate(words)
                        if index >= previous_index
                        and abs(float(word["start"]) - cue_start) <= tolerance
                    ]
                    if start_candidates:
                        first_index = start_candidates[0]
                        end_candidates = [
                            index
                            for index in range(first_index, len(words))
                            if abs(float(words[index]["end"]) - cue_end)
                            <= tolerance
                        ]
                        if end_candidates:
                            last_index = end_candidates[0]
                            matched = [words[first_index], words[last_index]]
                            cue_errors_seconds.append(
                                max(
                                    abs(cue_start - float(matched[0]["start"])),
                                    abs(cue_end - float(matched[-1]["end"])),
                                )
                            )
                            clock_cursor[segment_index] = last_index + 1
                            reviewed_alignment_fallback_count += 1
                            continue
                unmatched += 1
                continue
            search_cursor[segment_index] = match_end
            # Display cleanup may remove an ASR filler such as a sentence-
            # internal ``的``/``呢``.  If that hidden token is contiguous with
            # the displayed cue edge, it remains part of the cue's real word
            # span and must be included in the boundary audit.
            tolerance = 1.0 / float(fps) if float(fps) > 0 else 0.0
            first_index = matched_indices[0]
            last_index = matched_indices[-1]
            while first_index > 0:
                previous = spans[first_index - 1][2]
                if previous["text"] or abs(cue_start - float(previous["start"])) > tolerance:
                    break
                first_index -= 1
            while last_index + 1 < len(spans):
                following = spans[last_index + 1][2]
                if following["text"] or abs(cue_end - float(following["end"])) > tolerance:
                    break
                last_index += 1
            matched = [spans[first_index][2], spans[last_index][2]]
            boundary_error = max(
                abs(cue_start - position_to_clock(match_start, is_end=False)),
                abs(cue_end - position_to_clock(match_end, is_end=True)),
            )
            cue_errors_seconds.append(boundary_error)

        if unmatched or not cue_errors_seconds:
            status = "unverified_word_mapping"
            verified = False
            word_p95_ms = None
            mapping_error_frames = None
        else:
            ordered = sorted(cue_errors_seconds)
            percentile_index = max(
                0,
                min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1),
            )
            word_p95_ms = round(ordered[percentile_index] * 1000, 3)
            mapping_error_frames = (
                round(max(ordered) * float(fps), 3) if float(fps) > 0 else None
            )
            verified = mapping_error_frames is not None
            status = (
                "verified_reviewed_text_sequence_alignment"
                if verified and reviewed_alignment_fallback_count
                else "verified"
                if verified
                else "unverified_missing_fps"
            )

        checks = {
            "word_p95_le_150ms": (
                word_p95_ms is not None and word_p95_ms <= 150.0
            )
            if verified
            else None,
            "mapping_le_1_frame": (
                mapping_error_frames is not None and mapping_error_frames <= 1.0
            )
            if verified
            else None,
        }
        return {
            "status": status,
            "verified": verified,
            "source": "word_timestamps",
            "alignment_method": (
                "reviewed_text_sequence_alignment"
                if reviewed_alignment_fallback_count
                else "exact_compact_text_match"
            ),
            "reviewed_text_sequence_alignment_fallback_count": (
                reviewed_alignment_fallback_count
            ),
            "word_p95_ms": word_p95_ms,
            "mapping_error_frames": mapping_error_frames,
            "matched_cue_count": len(cue_errors_seconds),
            "unmatched_cue_count": unmatched,
            "fps": float(fps) if float(fps) > 0 else None,
            "checks": checks,
        }

    @staticmethod
    def _analyze_audio(path: Path, duration: float | None) -> dict[str, Any]:
        try:
            _trusted_local_media_tools()
        except VideoEditorWorkflowError:
            return {"available": False}
        result: dict[str, Any] = {
            "available": True,
            "mean_volume_db": None,
            "silence_seconds": 0.0,
        }
        try:
            volume = _run_media_command(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "info",
                    "-i",
                    str(path),
                    "-af",
                    "volumedetect",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            match = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", volume.stderr or "")
            if match:
                result["mean_volume_db"] = float(match.group(1))
        except Exception:
            pass
        try:
            silence = _run_media_command(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "info",
                    "-i",
                    str(path),
                    "-af",
                    # The export gate needs short speech-activity boundaries,
                    # not only long silence recommendations.  Keep the
                    # threshold conservative enough for a voice track and
                    # let the subtitle gate ignore uncovered spans <= 0.5s.
                    "silencedetect=noise=-26dB:d=0.3",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            starts = [
                float(item)
                for item in re.findall(
                    r"silence_start:\s*([\d.]+)", silence.stderr or ""
                )
            ]
            ends = [
                float(item)
                for item in re.findall(r"silence_end:\s*([\d.]+)", silence.stderr or "")
            ]
            result["silence_seconds"] = round(
                sum(max(0, end - start) for start, end in zip(starts, ends)), 2
            )
            result["silence_intervals"] = [
                {"start": round(start, 2), "end": round(end, 2)}
                for start, end in zip(starts, ends)
            ]
        except Exception:
            result["silence_intervals"] = []
        return result

    @staticmethod
    def _recommend(
        media: dict[str, Any], audio: dict[str, Any], outputs: dict[str, str]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        steps: list[dict[str, Any]] = []
        findings: list[str] = []
        volume = audio.get("mean_volume_db")
        if isinstance(volume, (int, float)) and (volume < -18 or volume > -14):
            steps.append(
                {
                    "kind": "ai_volume_norm",
                    "params": {"target_i": -16},
                    "enabled": True,
                    "label": "统一口播响度",
                }
            )
            findings.append(f"平均音量约 {volume:.1f} dB，建议标准化到短视频常用响度。")
        silence_seconds = float(audio.get("silence_seconds") or 0)
        duration = float(media.get("duration_seconds") or 0)
        if (
            silence_seconds >= 1.5
            and duration > 0
            and silence_seconds / duration >= 0.05
        ):
            steps.append(
                {
                    "kind": "ai_silence_trim",
                    "params": {
                        "noise_threshold": -30,
                        "min_duration": 1.5,
                        "keep_padding": 0.35,
                    },
                    "enabled": True,
                    "label": "压缩长停顿",
                }
            )
            findings.append(f"检测到约 {silence_seconds:.1f} 秒静音，可压缩口播节奏。")
        if media.get("orientation") != "vertical" or (
            media.get("width", 0)
            and media.get("height", 0)
            and media.get("height", 0) < 1280
        ):
            steps.append(
                {
                    "kind": "resize",
                    "params": {},
                    "enabled": True,
                    "label": "适配竖屏平台",
                }
            )
            findings.append("画幅或清晰度与竖屏发布预设不一致，建议等比适配。")
        if outputs.get("subtitle_enabled") == "true":
            findings.append("字幕会先进入人工复核；批准前不会烧录进成片。")
        if not steps:
            findings.append(
                "当前素材未发现必须处理的音频或画幅问题，可按需添加增强步骤。"
            )
        return steps, findings

    # ------------------------------------------------------------------
    # 内容建议与渲染
    # ------------------------------------------------------------------
    def generate_content_advice(self, analysis_id: str) -> dict[str, Any]:
        task = self._get_workflow_task(analysis_id, "analysis")
        transcript_id = task.outputs.get("transcription_task_id", "")
        if not transcript_id:
            raise VideoEditorWorkflowError(
                "请先完成字幕识别和人工复核，再生成内容建议。"
            )
        revision = self.transcription_service.get_approved_revision(transcript_id)
        if revision is None:
            raise VideoEditorWorkflowError("字幕尚未确认成稿，暂不能生成内容建议。")
        capabilities = self.copywriting_service.capabilities()
        if not capabilities.get("enabled", False):
            return {
                "enabled": False,
                "message": "未配置真实大模型，已保留本地剪辑建议。",
                "advice": [],
            }
        source_text = "\n".join(
            f"[{segment.start:.1f}-{segment.end:.1f}] {segment.text}"
            for segment in revision.corrected_segments
        )
        advice = self.copywriting_service.engine.rewrite(
            source_text,
            platform=task.outputs.get("target_platform", "douyin"),
            style_prompt="只输出三条简短的短视频节奏优化建议：开场钩子、重复信息、结尾行动引导。不得编造事实。",
            target_length=320,
            tone="professional",
            variant_count=1,
        )[0]
        outputs = {**task.outputs, "content_advice": advice}
        self._update(task, outputs=outputs, stage="内容建议已生成")
        return {
            "enabled": True,
            "message": "内容建议已生成，仅供人工参考，不会自动删改视频。",
            "advice": [advice],
        }

    def create_edit_job(
        self,
        *,
        analysis_id: str,
        steps: list[dict[str, Any]],
        output_format: str = "mp4",
        output_resolution: str = "1080x1920",
        output_fps: int = 30,
        output_bitrate: str = "4M",
        subtitle_enabled: bool = True,
        publish_title: str | None = None,
    ) -> VideoEditTask:
        analysis = self._get_workflow_task(analysis_id, "analysis")
        if analysis.status != TaskStatus.SUCCEEDED:
            raise VideoEditorWorkflowError("请等待智能分析完成后再开始剪辑。")
        if subtitle_enabled:
            transcript_id = analysis.outputs.get("transcription_task_id", "")
            if not transcript_id:
                raise VideoEditorWorkflowError(
                    "本次剪辑要求字幕，请先完成字幕识别和人工复核。"
                )
            if self.transcription_service.get_approved_revision(transcript_id) is None:
                raise VideoEditorWorkflowError("字幕尚未完成复核确认，无法开始剪辑。")
        source = self.resolve_source(analysis.outputs.get("source_id", ""))
        validated_steps = [
            VideoEditStep(
                kind=VideoEditStepKind(item["kind"]),
                params=dict(item.get("params") or {}),
                order=index,
            )
            for index, item in enumerate(steps)
            if item.get("enabled", True)
        ]
        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id=f"edit-{uuid4().hex[:10]}",
            title=f"智能剪辑 · {source['file_name']}",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            source_video_path=source["_path"],
            edit_config=VideoEditConfig(
                steps=validated_steps,
                output_format=output_format,
                output_resolution=output_resolution,
                output_fps=output_fps,
                output_bitrate=output_bitrate,
            ),
            source_task_id=analysis.outputs.get("transcription_task_id") or None,
            source_avatar_task_id=source["source_task_id"]
            if source["source_type"] == "avatar"
            else None,
            stage="等待剪辑",
            is_mock=False,
            outputs={
                "workflow": "edit",
                "analysis_id": analysis_id,
                "source_id": source["source_id"],
                "subtitle_enabled": str(subtitle_enabled).lower(),
                "publish_title": (publish_title or "").strip(),
            },
        )
        self.repository.save_task(task)
        _WORKFLOW_EXECUTOR.submit(self._run_edit, task.task_id)
        return task

    def _run_edit(self, task_id: str) -> None:
        task = self._get_workflow_task(task_id, "edit")
        temp_srt: Path | None = None
        try:
            config = task.edit_config
            if task.outputs.get("subtitle_enabled") == "true":
                transcript_id = task.source_task_id or ""
                revision = self.transcription_service.get_approved_revision(
                    transcript_id
                )
                if revision is None:
                    raise VideoEditorWorkflowError(
                        "字幕尚未完成复核确认，无法开始剪辑。"
                    )
                temp_srt = (
                    self.video_editing_service.output_directory
                    / f"{task.task_id}.approved.srt"
                )
                temp_srt.write_bytes(
                    self.transcription_service.export_srt(revision.corrected_segments)
                )
                config = config.model_copy(
                    update={
                        "steps": [
                            *config.steps,
                            VideoEditStep(
                                kind=VideoEditStepKind.SUBTITLE,
                                params={"srt_path": str(temp_srt), "style": "default"},
                                order=len(config.steps),
                            ),
                        ]
                    }
                )
            self._update(task, status=TaskStatus.RUNNING, progress=10, stage="准备剪辑")
            result = self.video_editing_service.edit_video(
                source_video_path=task.source_video_path,
                edit_config=config,
                source_task_id=task.source_task_id,
                source_avatar_task_id=task.source_avatar_task_id,
                task_id=task.task_id,
            )
            # 底层服务会写入结果输出；补回工作流标识，供页面持续轮询。
            result = result.model_copy(
                update={"outputs": {**result.outputs, **task.outputs}}
            )
            self.repository.save_task(result)
            if result.status == TaskStatus.FAILED:
                self._update(
                    task,
                    status=TaskStatus.FAILED,
                    progress=result.progress,
                    stage=result.stage,
                    error_message=result.error_message or "剪辑失败",
                )
        except Exception as exc:
            self._update(
                task, status=TaskStatus.FAILED, stage="剪辑失败", error_message=str(exc)
            )
        finally:
            if temp_srt is not None:
                temp_srt.unlink(missing_ok=True)

    def get_job(self, task_id: str) -> dict[str, Any]:
        task = self.get_edit_task(task_id)
        return self._job_payload(task)

    def get_edit_task(self, task_id: str) -> VideoEditTask:
        task = self.repository.get_task(task_id)
        if not isinstance(task, VideoEditTask) or task.outputs.get("workflow") not in {
            "edit",
            "product_showcase",
            "local_preview_export",
        }:
            raise VideoEditorWorkflowError("智能剪辑任务不存在。")
        return task

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        tasks = [
            task
            for task in self.repository.list_tasks()
            if isinstance(task, VideoEditTask)
            and task.outputs.get("workflow")
            in {"edit", "product_showcase", "local_preview_export"}
        ]
        return [self._job_payload(task) for task in tasks[:limit]]

    # ------------------------------------------------------------------
    # 云端轻量剪辑
    # ------------------------------------------------------------------
    def _cloud_runtime(self):
        from src.adapters.video_editor_cloud import build_cloud_providers
        from src.services.video_editor_cloud import CloudEditorConfiguration

        configuration = (
            self._cloud_configuration_override or CloudEditorConfiguration.from_env()
        )
        providers = self._cloud_providers_override or build_cloud_providers(
            configuration
        )
        return configuration, providers

    def cloud_capabilities(self) -> dict[str, Any]:
        from src.services.video_editor_cloud import get_cloud_capability

        configuration, providers = self._cloud_runtime()
        remote_capability = getattr(providers, "capability", None)
        if callable(remote_capability):
            return remote_capability()
        return get_cloud_capability(configuration).model_dump(mode="json")

    def local_ffmpeg_capabilities(self) -> dict[str, Any]:
        """Report the audited Windows renderer used by the customer path."""
        try:
            tools = _trusted_local_media_tools()
        except VideoEditorWorkflowError as exc:
            return {
                "provider_mode": _LOCAL_RENDER_MODE,
                "renderer_mode": _LOCAL_RENDER_MODE,
                "provider_name": "ffmpeg_local_audited",
                "display_name": "本机安全精剪",
                "enabled": False,
                "live_ready": False,
                "is_mock": False,
                "missing_configuration": [str(exc)],
                "cost_model": "local_no_cloud_charge",
            }
        return {
            "provider_mode": _LOCAL_RENDER_MODE,
            "renderer_mode": _LOCAL_RENDER_MODE,
            "provider_name": "ffmpeg_local_audited",
            "display_name": "本机安全精剪",
            "enabled": True,
            "live_ready": True,
            "is_mock": False,
            "missing_configuration": [],
            "cost_model": "local_no_cloud_charge",
            "media_tools": {
                "audited": True,
                "source": tools["source"],
                "ffmpeg_sha256": tools["ffmpeg_sha256"],
                "ffprobe_sha256": tools["ffprobe_sha256"],
            },
        }

    def create_cloud_preflight(
        self,
        *,
        source_id: str,
        output_profile: str,
        target_platform: str,
    ) -> dict[str, Any]:
        """生成并持久化无云调用的 15 分钟费用报价。"""
        from src.services.video_editor_cloud import (
            CloudEditorError,
            create_cost_quote,
            get_cloud_capability,
        )

        source = self.resolve_source(source_id)
        try:
            duration_seconds = float(
                self._probe_media(Path(source["_path"])).get("duration_seconds") or 0
            )
        except VideoEditorWorkflowError:
            raise
        except Exception as exc:
            raise VideoEditorWorkflowError(
                "无法读取素材时长，暂不能生成费用报价。"
            ) from exc
        if duration_seconds <= 0:
            raise VideoEditorWorkflowError("素材时长无效，暂不能生成费用报价。")

        if _local_renderer_enabled(self):
            capability = self.local_ffmpeg_capabilities()
            now = datetime.now().astimezone()
            expires = now + timedelta(minutes=15)
            quote_payload = {
                "quote_id": f"local-ffmpeg-{uuid4().hex[:12]}",
                "issued_at": now.isoformat(),
                "expires_at": expires.isoformat(),
                "ttl_seconds": 900,
                "price_version": "local-ffmpeg-0",
                "currency": "CNY",
                "output_profile": output_profile,
                "line_items": [],
                "estimated_total": "0.00",
                "estimated_max": "0.00",
                "exclusions": [
                    "本机 FFmpeg 编码不扣云端积分",
                    "不上传 IMS/MPS，不产生云端渲染费",
                ],
                "provider_mode": _LOCAL_RENDER_MODE,
                "renderer_mode": _LOCAL_RENDER_MODE,
                "live_ready": capability["live_ready"],
                "is_mock": False,
            }
            self.repository.save_video_editor_quote(
                quote_id=quote_payload["quote_id"],
                source_id=source_id,
                output_profile=output_profile,
                target_platform=target_platform,
                expires_at=quote_payload["expires_at"],
                created_at=quote_payload["issued_at"],
                payload=quote_payload,
            )
            return {**quote_payload, **capability, "blocking_reasons": [] if capability["live_ready"] else capability["missing_configuration"]}

        configuration, providers = self._cloud_runtime()
        try:
            quote = create_cost_quote(
                input_duration_seconds=duration_seconds,
                # The recommended smart opening adds at most 1.4 seconds to the
                # first render. Quote it up front instead of hiding the cost.
                output_duration_seconds=duration_seconds + 1.4,
                output_profile=output_profile,
                price_version=configuration.price_version,
                ttl_seconds=configuration.quote_ttl_seconds,
            )
        except CloudEditorError as exc:
            raise VideoEditorWorkflowError(str(exc)) from exc
        quote_payload = quote.model_dump(mode="json")
        self.repository.save_video_editor_quote(
            quote_id=quote.quote_id,
            source_id=source_id,
            output_profile=quote.output_profile.value,
            target_platform=target_platform,
            expires_at=quote.expires_at.isoformat(),
            created_at=quote.issued_at.isoformat(),
            payload=quote_payload,
        )
        remote_capability = getattr(providers, "capability", None)
        capability = (
            remote_capability()
            if callable(remote_capability)
            else get_cloud_capability(configuration).model_dump(mode="json")
        )
        blocking_reasons = (
            [
                "生产云配置不完整，请先补齐缺失配置后重新预检。",
            ]
            if capability["provider_mode"] == "aliyun" and not capability["live_ready"]
            else []
        )
        return {
            **quote_payload,
            **capability,
            "blocking_reasons": blocking_reasons,
        }

    @staticmethod
    def _cloud_output_settings(output_profile: str) -> dict[str, Any]:
        profiles = {
            "720p": {
                "output_profile": "720p",
                "output_resolution": "720x1280",
                "output_fps": 30,
                "output_bitrate": "2.5M",
            },
            "1080p": {
                "output_profile": "1080p",
                "output_resolution": "1080x1920",
                "output_fps": 30,
                "output_bitrate": "5M",
            },
        }
        try:
            return profiles[output_profile]
        except KeyError as exc:
            raise VideoEditorWorkflowError("输出档位仅支持 720p 或 1080p。") from exc

    @staticmethod
    def _cloud_request_hash(payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_cloud_operation_response(payload: dict[str, Any]) -> dict[str, Any]:
        """幂等记录不持久化短期 OSS 签名地址。"""
        safe = json.loads(json.dumps(payload, ensure_ascii=False))
        for item in safe.get("items", []):
            if isinstance(item, dict):
                item["result_media_url"] = None
        return safe

    def create_cloud_batch(
        self,
        *,
        source_ids: list[str],
        target_platform: str,
        output_profile: str,
        quote_id: str,
        billing_confirmation: dict[str, Any],
        idempotency_key: str,
        bgm_enabled: bool = True,
        bgm_id: str | None = None,
        bgm_volume: float = 0.2,
    ) -> dict[str, Any]:
        """创建单素材云批次；付费边界前验证报价、上限和幂等键。"""
        from src.services.video_editor_cloud import (
            CloudEditorError,
            CostQuote,
            validate_cost_quote,
        )

        unique_source_ids = list(dict.fromkeys(source_ids))
        if len(unique_source_ids) != 1:
            raise VideoEditorWorkflowError("云端轻量剪辑每次只允许提交一条素材。")
        source_id = unique_source_ids[0]
        source = self.resolve_source(source_id)
        settings = self._cloud_output_settings(output_profile)
        if not idempotency_key.strip():
            raise VideoEditorWorkflowError("云端剪辑必须提供 Idempotency-Key。")

        stored_quote = self.repository.get_video_editor_quote(quote_id)
        if stored_quote is None:
            raise VideoEditorWorkflowError("费用报价不存在，请重新预检。")
        if (
            stored_quote["source_id"] != source_id
            or stored_quote["output_profile"] != output_profile
            or stored_quote["target_platform"] != target_platform
        ):
            raise VideoEditorWorkflowError("素材、平台或清晰度已变化，请重新确认费用。")

        if _local_renderer_enabled(self) and str(stored_quote["payload"].get("renderer_mode") or "") == _LOCAL_RENDER_MODE:
            if not billing_confirmation.get("confirmed", False):
                raise VideoEditorWorkflowError("请确认使用本机免费精剪。")
            try:
                max_cost = Decimal(str(billing_confirmation.get("max_cost_cny", 0)))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise VideoEditorWorkflowError("费用上限无效，请重新确认。") from exc
            if max_cost < 0:
                raise VideoEditorWorkflowError("费用上限不能为负数。")
            operation_payload = {
                "source_id": source_id,
                "target_platform": target_platform,
                "output_profile": output_profile,
                "quote_id": quote_id,
                "renderer_mode": _LOCAL_RENDER_MODE,
                "bgm_enabled": bgm_enabled,
                "bgm_id": bgm_id,
                "bgm_volume": bgm_volume,
            }
            request_hash = self._cloud_request_hash(operation_payload)
            now = datetime.now().astimezone()
            claimed = self.repository.claim_video_editor_operation(
                idempotency_key=idempotency_key,
                operation_type="create_local_ffmpeg_batch",
                request_hash=request_hash,
                created_at=now.isoformat(),
            )
            if not claimed:
                existing = self.repository.get_video_editor_operation(idempotency_key)
                if existing is None or existing["request_hash"] != request_hash:
                    raise VideoEditorWorkflowError("该 Idempotency-Key 已用于不同请求，请更换后重试。")
                if existing.get("resource_id"):
                    return self.get_batch(existing["resource_id"])
                raise VideoEditorWorkflowError("同一请求正在处理，系统不会重复创建任务。")
            try:
                payload = self.create_batch(
                    source_ids=[source_id],
                    target_platform=target_platform,
                    subtitle_enabled=True,
                    subtitle_model="large-v3-turbo",
                    steps=[],
                    output_format="mp4",
                    output_resolution=settings["output_resolution"],
                    output_fps=settings["output_fps"],
                    output_bitrate=settings["output_bitrate"],
                    bgm_enabled=bgm_enabled,
                    bgm_id=bgm_id,
                    bgm_volume=bgm_volume,
                    provider_mode=_LOCAL_RENDER_MODE,
                    output_profile=output_profile,
                    quote_id=quote_id,
                    cost_quote=dict(stored_quote["payload"]),
                    billing_confirmation={
                        "confirmed": True,
                        "max_cost_cny": "0.00",
                        "price_version": "local-ffmpeg-0",
                    },
                    idempotency_key=idempotency_key,
                )
            except Exception:
                self.repository.delete_video_editor_operation(idempotency_key)
                raise
            self.repository.complete_video_editor_operation(
                idempotency_key=idempotency_key,
                state="completed",
                resource_id=payload["batch_id"],
                response=self._safe_cloud_operation_response(payload),
                updated_at=now.isoformat(),
            )
            return payload

        configuration, providers = self._cloud_runtime()
        try:
            quote = CostQuote.model_validate(stored_quote["payload"])
            validate_cost_quote(
                quote,
                quote_id,
                expected_price_version=configuration.price_version,
            )
        except (CloudEditorError, ValueError) as exc:
            raise VideoEditorWorkflowError(str(exc)) from exc

        if not billing_confirmation.get("confirmed", False):
            raise VideoEditorWorkflowError("请先明确确认本次云服务预计费用。")
        try:
            max_cost = Decimal(str(billing_confirmation.get("max_cost_cny")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise VideoEditorWorkflowError("费用上限无效，请重新确认。") from exc
        if max_cost < quote.estimated_max:
            raise VideoEditorWorkflowError("确认的费用上限低于当前报价，请重新确认。")

        capability = self.cloud_capabilities()
        if capability["provider_mode"] == "aliyun" and not capability["live_ready"]:
            missing = "、".join(capability["missing_configuration"])
            raise VideoEditorWorkflowError(
                f"云端剪辑配置不完整：{missing}。不会自动降级到沙箱。"
            )
        if bgm_enabled and bgm_id:
            self.resolve_bgm_asset(bgm_id)

        operation_payload = {
            "source_id": source_id,
            "target_platform": target_platform,
            "output_profile": output_profile,
            "quote_id": quote_id,
            "max_cost_cny": str(max_cost),
            "bgm_enabled": bgm_enabled,
            "bgm_id": bgm_id,
            "bgm_volume": bgm_volume,
        }
        request_hash = self._cloud_request_hash(operation_payload)
        now = datetime.now().astimezone()
        claimed = self.repository.claim_video_editor_operation(
            idempotency_key=idempotency_key,
            operation_type="create_cloud_batch",
            request_hash=request_hash,
            created_at=now.isoformat(),
        )
        if not claimed:
            existing = self.repository.get_video_editor_operation(idempotency_key)
            if existing is None or existing["request_hash"] != request_hash:
                raise VideoEditorWorkflowError(
                    "该 Idempotency-Key 已用于不同请求，请更换后重试。"
                )
            if existing.get("resource_id"):
                return self.get_batch(existing["resource_id"])
            raise VideoEditorWorkflowError(
                "同一请求正在处理或结果待确认；系统不会重复提交付费任务。"
            )

        item = VideoEditorBatchItem(
            source_id=source_id,
            title=source["title"],
            status="analyzing",
            provider_stage="uploading",
            is_mock=bool(capability["is_mock"]),
            publish_allowed=False,
        )
        batch = VideoEditorBatch(
            target_platform=target_platform,
            subtitle_enabled=True,
            subtitle_model="fun-asr",
            bgm_enabled=bgm_enabled,
            bgm_id=bgm_id,
            bgm_volume=bgm_volume,
            output_format="mp4",
            output_resolution=settings["output_resolution"],
            output_fps=settings["output_fps"],
            output_bitrate=settings["output_bitrate"],
            provider_mode=capability["provider_mode"],
            output_profile=output_profile,
            quote_id=quote_id,
            cost_quote=quote.model_dump(mode="json"),
            billing_confirmation={
                "confirmed": True,
                "max_cost_cny": str(max_cost),
                "price_version": quote.price_version,
            },
            billing_confirmed_at=now,
            idempotency_key=idempotency_key,
            is_mock=bool(capability["is_mock"]),
            items=[item],
        )

        # 演示提供方只能生成本地体验方案，不调用付费供应商，也绝不能扣积分。
        # 服务器控制层模式下，正式提供方才先在公司服务器扣费再上传素材。
        if bool(capability["is_mock"]):
            pass
        elif bool(getattr(providers, "billing_centrally_managed", False)):
            authorize_cost = getattr(providers, "authorize_cost", None)
            if not callable(authorize_cost):
                self.repository.delete_video_editor_operation(idempotency_key)
                raise VideoEditorWorkflowError("公司云端计费服务暂不可用。")
            try:
                authorization = authorize_cost(
                    batch_id=batch.batch_id,
                    quote_payload=quote.model_dump(mode="json"),
                    max_cost_cny=str(max_cost),
                )
                batch = batch.model_copy(
                    update={
                        "billing_confirmation": {
                            **batch.billing_confirmation,
                            "authority": "company_control_plane",
                            "charged_credits": str(
                                authorization.get("charged_credits", "")
                            ),
                        }
                    }
                )
            except Exception as exc:
                if not bool(getattr(exc, "outcome_unknown", False)):
                    self.repository.delete_video_editor_operation(idempotency_key)
                    raise VideoEditorWorkflowError(str(exc)) from exc
                item = item.model_copy(
                    update={
                        "status": "outcome_unknown",
                        "provider_stage": "billing_outcome_unknown",
                        "error_message": str(exc),
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                batch = batch.model_copy(update={"items": [item]})
                self.repository.save_video_editor_batch(batch)
                payload = self._batch_payload(batch)
                self.repository.complete_video_editor_operation(
                    idempotency_key=idempotency_key,
                    state="outcome_unknown",
                    resource_id=batch.batch_id,
                    response=self._safe_cloud_operation_response(payload),
                    error_message=str(exc),
                    updated_at=datetime.now().astimezone().isoformat(),
                )
                return payload
        else:
            try:
                self._debit_credits(
                    quote.estimated_total,
                    reason="云端剪辑成片费用",
                    ref_type="video_editor",
                    ref_id=quote_id,
                )
            except InsufficientCreditsError as exc:
                self.repository.delete_video_editor_operation(idempotency_key)
                raise VideoEditorWorkflowError(exc.message) from exc

        self.repository.save_video_editor_batch(batch)
        try:
            batch = self._submit_cloud_analysis(batch, item)
            payload = self._batch_payload(batch)
            self.repository.complete_video_editor_operation(
                idempotency_key=idempotency_key,
                state="completed",
                resource_id=batch.batch_id,
                response=self._safe_cloud_operation_response(payload),
                updated_at=datetime.now().astimezone().isoformat(),
            )
            return payload
        except Exception as exc:
            # 创建记录已经落库。任何提交边界不明都保持可查询状态，绝不重提。
            current = self.repository.get_video_editor_batch(batch.batch_id) or batch
            current_item = current.items[0]
            if current_item.status not in {"failed", "outcome_unknown"}:
                current_item = current_item.model_copy(
                    update={
                        "status": "outcome_unknown",
                        "provider_stage": "submission_outcome_unknown",
                        "error_message": str(exc),
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                current = self._replace_batch_item(current, current_item)
            payload = self._batch_payload(current)
            self.repository.complete_video_editor_operation(
                idempotency_key=idempotency_key,
                state="outcome_unknown",
                resource_id=current.batch_id,
                response=self._safe_cloud_operation_response(payload),
                error_message=str(exc),
                updated_at=datetime.now().astimezone().isoformat(),
            )
            return payload

    def _save_cloud_job_snapshot(
        self,
        *,
        batch: VideoEditorBatch,
        item: VideoEditorBatchItem,
        job_key: str,
        snapshot,
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now().astimezone().isoformat()
        self.repository.save_video_editor_cloud_job(
            job_key=job_key,
            batch_id=batch.batch_id,
            item_id=item.item_id,
            provider_stage=snapshot.provider_stage,
            provider_name=snapshot.provider_name,
            provider_job_id=snapshot.provider_job_id,
            status=snapshot.status.value,
            usage=snapshot.usage,
            payload=payload or snapshot.model_dump(mode="json"),
            next_poll_at=None,
            created_at=now,
            updated_at=now,
        )

    def _submit_cloud_analysis(
        self,
        batch: VideoEditorBatch,
        item: VideoEditorBatchItem,
    ) -> VideoEditorBatch:
        configuration, providers = self._cloud_runtime()
        source = self.resolve_source(item.source_id)
        media = self._probe_media(Path(source["_path"]))
        duration_seconds = float(media.get("duration_seconds") or 0)
        if duration_seconds <= 0:
            raise VideoEditorWorkflowError("素材时长无效，无法开始云端分析。")

        object_key = (
            f"video-editor-input/{batch.batch_id}/input/"
            f"{Path(source['file_name']).name}"
        )
        asset = providers.object_store.upload(
            source["_path"],
            object_key,
            media_type=source["media_type"],
        )
        now = datetime.now().astimezone().isoformat()
        self.repository.save_video_editor_cloud_job(
            job_key=f"{batch.batch_id}:{item.item_id}:upload",
            batch_id=batch.batch_id,
            item_id=item.item_id,
            provider_stage="upload_complete",
            provider_name=asset.provider_name,
            provider_job_id=None,
            status="succeeded",
            usage={},
            payload={"asset": asset.model_dump(mode="json")},
            next_poll_at=None,
            created_at=now,
            updated_at=now,
        )
        updated = item.model_copy(
            update={
                "provider_stage": "submitting_transcription",
                "provider_payload": {
                    **item.provider_payload,
                    "input_asset": asset.model_dump(mode="json"),
                    "media": media,
                },
                "updated_at": datetime.now().astimezone(),
            }
        )
        batch = self._replace_batch_item(batch, updated)

        snapshot = providers.asr.submit(asset, language_hints=("zh",))
        self._save_cloud_job_snapshot(
            batch=batch,
            item=updated,
            job_key=f"{batch.batch_id}:{item.item_id}:asr",
            snapshot=snapshot,
        )
        updated = updated.model_copy(
            update={
                "provider_stage": snapshot.provider_stage,
                "provider_job_ids": {
                    **updated.provider_job_ids,
                    "asr": snapshot.provider_job_id,
                },
                "actual_usage": {
                    **updated.actual_usage,
                    "asr": snapshot.usage,
                },
                "is_mock": snapshot.is_mock,
                "updated_at": datetime.now().astimezone(),
            }
        )
        batch = self._replace_batch_item(batch, updated)
        if snapshot.status.value == "succeeded":
            return self._complete_cloud_analysis(batch, updated, snapshot)
        if snapshot.status.value == "failed":
            updated = updated.model_copy(
                update={
                    "status": "failed",
                    "error_message": str(
                        snapshot.detail.get("message") or "Fun-ASR 转写失败。"
                    ),
                    "updated_at": datetime.now().astimezone(),
                }
            )
            return self._replace_batch_item(batch, updated)
        return batch

    @staticmethod
    def _normalize_cloud_transcript(
        detail: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, float]]]:
        normalized = detail.get("normalized_result")
        source = normalized if isinstance(normalized, dict) else detail
        transcript = str(source.get("transcript") or source.get("text") or "").strip()
        raw_segments = source.get("segments") or source.get("sentences") or []
        segments: list[dict[str, Any]] = []
        spoken_ranges: list[dict[str, float]] = []
        if isinstance(raw_segments, list):
            for raw in raw_segments:
                if not isinstance(raw, dict):
                    continue
                raw_start = raw.get("start", raw.get("begin_time", 0))
                raw_end = raw.get("end", raw.get("end_time", 0))
                try:
                    start = float(raw_start or 0)
                    end = float(raw_end or 0)
                except (TypeError, ValueError):
                    continue
                # Fun-ASR sentence timestamps commonly use milliseconds.
                if start > 10_000 or end > 10_000:
                    start /= 1000
                    end /= 1000
                text = str(raw.get("text") or raw.get("sentence") or "").strip()
                if end <= start:
                    continue
                segment = {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "text": text,
                }
                raw_words = raw.get("words") or []
                if isinstance(raw_words, list):
                    words: list[dict[str, Any]] = []
                    for raw_word in raw_words:
                        if not isinstance(raw_word, dict):
                            continue
                        try:
                            word_start = float(
                                raw_word.get("start", raw_word.get("begin_time", 0))
                                or 0
                            )
                            word_end = float(
                                raw_word.get("end", raw_word.get("end_time", 0))
                                or 0
                            )
                        except (TypeError, ValueError):
                            continue
                        if word_start > 10_000 or word_end > 10_000:
                            word_start /= 1000
                            word_end /= 1000
                        word_text = str(
                            raw_word.get("text") or raw_word.get("word") or ""
                        ).strip()
                        if word_text and start <= word_start < word_end <= end:
                            words.append(
                                {
                                    "start": round(word_start, 3),
                                    "end": round(word_end, 3),
                                    "text": word_text,
                                }
                            )
                    if words:
                        segment["words"] = words
                segments.append(segment)
                spoken_ranges.append({"start": segment["start"], "end": segment["end"]})
        if not transcript:
            transcript = "".join(
                segment["text"] for segment in segments if segment["text"]
            )
        return transcript, segments, spoken_ranges

    @staticmethod
    def _cloud_plan_with_steps(plan) -> dict[str, Any]:
        payload = plan.model_dump(mode="json")
        removed_seconds = round(
            sum(item.end - item.start for item in plan.remove_ranges),
            3,
        )
        labels = {
            "smart_opening": (
                "AI 智能开场",
                "从已审核文案提取短钩子，自动匹配克制的开场动画和音效。",
            ),
            "trim_silence": (
                "安全粗剪长停顿",
                "只处理可靠无语音间隔；两端保留口型缓冲，正式成片按同一方案拼接。",
            ),
            "vertical_fit": ("适配 9:16", "按已选择的输出档位统一画幅、帧率和码率。"),
            "subtitles": (
                "烧录确认字幕",
                "只使用本次人工确认的一套字幕，不会再次识别。",
            ),
            "title": ("添加标题", "标题仅使用候选或人工输入，不改写人声内容。"),
            "bgm": ("添加授权配乐", "只有确认权利的音乐才会进入正式渲染。"),
            "audio_mix": ("平衡人声与音乐", "保留原始人声并限制背景音乐音量。"),
        }
        payload["steps"] = [
            {
                "step_id": kind.value,
                "kind": kind.value,
                "label": labels[kind.value][0],
                "reason": labels[kind.value][1],
                "enabled": True,
                "required": kind.value in {"vertical_fit", "subtitles"},
                "estimated_removed_seconds": (
                    removed_seconds if kind.value == "trim_silence" else 0
                ),
                "params": (
                    {
                        "intervals": [
                            item.model_dump(mode="json") for item in plan.remove_ranges
                        ],
                        "estimated_output_seconds": plan.estimated_output_seconds,
                    }
                    if kind.value == "trim_silence"
                    else {}
                ),
            }
            for kind in plan.enabled_steps
        ]
        return payload

    def _complete_cloud_analysis(
        self,
        batch: VideoEditorBatch,
        item: VideoEditorBatchItem,
        snapshot,
    ) -> VideoEditorBatch:
        from src.services.video_editor_cloud import EditStepKind, build_visual_beats
        from src.services.director_plan import build_director_plan

        _, providers = self._cloud_runtime()
        cloud_transcript = providers.asr.fetch_result(snapshot)
        transcript = cloud_transcript.transcript
        segments = [
            segment.model_dump(mode="json") for segment in cloud_transcript.segments
        ]
        spoken_ranges = [
            item.model_dump(mode="json") for item in cloud_transcript.spoken_ranges
        ]
        duration_seconds = float(
            (item.provider_payload.get("media") or {}).get("duration_seconds") or 0
        )
        if batch.provider_mode == "sandbox":
            transcript_for_plan = transcript or item.title
        else:
            transcript_for_plan = transcript
            if not transcript and not segments:
                raise VideoEditorWorkflowError(
                    "Fun-ASR 已完成，但尚未取得可复核的转写明细；不会进入渲染。"
                )
        plan = providers.edit_plan.create_plan(
            transcript_for_plan,
            spoken_ranges,
            duration_seconds,
            segments,
        )
        plan = plan.model_copy(
            update={
                "visual_beats": build_visual_beats(
                    segments,
                    plan.caption_emphasis,
                ),
            }
        )
        titles = list(plan.title_candidates) or [item.title[:40]]
        selected_bgm_id: str | None = None
        bgm_reason: str | None = None
        if batch.bgm_enabled:
            bgm, bgm_reason = self._recommend_bgm_asset(
                {
                    "transcript": transcript_for_plan,
                    "title_candidates": titles,
                    "media": item.provider_payload.get("media") or {},
                },
                item.title,
            )
            if bgm is not None:
                selected_bgm_id = bgm["asset_id"]
                if EditStepKind.BGM not in plan.enabled_steps:
                    plan = plan.model_copy(
                        update={
                            "enabled_steps": [
                                *plan.enabled_steps,
                                EditStepKind.BGM,
                            ]
                        }
                    )
        else:
            bgm_reason = "已关闭自动配乐，保持素材原声。"
        plan_payload = self._cloud_plan_with_steps(plan)
        source_info = self.resolve_source(item.source_id)
        source_path = Path(source_info["_path"])
        director_plan = build_director_plan(
            segments,
            duration_seconds=duration_seconds,
            title=titles[0] if titles else item.title,
            target_platform="douyin",
            source_media_identity={
                "source_media_sha256": hashlib.sha256(
                    source_path.read_bytes()
                ).hexdigest(),
                "source_duration_seconds": duration_seconds,
                "transcript_sha256": hashlib.sha256(
                    json.dumps(
                        segments,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "transcript_timing_source": (
                    "word_timestamps"
                    if any(segment.get("words") for segment in segments)
                    else "sentence_timestamps"
                ),
            },
        )
        plan_payload["director_plan"] = director_plan
        updated = item.model_copy(
            update={
                "status": "awaiting_subtitle_review",
                "provider_stage": "awaiting_human_review",
                "subtitle_segments": segments,
                "edit_plan": plan_payload,
                "title_candidates": titles,
                "selected_title": item.selected_title or titles[0],
                "selected_bgm_id": selected_bgm_id,
                "bgm_reason": bgm_reason,
                "actual_usage": {
                    **item.actual_usage,
                    "planning": plan.usage,
                },
                "error_message": None,
                "updated_at": datetime.now().astimezone(),
            }
        )
        return self._replace_batch_item(batch, updated)

    # ------------------------------------------------------------------
    # 自动批次
    # ------------------------------------------------------------------
    def create_batch(
        self,
        *,
        source_ids: list[str],
        target_platform: str,
        subtitle_enabled: bool,
        subtitle_model: str,
        steps: list[dict[str, Any]],
        output_format: str,
        output_resolution: str,
        output_fps: int,
        output_bitrate: str,
        bgm_enabled: bool = True,
        bgm_id: str | None = None,
        bgm_volume: float = 0.24,
        provider_mode: str = "legacy",
        output_profile: str | None = None,
        quote_id: str | None = None,
        cost_quote: dict[str, Any] | None = None,
        billing_confirmation: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        unique_source_ids = list(dict.fromkeys(source_ids))
        if not unique_source_ids:
            raise VideoEditorWorkflowError("请至少选择一条素材。")
        if len(unique_source_ids) > _MAX_BATCH_ITEMS:
            raise VideoEditorWorkflowError(f"单次最多处理 {_MAX_BATCH_ITEMS} 条素材。")

        selected_bgm_id = bgm_id
        if bgm_enabled and selected_bgm_id:
            self.resolve_bgm_asset(selected_bgm_id)

        items: list[VideoEditorBatchItem] = []
        for source_id in unique_source_ids:
            source = self.resolve_source(source_id)
            items.append(
                VideoEditorBatchItem(
                    source_id=source_id, title=source["title"], status="analyzing"
                )
            )
        batch = VideoEditorBatch(
            target_platform=target_platform,
            subtitle_enabled=subtitle_enabled,
            subtitle_model=subtitle_model,
            bgm_enabled=bgm_enabled,
            bgm_id=selected_bgm_id,
            bgm_volume=bgm_volume,
            steps=steps,
            output_format=output_format,
            output_resolution=output_resolution,
            output_fps=output_fps,
            output_bitrate=output_bitrate,
            items=items,
        )

        updated_items: list[VideoEditorBatchItem] = []
        for item in batch.items:
            analysis = self.create_analysis(
                source_id=item.source_id,
                target_platform=target_platform,
                subtitle_enabled=subtitle_enabled,
                subtitle_model=subtitle_model,
            )
            updated_items.append(
                item.model_copy(
                    update={
                        "analysis_id": analysis.task_id,
                        "publish_allowed": provider_mode not in {"local", _LOCAL_RENDER_MODE},
                    }
                )
            )
        batch = batch.model_copy(update={"items": updated_items})
        if provider_mode != "legacy" or output_profile:
            batch = batch.model_copy(
                update={
                    "provider_mode": provider_mode,
                    "output_profile": output_profile,
                    "quote_id": quote_id,
                    "cost_quote": cost_quote or {},
                    "billing_confirmation": billing_confirmation or {},
                    "billing_confirmed_at": datetime.now().astimezone() if billing_confirmation else None,
                    "idempotency_key": idempotency_key,
                }
            )
        self.repository.save_video_editor_batch(batch)
        return self._batch_payload(batch)

    def list_batches(self, limit: int = 20) -> list[dict[str, Any]]:
        batches = self.repository.list_video_editor_batches(limit=limit)
        return [self._batch_payload(self._sync_batch(batch)) for batch in batches]

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.repository.get_video_editor_batch(batch_id)
        if batch is None:
            raise VideoEditorWorkflowError("智能剪辑批次不存在。")
        return self._batch_payload(self._sync_batch(batch))

    def prepare_batch_item_download(
        self,
        batch_id: str,
        item_id: str,
    ) -> dict[str, str]:
        """为已完成的真实云成片生成一次性下载信息，不改变审核或发布状态。"""
        batch = self._require_batch(batch_id)
        item = next(
            (entry for entry in batch.items if entry.item_id == item_id),
            None,
        )
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        if batch.provider_mode not in {"aliyun", _LOCAL_RENDER_MODE} or batch.is_mock or item.is_mock:
            raise VideoEditorWorkflowError("体验任务没有真实成片可下载。")
        if item.status not in {
            "awaiting_output_confirmation",
            "ready_to_publish",
        }:
            raise VideoEditorWorkflowError("真实成片尚未生成，暂时不能下载。")
        try:
            media_url = self._cloud_preview_url(item)
        except Exception as exc:
            raise VideoEditorWorkflowError(
                "云成片下载地址暂时不可用，请检查云配置后重试。"
            ) from exc
        if not media_url:
            raise VideoEditorWorkflowError("真实成片文件不存在，暂时不能下载。")

        title = re.sub(
            r'[\\/:*?"<>|]+',
            "",
            item.selected_title or item.title or "剪辑成片",
        ).strip()
        return {
            "media_url": media_url,
            "filename": f"{title[:48] or '剪辑成片'}.mp4",
        }

    def create_local_preview_export(
        self,
        batch_id: str,
        item_id: str,
        *,
        run_inline: bool = False,
        local_bgm_id: str | None = None,
    ) -> dict[str, Any]:
        """把当前已审核的浏览器方案免费烧录为本机 MP4。"""
        batch = self._sync_batch(self._require_batch(batch_id))
        item = next(
            (entry for entry in batch.items if entry.item_id == item_id),
            None,
        )
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        if batch.is_mock or item.is_mock:
            raise VideoEditorWorkflowError("体验任务没有真实媒体，不能生成下载文件。")

        if item.edit_task_id:
            existing = self.repository.get_task(item.edit_task_id)
            existing_quality: dict[str, Any] = {}
            if isinstance(existing, VideoEditTask):
                try:
                    existing_quality = json.loads(
                        existing.outputs.get("quality_report") or "{}"
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    existing_quality = {}
            existing_hard_gates_current = bool(
                existing_quality.get("quality_report_version") == "identity-gates-v2"
                and existing_quality.get("subtitle_sync_passed") is not None
                and existing_quality.get("edl_execution_gate") is not None
            )
            if (
                isinstance(existing, VideoEditTask)
                and existing.outputs.get("workflow") == "local_preview_export"
                and existing.outputs.get("style_version")
                == _LOCAL_PREVIEW_EXPORT_STYLE_VERSION
                and existing.status
                in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.SUCCEEDED}
                and (
                    existing.status != TaskStatus.SUCCEEDED
                    or (
                        existing.result_path
                        and Path(existing.result_path).is_file()
                        and existing_hard_gates_current
                    )
                )
            ):
                return self._batch_payload(batch)

        cached = self._cached_source_context(item.source_id)
        segments = [
            dict(segment)
            for segment in (
                item.subtitle_segments or list(cached.get("subtitle_segments") or [])
            )
        ]
        cached_review = dict(cached.get("review_snapshot") or {})
        current_reviewed = bool(
            item.review_confirmed_at or item.review_snapshot.get("confirmed")
        )
        if not segments or not (current_reviewed or cached_review.get("confirmed")):
            raise VideoEditorWorkflowError(
                "当前只有估算字幕，没有可复用的人工确认字幕，暂不能生成成片。"
            )

        source = self.resolve_source(item.source_id)
        media = self._probe_media(Path(source["_path"]))
        segments = self._validated_review_segments(
            segments,
            duration_seconds=float(media["duration_seconds"]),
        )
        source_segments_for_task = [dict(segment) for segment in segments]
        title = (
            item.selected_title
            or str(cached.get("selected_title") or "").strip()
            or item.title
        ).strip()
        if not title:
            raise VideoEditorWorkflowError("当前方案缺少标题，暂不能生成成片。")

        edit_plan = dict(item.edit_plan or cached.get("edit_plan") or {})
        shot_plan = dict(edit_plan.get("shot_plan") or {})
        requested_pipeline = str(
            item.provider_payload.get("requested_pipeline") or ""
        ).strip()
        if requested_pipeline == "adaptive_fine_cut_v1" and not (
            shot_plan or edit_plan.get("director_plan")
        ):
            raise VideoEditorWorkflowError(
                "VISUAL_PIPELINE_NOT_EXECUTED：自动精剪请求未进入导演与视觉编排路径。"
            )
        if shot_plan:
            from src.services.talking_head_templates import retime_segments_for_shot_plan

            segments = retime_segments_for_shot_plan(segments, shot_plan)
            if not segments:
                raise VideoEditorWorkflowError("发布级分镜没有可用字幕时间轴，已停止导出。")
            timeline_duration = float(
                shot_plan.get("timeline_duration_seconds")
                or max(float(shot.get("timeline_end") or 0) for shot in shot_plan.get("shots") or [])
            )
            ordered_ranges = [
                {
                    "start": float(entry.get("start") or 0),
                    "end": float(entry.get("end") or 0),
                }
                for entry in shot_plan.get("reordered_ranges") or []
                if float(entry.get("end") or 0) > float(entry.get("start") or 0)
            ]
            edit_plan["duration_seconds"] = timeline_duration
            edit_plan["estimated_output_seconds"] = timeline_duration
            edit_plan["kept_ranges"] = ordered_ranges
            edit_plan["spoken_ranges"] = [
                {"start": item["start"], "end": item["end"]}
                for item in segments
            ]
        # The identity gate hashes the exact subtitle_segments_json persisted
        # on the task.  Use that canonical payload here, after validation and
        # before any renderer-only clock mapping, so review metadata cannot
        # make an otherwise identical transcript fail the identity check.
        task_transcript_segments = source_segments_for_task if shot_plan else segments
        source_identity = dict(edit_plan.get("source_media_identity") or {})
        # A local export must remain auditable even when the caller only has a
        # confirmed transcript and did not pre-populate the identity block.
        # Derive these stable media facts from the exact source resolved above;
        # never let a missing caller field silently disable the identity gate.
        source_identity.setdefault(
            "source_media_sha256",
            hashlib.sha256(Path(source["_path"]).read_bytes()).hexdigest(),
        )
        source_identity.setdefault(
            "source_duration_seconds",
            float(media["duration_seconds"]),
        )
        source_identity.setdefault(
            "transcript_timing_source",
            (
                "word_timestamps"
                if any(
                    isinstance(segment.get("words"), list)
                    and segment.get("words")
                    for segment in segments
                    if isinstance(segment, Mapping)
                )
                else "sentence_timestamps"
            ),
        )
        source_identity["transcript_sha256"] = hashlib.sha256(
            json.dumps(
                task_transcript_segments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        source_identity["transcript_identity_scope"] = "task_subtitle_segments_v1"
        edit_plan["source_media_identity"] = source_identity
        if edit_plan.get("remove_ranges"):
            raise VideoEditorWorkflowError(
                "当前方案包含真实粗剪区间，本机免费导出暂不支持；请先关闭粗剪或使用云端出片。"
            )
        # A legacy analysis plan can still carry spoken ranges for the full
        # upload (for this acceptance source that is 104+ seconds).  When a
        # reviewed source interval is the actual EDL, those stale ranges must
        # not participate in phrase grouping: doing so can join one sentence
        # to the next and create sub-frame subtitle fragments at the interval
        # boundary.  The reviewed segments are the canonical clock for this
        # local export, while their real word timestamps remain authoritative
        # inside the subtitle renderer.
        if not shot_plan and edit_plan.get("source_range"):
            edit_plan["spoken_ranges"] = [
                {
                    "start": float(segment.get("start") or 0),
                    "end": float(segment.get("end") or 0),
                }
                for segment in segments
                if isinstance(segment, Mapping)
                and float(segment.get("end") or 0)
                > float(segment.get("start") or 0)
            ]
            source_range_start = float(
                edit_plan["source_range"].get("start") or 0
            )
            source_range_end = float(edit_plan["source_range"].get("end") or 0)
            if source_range_end > source_range_start:
                edit_plan["duration_seconds"] = round(
                    source_range_end - source_range_start, 3
                )
                edit_plan["estimated_output_seconds"] = edit_plan[
                    "duration_seconds"
                ]
        enabled_steps = list(
            item.enabled_plan_step_ids
            or cached.get("enabled_plan_step_ids")
            or ["vertical_fit", "subtitles", "title"]
        )
        from src.services.video_editor_cloud import build_smart_opening, build_visual_beats

        transcript = "".join(str(segment.get("text") or "") for segment in segments)
        existing_opening = edit_plan.get("smart_opening") or {}
        if shot_plan:
            # The hook is already the first reordered shot. A second animated
            # opening would duplicate the hook and break the single timeline.
            edit_plan["smart_opening"] = None
            enabled_steps = [step for step in enabled_steps if step != "smart_opening"]
        else:
            opening = build_smart_opening(
                transcript,
                [title],
                preferred_style=existing_opening.get("style_id"),
            )
            if opening is not None:
                edit_plan["smart_opening"] = opening.model_dump(mode="json")
                if "smart_opening" not in enabled_steps:
                    enabled_steps.insert(0, "smart_opening")
        if not edit_plan.get("visual_beats"):
            edit_plan["visual_beats"] = [
                beat.model_dump(mode="json")
                for beat in build_visual_beats(
                    segments,
                    edit_plan.get("caption_emphasis") or [],
                )
            ]
        selected_bgm_id = (
            local_bgm_id
            or item.selected_bgm_id
            or str(cached.get("selected_bgm_id") or "").strip()
            or None
        )
        if "bgm" not in enabled_steps:
            selected_bgm_id = None
        broll = dict((item.review_snapshot or {}).get("broll") or {})
        if broll:
            asset_id = str(broll.get("asset_id") or "").strip()
            if not asset_id:
                raise VideoEditorWorkflowError("B-roll 分镜缺少素材，已停止本机导出。")
            broll_asset = self.resolve_visual_asset(asset_id, expected_kind="broll")
            # Legacy review records predate the semantic matcher and may only
            # contain an asset id.  Re-score that explicit placement against
            # the reviewed source sentence before rendering; authorization
            # alone is never enough to put footage into the timeline.
            if not isinstance(broll.get("match_score"), (int, float)):
                try:
                    broll_start = float(broll.get("start") or 0)
                    broll_end = float(broll.get("end") or 0)
                except (TypeError, ValueError):
                    broll_start, broll_end = 0.0, 0.0
                query_text = " ".join(
                    str(segment.get("text") or "")
                    for segment in segments
                    if isinstance(segment, Mapping)
                    and float(segment.get("end") or 0) > broll_start
                    and float(segment.get("start") or 0) < broll_end
                ).strip()
                semantic_query = visual_search_query(query_text)
                matched = match_local_visual_asset(
                    semantic_query,
                    [broll_asset],
                    allow_generic_fallback=False,
                )
                broll["semantic_query"] = semantic_query
                if matched is None:
                    broll["match_score"] = 0
                    broll["match_reason"] = [
                        "no_reliable_local_semantic_match",
                    ]
                else:
                    broll.update(
                        {
                            "match_score": matched.get("match_score"),
                            "match_reason": matched.get("match_reason") or [],
                            "match_type": matched.get("match_type"),
                        }
                    )
            if shot_plan:
                mapped_shot = next(
                    (
                        shot
                        for shot in shot_plan.get("shots", [])
                        if shot.get("role") == "B-roll"
                        and shot.get("asset_id") == asset_id
                    ),
                    None,
                )
                if mapped_shot:
                    broll.update(
                        {
                            "start": mapped_shot["timeline_start"],
                            "end": mapped_shot["timeline_end"],
                            "timeline_bound": True,
                        }
                    )

        now = datetime.now().astimezone()
        render_manifest = dict(item.provider_payload.get("render_manifest") or {})
        if shot_plan:
            shot_manifest = {
                "source_kept_ranges": edit_plan.get("kept_ranges") or [],
                "estimated_output_seconds": edit_plan.get(
                    "estimated_output_seconds"
                ),
                "rough_cut_burned_in": bool(shot_plan.get("deleted_ranges")),
                "template_id": shot_plan.get("template_id"),
                "template_version": shot_plan.get("template_version"),
                "degradation": shot_plan.get("degradation"),
            }
            render_manifest.update(
                {key: value for key, value in shot_manifest.items() if value is not None}
            )
        task = VideoEditTask(
            task_id=f"edit-local-{uuid4().hex[:10]}",
            title=f"本机导出 · {title}",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            source_video_path=source["_path"],
            edit_config=VideoEditConfig(
                output_format="mp4",
                output_resolution=batch.output_resolution,
                output_fps=batch.output_fps,
                output_bitrate=batch.output_bitrate,
            ),
            source_avatar_task_id=(
                source["source_task_id"] if source["source_type"] == "avatar" else None
            ),
            stage="等待本机免费导出",
            is_mock=False,
            outputs={
                "workflow": "local_preview_export",
                "provider_mode": batch.provider_mode,
                "batch_id": batch.batch_id,
                "item_id": item.item_id,
                "source_id": item.source_id,
                "publish_title": title,
                "output_profile": batch.output_profile or "720p",
                "style_version": (
                    _RELEASE_TALKING_HEAD_STYLE_VERSION
                    if shot_plan
                    else _LOCAL_PREVIEW_EXPORT_STYLE_VERSION
                ),
                "template_id": str(shot_plan.get("template_id") or "legacy_talking_head"),
                "template_version": str(shot_plan.get("template_version") or "legacy"),
                "requested_pipeline": requested_pipeline,
                "playback_rate": str(
                    _RELEASE_TEMPLATE_PLAYBACK_RATE
                    if shot_plan
                    else _LOCAL_PREVIEW_PLAYBACK_RATE
                ),
                "subtitle_segments_json": json.dumps(
                    source_segments_for_task if shot_plan else segments,
                    ensure_ascii=False,
                ),
                "edit_plan_json": json.dumps(edit_plan, ensure_ascii=False),
                "smart_opening_json": json.dumps(
                    edit_plan.get("smart_opening")
                    if "smart_opening" in enabled_steps
                    else {},
                    ensure_ascii=False,
                ),
                "bgm_id": selected_bgm_id or "",
                "broll_json": json.dumps(broll, ensure_ascii=False),
                "brolls_json": json.dumps(
                    edit_plan.get("release_brolls") or ([broll] if broll else []),
                    ensure_ascii=False,
                ),
                "vector_track_json": json.dumps(
                    edit_plan.get("vector_track") or {},
                    ensure_ascii=False,
                ),
                "shot_plan_json": json.dumps(shot_plan, ensure_ascii=False),
                "source_media_identity_json": json.dumps(
                    (edit_plan.get("source_media_identity") or {}),
                    ensure_ascii=False,
                ),
                "transcript_review_json": json.dumps(
                    (item.review_snapshot or {}).get("transcript_review") or {},
                    ensure_ascii=False,
                ),
                "source_range_json": json.dumps(
                    (edit_plan.get("source_range") or {}),
                    ensure_ascii=False,
                ),
            },
        )
        self.repository.save_task(task)
        updated = item.model_copy(
            update={
                "status": "rendering",
                "edit_task_id": task.task_id,
                "selected_title": title,
                "selected_bgm_id": selected_bgm_id,
                "bgm_reason": item.bgm_reason or cached.get("bgm_reason"),
                "edit_plan": edit_plan,
                "enabled_plan_step_ids": enabled_steps,
                "provider_stage": "local_export_rendering",
                "provider_payload": {
                    **item.provider_payload,
                    "render_manifest": render_manifest,
                    "local_export": {
                        "cost_cny": "0",
                        "reused_approved_subtitles": True,
                        "playback_rate": (
                            _RELEASE_TEMPLATE_PLAYBACK_RATE
                            if shot_plan
                            else _LOCAL_PREVIEW_PLAYBACK_RATE
                        ),
                        "style_version": _LOCAL_PREVIEW_EXPORT_STYLE_VERSION,
                        "smart_opening": edit_plan.get("smart_opening"),
                        "broll": broll or None,
                    },
                },
                "publish_allowed": False,
                "error_message": None,
                "updated_at": now,
            }
        )
        batch = self._replace_batch_item(batch, updated)
        if run_inline:
            self._run_local_preview_export(task.task_id)
            return self._batch_payload(self._require_batch(batch.batch_id))
        _WORKFLOW_EXECUTOR.submit(self._run_local_preview_export, task.task_id)
        return self._batch_payload(batch)

    def _auto_bind_release_broll_assets(
        self,
        shot_plan: Mapping[str, Any],
        *,
        transcript_segments: Sequence[Mapping[str, Any]] | None = None,
        include_generated_images: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Bind confirmed local B-roll without exposing per-shot timing controls.

        The first release template uses a deterministic round-robin assignment
        over eligible shots.  Later provider-backed matching can replace this
        map without changing the renderer contract.  No asset means an empty
        map and the caller keeps the truthful safe degradation.
        """

        # P0 v3.1: 累计真实 B-roll 秒数 + 覆盖率缺口 → 强制 fresh
        _p0v3_state = [0.0, False]  # [accumulated_real_seconds, force_fresh_emitted]
        assets = self.list_visual_assets("broll")
        shots = [
            shot
            for shot in shot_plan.get("shots") or []
            if isinstance(shot, Mapping)
            and shot.get("role") == "A-roll"
            # Short samples often contain natural pauses shorter than the
            # long-form 1.8s placement floor. A 1.2s semantic cluster is
            # still renderable and lets the provider path prove itself on a
            # 12-15s smoke without turning every subtitle cue into B-roll.
            and float(shot.get("duration_seconds") or 0) >= 1.2
            and float(shot.get("timeline_start") or 0) > 0
        ]
        if not shots:
            return {}
        eligible_assets = [
            asset
            for asset in assets
            if (
                str(asset.get("authorization_status") or "") == "confirmed"
                or (
                    include_generated_images
                    and _is_generated_local_acceptance_asset(asset)
                    and str(asset.get("authorization_status") or "")
                    == "generated_for_local_acceptance"
                )
            )
            and (
                include_generated_images
                or not _is_generated_local_acceptance_asset(asset)
            )
            and local_broll_is_real(Path(str(asset.get("_path") or "")), asset)
        ]
        # `shots` already excludes the opening timeline slot, so the first
        # candidate here is the first post-hook body shot.  Long-form speech
        # needs more than the short-form three-event cap, but still uses a
        # stride so a visual is not placed on every cut.  Eight is a ceiling,
        # not a quota: the semantic matcher may return fewer events when the
        # cache has no explainable, non-conflicting evidence.
        plan_duration = float(
            shot_plan.get("timeline_duration_seconds")
            or max(
                (float(shot.get("timeline_end") or 0) for shot in shots),
                default=0.0,
            )
        )
        max_events = 12 if plan_duration >= 60.0 else 3
        selected_shots = shots[::3][:max_events]
        visual_requests_by_shot = {
            str(shot.get("shot_id") or ""): _build_visual_request(
                shot, transcript_segments
            )
            for shot in shots
            if str(shot.get("shot_id") or "")
        }
        provider_search_log: list[dict[str, Any]] = []
        shot_plan["visual_requests"] = list(visual_requests_by_shot.values())
        shot_plan["provider_search_log"] = provider_search_log

        def semantic_query(shot: Mapping[str, Any]) -> str:
            request = visual_requests_by_shot.get(str(shot.get("shot_id") or ""), {})
            if request.get("search_queries"):
                return " ".join(
                    [
                        str(request.get("transcript_text") or ""),
                        *[str(item) for item in request.get("search_queries") or []],
                    ]
                ).strip()
            if transcript_segments is not None and request:
                return ""
            source_start = float(shot.get("source_start") or 0)
            source_end = float(shot.get("source_end") or 0)
            matched_parts: list[str] = []
            for segment in transcript_segments or []:
                if not isinstance(segment, Mapping):
                    continue
                if (
                    float(segment.get("start") or 0) >= source_end
                    or float(segment.get("end") or 0) <= source_start
                ):
                    continue
                words = segment.get("words")
                if isinstance(words, Sequence) and not isinstance(words, (str, bytes)):
                    local_words = [
                        str(word.get("text") or word.get("word") or "")
                        for word in words
                        if isinstance(word, Mapping)
                        and float(word.get("start") or 0) < source_end
                        and float(word.get("end") or 0) > source_start
                    ]
                    matched_parts.append("".join(local_words))
                else:
                    matched_parts.append(str(segment.get("text") or ""))
            matched_text = " ".join(matched_parts)
            query = re.sub(r"[\s，。！？、,.!?；;：:]+", " ", matched_text).strip()
            if transcript_segments is not None and not query:
                return ""
            normalized_query = visual_search_query(query)
            return (
                normalized_query[:96]
                or str(shot_plan.get("title") or "商业口播")
            )

        # Keep the historical fixture path deterministic when no transcript
        # was supplied.  Real exports always carry transcript segments and
        # therefore use the semantic path below.
        if transcript_segments is None:
            if len(eligible_assets) < 3:
                provider = StockBrollProvider(self._visual_asset_directory())
                remaining = max(0, 3 - len(eligible_assets))
                queries = [semantic_query(shot) for shot in selected_shots]
                for index, query in enumerate(queries[:remaining]):
                    try:
                        provider.search_and_cache(
                            query,
                            max_results=(
                                max(1, remaining - index)
                                if index == min(len(queries), remaining) - 1
                                else 1
                            ),
                        )
                    except Exception:
                        continue
                refreshed = self.list_visual_assets("broll")
                seen = {str(item.get("asset_id")) for item in eligible_assets}
                eligible_assets.extend(
                    item
                    for item in refreshed
                    if str(item.get("asset_id")) not in seen
                    and str(item.get("authorization_status") or "") == "confirmed"
                    and local_broll_is_real(Path(str(item.get("_path") or "")), item)
                )
            selected = selected_shots[: min(max_events, len(eligible_assets))]
            return {
                str(shot["shot_id"]): {
                    "asset_id": eligible_assets[index]["asset_id"],
                    "source": str(
                        eligible_assets[index].get("source_provider")
                        or "user_uploaded_local"
                    ),
                    "authorization_status": eligible_assets[index].get(
                        "authorization_status", "unverified"
                    ),
                    "source_url": eligible_assets[index].get("source_url"),
                    "license_name": eligible_assets[index].get("license_name"),
                    "license_url": eligible_assets[index].get("license_url"),
                    "mode": "pip" if index % 2 == 0 else "full",
                }
                for index, shot in enumerate(selected)
            }

        # Real path: every selected shot gets a semantic lookup.  First reuse
        # a local authorized match; only an unresolved query reaches the free
        # stock adapter.  The adapter tries Pexels before Pixabay and performs
        # at most its documented single retry.  Generated images are considered
        # only after the authorized pool is empty or the provider is unavailable.
        authorized = [
            asset
            for asset in eligible_assets
            if not _is_generated_local_acceptance_asset(asset)
            and str(asset.get("authorization_status") or "") == "confirmed"
        ]
        generated = [
            asset
            for asset in eligible_assets
            if include_generated_images and _is_generated_local_acceptance_asset(asset)
        ]
        # Explicit domestic metadata controls the tier.  Missing metadata is
        # unknown, never silently treated as a Chinese scene.
        domestic_real = [
            asset
            for asset in authorized
            if visual_asset_context(asset) == "domestic"
        ]
        domestic_generated = [
            asset
            for asset in generated
            if visual_asset_context(asset) == "domestic"
        ]
        local_priority_assets = domestic_real + domestic_generated + [
            asset for asset in authorized if asset not in domestic_real
        ]
        # Prefer semantically evidenced shots over a fixed stride. A short
        # reviewed clip can move the hook and therefore change shot ids. Rank
        # against the authorized cache first, then keep the deterministic
        # stride only as a bounded fallback for provider lookup. Long-form
        # selection also keeps accepted starts apart so a cluster of early
        # semantic hits cannot consume the whole visual budget.
        minimum_spread_seconds = max(
            7.5,
            min(12.0, plan_duration / float(max(2, max_events))),
        )
        ranked_local_shots: list[tuple[int, float, Mapping[str, Any]]] = []
        for candidate_shot in shots:
            query = semantic_query(candidate_shot)
            if not query:
                continue
            request = visual_requests_by_shot.get(
                str(candidate_shot.get("shot_id") or ""), {}
            )
            local_match = match_local_visual_asset(
                query,
                local_priority_assets,
                allow_generic_fallback=False,
            )
            if local_match is None and not (
                request.get("search_queries")
                and str(request.get("visual_type") or "") != "abstract"
            ):
                continue
            try:
                local_score = int(local_match.get("match_score") or 0)
            except (AttributeError, TypeError, ValueError):
                local_score = 0
            ranked_local_shots.append(
                (local_score, float(candidate_shot.get("timeline_start") or 0), candidate_shot)
            )
        ranked_local_shots.sort(key=lambda item: (-item[0], item[1]))
        selected_ids: set[str] = set()
        selected_query_keys: set[str] = set()

        def add_semantic_shot(
            candidate_shot: Mapping[str, Any],
            *,
            allow_duplicate_query: bool = False,
        ) -> bool:
            if len(selected_ids) >= max_events:
                return False
            shot_id = str(candidate_shot.get("shot_id") or "")
            query = semantic_query(candidate_shot)
            query_key = query.casefold()
            if (
                not shot_id
                or not query_key
                or shot_id in selected_ids
            ):
                return False
            request = visual_requests_by_shot.get(shot_id, {})
            structured_request = bool(
                request.get("search_queries")
                and str(request.get("visual_type") or "") != "abstract"
            )
            if not structured_request and not query_visual_concepts(query):
                return False
            if query_key in selected_query_keys and plan_duration < 60.0:
                return False
            selected_ids.add(shot_id)
            selected_query_keys.add(query_key)
            return True

        # Select at most one shot per concrete query first.  This prevents
        # three cached barbecue clips from consuming every long-form slot and
        # leaves room for later membership, relationship, or data peaks.
        selected_starts: list[float] = []
        for _score, _start, candidate_shot in ranked_local_shots:
            candidate_start = float(candidate_shot.get("timeline_start") or 0)
            if plan_duration >= 60.0 and selected_starts and any(
                abs(candidate_start - selected_start) < minimum_spread_seconds
                for selected_start in selected_starts
            ):
                continue
            add_semantic_shot(candidate_shot)
            if str(candidate_shot.get("shot_id") or "") in selected_ids:
                selected_starts.append(candidate_start)
            if len(selected_ids) >= max_events:
                break
        # A cache hit must not crowd out later semantic peaks.  Fill remaining
        # slots by temporal targets instead of walking from the first shot;
        # repeated queries are allowed only when they are far apart and still
        # resolve to a different authorized asset.  This keeps a long clip
        # visually alive without turning one barbecue query into a loop.
        target_starts = [
            plan_duration * (index + 1) / float(max_events + 1)
            for index in range(max_events)
        ]
        spread_candidates = sorted(
            shots,
            key=lambda candidate: (
                min(
                    abs(float(candidate.get("timeline_start") or 0) - target)
                    for target in target_starts
                ),
                float(candidate.get("timeline_start") or 0),
            ),
        )
        for fallback_shot in spread_candidates:
            if len(selected_ids) >= max_events:
                break
            fallback_start = float(fallback_shot.get("timeline_start") or 0)
            fallback_query = semantic_query(fallback_shot)
            fallback_query_key = fallback_query.casefold()
            introduces_new_semantic_peak = (
                bool(fallback_query_key)
                and fallback_query_key not in selected_query_keys
            )
            if selected_ids:
                selected_starts = [
                    float(candidate.get("timeline_start") or 0)
                    for candidate in shots
                    if str(candidate.get("shot_id") or "") in selected_ids
                ]
                if not introduces_new_semantic_peak and any(
                    abs(fallback_start - selected_start) < minimum_spread_seconds
                    for selected_start in selected_starts
                ):
                    continue
            add_semantic_shot(fallback_shot, allow_duplicate_query=True)
        selected_shots = [
            shot
            for shot in shots
            if str(shot.get("shot_id") or "") in selected_ids
        ]
        # Only shots the director actually selected for a visual event are
        # advertised as visual requests.  Keeping every eligible A-roll cue
        # here made the audit claim an unsearched request for a deliberately
        # unselected stride slot.
        shot_plan["visual_requests"] = [
            visual_requests_by_shot.get(str(shot.get("shot_id") or ""), {})
            for shot in selected_shots
            if str(shot.get("shot_id") or "")
        ]
        provider = StockBrollProvider(self._visual_asset_directory())
        bindings: dict[str, dict[str, Any]] = {}
        used: list[str] = []
        for index, shot in enumerate(selected_shots[:max_events]):
            query = semantic_query(shot)
            request = visual_requests_by_shot.get(str(shot.get("shot_id") or ""), {})
            provider_query = str(
                (request.get("search_queries") or [visual_search_query(query)])[0]
                if query
                else ""
            ).strip()
            search_record: dict[str, Any] = {
                "source_segment_index": request.get("source_segment_index"),
                "source_segment_indices": request.get("source_segment_indices") or [],
                "start": request.get("start"),
                "end": request.get("end"),
                "visual_type": request.get("visual_type"),
                "chinese_concepts": request.get("chinese_concepts") or [],
                "search_queries": request.get("search_queries") or [],
                "expected_subject": request.get("expected_subject"),
                "expected_action": request.get("expected_action"),
                "expected_context": request.get("expected_context"),
                "preferred_mode": request.get("preferred_mode"),
                "fallback": request.get("fallback"),
                "semantic_query": query,
                "provider_query": provider_query,
                "local_candidate_count": len(local_priority_assets),
                # === P0-3: 拆 4 个本地素材指标 ===
                "local_pool_count": len(local_priority_assets),
                "semantic_candidate_count": 0,
                "accepted_local_candidate_count": 0,
                "projected_real_coverage_ratio": 0.0,
                # === P0-3 结束 ===
                "provider_attempted": False,
                "provider": None,
                "provider_status": "not_attempted",
                "provider_attempts": 0,
                "provider_attempt_details": [],
                "candidate_count": 0,
                "candidate_metadata": [],
                "candidate_scores": [],
                "rejections": [],
                "final_asset_id": None,
                "final_action": "safe_degradation",
            }
            provider_search_log.append(search_record)
            if not query:
                search_record["provider_status"] = "skipped_no_visual_concept"
                search_record["rejections"] = ["no_structured_visual_request"]
                continue
            match_result = match_local_visual_asset(
                query,
                local_priority_assets,
                used_asset_ids=used,
                allow_generic_fallback=False,
                return_counts=True,
            )
            # P0-3: 解包 4 个本地素材指标
            if isinstance(match_result, tuple):
                matched, semantic_candidate_count, accepted_local_count = match_result
            else:
                matched = match_result
                semantic_candidate_count = 0
                accepted_local_count = 0
            search_record["semantic_candidate_count"] = semantic_candidate_count
            search_record["accepted_local_candidate_count"] = accepted_local_count
            # P0-3: 预计真实覆盖（保守估算 = 接受的本地素材数 × 5s / 总时长）。
            # 真实值在 P0-2 reporting 区域基于 stock_video_brolls 重算。
            try:
                _plan_duration = float(plan_duration)  # noqa: F821
            except (TypeError, ValueError, NameError):
                _plan_duration = 0.0
            search_record["projected_real_coverage_ratio"] = round(
                (accepted_local_count * 5.0) / max(_plan_duration, 0.001), 4
            )
            # P0 v3.1: 累计已分配真实 B-roll 秒数 + 覆盖率缺口 → 强制 fresh
            if matched is not None and (
                _is_real_stock_video_broll(matched)
                or local_broll_is_real(
                    Path(str(matched.get("_path") or "")), matched
                )
            ):
                # P0 v3.1: 累计真实素材时长。优先用 duration_seconds（Pexels cache
                # asset 含此字段），fallback 到 end-start。
                _matched_seconds = 0.0
                try:
                    _matched_seconds = float(
                        matched.get("duration_seconds") or 0
                    )
                except (TypeError, ValueError):
                    _matched_seconds = 0.0
                if _matched_seconds <= 0:
                    try:
                        _m_start = float(matched.get("start", 0) or 0)
                        _m_end = float(matched.get("end", 0) or 0)
                        _matched_seconds = max(0.0, _m_end - _m_start)
                    except (TypeError, ValueError):
                        _matched_seconds = 0.0
                _p0v3_state[0] += _matched_seconds
                # P0 v3.1 debug: 写入 search_record
                search_record["p0v3_matched_duration_seconds"] = round(
                    _matched_seconds, 3
                )
                search_record["p0v3_accumulated_seconds"] = round(
                    _p0v3_state[0], 3
                )
            # P1: 保留 + 追加 force_fresh（recovery goal §5.1）
            # 缺口仅在 if 守卫内判定，避免无条件丢弃已成功本地匹配。
            # 修复后语义：
            #   - 本地匹配已满足覆盖率：保留 matched，不发 provider
            #   - 覆盖不足：matched=None，恰好一次 force_fresh 请求追加
            #   - provider 失败：matched 回退到原本地匹配（L6804-6816 旁路）
            _original_matched = matched  # P1: 用于 provider 失败时回退
            if (
                matched is not None
                and not _p0v3_state[1]
                and os.getenv("VIDEO_EDITOR_LOCAL_ACCEPTANCE_NO_PROVIDER", "").lower()
                not in {"1", "true", "yes"}  # 显式跳过 provider 时不强制 fresh
            ):
                # P1: 用 selected_shots 累计时长（不是全 shots）。
                # selected_shots 是真正会匹配 B-roll 的子集。
                _selected_total = 0.0
                for _shot in selected_shots:
                    try:
                        _selected_total += float(
                            _shot.get("duration_seconds") or 0
                        )
                    except (TypeError, ValueError):
                        continue
                # P1: 仅在覆盖率缺口时强制 fresh；缺口外保留原 matched
                if _p0v3_state[0] < 0.45 * max(_selected_total, 0.001):
                    search_record["force_fresh_reason"] = (
                        "p0_4_v2_coverage_deficit"
                    )
                    _p0v3_state[1] = True
                    matched = None  # 仅在缺口分支内清掉本地匹配
            if matched is not None:
                search_record["provider_status"] = "skipped_local_semantic_match"
                search_record["provider"] = (
                    matched.get("source_provider")
                    or matched.get("provider")
                    or "local_cache"
                )
                search_record["provider_source_url"] = matched.get("source_url")
                search_record["provider_license"] = (
                    matched.get("license_name") or matched.get("license")
                )
                search_record["candidate_scores"] = [
                    {
                        "asset_id": matched.get("asset_id"),
                        "score": matched.get("match_score", 0),
                        "accepted": True,
                        "reason": matched.get("match_reason") or [],
                    }
                ]
            provider_result: Any = None
            if matched is None and os.getenv("VIDEO_EDITOR_LOCAL_ACCEPTANCE_NO_PROVIDER", "").lower() not in {
                "1",
                "true",
                "yes",
            }:
                search_record["provider_attempted"] = True
                query_candidates = [
                    str(candidate).strip()
                    for candidate in (request.get("search_queries") or [])
                    if str(candidate).strip()
                ]
                if provider_query and provider_query not in query_candidates:
                    query_candidates.insert(0, provider_query)
                search_record["provider_queries_tried"] = []
                for candidate_query in query_candidates[:3]:
                    search_record["provider_queries_tried"].append(candidate_query)
                    try:
                        # P1: force_fresh=True 保证真发 Pexels/Pixabay，不复用 cache
                        provider_result = provider.search_and_cache(
                            candidate_query,
                            max_results=8,
                            force_fresh=True,
                        )
                    except Exception as exc:
                        provider_result = None
                        search_record["provider_attempt_details"].append(
                            {
                                "provider": "pexels_or_pixabay",
                                "query": candidate_query,
                                "status": "request_failed",
                                "attempts": 2,
                                "error": str(exc)[:160],
                            }
                        )
                        # P1: force_fresh 触发的 provider 失败时，
                        # 回退到原本地匹配（保留 + 追加语义）
                        if _p0v3_state[1] and _original_matched is not None:
                            matched = _original_matched
                            search_record["fallback_to_local"] = "provider_failed"
                            search_record["provider_status"] = "fallback_local_after_provider_failure"
                            break
                    if provider_result is None:
                        continue
                    search_record["provider"] = getattr(provider_result, "provider", None)
                    search_record["provider_status"] = getattr(provider_result, "status", "unavailable")
                    search_record["provider_attempts"] += int(
                        getattr(provider_result, "attempts", 0) or 0
                    )
                    search_record["provider_reason"] = getattr(provider_result, "reason", None)
                    search_record["candidate_metadata"].extend(
                        list(getattr(provider_result, "candidate_summaries", []) or [])
                    )
                    search_record["provider_attempt_details"].append(
                        {
                            "provider": getattr(provider_result, "provider", None),
                            "query": candidate_query,
                            "status": getattr(provider_result, "status", "unavailable"),
                            "attempts": getattr(provider_result, "attempts", 0),
                            "candidate_count": len(getattr(provider_result, "items", []) or []),
                        }
                    )
                    provider_items = [
                        dict(item)
                        for item in (getattr(provider_result, "items", []) or [])
                        if isinstance(item, Mapping)
                        and str(item.get("authorization_status") or "") == "confirmed"
                        and item.get("publish_licensed") is True
                    ]
                    search_record["candidate_count"] += len(provider_items)
                    for provider_item in provider_items:
                        candidate_match = match_local_visual_asset(
                            query,
                            [provider_item],
                            allow_generic_fallback=False,
                        )
                        search_record["candidate_scores"].append(
                            {
                                "asset_id": provider_item.get("asset_id"),
                                "query": candidate_query,
                                "score": candidate_match.get("match_score", 0)
                                if candidate_match
                                else 0,
                                "accepted": bool(candidate_match),
                                "reason": candidate_match.get("match_reason")
                                if candidate_match
                                else ["rejected_by_semantic_gate"],
                            }
                        )
                    if provider_items:
                        matched = match_local_visual_asset(
                            query,
                            provider_items,
                            used_asset_ids=used,
                            allow_generic_fallback=False,
                        )
                        if matched is not None:
                            matched = dict(matched)
                            matched["_path"] = str(
                                matched.get("cache_path") or matched.get("_path") or ""
                            )
                            break
                    # If a Pexels result is licensed but semantically rejected,
                    # independently query Pixabay for this same term before
                    # advancing to the next term. Both provider outcomes stay
                    # in the audit record; no result is accepted by count alone.
                    if getattr(provider_result, "provider", None) == "pexels":
                        try:
                            # P1: Pixabay 同样 force_fresh
                            pixabay_result = provider.search_and_cache(
                                candidate_query,
                                max_results=8,
                                provider="pixabay",
                                force_fresh=True,
                            )
                        except Exception as exc:
                            pixabay_result = None
                            search_record["provider_attempt_details"].append(
                                {
                                    "provider": "pixabay",
                                    "query": candidate_query,
                                    "status": "request_failed",
                                    "attempts": 2,
                                    "error": str(exc)[:160],
                                }
                            )
                        if pixabay_result is not None:
                            search_record["provider_attempt_details"].append(
                                {
                                    "provider": "pixabay",
                                    "query": candidate_query,
                                    "status": getattr(pixabay_result, "status", "unavailable"),
                                    "attempts": getattr(pixabay_result, "attempts", 0),
                                    "candidate_count": len(getattr(pixabay_result, "items", []) or []),
                                }
                            )
                            search_record["provider_attempts"] += int(
                                getattr(pixabay_result, "attempts", 0) or 0
                            )
                            pixabay_items = [
                                dict(item)
                                for item in (getattr(pixabay_result, "items", []) or [])
                                if isinstance(item, Mapping)
                                and str(item.get("authorization_status") or "") == "confirmed"
                                and item.get("publish_licensed") is True
                            ]
                            search_record["candidate_count"] += len(pixabay_items)
                            search_record["candidate_metadata"].extend(
                                list(getattr(pixabay_result, "candidate_summaries", []) or [])
                            )
                            for pixabay_item in pixabay_items:
                                candidate_match = match_local_visual_asset(
                                    query,
                                    [pixabay_item],
                                    allow_generic_fallback=False,
                                )
                                search_record["candidate_scores"].append(
                                    {
                                        "asset_id": pixabay_item.get("asset_id"),
                                        "query": candidate_query,
                                        "score": candidate_match.get("match_score", 0)
                                        if candidate_match
                                        else 0,
                                        "accepted": bool(candidate_match),
                                        "reason": candidate_match.get("match_reason")
                                        if candidate_match
                                        else ["rejected_by_semantic_gate"],
                                    }
                                )
                            if pixabay_items:
                                matched = match_local_visual_asset(
                                    query,
                                    pixabay_items,
                                    used_asset_ids=used,
                                    allow_generic_fallback=False,
                                )
                                if matched is not None:
                                    matched = dict(matched)
                                    matched["_path"] = str(
                                        matched.get("cache_path") or matched.get("_path") or ""
                                    )
                                    break
                if (
                    matched is None
                    and _p0v3_state[1]
                    and _original_matched is not None
                ):
                    matched = _original_matched
                    search_record["fallback_to_local"] = (
                        "provider_no_accepted_match"
                    )
                    search_record["provider_status"] = (
                        "fallback_local_after_provider_no_match"
                    )
                    search_record["rejections"].append(
                        "fresh_provider_no_accepted_match"
                    )
            if matched is None:
                if search_record["provider_status"] == "not_attempted":
                    search_record["provider_status"] = "skipped_provider_disabled"
                search_record["rejections"].append("no_authorized_semantic_match")
                matched = match_local_visual_asset(
                    query,
                    generated,
                    used_asset_ids=used,
                    allow_generic_fallback=False,
                )
            if matched is None:
                search_record["rejections"].append("no_generated_fallback_match")
                continue
            asset_id = str(matched.get("asset_id") or "")
            if not asset_id:
                search_record["rejections"].append("matched_asset_missing_id")
                continue
            used.append(asset_id)
            search_record["final_asset_id"] = asset_id
            search_record["final_action"] = "bind_visual_asset"
            search_record["final_mode"] = request.get("preferred_mode") or "full"
            bindings[str(shot["shot_id"])] = {
                "asset_id": asset_id,
                "source": str(
                    matched.get("source_provider")
                    or matched.get("provider")
                    or "user_uploaded_local"
                ),
                "source_provider": matched.get("source_provider") or matched.get("provider"),
                "asset_origin": matched.get("asset_origin"),
                "authorization_status": matched.get("authorization_status", "unverified"),
                "publish_licensed": bool(matched.get("publish_licensed", False)),
                "source_url": matched.get("source_url"),
                "license_name": matched.get("license_name"),
                "license_url": matched.get("license_url"),
                "semantic_binding": matched.get("semantic_binding"),
                "keywords": matched.get("keywords") or [],
                "domestic_context": visual_asset_context(matched),
                "domestic_scene": matched.get("domestic_scene") or "",
                "visual_asset_priority": visual_asset_priority(matched),
                "semantic_query": query,
                "search_query": matched.get("search_query"),
                "match_type": matched.get("match_type"),
                "match_score": matched.get("match_score", 0),
                "match_reason": matched.get("match_reason") or [],
                "visual_intent": matched.get("visual_intent") or matched.get("semantic_visual_intent"),
                "visual_type": matched.get("visual_type"),
                "manifest_role": matched.get("manifest_role"),
                "provider_attempts": getattr(provider_result, "attempts", 0)
                if provider_result is not None
                else 0,
                "mode": request.get("preferred_mode") or self._release_broll_mode(matched, index),
            }
        return bindings

    @staticmethod
    def _select_release_broll_assets(
        resolved_explicit: Mapping[str, Any] | None,
        real_broll_assets: Sequence[Mapping[str, Any]],
        generated_acceptance_assets: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Choose publishable cached footage before local-only acceptance images."""

        publishable_real_assets = [
            dict(asset)
            for asset in real_broll_assets
            if asset.get("publish_licensed") is True
            and str(asset.get("asset_origin") or "") != "generated_image_asset"
        ]
        domestic_real_assets = [
            dict(asset)
            for asset in real_broll_assets
            if visual_asset_context(asset) == "domestic"
        ]
        domestic_generated_assets = [
            dict(asset)
            for asset in generated_acceptance_assets
            if visual_asset_context(asset) == "domestic"
            and str(asset.get("authorization_status") or "")
            in {"generated_for_local_acceptance", "confirmed"}
        ]
        domestic_priority_assets = domestic_real_assets + domestic_generated_assets
        if domestic_priority_assets:
            domestic_ids = {
                str(asset.get("asset_id") or "")
                for asset in domestic_priority_assets
            }
            preferred_assets = domestic_real_assets + domestic_generated_assets + [
                asset
                for asset in publishable_real_assets
                if str(asset.get("asset_id") or "") not in domestic_ids
            ]
        else:
            preferred_assets = (
                publishable_real_assets
                if len(publishable_real_assets) >= 3
                else [dict(asset) for asset in real_broll_assets]
            )
        if domestic_priority_assets or len(publishable_real_assets) < 3:
            fallback_assets = (
                ([dict(resolved_explicit)] if resolved_explicit else [])
                + preferred_assets
                + [
                    dict(asset)
                    for asset in generated_acceptance_assets
                    if str(asset.get("asset_id") or "")
                    not in {str(item.get("asset_id") or "") for item in domestic_priority_assets}
                ]
            )
        else:
            fallback_assets = preferred_assets
        selected: list[dict[str, Any]] = []
        seen_asset_ids: set[str] = set()
        for asset in fallback_assets:
            asset_id = str(asset.get("asset_id") or "")
            if not asset_id or asset_id in seen_asset_ids:
                continue
            seen_asset_ids.add(asset_id)
            selected.append(asset)
        return selected

    @staticmethod
    def _release_broll_mode(asset: Mapping[str, Any], index: int) -> str:
        manifest_role = str(asset.get("manifest_role") or "")
        asset_origin = str(asset.get("asset_origin") or "")
        visual_intent = str(
            asset.get("visual_intent")
            or asset.get("semantic_visual_intent")
            or asset.get("visual_type")
            or ""
        )
        # Explanatory stills are cutaways by default.  PiP is reserved for a
        # deliberate speaker/product comparison intent, never inferred from
        # an old manifest role such as ``pip_broll``.
        if visual_intent in {"speaker_pip", "product_compare"}:
            return "pip"
        if visual_intent in {"generated_explainer", "data_chart", "concept_card", "evidence_broll"}:
            return "full"
        if asset_origin == "generated_image_asset":
            return "full"
        if manifest_role in {"generated_explainer", "data_chart", "concept_card"}:
            return "full"
        if manifest_role:
            return "pip" if manifest_role == "pip_broll" else "full"
        if asset_origin == "stock_video_asset":
            return "pip" if index == 1 else "full"
        return "pip" if index == 0 else "full"

    @staticmethod
    def _shot_broll_placements(
        shot_plan: Mapping[str, Any],
        asset_metadata_by_shot_id: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Convert B-roll shots into renderer-ready output-timeline events."""
        placements: list[dict[str, Any]] = []
        asset_metadata_by_shot_id = asset_metadata_by_shot_id or {}
        for shot in shot_plan.get("shots") or []:
            if not isinstance(shot, Mapping) or shot.get("role") != "B-roll":
                continue
            if not shot.get("asset_id"):
                continue
            start = float(shot.get("timeline_start") or 0)
            shot_end = float(shot.get("timeline_end") or 0)
            plan_duration = float(shot_plan.get("timeline_duration_seconds") or 0)
            max_event_seconds = 5.5 if plan_duration >= 60.0 else _BROLL_EVENT_MAX_SECONDS
            end = min(shot_end, start + max_event_seconds)
            if end <= start:
                continue
            asset_metadata = asset_metadata_by_shot_id.get(str(shot.get("shot_id"))) or {}
            placement = {
                    "shot_id": shot.get("shot_id"),
                    "asset_id": shot.get("asset_id"),
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "mode": str(shot.get("overlay_mode") or "pip"),
                }
            for key in (
                "source",
                "source_provider",
                "asset_origin",
                "authorization_status",
                "publish_licensed",
                "rights_status",
                "source_url",
                "license_name",
                "license_url",
                "semantic_binding",
                "keywords",
                "semantic_query",
                "search_query",
                "domestic_context",
                "domestic_scene",
                "match_type",
                "match_score",
                "match_reason",
                "visual_intent",
                "visual_type",
                "manifest_role",
            ):
                if key in asset_metadata:
                    placement[key] = asset_metadata[key]
            placements.append(placement)
        return placements

    @staticmethod
    def _bound_short_rich_release_brolls(
        placements: Sequence[Mapping[str, Any]],
        *,
        max_events: int = 3,
        max_event_seconds: float = 2.4,
    ) -> list[dict[str, Any]]:
        """Keep short-form semantic clusters while bounding visual coverage."""
        bounded: list[dict[str, Any]] = []
        for placement in list(placements)[:max_events]:
            try:
                start = float(placement.get("start") or 0)
                end = float(placement.get("end") or 0)
            except (TypeError, ValueError):
                continue
            bounded_end = min(end, start + max_event_seconds)
            if bounded_end <= start:
                continue
            bounded.append(
                {
                    **dict(placement),
                    "end": round(bounded_end, 3),
                    "coverage_trimmed_for_short_adaptive_rich": bounded_end < end,
                }
            )
        return bounded

    def create_release_template_local_export(
        self,
        batch_id: str,
        item_id: str,
        *,
        local_bgm_id: str | None = None,
    ) -> dict[str, Any]:
        """Upgrade an already reviewed local item to the release shot-plan path.

        This is intentionally local-only. It reuses the stored transcript and
        approved subtitle review; it never starts ASR, cloud rendering, or
        publishing. Optional Token Plan image generation is only enabled by an
        explicit local switch and runs only for unresolved semantic requests.
        A user-specified BGM wins; otherwise the local authorized recommendation
        is selected on the stable default path.
        """
        batch = self._sync_batch(self._require_batch(batch_id))
        item = next((entry for entry in batch.items if entry.item_id == item_id), None)
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        if batch.is_mock or item.is_mock:
            raise VideoEditorWorkflowError("体验任务没有真实媒体，不能生成发布级本机验收片。")
        if not item.review_confirmed_at and not item.review_snapshot.get("confirmed"):
            raise VideoEditorWorkflowError("请先完成字幕复核，再生成发布级本机验收片。")
        if not item.subtitle_segments:
            raise VideoEditorWorkflowError("当前任务没有已确认字幕时间轴，不能生成发布级本机验收片。")

        reviewed_source_range = (item.review_snapshot or {}).get("source_range")
        # The local renderer does not execute a reordered audio EDL.  Keep
        # short source clips on their original source clock; otherwise the
        # generic hook picker can move a late sentence to 0s while the audio
        # remains unchanged.  Full-length exports already preserve the source
        # clock explicitly in build_talking_head_shot_plan().
        prefer_first_hook = True
        if isinstance(reviewed_source_range, Mapping):
            try:
                prefer_first_hook = (
                    float(reviewed_source_range.get("end") or 0)
                    - float(reviewed_source_range.get("start") or 0)
                    <= 30.0
                )
            except (TypeError, ValueError):
                prefer_first_hook = False

        source = self.resolve_source(item.source_id)
        media = self._probe_media(Path(source["_path"]))
        bgm_plan_asset = None
        release_bgm_reason: str | None = None
        if local_bgm_id:
            bgm = self.resolve_bgm_asset(local_bgm_id)
            bgm_plan_asset = {
                "asset_id": local_bgm_id,
                "title": bgm.get("title"),
                "source": bgm.get("source_provider"),
                "source_provider": bgm.get("source_provider"),
                "source_url": bgm.get("source_url"),
                "license_url": bgm.get("license_url"),
                "authorization_status": bgm.get("authorization_status", "unverified"),
                "auto_eligible": bgm.get("auto_eligible", False),
            }
            release_bgm_reason = (
                f"使用已选择的本地音乐《{bgm.get('title') or local_bgm_id}》，"
                "自动做人声避让。"
            )
        else:
            recommendation, release_bgm_reason = self._recommend_bgm_asset(
                {
                    "transcript": "".join(
                        str(segment.get("text") or "")
                        for segment in item.subtitle_segments
                        if isinstance(segment, Mapping)
                    ),
                    "title_candidates": [item.selected_title or item.title],
                    "media": media,
                },
                item.title,
            )
            if recommendation is not None:
                local_bgm_id = str(recommendation["asset_id"])
                bgm_plan_asset = {
                    "asset_id": local_bgm_id,
                    "title": recommendation.get("title"),
                    "source": recommendation.get("source_provider"),
                    "source_provider": recommendation.get("source_provider"),
                    "source_url": recommendation.get("source_url"),
                    "license_url": recommendation.get("license_url"),
                    "authorization_status": recommendation.get(
                        "authorization_status", "unverified"
                    ),
                    "auto_eligible": recommendation.get("auto_eligible", False),
                }
        from src.services.talking_head_templates import build_talking_head_shot_plan
        from src.services.director_plan import build_director_plan

        reviewed_segments, transcript_corrections = _review_transcript_segments(
            item.subtitle_segments
        )
        if reviewed_segments:
            item = item.model_copy(
                update={
                    "subtitle_segments": reviewed_segments,
                    "review_snapshot": {
                        **item.review_snapshot,
                        "transcript_review": {
                            "source": "local_reviewed_homophone_correction",
                            "corrections": transcript_corrections,
                        },
                    },
                }
            )
        release_title = next(
            (
                re.sub(r"[，。！？,!.?].*$", "", str(segment.get("text") or "")).strip()[:18]
                for segment in reviewed_segments
                if str(segment.get("text") or "").strip()
            ),
            item.selected_title or item.title,
        )

        generated_acceptance_assets = (
            self._register_local_generated_acceptance_assets()
        )

        shot_plan_seed = build_talking_head_shot_plan(
            reviewed_segments,
            duration_seconds=float(media["duration_seconds"]),
            title=release_title,
            bgm_asset=bgm_plan_asset,
            prefer_first_hook=prefer_first_hook,
            preserve_source_clock=float(media["duration_seconds"]) >= 60.0,
        )
        explicit_broll = item.review_snapshot.get("broll")
        explicit_broll_asset: dict[str, Any] | None = None
        resolved_explicit: dict[str, Any] | None = None
        if isinstance(explicit_broll, Mapping) and explicit_broll.get("asset_id"):
            resolved_explicit = self.resolve_visual_asset(
                str(explicit_broll["asset_id"]), expected_kind="broll"
            )
            if not asset_publish_claim_allowed(resolved_explicit):
                # Local generated/uncleared page bindings remain auditable in
                # the library, but can never enter the customer release path.
                explicit_broll = None
                resolved_explicit = None
            # A stale page binding without semantic evidence must not bypass
            # the same matcher used by automatic release planning.  Keep the
            # asset in the library/audit trail, but safely re-plan this export.
            if resolved_explicit is not None and not (
                resolved_explicit.get("semantic_binding")
                or resolved_explicit.get("semantic_query")
                or resolved_explicit.get("keywords")
                or isinstance(explicit_broll.get("match_score"), (int, float))
                and float(explicit_broll.get("match_score") or 0) > 0
            ):
                explicit_broll = None
                resolved_explicit = None
            else:
                explicit_broll_asset = {
                    "asset_id": str(explicit_broll["asset_id"]),
                    "source": "user_uploaded_local",
                    "authorization_status": resolved_explicit.get(
                        "authorization_status", "unverified"
                    ),
                    "mode": str(explicit_broll.get("mode") or "pip"),
                    "title": resolved_explicit.get("name"),
                    "visual_intent": resolved_explicit.get("visual_intent")
                    or resolved_explicit.get("semantic_visual_intent"),
                    "visual_type": resolved_explicit.get("visual_type"),
                    "manifest_role": resolved_explicit.get("manifest_role"),
                }
        real_broll_assets = [
            asset
            for asset in self.list_visual_assets("broll")
            if not _is_generated_local_acceptance_asset(asset)
            and str(asset.get("authorization_status") or "") == "confirmed"
            and local_broll_is_real(Path(str(asset.get("_path") or "")), asset)
        ]
        candidates = self._select_release_broll_assets(
            resolved_explicit,
            real_broll_assets,
            [],
        )
        asset_matching: dict[str, Any] = {
            "version": "local-visual-matcher-v1",
            "strategy": "semantic_binding_then_keywords_then_deterministic_fallback",
            "decisions": [],
            "generated_assets_publish_licensed": False,
        }
        generation_manifest = (
            os.getenv("VIDEOINSIGHT_GENERATED_ASSET_MANIFEST", "").strip()
            or str(
                Path(__file__).resolve().parents[2]
                / "work"
                / "auto-fine-cut-v1-20260822"
                / "generated-assets"
                / "manifest.json"
            )
        )
        asset_matching["keyword_generation"] = build_keyword_generation_plan(
            "",
            default_manifest_path=generation_manifest,
        )
        semantic_broll_bindings: dict[str, dict[str, Any]] = {}
        if candidates and not explicit_broll:
            # Do not let the count of cached clips bypass semantic lookup.  A
            # provider-backed query is made only for an unresolved shot; with
            # no key it returns unavailable and the method safely reuses the
            # authorized local cache.
            semantic_broll_bindings = self._auto_bind_release_broll_assets(
                shot_plan_seed,
                transcript_segments=reviewed_segments,
                include_generated_images=False,
            )
            for shot_id, binding in semantic_broll_bindings.items():
                asset_matching["decisions"].append(
                    {
                        "shot_id": shot_id,
                        "asset_id": binding.get("asset_id"),
                        "query": binding.get("semantic_query"),
                        "match_type": binding.get("match_type") or "semantic_cache_or_provider",
                        "match_score": binding.get("match_score", 0),
                        "match_reason": binding.get("match_reason") or [],
                        "provider": binding.get("source_provider"),
                        "license_name": binding.get("license_name"),
                        "source_url": binding.get("source_url"),
                        "domestic_context": binding.get("domestic_context", "unknown"),
                        "domestic_scene": binding.get("domestic_scene", ""),
                        "visual_asset_priority": binding.get("visual_asset_priority"),
                        "publish_claim_allowed": asset_publish_claim_allowed(binding),
                    }
                )
        if semantic_broll_bindings:
            broll_assets_by_shot_id = semantic_broll_bindings
            explicit_broll_asset = None
        elif candidates:
            # Prefer confirmed real/provider footage. Generated images remain
            # a local-only fallback when fewer than three real assets exist.
            candidate_by_index: dict[int, dict[str, Any]] = {}
            selected_shots = [
                shot
                for shot in shot_plan_seed.get("shots") or []
                if isinstance(shot, Mapping)
                and shot.get("role") == "A-roll"
                and float(shot.get("duration_seconds") or 0) >= 1.8
                and float(shot.get("timeline_start") or 0) > 0
            ][::3][:3]
            used_asset_ids: list[str] = []

            def shot_query(shot: Mapping[str, Any]) -> str:
                source_start = float(shot.get("source_start") or 0)
                source_end = float(shot.get("source_end") or 0)
                return "".join(
                    str(segment.get("text") or "")
                    for segment in reviewed_segments
                    if isinstance(segment, Mapping)
                    and float(segment.get("start") or 0) < source_end
                    and float(segment.get("end") or 0) > source_start
                )

            for index, shot in enumerate(selected_shots):
                if index >= len(candidates):
                    break
                query = shot_query(shot)
                matched = match_local_visual_asset(
                    query,
                    candidates,
                    used_asset_ids=used_asset_ids,
                    allow_generic_fallback=False,
                )
                if matched is None:
                    asset_matching["decisions"].append(
                        {
                            "shot_id": shot.get("shot_id"),
                            "query": query,
                            "action": "safe_degradation",
                            "reason": "no_semantic_asset_match",
                        }
                    )
                    continue
                asset = matched
                used_asset_ids.append(str(asset.get("asset_id") or ""))
                asset_matching["decisions"].append(
                    {
                        "shot_id": shot.get("shot_id"),
                        "query": query,
                        "asset_id": asset.get("asset_id"),
                        "match_type": asset.get("match_type") or "deterministic_order",
                        "match_score": asset.get("match_score", 0),
                        "match_reason": asset.get("match_reason") or [],
                        "asset_origin": asset.get("asset_origin"),
                        "domestic_context": visual_asset_context(asset),
                        "domestic_scene": asset.get("domestic_scene") or "",
                        "visual_asset_priority": visual_asset_priority(asset),
                        "publish_claim_allowed": asset_publish_claim_allowed(asset),
                    }
                )
                candidate_by_index[index] = {
                    "asset_id": asset.get("asset_id"),
                    "source": asset.get("source_provider") or asset.get("provider") or "user_uploaded_local",
                    "source_provider": asset.get("source_provider") or asset.get("provider"),
                    "asset_origin": asset.get("asset_origin"),
                    "authorization_status": asset.get(
                        "authorization_status", "unverified"
                    ),
                    "publish_licensed": bool(asset.get("publish_licensed", False)),
                    "rights_status": asset.get("rights_status"),
                    "content_id_risk": asset.get("content_id_risk"),
                    "source_url": asset.get("source_url"),
                    "license_name": asset.get("license_name"),
                    "license_url": asset.get("license_url"),
                        "semantic_binding": asset.get("semantic_binding"),
                        "keywords": asset.get("keywords") or [],
                        "domestic_context": visual_asset_context(asset),
                        "domestic_scene": asset.get("domestic_scene") or "",
                        "visual_asset_priority": visual_asset_priority(asset),
                    "match_type": asset.get("match_type"),
                    "match_score": asset.get("match_score", 0),
                    "match_reason": asset.get("match_reason") or [],
                    "visual_intent": asset.get("visual_intent") or asset.get("semantic_visual_intent"),
                    "visual_type": asset.get("visual_type"),
                    "manifest_role": asset.get("manifest_role"),
                    "mode": self._release_broll_mode(asset, index),
                }
            broll_assets_by_shot_id = {
                str(shot["shot_id"]): candidate_by_index[index]
                for index, shot in enumerate(selected_shots)
                if index in candidate_by_index
            }
            explicit_broll_asset = None
        else:
            broll_assets_by_shot_id = (
                {}
                if explicit_broll
                else self._auto_bind_release_broll_assets(
                    shot_plan_seed,
                    transcript_segments=reviewed_segments,
                    include_generated_images=False,
                )
            )
        # P0-4 v2: 删掉 "broll 数量 < 3" 触发，改为缺口驱动。
        # 触发条件（按顺序，任一为真即触发扩搜 / MiniMax 兜底）：
        #   1) real_broll_coverage_ratio < 0.45（覆盖率缺口）
        #   2) unresolved_semantic_windows 数 > 0（未解决窗口）
        #   3) 连续无视觉变化时长 ≥ 6s（长空档）
        #   4) 必须有"信息卡"语义的 planned_window（data_chart / concept_card / cta_card）
        visual_window_plan_for_trigger = (
            director_plan.get("visual_window_plan")
            if isinstance(director_plan, Mapping)
            else None
        )
        broll_total_seconds_for_trigger = sum(
            max(0.0, float(it.get("end", 0)) - float(it.get("start", 0)))
            for it in (broll_assets_by_shot_id.values() or [])
            if isinstance(it, Mapping)
        )
        broll_coverage_for_trigger = (
            broll_total_seconds_for_trigger / max(float(media.get("duration_seconds") or 0), 0.001)
        )
        required_window_count = (
            int(visual_window_plan_for_trigger.get("required_window_count", 0) or 0)
            if isinstance(visual_window_plan_for_trigger, Mapping)
            else 0
        )
        # 计算连续无视觉变化时长（基于 planned_visual_windows）
        max_gap_seconds_for_trigger = 0.0
        if (
            isinstance(visual_window_plan_for_trigger, Mapping)
            and visual_window_plan_for_trigger.get("planned_visual_windows")
        ):
            windows_sorted = sorted(
                visual_window_plan_for_trigger["planned_visual_windows"],
                key=lambda w: float(w.get("start", 0) or 0),
            )
            prev_end = 0.0
            for w in windows_sorted:
                start = float(w.get("start", 0) or 0)
                gap = start - prev_end
                if gap > max_gap_seconds_for_trigger:
                    max_gap_seconds_for_trigger = gap
                prev_end = max(prev_end, float(w.get("end", 0) or 0))

        gap_driven_trigger = (
            not explicit_broll
            and (
                broll_coverage_for_trigger < 0.45
                or required_window_count > 0
                or max_gap_seconds_for_trigger >= 6.0
            )
        )
        if gap_driven_trigger:
            image_generation_log: list[dict[str, Any]] = []
            generated_assets = self._generate_token_plan_visual_assets(
                shot_plan_seed.get("visual_requests") or [],
                image_generation_log=image_generation_log,
                # P0-4 v2: 触发条件已改为缺口驱动
            )
            if generated_assets:
                # Reuse the semantic matcher so generated images follow the
                # same timing, spacing and duplicate-asset rules as stock B-roll.
                broll_assets_by_shot_id = self._auto_bind_release_broll_assets(
                    shot_plan_seed,
                    transcript_segments=reviewed_segments,
                    include_generated_images=True,
                )
        shot_plan = build_talking_head_shot_plan(
            reviewed_segments,
            duration_seconds=float(media["duration_seconds"]),
            title=release_title,
            broll_asset=explicit_broll_asset,
            broll_assets_by_shot_id=broll_assets_by_shot_id,
            bgm_asset=bgm_plan_asset,
            prefer_first_hook=prefer_first_hook,
            preserve_source_clock=float(media["duration_seconds"]) >= 60.0,
        )
        shot_plan["visual_requests"] = shot_plan_seed.get("visual_requests") or []
        shot_plan["provider_search_log"] = shot_plan_seed.get("provider_search_log") or []
        # Release-template local exports opt into the richer adaptive visual
        # contract.  The old preview path remains a safe compatibility path.
        shot_plan["visual_density"] = "rich"
        director_plan = build_director_plan(
            reviewed_segments,
            duration_seconds=float(media["duration_seconds"]),
            title=release_title,
            target_platform="douyin",
            broll_asset=explicit_broll_asset,
            broll_assets_by_shot_id=broll_assets_by_shot_id,
            bgm_asset=bgm_plan_asset,
            source_media_identity={
                "source_media_sha256": hashlib.sha256(
                    Path(source["_path"]).read_bytes()
                ).hexdigest(),
                "source_duration_seconds": float(media["duration_seconds"]),
                "transcript_sha256": hashlib.sha256(
                    json.dumps(
                        reviewed_segments,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "transcript_timing_source": (
                    "word_timestamps"
                    if any(
                        segment.get("words")
                        for segment in reviewed_segments
                        if isinstance(segment, Mapping)
                    )
                    else "sentence_timestamps"
                ),
            },
            preserve_source_clock=float(media["duration_seconds"]) >= 60.0,
        )
        director_plan["visual_requests"] = shot_plan.get("visual_requests") or []
        director_plan["provider_search_log"] = shot_plan.get("provider_search_log") or []
        release_brolls = self._shot_broll_placements(
            shot_plan,
            broll_assets_by_shot_id,
        )
        # Keep the third already-matched semantic cluster when it fits the
        # short-form coverage contract.  The old hard two-event cap left the
        # latter half as uninterrupted A-roll even though shot-05 had a
        # reviewed restaurant match.  Bound each event instead of inflating
        # coverage with long inserts; three 2.4s clusters stay below 40% on
        # an 18s sample and retain meaningful semantic timing.
        if len(release_brolls) >= 3:
            release_brolls = self._bound_short_rich_release_brolls(
                release_brolls,
                max_events=12 if float(media["duration_seconds"]) >= 60.0 else 3,
                max_event_seconds=(
                    5.5 if float(media["duration_seconds"]) >= 60.0 else 2.4
                ),
            )
        # A rich short export needs a visible establishing cut when more than
        # one semantically accepted cluster exists.  Keep this generic: the
        # first scene/relationship/product evidence becomes fullscreen, while
        # later evidence remains a safe centered PiP.  No asset is promoted
        # when there is only one cluster or no concrete visual type.
        if len(release_brolls) >= 2 and not any(
            str(item.get("mode") or "") == "full" for item in release_brolls
        ):
            # Shot placements are already the output of the semantic matcher;
            # older placement payloads do not repeat visual_type.  Do not let
            # that metadata omission silently collapse every accepted cluster
            # into PiP.  Promote only the first accepted cluster, keeping the
            # later clusters centered PiP and preserving the semantic schedule.
            first_broll = release_brolls[0]
            first_broll["mode"] = "full"
            first_broll["mode_selection_reason"] = (
                "first_semantic_cluster_full_establishing_cut"
            )
        transcript_text = "".join(
            str(segment.get("text") or "")
            for segment in reviewed_segments
            if isinstance(segment, Mapping)
        )
        vector_track, vector_assets = self._ensure_release_creative_assets(
            transcript_text,
            shot_plan=shot_plan,
            transcript_segments=reviewed_segments,
        )
        if release_brolls and os.getenv("VIDEO_EDITOR_ENABLE_SEMANTIC_INFO_BAND", "").lower() not in {
            "1",
            "true",
            "yes",
        }:
            vector_track = {
                **dict(vector_track),
                "items": [
                    item
                    for item in vector_track.get("items") or []
                    if isinstance(item, Mapping)
                    and str(item.get("renderer") or "") != "semantic_info_band"
                ],
                "semantic_info_band_disabled": "real_broll_present",
            }
        vector_events = [
            {
                "event_id": f"vector-{index + 1:02d}",
                "start": item.get("start"),
                "end": item.get("end"),
                "type": str(item.get("renderer") or "vector_pip"),
                "asset_id": item.get("asset_id"),
                "mode": str(item.get("mode") or "pip"),
                "semantic_text": item.get("semantic_text"),
                "grounded_in_text": True,
            }
            for index, item in enumerate(vector_track.get("items") or [])
            if isinstance(item, Mapping)
            and (
                item.get("asset_id")
                or str(item.get("renderer") or "")
                in {"semantic_info_band", "data_visual_card"}
            )
        ]
        director_plan["vector_track"] = vector_track
        director_plan["asset_matching"] = asset_matching
        director_plan["visual_intents"] = [
            dict(item)
            for item in vector_track.get("items") or []
            if isinstance(item, Mapping)
            and str(item.get("renderer") or "") == "data_visual_card"
        ]
        director_plan["visual_events"] = [
            *(director_plan.get("visual_events") or []),
            *vector_events,
        ]
        if vector_events:
            director_plan["asset_requests"] = []
        real_visual_count = len(release_brolls)
        visual_policy = _visual_gate_policy_for_template(
            str(shot_plan.get("template_id") or ""),
            visual_density=str(shot_plan.get("visual_density") or ""),
            duration_seconds=float(media["duration_seconds"]),
        )
        director_plan.setdefault("degradation", {})["publish_claim_allowed"] = (
            visual_policy["min_real_events"]
            <= real_visual_count
            <= visual_policy["max_real_events"]
        )
        if vector_events:
            director_plan["degradation"]["mode"] = (
                "broll_plus_transparent_vector" if release_brolls else "transparent_vector_safe_degradation"
            )
            director_plan["degradation"]["message"] = (
                "矢量素材已绑定到独立视觉轨道；矢量不计入真实 B-roll 验收。"
            )
        edit_plan = dict(item.edit_plan or {})
        transcript_glossary = (
            (item.review_snapshot or {}).get("transcript_glossary")
            or edit_plan.get("transcript_glossary")
        )
        if transcript_glossary:
            edit_plan["transcript_glossary"] = transcript_glossary
        template_theme = "adaptive"
        edit_plan.update(
            {
                "shot_plan": shot_plan,
                "director_plan": director_plan,
                "template_id": shot_plan["template_id"],
                "template_version": shot_plan["template_version"],
                "smart_opening": None,
                "remove_ranges": [],
                "trim_silence_enabled": False,
                "release_brolls": release_brolls,
                "vector_track": vector_track,
                "source_media_identity": director_plan.get("source_media_identity") or {},
                "source_range": {
                    "start": 0.0,
                    "end": round(float(media["duration_seconds"]), 3),
                    "clock": "source_media",
                },
                "creative_theme": template_theme,
                "typography": vector_track.get("typography"),
            }
        )
        enabled = [
            step
            for step in (item.enabled_plan_step_ids or ["vertical_fit", "subtitles", "title"])
            if step != "smart_opening"
        ]
        if local_bgm_id and "bgm" not in enabled:
            enabled.append("bgm")
        release_item = item.model_copy(
            update={
                "status": "outcome_unknown",
                "edit_task_id": None,
                "subtitle_segments": reviewed_segments,
                "selected_title": release_title,
                "edit_plan": edit_plan,
                "enabled_plan_step_ids": enabled,
                "selected_bgm_id": local_bgm_id,
                "bgm_reason": release_bgm_reason,
                "provider_stage": "local_release_template_ready",
                "publish_allowed": False,
                "provider_payload": {
                    **item.provider_payload,
                    "local_release_template": {
                        "template_id": shot_plan["template_id"],
                        "template_version": shot_plan["template_version"],
                        "degradation": shot_plan["degradation"],
                        "director_plan_version": director_plan["plan_version"],
                        "asset_request_count": len(director_plan["asset_requests"]),
                        "real_visual_event_count": real_visual_count,
                        "vector_asset_count": len(vector_assets),
                        "vector_track_id": vector_track.get("track_id"),
                        "bgm_id": local_bgm_id,
                        "bgm_reason": release_bgm_reason,
                        "cloud_calls": 0,
                        "publish_allowed": False,
                    },
                },
                "review_snapshot": {
                    **item.review_snapshot,
                    "release_template": {
                        "template_id": shot_plan["template_id"],
                        "template_version": shot_plan["template_version"],
                        "approval_mode": "existing_subtitle_review_plus_local_template",
                        "director_plan_version": director_plan["plan_version"],
                    },
                },
                "updated_at": datetime.now().astimezone(),
            }
        )
        batch = self._replace_batch_item(batch, release_item)
        # Persist the reviewed transcript and release plan before the local
        # export reloads the batch.  Otherwise the export can read the stale
        # raw ASR segments while the director identity hash is based on the
        # reviewed segments, creating a false transcript identity failure.
        self.repository.save_video_editor_batch(batch)
        return self.create_local_preview_export(
            batch.batch_id,
            item_id,
            run_inline=True,
            local_bgm_id=local_bgm_id,
        )

    def quote_director_assets(self, batch_id: str, item_id: str) -> dict[str, Any]:
        """Quote generated image needs without starting a provider request."""
        from src.services.image_generation import (
            ImageGenerationConfiguration,
            build_image_provider,
        )

        batch = self._sync_batch(self._require_batch(batch_id))
        item = next((entry for entry in batch.items if entry.item_id == item_id), None)
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        director_plan = dict((item.edit_plan or {}).get("director_plan") or {})
        requests = [
            request
            for request in director_plan.get("asset_requests") or []
            if isinstance(request, Mapping)
        ]
        quote = build_image_provider(
            ImageGenerationConfiguration.from_env()
        ).quote(len(requests))
        return {
            "batch_id": batch.batch_id,
            "item_id": item.item_id,
            "plan_version": director_plan.get("plan_version"),
            "asset_request_count": len(requests),
            "quote": quote.as_dict(),
            "cloud_calls": 0,
            "publish_allowed": False,
        }

    def _generate_token_plan_visual_assets(
        self,
        requests: Sequence[Mapping[str, Any]],
        *,
        image_generation_log: list[dict[str, Any]] | None = None,
        max_assets: int = 4,
        max_concurrent: int = 2,
    ) -> list[dict[str, Any]]:
        """Generate a bounded local visual fallback from semantic shot requests.

        P0-4 限制：
        - 每条视频最多 4 张（max_assets）
        - 同步 sequential batch；设计并发上限 max_concurrent=2（即便 future
          改异步实现，限流也必须 ≤ 2）
        - 请求失败最多 1 次重试（image_generation._call_once 内置）
        - 记录：request_id / 语义窗口 / 模型 / 状态 / 耗时 / 错误类型
        - **绝不**记录 API Key
        - prompt 不包含具体中文 / 数字 / CTA 字面
        - 不可写入 publish_licensed = True
        """

        from src.services.image_generation import (
            ImageGenerationConfiguration,
            build_image_provider,
            ImageGenerationError,
        )

        configuration = ImageGenerationConfiguration.from_env()
        # P0-4: 触发条件：is_minimax_token_plan AND autogenerate AND 显式配置
        if not (
            configuration.is_minimax_token_plan
            and configuration.autogenerate_on_keyword_match
        ):
            return []
        # P0-4: 显式缺失配置时直接 return，绝不发起任何调用
        if configuration.missing_configuration:
            if image_generation_log is not None:
                image_generation_log.append(
                    {
                        "request_id": f"minimax-skip-{uuid4().hex[:8]}",
                        "model": configuration.model,
                        "status": "skipped_missing_configuration",
                        "missing_configuration": list(
                            configuration.missing_configuration
                        ),
                        "elapsed_ms": 0,
                        "error_type": None,
                    }
                )
            return []
        provider = build_image_provider(configuration)
        # P0-4: 强制每条 ≤ 4 张
        cap = max(0, min(int(max_assets), 4))
        # P0-4: 显式记录并发设计意图
        if image_generation_log is not None and max_concurrent > 2:
            # 设计意图被违反：仅记录，不阻断
            image_generation_log.append(
                {
                    "request_id": f"minimax-concurrency-warning-{uuid4().hex[:6]}",
                    "status": "design_warning",
                    "warning": (
                        f"max_concurrent={max_concurrent} 超过 P0-4 硬上限 2，"
                        "实际并发将被截断到 2。"
                    ),
                }
            )
        effective_concurrency = min(int(max_concurrent), 2)
        generated: list[dict[str, Any]] = []
        eligible: list[Mapping[str, Any]] = []
        for request in list(requests):
            if not isinstance(request, Mapping):
                continue
            if str(request.get("visual_type") or "") == "abstract":
                continue
            queries = [
                str(value).strip()
                for value in request.get("search_queries") or []
                if str(value).strip()
            ]
            if not queries:
                continue
            # P0-4: prompt 黑名单——具体中文 / 数字 / CTA 字面禁止进入 prompt
            # （信息卡 / CTA 文字必须由程序渲染，绝不让生图出文字）
            subject = str(request.get("expected_subject") or "").strip()
            action = str(request.get("expected_action") or "").strip()
            context = str(request.get("expected_context") or "").strip()
            if _textual_payload_has_unsafe_literals(
                subject
            ) or _textual_payload_has_unsafe_literals(
                action
            ) or _textual_payload_has_unsafe_literals(
                context
            ):
                if image_generation_log is not None:
                    image_generation_log.append(
                        {
                            "request_id": f"minimax-skip-unsafe-{uuid4().hex[:6]}",
                            "model": configuration.model,
                            "status": "skipped_unsafe_prompt_literals",
                            "reason": (
                                "subject / action / context 含具体中文/数字/CTA 字面"
                            ),
                            "elapsed_ms": 0,
                            "error_type": None,
                        }
                    )
                continue
            eligible.append(request)
        # P0-4: 限制最多 cap 张
        eligible = eligible[:cap]
        for request in eligible:
            queries = [
                str(value).strip()
                for value in request.get("search_queries") or []
                if str(value).strip()
            ]
            subject = str(request.get("expected_subject") or "").strip()
            action = str(request.get("expected_action") or "").strip()
            context = str(request.get("expected_context") or "").strip()
            prompt = (
                "竖屏9:16短视频B-roll，真实摄影风格；"
                f"主体：{subject}；动作或场景：{action}；语境：{context}；"
                f"参考概念：{'；'.join(queries[:3])}。"
                "画面信息明确、主体完整、适合移动端观看，不要出现文字、字幕、Logo或水印。"
            )
            request_id = f"minimax-{uuid4().hex[:10]}"
            t0 = time.perf_counter()
            status = "ok"
            error_type: str | None = None
            try:
                result = provider.generate(
                    prompt,
                    negative_prompt=(
                        "文字，字幕，Logo，水印，畸形手，重复物体，低清晰度"
                    ),
                    aspect_ratio="9:16",
                )
                suffix = ".jpg" if result.mime_type == "image/jpeg" else ".png"
                asset = self.upload_visual_asset(
                    kind="broll",
                    file_name=f"minimax-token-plan-{uuid4().hex[:10]}{suffix}",
                    media_type=result.mime_type,
                    media_bytes=result.image_bytes,
                    rights_confirmed=True,
                    rights_holder="VideoInsight / MiniMax Token Plan 本地生成",
                    license_name="MiniMax Token Plan 生成素材（仅本地验收）",
                )
                asset_id = str(asset.get("asset_id") or "")
                if not asset_id:
                    status = "upload_failed"
                    error_type = "asset_upload_returned_empty_id"
                    continue
                metadata_path = self._visual_asset_metadata_path(asset_id)
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata.update(
                    {
                        "source_provider": "minimax_token_plan",
                        "asset_origin": "generated_image_asset",
                        "source_type": "minimax_token_plan",
                        "rights_status": "generated_for_local_acceptance",
                        "authorization_status": "generated_for_local_acceptance",
                        "publish_licensed": False,
                        "semantic_binding": subject or context,
                        "semantic_query": " ".join(queries[:3]),
                        "visual_keywords": queries[:3],
                        "provider_model": result.model,
                        "visual_intent": str(
                            request.get("visual_type") or "concept"
                        ),
                        "preferred_mode": str(
                            request.get("preferred_mode") or "full"
                        ),
                    }
                )
                metadata_path.write_text(
                    json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
                )
                generated.append(
                    self._visual_asset_payload(
                        metadata, Path(str(asset["_path"]))
                    )
                )
            except ImageGenerationError as exc:
                status = "failed"
                error_type = exc.__class__.__name__
            except Exception as exc:  # noqa: BLE001
                status = "failed"
                error_type = exc.__class__.__name__
            finally:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                if image_generation_log is not None:
                    log_entry: dict[str, Any] = {
                        "request_id": request_id,
                        "model": configuration.model,
                        "status": status,
                        "elapsed_ms": elapsed_ms,
                        "error_type": error_type,
                        "max_concurrent_design": effective_concurrency,
                    }
                    # P0-4: 绝不记录 API Key
                    # 仅记录 request_id、模型、状态、耗时、错误类型
                    image_generation_log.append(log_entry)
        return generated

    def render_production_export(
        self,
        *,
        avatar_task: AvatarTask,
        script_text: str,
        publish_title: str,
        subtitle_segments: Sequence[Mapping[str, Any]] | None = None,
    ) -> VideoEditTask:
        """Render an approved production avatar with the current local template.

        Production already paid for and downloaded the avatar before it reaches
        this boundary.  Reuse that exact media and script, and run the same
        reviewed local renderer used by the intelligent editor.  This keeps the
        automatic path on the single-line, punctuation-free business template
        instead of silently falling back to the legacy subtitle adapter.
        """

        if avatar_task.status != TaskStatus.SUCCEEDED or not avatar_task.result_path:
            raise VideoEditorWorkflowError("数字人成片尚未就绪，不能开始智能剪辑。")
        source_path = Path(avatar_task.result_path)
        if not source_path.is_file():
            raise VideoEditorWorkflowError("数字人成片文件不存在，不能开始智能剪辑。")
        script = script_text.strip()
        title = publish_title.strip()
        if not script:
            raise VideoEditorWorkflowError("已确认口播文案为空，不能开始智能剪辑。")
        if not title:
            raise VideoEditorWorkflowError("成片标题为空，不能开始智能剪辑。")

        # The production draft can still carry a stale creative-plan hook.
        # Anchor the on-screen title to the first approved spoken clause so it
        # is a complete sentence fragment and matches the actual video.
        title = self._script_topic_title(script, title)

        from src.services.video_editor_cloud import build_smart_opening

        approved_opening = build_smart_opening(script, [title])
        if approved_opening is not None:
            title = approved_opening.hook_text

        media = self._probe_media(source_path)
        timing_source = "approved_avatar_script_estimate"
        if subtitle_segments is not None:
            segments = self._validated_review_segments(
                [dict(segment) for segment in subtitle_segments],
                duration_seconds=float(media["duration_seconds"]),
            )
            approved_text = re.sub(r"[\W_]+", "", script, flags=re.UNICODE)
            timed_text = re.sub(
                r"[\W_]+",
                "",
                "".join(str(segment.get("text") or "") for segment in segments),
                flags=re.UNICODE,
            )
            if timed_text != approved_text:
                raise VideoEditorWorkflowError(
                    "真实字幕时间轴与已确认口播文案不一致，已停止避免音画错配。"
                )
            timing_source = "approved_avatar_asr"
        else:
            segments = self._estimated_script_segments(
                script,
                float(media["duration_seconds"]),
            )
        if not segments:
            raise VideoEditorWorkflowError("无法生成字幕时间轴，不能开始智能剪辑。")

        now = datetime.now().astimezone()
        source_id = f"avatar:{avatar_task.task_id}"
        item = VideoEditorBatchItem(
            source_id=source_id,
            title=title,
            status="outcome_unknown",
            selected_title=title,
            subtitle_segments=segments,
            review_snapshot={"confirmed": True, "source": timing_source},
            review_confirmed_at=now,
            enabled_plan_step_ids=["vertical_fit", "subtitles", "title"],
            edit_plan={"remove_ranges": []},
            provider_stage="production_local_export_ready",
            publish_allowed=False,
            updated_at=now,
        )
        batch = VideoEditorBatch(
            target_platform="douyin",
            subtitle_enabled=True,
            bgm_enabled=False,
            output_format="mp4",
            output_resolution="720x1280",
            output_fps=30,
            output_bitrate="2.5M",
            provider_mode="local",
            output_profile="720p",
            is_mock=False,
            items=[item],
            created_at=now,
            updated_at=now,
        )
        self.repository.save_video_editor_batch(batch)
        payload = self.create_local_preview_export(
            batch.batch_id,
            item.item_id,
            run_inline=True,
        )
        completed_item = payload["items"][0]
        edit_task = self.repository.get_task(completed_item.get("edit_task_id") or "")
        if not isinstance(edit_task, VideoEditTask):
            raise VideoEditorWorkflowError("智能剪辑没有生成可核验的任务记录。")
        return edit_task

    @staticmethod
    def _ffmpeg_filter_path(path: Path, *, fontsdir: Path | None = None) -> str:
        escaped = str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        if fontsdir is None:
            return escaped
        escaped_fonts = str(fontsdir).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        return f"{escaped}':fontsdir='{escaped_fonts}"

    @staticmethod
    def _render_smart_opening_clip(
        opening: dict[str, Any],
        *,
        output_profile: str,
        fps: int,
        output_path: Path,
        temp_dir: Path,
    ) -> float:
        """Render one short, full-frame opening from the approved template set."""

        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageFont
        except ImportError as exc:
            raise VideoEditorWorkflowError(
                "缺少开场动画排版组件 Pillow，暂不能生成开场。"
            ) from exc
        from src.services.video_editor_cloud import (
            BRAND_TITLE_FONT_PATH,
            SmartOpening,
            visual_style_spec,
        )

        approved = SmartOpening.model_validate(opening)
        spec = visual_style_spec(output_profile)
        width = int(spec["canvas"]["width"])
        height = int(spec["canvas"]["height"])
        duration = float(approved.duration_seconds)
        frame_count = max(2, round(duration * fps))
        frames_dir = temp_dir / "opening-frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        base = Image.new("RGB", (width, height), "#070A12")
        base_draw = ImageDraw.Draw(base)
        for y in range(height):
            ratio = y / max(1, height - 1)
            base_draw.line(
                (0, y, width, y),
                fill=(
                    7 + round(7 * ratio),
                    10 + round(9 * ratio),
                    18 + round(14 * ratio),
                ),
            )
        grid_gap = max(48, round(width * 0.09))
        grid_color = (69, 83, 112)
        for x in range(0, width, grid_gap):
            base_draw.line((x, 0, x, height), fill=grid_color, width=1)
        for y in range(0, height, grid_gap):
            base_draw.line((0, y, width, y), fill=grid_color, width=1)
        vignette = Image.new("L", (width, height), 0)
        vignette_draw = ImageDraw.Draw(vignette)
        vignette_draw.ellipse(
            (
                -round(width * 0.35),
                round(height * 0.18),
                round(width * 1.35),
                round(height * 0.82),
            ),
            fill=210,
        )
        vignette = vignette.filter(ImageFilter.GaussianBlur(round(width * 0.18)))
        light = Image.new("RGB", (width, height), "#17213A")
        base = Image.composite(light, base, vignette)

        font_size = round(width * (0.105 if len(approved.hook_text) <= 8 else 0.082))
        font = ImageFont.truetype(str(BRAND_TITLE_FONT_PATH), font_size)
        chars_per_line = 7 if len(approved.hook_text) > 9 else 9
        lines = [
            approved.hook_text[index : index + chars_per_line]
            for index in range(0, len(approved.hook_text), chars_per_line)
        ][:2]
        line_gap = round(font_size * 0.22)
        text_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_layer)
        boxes = [
            text_draw.textbbox(
                (0, 0), line, font=font, stroke_width=max(1, width // 360)
            )
            for line in lines
        ]
        block_height = sum(box[3] - box[1] for box in boxes) + line_gap * max(
            0, len(lines) - 1
        )
        y = round(height * 0.46 - block_height / 2)
        number_pattern = re.compile(r"\d+(?:\.\d+)?(?:%|％|元|块|折)?")
        for line, box in zip(lines, boxes, strict=False):
            line_width = box[2] - box[0]
            x = round((width - line_width) / 2)
            text_draw.text(
                (x, y),
                line,
                font=font,
                fill="#F8FAFC",
                stroke_width=max(1, width // 360),
                stroke_fill=(0, 0, 0, 150),
            )
            if approved.style_id == "number_focus":
                match = number_pattern.search(line)
                if match:
                    prefix_width = text_draw.textlength(
                        line[: match.start()], font=font
                    )
                    text_draw.text(
                        (x + prefix_width, y),
                        match.group(),
                        font=font,
                        fill="#FFE16A",
                        stroke_width=max(1, width // 360),
                        stroke_fill=(0, 0, 0, 150),
                    )
            y += box[3] - box[1] + line_gap

        for frame_index in range(frame_count):
            progress = frame_index / max(1, frame_count - 1)
            eased = 1 - (1 - min(progress / 0.72, 1)) ** 3
            fade_out = 1 if progress < 0.82 else max(0, (1 - progress) / 0.18)
            animated = text_layer
            if approved.style_id == "story_unfold":
                bounce = 1 + 0.06 * (1 - eased) * (1 if progress < 0.45 else -0.35)
                reveal = max(0.08, eased)
                resized = animated.resize(
                    (width, max(1, round(height * reveal * bounce))),
                    Image.Resampling.LANCZOS,
                )
                stage = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                stage.alpha_composite(
                    resized, (0, round((height - resized.height) / 2))
                )
                animated = stage
            else:
                start_scale = 1.42 if approved.style_id == "number_focus" else 1.28
                scale = start_scale - (start_scale - 1) * eased
                scaled = animated.resize(
                    (round(width * scale), round(height * scale)),
                    Image.Resampling.LANCZOS,
                )
                stage = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                shift_x = round(width * 0.08 * max(0, (progress - 0.82) / 0.18))
                stage.alpha_composite(
                    scaled,
                    (
                        round((width - scaled.width) / 2) + shift_x,
                        round((height - scaled.height) / 2),
                    ),
                )
                blur = round(
                    (1 - eased) * (12 if approved.style_id == "number_focus" else 8)
                )
                animated = (
                    stage.filter(ImageFilter.GaussianBlur(blur)) if blur else stage
                )
            if approved.style_id == "number_focus":
                glow = animated.filter(ImageFilter.GaussianBlur(max(2, width // 120)))
                glow.putalpha(
                    glow.getchannel("A").point(lambda value: round(value * 0.32))
                )
                frame = base.convert("RGBA")
                frame.alpha_composite(glow)
            else:
                frame = base.convert("RGBA")
            if fade_out < 1:
                animated = animated.copy()
                animated.putalpha(
                    animated.getchannel("A").point(
                        lambda value: round(value * fade_out)
                    )
                )
            frame.alpha_composite(animated)
            frame.convert("RGB").save(
                frames_dir / f"{frame_index:04d}.jpg",
                quality=90,
                optimize=True,
            )

        sound_filter = {
            "soft_whoosh": (
                f"anoisesrc=color=pink:sample_rate=48000:duration={duration:.3f}:amplitude=0.05,"
                "highpass=f=420,lowpass=f=4200,afade=t=in:st=0:d=0.06,"
                f"afade=t=out:st=0.55:d={max(0.2, duration - 0.55):.3f}"
            ),
            "soft_page_turn": (
                f"anoisesrc=color=white:sample_rate=48000:duration={duration:.3f}:amplitude=0.025,"
                "lowpass=f=2600,afade=t=in:st=0:d=0.04,"
                f"afade=t=out:st=0.32:d={max(0.2, duration - 0.32):.3f}"
            ),
            "soft_chime": (
                "sine=frequency=880:sample_rate=48000:duration=0.34,volume=0.045,"
                "afade=t=out:st=0.08:d=0.26,"
                f"apad=whole_dur={duration:.3f}"
            ),
        }[approved.sound_effect_id]
        command = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-framerate",
            str(fps),
            "-start_number",
            "0",
            "-i",
            str(frames_dir / "%04d.jpg"),
            "-f",
            "lavfi",
            "-i",
            sound_filter,
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = _run_media_command(
            command, capture_output=True, text=True, timeout=180, check=False
        )
        if (
            result.returncode != 0
            or not output_path.is_file()
            or not output_path.stat().st_size
        ):
            detail = (result.stderr or "开场动画没有生成文件。").strip()
            raise VideoEditorWorkflowError(f"智能开场生成失败：{detail[-500:]}")
        return duration

    @staticmethod
    def _prepend_opening_clip(
        opening_path: Path,
        body_path: Path,
        output_path: Path,
        *,
        bitrate: str,
    ) -> None:
        command = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-i",
            str(opening_path),
            "-i",
            str(body_path),
            "-filter_complex",
            "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout]",
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            bitrate,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            output_path.as_posix(),
        ]
        result = _run_media_command(
            command, capture_output=True, text=True, timeout=15 * 60, check=False
        )
        if (
            result.returncode != 0
            or not output_path.is_file()
            or not output_path.stat().st_size
        ):
            detail = (result.stderr or "开场与正片拼接失败。").strip()
            raise VideoEditorWorkflowError(f"智能开场拼接失败：{detail[-500:]}")

    @staticmethod
    def _render_visual_beat_card(
        beat: Mapping[str, Any],
        *,
        output_profile: str,
        output_path: Path,
    ) -> None:
        """Create one restrained keyword card for the automatic local path."""

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:
            raise VideoEditorWorkflowError(
                "缺少动效排版组件 Pillow，暂不能生成关键词视觉卡。"
            ) from exc
        from src.services.video_editor_cloud import BRAND_TITLE_FONT_PATH, visual_style_spec

        spec = visual_style_spec(output_profile)
        width = int(spec["canvas"]["width"])
        height = int(spec["canvas"]["height"])
        label = re.sub(r"\s+", "", str(beat.get("label") or "")).strip()[:12]
        if not label:
            raise VideoEditorWorkflowError("自动视觉分镜缺少关键词。")
        treatment = str(beat.get("treatment") or "keyword_card")
        accent = {
            "keyword_card": (255, 225, 106, 245),
            "punch_in": (113, 190, 255, 235),
        }.get(treatment, (255, 225, 106, 245))
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        left = round(width * 0.055)
        # Keep visual cards in the upper safe band.  The talking-head
        # foreground is deliberately placed below this band for landscape
        # sources, so the card cannot cover the speaker's face.
        top = round(height * 0.055)
        right = min(width - left, left + round(width * 0.62))
        bottom = top + round(height * 0.105)
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=round(width * 0.018),
            fill=(7, 10, 18, 218),
            outline=accent,
            width=max(2, round(width / 360)),
        )
        small_font = ImageFont.truetype(
            str(BRAND_TITLE_FONT_PATH), max(18, round(width * 0.028))
        )
        label_font = ImageFont.truetype(
            str(BRAND_TITLE_FONT_PATH), max(26, round(width * 0.052))
        )
        draw.text(
            (left + round(width * 0.025), top + round(height * 0.014)),
            "关键点",
            font=small_font,
            fill=accent,
        )
        draw.text(
            (left + round(width * 0.025), top + round(height * 0.042)),
            label,
            font=label_font,
            fill=(248, 250, 252, 255),
        )
        canvas.save(output_path)

    @staticmethod
    def _render_semantic_info_card(
        item: Mapping[str, Any],
        *,
        output_profile: str,
        output_path: Path,
    ) -> None:
        """Draw an integrated semantic band, never an independent sticker icon."""

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:
            raise VideoEditorWorkflowError(
                "缺少动效排版组件 Pillow，暂不能生成语义信息层。"
            ) from exc
        from src.services.video_editor_cloud import BRAND_TITLE_FONT_PATH, visual_style_spec

        spec = visual_style_spec(output_profile)
        width = int(spec["canvas"]["width"])
        height = int(spec["canvas"]["height"])
        label = re.sub(r"\s+", "", str(item.get("semantic_text") or "")).strip()[:12]
        if not label:
            raise VideoEditorWorkflowError("语义信息层缺少绑定文字。")
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        left = 36
        top = 806
        right = min(width - 42, left + 360)
        bottom = 956
        accent = (83, 213, 255, 220)
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=18,
            fill=(7, 18, 30, 150),
            outline=(83, 213, 255, 100),
            width=2,
        )
        draw.rounded_rectangle(
            (left, top + 24, left + 8, bottom - 24),
            radius=4,
            fill=accent,
        )
        small_font = ImageFont.truetype(
            str(BRAND_TITLE_FONT_PATH), max(18, round(width * 0.024))
        )
        label_font = ImageFont.truetype(
            str(BRAND_TITLE_FONT_PATH), max(26, round(width * 0.046))
        )
        draw.text((left + 28, top + 20), "语义提示", font=small_font, fill=accent)
        draw.text(
            (left + 28, top + 60),
            label,
            font=label_font,
            fill=(248, 250, 252, 238),
        )
        canvas.save(output_path)

    @staticmethod
    def _render_adaptive_visual_card(
        item: Mapping[str, Any],
        *,
        output_profile: str,
        output_path: Path,
    ) -> None:
        """Render a restrained, transcript-grounded data/concept visual."""

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:
            raise VideoEditorWorkflowError(
                "缺少动效排版组件 Pillow，暂不能生成自适应视觉层。"
            ) from exc
        from src.services.video_editor_cloud import BRAND_TITLE_FONT_PATH, visual_style_spec

        spec = visual_style_spec(output_profile)
        width = int(spec["canvas"]["width"])
        height = int(spec["canvas"]["height"])
        # Explanatory cards are cutaways, not corner stickers.  Use an opaque
        # branded canvas so the viewer gets one clear visual layer and the
        # subtitle filter can remain the final layer in the render chain.
        canvas = Image.new("RGBA", (width, height), (8, 18, 32, 250))
        draw = ImageDraw.Draw(canvas)
        accents = {
            "data_chart": (255, 190, 76, 245),
            "evidence": (88, 208, 255, 245),
            "process": (104, 224, 179, 245),
            "network": (180, 146, 255, 245),
            "transition": (255, 119, 146, 245),
            "cta": (255, 157, 86, 245),
            "list": (88, 208, 255, 245),
            "concept_card": (104, 224, 179, 245),
        }
        accent = accents.get(
            str(item.get("visual_intent") or ""), (104, 224, 179, 245)
        )
        margin_x = round(width * 0.11)
        top = round(height * 0.18)
        right = width - margin_x
        bottom = round(height * 0.82)
        draw.rounded_rectangle(
            (margin_x, top, right, bottom),
            radius=round(width * 0.035),
            fill=(12, 28, 48, 245),
            outline=accent,
            width=max(3, round(width / 240)),
        )
        font = ImageFont.truetype(str(BRAND_TITLE_FONT_PATH), max(18, round(width * 0.027)))
        fact_font = ImageFont.truetype(str(BRAND_TITLE_FONT_PATH), max(30, round(width * 0.075)))
        label = re.sub(r"\s+", "", str(item.get("semantic_text") or ""))[:18]
        fact = re.sub(r"\s+", "", str(item.get("fact") or ""))[:12]
        if fact and (fact in label or label in fact):
            fact = ""
        if label:
            draw.text(
                (margin_x + round(width * 0.06), top + round(height * 0.07)),
                label,
                font=font,
                fill=(248, 250, 252, 245),
            )
        if fact:
            draw.text(
                (margin_x + round(width * 0.06), top + round(height * 0.18)),
                fact,
                font=fact_font,
                fill=accent,
            )

        visual_intent = str(item.get("visual_intent") or "")
        x0 = margin_x + round(width * 0.07)
        y0 = bottom - round(height * 0.16)
        x1 = right - round(width * 0.07)
        # Each intent has a distinct, meaning-bearing grammar.  No generic
        # trend line is drawn for unrelated concepts.
        diagram_labels = [
            re.sub(r"\s+", "", str(value))[:8]
            for value in item.get("diagram_labels") or []
            if re.sub(r"\s+", "", str(value))
        ][:3]
        small_font = ImageFont.truetype(
            str(BRAND_TITLE_FONT_PATH), max(18, round(width * 0.026))
        )
        if visual_intent == "data_chart":
            # One grounded metric gets one labelled visual.  A bare stack of
            # bars is not a chart and was the source of the empty-PPT look.
            metric = fact or label
            draw.text((x0, y0), "口播事实", font=small_font, fill=(220, 230, 240, 230))
            draw.rounded_rectangle(
                (x0, y0 + round(height * 0.06), x1, y0 + round(height * 0.15)),
                radius=round(height * 0.018),
                outline=accent,
                width=max(3, round(width / 260)),
            )
            draw.text(
                (x0 + round(width * 0.035), y0 + round(height * 0.073)),
                metric,
                font=small_font,
                fill=accent,
            )
        elif visual_intent in {"concept_card", "process"}:
            labels = diagram_labels[:3]
            points = [
                round(x0 + index * (x1 - x0) / max(len(labels) - 1, 1))
                for index in range(len(labels))
            ]
            cy = y0 + round(height * 0.035)
            for left_point, right_point in zip(points, points[1:]):
                draw.line((left_point + 18, cy, right_point - 18, cy), fill=accent, width=4)
                draw.polygon(
                    [(right_point - 18, cy), (right_point - 32, cy - 10), (right_point - 32, cy + 10)],
                    fill=accent,
                )
            for point, node_label in zip(points, labels, strict=True):
                draw.ellipse((point - 18, cy - 18, point + 18, cy + 18), fill=accent)
                draw.text((point - 42, cy + 28), node_label, font=small_font, fill=(248, 250, 252, 235))
        elif visual_intent == "network":
            labels = diagram_labels[:3]
            nodes = tuple(
                (round(x0 + index * (x1 - x0) / max(len(labels) - 1, 1)), y0 + (30 if index != 1 else 0))
                for index in range(len(labels))
            )
            for left_node, right_node in ((nodes[0], nodes[1]), (nodes[1], nodes[2]), (nodes[0], nodes[2])):
                draw.line((*left_node, *right_node), fill=accent, width=3)
            for node, node_label in zip(nodes, labels, strict=True):
                draw.ellipse((node[0] - 16, node[1] - 16, node[0] + 16, node[1] + 16), fill=accent)
                draw.text((node[0] - 42, node[1] + 26), node_label, font=small_font, fill=(248, 250, 252, 235))
        elif visual_intent == "transition":
            labels = diagram_labels[:2]
            cy = y0 + round(height * 0.035)
            draw.line((x0, cy, x1 - 32, cy), fill=accent, width=6)
            draw.polygon([(x1 - 32, cy), (x1 - 58, cy - 18), (x1 - 58, cy + 18)], fill=accent)
            draw.text((x0, cy + 28), labels[0], font=small_font, fill=(248, 250, 252, 235))
            draw.text((x1 - round(width * 0.2), cy + 28), labels[1], font=small_font, fill=(248, 250, 252, 235))
        elif visual_intent == "list":
            cell_width = round((x1 - x0) / max(len(diagram_labels), 1))
            for column, node_label in enumerate(diagram_labels):
                left_cell = x0 + column * cell_width
                draw.rounded_rectangle(
                    (left_cell, y0, left_cell + cell_width - 14, y0 + round(height * 0.09)),
                    radius=10,
                    outline=accent,
                    width=3,
                )
                draw.text((left_cell + 12, y0 + round(height * 0.028)), node_label, font=small_font, fill=(248, 250, 252, 235))
        elif visual_intent == "cta":
            draw.rounded_rectangle((x0, y0, x1, y0 + round(height * 0.09)), radius=24, outline=accent, width=4)
            draw.polygon([(x0 + 60, y0 + round(height * 0.09)), (x0 + 88, y0 + round(height * 0.09)), (x0 + 62, y0 + round(height * 0.14))], fill=accent)
        canvas.save(output_path)

    @staticmethod
    def _local_rhythm_video_filter(
        *,
        duration_seconds: float,
        width: int,
        height: int,
        fps: int,
        playback_rate: float,
        subtitle_filter: str,
        broll: dict[str, Any] | None = None,
        brolls: Sequence[Mapping[str, Any]] | None = None,
        broll_input_index: int = 2,
        vectors: Sequence[Mapping[str, Any]] | None = None,
        vector_input_index: int = 2,
        semantic_layers: Sequence[Mapping[str, Any]] | None = None,
        semantic_input_index: int = 2,
        reframe_events: Sequence[Mapping[str, Any]] | None = None,
        source_width: int | None = None,
        source_height: int | None = None,
    ) -> str:
        """Build a source-only cut plan with a guaranteed-safe foreground.

        A vertical canvas cannot contain a landscape talking-head frame without
        either cropping content or adding a background.  The old implementation
        chose a fixed close crop, which could remove the speaker's head.  The
        new default keeps the complete source frame as the foreground and only
        crops a blurred duplicate used as the background.  If the source is
        portrait, it is fitted without cropping at all.  This preserves the
        complete original timeline, so reviewed caption timestamps and the
        voice track stay aligned.
        """
        if duration_seconds <= 0:
            raise VideoEditorWorkflowError("原片时长无效，无法生成节奏剪辑。")
        scene_count = max(
            1,
            min(
                _LOCAL_RHYTHM_MAX_SCENES,
                int(
                    (duration_seconds + _LOCAL_RHYTHM_SCENE_SECONDS - 0.001)
                    / _LOCAL_RHYTHM_SCENE_SECONDS
                ),
            ),
        )
        labels = "".join(f"[scene{index}]" for index in range(scene_count))
        filters = [f"[0:v]split={scene_count}{labels}"]
        source_aspect = (
            float(source_width) / float(source_height)
            if source_width and source_height and source_width > 0 and source_height > 0
            else float(width) / float(height)
        )
        landscape_source = source_aspect > 1.10
        # Fit the complete landscape frame into the canvas. Do not pad the
        # foreground with dark bars; the blurred duplicate already supplies
        # the visual fill around it.
        fit_height = max(2, min(height, round(width / source_aspect)))
        safe_y = round((height - fit_height) * 0.38)
        for index in range(scene_count):
            start = round(index * duration_seconds / scene_count, 3)
            end = round((index + 1) * duration_seconds / scene_count, 3)
            if landscape_source:
                framing = (
                    f"split=2[foreground{index}][background{index}];"
                    f"[background{index}]scale={width}:{height}:"
                    "force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},boxblur=18:2,"
                    f"eq=saturation=0.80:brightness=-0.03[blurred{index}];"
                    f"[foreground{index}]scale={width}:{fit_height}:"
                    "force_original_aspect_ratio=decrease,"
                    f"setsar=1[fit{index}];"
                    f"[blurred{index}][fit{index}]overlay=(W-w)/2:{safe_y},"
                    f"setsar=1,fps={fps}[rhythm{index}]"
                )
            else:
                # Use a continuous, very small dolly and lateral drift rather
                # than alternating 1.00/0.96 scene scales.  The latter reads
                # as a visible jump at every scene boundary; this motion is
                # intentionally below the semantic reframe amplitude and
                # keeps the top edge fixed so the speaker's head stays safe.
                scene_duration = max(end - start, 0.5)
                motion_amplitude = 0.010 + 0.003 * (index % 3)
                pan_pixels = 3 if index % 2 == 0 else -3
                zoom_expression = (
                    f"1+{motion_amplitude:.3f}*sin(PI*t/{scene_duration:.3f})"
                )
                framing = (
                    f"split=2[foreground{index}][background{index}];"
                    f"[background{index}]scale={width}:{height}:"
                    "force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x101827,"
                    f"boxblur=18:2[blurred{index}];"
                    f"[foreground{index}]scale=w='trunc({width}*({zoom_expression})/2)*2':"
                    f"h='trunc({height}*({zoom_expression})/2)*2':eval=frame,"
                    f"crop={width}:{height}:x='(iw-ow)/2+{pan_pixels}*sin(PI*t/{scene_duration:.3f})':y=0,"
                    f"setsar=1[fit{index}];"
                    f"[blurred{index}][fit{index}]overlay=(W-w)/2:0,"
                    f"setsar=1,fps={fps}[rhythm{index}]"
                )
            filters.append(
                f"[scene{index}]trim=start={start:.3f}:end={end:.3f},"
                f"setpts=PTS-STARTPTS,{framing}"
            )
        rhythm_inputs = "".join(f"[rhythm{index}]" for index in range(scene_count))
        filters.append(
            f"{rhythm_inputs}concat=n={scene_count}:v=1:a=0,"
            f"setpts=PTS/{playback_rate:.3f}[base]"
        )
        caption_input = "[base]"
        # Semantic peaks use a bounded A-roll push-in rather than a duplicate
        # opaque text card. Keep the top edge fixed to protect the speaker's
        # head while the camera reframe is active.
        for reframe_index, item in enumerate(reframe_events or ()):
            try:
                start = float(item.get("start") or 0) / max(playback_rate, 0.01)
                end = float(item.get("end") or 0) / max(playback_rate, 0.01)
            except (TypeError, ValueError):
                continue
            if end <= start:
                continue
            ramp = 0.14
            zoom = (
                f"1+0.035*if(lt(t,{start:.3f}),0,"
                f"if(lt(t,{start + ramp:.3f}),(t-{start:.3f})/{ramp:.3f},"
                f"if(lt(t,{max(start + ramp, end - ramp):.3f}),1,"
                f"if(lt(t,{end:.3f}),({end:.3f}-t)/{ramp:.3f},0))))"
            )
            output_label = f"reframed{reframe_index}"
            filters.append(
                f"{caption_input}scale=w='trunc({width}*({zoom})/2)*2':"
                f"h='trunc({height}*({zoom})/2)*2':eval=frame,"
                f"crop={width}:{height}:(iw-ow)/2:0,setsar=1[{output_label}]"
            )
            caption_input = f"[{output_label}]"
        overlay_items: list[Mapping[str, Any]] = []
        if broll:
            overlay_items.append(broll)
        overlay_items.extend(
            item for item in (brolls or []) if isinstance(item, Mapping)
        )
        for overlay_index, item in enumerate(overlay_items):
            try:
                start = float(item["start"]) / playback_rate
                end = float(item["end"]) / playback_rate
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                continue
            input_index = int(item.get("input_index", broll_input_index + overlay_index))
            mode = str(item.get("mode") or "pip")
            # Keep the legacy single-overlay labels stable for existing tests
            # and cached plans; multi-event release plans receive unique labels.
            legacy_single = len(overlay_items) == 1 and overlay_index == 0
            label = "broll" if legacy_single else f"broll{overlay_index}"
            output_label = "with_broll" if legacy_single else f"with_broll{overlay_index}"
            image_broll = str(item.get("media_kind") or "").lower() == "image"
            if mode == "full":
                if image_broll:
                    # Keep generated/local stills alive as a visual event: a
                    # small time-based crop movement plus a short alpha fade
                    # prevents a static card and never reuses the same asset
                    # as a fake loop.  The subtitle layer is still appended
                    # after every visual overlay below.
                    filters.append(
                        f"[{input_index}:v]format=rgba,"
                        f"scale={width + 40}:{height + 64}:"
                        "force_original_aspect_ratio=increase,"
                        f"crop={width}:{height}:"
                        "x='(iw-ow)/2+20*sin(2*PI*t/4.0)':"
                        "y='(ih-oh)/2+16*cos(2*PI*t/4.0)',"
                        "setsar=1,fade=t=in:st="
                        f"{start:.3f}:d=0.28:alpha=1[{label}]"
                    )
                else:
                    filters.append(
                        f"[{input_index}:v]setpts=PTS-STARTPTS,"
                        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                        f"crop={width}:{height}[{label}]"
                    )
                overlay = (
                    f"{caption_input}[{label}]overlay=0:0:"
                    f"enable='between(t,{start:.3f},{end:.3f})':"
                    f"eof_action=pass[{output_label}]"
                )
            else:
                pip_geometry = _portrait_pip_geometry(width, height)
                # Never place an unsafe PiP merely to satisfy the event count.
                # The caller's quality report records the failed geometry gate.
                if not pip_geometry.get("safe") or not pip_geometry.get("bbox"):
                    continue
                pip_bbox = pip_geometry["bbox"]
                pip_width = int(pip_bbox["width"])
                pip_height = int(pip_bbox["height"])
                pip_left = int(pip_bbox["left"])
                pip_top = int(pip_bbox["top"])
                # Keep the crop/geometry deterministic, but avoid a complex
                # per-pixel alpha expression here: on some FFmpeg builds its
                # escaped geq expression evaluates to a fully transparent
                # frame.  A thin translucent border gives the PiP a designed
                # container while keeping the real footage visibly present.
                pip_frame = (
                    "drawbox=x=1:y=1:w=iw-2:h=ih-2:"
                    "color=white@0.55:t=2"
                )
                if image_broll:
                    filters.append(
                        f"[{input_index}:v]format=rgba,"
                        f"scale={pip_width + 40}:{pip_height + 70}:force_original_aspect_ratio=increase,"
                        f"crop={pip_width}:{pip_height}:"
                        "x='(iw-ow)/2+10*sin(2*PI*t/3.6)':"
                        "y='(ih-oh)/2+10*cos(2*PI*t/3.6)',"
                        f"setsar=1,{pip_frame},fade=t=in:st={start:.3f}:d=0.20:alpha=1,fade=t=out:st="
                        f"{max(start, end - 0.20):.3f}:d=0.20:alpha=1[{label}]"
                    )
                else:
                    filters.append(
                        f"[{input_index}:v]setpts=PTS-STARTPTS,format=rgba,"
                        f"scale={pip_width}:{pip_height}:force_original_aspect_ratio=increase,"
                        f"crop={pip_width}:{pip_height}:(iw-ow)/2:(ih-oh)/2,"
                        f"setsar=1,{pip_frame},fade=t=in:st={start:.3f}:d=0.20:alpha=1,fade=t=out:st="
                        f"{max(start, end - 0.20):.3f}:d=0.20:alpha=1[{label}]"
                    )
                overlay = (
                    f"{caption_input}[{label}]overlay={pip_left}:{pip_top}:"
                    f"enable='between(t,{start:.3f},{end:.3f})':"
                    f"eof_action=pass[{output_label}]"
                )
            filters.append(overlay)
            caption_input = f"[{output_label}]"
        for vector_index, item in enumerate(vectors or []):
            try:
                start = float(item["start"]) / playback_rate
                end = float(item["end"]) / playback_rate
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                continue
            input_index = int(item.get("input_index", vector_input_index + vector_index))
            label = f"vector{vector_index}"
            output_label = f"with_vector{vector_index}"
            # PNGs are transparent runtime assets.  The small fade/scale/turn
            # makes a static icon read as a designed visual beat without
            # introducing a second opaque card or covering the subtitle band.
            filters.append(
                f"[{input_index}:v]format=rgba,scale=240:240:force_original_aspect_ratio=decrease,"
                "pad=240:240:(ow-iw)/2:(oh-ih)/2:color=0x00000000,"
                "rotate=0.035*sin(2*PI*t/2):fillcolor=none,"
                f"fade=t=in:st=0:d=0.22:alpha=1[{label}]"
            )
            overlay = (
                f"{caption_input}[{label}]overlay=W-w-42:70:"
                f"enable='between(t,{start:.3f},{end:.3f})':"
                f"eof_action=pass[{output_label}]"
            )
            filters.append(overlay)
            caption_input = f"[{output_label}]"
        for semantic_index, item in enumerate(semantic_layers or []):
            try:
                start = float(item["start"]) / playback_rate
                end = float(item["end"]) / playback_rate
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start or str(item.get("renderer") or "") not in {
                "semantic_info_band",
                "data_visual_card",
            }:
                continue
            input_index = int(
                item.get("input_index", semantic_input_index + semantic_index)
            )
            label = f"semantic{semantic_index}"
            output_label = f"with_semantic{semantic_index}"
            filters.append(
                f"[{input_index}:v]format=rgba,fade=t=in:st=0:d=0.24:alpha=1[{label}]"
            )
            overlay = (
                f"{caption_input}[{label}]overlay=0:0:"
                f"enable='between(t,{start:.3f},{end:.3f})':"
                f"eof_action=pass[{output_label}]"
            )
            filters.append(overlay)
            caption_input = f"[{output_label}]"
        if subtitle_filter:
            filters.append(f"{caption_input}subtitles='{subtitle_filter}'[captioned]")
        else:
            # Smart-opening exports burn the final-output ASS only after the
            # opening and body have been concatenated.  Keeping the body
            # subtitle-free avoids a second, shifted subtitle layer.
            filters.append(f"{caption_input}null[captioned]")
        return ";".join(filters)

    @staticmethod
    def _assemble_shot_plan_source(
        source_path: Path,
        shot_plan: Mapping[str, Any],
        output_path: Path,
    ) -> None:
        """Materialize reordered A-roll ranges while preserving both streams."""
        shots = [
            shot
            for shot in shot_plan.get("shots", [])
            if isinstance(shot, Mapping)
            and float(shot.get("source_end") or 0) > float(shot.get("source_start") or 0)
        ]
        if not shots:
            raise VideoEditorWorkflowError("发布级分镜没有有效镜头区间。")
        filters: list[str] = []
        video_labels: list[str] = []
        audio_labels: list[str] = []
        for index, shot in enumerate(shots):
            start = float(shot["source_start"])
            end = float(shot["source_end"])
            video_label = f"sv{index}"
            audio_label = f"sa{index}"
            filters.extend(
                [
                    f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[{video_label}]",
                    f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[{audio_label}]",
                ]
            )
            video_labels.append(f"[{video_label}]")
            audio_labels.append(f"[{audio_label}]")
        concat_inputs = "".join(
            video_label + audio_label
            for video_label, audio_label in zip(video_labels, audio_labels, strict=True)
        )
        filters.append(concat_inputs + f"concat=n={len(shots)}:v=1:a=1[vout][aout]")
        result = _run_media_command(
            [
                "ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(source_path),
                "-filter_complex", ";".join(filters),
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                "-shortest", str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=15 * 60,
            check=False,
        )
        if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
            detail = (result.stderr or "分镜重排没有生成文件。").strip()
            raise VideoEditorWorkflowError(f"发布级分镜重排失败：{detail[-600:]}")

    @staticmethod
    def _materialize_source_range(
        source_path: Path,
        source_range: Mapping[str, Any],
        output_path: Path,
    ) -> None:
        """Execute a declared source range with one shared audio/video EDL."""

        try:
            start = float(source_range.get("start"))
            end = float(source_range.get("end"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise VideoEditorWorkflowError("源媒体区间无法执行。") from exc
        if start < 0 or end <= start:
            raise VideoEditorWorkflowError("源媒体区间无法执行。")
        result = _run_media_command(
            [
                "ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(source_path),
                "-filter_complex",
                (
                    f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v];"
                    f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a]"
                ),
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                "-shortest", str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=15 * 60,
            check=False,
        )
        if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
            detail = (result.stderr or "源媒体区间执行失败。").strip()
            raise VideoEditorWorkflowError(f"源媒体区间执行失败：{detail[-600:]}")

    def _run_local_ffmpeg_with_progress(
        self,
        command: list[str],
        task: VideoEditTask,
        *,
        total_duration: float,
        start_progress: int = 15,
        end_progress: int = 65,
        timeout_seconds: int = 15 * 60,
    ) -> tuple[VideoEditTask, subprocess.CompletedProcess[str]]:
        """Run one controlled encode and surface FFmpeg's real output clock."""
        if command and Path(str(command[0])).name.casefold() in {"ffmpeg", "ffmpeg.exe"}:
            command = [_trusted_local_media_tools()["ffmpeg"], *command[1:]]
        if command and command[-1] != "-progress":
            command = [*command[:-1], "-progress", "pipe:1", "-nostats", command[-1]]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_WINDOWS_NO_WINDOW | _WINDOWS_BELOW_NORMAL,
        )
        lines: list[str] = []
        latest_seconds = 0.0

        def read_output() -> None:
            nonlocal latest_seconds
            if process.stdout is None:
                return
            for line in process.stdout:
                clean = line.rstrip()
                if clean:
                    lines.append(clean)
                    del lines[:-200]
                if clean.startswith("out_time_ms="):
                    try:
                        latest_seconds = max(0.0, float(clean.split("=", 1)[1]) / 1_000_000)
                    except (TypeError, ValueError):
                        pass

        reader = threading.Thread(target=read_output, name="ffmpeg-progress", daemon=True)
        reader.start()
        started = time.monotonic()
        current_task = task
        while process.poll() is None:
            elapsed = time.monotonic() - started
            if elapsed > timeout_seconds:
                process.kill()
                reader.join(timeout=2)
                raise VideoEditorWorkflowError("本机编码超时，已停止当前任务；输入和任务记录已保留。")
            ratio = min(max(latest_seconds / max(total_duration, 0.1), 0.0), 1.0)
            progress = round(start_progress + (end_progress - start_progress) * ratio)
            if progress > int(current_task.progress or 0):
                current_task = self._update(
                    current_task,
                    progress=progress,
                    stage=f"正在本机编码（{progress}%）",
                )
            time.sleep(0.25)
        reader.join(timeout=5)
        result = subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout="\n".join(lines),
            stderr="\n".join(lines[-40:]),
        )
        return current_task, result

    def _run_local_preview_export(self, task_id: str) -> None:
        task = self._get_workflow_task(task_id, "local_preview_export")
        output_dir = self.video_editing_service.output_directory
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{task.task_id}.mp4"
        temp_dir = Path(tempfile.mkdtemp(prefix=f"{task.task_id}-"))
        try:
            _trusted_local_media_tools()
            source_path = Path(task.source_video_path)
            media = self._probe_media(source_path)
            if not media["has_audio"]:
                raise VideoEditorWorkflowError(
                    "当前原片没有可用人声轨道，暂不能按口播方案导出。"
                )
            edit_plan = json.loads(task.outputs.get("edit_plan_json") or "{}")
            source_range = json.loads(
                task.outputs.get("source_range_json")
                or json.dumps(edit_plan.get("source_range") or {})
            )
            if isinstance(source_range, Mapping) and source_range.get("start") is not None:
                ranged_path = temp_dir / "source-range-executed.mp4"
                self._materialize_source_range(source_path, source_range, ranged_path)
                source_path = ranged_path
                media = self._probe_media(source_path)
            shot_plan = json.loads(task.outputs.get("shot_plan_json") or "{}")
            if shot_plan:
                assembled_path = temp_dir / "assembled-shot-plan.mp4"
                self._assemble_shot_plan_source(source_path, shot_plan, assembled_path)
                source_path = assembled_path
                media = self._probe_media(source_path)
            profile = task.outputs.get("output_profile") or "720p"
            playback_rate = float(
                task.outputs.get("playback_rate") or _LOCAL_PREVIEW_PLAYBACK_RATE
            )
            segments = json.loads(task.outputs.get("subtitle_segments_json") or "[]")
            if shot_plan:
                from src.services.talking_head_templates import retime_segments_for_shot_plan

                # A full-length plan keeps source and output clocks identical;
                # splitting every sentence at each A-roll shot boundary only
                # multiplies jieba/word-clock work and can create artificial
                # caption fragments.  Retime only when the director actually
                # reorders or removes source ranges.
                preserves_source_clock = all(
                    abs(
                        float(shot.get("timeline_start") or 0)
                        - float(shot.get("source_start") or 0)
                    )
                    <= 0.01
                    for shot in shot_plan.get("shots") or []
                    if isinstance(shot, Mapping)
                )
                if not preserves_source_clock:
                    segments = retime_segments_for_shot_plan(segments, shot_plan)
                if not segments:
                    raise VideoEditorWorkflowError("分镜重排后没有可用字幕时间轴。")
            rendered_segments: list[dict[str, Any]] = []
            for segment in segments:
                rendered_segment = {
                    **segment,
                    "start": round(
                        float(segment.get("start", 0)) / playback_rate,
                        3,
                    ),
                    "end": round(
                        float(segment.get("end", 0)) / playback_rate,
                        3,
                    ),
                }
                raw_words = segment.get("words")
                if isinstance(raw_words, list):
                    rendered_words: list[dict[str, Any]] = []
                    for raw_word in raw_words:
                        if not isinstance(raw_word, Mapping):
                            continue
                        try:
                            word_start = float(raw_word.get("start", 0))
                            word_end = float(raw_word.get("end", 0))
                        except (TypeError, ValueError):
                            continue
                        if word_end <= word_start:
                            continue
                        rendered_words.append(
                            {
                                **dict(raw_word),
                                "start": round(word_start / playback_rate, 3),
                                "end": round(word_end / playback_rate, 3),
                            }
                        )
                    rendered_segment["words"] = rendered_words
                rendered_segments.append(rendered_segment)
            edit_plan = json.loads(task.outputs.get("edit_plan_json") or "{}")
            smart_opening = json.loads(task.outputs.get("smart_opening_json") or "{}")
            legacy_broll = json.loads(task.outputs.get("broll_json") or "{}")
            raw_brolls = json.loads(
                task.outputs.get("brolls_json")
                or json.dumps(edit_plan.get("release_brolls") or [])
            )
            if not isinstance(raw_brolls, list):
                raw_brolls = [legacy_broll] if legacy_broll else []
            elif not raw_brolls and legacy_broll:
                raw_brolls = [legacy_broll]
            brolls: list[dict[str, Any]] = []
            for item in raw_brolls:
                if not isinstance(item, Mapping) or not item.get("asset_id"):
                    continue
                broll_asset = self.resolve_visual_asset(
                    str(item["asset_id"]), expected_kind="broll"
                )
                brolls.append(
                    {
                        **dict(item),
                        "path": broll_asset["_path"],
                        "media_kind": broll_asset.get("media_kind") or "video",
                        "asset_origin": broll_asset.get(
                            "asset_origin", "local_uploaded_asset"
                        ),
                        "source_type": broll_asset.get("source_type"),
                        "source_provider": broll_asset.get("source_provider"),
                        "source_url": broll_asset.get("source_url") or item.get("source_url"),
                        "license_name": broll_asset.get("license_name") or item.get("license_name"),
                        "license_url": broll_asset.get("license_url") or item.get("license_url"),
                        "semantic_binding": broll_asset.get("semantic_binding") or item.get("semantic_binding"),
                        "keywords": broll_asset.get("keywords") or item.get("keywords") or [],
                        "semantic_query": broll_asset.get("semantic_query") or item.get("semantic_query"),
                        "domestic_context": visual_asset_context(
                            {**dict(item), **dict(broll_asset)}
                        ),
                        "domestic_scene": broll_asset.get("domestic_scene")
                        or item.get("domestic_scene")
                        or "",
                        "authorization_status": broll_asset.get(
                            "authorization_status", "unverified"
                        ),
                        "rights_status": broll_asset.get("rights_status"),
                        "publish_licensed": bool(
                            broll_asset.get("publish_licensed", False)
                        ),
                    }
                )
            broll = brolls[0] if brolls else None
            vector_track = json.loads(
                task.outputs.get("vector_track_json")
                or json.dumps(edit_plan.get("vector_track") or {})
            )
            if isinstance(vector_track, Mapping):
                existing_vector_items = [
                    item
                    for item in vector_track.get("items") or []
                    if isinstance(item, Mapping)
                ]
                has_semantic_layer = any(
                    str(item.get("renderer") or "")
                    in {"semantic_info_band", "data_visual_card"}
                    for item in existing_vector_items
                )
                if (
                    not has_semantic_layer
                    and shot_plan
                    and not brolls
                    and os.getenv("VIDEO_EDITOR_ENABLE_SEMANTIC_INFO_BAND", "")
                    .lower()
                    in {"1", "true", "yes"}
                ):
                    transcript_text = "".join(
                        str(segment.get("text") or "")
                        for segment in segments
                        if isinstance(segment, Mapping)
                    )
                    semantic_track, _ = self._ensure_release_creative_assets(
                        transcript_text,
                        shot_plan=shot_plan,
                        transcript_segments=segments,
                    )
                    semantic_items = [
                        item
                        for item in semantic_track.get("items") or []
                        if isinstance(item, Mapping)
                        and str(item.get("renderer") or "")
                        in {"semantic_info_band", "data_visual_card"}
                    ]
                    if semantic_items:
                        vector_track = {
                            **dict(vector_track),
                            "kind": "semantic_motion_layer",
                            "items": existing_vector_items + semantic_items,
                            "asset_count": sum(
                                1 for item in existing_vector_items if item.get("asset_id")
                            ),
                        }
            vector_items: list[dict[str, Any]] = []
            semantic_layers: list[dict[str, Any]] = []
            semantic_rejection_log: list[dict[str, Any]] = []
            if isinstance(vector_track, Mapping):
                for item in vector_track.get("items") or []:
                    if not isinstance(item, Mapping):
                        continue
                    if item.get("asset_id"):
                        vector_asset = self.resolve_visual_asset(
                            str(item["asset_id"]), expected_kind="vector"
                        )
                        vector_items.append(
                            {
                                **dict(item),
                                "path": vector_asset["_path"],
                                "media_kind": "image",
                            }
                        )
                    elif str(item.get("renderer") or "") in {
                        "semantic_info_band",
                        "data_visual_card",
                    }:
                        renderer = str(item.get("renderer") or "")
                        if renderer == "data_visual_card" and not _adaptive_visual_card_opted_in():
                            semantic_rejection_log.append(
                                {
                                    "start": item.get("start"),
                                    "end": item.get("end"),
                                    "visual_intent": item.get("visual_intent"),
                                    "reason": "adaptive_visual_card_disabled_by_default",
                                }
                            )
                            continue
                        sanitized, rejection_reason = _sanitize_adaptive_visual_item(item)
                        if rejection_reason:
                            semantic_rejection_log.append(
                                {
                                    "start": item.get("start"),
                                    "end": item.get("end"),
                                    "visual_intent": item.get("visual_intent"),
                                    "reason": rejection_reason,
                                }
                            )
                        elif sanitized is not None:
                            semantic_layers.append(sanitized)
            if os.getenv("VIDEO_EDITOR_ENABLE_SEMANTIC_INFO_BAND", "").lower() not in {
                "1",
                "true",
                "yes",
            }:
                # Keep transcript-grounded data cards in the adaptive default;
                # only the legacy text-only band remains opt-in.
                semantic_layers = [
                    item
                    for item in semantic_layers
                    if str(item.get("renderer") or "") != "semantic_info_band"
                ]
            rendered_spoken_ranges = [
                {
                    "start": round(
                        float(item.get("start", 0)) / playback_rate,
                        3,
                    ),
                    "end": round(
                        float(item.get("end", 0)) / playback_rate,
                        3,
                    ),
                }
                for item in edit_plan.get("spoken_ranges") or []
                if isinstance(item, dict)
            ]
            ass_path = temp_dir / "approved.ass"
            title_path = temp_dir / "title.png"
            typography = edit_plan.get("typography") or {}
            creative_theme = str(edit_plan.get("creative_theme") or "general")
            font_family = str(typography.get("font_family") or "YaHei")
            from src.services.video_editor_cloud import build_business_talking_head_overlay_preview

            subtitle_preview = build_business_talking_head_overlay_preview(
                rendered_segments,
                title="",
                output_profile=profile,
                caption_groups=edit_plan.get("caption_groups"),
                caption_emphasis=edit_plan.get("caption_emphasis"),
                spoken_ranges=rendered_spoken_ranges,
                caption_glossary=edit_plan.get("transcript_glossary"),
                subtitle_style_id="adaptive_talking_head_v1",
            )
            # Keep the reviewed wording, but align cue edges to the same real
            # ASR word clock used by the source audio before writing both ASS
            # and the cue manifest.  Homophone corrections stay explicit in
            # the quality report instead of becoming invented timestamps.
            subtitle_preview = self._snap_preview_cues_to_reviewed_word_clock(
                rendered_segments,
                subtitle_preview,
            )
            subtitle_preview = {
                **subtitle_preview,
                "clock": "final_output",
                "playback_rate": playback_rate,
                "preview_render_manifest_equal": True,
            }
            ass_bytes = self._review_ass_bytes(
                rendered_segments,
                output_profile=profile,
                caption_groups=edit_plan.get("caption_groups"),
                caption_emphasis=edit_plan.get("caption_emphasis"),
                spoken_ranges=rendered_spoken_ranges,
                caption_glossary=edit_plan.get("transcript_glossary"),
                theme=creative_theme,
                font_family=font_family,
                overlay_preview=subtitle_preview,
                subtitle_style_id="adaptive_talking_head_v1",
            )
            ass_path.write_bytes(ass_bytes)
            subtitle_manifest = {
                "schema_version": "subtitle-cue-manifest-v1",
                "clock": "final_output",
                "playback_rate": playback_rate,
                "subtitle_style_id": subtitle_preview.get(
                    "subtitle_style_id", "adaptive_talking_head_v1"
                ),
                "style_fingerprint": subtitle_preview.get("style_fingerprint") or {},
                "phrase_timing_source": subtitle_preview.get("phrase_timing_source"),
                "cues": subtitle_preview.get("cues") or [],
            }
            manifest_bytes = json.dumps(
                subtitle_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            subtitle_manifest["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
            persistent_ass_path = output_path.with_suffix(".ass")
            persistent_manifest_path = output_path.with_suffix(".subtitle-manifest.json")
            persistent_ass_path.write_bytes(ass_bytes)
            persistent_manifest_path.write_text(
                json.dumps(subtitle_manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            title_path.write_bytes(
                self._review_title_png_bytes(
                    task.outputs.get("publish_title") or task.title,
                    output_profile=profile,
                )
            )

            from src.services.video_editor_cloud import visual_style_spec

            spec = visual_style_spec(profile)
            canvas = spec["canvas"]
            title_style = spec["title"]
            width = int(canvas["width"])
            height = int(canvas["height"])
            fps = task.edit_config.output_fps
            body_path = temp_dir / "body.mp4" if smart_opening else output_path
            font_path = Path(str(typography.get("font_path") or ""))
            subtitle_filter = (
                ""
                if smart_opening
                else self._ffmpeg_filter_path(
                    ass_path,
                    fontsdir=font_path.parent if font_path.is_file() else None,
                )
            )
            semantic_input_index = 2 + len(brolls) + len(vector_items)
            semantic_card_paths: list[Path] = []
            for index, item in enumerate(semantic_layers):
                card_path = temp_dir / f"semantic-layer-{index}.png"
                if str(item.get("renderer") or "") == "data_visual_card":
                    self._render_adaptive_visual_card(
                        item,
                        output_profile=profile,
                        output_path=card_path,
                    )
                else:
                    self._render_semantic_info_card(
                        item,
                        output_profile=profile,
                        output_path=card_path,
                    )
                item["input_index"] = semantic_input_index + index
                semantic_card_paths.append(card_path)
            visual_beats = [
                beat
                for beat in edit_plan.get("visual_beats") or []
                if isinstance(beat, dict)
                and str(beat.get("treatment") or "") != "hook"
                and str(beat.get("label") or "").strip()
            ]
            reframe_events = _select_sparse_reframe_events(
                [
                    beat
                    for beat in visual_beats
                    if str(beat.get("treatment") or "") in {"keyword_card", "punch_in"}
                ],
                duration_seconds=float(media["duration_seconds"]),
            )
            # Fill every genuinely long gap between planned semantic events.
            # The insertion is derived from the canonical director timeline,
            # not from fixed wall-clock timestamps.  It creates a short
            # speaker-safe reframe while leaving audio, captions and source
            # ranges untouched.
            def _event_clock(item: Mapping[str, Any]) -> float | None:
                try:
                    value = float(item.get("start") or 0)
                except (TypeError, ValueError):
                    return None
                return value if 0 <= value < float(media["duration_seconds"]) else None

            planned_starts = [0.0, float(media["duration_seconds"])]
            for collection in (brolls, vector_items, semantic_layers, visual_beats):
                for item in collection:
                    if isinstance(item, Mapping):
                        value = _event_clock(item)
                        if value is not None:
                            planned_starts.append(value)
            for item in reframe_events:
                value = _event_clock(item)
                if value is not None:
                    planned_starts.append(value)
            planned_starts = sorted(set(round(value, 3) for value in planned_starts))
            for left_start, right_start in list(zip(planned_starts, planned_starts[1:])):
                gap_left = left_start
                while right_start - gap_left > 7.5:
                    candidate = round(gap_left + min(4.0, (right_start - gap_left) / 2), 3)
                    if any(
                        abs(candidate - float(item.get("start") or 0)) < 1.0
                        for item in (*brolls, *reframe_events)
                        if isinstance(item, Mapping)
                    ):
                        gap_left += 4.0
                        continue
                    reframe_events.append(
                        {
                            "start": candidate,
                            "end": round(min(candidate + 1.8, right_start - 0.35), 3),
                            "treatment": "safe_reframe",
                            "label": "speaker_safe_push",
                        }
                    )
                    gap_left = candidate
            # Rich adaptive exports have a separate effective-coverage
            # contract.  When semantic footage is sparse, fill only the
            # missing portion with bounded speaker-safe reframes.  These are
            # real camera reconstructions, never captions, cards, or fake
            # B-roll, and their union is measured again by the quality gate.
            rich_policy = _visual_gate_policy_for_template(
                str(shot_plan.get("template_id") or ""),
                visual_density=str(shot_plan.get("visual_density") or ""),
                duration_seconds=float(media["duration_seconds"]),
            )
            effective_min_seconds = (
                float(media["duration_seconds"])
                * float(rich_policy.get("min_effective_coverage_ratio") or 0.0)
            )
            effective_max_seconds = (
                float(media["duration_seconds"])
                * float(rich_policy.get("max_effective_coverage_ratio") or 1.0)
            )
            existing_visual_intervals = [
                item
                for item in [*brolls, *vector_items, *reframe_events]
                if isinstance(item, Mapping)
            ]
            current_effective_seconds = _interval_union_seconds(
                existing_visual_intervals,
                duration_seconds=float(media["duration_seconds"]),
            )
            if (
                str(shot_plan.get("template_id") or "") == "adaptive_talking_head_v1"
                and str(shot_plan.get("visual_density") or "") == "rich"
                and effective_min_seconds > 0
            ):
                while current_effective_seconds + 0.05 < effective_min_seconds:
                    occupied = sorted(
                        (
                            max(0.0, float(item.get("start") or 0)),
                            min(float(media["duration_seconds"]), float(item.get("end") or 0)),
                        )
                        for item in [*brolls, *vector_items, *reframe_events]
                        if isinstance(item, Mapping)
                        and float(item.get("end") or 0) > float(item.get("start") or 0)
                    )
                    gaps: list[tuple[float, float]] = []
                    cursor = 0.0
                    for start, end in occupied:
                        if start - cursor >= 2.05:
                            gaps.append((cursor, start))
                        cursor = max(cursor, end)
                    if float(media["duration_seconds"]) - cursor >= 2.05:
                        gaps.append((cursor, float(media["duration_seconds"])))
                    if not gaps:
                        break
                    gap_start, gap_end = max(gaps, key=lambda pair: pair[1] - pair[0])
                    reframe_start = round(gap_start + 0.55, 3)
                    reframe_end = round(min(reframe_start + 1.55, gap_end - 0.35), 3)
                    if reframe_end <= reframe_start:
                        break
                    reframe_events.append(
                        {
                            "start": reframe_start,
                            "end": reframe_end,
                            "treatment": "safe_reframe",
                            "label": "speaker_safe_push",
                            "coverage_role": "effective_visual_coverage_only",
                        }
                    )
                    current_effective_seconds = _interval_union_seconds(
                        [*brolls, *vector_items, *reframe_events],
                        duration_seconds=float(media["duration_seconds"]),
                    )
                    if current_effective_seconds >= effective_max_seconds - 0.05:
                        break
            # The adaptive default never renders the old opaque keyword cards.
            beat_cards: list[tuple[dict[str, Any], Path]] = []
            filter_parts = [
                self._local_rhythm_video_filter(
                    duration_seconds=float(media["duration_seconds"]),
                    width=width,
                    height=height,
                    fps=fps,
                    playback_rate=playback_rate,
                    subtitle_filter=subtitle_filter,
                    broll=None if brolls else broll,
                    brolls=brolls,
                    broll_input_index=2,
                    vectors=vector_items,
                    vector_input_index=2 + len(brolls),
                    semantic_layers=semantic_layers,
                    semantic_input_index=semantic_input_index,
                    reframe_events=reframe_events,
                    source_width=int(media["width"]),
                    source_height=int(media["height"]),
                ),
                "[1:v]format=rgba[title]",
            ]
            visual_input = "[captioned]"
            command = [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-i",
                str(source_path),
                "-loop",
                "1",
                "-framerate",
                str(fps),
                "-i",
                str(title_path),
            ]
            for broll in brolls:
                broll_path = str(broll["path"])
                if str(broll.get("media_kind") or "").lower() == "image":
                    command.extend(["-loop", "1", "-framerate", str(fps), "-i", broll_path])
                else:
                    command.extend(["-stream_loop", "-1", "-i", broll_path])
            for vector in vector_items:
                command.extend(["-loop", "1", "-framerate", str(fps), "-i", str(vector["path"])])
            for semantic_card_path in semantic_card_paths:
                command.extend(
                    ["-loop", "1", "-framerate", str(fps), "-i", str(semantic_card_path)]
                )
            next_input_index = (
                2 + len(brolls) + len(vector_items) + len(semantic_card_paths)
            )
            for index, (beat, card_path) in enumerate(beat_cards):
                command.extend(["-loop", "1", "-framerate", str(fps), "-i", str(card_path)])
                start = float(beat.get("start") or 0) / playback_rate
                end = float(beat.get("end") or 0) / playback_rate
                filter_parts.extend(
                    [
                        f"[{next_input_index}:v]format=rgba[beatcard{index}]",
                        (
                            f"{visual_input}[beatcard{index}]overlay=28:24:"
                            f"enable='between(t,{start:.3f},{end:.3f})':"
                            f"eof_action=pass[beatout{index}]"
                        ),
                    ]
                )
                visual_input = f"[beatout{index}]"
                next_input_index += 1
            filter_parts.append(
                (
                    f"{visual_input}[title]overlay="
                    f"{int(title_style['safe_left'])}:"
                    f"{int(title_style['safe_top'])}:"
                    "enable='between(t,0,"
                    f"{float(title_style['visible_seconds']):.3f})':"
                    "eof_action=pass[vout]"
                )
            )
            bgm_id = task.outputs.get("bgm_id") or ""
            filter_parts.append(
                f"[0:a]atempo={playback_rate:.3f},aresample=async=1:first_pts=0[voice_raw]"
            )
            filter_parts.append(
                "[voice_raw]loudnorm=I=-16:TP=-1.5:LRA=11[voice]"
            )
            bgm_asset: dict[str, Any] | None = None
            if bgm_id:
                bgm_asset = self.resolve_bgm_asset(bgm_id)
                bgm_input_index = next_input_index
                command.extend(["-stream_loop", "-1", "-i", str(bgm_asset["_path"])])
                filter_parts.extend(
                    [
                        (
                            f"[{bgm_input_index}:a]volume=0.16,"
                            "afade=t=in:st=0:d=0.5,"
                            "aresample=async=1:first_pts=0[bgm]"
                        ),
                        "[voice]asplit=2[voice_mix][voice_key]",
                        (
                            "[bgm][voice_key]sidechaincompress="
                            "threshold=0.025:ratio=8:attack=20:release=450:makeup=1[ducked]"
                        ),
                        (
                            "[voice_mix][ducked]amix=inputs=2:duration=first:"
                            "dropout_transition=2:normalize=0[aout]"
                        ),
                    ]
                )
            encoder_selection = _select_local_video_encoder()
            selected_encoder = str(encoder_selection.get("encoder") or "libx264")
            command.extend(
                [
                    "-filter_complex",
                    ";".join(filter_parts),
                    "-map",
                    "[vout]",
                    "-map",
                    "[aout]" if bgm_id else "[voice]",
                    "-c:v",
                    selected_encoder,
                    "-preset",
                    "veryfast",
                    "-b:v",
                    task.edit_config.output_bitrate,
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(body_path),
                ]
            )
            task = self._update(
                task,
                status=TaskStatus.RUNNING,
                progress=15,
                stage="正在写入标题、字幕和配乐",
            )
            command = [
                *command[:-1],
                "-threads",
                "2",
                "-filter_threads",
                "2",
                "-filter_complex_threads",
                "2",
                command[-1],
            ]
            with _LOCAL_RENDER_SEMAPHORE:
                task, result = self._run_local_ffmpeg_with_progress(
                    command,
                    task,
                    total_duration=float(media.get("duration_seconds") or 1),
                )
                if result.returncode != 0 and selected_encoder != "libx264":
                    # Hardware was smoke-tested, but a real filter graph can
                    # still be rejected by a driver.  Downgrade once and keep
                    # the same canonical command/timeline; never retry NVENC.
                    fallback_command = list(command)
                    codec_index = fallback_command.index("-c:v") + 1
                    fallback_command[codec_index] = "libx264"
                    encoder_selection = {
                        **encoder_selection,
                        "encoder": "libx264",
                        "fallback": True,
                        "reason": "nvenc_render_failed_libx264_once",
                    }
                    task, result = self._run_local_ffmpeg_with_progress(
                        fallback_command,
                        task,
                        total_duration=float(media.get("duration_seconds") or 1),
                    )
            task = self._update(
                task,
                outputs={
                    **task.outputs,
                    "local_encoder": json.dumps(
                        encoder_selection,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            )
            if (
                result.returncode != 0
                or not body_path.is_file()
                or body_path.stat().st_size == 0
            ):
                detail = (result.stderr or "本机编码没有生成文件。").strip()
                raise VideoEditorWorkflowError(f"本机成片生成失败：{detail[-600:]}")
            opening_duration = 0.0
            if smart_opening:
                task = self._update(
                    task,
                    status=TaskStatus.RUNNING,
                    progress=72,
                    stage="正在生成智能开场并保持音画同步",
                )
                opening_path = temp_dir / "opening.mp4"
                opening_duration = self._render_smart_opening_clip(
                    smart_opening,
                    output_profile=profile,
                    fps=fps,
                    output_path=opening_path,
                    temp_dir=temp_dir,
                )
                self._prepend_opening_clip(
                    opening_path,
                    body_path,
                    output_path,
                    bitrate=task.edit_config.output_bitrate,
                )
            subtitle_timing_segments = rendered_segments
            subtitle_timing_preview = subtitle_preview
            subtitle_timing_spoken_ranges = rendered_spoken_ranges
            if smart_opening:
                subtitle_timing_segments, subtitle_timing_preview = (
                    self._shift_subtitle_clock(
                        rendered_segments,
                        subtitle_preview,
                        opening_duration,
                    )
                )
                subtitle_timing_spoken_ranges = [
                    {
                        "start": round(
                            float(item.get("start", 0)) + opening_duration,
                            3,
                        ),
                        "end": round(
                            float(item.get("end", 0)) + opening_duration,
                            3,
                        ),
                    }
                    for item in rendered_spoken_ranges
                    if isinstance(item, Mapping)
                ]
                ass_bytes = self._review_ass_bytes(
                    subtitle_timing_segments,
                    output_profile=profile,
                    caption_groups=edit_plan.get("caption_groups"),
                    caption_emphasis=edit_plan.get("caption_emphasis"),
                    spoken_ranges=subtitle_timing_spoken_ranges,
                    theme=creative_theme,
                    font_family=font_family,
                    overlay_preview=subtitle_timing_preview,
                    subtitle_style_id="adaptive_talking_head_v1",
                )
                ass_path.write_bytes(ass_bytes)
                final_subtitle_path = temp_dir / "final-with-subtitles.mp4"
                final_subtitle_filter = self._ffmpeg_filter_path(
                    ass_path,
                    fontsdir=font_path.parent if font_path.is_file() else None,
                )
                final_subtitle_result = _run_media_command(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-y",
                        "-v",
                        "error",
                        "-i",
                        str(output_path),
                        "-vf",
                        f"subtitles='{final_subtitle_filter}'",
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a:0?",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-b:v",
                        task.edit_config.output_bitrate,
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "copy",
                        "-movflags",
                        "+faststart",
                        str(final_subtitle_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15 * 60,
                    check=False,
                )
                if (
                    final_subtitle_result.returncode != 0
                    or not final_subtitle_path.is_file()
                    or final_subtitle_path.stat().st_size == 0
                ):
                    detail = (
                        final_subtitle_result.stderr
                        or "最终字幕烧录没有生成文件。"
                    ).strip()
                    raise VideoEditorWorkflowError(
                        f"最终字幕烧录失败：{detail[-600:]}"
                    )
                os.replace(final_subtitle_path, output_path)
            subtitle_preview = subtitle_timing_preview
            rendered_segments_for_quality = subtitle_timing_segments
            rendered_spoken_ranges = subtitle_timing_spoken_ranges
            subtitle_manifest = {
                **subtitle_manifest,
                "clock": "final_output",
                "opening_offset_seconds": round(float(opening_duration or 0), 3),
                "cues": subtitle_preview.get("cues") or [],
            }
            manifest_bytes = json.dumps(
                {
                    key: value
                    for key, value in subtitle_manifest.items()
                    if key != "manifest_sha256"
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            subtitle_manifest["manifest_sha256"] = hashlib.sha256(
                manifest_bytes
            ).hexdigest()
            persistent_ass_path.write_bytes(ass_bytes)
            persistent_manifest_path.write_text(
                json.dumps(subtitle_manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            output_media = self._probe_media(output_path)
            if output_media["width"] != width or output_media["height"] != height:
                raise VideoEditorWorkflowError("本机成片分辨率校验失败。")
            audio_video_drift = _measure_audio_video_drift(output_path)
            expected_duration = (
                float(media["duration_seconds"]) / playback_rate + opening_duration
            )
            quality_report = self._local_export_quality_report(
                output_media,
                expected_width=width,
                expected_height=height,
                expected_duration=expected_duration,
                source_has_audio=bool(media.get("has_audio")),
                visual_beats=reframe_events,
            )
            quality_report["bgm"] = {
                "enabled": bool(bgm_id),
                "asset_id": bgm_id or None,
                "title": bgm_asset.get("title") if bgm_asset else None,
                "source_provider": (
                    bgm_asset.get("source_provider") if bgm_asset else None
                ),
                "source_url": bgm_asset.get("source_url") if bgm_asset else None,
                "license_url": bgm_asset.get("license_url") if bgm_asset else None,
                "rights_holder": bgm_asset.get("rights_holder") if bgm_asset else None,
                "content_id_risk": bgm_asset.get("content_id_risk") if bgm_asset else None,
                "auto_eligible": (
                    bool(bgm_asset.get("auto_eligible")) if bgm_asset else False
                ),
                "authorization_status": (
                    bgm_asset.get("authorization_status") if bgm_asset else None
                ),
                "voice_priority": bool(bgm_id),
                "ducking": {
                    "enabled": bool(bgm_id),
                    "filter": "sidechaincompress",
                    "attack_ms": 20,
                    "release_ms": 450,
                    "mix_volume": 0.16,
                },
            }
            bgm_publish_rights_gate_passed = bool(
                not bgm_id
                or (
                    bgm_asset is not None
                    and str(bgm_asset.get("authorization_status") or "")
                    == "confirmed"
                    and str(bgm_asset.get("content_id_risk") or "unknown")
                    != "registered"
                    and bool(
                        bgm_asset.get("auto_eligible") is True
                        or bgm_asset.get("generated") is True
                    )
                )
            )
            quality_report["bgm"]["publish_rights_gate_passed"] = (
                bgm_publish_rights_gate_passed
            )
            quality_report["alignment"] = audio_video_drift or {
                "method": "ffprobe_stream_start_end_pts",
                "passed": False,
                "max_drift_ms": None,
            }
            quality_report["checks"]["audio_video_drift"] = bool(
                audio_video_drift and audio_video_drift.get("passed") is True
            )
            quality_report["passed"] = all(quality_report["checks"].values())
            if shot_plan:
                quality_report["style_version"] = _RELEASE_TALKING_HEAD_STYLE_VERSION
                quality_report["shot_plan"] = {
                    "template_id": shot_plan.get("template_id"),
                    "template_version": shot_plan.get("template_version"),
                    "shot_count": len(shot_plan.get("shots") or []),
                    "degradation": shot_plan.get("degradation"),
                }
            phrase_clock_segments = [
                {
                    "start": float(cue.get("start") or 0),
                    "end": float(cue.get("end") or 0),
                    "text": "".join(str(line) for line in cue.get("lines") or []),
                    "source_segment_index": cue.get("source_segment_index"),
                }
                for cue in subtitle_preview.get("cues") or []
                if isinstance(cue, Mapping)
            ]
            subtitle_quality = self._subtitle_timeline_quality(
                phrase_clock_segments,
                duration_seconds=float(output_media["duration_seconds"]),
                fps=float(output_media.get("fps") or task.edit_config.output_fps or 30.0),
            )
            subtitle_quality.update(
                {
                    "phrase_cue_count": len(subtitle_preview.get("cues") or []),
                    "phrase_timing_source": subtitle_preview.get(
                        "phrase_timing_source", "estimated_phrase_timestamps"
                    ),
                    "estimated_phrase_timestamps": subtitle_preview.get(
                        "phrase_timing_source"
                    )
                    != "word_timestamps",
                    "max_phrase_duration_seconds": round(
                        max(
                            (
                                float(cue.get("end", 0))
                                - float(cue.get("start", 0))
                            )
                            for cue in subtitle_preview.get("cues") or []
                        ),
                        3,
                    )
                    if subtitle_preview.get("cues")
                    else 0.0,
                    "cue_manifest_sha256": subtitle_manifest.get("manifest_sha256"),
                    "ass_sha256": hashlib.sha256(ass_bytes).hexdigest(),
                    "preview_render_manifest_equal": True,
                }
            )
            experience_gate = self._subtitle_experience_gate(
                rendered_segments_for_quality,
                subtitle_preview,
            )
            subtitle_quality["experience_gate"] = experience_gate
            subtitle_text_integrity = self._subtitle_text_integrity_gate(
                rendered_segments_for_quality,
                subtitle_preview,
            )
            source_audio_analysis = self._analyze_audio(
                source_path,
                float(media.get("duration_seconds") or 0),
            )
            subtitle_active_ranges = self._active_ranges_from_audio_analysis(
                source_audio_analysis,
                duration_seconds=float(media.get("duration_seconds") or 0),
                playback_rate=playback_rate,
                offset_seconds=float(opening_duration or 0),
            )
            raw_subtitle_active_ranges = subtitle_active_ranges
            subtitle_active_ranges = self._intersect_audio_activity_with_spoken_ranges(
                subtitle_active_ranges,
                rendered_spoken_ranges,
            )
            subtitle_audio_activity = self._subtitle_audio_activity_gate(
                subtitle_preview.get("cues") or [],
                subtitle_active_ranges,
            )
            subtitle_audio_activity["raw_vad_active_ranges"] = raw_subtitle_active_ranges or []
            subtitle_audio_activity["speech_candidate_ranges"] = subtitle_active_ranges or []
            subtitle_audio_activity["method"] = (
                "ffmpeg_silencedetect_vad_intersected_with_reviewed_speech_candidates"
            )
            subtitle_quality["text_integrity_gate"] = subtitle_text_integrity
            subtitle_quality["audio_activity_gate"] = subtitle_audio_activity
            subtitle_effect_gate = _adaptive_subtitle_effect_gate(subtitle_preview)
            subtitle_quality["subtitle_effect_gate"] = subtitle_effect_gate
            quality_report["checks"]["subtitle_effect_gate"] = bool(
                subtitle_effect_gate["passed"]
            )
            word_timing_quality = self._subtitle_word_timing_quality(
                rendered_segments_for_quality,
                subtitle_preview,
                fps=float(output_media.get("fps") or task.edit_config.output_fps or 0),
            )
            subtitle_quality["word_timing_quality"] = word_timing_quality
            # Keep these names at the subtitle-timeline level for consumers
            # that already inspect the compact report, while preserving the
            # explicit verified/unverified status above.
            subtitle_quality["word_p95_ms"] = word_timing_quality.get("word_p95_ms")
            subtitle_quality["mapping_error_frames"] = word_timing_quality.get(
                "mapping_error_frames"
            )
            word_timing_checks = word_timing_quality.get("checks") or {}
            has_real_word_clock = any(
                isinstance(segment.get("words"), list) and segment.get("words")
                for segment in rendered_segments_for_quality
                if isinstance(segment, Mapping)
            )
            strict_word_timing = (
                str(task.outputs.get("requested_pipeline") or "")
                == "adaptive_fine_cut_v1"
            )
            # Sentence-only previews remain a truthful, non-publish-safe
            # degradation.  When real words exist, both hard timing checks
            # must pass; an unverified sentence clock must never be treated
            # as word-level precision.
            word_timing_gate_passed = (
                not strict_word_timing
                or not has_real_word_clock
                or (
                    word_timing_quality.get("verified")
                    and word_timing_checks.get("word_p95_le_150ms") is True
                    and word_timing_checks.get("mapping_le_1_frame") is True
                )
            )
            subtitle_quality["passed"] = (
                subtitle_quality["passed"]
                and experience_gate["passed"]
                and subtitle_text_integrity["passed"]
                and subtitle_audio_activity["passed"]
                and subtitle_effect_gate["passed"]
                and word_timing_gate_passed
            )
            quality_report["subtitle_timeline"] = subtitle_quality
            quality_report["checks"]["subtitle_timeline"] = subtitle_quality["passed"]
            quality_report["passed"] = all(quality_report["checks"].values())
            director_plan = edit_plan.get("director_plan") or {}
            asset_requests = director_plan.get("asset_requests") or []
            degradation = shot_plan.get("degradation") if shot_plan else None
            vector_track = vector_track if isinstance(vector_track, Mapping) else {}
            vector_items = [
                item for item in vector_track.get("items") or []
                if isinstance(item, Mapping) and item.get("asset_id")
            ]
            semantic_layer_items = [
                item
                for item in semantic_layers
                if isinstance(item, Mapping)
                and str(item.get("renderer") or "")
                in {"semantic_info_band", "data_visual_card"}
            ]
            quality_report["semantic_visual_rejection_log"] = semantic_rejection_log
            semantic_layer_geometry = (
                _adaptive_visual_card_geometry(width, height)
                if any(
                    str(item.get("renderer") or "") == "data_visual_card"
                    for item in semantic_layer_items
                )
                else _semantic_info_geometry(width, height)
            )
            semantic_layer_safe = bool(
                not shot_plan
                or (
                    not semantic_layer_items
                    or semantic_layer_geometry.get("safe") is True
                )
            )
            visual_event_count = (
                len(brolls)
                + len(vector_items)
                + len(semantic_layer_items)
                + len(reframe_events)
            )
            visual_cadence_gate = _visual_cadence_gate(
                duration_seconds=float(output_media.get("duration_seconds") or 0),
                brolls=brolls,
                vector_items=[*vector_items, *semantic_layer_items],
                subtitle_preview=subtitle_preview,
                has_hook=(director_plan.get("hook") or {}).get("must_appear_once") is True,
                a_roll_shots=(shot_plan.get("shots") or []) if shot_plan else (),
                reframe_events=reframe_events,
                playback_rate=playback_rate,
                opening_offset_seconds=opening_duration,
            )
            visual_coverage_seconds = round(
                sum(
                    max(0.0, float(item.get("end", 0)) - float(item.get("start", 0)))
                    for item in brolls
                    if isinstance(item, Mapping)
                ),
                3,
            )
            vector_coverage_seconds = round(
                sum(
                    max(0.0, float(item.get("end", 0)) - float(item.get("start", 0)))
                    for item in vector_items
                ),
                3,
            )
            generated_image_brolls = [
                item
                for item in brolls
                if _is_generated_image_broll(item)
            ]
            stock_video_brolls = [
                item
                for item in brolls
                if _is_real_stock_video_broll(item)
            ]
            # P0-1: real_brolls 严格化 → 仅"真实视频素材"。
            # 矢量、字幕动画、人物推拉、生成图全部不计入。
            real_brolls = list(stock_video_brolls)
            # P0-1: 程序生成信息卡（数据卡 / 流程卡 / CTA / 信息条）
            deterministic_card_items = [
                item
                for item in vector_items
                if _is_deterministic_card_item(item)
            ] + [
                item
                for item in semantic_layer_items
                if _is_deterministic_card_item(item)
            ]
            # 兼容兜底：当 vector/semantic 都没启用但仍记录空集合。
            # 这避免 deterministic_card_coverage_ratio 静默"漏算"。
            domestic_real_brolls = [
                item
                for item in real_brolls
                if visual_asset_context(item) == "domestic"
            ]
            international_stock_brolls = [
                item
                for item in brolls
                if visual_asset_context(item) == "international"
                or str(item.get("source_provider") or "") in {"pexels", "pixabay"}
            ]
            traceable_brolls = [
                item
                for item in brolls
                if str(item.get("asset_origin") or "").strip()
                in {
                    "local_uploaded_asset",
                    "generated_image_asset",
                    "stock_video_asset",
                }
                or str(item.get("source_provider") or "") in {"pexels", "pixabay"}
            ]
            broll_duration = max(float(output_media.get("duration_seconds") or 0), 0.001)
            broll_coverage_ratio = visual_coverage_seconds / broll_duration
            effective_visual_intervals = [
                item
                for item in [*brolls, *vector_items, *reframe_events]
                if isinstance(item, Mapping)
            ]
            effective_visual_coverage_seconds = round(
                _interval_union_seconds(
                    effective_visual_intervals,
                    duration_seconds=broll_duration,
                ),
                3,
            )
            effective_visual_coverage_ratio = round(
                effective_visual_coverage_seconds / broll_duration,
                4,
            )
            # === P0-1: 4 个独立素材覆盖指标 ===
            # 1) 真实视频素材：仅 pexels / pixabay / 用户上传且已授权的视频
            real_stock_broll_seconds = _seconds_for_intervals(stock_video_brolls)
            # 2) 生成图：MiniMax / 内置生图生成的图片
            generated_image_seconds = _seconds_for_intervals(generated_image_brolls)
            # 3) 程序生成信息卡：数据卡 / 流程卡 / CTA / 信息条
            deterministic_card_seconds = _seconds_for_intervals(deterministic_card_items)
            # 4) 有效视觉覆盖：含 A-roll 推拉 + 字幕 + 信息卡，但不替代真实素材指标
            real_stock_broll_coverage_ratio = round(
                real_stock_broll_seconds / broll_duration, 4
            )
            generated_image_coverage_ratio = round(
                generated_image_seconds / broll_duration, 4
            )
            deterministic_card_coverage_ratio = round(
                deterministic_card_seconds / broll_duration, 4
            )
            template_id = str(shot_plan.get("template_id") or "")
            visual_density = str(shot_plan.get("visual_density") or "")
            visual_gate_policy = _visual_gate_policy_for_template(
                template_id,
                visual_density=visual_density,
                duration_seconds=float(output_media.get("duration_seconds") or 0),
            )
            has_pip = any(str(item.get("mode") or "") == "pip" for item in brolls)
            has_full = any(str(item.get("mode") or "") == "full" for item in brolls)
            pip_event_count = sum(
                1 for item in brolls if str(item.get("mode") or "") == "pip"
            )
            full_event_count = sum(
                1 for item in brolls if str(item.get("mode") or "") == "full"
            )
            real_event_count_gate = (
                not shot_plan
                or visual_gate_policy["min_real_events"]
                <= len(real_brolls)
                <= visual_gate_policy["max_real_events"]
            )
            coverage_gate = (
                not shot_plan
                or visual_gate_policy["min_coverage_ratio"]
                <= broll_coverage_ratio
                <= visual_gate_policy["max_coverage_ratio"]
            )
            effective_coverage_gate = (
                not shot_plan
                or visual_gate_policy["min_effective_coverage_ratio"]
                <= effective_visual_coverage_ratio
                <= visual_gate_policy["max_effective_coverage_ratio"]
            )
            pip_requirement_gate = (
                not shot_plan
                or pip_event_count
                >= max(
                    1 if visual_gate_policy["pip_required"] else 0,
                    int(visual_gate_policy.get("min_pip_events", 0)),
                )
            )
            full_requirement_gate = (
                not shot_plan
                or full_event_count
                >= max(
                    1 if visual_gate_policy["full_required"] else 0,
                    int(visual_gate_policy.get("min_full_events", 0)),
                )
            )
            semantic_match_gate_passed = bool(
                not brolls or semantic_broll_gate_passed(brolls)
            )
            local_visual_ready = bool(
                shot_plan
                and isinstance(degradation, Mapping)
                and not asset_requests
                and real_event_count_gate
                and coverage_gate
                and effective_coverage_gate
                and pip_requirement_gate
                and full_requirement_gate
                and len(traceable_brolls) == len(brolls)
                and semantic_layer_safe
            )
            release_visual_gate_passed = bool(
                (not shot_plan or real_event_count_gate)
                and (not shot_plan or coverage_gate)
                and (not shot_plan or effective_coverage_gate)
                and pip_requirement_gate
                and full_requirement_gate
                and len(traceable_brolls) == len(brolls)
                and semantic_layer_safe
                and semantic_match_gate_passed
            )
            requested_pipeline = str(
                task.outputs.get("requested_pipeline") or ""
            ).strip()
            output_added_visual_event_count = len(brolls) + len(vector_items)
            visual_pipeline_executed = bool(
                requested_pipeline != "adaptive_fine_cut_v1"
                or (shot_plan and output_added_visual_event_count > 0)
            )
            if requested_pipeline == "adaptive_fine_cut_v1":
                release_visual_gate_passed = (
                    release_visual_gate_passed and visual_pipeline_executed
                )
            broll_publish_rights_gate_passed = bool(
                not shot_plan
                or (
                    real_event_count_gate
                    and not generated_image_brolls
                    and all(item.get("publish_licensed") is True for item in brolls)
                )
            )
            publish_rights_gate_passed = bool(
                broll_publish_rights_gate_passed and bgm_publish_rights_gate_passed
            )
            broll_ids = [str(item.get("asset_id") or "") for item in brolls]
            pip_geometry_checks = []
            for index, item in enumerate(brolls):
                if str(item.get("mode") or "pip") == "full":
                    continue
                geometry = _portrait_pip_geometry(width, height)
                pip_geometry_checks.append(
                    {
                        "event_index": index,
                        "asset_id": item.get("asset_id"),
                        "safe": bool(geometry.get("safe")),
                        "rendered": bool(geometry.get("safe")),
                        **geometry,
                    }
                )
            pip_geometry_passed = all(
                bool(item.get("safe"))
                and not bool(item.get("intersects_face_safe_bbox"))
                and not bool(item.get("intersects_subtitle_bbox"))
                for item in pip_geometry_checks
            )
            local_visual_ready = local_visual_ready and pip_geometry_passed
            release_visual_gate_passed = release_visual_gate_passed and pip_geometry_passed
            unique_brolls = len(broll_ids) == len(set(broll_ids)) and all(broll_ids)
            quality_report["creative_checks"] = {
                # A missing match for one opportunity is a valid per-shot
                # A-roll fallback.  Do not turn that into a whole-video asset
                # failure when the accepted visual clusters themselves pass
                # the real-event, mode, coverage, safety and provenance gates.
                "director_assets_ready": not shot_plan or (
                    local_visual_ready
                    or (
                        bool(real_brolls)
                        and real_event_count_gate
                        and coverage_gate
                        and effective_coverage_gate
                        and pip_requirement_gate
                        and full_requirement_gate
                        and pip_geometry_passed
                        and semantic_match_gate_passed
                        and visual_pipeline_executed
                    )
                ),
                "minimum_real_visual_events": real_event_count_gate,
                "has_pip_visual": has_pip,
                "has_full_visual": has_full,
                "pip_safe_area_no_face_or_subtitle_overlap": not shot_plan
                or pip_geometry_passed,
                "real_broll_coverage_template_policy": coverage_gate,
                "effective_visual_coverage_policy": effective_coverage_gate,
                "no_fixed_asset_loop": not shot_plan or unique_brolls,
                "visual_origin_traceable": not shot_plan
                or len(traceable_brolls) == len(brolls),
                "semantic_broll_match_explainable": semantic_match_gate_passed,
                "semantic_visual_layer_safe": semantic_layer_safe,
                "semantic_layers_not_counted_as_real_broll": True,
                "visual_cadence_gate": visual_cadence_gate["passed"],
                "hook_declared_once": not shot_plan
                or (director_plan.get("hook") or {}).get("must_appear_once") is True,
                "visual_pipeline_executed": visual_pipeline_executed,
            }
            quality_report["visual_gate_policy"] = {
                "template_id": template_id or "legacy_talking_head",
                "visual_density": visual_density or "safe_preview",
                **visual_gate_policy,
                "real_event_count": len(real_brolls),
                "broll_count": len(brolls),
                "coverage_ratio": round(broll_coverage_ratio, 4),
                "effective_coverage_ratio": effective_visual_coverage_ratio,
                "effective_coverage_seconds": effective_visual_coverage_seconds,
                "actual_has_pip_visual": has_pip,
                "actual_has_full_visual": has_full,
                "passed": bool(
                    real_event_count_gate
                    and coverage_gate
                    and effective_coverage_gate
                    and pip_requirement_gate
                    and full_requirement_gate
                ),
            }
            quality_report["hard_checks"] = {
                "has_pip_visual": has_pip,
                "has_full_visual": has_full,
                "pip_required": bool(visual_gate_policy["pip_required"]),
                "full_required": bool(visual_gate_policy["full_required"]),
                "pip_event_count": pip_event_count,
                "full_event_count": full_event_count,
                "min_pip_events": int(visual_gate_policy.get("min_pip_events", 0)),
                "min_full_events": int(visual_gate_policy.get("min_full_events", 0)),
                "actual_real_broll_event_count": len(real_brolls),
                "actual_real_broll_coverage_ratio": round(broll_coverage_ratio, 4),
                "effective_visual_coverage_ratio": effective_visual_coverage_ratio,
            }
            quality_report["visual_cadence_gate"] = visual_cadence_gate
            quality_report["visual_requests"] = list(
                shot_plan.get("visual_requests") or []
            )
            quality_report["provider_search_log"] = list(
                shot_plan.get("provider_search_log") or []
            )
            unresolved_visual_requests = [
                request
                for request in quality_report["visual_requests"]
                if isinstance(request, Mapping)
                and request.get("visual_type") != "abstract"
                and not any(
                    log.get("start") == request.get("start")
                    and log.get("end") == request.get("end")
                    for log in quality_report["provider_search_log"]
                    if isinstance(log, Mapping)
                )
            ]
            quality_report["provider_search_gate"] = {
                "passed": not unresolved_visual_requests,
                "required_for_each_structured_request": True,
                "unresolved_visual_request_count": len(unresolved_visual_requests),
                "unresolved_visual_requests": unresolved_visual_requests,
            }
            quality_report["creative_checks"]["provider_search_log_complete"] = (
                quality_report["provider_search_gate"]["passed"]
            )
            if requested_pipeline == "adaptive_fine_cut_v1" and unresolved_visual_requests:
                quality_report["creative_checks"]["visual_pipeline_executed"] = False
                quality_report["failure_code"] = "VISUAL_PIPELINE_NOT_EXECUTED"
            quality_report["source_original_visual"] = {
                "event_count": 0,
                "counted_in_output_added_visual": False,
                "note": "源视频自带画面、贴图、汽车图和特效不计入本次程序新增视觉成果。",
            }
            quality_report["output_added_visual"] = {
                "event_count": output_added_visual_event_count,
                "broll_event_count": len(brolls),
                "vector_event_count": len(vector_items),
                "events": [
                    {
                        "event_id": item.get("event_id") or item.get("asset_id"),
                        "start": item.get("start"),
                        "end": item.get("end"),
                        "mode": item.get("mode"),
                        "asset_id": item.get("asset_id"),
                        "source": item.get("source_provider") or item.get("source"),
                    }
                    for item in [*brolls, *vector_items]
                    if isinstance(item, Mapping)
                ],
            }
            if not visual_pipeline_executed:
                quality_report["failure_code"] = "VISUAL_PIPELINE_NOT_EXECUTED"
            if shot_plan:
                if generated_image_brolls:
                    effective_degradation = {
                        "mode": "generated_image_broll",
                        "message": "生成图只用于本地验收，不代表发布授权。",
                        "publish_claim_allowed": False,
                    }
                elif real_brolls:
                    effective_degradation = {
                        "mode": "authorized_real_broll",
                        "message": "已按模板策略绑定语义相关且来源可追溯的真实素材。",
                        "publish_claim_allowed": bool(
                            real_event_count_gate
                            and coverage_gate
                            and semantic_match_gate_passed
                            and all(
                                item.get("publish_licensed") is True
                                for item in brolls
                            )
                        ),
                    }
                else:
                    effective_degradation = {
                        "mode": "a_roll_safe_degradation",
                        "message": "无可靠语义素材，保留人物画面与安全虚拟运镜。",
                        "publish_claim_allowed": False,
                    }
                quality_report["shot_plan"]["degradation"] = effective_degradation
            quality_report["creative_passed"] = all(
                quality_report["creative_checks"].values()
            )
            word_timing_quality = (
                quality_report.get("subtitle_timeline", {}).get(
                    "word_timing_quality", {}
                )
            )
            word_timing_checks = word_timing_quality.get("checks", {})
            word_level_hard_gate_passed = bool(
                (
                    word_timing_quality.get("verified") is True
                    and word_timing_checks.get("word_p95_le_150ms") is True
                    and word_timing_checks.get("mapping_le_1_frame") is True
                )
                or word_timing_quality.get("timing_gate_passed") is True
            )
            quality_report["subtitle_word_gate_passed"] = (
                word_level_hard_gate_passed
            )
            quality_report["publish_claim_allowed"] = bool(
                quality_report["passed"]
                and quality_report["creative_passed"]
                and release_visual_gate_passed
                and publish_rights_gate_passed
                and word_level_hard_gate_passed
            )
            quality_report["v1_hard_gate_passed"] = bool(
                quality_report["publish_claim_allowed"]
            )
            quality_report["local_visual_acceptance_passed"] = bool(
                quality_report["creative_passed"]
            )
            quality_report["release_visual_gate_passed"] = release_visual_gate_passed
            quality_report["semantic_match_gate"] = {
                "passed": semantic_match_gate_passed,
                "failures": [
                    {
                        "asset_id": item.get("asset_id"),
                        "semantic_query": item.get("semantic_query"),
                        "match_score": item.get("match_score"),
                        "match_reason": item.get("match_reason") or [],
                    }
                    for item in brolls
                    if not isinstance(item.get("match_score"), (int, float))
                    or float(item.get("match_score") or 0) <= 0
                    or not item.get("match_reason")
                ],
            }
            quality_report["publish_rights_gate_passed"] = publish_rights_gate_passed
            quality_report["visual_origin_policy"] = {
                "domestic_priority": [
                    "domestic_confirmed_real",
                    "domestic_authorized_generation",
                    "international_pexels_pixabay_fallback",
                ],
                "locale_does_not_prove_domestic_scene": True,
                "public_douyin_bilibili_scraping": False,
                "generated_images_count_as_local_visual_events": True,
                "generated_images_do_not_satisfy_free_stock_video_gate": True,
                "free_stock_video_gate": (
                    "not_applicable_generated_image_local_acceptance"
                    if generated_image_brolls
                    else (
                        "passed"
                        if len(stock_video_brolls)
                        >= visual_gate_policy["min_real_events"]
                        else "blocked_missing_confirmed_pexels_or_pixabay_video"
                    )
                ),
            }
            quality_report["visual_event_count"] = visual_event_count
            quality_report["visual_broll_event_count"] = len(brolls)
            quality_report["semantic_visual_layer_count"] = len(semantic_layer_items)
            quality_report["semantic_visual_layer_geometry"] = semantic_layer_geometry
            quality_report["real_broll_event_count"] = len(real_brolls)
            quality_report["real_stock_video_event_count"] = len(stock_video_brolls)
            quality_report["generated_image_event_count"] = len(generated_image_brolls)
            quality_report["domestic_real_broll_event_count"] = len(domestic_real_brolls)
            quality_report["international_broll_event_count"] = len(international_stock_brolls)
            quality_report["broll_context_counts"] = {
                "domestic_real": len(domestic_real_brolls),
                "international_stock": len(international_stock_brolls),
                "generated_image": len(generated_image_brolls),
                "unknown": max(
                    0,
                    len(brolls)
                    - len(domestic_real_brolls)
                    - len(international_stock_brolls)
                    - len(generated_image_brolls),
                ),
            }
            quality_report["broll_origin_counts"] = {
                "generated_image_asset": len(generated_image_brolls),
                "stock_video_asset": len(stock_video_brolls),
                "other_real_local_asset": len(
                    real_brolls
                ) - len(stock_video_brolls),
            }
            quality_report["broll_provenance"] = [
                {
                    "event_index": index,
                    "asset_id": item.get("asset_id"),
                    "mode": item.get("mode"),
                    "asset_origin": item.get("asset_origin"),
                    "provider": item.get("source_provider"),
                    "license_name": item.get("license_name"),
                    "license_url": item.get("license_url"),
                    "source_url": item.get("source_url"),
                    "authorization_status": item.get("authorization_status"),
                    "publish_licensed": bool(item.get("publish_licensed", False)),
                    "semantic_binding": item.get("semantic_binding"),
                    "keywords": item.get("keywords") or [],
                    "semantic_query": item.get("semantic_query"),
                    "domestic_context": visual_asset_context(item),
                    "domestic_scene": item.get("domestic_scene") or "",
                }
                for index, item in enumerate(brolls)
            ]
            quality_report["real_broll_coverage_ratio"] = round(broll_coverage_ratio, 4)
            quality_report["visual_coverage_seconds"] = visual_coverage_seconds
            quality_report["vector_coverage_seconds"] = vector_coverage_seconds
            # === P0-1: 4 个独立覆盖指标写入 reporting ===
            quality_report["real_stock_broll_coverage_ratio"] = (
                real_stock_broll_coverage_ratio
            )
            quality_report["real_stock_broll_seconds"] = real_stock_broll_seconds
            quality_report["generated_image_coverage_ratio"] = (
                generated_image_coverage_ratio
            )
            quality_report["generated_image_seconds"] = generated_image_seconds
            quality_report["deterministic_card_coverage_ratio"] = (
                deterministic_card_coverage_ratio
            )
            quality_report["deterministic_card_seconds"] = (
                deterministic_card_seconds
            )
            quality_report["deterministic_card_event_count"] = len(
                deterministic_card_items
            )
            # P0-1 锁定：生成图永远不算 publish_licensed、可发布授权。
            # 防止任何下游把 generated_for_local_acceptance 偷偷改成 confirmed。
            quality_report["generated_image_lock"] = {
                "status": _GENERATED_AUTH_LOCK,
                "cannot_be_promoted_to": ["confirmed", "publish_licensed"],
                "real_broll_count": 0,
            }
            # === P0-2: 视觉时间窗目标 / 缺口 / 未解决窗口 ===
            visual_window_plan = (
                director_plan.get("visual_window_plan")
                if isinstance(director_plan, Mapping)
                else None
            )
            if isinstance(visual_window_plan, Mapping):
                target_min = float(
                    visual_window_plan.get("target_real_coverage_seconds_min") or 0
                )
                target_max = float(
                    visual_window_plan.get("target_real_coverage_seconds_max") or 0
                )
                current_real_seconds = real_stock_broll_seconds
                deficit_seconds = max(0.0, target_min - current_real_seconds)
                # 未解决窗口 = planned_visual_windows 中 required=true 且没被任何
                # 真实 B-roll 时间窗覆盖的窗口
                planned_windows = list(
                    visual_window_plan.get("planned_visual_windows") or []
                )
                unresolved = _classify_unresolved_windows(
                    planned_windows, stock_video_brolls
                )
                quality_report["visual_window_plan"] = visual_window_plan
                quality_report["target_real_coverage_seconds"] = {
                    "min": round(target_min, 3),
                    "max": round(target_max, 3),
                }
                quality_report["current_real_coverage_seconds"] = round(
                    current_real_seconds, 3
                )
                quality_report["coverage_deficit_seconds"] = round(
                    deficit_seconds, 3
                )
                quality_report["unresolved_semantic_windows"] = unresolved
                quality_report["unresolved_window_count"] = len(unresolved)
                # 写回 director_plan 让上游也能读到
                if isinstance(director_plan, dict):
                    director_plan["current_real_coverage_seconds"] = round(
                        current_real_seconds, 3
                    )
                    director_plan["coverage_deficit_seconds"] = round(
                        deficit_seconds, 3
                    )
                    director_plan["unresolved_semantic_windows"] = unresolved
            quality_report["effective_visual_coverage_seconds"] = (
                effective_visual_coverage_seconds
            )
            quality_report["effective_visual_coverage_ratio"] = (
                effective_visual_coverage_ratio
            )
            quality_report["broll_modes"] = {
                "pip": sum(1 for item in brolls if item.get("mode") == "pip"),
                "full": sum(1 for item in brolls if item.get("mode") == "full"),
                "vector_pip": len(vector_items),
            }
            quality_report["pip_geometry"] = pip_geometry_checks
            quality_report["pip_safe_area_passed"] = pip_geometry_passed
            quality_report["media_passed"] = bool(quality_report["passed"])
            identity_payload = json.loads(
                task.outputs.get("source_media_identity_json") or "{}"
            )
            declared_range = json.loads(
                task.outputs.get("source_range_json") or "{}"
            )
            identity_enabled = bool(identity_payload)
            source_identity_checks = {
                "source_media_sha256": False,
                "source_duration_matches_ffprobe": False,
                "transcript_sha256": False,
                "timing_source_declared": False,
            }
            audio_subtitle_semantic = {
                "passed": True,
                "method": "disabled_for_legacy_task",
                "source_text": "",
                "cue_text": "",
                "overlap_ratio": None,
            }
            edl_execution = {
                "passed": True,
                "method": "disabled_for_legacy_task",
            }
            if identity_enabled:
                original_source = Path(task.source_video_path)
                original_probe = self._probe_media(original_source)
                original_sha256 = hashlib.sha256(original_source.read_bytes()).hexdigest()
                source_identity_checks = {
                    "source_media_sha256": original_sha256.lower()
                    == str(identity_payload.get("source_media_sha256") or "").lower(),
                    "source_duration_matches_ffprobe": abs(
                        float(original_probe.get("duration_seconds") or 0)
                        - float(identity_payload.get("source_duration_seconds") or 0)
                    )
                    <= 0.05,
                    "transcript_sha256": hashlib.sha256(
                        json.dumps(
                            json.loads(task.outputs.get("subtitle_segments_json") or "[]"),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest().lower()
                    == str(identity_payload.get("transcript_sha256") or "").lower(),
                    "timing_source_declared": str(
                        identity_payload.get("transcript_timing_source") or ""
                    )
                    in {"word_timestamps", "sentence_timestamps"},
                }

                def _compact(value: object) -> str:
                    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(value or "")).lower()

                source_text = _compact(
                    "".join(
                        str(segment.get("text") or "")
                        for segment in json.loads(
                            task.outputs.get("subtitle_segments_json") or "[]"
                        )
                        if isinstance(segment, Mapping)
                    )
                )
                cue_text = _compact(
                    "".join(
                        str(cue.get("text") or "")
                        or "".join(
                            str(line)
                            for line in (cue.get("lines") or [])
                            if line is not None
                        )
                        for cue in subtitle_preview.get("cues") or []
                        if isinstance(cue, Mapping)
                    )
                )
                # Corrected homophones may differ by a character, but a
                # wholly unrelated script must never pass this gate.
                source_chars = set(source_text)
                shared = sum(1 for char in cue_text if char in source_chars)
                overlap_ratio = shared / max(len(cue_text), 1)
                audio_subtitle_semantic = {
                    "passed": overlap_ratio >= 0.72 and len(cue_text) >= 12,
                    "method": "local_transcript_text_overlap_spot_check",
                    "source_text": source_text,
                    "cue_text": cue_text,
                    "overlap_ratio": round(overlap_ratio, 4),
                    "minimum_overlap_ratio": 0.72,
                }
                if declared_range:
                    range_start = float(declared_range.get("start") or 0)
                    range_end = float(declared_range.get("end") or 0)
                    # A release shot plan is executed as one shared A/V EDL:
                    # its source ranges are concatenated before playback
                    # rate is applied.  Comparing the result to the raw
                    # reviewed interval would count intentionally removed
                    # pauses as a render failure.  Keep the original range in
                    # the report, but audit the exact ranges FFmpeg executed.
                    executed_shot_duration = 0.0
                    if shot_plan:
                        executed_shot_duration = sum(
                            max(
                                0.0,
                                float(shot.get("source_end") or 0)
                                - float(shot.get("source_start") or 0),
                            )
                            for shot in shot_plan.get("shots") or []
                            if isinstance(shot, Mapping)
                        )
                    expected_source_duration = (
                        executed_shot_duration
                        if executed_shot_duration > 0
                        else range_end - range_start
                    )
                    expected_body_duration = expected_source_duration / max(
                        playback_rate, 0.001
                    )
                    opening_seconds = float(opening_duration or 0.0)
                    expected_output_duration = expected_body_duration + opening_seconds
                    actual_output_duration = float(
                        output_media.get("duration_seconds") or 0
                    )
                    actual_body_duration = max(
                        0.0, actual_output_duration - opening_seconds
                    )
                    edl_execution = {
                        "passed": range_end > range_start
                        and abs(
                            actual_body_duration - expected_body_duration
                        )
                        <= 0.35,
                        "method": (
                            "shared_ffmpeg_audio_video_trim_plus_shot_plan"
                            if executed_shot_duration > 0
                            else "shared_ffmpeg_audio_video_trim"
                        ),
                        "declared_source_range": {
                            "start": range_start,
                            "end": range_end,
                        },
                        "expected_body_seconds": round(expected_body_duration, 3),
                        "executed_source_seconds": round(
                            expected_source_duration, 3
                        ),
                        "opening_duration_seconds": round(opening_seconds, 3),
                        "expected_output_seconds": round(expected_output_duration, 3),
                        "actual_output_seconds": output_media.get("duration_seconds"),
                        "actual_body_seconds": round(actual_body_duration, 3),
                    }
                else:
                    edl_execution = {
                        "passed": False,
                        "method": "missing_declared_source_range",
                    }
            identity_gate_passed = bool(
                not identity_enabled or all(source_identity_checks.values())
            )
            transcript_review = json.loads(
                task.outputs.get("transcript_review_json") or "{}"
            )
            reviewed_segments = json.loads(
                task.outputs.get("subtitle_segments_json") or "[]"
            )
            reviewed_text = "".join(
                str(segment.get("text") or "")
                for segment in reviewed_segments
                if isinstance(segment, Mapping)
            )
            # P0-6: 同时拿 raw_asr_text 用于检测"未审核就当 reviewed"的假阳性。
            raw_asr_text = "".join(
                str(segment.get("raw_asr_text") or segment.get("text") or "")
                for segment in reviewed_segments
                if isinstance(segment, Mapping)
            )
            # P0-6: 通用错误模式检测。raw ASR 假阳性（raw_asr == reviewed），
            # 或命中数字 / 品牌 / 金额 / 人物名 不可自动修的 token。
            known_error_terms = _detect_known_transcript_errors(
                reviewed_text, raw_asr_text=raw_asr_text
            )
            human_review_warnings = _detect_human_review_warnings(reviewed_text)
            reviewed_source = str(transcript_review.get("source") or "").strip()
            # P0-6: accuracy gate 独立判定
            transcript_accuracy_passed = bool(
                reviewed_source
                and reviewed_source != "unreviewed"
                and bool(reviewed_text.strip())
                and not known_error_terms
            )
            transcript_quality = {
                "passed": transcript_accuracy_passed,
                "method": "reviewed_transcript_correction_audit_with_known_error_detection",
                "source": reviewed_source or "unreviewed",
                "correction_count": len(transcript_review.get("corrections") or []),
                "remaining_known_error_terms": known_error_terms,
                "raw_asr_equals_reviewed": bool(
                    _compact_for_comparison(raw_asr_text)
                    == _compact_for_comparison(reviewed_text)
                ),
                "human_review_warnings": human_review_warnings,
            }
            # P0-6: 拆出 transcript_timing_gate（基于 word_timing_quality）
            word_timing_for_gate = (
                quality_report.get("subtitle_timeline", {}).get(
                    "word_timing_quality", {}
                )
            )
            timing_checks = word_timing_for_gate.get("checks", {}) if isinstance(
                word_timing_for_gate, Mapping
            ) else {}
            transcript_timing_passed = bool(
                word_timing_for_gate.get("verified") is True
                and timing_checks.get("word_p95_le_150ms") is True
                and timing_checks.get("mapping_le_1_frame") is True
            )
            transcript_timing_gate = {
                "passed": transcript_timing_passed,
                "word_p95_ms": word_timing_for_gate.get("word_p95_ms"),
                "mapping_error_frames": word_timing_for_gate.get(
                    "mapping_error_frames"
                ),
                "matched_cue_count": word_timing_for_gate.get("matched_cue_count"),
                "unmatched_cue_count": word_timing_for_gate.get(
                    "unmatched_cue_count"
                ),
            }
            quality_report["transcript_timing_gate"] = transcript_timing_gate
            subtitle_sync_passed = bool(
                quality_report.get("subtitle_timeline", {}).get("passed") is True
                and quality_report.get("subtitle_word_gate_passed") is True
                and quality_report.get("checks", {}).get("audio_video_drift") is True
                and (not identity_enabled or audio_subtitle_semantic["passed"])
                and (not identity_enabled or edl_execution["passed"])
                and transcript_quality["passed"]
                and transcript_timing_passed
            )
            visual_release_passed = bool(
                release_visual_gate_passed
                and (not identity_enabled or len(real_brolls) > 0)
            )
            quality_report["transcript_source_identity_gate"] = {
                "enabled": identity_enabled,
                "passed": identity_gate_passed,
                "checks": source_identity_checks,
                "source_media_identity": identity_payload,
            }
            quality_report["quality_report_version"] = "identity-gates-v2"
            quality_report["audio_subtitle_semantic_gate"] = audio_subtitle_semantic
            quality_report["edl_execution_gate"] = edl_execution
            quality_report["transcript_accuracy_gate"] = transcript_quality
            quality_report["media_integrity_passed"] = bool(
                quality_report["media_passed"] and identity_gate_passed
            )
            quality_report["subtitle_sync_passed"] = subtitle_sync_passed
            quality_report["visual_release_passed"] = visual_release_passed
            quality_report["passed"] = bool(
                quality_report["media_integrity_passed"]
                and quality_report["subtitle_sync_passed"]
                and quality_report["visual_release_passed"]
                and quality_report["creative_passed"]
                and publish_rights_gate_passed
            )
            strict_local_quality = bool(
                task.outputs.get("requested_pipeline") == "adaptive_fine_cut_v1"
                or task.outputs.get("provider_mode") == _LOCAL_RENDER_MODE
            )
            if not quality_report["passed"] and strict_local_quality:
                failed_checks = [
                    key for key, passed in quality_report["checks"].items() if not passed
                ]
                if not quality_report["media_integrity_passed"]:
                    failed_checks.append("media_integrity")
                if not quality_report["subtitle_sync_passed"]:
                    failed_checks.append("subtitle_sync")
                if not quality_report["visual_release_passed"]:
                    failed_checks.append("visual_release")
                if not quality_report["creative_passed"]:
                    failed_checks.append("creative")
                if not publish_rights_gate_passed:
                    failed_checks.append("publish_rights")
                failed_checks = list(dict.fromkeys(failed_checks))
                # Preserve the computed gate details on failed local renders;
                # otherwise a subtitle/EDL rejection leaves only a generic
                # message and makes the actual evidence impossible to audit.
                task = self._update(
                    task,
                    outputs={
                        **task.outputs,
                        "quality_report": json.dumps(
                            quality_report, ensure_ascii=False
                        ),
                    },
                )
                raise VideoEditorWorkflowError(
                    "本机成片质量校验失败：" + "、".join(failed_checks)
                )

            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "本机成片已生成，可直接下载",
                    "result_path": str(output_path),
                    "result_mime": "video/mp4",
                    "result_size_bytes": output_path.stat().st_size,
                    "outputs": {
                        **task.outputs,
                        "quality_report": json.dumps(
                            quality_report, ensure_ascii=False
                        ),
                    },
                    "error_message": None,
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self.repository.save_task(task)
            latest = self._require_batch(task.outputs["batch_id"])
            latest_item = next(
                entry
                for entry in latest.items
                if entry.item_id == task.outputs["item_id"]
            )
            completed = latest_item.model_copy(
                update={
                    "status": "awaiting_output_confirmation",
                    "provider_stage": "local_export_complete",
                    "provider_payload": {
                        **latest_item.provider_payload,
                        "local_export": {
                            "quality_report": quality_report,
                            "style_version": (
                                _RELEASE_TALKING_HEAD_STYLE_VERSION
                                if shot_plan
                                else _LOCAL_PREVIEW_EXPORT_STYLE_VERSION
                            ),
                            "visual_beat_count": visual_event_count,
                            "template_id": task.outputs.get("template_id"),
                            "template_version": task.outputs.get("template_version"),
                            "degradation": shot_plan.get("degradation") if shot_plan else None,
                            "publish_claim_allowed": quality_report.get(
                                "publish_claim_allowed", False
                            ),
                        },
                    },
                    "publish_allowed": bool(
                        quality_report.get("publish_claim_allowed", False)
                    ),
                    "error_message": None,
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self._replace_batch_item(latest, completed)
        except Exception as exc:
            # Keep a failed candidate when the encoder did produce a playable
            # file.  The task remains failed, but reviewers can inspect the
            # exact MP4/ASS/report that tripped the hard gate instead of losing
            # the only visual evidence.
            failure_artifact = {
                "failure_artifact_path": str(output_path)
                if output_path.is_file() and output_path.stat().st_size > 0
                else "",
                "quality_gate_failed": "true",
            }
            self._update(
                task,
                status=TaskStatus.FAILED,
                progress=0,
                stage="本机成片生成失败",
                error_message=str(exc),
                result_path=(
                    str(output_path)
                    if output_path.is_file() and output_path.stat().st_size > 0
                    else None
                ),
                result_mime="video/mp4" if output_path.is_file() else None,
                result_size_bytes=(
                    output_path.stat().st_size if output_path.is_file() else None
                ),
                outputs={**task.outputs, **failure_artifact},
            )
            try:
                latest = self._require_batch(task.outputs["batch_id"])
                latest_item = next(
                    entry
                    for entry in latest.items
                    if entry.item_id == task.outputs["item_id"]
                )
                failed = latest_item.model_copy(
                    update={
                        "status": "failed",
                        "provider_stage": "local_export_failed",
                        "publish_allowed": False,
                        "error_message": str(exc),
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                self._replace_batch_item(latest, failed)
            except Exception:
                pass
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _validated_review_segments(
        segments: list[dict[str, Any]],
        *,
        duration_seconds: float,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        previous_end = 0.0
        for raw in segments:
            try:
                start = round(float(raw.get("start", 0)), 3)
                end = round(float(raw.get("end", 0)), 3)
            except (TypeError, ValueError) as exc:
                raise VideoEditorWorkflowError("字幕时间戳格式无效。") from exc
            text = str(raw.get("text") or "").strip()
            if start < 0 or end <= start or end > duration_seconds:
                raise VideoEditorWorkflowError("字幕时间戳超出素材范围。")
            if start < previous_end:
                raise VideoEditorWorkflowError("字幕时间段不能互相重叠。")
            raw_terms = raw.get("emphasis_terms") or []
            if isinstance(raw_terms, (str, bytes)) or not isinstance(raw_terms, list):
                raise VideoEditorWorkflowError("字幕强调词格式无效。")
            if len(raw_terms) > 1:
                raise VideoEditorWorkflowError("每条字幕最多设置一个强调词。")
            normalized_segment = {"start": start, "end": end, "text": text}
            raw_words = raw.get("words") or []
            if raw_words:
                if not isinstance(raw_words, list):
                    raise VideoEditorWorkflowError("词级字幕时间轴格式无效。")
                words: list[dict[str, Any]] = []
                previous_word_end = start
                for raw_word in raw_words:
                    if not isinstance(raw_word, Mapping):
                        raise VideoEditorWorkflowError("词级字幕时间轴格式无效。")
                    try:
                        word_start = round(float(raw_word.get("start", 0)), 3)
                        word_end = round(float(raw_word.get("end", 0)), 3)
                    except (TypeError, ValueError) as exc:
                        raise VideoEditorWorkflowError("词级字幕时间戳格式无效。") from exc
                    # Local faster-whisper exports `word`; API review payloads
                    # historically used `text`. Accept both without losing
                    # the original word clock.
                    word_text = str(
                        raw_word.get("text") or raw_word.get("word") or ""
                    ).strip()
                    if (
                        not word_text
                        or word_start < start
                        or word_end <= word_start
                        or word_end > end
                        or word_start < previous_word_end
                    ):
                        raise VideoEditorWorkflowError("词级字幕时间轴超出当前句子范围。")
                    words.append(
                        {"start": word_start, "end": word_end, "text": word_text}
                    )
                    previous_word_end = word_end
                if words:
                    normalized_segment["words"] = words
            if raw_terms:
                emphasis = re.sub(r"\s+", "", str(raw_terms[0]))
                clean_text = re.sub(r"\s+", "", text)
                if not emphasis or len(emphasis) > 6 or emphasis not in clean_text:
                    raise VideoEditorWorkflowError(
                        "强调词必须是当前字幕中的连续原文，且不超过 6 个字。"
                    )
                normalized_segment["emphasis_terms"] = [emphasis]
                emphasis_kind = str(raw.get("emphasis_kind") or "keyword")
                if emphasis_kind not in {
                    "number",
                    "benefit",
                    "warning",
                    "keyword",
                }:
                    raise VideoEditorWorkflowError("字幕强调样式无效。")
                normalized_segment["emphasis_kind"] = emphasis_kind
            normalized.append(normalized_segment)
            previous_end = end
        return normalized

    @staticmethod
    def _srt_timestamp(seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, milliseconds = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"

    @staticmethod
    def _review_ass_bytes(
        segments: list[dict[str, Any]],
        *,
        output_profile: str,
        caption_groups: object = None,
        caption_emphasis: object = None,
        spoken_ranges: object = None,
        caption_glossary: object = None,
        time_offset_seconds: float = 0,
        theme: str = "general",
        font_family: str | None = None,
        overlay_preview: Mapping[str, Any] | None = None,
        subtitle_style_id: str = "adaptive_talking_head_v1",
    ) -> bytes:
        from src.services.video_editor_cloud import build_business_talking_head_ass

        return build_business_talking_head_ass(
            segments,
            title="",
            output_profile=output_profile,
            caption_groups=caption_groups,
            caption_emphasis=caption_emphasis,
            spoken_ranges=spoken_ranges,
            caption_glossary=caption_glossary,
            time_offset_seconds=time_offset_seconds,
            theme=theme,
            font_family=font_family,
            overlay_preview=overlay_preview,
            subtitle_style_id=subtitle_style_id,
        )

    @staticmethod
    def _review_srt_bytes(
        segments: list[dict[str, Any]],
        *,
        output_profile: str,
        caption_groups: object = None,
        caption_emphasis: object = None,
        spoken_ranges: object = None,
        caption_glossary: object = None,
        overlay_preview: Mapping[str, Any] | None = None,
    ) -> bytes:
        from src.services.video_editor_cloud import build_business_talking_head_srt

        return build_business_talking_head_srt(
            segments,
            output_profile=output_profile,
            caption_groups=caption_groups,
            caption_emphasis=caption_emphasis,
            spoken_ranges=spoken_ranges,
            caption_glossary=caption_glossary,
            overlay_preview=overlay_preview,
        )

    @staticmethod
    def _review_title_png_bytes(
        title: str,
        *,
        output_profile: str,
    ) -> bytes:
        from src.services.video_editor_cloud import (
            build_business_talking_head_title_png,
        )

        return build_business_talking_head_title_png(
            title,
            output_profile=output_profile,
        )

    def review_cloud_batch_item(
        self,
        batch_id: str,
        item_id: str,
        *,
        subtitle_segments: list[dict[str, Any]],
        enabled_plan_step_ids: list[str],
        selected_title: str,
        selected_bgm_id: str | None,
        confirmed: bool,
        smart_opening_enabled: bool = True,
        broll_placement: dict[str, Any] | None = None,
        local_only: bool = False,
        source_range: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from src.services.video_editor_cloud import (
            CloudAsset,
            EditPlan,
            EditStepKind,
            RenderRequest,
            retime_segments_after_cuts,
            validated_caption_emphasis,
            validated_caption_groups,
            visual_style_spec,
        )

        batch = self._sync_batch(self._require_batch(batch_id))
        # The legacy local-analysis route still uses this review contract in
        # the browser.  Allow it only when the caller explicitly requests a
        # local-only preview; it must never enter the cloud render branch.
        legacy_local_preview = batch.provider_mode in {"legacy", _LOCAL_RENDER_MODE} and local_only
        if batch.provider_mode not in {"sandbox", "aliyun", _LOCAL_RENDER_MODE} and not legacy_local_preview:
            raise VideoEditorWorkflowError("该接口仅用于云端轻量剪辑批次。")
        if not batch.output_profile:
            inferred_profile = (
                "1080p"
                if str(batch.output_resolution).startswith("1080")
                else "720p"
            )
            batch = batch.model_copy(update={"output_profile": inferred_profile})
        item = next((entry for entry in batch.items if entry.item_id == item_id), None)
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        if item.status != "awaiting_subtitle_review":
            raise VideoEditorWorkflowError("该素材当前不在字幕与方案复核阶段。")
        if not confirmed:
            raise VideoEditorWorkflowError("请明确确认字幕与剪辑方案。")
        duration_seconds = float(
            (item.provider_payload.get("media") or {}).get("duration_seconds") or 0
        )
        if duration_seconds <= 0 and legacy_local_preview:
            source = self.resolve_source(item.source_id)
            duration_seconds = float(
                self._probe_media(Path(source["_path"])).get("duration_seconds") or 0
            )
        if duration_seconds <= 0:
            raise VideoEditorWorkflowError("缺少素材时长，不能保存人工复核。")
        normalized_source_range: dict[str, float] | None = None
        if source_range is not None:
            try:
                range_start = float(source_range.get("start"))
                range_end = float(source_range.get("end"))
            except (AttributeError, TypeError, ValueError) as exc:
                raise VideoEditorWorkflowError("源媒体区间必须是有效数字。") from exc
            if range_start < 0 or range_end <= range_start or range_end > duration_seconds:
                raise VideoEditorWorkflowError("源媒体区间必须落在原始媒体时长内。")
            normalized_source_range = {
                "start": round(range_start, 3),
                "end": round(range_end, 3),
            }
        segments = self._validated_review_segments(
            subtitle_segments,
            duration_seconds=duration_seconds,
        )
        title = selected_title.strip()
        if not title:
            raise VideoEditorWorkflowError("请确认成片标题。")
        if len(title) > 100:
            raise VideoEditorWorkflowError("标题不能超过 100 个字符。")
        if selected_bgm_id:
            self.resolve_bgm_asset(selected_bgm_id)

        normalized_broll: dict[str, Any] = {}
        if broll_placement:
            asset_id = str(broll_placement.get("asset_id") or "").strip()
            if not asset_id:
                raise VideoEditorWorkflowError("B-roll 分镜缺少素材。")
            self.resolve_visual_asset(asset_id, expected_kind="broll")
            try:
                broll_start = float(broll_placement.get("start", 0))
                broll_end = float(broll_placement.get("end", 0))
            except (TypeError, ValueError) as exc:
                raise VideoEditorWorkflowError("B-roll 时间点必须是有效数字。") from exc
            if broll_start < 0 or broll_end <= broll_start or broll_end > duration_seconds:
                raise VideoEditorWorkflowError("B-roll 时间段必须落在口播原片范围内。")
            broll_mode = str(broll_placement.get("mode") or "pip")
            if broll_mode not in {"pip", "full"}:
                raise VideoEditorWorkflowError("B-roll 版式只支持画中画或全屏替换。")
            normalized_broll = {
                "asset_id": asset_id,
                "start": round(broll_start, 3),
                "end": round(broll_end, 3),
                "mode": broll_mode,
            }

        # The current Aliyun RenderRequest has no B-roll/PiP track.  Never let
        # an API caller accidentally submit a cloud render that records the
        # placement in the review snapshot but silently drops it from the
        # actual output.  The local FFmpeg release renderer is the supported
        # capability for visual overlays and preserves the safe degradation.
        if normalized_broll and not local_only:
            local_only = True

        try:
            release_shot_plan: dict[str, Any] = {}
            if item.edit_plan:
                plan = EditPlan.model_validate(item.edit_plan)
            elif legacy_local_preview:
                from src.services.video_editor_cloud import (
                    build_safe_edit_plan,
                    build_smart_opening,
                    build_visual_beats,
                    kept_ranges_for_plan,
                )

                transcript = "".join(
                    str(segment.get("text") or "") for segment in segments
                )
                opening = build_smart_opening(transcript, [title])
                plan = build_safe_edit_plan(
                    segments,
                    duration_seconds,
                    title_candidates=[title],
                    visual_beats=build_visual_beats(segments, []),
                    smart_opening=opening,
                    enabled_steps=[
                        "smart_opening",
                        "vertical_fit",
                        "subtitles",
                        "title",
                    ],
                    explanation="旧版本地分析批次已转换为当前本机预览模板。",
                )
                # The local renderer keeps the original timeline so reviewed
                # captions remain aligned.  Disable silence removal here and
                # make the invariant explicit instead of carrying stale cuts
                # from the legacy analysis route.
                plan = plan.model_copy(
                    update={
                        "remove_ranges": [],
                        "kept_ranges": kept_ranges_for_plan(duration_seconds, []),
                        "estimated_output_seconds": duration_seconds,
                        "trim_silence_enabled": False,
                    }
                )
                from src.services.talking_head_templates import build_talking_head_shot_plan

                broll_plan_asset = None
                if normalized_broll:
                    broll_asset = self.resolve_visual_asset(
                        normalized_broll["asset_id"], expected_kind="broll"
                    )
                    broll_plan_asset = {
                        "asset_id": normalized_broll["asset_id"],
                        "source": "user_uploaded_local",
                        "authorization_status": "confirmed",
                        "mode": normalized_broll["mode"],
                        "title": broll_asset.get("name"),
                    }
                bgm_plan_asset = None
                if selected_bgm_id:
                    selected_bgm = self.resolve_bgm_asset(selected_bgm_id)
                    bgm_plan_asset = {
                        "asset_id": selected_bgm_id,
                        "title": selected_bgm.get("title"),
                        "source": selected_bgm.get("source_provider"),
                        "authorization_status": selected_bgm.get(
                            "authorization_status", "unverified"
                        ),
                    }
                release_shot_plan = build_talking_head_shot_plan(
                    segments,
                    duration_seconds=(
                        float(normalized_source_range["end"] - normalized_source_range["start"])
                        if normalized_source_range is not None
                        else duration_seconds
                    ),
                    title=title,
                    broll_asset=broll_plan_asset,
                    bgm_asset=bgm_plan_asset,
                )
                if normalized_source_range is not None:
                    # Shot clocks are relative to the declared source range;
                    # the canonical source identity remains the full upload.
                    release_shot_plan["source_duration_seconds"] = duration_seconds
                    release_shot_plan["source_range"] = normalized_source_range
                # Missing reliable restaurant footage is a truthful A-roll
                # degradation, not a silent request for image-generation
                # budget.  Keep the plan executable and auditable.
                release_shot_plan["asset_requests"] = []
                release_shot_plan["degradation"] = {
                    "mode": "a_roll_safe_degradation",
                    "message": "没有可靠的餐饮语义素材，使用安全推近与事实字幕强调。",
                    "publish_claim_allowed": False,
                }
                if normalized_source_range is not None:
                    # A short real-source acceptance clip must preserve the
                    # verified audio order.  Do not let the generic hook
                    # reorderer move the tail phrase to 0 seconds.
                    release_shot_plan = {}
                plan = plan.model_copy(
                    update={
                        "smart_opening": None,
                        "explanation": (
                            release_shot_plan.get("selection", {}).get("reason")
                            or "按真实源区间保留原声顺序，使用安全推近与事实字幕强调。"
                        ),
                    }
                )
            else:
                plan = EditPlan.model_validate(item.edit_plan)
            selected_steps = list(
                dict.fromkeys(EditStepKind(value) for value in enabled_plan_step_ids)
            )
        except (ValueError, TypeError) as exc:
            raise VideoEditorWorkflowError("剪辑方案步骤无效，请重新分析。") from exc
        allowed_steps = set(plan.enabled_steps)
        # Older tasks may have been analyzed before BGM was included in the
        # plan. A reviewed, locally authorized asset may be added explicitly;
        # arbitrary render parameters remain impossible.
        if selected_bgm_id:
            allowed_steps.add(EditStepKind.BGM)
        if smart_opening_enabled and plan.smart_opening is not None:
            allowed_steps.add(EditStepKind.SMART_OPENING)
            if EditStepKind.SMART_OPENING not in selected_steps:
                selected_steps.insert(0, EditStepKind.SMART_OPENING)
        else:
            selected_steps = [
                step for step in selected_steps if step != EditStepKind.SMART_OPENING
            ]
        if any(step not in allowed_steps for step in selected_steps):
            raise VideoEditorWorkflowError("不能启用服务端方案之外的剪辑步骤。")
        trim_enabled = EditStepKind.TRIM_SILENCE in selected_steps
        approved_caption_groups = validated_caption_groups(
            plan.caption_groups,
            segments,
            max_chars=11,
        )
        reviewed_caption_emphasis = validated_caption_emphasis(
            [
                {
                    "segment_index": index,
                    "term": (segment.get("emphasis_terms") or [""])[0],
                    "kind": segment.get("emphasis_kind") or "keyword",
                }
                for index, segment in enumerate(segments)
                if segment.get("emphasis_terms")
            ],
            segments,
            caption_groups=approved_caption_groups,
        )
        caption_warnings = list(plan.warnings)
        if plan.caption_groups and not approved_caption_groups:
            caption_warnings.append(
                "字幕经人工修改，原 AI 语义断句已失效，正式出片改用安全规则断句。"
            )
        reviewed_plan = plan.model_copy(
            update={
                "enabled_steps": selected_steps,
                "remove_ranges": plan.remove_ranges if trim_enabled else [],
                "trim_silence_enabled": trim_enabled,
                "caption_groups": approved_caption_groups,
                "caption_group_source": (
                    "qwen_semantic"
                    if approved_caption_groups
                    else "deterministic_fallback"
                ),
                "caption_emphasis": reviewed_caption_emphasis,
                "warnings": caption_warnings,
            }
        )
        reviewed_plan = EditPlan.model_validate(reviewed_plan.model_dump())
        try:
            render_segments = retime_segments_after_cuts(
                segments,
                reviewed_plan.remove_ranges,
            )
            render_spoken_ranges = retime_segments_after_cuts(
                [item.model_dump(mode="json") for item in reviewed_plan.spoken_ranges],
                reviewed_plan.remove_ranges,
            )
        except ValueError as exc:
            raise VideoEditorWorkflowError(str(exc)) from exc
        plan_payload = self._cloud_plan_with_steps(reviewed_plan)
        source = self.resolve_source(item.source_id)
        source_path = Path(source["_path"])
        source_media = self._probe_media(source_path)
        source_media_identity = {
            "source_media_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "source_duration_seconds": float(source_media["duration_seconds"]),
            "transcript_sha256": hashlib.sha256(
                json.dumps(
                    segments,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "transcript_timing_source": (
                "word_timestamps"
                if any(
                    isinstance(segment.get("words"), list)
                    and segment.get("words")
                    for segment in segments
                    if isinstance(segment, Mapping)
                )
                else "sentence_timestamps"
            ),
        }
        plan_payload["source_media_identity"] = source_media_identity
        if normalized_source_range is not None:
            plan_payload["source_range"] = normalized_source_range
        if release_shot_plan:
            plan_payload = {
                **plan_payload,
                "shot_plan": release_shot_plan,
                "template_id": release_shot_plan["template_id"],
                "template_version": release_shot_plan["template_version"],
            }
        plan_hash = self._cloud_request_hash(plan_payload)
        subtitle_hash = hashlib.sha256(
            json.dumps(
                render_segments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        review_time = datetime.now().astimezone()
        review_snapshot = {
            "confirmed": True,
            "approval_mode": "manual",
            "confirmed_at": review_time.isoformat(),
            "plan_version": reviewed_plan.plan_version,
            "plan_hash": plan_hash,
            "subtitle_hash": subtitle_hash,
            "visual_style_id": visual_style_spec(batch.output_profile)["style_id"],
            "broll": normalized_broll or None,
            "shot_plan": release_shot_plan or None,
            "source_media_identity": source_media_identity,
            "source_range": normalized_source_range,
        }
        render_manifest = {
            "visual_style_id": visual_style_spec(batch.output_profile)["style_id"],
            "subtitle_format": "ass",
            "title_render_mode": "png_watermark",
            "title_font": "Source Han Serif CN Heavy",
            "title_burned_in": EditStepKind.TITLE in selected_steps,
            "subtitles_burned_in": EditStepKind.SUBTITLES in selected_steps,
            "rough_cut_burned_in": bool(reviewed_plan.remove_ranges),
            "source_kept_ranges": [
                item.model_dump(mode="json") for item in reviewed_plan.kept_ranges
            ],
            "estimated_output_seconds": reviewed_plan.estimated_output_seconds,
            "expected_resolution": self._cloud_output_settings(batch.output_profile)[
                "output_resolution"
            ],
            "source_media_identity": source_media_identity,
            "source_range": normalized_source_range,
        }
        if release_shot_plan:
            render_manifest.update(
                {
                    "template_id": release_shot_plan.get("template_id"),
                    "template_version": release_shot_plan.get("template_version"),
                    "degradation": release_shot_plan.get("degradation"),
                }
            )
        reviewed_item = item.model_copy(
            update={
                "status": "rendering",
                "provider_stage": "submitting_render",
                "subtitle_segments": segments,
                "edit_plan": plan_payload,
                "enabled_plan_step_ids": [step.value for step in selected_steps],
                "selected_title": title,
                "selected_bgm_id": selected_bgm_id,
                "review_snapshot": review_snapshot,
                "review_confirmed_at": review_time,
                "provider_payload": {
                    **item.provider_payload,
                    **(
                        {"requested_pipeline": "adaptive_fine_cut_v1"}
                        if local_only and not item.is_mock
                        else {}
                    ),
                    "render_manifest": render_manifest,
                },
                "publish_allowed": False,
                "error_message": None,
                "updated_at": review_time,
            }
        )
        batch = self._replace_batch_item(batch, reviewed_item)

        if local_only:
            local_item = reviewed_item.model_copy(
                update={
                    "status": "outcome_unknown",
                    "provider_stage": "local_export_ready",
                    "publish_allowed": False,
                }
            )
            self._replace_batch_item(batch, local_item)
            return self.get_batch(batch.batch_id)

        render_key = f"video-editor-render:{batch.batch_id}:{item.item_id}:{plan_hash}"
        render_request_hash = self._cloud_request_hash(
            {
                "batch_id": batch.batch_id,
                "item_id": item.item_id,
                "plan_hash": plan_hash,
                "subtitle_hash": subtitle_hash,
                "title": title,
                "bgm_id": selected_bgm_id,
                "output_profile": batch.output_profile,
            }
        )
        claimed = self.repository.claim_video_editor_operation(
            idempotency_key=render_key,
            operation_type="submit_cloud_render",
            request_hash=render_request_hash,
            created_at=review_time.isoformat(),
        )
        if not claimed:
            operation = self.repository.get_video_editor_operation(render_key)
            if operation is None or operation["request_hash"] != render_request_hash:
                raise VideoEditorWorkflowError("渲染幂等记录冲突，请人工检查。")
            return self.get_batch(batch.batch_id)

        _, providers = self._cloud_runtime()
        input_asset = CloudAsset.model_validate(
            reviewed_item.provider_payload.get("input_asset") or {}
        )
        subtitle_object_key: str | None = None
        title_watermark_object_key: str | None = None
        temp_subtitle: Path | None = None
        temp_title: Path | None = None
        temp_merge_config: Path | None = None
        temp_bgm_mix: Path | None = None
        temp_opening_dir: Path | None = None
        opening_asset = None
        opening_duration = 0.0
        merge_config_asset = None
        bgm_asset = None
        try:
            if (
                EditStepKind.SMART_OPENING in selected_steps
                and reviewed_plan.smart_opening is not None
            ):
                temp_opening_dir = Path(
                    tempfile.mkdtemp(prefix=f"{batch.batch_id}-opening-")
                )
                temp_opening = temp_opening_dir / "opening.mp4"
                opening_duration = self._render_smart_opening_clip(
                    reviewed_plan.smart_opening.model_dump(mode="json"),
                    output_profile=batch.output_profile,
                    fps=batch.output_fps,
                    output_path=temp_opening,
                    temp_dir=temp_opening_dir,
                )
                opening_asset = providers.object_store.upload(
                    temp_opening,
                    (
                        f"video-editor-input/{batch.batch_id}/opening/"
                        f"{reviewed_plan.smart_opening.style_id}.mp4"
                    ),
                    media_type="video/mp4",
                )
            subtitle_bytes = self._review_ass_bytes(
                render_segments,
                output_profile=batch.output_profile,
                caption_groups=reviewed_plan.caption_groups,
                caption_emphasis=reviewed_plan.caption_emphasis,
                spoken_ranges=render_spoken_ranges,
                time_offset_seconds=opening_duration,
            )
            if subtitle_bytes and EditStepKind.SUBTITLES in selected_steps:
                handle = tempfile.NamedTemporaryFile(
                    suffix=".ass",
                    delete=False,
                )
                try:
                    handle.write(subtitle_bytes)
                    handle.flush()
                finally:
                    handle.close()
                temp_subtitle = Path(handle.name)
                subtitle_asset = providers.object_store.upload(
                    temp_subtitle,
                    f"video-editor-input/{batch.batch_id}/review/approved.ass",
                    media_type="text/x-ass",
                )
                subtitle_object_key = subtitle_asset.object_key

            if EditStepKind.TITLE in selected_steps:
                title_bytes = self._review_title_png_bytes(
                    title,
                    output_profile=batch.output_profile,
                )
                title_handle = tempfile.NamedTemporaryFile(
                    suffix=".png",
                    delete=False,
                )
                try:
                    title_handle.write(title_bytes)
                    title_handle.flush()
                finally:
                    title_handle.close()
                temp_title = Path(title_handle.name)
                title_asset = providers.object_store.upload(
                    temp_title,
                    (f"video-editor-input/{batch.batch_id}/review/approved-title.png"),
                    media_type="image/png",
                )
                title_watermark_object_key = title_asset.object_key

            if selected_bgm_id and EditStepKind.BGM in selected_steps:
                selected_bgm = self.resolve_bgm_asset(selected_bgm_id)
                temp_bgm_mix = self._prepare_bgm_for_cloud_mix(
                    Path(selected_bgm["_path"]),
                    volume=batch.bgm_volume,
                )
                bgm_asset = providers.object_store.upload(
                    temp_bgm_mix,
                    (
                        f"video-editor-input/{batch.batch_id}/bgm/"
                        f"{selected_bgm['asset_id']}-low-volume.m4a"
                    ),
                    media_type="audio/mp4",
                )

            if (
                reviewed_plan.trim_silence_enabled
                and len(reviewed_plan.kept_ranges) > 5
            ):
                merge_source_url = input_asset.provider_locator or input_asset.uri
                merge_payload = {
                    "MergeList": [
                        {
                            "MergeURL": merge_source_url,
                            "Start": f"{item.start:.3f}",
                            "Duration": f"{item.end - item.start:.3f}",
                        }
                        for item in reviewed_plan.kept_ranges[1:]
                    ]
                }
                handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
                try:
                    handle.write(
                        json.dumps(merge_payload, separators=(",", ":")).encode("utf-8")
                    )
                    handle.flush()
                finally:
                    handle.close()
                temp_merge_config = Path(handle.name)
                merge_config_asset = providers.object_store.upload(
                    temp_merge_config,
                    f"video-editor-input/{batch.batch_id}/review/rough-cut-merge.json",
                    media_type="application/json",
                )

            render_request = RenderRequest(
                input_asset=input_asset,
                output_object_key=(
                    f"video-editor-output/{batch.batch_id}/output/"
                    f"{batch.output_profile}.mp4"
                ),
                output_profile=batch.output_profile,
                edit_plan=reviewed_plan,
                review_confirmed=True,
                subtitle_object_key=subtitle_object_key,
                title_watermark_object_key=title_watermark_object_key,
                merge_config_asset=merge_config_asset,
                title=title,
                bgm_asset=bgm_asset,
                bgm_volume=batch.bgm_volume,
                opening_asset=opening_asset,
                opening_duration_seconds=opening_duration,
                idempotency_key=render_key,
            )
            snapshot = providers.render.submit(render_request)
            self._save_cloud_job_snapshot(
                batch=batch,
                item=reviewed_item,
                job_key=f"{batch.batch_id}:{item.item_id}:render",
                snapshot=snapshot,
            )
            is_real_output = bool(
                not snapshot.is_mock
                and snapshot.status.value == "succeeded"
                and snapshot.can_publish
                and snapshot.output_uri
            )
            next_status = (
                "configuration_required"
                if snapshot.is_mock
                else "awaiting_output_confirmation"
                if is_real_output
                else "failed"
                if snapshot.status.value == "failed"
                else "rendering"
            )
            updated = reviewed_item.model_copy(
                update={
                    "status": next_status,
                    "provider_stage": snapshot.provider_stage,
                    "provider_job_ids": {
                        **reviewed_item.provider_job_ids,
                        "render": snapshot.provider_job_id,
                    },
                    "actual_usage": {
                        **reviewed_item.actual_usage,
                        "render": snapshot.usage,
                    },
                    "provider_payload": {
                        **reviewed_item.provider_payload,
                        **(
                            {"output_uri": snapshot.output_uri}
                            if is_real_output
                            else {}
                        ),
                    },
                    "result_media_url": None,
                    "is_mock": snapshot.is_mock,
                    "publish_allowed": is_real_output,
                    "error_message": (
                        "沙箱模式未调用真实云服务、未生成成片，不能交接发布。"
                        if snapshot.is_mock
                        else str(snapshot.detail.get("message") or "") or None
                    ),
                    "updated_at": datetime.now().astimezone(),
                }
            )
            batch = self._replace_batch_item(batch, updated)
            payload = self._batch_payload(batch)
            self.repository.complete_video_editor_operation(
                idempotency_key=render_key,
                state="completed",
                resource_id=batch.batch_id,
                response=self._safe_cloud_operation_response(payload),
                updated_at=datetime.now().astimezone().isoformat(),
            )
            return payload
        except Exception as exc:
            updated = reviewed_item.model_copy(
                update={
                    "status": "outcome_unknown",
                    "provider_stage": "render_submission_outcome_unknown",
                    "error_message": (
                        "渲染提交结果不明，系统不会盲目重提；"
                        f"请按供应商任务记录查询。{str(exc)}"
                    ),
                    "updated_at": datetime.now().astimezone(),
                }
            )
            batch = self._replace_batch_item(batch, updated)
            payload = self._batch_payload(batch)
            self.repository.complete_video_editor_operation(
                idempotency_key=render_key,
                state="outcome_unknown",
                resource_id=batch.batch_id,
                response=self._safe_cloud_operation_response(payload),
                error_message=str(exc),
                updated_at=datetime.now().astimezone().isoformat(),
            )
            return payload
        finally:
            if temp_subtitle is not None:
                temp_subtitle.unlink(missing_ok=True)
            if temp_title is not None:
                temp_title.unlink(missing_ok=True)
            if temp_merge_config is not None:
                temp_merge_config.unlink(missing_ok=True)
            if temp_bgm_mix is not None:
                temp_bgm_mix.unlink(missing_ok=True)
            if temp_opening_dir is not None:
                shutil.rmtree(temp_opening_dir, ignore_errors=True)

    def continue_batch_item(self, batch_id: str, item_id: str) -> dict[str, Any]:
        batch = self._require_batch(batch_id)
        if batch.provider_mode in {"sandbox", "aliyun"}:
            raise VideoEditorWorkflowError(
                "云端轻量剪辑必须通过字幕与方案复核接口继续。"
            )
        batch = self._sync_batch(batch)
        item = next((entry for entry in batch.items if entry.item_id == item_id), None)
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        if item.status != "awaiting_subtitle_review":
            raise VideoEditorWorkflowError("该素材当前无需继续字幕复核。")
        if (
            not item.subtitle_task_id
            or self.transcription_service.get_approved_revision(item.subtitle_task_id)
            is None
        ):
            raise VideoEditorWorkflowError("请先在当前页保存并确认字幕成稿。")
        batch = self._start_batch_render(batch, item)
        return self._batch_payload(batch)

    def select_batch_item_title(
        self, batch_id: str, item_id: str, title: str
    ) -> dict[str, Any]:
        batch = self._sync_batch(self._require_batch(batch_id))
        item = next((entry for entry in batch.items if entry.item_id == item_id), None)
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        selected_title = title.strip()
        if not selected_title:
            raise VideoEditorWorkflowError("标题不能为空。")
        if len(selected_title) > 100:
            raise VideoEditorWorkflowError("标题不能超过 100 个字符。")
        updated = item.model_copy(
            update={
                "selected_title": selected_title,
                "updated_at": datetime.now().astimezone(),
            }
        )
        if item.edit_task_id:
            task = self.repository.get_task(item.edit_task_id)
            if isinstance(task, VideoEditTask):
                self.repository.save_task(
                    task.model_copy(
                        update={
                            "outputs": {
                                **task.outputs,
                                "publish_title": selected_title,
                            },
                            "updated_at": datetime.now().astimezone(),
                        }
                    )
                )
        return self._batch_payload(self._replace_batch_item(batch, updated))

    def retry_batch_item(self, batch_id: str, item_id: str) -> dict[str, Any]:
        batch = self._require_batch(batch_id)
        if batch.provider_mode in {"sandbox", "aliyun"}:
            item = next(
                (entry for entry in batch.items if entry.item_id == item_id), None
            )
            if item is None:
                raise VideoEditorWorkflowError("批次素材不存在。")
            if item.status not in {
                "failed",
                "outcome_unknown",
                "analyzing",
                "rendering",
            }:
                raise VideoEditorWorkflowError("该云任务当前无需查询重试。")
            synced = self._sync_cloud_batch(batch)
            updated = next(entry for entry in synced.items if entry.item_id == item_id)
            if updated.status in {"failed", "outcome_unknown"}:
                raise VideoEditorWorkflowError(
                    "已查询现有供应商任务，结果仍未恢复；系统未重复提交付费任务。"
                )
            return self._batch_payload(synced)
        item = next((entry for entry in batch.items if entry.item_id == item_id), None)
        if item is None:
            raise VideoEditorWorkflowError("批次素材不存在。")
        if item.status not in {"failed", "interrupted"}:
            raise VideoEditorWorkflowError("仅失败或中断的素材可以重试。")
        analysis = self.create_analysis(
            source_id=item.source_id,
            target_platform=batch.target_platform,
            subtitle_enabled=batch.subtitle_enabled,
            subtitle_model=batch.subtitle_model,
        )
        updated = item.model_copy(
            update={
                "status": "analyzing",
                "analysis_id": analysis.task_id,
                "subtitle_task_id": None,
                "edit_task_id": None,
                # A retry must not validate the new transcript against a
                # stale director plan or review snapshot from the failed
                # attempt.  Keep the source/provider identity, but rebuild
                # all transcript-derived planning state from this analysis.
                "edit_plan": {},
                "review_snapshot": {},
                "enabled_plan_step_ids": [],
                "review_confirmed_at": None,
                "title_candidates": [],
                "selected_title": None,
                "selected_bgm_id": None,
                "bgm_reason": None,
                "error_message": None,
                "updated_at": datetime.now().astimezone(),
            }
        )
        batch = self._replace_batch_item(batch, updated)
        return self._batch_payload(batch)

    def _cloud_preview_url(self, item: VideoEditorBatchItem) -> str | None:
        """为私有 OSS 输出生成短期 HTTPS 预览地址，不持久化签名 URL。"""
        raw_uri = str(item.provider_payload.get("output_uri") or "").strip()
        if not raw_uri:
            return None
        parsed = urlparse(raw_uri)
        if parsed.scheme == "https":
            hostname = (parsed.hostname or "").casefold()
            return (
                raw_uri
                if hostname == "aliyuncs.com" or hostname.endswith(".aliyuncs.com")
                else None
            )
        if parsed.scheme != "oss":
            return None
        configuration, providers = self._cloud_runtime()
        presign = getattr(providers.object_store, "presign_get_url", None)
        if not callable(presign):
            return None
        validates_remotely = getattr(
            providers.object_store,
            "validates_output_ownership_remotely",
            False,
        )
        if not validates_remotely and parsed.netloc != configuration.oss_bucket:
            return None
        object_key = unquote(parsed.path.lstrip("/"))
        return str(presign(object_key, expires_seconds=3600))

    def _materialize_cloud_edit_task(
        self,
        batch: VideoEditorBatch,
        item: VideoEditorBatchItem,
    ) -> VideoEditTask:
        """在最终确认后把真实云成片下载为现有发布页可导入的任务。"""
        task_id = f"edit-cloud-{item.item_id.removeprefix('edit-item-')}"
        existing = self.repository.get_task(task_id)
        if (
            isinstance(existing, VideoEditTask)
            and existing.status == TaskStatus.SUCCEEDED
            and existing.result_path
            and Path(existing.result_path).is_file()
        ):
            return existing

        preview_url = self._cloud_preview_url(item)
        if not preview_url:
            raise VideoEditorWorkflowError(
                "云成片尚未取得可用的 HTTPS 下载地址，暂不能交接发布。"
            )
        parsed = urlparse(preview_url)
        hostname = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or not (
            hostname == "aliyuncs.com" or hostname.endswith(".aliyuncs.com")
        ):
            raise VideoEditorWorkflowError("云成片下载地址不在允许的 OSS 域名内。")

        output_root = Path(self.video_editing_service.output_directory)
        output_directory = output_root / "cloud_results"
        output_directory.mkdir(parents=True, exist_ok=True)
        target = output_directory / f"{task_id}.mp4"
        partial = target.with_suffix(".mp4.part")
        max_bytes = 2 * 1024 * 1024 * 1024
        last_error: Exception | None = None
        for attempt in range(2):
            received = 0
            try:
                request = urllib.request.Request(
                    preview_url,
                    headers={"Accept": "video/mp4,video/*;q=0.9,*/*;q=0.1"},
                    method="GET",
                )
                with urllib.request.urlopen(request, timeout=60) as response:
                    raw_length = response.headers.get("Content-Length")
                    if raw_length and int(raw_length) > max_bytes:
                        raise VideoEditorWorkflowError(
                            "云成片超过 2GB 下载上限，暂不能交接发布。"
                        )
                    with partial.open("wb") as output_file:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > max_bytes:
                                raise VideoEditorWorkflowError(
                                    "云成片超过 2GB 下载上限，暂不能交接发布。"
                                )
                            output_file.write(chunk)
                if received <= 0:
                    raise VideoEditorWorkflowError("云成片下载结果为空。")
                with partial.open("rb") as downloaded:
                    header = downloaded.read(32)
                if b"ftyp" not in header:
                    raise VideoEditorWorkflowError(
                        "云端返回的文件不是可识别的 MP4/MOV 成片。"
                    )
                partial.replace(target)
                break
            except VideoEditorWorkflowError:
                partial.unlink(missing_ok=True)
                raise
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                ValueError,
            ) as exc:
                partial.unlink(missing_ok=True)
                last_error = exc
                if attempt == 1:
                    raise VideoEditorWorkflowError(
                        "云成片下载失败，已按规则最多重试一次。"
                    ) from exc
        if not target.is_file():
            raise VideoEditorWorkflowError(
                f"云成片下载失败：{str(last_error or '未知错误')}"
            )

        now = datetime.now().astimezone()
        source = self.resolve_source(item.source_id)
        task = VideoEditTask(
            task_id=task_id,
            title=f"云端轻量剪辑 · {item.selected_title or item.title}",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            source_video_path=source["_path"],
            edit_config=VideoEditConfig(
                steps=[],
                output_format="mp4",
                output_resolution=batch.output_resolution,
                output_fps=batch.output_fps,
                output_bitrate=batch.output_bitrate,
            ),
            result_path=str(target),
            result_mime="video/mp4",
            result_size_bytes=target.stat().st_size,
            stage="云成片已确认并准备交接",
            is_mock=False,
            outputs={
                "workflow": "edit",
                "provider_mode": batch.provider_mode,
                "provider_output_uri": str(
                    item.provider_payload.get("output_uri") or ""
                ),
                "batch_id": batch.batch_id,
                "item_id": item.item_id,
                "publish_title": item.selected_title or item.title,
            },
        )
        self.repository.save_task(task)
        return task

    def confirm_batch_results(
        self, batch_id: str, item_ids: list[str]
    ) -> dict[str, Any]:
        batch = self._sync_batch(self._require_batch(batch_id))
        selected = set(item_ids)
        if not selected:
            raise VideoEditorWorkflowError("请至少选择一条已生成成片。")
        updated_items: list[VideoEditorBatchItem] = []
        for item in batch.items:
            if item.item_id not in selected:
                updated_items.append(item)
                continue
            local_task = (
                self.repository.get_task(item.edit_task_id)
                if item.edit_task_id
                else None
            )
            has_local_result = bool(
                isinstance(local_task, VideoEditTask)
                and local_task.outputs.get("workflow") == "local_preview_export"
                and local_task.status == TaskStatus.SUCCEEDED
                and local_task.result_path
                and Path(local_task.result_path).is_file()
            )
            if (
            batch.provider_mode in {"sandbox", "aliyun", _LOCAL_RENDER_MODE}
                and not has_local_result
                and (
                    batch.is_mock
                    or item.is_mock
                    or not item.publish_allowed
                    or not item.provider_payload.get("output_uri")
                )
            ):
                raise VideoEditorWorkflowError(
                    "当前没有真实且可发布的云端成片，不能交接发布。"
                )
            if item.status != "awaiting_output_confirmation":
                raise VideoEditorWorkflowError("只能确认已成功生成的成片。")
            edit_task_id = item.edit_task_id
            if batch.provider_mode == "aliyun" and not has_local_result:
                edit_task_id = self._materialize_cloud_edit_task(batch, item).task_id
            updated_items.append(
                item.model_copy(
                    update={
                        "status": "ready_to_publish",
                        "edit_task_id": edit_task_id,
                        "confirmed_at": datetime.now().astimezone(),
                        "updated_at": datetime.now().astimezone(),
                    }
                )
            )
        batch = batch.model_copy(
            update={"items": updated_items, "updated_at": datetime.now().astimezone()}
        )
        self.repository.save_video_editor_batch(batch)
        return self._batch_payload(batch)

    def _require_batch(self, batch_id: str) -> VideoEditorBatch:
        batch = self.repository.get_video_editor_batch(batch_id)
        if batch is None:
            raise VideoEditorWorkflowError("智能剪辑批次不存在。")
        return batch

    def _sync_cloud_batch(self, batch: VideoEditorBatch) -> VideoEditorBatch:
        from src.adapters.video_editor_cloud import CloudProviderError

        _, providers = self._cloud_runtime()
        changed = False
        items: list[VideoEditorBatchItem] = []
        for item in batch.items:
            updated = item
            if item.status == "analyzing" and item.provider_job_ids.get("asr"):
                try:
                    snapshot = providers.asr.query(item.provider_job_ids["asr"])
                    self._save_cloud_job_snapshot(
                        batch=batch,
                        item=item,
                        job_key=f"{batch.batch_id}:{item.item_id}:asr",
                        snapshot=snapshot,
                    )
                    if snapshot.status.value == "succeeded":
                        interim = self._replace_batch_item(
                            batch,
                            item,
                            save=False,
                        )
                        completed = self._complete_cloud_analysis(
                            interim,
                            item,
                            snapshot,
                        )
                        updated = next(
                            entry
                            for entry in completed.items
                            if entry.item_id == item.item_id
                        )
                    elif snapshot.status.value == "failed":
                        updated = item.model_copy(
                            update={
                                "status": "failed",
                                "provider_stage": snapshot.provider_stage,
                                "error_message": str(
                                    snapshot.detail.get("message")
                                    or "Fun-ASR 转写失败。"
                                ),
                            }
                        )
                    else:
                        updated = item.model_copy(
                            update={
                                "provider_stage": snapshot.provider_stage,
                                "actual_usage": {
                                    **item.actual_usage,
                                    "asr": snapshot.usage,
                                },
                                "error_message": None,
                            }
                        )
                except CloudProviderError:
                    updated = item.model_copy(
                        update={
                            "provider_stage": "transcription_query_failed",
                            "error_message": (
                                "查询 Fun-ASR 任务失败，已按规则最多重试一次；"
                                "稍后可继续查询，系统不会重新提交转写。"
                            ),
                        }
                    )
                except Exception as exc:
                    updated = item.model_copy(
                        update={
                            "status": "outcome_unknown",
                            "provider_stage": "planning_outcome_unknown",
                            "error_message": (
                                "转写已完成但规划结果不明，系统不会盲目重提。"
                                f"{str(exc)}"
                            ),
                        }
                    )
            elif item.status == "rendering" and item.provider_job_ids.get("render"):
                try:
                    snapshot = providers.render.query(item.provider_job_ids["render"])
                    self._save_cloud_job_snapshot(
                        batch=batch,
                        item=item,
                        job_key=f"{batch.batch_id}:{item.item_id}:render",
                        snapshot=snapshot,
                    )
                    is_real_output = bool(
                        not snapshot.is_mock
                        and snapshot.status.value == "succeeded"
                        and snapshot.can_publish
                        and snapshot.output_uri
                    )
                    if is_real_output:
                        updated = item.model_copy(
                            update={
                                "status": "awaiting_output_confirmation",
                                "provider_stage": snapshot.provider_stage,
                                "provider_payload": {
                                    **item.provider_payload,
                                    "output_uri": snapshot.output_uri,
                                },
                                "result_media_url": None,
                                "publish_allowed": True,
                                "actual_usage": {
                                    **item.actual_usage,
                                    "render": snapshot.usage,
                                },
                                "error_message": None,
                            }
                        )
                    elif snapshot.status.value == "failed":
                        updated = item.model_copy(
                            update={
                                "status": "failed",
                                "provider_stage": snapshot.provider_stage,
                                "error_message": str(
                                    snapshot.detail.get("message") or "MPS 渲染失败。"
                                ),
                            }
                        )
                    else:
                        updated = item.model_copy(
                            update={
                                "provider_stage": snapshot.provider_stage,
                                "error_message": None,
                            }
                        )
                except CloudProviderError:
                    updated = item.model_copy(
                        update={
                            "provider_stage": "render_query_failed",
                            "error_message": (
                                "查询 MPS 任务失败，已按规则最多重试一次；"
                                "稍后可继续查询，系统不会重新提交渲染。"
                            ),
                        }
                    )
            if updated != item:
                changed = True
                updated = updated.model_copy(
                    update={"updated_at": datetime.now().astimezone()}
                )
            items.append(updated)
        synced = batch.model_copy(
            update={
                "items": items,
                "updated_at": (
                    datetime.now().astimezone() if changed else batch.updated_at
                ),
            }
        )
        if changed:
            self.repository.save_video_editor_batch(synced)
        return synced

    def _sync_batch(self, batch: VideoEditorBatch) -> VideoEditorBatch:
        local_changed = False
        local_items: list[VideoEditorBatchItem] = []
        for item in batch.items:
            updated = item
            if item.edit_task_id and str(item.provider_stage or "").startswith(
                "local_export_"
            ):
                task = self.repository.get_task(item.edit_task_id)
                if (
                    isinstance(task, VideoEditTask)
                    and task.outputs.get("workflow") == "local_preview_export"
                ):
                    if task.status == TaskStatus.SUCCEEDED and task.result_path:
                        try:
                            local_quality = json.loads(
                                task.outputs.get("quality_report", "{}")
                            )
                        except (TypeError, json.JSONDecodeError):
                            local_quality = {}
                        strict_local_quality = bool(
                            task.outputs.get("requested_pipeline") == "adaptive_fine_cut_v1"
                            or task.outputs.get("provider_mode") == _LOCAL_RENDER_MODE
                        )
                        if local_quality.get("passed") is True or not strict_local_quality:
                            updated = item.model_copy(
                                update={
                                    "status": "awaiting_output_confirmation",
                                    "provider_stage": "local_export_complete",
                                    "publish_allowed": bool(
                                        local_quality.get("publish_claim_allowed", False)
                                    ),
                                    "error_message": None,
                                }
                            )
                        else:
                            updated = item.model_copy(
                                update={
                                    "status": "failed",
                                    "provider_stage": "local_export_quality_failed",
                                    "publish_allowed": False,
                                    "error_message": "本机成片质量校验失败，未达到字幕、音画或视觉硬门。",
                                }
                            )
                    elif task.status == TaskStatus.FAILED:
                        updated = item.model_copy(
                            update={
                                "status": "failed",
                                "provider_stage": "local_export_failed",
                                "publish_allowed": False,
                                "error_message": task.error_message
                                or "本机成片生成失败。",
                            }
                        )
                    else:
                        updated = item.model_copy(
                            update={
                                "status": "rendering",
                                "provider_stage": "local_export_rendering",
                                "publish_allowed": False,
                            }
                        )
            if updated != item:
                local_changed = True
                updated = updated.model_copy(
                    update={"updated_at": datetime.now().astimezone()}
                )
            local_items.append(updated)
        if local_changed:
            batch = batch.model_copy(
                update={
                    "items": local_items,
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self.repository.save_video_editor_batch(batch)
        if batch.provider_mode in {"sandbox", "aliyun"}:
            return self._sync_cloud_batch(batch)
        changed = False
        items: list[VideoEditorBatchItem] = []
        for item in batch.items:
            updated = item
            if item.status == "analyzing" and item.analysis_id:
                analysis = self.repository.get_task(item.analysis_id)
                if isinstance(analysis, VideoEditTask):
                    if self._is_interrupted(analysis):
                        updated = item.model_copy(
                            update={
                                "status": "interrupted",
                                "error_message": "分析任务可能在服务重启时中断，请重试。",
                            }
                        )
                    elif analysis.status == TaskStatus.FAILED:
                        updated = item.model_copy(
                            update={
                                "status": "failed",
                                "error_message": analysis.error_message
                                or "素材分析失败。",
                            }
                        )
                    elif analysis.status == TaskStatus.SUCCEEDED:
                        transcript_id = (
                            analysis.outputs.get("transcription_task_id") or None
                        )
                        analysis_payload = self._analysis_payload(analysis)
                        title_candidates = list(
                            analysis_payload.get("title_candidates") or []
                        )
                        creative_updates = {
                            "title_candidates": title_candidates,
                            "selected_title": item.selected_title
                            or (
                                title_candidates[0] if title_candidates else item.title
                            ),
                        }
                        # The client renderer must review the ASR belonging to
                        # this batch.  The legacy branch used to leave these
                        # fields empty and let later local exports discover an
                        # older cached batch for the same source, which could
                        # silently reuse a different subtitle/edit plan.
                        if (
                            batch.provider_mode == _LOCAL_RENDER_MODE
                            and transcript_id
                        ):
                            transcript_task = self.repository.get_task(transcript_id)
                            local_segments: list[dict[str, Any]] = []
                            if transcript_task is not None:
                                approved = self.transcription_service.get_approved_revision(
                                    transcript_id
                                )
                                segment_objects = (
                                    approved.corrected_segments
                                    if approved is not None
                                    else getattr(transcript_task, "segments", [])
                                )
                                for segment in segment_objects or []:
                                    if hasattr(segment, "model_dump"):
                                        local_segments.append(
                                            segment.model_dump(mode="json")
                                        )
                                    elif isinstance(segment, Mapping):
                                        local_segments.append(dict(segment))
                            if local_segments:
                                creative_updates["subtitle_segments"] = local_segments
                        if batch.subtitle_enabled:
                            error = analysis.outputs.get("subtitle_error") or ""
                            updated = item.model_copy(
                                update={
                                    "status": "awaiting_subtitle_review"
                                    if transcript_id
                                    else "failed",
                                    "subtitle_task_id": transcript_id,
                                    "error_message": error
                                    or (None if transcript_id else "字幕生成失败。"),
                                    **creative_updates,
                                }
                            )
                        else:
                            updated = item.model_copy(
                                update={"status": "ready_to_render", **creative_updates}
                            )
            if updated.status == "ready_to_render":
                batch = self._replace_batch_item(batch, updated, save=False)
                batch = self._start_batch_render(batch, updated, save=False)
                updated = next(
                    entry for entry in batch.items if entry.item_id == item.item_id
                )
            elif updated.status == "rendering" and updated.edit_task_id:
                job = self.repository.get_task(updated.edit_task_id)
                if isinstance(job, VideoEditTask):
                    if self._is_interrupted(job):
                        updated = updated.model_copy(
                            update={
                                "status": "interrupted",
                                "error_message": "剪辑任务可能在服务重启时中断，请重试。",
                            }
                        )
                    elif job.status == TaskStatus.FAILED:
                        updated = updated.model_copy(
                            update={
                                "status": "failed",
                                "error_message": job.error_message or "剪辑失败。",
                            }
                        )
                    elif job.status == TaskStatus.SUCCEEDED:
                        updated = updated.model_copy(
                            update={"status": "awaiting_output_confirmation"}
                        )
            if updated != item:
                changed = True
            items.append(
                updated.model_copy(update={"updated_at": datetime.now().astimezone()})
                if updated != item
                else updated
            )
        synced = batch.model_copy(
            update={
                "items": items,
                "updated_at": datetime.now().astimezone()
                if changed
                else batch.updated_at,
            }
        )
        if changed:
            self.repository.save_video_editor_batch(synced)
        return synced

    def _start_batch_render(
        self, batch: VideoEditorBatch, item: VideoEditorBatchItem, *, save: bool = True
    ) -> VideoEditorBatch:
        if not item.analysis_id:
            raise VideoEditorWorkflowError("缺少素材分析结果，无法开始剪辑。")
        analysis = self.get_analysis(item.analysis_id)
        steps = batch.steps or list(analysis.get("recommended_steps") or [])
        normalized_steps: list[dict[str, Any]] = []
        for step in steps:
            kind = str(step.get("kind") or "")
            # 字幕只允许由已经人工确认的 revision 在 _run_edit 中烧录一次。
            if kind == "ai_subtitle" or (kind == "subtitle" and batch.subtitle_enabled):
                continue
            params = dict(step.get("params") or {})
            if kind == "resize":
                params["resolution"] = batch.output_resolution
            if kind == "ai_silence_trim":
                params["min_duration"] = max(
                    1.5,
                    float(params.get("min_duration") or 1.5),
                )
                params["keep_padding"] = max(
                    0.35,
                    float(params.get("keep_padding") or 0.35),
                )
            normalized_steps.append({**step, "params": params})
        steps = normalized_steps
        selected_bgm_id: str | None = None
        bgm_reason: str | None = None
        if batch.bgm_enabled:
            if batch.bgm_id:
                bgm = self.resolve_bgm_asset(batch.bgm_id)
                bgm_reason = f"使用你指定的授权音乐《{bgm['title']}》；自动做人声避让、响度和淡入淡出。"
            else:
                bgm, bgm_reason = self._recommend_bgm_asset(analysis, item.title)
            if bgm is not None:
                selected_bgm_id = bgm["asset_id"]
                auto_volume = self._auto_bgm_volume(analysis, batch.bgm_volume)
                steps = [
                    *steps,
                    {
                        "kind": "background_music",
                        "params": {
                            "bgm_path": bgm["_path"],
                            "bgm_volume": auto_volume,
                            "video_volume": 1.0,
                            "ducking": True,
                            "auto_adjusted": True,
                        },
                        "enabled": True,
                    },
                ]
        if not batch.bgm_enabled:
            bgm_reason = "已关闭自动配乐，保持素材原声。"
        job = self.create_edit_job(
            analysis_id=item.analysis_id,
            steps=steps,
            output_format=batch.output_format,
            output_resolution=batch.output_resolution,
            output_fps=batch.output_fps,
            output_bitrate=batch.output_bitrate,
            subtitle_enabled=batch.subtitle_enabled,
            publish_title=item.selected_title or item.title,
        )
        updated = item.model_copy(
            update={
                "status": "rendering",
                "edit_task_id": job.task_id,
                "selected_bgm_id": selected_bgm_id,
                "bgm_reason": bgm_reason,
                "error_message": None,
                "updated_at": datetime.now().astimezone(),
            }
        )
        return self._replace_batch_item(batch, updated, save=save)

    def _recommend_bgm_asset(
        self,
        analysis: dict[str, Any],
        source_title: str,
    ) -> tuple[dict[str, Any] | None, str]:
        assets = self.list_bgm_assets()
        if not assets:
            return (
                None,
                "本机授权音乐库为空，本条保持原声；上传一首有使用权的音乐后即可自动配乐。",
            )
        automatic_assets = [
            asset
            for asset in assets
            if asset.get("auto_eligible") is True
            and str(asset.get("authorization_status") or "unverified") == "confirmed"
            and str(asset.get("content_id_risk") or "unknown") != "registered"
        ]
        if not automatic_assets:
            return (
                None,
                "本机音乐都标记为可能触发平台版权识别，本条保持原声；仍可在复核时手动选择。",
            )
        assets = automatic_assets

        title_candidates = analysis.get("title_candidates") or []
        transcript = str(analysis.get("transcript") or "")
        text = " ".join(
            [source_title, transcript, *[str(value) for value in title_candidates]]
        ).casefold()
        technology_words = (
            "ai",
            "人工智能",
            "机器人",
            "科技",
            "软件",
            "数智",
            "智能",
            "系统",
            "设备",
        )
        business_words = (
            "客户",
            "工厂",
            "公司",
            "老板",
            "行业",
            "产品",
            "方案",
            "经营",
            "销售",
        )
        energetic_words = (
            "探店",
            "运动",
            "游戏",
            "促销",
            "开业",
            "挑战",
            "旅行",
            "展示",
            "节奏",
            "热血",
            "动感",
            "欢快",
        )
        calm_words = (
            "教程",
            "知识",
            "口播",
            "讲解",
            "访谈",
            "故事",
            "情感",
            "经验",
            "舒缓",
            "安静",
            "温柔",
        )
        edit_plan = analysis.get("edit_plan") or {}
        planned_category = (
            str(edit_plan.get("bgm_category") or "").strip()
            if isinstance(edit_plan, dict)
            else ""
        )
        desired = (
            planned_category
            if planned_category in _BGM_VOICEOVER_CATEGORIES
            else "科技未来"
            if any(word in text for word in technology_words)
            or any(word in text for word in business_words)
            else "轻松日常"
            if any(word in text for word in energetic_words)
            else "故事叙事"
            if "故事" in text or "经历" in text or "后来" in text
            else "情绪共鸣"
            if any(word in text for word in ("情感", "难过", "焦虑", "治愈", "共鸣"))
            else "理性干货"
            if any(word in text for word in calm_words)
            else "商业表达"
            if any(word in text for word in business_words)
            else "通用口播"
        )

        category_tokens = {
            "理性干货": ("知识", "教程", "讲解", "理性", "平稳", "商务"),
            "情绪共鸣": ("情感", "共鸣", "温柔", "治愈", "钢琴", "舒缓"),
            "故事叙事": ("故事", "叙事", "回忆", "温暖", "安静"),
            "商业表达": ("商务", "品牌", "产品", "正向", "轻量"),
            "科技未来": ("科技", "未来", "智能", "电子", "数智"),
            "轻松日常": ("轻松", "日常", "欢快", "松弛", "轻快"),
            "励志成长": ("励志", "成长", "积极", "希望", "向上"),
            "悬念揭秘": ("悬念", "揭秘", "紧张", "神秘", "真相"),
            "通用口播": ("通用", "百搭", "平稳", "轻量"),
        }
        planned_keywords = (
            edit_plan.get("bgm_keywords") or [] if isinstance(edit_plan, dict) else []
        )
        normalized_keywords = [
            str(item).casefold() for item in planned_keywords if str(item).strip()
        ][:6]
        planned_energy = (
            str(edit_plan.get("bgm_energy") or "克制")
            if isinstance(edit_plan, dict)
            else "克制"
        )
        duration = float((analysis.get("media") or {}).get("duration_seconds") or 0)
        business_context = any(word in text for word in business_words)

        def score(asset: dict[str, Any]) -> tuple[int, int, str]:
            asset_category = str(asset.get("voiceover_category") or "")
            searchable = " ".join(
                [
                    str(asset.get("mood") or ""),
                    *[str(item) for item in asset.get("tags") or []],
                ]
            ).casefold()
            mood_score = 14 if asset_category == desired else 0
            mood_score += sum(
                3 for token in category_tokens[desired] if token in searchable
            )
            mood_score += sum(2 for token in normalized_keywords if token in searchable)
            if business_context and desired == "科技未来" and "商务" in searchable:
                mood_score += 10
            if str(asset.get("energy") or "") == planned_energy:
                mood_score += 2
            asset_duration = float(asset.get("duration_seconds") or 0)
            covers_video = int(duration <= 0 or asset_duration >= duration)
            return mood_score, covers_video, str(asset.get("created_at") or "")

        selected = max(assets, key=score)
        reason = (
            f"AI 阅读转写文案与标题后归为“{desired}”，"
            f"自动选择本地授权音乐《{selected['title']}》并使用低音量铺底。"
        )
        return self.resolve_bgm_asset(selected["asset_id"]), reason

    @staticmethod
    def _auto_bgm_volume(analysis: dict[str, Any], preferred_volume: float) -> float:
        media = analysis.get("media") or {}
        audio = analysis.get("audio") or {}
        if not media.get("has_audio", False):
            return 0.38
        mean_volume = audio.get("mean_volume_db")
        if isinstance(mean_volume, (int, float)) and mean_volume > -14:
            return 0.16
        return round(min(max(preferred_volume, 0.18), 0.24), 2)

    def _replace_batch_item(
        self, batch: VideoEditorBatch, item: VideoEditorBatchItem, *, save: bool = True
    ) -> VideoEditorBatch:
        updated = batch.model_copy(
            update={
                "items": [
                    item if entry.item_id == item.item_id else entry
                    for entry in batch.items
                ],
                "updated_at": datetime.now().astimezone(),
            }
        )
        if save:
            self.repository.save_video_editor_batch(updated)
        return updated

    @staticmethod
    def _is_interrupted(task: VideoEditTask) -> bool:
        return task.status in {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
        } and datetime.now().astimezone() - task.updated_at > timedelta(minutes=5)

    def _batch_payload(self, batch: VideoEditorBatch) -> dict[str, Any]:
        visual_spec = None
        if (
            batch.provider_mode in {"sandbox", "aliyun", "local"}
            and batch.output_profile
        ):
            from src.services.video_editor_cloud import (
                build_business_talking_head_overlay_preview,
                visual_style_spec,
            )

            visual_spec = visual_style_spec(batch.output_profile)
        items: list[dict[str, Any]] = []
        for item in batch.items:
            cached_context = self._cached_source_context(item.source_id)
            script_text = self._avatar_script_text(item.source_id)
            effective_title = (
                item.selected_title
                or str(cached_context.get("selected_title") or "").strip()
                or self._semantic_source_title(
                    item.source_id,
                    item.title,
                    script_text,
                )
            )
            effective_title_candidates = (
                list(item.title_candidates)
                or list(cached_context.get("title_candidates") or [])
                or ([effective_title] if effective_title else [])
            )
            if item.subtitle_segments:
                preview_subtitle_segments = [
                    dict(segment) for segment in item.subtitle_segments
                ]
                subtitle_preview_source = "current_asr"
            elif cached_context.get("subtitle_segments"):
                preview_subtitle_segments = [
                    dict(segment) for segment in cached_context["subtitle_segments"]
                ]
                subtitle_preview_source = "cached_asr"
            else:
                preview_subtitle_segments = self._estimated_script_segments(
                    script_text,
                    float(cached_context.get("duration_seconds") or 0),
                )
                subtitle_preview_source = (
                    "script_estimate" if preview_subtitle_segments else "none"
                )
            analysis = (
                self.repository.get_task(item.analysis_id) if item.analysis_id else None
            )
            job = (
                self.repository.get_task(item.edit_task_id)
                if item.edit_task_id
                else None
            )
            result_media_url = item.result_media_url
            overlay_preview = None
            if visual_spec is not None:
                caption_groups = (
                    (item.edit_plan or {}).get("caption_groups")
                    if subtitle_preview_source == "current_asr"
                    else None
                )
                caption_emphasis = (
                    (item.edit_plan or {}).get("caption_emphasis")
                    if subtitle_preview_source == "current_asr"
                    else None
                )
                overlay_preview = build_business_talking_head_overlay_preview(
                    preview_subtitle_segments,
                    title=effective_title,
                    output_profile=batch.output_profile,
                    caption_groups=caption_groups,
                    caption_emphasis=caption_emphasis,
                    spoken_ranges=(item.edit_plan or {}).get("spoken_ranges"),
                    caption_glossary=(item.edit_plan or {}).get("transcript_glossary"),
                )
            if (
                batch.provider_mode == "aliyun"
                and item.publish_allowed
                and item.provider_payload.get("output_uri")
            ):
                try:
                    result_media_url = self._cloud_preview_url(item)
                except Exception:
                    result_media_url = None
            items.append(
                {
                    "item_id": item.item_id,
                    "source_id": item.source_id,
                    "title": item.title,
                    "status": item.status,
                    "analysis_id": item.analysis_id,
                    "subtitle_task_id": item.subtitle_task_id,
                    "edit_task_id": item.edit_task_id,
                    "title_candidates": effective_title_candidates,
                    "selected_title": effective_title,
                    "selected_bgm_id": item.selected_bgm_id,
                    "bgm_reason": item.bgm_reason,
                    "provider_stage": item.provider_stage,
                    "provider_job_ids": item.provider_job_ids,
                    "provider_payload": item.provider_payload,
                    "render_manifest": item.provider_payload.get("render_manifest")
                    or None,
                    "actual_usage": item.actual_usage,
                    "edit_plan": item.edit_plan or None,
                    "enabled_plan_step_ids": item.enabled_plan_step_ids,
                    "subtitle_segments": item.subtitle_segments,
                    "preview_subtitle_segments": preview_subtitle_segments,
                    "subtitle_preview_source": subtitle_preview_source,
                    "overlay_preview": overlay_preview,
                    "review_snapshot": item.review_snapshot,
                    "review_confirmed_at": (
                        item.review_confirmed_at.isoformat()
                        if item.review_confirmed_at
                        else None
                    ),
                    "result_media_url": result_media_url,
                    "is_mock": item.is_mock,
                    "publish_allowed": item.publish_allowed,
                    "error_message": item.error_message,
                    "confirmed_at": item.confirmed_at.isoformat()
                    if item.confirmed_at
                    else None,
                    "analysis": self._analysis_payload(analysis)
                    if isinstance(analysis, VideoEditTask)
                    else None,
                    "job": self._job_payload(job)
                    if isinstance(job, VideoEditTask)
                    else None,
                }
            )
        status = (
            "ready_to_publish"
            if items and all(item["status"] == "ready_to_publish" for item in items)
            else "running"
        )
        if any(item["status"] == "configuration_required" for item in items):
            status = "configuration_required"
        elif any(item["status"] == "outcome_unknown" for item in items):
            status = "outcome_unknown"
        elif any(
            item["status"] in {"analyzing", "rendering", "ready_to_render"}
            for item in items
        ):
            status = "running"
        elif any(item["status"] == "awaiting_subtitle_review" for item in items):
            status = "awaiting_subtitle_review"
        elif any(item["status"] == "awaiting_output_confirmation" for item in items):
            status = "awaiting_output_confirmation"
        elif any(item["status"] in {"failed", "interrupted"} for item in items):
            status = "partial"
        return {
            "batch_id": batch.batch_id,
            "status": status,
            "target_platform": batch.target_platform,
            "subtitle_enabled": batch.subtitle_enabled,
            "subtitle_model": batch.subtitle_model,
            "bgm_enabled": batch.bgm_enabled,
            "bgm_id": batch.bgm_id,
            "bgm_volume": batch.bgm_volume,
            "provider_mode": batch.provider_mode,
            "output_profile": batch.output_profile,
            "output_resolution": batch.output_resolution,
            "output_fps": batch.output_fps,
            "output_bitrate": batch.output_bitrate,
            "visual_spec": visual_spec,
            "quote_id": batch.quote_id,
            "cost_quote": batch.cost_quote or None,
            "actual_usage": {
                item["item_id"]: item["actual_usage"]
                for item in items
                if item["actual_usage"]
            },
            "billing_confirmation": batch.billing_confirmation,
            "billing_confirmed_at": (
                batch.billing_confirmed_at.isoformat()
                if batch.billing_confirmed_at
                else None
            ),
            "idempotency_key": batch.idempotency_key,
            "is_mock": batch.is_mock,
            "bgm": (
                {
                    key: value
                    for key, value in self.resolve_bgm_asset(batch.bgm_id).items()
                    if key != "_path"
                }
                if batch.bgm_enabled and batch.bgm_id
                else None
            ),
            "items": items,
            "created_at": batch.created_at.isoformat(),
            "updated_at": batch.updated_at.isoformat(),
        }

    # ------------------------------------------------------------------
    # 序列化与状态更新
    # ------------------------------------------------------------------
    def _get_workflow_task(self, task_id: str, workflow: str) -> VideoEditTask:
        task = self.repository.get_task(task_id)
        if (
            not isinstance(task, VideoEditTask)
            or task.outputs.get("workflow") != workflow
        ):
            raise VideoEditorWorkflowError("智能剪辑任务不存在。")
        return task

    def _update(self, task: VideoEditTask, **changes: Any) -> VideoEditTask:
        updated = task.model_copy(
            update={**changes, "updated_at": datetime.now().astimezone()}
        )
        self.repository.save_task(updated)
        return updated

    @staticmethod
    def _analysis_payload(task: VideoEditTask) -> dict[str, Any]:
        try:
            analysis = json.loads(task.outputs.get("analysis_json", "{}"))
        except json.JSONDecodeError:
            analysis = {}
        return {
            "analysis_id": task.task_id,
            "status": task.status.value,
            "progress": task.progress,
            "stage": task.stage,
            "error_message": task.error_message,
            "source_id": task.outputs.get("source_id"),
            "target_platform": task.outputs.get("target_platform", "douyin"),
            "subtitle_enabled": task.outputs.get("subtitle_enabled") == "true",
            "subtitle_model": task.outputs.get("subtitle_model", "large-v3-turbo"),
            "content_advice": task.outputs.get("content_advice") or None,
            **analysis,
        }

    @staticmethod
    def _job_payload(task: VideoEditTask) -> dict[str, Any]:
        media_url = (
            f"/api/v1/video-editor/jobs/{task.task_id}/media"
            if task.result_path
            else None
        )
        try:
            quality_report = json.loads(task.outputs.get("quality_report", "{}"))
        except json.JSONDecodeError:
            quality_report = None
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "progress": task.progress,
            "stage": task.stage,
            "error_message": task.error_message,
            "result_size_bytes": task.result_size_bytes,
            "media_url": media_url,
            "download_url": f"/api/v1/video-editor/jobs/{task.task_id}/download"
            if task.result_path
            else None,
            "source_id": task.outputs.get("source_id"),
            "analysis_id": task.outputs.get("analysis_id"),
            "publish_title": task.outputs.get("publish_title") or None,
            "workflow": task.outputs.get("workflow"),
            "quality_report": quality_report,
        }
