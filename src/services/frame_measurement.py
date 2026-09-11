"""Measure where the subject actually is in a video before placing anything.

Every overlay position used to be a constant tuned to one clip (``face`` at
0.10-0.60H, subtitles at 0.79H, the safe band between 0.602H and 0.757H).  That
works for the sample it was tuned on and breaks the moment the framing changes:
a subject sitting lower, a wider shot, a different aspect.  The ``face_boxes``
field the creative compiler consults is also always empty in practice -- nothing
in the project detects a face -- so the director was making placement decisions
from no measurement at all.

This module supplies the missing measurement.  It samples the source, finds the
region the subject occupies, and reports a placement plan derived from that
observation.  Callers get real numbers instead of a guess, and still get a
documented fallback when sampling is unavailable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:  # The optional dependency must not break import when it is absent.
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:  # pragma: no cover - dotenv is present in this project
    pass

# Grid resolution for the coarse subject map.  Deliberately small: the
# measurement is about finding the occupied band, not about faces.
_GRID_COLUMNS = 24
_GRID_ROWS = 40

_VISION_WIDTH_LADDER = (288, 256, 224, 192, 160)

# A vision call costs 25-45s, so the answer is memoised per source revision.
# Repeated exports of the same clip reuse it; a re-uploaded file has a new
# mtime and is measured again.
_VISION_CACHE: dict[tuple[str, float, int], dict[str, Any] | None] = {}


def _merge_zones(
    results: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Combine several frame measurements into one stable layout.

    A single frame gave a different safe-area set on each call, so placement
    jittered between exports.  Averaging those answers is the obvious fix and
    the wrong one: the model reports different areas per frame, so the bounding
    box of "every area anyone mentioned" grew to almost the whole frame (an
    earlier version produced ``[0,0]-[1,0.95]`` at 0.23 agreement), which would
    put a component over the speaker.

    Consensus has to produce a rectangle, so instead of averaging, pick the
    single reported zone that the most frames corroborate, breaking ties toward
    the smaller area.  Corroboration is overlap, not equality: two frames rarely
    report byte-identical boxes for the same free space.
    """

    usable = [item for item in results if isinstance(item, Mapping)]
    if not usable:
        return None
    zones: list[tuple[float, float, float, float]] = []
    for item in usable:
        for zone in item.get("safe_zones") or []:
            if not isinstance(zone, Mapping):
                continue
            try:
                left = float(zone["left"])
                top = float(zone["top"])
                right = float(zone["right"])
                bottom = float(zone["bottom"])
            except (KeyError, TypeError, ValueError):
                continue
            if right > left and bottom > top:
                zones.append((left, top, right, bottom))
    if not zones:
        return None

    def overlap_ratio(
        one: tuple[float, float, float, float],
        other: tuple[float, float, float, float],
    ) -> float:
        width = min(one[2], other[2]) - max(one[0], other[0])
        height = min(one[3], other[3]) - max(one[1], other[1])
        if width <= 0 or height <= 0:
            return 0.0
        smaller = min(
            (one[2] - one[0]) * (one[3] - one[1]),
            (other[2] - other[0]) * (other[3] - other[1]),
        )
        return (width * height) / smaller if smaller > 0 else 0.0

    # Merge by shape, not into one box.  The layout needs a wide region for the
    # pill and a tall one for the square marks; collapsing everything to the
    # single most-corroborated area left one 0.12W strip and every component
    # then failed the safety check.
    horizontal = [zone for zone in zones if (zone[2] - zone[0]) >= (zone[3] - zone[1])]
    vertical = [zone for zone in zones if (zone[2] - zone[0]) < (zone[3] - zone[1])]

    def pick(
        pool: list[tuple[float, float, float, float]],
    ) -> tuple[tuple[float, float, float, float], int] | None:
        best: tuple[float, float, float, float] | None = None
        best_rank: tuple[int, float, float] | None = None
        for candidate in pool:
            corroborating = sum(
                1 for other in pool if overlap_ratio(candidate, other) >= 0.5
            )
            area = (candidate[2] - candidate[0]) * (candidate[3] - candidate[1])
            # More corroboration wins; then the larger area, because a roomier
            # free region is more useful for a readable component; then a
            # deterministic coordinate so the result is reproducible.
            rank = (corroborating, area, -candidate[0] - candidate[1])
            if best_rank is None or rank > best_rank:
                best, best_rank = candidate, rank
        if best is None or best_rank is None:
            return None
        return best, best_rank[0]

    subjects = [
        item.get("subject")
        for item in usable
        if isinstance(item.get("subject"), Mapping)
    ]
    merged_zones: list[dict[str, Any]] = []
    agreed = 0
    for pool, label in ((horizontal, "横向"), (vertical, "纵向")):
        chosen = pick(pool)
        if chosen is None:
            continue
        (left, top, right, bottom), votes = chosen
        agreed = max(agreed, votes)
        merged_zones.append(
            {
                "left": round(left, 4),
                "top": round(top, 4),
                "right": round(right, 4),
                "bottom": round(bottom, 4),
                "why": f"{votes} 帧互证的{label}安全区",
            }
        )
    if not merged_zones:
        return None
    return {
        "source": "multimodal_frame_analysis_merged",
        "model": usable[0].get("model"),
        "frame_count": len(usable),
        "subject": subjects[0] if subjects else None,
        "safe_zones": merged_zones,
        "agreement": agreed,
    }


