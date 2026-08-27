"""P0 v4 v3: 用 PIL PNG 字幕 + ffmpeg overlay chain 烧录 MP4。

ffmpeg filter_complex 链式 overlay，每个 PNG 用 enable='between(t,start,end)'。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path("C:/Users/zeng/Desktop/video")
PNG_DIR = ROOT / "work/v4_subtitle_png"
META_PATH = ROOT / "data/video_edits/r8-v4-sample-30s.meta.json"
SRC_MP4 = ROOT / "data/avatar_results/avatar-2704b95149a3.mp4"
OUT_MP4 = ROOT / "work/r8-v4-sample-30s.mp4"
BURN_DUR = 30


def main() -> int:
    with META_PATH.open(encoding="utf-8") as f:
        meta = json.load(f)
    cues = [c for c in meta["cues"] if c["start"] < BURN_DUR]
    print("cues: " + str(len(cues)))
    if not cues:
        return 1
    # 拼 ffmpeg 命令
    args = ["ffmpeg", "-y", "-i", str(SRC_MP4)]
    for i, cue in enumerate(cues):
        png = PNG_DIR / ("cue_%03d.png" % i)
        if not png.is_file():
            continue
        args.extend(["-i", str(png)])
    # filter_complex
    n_inputs = len(cues) + 1
    parts = []
    prev_label = "0:v"
    for i, cue in enumerate(cues):
        idx = 1 + i
        new_label = "v" + str(i)
        cond = "enable='between(t,{s},{e})'".format(
            s=round(cue["start"], 3),
            e=round(cue["end"], 3),
        )
        parts.append(
            "[{prev}][{idx}:v]overlay={cond}:x=0:y=0[{new}]".format(
                prev=prev_label, idx=idx, cond=cond, new=new_label
            )
        )
        prev_label = new_label
    filter_str = ";\n".join(parts) + ";\n" + "[{last}]null[out]".format(
        last=prev_label
    )
    args.extend(["-filter_complex", filter_str, "-map", "[out]", "-map", "0:a?",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                 "-c:a", "aac", "-b:a", "132k", "-t", str(BURN_DUR),
                 str(OUT_MP4)])
    # 写到文件避免命令行太长
    cmd_file = ROOT / "work/v4_burn_cmd.txt"
    cmd_file.write_text(" ".join(args), encoding="utf-8")
    print("cmd lines: " + str(len(args)))
    # 执行
    r = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print("STDERR tail:")
        print(r.stderr[-2000:])
        return r.returncode
    if OUT_MP4.is_file():
        print("output: " + str(OUT_MP4.stat().st_size) + " bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
