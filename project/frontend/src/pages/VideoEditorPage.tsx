/** 云端轻量智能剪辑工作台。 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Radio,
  Segmented,
  Select,
  Slider,
  Space,
  Spin,
  Switch,
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
  FileProtectOutlined,
  FullscreenOutlined,
  HistoryOutlined,
  MutedOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SettingOutlined,
  SoundOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import * as videoEditorApi from "../api/client";
import type {
  TranscriptSegment,
  VideoCapabilitiesResponse,
  VideoEditorBatch,
  VideoEditorBatchItem,
  VideoEditorBgmAsset,
  VideoEditorSource,
  VideoEditorOverlayPreview,
  VideoEditorTimeRange,
  VideoEditorVisualSpec,
} from "../api/types";

const { Title, Text } = Typography;

const BGM_CATEGORY_OPTIONS = [
  "理性干货",
  "情绪共鸣",
  "故事叙事",
  "商业表达",
  "科技未来",
  "轻松日常",
  "励志成长",
  "悬念揭秘",
  "通用口播",
].map((value) => ({ value, label: value }));

const BGM_SOURCE_OPTIONS = [
  { value: "manual", label: "本人/公司或已有授权" },
  { value: "pixabay", label: "Pixabay 免费素材" },
  { value: "light_factory", label: "光厂已购授权" },
  { value: "bodian", label: "波点商用库授权" },
];

const BGM_SOURCE_LABELS: Record<string, string> = {
  manual: "本地授权",
  freepd: "FreePD 公共领域",
  pixabay: "Pixabay",
  light_factory: "光厂",
  bodian: "波点商用库",
};

const BGM_LIBRARY_SOURCES = ["freepd", "pixabay", "light_factory", "bodian", "manual"];
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
const formatBgmOptionLabel = (asset: VideoEditorBgmAsset) => (
  `${asset.voiceover_category || asset.mood} · ${asset.title} · ${
    BGM_SOURCE_LABELS[asset.source_provider] || "授权素材"
  }`
);

type OutputProfile = "720p" | "1080p";
type PreviewMode = "original" | "plan" | "output";

interface CloudCapabilities extends VideoCapabilitiesResponse {
  provider_mode?: string;
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
  style_id: "business_talking_head_v8",
  playback_rate: 1.15,
  canvas: { width: 720, height: 1280, pixel_aspect_ratio: "1:1" },
  title: {
    visible_seconds: 2.5,
    fade_in_ms: 0,
    fade_out_ms: 0,
    max_lines: 2,
    max_chars_per_line: 9,
    font_family: "Source Han Serif CN Heavy",
    render_mode: "png_watermark",
    font_size: 52,
    line_height: 1.1,
    safe_top: 84,
    safe_left: 56,
    asset_width: 520,
    asset_height: 150,
    outline_width: 1,
    shadow: 3,
    color: "#FFFFFF",
  },
  accent: { color: "transparent", width: 0, height: 0, gap: 0 },
  subtitle: {
    max_lines: 1,
    max_chars_per_line: 11,
    font_size: 52,
    safe_bottom: 170,
    outline_width: 2,
    shadow: 3,
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
  return `¥${value.toFixed(value >= 1 ? 2 : value > 0 && value < 0.001 ? 4 : 3)}`;
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
  return asNumber(
    quote.estimated_upper_bound_cny
      ?? quote.estimated_total_cny
      ?? quote.max_cost_cny
      ?? quote.total_cny
      ?? quote.estimated_max
      ?? quote.estimated_total,
    fallback,
  );
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

function recommendReviewBgm(
  item: CloudBatchItem,
  assets: VideoEditorBgmAsset[],
): VideoEditorBgmAsset | null {
  const usableAssets = assets.filter((asset) => asset.content_id_risk !== "registered");
  if (!usableAssets.length) return null;

  const plan = item.edit_plan as unknown as Record<string, unknown> | null | undefined;
  const planCategory = typeof plan?.bgm_category === "string"
    ? plan.bgm_category.trim()
    : "";
  const transcript = (item.subtitle_segments || []).map((segment) => segment.text).join(" ");
  const text = `${item.selected_title || ""} ${item.title || ""} ${transcript} ${
    typeof plan?.explanation === "string" ? plan.explanation : ""
  }`;
  const inferredCategory = planCategory
    || (/人工智能|AI|机器人|科技|智能|数智/i.test(text)
      ? "科技未来"
      : /商业|企业|老板|品牌|营销|获客|客户|市场|销售/.test(text)
        ? "商业表达"
        : /情绪|共鸣|焦虑|治愈|温柔/.test(text)
          ? "情绪共鸣"
          : /故事|经历|曾经|后来|回忆/.test(text)
            ? "故事叙事"
            : /成长|励志|坚持|成功/.test(text)
              ? "励志成长"
              : /真相|揭秘|为什么|竟然/.test(text)
                ? "悬念揭秘"
                : /日常|生活|轻松/.test(text)
                  ? "轻松日常"
                  : "理性干货");

  return usableAssets.find((asset) => asset.voiceover_category === inferredCategory)
    || usableAssets.find((asset) => asset.voiceover_category === "通用口播")
    || usableAssets[0];
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
  let cursor = 0;
  for (const token of new SegmenterApi("zh-CN", { granularity: "word" }).segment(piece)) {
    cursor += token.segment.length;
    if (cursor < piece.length) boundaries.add(cursor);
  }
  return boundaries;
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
    ).filter((splitAt) => wordSplits.has(splitAt) || semanticSplits.has(splitAt));
    const naturalSplits = availableSplits.filter(
      (splitAt) => captionSplitReadsNaturally(remaining, splitAt),
    );
    const safeSplits = naturalSplits.length ? naturalSplits : availableSplits;
    const semanticCandidates = safeSplits.filter((splitAt) => semanticSplits.has(splitAt));
    const candidates = semanticCandidates.length ? semanticCandidates : safeSplits;
    const fallback = Math.max(minimumSplit, Math.min(maximumSplit, ideal));
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

function captionChunks(text: string, maxChars: number) {
  const characters = Array.from(text.replace(/\s+/g, ""));
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
  return pieces.flatMap((piece) => captionPhraseParts(piece, maxChars));
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
    clean = `${clean.slice(0, maxChars - 1)}…`;
  }
  return Array.from({ length: Math.ceil(clean.length / charsPerLine) }, (_, index) => (
    clean.slice(index * charsPerLine, (index + 1) * charsPerLine)
  )).filter(Boolean);
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

function captionEmphasisStyle(_kind?: string) {
  return {
    color: "#FFE16A",
    scale: 1.5,
    animation: "soft_pop",
    duration_ms: 120,
  };
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
  return segments.flatMap((segment, segmentIndex) => {
    const start = asNumber(segment.start, -1);
    const end = asNumber(segment.end, -1);
    const chunks = semanticParts?.get(segmentIndex) || captionChunks(segment.text, maxChars);
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
      const cue = {
        start: timings[index].start,
        end: timings[index].end,
        lines,
        emphasis_range: emphasis,
        emphasis_style: emphasis
          ? captionEmphasisStyle(segment.emphasis_kind || automatic?.kind)
          : null,
      };
      return cue;
    });
  });
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
) {
  if (!emphasis || emphasis.line_index !== lineIndex) return line;
  return (
    <>
      {line.slice(0, emphasis.start)}
      <span
        className="video-editor-subtitle-emphasis"
        style={{
          color: emphasisStyle?.color,
          "--video-editor-emphasis-size": emphasisStyle?.scale || 1.5,
          "--video-editor-emphasis-duration": `${emphasisStyle?.duration_ms || 120}ms`,
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
    cloudBatch.provider_mode === "sandbox"
    || cloudBatch.provider_mode === "aliyun"
    || Boolean(!cloudBatch.provider_mode && (cloudBatch.output_profile || cloudBatch.cost_quote))
  );
}

export default function VideoEditorPage() {
  const navigate = useNavigate();
  const previewRef = useRef<HTMLVideoElement | null>(null);
  const filmstripMouseDraggingRef = useRef(false);
  const pendingLocalDownloadRef = useRef<string | null>(null);
  const [sources, setSources] = useState<VideoEditorSource[]>([]);
  const [bgmAssets, setBgmAssets] = useState<VideoEditorBgmAsset[]>([]);
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
  const [timelineFrames, setTimelineFrames] = useState<string[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [reviewItem, setReviewItem] = useState<CloudBatchItem | null>(null);
  const [reviewSegments, setReviewSegments] = useState<TranscriptSegment[]>([]);
  const [reviewPlanStepIds, setReviewPlanStepIds] = useState<string[]>([]);
  const [reviewTitle, setReviewTitle] = useState("");
  const [reviewBgmId, setReviewBgmId] = useState<string | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [bgmEnabled, setBgmEnabled] = useState(true);
  const [bgmId, setBgmId] = useState<string>();
  const [bgmVolume, setBgmVolume] = useState(0.18);
  const [bgmRightsHolder, setBgmRightsHolder] = useState("");
  const [bgmMood, setBgmMood] = useState("知识·讲解·平稳");
  const [bgmVoiceoverCategory, setBgmVoiceoverCategory] = useState("理性干货");
  const [bgmEnergy, setBgmEnergy] = useState("克制");
  const [bgmSourceProvider, setBgmSourceProvider] = useState("manual");
  const [bgmSourceUrl, setBgmSourceUrl] = useState("");
  const [bgmLicenseUrl, setBgmLicenseUrl] = useState("");
  const [bgmContentIdRisk, setBgmContentIdRisk] = useState<"none" | "registered" | "unknown">("unknown");
  const [bgmUploading, setBgmUploading] = useState(false);

  const refresh = useCallback(async (keepCurrent = true) => {
    setLoading(true);
    try {
      const [sourceResponse, capabilityResponse, batchResponse, bgmResponse] = await Promise.all([
        videoEditorApi.listVideoEditorSources(),
        videoEditorApi.getVideoCapabilities(),
        videoEditorApi.listVideoEditorBatches(),
        videoEditorApi.listVideoEditorBgm(),
      ]);
      const cloudBatches = batchResponse.items.filter(isCloudBatch);
      const nextBatch = keepCurrent
        ? cloudBatches.find((item) => item.batch_id === batch?.batch_id) || batch || cloudBatches[0] || null
        : cloudBatches[0] || null;
      setSources(sourceResponse.items);
      setCapabilities(capabilityResponse as CloudCapabilities);
      setBatches(cloudBatches);
      setBgmAssets(bgmResponse.items);
      setBatch(nextBatch);
      setQuote(nextBatch?.cost_quote || null);
      if (nextBatch) {
        setOutputProfile(nextBatch.output_profile || "720p");
        setPlatform(nextBatch.target_platform);
        setBgmEnabled(nextBatch.bgm_enabled);
        setBgmId(nextBatch.bgm_id || undefined);
        setBgmVolume(nextBatch.bgm_volume);
      }
      setSelectedSourceId((current) => (
        current
        || nextBatch?.items[0]?.source_id
        || sourceResponse.items[0]?.source_id
      ));
      setPollingStopped(false);
    } catch (error) {
      message.error((error as Error).message || "云端剪辑工作台加载失败");
    } finally {
      setLoading(false);
    }
  }, [batch]);

  useEffect(() => {
    void refresh(false);
    // Initial load should not be coupled to a batch object created later.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const currentItem = (batch?.items[0] || null) as CloudBatchItem | null;
  const usableBgmAssets = useMemo(
    () => bgmAssets.filter((asset) => asset.content_id_risk !== "registered"),
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
  const bgmSourceCounts = useMemo(
    () => BGM_LIBRARY_SOURCES.map((source) => ({
      source,
      count: bgmAssets.filter((asset) => asset.source_provider === source).length,
    })),
    [bgmAssets],
  );
  const currentStatus = currentItem?.status || batch?.status || "idle";
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
  const providerMode = batch?.provider_mode || capabilities?.provider_mode || "configuration_required";
  const isSandbox = Boolean(
    batch?.is_mock
    || currentItem?.is_mock
    || capabilities?.is_mock
    || providerMode === "sandbox",
  );
  const missingConfiguration = capabilities?.missing_configuration || [];
  const displayStatus = isSandbox && currentStatus === "configuration_required"
    ? "sandbox_completed"
    : currentStatus;
  const configurationBlocked = !isSandbox && (
    providerMode === "configuration_required"
    || capabilities?.live_ready === false
    || capabilities?.enabled === false
    || missingConfiguration.length > 0
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
  const playableSourceMediaUrl = isBrowserMediaUrl(selectedSource?.media_url)
    ? selectedSource.media_url
    : null;
  const canConfirmOutput = Boolean(
    !isSandbox
    && playableResultMediaUrl
    && currentItem?.publish_allowed !== false,
  );
  const publishHandoffReady = Boolean(canConfirmOutput && currentItem?.edit_task_id);

  useEffect(() => {
    const duration = previewDuration || asNumber(currentItem?.edit_plan?.duration_seconds, 0);
    if (!playableSourceMediaUrl || duration <= 0 || typeof document === "undefined") {
      setTimelineFrames([]);
      return undefined;
    }

    let cancelled = false;
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.crossOrigin = "anonymous";
    video.src = playableSourceMediaUrl;

    const waitForMetadata = () => new Promise<void>((resolve, reject) => {
      if (video.readyState >= 1) {
        resolve();
        return;
      }
      const timeout = window.setTimeout(() => reject(new Error("metadata timeout")), 5000);
      video.addEventListener("loadedmetadata", () => {
        window.clearTimeout(timeout);
        resolve();
      }, { once: true });
      video.addEventListener("error", () => {
        window.clearTimeout(timeout);
        reject(new Error("media unavailable"));
      }, { once: true });
    });

    const seekTo = (time: number) => new Promise<void>((resolve) => {
      const timeout = window.setTimeout(resolve, 900);
      video.addEventListener("seeked", () => {
        window.clearTimeout(timeout);
        resolve();
      }, { once: true });
      video.currentTime = Math.max(0, Math.min(time, Math.max(0, video.duration - 0.05)));
    });

    const captureFrames = async () => {
      try {
        await waitForMetadata();
        const canvas = document.createElement("canvas");
        canvas.width = 72;
        canvas.height = 82;
        const context = canvas.getContext("2d");
        if (!context || !video.videoWidth || !video.videoHeight) return;

        const frameDuration = Number.isFinite(video.duration) ? video.duration : duration;
        const frameIntervalSeconds = 8;
        const frameCount = Math.max(1, Math.min(30, Math.ceil(frameDuration / frameIntervalSeconds)));
        const frames: string[] = [];
        for (let index = 0; index < frameCount; index += 1) {
          if (cancelled) return;
          const time = Math.min(
            frameDuration - 0.05,
            index * frameIntervalSeconds + frameIntervalSeconds / 2,
          );
          await seekTo(time);
          const sourceRatio = video.videoWidth / video.videoHeight;
          const targetRatio = canvas.width / canvas.height;
          let sourceX = 0;
          let sourceY = 0;
          let sourceWidth = video.videoWidth;
          let sourceHeight = video.videoHeight;
          if (sourceRatio > targetRatio) {
            sourceWidth = video.videoHeight * targetRatio;
            sourceX = (video.videoWidth - sourceWidth) / 2;
          } else {
            sourceHeight = video.videoWidth / targetRatio;
            sourceY = (video.videoHeight - sourceHeight) / 2;
          }
          context.drawImage(
            video,
            sourceX,
            sourceY,
            sourceWidth,
            sourceHeight,
            0,
            0,
            canvas.width,
            canvas.height,
          );
          frames.push(canvas.toDataURL("image/jpeg", 0.78));
          // 让出主线程,避免帧生成期间页面卡顿
          await new Promise((resolve) => window.setTimeout(resolve, 0));
        }
        if (!cancelled) setTimelineFrames(frames);
      } catch {
        if (!cancelled) setTimelineFrames([]);
      }
    };

    setTimelineFrames([]);
    void captureFrames();
    return () => {
      cancelled = true;
      video.removeAttribute("src");
    };
  }, [currentItem?.edit_plan?.duration_seconds, playableSourceMediaUrl, previewDuration]);

  useEffect(() => {
    if (
      !batch
      || pollingStopped
      || !["queued", "analyzing", "ready_to_render", "rendering"].includes(currentStatus)
    ) return undefined;
    const timer = window.setTimeout(() => {
      void videoEditorApi.getVideoEditorBatch(batch.batch_id).then((next) => {
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
  }, [batch, currentStatus, pollingStopped]);

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
    setSelectedSourceId(sourceId);
    setBatch(null);
    setQuote(null);
    setPreviewMode("original");
    setReviewSegments([]);
    setReviewTitle("");
    setReviewBgmId(null);
    setBgmEnabled(true);
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
      message.success("素材已上传，客户端不会运行本地转码或识别模型");
    } catch (error) {
      message.error((error as Error).message || "视频素材上传失败");
    } finally {
      setUploading(false);
    }
  };

  const uploadBgm = async (file: File) => {
    if (!bgmRightsHolder.trim()) {
      message.warning("请先填写音乐权利主体");
      return;
    }
    setBgmUploading(true);
    try {
      const asset = await videoEditorApi.uploadVideoEditorBgm({
        file,
        mood: bgmMood,
        voiceoverCategory: bgmVoiceoverCategory,
        energy: bgmEnergy,
        rightsHolder: bgmRightsHolder.trim(),
        sourceProvider: bgmSourceProvider,
        sourceUrl: bgmSourceUrl.trim(),
        licenseUrl: bgmLicenseUrl.trim(),
        contentIdRisk: bgmContentIdRisk,
      });
      setBgmAssets((items) => [asset, ...items.filter((item) => item.asset_id !== asset.asset_id)]);
      setBgmId(asset.asset_id);
      setBgmEnabled(true);
      message.success("授权音乐已加入素材库");
    } catch (error) {
      message.error((error as Error).message || "背景音乐上传失败");
    } finally {
      setBgmUploading(false);
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
          : "已确认费用，开始云端分析",
      );
    } catch (error) {
      message.error((error as Error).message || "无法启动云端分析");
    } finally {
      setSubmitting(false);
    }
  };

  const openReview = async (item: CloudBatchItem) => {
    setReviewItem(item);
    setReviewSegments(normalizeSubtitleSegments(item));
    const itemPlan = normalizePlan(item);
    const enabledIds = item.enabled_plan_step_ids?.length
        ? item.enabled_plan_step_ids
        : itemPlan.filter((step) => step.enabled).map((step) => step.id);
    setReviewTitle(item.selected_title || titleCandidates(item)[0] || item.title);
    const nextBgmId = reviewBgmId
      || item.selected_bgm_id
      || bgmId
      || recommendReviewBgm(item, bgmAssets)?.asset_id
      || null;
    setReviewBgmId(nextBgmId);
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
    if (!batch || !reviewItem) return;
    setReviewSaving(true);
    try {
      const next = await videoEditorApi.reviewVideoEditorBatchItem(
        batch.batch_id,
        reviewItem.item_id,
        {
          subtitleSegments: reviewSegments as unknown as Array<Record<string, unknown>>,
          enabledPlanStepIds: reviewPlanStepIds,
          selectedTitle: reviewTitle.trim() || reviewItem.title,
          selectedBgmId: reviewBgmId,
          confirmed: true,
        },
      ) as CloudBatch;
      setBatch(next);
      setBatches((items) => [next, ...items.filter((item) => item.batch_id !== next.batch_id)]);
      setReviewItem(null);
      const nextItem = next.items[0] as CloudBatchItem | undefined;
      const nextMediaUrl = nextItem?.result_media_url || nextItem?.job?.media_url;
      setPreviewMode(isBrowserMediaUrl(nextMediaUrl) ? "output" : "plan");
      message.success(
        next.is_mock
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
    if (!batch || !currentItem) return;
    setSubmitting(true);
    try {
      const next = await videoEditorApi.retryVideoEditorBatchItem(
        batch.batch_id,
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
    if (!batch || !currentItem) return;
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
        batch.batch_id,
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
      batch
      && currentItem
      && !isSandbox
      && currentStatus === "outcome_unknown"
      && !playableResultMediaUrl
    ) {
      setSubmitting(true);
      try {
        const next = await videoEditorApi.createVideoEditorLocalExport(
          batch.batch_id,
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
    if (!batch || !currentItem || !playableResultMediaUrl || isSandbox) {
      message.warning("真实成片生成后才可以下载");
      return;
    }
    const downloadUrl = currentItem.job?.workflow === "local_preview_export"
      && currentItem.job.download_url
      ? currentItem.job.download_url
      : providerMode === "aliyun"
        ? videoEditorApi.getVideoEditorBatchItemDownloadUrl(
          batch.batch_id,
          currentItem.item_id,
        )
        : playableResultMediaUrl;
    downloadBrowserMedia(
      downloadUrl,
      currentItem?.selected_title || currentItem?.title || selectedSource?.title || "剪辑成片",
    );
  };

  const downloadSourceVideo = () => {
    if (!playableSourceMediaUrl) {
      message.warning("当前原片暂时不可下载");
      return;
    }
    downloadBrowserMedia(playableSourceMediaUrl, `${selectedSource?.title || "视频"}-原片`);
  };

  const handlePrimaryAction = () => {
    if (!batch || currentStatus === "idle") {
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
            : "确认并生成成片",
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
  const visualSpec = resolveVisualSpec(batch?.visual_spec);
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
    batch?.visual_spec?.style_id === DEFAULT_VISUAL_SPEC.style_id
  );
  const overlayPreview = reviewItem || !serverPreviewUsesCurrentStyle
    ? localPreview
    : currentItem?.overlay_preview || localPreview;
  const previewCaption = overlayPreview.cues.find((cue) => (
    cue.start <= previewTime && cue.end >= previewTime
  ));
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
  const timelineTickValues = Array.from({ length: 6 }, (_, index) => (
    timelineDuration * (index / 5)
  ));
  const timelinePosition = timelineDuration > 0
    ? Math.max(0, Math.min(100, (timelineValue / timelineDuration) * 100))
    : 0;
  const sourceTimeToTimelineTime = (sourceTime: number) => (
    previewMode === "plan"
      ? planTimeFromSourceTime(sourceTime, activeIntervals) / playbackRate
      : sourceTime
  );
  const representativeSubtitle = previewSegments.length
    ? previewSegments[Math.floor(previewSegments.length / 2)]
    : null;
  const timelineMarkerTargets = {
    pause: sourceTimeToTimelineTime(
      activeIntervals[0]?.start ?? sourceTimelineDuration * 0.23,
    ),
    caption: sourceTimeToTimelineTime(
      asNumber(representativeSubtitle?.start, sourceTimelineDuration * 0.49),
    ),
    title: Math.min(timelineDuration, 0.5),
  };
  const timelineMarkerPosition = (target: number) => {
    if (timelineDuration <= 0) return "8%";
    return `${Math.max(8, Math.min(92, (target / timelineDuration) * 100))}%`;
  };
  const jumpToTimelineMarker = (target: number) => {
    seekPreviewTimeline(Math.max(0, Math.min(timelineDuration, target)));
  };
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
  const seekFromFilmstripPosition = (surface: HTMLDivElement, clientX: number) => {
    if (timelineDuration <= 0) return;
    const bounds = surface.getBoundingClientRect();
    if (bounds.width <= 0) return;
    const position = Math.max(0, Math.min(1, (clientX - bounds.left) / bounds.width));
    seekPreviewTimeline(position * timelineDuration);
  };
  const startFilmstripDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    seekFromFilmstripPosition(event.currentTarget, event.clientX);
  };
  const continueFilmstripDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    seekFromFilmstripPosition(event.currentTarget, event.clientX);
  };
  const finishFilmstripDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    seekFromFilmstripPosition(event.currentTarget, event.clientX);
    event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const cancelFilmstripDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const startFilmstripMouseDrag = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    filmstripMouseDraggingRef.current = true;
    seekFromFilmstripPosition(event.currentTarget, event.clientX);
  };
  const continueFilmstripMouseDrag = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (!filmstripMouseDraggingRef.current) return;
    seekFromFilmstripPosition(event.currentTarget, event.clientX);
  };
  const finishFilmstripMouseDrag = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (!filmstripMouseDraggingRef.current) return;
    seekFromFilmstripPosition(event.currentTarget, event.clientX);
    filmstripMouseDraggingRef.current = false;
  };
  const cancelFilmstripMouseDrag = () => {
    filmstripMouseDraggingRef.current = false;
  };

  const returnToSourceSelection = () => {
    if (selectedSourceId) selectSource(selectedSourceId);
  };

  const chooseHistory = (selected: CloudBatch) => {
    const selectedItem = selected.items[0] as CloudBatchItem | undefined;
    setBatch(selected);
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
          <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>任务历史</Button>
          <Button icon={<SettingOutlined />} onClick={() => setAdvancedOpen(true)}>高级设置</Button>
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
                {previewMode === "plan" && previewCaption?.lines.length && (
                  <div className="video-editor-subtitle-overlay" style={subtitleOverlayStyle}>
                    {previewCaption.lines.map((line, index) => (
                      <span className="video-editor-overlay-line" key={`${line}-${index}`}>
                        {renderOverlayLine(
                          line,
                          index,
                          previewCaption.emphasis_range,
                          previewCaption.emphasis_style,
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
              {!playableResultMediaUrl && playableSourceMediaUrl && (
                <Tooltip title="下载当前原片">
                  <Button type="text" aria-label="下载原片" icon={<DownloadOutlined />} onClick={downloadSourceVideo} />
                </Tooltip>
              )}
            </div>
            <div className="video-editor-timeline-ruler" aria-hidden="true">
              {timelineTickValues.map((time, index) => (
                <span key={`${index}-${time}`}>{formatTimelineTime(time)}</span>
              ))}
            </div>
            <div
              className="video-editor-filmstrip"
              aria-label="可拖动的视频画面缩略时间轴"
              data-testid="filmstrip-surface"
              onPointerDown={startFilmstripDrag}
              onPointerMove={continueFilmstripDrag}
              onPointerUp={finishFilmstripDrag}
              onPointerCancel={cancelFilmstripDrag}
              onMouseDown={startFilmstripMouseDrag}
              onMouseMove={continueFilmstripMouseDrag}
              onMouseUp={finishFilmstripMouseDrag}
              onMouseLeave={cancelFilmstripMouseDrag}
              onDragStart={(event) => event.preventDefault()}
            >
              <div className="video-editor-filmstrip-content">
                  {timelineFrames.length ? (
                    <div
                      className="video-editor-filmstrip-frames"
                      style={{ gridTemplateColumns: `repeat(${timelineFrames.length}, minmax(0, 1fr))` }}
                    >
                      {timelineFrames.map((frame, index) => (
                        <img
                          alt=""
                          aria-hidden="true"
                          data-testid="timeline-frame"
                          draggable={false}
                          key={`${selectedSourceId}-${index}`}
                          src={frame}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="video-editor-filmstrip-loading">
                      <Spin size="small" />
                      <span>正在读取原片画面</span>
                    </div>
                  )}
                  <span
                    className="video-editor-timeline-playhead"
                    aria-hidden="true"
                    style={{ left: `${timelinePosition}%` }}
                  />
                  <Slider
                    className="video-editor-filmstrip-slider"
                    ariaLabelForHandle="视频预览进度"
                    min={0}
                    max={Math.max(timelineDuration, 0.1)}
                    step={0.05}
                    value={timelineValue}
                    tooltip={{ formatter: (value) => formatTimelineTime(asNumber(value)) }}
                    onChange={seekPreviewTimeline}
                  />
              </div>
            </div>
            <div className="video-editor-timeline-markers" aria-label="剪辑时间线标记">
              <Tooltip title={`点击跳到明显停顿处 · ${formatTimelineTime(timelineMarkerTargets.pause)}`}>
                <Button
                  type="text"
                  className="is-pause"
                  style={{ left: timelineMarkerPosition(timelineMarkerTargets.pause) }}
                  aria-label={`跳到停顿标记 ${formatTimelineTime(timelineMarkerTargets.pause)}`}
                  icon={<ClockCircleOutlined />}
                  onClick={() => jumpToTimelineMarker(timelineMarkerTargets.pause)}
                >
                  停顿 · {formatTimelineTime(timelineMarkerTargets.pause)}
                </Button>
              </Tooltip>
              <Tooltip title={`点击跳到字幕位置 · ${formatTimelineTime(timelineMarkerTargets.caption)}`}>
                <Button
                  type="text"
                  className="is-caption"
                  style={{ left: timelineMarkerPosition(timelineMarkerTargets.caption) }}
                  aria-label={`跳到字幕标记 ${formatTimelineTime(timelineMarkerTargets.caption)}`}
                  icon={<EditOutlined />}
                  onClick={() => jumpToTimelineMarker(timelineMarkerTargets.caption)}
                >
                  字幕 · {formatTimelineTime(timelineMarkerTargets.caption)}
                </Button>
              </Tooltip>
              <Tooltip title={`点击跳到标题出现位置 · ${formatTimelineTime(timelineMarkerTargets.title)}`}>
                <Button
                  type="text"
                  className="is-title"
                  style={{ left: timelineMarkerPosition(timelineMarkerTargets.title) }}
                  aria-label={`跳到标题标记 ${formatTimelineTime(timelineMarkerTargets.title)}`}
                  icon={<FileProtectOutlined />}
                  onClick={() => jumpToTimelineMarker(timelineMarkerTargets.title)}
                >
                  标题 · {formatTimelineTime(timelineMarkerTargets.title)}
                </Button>
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

            <div className="video-editor-result-list">
              {RESULT_SUMMARY_ITEMS.map((item) => (
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
                {isLocalExport ? "¥0" : formatCost(costUpperBound)}
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
                  label: "粗剪方案",
                  children: (
                    <Checkbox.Group
                      value={reviewPlanStepIds}
                      onChange={(values) => setReviewPlanStepIds(values as string[])}
                      style={{ width: "100%" }}
                    >
                      <Space direction="vertical" size={10} style={{ width: "100%" }}>
                        {planSteps.map((step) => (
                          <Card
                            key={step.id}
                            size="small"
                            styles={{ body: { padding: 12 } }}
                          >
                            <Checkbox value={step.id}>
                              <Space direction="vertical" size={2}>
                                <Text strong>{step.label}</Text>
                                <Text type="secondary">{step.reason}</Text>
                              </Space>
                            </Checkbox>
                          </Card>
                        ))}
                      </Space>
                    </Checkbox.Group>
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
                        <Select
                          aria-label="复核背景音乐"
                          allowClear
                          value={reviewBgmId || undefined}
                          onChange={(value) => setReviewBgmId(value || null)}
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
                                来源：{BGM_SOURCE_LABELS[selectedReviewBgm.source_provider] || "授权素材"}
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
        title="高级设置"
        width={520}
        open={advancedOpen}
        onClose={() => setAdvancedOpen(false)}
      >
        <Space direction="vertical" size={20} style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            message="安全轻剪边界固定"
            description="v1 不做语义删句、智能高光、主体追踪横转竖或自动发布，也不接受任意 MPS 参数。"
          />
          <section>
            <Space>
              <Switch checked={bgmEnabled} onChange={setBgmEnabled} />
              <Text strong>AI 自动选择背景音乐</Text>
            </Space>
            <Select
              aria-label="选择背景音乐"
              allowClear
              disabled={!bgmEnabled}
              value={bgmId}
              onChange={setBgmId}
              placeholder="让系统根据文案自动挑选"
              options={bgmAssets.map((asset) => ({
                value: asset.asset_id,
                label: formatBgmOptionLabel(asset),
              }))}
              style={{ width: "100%", marginTop: 10 }}
            />
            <Space wrap size={[6, 6]} style={{ marginTop: 10 }}>
              {bgmSourceCounts.filter(({ count }) => count > 0).map(({ source, count }) => (
                <Tag key={source} color={count ? "blue" : "default"}>
                  {BGM_SOURCE_LABELS[source]} {count} 首
                </Tag>
              ))}
            </Space>
            <Text type="secondary" style={{ display: "block", marginTop: 8 }}>
              这里只显示已真实入库的音乐；新增授权素材可在下方导入。
            </Text>
            <Space style={{ width: "100%", marginTop: 10 }}>
              <Text type="secondary">BGM 音量</Text>
              <Slider
                disabled={!bgmEnabled}
                min={0.08}
                max={0.5}
                step={0.01}
                value={bgmVolume}
                onChange={setBgmVolume}
                style={{ width: 220 }}
              />
              <Text>{Math.round(bgmVolume * 100)}%</Text>
            </Space>
          </section>
          <Card title="导入口播音乐" size="small" styles={{ body: { padding: 14 } }}>
            <Space direction="vertical" size={10} style={{ width: "100%" }}>
              <Select
                aria-label="口播音乐分类"
                value={bgmVoiceoverCategory}
                onChange={setBgmVoiceoverCategory}
                options={BGM_CATEGORY_OPTIONS}
                placeholder="适合哪类口播"
                style={{ width: "100%" }}
              />
              <Select
                aria-label="音乐能量等级"
                value={bgmEnergy}
                onChange={setBgmEnergy}
                options={["克制", "平稳", "有推动感"].map((value) => ({ value, label: value }))}
                placeholder="音乐能量"
                style={{ width: "100%" }}
              />
              <Input
                value={bgmRightsHolder}
                onChange={(event) => setBgmRightsHolder(event.target.value)}
                placeholder="音乐权利主体"
              />
              <Input
                value={bgmMood}
                onChange={(event) => setBgmMood(event.target.value)}
                placeholder="补充标签，如：知识、讲解、钢琴"
              />
              <Select
                aria-label="音乐素材来源"
                value={bgmSourceProvider}
                onChange={(value) => {
                  setBgmSourceProvider(value);
                  if (value !== "pixabay") {
                    setBgmContentIdRisk("unknown");
                  }
                  if (value === "manual") {
                    setBgmSourceUrl("");
                    setBgmLicenseUrl("");
                  }
                }}
                options={BGM_SOURCE_OPTIONS}
                style={{ width: "100%" }}
              />
              {bgmSourceProvider !== "manual" && (
                <>
                  <Input
                    value={bgmSourceUrl}
                    onChange={(event) => setBgmSourceUrl(event.target.value)}
                    placeholder="原始素材页面链接（必填）"
                  />
                  <Input
                    value={bgmLicenseUrl}
                    onChange={(event) => setBgmLicenseUrl(event.target.value)}
                    placeholder="授权说明或订单凭证链接（建议填写）"
                  />
                </>
              )}
              {bgmSourceProvider === "pixabay" && (
                <Select
                  aria-label="Pixabay Content ID 状态"
                  value={bgmContentIdRisk}
                  onChange={setBgmContentIdRisk}
                  options={[
                    { value: "none", label: "页面未标记 Content ID" },
                    { value: "registered", label: "页面已标记 Content ID" },
                    { value: "unknown", label: "尚未确认 Content ID" },
                  ]}
                  style={{ width: "100%" }}
                />
              )}
              <Alert
                type="warning"
                showIcon
                message="短视频也要按实际发布用途确认音乐许可"
                description="个人非推广内容按素材页允许范围使用；企业号、获客、品牌宣传或带货通常属于商业使用。光厂、波点和 Pixabay 都应保存对应作品的来源或授权记录。"
              />
              <Upload
                accept="audio/mpeg,audio/wav,audio/mp4,audio/aac,audio/flac"
                showUploadList={false}
                beforeUpload={(file) => {
                  void uploadBgm(file as File);
                  return Upload.LIST_IGNORE;
                }}
              >
                <Button
                  icon={<UploadOutlined />}
                  loading={bgmUploading}
                  disabled={
                    !bgmRightsHolder.trim()
                    || (bgmSourceProvider !== "manual" && !bgmSourceUrl.trim())
                  }
                >
                  确认有权并上传到音乐库
                </Button>
              </Upload>
            </Space>
          </Card>
        </Space>
      </Drawer>

      <Drawer
        title="智能剪辑任务历史"
        width={560}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
      >
        <List
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
        .video-editor-title-overlay{position:absolute;width:76%;font-family:"VideoInsight Title Serif","Microsoft YaHei UI",serif;font-size:var(--video-editor-title-font-size);font-weight:900;line-height:var(--video-editor-title-line-height);letter-spacing:.01em;text-align:left;white-space:normal;-webkit-text-stroke:var(--video-editor-title-outline) rgba(0,0,0,.72);paint-order:stroke fill;text-shadow:0 2px 7px rgba(0,0,0,.42),0 1px 2px rgba(0,0,0,.62);pointer-events:none;transition:opacity .12s linear}
        .video-editor-title-accent{position:absolute;border-radius:999px;pointer-events:none;transition:opacity .12s linear}
        .video-editor-subtitle-overlay{position:absolute;font-family:"Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif;font-size:var(--video-editor-subtitle-font-size);font-weight:700;line-height:var(--video-editor-subtitle-line-height);letter-spacing:.035em;text-align:center;white-space:nowrap;-webkit-text-stroke:var(--video-editor-subtitle-outline) rgba(0,0,0,.76);paint-order:stroke fill;text-shadow:0 1px 2px rgba(0,0,0,.72),0 3px 8px rgba(0,0,0,.48);pointer-events:none}
        .video-editor-overlay-line{display:block}
        .video-editor-subtitle-emphasis{display:inline-block;color:var(--video-editor-subtitle-emphasis);font-size:calc(var(--video-editor-emphasis-size,1.5) * 1em);line-height:0;vertical-align:baseline;-webkit-text-stroke:var(--video-editor-subtitle-outline) rgba(0,0,0,.76);animation:video-editor-emphasis-pop var(--video-editor-emphasis-duration,120ms) cubic-bezier(.2,.9,.3,1.18) both;transform-origin:center bottom}
        @keyframes video-editor-emphasis-pop{0%{transform:scale(.94)}70%{transform:scale(1.05)}100%{transform:scale(1)}}
        .video-editor-preview-footer{display:flex;flex:none;flex-direction:column;gap:4px;padding:7px 16px 9px;border-top:1px solid rgba(255,255,255,.1);background:#151a24}
        .video-editor-preview-footer .ant-typography,.video-editor-preview-footer .ant-btn{color:#f8fafc}
        .video-editor-timeline-time{font-variant-numeric:tabular-nums}
        .video-editor-transport{display:flex;min-width:0;align-items:center;gap:8px}
        .video-editor-transport>.ant-btn{flex:none;width:26px;height:26px;padding:0}
        .video-editor-transport-slider{min-width:80px;flex:1;margin:0 4px!important}
        .video-editor-transport-slider .ant-slider-rail{background:rgba(255,255,255,.22)}
        .video-editor-transport-slider .ant-slider-track{background:#8b5cf6}
        .video-editor-transport-slider .ant-slider-handle:after{box-shadow:0 0 0 2px #8b5cf6}
        .video-editor-timeline-ruler{display:grid;grid-template-columns:repeat(6,1fr);padding:0 2px;color:#94a3b8;font-size:11px;font-variant-numeric:tabular-nums}
        .video-editor-timeline-ruler span{text-align:center}
        .video-editor-timeline-ruler span:first-child{text-align:left}
        .video-editor-timeline-ruler span:last-child{text-align:right}
        .video-editor-filmstrip{position:relative;height:62px;flex:none;overflow:hidden;touch-action:none;cursor:ew-resize;border:1px solid rgba(255,255,255,.28);border-radius:7px;background:#090e18;box-shadow:inset 0 0 0 1px rgba(0,0,0,.35)}
        .video-editor-filmstrip-content{position:relative;width:100%;height:100%;overflow:hidden}
        .video-editor-filmstrip-frames{display:grid;height:100%;pointer-events:none;user-select:none}
        .video-editor-filmstrip-frames img{display:block;width:100%;height:100%;-webkit-user-drag:none;user-select:none;object-fit:cover;border-right:1px solid rgba(255,255,255,.16)}
        .video-editor-filmstrip-frames img:last-child{border-right:0}
        .video-editor-filmstrip-loading{display:flex;height:100%;align-items:center;justify-content:center;gap:8px;color:#94a3b8;font-size:12px}
        .video-editor-timeline-playhead{position:absolute;z-index:2;top:0;bottom:0;width:2px;transform:translateX(-1px);background:#8b5cf6;box-shadow:0 0 0 1px rgba(139,92,246,.2),0 0 9px rgba(139,92,246,.8);pointer-events:none}
        .video-editor-filmstrip-slider{position:absolute;z-index:3;inset:0;margin:0!important;padding:0!important;pointer-events:none}
        .video-editor-filmstrip-slider .ant-slider-rail,.video-editor-filmstrip-slider .ant-slider-track{height:100%;background:transparent!important}
        .video-editor-filmstrip-slider .ant-slider-handle{opacity:1}
        .video-editor-filmstrip-slider .ant-slider-handle:after{width:12px;height:12px;inset:-1px;background:#8b5cf6;box-shadow:0 0 0 2px #fff,0 2px 8px rgba(0,0,0,.42)}
        .video-editor-timeline-markers{position:relative;height:38px;color:#cbd5e1;font-size:11px}
        .video-editor-timeline-markers>button{position:absolute;top:0;display:flex;height:auto;flex-direction:column;align-items:center;gap:2px;padding:0 6px;transform:translateX(-50%);color:#cbd5e1!important}
        .video-editor-timeline-markers .anticon{display:flex;width:21px;height:21px;align-items:center;justify-content:center;border-radius:6px;background:rgba(124,58,237,.2);color:#c4b5fd}
        .video-editor-timeline-markers .is-caption .anticon{background:rgba(14,165,233,.19);color:#7dd3fc}
        .video-editor-timeline-markers .is-title .anticon{background:rgba(245,158,11,.18);color:#fcd34d}
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