def _layout_cache_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "layout_measurements.json"


def _load_layout_cache() -> dict[str, Any]:
    path = _layout_cache_path()
    try:
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def _save_layout_cache(cache: Mapping[str, Any]) -> None:
    path = _layout_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def measure_layout(
    frames: list[Path],
    *,
    cache_key: Any = None,
    timeout: float = 180.0,
) -> dict[str, Any] | None:
    """Measure a clip from several frames, with a durable per-source cache.

    The cache is written to disk so a restart does not pay the vision cost
    again, and it is invalidated by the source clip's mtime and size.
    """

    if os.getenv("VIDEO_LAYOUT_VISION", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return None
    if not frames:
        return None
    identity = str(cache_key) if cache_key is not None else str(frames[0])
    stamp: list[Any] = []
    for frame in frames:
        try:
            stat = frame.stat()
        except OSError:
            continue
        stamp.append([frame.name, stat.st_mtime, stat.st_size])
    cache = _load_layout_cache()
    entry = cache.get(identity)
    if isinstance(entry, dict) and entry.get("stamp") == stamp and entry.get("layout"):
        return entry["layout"]

    results: list[Mapping[str, Any]] = []
    for frame in frames:
        layout = vision_layout(frame, timeout=timeout)
        if layout:
            results.append(layout)
    merged = _merge_zones(results)
    if merged:
        cache[identity] = {"stamp": stamp, "layout": merged}
        _save_layout_cache(cache)
    return merged


def vision_layout_cached(
    frame_path: Path | str | None,
    *,
    timeout: float = 180.0,
    cache_key: Any = None,
) -> dict[str, Any] | None:
    """``vision_layout`` with memoisation and an explicit off switch.

    ``cache_key`` must identify the *source*, not the scratch frame.  It
    previously defaulted to the frame path, which lives in a per-export
    temporary directory, so the key changed on every run and the cache never
    hit -- every export paid the full 15-45s again.
    """

    if os.getenv("VIDEO_LAYOUT_VISION", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return None
    if not frame_path:
        return None
    source = Path(frame_path)
    try:
        stat = source.stat()
    except OSError:
        return None
    identity = str(cache_key) if cache_key is not None else str(source)
    key = (identity, stat.st_mtime, stat.st_size)
    if key not in _VISION_CACHE:
        _VISION_CACHE[key] = vision_layout(source, timeout=timeout)
    return _VISION_CACHE[key]

_VISION_PROMPT = (
    "这是竖屏口播视频的一帧。只输出 JSON，不要任何解释或 markdown 代码块。"
    "格式："
    '{"people": 1,'
    ' "subject": {"left":0.0,"top":0.0,"right":1.0,"bottom":1.0},'
    ' "subject_bottom_is_real": true,'
    ' "safe_zones":[{"left":0.0,"top":0.0,"right":1.0,"bottom":1.0,"why":"说明"}]}'
    "。subject 只框人物本身（头部到身体可见部分），"
    "不要为了凑满画面而把框拉到图片底部；如果人物下方是空白或背景，bottom 就停在人物下缘。"
    "subject_bottom_is_real 表示 bottom 是真实量出来的而不是因为不确定而取 1.0。"
    "safe_zones 指可以安全叠加图形或文字、既不遮挡人物也不覆盖画面底部字幕条的区域，"
    "最多 3 个，按面积从大到小。所有坐标是 0 到 1 的小数。"
)


@dataclass
class FrameMeasurement:
    """Observed geometry for one source clip."""

    frame_width: int = 720
    frame_height: int = 1280
    # Rows whose cells carry subject energy, as fractions of frame height.
    occupied_rows: list[float] = field(default_factory=list)
    subject_top: float | None = None
    subject_bottom: float | None = None
    subject_left: float | None = None
    subject_right: float | None = None
    subject_row_centroid: float | None = None
    subject_column_centroid: float | None = None
    # Fraction of the frame the subject covers, used to tell a talking head from
    # a wide shot.
    subject_coverage: float = 0.0
    sampled_frames: int = 0
    measured: bool = False
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "subject_top": self.subject_top,
            "subject_bottom": self.subject_bottom,
            "subject_left": self.subject_left,
            "subject_right": self.subject_right,
            "subject_row_centroid": self.subject_row_centroid,
            "subject_column_centroid": self.subject_column_centroid,
            "subject_coverage": round(self.subject_coverage, 4),
            "sampled_frames": self.sampled_frames,
            "measured": self.measured,
            "reason": self.reason,
        }


def _decode_gray_grid(
    path: Path, *, rows: int = _GRID_ROWS, columns: int = _GRID_COLUMNS
) -> tuple[bytes, int, int]:
    """Decode one frame as a small grayscale grid via ffmpeg.

    Returns ``(pixels, width, height)``.
    """

    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(path),
        "-vf",
        f"scale={columns}:{rows},format=gray",
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-",
    ]
    completed = subprocess.run(command, capture_output=True, check=False, timeout=120)
    if completed.returncode != 0 or not completed.stdout:
        return b"", 0, 0
    pixels = completed.stdout
    frame_bytes = columns * rows
    if len(pixels) < frame_bytes:
        return b"", 0, 0
    return pixels[:frame_bytes], columns, rows


