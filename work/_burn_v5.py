"""P0 v4 v5: 用 ffmpeg drawtext + fontfile=msyh.ttc 烧录字幕。

drawtext 用 enable='between(t,start,end)' 控制显示时间，fontfile 直接指定中文字体。
每条 cue 一个 drawtext filter（88 条），按时间 enable。
"""
import json
import re
import shlex
import subprocess
from pathlib import Path

ROOT = Path("C:/Users/zeng/Desktop/video")
META_PATH = ROOT / "data/video_edits/r8-v4-sample-30s.meta.json"
SRC_MP4 = ROOT / "data/avatar_results/avatar-2704b95149a3.mp4"
OUT_MP4 = ROOT / "work/r8-v4-sample-30s.mp4"
FONT_PATH = "C\\:/Windows/Fonts/msyh.ttc"
BURN_DUR = 30
W, H = 720, 1280
FONT_SIZE = 52
MARGIN_V = 170


def _escape_drawtext(text: str) -> str:
    """转义 ffmpeg drawtext text 特殊字符：\\ : ' '"""
    # drawtext text 用单引号包裹；文本中单引号要 escape
    # 反斜杠、冒号、单引号、% 等要 escape
    out = text.replace("\\", "\\\\")
    out = out.replace(":", "\\:")
    out = out.replace("'", "\\'")
    out = out.replace("%", "\\%")
    return out


def make_filter(cues: list[dict]) -> str:
    """每个 cue 一个 drawtext，enable='between(t,start,end)'，用 :fontfile msyh.ttc。"""
    filters: list[str] = []
    for cue in cues:
        line = "".join(w["text"] for w in cue["words"])
        if not line.strip():
            continue
        # drawtext 默认有持续时长；用 enable 限制
        s = round(cue["start"], 3)
        e = round(cue["end"], 3)
        # box=1 boxcolor=black@0.5 boxborderw=8 → 黑色半透明描边背景
        text_arg = (
            "fontfile=" + FONT_PATH
            + ":text='" + _escape_drawtext(line) + "'"
            + ":fontsize=" + str(FONT_SIZE)
            + ":fontcolor=white@0xFF"
            + ":x=(w-text_w)/2"
            + ":y=h-" + str(MARGIN_V) + "-text_h"
            + ":box=1:boxcolor=black@0x80:boxborderw=8"
            + ":enable='between(t," + str(s) + "," + str(e) + ")'"
        )
        filters.append("drawtext=" + text_arg)
    return ",".join(filters)


def main() -> int:
    with META_PATH.open(encoding="utf-8") as f:
        meta = json.load(f)
    cues = [c for c in meta["cues"] if c["start"] < BURN_DUR]
    print("cues: " + str(len(cues)))
    filter_str = make_filter(cues)
    print("filter length: " + str(len(filter_str)))
    args = [
        "ffmpeg", "-y",
        "-i", str(SRC_MP4),
        "-vf", filter_str,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "132k",
        "-t", str(BURN_DUR),
        str(OUT_MP4),
    ]
    # 写 cmd 到文件查看（避免 shlex 转义错）
    cmd_file = ROOT / "work/v4_burn_v5_cmd.txt"
    cmd_file.write_text(" ".join(a if " " not in a else "'" + a + "'" for a in args), encoding="utf-8")
    r = subprocess.run(args, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print("STDERR tail:")
        print(r.stderr[-3000:])
        return r.returncode
    if OUT_MP4.is_file():
        print("output: " + str(OUT_MP4.stat().st_size) + " bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
