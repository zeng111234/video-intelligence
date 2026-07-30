/** 云端轻量智能剪辑工作台。 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
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
  Progress,
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
  EyeOutlined,
  FileProtectOutlined,
  HistoryOutlined,
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
  VideoEditorVisualSpec,
} from "../api/types";

const { Title, Text, Paragraph } = Typography;

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
  "为了", "其实", "结果", "现在", "大量", "少量", "很多", "有些", "倒闭",
  "取代", "替代", "增长", "减少", "出现", "成为", "变成", "开始", "进入",
  "面对", "发现", "需要", "可以", "不能", "没有", "不是", "就是", "已经",
  "正在", "也是", "仍然", "被", "把", "让", "待",
];
const CAPTION_BREAK_AFTER_TOKENS = [
  "的话", "以后", "之前", "之后", "时候", "一来", "说到底",
  "个", "段", "条", "种", "次", "件", "位", "家", "台", "套",
];
const CAPTION_PROTECTED_TERMS = [
  "待人工确认", "人工智能", "工业机器人", "机器人", "人工", "工厂", "倒闭", "废铁", "字幕",
  "确认", "市场", "收入", "消费", "企业", "设备", "订单", "未来", "工作",
  "用户", "客户", "视频", "标题", "音乐", "智能", "取代", "替代", "岗位",
];

const formatBgmOptionLabel = (asset: VideoEditorBgmAsset) => (
  `${asset.voiceover_category || asset.mood} · ${asset.title} · ${
    BGM_SOURCE_LABELS[asset.source_provider] || "授权素材"
  }`
);

type OutputProfile = "720p" | "1080p";
type CompactSection = "source" | "preview" | "plan";
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
  style_id: "business_talking_head_v7",
  canvas: { width: 720, height: 1280, pixel_aspect_ratio: "1:1" },
  title: {
    visible_seconds: 2.5,
    fade_in_ms: 0,
    fade_out_ms: 0,
    max_lines: 2,
    max_chars_per_line: 9,
    font_family: "Source Han Serif CN Heavy",
    render_mode: "png_watermark",
    font_size: 48,
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
    max_chars_per_line: 10,
    font_size: 46,
    safe_bottom: 170,
    outline_width: 2,
    shadow: 3,
    color: "#F8FAFC",
    emphasis_color: "#FFE16A",
  },
};

const PLATFORM_OPTIONS = [
  { value: "douyin", label: "抖音 · 9:16" },
  { value: "kuaishou", label: "快手 · 9:16" },
  { value: "wechat_channels", label: "视频号 · 9:16" },
  { value: "xiaohongshu", label: "小红书 · 9:16" },
];

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

const PROVIDER_STAGE_LABELS: Record<string, string> = {
  awaiting_confirmation: "等待费用确认",
  uploading: "上传至私有 OSS",
  upload_complete: "私有 OSS 上传完成",
  submitting_transcription: "提交 Fun-ASR 转写",
  transcribing: "Fun-ASR 转写",
  sandbox_transcription_complete: "沙箱转写契约已完成",
  planning: "生成安全剪辑方案",
  awaiting_review: "等待人工复核",
  awaiting_human_review: "等待字幕与方案复核",
  submitting_render: "提交 MPS 渲染",
  polling_render: "查询 MPS 任务",
  sandbox_render_complete: "沙箱渲染契约已完成",
  transcription_query_failed: "Fun-ASR 状态查询失败",
  render_query_failed: "MPS 状态查询失败",
  submission_outcome_unknown: "云任务提交结果待查",
  planning_outcome_unknown: "规划任务结果待查",
  render_submission_outcome_unknown: "MPS 提交结果待查",
  completed: "供应商处理完成",
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

const STEP_ICON: Record<string, React.ReactNode> = {
  silence_trim: <ClockCircleOutlined />,
  resize: <EyeOutlined />,
  subtitle: <EditOutlined />,
  title: <FileProtectOutlined />,
  background_music: <SoundOutlined />,
  volume_norm: <SoundOutlined />,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formatBytes(value?: number | null) {
  if (!value) return "大小未知";
  return value >= 1024 * 1024
    ? `${(value / 1024 / 1024).toFixed(1)} MB`
    : `${(value / 1024).toFixed(1)} KB`;
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
        asr: "Fun-ASR 转写",
        speech_recognition: "Fun-ASR 转写",
        planning: "qwen-flash 规划",
        edit_planning: "qwen-flash 规划",
        render: "MPS H.264 渲染",
        cloud_render: "MPS H.264 渲染",
        brand_title_overlay: "品牌标题排版",
      }[item.component || ""] || item.provider || "云服务"),
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
  return item.subtitle_segments.flatMap((segment) => {
    if (!isRecord(segment)) return [];
    return [{
      ...segment,
      start: asNumber(segment.start),
      end: asNumber(segment.end),
      text: String(segment.text || ""),
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

function splitInsideProtectedTerm(piece: string, splitAt: number) {
  return CAPTION_PROTECTED_TERMS.some((term) => {
    let start = piece.indexOf(term);
    while (start >= 0) {
      if (start < splitAt && splitAt < start + term.length) return true;
      start = piece.indexOf(term, start + 1);
    }
    return false;
  });
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
    const safeSplits = Array.from(
      { length: Math.max(0, maximumSplit - minimumSplit + 1) },
      (_, index) => minimumSplit + index,
    ).filter((splitAt) => !splitInsideProtectedTerm(remaining, splitAt));
    const semanticSplits = captionBoundarySplits(remaining);
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
  const chunks: string[] = [];
  let current = "";
  for (const piece of pieces) {
    for (const part of captionPhraseParts(piece, maxChars)) {
      if (current && current.length + part.length > maxChars) {
        chunks.push(current);
        current = "";
      }
      current += part;
    }
  }
  if (current) chunks.push(current);
  return chunks;
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

function previewCaptionCues(segments: TranscriptSegment[], spec: VideoEditorVisualSpec) {
  const maxChars = spec.subtitle.max_chars_per_line * spec.subtitle.max_lines;
  return segments.flatMap((segment) => {
    const start = asNumber(segment.start, -1);
    const end = asNumber(segment.end, -1);
    const chunks = captionChunks(segment.text, maxChars);
    if (start < 0 || end <= start || !chunks.length) return [];
    const totalChars = chunks.reduce((total, chunk) => total + chunk.length, 0) || 1;
    let cursor = start;
    return chunks.map((chunk, index) => {
      const cueEnd = index === chunks.length - 1
        ? end
        : cursor + ((end - start) * chunk.length / totalChars);
      const lines = displayLines(
        chunk,
        spec.subtitle.max_chars_per_line,
        spec.subtitle.max_lines,
      );
      const cue = {
        start: cursor,
        end: cueEnd,
        lines,
        emphasis_range: emphasisRange(lines, segment.emphasis_terms),
      };
      cursor = cueEnd;
      return cue;
    });
  });
}

function localOverlayPreview(
  segments: TranscriptSegment[],
  title: string,
  spec: VideoEditorVisualSpec,
): VideoEditorOverlayPreview {
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
    cues: previewCaptionCues(segments, spec),
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
) {
  if (!emphasis || emphasis.line_index !== lineIndex) return line;
  return (
    <>
      {line.slice(0, emphasis.start)}
      <span className="video-editor-subtitle-emphasis">{line.slice(emphasis.start, emphasis.end)}</span>
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
  const [compactSection, setCompactSection] = useState<CompactSection>("preview");
  const [previewMode, setPreviewMode] = useState<PreviewMode>("original");
  const [previewTime, setPreviewTime] = useState(0);
  const [planPreviewElapsed, setPlanPreviewElapsed] = useState(0);
  const [previewDuration, setPreviewDuration] = useState(0);
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
  const recommendedBgm = useMemo(
    () => currentItem ? recommendReviewBgm(currentItem, bgmAssets) : null,
    [bgmAssets, currentItem],
  );
  const displayedBgmId = reviewBgmId
    || currentItem?.selected_bgm_id
    || bgmId
    || recommendedBgm?.asset_id
    || null;
  const displayedBgm = useMemo(
    () => bgmAssets.find((asset) => asset.asset_id === displayedBgmId) || null,
    [bgmAssets, displayedBgmId],
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
  const estimatedRemovedSeconds = planSteps.reduce(
    (total, step) => total + step.estimated_removed_seconds,
    0,
  );
  const estimatedOutputSeconds = asNumber(currentItem?.edit_plan?.estimated_output_seconds, 0);

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

  const changePlatform = (value: string) => {
    setPlatform(value);
    setQuote(null);
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
      setCompactSection("plan");
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
      setReviewSegments(task.segments.map((segment) => ({ ...segment })));
    } catch (error) {
      message.error((error as Error).message || "字幕草稿加载失败");
    } finally {
      setReviewLoading(false);
    }
  };

  const chooseNextBgm = () => {
    if (!usableBgmAssets.length) {
      message.warning("配乐库还没有可用音乐");
      return;
    }
    const currentIndex = usableBgmAssets.findIndex(
      (asset) => asset.asset_id === displayedBgmId,
    );
    const next = usableBgmAssets[(currentIndex + 1) % usableBgmAssets.length];
    setReviewBgmId(next.asset_id);
    setBgmId(next.asset_id);
    setBgmEnabled(true);
    message.success(`已换为《${next.title}》，确认生成时会使用这首`);
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

  const downloadVideo = (mediaUrl: string, fallbackTitle: string) => {
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
  };

  const downloadFinishedVideo = () => {
    if (!playableResultMediaUrl || isSandbox) {
      message.warning("真实成片生成后才可以下载");
      return;
    }
    downloadVideo(
      playableResultMediaUrl,
      currentItem?.selected_title || currentItem?.title || selectedSource?.title || "剪辑成片",
    );
  };

  const downloadSourceVideo = () => {
    if (!playableSourceMediaUrl) {
      message.warning("当前原片暂时不可下载");
      return;
    }
    downloadVideo(playableSourceMediaUrl, `${selectedSource?.title || "视频"}-原片`);
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
      void loadQuote(outputProfile, true);
      return;
    }
    if (["failed", "interrupted"].includes(currentStatus)) {
      void retryCurrentItem();
      return;
    }
    if (["awaiting_output_confirmation", "ready_to_publish"].includes(currentStatus)) {
      void confirmAndPublish();
    }
  };

  const primaryAction = (() => {
    if (!batch || currentStatus === "idle") {
      return {
        label: isSandbox
          ? "免费体验剪辑方案"
          : configurationBlocked
            ? "查看出片参考与开通说明"
            : "查看费用并开始分析",
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
        label: "重新报价并生成带字幕成片",
        disabled: !selectedSourceId,
        icon: <CloudOutlined />,
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
  const previewTitle = reviewTitle || currentItem?.selected_title || titleCandidates(currentItem)[0] || "";
  const localPreview = localOverlayPreview(previewSegments, previewTitle, visualSpec);
  const serverPreviewUsesCurrentStyle = (
    batch?.visual_spec?.style_id === DEFAULT_VISUAL_SPEC.style_id
  );
  const overlayPreview = reviewItem || !serverPreviewUsesCurrentStyle
    ? localPreview
    : currentItem?.overlay_preview || localPreview;
  const previewCaption = overlayPreview.cues.find((cue) => (
    cue.start <= previewTime && cue.end >= previewTime
  ));
  const titlePreviewTime = previewMode === "plan" ? planPreviewElapsed : previewTime;
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
  );
  const planTimelineValue = Math.min(planPreviewElapsed, planTimelineDuration);

  const togglePlanPreview = () => {
    const video = previewRef.current;
    if (!video) return;
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
      nextPlanTime,
      sourceTimelineDuration,
      activeIntervals,
    );
    video.currentTime = sourceTime;
    setPreviewTime(sourceTime);
    setPlanPreviewElapsed(nextPlanTime);
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
    setCompactSection("plan");
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
        <div>
          <Space size={10} align="center">
            <Title level={3} style={{ margin: 0 }}>轻量智能剪辑</Title>
            <Tag icon={<CloudOutlined />} color={isSandbox ? "default" : "blue"}>
              {isSandbox ? "免费体验模式" : providerMode === "aliyun" ? "云端出片模式" : "云端出片待开通"}
            </Tag>
          </Space>
          <Text type="secondary">
            先免费查看剪辑方案；需要真实字幕和成片时，再开通云端出片。
          </Text>
        </div>
        <Space wrap>
          <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>任务历史</Button>
          <Button icon={<SettingOutlined />} onClick={() => setAdvancedOpen(true)}>高级设置</Button>
          <Tooltip title="刷新当前任务与云端状态">
            <Button aria-label="刷新工作台" icon={<ReloadOutlined />} onClick={() => void refresh()} />
          </Tooltip>
        </Space>
      </header>

      <Segmented
        className="video-editor-compact-nav"
        block
        value={compactSection}
        onChange={(value) => setCompactSection(value as CompactSection)}
        options={[
          { value: "source", label: "素材与输出" },
          { value: "preview", label: "预览" },
          { value: "plan", label: "方案与费用" },
        ]}
      />

      <div className="video-editor-cloud-workspace" data-section={compactSection}>
        <Card
          className="video-editor-workspace-card video-editor-source-card"
          title={<Space><Tag>01</Tag><span>素材与输出</span></Space>}
          styles={{ body: { padding: 16 } }}
        >
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <section>
              <Text strong>单条视频素材</Text>
              <Select
                aria-label="选择视频素材"
                value={selectedSourceId}
                onChange={selectSource}
                placeholder="选择系统已有成片"
                style={{ width: "100%", marginTop: 8 }}
                options={sources.map((source) => ({
                  value: source.source_id,
                  label: `${source.title} · ${formatBytes(source.size_bytes)}`,
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
                <Button
                  block
                  icon={<UploadOutlined />}
                  loading={uploading}
                  style={{ marginTop: 8 }}
                >
                  上传 MP4 / MOV
                </Button>
              </Upload>
              {selectedSource && (
                <div className="video-editor-selected-source">
                  <Text strong ellipsis>{selectedSource.title}</Text>
                  <Text type="secondary">{formatBytes(selectedSource.size_bytes)} · 仅上传与播放</Text>
                </div>
              )}
            </section>

            <section>
              <Text strong>发布平台</Text>
              <Select
                aria-label="目标发布平台"
                value={platform}
                onChange={changePlatform}
                options={PLATFORM_OPTIONS}
                style={{ width: "100%", marginTop: 8 }}
              />
            </section>

            <section>
              <Text strong>输出清晰度</Text>
              <Radio.Group
                aria-label="输出清晰度"
                value={outputProfile}
                onChange={(event) => changeOutputProfile(event.target.value as OutputProfile)}
                optionType="button"
                buttonStyle="solid"
                className="video-editor-profile-options"
              >
                <Radio.Button value="720p">
                  <span>720P</span>
                  <small>60 秒约 ¥0.048</small>
                </Radio.Button>
                <Radio.Button value="1080p">
                  <span>1080P</span>
                  <small>60 秒约 ¥0.080</small>
                </Radio.Button>
              </Radio.Group>
              <Text type="secondary" className="video-editor-helper">
                档位是分辨率、帧率和码率的唯一权威值。
              </Text>
            </section>

          </Space>
          <Alert
            className="video-editor-local-free"
            type="info"
            showIcon
            message="本机零模型负担"
            description="生产链路在云端完成；预览直接播放原片，不生成收费低清代理。"
          />
        </Card>

        <Card
          className="video-editor-workspace-card video-editor-preview-card"
          title={<Space><Tag>02</Tag><span>原片与方案预览</span></Space>}
          extra={statusTag(displayStatus)}
          styles={{ body: { padding: 14 } }}
        >
          <div className="video-editor-preview-toolbar">
            <Segmented
              value={previewMode}
              onChange={(value) => setPreviewMode(value as PreviewMode)}
              options={[
                { value: "original", label: "原片" },
                { value: "plan", label: "方案预览" },
                { value: "output", label: "成片", disabled: !playableResultMediaUrl },
              ]}
            />
            {currentItem?.provider_stage && (
              <Text type="secondary">
                {PROVIDER_STAGE_LABELS[currentItem.provider_stage] || currentItem.provider_stage}
              </Text>
            )}
          </div>

          <div className="video-editor-preview-stage">
            {!selectedSource ? (
              <Empty description="在左侧选择一条视频后即可预览" />
            ) : (
              <div className="video-editor-phone-preview">
                <video
                  key={`${previewMode}-${playableResultMediaUrl || selectedSource.media_url}`}
                  ref={previewRef}
                  controls={previewMode !== "plan"}
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
                  }}
                  onTimeUpdate={handlePreviewTimeUpdate}
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
                        {renderOverlayLine(line, index, previewCaption.emphasis_range)}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="video-editor-preview-footer">
            {previewMode === "plan" && (
              <div className="video-editor-preview-timeline">
                <Slider
                  ariaLabelForHandle="方案预览进度"
                  min={0}
                  max={Math.max(planTimelineDuration, 0.1)}
                  step={0.05}
                  value={planTimelineValue}
                  tooltip={{
                    formatter: (value) => formatTimelineTime(asNumber(value)),
                  }}
                  onChange={seekPlanPreview}
                />
                <div>
                  <Text>
                    {formatTimelineTime(planTimelineValue)}
                    {" / "}
                    {formatTimelineTime(planTimelineDuration)}
                  </Text>
                  <Text type="secondary">可拖动查看剪后时间</Text>
                </div>
              </div>
            )}
            <Space wrap>
              {previewMode === "plan" && (
                <Button size="small" icon={<PlayCircleOutlined />} onClick={togglePlanPreview}>
                  播放方案预览
                </Button>
              )}
              <Tag icon={<PlayCircleOutlined />}>
                {previewMode === "plan" ? "按正式粗剪方案预览" : previewMode === "output" ? "正式成片" : "原始素材"}
              </Tag>
              {!playableResultMediaUrl && playableSourceMediaUrl && (
                <Tooltip title="原片可直接下载；不含字幕、剪辑或配乐">
                  <Button size="small" icon={<DownloadOutlined />} onClick={downloadSourceVideo}>
                    下载原片
                  </Button>
                </Tooltip>
              )}
              {currentStatus === "awaiting_subtitle_review" ? (
                <Button
                  size="small"
                  icon={<EditOutlined />}
                  disabled={!currentItem}
                  onClick={() => {
                    if (currentItem) void openReview(currentItem);
                  }}
                >
                  去审核并生成成片
                </Button>
              ) : (
                <Tooltip title={playableResultMediaUrl && !isSandbox ? "下载已生成的正式成片" : "真实成片生成后可下载"}>
                  <span>
                    <Button
                      size="small"
                      icon={<DownloadOutlined />}
                      disabled={!playableResultMediaUrl || isSandbox}
                      onClick={downloadFinishedVideo}
                    >
                      下载成片
                    </Button>
                  </span>
                </Tooltip>
              )}
              {estimatedRemovedSeconds > 0 && (
                <Text>预计删减 {estimatedRemovedSeconds.toFixed(1)} 秒</Text>
              )}
              {estimatedOutputSeconds > 0 && (
                <Text>剪后约 {estimatedOutputSeconds.toFixed(1)} 秒</Text>
              )}
            </Space>
            {currentItem && (
              <Progress
                percent={STATUS_META[displayStatus]?.progress || currentItem.analysis?.progress || 0}
                size="small"
                status={["failed", "interrupted"].includes(currentStatus) ? "exception" : undefined}
                showInfo={false}
              />
            )}
          </div>
        </Card>

        <Card
          className="video-editor-workspace-card video-editor-plan-card"
          title={<Space><Tag>03</Tag><span>粗剪方案与出片参考</span></Space>}
          extra={<Text type="secondary">安全粗剪</Text>}
          styles={{ body: { padding: 16 } }}
        >
          <div className="video-editor-plan-body">
            {isSandbox && (
              <Alert
                data-testid="provider-alert"
                type="info"
                showIcon
                message="免费体验：不调用真实云服务"
                description="先查看操作流程和方案预览；不会调用真实识别、生成成片或发布。"
              />
            )}
            {configurationBlocked && (
              <Alert
                data-testid="provider-alert"
                type="warning"
                showIcon
                message="云端出片尚未开通"
                description="可先使用免费体验查看流程；需要真实字幕和成片时，再一次性开通云端服务。"
              />
            )}
            {pollingStopped && (
              <Alert
                type="warning"
                showIcon
                message="自动查询已停止"
                description="网络查询失败后没有连续重试；请手动刷新确认供应商原任务状态。"
              />
            )}
            {currentStatus === "outcome_unknown" && (
              <Alert
                type="warning"
                showIcon
                message="付费提交结果未知"
                description="旧任务不会自动重提。可重新获取报价，确认后按已校对的字幕生成一条新成片。"
              />
            )}

            <div className="video-editor-plan-heading">
              <div>
                <Text strong>可解释建议</Text>
                <Paragraph type="secondary">
                  只处理可靠空白和长停顿；正式出片与此预览使用同一组保留片段。
                </Paragraph>
              </div>
            </div>

            <div className="video-editor-plan-list">
              {planSteps.map((step) => (
                <div className="video-editor-plan-item" key={step.id}>
                  <div className="video-editor-plan-icon">{STEP_ICON[step.kind] || <CheckCircleOutlined />}</div>
                  <div className="video-editor-plan-copy">
                    <Space size={6} wrap>
                      <Text strong>{step.label}</Text>
                      {step.estimated_removed_seconds > 0 && (
                        <Tag>约 -{step.estimated_removed_seconds.toFixed(1)} 秒</Tag>
                      )}
                    </Space>
                    <Text type="secondary">{step.reason}</Text>
                  </div>
                </div>
              ))}
            </div>

            <div className="video-editor-bgm-card">
              <div className="video-editor-bgm-header">
                <Space size={8}>
                  <SoundOutlined />
                  <Text strong>{displayedBgm ? "AI 已匹配配乐" : "AI 自动配乐"}</Text>
                </Space>
                {displayedBgm && (
                  <Tag color="purple">
                    {displayedBgm.voiceover_category || displayedBgm.mood}
                  </Tag>
                )}
              </div>
              {displayedBgm ? (
                <>
                  <div className="video-editor-bgm-result">
                    <div>
                      <Text strong>{displayedBgm.title}</Text>
                      <Text type="secondary">
                        {displayedBgm.energy || "克制"} · {Math.round(displayedBgm.duration_seconds)} 秒
                      </Text>
                    </div>
                    <Text type="secondary">
                      {currentItem?.selected_bgm_id === displayedBgm.asset_id && currentItem.bgm_reason
                        ? currentItem.bgm_reason
                        : `根据文案语气匹配“${displayedBgm.voiceover_category || displayedBgm.mood}”，将以低音量铺底。`}
                    </Text>
                  </div>
                  <audio
                    controls
                    preload="metadata"
                    src={displayedBgm.media_url}
                    aria-label={`试听 AI 配乐：${displayedBgm.title}`}
                  />
                  <div className="video-editor-bgm-actions">
                    <Button size="small" icon={<ReloadOutlined />} onClick={chooseNextBgm}>
                      换一首
                    </Button>
                    <Text type="secondary">确认生成前仍可调整</Text>
                  </div>
                </>
              ) : (
                <Text type="secondary">
                  分析文案后会自动匹配并直接提供试听；没有合适音乐时保持原声。
                </Text>
              )}
            </div>

            <div className="video-editor-cost-card">
              <div className="video-editor-cost-header">
                <div>
                  <Text strong>{isSandbox ? "云端出片参考费用" : "预计费用上限"}</Text>
                  <Text type="secondary">{PROFILE_META[outputProfile].label}</Text>
                </div>
                <Text className="video-editor-cost-total" data-testid="cost-total">
                  {formatCost(costUpperBound)}
                </Text>
              </div>
              {quoteBreakdown.length ? (
                <div className="video-editor-cost-lines">
                  {quoteBreakdown.map((line) => (
                    <div key={line.key}>
                      <Text type="secondary">{line.label}</Text>
                      <Text>{formatCost(line.amount)}</Text>
                    </div>
                  ))}
                </div>
              ) : (
                <Text type="secondary">
                  {isSandbox
                    ? "开通云端出片后，会先给出 15 分钟有效的正式报价。"
                    : "60 秒官方单价示例；点击主按钮获取 15 分钟有效的正式报价。"}
                </Text>
              )}
              <Space size={6} wrap>
                {quote?.quote_id && <Tag>{quoteExpired ? "报价已过期" : "报价有效 15 分钟"}</Tag>}
                {quote?.pricing_version || quote?.price_version
                  ? <Tag>价格版本 {quote.pricing_version || quote.price_version}</Tag>
                  : null}
                {isSandbox && <Tag>免费体验不收费</Tag>}
              </Space>
            </div>

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
                {!batch
                  ? isSandbox
                    ? "免费体验不调用真实识别或出片；开通云端后才会显示实际费用。"
                    : "免费预检不会调用付费 API；确认报价后才允许分析。"
                  : STATUS_META[displayStatus]?.label || displayStatus}
              </Text>
            </div>
          </div>
        </Card>
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
                            maxLength={4}
                            placeholder="强调词（可选，原文最多 4 字）"
                            style={{ marginTop: 8 }}
                            onChange={(event) => setReviewSegments((segments) => segments.map(
                              (item, itemIndex) => itemIndex === index
                                ? {
                                    ...item,
                                    emphasis_terms: event.target.value.trim()
                                      ? [event.target.value.trim()]
                                      : [],
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
                      description={isSandbox ? "免费体验不会调用真实字幕识别" : "没有可复核的字幕片段"}
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
        .video-editor-cloud-page{display:flex;flex-direction:column;gap:14px;width:100%;min-width:0}
        .video-editor-cloud-header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
        .video-editor-compact-nav{display:none}
        .video-editor-cloud-workspace{display:grid;grid-template-columns:minmax(252px,.78fr) minmax(340px,1.22fr) minmax(310px,.9fr);gap:14px;height:clamp(630px,calc(100vh - 174px),760px);min-height:0}
        .video-editor-workspace-card{height:100%;overflow:hidden;border-color:var(--border-default);box-shadow:var(--shadow-sm)}
        .video-editor-workspace-card>.ant-card-head{min-height:50px;padding-inline:16px}
        .video-editor-workspace-card>.ant-card-body{height:calc(100% - 51px);overflow:auto}
        .video-editor-source-card>.ant-card-body{display:flex;flex-direction:column;justify-content:space-between;gap:14px}
        .video-editor-selected-source{display:flex;flex-direction:column;gap:2px;margin-top:8px;padding:10px;border:1px solid var(--border-default);border-radius:var(--radius-sm);background:var(--gray-50)}
        .video-editor-profile-options{display:flex;width:100%;margin-top:8px}
        .video-editor-profile-options .ant-radio-button-wrapper{display:flex;flex:1;height:auto;min-height:52px;align-items:flex-start;justify-content:center;padding:7px 8px;line-height:1.3}
        .video-editor-profile-options .ant-radio-button-wrapper span:not(.ant-radio-button){display:flex;flex-direction:column;align-items:center;gap:3px}
        .video-editor-profile-options small{font-size:11px;font-weight:400;white-space:nowrap}
        .video-editor-helper{display:block;margin-top:6px;font-size:12px}
        .video-editor-local-free{margin-top:auto}
        .video-editor-preview-card>.ant-card-body{display:flex;flex-direction:column;min-height:0}
        .video-editor-preview-toolbar,.video-editor-preview-footer,.video-editor-cost-header,.video-editor-modal-total{display:flex;align-items:center;justify-content:space-between;gap:12px}
        .video-editor-preview-toolbar{flex-wrap:wrap}
        .video-editor-preview-stage{display:flex;flex:1;min-height:0;align-items:center;justify-content:center;margin:12px 0;padding:16px;border-radius:var(--radius-md);background:linear-gradient(145deg,#0f172a,#18223a)}
        .video-editor-preview-stage .ant-empty-description{color:#d1d5db}
        .video-editor-phone-preview{position:relative;width:auto;height:100%;max-width:100%;max-height:570px;aspect-ratio:9/16;overflow:hidden;container-type:inline-size;border:1px solid rgba(255,255,255,.28);border-radius:20px;background:#030712;box-shadow:0 18px 42px rgba(0,0,0,.34)}
        .video-editor-phone-preview video{width:100%;height:100%;object-fit:contain;background:#030712}
        .video-editor-phone-preview video.video-editor-plan-video{object-fit:contain}
        @font-face{font-family:"VideoInsight Title Serif";src:url("/api/v1/video-editor/brand-title-font") format("opentype");font-display:swap;font-style:normal;font-weight:900}
        .video-editor-title-overlay{position:absolute;width:76%;font-family:"VideoInsight Title Serif","Microsoft YaHei UI",serif;font-size:var(--video-editor-title-font-size);font-weight:900;line-height:var(--video-editor-title-line-height);letter-spacing:.01em;text-align:left;white-space:normal;text-shadow:0 3px 9px rgba(0,0,0,.48),0 1px 1px rgba(0,0,0,.68);pointer-events:none;transition:opacity .12s linear}
        .video-editor-title-accent{position:absolute;border-radius:999px;pointer-events:none;transition:opacity .12s linear}
        .video-editor-subtitle-overlay{position:absolute;font-family:"Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif;font-size:var(--video-editor-subtitle-font-size);font-weight:700;line-height:var(--video-editor-subtitle-line-height);letter-spacing:.035em;text-align:center;white-space:nowrap;-webkit-text-stroke:var(--video-editor-subtitle-outline) rgba(0,0,0,.7);text-shadow:0 3px 8px rgba(0,0,0,.72);pointer-events:none}
        .video-editor-overlay-line{display:block}
        .video-editor-subtitle-emphasis{color:var(--video-editor-subtitle-emphasis);-webkit-text-stroke:var(--video-editor-subtitle-outline) rgba(0,0,0,.42)}
        .video-editor-preview-footer{flex-direction:column;align-items:stretch}
        .video-editor-preview-timeline{display:flex;flex-direction:column;gap:2px}
        .video-editor-preview-timeline>.ant-slider{margin:4px 6px}
        .video-editor-preview-timeline>div{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:12px}
        .video-editor-plan-card>.ant-card-body{overflow:hidden}
        .video-editor-plan-body{display:flex;flex-direction:column;height:100%;min-height:0;gap:12px}
        .video-editor-plan-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
        .video-editor-plan-heading .ant-typography{margin-bottom:0}
        .video-editor-plan-list{display:flex;flex:1;min-height:140px;flex-direction:column;gap:8px;overflow:auto;padding-right:2px}
        .video-editor-plan-item{display:grid;grid-template-columns:30px minmax(0,1fr);align-items:start;gap:8px;padding:10px;border:1px solid var(--border-default);border-radius:var(--radius-sm);background:var(--bg-card)}
        .video-editor-plan-icon{display:flex;width:30px;height:30px;align-items:center;justify-content:center;border-radius:8px;background:var(--primary-50);color:var(--primary-600)}
        .video-editor-plan-copy{display:flex;min-width:0;flex-direction:column;gap:3px}
        .video-editor-plan-copy>.ant-typography{font-size:12px;line-height:1.45}
        .video-editor-cost-card{display:flex;flex-direction:column;gap:8px;padding:12px;border:1px solid var(--primary-200);border-radius:var(--radius-md);background:var(--primary-50)}
        .video-editor-bgm-card{display:flex;flex-direction:column;gap:8px;padding:12px;border:1px solid var(--border-default);border-radius:var(--radius-md);background:var(--surface-muted)}
        .video-editor-bgm-header{display:flex;align-items:center;justify-content:space-between;gap:12px}
        .video-editor-bgm-result{display:flex;flex-direction:column;gap:4px}
        .video-editor-bgm-result>div{display:flex;align-items:center;justify-content:space-between;gap:10px}
        .video-editor-bgm-card audio{display:block;width:100%;height:34px}
        .video-editor-bgm-actions{display:flex;align-items:center;justify-content:space-between;gap:10px}
        .video-editor-cost-header>div{display:flex;flex-direction:column}
        .video-editor-cost-total{font-size:25px;font-weight:700;color:var(--primary-700)}
        .video-editor-cost-lines{display:flex;flex-direction:column;gap:4px}
        .video-editor-cost-lines>div{display:flex;align-items:center;justify-content:space-between;gap:10px}
        .video-editor-primary-zone{display:flex;flex-direction:column;gap:6px;text-align:center}
        .video-editor-primary-zone>.ant-typography{font-size:12px}
        .video-editor-modal-total{padding:12px;border-radius:var(--radius-sm);background:var(--primary-50)}
        @media(max-width:1180px){
          .video-editor-compact-nav{display:flex}
          .video-editor-cloud-workspace{display:block;height:auto;min-height:640px}
          .video-editor-workspace-card{display:none;height:640px}
          .video-editor-cloud-workspace[data-section="source"] .video-editor-source-card,
          .video-editor-cloud-workspace[data-section="preview"] .video-editor-preview-card,
          .video-editor-cloud-workspace[data-section="plan"] .video-editor-plan-card{display:block}
        }
        @media(max-width:720px){
          .video-editor-cloud-header{flex-direction:column}
          .video-editor-cloud-workspace,.video-editor-workspace-card{min-height:600px;height:auto}
          .video-editor-preview-card{height:660px}
          .video-editor-phone-preview{max-height:500px}
        }
      `}</style>
    </div>
  );
}
