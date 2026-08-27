"""P0 v4 v2: 用 PIL 直接画字幕（避免 ffmpeg libass 字体回退问题）。

输入：ASR 词级时间戳 + Caption 样式
输出：透明 PNG 字幕帧（按每条 cue 一帧 PNG）+ ffmpeg overlay 命令
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# === 路径 ===
ASR_JSON = Path(
    "C:/Users/zeng/Desktop/video/work/auto-fine-cut-adaptive-20260824-short-real/asr-full.json"
)
ASS_OUT = Path(
    "C:/Users/zeng/Desktop/video/data/video_edits/r8-v4-sample-30s.ass"
)
PNG_DIR = Path(
    "C:/Users/zeng/Desktop/video/work/v4_subtitle_png"
)
FONT_PATH = Path("C:/Windows/Fonts/msyh.ttc")  # 用 msyh（系统已装）
# 旧基准 ASS 用的是 Source Han Serif CN Heavy — msyh 类似衬线，灰度感接近
# ffmpeg libass 在 Windows + 没装 Source Han Serif CN 时会回退到 Liberation Sans 算宽度
# 直接用 PIL + msyh.ttc 保证中文宽度正确
W, H = 720, 1280
FONT_SIZE = 52
MARGIN_V = 170
KEYWORD_COLOR = (87, 200, 255)  # 蓝色高亮（&H0057C8FF）
NORMAL_COLOR = (248, 250, 252)  # 接近白色（&H00F8FAFC）
OUTLINE_COLOR = (0, 0, 0)


def load_asr() -> list[dict]:
    with ASR_JSON.open(encoding="utf-8") as f:
        asr = json.load(f)
    return list(asr.get("segments") or [])


def collect_words(segs: list[dict]) -> list[dict]:
    out: list[dict] = []
    for s in segs:
        for w in s.get("words") or []:
            t = w.get("word")
            if not t:
                continue
            out.append(
                {
                    "start": float(w.get("start", 0)),
                    "end": float(w.get("end", 0)),
                    "text": str(t),
                }
            )
    return out


def cut_into_cues(words: list[dict]) -> list[dict]:
    cues: list[dict] = []
    if not words:
        return cues
    cur: list[dict] = []
    cur_start = None
    for w in words:
        if cur_start is None:
            cur_start = w["start"]
        cur.append(w)
        cur_dur = w["end"] - cur_start
        if cur_dur >= 0.9 and cur_dur <= 2.4:
            cues.append({"start": cur_start, "end": w["end"], "words": list(cur)})
            cur = []
            cur_start = None
        elif cur_dur > 2.4:
            cues.append({"start": cur_start, "end": w["end"], "words": list(cur)})
            cur = []
            cur_start = None
    if cur:
        cues.append(
            {"start": cur_start, "end": cur[-1]["end"], "words": list(cur)}
        )
    return cues


_DIGIT_RE = re.compile(r"^[\d.]+")


def is_keyword_word(w_text: str) -> bool:
    base = w_text.strip()
    if not base:
        return False
    if _DIGIT_RE.match(base):
        return True
    return False


def mark_keywords(cues: list[dict]) -> list[dict]:
    last_kw_end = -1000.0
    min_gap = 8.0
    for cue in cues:
        kws: list[int] = []
        for j, w in enumerate(cue["words"]):
            if is_keyword_word(w["text"]):
                kws.append(j)
        gap = cue["start"] - last_kw_end
        if kws and gap < min_gap and len(kws) > 1:
            kws = kws[:1]
        cue["keyword_indices"] = kws
        if kws:
            last_kw_end = cue["end"]
    return cues


def draw_cue(cue: dict, font: ImageFont.ImageFont) -> Image.Image:
    """画一条字幕到透明 PNG（按 char 位置画，方便混色）。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 拼字符串（不含高亮 tag）
    line = "".join(w["text"] for w in cue["words"])
    # 测量整行宽
    bbox = draw.textbbox((0, 0), line, font=font, anchor="lt")
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (W - text_w) // 2
    y = H - MARGIN_V - text_h
    # 描边（用大字体画黑色 4 遍：上下左右各偏移 2px）
    outline_w = 2
    for ox in (-outline_w, 0, outline_w):
        for oy in (-outline_w, 0, outline_w):
            if ox == 0 and oy == 0:
                continue
            draw.text(
                (x + ox, y + oy), line, font=font,
                fill=(*OUTLINE_COLOR, 220), anchor="lt",
            )
    # 画字符（按 char 高亮关键词）
    cur_x = x
    for j, w in enumerate(cue["words"]):
        t = w["text"]
        if j in cue.get("keyword_indices", []):
            color = (*KEYWORD_COLOR, 255)
        else:
            color = (*NORMAL_COLOR, 255)
        # 画 char 阴影（1px 右下）
        draw.text((cur_x + 1, y + 1), t, font=font,
                  fill=(0, 0, 0, 180), anchor="lt")
        draw.text((cur_x, y), t, font=font, fill=color, anchor="lt")
        cb = draw.textbbox((0, 0), t, font=font, anchor="lt")
        cur_x += cb[2] - cb[0]
    return img


def write_ass_metadata(cues: list[dict]) -> None:
    """同时写一份 metadata 给 Python 渲染用（每条 cue start/end/words/keyword_indices）。"""
    meta = {
        "play_res": [W, H],
        "cues": [
            {
                "start": c["start"],
                "end": c["end"],
                "words": c["words"],
                "keyword_indices": c.get("keyword_indices", []),
            }
            for c in cues
        ],
    }
    out = ASS_OUT.with_suffix(".meta.json")
    out.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print("wrote meta:", out)


def main() -> int:
    segs = load_asr()
    words = collect_words(segs)
    print("total words:", len(words))
    cues = cut_into_cues(words)
    cues = mark_keywords(cues)
    k_count = sum(1 for c in cues if c.get("keyword_indices"))
    print(f"cues: {len(cues)}, keyword: {k_count} ({k_count/max(len(cues),1)*100:.1f}%)")
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(FONT_PATH), FONT_SIZE)
    # 写一帧示例
    if cues:
        img = draw_cue(cues[0], font)
        img.save(PNG_DIR / "sample.png")
        print("sample drawn:", cues[0])
    write_ass_metadata(cues)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
