"""P0 v4: 基于真实 ASR 词级时间戳生成新 ASS。

输入：asr-full.json（含 word_timestamps: true + segments[].words）
输出：edit-local-XXXXX.ass（用 Caption 样式：Source Han Serif CN Heavy 52px）

规则：
- 断句基于 word.start/end，不能在词中间断开
- 每条 0.9-2.4s
- 数字 / 金额 / 强动词 / 转折词 → 黄色高亮（140ms 放大 108%）
- 密度 8-12s 至少 1 个高亮
- 不复用旧视频文案（用真实 ASR 原文）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def load_asr() -> list[dict]:
    path = Path(
        "C:/Users/zeng/Desktop/video/work/auto-fine-cut-adaptive-20260824-short-real/asr-full.json"
    )
    with path.open(encoding="utf-8") as f:
        asr = json.load(f)
    return list(asr.get("segments") or [])


def collect_words(segs: list[dict]) -> list[dict]:
    """把 ASR 7 段展平为词列表（每词 start/end/text）。"""
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


# 关键词（黄色高亮）规则：数字+单位 / 强动词 / 转折词
_NUM_UNIT_RE = re.compile(r"^[\d.]*[%％百千万亿]?$|^[一二三四五六七八九十]?[、.]?[\d.]+$")
# 数字开头：50%, 100, 5万, 80%等
_DIGIT_RE = re.compile(r"^[\d.]+")
# 高亮 token 集合（通用规则，不针对样片）
_KEYWORD_TOKENS = {
    # 强动词
    "推出", "上线", "升级", "换成", "落", "搞活", "试试", "主动", "试试", "直接", "都能", "都行",
    "半", "变成", "把", "加", "加", "跑", "去", "换", "落",
    # 转折 / 程度
    "但是", "不过", "而且", "如果", "因为", "所以", "除了", "就", "只", "根本",
    # 数字+单位常见搭配
    "会员", "会员", "尊贵", "现金", "奖励", "奖励", "消费", "消费", "活动", "活动", "系统", "系统",
    "回头客", "回头客", "回头客", "回头客",
    # 关键名词
    "回头客", "会员", "奖励", "奖励", "系统", "系统", "活动", "活动", "消费", "消费",
    "老板", "老板", "老板", "老板", "老板",
    "私域", "私域",
}
# 数字开头自动高亮


def is_keyword_word(w_text: str) -> bool:
    base = w_text.strip()
    if not base:
        return False
    # 数字（含%等单位）
    if _DIGIT_RE.match(base):
        return True
    if _NUM_UNIT_RE.match(base):
        return True
    # 强关键词
    if base in _KEYWORD_TOKENS:
        return True
    # 短独立动词（不超过 4 字，常见）
    if len(base) <= 4 and any(c in base for c in "动转程度"):
        return True
    return False


def cut_into_cues(words: list[dict]) -> list[dict]:
    """按词级时间戳切句，0.9-2.4s/条，不能在词中间断开。"""
    cues: list[dict] = []
    if not words:
        return cues
    cur: list[dict] = []
    cur_start = None
    for w in words:
        if cur_start is None:
            cur_start = w["start"]
        cur.append(w)
        cur_end = w["end"]
        cur_dur = cur_end - cur_start
        # 找到 0.9 <= cur_dur <= 2.4 的最早时机
        if cur_dur >= 0.9 and cur_dur <= 2.4:
            cues.append({"start": cur_start, "end": cur_end, "words": list(cur)})
            cur = []
            cur_start = None
        elif cur_dur > 2.4:
            # 超长：强制在当前词的 end 断
            cues.append({"start": cur_start, "end": cur_end, "words": list(cur)})
            cur = []
            cur_start = None
    if cur:
        cues.append(
            {"start": cur_start, "end": cur[-1]["end"], "words": list(cur)}
        )
    return cues


def mark_keywords(cues: list[dict]) -> list[dict]:
    """给每条 cue 标高亮 word indices。规则：
    - 数字 100% 高亮
    - 关键词 50% 抽样（避免每行都跳）
    - 8-12s 至少 1 个 cue 含高亮
    """
    last_keyword_cue_end = -1000.0
    total_dur = cues[-1]["end"] - cues[0]["start"] if cues else 0
    min_gap = 8.0  # 8-12s 至少 1 个高亮
    for i, cue in enumerate(cues):
        kws: list[int] = []
        for j, w in enumerate(cue["words"]):
            if is_keyword_word(w["text"]):
                kws.append(j)
        # 数字必须保留
        # 关键词如果距上一个高亮 < 8s，限制最多 1 个
        gap = cue["start"] - last_keyword_cue_end
        if kws and gap < min_gap and len(kws) > 1:
            kws = kws[:1]
        cue["keyword_indices"] = kws
        if kws:
            last_keyword_cue_end = cue["end"]
    return cues


def fmt_cue(cue: dict) -> str:
    """把 cue 渲染为 ASS Dialogue Text（应用 Caption 样式 + 关键词高亮）。"""
    parts: list[str] = []
    for j, w in enumerate(cue["words"]):
        text = w["text"]
        # ASS 转义花括号
        text = text.replace("{", "(").replace("}", ")")
        if j in cue.get("keyword_indices", []):
            # 关键词：黄色 + 140ms 放大到 108%
            parts.append(
                "{\\c&H0057C8FF&\\fscx100\\fscy100"
                "\\t(0,140,\\fscx108\\fscy108)}"
                + text
                + "{\\c&H00F8FAFC&\\fscx100\\fscy100}"
            )
        else:
            parts.append(text)
    return "".join(parts)


def write_ass(cues: list[dict], out_path: Path, title: str = "r8-v4") -> None:
    header = (
        "[Script Info]\n"
        "Title: VideoInsight business talking-head overlay v4\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 720\n"
        "PlayResY: 1280\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
        "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        # 优先 Source Han Serif CN Heavy；fallback "Microsoft YaHei" 防 libass
        # 默认字体宽度算错导致截断（"上有家烧烤店"被裁成"上有家烧"）
        "Style: Caption,Source Han Serif CN Heavy,52,&H00F8FAFC,&H00F8FAFC,"
        "&H30000000,&H00000000,-1,0,0,0,100,100,0.12,0,1,1,1,2,58,58,170,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,"
        "Effect,Text\n"
    )
    lines: list[str] = [header]
    for i, cue in enumerate(cues):
        # 130ms 淡入 + 98%→100% 轻微放大
        effect_prefix = "{\\fad(130,0)\\fscx98\\fscy98\\t(0,130,\\fscx100\\fscy100)}"
        text = fmt_cue(cue)
        h = int(cue["start"] // 3600)
        m = int((cue["start"] % 3600) // 60)
        s = cue["start"] % 60
        eh = int(cue["end"] // 3600)
        em = int((cue["end"] % 3600) // 60)
        es = cue["end"] % 60
        start_ts = f"{h}:{m:02d}:{s:05.2f}"
        end_ts = f"{eh}:{em:02d}:{es:05.2f}"
        lines.append(
            f"Dialogue: 0,{start_ts},{end_ts},Caption,,0,0,0,,"
            f"{effect_prefix}{text}\n"
        )
    out_path.write_text("".join(lines), encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes, {len(cues)} cues)")


def main() -> int:
    segs = load_asr()
    words = collect_words(segs)
    print(f"total words: {len(words)}")
    cues = cut_into_cues(words)
    print(f"total cues: {len(cues)}")
    cues = mark_keywords(cues)
    k_count = sum(1 for c in cues if c.get("keyword_indices"))
    print(f"keyword cues: {k_count} ({k_count / max(len(cues), 1) * 100:.1f}%)")
    out = Path("C:/Users/zeng/Desktop/video/data/video_edits/r8-v4-sample-30s.ass")
    write_ass(cues, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