def _detail_axes(
    pixels: bytes, width: int, height: int
) -> tuple[list[float], list[float]]:
    """Per-row and per-column detail, each normalised to its own peak.

    A tall portrait frame has room to the left or right of the subject, and that
    is where an overlay belongs.  Measuring both axes is what lets placement
    follow the actual subject instead of a constant band.
    """

    row_scores: list[float] = [0.0] * height
    column_scores: list[float] = [0.0] * width
    for row in range(height):
        for column in range(1, width):
            gradient = abs(
                pixels[row * width + column] - pixels[row * width + column - 1]
            )
            row_scores[row] += gradient
            column_scores[column] += gradient
    for row in range(1, height):
        for column in range(width):
            gradient = abs(
                pixels[row * width + column] - pixels[(row - 1) * width + column]
            )
            row_scores[row] += gradient
            column_scores[column] += gradient
    row_peak = max(row_scores) if row_scores else 0.0
    column_peak = max(column_scores) if column_scores else 0.0
    rows = [value / row_peak for value in row_scores] if row_peak else []
    columns = [value / column_peak for value in column_scores] if column_peak else []
    return rows, columns


def _centroid(scores: list[float]) -> float | None:
    """Position of the detail mass, as a fraction along the axis."""

    total = sum(scores)
    if total <= 0.0:
        return None
    weighted = sum(index * value for index, value in enumerate(scores))
    return weighted / total / max(1, len(scores))


def _span(scores: list[float], threshold: float = 0.55) -> tuple[float, float] | None:
    """First and last position above ``threshold``, as fractions of the axis."""

    hits = [index for index, value in enumerate(scores) if value > threshold]
    if not hits:
        return None
    length = max(1, len(scores))
    return hits[0] / length, hits[-1] / length


def measure_source_frame(path: Path | str | None) -> FrameMeasurement:
    """Measure the subject band of a source clip.

    Falls back to an unmeasured result (``measured=False``) with a reason rather
    than raising, so a placement that must not fail can still choose its
    documented default.
    """

    measurement = FrameMeasurement()
    if not path:
        measurement.reason = "没有可测量的素材路径"
        return measurement
    source = Path(path)
    if not source.is_file():
        measurement.reason = "素材文件不存在"
        return measurement
    try:
        pixels, width, height = _decode_gray_grid(source)
    except (OSError, subprocess.SubprocessError) as error:
        measurement.reason = f"取样失败：{type(error).__name__}"
        return measurement
    if not pixels:
        measurement.reason = "解码没有返回画面"
        return measurement
    rows, columns = _detail_axes(pixels, width, height)
    if not rows or not columns:
        measurement.reason = "画面细节过低，无法定位主体"
        return measurement
    row_span = _span(rows)
    column_span = _span(columns)
    if row_span is None or column_span is None:
        measurement.reason = "没有找到明显的主体细节带"
        return measurement
    measurement.subject_top = round(row_span[0], 4)
    measurement.subject_bottom = round(row_span[1], 4)
    measurement.subject_left = round(column_span[0], 4)
    measurement.subject_right = round(column_span[1], 4)
    measurement.subject_row_centroid = _centroid(rows)
    measurement.subject_column_centroid = _centroid(columns)
    measurement.subject_coverage = round(
        (row_span[1] - row_span[0]) * (column_span[1] - column_span[0]), 4
    )
    measurement.sampled_frames = 1
    measurement.measured = True
    measurement.reason = "已按画面细节测量主体范围"
    return measurement


