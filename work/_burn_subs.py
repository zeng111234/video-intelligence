"""P0 v4 v3: PIL 渲染字幕到 PNG + ffmpeg overlay 烧录 MP4。

解决：ffmpeg libass 在 Windows 找不到 Source Han Serif CN Heavy → 字幕截断。
直接用 PIL + msyh.ttc 画到透明 PNG，按 cue start/end 序列化为 PNG，
ffmpeg 用 ffmpeg filter overlay 烧录。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# === 路径 ===
ASR_JSON = Path(
    "C:/Users/zeng/Desktop/video/work/auto-fine-cut-adaptive-20260824-short-real/asr-full.json"
)
PNG_DIR = Path("C:/Users/zeng/Desktop/video/work/v4_subtitle_png")
FONT_PATH = Path("C:/Windows/Fonts/msyh.ttc")
META_PATH = Path("C:/Users/zeng/Desktop/video/data/video_edits/r8-v4-sample-30s.meta.json")
ASS_OUT = Path("C:/Users/zeng/Desktop/video/data/video_edits/r8-v4-sample-30s.ass")
W, H = 720, 1280
FONT_SIZE = 52
MARGIN_V = 170
KEYWORD_COLOR = (87, 200, 255)
NORMAL_COLOR = (248, 250, 252)
OUTLINE_COLOR = (0, 0, 0)


def load_meta() -> dict:
    with META_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def draw_cue(cue: dict, font: ImageFont.ImageFont) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    line = "".join(w["text"] for w in cue["words"])
    bbox = draw.textbbox((0, 0), line, font=font, anchor="lt")
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (W - text_w) // 2
    y = H - MARGIN_V - text_h
    outline_w = 2
    for ox in (-outline_w, 0, outline_w):
        for oy in (-outline_w, 0, outline_w):
            if ox == 0 and oy == 0:
                continue
            draw.text(
                (x + ox, y + oy), line, font=font,
                fill=(*OUTLINE_COLOR, 220), anchor="lt",
            )
    cur_x = x
    for j, w in enumerate(cue["words"]):
        t = w["text"]
        if j in cue.get("keyword_indices", []):
            color = (*KEYWORD_COLOR, 255)
        else:
            color = (*NORMAL_COLOR, 255)
        draw.text((cur_x + 1, y + 1), t, font=font,
                  fill=(0, 0, 0, 180), anchor="lt")
        draw.text((cur_x, y), t, font=font, fill=color, anchor="lt")
        cb = draw.textbbox((0, 0), t, font=font, anchor="lt")
        cur_x += cb[2] - cb[0]
    return img


def main() -> int:
    if not META_PATH.is_file():
        # 没有 meta 就调 _make_ass_v2 生成
        subprocess.check_call(["python", "-X", "utf8", "work/_make_ass_v2.py"])
    meta = load_meta()
    cues = meta["cues"]
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    if PNG_DIR.exists():
        for f in PNG_DIR.glob("*.png"):
            f.unlink()
    font = ImageFont.truetype(str(FONT_PATH), FONT_SIZE)
    for i, cue in enumerate(cues):
        img = draw_cue(cue, font)
        img.save(PNG_DIR / f"cue_{i:03d}.png")
    print("drew " + str(len(cues)) + " PNG cues in " + str(PNG_DIR))
    # 同时写一份 metadata 含 list 给 ffmpeg 引用
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
