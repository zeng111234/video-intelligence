import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  List,
  Modal,
  Pagination,
  Popover,
  Progress,
  Segmented,
  Select,
  Space,
  Tag,
  Timeline,
  Typography,
  Upload,
} from "antd";
import {
  AudioOutlined,
  CheckCircleOutlined,
  ControlOutlined,
  DownOutlined,
  FileTextOutlined,
  FilterOutlined,
  GlobalOutlined,
  LinkOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  RightOutlined,
  RocketOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SendOutlined,
  SettingOutlined,
  StarOutlined,
  UploadOutlined,
  UpOutlined,
  UserOutlined,
  VideoCameraOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { SiBilibili, SiKuaishou, SiTiktok, SiXiaohongshu } from "react-icons/si";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  confirmPublishTaskAuto,
  confirmProductionBatchPublish,
  connectPublishAccount,
  createCrawlerProgressiveBatch,
  createPublishAccount,
  createProductionBatch,
  createProductionProfile,
  getAvatarCapabilities,
  getCrawlerBrowserDiscoveryCapabilities,
  getProductionWorkspaceConfiguration,
  getCrawlerBatch,
  getCrawlerBatchForSelection,
  getCrawlerKeywordQueue,
  getCrawlerHotWords,
  getPublishAccountStatus,
  getProductionBatchWorkspace,
  listAvatarAssets,
  listCrawlerBatches,
  listProductionBatches,
  listProductionProfiles,
  listPublishAccounts,
  listPublishPlatforms,
  pauseProductionBatch,
  preparePublishOfficialPage,
  preflightProductionBatch,
  preflightProductionBatchPublish,
  recordManualPublishResult,
  reconnectProductionBatchTranscription,
  resumeProductionBatch,
  retryProductionBatchFailed,
  reviewProductionBatchItems,
  reviewProductionTranscriptWithAI,
  openCrawlerCandidateOriginalInBrowser,
  resolveCrawlerCandidateOriginalMedia,
  saveProductionWorkspaceConfiguration,
  startCrawlerBrowserDiscovery,
  startProductionBatch,
  trainCloudAvatar,
  trainCloudVoice,
  uploadAvatarAsset,
} from "../api/client";
import type {
  AvatarAsset,
  AvatarCapability,
  CrawlerBatchResponse,
  CrawlerBrowserDiscoveryCapabilities,
  CrawlerCandidateResult,
  CrawlerHotWordItem,
  CrawlerKeywordQueueResponse,
  CrawlerSearchRequest,
  ProductionBatch,
  ProductionCreativePlan,
  ProductionProfile,
  ProductionPublishTarget,
  ProductionWorkspace,
  ProductionWorkspaceConfiguration,
  PublishAccount,
  PublishPlatformCapability,
} from "../api/types";
import MaterialSearchExperience from "../components/MaterialSearchExperience";
import { getAdminToken } from "../hooks/useAdminAuth";
import { productionTaskTitle } from "../utils/productionTask";
import { cnyToCredits } from "../utils/credits";

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

type SourceMode = "keyword" | "share_link" | "script";
type CreationMode = "auto" | "manual";
type ReviewStage = "transcript" | "script" | "output" | "publish";
type BrowserPlatform = "douyin" | "xiaohongshu" | "kuaishou" | "bilibili";
type MaterialCount = 30 | 50 | 100;
type PublishedWindowDays = CrawlerSearchRequest["published_window_days"];

const PROFILE_STORAGE_KEY = "pipeline.lastProfileId";
const CANDIDATE_LIST_PREVIEW_LIMIT = 10;

const PLATFORM_LABELS: Record<string, string> = {
  douyin: "抖音",
  kuaishou: "快手",
  xiaohongshu: "小红书",
  bilibili: "B站",
  wechat_channels: "视频号",
};

const DEFAULT_PUBLISH_ACCOUNT_NAME = "公司主号";

const SOURCE_OPTIONS = [
  { label: "关键词找素材", value: "keyword", icon: <SearchOutlined aria-hidden /> },
  { label: "视频链接", value: "share_link", icon: <LinkOutlined aria-hidden /> },
  { label: "已有文案", value: "script", icon: <FileTextOutlined aria-hidden /> },
];

const SOURCE_BROWSER_PLATFORMS: Array<{ platform: BrowserPlatform; label: string }> = [
  { platform: "douyin", label: "抖音" },
  { platform: "kuaishou", label: "快手" },
  { platform: "xiaohongshu", label: "小红书" },
  { platform: "bilibili", label: "B站" },
];

const PUBLISHED_WINDOW_OPTIONS: Array<{ label: string; value: PublishedWindowDays }> = [
  { label: "不限", value: 0 },
  { label: "一天内", value: 1 },
  { label: "一周内", value: 7 },
  { label: "半年内", value: 180 },
];

function sourcePlatformIcon(platform: BrowserPlatform) {
  if (platform === "douyin") return <SiTiktok aria-hidden />;
  if (platform === "kuaishou") return <SiKuaishou aria-hidden />;
  if (platform === "xiaohongshu") return <SiXiaohongshu aria-hidden />;
  return <SiBilibili aria-hidden />;
}

function sourcePlatformReady(status: CrawlerBrowserDiscoveryCapabilities) {
  return Boolean(status.enabled && status.ready_to_crawl);
}
function isBrowserPlatform(value: string | undefined): value is BrowserPlatform {
  return value === "douyin" || value === "xiaohongshu" || value === "kuaishou" || value === "bilibili";
}

const WORKSPACE_STEPS = [
  { title: "01 告诉我想做什么", description: "找素材并选择方向" },
  { title: "02 确认创作方案", description: "查看方案并确认费用" },
  { title: "03 查看成片并发布", description: "制作成片并确认发布" },
];

const STATUS_LABEL: Record<string, string> = {
  planned: "待预检",
  pending: "等待中",
  queued: "排队中",
  running: "执行中",
  paused: "已暂停",
  awaiting_review: "待人工确认",
  awaiting_publish: "待确认发布",
  ready_to_publish: "成片待发布",
  skipped: "已参与选稿",
  succeeded: "已完成",
  completed: "已完成",
  failed: "失败",
  partial: "部分完成",
  blocked: "已阻断",
  outcome_unknown: "结果待核对",
};

const WORKBENCH_TASK_LIMIT = 2;

const STAGE_LABEL: Record<string, string> = {
  source: "素材与选题",
  transcript: "核对原转写",
  script: "确认创作方案",
  avatar: "数字人口播",
  editing: "剪辑成片",
  output: "复核成片",
  publish: "确认发布",
  completed: "已完成",
  media_resolution: "解析素材",
  transcription: "提取原转写",
  copywriting: "生成改写稿",
  human_review: "人工确认",
  avatar_generation: "数字人口播",
  video_editing: "剪辑成片",
  publishing: "确认发布",
};

function isUsableProfile(
  profile: ProductionProfile,
  assets: AvatarAsset[],
) {
  const avatar = assets.find((item) => item.asset_id === profile.avatar_id);
  const voice = assets.find((item) => item.asset_id === profile.voice_id);
  return Boolean(
    avatar?.kind === "avatar"
    && avatar.authorized
    && avatar.status === "ready"
    && voice?.kind === "voice"
    && voice.authorized
    && voice.status === "ready",
  );
}

function stageIndex(stage: string | null | undefined) {
  if (!stage || stage === "source" || stage === "media_resolution") return 0;
  if (["transcript", "script", "transcription", "copywriting", "human_review"].includes(stage)) return 1;
  if (["avatar", "avatar_generation"].includes(stage)) return 2;
  if (["editing", "video_editing"].includes(stage)) return 3;
  return 4;
}

function businessStageIndex(stage: string | null | undefined) {
  if (!stage || stage === "source" || stage === "media_resolution") return 0;
  if (["transcript", "script", "transcription", "copywriting", "human_review"].includes(stage)) return 1;
  return 2;
}

function buildCreativePlan(script: string): ProductionCreativePlan {
  const sentences = script
    .split(/(?<=[。！？!?])|\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  const hook = (sentences[0] || script.trim()).slice(0, 160);
  const body = sentences.slice(1, -1).filter(Boolean).slice(0, 3);
  const keyPoints = (body.length ? body : sentences.slice(0, 3)).map((value) => value.slice(0, 180));
  const callToAction = (sentences[sentences.length - 1] || "请根据实际情况选择下一步").slice(0, 160);
  return {
    hook,
    key_points: keyPoints.length ? keyPoints : ["讲清楚这条内容能帮客户解决什么"],
    call_to_action: callToAction,
    visual_sections: [
      `开场：${hook || "提出客户关心的问题"}`.slice(0, 180),
      `讲解：${(keyPoints[0] || "用一个具体要点讲清楚做法")}`.slice(0, 180),
      `收尾：${callToAction}`.slice(0, 180),
    ],
  };
}

function statusColor(status: string | null | undefined) {
  if (["succeeded", "completed", "ready_to_publish", "skipped"].includes(status || "")) return "success";
  if (["failed"].includes(status || "")) return "error";
  if (["blocked", "partial", "awaiting_review", "awaiting_publish", "outcome_unknown"].includes(status || "")) return "warning";
  if (["running", "queued"].includes(status || "")) return "processing";
  return "default";
}

function workbenchTaskAction(status: string) {
  if (status === "awaiting_review") return "继续确认";
  if (["awaiting_publish", "ready_to_publish"].includes(status)) return "确认发布";
  if (["failed", "blocked", "partial", "outcome_unknown"].includes(status)) return "查看原因";
  if (["running", "queued", "pending", "planned", "paused"].includes(status)) return "查看进度";
  return "查看结果";
}

function isFinishedBatch(batch: ProductionBatch) {
  return ["succeeded", "completed"].includes(batch.status);
}

function workbenchTaskPriority(batch: ProductionBatch, selectedBatchId: string) {
  if (batch.batch_id === selectedBatchId) return -1;
  if (["awaiting_review", "awaiting_publish", "ready_to_publish"].includes(batch.status)) return 0;
  if (["failed", "blocked", "partial", "outcome_unknown"].includes(batch.status)) return 1;
  return 2;
}

function candidateRank(candidate: CrawlerCandidateResult) {
  return candidate.system_rank ?? candidate.provider_hot_rank ?? candidate.platform_rank ?? 999_999;
}

function candidateInteractionHeat(candidate: CrawlerCandidateResult) {
  const metrics = [candidate.likes, candidate.comments, candidate.shares, candidate.favorites];
  if (metrics.every((value) => value === null || value === undefined)) return null;
  if (candidate.heat_score !== null && candidate.heat_score !== undefined) return candidate.heat_score;
  return (candidate.likes ?? 0)
    + (candidate.comments ?? 0) * 3
    + (candidate.shares ?? 0) * 4
    + (candidate.favorites ?? 0) * 4;
}

function formatCandidateMetric(value: number | null | undefined) {
  if (value === null || value === undefined) return "平台未提供";
  if (value >= 100_000_000) return `${Number((value / 100_000_000).toFixed(1))}亿`;
  if (value >= 10_000) return `${Number((value / 10_000).toFixed(1))}万`;
  return value.toLocaleString("zh-CN");
}

function formatWorkbenchTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  const M = d.getMonth() + 1;
  const D = d.getDate();
  const h = d.getHours().toString().padStart(2, "0");
  const m = d.getMinutes().toString().padStart(2, "0");
  return sameYear ? `${M}/${D} ${h}:${m}` : `${d.getFullYear()}/${M}/${D} ${h}:${m}`;
}

function platformRunSummary(
  run: CrawlerBatchResponse["platform_runs"][number],
  defaultTarget: number,
) {
  const label = run.platform_label || PLATFORM_LABELS[run.platform] || run.platform;
  const target = run.requested_count || defaultTarget;
  const returned = run.returned_count || 0;
  const relatedCount = run.platform === "bilibili"
    ? (run.candidates || []).filter((candidate) => candidate.selection_tier === "reserve").length
      + (run.reference_count || 0)
    : 0;
  const directCount = run.strict_relevant_count ?? Math.max(0, returned - relatedCount);
  const relatedNote = relatedCount > 0
    ? `（直接匹配 ${directCount}，相关待确认 ${relatedCount}）`
    : "";
  if (returned >= target || run.crawl_stop_reason === "target_reached") {
    return `${label} ${returned}/${target}${relatedNote}`;
  }
  const errorCode = (run.errors || [])
    .map((error) => (typeof error.code === "string" ? error.code : ""))
    .find(Boolean);
  const reasons: string[] = [];
  if (errorCode === "public_search_video_filter_unavailable"
    || errorCode === "public_search_video_filter_unconfirmed") {
    reasons.push("视频筛选未生效");
  }
  if ((run.out_of_window_count || 0) > 0) {
    reasons.push(`已排除 ${run.out_of_window_count} 条时间范围外素材`);
  }
  if ((run.irrelevant_count || 0) > 0) {
    reasons.push(`另有 ${run.irrelevant_count} 条未命中关键词`);
  }
  if (run.crawl_stop_reason === "no_more_loaded" || run.crawl_stop_reason === "platform_end") {
    reasons.push("平台没有继续加载");
  } else if (run.crawl_stop_reason === "safety_limit") {
    reasons.push("已到本次安全加载上限");
  } else if (run.status === "failed") {
    reasons.push("本次未完成");
  }
  return `${label} ${returned}/${target}${relatedNote || (reasons.length ? `（${reasons.join("；")}）` : "（本次只返回这些结果）")}`;
}

const CRAWLER_QUEUE_TERMINAL_STATUSES = new Set([
  "succeeded",
  "partial",
  "failed",
  "cancelled",
  "paused",
]);

function crawlerQueueItemFinished(queue: CrawlerKeywordQueueResponse) {
  if (CRAWLER_QUEUE_TERMINAL_STATUSES.has(queue.status)) return true;
  const current = queue.items[0];
  if (!current || !["succeeded", "partial", "failed", "cancelled"].includes(current.status)) {
    return false;
  }
  return !queue.items.slice(1).some((item) => ["queued", "running"].includes(item.status));
}

function buildPipelineProgressBatch(queue: CrawlerKeywordQueueResponse): CrawlerBatchResponse | null {
  const item = queue.items[0];
  if (!item) return null;
  const candidates = item.progress_candidates || [];
  const terminal = crawlerQueueItemFinished(queue);
  const platformRuns = queue.platforms.map((platform) => {
    const platformCandidates = candidates.filter((candidate) => candidate.platform === platform);
    return {
      run_id: `progress-${queue.queue_id}-${platform}`,
      platform,
      platform_label: PLATFORM_LABELS[platform] || platform,
      provider: "free_multi_platform",
      mode: "local_browser",
      status: terminal ? "partial" : "running",
      requested_count: queue.count_per_platform,
      returned_count: platformCandidates.length,
      raw_item_count: item.scanned_count || 0,
      parsed_item_count: item.parsed_count || 0,
      raw_discovered: item.scanned_count || 0,
      parsed: item.parsed_count || 0,
      retained: platformCandidates.length,
      out_of_window_count: 0,
      invalid_count: 0,
      duplicate_count: 0,
      result_state: "progressive_snapshot",
      payload_diagnostic: null,
      cache_hit: false,
      cached_from_run_id: null,
      api_call_count: 0,
      billable_units: null,
      quota_remaining: null,
      error: null,
      errors: [],
      started_at: item.started_at,
      finished_at: item.finished_at,
      candidates: platformCandidates,
      reference_count: 0,
      reference_candidates: [],
    };
  });
  return {
    batch_id: item.batch_id || `progress-${queue.queue_id}`,
    keyword: item.keyword,
    platforms: queue.platforms,
    published_window_days: queue.published_window_days,
    count_per_platform: queue.count_per_platform,
    provider: "free_multi_platform",
    mode: "local_browser",
    status: terminal ? queue.status : "running",
    force_refresh: false,
    created_at: queue.created_at,
    finished_at: item.finished_at,
    error: item.error,
    platform_runs: platformRuns,
    total_api_calls: 0,
    total_candidates: candidates.length,
    total_estimated_cost_cny: 0,
  };
}

async function openPipelineCandidateOriginalMedia(
  candidate: Pick<CrawlerCandidateResult, "platform" | "video_id" | "source_url">,
) {
  if (candidate.platform === "xiaohongshu") {
    await openCrawlerCandidateOriginalInBrowser(candidate.video_id);
    return;
  }
  const sourceUrl = candidate.source_url?.trim();
  if (!sourceUrl) throw new Error("该候选没有可用的原视频链接。");
  const popup = window.open(sourceUrl, "_blank", "noopener,noreferrer");
  if (!popup) throw new Error("浏览器拦截了新窗口，请允许弹窗后重试。");
}

function compareCandidateRanking(left: CrawlerCandidateResult, right: CrawlerCandidateResult) {
  const tier = (left.selection_tier === "reserve" ? 1 : 0)
    - (right.selection_tier === "reserve" ? 1 : 0);
  if (tier !== 0) return tier;

  const leftHeat = candidateInteractionHeat(left);
  const rightHeat = candidateInteractionHeat(right);
  if (leftHeat === null && rightHeat !== null) return 1;
  if (leftHeat !== null && rightHeat === null) return -1;
  if (leftHeat !== null && rightHeat !== null && leftHeat !== rightHeat) return rightHeat - leftHeat;

  const plays = (right.plays ?? -1) - (left.plays ?? -1);
  if (plays !== 0) return plays;
  const rank = candidateRank(left) - candidateRank(right);
  if (rank !== 0) return rank;
  const trend = (right.trend_score ?? -1) - (left.trend_score ?? -1);
  if (trend !== 0) return trend;
  return left.video_id.localeCompare(right.video_id);
}

function strictCandidates(batch: CrawlerBatchResponse, preferredCandidateId = "") {
  const byId = new Map<string, CrawlerCandidateResult>();
  batch.platform_runs.forEach((run) => {
    (run.candidates || []).forEach((candidate) => {
      byId.set(candidate.video_id, {
        ...candidate,
        selection_tier: candidate.selection_tier === "reserve" ? "reserve" : "priority",
      });
    });
    (run.low_incremental_candidates || []).forEach((candidate) => {
      if (!byId.has(candidate.video_id)) {
        byId.set(candidate.video_id, { ...candidate, selection_tier: "reserve" });
      }
    });
  });
  const preferred = preferredCandidateId ? byId.get(preferredCandidateId) : undefined;
  const ranked = [...byId.values()].sort(compareCandidateRanking);
  if (!preferred) return ranked;
  return [preferred, ...ranked.filter((item) => item.video_id !== preferred.video_id)];
}

function selectAutomaticCandidates(candidates: CrawlerCandidateResult[]) {
  // B 站放宽匹配的相关候补只用于扩大可选结果，不能未经确认进入自动创作。
  return candidates
    .filter((candidate) => candidate.selection_tier !== "reserve")
    .sort(compareCandidateRanking);
}

function diagnoseCrawlerResult(batch: CrawlerBatchResponse) {
  const runs = batch.platform_runs || [];
  const errors = runs.filter((run) => run.error || run.status === "failed");
  if (batch.error || errors.length) {
    return {
      kind: "服务失败",
      message: batch.error || errors.map((run) => run.error).filter(Boolean).join("；") || "素材查找失败。",
    };
  }
  const returned = runs.reduce((sum, run) => sum + (run.raw_item_count || run.returned_count || 0), 0);
  const strict = runs.reduce((sum, run) => sum + (run.strict_relevant_count ?? run.relevant_count ?? run.candidates.length), 0);
  const belowFloor = runs.reduce((sum, run) => sum + (run.below_heat_floor_count || 0), 0);
  const lowIncremental = runs.reduce(
    (sum, run) => sum + Math.max(
      run.incremental_play_filtered_count || 0,
      run.low_incremental_candidates?.length || 0,
    ),
    0,
  );
  const belowThreshold = belowFloor + lowIncremental;
  if (
    belowThreshold > 0
    || runs.some((run) => ["all_below_heat_floor", "low_incremental_only"].includes(run.result_state))
  ) {
    return { kind: "未达热门阈值", message: `找到 ${Math.max(belowThreshold, strict)} 条严格相关内容，但都未达到当前热门阈值。` };
  }
  if (strict === 0 && returned > 0) {
    return { kind: "无严格相关", message: `供应商返回 ${returned} 条内容，但没有标题或话题严格命中当前关键词。` };
  }
  if (returned === 0) {
    return { kind: "数据源无返回", message: "当前数据源没有返回内容，可换词后重试或进入素材发现查看详情。" };
  }
  return { kind: "无严格相关", message: "当前检索没有可进入生产的严格相关候选。" };
}

function stableFingerprint(value: unknown) {
  return JSON.stringify(value);
}

function resultMediaUrl(item: ProductionWorkspace["items"][number] | undefined) {
  // video_path 是服务端机器上的文件路径，不能也不应暴露给浏览器。
  // 兼容尚未重启的旧后端：它已有受控媒体接口，只是旧工作台遗漏了该字段。
  if (item?.result_media_url) return item.result_media_url;
  return item?.video_path && item.run_id
    ? `/api/v1/pipelines/${encodeURIComponent(item.run_id)}/media`
    : null;
}

function formatElapsedSeconds(seconds: number | null | undefined) {
  const safeSeconds = Math.max(0, Math.floor(seconds || 0));
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return minutes > 0 ? `${minutes} 分 ${remainder} 秒` : `${remainder} 秒`;
}

