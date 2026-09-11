/** 云端轻量智能剪辑工作台。 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Input,
  InputNumber,
  List,
  Modal,
  Radio,
  Segmented,
  Select,
  Slider,
  Space,
  Spin,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from "antd";
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudOutlined,
  DownloadOutlined,
  EditOutlined,
  FullscreenOutlined,
  HistoryOutlined,
  MutedOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SoundOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import * as videoEditorApi from "../api/client";
import { cnyToCredits, handleCreditsError } from "../utils/credits";
import type {
  TranscriptSegment,
  VideoCapabilitiesResponse,
  VideoEditorBatch,
  VideoEditorBatchItem,
  VideoEditorBrollPlacement,
  VideoEditorBgmAsset,
  VideoEditorSource,
  VideoEditorOverlayPreview,
  VideoEditorVisualAsset,
  VideoEditorTimeRange,
  VideoEditorVisualSpec,
} from "../api/types";

const { Title, Text } = Typography;

const BGM_SOURCE_LABELS: Record<string, string> = {
  manual: "本地授权素材",
  freepd: "FreePD 公共领域",
  pixabay: "Pixabay",
  light_factory: "光厂",
  bodian: "波点商用库",
};
const getBgmSourceLabel = (asset: VideoEditorBgmAsset) =>
  asset.generated && asset.source_provider === "manual"
    ? "本地原创合成"
    : BGM_SOURCE_LABELS[asset.source_provider] || "授权素材";
const formatBgmOptionLabel = (asset: VideoEditorBgmAsset) => (
  `${asset.voiceover_category || asset.mood} · ${asset.title} · ${
    getBgmSourceLabel(asset)
  }`
);

const CAPTION_BREAK_BEFORE_TOKENS = [
  "因为", "所以", "但是", "不过", "而且", "然后", "如果", "虽然",
  "为了", "其实", "基本", "通常", "一般", "几乎", "结果", "现在", "大量", "少量", "很多", "有些", "倒闭",
  "取代", "替代", "增长", "减少", "出现", "成为", "变成", "开始", "进入",
  "通过", "面对", "发现", "需要", "可以", "不能", "没有", "不是", "就是", "已经",
  "正在", "也是", "仍然", "被", "把", "让", "比", "待",
];
const CAPTION_BREAK_AFTER_TOKENS = [
  "的话", "以后", "之前", "之后", "时候", "一来", "说到底",
];
const CAPTION_BAD_LINE_ENDINGS = [
  "的", "地", "得", "了", "着", "过", "和", "与", "及", "或", "跟", "比",
  "把", "被", "让", "给", "向", "对", "在", "从", "为", "还", "就", "才",
  "都", "又", "再", "更", "最", "很", "太", "也", "挺", "正", "将", "要", "会",
  "能", "可", "无", "不", "没", "未", "非", "主动", "自动", "直接", "立刻",
  "马上", "基本", "通常", "一般", "几乎", "自然", "通过", "想", "用", "办",
  "拿", "加", "送", "发", "搞", "打", "第", "每", "各", "这", "那",
  "此", "其", "一", "两", "几", "多", "个", "位", "名", "家", "户", "只",
  "条", "件", "张", "种", "次", "套", "台", "份", "部", "本", "辆", "斤",
  "米", "块", "元",
];
const CAPTION_BAD_LINE_STARTS = [
  "的", "地", "得", "了", "着", "过", "们", "吗", "呢", "吧", "啊", "呀", "嘛",
  "个", "位", "名", "家", "户", "只", "条", "件", "张", "种", "次", "套",
  "台", "份", "部", "本", "辆",
];
const CAPTION_NUMERIC_ATOM_RE = /\d+(?:[.,]\d+)*(?:[%％元块万亿千百十公里米厘米分钟秒个家人套条次岁年月天斤倍折号点]+)?/g;
type OutputProfile = "720p" | "1080p";
type PreviewMode = "original" | "plan" | "output";

interface CloudCapabilities extends VideoCapabilitiesResponse {
  provider_mode?: string;
  renderer_mode?: string;
  live_ready?: boolean;
  missing_configuration?: string[];
  is_mock?: boolean;
  pricing_version?: string;
}

interface CostLine {
  code?: string;
  key?: string;
  component?: string;
  name?: string;
  label?: string;
  provider?: string;
  amount_cny?: number | string;
  estimated_cost_cny?: number | string;
  cost_cny?: number | string;
}

interface CostQuote {
  quote_id: string;
  expires_at: string;
  currency?: string;
  pricing_version?: string;
  price_version?: string;
  line_items?: CostLine[];
  items?: CostLine[];
  breakdown?: CostLine[] | Record<string, number>;
  estimated_total_cny?: number;
  estimated_upper_bound_cny?: number;
  max_cost_cny?: number;
  total_cny?: number;
  estimated_total?: number | string;
  estimated_max?: number | string;
  provider_ready?: boolean;
  live_ready?: boolean;
  provider_mode?: string;
  renderer_mode?: string;
  is_mock?: boolean;
  missing_configuration?: string[];
  blocked_reasons?: string[];
  blocking_reasons?: string[];
  exclusions?: string[];
}

interface EditPlanStep {
  id: string;
  kind: string;
  label: string;
  reason: string;
  enabled: boolean;
  estimated_removed_seconds: number;
  params: Record<string, unknown>;
}

type CloudBatchItem = VideoEditorBatchItem;
type CloudBatch = VideoEditorBatch;

const DEFAULT_VISUAL_SPEC: VideoEditorVisualSpec = {
  style_id: "business_talking_head_v9.1-smart-opening-clean-hook-speed-1.15",
  playback_rate: 1.15,
  canvas: { width: 720, height: 1280, pixel_aspect_ratio: "1:1" },
  title: {
    visible_seconds: 2.5,
    fade_in_ms: 0,
    fade_out_ms: 0,
    max_lines: 1,
    max_chars_per_line: 14,
    font_family: "Source Han Serif CN Heavy",
    render_mode: "png_watermark",
    font_size: 44,
    line_height: 1.1,
    safe_top: 84,
    safe_left: 30,
    asset_width: 660,
    asset_height: 72,
    outline_width: 1,
    shadow: 1,
    color: "#FFFFFF",
  },
  accent: { color: "transparent", width: 0, height: 0, gap: 0 },
  subtitle: {
    max_lines: 1,
    max_chars_per_line: 11,
    font_size: 52,
    safe_bottom: 170,
    font_family: "Smiley Sans",
    font_style: "oblique",
    outline_width: 4,
    shadow: 1,
    color: "#F8FAFC",
    emphasis_color: "#FFE16A",
  },
};

const PROFILE_META: Record<OutputProfile, {
  label: string;
  resolution: string;
  fps: number;
  bitrate: string;
  exampleCost: number;
}> = {
  "720p": {
    label: "720P 轻量",
    resolution: "720x1280",
    fps: 30,
    bitrate: "2.5M",
    exampleCost: 0.048,
  },
  "1080p": {
    label: "1080P 清晰",
    resolution: "1080x1920",
    fps: 30,
    bitrate: "5M",
    exampleCost: 0.080,
  },
};

const RESULT_SUMMARY_ITEMS = [
  {
    key: "silence_trim",
    label: "去除明显停顿",
    reason: "识别并移除较长空白，让内容更紧凑。",
  },
  {
    key: "subtitle",
    label: "使用已确认字幕",
    reason: "只使用复核后的字幕，准确匹配画面。",
  },
  {
    key: "title",
    label: "添加标题",
    reason: "使用确认后的标题，突出内容重点。",
  },
  {
    key: "background_music",
    label: "添加授权配乐",
    reason: "只使用已经确认权利的背景音乐。",
  },
];

const STATUS_META: Record<string, { color: string; label: string; progress: number }> = {
  queued: { color: "default", label: "等待分析", progress: 5 },
  analyzing: { color: "processing", label: "云端分析中", progress: 35 },
  local_analyzing: { color: "processing", label: "本机分析中", progress: 35 },
  awaiting_subtitle_review: { color: "gold", label: "待人工复核", progress: 58 },
  ready_to_render: { color: "processing", label: "准备渲染", progress: 66 },
  rendering: { color: "processing", label: "正式成片生成中", progress: 82 },
  awaiting_output_confirmation: { color: "blue", label: "待确认成片", progress: 100 },
  ready_to_publish: { color: "success", label: "成片已确认", progress: 100 },
  sandbox_completed: { color: "default", label: "体验方案已保存", progress: 100 },
  configuration_required: { color: "warning", label: "云端出片待开通", progress: 0 },
  outcome_unknown: { color: "warning", label: "供应商结果待查", progress: 72 },
  failed: { color: "error", label: "处理失败", progress: 0 },
  interrupted: { color: "warning", label: "任务中断", progress: 0 },
};

const STEP_KIND_ALIASES: Record<string, string> = {
  silence_trim: "silence_trim",
  trim_silence: "silence_trim",
  resize: "resize",
  vertical_fit: "resize",
  subtitle: "subtitle",
  subtitles: "subtitle",
  title: "title",
  background_music: "background_music",
  bgm: "background_music",
  volume_norm: "volume_norm",
  audio_mix: "volume_norm",
};

// The review drawer uses friendly UI step ids, while the API accepts the
// server's EditStepKind values.  Keep this conversion at the boundary so a
// click on “确认复核并生成” cannot fail on an internal display id.
const REVIEW_STEP_KIND_ALIASES: Record<string, string> = {
  "silence-trim": "trim_silence",
  silence_trim: "trim_silence",
  trim_silence: "trim_silence",
  "resize-vertical": "vertical_fit",
  resize: "vertical_fit",
  vertical_fit: "vertical_fit",
  "subtitle-approved": "subtitles",
  subtitle: "subtitles",
  subtitles: "subtitles",
  "title-overlay": "title",
  title: "title",
  "authorized-bgm": "bgm",
  background_music: "bgm",
  bgm: "bgm",
  "volume-normalize": "audio_mix",
  volume_norm: "audio_mix",
  audio_mix: "audio_mix",
  smart_opening: "smart_opening",
};

function reviewStepKind(value: unknown): string | null {
  const raw = String(value || "").trim();
  return REVIEW_STEP_KIND_ALIASES[raw] || null;
}

const DEFAULT_PLAN: EditPlanStep[] = [
  {
    id: "silence-trim",
    kind: "silence_trim",
    label: "处理长停顿",
    reason: "只建议不少于 1.5 秒的无语音区间，两端各保留 0.35 秒。",
    enabled: true,
    estimated_removed_seconds: 0,
    params: {},
  },
  {
    id: "resize-vertical",
    kind: "resize",
    label: "适配 9:16",
    reason: "按所选 720P 或 1080P 档位统一映射帧率与码率。",
    enabled: true,
    estimated_removed_seconds: 0,
    params: {},
  },
  {
    id: "subtitle-approved",
    kind: "subtitle",
    label: "烧录人工确认字幕",
    reason: "每条素材只识别一次，未完成复核不会提交正式渲染。",
    enabled: true,
    estimated_removed_seconds: 0,
    params: {},
  },
  {
    id: "title-overlay",
    kind: "title",
    label: "添加标题",
    reason: "标题候选可修改，不删除或改写任何有人声内容。",
    enabled: true,
    estimated_removed_seconds: 0,
    params: {},
  },
  {
    id: "authorized-bgm",
    kind: "background_music",
    label: "添加授权配乐",
    reason: "只有确认权利的音乐才会进入正式渲染；未选择时保持原声。",
    enabled: true,
    estimated_removed_seconds: 0,
    params: {},
  },
  {
    id: "volume-normalize",
    kind: "volume_norm",
    label: "统一音量",
    reason: "只调整响度与已授权 BGM 音量，不改变人声内容。",
    enabled: true,
    estimated_removed_seconds: 0,
    params: {},
  },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formatCost(value: number) {
  return `${cnyToCredits(value)} 积分`;
}

function statusTag(status: string) {
  const meta = STATUS_META[status] || { color: "default", label: status, progress: 0 };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function quoteLines(quote: CostQuote | null): Array<{ key: string; label: string; amount: number }> {
  if (!quote) return [];
  const source = quote.line_items || quote.items;
  if (Array.isArray(source)) {
    return source.map((item, index) => ({
      key: item.code || item.key || item.component || `${item.label || item.name || "cost"}-${index}`,
      label: item.label || item.name || ({
        asr: "字幕识别",
        speech_recognition: "字幕识别",
        planning: "剪辑方案",
        edit_planning: "剪辑方案",
        render: "成片制作",
        cloud_render: "成片制作",
        brand_title_overlay: "品牌标题排版",
      }[item.component || ""] || item.provider || "制作服务"),
      amount: asNumber(item.amount_cny ?? item.estimated_cost_cny ?? item.cost_cny),
    }));
  }
  if (Array.isArray(quote.breakdown)) {
    return quote.breakdown.map((item, index) => ({
      key: item.code || item.key || `${item.label || item.name || "cost"}-${index}`,
      label: item.label || item.name || item.provider || "云服务",
      amount: asNumber(item.amount_cny ?? item.estimated_cost_cny ?? item.cost_cny),
    }));
  }
  if (isRecord(quote.breakdown)) {
    return Object.entries(quote.breakdown).map(([key, value]) => ({
      key,
      label: key,
      amount: asNumber(value),
    }));
  }
  return [];
}

function quoteUpperBound(quote: CostQuote | null, fallback: number) {
  if (!quote) return fallback;
  const declared = asNumber(
    quote.estimated_upper_bound_cny
      ?? quote.estimated_total_cny
      ?? quote.max_cost_cny
      ?? quote.total_cny
      ?? quote.estimated_max
      ?? quote.estimated_total,
    fallback,
  );
  const itemTotal = quoteLines(quote).reduce((total, item) => total + item.amount, 0);
  return Math.max(declared, itemTotal);
}

function hasQuoteExpired(quote: CostQuote | null) {
  return Boolean(quote?.expires_at && Date.parse(quote.expires_at) <= Date.now());
}

function normalizePlan(item?: CloudBatchItem | null): EditPlanStep[] {
  if (!item) return DEFAULT_PLAN;
  const plan = item.edit_plan;
  const rawSteps = plan?.steps?.length
    ? plan.steps
    : plan?.enabled_steps?.length
      ? plan.enabled_steps.map((kind) => ({
          step_id: kind,
          kind,
          enabled: true,
          params: kind === "trim_silence" ? { intervals: plan.remove_ranges } : {},
          estimated_removed_seconds: kind === "trim_silence"
            ? plan.remove_ranges.reduce((total, range) => total + Math.max(0, range.end - range.start), 0)
            : 0,
        }))
      : item.analysis?.recommended_steps || [];
  const normalized = (rawSteps as unknown[]).flatMap((raw, index) => {
    if (!isRecord(raw)) return [];
    const rawKind = String(raw.kind || "");
    const kind = STEP_KIND_ALIASES[rawKind];
    if (!kind || rawKind === "ai_subtitle") return [];
    const params = isRecord(raw.params) ? raw.params : {};
    const step: EditPlanStep = {
      id: String(raw.id || raw.step_id || `${kind}-${index}`),
      kind,
      label: String(raw.label || DEFAULT_PLAN.find((item) => item.kind === kind)?.label || kind),
      reason: String(raw.reason || raw.description || DEFAULT_PLAN.find((item) => item.kind === kind)?.reason || "安全剪辑建议"),
      enabled: raw.enabled !== false,
      estimated_removed_seconds: asNumber(
        raw.estimated_removed_seconds ?? params.estimated_removed_seconds,
      ),
      params,
    };
    return [step];
  });
  return normalized.length ? normalized : DEFAULT_PLAN;
}

function normalizeSubtitleSegments(item?: CloudBatchItem | null): TranscriptSegment[] {
  if (!item || !Array.isArray(item.subtitle_segments)) return [];
  const emphasisBySegment = new Map(
    (item.edit_plan?.caption_emphasis || []).map((entry) => [
      entry.segment_index,
      entry,
    ]),
  );
  return item.subtitle_segments.flatMap((segment, index) => {
    if (!isRecord(segment)) return [];
    const aiEmphasis = emphasisBySegment.get(index);
    return [{
      ...segment,
      start: asNumber(segment.start),
      end: asNumber(segment.end),
      text: String(segment.text || ""),
      emphasis_terms: Array.isArray(segment.emphasis_terms)
        ? segment.emphasis_terms.map(String)
        : aiEmphasis
          ? [aiEmphasis.term]
          : [],
      emphasis_kind: String(segment.emphasis_kind || aiEmphasis?.kind || "keyword"),
    } as TranscriptSegment];
  });
}

function titleCandidates(item?: CloudBatchItem | null) {
  if (!item) return [];
  if (item.title_candidates?.length) return item.title_candidates;
  return item.edit_plan?.title_candidates || [];
}

function silenceIntervals(steps: EditPlanStep[]) {
  return steps.flatMap((step) => {
    if (step.kind !== "silence_trim") return [];
    const raw = step.params.intervals;
    if (!Array.isArray(raw)) return [];
    return raw.flatMap((interval) => {
      if (!isRecord(interval)) return [];
      const start = asNumber(interval.start, -1);
      const end = asNumber(interval.end, -1);
      return start >= 0 && end > start ? [{ start, end }] : [];
    });
  });
}

type PreviewInterval = { start: number; end: number };

function sortedPreviewIntervals(intervals: PreviewInterval[]) {
  return [...intervals].sort((left, right) => left.start - right.start);
}

function planTimeFromSourceTime(sourceTime: number, intervals: PreviewInterval[]) {
  let effectiveTime = Math.max(0, sourceTime);
  for (const interval of sortedPreviewIntervals(intervals)) {
    if (sourceTime >= interval.end) {
      effectiveTime -= interval.end - interval.start;
      continue;
    }
    if (sourceTime > interval.start) {
      effectiveTime -= sourceTime - interval.start;
    }
    break;
  }
  return Math.max(0, effectiveTime);
}

function sourceTimeFromPlanTime(
  planTime: number,
  sourceDuration: number,
  intervals: PreviewInterval[],
) {
  const target = Math.max(0, planTime);
  let sourceCursor = 0;
  let planCursor = 0;
  for (const interval of sortedPreviewIntervals(intervals)) {
    const cutStart = Math.max(sourceCursor, Math.min(sourceDuration, interval.start));
    const cutEnd = Math.max(cutStart, Math.min(sourceDuration, interval.end));
    const keptDuration = cutStart - sourceCursor;
    if (target < planCursor + keptDuration) {
      return sourceCursor + target - planCursor;
    }
    planCursor += keptDuration;
    sourceCursor = cutEnd;
  }
  return Math.min(sourceDuration, sourceCursor + target - planCursor);
}

function formatTimelineTime(seconds: number) {
  const wholeSeconds = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(wholeSeconds / 60);
  return `${minutes}:${String(wholeSeconds % 60).padStart(2, "0")}`;
}

function captionBoundarySplits(piece: string) {
  const boundaries = new Set<number>();
  CAPTION_BREAK_BEFORE_TOKENS.forEach((token) => {
    let start = piece.indexOf(token);
    while (start >= 0) {
      if (start > 0) boundaries.add(start);
      start = piece.indexOf(token, start + 1);
    }
  });
  CAPTION_BREAK_AFTER_TOKENS.forEach((token) => {
    let start = piece.indexOf(token);
    while (start >= 0) {
      const end = start + token.length;
      if (end < piece.length) boundaries.add(end);
      start = piece.indexOf(token, start + 1);
    }
  });
  return boundaries;
}

function captionWordSplits(piece: string) {
  type Segment = { segment: string };
  type Segmenter = { segment: (input: string) => Iterable<Segment> };
  type SegmenterConstructor = new (
    locale: string,
    options: { granularity: "word" },
  ) => Segmenter;
  const SegmenterApi = (
    Intl as unknown as { Segmenter?: SegmenterConstructor }
  ).Segmenter;
  if (!SegmenterApi) return new Set<number>();
  const boundaries = new Set<number>();
  const numericRanges = captionNumericRanges(piece);
  let cursor = 0;
  for (const token of new SegmenterApi("zh-CN", { granularity: "word" }).segment(piece)) {
    cursor += token.segment.length;
    if (cursor < piece.length && !numericRanges.some(([start, end]) => start < cursor && cursor < end)) {
      boundaries.add(cursor);
    }
  }
  return boundaries;
}

function captionNumericRanges(text: string): Array<[number, number]> {
  return Array.from(text.matchAll(CAPTION_NUMERIC_ATOM_RE), (match) => [
    match.index,
    match.index + match[0].length,
  ]);
}

function captionSplitInsideNumeric(text: string, splitAt: number) {
  return captionNumericRanges(text).some(([start, end]) => start < splitAt && splitAt < end);
}

function captionSplitReadsNaturally(piece: string, splitAt: number) {
  const left = piece.slice(0, splitAt);
  const right = piece.slice(splitAt);
  return !CAPTION_BAD_LINE_ENDINGS.some((token) => left.endsWith(token))
    && !CAPTION_BAD_LINE_STARTS.some((token) => right.startsWith(token));
}

function captionPhraseParts(piece: string, maxChars: number) {
  const parts: string[] = [];
  let remaining = piece;
  const minimumChars = Math.min(4, maxChars);
  while (remaining.length > maxChars) {
    const partCount = Math.ceil(remaining.length / maxChars);
    const ideal = Math.round(remaining.length / partCount);
    const minimumSplit = Math.max(
      minimumChars,
      remaining.length - maxChars * (partCount - 1),
    );
    const maximumSplit = Math.min(
      maxChars,
      remaining.length - minimumChars * (partCount - 1),
    );
    const wordSplits = captionWordSplits(remaining);
    const semanticSplits = captionBoundarySplits(remaining);
    const availableSplits = Array.from(
      { length: Math.max(0, maximumSplit - minimumSplit + 1) },
      (_, index) => minimumSplit + index,
    ).filter((splitAt) => (
      (wordSplits.has(splitAt) || semanticSplits.has(splitAt))
      && !captionSplitInsideNumeric(remaining, splitAt)
    ));
    const naturalSplits = availableSplits.filter(
      (splitAt) => captionSplitReadsNaturally(remaining, splitAt),
    );
    const safeSplits = naturalSplits.length ? naturalSplits : availableSplits;
    const semanticCandidates = safeSplits.filter((splitAt) => semanticSplits.has(splitAt));
    const candidates = semanticCandidates.length ? semanticCandidates : safeSplits;
    const fallbackCandidates = Array.from(
      { length: Math.max(0, maximumSplit - minimumSplit + 1) },
      (_, index) => minimumSplit + index,
    ).filter((splitAt) => !captionSplitInsideNumeric(remaining, splitAt));
    const fallback = fallbackCandidates.reduce(
      (best, candidate) => (
        Math.abs(candidate - ideal) < Math.abs(best - ideal)
        || (
          Math.abs(candidate - ideal) === Math.abs(best - ideal)
          && candidate > best
        )
          ? candidate
          : best
      ),
      Math.max(minimumSplit, Math.min(maximumSplit, ideal)),
    );
    const splitAt = candidates.reduce(
      (best, candidate) => (
        Math.abs(candidate - ideal) < Math.abs(best - ideal)
        || (
          Math.abs(candidate - ideal) === Math.abs(best - ideal)
          && candidate > best
        )
          ? candidate
          : best
      ),
      candidates[0] ?? fallback,
    );
    parts.push(remaining.slice(0, splitAt));
    remaining = remaining.slice(splitAt);
  }
  if (remaining) parts.push(remaining);
  return parts;
}

function captionChunks(text: string, maxChars: number, durationSeconds?: number) {
  const cleanText = text.replace(/\s+/g, "");
  const characters = Array.from(cleanText);
  const breakCharacters = new Set(Array.from("，。！？；：、,.!?;:“”‘’（）()【】[]《》…—"));
  const numericPunctuation = new Set([".", ",", ":"]);
  const pieces: string[] = [];
  let phrase = "";
  characters.forEach((character, index) => {
    const betweenDigits = numericPunctuation.has(character)
      && index > 0
      && index + 1 < characters.length
      && /\d/.test(characters[index - 1])
      && /\d/.test(characters[index + 1]);
    if (breakCharacters.has(character) && !betweenDigits) {
      if (phrase) pieces.push(phrase);
      phrase = "";
      return;
    }
    phrase += character;
  });
  if (phrase) pieces.push(phrase);
  const base = pieces.flatMap((piece) => captionPhraseParts(piece, maxChars));
  const duration = Math.max(0, durationSeconds ?? 0);
  if (!duration || duration <= 2.2 || base.length === 0) return base;
  const minimumCues = Math.max(1, Math.ceil(duration / 2.2));
  const maximumCues = duration >= 0.8 ? Math.max(1, Math.floor(duration / 0.8)) : 1;
  const targetCues = Math.min(
    Math.max(base.length, minimumCues),
    maximumCues,
    characters.length,
  );
  if (targetCues <= base.length) return base;
  const dynamicMaxChars = Math.max(
    1,
    Math.min(maxChars, Math.ceil(characters.length / targetCues)),
  );
  const refined = pieces.flatMap((piece) => captionPhraseParts(piece, dynamicMaxChars));
  return refined.length ? refined : base;
}

function displayLines(
  text: string,
  charsPerLine: number,
  maxLines?: number,
  truncate = false,
) {
  let clean = text.replace(/\s+/g, "");
  const maxChars = maxLines ? charsPerLine * maxLines : undefined;
  if (truncate && maxChars && clean.length > maxChars) {
    let cutoff = maxChars - 1;
    const numeric = captionNumericRanges(clean).find(([start, end]) => start < cutoff && cutoff < end);
    if (numeric) cutoff = numeric[0] > 0 ? numeric[0] : numeric[1];
    clean = `${clean.slice(0, cutoff)}…`;
  }
  return captionWrapLines(clean, charsPerLine);
}

function captionWrapLines(text: string, charsPerLine: number) {
  const clean = text.replace(/\s+/g, "");
  if (!clean) return [];
  const numericRanges = captionNumericRanges(clean);
  const lines: string[] = [];
  let cursor = 0;
  while (cursor < clean.length) {
    let limit = Math.min(clean.length, cursor + Math.max(1, charsPerLine));
    const containingNumeric = numericRanges.find(([start, end]) => start < limit && limit < end);
    if (containingNumeric) {
      limit = containingNumeric[0] > cursor ? containingNumeric[0] : containingNumeric[1];
    }
    if (limit <= cursor) limit = Math.min(clean.length, cursor + Math.max(1, charsPerLine));
    lines.push(clean.slice(cursor, limit));
    cursor = limit;
  }
  return lines;
}

function emphasisRange(lines: string[], terms?: string[]) {
  const term = captionChunks(terms?.[0] || "", Number.MAX_SAFE_INTEGER)[0];
  if (!term) return null;
  for (const [lineIndex, line] of lines.entries()) {
    const start = line.indexOf(term);
    if (start >= 0) return { line_index: lineIndex, start, end: start + term.length };
  }
  return null;
}

function captionEmphasisStyle(kind?: string) {
  const colors: Record<string, string> = {
    number: "#FFD166",
    benefit: "#FFB86B",
    method: "#FF9F68",
    warning: "#FF7A70",
    result: "#FF8A7A",
    cta: "#FFC857",
    keyword: "#FFC857",
  };
  return {
    color: colors[String(kind || "").toLowerCase()] || "#FFE7C2",
    scale: 1.08,
    animation: "scale_overshoot",
    duration_ms: 200,
  };
}

function kineticCleanText(value: string) {
  return value.replace(/[\s，。！？、,.!?；;：:‘’“”"（）()【】[\]《》…—]/g, "");
}

function previewKineticWords(
  segment: TranscriptSegment,
  line: string,
  cueStart: number,
  cueEnd: number,
  emphasis: { line_index: number; start: number; end: number } | null,
  lineIndex: number,
) {
  const words = (segment.words || [])
    .map((item) => ({
      text: kineticCleanText(item.text || ""),
      start: Number(item.start),
      end: Number(item.end),
    }))
    .filter((item) => item.text.length > 0 && item.end > item.start);
  if (!words.length || !line || !emphasis || emphasis.line_index !== lineIndex) return [];
  const normalizedLine = kineticCleanText(line);
  const indexMap = Array.from(line).flatMap((character, index) => (
    /[\s，。！？、,.!?；;：:‘’“”"（）()【】[\]《》…—]/.test(character)
      ? []
      : [index]
  ));
  if (!normalizedLine || indexMap.length !== normalizedLine.length) return [];
  const candidates: Array<{ score: number; words: typeof words }> = [];
  for (let begin = 0; begin < words.length; begin += 1) {
    const matched: typeof words = [];
    let accumulated = "";
    for (const word of words.slice(begin)) {
      accumulated += word.text;
      matched.push(word);
      if (accumulated === normalizedLine) {
        candidates.push({
          score: Math.abs(matched[0].start - cueStart)
            + Math.abs(matched[matched.length - 1].end - cueEnd),
          words: matched,
        });
        break;
      }
      if (!normalizedLine.startsWith(accumulated)) break;
    }
  }
  const selected = candidates.sort((left, right) => left.score - right.score)[0]?.words;
  if (!selected) return [];
  const semanticColor = kineticSemanticColor(segment, line);
  let searchCursor = 0;
  const duration = Math.max(cueEnd - cueStart, 0.001);
  const spans = selected.flatMap((word) => {
    const position = normalizedLine.indexOf(word.text, searchCursor);
    if (position < 0) return [];
    const startOffset = indexMap[position];
    const endOffset = indexMap[position + word.text.length - 1] + 1;
    searchCursor = position + word.text.length;
    const start = Math.max(0, Math.min(duration, word.start - cueStart));
    const end = Math.max(start + 0.04, Math.min(duration, word.end - cueStart));
    return [{
      line_index: lineIndex,
      start_offset: startOffset,
      end_offset: endOffset,
      start,
      end,
      text: line.slice(startOffset, endOffset),
      color: semanticColor,
      kind: "word",
    }];
  }).filter((span) => (
    span.start_offset < emphasis.end && span.end_offset > emphasis.start
  ));
  return spans;
}

const AUTO_EMPHASIS_NUMBER = /\d+(?:\.\d+)?(?:%|元|块|万|倍|折|公里|分钟|秒|张|个|家|人|套)/;
const AUTO_EMPHASIS_PROMOTION_REWARD = /送(\d+(?:\.\d+)?(?:元|块)?)/;
const AUTO_EMPHASIS_KEYWORDS = [
  "现金奖励",
  "自动执行",
  "不用人管",
  "免费",
  "赚钱",
  "省钱",
  "优惠",
  "奖励",
  "增长",
  "翻倍",
  "关键",
  "重点",
  "注意",
  "千万",
  "必须",
  "不要",
  "风险",
  "警告",
  "爆款",
  "成交",
  "引流",
  "裂变",
  "回头客",
];

function automaticEmphasisTerm(text: string) {
  const clean = captionChunks(text, Number.MAX_SAFE_INTEGER).join("");
  const reward = clean.match(AUTO_EMPHASIS_PROMOTION_REWARD)?.[1];
  if (reward && reward.length <= 6) return { term: reward, kind: "number" };
  const number = clean.match(AUTO_EMPHASIS_NUMBER)?.[0];
  if (number && number.length <= 6) return { term: number, kind: "number" };
  const keyword = AUTO_EMPHASIS_KEYWORDS.find((item) => clean.includes(item));
  if (!keyword) return null;
  return {
    term: keyword,
    kind: ["注意", "千万", "必须", "不要", "风险", "警告"].includes(keyword)
      ? "warning"
      : "benefit",
  };
}

const KINETIC_SEMANTIC_COLORS: Record<string, string> = {
  number: "#FFD166",
  benefit: "#FFB86B",
  warning: "#FF7A70",
  emotion: "#FF8A7A",
  method: "#FF9F68",
  result: "#FF8A7A",
  cta: "#FFC857",
  keyword: "#F4B183",
  default: "#FFE7C2",
};

function kineticSemanticColor(segment: TranscriptSegment, text: string) {
  const kind = String(segment.emphasis_kind || "").toLowerCase();
  if (KINETIC_SEMANTIC_COLORS[kind]) return KINETIC_SEMANTIC_COLORS[kind];
  const automatic = automaticEmphasisTerm(text);
  return KINETIC_SEMANTIC_COLORS[automatic?.kind || "default"];
}

function kineticStyleForCue(
  segment: TranscriptSegment,
  text: string,
  automatic?: { term: string; kind: string } | null,
  openingHook = false,
) {
  const kind = String(segment.emphasis_kind || "").toLowerCase();
  if (openingHook) {
    return "slam";
  }
  if (kind === "warning" || automatic?.kind === "warning") return "shake";
  if (kind === "number" || automatic?.kind === "number" || /\d/.test(text)) return "stamp";
  if (kind === "result") return "stamp";
  if (kind === "benefit" || automatic?.kind === "benefit") return "marker";
  if (kind === "method") return "underline";
  if (kind === "cta") return "bounce";
  if (kind === "keyword") return "marker";
  return "marker";
}

function captionCueTimings(
  chunks: string[],
  segmentStart: number,
  segmentEnd: number,
  spokenRanges?: VideoEditorTimeRange[],
) {
  const totalChars = chunks.reduce((total, chunk) => total + chunk.length, 0) || 1;
  let fallbackCursor = segmentStart;
  const fallback = chunks.map((chunk, index) => {
    const end = index === chunks.length - 1
      ? segmentEnd
      : fallbackCursor + ((segmentEnd - segmentStart) * chunk.length / totalChars);
    const timing = { start: fallbackCursor, end };
    fallbackCursor = end;
    return timing;
  });
  const ranges = (spokenRanges || [])
    .map((range) => ({
      start: Math.max(segmentStart, asNumber(range.start)),
      end: Math.min(segmentEnd, asNumber(range.end)),
    }))
    .filter((range) => range.end > range.start)
    .sort((left, right) => left.start - right.start);
  if (!ranges.length || ranges.length > chunks.length) return fallback;

  const chunkLengths = chunks.map((chunk) => Math.max(1, chunk.length));
  const chunkPrefix = [0];
  chunkLengths.forEach((length) => chunkPrefix.push(
    chunkPrefix[chunkPrefix.length - 1] + length,
  ));
  const rangeDurations = ranges.map((range) => range.end - range.start);
  const totalDuration = rangeDurations.reduce((total, duration) => total + duration, 0) || 1;
  const costs = Array.from(
    { length: ranges.length + 1 },
    () => Array(chunks.length + 1).fill(Number.POSITIVE_INFINITY),
  );
  const previous = Array.from(
    { length: ranges.length + 1 },
    () => Array(chunks.length + 1).fill(-1),
  );
  costs[0][0] = 0;
  for (let rangeIndex = 1; rangeIndex <= ranges.length; rangeIndex += 1) {
    const maxChunks = chunks.length - (ranges.length - rangeIndex);
    const durationShare = rangeDurations[rangeIndex - 1] / totalDuration;
    for (let chunkEnd = rangeIndex; chunkEnd <= maxChunks; chunkEnd += 1) {
      for (let chunkStart = rangeIndex - 1; chunkStart < chunkEnd; chunkStart += 1) {
        const prior = costs[rangeIndex - 1][chunkStart];
        if (!Number.isFinite(prior)) continue;
        const charShare = (
          chunkPrefix[chunkEnd] - chunkPrefix[chunkStart]
        ) / chunkPrefix[chunkPrefix.length - 1];
        const cost = prior + ((charShare - durationShare) ** 2);
        if (cost < costs[rangeIndex][chunkEnd]) {
          costs[rangeIndex][chunkEnd] = cost;
          previous[rangeIndex][chunkEnd] = chunkStart;
        }
      }
    }
  }
  if (previous[ranges.length][chunks.length] < 0) return fallback;

  const assignments: Array<[number, number]> = [];
  let chunkEnd = chunks.length;
  for (let rangeIndex = ranges.length; rangeIndex > 0; rangeIndex -= 1) {
    const chunkStart = previous[rangeIndex][chunkEnd];
    assignments.push([chunkStart, chunkEnd]);
    chunkEnd = chunkStart;
  }
  assignments.reverse();
  return assignments.flatMap(([chunkStart, groupEnd], rangeIndex) => {
    const range = ranges[rangeIndex];
    const groupTotal = chunkPrefix[groupEnd] - chunkPrefix[chunkStart];
    let cursor = range.start;
    return chunks.slice(chunkStart, groupEnd).map((_chunk, offset) => {
      const index = chunkStart + offset;
      const end = index === groupEnd - 1
        ? range.end
        : cursor + ((range.end - range.start) * chunkLengths[index] / groupTotal);
      const timing = { start: cursor, end };
      cursor = end;
      return timing;
    });
  });
}

function validatedSemanticCaptionParts(
  segments: TranscriptSegment[],
  groups: Array<{ segment_index: number; parts: string[] }> | undefined,
  maxChars: number,
) {
  if (!groups?.length) return null;
  const expected = new Map(
    segments
      .map((segment, index) => [index, captionChunks(segment.text, Number.MAX_SAFE_INTEGER).join("")] as const)
      .filter(([, text]) => Boolean(text)),
  );
  if (groups.length !== expected.size) return null;
  const accepted = new Map<number, string[]>();
  for (const group of groups) {
    const parts = Array.isArray(group.parts)
      ? group.parts.map((part) => captionChunks(part, Number.MAX_SAFE_INTEGER).join(""))
      : [];
    if (
      !expected.has(group.segment_index)
      || accepted.has(group.segment_index)
      || !parts.length
      || parts.some((part) => !part || part.length > maxChars)
      || parts.join("") !== expected.get(group.segment_index)
    ) {
      return null;
    }
    accepted.set(group.segment_index, parts);
  }
  return accepted.size === expected.size ? accepted : null;
}

function previewCaptionCues(
  segments: TranscriptSegment[],
  spec: VideoEditorVisualSpec,
  captionGroups?: Array<{ segment_index: number; parts: string[] }>,
  spokenRanges?: VideoEditorTimeRange[],
) {
  const maxChars = spec.subtitle.max_chars_per_line * spec.subtitle.max_lines;
  const semanticParts = validatedSemanticCaptionParts(segments, captionGroups, maxChars);
  const cues: VideoEditorOverlayPreview["cues"] = segments.flatMap((segment, segmentIndex) => {
    const start = asNumber(segment.start, -1);
    const end = asNumber(segment.end, -1);
    const chunks = semanticParts?.get(segmentIndex) || captionChunks(
      segment.text,
      maxChars,
      end - start,
    );
    if (start < 0 || end <= start || !chunks.length) return [];
    const timings = captionCueTimings(chunks, start, end, spokenRanges);
    return chunks.map((chunk, index) => {
      const automatic = automaticEmphasisTerm(chunk);
      const emphasisTerms = (segment.emphasis_terms || []).filter((term) => (
        chunk.includes(term)
      ));
      if (!emphasisTerms.length && automatic) emphasisTerms.push(automatic.term);
      const lines = displayLines(
        chunk,
        spec.subtitle.max_chars_per_line,
        spec.subtitle.max_lines,
      );
      const emphasis = emphasisRange(lines, emphasisTerms);
      const kineticWords = emphasis
        ? lines.flatMap((line, lineIndex) => previewKineticWords(
          segment,
          line,
          timings[index].start,
          timings[index].end,
          emphasis,
          lineIndex,
        ))
        : [];
      const isOpeningHook = segmentIndex === 0 && index === 0;
      const cue = {
        start: timings[index].start,
        end: timings[index].end,
        lines,
        emphasis_range: emphasis,
        emphasis_style: emphasis
          ? captionEmphasisStyle(segment.emphasis_kind || automatic?.kind)
          : null,
        kinetic_mode: kineticWords.length ? "word_pop" : isOpeningHook ? "cue_pop" : "static",
        kinetic_style: kineticWords.length || isOpeningHook
          ? kineticStyleForCue(segment, chunk, automatic, isOpeningHook)
          : null,
        kinetic_words: kineticWords,
      };
      return cue;
    });
  });
  return sparsePreviewCaptionEmphasis(cues);
}

function sparsePreviewCaptionEmphasis(
  cues: VideoEditorOverlayPreview["cues"],
) {
  const candidates = cues
    .map((cue, index) => ({ cue, index }))
    .filter(({ cue }) => Boolean(cue.emphasis_range));
  if (!candidates.length) return cues;
  const maxEnd = Math.max(...cues.map((cue) => cue.end), 0);
  const totalBudget = Math.max(1, Math.ceil((maxEnd / 60) * 5));
  const selected: number[] = [];
  const bucketCounts = new Map<number, number>();
  const ranked = [...candidates].sort((left, right) => {
    const score = (cue: VideoEditorOverlayPreview["cues"][number]) => {
      const text = cue.lines.join("");
      return /\d/.test(text) || /注意|风险|关键|结论|不要/.test(text) ? 1 : 0;
    };
    return score(right.cue) - score(left.cue) || left.index - right.index;
  });
  for (const { cue, index } of ranked) {
    if (selected.length >= totalBudget) break;
    const bucket = Math.max(0, Math.floor(cue.start / 60));
    if ((bucketCounts.get(bucket) || 0) >= 5) continue;
    if (selected.some((previous) => Math.abs(previous - index) < 2)) continue;
    selected.push(index);
    bucketCounts.set(bucket, (bucketCounts.get(bucket) || 0) + 1);
  }
  const selectedSet = new Set(selected);
  candidates.forEach(({ cue, index }) => {
    if (selectedSet.has(index)) return;
    cue.emphasis_range = null;
    cue.emphasis_style = null;
    cue.kinetic_words = [];
    cue.kinetic_mode = index === 0 ? "cue_pop" : "static";
    cue.kinetic_style = index === 0 ? "slam" : null;
  });
  return cues;
}

function localOverlayPreview(
  segments: TranscriptSegment[],
  title: string,
  spec: VideoEditorVisualSpec,
  captionGroups?: Array<{ segment_index: number; parts: string[] }>,
  spokenRanges?: VideoEditorTimeRange[],
): VideoEditorOverlayPreview {
  const maxChars = spec.subtitle.max_chars_per_line * spec.subtitle.max_lines;
  const semanticParts = validatedSemanticCaptionParts(segments, captionGroups, maxChars);
  return {
    title: {
      lines: displayLines(
        title,
        spec.title.max_chars_per_line,
        spec.title.max_lines,
        true,
      ),
      start: 0,
      end: spec.title.visible_seconds,
    },
    cues: previewCaptionCues(segments, spec, captionGroups, spokenRanges),
    caption_group_source: semanticParts ? "qwen_semantic" : "deterministic_fallback",
    phrase_timing_source: segments.some((segment) => (
      Array.isArray(segment.words) && segment.words.length > 0
    )) ? "word_timestamps" : "estimated_phrase_timestamps",
  };
}

function resolveVisualSpec(spec?: VideoEditorVisualSpec | null): VideoEditorVisualSpec {
  const canvas = { ...DEFAULT_VISUAL_SPEC.canvas, ...spec?.canvas };
  const scale = canvas.width / DEFAULT_VISUAL_SPEC.canvas.width;
  const v2Default: VideoEditorVisualSpec = {
    ...DEFAULT_VISUAL_SPEC,
    canvas,
    title: {
      ...DEFAULT_VISUAL_SPEC.title,
      font_size: Math.round(DEFAULT_VISUAL_SPEC.title.font_size * scale),
      safe_top: Math.round(DEFAULT_VISUAL_SPEC.title.safe_top * scale),
      safe_left: Math.round(DEFAULT_VISUAL_SPEC.title.safe_left * scale),
      asset_width: Math.round(DEFAULT_VISUAL_SPEC.title.asset_width * scale),
      asset_height: Math.round(DEFAULT_VISUAL_SPEC.title.asset_height * scale),
      outline_width: Math.max(0, Math.round(DEFAULT_VISUAL_SPEC.title.outline_width * scale)),
      shadow: Math.max(1, Math.round(DEFAULT_VISUAL_SPEC.title.shadow * scale)),
    },
    accent: {
      ...DEFAULT_VISUAL_SPEC.accent,
      width: Math.round(DEFAULT_VISUAL_SPEC.accent.width * scale),
      height: Math.round(DEFAULT_VISUAL_SPEC.accent.height * scale),
      gap: Math.round(DEFAULT_VISUAL_SPEC.accent.gap * scale),
    },
    subtitle: {
      ...DEFAULT_VISUAL_SPEC.subtitle,
      font_size: Math.round(DEFAULT_VISUAL_SPEC.subtitle.font_size * scale),
      safe_bottom: Math.round(DEFAULT_VISUAL_SPEC.subtitle.safe_bottom * scale),
      outline_width: Math.max(1, Math.round(DEFAULT_VISUAL_SPEC.subtitle.outline_width * scale)),
      shadow: Math.max(1, Math.round(DEFAULT_VISUAL_SPEC.subtitle.shadow * scale)),
    },
  };
  if (spec?.style_id !== DEFAULT_VISUAL_SPEC.style_id) return v2Default;
  return {
    ...v2Default,
    ...spec,
    canvas,
    title: { ...v2Default.title, ...spec.title },
    accent: { ...v2Default.accent, ...spec.accent },
    subtitle: { ...v2Default.subtitle, ...spec.subtitle },
  };
}

function renderOverlayLine(
  line: string,
  lineIndex: number,
  emphasis: VideoEditorOverlayPreview["cues"][number]["emphasis_range"],
  emphasisStyle: VideoEditorOverlayPreview["cues"][number]["emphasis_style"],
  kineticMode: VideoEditorOverlayPreview["cues"][number]["kinetic_mode"],
  kineticStyle: VideoEditorOverlayPreview["cues"][number]["kinetic_style"],
  kineticWords: VideoEditorOverlayPreview["cues"][number]["kinetic_words"],
  cueStart: number,
  previewTime: number,
) {
  const lineKinetics = (kineticWords || []).filter((word) => word.line_index === lineIndex);
  const effect = kineticStyle || "bounce";
  if (lineKinetics.length > 0) {
    const relativeTime = previewTime - cueStart;
    let cursor = 0;
    return (
      <>
        {lineKinetics.map((word, index) => {
          const start = Math.max(0, word.start_offset);
          const end = Math.min(line.length, word.end_offset);
          if (end <= start || start < cursor) return null;
          const isBefore = relativeTime < word.start;
          const isActive = !isBefore && relativeTime <= word.end;
          const prefix = line.slice(cursor, start);
          cursor = end;
          const activeTransform = effect === "slam"
            ? "translateY(-5px) scale(1.10) rotate(-1deg)"
            : effect === "stamp"
              ? "translateY(-2px) scale(1.08) rotate(2deg)"
              : effect === "marker"
                ? "translateY(-1px) scale(1.08)"
                : effect === "underline"
                  ? "translateY(-2px) scale(1.08)"
                  : effect === "shake"
                    ? "translateY(-2px) scale(1.08) rotate(-2deg)"
                    : effect === "bounce"
                      ? "translateY(-5px) scale(1.12)"
                    : "translateY(-2px) scale(1.08)";
          return (
            <Fragment key={`${word.text || "word"}-${index}`}>
              {prefix}
              <span
                className="video-editor-subtitle-kinetic-word"
                style={{
                  opacity: isBefore ? 0.42 : 1,
                  transform: isBefore
                    ? "translateY(6px) scale(.96)"
                    : (isActive ? activeTransform : "translateY(0) scale(1)"),
                  backgroundColor: "transparent",
                  color: isActive
                    ? "#FFF7E7"
                    : (isBefore ? "rgba(248,250,252,.55)" : word.color),
                  textShadow: isActive
                    ? `0 0 ${effect === "slam" ? 12 : 8}px ${word.color}, 0 1px 3px rgba(0,0,0,.42)`
                    : undefined,
                  textDecorationLine: isActive && effect === "underline" ? "underline" : undefined,
                  textDecorationColor: isActive && effect === "underline" ? word.color : undefined,
                  textDecorationThickness: isActive && effect === "underline" ? "0.14em" : undefined,
                  textUnderlineOffset: isActive && effect === "underline" ? "0.18em" : undefined,
                }}
              >
                {line.slice(start, end)}
              </span>
            </Fragment>
          );
        })}
        {line.slice(cursor)}
      </>
    );
  }
  if (kineticMode === "cue_pop") {
    const cueAge = Math.max(0, previewTime - cueStart);
    const fresh = cueAge < 0.24;
    const cueTransform = !fresh
      ? "translateY(0) scale(1) rotate(0)"
      : effect === "slam"
        ? "translateY(-10px) scale(.72) rotate(5deg)"
          : effect === "stamp"
            ? "translateY(-3px) scale(.84) rotate(-7deg)"
          : effect === "shake"
            ? "translateY(0) scale(.9) rotate(-4deg)"
          : effect === "bounce"
            ? "translateY(8px) scale(.78)"
            : "translateY(5px) scale(.88)";
    return (
      <span
        className="video-editor-subtitle-kinetic-line"
        style={{
          display: "inline-block",
          transform: cueTransform,
          opacity: fresh ? 0.7 : 1,
          textShadow: fresh && effect === "slam" ? "0 0 22px rgba(255,225,106,.9)" : undefined,
        }}
      >
        {line}
      </span>
    );
  }
  if (!emphasis || emphasis.line_index !== lineIndex) return line;
  return (
    <>
      {line.slice(0, emphasis.start)}
      <span
        className="video-editor-subtitle-emphasis"
        style={{
          color: emphasisStyle?.color,
          "--video-editor-emphasis-size": emphasisStyle?.scale || 1.08,
          "--video-editor-emphasis-duration": `${emphasisStyle?.duration_ms || 200}ms`,
        } as CSSProperties}
      >
        {line.slice(emphasis.start, emphasis.end)}
      </span>
      {line.slice(emphasis.end)}
    </>
  );
}

function createIdempotencyKey() {
  if (typeof window !== "undefined" && typeof window.crypto?.randomUUID === "function") {
    return `video-editor-${window.crypto.randomUUID()}`;
  }
  return `video-editor-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function isBrowserMediaUrl(value?: string | null): value is string {
  return Boolean(
    value
    && (
      value.startsWith("/")
      || value.startsWith("https://")
      || value.startsWith("http://")
    ),
  );
}

function downloadBrowserMedia(mediaUrl: string, fallbackTitle: string) {
  const title = fallbackTitle
    .replace(/[\\/:*?"<>|]/g, "")
    .trim()
    .slice(0, 48) || "视频";
  const link = document.createElement("a");
  link.href = mediaUrl;
  link.download = `${title}.mp4`;
  link.rel = "noopener";
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function isCloudBatch(batch: VideoEditorBatch): batch is CloudBatch {
  const cloudBatch = batch as CloudBatch;
  return (
    cloudBatch.provider_mode === "legacy"
    ||
    cloudBatch.provider_mode === "sandbox"
    || cloudBatch.provider_mode === "local"
    || cloudBatch.provider_mode === "local_ffmpeg"
    || cloudBatch.provider_mode === "aliyun"
    || Boolean(!cloudBatch.provider_mode && (cloudBatch.output_profile || cloudBatch.cost_quote))
  );
}

function batchSourceId(batch: CloudBatch | null | undefined): string | undefined {
  const sourceId = batch?.items[0]?.source_id;
  return typeof sourceId === "string" && sourceId ? sourceId : undefined;
}

export default function VideoEditorPage() {
  const navigate = useNavigate();
  const previewRef = useRef<HTMLVideoElement | null>(null);
  const pendingLocalDownloadRef = useRef<string | null>(null);
  // A source picker change can happen while the initial batch request is still
  // in flight.  Keep the identity outside React's async render timing so that a
  // late "latest batch" response can never attach an old transcript to a newly
  // selected video.
  const selectedSourceIdRef = useRef<string>();
  const [sources, setSources] = useState<VideoEditorSource[]>([]);
  const [bgmAssets, setBgmAssets] = useState<VideoEditorBgmAsset[]>([]);
  const [brollAssets, setBrollAssets] = useState<VideoEditorVisualAsset[]>([]);
  const [capabilities, setCapabilities] = useState<CloudCapabilities | null>(null);
  const [batches, setBatches] = useState<CloudBatch[]>([]);
  const [batch, setBatch] = useState<CloudBatch | null>(null);
  const [selectedSourceId, setSelectedSourceId] = useState<string>();
  const [platform, setPlatform] = useState("douyin");
  const [outputProfile, setOutputProfile] = useState<OutputProfile>("720p");
  const [quote, setQuote] = useState<CostQuote | null>(null);
  const [quoteOpen, setQuoteOpen] = useState(false);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [pollingStopped, setPollingStopped] = useState(false);
  const [previewMode, setPreviewMode] = useState<PreviewMode>("original");
  const [previewTime, setPreviewTime] = useState(0);
  const [planPreviewElapsed, setPlanPreviewElapsed] = useState(0);
  const [previewDuration, setPreviewDuration] = useState(0);
  const [isPreviewPlaying, setIsPreviewPlaying] = useState(false);
  const [isPreviewMuted, setIsPreviewMuted] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [reviewItem, setReviewItem] = useState<CloudBatchItem | null>(null);
  const [reviewSegments, setReviewSegments] = useState<TranscriptSegment[]>([]);
  const [reviewPlanStepIds, setReviewPlanStepIds] = useState<string[]>([]);
  const [reviewTitle, setReviewTitle] = useState("");
  const [reviewBgmId, setReviewBgmId] = useState<string | null>(null);
  const [reviewBrollAsset, setReviewBrollAsset] = useState<VideoEditorVisualAsset | null>(null);
  const [reviewBrollStart, setReviewBrollStart] = useState<number | null>(null);
  const [reviewBrollEnd, setReviewBrollEnd] = useState<number | null>(null);
  const [reviewBrollMode, setReviewBrollMode] = useState<"pip" | "full">("pip");
  const [brollUploading, setBrollUploading] = useState(false);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [bgmEnabled, setBgmEnabled] = useState(false);
  const [bgmId, setBgmId] = useState<string>();
  const [bgmVolume, setBgmVolume] = useState(0.18);

  const refresh = useCallback(async (keepCurrent = true) => {
    setLoading(true);
    const secondaryData = Promise.allSettled([
      // Only hydrate the latest batch during first render.  The full history
      // list is requested when the user opens the history drawer.
      videoEditorApi.listVideoEditorBatches(1),
      videoEditorApi.listVideoEditorBgm(),
      videoEditorApi.listVideoEditorVisualAssets("broll"),
    ] as const);
    try {
      const [sourceResponse, capabilityResponse] = await Promise.all([
        videoEditorApi.listVideoEditorSources(),
        videoEditorApi.getVideoCapabilities(),
      ]);
      setSources(sourceResponse.items);
      setCapabilities(capabilityResponse as CloudCapabilities);
      const sourceIdToKeep = selectedSourceIdRef.current || sourceResponse.items[0]?.source_id;
      selectedSourceIdRef.current = sourceIdToKeep;
      setSelectedSourceId((current) => current || sourceIdToKeep);
      setPollingStopped(false);
    } catch (error) {
      message.error((error as Error).message || "云端剪辑工作台加载失败");
    } finally {
      setLoading(false);
    }
    const [batchResult, bgmResult, brollResult] = await secondaryData;
    if (bgmResult.status === "fulfilled") {
      setBgmAssets(bgmResult.value.items);
    }
    if (brollResult.status === "fulfilled") {
      setBrollAssets(brollResult.value.items);
    }
    if (batchResult.status === "fulfilled") {
      const cloudBatches = batchResult.value.items.filter(isCloudBatch);
      const activeSourceId = selectedSourceIdRef.current;
      const currentBatchMatchesSource = batchSourceId(batch) === activeSourceId;
      const nextBatch = activeSourceId
        ? (
          keepCurrent && currentBatchMatchesSource
            ? cloudBatches.find((item) => item.batch_id === batch?.batch_id) || batch
            : cloudBatches.find((item) => batchSourceId(item) === activeSourceId)
        ) || null
        : cloudBatches[0] || null;
      setBatches(cloudBatches);
      setBatch(nextBatch);
      setQuote(nextBatch?.cost_quote || null);
      if (nextBatch) {
        setOutputProfile(nextBatch.output_profile || "720p");
        setPlatform(nextBatch.target_platform);
        setBgmEnabled(nextBatch.bgm_enabled);
        setBgmId(nextBatch.bgm_id || undefined);
        setBgmVolume(nextBatch.bgm_volume);
        const sourceIdToKeep = selectedSourceIdRef.current || batchSourceId(nextBatch);
        selectedSourceIdRef.current = sourceIdToKeep;
        setSelectedSourceId((current) => current || sourceIdToKeep);
      }
    }
  }, [batch]);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const response = await videoEditorApi.listVideoEditorBatches(20);
      setBatches(response.items.filter(isCloudBatch));
    } catch (error) {
      message.error((error as Error).message || "任务历史加载失败");
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh(false);
    // Initial load should not be coupled to a batch object created later.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeBatch = selectedSourceId && batchSourceId(batch) === selectedSourceId ? batch : null;
  const currentItem = (activeBatch?.items[0] || null) as CloudBatchItem | null;
  const usableBgmAssets = useMemo(
    () => bgmAssets.filter((asset) => asset.auto_eligible !== false && asset.authorization_status !== "unverified"),
    [bgmAssets],
  );
  const selectedReviewBgm = useMemo(
    () => bgmAssets.find((asset) => asset.asset_id === reviewBgmId) || null,
    [bgmAssets, reviewBgmId],
  );
  const reviewBgmExplanation = reviewItem?.bgm_reason
    || (selectedReviewBgm
      ? `当前任务原先未选配乐，已根据文案预选《${selectedReviewBgm.title}》；请试听后再确认生成。`
      : null);
  const currentStatus = currentItem?.status || activeBatch?.status || "idle";
  const isLocalExport = Boolean(
    currentItem?.job?.workflow === "local_preview_export"
    || currentItem?.provider_stage?.startsWith("local_export_"),
  );
  const selectedSource = useMemo(
    () => sources.find((source) => source.source_id === selectedSourceId) || null,
    [selectedSourceId, sources],
  );
  const planSteps = useMemo(() => {
    const normalized = normalizePlan(currentItem);
    if (
      !currentItem
      || !usableBgmAssets.length
      || normalized.some((step) => step.kind === "background_music")
    ) {
      return normalized;
    }
    return [
      ...normalized,
      {
        ...DEFAULT_PLAN.find((step) => step.kind === "background_music")!,
        id: "bgm",
        label: "智能匹配口播配乐",
        reason: "系统已根据文案语气预选一首低音量背景音乐，确认前可试听或更换。",
      },
    ];
  }, [currentItem, usableBgmAssets.length]);
  const enabledPlanSteps = planSteps.filter((step) => step.enabled);
  const providerMode = activeBatch?.provider_mode || capabilities?.provider_mode || "configuration_required";
  const isSandbox = Boolean(
    activeBatch?.is_mock
    || currentItem?.is_mock
    || capabilities?.is_mock
    || providerMode === "sandbox",
  );
  const missingConfiguration = capabilities?.missing_configuration || [];
  const localRenderer = providerMode === "local_ffmpeg" || capabilities?.renderer_mode === "local_ffmpeg";
  const displayStatus = localRenderer && currentStatus === "analyzing"
    ? "local_analyzing"
    : isSandbox && currentStatus === "configuration_required"
    ? "sandbox_completed"
    : currentStatus;
  const configurationBlocked = !isSandbox && !localRenderer && (
    activeBatch?.provider_mode !== "legacy"
    && (
    providerMode === "configuration_required"
    || capabilities?.live_ready === false
    || capabilities?.enabled === false
    || missingConfiguration.length > 0
    )
  );
  const quoteBreakdown = quoteLines(quote);
  const costUpperBound = quoteUpperBound(quote, PROFILE_META[outputProfile].exampleCost);
  const quoteExpired = hasQuoteExpired(quote);
  const quoteBlocked = Boolean(
    !isSandbox
    && (
      quote?.provider_ready === false
      || quote?.live_ready === false
      || quote?.blocked_reasons?.length
      || quote?.blocking_reasons?.length
      || quote?.missing_configuration?.length
    ),
  );
  const resultMediaUrl = currentItem?.result_media_url || currentItem?.job?.media_url || null;
  const playableResultMediaUrl = isBrowserMediaUrl(resultMediaUrl) ? resultMediaUrl : null;
  const canConfirmOutput = Boolean(
    !isSandbox
    && playableResultMediaUrl
    && currentItem?.publish_allowed !== false,
  );
  const publishHandoffReady = Boolean(canConfirmOutput && currentItem?.edit_task_id);

  useEffect(() => {
    if (
      !activeBatch
      || pollingStopped
      || !["queued", "analyzing", "ready_to_render", "rendering"].includes(currentStatus)
    ) return undefined;
    const timer = window.setTimeout(() => {
      void videoEditorApi.getVideoEditorBatch(activeBatch.batch_id).then((next) => {
        const cloudBatch = next as CloudBatch;
        setBatch(cloudBatch);
        setBatches((items) => [
          cloudBatch,
          ...items.filter((item) => item.batch_id !== cloudBatch.batch_id),
        ]);
        const exported = cloudBatch.items[0];
        if (
          pendingLocalDownloadRef.current === cloudBatch.batch_id
          && exported?.job?.status === "succeeded"
          && exported.job.download_url
        ) {
          pendingLocalDownloadRef.current = null;
          downloadBrowserMedia(
            exported.job.download_url,
            exported.selected_title || exported.title || "剪辑成片",
          );
          message.success("成片已生成，正在下载");
        }
      }).catch(() => {
        setPollingStopped(true);
        message.warning("状态查询失败，已停止自动查询；可手动刷新后继续。");
      });
    }, 1800);
    return () => window.clearTimeout(timer);
  }, [activeBatch, currentStatus, pollingStopped]);

  useEffect(() => {
    setPreviewTime(0);
    setPlanPreviewElapsed(0);
    setPreviewDuration(0);
    if (previewRef.current) previewRef.current.currentTime = 0;
  }, [previewMode, selectedSourceId]);

  const loadQuote = async (profile: OutputProfile, openModal: boolean) => {
    if (!selectedSourceId) {
      message.warning("请先选择一条视频素材");
      return;
    }
    setQuoteLoading(true);
    try {
      const nextQuote = await videoEditorApi.preflightVideoEditor({
        sourceId: selectedSourceId,
        outputProfile: profile,
        targetPlatform: platform,
      });
      setQuote(nextQuote as CostQuote);
      if (openModal) setQuoteOpen(true);
    } catch (error) {
      message.error((error as Error).message || "无法获取剪辑费用");
    } finally {
      setQuoteLoading(false);
    }
  };

  const changeOutputProfile = (profile: OutputProfile) => {
    const shouldRefreshQuote = Boolean(quote && selectedSourceId);
    setOutputProfile(profile);
    setQuote(null);
    if (shouldRefreshQuote) void loadQuote(profile, false);
  };

  const selectSource = (sourceId: string) => {
    selectedSourceIdRef.current = sourceId;
    setSelectedSourceId(sourceId);
    setBatch(null);
    setQuote(null);
    setPreviewMode("original");
    setReviewSegments([]);
    setReviewTitle("");
    setReviewBgmId(null);
    setReviewBrollAsset(null);
    setReviewBrollStart(null);
    setReviewBrollEnd(null);
    setReviewBrollMode("pip");
    setBgmEnabled(false);
    setBgmId(undefined);
  };

  const uploadSource = async (file: File) => {
    setUploading(true);
    try {
      const response = await videoEditorApi.uploadVideoEditorSources(
        [file],
        "当前账号（上传即确认）",
      );
      const uploaded = response.items[0];
      setSources((items) => [
        ...response.items,
        ...items.filter((item) => !response.items.some((next) => next.source_id === item.source_id)),
      ]);
      if (uploaded) selectSource(uploaded.source_id);
      message.success("素材已上传，请确认方案后开始处理");
    } catch (error) {
      message.error((error as Error).message || "视频素材上传失败");
    } finally {
      setUploading(false);
    }
  };

  const uploadBroll = async (file: File) => {
    setBrollUploading(true);
    try {
      const asset = await videoEditorApi.uploadVideoEditorVisualAsset({
        kind: "broll",
        file,
        rightsHolder: "当前账号（上传即确认）",
      });
      setBrollAssets((items) => [asset, ...items.filter((item) => item.asset_id !== asset.asset_id)]);
      setReviewBrollAsset(asset);
      if (reviewBrollStart === null) setReviewBrollStart(0);
      if (reviewBrollEnd === null) {
        const firstSegment = reviewSegments[0];
        setReviewBrollEnd(firstSegment ? Number(firstSegment.end) : 3);
      }
      message.success("B-roll 素材已就绪，请绑定口播时间段");
    } catch (error) {
      message.error((error as Error).message || "B-roll 上传失败");
    } finally {
      setBrollUploading(false);
    }
  };

  const startAnalysis = async () => {
    if (!selectedSourceId || !quote) return;
    if (hasQuoteExpired(quote)) {
      message.info("报价已过期，正在刷新；请重新确认费用。");
      await loadQuote(outputProfile, true);
      return;
    }
    setSubmitting(true);
    try {
      const profile = PROFILE_META[outputProfile];
      const request = {
        sourceIds: [selectedSourceId],
        targetPlatform: platform,
        outputProfile,
        stylePresetId: "talking-head-semantic-adaptive-v1",
        quoteId: quote.quote_id,
        billingConfirmation: {
          confirmed: true,
          maxCostCny: costUpperBound,
        },
        idempotencyKey: createIdempotencyKey(),
        subtitleEnabled: true,
        steps: [],
        outputFormat: "mp4",
        outputResolution: profile.resolution,
        outputFps: profile.fps,
        outputBitrate: profile.bitrate,
        bgmEnabled,
        bgmId,
        bgmVolume,
      } as unknown as Parameters<typeof videoEditorApi.createVideoEditorBatch>[0];
      const created = await videoEditorApi.createVideoEditorBatch(request) as CloudBatch;
      setBatch(created);
      setBatches((items) => [created, ...items.filter((item) => item.batch_id !== created.batch_id)]);
      setQuote(created.cost_quote || quote);
      setQuoteOpen(false);
      setPreviewMode("plan");
      message.success(
        isSandbox
          ? "免费体验已开始：不会调用真实识别、出片或发布"
          : localRenderer ? "已开始本机分析，完成后请确认字幕与方案" : "已确认费用，开始云端分析",
      );
    } catch (error) {
      if (!handleCreditsError(error, () => navigate("/admin"))) {
        message.error((error as Error).message || "无法启动云端分析");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const openReview = async (item: CloudBatchItem) => {
    setReviewItem(item);
    setReviewSegments(normalizeSubtitleSegments(item));
    const itemPlan = normalizePlan(item);
    const fallbackEnabledKinds = item.edit_plan?.enabled_steps?.length
      ? item.edit_plan.enabled_steps
      : item.edit_plan?.steps?.length
        ? itemPlan.filter((step) => step.enabled).map((step) => step.kind)
        : ["vertical_fit", "subtitles", "title"];
    const rawEnabledIds = item.enabled_plan_step_ids?.length
      ? item.enabled_plan_step_ids
      : fallbackEnabledKinds;
    const enabledIds = Array.from(new Set(
      rawEnabledIds.map(reviewStepKind).filter((value): value is string => Boolean(value)),
    ));
    setReviewTitle(item.selected_title || titleCandidates(item)[0] || item.title);
    const bgmWasReviewed = Boolean(item.review_snapshot?.bgm_confirmed);
    const nextBgmId = bgmWasReviewed
      ? (item.selected_bgm_id || null)
        : reviewBgmId
        || item.selected_bgm_id
        || bgmId
        || null;
    setReviewBgmId(nextBgmId);
    const rawBroll = item.review_snapshot?.broll;
    const savedBroll = rawBroll && typeof rawBroll === "object"
      ? rawBroll as Partial<VideoEditorBrollPlacement>
      : null;
    const savedAsset = savedBroll?.asset_id
      ? brollAssets.find((asset) => asset.asset_id === savedBroll.asset_id) || null
      : null;
    setReviewBrollAsset(savedAsset);
    setReviewBrollStart(typeof savedBroll?.start === "number" ? savedBroll.start : null);
    setReviewBrollEnd(typeof savedBroll?.end === "number" ? savedBroll.end : null);
    setReviewBrollMode(savedBroll?.mode === "full" ? "full" : "pip");
    setReviewPlanStepIds(Array.from(new Set([
      ...enabledIds,
      ...(nextBgmId ? ["bgm"] : []),
    ])));
    if (!item.subtitle_task_id) return;
    setReviewLoading(true);
    try {
      const task = await videoEditorApi.getTranscription(item.subtitle_task_id);
      const segments = Array.isArray(task?.segments) ? task.segments : [];
      setReviewSegments(segments.map((segment) => ({ ...segment })));
    } catch (error) {
      message.error((error as Error).message || "字幕草稿加载失败");
      setReviewItem(null);
    } finally {
      setReviewLoading(false);
    }
  };

  const saveReview = async () => {
    if (!activeBatch || !reviewItem) return;
    setReviewSaving(true);
    try {
      const brollPlacement = reviewBrollAsset && reviewBrollStart !== null && reviewBrollEnd !== null
        ? {
            asset_id: reviewBrollAsset.asset_id,
            start: reviewBrollStart,
            end: reviewBrollEnd,
            mode: reviewBrollMode,
          }
        : null;
      if (brollPlacement && (isSandbox || reviewItem.is_mock)) {
        message.warning("免费体验没有真实媒体，B-roll 请在真实本机素材批次中使用。");
        setReviewSaving(false);
        return;
      }
      const localOnlyPreview = Boolean(brollPlacement)
        || activeBatch.provider_mode === "legacy"
        || activeBatch.provider_mode === "local"
        || activeBatch.provider_mode === "local_ffmpeg"
        || capabilities?.renderer_mode === "local_ffmpeg";
      const next = await videoEditorApi.reviewVideoEditorBatchItem(
        activeBatch.batch_id,
        reviewItem.item_id,
        {
          subtitleSegments: reviewSegments as unknown as Array<Record<string, unknown>>,
          enabledPlanStepIds: reviewPlanStepIds,
          selectedTitle: reviewTitle.trim() || reviewItem.title,
          selectedBgmId: reviewBgmId,
          brollPlacement,
          localOnly: localOnlyPreview,
          smartOpeningEnabled: reviewPlanStepIds.includes("smart_opening"),
          confirmed: true,
        },
      ) as CloudBatch;
      const reviewedItem = next.items[0] as CloudBatchItem | undefined;
      const releaseTemplateLocal = Boolean(
        localOnlyPreview
        && reviewedItem
        && !isSandbox
        && !reviewedItem.is_mock,
      );
      const exported = releaseTemplateLocal && reviewedItem
        ? await videoEditorApi.createVideoEditorReleaseTemplateLocalExport(
            next.batch_id,
            reviewedItem.item_id,
            reviewBgmId,
          ) as CloudBatch
        : localOnlyPreview && reviewedItem
          ? await videoEditorApi.createVideoEditorLocalExport(
              next.batch_id,
              reviewedItem.item_id,
            ) as CloudBatch
          : next;
      setBatch(exported);
      setBatches((items) => [exported, ...items.filter((item) => item.batch_id !== exported.batch_id)]);
      setReviewItem(null);
      const nextItem = exported.items[0] as CloudBatchItem | undefined;
      const nextMediaUrl = nextItem?.result_media_url || nextItem?.job?.media_url;
      setPreviewMode(isBrowserMediaUrl(nextMediaUrl) ? "output" : "plan");
      message.success(
        localOnlyPreview
          ? releaseTemplateLocal
            ? "已自动套用口播发布级分镜；正在本机合成，不会上传云端"
            : brollPlacement
              ? "B-roll 已保存；正在本机免费合成，不会上传云端"
              : "字幕与方案已确认，正在本机免费生成成片"
          : exported.is_mock
          ? "体验方案已保存；云端出片开通后可处理真实素材"
          : "字幕与方案已确认，正在生成一次正式成片",
      );
    } catch (error) {
      message.error((error as Error).message || "人工复核保存失败");
    } finally {
      setReviewSaving(false);
    }
  };

  const retryCurrentItem = async () => {
    if (!activeBatch || !currentItem) return;
    setSubmitting(true);
    try {
      const next = await videoEditorApi.retryVideoEditorBatchItem(
        activeBatch.batch_id,
        currentItem.item_id,
      ) as CloudBatch;
      setBatch(next);
      setPollingStopped(false);
      message.success("任务已重新进入处理队列");
    } catch (error) {
      message.error((error as Error).message || "重试失败");
    } finally {
      setSubmitting(false);
    }
  };

  const confirmAndPublish = async () => {
    if (!activeBatch || !currentItem) return;
    if (!canConfirmOutput) {
      message.warning(
        isSandbox
          ? "沙箱没有真实成片，不能交接发布"
          : "真实成片尚未就绪，暂不能交接发布",
      );
      return;
    }
    if (currentStatus === "ready_to_publish") {
      if (!publishHandoffReady) {
        message.warning("成片已确认，但发布交接资产尚未就绪。");
        return;
      }
      navigate(`/publish?from_edit_task=${encodeURIComponent(currentItem.edit_task_id || "")}`);
      return;
    }
    setSubmitting(true);
    try {
      const next = await videoEditorApi.confirmVideoEditorBatchResults(
        activeBatch.batch_id,
        [currentItem.item_id],
      ) as CloudBatch;
      setBatch(next);
      const confirmed = next.items[0] as CloudBatchItem | undefined;
      const handoffTaskId = confirmed?.edit_task_id || currentItem.edit_task_id;
      if (handoffTaskId) {
        navigate(`/publish?from_edit_task=${encodeURIComponent(handoffTaskId)}`);
      } else {
        message.warning("成片已确认，但发布交接资产尚未就绪。");
      }
    } catch (error) {
      message.error((error as Error).message || "成片确认失败");
    } finally {
      setSubmitting(false);
    }
  };

  const downloadFinishedVideo = async () => {
    if (
      activeBatch
      && currentItem
      && !isSandbox
      && currentStatus === "outcome_unknown"
      && !playableResultMediaUrl
    ) {
      setSubmitting(true);
      try {
        const next = await videoEditorApi.createVideoEditorLocalExport(
          activeBatch.batch_id,
          currentItem.item_id,
        ) as CloudBatch;
        pendingLocalDownloadRef.current = next.batch_id;
        setBatch(next);
        setBatches((items) => [
          next,
          ...items.filter((item) => item.batch_id !== next.batch_id),
        ]);
        setPollingStopped(false);
        const exported = next.items[0];
        if (exported?.job?.status === "succeeded" && exported.job.download_url) {
          pendingLocalDownloadRef.current = null;
          downloadBrowserMedia(
            exported.job.download_url,
            exported.selected_title || exported.title || "剪辑成片",
          );
        } else {
          message.info("正在本机免费生成 MP4，完成后会自动下载");
        }
      } catch (error) {
        message.error((error as Error).message || "本机成片生成失败");
      } finally {
        setSubmitting(false);
      }
      return;
    }
    if (!activeBatch || !currentItem || !playableResultMediaUrl || isSandbox) {
      message.warning("真实成片生成后才可以下载");
      return;
    }
    const downloadUrl = currentItem.job?.workflow === "local_preview_export"
      && currentItem.job.download_url
      ? currentItem.job.download_url
      : providerMode === "aliyun"
        ? videoEditorApi.getVideoEditorBatchItemDownloadUrl(
          activeBatch.batch_id,
          currentItem.item_id,
        )
        : playableResultMediaUrl;
    downloadBrowserMedia(
      downloadUrl,
      currentItem?.selected_title || currentItem?.title || selectedSource?.title || "剪辑成片",
    );
  };

  const handlePrimaryAction = () => {
    if (!activeBatch || currentStatus === "idle") {
      void loadQuote(outputProfile, true);
      return;
    }
    if (currentStatus === "awaiting_subtitle_review" && currentItem) {
      void openReview(currentItem);
      return;
    }
    if (currentStatus === "outcome_unknown") {
      void downloadFinishedVideo();
      return;
    }
    if (["failed", "interrupted"].includes(currentStatus)) {
      void retryCurrentItem();
      return;
    }
    if (["awaiting_output_confirmation", "ready_to_publish"].includes(currentStatus)) {
      if (currentItem?.job?.workflow === "local_preview_export") {
        void downloadFinishedVideo();
        return;
      }
      void confirmAndPublish();
    }
  };

  const primaryAction = (() => {
    if (!batch || currentStatus === "idle") {
      return {
        label: isSandbox
          ? "免费预览剪辑方案"
            : configurationBlocked
              ? "查看出片参考与开通说明"
            : "制作发布级成片",
        disabled: !selectedSourceId,
        icon: <CloudOutlined />,
      };
    }
    if (configurationBlocked) {
      return { label: "补齐云配置后可继续", disabled: true, icon: <CloudOutlined /> };
    }
    if (currentStatus === "awaiting_subtitle_review") {
      return {
        label: "审核字幕、粗剪和配乐",
        disabled: false,
        icon: <EditOutlined />,
      };
    }
    if (["queued", "analyzing"].includes(currentStatus)) {
      return { label: "正在分析素材与生成方案", disabled: true, icon: <Spin size="small" /> };
    }
    if (["ready_to_render", "rendering"].includes(currentStatus)) {
      return { label: "正在生成一次正式成片", disabled: true, icon: <Spin size="small" /> };
    }
    if (currentStatus === "outcome_unknown") {
      return {
        label: "本机免费生成并下载",
        disabled: !selectedSourceId,
        icon: <DownloadOutlined />,
      };
    }
    if (currentStatus === "configuration_required") {
      return {
        label: isSandbox ? "体验已完成，云端出片待开通" : "补齐云配置后可继续",
        disabled: true,
        icon: <CloudOutlined />,
      };
    }
    if (["failed", "interrupted"].includes(currentStatus)) {
      return { label: "重试当前任务", disabled: false, icon: <ReloadOutlined /> };
    }
    if (["awaiting_output_confirmation", "ready_to_publish"].includes(currentStatus)) {
      if (currentItem?.job?.workflow === "local_preview_export") {
        return {
          label: "下载成片",
          disabled: !playableResultMediaUrl,
          icon: <DownloadOutlined />,
        };
      }
      return {
        label: "确认成片并去发布",
        disabled: currentStatus === "ready_to_publish" ? !publishHandoffReady : !canConfirmOutput,
        icon: <CheckCircleOutlined />,
      };
    }
    return { label: STATUS_META[currentStatus]?.label || "等待任务状态", disabled: true, icon: <ClockCircleOutlined /> };
  })();

  const previewSegments = reviewSegments.length
    ? reviewSegments
    : normalizeSubtitleSegments(currentItem);
  const visualSpec = resolveVisualSpec(activeBatch?.visual_spec);
  const playbackRate = asNumber(visualSpec.playback_rate, 1.15);
  const previewTitle = reviewTitle || currentItem?.selected_title || titleCandidates(currentItem)[0] || "";
  const localPreview = localOverlayPreview(
    previewSegments,
    previewTitle,
    visualSpec,
    currentItem?.edit_plan?.caption_groups,
    currentItem?.edit_plan?.spoken_ranges,
  );
  const serverPreviewUsesCurrentStyle = (
    activeBatch?.visual_spec?.style_id === DEFAULT_VISUAL_SPEC.style_id
  );
  const serverPreviewHasKineticWords = Boolean(
    currentItem?.overlay_preview?.cues?.some((cue) => (
      (cue.kinetic_words || []).length > 0
    ))
  );
  const overlayPreview = reviewItem || !serverPreviewUsesCurrentStyle || !serverPreviewHasKineticWords
    ? localPreview
    : currentItem?.overlay_preview || localPreview;
  const previewCaption = overlayPreview.cues.find((cue) => (
    cue.start <= previewTime && cue.end >= previewTime
  ));
  const motionEvents = currentItem?.edit_plan?.director_plan?.motion_events || [];
  const activeMotionEvent = previewMode === "plan"
    ? motionEvents.find((event) => event.start <= previewTime && event.end >= previewTime)
    : undefined;
  const titlePreviewTime = previewMode === "plan"
    ? planPreviewElapsed / playbackRate
    : previewTime;
  const titleOpacity = 1;
  const titleOverlayStyle = {
    "--video-editor-title-font-size": `${visualSpec.title.font_size / visualSpec.canvas.width * 100}cqw`,
    "--video-editor-title-line-height": String(visualSpec.title.line_height),
    "--video-editor-title-outline": `${visualSpec.title.outline_width / visualSpec.canvas.width * 100}cqw`,
    left: `${(visualSpec.title.safe_left / visualSpec.canvas.width) * 100}%`,
    top: `${(visualSpec.title.safe_top / visualSpec.canvas.height) * 100}%`,
    color: visualSpec.title.color,
    opacity: titleOpacity,
  } as CSSProperties;
  const accentTop = visualSpec.title.safe_top
    + overlayPreview.title.lines.length * visualSpec.title.font_size * visualSpec.title.line_height
    + visualSpec.accent.gap;
  const accentOverlayStyle = {
    left: `${(visualSpec.title.safe_left / visualSpec.canvas.width) * 100}%`,
    top: `${(accentTop / visualSpec.canvas.height) * 100}%`,
    width: `${(visualSpec.accent.width / visualSpec.canvas.width) * 100}%`,
    height: `${(visualSpec.accent.height / visualSpec.canvas.height) * 100}%`,
    backgroundColor: visualSpec.accent.color,
    opacity: titleOpacity,
  } as CSSProperties;
  const subtitleOverlayStyle = {
    "--video-editor-subtitle-font-size": `${visualSpec.subtitle.font_size / visualSpec.canvas.width * 100}cqw`,
    "--video-editor-subtitle-line-height": "1.28",
    "--video-editor-subtitle-entry-duration": "180ms",
    "--video-editor-subtitle-outline": `${visualSpec.subtitle.outline_width / visualSpec.canvas.width * 100}cqw`,
    left: "7.5%",
    right: "7.5%",
    bottom: `${(visualSpec.subtitle.safe_bottom / visualSpec.canvas.height) * 100}%`,
    color: visualSpec.subtitle.color,
    "--video-editor-subtitle-emphasis": visualSpec.subtitle.emphasis_color,
  } as CSSProperties;
  const titleEnabled = enabledPlanSteps.some((step) => step.kind === "title");
  const activeIntervals = silenceIntervals(enabledPlanSteps);
  const sourceTimelineDuration = previewDuration
    || asNumber(currentItem?.edit_plan?.duration_seconds, 0);
  const planTimelineDuration = planTimeFromSourceTime(
    sourceTimelineDuration,
    activeIntervals,
  ) / playbackRate;
  const planTimelineValue = Math.min(
    planPreviewElapsed / playbackRate,
    planTimelineDuration,
  );

  const togglePlanPreview = () => {
    const video = previewRef.current;
    if (!video) return;
    video.playbackRate = playbackRate;
    if (video.paused) {
      if (video.currentTime <= 0.05 || video.ended) {
        video.currentTime = 0;
        setPreviewTime(0);
        setPlanPreviewElapsed(0);
      }
      void video.play();
    } else {
      video.pause();
    }
  };

  const togglePreviewPlayback = () => {
    if (previewMode === "plan") {
      togglePlanPreview();
      return;
    }
    const video = previewRef.current;
    if (!video) return;
    if (video.paused) void video.play();
    else video.pause();
  };

  const togglePreviewMute = () => {
    const video = previewRef.current;
    if (!video) return;
    video.muted = !video.muted;
    setIsPreviewMuted(video.muted);
  };

  const openPreviewFullscreen = () => {
    const preview = previewRef.current?.parentElement;
    if (preview?.requestFullscreen) void preview.requestFullscreen();
  };

  const handlePreviewTimeUpdate = () => {
    const video = previewRef.current;
    if (!video) return;
    if (previewMode === "plan") {
      const interval = activeIntervals.find(({ start, end }) => (
        video.currentTime >= start && video.currentTime < end
      ));
      if (interval) {
        video.currentTime = interval.end;
        setPreviewTime(interval.end);
        setPlanPreviewElapsed(
          planTimeFromSourceTime(interval.end, activeIntervals),
        );
        return;
      }
      setPlanPreviewElapsed(
        planTimeFromSourceTime(video.currentTime, activeIntervals),
      );
    }
    setPreviewTime(video.currentTime);
  };

  const seekPlanPreview = (nextPlanTime: number) => {
    const video = previewRef.current;
    if (!video || sourceTimelineDuration <= 0) return;
    video.pause();
    const sourceTime = sourceTimeFromPlanTime(
      nextPlanTime * playbackRate,
      sourceTimelineDuration,
      activeIntervals,
    );
    video.currentTime = sourceTime;
    setPreviewTime(sourceTime);
    setPlanPreviewElapsed(nextPlanTime * playbackRate);
  };

  const timelineDuration = previewMode === "plan"
    ? planTimelineDuration
    : sourceTimelineDuration;
  const timelineValue = previewMode === "plan"
    ? planTimelineValue
    : Math.min(previewTime, timelineDuration);
  const seekPreviewTimeline = (nextTime: number) => {
    if (previewMode === "plan") {
      seekPlanPreview(nextTime);
      return;
    }
    const video = previewRef.current;
    if (!video || timelineDuration <= 0) return;
    video.pause();
    video.currentTime = nextTime;
    setPreviewTime(nextTime);
  };

  const returnToSourceSelection = () => {
    if (selectedSourceId) selectSource(selectedSourceId);
  };

  const chooseHistory = (selected: CloudBatch) => {
    const selectedItem = selected.items[0] as CloudBatchItem | undefined;
    setBatch(selected);
    selectedSourceIdRef.current = selectedItem?.source_id;
    setSelectedSourceId(selectedItem?.source_id);
    setOutputProfile(selected.output_profile || "720p");
    setPlatform(selected.target_platform);
    setQuote(selected.cost_quote || null);
    setBgmEnabled(selected.bgm_enabled);
    setBgmId(selected.bgm_id || undefined);
    setBgmVolume(selected.bgm_volume);
    setReviewSegments(normalizeSubtitleSegments(selectedItem));
    setReviewTitle("");
    setHistoryOpen(false);
  };

  if (loading && !sources.length) {
    return (
      <div style={{ marginTop: 96, textAlign: "center" }}>
        <Spin tip="正在加载云端轻量剪辑工作台"><div style={{ minHeight: 48 }} /></Spin>
      </div>
    );
  }

  return (
    <div className="video-editor-cloud-page">
      <header className="video-editor-cloud-header">
        <div>{batch ? statusTag(displayStatus) : null}</div>
        <Space wrap size={8}>
          <Button
            icon={<HistoryOutlined />}
            onClick={() => {
              setHistoryOpen(true);
              void loadHistory();
            }}
          >任务历史</Button>
          <Tooltip title="刷新当前任务状态">
            <Button aria-label="刷新工作台" icon={<ReloadOutlined />} onClick={() => void refresh()} />
          </Tooltip>
        </Space>
      </header>

      <section className="video-editor-context-bar" aria-label="素材与输出设置">
        <div className="video-editor-context-source">
          <Text type="secondary">素材</Text>
          <Select
            aria-label="选择视频素材"
            value={selectedSourceId}
            onChange={selectSource}
            placeholder="选择视频素材"
            options={sources.map((source) => ({
              value: source.source_id,
              label: source.title,
            }))}
          />
          <Upload
            accept="video/mp4,video/quicktime,video/x-m4v"
            maxCount={1}
            showUploadList={false}
            beforeUpload={(file) => {
              void uploadSource(file as File);
              return Upload.LIST_IGNORE;
            }}
          >
            <Tooltip title="上传 MP4 / MOV">
              <Button aria-label="上传 MP4 / MOV" icon={<UploadOutlined />} loading={uploading} />
            </Tooltip>
          </Upload>
        </div>
        <div className="video-editor-context-static">
          <Text type="secondary">画面</Text>
          <Text strong>9:16</Text>
        </div>
        <div className="video-editor-context-profile">
          <Text type="secondary">清晰度</Text>
          <Radio.Group
            aria-label="输出清晰度"
            value={outputProfile}
            onChange={(event) => changeOutputProfile(event.target.value as OutputProfile)}
            optionType="button"
            buttonStyle="solid"
            size="small"
          >
            <Radio.Button value="720p">720P</Radio.Button>
            <Radio.Button value="1080p">1080P</Radio.Button>
          </Radio.Group>
        </div>
      </section>

      <div className="video-editor-cloud-workspace">
        <section className="video-editor-cinema-panel" aria-label="视频预览">
          <div className="video-editor-preview-toolbar">
            <Text strong>预览</Text>
            <Segmented
              value={previewMode}
              onChange={(value) => setPreviewMode(value as PreviewMode)}
              options={[
                { value: "original", label: "原片" },
                { value: "plan", label: "方案预览" },
                { value: "output", label: "成片", disabled: !playableResultMediaUrl },
              ]}
            />
          </div>

          <div className="video-editor-preview-stage">
            {!selectedSource ? (
              <Empty description="在左侧选择一条视频后即可预览" />
            ) : (
              <div className="video-editor-phone-preview">
                <video
                  key={`${previewMode}-${playableResultMediaUrl || selectedSource.media_url}`}
                  ref={previewRef}
                  preload="metadata"
                  src={previewMode === "output" && playableResultMediaUrl ? playableResultMediaUrl : selectedSource.media_url}
                  className={previewMode === "plan" ? "video-editor-plan-video" : ""}
                  onLoadedMetadata={(event) => {
                    const duration = Number.isFinite(event.currentTarget.duration)
                      ? event.currentTarget.duration
                      : 0;
                    setPreviewDuration(duration);
                    setPreviewTime(0);
                    setPlanPreviewElapsed(0);
                    event.currentTarget.playbackRate = previewMode === "plan"
                      ? playbackRate
                      : 1;
                  }}
                  onTimeUpdate={handlePreviewTimeUpdate}
                  onPlay={() => setIsPreviewPlaying(true)}
                  onPause={() => setIsPreviewPlaying(false)}
                  onEnded={() => setIsPreviewPlaying(false)}
                  onClick={previewMode === "plan" ? togglePlanPreview : undefined}
                />
                {previewMode === "plan" && titleEnabled && overlayPreview.title.lines.length > 0 && titlePreviewTime <= overlayPreview.title.end && (
                  <>
                    <div className="video-editor-title-overlay" style={titleOverlayStyle}>
                      {overlayPreview.title.lines.map((line, index) => (
                        <span className="video-editor-overlay-line" key={`${line}-${index}`}>{line}</span>
                      ))}
                    </div>
                    <div className="video-editor-title-accent" style={accentOverlayStyle} />
                  </>
                )}
                {activeMotionEvent && (
                  <div
                    className={`video-editor-motion-accent video-editor-motion-${activeMotionEvent.style_id} video-editor-motion-verb-${activeMotionEvent.visual_verb || "legacy"}`}
                    data-testid="semantic-motion-accent"
                    aria-hidden="true"
                  >
                    <span className="motion-ray" />
                    <span className="motion-ray" />
                    <span className="motion-ray" />
                  </div>
                )}
                {previewMode === "plan" && previewCaption?.lines.length && (
                  <div className="video-editor-subtitle-overlay" style={subtitleOverlayStyle}>
                    {previewCaption.lines.map((line, index) => (
                      <span
                        className={`video-editor-overlay-line ${previewCaption.kinetic_mode === "static" ? "video-editor-subtitle-line-entry" : ""}`}
                        key={`${line}-${index}`}
                      >
                        {renderOverlayLine(
                          line,
                          index,
                          previewCaption.emphasis_range,
                          previewCaption.emphasis_style,
                          previewCaption.kinetic_mode,
                          previewCaption.kinetic_style,
                          previewCaption.kinetic_words,
                          previewCaption.start,
                          previewTime,
                        )}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="video-editor-preview-footer">
            <div className="video-editor-transport">
              <Button
                type="text"
                aria-label={isPreviewPlaying ? "暂停预览" : "播放预览"}
                icon={isPreviewPlaying ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                onClick={togglePreviewPlayback}
              />
              <Text className="video-editor-timeline-time">
                {formatTimelineTime(timelineValue)} / {formatTimelineTime(timelineDuration)}
              </Text>
              <Slider
                className="video-editor-transport-slider"
                ariaLabelForHandle="视频播放进度"
                min={0}
                max={Math.max(timelineDuration, 0.1)}
                step={0.05}
                value={timelineValue}
                tooltip={{ formatter: (value) => formatTimelineTime(asNumber(value)) }}
                onChange={seekPreviewTimeline}
              />
              <Tooltip title={isPreviewMuted ? "开启声音" : "静音"}>
                <Button
                  type="text"
                  aria-label={isPreviewMuted ? "开启声音" : "静音"}
                  icon={isPreviewMuted ? <MutedOutlined /> : <SoundOutlined />}
                  onClick={togglePreviewMute}
                />
              </Tooltip>
              <Tooltip title="全屏预览">
                <Button
                  type="text"
                  aria-label="全屏预览"
                  icon={<FullscreenOutlined />}
                  onClick={openPreviewFullscreen}
                />
              </Tooltip>
            </div>
          </div>
        </section>

        <aside className="video-editor-result-panel" aria-label="本次成片内容与费用">
          <div className="video-editor-result-scroll">
            {isSandbox && (
              <Alert
                data-testid="provider-alert"
                type="info"
                showIcon
                message="免费体验：不调用真实云服务"
              />
            )}
            {configurationBlocked && (
              <Alert
                data-testid="provider-alert"
                type="warning"
                showIcon
                message="云端出片尚未开通"
                description="可先查看剪辑方案；开通后才会生成真实字幕和成片。"
              />
            )}
            {pollingStopped && (
              <Alert
                type="warning"
                showIcon
                message="自动查询已停止"
                description="请手动刷新，确认原任务的最新状态。"
              />
            )}
            {currentStatus === "outcome_unknown" && (
              <Alert
                type="warning"
                showIcon
                message="付费提交结果未知"
                description="旧任务不会自动重提，请先确认任务结果。"
              />
            )}

            <div className="video-editor-result-heading">
              <Title level={4}>本次成片包含</Title>
              <Text type="secondary">以下处理为默认成片内容，无需重复选择。</Text>
            </div>

            {currentItem?.edit_plan?.shot_plan && (
              <Card size="small" data-testid="talking-head-template-summary">
                <Space direction="vertical" size={5} style={{ width: "100%" }}>
                  <Space wrap>
                    <Text strong>自动母版：</Text>
                    <Tag color="blue">
                      自适应综合型
                    </Tag>
                    {currentItem.edit_plan.director_plan?.visual_events && (
                      (() => {
                        const visualEvents = currentItem.edit_plan.director_plan.visual_events;
                        const generatedImageEvents = visualEvents.filter((event) => (
                          event.asset_origin === "generated_image_asset"
                          && Boolean(event.asset_id)
                          && (event.type === "broll_pip" || event.type === "broll_fullscreen")
                        ));
                        const realBrollEvents = visualEvents.filter((event) => (
                          Boolean(event.asset_id)
                          && event.asset_origin !== "generated_image_asset"
                          && (event.type === "broll_pip" || event.type === "broll_fullscreen")
                        ));
                        const quality = currentItem.job?.quality_report;
                        const realCount = quality?.real_broll_event_count ?? realBrollEvents.length;
                        const generatedCount = quality?.generated_image_event_count ?? generatedImageEvents.length;
                        return (
                          <>
                            <Tag color={quality?.visual_gate_policy?.passed ? "green" : "gold"}>
                              真实授权 B-roll {realCount} 个
                            </Tag>
                            {generatedCount > 0 && (
                              <Tag color="cyan">
                                生成图（仅本地验收） {generatedCount}
                              </Tag>
                            )}
                          </>
                        );
                      })()
                    )}
                    {currentItem.edit_plan.vector_track && (currentItem.edit_plan.vector_track.asset_count || 0) > 0 && (
                      <Tag color="cyan">
                        自动匹配 {currentItem.edit_plan.vector_track.asset_count} 个透明矢量素材
                      </Tag>
                    )}
                    {currentItem.edit_plan.shot_plan.degradation?.mode === "精剪口播降级" && (
                      <Tag color="gold">精剪口播降级</Tag>
                    )}
                  </Space>
                  <Text type="secondary">
                    {currentItem.job?.quality_report?.shot_plan?.degradation?.message
                      || currentItem.edit_plan.shot_plan.degradation?.message
                      || "分镜、字幕、动效和配乐共用同一时间轴。"}
                  </Text>
                </Space>
              </Card>
            )}

            {currentItem?.edit_plan?.director_plan && (
              <Card size="small" data-testid="director-plan-summary">
                <Space direction="vertical" size={5} style={{ width: "100%" }}>
                  {(() => {
                    const semanticDirector = currentItem.edit_plan.director_plan?.semantic_director;
                    const creativeDirector = currentItem.edit_plan.director_plan?.creative_director;
                    const providerUsed = semanticDirector?.provider === "minimax"
                      && String(semanticDirector.status || "").startsWith("used");
                    const previewReview = currentItem.edit_plan.director_plan?.director_preview_review;
                    const previewReviewCompleted = previewReview?.status === "completed"
                      && previewReview.preview_rendered === true;
                    return (
                      <Space wrap>
                        <Tag color={providerUsed ? "green" : "gold"}>
                          {providerUsed ? "智能导演已生成提案" : "本地安全方案"}
                        </Tag>
                        <Tag>
                          语义理解 {semanticDirector?.provider_annotation_count ?? 0} / {semanticDirector?.input_segment_count ?? 0}
                        </Tag>
                        <Tag color="orange">
                          接受 {creativeDirector?.compiled_count ?? semanticDirector?.compiled_count ?? 0}
                        </Tag>
                        {(creativeDirector?.rejected_count ?? semanticDirector?.rejected_count ?? 0) > 0 && (
                          <Tag color="red">
                            已拒绝 {creativeDirector?.rejected_count ?? semanticDirector?.rejected_count ?? 0}
                          </Tag>
                        )}
                        <Tag color={!providerUsed ? undefined : previewReviewCompleted ? "green" : previewReview?.status === "failed" ? "red" : "blue"}>
                          {!providerUsed
                            ? "低清复核不适用"
                            : previewReviewCompleted
                              ? "低清复核已完成"
                              : previewReview?.status === "failed"
                                ? "低清复核未通过"
                                : "低清复核待执行"}
                        </Tag>
                      </Space>
                    );
                  })()}
                  <Space wrap>
                    <Text strong>AI 导演计划：</Text>
                    <Tag color="purple">{currentItem.edit_plan.director_plan.plan_version}</Tag>
                    <Tag color="blue">
                      {currentItem.edit_plan.director_plan.scenes?.length || 0} 个场景
                    </Tag>
                    {(currentItem.edit_plan.director_plan.motion_events?.length || 0) > 0 && (
                      <Tag color="orange">
                        语义动效 {currentItem.edit_plan.director_plan.motion_events?.length} 个
                      </Tag>
                    )}
                  </Space>
                  <Text type="secondary">
                    钩子使用原片完整原话，只出现一次；字幕、画面和配乐共用一条时间轴。
                  </Text>
                  {currentItem.edit_plan.director_plan.cache_upgrade_notice && (
                    <Text type="warning">
                      {currentItem.edit_plan.director_plan.cache_upgrade_notice}
                    </Text>
                  )}
                  <Text type="secondary">
                    {currentItem.edit_plan.director_plan.semantic_director?.provider === "minimax"
                      ? "已完成语义理解 → 已生成剪辑提案 → 已通过本地检查；低清复核和正式渲染会继续显示真实状态。"
                      : "已完成语义理解；当前没有可用的 MiniMax 提案，已保留人物主镜头和安全降级。"}
                  </Text>
                  <Text type="secondary">
                    {currentItem.edit_plan.director_plan.asset_requests?.length
                      ? `已生成 ${currentItem.edit_plan.director_plan.asset_requests.length} 个图片素材需求，等待生图接口和本条预算。`
                      : currentItem.edit_plan.vector_track && (currentItem.edit_plan.vector_track.asset_count || 0) > 0
                        ? `已按${currentItem.edit_plan.vector_track.theme || "当前"}主题自动匹配透明矢量素材，独立视觉轨道会在字幕前渲染，并保留来源与许可证记录。`
                      : currentItem.job?.quality_report?.shot_plan?.degradation?.mode === "authorized_real_broll"
                        ? "当前成片使用语义相关且来源可追溯的真实素材，已按模板视觉门完成验收。"
                      : currentItem.job?.quality_report?.shot_plan?.degradation?.mode === "a_roll_safe_degradation"
                        ? "当前没有可靠语义素材，已保留人物主画面和安全虚拟运镜。"
                      : currentItem.edit_plan.director_plan.degradation?.mode === "generated_image_broll"
                        ? "已绑定生成图并完成本地视觉验收；生成图不代表免费素材授权，也不开放发布声明。"
                      : currentItem.edit_plan.director_plan.degradation?.mode === "精剪口播降级"
                        ? "当前没有可用的真实 B-roll，已安全降级为人物主画面；配置素材后才会进入发布级视觉验收。"
                        : "当前已绑定视觉素材，系统将按分镜自动安排画中画或全屏画面。"}
                  </Text>
                  <Text type="secondary">
                    质量门：{currentItem.job?.quality_report?.visual_gate_policy?.language
                      || "按内容类型检查有意义的视觉变化；无可靠素材时安全降级。"}
                    音画漂移不超过 67ms；无关素材、生成图和矢量不能冒充已授权真实素材。
                  </Text>
                </Space>
              </Card>
            )}

            {isLocalExport && currentItem?.job?.quality_report && (
              <Card size="small" data-testid="local-quality-summary">
                <Space direction="vertical" size={5} style={{ width: "100%" }}>
                  <Space wrap>
                    <Text strong>本机成片验收：</Text>
                    <Tag color={currentItem.job.quality_report.passed ? "green" : "red"}>
                      {currentItem.job.quality_report.passed ? "质量门通过" : "质量门未通过"}
                    </Tag>
                    <Tag color="blue">
                      真实 B-roll {currentItem.job.quality_report.real_broll_event_count || 0} 个
                    </Tag>
                    <Tag>
                      国内 {currentItem.job.quality_report.domestic_real_broll_event_count || 0}
                    </Tag>
                    <Tag>
                      国际 {currentItem.job.quality_report.international_broll_event_count || 0}
                    </Tag>
                    {(currentItem.job.quality_report.generated_image_event_count || 0) > 0 && (
                      <Tag color="cyan">
                        生成图（仅本地） {currentItem.job.quality_report.generated_image_event_count}
                      </Tag>
                    )}
                  </Space>
                  <Text type="secondary">
                    PiP {currentItem.job.quality_report.broll_modes?.pip || 0} 个，
                    全屏 {currentItem.job.quality_report.broll_modes?.full || 0} 个；
                    覆盖 {Math.round((currentItem.job.quality_report.real_broll_coverage_ratio || 0) * 100)}%
                  </Text>
                </Space>
              </Card>
            )}

            <div className="video-editor-result-list">
              {RESULT_SUMMARY_ITEMS.filter((item) => item.key !== "background_music" || bgmEnabled).map((item) => (
                <div className="video-editor-result-item" key={item.key}>
                  <CheckCircleOutlined />
                  <div>
                    <Text strong>{item.label}</Text>
                    <Text type="secondary">{item.reason}</Text>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="video-editor-result-actions">
            <div className="video-editor-cost-header">
              <div>
                <Text>{isLocalExport ? "本次下载新增费用" : isSandbox ? "参考费用" : "预计费用"}</Text>
                <Text type="secondary">画面 9:16 · {outputProfile === "720p" ? "720P" : "1080P"}</Text>
              </div>
              <Text className="video-editor-cost-total" data-testid="cost-total">
                {isLocalExport ? "0 积分" : formatCost(costUpperBound)}
              </Text>
            </div>
            {isLocalExport && quote && (
              <div className="video-editor-prior-quote" data-testid="prior-cloud-quote">
                <Text type="secondary">此前云端处理报价{quoteExpired ? "（已过期）" : ""}</Text>
                <Text strong>{formatCost(costUpperBound)}</Text>
              </div>
            )}
            {quote?.quote_id && !isLocalExport && (
              <Text type="secondary">{quoteExpired ? "报价已过期，请重新确认" : "确认后才会开始计费制作"}</Text>
            )}
            {quote && quoteBreakdown.length > 0 && (
              <details className="video-editor-cost-details">
                <summary>{isLocalExport ? "查看原云端报价明细" : "查看费用明细"}</summary>
                <div>
                  {quoteBreakdown.map((line) => (
                    <p key={line.key}>
                      <Text type="secondary">{line.label}</Text>
                      <Text>{formatCost(line.amount)}</Text>
                    </p>
                  ))}
                </div>
                <Text type="secondary">
                  {quote.exclusions?.length
                    ? `未包含：${quote.exclusions.join("、")}。`
                    : "实际扣费以云服务商最终账单为准。"}
                </Text>
              </details>
            )}
            {playableResultMediaUrl && primaryAction.label !== "下载成片" && (
              <Button icon={<DownloadOutlined />} onClick={downloadFinishedVideo}>
                下载成片
              </Button>
            )}
            <div className="video-editor-primary-zone">
              <Button
                data-testid="primary-action"
                type={quoteOpen || Boolean(reviewItem) ? "default" : "primary"}
                size="large"
                block
                icon={primaryAction.icon}
                loading={submitting || quoteLoading}
                disabled={primaryAction.disabled || quoteOpen || Boolean(reviewItem)}
                onClick={handlePrimaryAction}
              >
                {primaryAction.label}
              </Button>
              <Text type="secondary">
                {batch ? STATUS_META[displayStatus]?.label || displayStatus : "确认费用后才会开始制作"}
              </Text>
            </div>
            <Button type="link" onClick={returnToSourceSelection} disabled={!selectedSourceId}>
              返回选择素材
            </Button>
          </div>
        </aside>
      </div>

      <Modal
        title={isSandbox ? "免费体验剪辑方案" : "确认预计费用"}
        open={quoteOpen}
        onCancel={() => setQuoteOpen(false)}
        width={560}
        footer={[
          <Button key="cancel" onClick={() => setQuoteOpen(false)}>{isSandbox ? "暂不体验" : "暂不开始"}</Button>,
          <Button
            key="confirm"
            type="primary"
            loading={submitting || quoteLoading}
            disabled={!quote || quoteExpired || quoteBlocked}
            onClick={() => void startAnalysis()}
          >
            {isSandbox ? "开始免费体验" : "确认费用并开始分析"}
          </Button>,
        ]}
      >
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Alert
            type={isSandbox ? "info" : quoteBlocked ? "warning" : "success"}
            showIcon
            message={isSandbox ? "本次免费，不会调用云端识别或出片" : quoteBlocked ? "供应商尚未准备完成" : "确认后才会调用云服务"}
            description={
              isSandbox
                ? "体验方案仅用于熟悉流程和人工确认；不会生成真实字幕或成片。"
                : "预计上限不含 OSS、出网和失败重试；报价过期或价格版本变化需再次确认。"
            }
          />
          <Descriptions
            size="small"
            column={1}
            items={[
              { key: "profile", label: isSandbox ? "未来出片档位" : "输出档位", children: PROFILE_META[outputProfile].label },
              { key: "source", label: "素材", children: selectedSource?.title || "未选择" },
              {
                key: "expires",
                label: "报价有效期",
                children: quote?.expires_at
                  ? new Date(quote.expires_at).toLocaleString("zh-CN", { hour12: false })
                  : "等待报价",
              },
            ]}
          />
          <List
            size="small"
            bordered
            dataSource={quoteBreakdown}
            locale={{ emptyText: "暂无费用分项" }}
            renderItem={(line) => (
              <List.Item extra={<Text strong>{formatCost(line.amount)}</Text>}>
                {line.label}
              </List.Item>
            )}
          />
          <div className="video-editor-modal-total">
            <Text strong>{isSandbox ? "未来云端出片参考上限" : "预计费用上限"}</Text>
            <Title level={3} style={{ margin: 0 }}>{formatCost(costUpperBound)}</Title>
          </div>
          {!!(quote?.blocked_reasons?.length || quote?.blocking_reasons?.length) && (
            <Alert
              type="warning"
              showIcon
              message="当前报价被阻塞"
              description={(quote.blocked_reasons || quote.blocking_reasons || []).join("；")}
            />
          )}
        </Space>
      </Modal>

      <Drawer
        title={isSandbox ? "字幕与方案体验" : "字幕与方案复核"}
        width={760}
        open={Boolean(reviewItem)}
        onClose={() => setReviewItem(null)}
        extra={(
          <Button type="primary" loading={reviewSaving} onClick={() => void saveReview()}>
            {isSandbox ? "保存体验方案" : "确认复核并生成"}
          </Button>
        )}
      >
        {reviewLoading ? <Spin /> : (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Alert
              type={isSandbox ? "info" : "warning"}
              showIcon
              message={isSandbox ? "体验模式只保存你的选择" : "未明确确认，不会提交 MPS"}
              description={isSandbox
                ? "不会调用真实字幕识别或生成成片；开通云端后，可用相同流程处理真实素材。"
                : "字幕修订、启用的方案步骤、标题和 BGM 将原子保存到当前单项任务。"}
            />
            <Tabs
              items={[
                {
                  key: "subtitle",
                  label: `字幕校对（${reviewSegments.length}）`,
                  children: reviewSegments.length ? (
                    <Space direction="vertical" size={10} style={{ width: "100%" }}>
                      {reviewSegments.map((segment, index) => (
                        <Card
                          size="small"
                          key={`${segment.start}-${segment.end}-${index}`}
                          title={`${asNumber(segment.start).toFixed(1)}s – ${asNumber(segment.end).toFixed(1)}s`}
                          extra={segment.needs_review ? <Tag color="gold">待核对</Tag> : <Tag>已识别</Tag>}
                          styles={{ body: { padding: 12 } }}
                        >
                          <Input.TextArea
                            aria-label={`字幕片段 ${index + 1}`}
                            value={segment.text}
                            autoSize={{ minRows: 2, maxRows: 5 }}
                            onChange={(event) => setReviewSegments((segments) => segments.map(
                              (item, itemIndex) => itemIndex === index
                                ? { ...item, text: event.target.value, reviewed: true }
                                : item,
                            ))}
                          />
                          <Input
                            aria-label={`字幕片段 ${index + 1} 的强调词`}
                            value={segment.emphasis_terms?.[0] || ""}
                            maxLength={6}
                            placeholder="强调词（可选，AI 已预选，原文最多 6 字）"
                            style={{ marginTop: 8 }}
                            onChange={(event) => setReviewSegments((segments) => segments.map(
                              (item, itemIndex) => itemIndex === index
                                ? {
                                    ...item,
                                    emphasis_terms: event.target.value.trim()
                                      ? [event.target.value.trim()]
                                      : [],
                                    emphasis_kind: /\d/.test(event.target.value)
                                      ? "number"
                                      : item.emphasis_kind || "keyword",
                                    reviewed: true,
                                  }
                                : item,
                            ))}
                          />
                        </Card>
                      ))}
                    </Space>
                  ) : (
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description={isSandbox
                        ? "免费体验不会调用真实字幕识别"
                        : "没有识别到画面内的人声口播，可自行填写口播文案，生成的脚本仍可作参考"}
                    />
                  ),
                },
                {
                  key: "plan",
                  label: "自动方案",
                  children: (
                    <Space direction="vertical" size={10} style={{ width: "100%" }}>
                      {currentItem?.edit_plan?.shot_plan && (
                        <Card size="small" title="自动选择的口播母版">
                          <Space direction="vertical" size={6} style={{ width: "100%" }}>
                            <Space wrap>
                              <Tag color="blue">
                                自适应综合型
                              </Tag>
                              <Text type="secondary">
                                {currentItem.edit_plan.shot_plan.template_version}
                              </Text>
                              {currentItem.edit_plan.shot_plan.degradation?.mode === "精剪口播降级" && (
                                <Tag color="gold">精剪口播降级</Tag>
                              )}
                            </Space>
                            <Text type="secondary">
                              {currentItem.edit_plan.shot_plan.selection?.reason || "系统按语义、句式和内容完整性自动选择。"}
                            </Text>
                            <Text type="secondary">
                              {currentItem.edit_plan.shot_plan.degradation?.message || "分镜、字幕、动效和配乐共用同一时间轴。"}
                            </Text>
                          </Space>
                        </Card>
                      )}
                      <Alert
                        type="success"
                        showIcon
                        message="系统自动组织剪辑方案并做发布门禁检查"
                        description="系统自动选择母版、识别报号/残句、安排钩子、绑定统一时间轴、匹配授权配乐和可用视觉素材；没有真实视觉素材时会明确降级，不把字幕卡片冒充 B-roll。"
                      />
                      {planSteps.map((step) => (
                        <Card
                          key={step.id}
                          size="small"
                          styles={{ body: { padding: 12 } }}
                        >
                          <Space align="start">
                            <Tag color="green">自动</Tag>
                            <Space direction="vertical" size={2}>
                              <Text strong>{step.label}</Text>
                              <Text type="secondary">{step.reason}</Text>
                            </Space>
                          </Space>
                        </Card>
                      ))}
                    </Space>
                  ),
                },
                {
                  key: "broll",
                  label: "画面素材（可选）",
                  children: (
                    <Space direction="vertical" size={14} style={{ width: "100%" }}>
                  <Alert
                        type="info"
                        showIcon
                        message="视觉素材由系统自动匹配"
                        description="你上传到素材库的已授权图片/视频会按口播内容自动安排为画中画或全屏画面；这里的手动绑定仅作为高级覆盖。未配置素材或生图接口时，系统会安全降级并明确标注。"
                      />
                      <Upload
                        accept=".png,.jpg,.jpeg,.webp,.mp4,.mov,.m4v"
                        showUploadList={false}
                        beforeUpload={(file) => {
                          void uploadBroll(file as File);
                          return false;
                        }}
                      >
                        <Button icon={<UploadOutlined />} loading={brollUploading}>
                          上传图片或视频素材
                        </Button>
                      </Upload>
                      <Select
                        aria-label="选择 B-roll 素材"
                        allowClear
                        value={reviewBrollAsset?.asset_id || undefined}
                        placeholder="不添加 B-roll（保持原模板）"
                        options={brollAssets.map((asset) => ({
                          value: asset.asset_id,
                          label: `${asset.name} · ${asset.media_kind === "image" ? "图片" : "视频"}${asset.asset_origin === "generated_image_asset" ? " · 生成图（仅本地验收）" : ""}`,
                        }))}
                        onChange={(value) => {
                          const asset = brollAssets.find((item) => item.asset_id === value) || null;
                          setReviewBrollAsset(asset);
                          if (!asset) {
                            setReviewBrollStart(null);
                            setReviewBrollEnd(null);
                          }
                        }}
                        style={{ width: "100%" }}
                      />
                      {reviewBrollAsset && (
                        <Card size="small" title={`绑定《${reviewBrollAsset.name}》`}>
                          <Space direction="vertical" size={10} style={{ width: "100%" }}>
                            <Space wrap>
                              <Text>出现时间（原片）</Text>
                              <InputNumber
                                aria-label="B-roll 开始时间"
                                min={0}
                                step={0.1}
                                precision={1}
                                value={reviewBrollStart ?? undefined}
                                onChange={(value) => setReviewBrollStart(value === null ? null : Number(value))}
                                addonAfter="秒"
                              />
                              <Text>到</Text>
                              <InputNumber
                                aria-label="B-roll 结束时间"
                                min={0.1}
                                step={0.1}
                                precision={1}
                                value={reviewBrollEnd ?? undefined}
                                onChange={(value) => setReviewBrollEnd(value === null ? null : Number(value))}
                                addonAfter="秒"
                              />
                              <Button
                                onClick={() => {
                                  const segment = reviewSegments[0];
                                  if (segment) {
                                    setReviewBrollStart(Number(segment.start));
                                    setReviewBrollEnd(Number(segment.end));
                                  }
                                }}
                              >
                                绑定第一句字幕
                              </Button>
                            </Space>
                            <Radio.Group
                              value={reviewBrollMode}
                              onChange={(event) => setReviewBrollMode(event.target.value)}
                              options={[
                                { label: "下方侧边安全区画中画", value: "pip" },
                                { label: "全屏替换", value: "full" },
                              ]}
                            />
                            <Text type="secondary">
                              只允许绑定在原片时间轴内；画中画会自动放到下方侧边安全区，避开人物头脸和字幕，人声与字幕仍按已确认时间轴输出。
                            </Text>
                          </Space>
                        </Card>
                      )}
                    </Space>
                  ),
                },
                {
                  key: "title",
                  label: "标题与配乐",
                  children: (
                    <Space direction="vertical" size={16} style={{ width: "100%" }}>
                      <div>
                        <Text strong>成片标题</Text>
                        <Input
                          aria-label="成片标题"
                          value={reviewTitle}
                          onChange={(event) => setReviewTitle(event.target.value)}
                          maxLength={60}
                          showCount
                          style={{ marginTop: 8 }}
                        />
                      </div>
                      <div>
                        <Text strong>AI 已选配乐</Text>
                        <Text type="secondary" style={{ display: "block", marginTop: 4 }}>
                          已按文案与视频语气自动匹配；不合适可换一首，清空则保持原声。
                        </Text>
                        {reviewBgmExplanation && (
                          <Text type="secondary" style={{ display: "block", marginTop: 4 }}>
                            {reviewBgmExplanation}
                          </Text>
                        )}
                        {!usableBgmAssets.length && (
                          <Alert
                            type="warning"
                            showIcon
                            style={{ marginTop: 10 }}
                            message={bgmAssets.length ? "当前配乐授权尚未核验，已安全保持原声" : "暂未配置可自动使用的授权配乐"}
                            description={
                              bgmAssets.length
                                ? "未核验商用授权的音乐不会被自动带入正式渲染；如需配乐，请补充授权凭证或继续保持原声。"
                                : "上传或配置一首有明确使用权的音乐后，系统才会按口播语气自动匹配。"
                            }
                          />
                        )}
                        <Select
                          aria-label="复核背景音乐"
                          allowClear
                          value={reviewBgmId || undefined}
                          onChange={(value) => {
                            const nextValue = value || null;
                            setReviewBgmId(nextValue);
                            setReviewPlanStepIds((ids) => nextValue
                              ? Array.from(new Set([...ids, "bgm"]))
                              : ids.filter((id) => id !== "bgm"));
                          }}
                          placeholder="本次保持原声"
                          options={bgmAssets.map((asset) => ({
                            value: asset.asset_id,
                            label: formatBgmOptionLabel(asset),
                          }))}
                          style={{ width: "100%", marginTop: 8 }}
                        />
                        {selectedReviewBgm && (
                          <div
                            style={{
                              marginTop: 10,
                              padding: 12,
                              border: "1px solid var(--border-default)",
                              borderRadius: 10,
                              background: "var(--surface-muted)",
                            }}
                          >
                            <Space size={8} style={{ marginBottom: 8 }}>
                              <SoundOutlined />
                              <Text strong>{selectedReviewBgm.title}</Text>
                              <Tag color="blue">
                                {selectedReviewBgm.voiceover_category || selectedReviewBgm.mood}
                              </Tag>
                              <Tag>{selectedReviewBgm.energy || "克制"}</Tag>
                              <Text type="secondary">
                                {Math.round(selectedReviewBgm.duration_seconds)} 秒
                              </Text>
                            </Space>
                            <Space wrap size={6} style={{ marginBottom: 8 }}>
                              <Text type="secondary">
                                来源：{getBgmSourceLabel(selectedReviewBgm)}
                              </Text>
                              {selectedReviewBgm.content_id_risk === "registered" && (
                                <Tag color="orange">可能触发平台版权识别</Tag>
                              )}
                              {selectedReviewBgm.source_url && (
                                <Typography.Link href={selectedReviewBgm.source_url} target="_blank">
                                  查看素材来源
                                </Typography.Link>
                              )}
                              {selectedReviewBgm.license_url && (
                                <Typography.Link href={selectedReviewBgm.license_url} target="_blank">
                                  查看授权说明
                                </Typography.Link>
                              )}
                            </Space>
                            <audio
                              controls
                              preload="metadata"
                              src={selectedReviewBgm.media_url}
                              aria-label={`试听背景音乐：${selectedReviewBgm.title}`}
                              style={{ width: "100%", display: "block" }}
                            />
                          </div>
                        )}
                      </div>
                    </Space>
                  ),
                },
              ]}
            />
          </Space>
        )}
      </Drawer>

      <Drawer
        title="智能剪辑任务历史"
        width={560}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
      >
        <List
          loading={historyLoading}
          dataSource={batches}
          locale={{ emptyText: "还没有云端轻量剪辑任务" }}
          renderItem={(item) => {
            const firstItem = item.items[0] as CloudBatchItem | undefined;
            return (
              <List.Item
                actions={[
                  <Button key="open" onClick={() => chooseHistory(item)}>打开</Button>,
                ]}
              >
                <List.Item.Meta
                  title={<Space wrap><Text strong>{firstItem?.title || "单条剪辑任务"}</Text>{statusTag(firstItem?.status || item.status)}</Space>}
                  description={(
                    <Space direction="vertical" size={2}>
                      <Text type="secondary">
                        {PROFILE_META[item.output_profile || "720p"].label} · {item.target_platform}
                      </Text>
                      <Text type="secondary">
                        {new Date(item.updated_at).toLocaleString("zh-CN", { hour12: false })}
                      </Text>
                    </Space>
                  )}
                />
              </List.Item>
            );
          }}
        />
      </Drawer>

      <style>{`
        .video-editor-cloud-page{display:flex;flex-direction:column;gap:12px;width:100%;min-width:0}
        .video-editor-cloud-header{display:flex;min-height:36px;align-items:center;justify-content:space-between;gap:16px}
        .video-editor-context-bar{display:flex;min-height:58px;align-items:center;gap:0;overflow:hidden;border:1px solid var(--border-default);border-radius:var(--radius-md);background:var(--bg-card);box-shadow:var(--shadow-sm)}
        .video-editor-context-source,.video-editor-context-static,.video-editor-context-profile{display:flex;align-items:center;gap:10px;padding:10px 16px}
        .video-editor-context-source{min-width:0;flex:1}
        .video-editor-context-source>.ant-select{min-width:220px;max-width:560px;flex:1}
        .video-editor-context-static,.video-editor-context-profile{flex:none;border-left:1px solid var(--border-default)}
        .video-editor-context-bar .ant-typography-secondary{white-space:nowrap}
        .video-editor-cloud-workspace{display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,340px);gap:14px;height:clamp(620px,calc(100dvh - 210px),820px);min-height:0}
        .video-editor-cinema-panel{display:flex;min-width:0;min-height:0;flex-direction:column;overflow:hidden;border:1px solid #202737;border-radius:var(--radius-md);background:#111827;box-shadow:var(--shadow-sm)}
        .video-editor-preview-toolbar,.video-editor-cost-header,.video-editor-modal-total{display:flex;align-items:center;justify-content:space-between;gap:12px}
        .video-editor-preview-toolbar{min-height:52px;flex:none;padding:10px 16px;border-bottom:1px solid var(--border-default);background:var(--bg-card)}
        .video-editor-preview-stage{display:flex;min-height:0;flex:1;align-items:center;justify-content:center;padding:20px;background:#101827}
        .video-editor-preview-stage .ant-empty-description{color:#d1d5db}
        .video-editor-phone-preview{position:relative;width:auto;height:100%;max-width:100%;max-height:540px;aspect-ratio:9/16;overflow:hidden;container-type:inline-size;border:1px solid rgba(255,255,255,.22);border-radius:18px;background:#030712;box-shadow:0 18px 42px rgba(0,0,0,.34)}
        .video-editor-phone-preview video{width:100%;height:100%;object-fit:contain;background:#030712}
        .video-editor-phone-preview video.video-editor-plan-video{object-fit:contain}
        @font-face{font-family:"VideoInsight Title Serif";src:url("/api/v1/video-editor/brand-title-font") format("opentype");font-display:swap;font-style:normal;font-weight:900}
        @font-face{font-family:"VideoInsight Caption Pop";src:url("/api/v1/video-editor/caption-font") format("truetype");font-style:oblique;font-weight:700 900;font-display:swap}
        .video-editor-title-overlay{position:absolute;width:76%;font-family:"VideoInsight Title Serif","Microsoft YaHei UI",serif;font-size:var(--video-editor-title-font-size);font-weight:900;line-height:var(--video-editor-title-line-height);letter-spacing:.01em;text-align:left;white-space:normal;-webkit-text-stroke:var(--video-editor-title-outline) rgba(0,0,0,.72);paint-order:stroke fill;text-shadow:0 2px 7px rgba(0,0,0,.42),0 1px 2px rgba(0,0,0,.62);pointer-events:none;transition:opacity .12s linear}
        .video-editor-title-accent{position:absolute;border-radius:999px;pointer-events:none;transition:opacity .12s linear}
        .video-editor-subtitle-overlay{position:absolute;font-family:"VideoInsight Caption Pop","Smiley Sans","Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif;font-size:var(--video-editor-subtitle-font-size);font-weight:900;font-style:oblique;line-height:var(--video-editor-subtitle-line-height);letter-spacing:.01em;text-align:center;white-space:nowrap;-webkit-text-stroke:var(--video-editor-subtitle-outline) rgba(0,0,0,.88);paint-order:stroke fill;text-shadow:0 1px 0 rgba(0,0,0,.72),0 2px 4px rgba(0,0,0,.22);pointer-events:none}
        .video-editor-overlay-line{display:block}
        .video-editor-subtitle-line-entry{animation:video-editor-subtitle-line-entry var(--video-editor-subtitle-entry-duration,180ms) cubic-bezier(.18,.9,.28,1.18) both;transform-origin:center bottom}
        .video-editor-subtitle-kinetic-word{display:inline-block;transform-origin:center bottom;transition:transform 90ms cubic-bezier(.2,.9,.3,1.18),color 110ms ease,opacity 110ms ease,text-shadow 110ms ease;will-change:transform,color,opacity,text-shadow;-webkit-text-stroke:var(--video-editor-subtitle-outline) rgba(0,0,0,.94);paint-order:stroke fill}
        .video-editor-subtitle-emphasis{display:inline-block;color:var(--video-editor-subtitle-emphasis);font-size:calc(var(--video-editor-emphasis-size,1.08) * 1em);line-height:1;vertical-align:baseline;-webkit-text-stroke:var(--video-editor-subtitle-outline) rgba(0,0,0,.88);paint-order:stroke fill;text-shadow:0 1px 0 rgba(0,0,0,.72),0 2px 4px rgba(0,0,0,.22);animation:video-editor-emphasis-pop var(--video-editor-emphasis-duration,200ms) cubic-bezier(.2,.9,.3,1.18) both;transform-origin:center bottom}
        @keyframes video-editor-subtitle-line-entry{0%{opacity:0;filter:blur(1.4px);transform:translateY(10px) scale(.9) rotate(-1deg)}68%{opacity:1;filter:blur(0);transform:translateY(-2px) scale(1.025) rotate(.3deg)}100%{opacity:1;filter:blur(0);transform:translateY(0) scale(1) rotate(0)}}
        @keyframes video-editor-emphasis-pop{0%{transform:scale(.98)}68%{transform:scale(1.08)}100%{transform:scale(1)}}
        .video-editor-motion-accent{position:absolute;z-index:3;left:50%;top:76%;width:74%;height:22%;pointer-events:none;transform:translate(-50%,-50%);opacity:.94;filter:drop-shadow(0 5px 8px rgba(0,0,0,.28));animation:video-editor-motion-accent-in 220ms cubic-bezier(.2,.9,.3,1.18) both}
        .video-editor-motion-accent::before,.video-editor-motion-accent::after{position:absolute;content:"";border-radius:999px;background:var(--motion-accent-color,#FFD166)}
        .video-editor-motion-accent::before{left:16%;right:16%;bottom:10%;height:3px;transform:rotate(-2deg);box-shadow:0 7px 0 rgba(255,255,255,.72)}
        .video-editor-motion-accent::after{left:50%;top:14%;width:15%;height:15%;transform:translate(-50%,-50%) rotate(45deg);opacity:.88}
        .video-editor-motion-accent .motion-ray{position:absolute;left:50%;top:46%;width:30%;height:3px;background:var(--motion-accent-color,#FFD166);transform-origin:left center;opacity:.72}
        .video-editor-motion-accent .motion-ray:nth-child(1){transform:rotate(-32deg) translateX(66%)}
        .video-editor-motion-accent .motion-ray:nth-child(2){transform:rotate(28deg) translateX(66%)}
        .video-editor-motion-accent .motion-ray:nth-child(3){transform:rotate(0deg) translateX(66%);width:22%}
        .video-editor-motion-road_push::before{left:35%;right:35%;top:10%;bottom:0;height:auto;background:linear-gradient(90deg,#252b34 0 43%,#ffd35c 43% 46%,#252b34 46% 54%,#ffd35c 54% 57%,#252b34 57%);clip-path:polygon(38% 0,62% 0,100% 100%,0 100%);border:2px solid rgba(248,250,252,.8);box-shadow:none;transform:none}
        .video-editor-motion-road_push::after{left:49%;top:16%;width:2%;height:76%;background:repeating-linear-gradient(to bottom,#ffd35c 0 10%,transparent 10% 19%);transform:none;opacity:1}
        .video-editor-motion-road_push .motion-ray{background:#ff9f68}
        .video-editor-motion-number_slam{--motion-accent-color:#FFD166;animation-name:video-editor-motion-slam}
        .video-editor-motion-process_marker{--motion-accent-color:#FF9F68;animation-name:video-editor-motion-marker}
        .video-editor-motion-warning_shake{--motion-accent-color:#FF7A70;animation-name:video-editor-motion-shake}
        .video-editor-motion-result_stamp{--motion-accent-color:#FF8A7A;animation-name:video-editor-motion-stamp}
        .video-editor-motion-cta_burst{--motion-accent-color:#FFC857;animation-name:video-editor-motion-burst}
        @keyframes video-editor-motion-accent-in{0%{opacity:0;transform:translate(-50%,-50%) scale(.9)}100%{opacity:.94;transform:translate(-50%,-50%) scale(1)}}
        @keyframes video-editor-motion-slam{0%{opacity:0;transform:translate(-50%,-50%) scale(.55) rotate(-5deg)}68%{opacity:1;transform:translate(-50%,-50%) scale(1.08) rotate(1deg)}100%{opacity:.94;transform:translate(-50%,-50%) scale(1) rotate(0)}}
        @keyframes video-editor-motion-marker{0%{opacity:0;transform:translate(-50%,-50%) scaleX(.55)}100%{opacity:.94;transform:translate(-50%,-50%) scaleX(1)}}
        @keyframes video-editor-motion-stamp{0%{opacity:0;transform:translate(-50%,-50%) scale(.72) rotate(-8deg)}72%{opacity:1;transform:translate(-50%,-50%) scale(1.06) rotate(2deg)}100%{opacity:.94;transform:translate(-50%,-50%) scale(1) rotate(0)}}
        @keyframes video-editor-motion-burst{0%{opacity:0;transform:translate(-50%,-50%) scale(.65)}100%{opacity:.94;transform:translate(-50%,-50%) scale(1)}}
        @keyframes video-editor-motion-shake{0%,100%{transform:translate(-50%,-50%) translateX(0)}25%{transform:translate(-50%,-50%) translateX(-5px) rotate(-1deg)}50%{transform:translate(-50%,-50%) translateX(5px) rotate(1deg)}75%{transform:translate(-50%,-50%) translateX(-3px)}}
        /* New semantic visual verbs: restrained linework attached to the caption, not a floating badge. */
        .video-editor-motion-accent[class*="video-editor-motion-verb-"]{left:50%;top:70%;width:46%;height:14%;opacity:.84;filter:none;animation:video-editor-motion-accent-in 180ms ease-out both}
        .video-editor-motion-accent[class*="video-editor-motion-verb-"]::before,.video-editor-motion-accent[class*="video-editor-motion-verb-"]::after{display:none!important}
        .video-editor-motion-accent[class*="video-editor-motion-verb-"] .motion-ray{left:auto;top:auto;width:auto;height:2px;margin:0;border-radius:1px;background:var(--motion-accent-color,#e7c77b);box-shadow:none;opacity:.82;transform-origin:left center}
        .video-editor-motion-verb-reveal{--motion-accent-color:#c9b8f2}
        .video-editor-motion-verb-reveal .motion-ray:nth-child(1){left:32%;top:64%;width:4px;height:28%;transform:none}
        .video-editor-motion-verb-reveal .motion-ray:nth-child(2){left:48%;top:45%;width:4px;height:47%;transform:none}
        .video-editor-motion-verb-reveal .motion-ray:nth-child(3){left:64%;top:22%;width:4px;height:70%;transform:none}
        .video-editor-motion-verb-accumulate{--motion-accent-color:#a9d9c4}
        .video-editor-motion-verb-accumulate .motion-ray:nth-child(1){left:31%;top:60%;width:5px;height:32%;transform:none}
        .video-editor-motion-verb-accumulate .motion-ray:nth-child(2){left:48%;top:43%;width:5px;height:49%;transform:none}
        .video-editor-motion-verb-accumulate .motion-ray:nth-child(3){left:65%;top:25%;width:5px;height:67%;transform:none}
        .video-editor-motion-verb-compare{--motion-accent-color:#f0bd9e}
        .video-editor-motion-verb-compare .motion-ray:nth-child(1){left:17%;top:31%;width:30%;height:2px;transform:none}
        .video-editor-motion-verb-compare .motion-ray:nth-child(2){left:53%;top:65%;width:30%;height:2px;transform:none}
        .video-editor-motion-verb-compare .motion-ray:nth-child(3){left:47%;top:18%;width:2px;height:68%;transform:rotate(27deg)}
        .video-editor-motion-verb-flow{--motion-accent-color:#b9d5ff}
        .video-editor-motion-verb-flow .motion-ray:nth-child(1){left:22%;top:22%;width:48%;height:52%;border-top:2px solid var(--motion-accent-color);border-right:2px solid var(--motion-accent-color);background:transparent;border-radius:0 80% 0 0;transform:none;opacity:.58}
        .video-editor-motion-verb-flow .motion-ray:nth-child(2){left:58%;top:46%;width:20%;height:2px;transform:none}
        .video-editor-motion-verb-flow .motion-ray:nth-child(3){left:75%;top:39%;width:10%;height:10px;border-top:2px solid var(--motion-accent-color);border-right:2px solid var(--motion-accent-color);background:transparent;transform:rotate(45deg);opacity:.82}
        .video-editor-motion-verb-impact{--motion-accent-color:#f0c56f}
        .video-editor-motion-verb-impact .motion-ray:nth-child(1){left:25%;top:73%;width:50%;height:3px;transform:rotate(-2deg)}
        .video-editor-motion-verb-impact .motion-ray:nth-child(2){left:38%;top:39%;width:18%;height:2px;transform:rotate(-40deg)}
        .video-editor-motion-verb-impact .motion-ray:nth-child(3){left:58%;top:42%;width:18%;height:2px;transform:rotate(38deg)}
        .video-editor-motion-verb-resolve{--motion-accent-color:#9fd7bd}
        .video-editor-motion-verb-resolve .motion-ray:nth-child(1){left:27%;top:48%;width:18%;height:3px;transform:rotate(42deg)}
        .video-editor-motion-verb-resolve .motion-ray:nth-child(2){left:39%;top:57%;width:30%;height:3px;transform:rotate(-47deg)}
        .video-editor-motion-verb-resolve .motion-ray:nth-child(3){left:62%;top:69%;width:21%;height:2px;transform:none;opacity:.48}
        .video-editor-motion-verb-warning{--motion-accent-color:#e7a49b}
        .video-editor-motion-verb-warning .motion-ray:nth-child(1){left:48%;top:15%;width:2px;height:70%;transform:rotate(26deg)}
        .video-editor-motion-verb-warning .motion-ray:nth-child(2){left:25%;top:34%;width:18%;height:2px;transform:rotate(-8deg);opacity:.45}
        .video-editor-motion-verb-warning .motion-ray:nth-child(3){left:59%;top:70%;width:18%;height:2px;transform:rotate(-8deg);opacity:.45}
         .video-editor-preview-footer{display:flex;flex:none;padding:7px 16px 9px;border-top:1px solid rgba(255,255,255,.1);background:#151a24}
        .video-editor-preview-footer .ant-typography,.video-editor-preview-footer .ant-btn{color:#f8fafc}
        .video-editor-timeline-time{flex:none;font-variant-numeric:tabular-nums;white-space:nowrap}
        .video-editor-transport{display:flex;width:100%;min-width:0;flex:1;align-items:center;gap:8px}
        .video-editor-transport>.ant-btn{flex:none;width:26px;height:26px;padding:0}
        .video-editor-transport-slider{min-width:80px;flex:1;margin:0 4px!important}
        .video-editor-transport-slider .ant-slider-rail{background:rgba(255,255,255,.22)}
        .video-editor-transport-slider .ant-slider-track{background:#8b5cf6}
        .video-editor-transport-slider .ant-slider-handle:after{box-shadow:0 0 0 2px #8b5cf6}
        .video-editor-result-panel{display:flex;min-height:0;flex-direction:column;overflow:hidden;border:1px solid var(--border-default);border-radius:var(--radius-md);background:var(--bg-card);box-shadow:var(--shadow-sm)}
        .video-editor-result-scroll{display:flex;min-height:0;flex:1;flex-direction:column;gap:14px;overflow-y:auto;padding:24px 22px}
        .video-editor-result-heading{display:flex;flex-direction:column;gap:4px}
        .video-editor-result-heading .ant-typography{margin:0}
        .video-editor-result-list{display:flex;flex-direction:column}
        .video-editor-result-item{display:grid;grid-template-columns:26px minmax(0,1fr);gap:12px;padding:17px 0;border-bottom:1px solid var(--border-default)}
        .video-editor-result-item:last-child{border-bottom:0}
        .video-editor-result-item>.anticon{display:flex;width:24px;height:24px;align-items:center;justify-content:center;border-radius:50%;background:var(--primary-600);color:white}
        .video-editor-result-item>div{display:flex;min-width:0;flex-direction:column;gap:4px}
        .video-editor-result-item .ant-typography-secondary{font-size:13px;line-height:1.55}
        .video-editor-result-actions{display:flex;flex:none;flex-direction:column;gap:10px;padding:18px 22px;border-top:1px solid var(--border-default);background:var(--bg-card)}
        .video-editor-cost-header>div{display:flex;flex-direction:column}
        .video-editor-cost-total{font-size:27px;font-weight:700;color:var(--primary-700)}
        .video-editor-prior-quote{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 10px;border-radius:8px;background:var(--surface-muted)}
        .video-editor-cost-details{font-size:12px}
        .video-editor-cost-details summary{cursor:pointer;color:var(--primary-600);user-select:none}
        .video-editor-cost-details>div{display:flex;flex-direction:column;gap:4px;margin:8px 0}
        .video-editor-cost-details p{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0}
        .video-editor-primary-zone{display:flex;flex-direction:column;gap:6px;text-align:center}
        .video-editor-primary-zone>.ant-typography{font-size:12px}
        .video-editor-modal-total{padding:12px;border-radius:var(--radius-sm);background:var(--primary-50)}
        @media(max-width:1180px){
          .video-editor-cloud-workspace{grid-template-columns:minmax(0,1fr) 300px}
          .video-editor-context-source>.ant-select{min-width:180px}
        }
        @media(max-width:960px){
          .video-editor-cloud-workspace{grid-template-columns:1fr;height:auto}
          .video-editor-cinema-panel{min-height:660px}
          .video-editor-result-panel{min-height:560px}
        }
        @media(max-width:720px){
          .video-editor-cloud-header{flex-direction:column}
          .video-editor-context-bar{align-items:stretch;flex-direction:column}
          .video-editor-context-static,.video-editor-context-profile{border-top:1px solid var(--border-default);border-left:0}
          .video-editor-context-source>.ant-select{min-width:0}
          .video-editor-cinema-panel{min-height:620px}
          .video-editor-phone-preview{max-height:430px}
          .video-editor-preview-footer{padding-inline:12px}
        }
      `}</style>
    </div>
  );
}
