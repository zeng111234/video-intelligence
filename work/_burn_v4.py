"""P0 v4 v4: 用 ffmpeg filter_complex 多 PNG overlay 链（修正 v3）。"""
import json
import subprocess
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
    cues = [c for c in cues if (PNG_DIR / ("cue_%03d.png" % cues.index(c))).is_file()]
    print("cues: " + str(len(cues)))
    if not cues:
        return 1
    args = ["ffmpeg", "-y", "-i", str(SRC_MP4)]
    for i, cue in enumerate(cues):
        png = PNG_DIR / ("cue_%03d.png" % i)
        if not png.is_file():
            continue
        args.extend(["-i", str(png)])
    # filter_complex: 链式 overlay，每个 PNG 用 enable
    n_png = len(cues)
    parts = []
    prev = "0:v"
    for i, cue in enumerate(cues):
        idx = i + 1
        out = "v" + str(i)
        cond = "enable='between(t,{s},{e})'".format(
            s=round(cue["start"], 3),
            e=round(cue["end"], 3),
        )
        parts.append(
            "[{prev}][{idx}:v]overlay={cond}[{out}]".format(
                prev=prev, idx=idx, cond=cond, out=out
            )
        )
        prev = out
    parts.append("[" + prev + "]null[out]")
    filter_str = ";".join(parts)
    args.extend(["-filter_complex", filter_str, "-map", "[out]", "-map", "0:a?",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                 "-c:a", "aac", "-b:a", "132k", "-t", str(BURN_DUR),
                 str(OUT_MP4)])
    # print first 500 chars
    print(filter_str[:600])
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