def vision_layout(
    frame_path: Path | str | None,
    *,
    timeout: float = 180.0,
) -> dict[str, Any] | None:
    """Ask the multimodal model for the subject box and the safe overlay areas.

    Returns ``None`` when the model is unavailable or the answer is unusable, so
    callers keep an explicit fallback instead of shipping a guessed placement.
    Payload size matters: a 640px JPEG broke the TLS connection outright, while
    the widths below answer reliably, so the frame is encoded down a ladder.
    """

    if not frame_path:
        return None
    source = Path(frame_path)
    if not source.is_file():
        return None
    base_url = (
        os.getenv("VIDEO_VISION_BASE_URL") or "https://api.minimaxi.com/v1"
    )
    api_key = (
        os.getenv("VIDEO_VISION_API_KEY")
        or os.getenv("MINIMAX_API_KEY")
        or os.getenv("MINIMAX_TEXT_API_KEY")
        or os.getenv("MINIMAX_TOKEN_PLAN_KEY")
        or ""
    )
    if not api_key:
        return None
    model = os.getenv("VIDEO_VISION_MODEL") or "MiniMax-M3"
    # The endpoint drops the TLS connection intermittently -- the same payload
    # answered once and failed on the next call, so size is not the only factor.
    # One retry per width, then move on: the project rule is a single automatic
    # retry, never a reconnect loop.
    for width in _VISION_WIDTH_LADDER:
        try:
            encoded = _encode_frame_b64(source, width)
        except (OSError, subprocess.SubprocessError):
            continue
        if not encoded:
            continue
        for attempt in range(2):
            ok, message = _post_vision(base_url, api_key, model, encoded, timeout)
            if not ok:
                continue
            parsed = _first_json_object(message)
            if parsed and _layout_is_usable(parsed):
                return {
                    "source": "multimodal_frame_analysis",
                    "model": model,
                    "frame_width": width,
                    "attempt": attempt + 1,
                    "people": parsed.get("people"),
                    "subject": parsed.get("subject"),
                    "safe_zones": parsed.get("safe_zones") or [],
                }
            break
    return None


def _encode_frame_b64(path: Path, width: int) -> str:
    """Encode one frame as base64 JPEG.

    Writes through a real file rather than piping mjpeg to stdout: the pipe
    produced a larger payload at the same width (22 KB vs 17 KB) and that extra
    size is what broke the TLS connection.
    """

    import base64
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "frame.jpg"
        completed = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(path),
                "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "6",
                str(target),
            ],
            capture_output=True,
            check=False,
            timeout=120,
        )
        if completed.returncode != 0 or not target.is_file():
            return ""
        return base64.b64encode(target.read_bytes()).decode("ascii")


def _post_vision(
    base_url: str, api_key: str, model: str, image_b64: str, timeout: float
) -> tuple[bool, str]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VISION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                ],
            }
        ],
        "temperature": 0.1,
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}"
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return False, type(error).__name__
    try:
        return True, str(body["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError):
        return False, "响应结构异常"


def _first_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _layout_is_usable(parsed: Mapping[str, Any]) -> bool:
    subject = parsed.get("subject")
    if not isinstance(subject, Mapping):
        return False
    try:
        top = float(subject["top"])
        bottom = float(subject["bottom"])
    except (KeyError, TypeError, ValueError):
        return False
    return 0.0 <= top < bottom <= 1.0


def probe_frame_size(path: Path | str | None) -> tuple[int, int]:
    """Return the source's pixel dimensions, or a portrait default."""

    if not path:
        return 720, 1280
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    match = re.match(r"(\d+)\D+(\d+)", (completed.stdout or "").strip())
    if not match:
        return 720, 1280
    return int(match.group(1)), int(match.group(2))