function formatSegmentTime(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "--:--";
  const seconds = Math.max(0, Math.floor(value));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

export default function PipelinePage() {
  const isAdminSession = Boolean(getAdminToken());
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const searchParamsKey = searchParams.toString();
  const operationKeys = useRef(new Map<string, string>());
  const candidatePreparationRef = useRef(new Map<string, Promise<void>>());
  const preparedCandidateIdsRef = useRef(new Set<string>());
  const reviewContextRef = useRef("");
  const voicePreviewRef = useRef<HTMLAudioElement | null>(null);
  const profileNameManuallyEditedRef = useRef(false);
  const runKeywordSearchRef = useRef<((forceRefresh?: boolean, requestedPlatforms?: BrowserPlatform[]) => Promise<void>) | null>(null);

  const [initializing, setInitializing] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionMessage, setActionMessage] = useState("");

  const [sourceMode, setSourceMode] = useState<SourceMode>("keyword");
  const [creationMode, setCreationMode] = useState<CreationMode>("manual");
  const [keyword, setKeyword] = useState("");
  const [publishedWindowDays, setPublishedWindowDays] = useState<PublishedWindowDays>(0);
  const [countPerPlatform, setCountPerPlatform] = useState<MaterialCount>(30);
  const [sourceValue, setSourceValue] = useState("");
  const [candidates, setCandidates] = useState<CrawlerCandidateResult[]>([]);
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [preparingCandidateId, setPreparingCandidateId] = useState("");
  const [preparedCandidateId, setPreparedCandidateId] = useState("");
  const [crawlerReason, setCrawlerReason] = useState<{ kind: string; message: string } | null>(null);
  const [hotWords, setHotWords] = useState<CrawlerHotWordItem[]>([]);
  const [materialSearchProgress, setMaterialSearchProgress] = useState<{
    startedAt: number;
    platforms: BrowserPlatform[];
  } | null>(null);
  const [materialSearchBatch, setMaterialSearchBatch] = useState<CrawlerBatchResponse | null>(null);
  const [materialSearchQueueId, setMaterialSearchQueueId] = useState<string | null>(null);
  const [materialSearchQueue, setMaterialSearchQueue] = useState<CrawlerKeywordQueueResponse | null>(null);
  const [pendingPlatformLoginSearch, setPendingPlatformLoginSearch] = useState<{
    platforms: BrowserPlatform[];
    forceRefresh: boolean;
  } | null>(null);
  const [materialSearchComplete, setMaterialSearchComplete] = useState(false);
  const [failedSearchPlatforms, setFailedSearchPlatforms] = useState<BrowserPlatform[]>([]);

  const [profiles, setProfiles] = useState<ProductionProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [assets, setAssets] = useState<AvatarAsset[]>([]);
  const [profileName, setProfileName] = useState("我的短视频 IP");
  const [avatarId, setAvatarId] = useState("");
  const [voiceId, setVoiceId] = useState("");

  const [platforms, setPlatforms] = useState<PublishPlatformCapability[]>([]);
  const [accounts, setAccounts] = useState<PublishAccount[]>([]);
  const [publishPlatforms, setPublishPlatforms] = useState<string[]>(["douyin"]);
  const [publishAccountAction, setPublishAccountAction] = useState("");
  const [workspaceConfiguration, setWorkspaceConfiguration] = useState<ProductionWorkspaceConfiguration>({ configured: false });
  const [browserDiscoveries, setBrowserDiscoveries] = useState<CrawlerBrowserDiscoveryCapabilities[]>([]);
  const [startingBrowserPlatform, setStartingBrowserPlatform] = useState<BrowserPlatform | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [sourceManagerOpen, setSourceManagerOpen] = useState(false);
  const [publishAccountManagerOpen, setPublishAccountManagerOpen] = useState(false);
  const [setupRightsHolder, setSetupRightsHolder] = useState("");
  const [profileCreateOpen, setProfileCreateOpen] = useState(false);
  const [profileEditorMode, setProfileEditorMode] = useState<"add" | "switch">("add");
  const [profileReturnToSetup, setProfileReturnToSetup] = useState(false);
  const [uploadingAvatar, setUploadingAvatar] = useState(false);
  const [voiceUploadOpen, setVoiceUploadOpen] = useState(false);
  const [uploadingVoice, setUploadingVoice] = useState(false);
  const [playingVoiceId, setPlayingVoiceId] = useState("");
  const [voicePreviewError, setVoicePreviewError] = useState("");
  const [avatarCapability, setAvatarCapability] = useState<AvatarCapability | null>(null);
  const [batches, setBatches] = useState<ProductionBatch[]>([]);
  const [recentMaterialBatches, setRecentMaterialBatches] = useState<CrawlerBatchResponse[]>([]);
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [workspace, setWorkspace] = useState<ProductionWorkspace | null>(null);
  const [reviewText, setReviewText] = useState("");
  const [reviewNote, setReviewNote] = useState("");
  const [creativePlan, setCreativePlan] = useState<ProductionCreativePlan | null>(null);
  const [creativePlanExpanded, setCreativePlanExpanded] = useState(false);
  const [candidatePage, setCandidatePage] = useState(1);
  const candidatePageSize = CANDIDATE_LIST_PREVIEW_LIMIT;
  const [publishTitle, setPublishTitle] = useState("");
  const [publishDescription, setPublishDescription] = useState("");
  const [publishTags, setPublishTags] = useState("");
  const [videoLoadError, setVideoLoadError] = useState(false);
  const [videoReloadKey, setVideoReloadKey] = useState(0);
  const [expandedWorkspaceStage, setExpandedWorkspaceStage] = useState<number | null>(null);
  const [materialSummaryExpanded, setMaterialSummaryExpanded] = useState(true);
  const [costSummaryExpanded, setCostSummaryExpanded] = useState(false);
  const [tasksExpanded, setTasksExpanded] = useState(false);
  const [profileSummaryExpanded, setProfileSummaryExpanded] = useState(false);
  const [transcriptionRetryConfirmOpen, setTranscriptionRetryConfirmOpen] = useState(false);

  useEffect(() => {
    setCreativePlanExpanded(false);
  }, [selectedRunId]);

  const completeProfiles = useMemo(
    () => profiles.filter((profile) => isUsableProfile(profile, assets)),
    [assets, profiles],
  );
  const selectedProfile = useMemo(
    () => completeProfiles.find((profile) => profile.profile_id === profileId) || null,
    [completeProfiles, profileId],
  );
  const selectedCandidate = useMemo(
    () => candidates.find((candidate) => candidate.video_id === selectedCandidateId) || null,
    [candidates, selectedCandidateId],
  );
  const prepareXiaohongshuCandidate = useCallback(
    (candidate: CrawlerCandidateResult): Promise<void> => {
      if (candidate.platform !== "xiaohongshu") return Promise.resolve();
      if (preparedCandidateIdsRef.current.has(candidate.video_id)) {
        setPreparedCandidateId(candidate.video_id);
        return Promise.resolve();
      }
      const pending = candidatePreparationRef.current.get(candidate.video_id);
      if (pending) return pending;

      setPreparingCandidateId(candidate.video_id);
      setPreparedCandidateId("");
      const preparation = resolveCrawlerCandidateOriginalMedia(candidate.video_id)
        .then(() => {
          preparedCandidateIdsRef.current.add(candidate.video_id);
          setPreparedCandidateId(candidate.video_id);
        })
        .catch((error) => {
          candidatePreparationRef.current.delete(candidate.video_id);
          throw error;
        })
        .finally(() => {
          setPreparingCandidateId((current) => (
            current === candidate.video_id ? "" : current
          ));
        });
      candidatePreparationRef.current.set(candidate.video_id, preparation);
      return preparation;
    },
    [],
  );
  const automaticCandidatePool = useMemo(
    () => selectAutomaticCandidates(candidates),
    [candidates],
  );
  const readySourceCount = browserDiscoveries.filter(sourcePlatformReady).length;
  const sourcePlatformNames = browserDiscoveries
    .filter(sourcePlatformReady)
    .map((connection) => (
      SOURCE_BROWSER_PLATFORMS.find((item) => item.platform === connection.platform)?.label
      || connection.platform_label
      || connection.platform
    ))
    .filter(Boolean)
    .join("、") || "未连接";
  const sourceConnectionReady = readySourceCount > 0;
  const allSourceConnectionsReady = readySourceCount === SOURCE_BROWSER_PLATFORMS.length;
  const selectedPublishAccountSummary = useMemo(() => {
    const ready = publishPlatforms.filter((platform) => (
      accounts.some((account) => account.platform === platform && account.status === "ready")
    )).length;
    return { ready, pending: publishPlatforms.length - ready };
  }, [accounts, publishPlatforms]);
  const waitingPublishAccountKey = useMemo(
    () => accounts
      .filter((account) => account.status === "browser_open")
      .map((account) => account.account_id)
      .join(","),
    [accounts],
  );
  const voiceSampleUploadAvailable = Boolean(
    avatarCapability?.enabled
    && (avatarCapability.provider_name === "local_avatar" || avatarCapability.supports_voice_sample_upload),
  );
  const avatarMaterialUploadAvailable = Boolean(
    avatarCapability?.enabled
    && (avatarCapability.provider_name === "local_avatar" || avatarCapability.supports_cloud_avatar_training),
  );
  const usableAvatarAssets = useMemo(
    () => assets.filter((asset) => asset.kind === "avatar" && asset.authorized && asset.status === "ready"),
    [assets],
  );
  const pendingAvatarAssets = useMemo(
    () => assets.filter((asset) => asset.kind === "avatar" && asset.authorized && asset.status !== "ready"),
    [assets],
  );
  const usableVoiceAssets = useMemo(
    () => assets.filter((asset) => asset.kind === "voice" && asset.authorized && asset.status === "ready"),
    [assets],
  );
  const pendingVoiceAssets = useMemo(
    () => assets.filter((asset) => asset.kind === "voice" && asset.authorized && asset.status !== "ready"),
    [assets],
  );
  const selectedCreatorVoice = useMemo(
    () => usableVoiceAssets.find((asset) => asset.asset_id === voiceId) || null,
    [usableVoiceAssets, voiceId],
  );
  const voicePreviewSrc = selectedCreatorVoice?.preview_url
    || (voiceId ? `/api/v1/avatar/assets/${encodeURIComponent(voiceId)}/voice-preview` : "");
  const activeItem = useMemo(() => {
    if (!workspace) return undefined;
    return workspace.items.find((item) => item.run_id === selectedRunId)
      || workspace.items.find((item) => item.run_id === workspace.current_run_id)
      || workspace.items[0];
  }, [selectedRunId, workspace]);
  const officialPageTaskIds = activeItem?.publish.official_page_task_ids !== undefined
    ? activeItem.publish.official_page_task_ids
    : (activeItem?.publish.targets || []).some((target) => target.mode === "real")
      ? activeItem?.publish.task_ids || []
      : [];
  const preparedPublishTaskIds = activeItem?.publish.prepared_task_ids !== undefined
    ? activeItem.publish.prepared_task_ids
    : (
        activeItem?.publish.status === "manual_ready"
        && activeItem.publish.stage?.startsWith("已在账号")
          ? activeItem.publish.task_ids
          : []
      );
  const publishPagePrepared = preparedPublishTaskIds.length > 0;
  const publishCompleted = activeItem?.publish.status === "succeeded";
  const manualPublishTaskIds = activeItem?.publish.manual_task_ids !== undefined
    ? activeItem.publish.manual_task_ids
    : activeItem?.publish.status === "manual_ready"
      && (activeItem.publish.targets || []).some((target) => target.mode === "manual")
      ? activeItem.publish.task_ids
      : [];
  const activePublishPlatformLabel = useMemo(() => {
    const labels = (activeItem?.publish.targets || [])
      .map((target) => target.display_name || PLATFORM_LABELS[target.platform] || target.platform)
      .filter(Boolean);
    const unique = [...new Set(labels)];
    if (unique.length) return unique.join("、");
    return "所选平台";
  }, [activeItem?.publish.targets]);
  const officialPagePlatformLabel = useMemo(() => {
    const labels = (activeItem?.publish.targets || [])
      .filter((target) => target.mode === "real")
      .map((target) => target.display_name || PLATFORM_LABELS[target.platform] || target.platform)
      .filter(Boolean);
    return [...new Set(labels)].join("、") || "所选平台";
  }, [activeItem?.publish.targets]);
  const automaticPublishPlatformLabel = useMemo(() => {
    const labels = (activeItem?.publish.targets || [])
      .filter((target) => target.mode === "real" && !target.manual_only)
      .map((target) => target.display_name || PLATFORM_LABELS[target.platform] || target.platform)
      .filter(Boolean);
    return [...new Set(labels)].join("、") || officialPagePlatformLabel;
  }, [activeItem?.publish.targets, officialPagePlatformLabel]);
  const currentStage = activeItem?.stage || activeItem?.current_stage || workspace?.current_stage || "source";
  const nextAction = activeItem?.next_action || workspace?.next_action || "start";
  const allowedActions = activeItem?.allowed_actions || workspace?.allowed_actions || [];
  const activeReview = activeItem?.reviews;
  const currentBusinessStage = businessStageIndex(currentStage);
  const transcriptionUploadRetry = activeItem?.recovery?.kind === "transcription_upload_retry"
    ? activeItem.recovery
    : null;
  const currentActionHeading = workspace?.status === "outcome_unknown"
    ? "转写结果待核对"
    : transcriptionUploadRetry
      ? "素材上传失败"
      : (STAGE_LABEL[currentStage] || currentStage);

  useEffect(() => {
    setExpandedWorkspaceStage(currentBusinessStage);
    setMaterialSummaryExpanded(true);
    setCostSummaryExpanded(false);
    setTasksExpanded(false);
    setProfileSummaryExpanded(false);
  }, [currentBusinessStage, selectedBatchId]);
  const activeProfile = workspace?.profile || selectedProfile;
  const profileAvatar = assets.find((asset) => asset.asset_id === activeProfile?.avatar_id);
  const profileVoice = assets.find((asset) => asset.asset_id === activeProfile?.voice_id);
  const pendingWorkbenchBatches = useMemo(
    () => batches
      .filter((batch) => !isFinishedBatch(batch))
      .sort((left, right) => {
        // 主排序：按创建时间倒序，最新任务在前面
        const createdDelta = new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
        if (createdDelta !== 0) return createdDelta;
        // 兜底：未结任务按状态优先级（awaiting_review > failed > other）
        return workbenchTaskPriority(left, selectedBatchId) - workbenchTaskPriority(right, selectedBatchId);
      }),
    [batches, selectedBatchId],
  );
  // 合并 pending + recent material, 按 created_at desc 排序, 取前 2 条;
  // 不再按业务分组, 用户只关心"最近还在做的任务".
  const unifiedWorkbenchBatches = useMemo(
    () => {
      type Item =
        | { type: "pending"; batch: ProductionBatch }
        | { type: "recent"; batch: CrawlerBatchResponse };
      const items: Item[] = [
        ...pendingWorkbenchBatches.map((batch) => ({ type: "pending" as const, batch })),
        ...recentMaterialBatches
          .filter((batch) => batch.total_candidates > 0)
          .map((batch) => ({ type: "recent" as const, batch })),
      ];
      return items
        .sort(
          (left, right) =>
            new Date(right.batch.created_at ?? 0).getTime() -
            new Date(left.batch.created_at ?? 0).getTime(),
        )
        .slice(0, WORKBENCH_TASK_LIMIT);
    },
    [pendingWorkbenchBatches, recentMaterialBatches],
  );

  const getOperationKey = useCallback((operation: string, payload: unknown) => {
    const fingerprint = `${operation}:${stableFingerprint(payload)}`;
    const existing = operationKeys.current.get(fingerprint);
    if (existing) return existing;
    const key = `workspace-${operation}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    operationKeys.current.set(fingerprint, key);
    return key;
  }, []);

  const loadWorkspace = useCallback(async (batchId: string, silent = false) => {
    if (!batchId) return null;
    try {
      const data = await getProductionBatchWorkspace(batchId);
      setWorkspace(data);
      setSelectedBatchId(batchId);
      setSelectedRunId((current) => (
        data.items.some((item) => item.run_id === current)
          ? current
          : data.current_run_id || data.items[0]?.run_id || ""
      ));
      setProfileId(data.batch.profile_id);
      const configuredPlatforms = data.batch.execution_config.publish_platforms;
      if (Array.isArray(configuredPlatforms)) {
        const restored = configuredPlatforms.filter(
          (item): item is string => typeof item === "string" && Boolean(item),
        );
        if (restored.length) setPublishPlatforms(restored);
      }
      setBatches((current) => {
        const without = current.filter((batch) => batch.batch_id !== data.batch.batch_id);
        return [data.batch, ...without];
      });
      return data;
    } catch (error) {
      if (!silent) setActionError((error as Error).message || "读取工作台失败");
      return null;
    }
  }, []);

  const returnToMaterialSelection = useCallback(() => {
    setWorkspace(null);
    setSelectedBatchId("");
    setSelectedRunId("");
    setReviewText("");
    setReviewNote("");
    setCreativePlan(null);
    setPublishTitle("");
    setPublishDescription("");
    setPublishTags("");
    setActionError("");
    setActionMessage("");
    setExpandedWorkspaceStage(null);
    setMaterialSummaryExpanded(true);
    navigate("/pipeline", { replace: true });
  }, [navigate]);

  const restoreMaterialSearchBatch = useCallback(async (
    crawlerBatchId: string,
    requestedCandidateId = "",
    quickRestore = false,
  ) => {
    const batch = await (quickRestore || !requestedCandidateId
      ? getCrawlerBatchForSelection(crawlerBatchId)
      : getCrawlerBatch(crawlerBatchId));
    const found = strictCandidates(batch, requestedCandidateId);
    const requestedCandidateFound = Boolean(
      requestedCandidateId
      && found.some((candidate) => candidate.video_id === requestedCandidateId),
    );
    setSourceMode("keyword");
    setCreationMode("manual");
    setKeyword(batch.keyword);
    setMaterialSearchBatch(batch);
    setMaterialSearchProgress(null);
    setMaterialSearchQueueId(null);
    setMaterialSearchQueue(null);
    setMaterialSearchComplete(true);
    setCandidates(found);
    setCandidatePage(1);
    setSelectedCandidateId(
      requestedCandidateId
        ? (requestedCandidateFound ? requestedCandidateId : "")
        : found[0]?.video_id || "",
    );
    if (requestedCandidateId && !requestedCandidateFound) {
      setCrawlerReason({
        kind: "所选候选不可用",
        message: "素材发现中选择的内容已不在本次结果中；不会自动替换成另一条，请返回素材发现重新选择。",
      });
    } else if (!found.length) {
      setCrawlerReason(diagnoseCrawlerResult(batch));
    } else if (
      requestedCandidateId
      && found[0]?.video_id === requestedCandidateId
      && !batch.platform_runs.some((run) =>
        run.candidates.some((candidate) => candidate.video_id === requestedCandidateId))
    ) {
      setCrawlerReason({
        kind: "未达热门阈值",
        message: "已按素材发现中的明确选择带入该内容；它未进入当前热门主榜，请确认后再创建任务。",
      });
    } else {
      setCrawlerReason(null);
    }
    return batch;
  }, []);

  const loadInitialData = useCallback(async () => {
    setInitializing(true);
    setLoadError("");
    try {
      const optionalData = Promise.allSettled([
        listPublishPlatforms(),
        listPublishAccounts(),
        getAvatarCapabilities(),
      ] as const);
      const batchData = listProductionBatches();
      const crawlerBatchData = listCrawlerBatches();
      const [profileData, assetData, configuration] =
        await Promise.all([
          listProductionProfiles(),
          listAvatarAssets(),
          getProductionWorkspaceConfiguration(),
        ]);
      setProfiles(profileData.items);
      setAssets(assetData);
      void batchData.then((result) => setBatches(result.items)).catch(() => undefined);
      void crawlerBatchData.then((result) => setRecentMaterialBatches(result.items)).catch(() => undefined);
      setWorkspaceConfiguration(configuration);
      void optionalData.then(([platformResult, accountResult, avatarResult]) => {
        if (platformResult.status === "fulfilled") setPlatforms(platformResult.value.platforms);
        if (accountResult.status === "fulfilled") setAccounts(accountResult.value);
        if (avatarResult.status === "fulfilled") setAvatarCapability(avatarResult.value);
      });
      setSetupRightsHolder(configuration.rights_holder || "");
      if (configuration.default_publish_platforms?.length) {
        setPublishPlatforms(configuration.default_publish_platforms);
      }

      const validProfiles = profileData.items.filter(
        (profile) => isUsableProfile(profile, assetData),
      );
      if (!configuration.configured && validProfiles.length) {
        setSetupOpen(true);
      }
      const rememberedProfile = localStorage.getItem(PROFILE_STORAGE_KEY) || "";
      setProfileId((current) =>
        validProfiles.find((profile) => profile.profile_id === current)?.profile_id
        || validProfiles.find((profile) => profile.profile_id === configuration.default_profile_id)?.profile_id
        || validProfiles.find((profile) => profile.profile_id === rememberedProfile)?.profile_id
        || validProfiles[0]?.profile_id
        || "",
      );

      const requestedSearchParams = new URLSearchParams(searchParamsKey);
      const requestedBatch = requestedSearchParams.get("batch") || "";
      const requestedRun = requestedSearchParams.get("run") || "";
      if (requestedBatch) {
        setSelectedRunId(requestedRun);
        setSelectedBatchId(requestedBatch);
        await loadWorkspace(requestedBatch);
      } else if (requestedRun) {
        const recoveredBatches = await batchData;
        const recoveredBatch = recoveredBatches.items.find(
          (batch) => batch.items.some((item) => item.run_id === requestedRun),
        );
        if (recoveredBatch) {
          setSelectedRunId(requestedRun);
          setSelectedBatchId(recoveredBatch.batch_id);
          await loadWorkspace(recoveredBatch.batch_id);
        }
      }

      const crawlerBatchId = requestedSearchParams.get("crawler_batch_id") || "";
      const requestedCandidateId = requestedSearchParams.get("candidate_id") || "";
      if (crawlerBatchId) {
        await restoreMaterialSearchBatch(crawlerBatchId, requestedCandidateId);
      }
    } catch (error) {
      setLoadError((error as Error).message || "工作台初始化失败");
    } finally {
      setInitializing(false);
    }
  }, [loadWorkspace, restoreMaterialSearchBatch, searchParamsKey]);

  useEffect(() => {
    void loadInitialData();
  }, [loadInitialData]);

  const loadSourceConnections = useCallback(async () => {
    const statuses = await Promise.all(
      SOURCE_BROWSER_PLATFORMS.map(async ({ platform, label }) => {
        try {
          const status = await getCrawlerBrowserDiscoveryCapabilities(platform);
          return {
            ...status,
            platform,
            platform_label: label,
          };
        } catch (error) {
          return {
            platform,
            platform_label: label,
            enabled: false,
            running: false,
            login_required: false,
            missing_configuration: [],
            browser_channel: "",
            ready_to_crawl: false,
            phase: "unavailable",
            provider_name: `${platform}_local_browser`,
            message: (error as Error).message || `${label}连接状态暂时无法读取。`,
          } satisfies CrawlerBrowserDiscoveryCapabilities;
        }
      }),
    );
    setBrowserDiscoveries(statuses);
    return statuses;
  }, []);

  useEffect(() => {
    const audio = voicePreviewRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
    }
    setPlayingVoiceId("");
    setVoicePreviewError("");
  }, [voiceId]);

  useEffect(() => {
    void loadSourceConnections();
  }, [loadSourceConnections]);

  useEffect(() => {
    if (!waitingPublishAccountKey) return;
    const timer = window.setInterval(() => {
      const accountIds = waitingPublishAccountKey.split(",").filter(Boolean);
      void Promise.all(accountIds.map((accountId) => getPublishAccountStatus(accountId)))
        .then((updatedAccounts) => {
          setAccounts((current) => current.map((account) => (
            updatedAccounts.find((updated) => updated.account_id === account.account_id) || account
          )));
        })
        .catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [waitingPublishAccountKey]);

  useEffect(() => {
    let active = true;
    void getCrawlerHotWords()
      .then((response) => {
        if (active) setHotWords(response.words.slice(0, 8));
      })
      .catch(() => {
        if (active) setHotWords([]);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const status = workspace?.status || workspace?.batch.status;
    if (!selectedBatchId || ["succeeded", "failed", "partial", "completed", "outcome_unknown"].includes(status || "")) return;
    const timer = window.setInterval(() => {
      void loadWorkspace(selectedBatchId, true);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [loadWorkspace, selectedBatchId, workspace?.batch.status, workspace?.status]);

  useEffect(() => {
    if (!activeItem) return;
    const context = `${activeItem.run_id}:${nextAction}`;
    if (reviewContextRef.current === context) return;
    reviewContextRef.current = context;
    if (nextAction === "review_transcript") {
      setReviewText(activeReview?.transcript.draft_text || activeReview?.transcript.approved_text || "");
      setCreativePlan(null);
    } else if (nextAction === "review_script") {
      const script = activeReview?.script.draft_text || activeReview?.script.approved_text || "";
      setReviewText(script);
      const savedPlan = activeReview?.script.creative_plan;
      setCreativePlan(
        savedPlan
        && savedPlan.hook
        && savedPlan.call_to_action
        && savedPlan.key_points?.length
        && savedPlan.visual_sections?.length
          ? savedPlan
          : buildCreativePlan(script),
      );
    } else if (stageIndex(currentStage) === 4 && activeItem.publish.draft) {
      const draft = activeItem.publish.draft;
      setPublishTitle(draft?.title || "");
      setPublishDescription(draft?.description || "");
      setPublishTags((draft?.tags || []).join("、"));
      setReviewText("");
      setCreativePlan(null);
    } else {
      setReviewText("");
      setCreativePlan(null);
    }
    setReviewNote("");
  }, [activeItem, activeReview, currentStage, nextAction]);

  const crawlerRequest = useMemo<CrawlerSearchRequest>(() => ({
    keyword: keyword.trim(),
    published_window_days: publishedWindowDays,
    hotspot_window_hours: 168,
    count_per_platform: countPerPlatform,
    force_refresh: false,
    mode: "smart",
    related_terms: [],
    allow_related_fallback: false,
    track_trend: false,
    target_main_count: countPerPlatform,
    // 免费检索固定不允许付费兜底；需与后端免费模式的 0 次上限保持一致。
    max_paid_calls: 0,
    allow_paid_fallback: false,
    hotspot_result_limit: countPerPlatform,
    // 只检索已连接就绪的素材平台，避免打开用户未配置的网站。
    platforms: browserDiscoveries.filter(sourcePlatformReady).map((item) => item.platform).filter(isBrowserPlatform),
  }), [browserDiscoveries, countPerPlatform, keyword, publishedWindowDays]);

  const retrySource = useMemo(() => {
    const alternatives = browserDiscoveries.filter((item) => (
      item.enabled
      && isBrowserPlatform(item.platform)
      && !failedSearchPlatforms.includes(item.platform)
      && !(item.missing_configuration || []).length
    ));
    return alternatives.find((item) => item.ready_to_crawl)
      || alternatives.find((item) => item.platform === "kuaishou")
      || alternatives[0]
      || null;
  }, [browserDiscoveries, failedSearchPlatforms]);

  const finishMaterialSearchReveal = useCallback((batch: CrawlerBatchResponse) => {
    const found = strictCandidates(batch);
    setCandidates(found);
    setSelectedCandidateId(found[0]?.video_id || "");
    if (found.length) {
      setFailedSearchPlatforms([]);
      setActionMessage(
        creationMode === "auto"
          ? `找到 ${found.length} 条相关素材，已按当前排序进入自动创作。`
          : `找到 ${found.length} 条相关素材，请选择要使用的素材。`,
      );
    } else {
      const attemptedPlatforms = (batch.platforms || batch.platform_runs.map((run) => run.platform))
        .filter(isBrowserPlatform);
      setFailedSearchPlatforms(attemptedPlatforms);
      const attemptedLabels = attemptedPlatforms.map((platform) => PLATFORM_LABELS[platform] || platform).join("、") || "当前平台";
      const reason = batch.error
        || batch.platform_runs.map((run) => run.crawl_stop_message || run.payload_diagnostic || run.error).find(Boolean)
        || "平台本次没有返回可用素材";
      const fallback = browserDiscoveries.find((item) => (
        item.enabled
        && isBrowserPlatform(item.platform)
        && !attemptedPlatforms.includes(item.platform)
        && !(item.missing_configuration || []).length
        && item.ready_to_crawl
      )) || browserDiscoveries.find((item) => (
        item.enabled
        && item.platform === "kuaishou"
        && !attemptedPlatforms.includes("kuaishou")
        && !(item.missing_configuration || []).length
      ));
      const readableReason = reason.replace(/[。.!！?？]+$/, "");
      setCrawlerReason({
        kind: "平台本次无结果",
        message: fallback
          ? `${attemptedLabels}本次未取到素材：${readableReason}。已保留关键词“${batch.keyword}”，可以切换到${fallback.platform_label || PLATFORM_LABELS[fallback.platform || ""] || "其他平台"}重新搜索，无需等待当前平台冷却。`
          : `${attemptedLabels}本次未取到素材：${readableReason}。已保留关键词“${batch.keyword}”，请检查平台登录后再试。`,
      });
    }
    setMaterialSearchProgress(null);
    setMaterialSearchQueueId(null);
    setMaterialSearchQueue(null);
    setMaterialSearchComplete(false);
    setBusy(false);
  }, [browserDiscoveries, creationMode]);

  const runKeywordSearch = async (forceRefresh = false, requestedPlatforms?: BrowserPlatform[]) => {
    if (keyword.trim().length < 2 || keyword.trim().length > 50) {
      setActionError("请输入 2–50 个字的关键词。");
      return;
    }
    const targetPlatforms = requestedPlatforms || crawlerRequest.platforms || [];
    if (!targetPlatforms.length) {
      setActionError("请先完成素材平台连接，再重新搜索。");
      return;
    }
    setBusy(true);
    setActionError("");
    setActionMessage("");
    setCrawlerReason(null);
    setPendingPlatformLoginSearch(null);
    setMaterialSearchBatch(null);
    setMaterialSearchQueueId(null);
    setMaterialSearchQueue(null);
    setMaterialSearchComplete(false);
    setMaterialSearchProgress({
      startedAt: Date.now(),
      platforms: [...targetPlatforms].filter(isBrowserPlatform),
    });
    try {
      const readyPlatforms: BrowserPlatform[] = [];
      for (const platform of targetPlatforms) {
        // The page can stay open after a platform session expires. Refresh the
        // live state before submitting instead of trusting the initial badge.
        let status = await getCrawlerBrowserDiscoveryCapabilities(platform);
        if (!status.ready_to_crawl) {
          status = await startCrawlerBrowserDiscovery(platform);
        }
        const label = SOURCE_BROWSER_PLATFORMS.find((item) => item.platform === platform)?.label || platform;
        const normalized = { ...status, platform, platform_label: label };
        setBrowserDiscoveries((items) => items.map((item) => item.platform === platform ? normalized : item));
        if (!status.ready_to_crawl) {
          setPendingPlatformLoginSearch({ platforms: targetPlatforms, forceRefresh });
          setCrawlerReason({
            kind: `等待${label}登录`,
            message: `${label}登录窗口已打开。请完成平台要求的扫码或人工验证；完成后系统会自动继续搜索“${keyword.trim()}”，无需重新输入关键词。`,
          });
          setMaterialSearchProgress(null);
          setBusy(false);
          return;
        }
        readyPlatforms.push(platform);
      }
      const request = {
        ...crawlerRequest,
        platforms: readyPlatforms,
        force_refresh: forceRefresh,
      };
      // 与“素材发现”页共用同一条免费搜索链路。这里不再额外预判，
      // 避免浏览器状态的瞬时差异把本可执行的搜索提前拦成“换关键词”。
      const task = await createCrawlerProgressiveBatch(request);
      setMaterialSearchQueueId(task.queue_id);
      setMaterialSearchQueue(task.queue);
    } catch (error) {
      setCrawlerReason({
        kind: "检索没有开始",
        message: (error as Error).message || "搜索请求没有成功提交；你的关键词不会丢失。",
      });
      setMaterialSearchBatch(null);
      setMaterialSearchQueueId(null);
      setMaterialSearchQueue(null);
      setMaterialSearchComplete(false);
      setMaterialSearchProgress(null);
      setBusy(false);
    }
  };

  runKeywordSearchRef.current = runKeywordSearch;

  useEffect(() => {
    if (!pendingPlatformLoginSearch) return undefined;
    let active = true;
    let timer: number | undefined;
    let checks = 0;

    const checkLoginAndResume = async () => {
      try {
        const statuses = await Promise.all(
          pendingPlatformLoginSearch.platforms.map((platform) => getCrawlerBrowserDiscoveryCapabilities(platform)),
        );
        if (!active) return;
        setBrowserDiscoveries((current) => current.map((item) => {
          const status = statuses.find((candidate) => candidate.platform === item.platform);
          return status ? { ...status, platform_label: status.platform_label || item.platform_label } : item;
        }));
        if (statuses.every((status) => status.ready_to_crawl)) {
          setPendingPlatformLoginSearch(null);
          setActionMessage("平台登录已确认，正在自动继续刚才的素材搜索。");
          void runKeywordSearchRef.current?.(
            pendingPlatformLoginSearch.forceRefresh,
            pendingPlatformLoginSearch.platforms,
          );
          return;
        }
      } catch {
        // A transient local status read is retried by the next bounded poll.
      }
      checks += 1;
      if (checks >= 150) {
        if (active) {
          setPendingPlatformLoginSearch(null);
          setCrawlerReason({
            kind: "仍在等待平台登录",
            message: "小红书登录窗口仍未完成验证，关键词已保留。完成后点击“找素材”即可继续。",
          });
        }
        return;
      }
      timer = window.setTimeout(() => void checkLoginAndResume(), 2_000);
    };

    void checkLoginAndResume();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [pendingPlatformLoginSearch]);

  useEffect(() => {
    if (!materialSearchQueueId) return undefined;
    let active = true;
    const sync = async () => {
      try {
        const queue = await getCrawlerKeywordQueue(materialSearchQueueId);
        if (!active) return;
        const item = queue.items[0];
        const progressBatch = buildPipelineProgressBatch(queue);
        setMaterialSearchQueue(queue);
        if (progressBatch) setMaterialSearchBatch(progressBatch);
        if (!crawlerQueueItemFinished(queue)) return;

        let finalBatch = progressBatch;
        const progressHasCandidates = Boolean(progressBatch?.platform_runs.some(
          (run) => run.candidates.length > 0 || (run.reference_candidates || []).length > 0,
        ));
        if (item?.batch_id) {
          try {
            finalBatch = await Promise.race([
              getCrawlerBatch(item.batch_id),
              new Promise<never>((_, reject) => window.setTimeout(
                () => reject(new Error("最终详情读取较慢，已先展示当前找到的素材。")),
                1_500,
              )),
            ]);
          } catch (error) {
            if (!progressHasCandidates) {
              setCrawlerReason({
                kind: "结果正在整理",
                message: `最终结果读取失败：${(error as Error).message || "请稍后重试"}`,
              });
            }
          }
        }
        if (!active) return;
        if (finalBatch) {
          setMaterialSearchBatch(finalBatch);
          setMaterialSearchComplete(true);
        } else {
          setMaterialSearchProgress(null);
          setCrawlerReason({
            kind: "检索没有完成",
            message: item?.error || item?.progress_message || "找素材没有返回可查看结果，请稍后重试。",
          });
        }
        setMaterialSearchQueueId(null);
        setBusy(false);
      } catch (error) {
        if (active) {
          setCrawlerReason({
            kind: "进度读取失败",
            message: `找素材仍在后台运行，进度暂时读取失败：${(error as Error).message || "请稍后刷新"}`,
          });
        }
      }
    };
    void sync();
    const timer = window.setInterval(() => void sync(), 1_500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [materialSearchQueueId]);

  const saveProfile = async () => {
    setActionError("");
    if (!profileName.trim() || !avatarId || !voiceId) {
      setActionError("请填写出镜人名称，并选择形象和声音。");
      return;
    }
    setBusy(true);
    try {
      const requestedName = profileName.trim();
      const existingNames = new Set(profiles.map((profile) => profile.name.trim()));
      let savedName = requestedName;
      let index = 2;
      while (existingNames.has(savedName)) {
        savedName = `${requestedName} ${index}`;
        index += 1;
      }
      const created = await createProductionProfile({
        name: savedName,
        description: "智能创作工作台默认单条配方",
        target_audience: "",
        platform: "douyin",
        script_style: "",
        avatar_id: avatarId,
        voice_id: voiceId,
        tags: [],
      });
      setProfiles((current) => [created, ...current]);
      setProfileId(created.profile_id);
      localStorage.setItem(PROFILE_STORAGE_KEY, created.profile_id);
      setProfileCreateOpen(false);
      setSetupOpen(true);
      setActionMessage(
        savedName === requestedName
          ? "出镜人已保存；再完成一次基础设置就能开始创作。"
          : `出镜组合已保存为“${savedName}”；再完成一次基础设置就能开始创作。`,
      );
    } catch (error) {
      setActionError((error as Error).message || "保存 IP 配方失败");
    } finally {
      setBusy(false);
    }
  };

  const openProfileCreator = () => {
    setProfileReturnToSetup(false);
    setProfileEditorMode("add");
    profileNameManuallyEditedRef.current = false;
    setProfileName("新出镜人");
    setAvatarId("");
    setVoiceId(selectedProfile?.voice_id || "");
    setVoicePreviewError("");
    setVoiceUploadOpen(false);
    setSetupOpen(false);
    setProfileCreateOpen(true);
  };

  const openProfileCreatorFromSetup = () => {
    openProfileCreator();
    setProfileReturnToSetup(true);
  };

  const openProfileSwitcher = () => {
    setProfileReturnToSetup(true);
    setProfileEditorMode("switch");
    setVoicePreviewError("");
    setVoiceUploadOpen(false);
    setSetupOpen(false);
    setProfileCreateOpen(true);
  };

  const switchToSavedProfile = (profile: ProductionProfile) => {
    setProfileId(profile.profile_id);
    localStorage.setItem(PROFILE_STORAGE_KEY, profile.profile_id);
    setProfileCreateOpen(false);
    setSetupOpen(true);
    setActionMessage(`已切换到“${profile.name}”。`);
  };

  const selectProfileAvatar = (asset: AvatarAsset) => {
    setAvatarId(asset.asset_id);
    if (!profileNameManuallyEditedRef.current) setProfileName(asset.name);
    const matchingVoice = usableVoiceAssets.find((voice) => voice.name === asset.name);
    if (matchingVoice) setVoiceId(matchingVoice.asset_id);
  };

  const uploadAvatarMaterial = async (file: File) => {
    const supportsLocalUpload = avatarCapability?.provider_name === "local_avatar";
    if (!supportsLocalUpload && !avatarCapability?.supports_cloud_avatar_training) {
      setActionError("当前数字人服务暂未开放新增形象。");
      return false;
    }
    setUploadingAvatar(true);
    setActionError("");
    try {
      const name = file.name.replace(/\.[^.]+$/, "") || "新出镜人形象";
      const asset = supportsLocalUpload
        ? await uploadAvatarAsset({ kind: "avatar", file, name })
        : await trainCloudAvatar({ file, name });
      setAssets((current) => [asset, ...current.filter((item) => item.asset_id !== asset.asset_id)]);
      if (asset.status === "ready") {
        setAvatarId(asset.asset_id);
        if (!profileNameManuallyEditedRef.current) setProfileName(asset.name);
        setActionMessage("人脸素材已上传并自动选中。");
      } else {
        setActionMessage(asset.status_message || "人脸素材已提交训练，完成后会出现在形象列表中。");
      }
    } catch (error) {
      setActionError((error as Error).message || "人脸素材上传失败，请保留当前内容后重试。");
    } finally {
      setUploadingAvatar(false);
    }
    return false;
  };

  const uploadVoiceSample = async (file: File) => {
    const supportsLocalUpload = avatarCapability?.provider_name === "local_avatar";
    if (!supportsLocalUpload && !avatarCapability?.supports_voice_sample_upload) {
      setActionError("当前数字人服务暂未开放声音上传。");
      return false;
    }
    setUploadingVoice(true);
    setActionError("");
    try {
      const name = file.name.replace(/\.[^.]+$/, "") || "新声音";
      const asset = supportsLocalUpload
        ? await uploadAvatarAsset({ kind: "voice", file, name })
        : await trainCloudVoice({ file, name });
      setAssets((current) => [asset, ...current.filter((item) => item.asset_id !== asset.asset_id)]);
      if (asset.status === "ready") {
        setVoiceId(asset.asset_id);
        setVoiceUploadOpen(false);
        setActionMessage("声音已上传并自动选中。");
      } else {
        setVoiceUploadOpen(false);
        setActionMessage(
          asset.status_message
          || "声音样本已保存，但当前声音服务尚未开通；暂时继续使用原来的声音。",
        );
      }
    } catch (error) {
      setActionError((error as Error).message || "声音上传失败，请保留当前内容后重试。");
    } finally {
      setUploadingVoice(false);
    }
    return false;
  };

  const toggleVoicePreview = async () => {
    const audio = voicePreviewRef.current;
    if (!audio || !voiceId) return;
    if (!audio.paused) {
      audio.pause();
      audio.currentTime = 0;
      setPlayingVoiceId("");
      return;
    }
    setVoicePreviewError("");
    try {
      audio.currentTime = 0;
      await audio.play();
    } catch {
      setPlayingVoiceId("");
      setVoicePreviewError("暂时无法试听这个声音，请稍后重试。");
    }
  };

  const saveWorkspaceSetup = async () => {
    if (!publishPlatforms.length) {
      setActionError("请至少选择一个发布平台。");
      return;
    }
    if (!sourceConnectionReady) {
      setActionError("请先连接至少一个素材网站；发布账号可以在成片确认发布前再登录。");
      return;
    }
    const rightsHolder = workspaceConfiguration.rights_holder?.trim()
      || setupRightsHolder.trim()
      || selectedProfile?.name.trim()
      || "当前操作人";
    setBusy(true);
    setActionError("");
    try {
      const configuration = await saveProductionWorkspaceConfiguration({
        rightsHolder,
        agreementAccepted: true,
        defaultProfileId: profileId || null,
        defaultPublishPlatforms: publishPlatforms.length ? publishPlatforms : ["douyin"],
      });
      setWorkspaceConfiguration(configuration);
      setSetupRightsHolder(configuration.rights_holder || rightsHolder);
      setPublishPlatforms(configuration.default_publish_platforms || ["douyin"]);
      setSetupOpen(false);
      setActionMessage(
        selectedPublishAccountSummary.pending
          ? "创作设置已保存。未登录的发布网站不会影响制作，确认发布前再完成登录即可。"
          : "创作设置已保存，现在可以开始本次创作。",
      );
    } catch (error) {
      setActionError((error as Error).message || "基础设置保存失败");
    } finally {
      setBusy(false);
    }
  };

  const refreshSourceConnections = async () => {
    setBusy(true);
    setActionError("");
    try {
      const statuses = await loadSourceConnections();
      const readyCount = statuses.filter(sourcePlatformReady).length;
      if (readyCount > 0) {
        setActionMessage(`已有 ${readyCount}/${SOURCE_BROWSER_PLATFORMS.length} 个素材平台可用，可以进入工作台。`);
      } else {
        setActionError("素材平台都还没有准备好，请完成登录或检查浏览器后再试一次。");
      }
    } catch (error) {
      setActionError((error as Error).message || "暂时无法检查素材平台，请稍后再试。");
    } finally {
      setBusy(false);
    }
  };

  const startSourceConnection = async (platform: BrowserPlatform) => {
    setBusy(true);
    setStartingBrowserPlatform(platform);
    setActionError("");
    try {
      const status = await startCrawlerBrowserDiscovery(platform);
      const label = SOURCE_BROWSER_PLATFORMS.find((item) => item.platform === platform)?.label || status.platform_label || platform;
      const normalized = { ...status, platform, platform_label: label };
      setBrowserDiscoveries((current) => current.map(
        (item) => item.platform === platform ? normalized : item,
      ));
      setActionMessage(
        status.ready_to_crawl
          ? `${label}已连接。`
          : `${label}登录窗口已打开，请在窗口中扫码或完成验证，然后刷新状态。建议使用专门的采集小号登录，避免主账号风险。`,
      );
    } catch (error) {
      setActionError((error as Error).message || "平台浏览器没有打开，请稍后再试。");
    } finally {
      setStartingBrowserPlatform(null);
      setBusy(false);
    }
  };

  const upsertPublishAccount = (account: PublishAccount) => {
    setAccounts((current) => (
      current.some((item) => item.account_id === account.account_id)
        ? current.map((item) => item.account_id === account.account_id ? account : item)
        : [...current, account]
    ));
  };

  const connectPublishAccountFromSetup = async (platform: string) => {
    setPublishAccountAction(platform);
    setActionError("");
    try {
      let account = accounts.find(
        (item) => item.platform === platform && item.status !== "ready",
      );
      if (!account) {
        account = await createPublishAccount({
          platform,
          name: DEFAULT_PUBLISH_ACCOUNT_NAME,
        });
        upsertPublishAccount(account);
      }
      const connected = await connectPublishAccount(account.account_id);
      upsertPublishAccount(connected);
      if (connected.status === "ready") {
        setPublishPlatforms((current) => (
          current.includes(platform) ? current : [...current, platform]
        ));
      }
      setActionMessage(
        `${PLATFORM_LABELS[platform] || platform}官方登录窗口已打开，扫码完成后系统会自动检查。建议使用专门的发布小号，避免主账号风险。`,
      );
    } catch (error) {
      setActionError((error as Error).message || "发布账号扫码窗口没有打开，请稍后再试。");
    } finally {
      setPublishAccountAction("");
    }
  };

  const refreshPublishAccount = async (accountId: string, platform: string) => {
    setPublishAccountAction(platform);
    setActionError("");
    try {
      const account = await getPublishAccountStatus(accountId);
      upsertPublishAccount(account);
      if (account.status === "ready") {
        setPublishPlatforms((current) => (
          current.includes(platform) ? current : [...current, platform]
        ));
        setActionMessage(`${PLATFORM_LABELS[platform] || platform}发布账号已核验，可以发布。`);
      } else {
        setActionError(account.message || "平台还没有完成登录，请在官方窗口完成扫码后再检查。");
      }
    } catch (error) {
      setActionError((error as Error).message || "暂时无法核验发布账号，请稍后再试。");
    } finally {
      setPublishAccountAction("");
    }
  };

  const validateExecution = () => {
    if (!workspaceConfiguration.configured) return "请先完成一次基础设置。";
    if (!sourceConnectionReady) return "请先完成开工前的素材浏览器连接。";
    if (!publishPlatforms.length) return "请至少选择一个发布平台。";
    return "";
  };

  const validateSource = () => {
    if (!selectedProfile) return "请先补齐一个可用的 IP 配方。";
    const executionError = validateExecution();
    if (executionError) return executionError;
    if (sourceMode === "keyword" && creationMode === "manual" && !selectedCandidate) {
      return "请先检索并选择一条严格相关候选。";
    }
    if (sourceMode === "share_link" && !/^https?:\/\//i.test(sourceValue.trim())) return "请输入以 http:// 或 https:// 开头的视频分享链接。";
    if (sourceMode === "script" && !sourceValue.trim()) return "请输入已有文案。";
    return "";
  };

  const executionParams = useMemo(() => ({
    rightsHolder: workspaceConfiguration.rights_holder || "",
    rightsConfirmed: workspaceConfiguration.configured,
    publishPlatforms: publishPlatforms.length ? publishPlatforms : ["douyin"],
    concurrency: 1,
    maxTotalCostCny: null,
    paidActionsConfirmed: false,
    automationMode: sourceMode === "keyword" ? creationMode : "manual" as const,
  }), [creationMode, publishPlatforms, sourceMode, workspaceConfiguration]);

  const preflightAndStart = async (batchId: string) => {
    const checked = await preflightProductionBatch(batchId, executionParams);
    const confirmationOnly = Boolean(
      checked.cost_known
      && (checked.estimated_cost_cny || 0) > 0
      && checked.cost_issues?.length
      && checked.cost_issues.every((issue) => issue.includes("必须确认预计费用")),
    );
    if (confirmationOnly) {
      const estimatedCredits = cnyToCredits(checked.estimated_cost_cny || 0);
      Modal.confirm({
        title: `确认本次最多 ${estimatedCredits} 积分？`,
        content: "这是提交前的冻结上限，包含转写、文案和数字人；数字人成片完成后按实际整秒结算，多余积分会自动退回。本地剪辑不重复收费。",
        okText: "确认费用并开始制作",
        cancelText: "暂不制作",
        onOk: async () => {
          setBusy(true);
          setActionError("");
          try {
            const confirmedParams = { ...executionParams, paidActionsConfirmed: true };
            const confirmed = await preflightProductionBatch(batchId, confirmedParams);
            if (confirmed.blocked_count || confirmed.cost_blocked || confirmed.ready_count === 0) {
              throw new Error(
                confirmed.cost_issues?.join("；")
                || confirmed.items.flatMap((item) => item.reasons || []).join("；")
                || "费用确认后预检仍未通过。",
              );
            }
            const idempotencyKey = getOperationKey("start", { batchId, ...confirmedParams });
            await startProductionBatch(batchId, { ...confirmedParams, idempotencyKey });
            localStorage.setItem(PROFILE_STORAGE_KEY, profileId);
            setActionMessage("费用已确认，任务已启动；系统会自动推进到下一次人工确认。 ");
            await loadWorkspace(batchId);
          } catch (error) {
            setActionError((error as Error).message || "费用确认后启动失败，任务内容已保留。 ");
            throw error;
          } finally {
            setBusy(false);
          }
        },
      });
      return;
    }
    if (checked.blocked_count || checked.cost_blocked || checked.ready_count === 0) {
      const costUnknown = checked.cost_blocked && !checked.cost_known;
      setActionError(
        costUnknown
          ? "暂时无法取得服务报价，系统不会扣费或开始制作。请稍后重新预检。"
          : checked.cost_issues?.join("；")
            || checked.items.flatMap((item) => item.reasons || []).join("；")
            || "预检未通过，请根据提示补齐后重试。",
      );
      await loadWorkspace(batchId);
      return;
    }
    const idempotencyKey = getOperationKey("start", { batchId, ...executionParams });
    await startProductionBatch(batchId, { ...executionParams, idempotencyKey });
    localStorage.setItem(PROFILE_STORAGE_KEY, profileId);
    setActionMessage(
      creationMode === "auto" && sourceMode === "keyword"
        ? `自动创作已启动：将依次处理 ${automaticCandidatePool.length} 条可用口播；AI 最终只会制作 1 条成片。`
        : "任务已启动，将自动推进到下一次人工确认。",
    );
    await loadWorkspace(batchId);
  };

  const createAndStart = async () => {
    // A selected candidate has already been persisted in an existing batch.  After a
    // preflight failure the page reloads without the in-memory candidate, so treating
    // it as a new task would incorrectly ask the customer to search again.
    const resumesExistingBatch = Boolean(
      selectedBatchId
      && workspace
      && (nextAction === "preflight" || workspace.batch.status === "planned"),
    );
    const validation = resumesExistingBatch ? validateExecution() : validateSource();
    if (validation) {
      setActionError(validation);
      return;
    }
    setBusy(true);
    setActionError("");
    setActionMessage("");
    try {
      if (resumesExistingBatch && selectedBatchId) {
        await preflightAndStart(selectedBatchId);
        return;
      }
      const baseCandidate = creationMode === "auto"
        ? automaticCandidatePool[0]
        : selectedCandidate;
      if (
        sourceMode === "keyword"
        && creationMode === "manual"
        && baseCandidate?.platform === "xiaohongshu"
      ) {
        setActionMessage("正在准备原视频，完成后会直接开始转写。");
        await prepareXiaohongshuCandidate(baseCandidate);
        setActionMessage("");
      }
      const item =
        sourceMode === "keyword"
          ? {
              source_type: "candidate" as const,
              source_value: baseCandidate!.video_id,
              display_title: baseCandidate!.title,
            }
          : {
              source_type: sourceMode,
              source_value: sourceValue.trim(),
              display_title:
                sourceMode === "share_link" ? "分享链接"
                : "已有口播文案",
            };
      const items = (
        sourceMode === "keyword" && creationMode === "auto"
          ? automaticCandidatePool.map((candidate) => ({
              source_type: "candidate" as const,
              source_value: candidate.video_id,
              display_title: candidate.title,
              candidate_role: "primary" as const,
            }))
          : [item]
      );
      const payload = {
        name: `${
          sourceMode === "keyword" && creationMode === "auto"
            ? `自动创作 · ${keyword.trim()}`
            : `单条创作 · ${item.display_title || new Date().toLocaleString()}`
        }`.slice(0, 100),
        profile_id: profileId,
        items,
      };
      const created = await createProductionBatch({
        ...payload,
        idempotencyKey: getOperationKey("create", payload),
      });
      setSelectedBatchId(created.batch_id);
      setBatches((current) => [created, ...current.filter((batch) => batch.batch_id !== created.batch_id)]);
      const query = new URLSearchParams({ batch: created.batch_id, run: created.items[0]?.run_id || "" });
      navigate(`/pipeline?${query.toString()}`, { replace: true });
      await preflightAndStart(created.batch_id);
    } catch (error) {
      setActionError((error as Error).message || "创建单条任务失败");
    } finally {
      setBusy(false);
    }
  };

  const submitReview = async (stage: ReviewStage) => {
    if (!workspace || !activeItem) return false;
    if (stage !== "output" && stage !== "publish" && !reviewText.trim()) {
      setActionError(stage === "transcript" ? "转写确认必须提交非空最终文本。" : "文案确认必须提交非空最终口播稿。");
      return false;
    }
    if (stage === "script" && (!creativePlan?.hook.trim() || !creativePlan.call_to_action.trim() || !creativePlan.key_points.some((item) => item.trim()) || !creativePlan.visual_sections.some((item) => item.trim()))) {
      setActionError("请补齐开头、讲解要点、行动引导和至少一个画面段落后再确认。");
      return false;
    }
    if (stage === "publish" && !publishTitle.trim()) {
      setActionError("请先确认一个不含话题和 @ 的发布标题。");
      return false;
    }
    setBusy(true);
    setActionError("");
    try {
      const response = await reviewProductionBatchItems(workspace.batch.batch_id, {
        stage,
        reviewer: workspaceConfiguration.rights_holder || "当前操作人",
        items: [{
          run_id: activeItem.run_id,
          approved_text: stage === "output" || stage === "publish" ? undefined : reviewText.trim(),
          note: reviewNote.trim(),
          creative_plan: stage === "script" && creativePlan ? {
            hook: creativePlan.hook.trim(),
            key_points: creativePlan.key_points.map((item) => item.trim()).filter(Boolean),
            call_to_action: creativePlan.call_to_action.trim(),
            visual_sections: creativePlan.visual_sections.map((item) => item.trim()).filter(Boolean),
          } : undefined,
          publish_draft: stage === "publish" ? {
            title: publishTitle.trim(),
            description: publishDescription.trim(),
            tags: publishTags.split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean),
          } : undefined,
        }],
      });
      const failed = response.results.find((item) => !item.ok);
      if (failed) throw new Error(failed.error || "审核提交失败");
      setActionMessage(
        stage === "transcript" ? "原转写已确认，正在生成待审核改写稿。"
        : stage === "script" ? "创作方案和最终口播稿已确认，后台将继续制作成片。"
        : stage === "publish" && activeItem.publish.task_ids.length
          ? "发布信息已保存；没有启动官方页，也没有执行最终发布。"
        : stage === "publish" ? "发布标题、描述和标签已确认；现在可以准备官方发布页。"
        : "成片复核已记录。",
      );
      await loadWorkspace(workspace.batch.batch_id);
      return true;
    } catch (error) {
      setActionError((error as Error).message || "提交人工确认失败");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const requestAITranscriptReview = () => {
    if (!workspace || !activeItem) return;
    const customerCost = workspaceConfiguration.bundled_compute
      ? 0
      : activeItem.cost.estimated_cost_cny || 0;
    Modal.confirm({
      title: "让 AI 先校对这条转写？",
      content: customerCost > 0
        ? `系统会调用一次 AI 批量校对，已包含在本批次预计费用中；本条预计费用约 ¥${customerCost.toFixed(2)}，不会重新提交阿里云转写。`
        : isAdminSession
          ? "当前是管理员验收账号，本次不扣管理员积分；普通用户会在确认方案时看到并确认实际预计费用。不会重新提交阿里云转写。"
          : "系统会调用一次 AI 批量校对，本次不额外扣积分，也不会重新提交阿里云转写。AI 只修明显错字；金额、人名等无法确定的事实仍会单独标出。",
      okText: "开始 AI 校对",
      cancelText: "暂不校对",
      onOk: async () => {
        setBusy(true);
        setActionError("");
        try {
          const refreshed = await reviewProductionTranscriptWithAI(
            workspace.batch.batch_id,
            activeItem.run_id,
          );
          setWorkspace(refreshed);
          setActionMessage("");
        } catch (error) {
          setActionError((error as Error).message || "AI 校对暂不可用，原转写已保留。");
          throw error;
        } finally {
          setBusy(false);
        }
      },
    });
  };

  const publishTargets = useMemo<ProductionPublishTarget[]>(
    () => publishPlatforms.map((platform) => {
      const ready = accounts.find(
        (account) =>
          account.platform === platform
          && account.status === "ready",
      );
      return {
        platform,
        account_id: ready?.account_id,
        use_manual_fallback: true,
      };
    }),
    [accounts, publishPlatforms],
  );
  const expectedPublishDestinations = useMemo(
    () => publishPlatforms.map((platform) => {
      const capability = platforms.find((item) => item.platform === platform);
      const ready = accounts.find(
        (account) =>
          account.platform === platform
          && account.status === "ready",
      );
      const known = ready || accounts.find((account) => account.platform === platform);
      const canAttemptReal = Boolean(
        ready
        && capability?.enabled
        && !capability.manual_only,
      );
      return {
        platform,
        displayName: capability?.display_name || platform,
        accountName: known?.name || "未绑定就绪账号",
        accountStatus: ready ? "已登录，可准备官方发布页" : known ? `账号状态：${known.status}` : "未配置账号",
        mode: canAttemptReal ? "确认后自动发布" : "生成手动发布包",
      };
    }),
    [accounts, platforms, publishPlatforms],
  );
  const publishDestinations = useMemo(() => {
    const confirmed = activeItem?.publish.targets || [];
    if (!confirmed.length) return expectedPublishDestinations;
    return confirmed.map((target) => {
      const expected = expectedPublishDestinations.find((item) => item.platform === target.platform);
      return {
        platform: target.platform,
        displayName: target.display_name || expected?.displayName || target.platform,
        accountName: target.account_name || expected?.accountName || "未绑定就绪账号",
        accountStatus: target.account_id ? "服务端已核验账号" : expected?.accountStatus || "未配置账号",
        mode:
          target.mode === "real" ? "确认后自动发布"
          : target.mode === "manual" ? "手动发布包"
          : expected?.mode || "由服务端决定",
      };
    });
  }, [activeItem?.publish.targets, expectedPublishDestinations]);

  const publishCurrent = async (data = workspace) => {
    const item =
      data?.items.find((value) => value.run_id === selectedRunId)
      || data?.items.find((value) => value.run_id === data.current_run_id)
      || data?.items[0];
    if (!data || !item) return;
    setBusy(true);
    setActionError("");
    try {
      const checked = await preflightProductionBatchPublish(data.batch.batch_id, {
        runIds: [item.run_id],
        targets: publishTargets,
      });
      const blocked = checked.items.flatMap((value) => value.issues || []);
      if (checked.blocked) {
        setActionError(blocked.join("；") || "发布预检未通过。");
        return;
      }
      const automaticallyPublishablePlatforms = new Set(
        expectedPublishDestinations
          .filter((destination) => destination.mode === "确认后自动发布")
          .map((destination) => destination.platform),
      );
      const confirmedTargets = publishTargets.map((target) => (
        target.account_id && automaticallyPublishablePlatforms.has(target.platform)
          ? { ...target, auto_publish_authorized: true }
          : target
      ));
      const autoAccounts = expectedPublishDestinations
        .filter((destination) => (
          automaticallyPublishablePlatforms.has(destination.platform)
          && destination.accountName !== "未绑定就绪账号"
        ))
        .map((destination) => `${destination.displayName}“${destination.accountName}”`);
      Modal.confirm({
        title: autoAccounts.length ? "确认并发布到所选平台？" : "确认生成发布任务？",
        content: autoAccounts.length
          ? `本次将使用${autoAccounts.join("、")}上传已审核成片，并在各平台最多点击一次最终发布。遇到验证码、页面异常或结果不明确时会立即停止，不会重复点击。`
          : "当前没有可自动发布的已登录账号；系统只会生成手动发布包，不会伪报发布成功。",
        okText: autoAccounts.length ? "确认并自动发布" : "确认生成",
        cancelText: "取消",
        onOk: async () => {
          setBusy(true);
          setActionError("");
          try {
            const idempotencyKey = getOperationKey("publish", {
              batchId: data.batch.batch_id,
              runId: item.run_id,
              targets: confirmedTargets,
            });
            await confirmProductionBatchPublish(data.batch.batch_id, {
              runIds: [item.run_id],
              targets: confirmedTargets,
              idempotencyKey,
            });
            setActionMessage(
              autoAccounts.length
                ? "已开始安全自动发布；只有平台确认成功后才会显示 100%。"
                : "手动发布包已创建，请在发布中心查看真实状态。",
            );
            await loadWorkspace(data.batch.batch_id);
          } catch (error) {
            setActionError((error as Error).message || "发布确认未完成");
            throw error;
          } finally {
            setBusy(false);
          }
        },
      });
    } catch (error) {
      setActionError((error as Error).message || "发布预检失败");
    } finally {
      setBusy(false);
    }
  };

  const finalizeAutomaticOutput = async () => {
    if (!workspace || !activeItem) return;
    if (!publishTitle.trim()) {
      setActionError("请先核对成片和发布标题，再执行最终发布确认。");
      return;
    }
    setBusy(true);
    setActionError("");
    try {
      const reviewer = workspaceConfiguration.rights_holder || "当前操作人";
      const outputResult = await reviewProductionBatchItems(workspace.batch.batch_id, {
        stage: "output",
        reviewer,
        items: [{ run_id: activeItem.run_id, note: "自动创作最终确认" }],
      });
      const outputFailure = outputResult.results.find((item) => !item.ok);
      if (outputFailure) throw new Error(outputFailure.error || "成片确认失败");
      const draftResult = await reviewProductionBatchItems(workspace.batch.batch_id, {
        stage: "publish",
        reviewer,
        items: [{
          run_id: activeItem.run_id,
          note: "与成片一并确认",
          publish_draft: {
            title: publishTitle.trim(),
            description: publishDescription.trim(),
            tags: publishTags.split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean),
          },
        }],
      });
      const draftFailure = draftResult.results.find((item) => !item.ok);
      if (draftFailure) throw new Error(draftFailure.error || "发布信息确认失败");
      const refreshed = await loadWorkspace(workspace.batch.batch_id);
      if (!refreshed) throw new Error("发布前状态刷新失败，请稍后再试。");
      await publishCurrent(refreshed);
    } catch (error) {
      setActionError((error as Error).message || "最终发布确认未完成");
    } finally {
      setBusy(false);
    }
  };

  const prepareExistingPublishPages = () => {
    const taskIds = officialPageTaskIds;
    if (!workspace || !taskIds.length) {
      setActionError("还没有可重新准备的发布任务，请先保存发布信息。");
      return;
    }
    Modal.confirm({
      title: `准备${officialPagePlatformLabel}官方发布页？`,
      content: "系统会逐个平台打开官方创作者页面，上传成片并填入已保存的标题、描述和标签；不会在这一步点击最终发布。",
      okText: "继续准备",
      cancelText: "暂不处理",
      onOk: async () => {
        setBusy(true);
        setActionError("");
        try {
          for (const taskId of taskIds) {
            await preparePublishOfficialPage(taskId);
          }
          setActionMessage(`正在打开${officialPagePlatformLabel}官方创作者页面并填入内容；窗口出现后请检查最终效果。`);
          // Keep the last trustworthy workspace state until the normal 2.5 s
          // refresh observes the worker result. An immediate reload can race
          // with the worker and briefly freeze the page on a stale "失败" state.
        } catch (error) {
          setActionError((error as Error).message || "官方发布页没有准备成功，请检查发布账号后重试。");
          throw error;
        } finally {
          setBusy(false);
        }
      },
    });
  };

  const confirmAutomaticPublish = () => {
    if (!workspace || !preparedPublishTaskIds.length) {
      setActionError("还没有准备好的官方发布页，请先准备官方发布页。");
      return;
    }
    const accountNames = publishDestinations
      .filter((destination) => destination.mode === "确认后自动发布")
      .map((destination) => `${destination.displayName}“${destination.accountName}”`);
    Modal.confirm({
      title: `确认并自动发布到${automaticPublishPlatformLabel}？`,
      content: `本次使用${accountNames.join("、") || "已核验账号"}。系统会等待视频上传完成并在每个平台最多点击一次最终发布；遇到验证码、页面异常或结果不明确时会停止并让你核对。`,
      okText: "确认并自动发布",
      cancelText: "我再检查一下",
      onOk: async () => {
        setBusy(true);
        setActionError("");
        try {
          for (const taskId of preparedPublishTaskIds) {
            await confirmPublishTaskAuto(taskId);
          }
          setActionMessage("已获得本次任务授权，正在等待上传完成并安全发布。");
        } catch (error) {
          setActionError((error as Error).message || "自动发布没有启动，请检查官方页面后重试。");
          throw error;
        } finally {
          setBusy(false);
        }
      },
    });
  };

  const confirmManualPublishCompleted = () => {
    const taskIds = manualPublishTaskIds.length
      ? manualPublishTaskIds
      : preparedPublishTaskIds;
    if (!workspace || !taskIds.length) {
      setActionError("还没有等待人工确认的发布任务，请先生成发布内容。");
      return;
    }
    Modal.confirm({
      title: `确认已经在${activePublishPlatformLabel}发布？`,
      content: `只有你已经在${activePublishPlatformLabel}官方页面点击发布，并确认作品提交成功时才点这里。确认后，这条任务将完成并显示 100%。`,
      okText: "确认已发布",
      cancelText: "还没有",
      onOk: async () => {
        setBusy(true);
        setActionError("");
        try {
          for (const taskId of taskIds) {
            await recordManualPublishResult(taskId, {
              succeeded: true,
              note: `用户在智能创作工作台确认已完成${activePublishPlatformLabel}官方发布。`,
            });
          }
          setActionMessage(`已记录${activePublishPlatformLabel}发布完成，这条任务现在是 100%。`);
          await loadWorkspace(workspace.batch.batch_id);
        } catch (error) {
          setActionError((error as Error).message || "发布完成状态没有保存成功，请保留当前内容后重试。");
          throw error;
        } finally {
          setBusy(false);
        }
      },
    });
  };

  const reviewOutput = async () => {
    await submitReview("output");
  };

  const runControl = async (action: "pause" | "resume" | "retry") => {
    if (!workspace) return;
    setBusy(true);
    setActionError("");
    try {
      if (action === "pause") await pauseProductionBatch(workspace.batch.batch_id);
      else if (action === "resume") await resumeProductionBatch(workspace.batch.batch_id);
      else await retryProductionBatchFailed(workspace.batch.batch_id);
      setActionMessage(action === "pause" ? "任务已暂停。" : action === "resume" ? "任务已继续。" : "已从可证明安全的阶段恢复。");
      await loadWorkspace(workspace.batch.batch_id);
    } catch (error) {
      setActionError((error as Error).message || "任务控制失败");
    } finally {
      setBusy(false);
    }
  };

  const confirmTranscriptionRetry = () => {
    if (!transcriptionUploadRetry) return;
    setTranscriptionRetryConfirmOpen(true);
  };

  const handlePrimaryAction = async () => {
    try {
      if (
        !workspace
        && !completeProfiles.length
        && sourceMode === "keyword"
        && !candidates.length
      ) {
        await runKeywordSearch();
        return;
      }
      if (!workspace && !completeProfiles.length) {
        openProfileCreator();
        return;
      }
      if (!workspace && !workspaceConfiguration.configured) {
        setSetupOpen(true);
        return;
      }
      if (!workspace && !sourceConnectionReady) {
        setSetupOpen(true);
        return;
      }
      if (!workspace) {
        if (sourceMode === "keyword" && !candidates.length) {
          await runKeywordSearch();
          return;
        }
        await createAndStart();
        return;
      }
      if (nextAction === "preflight" || nextAction === "start" || workspace.batch.status === "planned") {
        await createAndStart();
      } else if (nextAction === "review_transcript") {
        await submitReview("transcript");
      } else if (nextAction === "review_script") {
        await submitReview("script");
      } else if (nextAction === "review_output") {
        if (workspace.automation?.mode === "auto") {
          await finalizeAutomaticOutput();
        } else {
          await reviewOutput();
        }
      } else if (nextAction === "review_publish_draft") {
        await submitReview("publish");
      } else if (nextAction === "publish") {
        await publishCurrent();
      } else if (nextAction === "resume") {
        await runControl("resume");
      } else if (nextAction === "reconnect_transcription") {
        if (!workspace || !activeItem) return;
        setBusy(true);
        setActionError("");
        try {
          await reconnectProductionBatchTranscription(
            workspace.batch.batch_id,
            activeItem.run_id,
          );
          setActionMessage("已重新连接原转写任务，未创建第二个任务。");
          await loadWorkspace(workspace.batch.batch_id);
        } catch (error) {
          setActionError((error as Error).message || "重新连接转写任务失败");
        } finally {
          setBusy(false);
        }
      } else if (nextAction === "retry") {
        if (transcriptionUploadRetry) confirmTranscriptionRetry();
        else await runControl("retry");
      } else {
        await loadWorkspace(workspace.batch.batch_id);
      }
    } finally {
      setBusy(false);
    }
  };

  const primaryLabel = useMemo(() => {
    if (!workspace) {
      if (!completeProfiles.length) {
        return sourceMode === "keyword" && !candidates.length
          ? "找素材"
          : "设置人物与声音";
      }
      if (!workspaceConfiguration.configured || !sourceConnectionReady) return "开始创作";
      if (sourceMode === "keyword" && !candidates.length) {
        return "找素材";
      }
      if (sourceMode === "keyword") {
        return creationMode === "auto" ? "自动生成" : "手动挑选";
      }
      return "用这条开始创作";
    }
    const labels: Record<string, string> = {
      preflight: "完成预检并启动",
      start: "启动智能创作",
      review_transcript: "确认转写并生成去重口播稿",
      review_script: "确认方案并制作视频",
      review_output: workspace.automation?.mode === "auto" ? "核对成片并发布" : "确认成片",
      review_publish_draft: "确认发布信息",
      publish: "确认并自动发布",
      resume: "继续任务",
      retry: transcriptionUploadRetry ? "重新上传并识别" : "安全重试",
      reconnect_transcription: "重新连接原转写",
      wait: "刷新实时状态",
      view_result: "查看成片",
      completed: "查看完成结果",
    };
    return labels[nextAction] || "刷新实时状态";
  }, [candidates.length, completeProfiles.length, creationMode, nextAction, sourceConnectionReady, sourceMode, transcriptionUploadRetry, workspace, workspaceConfiguration.configured]);

  const chooseCreationMode = (nextMode: CreationMode) => {
    setCreationMode(nextMode);
    const nextCandidate = nextMode === "auto"
      ? selectAutomaticCandidates(candidates)[0]
      : candidates[0];
    setSelectedCandidateId(nextCandidate?.video_id || "");
    setActionError("");
  };

  const renderPrimaryButton = (inline = false) => (
    <Button
      className={`workspace-primary${inline ? " inline" : ""}`}
      type="primary"
      size="large"
      block={!inline}
      loading={busy}
      disabled={nextAction === "wait" && busy}
      onClick={() => void handlePrimaryAction()}
      icon={primaryLabel === "找素材" ? <SearchOutlined aria-hidden /> : undefined}
    >
      {primaryLabel}
    </Button>
  );

  const showReviewEditor = ["review_transcript", "review_script"].includes(nextAction);
  const needsProfileConfiguration = !workspace && !completeProfiles.length;
  const activeVideo = resultMediaUrl(activeItem);
  const avatarProcessing = activeItem?.processing;
  const avatarIsDelayed = Boolean(avatarProcessing?.stage === "avatar" && avatarProcessing.delayed);
  const progressPercent =
    ["succeeded", "completed"].includes(workspace?.status || "") ? 100
    : workspace ? [10, 35, 60, 80, 95][stageIndex(currentStage)]
    : 0;

  useEffect(() => {
    setVideoLoadError(false);
  }, [activeVideo]);

  if (initializing) {
    return (
      <div className="smart-workspace workspace-loading">
        <section className="workspace-hero">
          <Title level={1}>今天想做什么视频？</Title>
        </section>
        <Card className="workspace-loading-card">
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Text strong>正在带入你的常用设置</Text>
            <Text type="secondary">出镜人、音色和发布设置准备好后，就可以直接开始。</Text>
            <Button type="primary" size="large" block disabled>马上就好</Button>
          </Space>
        </Card>
      </div>
    );
  }

  const renderProfileContent = () => activeProfile ? (
        <div className="profile-summary">
          <div className="profile-summary-details" aria-label="当前 IP 信息">
            <button
              type="button"
              className="profile-setting-row"
              aria-label={`修改出镜人设置：${profileAvatar?.name || activeProfile.name}`}
              onClick={() => setSetupOpen(true)}
            >
              <span className="profile-setting-label"><UserOutlined />出镜人</span>
              <strong>{profileAvatar?.name || activeProfile.name}</strong>
              <RightOutlined aria-hidden />
            </button>
            <button
              type="button"
              className="profile-setting-row"
              aria-label={`修改音色设置：${profileVoice?.name || activeProfile.voice_id}`}
              onClick={() => setSetupOpen(true)}
            >
              <span className="profile-setting-label"><AudioOutlined />音色</span>
              <strong>{profileVoice?.name || activeProfile.voice_id}</strong>
              <RightOutlined aria-hidden />
            </button>
            <button
              type="button"
              className="profile-setting-row"
              aria-label={`修改素材网站设置：${sourcePlatformNames}`}
              onClick={() => setSetupOpen(true)}
            >
              <span className="profile-setting-label"><GlobalOutlined />素材网站</span>
              <strong>{sourcePlatformNames}</strong>
              <RightOutlined aria-hidden />
            </button>
            <button
              type="button"
              className="profile-setting-row"
              aria-label={`修改发布网站设置：${publishPlatforms.map((item) => PLATFORM_LABELS[item] || item).join("、")}`}
              onClick={() => setSetupOpen(true)}
            >
              <span className="profile-setting-label"><RocketOutlined />发布到</span>
              <strong>{publishPlatforms.map((item) => PLATFORM_LABELS[item] || item).join("、")}</strong>
              <RightOutlined aria-hidden />
            </button>
          </div>
          <div className="profile-avatar-preview" aria-label="当前 IP 出镜人预览">
            {profileAvatar?.preview_url ? (
              profileAvatar.preview_type === "video" ? (
                <video
                  src={profileAvatar.preview_url}
                  muted
                  playsInline
                  preload="auto"
                  onLoadedMetadata={(event) => {
                    if (event.currentTarget.currentTime === 0) {
                      event.currentTarget.currentTime = 0.01;
                    }
                  }}
                />
              ) : <img src={profileAvatar.preview_url} alt={`${profileAvatar.name} 形象预览`} />
            ) : <SafetyCertificateOutlined />}
          </div>
        </div>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无完整 IP 配方" />
      );

  const renderProfileCard = () => workspace ? (
    <section className="workspace-profile-card workspace-fold-section">
      <button
        type="button"
        className="workspace-fold-trigger profile-fold-trigger"
        aria-expanded={profileSummaryExpanded}
        onClick={() => setProfileSummaryExpanded((value) => !value)}
      >
        <span className="workspace-fold-title">
          <SettingOutlined aria-hidden />
          <strong>当前 IP 配方</strong>
          <small>
            {activeProfile
              ? `${profileAvatar?.name || activeProfile.name} · ${profileVoice?.name || activeProfile.voice_id}`
              : "尚未配置"}
          </small>
        </span>
        <span className="workspace-fold-action">
          {profileSummaryExpanded ? "收起" : "展开"}
          {profileSummaryExpanded ? <UpOutlined aria-hidden /> : <DownOutlined aria-hidden />}
        </span>
      </button>
      {profileSummaryExpanded && (
        <div className="workspace-fold-content profile-fold-content">{renderProfileContent()}</div>
      )}
    </section>
  ) : (
    <Card
      className="workspace-profile-card"
      title={<Space><SettingOutlined /> 默认创作设置</Space>}
      extra={activeProfile ? (
        <Button type="link" icon={<SettingOutlined />} onClick={() => setSetupOpen(true)}>
          修改设置
        </Button>
      ) : null}
    >
      {renderProfileContent()}
    </Card>
  );

  const renderWorkbenchTaskList = () => (
    <>
      {unifiedWorkbenchBatches.length ? (
        <List
          className="workbench-task-list"
          size="small"
          dataSource={unifiedWorkbenchBatches}
          renderItem={({ type, batch }) => {
            if (type === "pending") {
              const pending = batch as ProductionBatch;
              return (
                <List.Item
                  className={`workbench-task-item${pending.batch_id === selectedBatchId ? " active-batch" : ""}`}
                  style={{ minHeight: 76 }}
                  actions={[
                    <Button
                      key="open"
                      type="link"
                      onClick={() => {
                        const run = pending.items[0]?.run_id || "";
                        setSelectedRunId(run);
                        navigate(`/pipeline?batch=${encodeURIComponent(pending.batch_id)}&run=${encodeURIComponent(run)}`);
                        void loadWorkspace(pending.batch_id);
                      }}
                      aria-label={`${workbenchTaskAction(pending.status)}：${productionTaskTitle(pending.name)}`}
                    >
                      {workbenchTaskAction(pending.status)}
                    </Button>,
                  ]}
                >
                  <List.Item.Meta
                    title={(
                      <span className="workbench-task-title" title={productionTaskTitle(pending.name)}>
                        {productionTaskTitle(pending.name)}
                      </span>
                    )}
                    description={`${pending.profile_name} · ${formatWorkbenchTime(pending.created_at)}`}
                  />
                  <Tag color={statusColor(pending.status)}>{STATUS_LABEL[pending.status] || pending.status}</Tag>
                </List.Item>
              );
            }
            const recent = batch as CrawlerBatchResponse;
            return (
              <List.Item
                className="recent-material-search-item"
                style={{ minHeight: 76 }}
                actions={[
                  <Button
                    key="resume-material-search"
                    type="link"
                    onClick={() => {
                      void restoreMaterialSearchBatch(recent.batch_id, "", true).catch((error) => {
                        setActionError((error as Error).message || "读取已保存的素材失败");
                      });
                    }}
                    aria-label={`继续挑选：${recent.keyword}`}
                  >
                    继续挑选
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={<span className="workbench-task-title">{recent.keyword}</span>}
                  description={`${recent.total_candidates} 条可选素材 · ${formatWorkbenchTime(recent.created_at)}`}
                />
                <Tag color="green">已保留</Tag>
              </List.Item>
            );
          }}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无需要处理的任务" />
      )}
      {!workspace ? (
        <div className="workbench-task-footer start">
          <Link to="/production">查看全部任务（{batches.length}） <RightOutlined aria-hidden /></Link>
        </div>
      ) : pendingWorkbenchBatches.length > WORKBENCH_TASK_LIMIT ? (
        <div className="workbench-task-footer">
          <Text type="secondary">
            还有 {pendingWorkbenchBatches.length - WORKBENCH_TASK_LIMIT} 条需要处理
          </Text>
          <Link to="/production">去任务队列</Link>
        </div>
      ) : null}
    </>
  );

  const renderWorkbenchTasksCard = () => workspace ? (
    <section className="workspace-tasks-card workspace-fold-section">
      <button
        type="button"
        className="workspace-fold-trigger tasks-fold-trigger"
        aria-expanded={tasksExpanded}
        onClick={() => setTasksExpanded((value) => !value)}
      >
        <span className="workspace-fold-title">
          <ControlOutlined aria-hidden />
          <strong>待处理任务 {pendingWorkbenchBatches.length}</strong>
          <small>仅显示需要你处理的任务</small>
        </span>
        <span className="workspace-fold-action">
          {tasksExpanded ? "收起" : "展开"}
          {tasksExpanded ? <UpOutlined aria-hidden /> : <DownOutlined aria-hidden />}
        </span>
      </button>
      {tasksExpanded && <div className="workspace-fold-content tasks-fold-content">{renderWorkbenchTaskList()}</div>}
    </section>
  ) : (
    <Card className="workspace-tasks-card" title="继续上次任务">
      {renderWorkbenchTaskList()}
    </Card>
  );

  return (
    <div className="smart-workspace">
      <section className="workspace-hero">
        <div>
          <Title level={1}>{workspace ? "智能创作" : "今天想做什么视频？"}</Title>
          {workspace && <Paragraph>已完成内容自动收起，点击可查看。</Paragraph>}
        </div>
      </section>

      {workspace && (
        <section className="workspace-stage-strip" aria-label="智能创作三步流程">
          {WORKSPACE_STEPS.map((step, index) => {
            const isComplete = index < currentBusinessStage;
            const isCurrent = index === currentBusinessStage;
            const isExpanded = expandedWorkspaceStage === index;
            return (
              <button
                type="button"
                key={step.title}
                className={`workspace-stage-step${isComplete ? " complete" : ""}${isCurrent ? " current" : ""}`}
                aria-expanded={isExpanded}
                onClick={() => setExpandedWorkspaceStage((value) => value === index ? null : index)}
              >
                <span className="workspace-stage-number">
                  {isComplete ? <CheckCircleOutlined aria-hidden /> : index + 1}
                </span>
                <span className="workspace-stage-copy">
                  <strong>{step.title}</strong>
                  {isExpanded && <small>{step.description}</small>}
                </span>
                {isExpanded ? <UpOutlined aria-hidden /> : <DownOutlined aria-hidden />}
              </button>
            );
          })}
        </section>
      )}

      {loadError && <Alert type="error" showIcon message="工作台载入失败" description={loadError} />}
      {actionError && (
        <Alert
          type="error"
          showIcon
          closable
          onClose={() => setActionError("")}
          message="当前操作未完成"
          description={actionError}
          action={actionError.includes("积分不足") ? (
            <Button type="primary" size="small" onClick={() => navigate("/admin")}>
              去充值
            </Button>
          ) : undefined}
        />
      )}
      {actionMessage && <Alert type="success" showIcon closable onClose={() => setActionMessage("")} message={actionMessage} />}

      <Modal
        title="确认重新提交云端转写？"
        open={transcriptionRetryConfirmOpen}
        okText="重新上传并识别"
        cancelText="暂不付费"
        confirmLoading={busy}
        onCancel={() => setTranscriptionRetryConfirmOpen(false)}
        onOk={async () => {
          await runControl("retry");
          setTranscriptionRetryConfirmOpen(false);
        }}
      >
        <Space direction="vertical" size="small">
          <Text>上次失败发生在素材上传阶段，系统没有拿到阿里云识别任务号；素材已经保留。</Text>
          <Text>
            再次提交可能产生一笔新的阿里云转写费用，预计
            <Text strong>
              {" "}¥{transcriptionUploadRetry?.estimated_cost_cny == null
                ? "暂无法确定"
                : transcriptionUploadRetry.estimated_cost_cny.toFixed(4)}
            </Text>
            ；本任务最多只允许重试一次。
          </Text>
        </Space>
      </Modal>

      <div className={`workspace-grid${workspace ? " has-workspace" : " is-start"}`}>
        <div className="workspace-left">
          <Card
            className={`workspace-action-card${workspace?.status === "outcome_unknown" ? " outcome-focus-card" : ""}`}
            title={workspace && workspace.status !== "outcome_unknown" ? (
              <span className="workspace-action-heading">
                <small>当前需要你处理</small>
                <strong>{currentActionHeading}</strong>
              </span>
            ) : undefined}
            extra={workspace && workspace.status !== "outcome_unknown"
              ? <Tag color={statusColor(workspace.status)}>{STATUS_LABEL[workspace.status] || workspace.status}</Tag>
              : undefined}
          >
            {!workspace && (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <div className="creation-composer">
                  <Segmented
                    className="creation-source-tabs"
                    block
                    value={sourceMode}
                    disabled={Boolean(materialSearchProgress)}
                    options={SOURCE_OPTIONS}
                    onChange={(value) => {
                      setSourceMode(value as SourceMode);
                      setActionError("");
                      setCandidates([]);
                      setSelectedCandidateId("");
                      setCrawlerReason(null);
                    }}
                  />
                  {sourceMode === "keyword" ? (
                    <>
                      <div className="creation-entry-row">
                        <Input
                          size="large"
                          value={keyword}
                          disabled={Boolean(materialSearchProgress)}
                          placeholder="例如：餐饮老板获客、汽修店避坑"
                          onChange={(event) => {
                            setKeyword(event.target.value);
                            setCandidates([]);
                            setSelectedCandidateId("");
                            setCrawlerReason(null);
                            setFailedSearchPlatforms([]);
                          }}
                          onPressEnter={() => void handlePrimaryAction()}
                        />
                        <Popover
                          trigger="click"
                          placement="bottomLeft"
                          content={(
                            <Space direction="vertical" size="small" style={{ minWidth: 220 }}>
                              <Text strong>筛选素材</Text>
                              <label>
                                <Text type="secondary">发布时间</Text>
                                <Select<PublishedWindowDays>
                                  aria-label="发布时间筛选"
                                  style={{ width: "100%", marginTop: 4 }}
                                  value={publishedWindowDays}
                                  onChange={(value) => {
                                    setPublishedWindowDays(value);
                                    setCandidates([]);
                                    setSelectedCandidateId("");
                                    setCrawlerReason(null);
                                  }}
                                  options={PUBLISHED_WINDOW_OPTIONS}
                                />
                              </label>
                              <label>
                                <Text type="secondary">每个平台最多找</Text>
                                <Select<MaterialCount>
                                  aria-label="每平台素材数量"
                                  style={{ width: "100%", marginTop: 4 }}
                                  value={countPerPlatform}
                                  onChange={(value) => {
                                    setCountPerPlatform(value);
                                    setCandidates([]);
                                    setSelectedCandidateId("");
                                    setCrawlerReason(null);
                                  }}
                                  options={[
                                    { value: 30, label: "30 条" },
                                    { value: 50, label: "50 条" },
                                    { value: 100, label: "100 条" },
                                  ]}
                                />
                              </label>
                              <Text type="secondary">自动创作会处理本次筛选到的全部可用口播。</Text>
                            </Space>
                          )}
                        >
                          <Button
                            className="creation-filter-button"
                            aria-label="筛选"
                            title="筛选素材"
                            icon={<FilterOutlined aria-hidden />}
                          />
                        </Popover>
                        {!candidates.length && renderPrimaryButton(true)}
                      </div>
                      {hotWords.length > 0 && (
                        <Space wrap size={[4, 4]}>
                          <Text type="secondary">实时热点词：</Text>
                          {hotWords.map((item) => (
                            <Button
                              key={item.word}
                              type="link"
                              size="small"
                              onClick={() => {
                                setKeyword(item.word);
                                setCandidates([]);
                                setSelectedCandidateId("");
                                setCrawlerReason(null);
                              }}
                            >
                              {item.word}
                            </Button>
                          ))}
                        </Space>
                      )}
                    </>
                  ) : sourceMode === "share_link" ? (
                    <div className="creation-entry-row">
                      <Input
                        size="large"
                        value={sourceValue}
                        placeholder="粘贴已获授权的视频分享链接"
                        onChange={(event) => setSourceValue(event.target.value)}
                      />
                      {renderPrimaryButton(true)}
                    </div>
                  ) : (
                    <TextArea
                      rows={8}
                      value={sourceValue}
                      placeholder="粘贴已经确认的口播文案"
                      onChange={(event) => setSourceValue(event.target.value)}
                    />
                  )}
                </div>

                {sourceMode === "keyword" && (
                  <div className="creation-mode-section">
                    <div className="creation-mode-cards" role="radiogroup" aria-label="创作方式">
                      <button
                        type="button"
                        disabled={Boolean(materialSearchProgress)}
                        role="radio"
                        aria-checked={creationMode === "manual"}
                        aria-label="手动挑选"
                        className={`creation-mode-card${creationMode === "manual" ? " selected" : ""}`}
                        onClick={() => chooseCreationMode("manual")}
                      >
                        <span className="creation-mode-icon"><ControlOutlined aria-hidden /></span>
                        <span className="creation-mode-copy" aria-hidden>
                          <span className="creation-mode-title"><strong>手动挑选</strong><Tag color="purple">推荐</Tag></span>
                          <small>自己挑选素材，创作方向更可控</small>
                        </span>
                        <CheckCircleOutlined className="creation-mode-check" aria-hidden />
                      </button>
                      <button
                        type="button"
                        disabled={Boolean(materialSearchProgress)}
                        role="radio"
                        aria-checked={creationMode === "auto"}
                        aria-label="自动生成"
                        className={`creation-mode-card${creationMode === "auto" ? " selected risk" : " risk"}`}
                        onClick={() => chooseCreationMode("auto")}
                      >
                        <span className="creation-mode-icon"><StarOutlined aria-hidden /></span>
                        <span className="creation-mode-copy" aria-hidden>
                          <span className="creation-mode-title"><strong>自动生成</strong><Tag color="warning">有风险</Tag></span>
                          <small>可能选偏素材，文案与成片需要复核</small>
                        </span>
                        <CheckCircleOutlined className="creation-mode-check" aria-hidden />
                      </button>
                    </div>
                    {creationMode === "auto" ? (
                      <div className="creation-mode-risk-note" role="note">
                        <WarningOutlined aria-hidden />
                        <span>自动创作可能选偏素材或需要调整文案；费用确认、成片复核和最终发布仍由你决定。</span>
                      </div>
                    ) : (
                      <Paragraph type="secondary" className="creation-mode-note">
                        快手不支持发布时间筛选，会按不限时间搜索；其他平台按所选时间范围筛选。
                      </Paragraph>
                    )}
                  </div>
                )}

                {sourceMode === "keyword" && materialSearchProgress && (
                  <MaterialSearchExperience
                    compact
                    keyword={keyword.trim()}
                    platforms={materialSearchProgress.platforms}
                    startedAt={materialSearchProgress.startedAt}
                    batch={materialSearchBatch}
                    complete={materialSearchComplete}
                    progress={materialSearchQueue?.items[0]}
                    targetCount={countPerPlatform}
                    onRevealComplete={finishMaterialSearchReveal}
                  />
                )}

                {crawlerReason && (
                  crawlerReason.kind !== "结果正在整理"
                  || (candidates.length === 0 && automaticCandidatePool.length === 0)
                ) && (
                  <div className="search-empty">
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={crawlerReason.message} />
                    <Button
                      onClick={() => void runKeywordSearch(
                        true,
                        retrySource && isBrowserPlatform(retrySource.platform) ? [retrySource.platform] : undefined,
                      )}
                      loading={busy}
                    >
                      {crawlerReason.kind === "检索没有开始"
                        ? "重新提交"
                        : retrySource
                          ? `切换到${retrySource.platform_label || PLATFORM_LABELS[retrySource.platform || ""] || "其他平台"}重试`
                          : "重新搜索"}
                    </Button>
                  </div>
                )}

                {candidates.length > 0 && (
                  <div>
                    <div className="section-heading">
                      <Text strong>
                        {creationMode === "auto"
                          ? `自动创作素材（${automaticCandidatePool.length}）`
                          : `本次找到的全部素材（${candidates.length}）`}
                      </Text>
                    </div>
                    {materialSearchBatch?.platform_runs?.length ? (
                      <div className="candidate-platform-summary" aria-label="各平台素材数量">
                        {materialSearchBatch.platform_runs.map((run) => (
                          <span key={run.run_id}>
                            {platformRunSummary(run, materialSearchBatch.count_per_platform)}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    <div className="candidate-stack">
                      {(() => {
                        const allCandidates = creationMode === "auto" ? automaticCandidatePool : candidates;
                        const startIndex = (candidatePage - 1) * candidatePageSize;
                        const endIndex = startIndex + candidatePageSize;
                        const currentPageCandidates = allCandidates.slice(startIndex, endIndex);
                        return currentPageCandidates.map((candidate, localIndex) => {
                          const globalIndex = startIndex + localIndex;
                          return (
                        <div
                          key={candidate.video_id}
                          className={`candidate-card${creationMode === "manual" ? " manual" : ""}${selectedCandidateId === candidate.video_id ? " selected" : ""}`}
                        >
                          <button
                            type="button"
                            className={`candidate-select${selectedCandidateId === candidate.video_id ? " selected" : ""}`}
                            onClick={() => {
                              if (creationMode === "manual") {
                                setSelectedCandidateId(candidate.video_id);
                                setActionError("");
                                void prepareXiaohongshuCandidate(candidate).catch((error) => {
                                  setActionError((error as Error).message || "原视频准备失败，请确认素材浏览器仍处于登录状态。");
                                });
                              }
                            }}
                          >
                            <span className="candidate-rank">#{globalIndex + 1}</span>
                            <span className="candidate-copy">
                              <strong>{candidate.title || "未命名候选"}</strong>
                              <small>{candidate.platform_label} · {candidate.author_name || "作者未返回"}</small>
                              {candidate.selection_tier === "reserve" ? (
                                <small>
                                  {candidate.relevance_basis === "related_concept" ? "相关候补 · 请先确认" : "低热度候补"}
                                </small>
                              ) : null}
                              <small className="candidate-metrics">
                                点赞 {formatCandidateMetric(candidate.likes)} · 评论 {formatCandidateMetric(candidate.comments)} · 分享 {formatCandidateMetric(candidate.shares)} · 收藏 {formatCandidateMetric(candidate.favorites)}
                              </small>
                              {preparingCandidateId === candidate.video_id ? (
                                <small>正在准备原视频，完成后可直接查看和转写。</small>
                              ) : preparedCandidateId === candidate.video_id ? (
                                <small>原视频已准备，可直接查看和转写。</small>
                              ) : null}
                            </span>
                            {creationMode === "auto" || selectedCandidateId === candidate.video_id
                              ? <CheckCircleOutlined />
                              : null}
                          </button>
                          {creationMode === "manual" && (
                            candidate.source_url || candidate.platform === "xiaohongshu" ? (
                              candidate.platform === "xiaohongshu" ? (
                                <button
                                  type="button"
                                  className="candidate-source-link"
                                  aria-label={`查看「${candidate.title || "未命名候选"}」原视频`}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    void prepareXiaohongshuCandidate(candidate)
                                      .then(() => openPipelineCandidateOriginalMedia(candidate))
                                      .catch((error) => {
                                        setActionError((error as Error).message || "原视频暂时无法打开，请稍后重试。");
                                      });
                                  }}
                                >
                                  <VideoCameraOutlined /> 原视频
                                </button>
                              ) : (
                              <a
                                className="candidate-source-link"
                                href={candidate.source_url || undefined}
                                target="_blank"
                                rel="noreferrer"
                                aria-label={`查看「${candidate.title || "未命名候选"}」原视频`}
                                onClick={(event) => event.stopPropagation()}
                              >
                                <VideoCameraOutlined /> 原视频
                              </a>
                              )
                            ) : (
                              <span className="candidate-source-unavailable">暂无原视频</span>
                            )
                          )}
                        </div>
                      );
                    });
                  })()}

                      {(creationMode === "auto" ? automaticCandidatePool : candidates).length > candidatePageSize && (
                        <div className="candidate-stack-pagination">
                          <Pagination
                            current={candidatePage}
                            pageSize={candidatePageSize}
                            total={(creationMode === "auto" ? automaticCandidatePool : candidates).length}
                            onChange={(page) => setCandidatePage(page)}
                            size="small"
                            showSizeChanger={false}
                            showQuickJumper={false}
                          />
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </Space>
            )}

            {workspace && (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                {workspace.status === "outcome_unknown" && (
                  <div className="outcome-focus-intro">
                    <span className="outcome-focus-icon"><WarningOutlined aria-hidden /></span>
                    <Title level={2}>{currentActionHeading}</Title>
                    <Tag color={statusColor(workspace.status)}>{STATUS_LABEL[workspace.status] || workspace.status}</Tag>
                  </div>
                )}
                {workspace.automation?.mode === "auto" && (
                  <Alert
                    type={workspace.automation.review_state === "failed" ? "warning" : "info"}
                    showIcon
                    message={
                      workspace.automation.review_state === "completed"
                        ? "AI 已完成可用口播择优"
                        : workspace.automation.review_state === "failed"
                          ? "自动选稿已停止，可改为人工确认"
                          : (workspace.automation.reserve_activated_count || 0) > 0
                            ? `正在补转写 ${workspace.automation.reserve_activated_count} 条候补`
                          : "自动创作正在进行"
                    }
                    description={
                      workspace.automation.error
                      || workspace.automation.selection_reason
                      || `系统先处理 ${workspace.automation.target_count || "本批"} 条素材；纯画面只作参考，有效内容会改成自然口播，最终只制作 1 条成片。`
                    }
                  />
                )}
                <Alert
                  type={activeItem?.error_message || activeItem?.blocked_reasons?.length ? "warning" : "info"}
                  showIcon
                  message={
                    publishCompleted
                      ? `已完成${activePublishPlatformLabel}发布`
                      : publishPagePrepared
                        ? `${automaticPublishPlatformLabel}发布页已准备`
                        : manualPublishTaskIds.length
                          ? `${activePublishPlatformLabel}发布内容已准备`
                          : (STAGE_LABEL[currentStage] || currentStage)
                  }
                  description={
                    activeItem?.error_message
                    || activeItem?.blocked_reasons?.join("；")
                    || (publishCompleted ? "这条任务已完成，进度 100%。" : "")
                    || (publishPagePrepared ? "检查内容后，可让系统安全点击一次最终发布，也可以由你手动完成。" : "")
                    || (nextAction === "wait" ? "系统正在处理，页面每 2.5 秒自动刷新。" : `下一步：${primaryLabel}`)
                  }
                />

                {avatarIsDelayed && (
                  <Alert
                    type="warning"
                    showIcon
                    message={`数字人处理偏慢，已等待 ${formatElapsedSeconds(avatarProcessing?.elapsed_seconds)}`}
                    description="系统会继续查询这一次生成，不会重复提交。电脑关机期间，开机后才会继续下载和剪辑。"
                  />
                )}

                {showReviewEditor && (
                  <>
                    {nextAction === "review_transcript" && (
                      <>
                        <Space wrap>
                          <Tag color={activeReview?.transcript.auto_reviewed ? "success" : "warning"}>
                            {activeReview?.transcript.auto_reviewed
                              ? `AI 已校对 ${activeReview?.transcript.ai_corrected_count || 0} 处`
                              : "AI 尚未校对"}
                          </Tag>
                          <Tag color={(activeReview?.transcript.uncertain_segment_count || 0) > 0 ? "warning" : "success"}>
                            仍需确认 {activeReview?.transcript.uncertain_segment_count || 0} 处
                          </Tag>
                        </Space>
                        <Alert
                          type={activeReview?.transcript.auto_reviewed ? "success" : "warning"}
                          showIcon
                          message={activeReview?.transcript.auto_reviewed ? "AI 已先校对转写" : "这条转写还没经过 AI 校对"}
                          description={
                            activeReview?.transcript.auto_reviewed
                              ? (activeReview?.transcript.uncertain_segment_count || 0) > 0
                                ? "明显错字和断句已由 AI 处理；下面只保留金额、人名或原话无法唯一确定的部分，请确认这些事实。"
                                : "明显错字和断句已由 AI 处理，没有发现必须由你逐字修正的片段；快速确认全文即可继续。"
                              : "这是升级前创建的任务。先让 AI 批量校对，不需要你对着乱码逐字修改。"
                          }
                          action={!activeReview?.transcript.auto_reviewed ? (
                            <Button type="primary" loading={busy} onClick={requestAITranscriptReview}>
                              让 AI 先校对
                            </Button>
                          ) : undefined}
                        />
                        {(activeReview?.transcript.low_confidence_segments?.length || 0) > 0 && (
                          <List
                            size="small"
                            bordered
                            header="AI 仍无法确定的事实片段"
                            dataSource={activeReview?.transcript.low_confidence_segments || []}
                            renderItem={(segment) => (
                              <List.Item>
                                <Space direction="vertical" size={0}>
                                  <Text>
                                    {formatSegmentTime(segment.start)}–{formatSegmentTime(segment.end)}　{segment.text || "未返回片段文本"}
                                  </Text>
                                  <Text type="secondary">
                                    {segment.confidence === null
                                      ? "置信度未返回"
                                      : `置信度 ${Math.round(segment.confidence * 100)}%`}
                                    {segment.quality_note ? ` · ${segment.quality_note}` : ""}
                                  </Text>
                                </Space>
                              </List.Item>
                            )}
                          />
                        )}
                      </>
                    )}
                    {nextAction === "review_script" && activeReview?.script.compliance_status && (
                      <>
                        <Alert
                          type={activeReview.script.compliance_status === "passed" ? "success" : "warning"}
                          showIcon
                          message={`风险检查：${activeReview.script.compliance_status}`}
                          description={activeReview.script.compliance_notes?.join("；") || "请人工核对事实、承诺和平台规则。"}
                        />
                        {(activeReview.script.attention_terms?.length || 0) > 0 && (
                          <Space wrap>
                            <Text type="secondary">重点核对：</Text>
                            {activeReview.script.attention_terms?.map((term) => <Tag color="warning" key={term}>{term}</Tag>)}
                          </Space>
                        )}
                      </>
                    )}
                    {nextAction === "review_script" && activeReview?.script.ai_audit && (
                      <Alert
                        type={activeReview.script.ai_audit.status === "completed" && activeReview.script.ai_audit.approved ? "success" : "warning"}
                        showIcon
                        message={
                          activeReview.script.ai_audit.status === "mock"
                            ? "AI 文案审核：演示结果"
                            : activeReview.script.ai_audit.status === "unavailable"
                              ? "AI 文案审核未完成"
                              : activeReview.script.ai_audit.approved
                                ? "AI 文案审核通过"
                                : "AI 文案审核提示需核对"
                        }
                        description={
                          <Space direction="vertical" size={2}>
                            <span>{activeReview.script.ai_audit.summary}</span>
                            {(activeReview.script.ai_audit.issues || []).map((issue, index) => (
                              <span key={`${issue.category}-${index}`}>
                                {issue.severity === "block" ? "需修改" : "建议"} · {issue.category}：{issue.message}
                              </span>
                            ))}
                          </Space>
                        }
                      />
                    )}
                    <div className="review-primary-editor">
                      <div className="review-editor-heading">
                        <Text strong>{nextAction === "review_transcript" ? "AI 校对后的转写" : "最终口播稿"}</Text>
                        <Text type="secondary">
                          {nextAction === "review_transcript"
                            ? "不用逐字修正，只需确认事实没有被改动"
                            : "只需确认这一份，系统会自动带入后续制作"}
                        </Text>
                      </div>
                      <TextArea
                        className="primary-review-textarea"
                        aria-label={nextAction === "review_transcript" ? "AI 校对后的转写" : "最终口播稿"}
                        rows={6}
                        value={reviewText}
                        onChange={(event) => setReviewText(event.target.value)}
                        placeholder={nextAction === "review_transcript" ? "AI 会先处理明显错字；这里只需确认金额、人名和原意" : "核对并提交最终口播稿"}
                      />
                    </div>
                    {nextAction === "review_script" && creativePlan ? (
                      <details
                        className="creative-plan-details"
                        open={creativePlanExpanded}
                        onToggle={(event) => setCreativePlanExpanded(event.currentTarget.open)}
                      >
                        <summary>
                          <span>
                            <strong>查看创作拆解</strong>
                            <small>开头、讲解要点、行动引导和画面段落</small>
                          </span>
                          <span>可选修改</span>
                        </summary>
                        <div className="creative-plan-fields">
                          <Text type="secondary">系统已根据最终口播稿自动整理；不修改也可以直接确认。</Text>
                          <Input
                            aria-label="开头吸引点"
                            value={creativePlan.hook}
                            placeholder="用一句话说清客户为什么要继续看"
                            maxLength={160}
                            onChange={(event) => setCreativePlan((current) => current ? { ...current, hook: event.target.value } : current)}
                          />
                          <TextArea
                            aria-label="讲解要点"
                            rows={3}
                            value={creativePlan.key_points.join("\n")}
                            placeholder="每行一个讲解要点"
                            maxLength={900}
                            onChange={(event) => setCreativePlan((current) => current ? {
                              ...current,
                              key_points: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean).slice(0, 5),
                            } : current)}
                          />
                          <Input
                            aria-label="行动引导"
                            value={creativePlan.call_to_action}
                            placeholder="告诉客户下一步该做什么"
                            maxLength={160}
                            onChange={(event) => setCreativePlan((current) => current ? { ...current, call_to_action: event.target.value } : current)}
                          />
                          <TextArea
                            aria-label="三个画面段落"
                            rows={3}
                            value={creativePlan.visual_sections.join("\n")}
                            placeholder="每行一个画面段落，例如：开场人物口播、产品或案例、收尾行动引导"
                            maxLength={540}
                            onChange={(event) => setCreativePlan((current) => current ? {
                              ...current,
                              visual_sections: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean).slice(0, 3),
                            } : current)}
                          />
                          <Input
                            value={reviewNote}
                            onChange={(event) => setReviewNote(event.target.value)}
                            placeholder="补充备注（可选）"
                          />
                        </div>
                      </details>
                    ) : (
                      <Input
                        value={reviewNote}
                        onChange={(event) => setReviewNote(event.target.value)}
                        placeholder="复核备注（可选）"
                      />
                    )}
                  </>
                )}

                {stageIndex(currentStage) === 4 && (
                  <div className="publish-review">
                    <Text strong>发布信息与账号状态</Text>
                    <Alert
                      type="warning"
                      showIcon
                      message="请遵守平台规则并使用已授权内容"
                      description="平台审核、限流或封禁风险无法由软件消除；请在发布前人工核对内容和账号操作。"
                    />
                    {activeItem?.publish.draft && !["succeeded", "outcome_unknown"].includes(activeItem.publish.status) ? (
                      <Space direction="vertical" size={10} style={{ width: "100%" }}>
                        {publishPagePrepared && (
                          <>
                            <Alert
                              type="success"
                              showIcon
                              message={`${automaticPublishPlatformLabel}发布页已准备`}
                              description={`请检查任务栏中各平台的官方创作者窗口。${activeItem.publish.action_required || "确认标题、封面和可见范围后，可以让系统安全完成最终发布。"}`}
                            />
                            <Space wrap>
                              <Button type="primary" icon={<RocketOutlined />} loading={busy} onClick={confirmAutomaticPublish}>
                                确认并自动发布
                              </Button>
                              <Button icon={<CheckCircleOutlined />} loading={busy} onClick={confirmManualPublishCompleted}>
                                我已手动发布
                              </Button>
                            </Space>
                          </>
                        )}
                        {!publishPagePrepared && manualPublishTaskIds.length > 0 && (
                          <Alert
                            type="info"
                            showIcon
                            message={`${activePublishPlatformLabel}发布内容已准备`}
                            description="该平台当前需要手动完成最终发布。请在发布中心下载或复制已准备内容，确认官方平台发布成功后再回来登记结果。"
                            action={(
                              <Space wrap>
                                <Button onClick={() => navigate("/publish")}>前往发布中心</Button>
                                <Button type="primary" onClick={confirmManualPublishCompleted}>我已手动发布</Button>
                              </Space>
                            )}
                          />
                        )}
                        <Text type="secondary">系统已根据最终口播稿生成标题、描述和标签；不会继承原视频的人物或话题，请你审核后再保存。</Text>
                        <Input aria-label="发布标题" value={publishTitle} maxLength={30} showCount placeholder="30 字内，不写 # 或 @" onChange={(event) => setPublishTitle(event.target.value)} />
                        <TextArea aria-label="发布描述" rows={6} value={publishDescription} maxLength={1000} showCount placeholder="写给观众看的作品描述" onChange={(event) => setPublishDescription(event.target.value)} />
                        <Input aria-label="发布标签" value={publishTags} placeholder="用顿号或逗号分隔；不填也可以" onChange={(event) => setPublishTags(event.target.value)} />
                        {nextAction !== "review_publish_draft"
                          && !(workspace.automation?.mode === "auto" && nextAction === "review_output")
                          && (
                          <Space wrap>
                            <Button loading={busy} onClick={() => void submitReview("publish")}>
                              保存发布信息
                            </Button>
                            {activeItem.publish.task_ids.length > 0
                              && !publishPagePrepared
                              && manualPublishTaskIds.length === 0
                              && officialPageTaskIds.length > 0
                              && (
                              <Button type="primary" icon={<RocketOutlined />} loading={busy} onClick={prepareExistingPublishPages}>
                                准备官方发布页
                              </Button>
                            )}
                          </Space>
                        )}
                      </Space>
                    ) : (
                      <Descriptions size="small" column={1} bordered>
                        <Descriptions.Item label="标题">
                          {activeItem?.publish.draft?.title || "服务端尚未生成标题"}
                        </Descriptions.Item>
                        <Descriptions.Item label="描述">
                          {activeItem?.publish.draft?.description || "服务端尚未生成描述"}
                        </Descriptions.Item>
                        <Descriptions.Item label="标签">
                          {(activeItem?.publish.draft?.tags || []).length
                            ? activeItem?.publish.draft?.tags.map((tag) => <Tag key={tag}>{tag}</Tag>)
                            : "暂无标签"}
                        </Descriptions.Item>
                      </Descriptions>
                    )}
                    <List
                      size="small"
                      dataSource={publishDestinations}
                      locale={{ emptyText: "尚未选择发布平台" }}
                      renderItem={(destination) => (
                        <List.Item extra={<Tag color={destination.mode.includes("真实") ? "processing" : "default"}>{destination.mode}</Tag>}>
                          <List.Item.Meta
                            title={destination.displayName}
                            description={`${destination.accountName} · ${destination.accountStatus}`}
                          />
                        </List.Item>
                      )}
                    />
                  </div>
                )}

                <Progress
                  percent={Math.max(0, Math.min(100, Math.round(progressPercent)))}
                  showInfo={!avatarIsDelayed}
                  status={workspace.status === "failed" ? "exception" : "active"}
                />
                <Space wrap>
                  {allowedActions.includes("pause") && !avatarProcessing?.provider_job_received && (
                    <Button icon={<PauseCircleOutlined />} disabled={busy} onClick={() => void runControl("pause")}>暂停</Button>
                  )}
                  {allowedActions.includes("resume") && (
                    <Button icon={<PlayCircleOutlined />} disabled={busy} onClick={() => void runControl("resume")}>继续</Button>
                  )}
                </Space>
              </Space>
            )}

            {needsProfileConfiguration && (
              <div className="profile-setup-summary">
                <span className="profile-setup-summary-icon"><SettingOutlined aria-hidden /></span>
                <span className="profile-setup-summary-copy">
                  <strong>创作人物与声音</strong>
                  <small>找素材不受影响；制作成片前再选择已授权的形象和声音。</small>
                </span>
                <Button onClick={openProfileCreator}>去设置</Button>
              </div>
            )}

            {workspace ? (
              <div className="workspace-primary-actions">
                {renderPrimaryButton()}
                <Button size="large" icon={<FileTextOutlined />} onClick={returnToMaterialSelection}>返回素材</Button>
              </div>
            ) : (
              (candidates.length > 0 || sourceMode === "script") && renderPrimaryButton()
            )}

          </Card>

          {!workspace && (
            <Card className="stage-overview start-flow" variant="borderless">
              <div className="start-process" aria-label="智能创作四步流程">
                <div className="start-process-item current">
                  <span className="start-process-icon"><SearchOutlined aria-hidden /></span>
                  <span><strong>找素材</strong><small>正在为你寻找合适素材</small></span>
                </div>
                <RightOutlined className="start-process-arrow" aria-hidden />
                <div className="start-process-item">
                  <span className="start-process-icon"><FileTextOutlined aria-hidden /></span>
                  <span><strong>确认文案和费用</strong><small>查看方案并确认费用</small></span>
                </div>
                <RightOutlined className="start-process-arrow" aria-hidden />
                <div className="start-process-item">
                  <span className="start-process-icon"><VideoCameraOutlined aria-hidden /></span>
                  <span><strong>制作成片</strong><small>系统完成数字人和剪辑</small></span>
                </div>
                <RightOutlined className="start-process-arrow" aria-hidden />
                <div className="start-process-item">
                  <span className="start-process-icon"><SendOutlined aria-hidden /></span>
                  <span><strong>确认发布</strong><small>复核成片后由你确认发布</small></span>
                </div>
              </div>
              <Text type="secondary" className="cost-confirmation-note">
                预计费用将在确认方案时显示，确认后才会开始付费制作
              </Text>
            </Card>
          )}

          {workspace ? renderWorkbenchTasksCard() : renderProfileCard()}

        </div>

        <div className="workspace-right">
          <Card
            className="workspace-preview-card"
          >
            {workspace && (
              <div className="workspace-summary-rail">
                <section className="workspace-summary-group">
                  <button
                    type="button"
                    className="workspace-fold-trigger summary-fold-trigger"
                    aria-expanded={materialSummaryExpanded}
                    onClick={() => setMaterialSummaryExpanded((value) => !value)}
                  >
                    <span className="workspace-fold-title">
                      <strong>所选素材（1）</strong>
                    </span>
                    {materialSummaryExpanded ? <UpOutlined aria-hidden /> : <DownOutlined aria-hidden />}
                  </button>
                  {materialSummaryExpanded && (
                    <div className="workspace-fold-content material-summary-content">
                      {activeVideo && (
                        <div className="video-frame">
                          <video
                            key={`${activeVideo}-${videoReloadKey}`}
                            controls
                            preload="auto"
                            src={activeVideo}
                            onLoadedMetadata={(event) => {
                              if (event.currentTarget.currentTime === 0) event.currentTarget.currentTime = 0.01;
                            }}
                            onLoadedData={() => setVideoLoadError(false)}
                            onCanPlay={() => setVideoLoadError(false)}
                            onError={() => setVideoLoadError(true)}
                          />
                        </div>
                      )}
                      {activeVideo && videoLoadError && (
                        <Alert
                          className="video-load-error"
                          type="warning"
                          showIcon
                          message="成片已经生成，但预览暂时没有加载出来"
                          description="可先重新加载预览；若仍不行，可直接打开成片。"
                          action={(
                            <Space size="small">
                              <Button size="small" onClick={() => setVideoReloadKey((value) => value + 1)}>重新加载</Button>
                              <Button size="small" type="link" href={activeVideo} target="_blank">打开成片</Button>
                            </Space>
                          )}
                        />
                      )}
                      <div className="selected-material-summary">
                        <span className="selected-material-icon"><FileTextOutlined aria-hidden /></span>
                        <span>
                          <strong>{activeItem?.display_title || "未命名任务"}</strong>
                          <small>{activeItem?.source_type === "candidate" ? "候选素材" : "已带入内容"} · 已保留</small>
                        </span>
                      </div>
                    </div>
                  )}
                </section>

                <section className="workspace-summary-group">
                  <button
                    type="button"
                    className="workspace-fold-trigger summary-fold-trigger"
                    aria-expanded={costSummaryExpanded}
                    onClick={() => setCostSummaryExpanded((value) => !value)}
                  >
                    <span className="workspace-fold-title">
                      <strong>费用与提交</strong>
                      {workspace.publish.message && <small>{workspace.publish.message}</small>}
                      <small>
                        {workspace.status === "outcome_unknown"
                          ? "不会自动重复扣费"
                          : workspace.cost.known
                            ? isAdminSession && !workspace.cost.estimated_cost_cny
                              ? "管理员验收免扣；客户费用另行确认"
                              : `预计 ${cnyToCredits(workspace.cost.estimated_cost_cny || 0)} 积分`
                            : "费用待确认"}
                      </small>
                    </span>
                    {costSummaryExpanded ? <UpOutlined aria-hidden /> : <DownOutlined aria-hidden />}
                  </button>
                  {costSummaryExpanded && (
                    <div className="workspace-fold-content cost-summary-content">
                      <div className="status-summary">
                        <div><span>任务状态</span><strong>{STATUS_LABEL[workspace.status] || workspace.status}</strong></div>
                        <div><span>当前阶段</span><strong>{STAGE_LABEL[currentStage] || currentStage}</strong></div>
                        <div>
                          <span>预计费用</span>
                          <strong>{workspace.cost.known
                            ? isAdminSession && !workspace.cost.estimated_cost_cny
                              ? "管理员验收免扣；客户提交前重新确认"
                              : `${cnyToCredits(workspace.cost.estimated_cost_cny || 0)} 积分`
                            : "暂无法确定"}</strong>
                        </div>
                        <div><span>发布方式</span><strong>{workspace.publish.message || (workspace.publish.confirmed ? "已确认" : "待确认")}</strong></div>
                      </div>
                      <Timeline
                        items={(activeItem?.blocked_reasons?.length
                          ? activeItem.blocked_reasons.map((reason) => ({ color: "red", children: reason }))
                          : [
                              { color: stageIndex(currentStage) > 0 ? "green" : "blue", children: "素材与选题" },
                              { color: currentBusinessStage > 1 ? "green" : currentBusinessStage === 1 ? "blue" : "gray", children: "确认转写、口播稿与创作方案" },
                              { color: currentBusinessStage === 2 ? "blue" : "gray", children: "制作成片、复核并查看发布状态" },
                            ])}
                      />
                    </div>
                  )}
                </section>
              </div>
            )}
            {!workspace && (
              sourceMode === "keyword" && creationMode === "auto" && automaticCandidatePool.length ? (
                <div className="source-preview">
                  <Tag color="purple">自动创作素材 {automaticCandidatePool.length} 条</Tag>
                  <Title level={4}>按当前排序处理素材，再由 AI 选出 1 条</Title>
                  <List
                    size="small"
                    dataSource={automaticCandidatePool}
                    renderItem={(candidate) => (
                      <List.Item>
                        <List.Item.Meta
                          title={candidate.title || "未命名候选"}
                          description={`${candidate.platform_label} · ${candidate.author_name || "作者未返回"}`}
                        />
                      </List.Item>
                    )}
                  />
                </div>
              ) : selectedCandidate ? (
                <div className="source-preview">
                  <Tag color="purple">已选素材</Tag>
                  <Title level={4}>{selectedCandidate.title}</Title>
                  <Text>{selectedCandidate.platform_label} · {selectedCandidate.author_name || "作者未返回"}</Text>
                </div>
              ) : (
                <Empty description="选择素材后，这里显示预览" />
              )
            )}
          </Card>

          {workspace ? renderProfileCard() : renderWorkbenchTasksCard()}

        </div>
      </div>

      <Modal
        title={(
          <span className="creation-settings-title">
            <strong>创作设置</strong>
            <small aria-hidden>确认后即可开始本次创作</small>
          </span>
        )}
        aria-label="创作设置"
        className="creation-settings-modal"
        width={820}
        centered
        open={setupOpen}
        onCancel={() => setSetupOpen(false)}
        footer={(
          <div className="creation-settings-footer">
            <Button type="text" size="large" onClick={() => setSetupOpen(false)}>取消</Button>
            <Button
              type="primary"
              size="large"
              loading={busy}
              disabled={!sourceConnectionReady}
              onClick={() => void saveWorkspaceSetup()}
            >
              保存并开始创作
            </Button>
          </div>
        )}
        destroyOnHidden
      >
        <div className="creation-settings-body">
          <section className="settings-summary-card source-settings-summary" aria-label="素材网站摘要">
            <Text strong>素材网站</Text>
            <div className="source-summary-row">
              <div className="source-platform-icons" aria-label="已配置素材网站">
                {SOURCE_BROWSER_PLATFORMS.map(({ platform, label }) => (
                  <span key={platform} className={`source-platform-icon source-platform-icon-${platform}`} title={label}>
                    {sourcePlatformIcon(platform)}
                  </span>
                ))}
              </div>
              <Text>{readySourceCount} 个平台可用</Text>
              <span className={`source-health ${sourceConnectionReady ? "ready" : "waiting"}`}>
                <i />
                {allSourceConnectionsReady ? "全部正常" : sourceConnectionReady ? `${readySourceCount} 个正常` : "等待连接"}
              </span>
              <Button
                type="link"
                className="settings-manage-link"
                aria-label="管理素材网站"
                onClick={() => {
                  setSetupOpen(false);
                  setSourceManagerOpen(true);
                }}
              >
                管理素材网站 <RightOutlined />
              </Button>
            </div>
          </section>

          <div className="creation-settings-grid">
            <section className="settings-summary-card publish-settings-summary" aria-label="发布网站设置">
              <Text strong>发布网站</Text>
              <div className="publish-platform-chips" role="group" aria-label="选择发布网站">
                {platforms.map((platform) => {
                  const selectable = platform.enabled || platform.manual_fallback || platform.manual_only;
                  const selected = publishPlatforms.includes(platform.platform);
                  return (
                    <button
                      key={platform.platform}
                      type="button"
                      className={`publish-platform-chip${selected ? " selected" : ""}`}
                      aria-pressed={selected}
                      disabled={!selectable}
                      onClick={() => setPublishPlatforms((current) => (
                        selected
                          ? current.filter((item) => item !== platform.platform)
                          : [...current, platform.platform]
                      ))}
                    >
                      {PLATFORM_LABELS[platform.platform] || platform.display_name}
                      {selected && <CheckCircleOutlined aria-hidden />}
                    </button>
                  );
                })}
              </div>
              <div className="publish-account-summary">
                <Text>已选 {publishPlatforms.length} 个平台</Text>
                <span aria-hidden>·</span>
                <Text className="summary-ready">{selectedPublishAccountSummary.ready} 个已登录</Text>
                <span aria-hidden>·</span>
                <Text className="summary-waiting">{selectedPublishAccountSummary.pending} 个待登录</Text>
                <Button
                  type="link"
                  className="settings-manage-link"
                  aria-label="管理发布账号"
                  onClick={() => {
                    setSetupOpen(false);
                    setPublishAccountManagerOpen(true);
                  }}
                >
                  管理发布账号 <RightOutlined />
                </Button>
              </div>
              <Text type="secondary" className="publish-login-note">
                未登录不影响制作，确认发布前完成即可
              </Text>
            </section>

            {selectedProfile && (
              <section className="settings-summary-card profile-settings-summary" aria-label="出镜设置">
                <Text strong>出镜设置</Text>
                <div className="setup-profile-card">
                  <span className="setup-profile-avatar">
                    {profileAvatar?.preview_url ? (
                      profileAvatar.preview_type === "video" ? (
                        <video src={profileAvatar.preview_url} muted aria-label={`${profileAvatar.name} 出镜预览`} />
                      ) : (
                        <img src={profileAvatar.preview_url} alt={`${profileAvatar.name} 出镜预览`} />
                      )
                    ) : <UserOutlined aria-hidden />}
                  </span>
                  <span className="setup-profile-copy">
                    <strong>{profileAvatar?.name || selectedProfile.name}</strong>
                    <small>声音：{profileVoice?.name || selectedProfile.name}</small>
                  </span>
                  <Button aria-label="更换" onClick={openProfileSwitcher}>更换</Button>
                </div>
                <Button type="link" className="add-profile-link" aria-label="新增出镜人" onClick={openProfileCreatorFromSetup}>
                  新增出镜人 <RightOutlined />
                </Button>
              </section>
            )}
          </div>
        </div>
      </Modal>

      <Modal
        title="管理素材网站"
        aria-label="管理素材网站"
        className="settings-manager-modal"
        width={620}
        open={sourceManagerOpen}
        onCancel={() => {
          setSourceManagerOpen(false);
          setSetupOpen(true);
        }}
        footer={(
          <Button
            type="primary"
            onClick={() => {
              setSourceManagerOpen(false);
              setSetupOpen(true);
            }}
          >
            完成
          </Button>
        )}
        destroyOnHidden
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">素材网站只用于找素材，与发布账号相互独立。以后增加新网站也会集中在这里管理。</Text>
          <Alert
            type={allSourceConnectionsReady ? "success" : sourceConnectionReady ? "info" : "warning"}
            showIcon
            message={
              allSourceConnectionsReady
                ? "所有素材网站都已可用"
                : sourceConnectionReady
                  ? `已有 ${readySourceCount}/${SOURCE_BROWSER_PLATFORMS.length} 个素材网站可用`
                  : "先连接至少一个素材网站"
            }
          />
          <div className="settings-manager-list">
            {browserDiscoveries.map((connection) => {
              const platform = connection.platform as BrowserPlatform;
              const ready = sourcePlatformReady(connection);
              const actionLabel = ready
                ? `打开${connection.platform_label}`
                : `登录${connection.platform_label}`;
              return (
                <div className="settings-manager-row" key={platform}>
                  <span>
                    <strong>{connection.platform_label}</strong>
                    <small>{ready ? "已可用于找素材" : connection.message}</small>
                  </span>
                  <Tag color={ready ? "success" : "warning"}>{ready ? "已连接" : "待登录"}</Tag>
                  <Button
                    type={ready ? "default" : "primary"}
                    aria-label={actionLabel}
                    loading={startingBrowserPlatform === platform}
                    disabled={!connection.enabled || (busy && startingBrowserPlatform !== platform)}
                    onClick={() => void startSourceConnection(platform)}
                  >
                    {ready ? "打开网站" : `登录${connection.platform_label}`}
                  </Button>
                </div>
              );
            })}
            {!browserDiscoveries.length && <Text type="secondary">正在读取素材网站连接状态…</Text>}
          </div>
          <Button loading={busy && startingBrowserPlatform === null} onClick={() => void refreshSourceConnections()}>
            我已登录，刷新状态
          </Button>
        </Space>
      </Modal>

      <Modal
        title="管理发布账号"
        aria-label="管理发布账号"
        className="settings-manager-modal"
        width={660}
        open={publishAccountManagerOpen}
        onCancel={() => {
          setPublishAccountManagerOpen(false);
          setSetupOpen(true);
        }}
        footer={(
          <Button
            type="primary"
            onClick={() => {
              setPublishAccountManagerOpen(false);
              setSetupOpen(true);
            }}
          >
            完成
          </Button>
        )}
        destroyOnHidden
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">每个发布网站都要单独登录。现在可以先制作视频，确认发布前再补齐未登录账号。</Text>
          <div className="settings-manager-list">
            {platforms.map((capability) => {
              const platform = capability.platform;
              const readyAccount = accounts.find(
                (account) => account.platform === platform && account.status === "ready",
              );
              const knownAccount = readyAccount || accounts.find((account) => account.platform === platform);
              const selected = publishPlatforms.includes(platform);
              const statusLabel = readyAccount
                ? "已登录"
                : knownAccount?.status === "browser_open"
                  ? "等待扫码"
                  : capability.requires_account
                    ? "未登录"
                    : "发布时手动登录";
              const statusColor = readyAccount
                ? "success"
                : knownAccount?.status === "browser_open"
                  ? "processing"
                  : "warning";
              return (
                <div className="settings-manager-row" key={platform}>
                  <span>
                    <strong>
                      {PLATFORM_LABELS[platform] || capability.display_name}
                      {selected && <em>本次已选</em>}
                    </strong>
                    <small>{readyAccount?.name || knownAccount?.message || "尚未保存发布账号"}</small>
                  </span>
                  <Tag color={statusColor}>{statusLabel}</Tag>
                  {capability.requires_account ? (
                    readyAccount ? (
                      <Button aria-label={`${PLATFORM_LABELS[platform] || capability.display_name}已登录`} disabled>已登录</Button>
                    ) : knownAccount?.status === "browser_open" ? (
                      <Button
                        aria-label={`检查${PLATFORM_LABELS[platform] || capability.display_name}登录`}
                        loading={publishAccountAction === platform}
                        onClick={() => void refreshPublishAccount(knownAccount.account_id, platform)}
                      >
                        检查登录
                      </Button>
                    ) : (
                      <Button
                        type="primary"
                        aria-label={`登录${PLATFORM_LABELS[platform] || capability.display_name}`}
                        loading={publishAccountAction === platform}
                        disabled={Boolean(publishAccountAction && publishAccountAction !== platform)}
                        onClick={() => void connectPublishAccountFromSetup(platform)}
                      >
                        登录
                      </Button>
                    )
                  ) : (
                    <Button aria-label={`${PLATFORM_LABELS[platform] || capability.display_name}手动发布`} disabled>手动发布</Button>
                  )}
                </div>
              );
            })}
          </div>
        </Space>
      </Modal>

      <Modal
        title={profileEditorMode === "switch" ? "选择已有出镜人" : "新增出镜人"}
        aria-label={profileEditorMode === "switch" ? "选择已有出镜人" : "新增出镜人"}
        open={profileCreateOpen}
        onCancel={() => {
          setProfileCreateOpen(false);
          setSetupOpen(profileReturnToSetup);
        }}
        footer={null}
        destroyOnHidden
      >
        {profileEditorMode === "switch" ? (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Text type="secondary">
              这里仅显示已经保存并可直接使用的出镜人。点击其他出镜人即可切换。
            </Text>
            {completeProfiles.length ? (
              <div className="profile-picker-grid" aria-label="已有出镜人列表">
                {completeProfiles.map((profile) => {
                  const avatar = assets.find((asset) => asset.asset_id === profile.avatar_id);
                  const voice = assets.find((asset) => asset.asset_id === profile.voice_id);
                  const isCurrent = profile.profile_id === selectedProfile?.profile_id;
                  return (
                    <button
                      type="button"
                      key={profile.profile_id}
                      className={`profile-picker-card${isCurrent ? " selected" : ""}`}
                      aria-label={`${isCurrent ? "当前出镜人" : "切换到出镜人"}：${profile.name}`}
                      disabled={isCurrent}
                      onClick={() => switchToSavedProfile(profile)}
                    >
                      <span className="profile-picker-media">
                        {avatar?.preview_url ? (
                          avatar.preview_type === "video" ? (
                            <video src={avatar.preview_url} muted playsInline preload="metadata" />
                          ) : (
                            <img src={avatar.preview_url} alt="" />
                          )
                        ) : (
                          <SafetyCertificateOutlined />
                        )}
                      </span>
                      <span>
                        <strong>{profile.name}</strong>
                        <small>声音：{voice?.name || "已保存声音"}</small>
                        <small>{isCurrent ? "当前使用" : "点击切换"}</small>
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可切换的出镜人" />
            )}
            {completeProfiles.length <= 1 && (
              <Text type="secondary">还没有其他已保存的出镜人。如需创建新的，请返回点击“新增出镜人”。</Text>
            )}
          </Space>
        ) : (
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">
            选择形象和声音即可，系统会自动完成通用智能优化。
          </Text>
          <div className="profile-name-field">
            <Text strong>出镜人名称</Text>
            <Input
              aria-label="出镜人名称"
              value={profileName}
              onChange={(event) => {
                profileNameManuallyEditedRef.current = true;
                setProfileName(event.target.value);
              }}
              placeholder="例如：大树、店长本人"
            />
            <Text type="secondary">保存后会用这个名称显示在创作设置里。</Text>
          </div>
          <div>
            <div className="profile-avatar-section-heading">
              <span>
                <Text strong>选择形象</Text>
                <Text type="secondary">
                  {avatarCapability?.provider_name === "local_avatar"
                    ? "可上传本人或已获授权的人脸照片"
                    : "可上传本人或已获授权的正脸训练视频"}
                </Text>
              </span>
              <Upload
                accept={avatarCapability?.provider_name === "local_avatar"
                  ? "image/png,image/jpeg,image/webp"
                  : "video/mp4,video/quicktime,.mp4,.mov"}
                showUploadList={false}
                beforeUpload={(file) => {
                  void uploadAvatarMaterial(file);
                  return false;
                }}
              >
                <Button
                  aria-label="上传人脸素材"
                  icon={<UploadOutlined />}
                  loading={uploadingAvatar}
                  disabled={!avatarMaterialUploadAvailable}
                >
                  {avatarCapability?.provider_name === "local_avatar" ? "上传人脸照片" : "上传人脸训练视频"}
                </Button>
              </Upload>
            </div>
            {usableAvatarAssets.length ? (
              <div className="profile-avatar-grid" aria-label="形象选择列表">
                {usableAvatarAssets.map((asset) => (
                  <button
                    type="button"
                    key={asset.asset_id}
                    className={`profile-avatar-card${asset.asset_id === avatarId ? " selected" : ""}`}
                    aria-label={`选择形象：${asset.name}`}
                    aria-pressed={asset.asset_id === avatarId}
                    onClick={() => selectProfileAvatar(asset)}
                  >
                    <span className="profile-avatar-card-media">
                      {asset.preview_url ? (
                        asset.preview_type === "video" ? (
                          <video
                            src={asset.preview_url}
                            aria-label={`${asset.name} 形象预览`}
                            muted
                            playsInline
                            preload="metadata"
                          />
                        ) : (
                          <img src={asset.preview_url} alt={`${asset.name} 形象预览`} />
                        )
                      ) : (
                        <SafetyCertificateOutlined />
                      )}
                    </span>
                    <strong>{asset.name}</strong>
                    <small>{asset.asset_id === avatarId ? "已选择" : "点击选择"}</small>
                  </button>
                ))}
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可用形象" />
            )}
            {pendingAvatarAssets.length > 0 && (
              <Text type="secondary" className="pending-avatar-note">
                正在准备的形象：{pendingAvatarAssets.map((asset) => asset.name).join("、")}。完成后即可选择。
              </Text>
            )}
            {!avatarMaterialUploadAvailable && (
              <Text type="danger" className="pending-avatar-note">当前数字人服务暂未开放新增形象。</Text>
            )}
          </div>
          <Space.Compact block>
            <Select
              aria-label="选择声音"
              style={{ flex: 1 }}
              value={voiceId || undefined}
              onChange={setVoiceId}
              placeholder="选择已授权音色"
              options={[
                ...usableVoiceAssets.map((asset) => ({ label: asset.name, value: asset.asset_id })),
                ...pendingVoiceAssets.map((asset) => ({
                  label: `${asset.name}（等待声音服务开通）`,
                  value: asset.asset_id,
                  disabled: true,
                })),
              ]}
            />
            <Button
              icon={playingVoiceId === voiceId ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
              disabled={!voiceId}
              onClick={() => void toggleVoicePreview()}
            >
              {playingVoiceId === voiceId ? "停止" : "试听"}
            </Button>
            <Button
              aria-label="添加新声音"
              onClick={() => setVoiceUploadOpen(true)}
            >
              ＋ 添加
            </Button>
          </Space.Compact>
          <audio
            ref={voicePreviewRef}
            src={voicePreviewSrc || undefined}
            preload="none"
            aria-label={`声音试听：${selectedCreatorVoice?.name || "未选择"}`}
            onPlay={() => setPlayingVoiceId(voiceId)}
            onPause={() => setPlayingVoiceId("")}
            onEnded={() => setPlayingVoiceId("")}
            onError={() => {
              setPlayingVoiceId("");
              setVoicePreviewError("暂时无法试听这个声音，请稍后重试。");
            }}
          />
          {voicePreviewError && <Text type="danger">{voicePreviewError}</Text>}
          {pendingVoiceAssets.length > 0 && (
            <Text type="secondary">
              已保存声音样本：{pendingVoiceAssets.map((asset) => asset.name).join("、")}。当前声音服务尚未开通，暂不能选择。
            </Text>
          )}
          {voiceUploadOpen && (
            <div className="voice-upload-panel">
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <div className="voice-upload-heading">
                  <Text strong>添加新声音</Text>
                  <Button
                    type="link"
                    size="small"
                    disabled={uploadingVoice}
                    onClick={() => {
                      setVoiceUploadOpen(false);
                    }}
                  >
                    暂不添加
                  </Button>
                </div>
                <Text type="secondary">
                  {avatarCapability?.supports_voice_cloning
                    ? "支持 MP3、WAV、M4A，需在 30 秒以内。处理完成后会自动选中。"
                    : "支持 MP3、WAV、M4A，需在 30 秒以内。当前只能先保存样本，声音服务开通后才能选择。"}
                </Text>
                <Upload
                  accept="audio/wav,audio/mpeg,audio/mp3,audio/mp4,.wav,.mp3,.m4a"
                  showUploadList={false}
                  beforeUpload={(file) => {
                    void uploadVoiceSample(file);
                    return false;
                  }}
                >
                  <Button
                    type="primary"
                    icon={<UploadOutlined />}
                    loading={uploadingVoice}
                    disabled={!voiceSampleUploadAvailable}
                  >
                    {avatarCapability?.supports_voice_cloning ? "确认上传并处理" : "保存声音样本"}
                  </Button>
                </Upload>
                {!voiceSampleUploadAvailable && (
                  <Text type="danger">当前数字人服务暂未开放声音上传。</Text>
                )}
              </Space>
            </div>
          )}
          <Button type="primary" size="large" block loading={busy} onClick={() => void saveProfile()}>
            保存并使用这个出镜人
          </Button>
        </Space>
        )}
      </Modal>

      <style>{`
        .smart-workspace {
          --workspace-purple: #6c43e8;
          --workspace-blue: #4b73e8;
          display: flex;
          flex-direction: column;
          gap: 22px;
          width: 100%;
          max-width: 1180px;
          margin: 0 auto;
          min-width: 0;
        }
        .workspace-hero {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 24px;
          padding: 2px 4px 0;
        }
        .workspace-hero h1 {
          margin: 0;
          color: #121a2d;
          font-size: clamp(30px, 2.8vw, 38px);
          line-height: 1.18;
          letter-spacing: .02em;
        }
        .workspace-hero p {
          margin: 8px 0 0;
          color: #6f788c;
          font-size: 14px;
        }
        .workspace-stage-strip {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 0;
          overflow: hidden;
          border: 1px solid #e5e7ef;
          border-radius: 14px;
          background: #fff;
        }
        .workspace-stage-step {
          min-width: 0;
          min-height: 84px;
          display: grid;
          grid-template-columns: 38px minmax(0, 1fr) 16px;
          align-items: center;
          gap: 12px;
          padding: 18px 20px;
          border: 0;
          border-right: 1px solid #eceef3;
          background: transparent;
          color: #70788a;
          text-align: left;
          cursor: pointer;
        }
        .workspace-stage-step:last-child { border-right: 0; }
        .workspace-stage-step:hover { background: #faf9ff; }
        .workspace-stage-step:focus-visible {
          position: relative;
          z-index: 1;
          outline: 3px solid rgba(108, 67, 232, .18);
          outline-offset: -3px;
        }
        .workspace-stage-step.current { color: #1f2840; background: #fbfaff; }
        .workspace-stage-step.complete { color: #273149; }
        .workspace-stage-number {
          width: 34px;
          height: 34px;
          display: grid;
          place-items: center;
          border: 1px solid #dfe2ea;
          border-radius: 50%;
          background: #f7f8fb;
          color: #6d7588;
          font-weight: 700;
        }
        .workspace-stage-step.current .workspace-stage-number {
          border-color: var(--workspace-purple);
          background: var(--workspace-purple);
          color: #fff;
          box-shadow: 0 0 0 5px rgba(108, 67, 232, .09);
        }
        .workspace-stage-step.complete .workspace-stage-number {
          border-color: #22c55e;
          background: #f0fdf4;
          color: #16a34a;
        }
        .workspace-stage-copy {
          min-width: 0;
          display: flex;
          flex-direction: column;
          gap: 3px;
        }
        .workspace-stage-copy strong {
          overflow: hidden;
          color: inherit;
          font-size: 15px;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .workspace-stage-copy small { color: #8790a3; font-size: 12px; line-height: 1.4; }
        .stage-overview {
          overflow: hidden;
          border: 1px solid #e5e7ef;
          box-shadow: none;
        }
        .stage-overview .ant-steps-item-process .ant-steps-item-icon {
          background: var(--workspace-purple);
          border-color: var(--workspace-purple);
        }
        .start-flow .ant-card-body {
          display: flex;
          flex-direction: column;
          gap: 18px;
          padding: 24px 28px 20px;
        }
        .start-process {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 22px minmax(0, 1fr) 22px minmax(0, 1fr) 22px minmax(0, 1fr);
          align-items: center;
          gap: 8px;
        }
        .start-process-item {
          display: flex;
          align-items: center;
          gap: 10px;
          min-width: 0;
        }
        .start-process-item > span:last-child {
          display: flex;
          flex-direction: column;
          gap: 3px;
          min-width: 0;
        }
        .start-process-item strong { color: #263047; font-size: 15px; }
        .start-process-item small { color: #81899a; font-size: 12px; line-height: 1.4; }
        .start-process-icon {
          flex: 0 0 44px;
          width: 44px;
          height: 44px;
          display: grid;
          place-items: center;
          border: 1px solid #e3e5ec;
          border-radius: 50%;
          color: #737b8e;
          background: #f8f9fc;
          font-size: 19px;
        }
        .start-process-item.current .start-process-icon {
          border-color: #c9baf5;
          color: var(--workspace-purple);
          background: #f3efff;
        }
        .start-process-arrow {
          justify-self: center;
          color: #b3b8c5;
        }
        .cost-confirmation-note {
          align-self: center;
          text-align: center;
        }
        .workspace-grid {
          display: grid;
          grid-template-columns: minmax(0, 1.5fr) minmax(320px, .78fr);
          gap: 16px;
          align-items: start;
        }
        .workspace-grid.has-workspace .workspace-left,
        .workspace-grid.has-workspace .workspace-right {
          display: contents;
        }
        .workspace-grid.has-workspace .workspace-action-card {
          grid-column: 1;
          grid-row: 1;
        }
        .workspace-grid.has-workspace .workspace-preview-card {
          grid-column: 2;
          grid-row: 1;
        }
        .workspace-grid.has-workspace .workspace-tasks-card {
          grid-column: 1 / -1;
          grid-row: 2;
        }
        .workspace-grid.has-workspace .workspace-profile-card {
          grid-column: 1 / -1;
          grid-row: 3;
        }
        .workspace-grid.is-start {
          grid-template-columns: minmax(0, 1.05fr) minmax(340px, .95fr);
          gap: 18px;
        }
        .workspace-grid.is-start .workspace-left,
        .workspace-grid.is-start .workspace-right {
          display: contents;
        }
        .workspace-grid.is-start .workspace-action-card {
          grid-column: 1 / -1;
          grid-row: 1;
        }
        .workspace-grid.is-start .start-flow {
          grid-column: 1 / -1;
          grid-row: 2;
        }
        .workspace-grid.is-start .workspace-tasks-card {
          grid-column: 1;
          grid-row: 3;
        }
        .workspace-grid.is-start .workspace-profile-card {
          grid-column: 2;
          grid-row: 3;
        }
        .workspace-grid.is-start .workspace-preview-card {
          display: none;
        }
        .workspace-action-card,
        .workspace-preview-card,
        .workspace-profile-card,
        .workspace-tasks-card,
        .workspace-loading-card {
          border: 1px solid #e4e6ee;
          border-radius: 14px;
          box-shadow: none;
        }
        .workspace-grid.has-workspace .workspace-action-card,
        .workspace-grid.has-workspace .workspace-preview-card {
          min-height: 0;
          align-self: stretch;
        }
        .workspace-grid.has-workspace .workspace-action-card > .ant-card-head {
          min-height: 76px;
          padding: 0 24px;
        }
        .workspace-grid.has-workspace .workspace-action-card > .ant-card-body {
          padding: 24px;
        }
        .workspace-action-heading {
          display: flex;
          flex-direction: column;
          gap: 3px;
          padding: 12px 0;
        }
        .workspace-action-heading small {
          color: #7d8698;
          font-size: 12px;
          font-weight: 500;
        }
        .workspace-action-heading strong {
          color: #151d31;
          font-size: 20px;
          line-height: 1.35;
        }
        .workspace-grid.has-workspace .outcome-focus-card > .ant-card-body {
          padding: 34px 28px 24px;
        }
        .outcome-focus-intro {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 10px;
          padding: 0 0 6px;
          text-align: center;
        }
        .outcome-focus-intro h2 {
          margin: 0;
          color: #111a2f;
          font-size: clamp(28px, 3vw, 38px);
          line-height: 1.2;
        }
        .outcome-focus-icon {
          width: 56px;
          height: 56px;
          display: grid;
          place-items: center;
          border-radius: 50%;
          background: #fff7e6;
          color: #f59e0b;
          font-size: 27px;
          box-shadow: 0 0 0 10px rgba(245, 158, 11, .08);
        }
        .workspace-grid.is-start .workspace-action-card {
          border-color: #d8cff6;
          background: rgba(255, 255, 255, .94);
        }
        .workspace-grid.is-start .workspace-action-card > .ant-card-body {
          padding: 28px;
        }
        .creation-composer {
          display: flex;
          flex-direction: column;
          gap: 18px;
          padding: 2px;
        }
        .creation-source-tabs {
          padding: 0 0 12px;
          border-bottom: 1px solid #e8eaf0;
          background: transparent;
        }
        .creation-source-tabs .ant-segmented-item {
          color: #445066;
          font-weight: 600;
        }
        .creation-source-tabs .ant-segmented-item-selected {
          color: var(--workspace-purple);
          box-shadow: inset 0 -2px 0 var(--workspace-purple);
          background: transparent;
        }
        .creation-entry-row {
          display: grid;
          grid-template-columns: minmax(180px, 1fr) 40px 132px;
          align-items: center;
          gap: 8px;
        }
        .creation-entry-row .ant-input {
          min-height: 56px;
          font-size: 16px;
        }
        .creation-mode-section {
          padding: 2px;
        }
        .creation-mode-cards {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 14px;
        }
        .creation-mode-card {
          position: relative;
          display: flex;
          align-items: center;
          gap: 13px;
          min-height: 88px;
          padding: 16px 42px 16px 17px;
          border: 1px solid #e0e3eb;
          border-radius: 14px;
          background: #fff;
          color: #2a3040;
          text-align: left;
          cursor: pointer;
          transition: border-color .18s ease, background .18s ease, box-shadow .18s ease;
        }
        .creation-mode-card:hover { border-color: #c9bcf3; }
        .creation-mode-card:focus-visible {
          outline: 3px solid rgba(108, 67, 232, .16);
          outline-offset: 2px;
        }
        .creation-mode-card.selected {
          border-color: #bdaaf4;
          background: #faf8ff;
          box-shadow: 0 0 0 1px rgba(108, 67, 232, .05);
        }
        .creation-mode-icon {
          flex: 0 0 44px;
          width: 44px;
          height: 44px;
          display: grid;
          place-items: center;
          border-radius: 12px;
          color: #687286;
          background: #f2f4f8;
          font-size: 20px;
        }
        .creation-mode-card.selected .creation-mode-icon {
          color: var(--workspace-purple);
          background: #eee8ff;
        }
        .creation-mode-card.risk .creation-mode-icon {
          color: #9a6700;
          background: #fff7e0;
        }
        .creation-mode-card.risk.selected {
          border-color: #e8b84d;
          background: #fffaf0;
          box-shadow: 0 0 0 1px rgba(196, 134, 0, .06);
        }
        .creation-mode-card.risk.selected .creation-mode-check {
          color: #b77900;
        }
        .creation-mode-copy {
          display: flex;
          flex-direction: column;
          gap: 6px;
          min-width: 0;
        }
        .creation-mode-title {
          display: flex;
          align-items: center;
          gap: 7px;
          color: #242a39;
          font-size: 15px;
        }
        .creation-mode-copy small {
          color: #7e8697;
          font-size: 12px;
          line-height: 1.4;
        }
        .creation-mode-check {
          position: absolute;
          top: 14px;
          right: 14px;
          color: transparent;
        }
        .creation-mode-card.selected .creation-mode-check { color: var(--workspace-purple); }
        .creation-mode-note.ant-typography {
          margin: 10px 2px 0;
          font-size: 13px;
        }
        .creation-mode-risk-note {
          display: flex;
          align-items: flex-start;
          gap: 8px;
          margin: 10px 2px 0;
          color: #8a5a00;
          font-size: 13px;
          line-height: 1.55;
        }
        .creation-mode-risk-note .anticon {
          margin-top: 3px;
          color: #c47f00;
        }
        .workspace-left,
        .workspace-right {
          display: flex;
          flex-direction: column;
          gap: 16px;
          min-width: 0;
        }
        .section-heading {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          gap: 12px;
          margin-bottom: 10px;
        }
        .candidate-stack { display: flex; flex-direction: column; gap: 9px; }
        .candidate-legend {
          display: block;
          margin: -2px 2px 10px;
          font-size: 12.5px;
          line-height: 1.6;
        }
        .candidate-platform-summary {
          display: flex;
          flex-wrap: wrap;
          gap: 6px 8px;
          margin: -2px 0 12px;
        }
        .candidate-platform-summary span {
          padding: 4px 9px;
          border-radius: 999px;
          background: #f5f2ff;
          color: #5f43b2;
          font-size: 12px;
          line-height: 1.5;
        }
        .candidate-stack-toggle {
          display: flex;
          justify-content: flex-start;
          margin: 2px 0 0 2px;
        }
        .candidate-stack-toggle .ant-btn {
          height: 30px;
          padding: 0 4px;
          font-size: 13px;
        }
        .candidate-card {
          width: 100%;
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          align-items: center;
          border: 1px solid #e6e8ef;
          border-radius: 12px;
          background: #fff;
          color: inherit;
          overflow: hidden;
        }
        .candidate-card:hover { border-color: #8a65ed; box-shadow: 0 0 0 3px rgba(111,73,232,.12); }
        .candidate-card.selected { border-color: #7652e8; background: #f7f3ff; }
        .candidate-select {
          min-width: 0;
          display: grid;
          grid-template-columns: auto minmax(0, 1fr) auto;
          align-items: center;
          gap: 12px;
          border: 0;
          padding: 12px;
          background: transparent;
          color: inherit;
          text-align: left;
          cursor: pointer;
        }
        .candidate-select:focus-visible {
          outline: 3px solid rgba(111,73,232,.2);
          outline-offset: -3px;
        }
        .candidate-rank {
          display: grid;
          place-items: center;
          width: 34px;
          height: 34px;
          border-radius: 9px;
          color: #603ad4;
          background: #ebe3ff;
          font-weight: 700;
        }
        .candidate-copy { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
        .candidate-copy strong,
        .candidate-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .candidate-card.manual .candidate-copy strong {
          display: -webkit-box;
          white-space: normal;
          -webkit-box-orient: vertical;
          -webkit-line-clamp: 2;
        }
        .candidate-copy small { color: #777e8d; }
        .candidate-copy .candidate-metrics {
          color: #596174;
          font-variant-numeric: tabular-nums;
          white-space: normal;
        }
        .candidate-source-link,
        .candidate-source-unavailable {
          margin-right: 12px;
          white-space: nowrap;
        }
        .candidate-source-link {
          padding: 6px 10px;
          border: 1px solid #d9d0f5;
          border-radius: 8px;
          color: #6941d9;
          background: #fff;
        }
        .candidate-source-link:hover,
        .candidate-source-link:focus-visible {
          border-color: #7652e8;
          color: #4f2bb8;
          outline: none;
        }
        .candidate-source-unavailable {
          color: #a0a5b1;
          font-size: 12px;
        }
        .profile-setup-summary {
          display: grid;
          grid-template-columns: auto minmax(0, 1fr) auto;
          align-items: center;
          gap: 12px;
          margin-top: 14px;
          padding: 12px 14px;
          border: 1px solid #ece8f8;
          border-radius: 12px;
          background: #faf9ff;
        }
        .profile-setup-summary-icon {
          display: grid;
          place-items: center;
          width: 34px;
          height: 34px;
          border-radius: 10px;
          color: #6941d9;
          background: #f0ebff;
        }
        .profile-setup-summary-copy {
          display: flex;
          flex-direction: column;
          gap: 2px;
          min-width: 0;
        }
        .profile-setup-summary-copy small { color: #858b99; }
        .publish-review {
          display: flex;
          flex-direction: column;
          gap: 10px;
          padding: 12px;
          border: 1px solid #e4ddf7;
          border-radius: 12px;
          background: #fbf9ff;
        }
        .review-primary-editor {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .review-editor-heading {
          display: flex;
          align-items: baseline;
          justify-content: space-between;
          gap: 12px;
        }
        .review-editor-heading .ant-typography:last-child {
          text-align: right;
        }
        .primary-review-textarea {
          font-size: 16px;
          line-height: 1.75;
        }
        .creative-plan-details {
          margin-top: 2px;
          border-top: 1px solid #eceef4;
        }
        .creative-plan-details summary {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          padding: 12px 2px 4px;
          color: #6941d9;
          cursor: pointer;
          list-style: none;
        }
        .creative-plan-details summary::-webkit-details-marker { display: none; }
        .creative-plan-details summary > span:first-child {
          display: flex;
          flex-direction: column;
          gap: 2px;
          color: #2c3140;
        }
        .creative-plan-details summary small {
          color: #8a90a0;
          font-weight: 400;
        }
        .creative-plan-details summary:focus-visible {
          border-radius: 8px;
          outline: 3px solid rgba(111,73,232,.16);
          outline-offset: 2px;
        }
        .creative-plan-fields {
          display: flex;
          flex-direction: column;
          gap: 10px;
          margin-top: 10px;
          padding: 14px;
          border-radius: 12px;
          background: #faf9ff;
        }
        .search-empty {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 4px;
          padding: 6px 0;
        }
        .profile-summary {
          display: flex;
          flex-direction: column;
          gap: 14px;
        }
        .profile-summary-details {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 12px;
          background: transparent;
        }
        .profile-setting-row {
          display: grid;
          grid-template-columns: auto minmax(0, 1fr) auto;
          align-items: center;
          gap: 8px;
          min-width: 0;
          padding: 8px;
          border: 0;
          border-radius: 8px;
          background: transparent;
          color: #252938;
          font: inherit;
          text-align: left;
          cursor: pointer;
        }
        .profile-setting-row:hover { background: #f7f5ff; }
        .profile-setting-row:focus-visible {
          outline: 3px solid rgba(108, 67, 232, .18);
          outline-offset: 2px;
        }
        .profile-setting-label {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          color: #777e8d;
          font-weight: 400;
        }
        .profile-setting-row strong {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .profile-setting-row > .anticon {
          color: #a3a9b7;
          font-size: 12px;
        }
        .workspace-grid.is-start .profile-avatar-preview {
          display: none;
        }
        .workspace-grid.is-start .profile-summary-details {
          grid-template-columns: 1fr;
          gap: 0;
        }
        .workspace-grid.is-start .profile-setting-row {
          padding: 13px 0;
          border-radius: 0;
          border-bottom: 1px solid #edf0f4;
        }
        .workspace-grid.is-start .profile-setting-row:last-child {
          border-bottom: 0;
        }
        .profile-avatar-preview,
        .profile-picker-media {
          display: grid;
          place-items: center;
          overflow: hidden;
          background: #f2eeff;
          color: #7252dc;
        }
        .profile-avatar-preview {
          position: relative;
          align-self: center;
          width: min(42%, 168px);
          aspect-ratio: 9 / 16;
          border-radius: 14px;
        }
        .profile-avatar-preview img,
        .profile-avatar-preview video {
          position: absolute;
          inset: 0;
          display: block;
          width: 100%;
          height: 100%;
          min-width: 0;
          min-height: 0;
          object-fit: contain;
          object-position: center;
        }
        .profile-picker-media img,
        .profile-picker-media video { width: 100%; height: 100%; object-fit: cover; }
        .creation-settings-modal .ant-modal-content {
          padding: 32px 34px 28px;
          overflow: hidden;
          border-radius: 18px;
          box-shadow: 0 26px 70px rgba(24, 29, 45, .22);
        }
        .creation-settings-modal .ant-modal-close {
          top: 28px;
          inset-inline-end: 28px;
        }
        .creation-settings-modal .ant-modal-header { margin-bottom: 24px; }
        .creation-settings-modal .ant-modal-body { padding: 0; }
        .creation-settings-modal .ant-modal-footer {
          margin-top: 26px;
          padding-top: 0;
          border-top: 0;
        }
        .creation-settings-title {
          display: flex;
          flex-direction: column;
          gap: 5px;
        }
        .creation-settings-title strong {
          color: #182035;
          font-size: 24px;
          line-height: 1.35;
        }
        .creation-settings-title small {
          color: #8b93a7;
          font-size: 14px;
          font-weight: 400;
        }
        .creation-settings-body {
          display: flex;
          flex-direction: column;
          gap: 18px;
        }
        .settings-summary-card {
          padding: 20px;
          border: 1px solid #e6e8ef;
          border-radius: 14px;
          background: #fff;
        }
        .settings-summary-card > .ant-typography:first-child {
          color: #252b3a;
          font-size: 16px;
        }
        .source-summary-row {
          display: flex;
          align-items: center;
          gap: 16px;
          margin-top: 14px;
        }
        .source-platform-icons {
          display: flex;
          align-items: center;
          padding-right: 4px;
        }
        .source-platform-icon {
          display: grid;
          place-items: center;
          width: 34px;
          height: 34px;
          margin-right: -4px;
          border: 3px solid #fff;
          border-radius: 50%;
          color: #fff;
          font-size: 14px;
        }
        .source-platform-icon-douyin { background: #171a22; }
        .source-platform-icon-kuaishou { background: #ff7a2f; }
        .source-platform-icon-bilibili { background: #ec4899; }
        .source-platform-icon-xiaohongshu { background: #f02f46; }
        .source-health {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          color: #5f6677;
        }
        .source-health i {
          width: 9px;
          height: 9px;
          border-radius: 50%;
          background: #f59e0b;
        }
        .source-health.ready i { background: #18b87b; }
        .settings-manage-link {
          height: auto;
          margin-left: auto;
          padding: 0;
          font-weight: 600;
        }
        .creation-settings-grid {
          display: grid;
          grid-template-columns: minmax(0, 1.55fr) minmax(230px, .75fr);
          gap: 18px;
          align-items: stretch;
        }
        .publish-settings-summary,
        .profile-settings-summary {
          min-height: 230px;
        }
        .publish-platform-chips {
          display: grid;
          grid-template-columns: repeat(5, minmax(0, 1fr));
          gap: 8px;
          margin-top: 18px;
        }
        .publish-platform-chip {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 5px;
          min-width: 0;
          min-height: 40px;
          padding: 0 8px;
          border: 1px solid #e1e4eb;
          border-radius: 10px;
          background: #fff;
          color: #343a49;
          font: inherit;
          white-space: nowrap;
          cursor: pointer;
        }
        .publish-platform-chip:hover { border-color: #b6a4f5; }
        .publish-platform-chip.selected {
          border-color: #5f2eea;
          background: #5f2eea;
          color: #fff;
          box-shadow: 0 5px 12px rgba(95, 46, 234, .18);
        }
        .publish-platform-chip:focus-visible {
          outline: 3px solid rgba(95, 46, 234, .18);
          outline-offset: 2px;
        }
        .publish-platform-chip:disabled { opacity: .45; cursor: not-allowed; }
        .publish-account-summary {
          display: flex;
          align-items: center;
          flex-wrap: wrap;
          gap: 10px;
          margin-top: 32px;
        }
        .publish-account-summary .summary-ready { color: #149b68; }
        .publish-account-summary .summary-waiting { color: #d98316; }
        .publish-login-note {
          display: block;
          margin-top: 12px;
          font-size: 13px;
        }
        .profile-settings-summary {
          display: flex;
          flex-direction: column;
        }
        .setup-profile-card {
          display: grid;
          grid-template-columns: 68px minmax(0, 1fr);
          align-items: center;
          gap: 12px;
          margin-top: 18px;
        }
        .setup-profile-avatar {
          display: grid;
          place-items: center;
          width: 68px;
          height: 68px;
          overflow: hidden;
          border-radius: 50%;
          background: #f0ecff;
          color: #6c43e8;
          font-size: 24px;
        }
        .setup-profile-avatar img,
        .setup-profile-avatar video {
          width: 100%;
          height: 100%;
          object-fit: cover;
        }
        .setup-profile-copy {
          display: flex;
          flex-direction: column;
          gap: 4px;
          min-width: 0;
        }
        .setup-profile-copy strong,
        .setup-profile-copy small {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .setup-profile-copy small { color: #8a92a5; }
        .setup-profile-card > .ant-btn {
          grid-column: 2;
          justify-self: start;
          min-width: 76px;
          margin-top: -4px;
        }
        .add-profile-link {
          align-self: flex-start;
          width: 100%;
          height: auto;
          margin-top: auto;
          padding: 16px 0 0;
          border-top: 1px solid #eceef3;
          text-align: left;
        }
        .creation-settings-footer {
          display: flex;
          justify-content: flex-end;
          gap: 12px;
        }
        .creation-settings-footer .ant-btn { min-width: 92px; height: 48px; }
        .creation-settings-footer .ant-btn-primary {
          min-width: 238px;
          background: #5f2eea;
          box-shadow: none;
        }
        .settings-manager-modal .ant-modal-content { border-radius: 16px; }
        .settings-manager-list {
          display: flex;
          flex-direction: column;
          overflow: hidden;
          border: 1px solid #e6e8ef;
          border-radius: 12px;
        }
        .settings-manager-row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto minmax(104px, auto);
          align-items: center;
          gap: 14px;
          padding: 14px 16px;
          border-bottom: 1px solid #edf0f4;
        }
        .settings-manager-row:last-child { border-bottom: 0; }
        .settings-manager-row > span:first-child {
          display: flex;
          flex-direction: column;
          gap: 4px;
          min-width: 0;
        }
        .settings-manager-row strong {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .settings-manager-row strong em {
          padding: 2px 6px;
          border-radius: 999px;
          background: #f0ebff;
          color: #6c43e8;
          font-size: 11px;
          font-style: normal;
          font-weight: 500;
        }
        .settings-manager-row small {
          overflow: hidden;
          color: #8a92a5;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .settings-manager-row > .ant-btn { min-width: 104px; }
        .profile-picker-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
        .profile-picker-card {
          display: flex;
          align-items: center;
          gap: 10px;
          width: 100%;
          padding: 10px;
          border: 1px solid #e2e4eb;
          border-radius: 12px;
          background: #fff;
          color: inherit;
          text-align: left;
          cursor: pointer;
        }
        .profile-picker-card.selected { border-color: #7652e8; background: #f7f3ff; }
        .profile-picker-card:disabled { opacity: 1; cursor: default; }
        .profile-picker-card:focus-visible { outline: 3px solid rgba(111,73,232,.2); }
        .profile-picker-card span:last-child { display: flex; flex-direction: column; min-width: 0; }
        .profile-picker-card small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #7b8190; }
        .profile-picker-media {
          display: grid;
          place-items: center;
          flex: 0 0 64px;
          width: 64px;
          height: 64px;
          overflow: hidden;
          border-radius: 10px;
          background: #f1f3f8;
          color: #7652e8;
        }
        .profile-avatar-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
          gap: 8px;
          max-height: 300px;
          margin-top: 8px;
          padding-right: 4px;
          overflow-y: auto;
        }
        .profile-avatar-section-heading {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 16px;
        }
        .profile-avatar-section-heading > span {
          display: flex;
          flex-direction: column;
          gap: 3px;
        }
        .profile-avatar-section-heading .ant-typography-secondary,
        .pending-avatar-note {
          display: block;
          margin-top: 8px;
          font-size: 12px;
        }
        .profile-avatar-section-heading .ant-typography-secondary { margin-top: 0; }
        .profile-name-field {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .profile-name-field .ant-typography-secondary { font-size: 12px; }
        .profile-avatar-card {
          display: flex;
          flex-direction: column;
          gap: 4px;
          min-width: 0;
          padding: 0 0 8px;
          overflow: hidden;
          border: 2px solid #ebeef5;
          border-radius: 12px;
          background: #fff;
          color: #262a33;
          text-align: left;
          cursor: pointer;
        }
        .profile-avatar-card.selected {
          border-color: #7652e8;
          background: #f7f3ff;
          box-shadow: 0 0 0 2px rgba(111, 73, 232, .08);
        }
        .profile-avatar-card:focus-visible { outline: 3px solid rgba(111, 73, 232, .2); }
        .profile-avatar-card strong,
        .profile-avatar-card small {
          overflow: hidden;
          padding: 0 10px;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .profile-avatar-card small { color: #7b8190; }
        .profile-avatar-card-media {
          display: grid;
          place-items: center;
          width: 100%;
          height: 108px;
          overflow: hidden;
          background: #f3efff;
          color: #7652e8;
          font-size: 28px;
        }
        .profile-avatar-card-media img,
        .profile-avatar-card-media video {
          display: block;
          width: 100%;
          height: 108px !important;
          min-height: 108px;
          max-height: 108px;
          object-fit: cover;
          object-position: center 70%;
        }
        .voice-upload-panel {
          padding: 12px;
          border: 1px solid #e9e2ff;
          border-radius: 10px;
          background: #fbf9ff;
        }
        .voice-upload-heading {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
        }
        .workspace-primary {
          height: 48px;
          margin-top: 16px;
          border: 0;
          font-weight: 700;
          background: var(--workspace-purple);
          box-shadow: none;
        }
        .workspace-primary.inline {
          width: 132px;
          height: 56px;
          margin-top: 0;
          border-radius: 12px;
          font-size: 15px;
          font-weight: 650;
          letter-spacing: .02em;
          box-shadow: 0 6px 14px rgba(108, 67, 232, .16);
        }
        .creation-filter-button {
          width: 40px;
          height: 56px;
          padding: 0;
          border-radius: 12px;
        }
        .workspace-primary-actions {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 10px;
          align-items: stretch;
        }
        .workspace-primary-actions .workspace-primary {
          margin-top: 0;
        }
        .workspace-primary-actions > .ant-btn:not(.workspace-primary) {
          min-width: 126px;
          height: 48px;
        }
        .workspace-preview-card > .ant-card-body {
          height: 100%;
          padding: 0;
        }
        .workspace-summary-rail {
          height: 100%;
          display: flex;
          flex-direction: column;
        }
        .workspace-summary-group + .workspace-summary-group {
          border-top: 1px solid #eceef3;
        }
        .workspace-fold-trigger {
          width: 100%;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          border: 0;
          background: transparent;
          color: #253047;
          text-align: left;
          cursor: pointer;
        }
        .workspace-fold-trigger:hover { background: #faf9ff; }
        .workspace-fold-trigger:focus-visible {
          outline: 3px solid rgba(108, 67, 232, .18);
          outline-offset: -3px;
        }
        .workspace-fold-title {
          min-width: 0;
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .workspace-fold-title strong { color: #202a40; font-size: 16px; }
        .workspace-fold-title small { color: #81899b; font-size: 13px; font-weight: 400; }
        .summary-fold-trigger {
          min-height: 72px;
          padding: 20px 22px;
        }
        .summary-fold-trigger .workspace-fold-title {
          align-items: flex-start;
          flex-direction: column;
          gap: 4px;
        }
        .workspace-fold-content { border-top: 1px solid #f0f1f5; }
        .material-summary-content { padding: 4px 22px 24px; }
        .selected-material-summary {
          display: grid;
          grid-template-columns: 48px minmax(0, 1fr);
          align-items: center;
          gap: 13px;
          padding-top: 16px;
        }
        .selected-material-icon {
          width: 48px;
          height: 48px;
          display: grid;
          place-items: center;
          border: 1px solid #ddd5fb;
          border-radius: 12px;
          background: #f7f4ff;
          color: var(--workspace-purple);
          font-size: 20px;
        }
        .selected-material-summary > span:last-child {
          min-width: 0;
          display: flex;
          flex-direction: column;
          gap: 5px;
        }
        .selected-material-summary strong {
          overflow: hidden;
          color: #1e293b;
          font-size: 15px;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .selected-material-summary small { color: #7e8799; font-size: 13px; }
        .cost-summary-content { padding: 0 22px 20px; }
        .workspace-fold-section {
          overflow: hidden;
          border: 1px solid #e4e6ee;
          border-radius: 14px;
          background: #fff;
        }
        .tasks-fold-trigger { min-height: 72px; padding: 0 22px; }
        .tasks-fold-trigger .workspace-fold-title strong { font-size: 17px; }
        .profile-fold-trigger { min-height: 68px; padding: 0 22px; }
        .workspace-fold-action {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          color: #6f56d9;
          font-size: 14px;
        }
        .tasks-fold-content { padding: 0 22px 14px; }
        .profile-fold-content { padding: 20px 22px; }
        .video-frame {
          overflow: hidden;
          aspect-ratio: 16 / 9;
          border-radius: 14px;
          background: #12131a;
        }
        .video-frame video { width: 100%; height: 100%; object-fit: contain; }
        .source-preview {
          min-height: 250px;
          display: flex;
          flex-direction: column;
          justify-content: center;
          align-items: flex-start;
          padding: 28px;
          border: 1px dashed #cbc4e8;
          border-radius: 14px;
          background: linear-gradient(145deg, #faf9ff, #f1f5ff);
        }
        .status-summary {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
          margin: 18px 0 20px;
        }
        .status-summary div {
          display: flex;
          flex-direction: column;
          gap: 3px;
          padding: 12px;
          border-radius: 10px;
          background: #f7f7fb;
        }
        .status-summary span { color: #7a8090; font-size: 12px; }
        .active-batch { background: #faf7ff; }
        .workbench-task-list .ant-list-item {
          gap: 10px;
          align-items: center;
          padding: 14px 0;
        }
        .workbench-task-list .ant-list-item-meta {
          min-width: 0;
        }
        .workbench-task-title {
          display: -webkit-box;
          overflow: hidden;
          -webkit-box-orient: vertical;
          -webkit-line-clamp: 2;
          line-clamp: 2;
          line-height: 1.5;
        }
        .workbench-task-footer {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          padding-top: 12px;
          border-top: 1px solid #f0f0f0;
        }
        .workbench-task-footer.start {
          justify-content: center;
          margin-top: 2px;
          padding-top: 14px;
        }
        .workbench-task-footer.start a {
          display: inline-flex;
          align-items: center;
          gap: 6px;
        }
        @media (max-width: 1100px) {
          .workspace-grid { grid-template-columns: 1fr; }
          .workspace-grid.has-workspace .workspace-action-card,
          .workspace-grid.has-workspace .workspace-preview-card,
          .workspace-grid.has-workspace .workspace-tasks-card,
          .workspace-grid.has-workspace .workspace-profile-card {
            grid-column: 1;
          }
          .workspace-grid.has-workspace .workspace-action-card { grid-row: 1; }
          .workspace-grid.has-workspace .workspace-preview-card { grid-row: 2; }
          .workspace-grid.has-workspace .workspace-tasks-card { grid-row: 3; }
          .workspace-grid.has-workspace .workspace-profile-card { grid-row: 4; }
          .workspace-grid.is-start { grid-template-columns: 1fr; }
          .workspace-grid.is-start .workspace-tasks-card,
          .workspace-grid.is-start .workspace-profile-card {
            grid-column: 1;
          }
          .workspace-grid.is-start .workspace-tasks-card { grid-row: 3; }
          .workspace-grid.is-start .workspace-profile-card { grid-row: 4; }
          .start-process {
            grid-template-columns: minmax(0, 1fr) 22px minmax(0, 1fr);
            row-gap: 16px;
          }
          .start-process > :nth-child(1) { grid-area: 1 / 1; }
          .start-process > :nth-child(2) { grid-area: 1 / 2; }
          .start-process > :nth-child(3) { grid-area: 1 / 3; }
          .start-process > :nth-child(4) { display: none; }
          .start-process > :nth-child(5) { grid-area: 2 / 1; }
          .start-process > :nth-child(6) { grid-area: 2 / 2; }
          .start-process > :nth-child(7) { grid-area: 2 / 3; }
        }
        @media (max-width: 768px) {
          .workspace-hero { align-items: flex-start; padding: 0; }
          .workspace-hero { flex-direction: column; }
          .workspace-hero h1 { font-size: 32px; }
          .workspace-stage-strip { grid-template-columns: 1fr; }
          .workspace-stage-step {
            min-height: 68px;
            border-right: 0;
            border-bottom: 1px solid #eceef3;
          }
          .workspace-stage-step:last-child { border-bottom: 0; }
          .creation-entry-row { grid-template-columns: 1fr; }
          .creation-mode-cards { grid-template-columns: 1fr; }
          .workspace-primary.inline { width: 100%; }
          .workspace-primary-actions { grid-template-columns: 1fr; }
          .workspace-primary-actions > .ant-btn:not(.workspace-primary) { width: 100%; }
          .workspace-fold-title small { display: none; }
          .start-process { grid-template-columns: 1fr; gap: 10px; }
          .start-process > :nth-child(1),
          .start-process > :nth-child(2),
          .start-process > :nth-child(3),
          .start-process > :nth-child(4),
          .start-process > :nth-child(5),
          .start-process > :nth-child(6),
          .start-process > :nth-child(7) { grid-area: auto; }
          .start-process > :nth-child(4) { display: block; }
          .start-process-arrow {
            justify-self: start;
            margin-left: 22px;
            transform: rotate(90deg);
          }
          .status-summary { grid-template-columns: 1fr; }
          .section-heading { flex-direction: column; }
          .profile-picker-grid { grid-template-columns: 1fr; }
          .profile-avatar-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
          .creation-settings-grid { grid-template-columns: 1fr; }
          .source-summary-row { flex-wrap: wrap; }
          .source-summary-row .settings-manage-link { margin-left: 0; }
          .publish-account-summary .settings-manage-link { width: 100%; margin-left: 0; text-align: left; }
          .publish-settings-summary,
          .profile-settings-summary { min-height: 0; }
          .creation-settings-footer .ant-btn-primary { min-width: 0; flex: 1; }
          .settings-manager-row { grid-template-columns: minmax(0, 1fr) auto; }
          .settings-manager-row > .ant-btn { grid-column: 1 / -1; width: 100%; }
        }
      `}</style>
    </div>
  );
}
