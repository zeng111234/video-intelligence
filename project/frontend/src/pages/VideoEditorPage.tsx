/** 云端轻量智能剪辑工作台。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  CloseOutlined,
  CloudOutlined,
  EditOutlined,
  EyeOutlined,
  FileProtectOutlined,
  HistoryOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
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
} from "../api/types";

const { Title, Text, Paragraph } = Typography;

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
  return `¥${value.toFixed(value >= 1 ? 2 : 3)}`;
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
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [rightsHolder, setRightsHolder] = useState("本人/公司");
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
  const [dismissedStepIds, setDismissedStepIds] = useState<string[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [reviewItem, setReviewItem] = useState<CloudBatchItem | null>(null);
  const [reviewSegments, setReviewSegments] = useState<TranscriptSegment[]>([]);
  const [reviewPlanStepIds, setReviewPlanStepIds] = useState<string[]>([]);
  const [reviewTitle, setReviewTitle] = useState("");
  const [reviewBgmId, setReviewBgmId] = useState<string | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [bgmEnabled, setBgmEnabled] = useState(false);
  const [bgmId, setBgmId] = useState<string>();
  const [bgmVolume, setBgmVolume] = useState(0.18);
  const [bgmRightsConfirmed, setBgmRightsConfirmed] = useState(false);
  const [bgmRightsHolder, setBgmRightsHolder] = useState("");
  const [bgmMood, setBgmMood] = useState("通用");
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
  const currentStatus = currentItem?.status || batch?.status || "idle";
  const selectedSource = useMemo(
    () => sources.find((source) => source.source_id === selectedSourceId) || null,
    [selectedSourceId, sources],
  );
  const planSteps = useMemo(() => normalizePlan(currentItem), [currentItem]);
  const visiblePlanSteps = planSteps.filter((step) => !dismissedStepIds.includes(step.id));
  const enabledPlanSteps = planSteps.filter((step) => !dismissedStepIds.includes(step.id));
  const estimatedRemovedSeconds = enabledPlanSteps.reduce(
    (total, step) => total + step.estimated_removed_seconds,
    0,
  );

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
    setDismissedStepIds([]);
    if (currentItem?.enabled_plan_step_ids?.length) {
      const enabledIds = new Set(currentItem.enabled_plan_step_ids);
      setDismissedStepIds(planSteps.filter((step) => !enabledIds.has(step.id)).map((step) => step.id));
    }
  }, [currentItem?.item_id, currentItem?.review_confirmed_at, planSteps]);

  useEffect(() => {
    setPreviewTime(0);
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
    setDismissedStepIds([]);
  };

  const uploadSource = async (file: File) => {
    if (!rightsConfirmed || !rightsHolder.trim()) {
      message.warning("请先填写权利主体并确认素材使用权");
      return;
    }
    setUploading(true);
    try {
      const response = await videoEditorApi.uploadVideoEditorSources([file], rightsHolder.trim());
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
    if (!bgmRightsConfirmed || !bgmRightsHolder.trim()) {
      message.warning("请先填写音乐权利主体并确认拥有使用权");
      return;
    }
    setBgmUploading(true);
    try {
      const asset = await videoEditorApi.uploadVideoEditorBgm({
        file,
        mood: bgmMood,
        rightsHolder: bgmRightsHolder.trim(),
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
    setReviewPlanStepIds(enabledIds.filter((stepId) => !dismissedStepIds.includes(stepId)));
    setReviewTitle(item.selected_title || titleCandidates(item)[0] || item.title);
    setReviewBgmId(item.selected_bgm_id || bgmId || null);
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

  const handlePrimaryAction = () => {
    if (!batch || currentStatus === "idle") {
      void loadQuote(outputProfile, true);
      return;
    }
    if (currentStatus === "awaiting_subtitle_review" && currentItem) {
      void openReview(currentItem);
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
        disabled: !selectedSourceId || !rightsConfirmed || !rightsHolder.trim(),
        icon: <CloudOutlined />,
      };
    }
    if (configurationBlocked) {
      return { label: "补齐云配置后可继续", disabled: true, icon: <CloudOutlined /> };
    }
    if (currentStatus === "awaiting_subtitle_review") {
      return {
        label: isSandbox ? "查看字幕与剪辑方案" : "确认字幕与方案并生成",
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
      return { label: "供应商结果待查，不重复提交", disabled: true, icon: <ClockCircleOutlined /> };
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
  const currentSubtitle = previewSegments.find((segment) => (
    asNumber(segment.start, -1) <= previewTime
    && asNumber(segment.end, -1) >= previewTime
  ));
  const previewTitle = reviewTitle || currentItem?.selected_title || titleCandidates(currentItem)[0] || "";
  const activeIntervals = silenceIntervals(enabledPlanSteps);

  const handlePreviewTimeUpdate = () => {
    const video = previewRef.current;
    if (!video) return;
    if (previewMode === "plan") {
      const interval = activeIntervals.find(({ start, end }) => (
        video.currentTime >= start && video.currentTime < end
      ));
      if (interval) video.currentTime = interval.end;
    }
    setPreviewTime(video.currentTime);
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
                  disabled={!rightsConfirmed || !rightsHolder.trim()}
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

            <section className="video-editor-rights">
              <Text strong><SafetyCertificateOutlined /> 权利确认</Text>
              <Input
                aria-label="视频权利主体"
                value={rightsHolder}
                onChange={(event) => setRightsHolder(event.target.value)}
                placeholder="权利主体，例如：本人/公司"
              />
              <Checkbox
                checked={rightsConfirmed}
                onChange={(event) => setRightsConfirmed(event.target.checked)}
              >
                我确认拥有该视频及所用素材的使用权
              </Checkbox>
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
                  controls
                  preload="metadata"
                  src={previewMode === "output" && playableResultMediaUrl ? playableResultMediaUrl : selectedSource.media_url}
                  className={previewMode === "plan" ? "video-editor-plan-video" : ""}
                  onTimeUpdate={handlePreviewTimeUpdate}
                />
                {previewMode === "plan" && previewTitle && (
                  <div className="video-editor-title-overlay">{previewTitle}</div>
                )}
                {previewMode === "plan" && currentSubtitle?.text && (
                  <div className="video-editor-subtitle-overlay">{currentSubtitle.text}</div>
                )}
              </div>
            )}
          </div>

          <div className="video-editor-preview-footer">
            <Space wrap>
              <Tag icon={<PlayCircleOutlined />}>
                {previewMode === "plan" ? "浏览器模拟跳过建议区间" : previewMode === "output" ? "正式成片" : "原始素材"}
              </Tag>
              {estimatedRemovedSeconds > 0 && (
                <Text>预计删减 {estimatedRemovedSeconds.toFixed(1)} 秒</Text>
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
          title={<Space><Tag>03</Tag><span>剪辑方案与出片参考</span></Space>}
          extra={<Text type="secondary">安全轻剪</Text>}
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
                description="系统不会盲目重提。请等待查询原供应商任务或人工处理。"
              />
            )}

            <div className="video-editor-plan-heading">
              <div>
                <Text strong>可解释建议</Text>
                <Paragraph type="secondary">
                  可逐项关闭；不会删除或改写有人声区间。
                </Paragraph>
              </div>
              {!!dismissedStepIds.length && (
                <Button type="link" size="small" onClick={() => setDismissedStepIds([])}>
                  恢复全部
                </Button>
              )}
            </div>

            <div className="video-editor-plan-list">
              {!visiblePlanSteps.length ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="所有建议已关闭" />
              ) : visiblePlanSteps.map((step) => (
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
                  <Tooltip title="关闭这条建议">
                    <Button
                      type="text"
                      aria-label={`关闭建议：${step.label}`}
                      icon={<CloseOutlined />}
                      onClick={() => setDismissedStepIds((ids) => [...ids, step.id])}
                    />
                  </Tooltip>
                </div>
              ))}
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
                  label: "方案确认",
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
                        <Text strong>已授权 BGM</Text>
                        <Select
                          aria-label="复核背景音乐"
                          allowClear
                          value={reviewBgmId || undefined}
                          onChange={(value) => setReviewBgmId(value || null)}
                          placeholder="保持原声"
                          options={bgmAssets.map((asset) => ({
                            value: asset.asset_id,
                            label: `${asset.mood} · ${asset.title}`,
                          }))}
                          style={{ width: "100%", marginTop: 8 }}
                        />
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
              <Text strong>使用已授权背景音乐</Text>
            </Space>
            <Select
              allowClear
              disabled={!bgmEnabled}
              value={bgmId}
              onChange={setBgmId}
              placeholder="不选择则保持原声"
              options={bgmAssets.map((asset) => ({
                value: asset.asset_id,
                label: `${asset.mood} · ${asset.title}`,
              }))}
              style={{ width: "100%", marginTop: 10 }}
            />
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
          <Card title="上传授权音乐" size="small" styles={{ body: { padding: 14 } }}>
            <Space direction="vertical" size={10} style={{ width: "100%" }}>
              <Input
                value={bgmRightsHolder}
                onChange={(event) => setBgmRightsHolder(event.target.value)}
                placeholder="音乐权利主体"
              />
              <Input
                value={bgmMood}
                onChange={(event) => setBgmMood(event.target.value)}
                placeholder="情绪标签"
              />
              <Checkbox
                checked={bgmRightsConfirmed}
                onChange={(event) => setBgmRightsConfirmed(event.target.checked)}
              >
                我确认拥有该音乐使用权
              </Checkbox>
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
                  disabled={!bgmRightsConfirmed || !bgmRightsHolder.trim()}
                >
                  上传到授权音乐库
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
        .video-editor-rights{display:flex;flex-direction:column;gap:9px}
        .video-editor-local-free{margin-top:auto}
        .video-editor-preview-card>.ant-card-body{display:flex;flex-direction:column;min-height:0}
        .video-editor-preview-toolbar,.video-editor-preview-footer,.video-editor-cost-header,.video-editor-modal-total{display:flex;align-items:center;justify-content:space-between;gap:12px}
        .video-editor-preview-toolbar{flex-wrap:wrap}
        .video-editor-preview-stage{display:flex;flex:1;min-height:0;align-items:center;justify-content:center;margin:12px 0;padding:12px;border-radius:var(--radius-md);background:#111827}
        .video-editor-preview-stage .ant-empty-description{color:#d1d5db}
        .video-editor-phone-preview{position:relative;width:min(100%,310px);height:100%;max-height:570px;aspect-ratio:9/16;overflow:hidden;border-radius:12px;background:#030712;box-shadow:0 14px 34px rgba(0,0,0,.28)}
        .video-editor-phone-preview video{width:100%;height:100%;object-fit:contain;background:#030712}
        .video-editor-phone-preview video.video-editor-plan-video{object-fit:cover}
        .video-editor-title-overlay{position:absolute;top:8%;left:7%;right:7%;padding:7px 10px;border-radius:7px;background:rgba(15,23,42,.8);color:#fff;font-weight:700;text-align:center;pointer-events:none}
        .video-editor-subtitle-overlay{position:absolute;left:8%;right:8%;bottom:12%;padding:6px 9px;border-radius:6px;background:rgba(0,0,0,.72);color:#fff;font-weight:600;text-align:center;pointer-events:none}
        .video-editor-preview-footer{flex-direction:column;align-items:stretch}
        .video-editor-plan-card>.ant-card-body{overflow:hidden}
        .video-editor-plan-body{display:flex;flex-direction:column;height:100%;min-height:0;gap:12px}
        .video-editor-plan-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
        .video-editor-plan-heading .ant-typography{margin-bottom:0}
        .video-editor-plan-list{display:flex;flex:1;min-height:140px;flex-direction:column;gap:8px;overflow:auto;padding-right:2px}
        .video-editor-plan-item{display:grid;grid-template-columns:30px minmax(0,1fr) 28px;align-items:start;gap:8px;padding:10px;border:1px solid var(--border-default);border-radius:var(--radius-sm);background:var(--bg-card)}
        .video-editor-plan-icon{display:flex;width:30px;height:30px;align-items:center;justify-content:center;border-radius:8px;background:var(--primary-50);color:var(--primary-600)}
        .video-editor-plan-copy{display:flex;min-width:0;flex-direction:column;gap:3px}
        .video-editor-plan-copy>.ant-typography{font-size:12px;line-height:1.45}
        .video-editor-cost-card{display:flex;flex-direction:column;gap:8px;padding:12px;border:1px solid var(--primary-200);border-radius:var(--radius-md);background:var(--primary-50)}
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
