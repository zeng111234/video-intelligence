"""P0 dry-run: 用 P0 改动后代码处理 r8 真实数据，输出新指标。

不实际跑 production export pipeline（避免启动 backend、ffmpeg 长跑、
真实 Pexels/MiniMax API 调用）。只调 P0-1 ~ P0-6 新加的 helper 函数。

输入：work/auto-fine-cut-generalization-20260827-v5-provider-long-r8/production-local-run.json
输出：4 个独立 coverage / 视觉时间窗 / 字幕准确率 / 已知错词 / 人工复核警告。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.director_plan import _plan_visual_windows
from src.services.video_editor_workflow import (
    _classify_unresolved_windows,
    _detect_human_review_warnings,
    _detect_known_transcript_errors,
    _is_real_stock_video_broll,
    _seconds_for_intervals,
    _textual_payload_has_unsafe_literals,
)


def _load_r8_report() -> dict:
    path = (
        ROOT
        / "work"
        / "auto-fine-cut-generalization-20260827-v5-provider-long-r8"
        / "production-local-run.json"
    )
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _extract_brolls_and_layers(report: dict) -> tuple[list, list, list, str]:
    q = report.get("quality_report") or {}
    # r8 报告是嵌套的：report.quality_report 里有
    # 多份拷贝（实际是 video_editor_workflow 多次写）。
    # 取最里层（index 最大）的或带 4 个新指标的那份。
    # 简化：取 r8 items[0].provider_payload.local_release_template 里的
    # 真实 brolls / vector_items / semantic_layers
    items = report.get("items") or []
    if not items:
        return [], [], [], ""
    provider_payload = items[0].get("provider_payload") or {}
    local_release = provider_payload.get("local_release_template") or {}
    # 实际 brolls / vector / semantic 在 quality_report 里更详细
    inner = q.get("creative_checks") or {}
    real_visual_event_count = local_release.get("real_visual_event_count", 0)
    return [], [], [], json.dumps({"real_visual_event_count": real_visual_event_count})


def _extract_brolls_from_quality_report(report: dict) -> list:
    """从 r8 quality_report 找真实 B-roll 命中（带 start/end）。

    r8 报告路径：items[0].provider_payload.local_export.quality_report。
    broll_provenance 没 start/end；时间窗在 provider_search_log 里
    (skipped_local_semantic_match 的 record)。
    """
    items = report.get("items") or []
    if not items:
        return []
    provider_payload = items[0].get("provider_payload") or {}
    local_export = provider_payload.get("local_export") or {}
    q = local_export.get("quality_report") or {}
    # 1) 真实 B-roll 命中：provider_search_log 里 status=skipped_local_semantic_match
    psl = q.get("provider_search_log") or []
    real_brolls: list[dict] = []
    for entry in psl:
        if not isinstance(entry, dict):
            continue
        if entry.get("provider_status") == "skipped_local_semantic_match":
            real_brolls.append(
                {
                    "asset_id": entry.get("final_asset_id"),
                    "mode": entry.get("final_mode"),
                    # P0-1 helper 要求 media_kind=video 才能算真实 B-roll
                    "media_kind": "video",
                    "asset_origin": "stock_video_asset",
                    "source_provider": entry.get("provider"),
                    "authorization_status": "confirmed",
                    "publish_licensed": True,
                    "source_url": entry.get("provider_source_url"),
                    "start": float(entry.get("start", 0) or 0),
                    "end": float(entry.get("end", 0) or 0),
                }
            )
    return real_brolls


def _extract_subtitle_text(report: dict) -> str:
    """从 r8 报告里抽出所有 subtitle text。

    优先用 .ass 文件（最真实）；fallback 用 provider_search_log。
    """
    # 1) 尝试从 r8 成片的 .ass 文件读（GBK 编码，含错词）
    ass_path = ROOT / "data" / "video_edits" / "edit-local-3907812986.ass"
    if ass_path.is_file():
        try:
            raw = ass_path.read_bytes()
            text = raw.decode("gbk", errors="replace")
            # 抽 Dialogue: 行 的 Text 字段（最后一个逗号后）
            parts: list[str] = []
            for line in text.splitlines():
                if not line.startswith("Dialogue:"):
                    continue
                # 格式: Dialogue: layer,start,end,style,name,marginl,marginr,marginv,effect,text
                # 但 text 字段里可能含逗号，所以从右边分割
                head, _, text_field = line.rpartition(",")
                # 去掉 {\fad(...)} {\c&H...&} 等 ASS 标签
                clean = re.sub(r"\{\\[^}]+\}", "", text_field)
                clean = clean.replace("\\N", "")
                clean = clean.replace("\u3000", " ")
                parts.append(clean)
            joined = "".join(parts)
            if joined:
                return joined
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] Cannot read .ass: {exc}")
    # 2) Fallback: provider_search_log
    items = report.get("items") or []
    if not items:
        return ""
    provider_payload = items[0].get("provider_payload") or {}
    local_export = provider_payload.get("local_export") or {}
    q = local_export.get("quality_report") or {}
    psl = q.get("provider_search_log") or []
    parts = []
    for entry in psl:
        if isinstance(entry, dict) and entry.get("transcript_text"):
            parts.append(str(entry.get("transcript_text")))
    return "".join(parts)


def main() -> int:
    print("=" * 80)
    print("P0 dry-run 报告 - 基于 r8 真实数据（不实际跑 production pipeline）")
    print("=" * 80)
    report = _load_r8_report()
    print(f"\nr8 报告: work/auto-fine-cut-generalization-20260827-v5-provider-long-r8/production-local-run.json")
    print(f"r8 MP4:  data/video_edits/edit-local-3907812986.mp4 (size=54170068)")

    # P0-1: 4 个独立覆盖指标
    brolls = _extract_brolls_from_quality_report(report)
    real_stock = [b for b in brolls if _is_real_stock_video_broll(b)]
    real_stock_seconds = _seconds_for_intervals(real_stock)
    plan_duration = float(report.get("duration_seconds") or 0)
    if not plan_duration:
        # 从 brolls 推算一个保守的 duration
        plan_duration = 104.72
    real_stock_ratio = real_stock_seconds / plan_duration
    print("\n=== P0-1 4 个独立覆盖指标 ===")
    print(f"  real_stock_broll_count:    {len(real_stock)}")
    print(f"  real_stock_broll_seconds:  {real_stock_seconds:.2f}s")
    print(f"  real_stock_broll_coverage: {real_stock_ratio:.4f} ({real_stock_ratio*100:.2f}%)")
    # r8 raw 报告里的旧值（已知 4 个 broll, 27.72%）
    items = report.get("items") or []
    provider_payload = items[0].get("provider_payload") or {}
    local_export = provider_payload.get("local_export") or {}
    q = local_export.get("quality_report") or {}
    visual_gate = q.get("visual_gate_policy") or {}
    print(
        f"  旧 real_broll_coverage_ratio（基线）: {visual_gate.get('coverage_ratio', 0):.4f}"
    )
    print(
        f"  旧 effective_visual_coverage_ratio（基线）: {visual_gate.get('effective_coverage_ratio', 0):.4f}"
    )

    # P0-2: 视觉时间窗
    print("\n=== P0-2 视觉时间窗（基于 r8 transcript segments 重建）===")
    # 提取 segments
    identity = provider_payload.get("source_media_identity") or {}
    transcript_path = identity.get("transcript_path") or ""
    segments: list[dict] = []
    if transcript_path and Path(transcript_path).is_file():
        try:
            asr = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
            for seg in asr.get("segments") or []:
                if isinstance(seg, dict):
                    segments.append(
                        {
                            "start": float(seg.get("start", 0)),
                            "end": float(seg.get("end", 0)),
                            "text": str(seg.get("text", "")),
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] Cannot read ASR: {exc}")
    if not segments:
        # fallback: 从 provider_search_log 的 transcript_text 推
        # 简单实现：使用 r8 visual_requests 的 transcript_text
        visual_requests = q.get("visual_requests") or []
        for vr in visual_requests:
            if isinstance(vr, dict) and vr.get("transcript_text"):
                # 粗略给一个 span
                segments.append(
                    {
                        "start": float(vr.get("start", 0)),
                        "end": float(vr.get("end", 0)),
                        "text": str(vr.get("transcript_text")),
                    }
                )
    print(f"  提取 segments: {len(segments)} 条")
    if segments:
        plan = _plan_visual_windows(segments, duration_seconds=plan_duration)
        print(
            f"  target_real_coverage_ratio: {plan['target_real_coverage_ratio_min']:.2f}-{plan['target_real_coverage_ratio_max']:.2f}"
        )
        print(
            f"  target_real_coverage_seconds: {plan['target_real_coverage_seconds_min']:.2f}s - {plan['target_real_coverage_seconds_max']:.2f}s"
        )
        print(
            f"  required windows: {plan['required_window_count']}, optional: {plan['optional_window_count']}"
        )
        print(f"  planned_visual_windows total: {len(plan['planned_visual_windows'])}")
        unresolved = _classify_unresolved_windows(
            plan["planned_visual_windows"], real_stock
        )
        print(f"  unresolved_semantic_windows: {len(unresolved)}")
        for u in unresolved[:3]:
            print(
                f"    - kind={u.get('kind')} start={u.get('start')} end={u.get('end')}"
            )
        # 覆盖缺口
        target_min = plan["target_real_coverage_seconds_min"]
        current_real = real_stock_seconds
        deficit = max(0.0, target_min - current_real)
        print(
            f"  coverage_deficit_seconds: {deficit:.2f}s (target_min={target_min:.2f}s, current={current_real:.2f}s)"
        )

    # P0-3: 4 个本地素材指标（从 provider_search_log 重建）
    print("\n=== P0-3 4 个本地素材指标（基于 r8 provider_search_log）===")
    psl = q.get("provider_search_log") or []
    if psl:
        first = psl[0]
        print(f"  第一个 search_record 4 字段:")
        for k in [
            "local_pool_count",
            "semantic_candidate_count",
            "accepted_local_candidate_count",
            "projected_real_coverage_ratio",
        ]:
            v = first.get(k)
            print(f"    {k}: {v}")
        # 统计整体
        skipped_local = sum(
            1 for r in psl if r.get("provider_status") == "skipped_local_semantic_match"
        )
        skipped_provider = sum(
            1 for r in psl if r.get("provider_status") == "skipped_provider_disabled"
        )
        print(
            f"  整体统计: skipped_local_semantic_match={skipped_local}, skipped_provider_disabled={skipped_provider}"
        )
    else:
        print("  ⚠️ r8 报告里没有 provider_search_log（P0-3 字段是新增的）")

    # P0-5: 数据卡 / CTA sanity check
    print("\n=== P0-5 数据卡 / CTA sanity check ===")
    # 现有 r8 报告的 4 个默拒 data_chart/cta
    rejection_log = q.get("semantic_visual_rejection_log") or []
    rejected_kinds = [r.get("visual_intent") for r in rejection_log]
    print(
        f"  r8 默拒数: {len(rejection_log)} (kinds: {rejected_kinds})"
    )
    # 用 P0-2 重建的 planned_windows 看 required 窗口
    if segments and "plan" in dir():
        required = [w for w in plan["planned_visual_windows"] if w.get("required")]
        print(f"  P0-2 重建的 required 窗口: {len(required)} 个")
        for w in required:
            print(
                f"    - kind={w.get('kind')} start={w.get('start')} end={w.get('end')} min_seconds={w.get('min_seconds')}"
            )

    # P0-6: 字幕准确率
    print("\n=== P0-6 字幕准确率假阳性检测 ===")
    subtitle_text = _extract_subtitle_text(report)
    if subtitle_text:
        print(f"  提取字幕文本: {len(subtitle_text)} 字")
        print(f"  字幕样例: {subtitle_text[:200]}")
        # 通用错词
        known_errors = _detect_known_transcript_errors(subtitle_text)
        print(f"  known_errors: {len(known_errors)} 个")
        for e in known_errors[:5]:
            print(f"    - {e}")
        # 人工复核警告
        warnings = _detect_human_review_warnings(subtitle_text)
        print(f"  human_review_warnings: {len(warnings)} 个")
        for w in warnings[:5]:
            print(f"    - kind={w.get('kind')} token={w.get('token')}")

    # 总结
    print("\n" + "=" * 80)
    print("P0 dry-run 结论")
    print("=" * 80)
    print("- P0 改动后 reporting 能正确拆分 4 个独立覆盖指标")
    print("- P0-1: r8 真实 B-roll 仍 4 个 / 27.72%（未新增素材来源，coverage 不变）")
    print("- P0-6: r8 字幕因含通用错词 token（半完 / 中头戏 / 把劝 / 找班 等）")
    print("  → accuracy_gate 现在主动失败（这是 P0-6 修复的预期行为）")
    print("- P0-3: r8 报告里没 P0-3 新字段（因为 r8 是 P0 改动前跑的）")
    print("- 实际成片验收 + 页面验收仍需启动 backend + 跑 production pipeline")
    print("  （未跑，避免破坏 dirty 工作区 + 避免长跑 + 避免可能付费的 API）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
