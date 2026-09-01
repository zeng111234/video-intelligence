import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Drawer,
  Input,
  List,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  CheckCircleOutlined,
  DeleteOutlined,
  EyeOutlined,
  FileTextOutlined,
  HistoryOutlined,
  MoreOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import {
  createCrawlerProgressiveBatch,
  createCrawlerKeywordQueue,
  downloadCrawlerBatchCsv,
  getCrawlerKeywordQueue,
  createPipelineFromCandidate,
  deleteCrawlerBatch,
  generateOriginalScript,
  getCrawlerBatch,
  getCrawlerBatchForSelection,
  probeCrawlerBatchCopy,
  getCrawlerCapabilities,
  getCrawlerHotWords,
  listCrawlerBatches,
  listCrawlerKeywordQueues,
  pauseCrawlerKeywordQueue,
  previewCrawlerCandidateMedia,
  resolveCrawlerCandidateOriginalMedia,
  recheckCrawlerBatchLegacyNoText,
  resumeCrawlerKeywordQueue,
  cancelCrawlerKeywordQueue,
  resetCrawlerBrowserLoginState,
  startCrawlerBrowserDiscovery,
} from "../api/client";
import type {
  CrawlerBatchResponse,
  CrawlerBrowserDiscoveryCapabilities,
  CrawlerCandidateMediaPreviewResponse,
  CrawlerCandidateResult,
  CrawlerCapabilitiesResponse,
  CrawlerHotWordItem,
  CrawlerKeywordQueueItem,
  CrawlerKeywordQueueResponse,
  CrawlerOriginalScriptResponse,
  CrawlerPlatformRun,
  CrawlerSearchRequest,
} from "../api/types";
import { ApiRequestError } from "../api/client";
import MaterialSearchExperience from "../components/MaterialSearchExperience";
import { useToast } from "../components/Toast";
import { useNavigate } from "react-router-dom";
import { cnyToCredits, handleCreditsError } from "../utils/credits";
import "./KeywordCrawlerPage.css";

const { Text, Title, Paragraph } = Typography;
const RECENT_RESULT_REUSE_MINUTES = 10;

async function openResolvedCandidateOriginalMedia(candidateId: string) {
  await resolveCrawlerCandidateOriginalMedia(candidateId);
  window.location.assign(
    `/api/v1/crawler/candidates/${encodeURIComponent(candidateId)}/original-media`,
  );
}

function openCandidateOriginalMedia(candidate: Pick<CrawlerCandidateResult, "platform" | "video_id" | "source_url">) {
  if (candidate.platform === "xiaohongshu") {
    return openResolvedCandidateOriginalMedia(candidate.video_id);
  }
  const sourceUrl = candidate.source_url?.trim();
  if (!sourceUrl) throw new Error("该候选没有可用的原视频链接。");
  const popup = window.open(sourceUrl, "_blank", "noopener,noreferrer");
  if (!popup) throw new Error("浏览器拦截了新窗口，请允许弹窗后重试。");
}

const STATUS_COLOR: Record<string, string> = {
  queued: "default",
  submitted: "processing",
  pending: "default",
  running: "processing",
  succeeded: "success",
  partial: "warning",
  failed: "error",
  cached: "cyan",
  blocked: "warning",
  outcome_unknown: "error",
  paused: "warning",
  cancelled: "default",
};

type HotspotWindowHours = 1 | 24 | 72 | 168;
type BrowserPlatform = "douyin" | "xiaohongshu" | "kuaishou" | "bilibili";
type MaterialPlatform = "douyin" | "xiaohongshu" | "kuaishou" | "bilibili";
type MaterialCount = 30 | 50 | 100;
type MaterialSort = "heat" | "newest" | "likes" | "comments" | "plays";
type PublishedWindowDays = CrawlerSearchRequest["published_window_days"];

interface MaterialDisplaySettings {
  sort: MaterialSort;
}

const PLATFORM_OPTIONS: Array<{ label: string; value: MaterialPlatform }> = [
  { label: "抖音", value: "douyin" },
  { label: "小红书", value: "xiaohongshu" },
  { label: "快手", value: "kuaishou" },
  { label: "B站", value: "bilibili" },
];

const MATERIAL_PLATFORM_LABELS: Record<string, string> = {
  douyin: "抖音",
  xiaohongshu: "小红书",
  kuaishou: "快手",
  bilibili: "B站",
};

const PUBLISHED_WINDOW_OPTIONS: Array<{ label: string; value: PublishedWindowDays }> = [
  { label: "不限", value: 0 },
  { label: "一天内", value: 1 },
  { label: "一周内", value: 7 },
  { label: "半年内", value: 180 },
];

const HOTSPOT_WINDOW_OPTIONS: Array<{ label: string; value: HotspotWindowHours }> = [
  { label: "近1小时", value: 1 },
  { label: "近1天", value: 24 },
  { label: "近3天", value: 72 },
  { label: "近7天", value: 168 },
];

function hotspotWindowLabel(hours: number | null | undefined) {
  return HOTSPOT_WINDOW_OPTIONS.find((item) => item.value === hours)?.label || "近7天";
}

function formatCompactMaterialMetric(value: number | null | undefined) {
  if (value === null || value === undefined) return "未返回";
  if (Math.abs(value) < 10_000) return value.toLocaleString("zh-CN");
  const compact = Math.round((value / 10_000) * 10) / 10;
  return `${compact.toLocaleString("zh-CN")}万`;
}

function formatMaterialDuration(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined || seconds <= 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)}秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return remainder ? `${minutes}分${remainder}秒` : `${minutes}分钟`;
}

function publishedTimestamp(item: CrawlerCandidateResult) {
  if (!item.published_at || item.published_at_reliable === false) return null;
  const timestamp = Date.parse(item.published_at);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function formatMaterialPublishedAt(item: CrawlerCandidateResult) {
  const timestamp = publishedTimestamp(item);
  return timestamp === null ? "—" : new Date(timestamp).toLocaleDateString("zh-CN");
}

function interactionHeat(item: CrawlerCandidateResult) {
  const metrics = [item.likes, item.comments, item.shares, item.favorites];
  if (metrics.every((value) => value === null || value === undefined)) return null;
  if (item.heat_score !== null && item.heat_score !== undefined) return item.heat_score;
  return (item.likes ?? 0) + (item.comments ?? 0) * 3 + (item.shares ?? 0) * 4 + (item.favorites ?? 0) * 4;
}

function hasPartialInteractionMetrics(item: CrawlerCandidateResult) {
  const metrics = [item.likes, item.comments, item.shares, item.favorites];
  const available = metrics.filter((value) => value !== null && value !== undefined).length;
  return available > 0 && available < metrics.length;
}

function candidateSourceUrl(candidate: CrawlerCandidateResult) {
  if (candidate.source_url) return candidate.source_url;
  if (candidate.platform !== "xiaohongshu") return null;
  const rawId = candidate.video_id.trim();
  const itemId = rawId.toLowerCase().startsWith("xiaohongshu-")
    ? rawId.slice("xiaohongshu-".length)
    : rawId;
  if (!itemId || !/^[A-Za-z0-9_-]+$/.test(itemId)) return null;
  return `https://www.xiaohongshu.com/explore/${encodeURIComponent(itemId)}`;
}

function candidateOriginalMediaHref(candidate: CrawlerCandidateResult) {
  if (candidate.platform === "xiaohongshu") {
    return `/api/v1/crawler/candidates/${encodeURIComponent(candidate.video_id)}/original-media`;
  }
  return candidateSourceUrl(candidate);
}

function hasUsableXiaohongshuShareLink(candidate: CrawlerCandidateResult) {
  const sourceUrl = candidateSourceUrl(candidate);
  if (candidate.platform !== "xiaohongshu") return Boolean(sourceUrl);
  if (!sourceUrl) return false;
  try {
    const url = new URL(sourceUrl);
    const host = url.hostname.toLowerCase();
    return url.protocol === "https:"
      && (
        host === "xhslink.com"
        || host.endsWith(".xhslink.com")
        || (
          (host === "xiaohongshu.com" || host.endsWith(".xiaohongshu.com"))
          && (
            url.pathname.includes("/explore/")
            || url.pathname.includes("/discovery/item/")
          )
        )
      );
  } catch {
    return false;
  }
}

function buildTranscriptionHref(candidate: CrawlerCandidateResult, forceUpload = false) {
  const query = new URLSearchParams({
    candidate: candidate.video_id,
    title: candidate.title,
  });
  const sourceUrl = candidateSourceUrl(candidate);
  if (sourceUrl && !forceUpload && hasUsableXiaohongshuShareLink(candidate)) {
    query.set("share_text", sourceUrl);
  } else {
    query.set("entry", "upload");
  }
  return `/transcription?${query.toString()}`;
}

const XIAOHONGSHU_MANUAL_ONLY_MESSAGE =
  "小红书公开搜索只保存标题和互动数据；原视频可能仅支持 App/登录查看，不能直接自动转写。请上传已获授权的视频，或按话题生成原创口播。";

function isXiaohongshuTopicOnly(candidate: CrawlerCandidateResult) {
  return candidate.platform === "xiaohongshu"
    && (candidate.spoken_material_status || "topic_only") === "topic_only";
}

function copyStatus(item: CrawlerCandidateResult) {
  const status = item.audio_status || "unknown";
  if (status === "speech_detected") return { filter: "detected" as const, color: "success", label: "检测到文案" };
  if (status === "checking") return { filter: "unchecked" as const, color: "processing", label: "检测中" };
  if (status === "no_audio") return { filter: "not_detected" as const, color: "error", label: "没有音轨" };
  if (status === "no_clear_speech") return { filter: "not_detected" as const, color: "warning", label: "未检测到文案" };
  if (status === "check_failed") return { filter: "not_detected" as const, color: "error", label: "检测失败" };
  return { filter: "unchecked" as const, color: "default", label: "未检测" };
}

function candidateIdentity(item: CrawlerCandidateResult) {
  return `${item.platform}:${item.video_id || item.source_url || `${item.title}:${item.author_name}`}`;
}

function keywordMatchLabel(reason: string | null | undefined) {
  if (!reason) return null;
  return /未(?:直接)?命中/.test(reason) ? "平台参考" : "命中关键词";
}

function dedupeCandidates(candidates: CrawlerCandidateResult[]) {
  const seen = new Set<string>();
  return candidates.filter((item) => {
    const identity = candidateIdentity(item);
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

function materialPlatformLabel(platform: string) {
  return MATERIAL_PLATFORM_LABELS[platform] || platform;
}

function materialMetricCoverage(candidates: CrawlerCandidateResult[]) {
  const available = (field: "plays" | "likes" | "comments" | "shares" | "favorites") => candidates.filter(
    (item) => item[field] !== null && item[field] !== undefined,
  ).length;
  return {
    plays: available("plays"),
    likes: available("likes"),
    comments: available("comments"),
    shares: available("shares"),
    favorites: available("favorites"),
  };
}

function publishedWindowLabel(days: number) {
  return PUBLISHED_WINDOW_OPTIONS.find((item) => item.value === days)?.label || `近${days}天`;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    submitted: "已提交",
    pending: "等待中",
    running: "执行中",
    succeeded: "成功",
    partial: "部分成功",
    failed: "失败",
    cached: "使用近期结果",
    blocked: "已阻断",
    outcome_unknown: "结果未知",
    paused: "已暂停",
    cancelled: "已取消",
  };
  return labels[status] || status;
}

function formatNumber(value: number | null | undefined) {
  return value === null || value === undefined ? "未返回" : value.toLocaleString("zh-CN");
}

function formatMetricCoverage(label: string, available: number, total: number) {
  return available > 0 && total > 0 ? `${label} ${available}/${total}` : `${label}：未返回`;
}

function formatLikesPerDay(value: number | null | undefined) {
  return value === null || value === undefined ? "未返回" : `${value.toFixed(1)}/天`;
}

function formatCurrency(value: number | null | undefined, _currency = "CNY") {
  if (value === null || value === undefined) {
    return "未返回";
  }
  return `${cnyToCredits(value)} 积分`;
}

const OFFICIAL_HOT_NO_MATCH_MESSAGE = "官方热门池中没有匹配，不代表抖音搜索无视频。";

function resultStateMessage(run: CrawlerPlatformRun) {
  if (run.result_state === "official_hot_no_match" || run.result_state.includes("官方热榜无匹配")) {
    return OFFICIAL_HOT_NO_MATCH_MESSAGE;
  }
  const messages: Record<string, string> = {
    provider_empty: "供应商本次返回 0 条原始候选；这不是正常的关键词搜索结果，建议先核对供应商响应与用量。",
    provider_payload_invalid: "供应商有响应但未识别出候选列表；请核对响应结构与供应商接口变更。",
    all_out_of_window: `供应商返回了 ${run.raw_item_count} 条，但全部早于本次时间范围，未额外翻页以避免增加费用。`,
    all_invalid: "供应商返回的候选全部未通过平台、链接或去重校验，请核对供应商字段。",
    all_irrelevant: `平台已返回搜索内容，但需要人工从中确认合适素材；已整理 ${run.irrelevant_count ?? 0} 条筛选信息。`,
    all_low_spoken_value: `找到了相关内容，但公开文字不足以支撑原创口播；已隐藏 ${run.low_spoken_value_count ?? 0} 条。`,
    all_below_heat_floor: `有 ${run.strict_relevant_count ?? 0} 条严格相关内容，但互动热度均低于 100，已不进入主榜。`,
    reference_only: `平台已找到 ${run.reference_count ?? run.reference_candidates?.length ?? 0} 条搜索参考，请打开原视频确认后再继续。`,
    no_hot: "已得到候选，但没有达到本产品的热门/潜力阈值；它们不会被标为爆款。",
  };
  return messages[run.result_state] || "本次没有可展示候选，请查看诊断和供应商响应。";
}

function crawlerFunnelSummary(run: CrawlerPlatformRun) {
  const raw = run.raw_discovered ?? run.raw_item_count ?? 0;
  const parsed = run.parsed ?? run.parsed_item_count ?? 0;
  const retained = run.retained ?? run.relevant_count ?? run.returned_count ?? 0;
  const drops = [
    [`不相关 ${run.relevance_filtered ?? run.irrelevant_count ?? 0}`, run.relevance_filtered ?? run.irrelevant_count ?? 0],
    [`重复 ${run.duplicate_count ?? 0}`, run.duplicate_count ?? 0],
    [`无效 ${run.invalid_fields ?? run.invalid_count ?? 0}`, run.invalid_fields ?? run.invalid_count ?? 0],
    [`时长不符 ${run.duration_filtered ?? run.duration_filtered_count ?? 0}`, run.duration_filtered ?? run.duration_filtered_count ?? 0],
    [`超出时间范围 ${run.out_of_window_count ?? 0}`, run.out_of_window_count ?? 0],
  ] as const;
  const details = drops.filter(([, count]) => count > 0).map(([label]) => label).join("、");
  return `扫描 ${formatNumber(raw)} 条，解析 ${formatNumber(parsed)} 条，筛出 ${formatNumber(retained)} 条相关素材${details ? `；过滤：${details}` : ""}`;
}

const CRAWLER_STAGE_LABELS: Record<string, string> = {
  browser_attach_or_reuse_ms: "连接/复用浏览器",
  navigation_ms: "页面导航",
  first_response_ms: "首个搜索响应（后端）",
  first_result_ms: "首个解析结果（后端）",
  scroll_loading_ms: "滚动加载",
  parse_filter_ms: "解析与过滤",
  total_ms: "采集总耗时",
};

function crawlerStageLabel(stage: string) {
  return CRAWLER_STAGE_LABELS[stage] || stage;
}

function metricEntries(item: CrawlerCandidateResult, isHotspotLeaderboard = false) {
  const entries: Array<[string, number]> = [];
  if (!isHotspotLeaderboard && item.plays !== null && item.plays !== undefined && item.plays > 0) {
    entries.push(["播放", item.plays]);
  }
  for (const [label, value] of [
    [isHotspotLeaderboard ? "新增点赞" : "点赞", isHotspotLeaderboard ? item.new_likes ?? item.likes : item.likes],
    ["评论", item.comments],
    ["分享", item.shares],
    ["收藏", item.favorites],
  ] as const) {
    if (value !== null && value !== undefined) {
      entries.push([label, value]);
    }
  }
  return entries;
}

function hotspotEvidenceSummary(evidence: string) {
  return evidence
    .split(";")
    .slice(1)
    .filter((part) => !/^(新增播放量|新增点赞量|时长秒)=/.test(part))
    .join("；");
}

export default function KeywordCrawlerPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const searchInFlightRef = useRef(false);
  const initializationStartedRef = useRef(false);
  const [capabilities, setCapabilities] = useState<CrawlerCapabilitiesResponse | null>(null);
  const [keyword, setKeyword] = useState("");
  const [platforms, setPlatforms] = useState<MaterialPlatform[]>([]);
  const [countPerPlatform, setCountPerPlatform] = useState<MaterialCount>(30);
  const [publishedWindowDays, setPublishedWindowDays] = useState<PublishedWindowDays>(0);
  const [materialSort, setMaterialSort] = useState<MaterialSort>("heat");
  const [batches, setBatches] = useState<CrawlerBatchResponse[]>([]);
  const [selectedBatch, setSelectedBatch] = useState<CrawlerBatchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [searchProgress, setSearchProgress] = useState<{
    startedAt: number;
    platforms: MaterialPlatform[];
  } | null>(null);
  const [searchResultBatch, setSearchResultBatch] = useState<CrawlerBatchResponse | null>(null);
  const [singleSearchQueueId, setSingleSearchQueueId] = useState<string | null>(null);
  const [singleSearchQueue, setSingleSearchQueue] = useState<CrawlerKeywordQueueResponse | null>(null);
  const [searchProgressError, setSearchProgressError] = useState<string | null>(null);
  const [probingCopy, setProbingCopy] = useState(false);
  const [deletingBatchId, setDeletingBatchId] = useState<string | null>(null);
  const [openingBatchId, setOpeningBatchId] = useState<string | null>(null);
  const [mediaCandidate, setMediaCandidate] = useState<CrawlerCandidateResult | null>(null);
  const [mediaPreview, setMediaPreview] = useState<CrawlerCandidateMediaPreviewResponse | null>(null);
  const [mediaPreviewOpen, setMediaPreviewOpen] = useState(false);
  const [mediaSubmitting, setMediaSubmitting] = useState(false);
  const [rightsHolder, setRightsHolder] = useState("本人/公司已授权");
  const [hotWords, setHotWords] = useState<CrawlerHotWordItem[]>([]);
  const [originalScript, setOriginalScript] = useState<{
    candidate: CrawlerCandidateResult;
    data: CrawlerOriginalScriptResponse;
  } | null>(null);
  const [originalScriptLoadingId, setOriginalScriptLoadingId] = useState<string | null>(null);
  const [historyDrawerOpen, setHistoryDrawerOpen] = useState(false);
  const [connectingPlatform, setConnectingPlatform] = useState<BrowserPlatform | null>(null);
  const [resettingPlatform, setResettingPlatform] = useState<BrowserPlatform | null>(null);
  const [keywordQueueOpen, setKeywordQueueOpen] = useState(false);
  const [keywordQueueText, setKeywordQueueText] = useState("");
  const [keywordQueue, setKeywordQueue] = useState<CrawlerKeywordQueueResponse | null>(null);
  const [keywordQueueLoading, setKeywordQueueLoading] = useState(false);
  const [initializationError, setInitializationError] = useState<string | null>(null);

  const requestPayload = useMemo<CrawlerSearchRequest>(() => ({
    keyword: keyword.trim(),
    published_window_days: publishedWindowDays,
    count_per_platform: countPerPlatform,
    force_refresh: false,
    mode: "smart",
    track_trend: false,
    target_main_count: countPerPlatform,
    allow_paid_fallback: false,
    platforms,
  }), [countPerPlatform, keyword, platforms, publishedWindowDays]);
  const keywordLength = requestPayload.keyword.length;
  const canSearch = keywordLength >= 1 && keywordLength <= 50 && platforms.length > 0;
  const keywordHelp =
    keywordLength === 0
      ? "输入一个词，马上开始找素材。"
      : keywordLength > 50
        ? "关键词最多 50 个字符。"
        : "请选择至少一个平台。";
  const materialDisplaySettings = useMemo<MaterialDisplaySettings>(() => ({
    sort: materialSort,
  }), [materialSort]);
  const browserConnections = useMemo(
    () => capabilities?.platform_browsers ?? [],
    [capabilities],
  );

  const upsertBatch = useCallback((batch: CrawlerBatchResponse) => {
    setBatches((current) => [batch, ...current.filter((item) => item.batch_id !== batch.batch_id)]
      .sort((left, right) => (right.created_at || "").localeCompare(left.created_at || ""))
      .slice(0, 20));
  }, []);

  const loadBatches = useCallback(async (): Promise<unknown | null> => {
    setLoading(true);
    try {
      const list = await listCrawlerBatches();
      setBatches(list.items);
      return null;
    } catch (err) {
      return err;
    } finally {
      setLoading(false);
    }
  }, []);

  const loadSecondaryData = useCallback(async (): Promise<unknown | null> => {
    try {
      const caps = await getCrawlerCapabilities();
      setCapabilities(caps);
    } catch (err) {
      // The history table remains usable when the local browser status is
      // temporarily unavailable; the caller aggregates the root cause once.
      return err;
    }

    // Hot-word suggestions are optional and must never delay the main page.
    void getCrawlerHotWords()
      .then((response) => setHotWords(response.words))
      .catch(() => setHotWords([]));
    return null;
  }, []);

  const handleSendCandidateToWorkspace = (
    batchId: string,
    candidate: CrawlerCandidateResult,
  ) => {
    if (candidate.selection_tier === "reserve") {
      toast.info("这条是平台搜索参考，请先打开原视频确认相关性后再进入智能创作。" );
      return;
    }
    const query = new URLSearchParams({
      crawler_batch_id: batchId,
      candidate_id: candidate.video_id,
    });
    navigate(`/pipeline?${query.toString()}`);
  };

  const runInitialLoad = useCallback(async () => {
    const [historyError, capabilityError] = await Promise.all([
      loadBatches(),
      loadSecondaryData(),
    ]);
    const errors = [historyError, capabilityError].filter(Boolean);
    if (!errors.length) {
      setInitializationError(null);
      return;
    }
    const uniqueMessages = Array.from(new Set(errors.map((error) => {
      if (error instanceof ApiRequestError && error.status === 403) {
        return error.message;
      }
      return error instanceof Error ? error.message : "页面初始化失败，请重新加载。";
    })));
    setInitializationError(uniqueMessages.join("；"));
  }, [loadBatches, loadSecondaryData]);

  const retryInitialLoad = useCallback(() => {
    setInitializationError(null);
    void runInitialLoad();
  }, [runInitialLoad]);

  useEffect(() => {
    // React StrictMode may replay the effect. Keep one request round and let
    // the explicit retry button start a new round after a real user action.
    if (initializationStartedRef.current) return;
    initializationStartedRef.current = true;
    void runInitialLoad();
  }, [runInitialLoad]);

  const finishSearchReveal = useCallback((batch: CrawlerBatchResponse) => {
    setSelectedBatch(batch);
    upsertBatch(batch);
    setSearchResultBatch(null);
    setSearchProgressError(null);
    setSearchProgress(null);
    setSingleSearchQueueId(null);
    setSingleSearchQueue(null);
    setSubmitting(false);
    searchInFlightRef.current = false;
    toast.success("已找到素材；可排序并点选查看。");
  }, [toast, upsertBatch]);

  const handleSearch = async (
    keywordOverride?: string,
    forceRefresh = false,
    platformOverride?: MaterialPlatform[],
    filterOverrides?: Pick<CrawlerSearchRequest, "count_per_platform" | "published_window_days">,
  ) => {
    if (searchInFlightRef.current) {
      toast.info("正在找素材，请稍等，不要重复提交。");
      return;
    }
    const searchKeyword = (keywordOverride ?? keyword).trim();
    const searchPlatforms = platformOverride ?? platforms;
    if (searchKeyword.length < 1 || searchKeyword.length > 50) {
      toast.warning("关键词需为 1–50 个字符");
      return;
    }
    if (!searchPlatforms.length) {
      toast.warning("请至少选择一个平台");
      return;
    }
    const payload: CrawlerSearchRequest = {
      ...requestPayload,
      keyword: searchKeyword,
      platforms: searchPlatforms,
      force_refresh: forceRefresh,
      ...filterOverrides,
    };
    searchInFlightRef.current = true;
    setSubmitting(true);
    setKeyword(searchKeyword);
    setSearchResultBatch(null);
    setSingleSearchQueueId(null);
    setSingleSearchQueue(null);
    setSearchProgress({ startedAt: Date.now(), platforms: [...searchPlatforms] });
    try {
      const task = await createCrawlerProgressiveBatch(payload);
      if ((task as { progressive_task?: boolean }).progressive_task) {
        setSingleSearchQueueId(task.queue_id);
        setSingleSearchQueue(task.queue);
      } else {
        // Keeps test doubles and any older local client override compatible.
        setSearchResultBatch(task as unknown as CrawlerBatchResponse);
      }
    } catch (err) {
      if (!handleCreditsError(err, () => navigate("/admin"))) {
        toast.error((err as Error).message);
      }
      searchInFlightRef.current = false;
      setSubmitting(false);
      setSearchProgress(null);
      setSearchResultBatch(null);
      setSearchProgressError(null);
      setSingleSearchQueueId(null);
      setSingleSearchQueue(null);
    }
  };

  useEffect(() => {
    if (!singleSearchQueueId) return undefined;
    let active = true;
    const sync = async () => {
      try {
        const queue = await getCrawlerKeywordQueue(singleSearchQueueId);
        if (!active) return;
        const item = queue.items[0];
        const terminal = ["succeeded", "partial", "failed", "cancelled", "paused"].includes(queue.status);
        // Keep the last running queue visible while the durable terminal batch
        // is fetched. Publishing the terminal queue first lets the reveal
        // component finalize the previous in-progress snapshot prematurely.
        if (!terminal) setSingleSearchQueue(queue);
        const progressBatch = item ? buildProgressiveBatch(queue, item) : null;
        const snapshots: CrawlerBatchResponse[] = [];
        if (item?.batch_id || item?.partial_batch_ids?.length) {
          const ids = [...new Set(
            item.partial_batch_ids?.length
              ? item.partial_batch_ids
              : item.batch_id
                ? [item.batch_id]
                : [],
          )];
          const results = await Promise.allSettled(ids.map((id) => getCrawlerBatch(id)));
          results.forEach((result) => {
            if (result.status === "fulfilled") snapshots.push(result.value);
          });
          if (results.some((result) => result.status === "rejected")) {
            setSearchProgressError((current) => current || "部分旧结果已失效，已保留仍能读取的素材。");
          }
        }
        const partial = mergeCrawlerProgressSnapshots([
          ...(progressBatch ? [progressBatch] : []),
          ...snapshots,
        ]);
        if (active && partial) setSearchResultBatch(partial);
        if (terminal) {
          setSingleSearchQueue(queue);
          searchInFlightRef.current = false;
          setSubmitting(false);
          setSingleSearchQueueId(null);
          if (!item?.batch_id && !progressBatch) {
            setSearchProgressError((current) => current || item?.progress_message || item?.error || "找素材没有完成，请稍后重试。");
          }
        }
      } catch (err) {
        if (active) setSearchProgressError((current) => current || `找素材进度读取失败：${(err as Error).message}`);
      }
    };
    void sync();
    const timer = window.setInterval(() => void sync(), 1500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [singleSearchQueueId]);

  useEffect(() => {
    let active = true;
    void listCrawlerKeywordQueues().then((queues) => {
      if (!active) return;
      const queue = queues.find(
        (item) => item.items.length === 1 && ["queued", "running"].includes(item.status),
      );
      if (!queue) return;
      const item = queue.items[0];
      setKeyword(item.keyword);
      setSingleSearchQueue(queue);
      setSingleSearchQueueId(queue.queue_id);
      setSearchProgress({
        startedAt: item.started_at ? new Date(item.started_at).getTime() : Date.now(),
        platforms: queue.platforms as MaterialPlatform[],
      });
      searchInFlightRef.current = true;
      setSubmitting(true);
    }).catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  const handleProbeCopy = async (batch: CrawlerBatchResponse) => {
    const isLegacyRecheck = (batch.copy_probe_recheckable_count ?? 0) > 0;
    setProbingCopy(true);
    try {
      const checked = isLegacyRecheck
        ? await recheckCrawlerBatchLegacyNoText(batch.batch_id)
        : await probeCrawlerBatchCopy(batch.batch_id);
      setSelectedBatch(checked);
      upsertBatch(checked);
      toast.success(
        isLegacyRecheck
          ? "已复查旧的未识别候选；没有重新搜索平台"
          : "已完成前10秒文案检测；可在表格中查看",
      );
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setProbingCopy(false);
    }
  };

  const handleStartBrowserConnection = async (connection: CrawlerBrowserDiscoveryCapabilities) => {
    const platform = connection.platform as BrowserPlatform | undefined;
    if (!platform || !connection.enabled) {
      toast.warning(`${connection.platform_label || "该平台"}暂时无法连接`);
      return;
    }
    setConnectingPlatform(platform);
    try {
      const started = await startCrawlerBrowserDiscovery(platform);
      setCapabilities((current) => {
        if (!current) return current;
        return {
          ...current,
          platform_browsers: (current.platform_browsers ?? []).map((item) => (
            item.platform === platform ? started : item
          )),
        };
      });
      toast.success(
        started.running || started.ready_to_crawl
          ? `${started.platform_label}官方窗口已打开；完成登录或验证后即可选择找素材`
          : `${started.platform_label}官方窗口正在打开；请在窗口中处理登录或验证后再选择`,
      );
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setConnectingPlatform(null);
    }
  };

  const handleResetBrowserLoginState = (connection: CrawlerBrowserDiscoveryCapabilities) => {
    const platform = connection.platform as BrowserPlatform | undefined;
    if (!platform || !connection.enabled) {
      return;
    }
    Modal.confirm({
      title: `重置${connection.platform_label || "该平台"}登录状态？`,
      content: "这只会退出该平台的专用浏览器登录状态，不会删除浏览器资料；完成后需要你人工重新登录。",
      okText: "退出并重置",
      cancelText: "取消",
      onOk: async () => {
        setResettingPlatform(platform);
        try {
          const reset = await resetCrawlerBrowserLoginState(platform);
          const refreshed = await getCrawlerCapabilities();
          setCapabilities(refreshed);
          toast.success(reset.message);
        } catch (err) {
          toast.error((err as Error).message);
        } finally {
          setResettingPlatform(null);
        }
      },
    });
  };

  const handleDeleteBatch = (batch: CrawlerBatchResponse) => {
    Modal.confirm({
      title: "删除这条历史批次？",
      content: `将删除“${batch.keyword}”的本次搜索记录和关联运行记录；候选视频数据会保留。`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => {
        setDeletingBatchId(batch.batch_id);
        setBatches((current) => current.filter((item) => item.batch_id !== batch.batch_id));
        void deleteCrawlerBatch(batch.batch_id)
          .then(() => {
            if (selectedBatch?.batch_id === batch.batch_id) {
              setSelectedBatch(null);
            }
            toast.success("历史批次已删除");
          })
          .catch((err) => {
            upsertBatch(batch);
            toast.error(`删除失败，已恢复记录：${(err as Error).message}`);
          })
          .finally(() => setDeletingBatchId(null));
      },
    });
  };

  const handleOpenBatch = async (batch: CrawlerBatchResponse) => {
    if (openingBatchId) return; // 防止连点同一批/不同批导致状态错乱
    setOpeningBatchId(batch.batch_id);
    try {
      // 打开历史批次只需要候选和平台状态，先走轻量选择视图；
      // 转写、趋势等运行时字段在后续点选素材时再处理，避免大批次逐条扫描。
      setSelectedBatch(await getCrawlerBatchForSelection(batch.batch_id));
      setKeyword(batch.keyword);
      setHistoryDrawerOpen(false);
    } catch (err) {
      const message = (err as Error).message || String(err);
      toast.error(`打开详情失败：${message}`);
    } finally {
      setOpeningBatchId(null);
    }
  };

  const handleExportBatch = async (batch: CrawlerBatchResponse) => {
    try {
      await downloadCrawlerBatchCsv(batch.batch_id);
      toast.success("CSV 已导出；只包含公开素材字段。");
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const handleCreateKeywordQueue = async () => {
    if (!keywordQueueText.trim()) {
      toast.warning("请粘贴至少一个关键词，每行一个即可。");
      return;
    }
    if (!platforms.length) {
      toast.warning("请先选择至少一个平台。");
      return;
    }
    setKeywordQueueLoading(true);
    try {
      const created = await createCrawlerKeywordQueue({
        keywords: keywordQueueText,
        platforms,
        published_window_days: publishedWindowDays,
        count_per_platform: countPerPlatform,
      });
      setKeywordQueue(created);
      setKeywordQueueText("");
      toast.success(`已建立 ${created.total} 个关键词的找素材任务；会按低频顺序执行。`);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setKeywordQueueLoading(false);
    }
  };

  const handleOpenKeywordQueue = async () => {
    setKeywordQueueOpen(true);
    setKeywordQueueLoading(true);
    try {
      const queues = await listCrawlerKeywordQueues();
      const active = queues.find((item) => ["queued", "running", "paused"].includes(item.status));
      setKeywordQueue(active || queues[0] || null);
    } catch (err) {
      toast.error(`批量任务读取失败：${(err as Error).message}`);
    } finally {
      setKeywordQueueLoading(false);
    }
  };

  const refreshKeywordQueue = useCallback(async () => {
    if (!keywordQueue) return;
    try {
      setKeywordQueue(await getCrawlerKeywordQueue(keywordQueue.queue_id));
    } catch (err) {
      toast.error(`批量进度读取失败：${(err as Error).message}`);
    }
  }, [keywordQueue, toast]);

  useEffect(() => {
    if (!keywordQueue || !["queued", "running"].includes(keywordQueue.status)) return undefined;
    const timer = window.setInterval(() => void refreshKeywordQueue(), 2000);
    return () => window.clearInterval(timer);
  }, [keywordQueue, refreshKeywordQueue]);

  const handleKeywordQueueAction = async (
    action: "pause" | "resume" | "cancel",
  ) => {
    if (!keywordQueue) return;
    setKeywordQueueLoading(true);
    try {
      const update = action === "pause"
        ? await pauseCrawlerKeywordQueue(keywordQueue.queue_id)
        : action === "resume"
          ? await resumeCrawlerKeywordQueue(keywordQueue.queue_id)
          : await cancelCrawlerKeywordQueue(keywordQueue.queue_id);
      setKeywordQueue(update);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setKeywordQueueLoading(false);
    }
  };

  const handleGenerateOriginalScript = async (candidate: CrawlerCandidateResult) => {
    setOriginalScriptLoadingId(candidate.video_id);
    try {
      const data = await generateOriginalScript(candidate.video_id);
      setOriginalScript({ candidate, data });
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setOriginalScriptLoadingId(null);
    }
  };

  const handleOpenCandidateMedia = async (candidate: CrawlerCandidateResult) => {
    if (candidate.media_transcription_task_id) {
      navigate(`/transcription?task=${encodeURIComponent(candidate.media_transcription_task_id)}`);
      return;
    }
    setMediaSubmitting(true);
    setMediaCandidate(candidate);
    try {
      const resp = await previewCrawlerCandidateMedia(candidate.video_id);
      setMediaPreview(resp);
      setMediaPreviewOpen(true);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setMediaSubmitting(false);
    }
  };

  const handleCreateCandidateTranscription = async () => {
    if (!mediaCandidate || !mediaPreview) return;
    if (!rightsHolder.trim()) {
      toast.warning("请填写权利主体");
      return;
    }
    setMediaSubmitting(true);
    try {
      const run = await createPipelineFromCandidate({
        candidate_id: mediaCandidate.video_id,
        rights_holder: rightsHolder.trim(),
        rights_confirmed: true,
        idempotencyKey: [
          "pipeline",
          mediaCandidate.video_id,
          Date.now(),
          Math.random().toString(16).slice(2),
        ].join("-"),
        target_length: 300,
        tone: "casual",
        variant_count: 2,
      });
      if (run.status === "paused") {
        toast.success("已提取转写并生成待审核文案");
      } else if (run.status === "failed") {
        toast.warning(run.error_message || "流水线未完成，请查看生产批次详情");
      } else {
        toast.success("生产流水线已创建");
      }
      setMediaPreviewOpen(false);
      navigate(`/pipeline?run=${encodeURIComponent(run.run_id)}`);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setMediaSubmitting(false);
    }
  };

  const selectedBatchPlatforms = selectedBatch?.platforms?.length
    ? selectedBatch.platforms
    : selectedBatch?.platform_runs.map((run) => run.platform) || [];

  return (
    <div className="keyword-crawler-page">
      <header className="crawler-page-heading">
        <div>
          <Title level={4}>找素材</Title>
          <Text type="secondary">本页找素材不消耗积分，也不会自动开启付费补充。</Text>
        </div>
        <Space>
          <Button onClick={() => void handleOpenKeywordQueue()}>批量找素材</Button>
          <Button icon={<HistoryOutlined />} onClick={() => setHistoryDrawerOpen(true)}>
            历史记录{batches.length ? ` ${batches.length}` : ""}
          </Button>
        </Space>
      </header>

      {initializationError && (
        <Alert
          className="crawler-initialization-alert"
          type="error"
          showIcon
          message="页面暂时没有完全加载"
          description={initializationError}
          action={<Button size="small" onClick={retryInitialLoad}>重新加载</Button>}
        />
      )}

      <div className="crawler-workspace">
        <aside className="crawler-control-rail" aria-label="找素材设置">
          <section className="crawler-control-section crawler-keyword-section">
            <Text type="secondary">关键词</Text>
            <Input
              prefix={<SearchOutlined />}
              value={keyword}
              disabled={submitting}
              onChange={(event) => setKeyword(event.target.value)}
              onPressEnter={() => void handleSearch()}
              placeholder="例如：餐饮获客"
              allowClear
              status={keywordLength > 0 && !canSearch ? "error" : undefined}
            />
            <Tooltip title={!canSearch ? keywordHelp : "直接开始找素材"}>
              <Button
                block
                type="primary"
                loading={submitting}
                disabled={!canSearch}
                onClick={() => void handleSearch()}
              >
                找素材
              </Button>
            </Tooltip>
            {hotWords.length > 0 && (
              <div className="crawler-hot-words">
                {hotWords.slice(0, 4).map((item) => (
                  <Button key={item.word} type="link" size="small" onClick={() => void handleSearch(item.word)}>
                    {item.word}
                  </Button>
                ))}
              </div>
            )}
          </section>

          <section className="crawler-control-section">
            <Text strong>搜索范围</Text>
            <div className="crawler-platform-list">
              {PLATFORM_OPTIONS.map((option) => {
                const connection = browserConnections.find((item) => item.platform === option.value);
                const searchReady = Boolean(connection?.running && !connection.login_required && connection.ready_to_crawl);
                const needsAttention = Boolean(connection?.enabled && !searchReady);
                const status = !connection
                  ? "状态加载中"
                  : !connection.enabled
                    ? "未启用"
                    : searchReady
                      ? "可搜索"
                      : connection.login_required
                        ? "待登录"
                        : connection.running
                          ? "浏览器准备中"
                          : "未登录";
                return (
                  <div className="crawler-platform-row" key={option.value}>
                    <Checkbox
                      aria-label={option.label}
                      checked={platforms.includes(option.value)}
                      disabled={!searchReady}
                      onChange={(event) => setPlatforms((current) => (
                        event.target.checked
                          ? [...current, option.value]
                          : current.filter((item) => item !== option.value)
                      ))}
                    />
                    <span className={`crawler-platform-status${needsAttention ? " warning" : searchReady ? " ready" : ""}`} />
                    <Text className="crawler-platform-name">{option.label}</Text>
                    <Text type="secondary" className="crawler-platform-state">{status}</Text>
                    {connection?.enabled && !searchReady && (
                      <>
                        <Button
                          type="link"
                          size="small"
                          loading={connectingPlatform === option.value}
                          onClick={() => void handleStartBrowserConnection(connection)}
                        >
                          登录处理
                        </Button>
                        {connection.login_reset_available === true && (
                          <Button
                            type="link"
                            size="small"
                            danger
                            loading={resettingPlatform === option.value}
                            onClick={() => handleResetBrowserLoginState(connection)}
                          >
                            重置登录
                          </Button>
                        )}
                      </>
                    )}
                  </div>
                );
              })}
            </div>
            <Text type="secondary" className="crawler-platform-filter-note">
              {capabilities?.crawler_safety_policy ||
                "同平台不额外冷却；同平台仍一次只运行一个任务，遇到验证码或访问异常会自动暂停。"}
            </Text>
          </section>

          <section className="crawler-control-section crawler-target-section">
            <Text strong>筛选条件</Text>
            <div className="crawler-rule-field">
              <Text type="secondary">发布时间</Text>
              <Select<PublishedWindowDays>
                aria-label="发布时间"
                value={publishedWindowDays}
                onChange={setPublishedWindowDays}
                options={PUBLISHED_WINDOW_OPTIONS}
              />
            </div>
            <div className="crawler-rule-field">
              <Text type="secondary">每平台目标</Text>
              <Select<MaterialCount>
                aria-label="每平台目标"
                value={countPerPlatform}
                onChange={setCountPerPlatform}
                options={[
                  { value: 30, label: "30 条" },
                  { value: 50, label: "50 条" },
                  { value: 100, label: "100 条" },
                ]}
              />
            </div>
            {platforms.includes("kuaishou") && publishedWindowDays !== 0 && (
              <Text type="secondary" className="crawler-platform-filter-note">
                快手不支持发布时间筛选，将按不限时间搜索。
              </Text>
            )}
          </section>

          {selectedBatch && (
            <section className="crawler-control-section crawler-current-search" aria-label="本次搜索摘要">
              <Text strong>本次搜索</Text>
              <div className="crawler-current-search-row">
                <Text type="secondary">关键词</Text>
                <Text strong ellipsis={{ tooltip: selectedBatch.keyword }}>{selectedBatch.keyword}</Text>
              </div>
              <div className="crawler-current-search-row">
                <Text type="secondary">平台</Text>
                <Text>{(selectedBatch.platforms ?? []).map(materialPlatformLabel).join("、") || "以实际返回为准"}</Text>
              </div>
              <div className="crawler-current-search-row">
                <Text type="secondary">时间</Text>
                <Text>{publishedWindowLabel(selectedBatch.published_window_days)}</Text>
              </div>
              <div className="crawler-current-search-row">
                <Text type="secondary">结果</Text>
                <Text>{selectedBatch.total_candidates} 条</Text>
              </div>
              <Button size="small" onClick={() => void handleExportBatch(selectedBatch)}>
                导出 CSV
              </Button>
            </section>
          )}

        </aside>

        <main className="crawler-results-area">
          {searchProgress ? (
            <MaterialSearchExperience
              keyword={keyword}
              platforms={searchProgress.platforms}
              startedAt={searchProgress.startedAt}
              batch={searchResultBatch}
              complete={singleSearchQueueId
                ? Boolean(singleSearchQueue?.items[0]?.batch_id && singleSearchQueue && ["succeeded", "partial", "failed", "cancelled", "paused"].includes(singleSearchQueue.status))
                : Boolean(searchResultBatch)}
              progress={singleSearchQueue?.items[0]}
              targetCount={singleSearchQueue?.count_per_platform || 30}
              progressError={searchProgressError}
              onCandidateClick={(candidate) => void handleOpenCandidateMedia(candidate)}
              onRevealComplete={finishSearchReveal}
            />
          ) : selectedBatch ? (
            <BatchDetail
              batch={selectedBatch}
              onResolveMedia={handleOpenCandidateMedia}
              onSendToWorkspace={handleSendCandidateToWorkspace}
              onGenerateOriginalScript={handleGenerateOriginalScript}
              originalScriptLoadingId={originalScriptLoadingId}
              onProbeCopy={handleProbeCopy}
              copyProbeLoading={probingCopy}
               onRefreshSearch={() => void handleSearch(
                selectedBatch.keyword,
                true,
                selectedBatchPlatforms as MaterialPlatform[],
                {
                  count_per_platform: selectedBatch.count_per_platform as MaterialCount,
                  published_window_days: selectedBatch.published_window_days as PublishedWindowDays,
                },
               )}
              onReconnectPlatform={(platform) => {
                const connection = browserConnections.find((item) => item.platform === platform);
                if (connection) void handleStartBrowserConnection(connection);
              }}
              materialDisplaySettings={materialDisplaySettings}
              onSortChange={setMaterialSort}
            />
          ) : (
            <section className="crawler-recent-searches">
              <div className="crawler-section-heading">
                <Title level={5}>最近搜索</Title>
              </div>
              <List
                loading={loading}
                dataSource={batches}
                pagination={{
                  pageSize: 6,
                  hideOnSinglePage: true,
                  showSizeChanger: false,
                  showTotal: (total) => `共 ${total} 条`,
                }}
                locale={{ emptyText: "还没有搜索记录，先在左侧输入关键词。" }}
                renderItem={(batch) => (
                  <List.Item
                    actions={[<Button key="open" type="link" loading={openingBatchId === batch.batch_id} onClick={() => void handleOpenBatch(batch)}>详情</Button>]}
                  >
                    <List.Item.Meta
                      title={batch.keyword}
                      description={`${batch.created_at ? new Date(batch.created_at).toLocaleString("zh-CN") : "时间未返回"} · ${batch.total_candidates} 条素材`}
                    />
                    <Tag color={STATUS_COLOR[batch.status]}>{statusLabel(batch.status)}</Tag>
                  </List.Item>
                )}
              />
            </section>
          )}
        </main>
      </div>

      <Drawer
        title="历史记录"
        open={historyDrawerOpen}
        onClose={() => setHistoryDrawerOpen(false)}
        width={520}
        rootClassName="crawler-history-drawer"
      >
        <List
          loading={loading}
          dataSource={batches}
          pagination={{
            pageSize: 8,
            hideOnSinglePage: true,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 条`,
          }}
          locale={{ emptyText: "还没有搜索记录。" }}
          renderItem={(batch) => (
            <List.Item
              actions={[
                <Button key="open" type="link" icon={<EyeOutlined />} loading={openingBatchId === batch.batch_id} onClick={() => void handleOpenBatch(batch)}>查看</Button>,
                <Tooltip key="delete" title="删除记录">
                  <Button
                    type="text"
                    danger
                    aria-label={`删除 ${batch.keyword}`}
                    icon={<DeleteOutlined />}
                    loading={deletingBatchId === batch.batch_id}
                    onClick={() => handleDeleteBatch(batch)}
                  />
                </Tooltip>,
              ]}
            >
              <List.Item.Meta
                title={<Space size={8}><Text strong>{batch.keyword}</Text><Tag color={STATUS_COLOR[batch.status]}>{statusLabel(batch.status)}</Tag></Space>}
                description={`${batch.created_at ? new Date(batch.created_at).toLocaleString("zh-CN") : "时间未返回"} · ${batch.total_candidates} 条素材`}
              />
            </List.Item>
          )}
        />
      </Drawer>

      <Modal
        title="批量找素材"
        open={keywordQueueOpen}
        onCancel={() => setKeywordQueueOpen(false)}
        footer={null}
        destroyOnClose={false}
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">
            每行一个关键词，也可以用逗号分隔；系统会自动去重，最多一次处理 20 个。会复用当前已选平台和数量，不会导出登录信息。
          </Text>
          <Input.TextArea
            aria-label="批量关键词"
            value={keywordQueueText}
            onChange={(event) => setKeywordQueueText(event.target.value)}
            placeholder="餐饮获客\n门店短视频\n老板 IP"
            autoSize={{ minRows: 5, maxRows: 10 }}
            disabled={Boolean(keywordQueue && ["queued", "running"].includes(keywordQueue.status))}
          />
          <Space wrap>
            <Text type="secondary">当前平台：{platforms.map(materialPlatformLabel).join("、") || "未选择"}</Text>
            <Text type="secondary">每个平台：{countPerPlatform} 条</Text>
          </Space>
          {!keywordQueue && (
            <Button type="primary" onClick={() => void handleCreateKeywordQueue()} loading={keywordQueueLoading}>
              建立批量找素材任务
            </Button>
          )}
          {keywordQueue && (
            <Space direction="vertical" style={{ width: "100%" }}>
              <Alert
                type={keywordQueue.status === "failed" ? "error" : keywordQueue.status === "partial" ? "warning" : "info"}
                showIcon
                message={`已完成 ${keywordQueue.completed}/${keywordQueue.total} 个关键词 · ${statusLabel(keywordQueue.status)}`}
                description={keywordQueue.message || "每个关键词完成后会自动保存；刷新页面也能继续查看。"}
              />
              <List
                size="small"
                dataSource={keywordQueue.items}
                renderItem={(item) => (
                  <List.Item>
                    <Space>
                      <Tag color={STATUS_COLOR[item.status]}>{statusLabel(item.status)}</Tag>
                      <Text>{item.keyword}</Text>
                      {item.error && <Text type="danger">{item.error}</Text>}
                    </Space>
                  </List.Item>
                )}
              />
              <Space>
                {(keywordQueue.status === "queued" || (keywordQueue.status === "running" && keywordQueue.worker_active !== false)) && (
                  <Button onClick={() => void handleKeywordQueueAction("pause")} loading={keywordQueueLoading}>暂停</Button>
                )}
                {(keywordQueue.status === "paused" || keywordQueue.status === "failed" || keywordQueue.status === "partial" || (keywordQueue.status === "running" && keywordQueue.worker_active === false)) && (
                  <Button type="primary" onClick={() => void handleKeywordQueueAction("resume")} loading={keywordQueueLoading}>继续</Button>
                )}
                {!['succeeded', 'cancelled'].includes(keywordQueue.status) && (
                  <Button danger onClick={() => void handleKeywordQueueAction("cancel")} loading={keywordQueueLoading}>取消</Button>
                )}
                <Button onClick={() => setKeywordQueue(null)}>新建一批</Button>
              </Space>
            </Space>
          )}
        </Space>
      </Modal>

      <Modal
        title="生成文案"
        open={originalScript !== null}
        onCancel={() => setOriginalScript(null)}
        footer={
          <Button type="primary" onClick={() => setOriginalScript(null)}>
            关闭
          </Button>
        }
      >
        {originalScript && (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Alert
              type="warning"
              showIcon
              message="生成说明"
              description="文案已按数字人口播的短句节奏生成；请在使用前核对其中的事实、观点与表达。"
            />
            <Text strong>{originalScript.candidate.title}</Text>
            <Paragraph
              style={{
                whiteSpace: "pre-wrap",
                background: "#fafafa",
                padding: 12,
                borderRadius: 6,
                marginBottom: 0,
              }}
            >
              {originalScript.data.script}
            </Paragraph>
            {originalScript.data.needs_manual_review && <Tag color="warning">需要人工复核后再使用</Tag>}
          </Space>
        )}
      </Modal>

      <Modal
        title="确认单条媒体解析与转写"
        open={mediaPreviewOpen}
        onCancel={() => setMediaPreviewOpen(false)}
        onOk={handleCreateCandidateTranscription}
        okText="确认有权并提取文案"
        cancelText="取消"
        confirmLoading={mediaSubmitting}
        okButtonProps={{
          disabled: !mediaPreview?.resolvable || !rightsHolder.trim(),
        }}
      >
        {mediaCandidate && mediaPreview && (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Alert
              type={mediaPreview.resolvable ? "info" : "warning"}
              showIcon
              message={mediaCandidate.title}
              description={
                mediaPreview.block_reason
                  || `来源：${mediaPreview.source === "direct_url" ? "已有授权直链" : mediaPreview.provider}；预计费用 ${formatCurrency(mediaPreview.estimated_cost_cny)}；本地共享预算 ${formatCurrency(mediaPreview.monthly_budget_used_cny)} / ${formatCurrency(mediaPreview.monthly_budget_limit_cny)}`
              }
            />
            {mediaPreview.existing_task_id && (
              <Button
                type="link"
                onClick={() => navigate(`/transcription?task=${encodeURIComponent(mediaPreview.existing_task_id || "")}`)}
              >
                查看已有转写任务
              </Button>
            )}
            <Input
              aria-label="权利主体"
              value={rightsHolder}
              onChange={(event) => setRightsHolder(event.target.value)}
              prefix="权利主体"
              placeholder="填写授权主体或公司名称"
            />
            <Text type="secondary">
              点击“确认有权并提取文案”即确认拥有本次处理权。临时媒体地址只会在后端读取并立即用于转写，不会展示、保存或批量下载。
            </Text>
            <Alert
              type="info"
              showIcon
              message="转写使用准确率优先模型 large-v3-turbo"
              description="比快速预览更慢，但更适合后续校对、压缩口播稿和数字人生成。"
            />
          </Space>
        )}
      </Modal>
    </div>
  );
}

function BatchDetail({
  batch,
  onResolveMedia,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
  onProbeCopy,
  copyProbeLoading,
  onRefreshSearch,
  onReconnectPlatform,
  materialDisplaySettings,
  onSortChange,
}: {
  batch: CrawlerBatchResponse;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
  onProbeCopy: (batch: CrawlerBatchResponse) => void;
  copyProbeLoading: boolean;
  onRefreshSearch: () => void;
  onReconnectPlatform: (platform: string) => void;
  materialDisplaySettings: MaterialDisplaySettings;
  onSortChange: (value: MaterialSort) => void;
}) {
  const isHotspotBatch = batch.provider === "douyin_local_browser" || batch.monitoring_policy === "hotspot_single_snapshot_v1";
  const isFreeMultiPlatformBatch = batch.monitoring_policy === "free_single_snapshot_v1";
  const isSingleSnapshotBatch = isHotspotBatch || isFreeMultiPlatformBatch;
  const recheckableCount = batch.copy_probe_recheckable_count ?? 0;
  const hasFinishedCopyProbe = (batch.copy_probe_attempt_count ?? 0) > 0;
  const batchPlatforms = (batch.platforms?.length
    ? batch.platforms
    : Array.from(new Set(batch.platform_runs.map((run) => run.platform))))
    .map(materialPlatformLabel);
  const copyProbeActionLabel = recheckableCount > 0
    ? `复查未识别候选（前10秒）`
    : hasFinishedCopyProbe ? "重新检测文案（前10秒）" : "检测文案（前10秒）";

  if (!isFreeMultiPlatformBatch) {
    return (
      <Card title={`本次素材：${batch.keyword}`} extra={<Tag color={STATUS_COLOR[batch.status]}>{statusLabel(batch.status)}</Tag>}>
        <Descriptions size="small" column={{ xs: 1, md: 4 }} style={{ marginBottom: 16 }}>
          {!isSingleSnapshotBatch && <Descriptions.Item label="批次ID">{batch.batch_id}</Descriptions.Item>}
          <Descriptions.Item label={isHotspotBatch ? "搜索范围" : "发布时间"}>
            {isHotspotBatch ? "视频榜、话题榜、抖音搜索" : batch.published_window_days === 0 ? "不限" : "历史设置"}
          </Descriptions.Item>
          <Descriptions.Item label="本次候选目标">{batch.count_per_platform} 条</Descriptions.Item>
        </Descriptions>
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {batch.platform_runs.map((run) => (
            <PlatformRunDetail
              key={run.run_id}
              run={run}
              batchId={batch.batch_id}
              onResolveMedia={onResolveMedia}
              onSendToWorkspace={onSendToWorkspace}
               onGenerateOriginalScript={onGenerateOriginalScript}
               originalScriptLoadingId={originalScriptLoadingId}
               onReconnectPlatform={onReconnectPlatform}
             />
          ))}
        </Space>
      </Card>
    );
  }

  return (
    <section className="crawler-batch-workspace">
      <header className="crawler-result-heading">
        <div>
          <Space size={8} wrap>
            <Title level={5}>本次结果</Title>
            <Tag color={STATUS_COLOR[batch.status]}>{statusLabel(batch.status)}</Tag>
          </Space>
          <Text type="secondary">
            关键词：<Text strong>{batch.keyword}</Text> · {batchPlatforms.join(" / ") || "平台未返回"}
          </Text>
        </div>
        <Space wrap>
          <Button size="small" onClick={onRefreshSearch}>刷新搜索</Button>
          <Button size="small" loading={copyProbeLoading} onClick={() => void onProbeCopy(batch)}>
            {copyProbeActionLabel}
          </Button>
        </Space>
      </header>
      <UnifiedPlatformResults
        batch={batch}
        onResolveMedia={onResolveMedia}
        onSendToWorkspace={onSendToWorkspace}
        onGenerateOriginalScript={onGenerateOriginalScript}
        originalScriptLoadingId={originalScriptLoadingId}
        onReconnectPlatform={onReconnectPlatform}
        displaySettings={materialDisplaySettings}
        onSortChange={onSortChange}
      />
    </section>
  );
}

function emptyRunSummary(run: CrawlerPlatformRun) {
  const stopMessage = crawlShortfallSummary(run);
  if (stopMessage) return stopMessage;
  if (run.error) {
    if (run.provider.startsWith("douyin_public_browser")) {
      return `抖音登录搜索已暂停：${run.error}。`;
    }
    return `${run.platform_label}本次没有完成：${run.error}`;
  }
  if ((run.raw_discovered ?? run.raw_item_count) > 0) {
    return `${run.platform_label}${crawlerFunnelSummary(run)}，详细原因见下方诊断。`;
  }
  if (run.raw_item_count > 0 && run.parsed_item_count === 0) {
    return `${run.platform_label}发现 ${run.raw_item_count} 条页面结果，但当时没有解析出可校验的标题和发布时间，未进入候选榜。`;
  }
  if (run.parsed_item_count > 0) {
    const filtered = [
      run.out_of_window_count ? `时间不符 ${run.out_of_window_count}` : "",
      run.irrelevant_count ? `关键词不符 ${run.irrelevant_count}` : "",
      run.below_heat_floor_count ? `热度不足 ${run.below_heat_floor_count}` : "",
      run.low_spoken_value_count ? `标题信息不足 ${run.low_spoken_value_count}` : "",
      run.invalid_count ? `字段无效 ${run.invalid_count}` : "",
      run.duplicate_count ? `重复 ${run.duplicate_count}` : "",
    ].filter(Boolean);
    return `${run.platform_label}解析 ${run.parsed_item_count} 条，${filtered.length ? `其中${filtered.join("、")}` : "没有符合本次条件的内容"}，未进入候选榜。`;
  }
  return `${run.platform_label}没有返回搜索结果，可能需要登录或完成平台验证。`;
}

function crawlShortfallSummary(run: CrawlerPlatformRun) {
  if (!run.crawl_stop_message || run.returned_count >= run.requested_count) return null;
  const found = run.returned_count > 0
    ? `已找到 ${run.returned_count} 条`
    : "暂未整理出可查看素材";
  return `${run.platform_label}${found}，目标 ${run.requested_count} 条：${run.crawl_stop_message}`;
}

function buildProgressiveBatch(
  queue: CrawlerKeywordQueueResponse,
  item: CrawlerKeywordQueueItem,
): CrawlerBatchResponse | null {
  const candidates = item.progress_candidates || [];
  const terminal = ["succeeded", "partial", "failed", "cancelled", "paused"].includes(queue.status);
  const platformRuns: CrawlerPlatformRun[] = queue.platforms.map((platform) => {
    const platformCandidates = candidates.filter((candidate) => candidate.platform === platform);
    return {
      run_id: `progress-${queue.queue_id}-${platform}`,
      platform,
      platform_label: materialPlatformLabel(platform),
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
    monitoring_policy: "free_single_snapshot_v1",
  };
}

function mergeCrawlerProgressSnapshots(snapshots: CrawlerBatchResponse[]) {
  if (!snapshots.length) return null;
  const latest = snapshots[snapshots.length - 1];
  const runs = new Map<string, CrawlerPlatformRun>();
  snapshots.forEach((snapshot) => snapshot.platform_runs.forEach((run) => {
    // The persisted progress snapshot and the later saved platform batch can
    // describe the same platform with different run IDs. Merge by platform so
    // a candidate does not briefly appear twice when the durable batch arrives.
    const key = run.platform;
    const previous = runs.get(key);
    if (!previous) {
      runs.set(key, {
        ...run,
        candidates: [...run.candidates],
        reference_candidates: [...(run.reference_candidates || [])],
        low_incremental_candidates: [...(run.low_incremental_candidates || [])],
      });
      return;
    }
    const candidates = new Map<string, CrawlerCandidateResult>();
    [...previous.candidates, ...(previous.reference_candidates || []), ...(previous.low_incremental_candidates || []), ...run.candidates, ...(run.reference_candidates || []), ...(run.low_incremental_candidates || [])]
      .forEach((candidate) => candidates.set(candidate.video_id, candidate));
    const referenceIds = new Set(
      [...(previous.reference_candidates || []), ...(run.reference_candidates || [])]
        .map((candidate) => candidate.video_id),
    );
    const mergedCandidates = [...candidates.values()].filter((candidate) => !referenceIds.has(candidate.video_id));
    const mergedReferences = [...candidates.values()].filter((candidate) => referenceIds.has(candidate.video_id));
    runs.set(key, {
      ...previous,
      ...run,
      run_id: run.run_id,
      candidates: mergedCandidates,
      reference_count: mergedReferences.length,
      reference_candidates: mergedReferences,
      low_incremental_candidates: [],
      returned_count: Math.max(previous.returned_count, run.returned_count, mergedCandidates.length),
      retained: Math.max(previous.retained || 0, run.retained || 0, mergedCandidates.length),
    });
  }));
  return {
    ...latest,
    platform_runs: [...runs.values()],
    total_candidates: [...runs.values()].reduce((sum, run) => sum + run.returned_count, 0),
  };
}

function UnifiedPlatformResults({
  batch,
  onResolveMedia,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
  onReconnectPlatform,
  displaySettings,
  onSortChange,
}: {
  batch: CrawlerBatchResponse;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
  onReconnectPlatform: (platform: string) => void;
  displaySettings: MaterialDisplaySettings;
  onSortChange: (value: MaterialSort) => void;
}) {
  const runs = batch.platform_runs;
  const batchId = batch.batch_id;
  const candidates = useMemo(
    () => dedupeCandidates(runs.flatMap((run) => run.candidates)),
    [runs],
  );
  const referenceCandidates = useMemo(
    () => dedupeCandidates(runs.flatMap((run) => run.reference_candidates || [])),
    [runs],
  );
  const emptyRuns = runs.filter((run) => run.candidates.length === 0 && !(run.reference_candidates || []).length);
  const xiaohongshuRuleFailures = runs.filter(
    (run) => run.platform === "xiaohongshu" && run.payload_diagnostic === "页面结构发生变化，请重新连接",
  );
  const referenceRuns = runs.filter((run) => (run.reference_candidates || []).length > 0);
  const cachedRuns = runs.filter((run) => run.cache_hit);
  const funnelSummaries = runs
    .filter((run) => (run.raw_discovered ?? run.raw_item_count ?? 0) > 0)
    .map((run) => `${run.platform_label}${crawlerFunnelSummary(run)}`);
  const platformShortfalls = runs
    .filter((run) => run.candidates.length > 0)
    .map(crawlShortfallSummary)
    .filter((message): message is string => Boolean(message));
  const missingSelectedPlatforms = (batch.platforms ?? [])
    .filter((platform) => !runs.some((run) => run.platform === platform));
  const missingPlatformSummary = (platform: string) => {
    if (platform === "douyin") {
      return `抖音本批未执行（旧批次无法补回）；重新找素材会尝试抖音登录搜索。${batch.error ? `当时提示：${batch.error}` : ""}`;
    }
    return `${materialPlatformLabel(platform)}已选中但本次未完成搜索：${batch.error || "请检查登录或验证。"}`;
  };

  return (
    <div className="crawler-unified-results">
      <div className="crawler-platform-summary">
        <Space wrap size={[6, 6]}>
        {runs.map((run) => {
          const visibleCount = run.candidates.length;
          const referenceCount = run.reference_candidates?.length || run.reference_count || 0;
          const label = visibleCount > 0
            ? `${run.platform_label} ${visibleCount} 条${referenceCount > 0 ? `高相关 · ${referenceCount} 条待确认` : ""}`
            : referenceCount > 0
              ? `${run.platform_label} ${referenceCount} 条（待确认）`
            : run.error
              ? `${run.platform_label} 未完成`
            : (run.raw_discovered ?? run.raw_item_count) > 0
              ? `${run.platform_label} ${crawlerFunnelSummary(run)}`
              : `${run.platform_label} 暂无结果`;
          return (
            <Tooltip
              key={run.run_id}
              title={visibleCount > 0 ? `${run.platform_label}提供 ${visibleCount} 条可查看素材。` : referenceCount > 0 ? `平台已找到 ${referenceCount} 条搜索参考，请人工确认。` : emptyRunSummary(run)}
            >
              <Tag color={visibleCount > 0 ? "success" : referenceCount > 0 ? "warning" : run.raw_item_count > 0 ? "warning" : "default"}>
                {label}
              </Tag>
            </Tooltip>
          );
        })}
        {missingSelectedPlatforms.map((platform) => (
          <Tooltip key={`missing-${platform}`} title={missingPlatformSummary(platform)}>
            <Tag color="warning">{materialPlatformLabel(platform)} {platform === "douyin" ? "未执行" : "未完成"}</Tag>
          </Tooltip>
        ))}
        </Space>
        <Text type="secondary">已合并去重</Text>
      </div>
      {funnelSummaries.length > 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 8 }}
          message="本次采集漏斗"
          description={funnelSummaries.join("；")}
        />
      )}
      {cachedRuns.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 8 }}
          message="使用近期结果，本次没有重新访问平台"
          description={`${cachedRuns.map((run) => run.platform_label).join("、")}复用了 ${RECENT_RESULT_REUSE_MINUTES} 分钟内的搜索结果；超过 ${RECENT_RESULT_REUSE_MINUTES} 分钟后再搜索会重新获取。`}
        />
      )}
      {referenceRuns.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 8 }}
          message={`另有 ${referenceCandidates.length} 条待确认素材`}
          description={referenceRuns.map((run) => `${run.platform_label}有 ${run.reference_candidates?.length || run.reference_count || 0} 条待确认；请打开原视频确认相关性，确认前不会进入智能创作。`).join(" ")}
        />
      )}
      {(emptyRuns.length > 0 || missingSelectedPlatforms.length > 0 || platformShortfalls.length > 0) && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: candidates.length ? 8 : 0 }}
            message="部分平台本次没有返回可用素材"
            description={[
              ...missingSelectedPlatforms.map(missingPlatformSummary),
              ...emptyRuns.map(emptyRunSummary),
              ...platformShortfalls,
            ].join(" ")}
          />
      )}
      {xiaohongshuRuleFailures.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: candidates.length ? 8 : 0 }}
          message="小红书页面结构发生变化，请重新连接"
          description={(
            <Button type="primary" onClick={() => onReconnectPlatform("xiaohongshu")}>
              重新连接小红书
            </Button>
          )}
        />
      )}
      {candidates.length > 0 ? (
        <MaterialCandidateTable
          candidates={candidates}
          batchId={batchId}
          displaySettings={displaySettings}
          onResolveMedia={onResolveMedia}
          onSendToWorkspace={onSendToWorkspace}
          onGenerateOriginalScript={onGenerateOriginalScript}
          originalScriptLoadingId={originalScriptLoadingId}
          onSortChange={onSortChange}
        />
      ) : referenceCandidates.length === 0 ? (
        <Alert
          type="warning"
          showIcon
          message="本次没有返回可展示素材"
          description="换一个关键词，或完成上方提示的平台登录后再找。"
        />
      ) : null}
      {referenceCandidates.length > 0 && (
        <MaterialCandidateTable
          candidates={referenceCandidates}
          resultLabel="待确认素材"
          batchId={batchId}
          displaySettings={displaySettings}
          onResolveMedia={onResolveMedia}
          onSendToWorkspace={onSendToWorkspace}
          onGenerateOriginalScript={onGenerateOriginalScript}
          originalScriptLoadingId={originalScriptLoadingId}
          onSortChange={onSortChange}
        />
      )}
    </div>
  );
}

function MaterialCandidateTable({
  candidates,
  resultLabel = "本次结果",
  batchId,
  displaySettings,
  onResolveMedia,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
  onSortChange,
}: {
  candidates: CrawlerCandidateResult[];
  resultLabel?: string;
  batchId: string;
  displaySettings: MaterialDisplaySettings;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
  onSortChange: (value: MaterialSort) => void;
}) {
  const toast = useToast();
  const [detail, setDetail] = useState<CrawlerCandidateResult | null>(null);
  const metricCoverage = useMemo(() => materialMetricCoverage(candidates), [candidates]);
  const rows = useMemo(() => {
    const sortValue = (item: CrawlerCandidateResult) => {
      if (displaySettings.sort === "heat") return interactionHeat(item);
      if (displaySettings.sort === "newest") return publishedTimestamp(item);
      if (displaySettings.sort === "likes") return item.likes;
      if (displaySettings.sort === "comments") return item.comments;
      return item.plays;
    };

    return candidates
      .map((item, index) => ({ item, index, sortValue: sortValue(item) }))
      .sort((left, right) => {
        if (left.sortValue === null || left.sortValue === undefined) {
          return right.sortValue === null || right.sortValue === undefined ? left.index - right.index : 1;
        }
        if (right.sortValue === null || right.sortValue === undefined) return -1;
        return right.sortValue - left.sortValue || left.index - right.index;
      })
      .map(({ item }) => item);
  }, [candidates, displaySettings]);

  useEffect(() => {
    setDetail((current) => {
      if (!rows.length) return null;
      if (current && rows.some((item) => candidateIdentity(item) === candidateIdentity(current))) {
        return current;
      }
      return rows[0];
    });
  }, [rows]);

  return (
    <div className="crawler-candidate-workspace">
      <section className="crawler-candidate-list-pane">
        <div className="crawler-candidate-toolbar">
          <div>
            <Title level={5}>{resultLabel} · {rows.length} 条</Title>
            <Space size={6} wrap>
              <Text type="secondary">高相关 {candidates.length} 条，点选一条查看详情。</Text>
              <Text type="secondary">
                {formatMetricCoverage("评论", metricCoverage.comments, candidates.length)} · {formatMetricCoverage("分享", metricCoverage.shares, candidates.length)} · {formatMetricCoverage("收藏", metricCoverage.favorites, candidates.length)}
              </Text>
            </Space>
          </div>
          <Space>
            <Select<MaterialSort>
              aria-label="排序依据"
              value={displaySettings.sort}
              className="crawler-sort-select"
              onChange={onSortChange}
              options={[
                { value: "heat", label: "综合热度" },
                { value: "newest", label: "最新发布" },
                { value: "likes", label: "最多点赞" },
                { value: "comments", label: "最多评论" },
                { value: "plays", label: "最多播放" },
              ]}
            />
          </Space>
        </div>
        <List
          className="crawler-candidate-list"
          dataSource={rows}
          locale={{ emptyText: "没有符合当前条件的素材。" }}
          pagination={{ pageSize: 5, hideOnSinglePage: true, showSizeChanger: false }}
          renderItem={(item) => {
            const selected = detail ? candidateIdentity(detail) === candidateIdentity(item) : false;
            const heat = interactionHeat(item);
            const primaryMetric = heat ?? item.plays ?? item.likes;
            const primaryLabel = heat !== null
              ? (hasPartialInteractionMetrics(item) ? "参考热度" : "热度")
              : item.plays !== null && item.plays !== undefined ? "播放" : "点赞";
            return (
              <List.Item
                className={`crawler-candidate-row${selected ? " selected" : ""}`}
                onClick={() => setDetail(item)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") setDetail(item);
                }}
                role="button"
                tabIndex={0}
                aria-pressed={selected}
              >
                <Checkbox
                  aria-label={`选择 ${item.title}`}
                  checked={selected}
                  onChange={() => setDetail(item)}
                  onClick={(event) => event.stopPropagation()}
                />
                <div className="crawler-candidate-copy">
                  <Text strong ellipsis={{ tooltip: item.title }}>{item.title}</Text>
                  <Text type="secondary" ellipsis={{ tooltip: item.author_name || "作者未返回" }}>
                    {item.author_name || "作者未返回"}
                  </Text>
                  <Space size={6} wrap>
                    <Tag color="blue">{item.platform_label || item.platform}</Tag>
                    <Text type="secondary">{formatMaterialPublishedAt(item)}</Text>
                  </Space>
                </div>
                <div className="crawler-candidate-metric">
                  <Text strong>{primaryLabel} {formatCompactMaterialMetric(primaryMetric)}</Text>
                  <Text type="secondary">{formatMaterialDuration(item.duration_seconds)}</Text>
                </div>
                <MoreOutlined aria-hidden />
              </List.Item>
            );
          }}
        />
      </section>
      <MaterialCandidatePreview
        candidate={detail}
        batchId={batchId}
        originalScriptLoadingId={originalScriptLoadingId}
        onResolveMedia={onResolveMedia}
        onSendToWorkspace={onSendToWorkspace}
        onGenerateOriginalScript={onGenerateOriginalScript}
      />
    </div>
  );
}

function MaterialCandidatePreview({
  candidate,
  batchId,
  originalScriptLoadingId,
  onResolveMedia,
  onSendToWorkspace,
  onGenerateOriginalScript,
}: {
  candidate: CrawlerCandidateResult | null;
  batchId: string;
  originalScriptLoadingId: string | null;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
}) {
  const toast = useToast();
  const materialStatus = candidate?.spoken_material_status || "topic_only";
  const isTopicOnly = materialStatus === "topic_only";
  const hasOriginalTranscript = candidate?.is_original_transcript === true
    || candidate?.copy_source === "doubao_mobile_transcript"
    || candidate?.copy_source === "authorized_asr_transcript"
    || Boolean(candidate?.media_transcription_task_id);
  const status = candidate ? copyStatus(candidate) : null;
  const keywordMatch = candidate ? keywordMatchLabel(candidate.relevance_reason) : null;
  const isReferenceCandidate = candidate?.selection_tier === "reserve";
  const partialHeat = candidate ? hasPartialInteractionMetrics(candidate) : false;

  if (!candidate) {
    return <aside className="crawler-candidate-preview empty"><Text type="secondary">点选一条素材查看详情</Text></aside>;
  }

  const originalMediaHref = candidateOriginalMediaHref(candidate);
  const canTranscribeFromLink = hasUsableXiaohongshuShareLink(candidate);
  const xiaohongshuManualOnly = isXiaohongshuTopicOnly(candidate) && !canTranscribeFromLink;

  return (
    <aside className="crawler-candidate-preview">
      <Text type="secondary">已选择 1 条</Text>
      <Title level={5}>{candidate.title}</Title>
      <Space wrap size={8}>
        <Text type="secondary">{candidate.author_name || "作者未返回"}</Text>
        <Tag color="blue">{candidate.platform_label || candidate.platform}</Tag>
        {candidate.evidence?.startsWith("douyin_public_search:") && <Tag color="cyan">抖音登录搜索</Tag>}
        {keywordMatch && <Tag color="green">{keywordMatch}</Tag>}
        {isReferenceCandidate && <Tag color="orange">待人工确认</Tag>}
        {xiaohongshuManualOnly && <Tag color="orange">需 App/登录确认</Tag>}
        {status && <Tag color={status.color}>{status.label}</Tag>}
      </Space>
      <div className="crawler-preview-metrics">
        <div><Text type="secondary">{partialHeat ? "参考热度" : "热度"}</Text><Text strong>{formatCompactMaterialMetric(interactionHeat(candidate))}</Text></div>
        <div><Text type="secondary">发布时间</Text><Text strong>{formatMaterialPublishedAt(candidate)}</Text></div>
        <div><Text type="secondary">时长</Text><Text strong>{formatMaterialDuration(candidate.duration_seconds)}</Text></div>
      </div>
      <Space wrap size={[10, 4]} className="crawler-preview-secondary-metrics">
        <Text type="secondary">播放 {formatCompactMaterialMetric(candidate.plays)}</Text>
        <Text type="secondary">点赞 {formatCompactMaterialMetric(candidate.likes)}</Text>
        <Text type="secondary">评论 {formatCompactMaterialMetric(candidate.comments)}</Text>
        <Text type="secondary">分享 {formatCompactMaterialMetric(candidate.shares)}</Text>
        <Text type="secondary">收藏 {formatCompactMaterialMetric(candidate.favorites)}</Text>
      </Space>
      {isReferenceCandidate && (
        <Alert
          type="warning"
          showIcon
          message="这是平台参考候选"
          description="这是平台搜索参考。请先打开原视频确认相关性，确认前不能送入智能创作。"
          style={{ marginTop: 12 }}
        />
      )}
      <div className="crawler-preview-actions">
        {!isTopicOnly && !isReferenceCandidate && (
          <Button block type="primary" onClick={() => onSendToWorkspace(batchId, candidate)}>送入智能创作</Button>
        )}
        {originalMediaHref && <Button block onClick={() => void openCandidateOriginalMedia(candidate).catch((error) => toast.error((error as Error).message))}>打开原视频</Button>}
        {candidate.media_transcription_task_id && (
          <Button block onClick={() => onResolveMedia(candidate)}>查看原文案</Button>
        )}
        {!hasOriginalTranscript && (
          <Button
            block
            icon={<FileTextOutlined />}
            loading={originalScriptLoadingId === candidate.video_id}
            onClick={() => onGenerateOriginalScript(candidate)}
          >
            {materialStatus === "text_reference" ? "根据可见文案改写" : "生成原创口播"}
          </Button>
        )}
        {xiaohongshuManualOnly ? (
          <Tooltip title={XIAOHONGSHU_MANUAL_ONLY_MESSAGE}>
            <Button block href={buildTranscriptionHref(candidate, true)}>上传授权视频后转写</Button>
          </Tooltip>
        ) : status?.filter !== "detected" && (
          <Button block href={buildTranscriptionHref(candidate)}>
            {canTranscribeFromLink ? "转写文案" : "上传视频转写"}
          </Button>
        )}
      </div>
    </aside>
  );
}

function PlatformRunDetail({
  run,
  batchId,
  onResolveMedia,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
  onReconnectPlatform,
}: {
  run: CrawlerPlatformRun;
  batchId: string;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
  onReconnectPlatform: (platform: string) => void;
}) {
  const isHotspotRun = run.provider === "douyin_local_browser";
  const shortfallMessage = crawlShortfallSummary(run);
  const referenceCandidates = run.reference_candidates || [];
  const allCandidates = [...run.candidates, ...referenceCandidates];
  const totalRanked = [...allCandidates]
    .sort((a, b) => isHotspotRun
      ? (b.new_plays ?? b.plays ?? 0) - (a.new_plays ?? a.plays ?? 0)
      : (b.effective_interactions ?? 0) - (a.effective_interactions ?? 0));
  const candidates = totalRanked;
  const lowIncrementalCandidates = run.low_incremental_candidates || [];

  return (
    <Card
      size="small"
      title={<Space><Tag color="blue">{run.platform_label}</Tag><Tag color={STATUS_COLOR[run.status]}>{statusLabel(run.status)}</Tag>{run.cache_hit && <Tag color="cyan">使用近期结果</Tag>}</Space>}
    >
      {isHotspotRun ? (
        <Space wrap size={[6, 6]}>
          <Tag color="blue">已找到 {run.relevant_count ?? run.returned_count} 条</Tag>
          <Tag color="green">质量线：100 赞 + 1 赞/天</Tag>
          <Text type="secondary">视频总榜不足才会依次查话题总榜、搜索总榜。</Text>
        </Space>
      ) : (
        <Descriptions size="small" column={{ xs: 1, md: 4 }}>
          <Descriptions.Item label="高相关素材">{run.relevant_count ?? run.returned_count}/{run.requested_count}</Descriptions.Item>
          <Descriptions.Item label="待确认素材">{referenceCandidates.length}</Descriptions.Item>
          <Descriptions.Item label="采集漏斗">{crawlerFunnelSummary(run)}</Descriptions.Item>
          <Descriptions.Item label="原始 / 解析 / 去重">{`${run.raw_discovered ?? run.raw_item_count} / ${run.parsed ?? run.parsed_item_count} / ${run.deduped ?? run.parsed_item_count}`}</Descriptions.Item>
          <Descriptions.Item label="过滤">{`不相关 ${run.relevance_filtered ?? run.irrelevant_count ?? 0} · 互动热度不足 ${run.below_heat_floor_count ?? 0} · 标题信息不足 ${run.low_spoken_value_count ?? 0} · 无效 ${run.invalid_fields ?? run.invalid_count} · 重复 ${run.duplicate_count} · 时长 ${run.duration_filtered ?? run.duration_filtered_count ?? 0}`}</Descriptions.Item>
          <Descriptions.Item label="API 调用">{run.api_call_count}</Descriptions.Item>
          <Descriptions.Item label="额度">{run.quota_remaining ?? "未返回"}</Descriptions.Item>
          <Descriptions.Item label="估算费用">{formatCurrency(run.billable_units)}</Descriptions.Item>
        </Descriptions>
      )}
      {run.stage_timings_ms && Object.keys(run.stage_timings_ms).length > 0 && (
        <Descriptions size="small" column={{ xs: 1, md: 4 }} style={{ marginTop: 8 }} title="高级诊断（首个响应时间为后端记录）">
          {Object.entries(run.stage_timings_ms).map(([stage, value]) => (
            <Descriptions.Item key={stage} label={crawlerStageLabel(stage)}>{formatNumber(value)} ms</Descriptions.Item>
          ))}
          <Descriptions.Item label="规则版本">{run.rule_version || run.relevance_rule_version || "历史批次未记录"}</Descriptions.Item>
          <Descriptions.Item label="会话">{run.session_recovered ? "已保留登录态软恢复" : run.browser_reused === false ? "新页面" : "复用页面"}</Descriptions.Item>
        </Descriptions>
      )}
      {run.error && <Alert style={{ marginTop: 12 }} type="error" showIcon message={run.error} />}
      {referenceCandidates.length > 0 && (
        <Alert
          style={{ marginTop: 12 }}
          type="warning"
          showIcon
          message={`另有 ${referenceCandidates.length} 条待确认素材`}
          description="这些条目可以直接查看；请先打开原视频确认相关性，确认前不会进入智能创作。"
        />
      )}
      {shortfallMessage && <Alert style={{ marginTop: 12 }} type="info" showIcon message="本次暂未凑足目标" description={shortfallMessage} />}
      {run.payload_diagnostic && <Alert style={{ marginTop: 12 }} type="warning" showIcon message="供应商响应诊断" description={run.payload_diagnostic} />}
      {run.platform === "xiaohongshu" && run.payload_diagnostic === "页面结构发生变化，请重新连接" && (
        <Button
          type="primary"
          style={{ marginTop: 12 }}
          onClick={() => onReconnectPlatform(run.platform)}
        >
          重新连接小红书
        </Button>
      )}
      {allCandidates.length === 0 && isHotspotRun && lowIncrementalCandidates.length > 0 ? (
        <Space direction="vertical" style={{ width: "100%", marginTop: 16 }} size="middle">
          <Alert
            type="warning"
            showIcon
          message="暂时没有热门素材"
          description={`找到了 ${lowIncrementalCandidates.length} 条相关视频，但热度不够高。可以直接换一个词再找。`}
          />
          <HotspotCandidateTable
            candidates={lowIncrementalCandidates}
            batchId={batchId}
            onSendToWorkspace={onSendToWorkspace}
            onGenerateOriginalScript={onGenerateOriginalScript}
            originalScriptLoadingId={originalScriptLoadingId}
          />
        </Space>
      ) : allCandidates.length === 0 && isHotspotRun ? (
        <Alert
          style={{ marginTop: 16 }}
          type="warning"
          showIcon
          message="暂时没有找到素材"
          description={run.error || "换一个词再找；也可以直接去抖音看看这个词的常见说法。"}
        />
      ) : allCandidates.length === 0 ? (
        <Alert
          style={{ marginTop: 16 }}
          type="warning"
          showIcon
          message="本次没有可展示候选"
          description={run.error || resultStateMessage(run)}
        />
      ) : isHotspotRun ? (
        <HotspotCandidateTable
          candidates={candidates}
          batchId={batchId}
          onSendToWorkspace={onSendToWorkspace}
          onGenerateOriginalScript={onGenerateOriginalScript}
          originalScriptLoadingId={originalScriptLoadingId}
        />
      ) : (
        <Space direction="vertical" style={{ width: "100%", marginTop: 12 }} size="middle">
          {([
            ["exploding", "爆发候选", "volcano"],
            ["hot", "热门候选", "red"],
            ["potential", "潜力候选", "gold"],
            ["observing", "观察样本", "blue"],
            ["ordinary", "普通候选", "default"],
          ] as const).map(([tier, label, color]) => {
            const tierCandidates = candidates.filter((item) => item.display_tier === tier);
            if (tierCandidates.length === 0) return null;
            return (
              <Card key={tier} size="small" title={<Tag color={color}>{label}（{tierCandidates.length}）</Tag>}>
                <List
                  dataSource={tierCandidates}
                  renderItem={(item) => (
                    <CandidateListItem
                      item={item}
                      batchId={batchId}
                      onResolveMedia={onResolveMedia}
                      onSendToWorkspace={onSendToWorkspace}
                      onGenerateOriginalScript={onGenerateOriginalScript}
                      originalScriptLoading={originalScriptLoadingId === item.video_id}
                    />
                  )}
                />
              </Card>
            );
          })}
        </Space>
      )}
    </Card>
  );
}

function HotspotCandidateTable({
  candidates,
  batchId,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
}: {
  candidates: CrawlerCandidateResult[];
  batchId: string;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
}) {
  const [listLabel, setListLabel] = useState("all");
  const [detail, setDetail] = useState<CrawlerCandidateResult | null>(null);
  const labels = useMemo(
    () => Array.from(new Set(candidates.flatMap((item) => item.hotspot_list_labels || []))),
    [candidates],
  );
  const rows = useMemo(
    () => candidates
      .filter((item) => listLabel === "all" || (item.hotspot_list_labels || []).includes(listLabel))
      .sort((a, b) => (b.likes_per_day ?? 0) - (a.likes_per_day ?? 0)),
    [candidates, listLabel],
  );

  const columns: ColumnsType<CrawlerCandidateResult> = [
    {
      title: "排名",
      width: 56,
      render: (_, item, index) => item.provider_hot_rank ?? index + 1,
    },
    {
      title: "视频 / 作者",
      dataIndex: "title",
      width: "36%",
      render: (_, item) => {
        const topicOnly = (item.spoken_material_status || "topic_only") === "topic_only";
        return (
        <Space direction="vertical" size={0} style={{ width: "100%" }}>
          <Paragraph
            ellipsis={{ rows: 2, tooltip: item.title }}
            style={{ margin: 0, cursor: "pointer", lineHeight: 1.45 }}
            onClick={() => setDetail(item)}
          >
            {item.title}
          </Paragraph>
          <Text type="secondary" ellipsis={{ tooltip: `作者：${item.author_name || "未返回"}` }} style={{ width: "100%" }}>
            作者：{item.author_name || "未返回"}
          </Text>
          <Text type="success" ellipsis={{ tooltip: item.spoken_seed_message }} style={{ width: "100%" }}>
            标题信息{item.spoken_seed_score != null ? `（${item.spoken_seed_score}分）` : ""}：{item.spoken_seed_message}
          </Text>
          <Text type={topicOnly ? "warning" : "secondary"} ellipsis={{ tooltip: item.audio_message }} style={{ width: "100%" }}>
            {item.audio_message || "尚未检测文案。"}
          </Text>
        </Space>
        );
      },
    },
    {
      title: "点赞",
      width: 106,
      align: "right",
      render: (_, item) => formatNumber(item.likes),
    },
    {
      title: "日均点赞",
      width: 112,
      align: "right",
      render: (_, item) => formatLikesPerDay(item.likes_per_day),
    },
    {
      title: "时长",
      width: 80,
      align: "right",
      render: (_, item) => item.duration_seconds ? `${formatNumber(item.duration_seconds)} 秒` : "未返回",
    },
    {
      title: "来自",
      width: 165,
      render: (_, item) => (
        <Space wrap size={[2, 2]}>
          {(item.hotspot_list_labels || []).map((label) => <Tag color="purple" key={label}>{label}</Tag>)}
        </Space>
      ),
    },
    {
      title: "操作",
      width: 280,
      render: (_, item) => {
        const topicOnly = (item.spoken_material_status || "topic_only") === "topic_only";
        const originalMediaHref = candidateOriginalMediaHref(item);
        const canTranscribeFromLink = hasUsableXiaohongshuShareLink(item);
        const xiaohongshuManualOnly = isXiaohongshuTopicOnly(item) && !canTranscribeFromLink;
        return (
        <Space size={6}>
          {!topicOnly && (
            <Button
              size="small"
              type="primary"
              onClick={() => onSendToWorkspace(batchId, item)}
            >
              送入智能创作
            </Button>
          )}
          <Button
            size="small"
            loading={originalScriptLoadingId === item.video_id}
            onClick={() => onGenerateOriginalScript(item)}
          >
            按这个话题写原创
          </Button>
          {xiaohongshuManualOnly ? (
            <Tooltip title={XIAOHONGSHU_MANUAL_ONLY_MESSAGE}>
              <Button size="small" href={buildTranscriptionHref(item, true)}>上传授权视频后转写</Button>
            </Tooltip>
          ) : (
            <Button size="small" href={buildTranscriptionHref(item)}>
              {canTranscribeFromLink ? "转写文案" : "上传视频转写"}
            </Button>
          )}
          <Tooltip title={!originalMediaHref ? "该候选没有可用的原视频链接。" : undefined}>
            <span>
              <Button
                size="small"
                onClick={() => void openCandidateOriginalMedia(item).catch((error) => toast.error((error as Error).message))}
                disabled={!originalMediaHref}
              >
                原视频
              </Button>
            </span>
          </Tooltip>
        </Space>
        );
      },
    },
  ];

  return (
    <>
      <Space wrap style={{ marginTop: 12, marginBottom: 8 }}>
        <Text type="secondary">来源</Text>
        <Select
          size="small"
          value={listLabel}
          style={{ minWidth: 155 }}
          onChange={setListLabel}
          options={[{ value: "all", label: `全部（${candidates.length}）` }, ...labels.map((label) => ({ value: label, label }))]}
        />
        <Text type="secondary">优先给你 3 条，选一条即可开始。</Text>
      </Space>
      <Table
        rowKey="video_id"
        size="small"
        scroll={{ x: 1160 }}
        columns={columns}
        dataSource={rows}
        pagination={{ pageSize: 10, showSizeChanger: false, hideOnSinglePage: true, showTotal: (total) => `共 ${total} 条` }}
      />
      <Drawer
        title={detail?.title || "视频详情"}
        open={detail !== null}
        width={560}
        onClose={() => setDetail(null)}
      >
        {detail && (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Space wrap>
              <Tag color="magenta">浏览器爆款榜</Tag>
              {(detail.hotspot_list_labels || []).map((label) => <Tag color="purple" key={label}>{label}</Tag>)}
            </Space>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="作者">{detail.author_name || "未返回"}</Descriptions.Item>
              <Descriptions.Item label="点赞数">{formatNumber(detail.likes)}</Descriptions.Item>
              <Descriptions.Item label="日均点赞">{formatLikesPerDay(detail.likes_per_day)}</Descriptions.Item>
              {detail.quality_source && <Descriptions.Item label="计算方式">{detail.quality_source}</Descriptions.Item>}
              <Descriptions.Item label="时长">{detail.duration_seconds ? `${formatNumber(detail.duration_seconds)} 秒` : "未返回"}</Descriptions.Item>
              <Descriptions.Item label="素材状态">{detail.spoken_material_message || "仅有标题和互动数据，只能用于选题参考。"}</Descriptions.Item>
              <Descriptions.Item label="标题信息">{detail.spoken_seed_message || "公开文字不足以支撑原创文案。"}</Descriptions.Item>
              <Descriptions.Item label="文案状态">{detail.audio_message || "尚未检测文案。"}</Descriptions.Item>
              {candidateOriginalMediaHref(detail) && <Descriptions.Item label="原视频"><Button type="link" onClick={() => void openCandidateOriginalMedia(detail).catch((error) => toast.error((error as Error).message))}>打开原视频</Button></Descriptions.Item>}
              {detail.evidence?.includes(";") && <Descriptions.Item label="榜单指标">{hotspotEvidenceSummary(detail.evidence)}</Descriptions.Item>}
            </Descriptions>
            {detail.reasons.length > 0 && (
              <Space wrap>{detail.reasons.map((reason, index) => <Tag key={`${reason}-${index}`}>{reason}</Tag>)}</Space>
            )}
            {(detail.share_count != null || detail.collect_count != null) && (
              <Text type="secondary">分享：{formatNumber(detail.share_count)}；收藏：{formatNumber(detail.collect_count)}</Text>
            )}
            <Space>
              {(detail.spoken_material_status || "topic_only") !== "topic_only" && (
                <Button
                  type="primary"
                  onClick={() => onSendToWorkspace(batchId, detail)}
                >
                  送入智能创作
                </Button>
              )}
              <Button
                loading={originalScriptLoadingId === detail.video_id}
                onClick={() => onGenerateOriginalScript(detail)}
              >
                按这个话题写原创
              </Button>
              {isXiaohongshuTopicOnly(detail) && !hasUsableXiaohongshuShareLink(detail) ? (
                <Tooltip title={XIAOHONGSHU_MANUAL_ONLY_MESSAGE}>
                  <Button href={buildTranscriptionHref(detail, true)}>上传授权视频后转写</Button>
                </Tooltip>
              ) : (
                <Button href={buildTranscriptionHref(detail)}>
                  {hasUsableXiaohongshuShareLink(detail) ? "转写文案" : "上传视频转写"}
                </Button>
              )}
              <Button onClick={() => void openCandidateOriginalMedia(detail).catch((error) => toast.error((error as Error).message))} disabled={!candidateOriginalMediaHref(detail)}>
                原视频
              </Button>
            </Space>
          </Space>
        )}
      </Drawer>
    </>
  );
}

function CandidateListItem({
  item,
  batchId,
  onResolveMedia,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoading,
}: {
  item: CrawlerCandidateResult;
  batchId: string;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoading: boolean;
}) {
  const toast = useToast();
  const isHotspotLeaderboard = item.evidence?.startsWith("hotspot:") ?? false;
  const isDouyinPublicSearch = item.evidence?.startsWith("douyin_public_search:") ?? false;
  const hotspotLabel = hotspotWindowLabel(item.hotspot_window_hours);
  const metrics = metricEntries(item, isHotspotLeaderboard);
  const displayedReasons = item.reasons.filter(
    (reason) => !/(按发布时间折算|快照|复爬|复搜|采样|增长|趋势|小时|分位 P\d+)/.test(reason),
  );
  // 已有原版转写（或正在/已有转写任务）的候选不提供元数据文案，避免与原版转写混淆。
  const hasOriginalTranscript =
    item.is_original_transcript === true ||
    item.copy_source === "doubao_mobile_transcript" ||
    item.copy_source === "authorized_asr_transcript" ||
    Boolean(item.media_transcription_task_id);
  const materialStatus = item.spoken_material_status || "topic_only";
  const isTopicOnly = materialStatus === "topic_only";
  const isReferenceCandidate = item.selection_tier === "reserve";
  const originalMediaHref = candidateOriginalMediaHref(item);
  const canTranscribeFromLink = hasUsableXiaohongshuShareLink(item);
  const xiaohongshuManualOnly = isXiaohongshuTopicOnly(item) && !canTranscribeFromLink;
  const materialMessage = item.spoken_material_message || (isTopicOnly
    ? (xiaohongshuManualOnly
      ? XIAOHONGSHU_MANUAL_ONLY_MESSAGE
      : "仅有标题和互动数据，只能用于选题参考，不能提取原视频文案。")
    : "已有可核验的文字素材，请先核对原意再继续创作。");
  const seedStatus = item.spoken_seed_status || "low_information";
  const seedTag = seedStatus === "writeable"
    ? { color: "success", label: `标题信息较完整${item.spoken_seed_score != null ? ` · ${item.spoken_seed_score}分` : ""}` }
    : seedStatus === "reference_only"
      ? { color: "gold", label: "仅作选题" }
      : { color: "default", label: "信息不足" };
  const audioStatus = item.audio_status || "unknown";
  const audioTag = audioStatus === "speech_detected"
    ? { color: "success", label: "检测到文案" }
    : audioStatus === "no_audio"
      ? { color: "error", label: "没有音轨" }
      : audioStatus === "no_clear_speech"
        ? { color: "warning", label: "疑似无文案" }
        : audioStatus === "checking"
          ? { color: "processing", label: "文案检测中" }
          : audioStatus === "check_failed"
            ? { color: "error", label: "检测失败" }
            : { color: "default", label: "待检测文案" };
  const copyPoolTag = item.copy_pool_status === "primary"
    ? { color: "success", label: "优先素材" }
    : item.copy_pool_status === "reserve"
      ? { color: "gold", label: "备用素材" }
      : item.copy_pool_status === "excluded"
        ? { color: "default", label: "未入选" }
        : null;
  const materialTag = materialStatus === "transcript_ready"
    ? { color: "success", label: "已有授权转写" }
    : materialStatus === "text_reference"
      ? { color: "gold", label: "有可见文案参考" }
      : { color: "default", label: "仅作选题参考" };

  return (
    <List.Item
      actions={[
        !isTopicOnly && !isReferenceCandidate ? (
          <Button
            type="link"
            size="small"
            onClick={() => onSendToWorkspace(batchId, item)}
          >
            送入智能创作
          </Button>
        ) : null,
        originalMediaHref ? (
          item.platform === "xiaohongshu"
            ? <Button type="link" size="small" onClick={() => void openCandidateOriginalMedia(item).catch((error) => toast.error((error as Error).message))}>原视频</Button>
            : <a href={originalMediaHref} target="_blank" rel="noreferrer">原视频</a>
        ) : <Text type="secondary">无原视频链接</Text>,
        item.media_transcription_task_id ? (
          <Button type="link" size="small" onClick={() => onResolveMedia(item)}>查看原文案</Button>
        ) : null,
        !hasOriginalTranscript ? (
          <Tooltip title={materialStatus === "text_reference" ? "基于人工保存的可见文案进行原创改写，不会还原原视频逐字内容。" : "这条没有可提取的原文案；系统只会根据标题、热点词和互动数据生成原创口播。"}>
            <Button
              type="link"
              size="small"
              icon={<FileTextOutlined />}
              loading={originalScriptLoading}
              onClick={() => onGenerateOriginalScript(item)}
            >
              {materialStatus === "text_reference" ? "根据可见文案改写" : "生成原创口播"}
            </Button>
          </Tooltip>
        ) : null,
        xiaohongshuManualOnly ? (
          <Tooltip title={XIAOHONGSHU_MANUAL_ONLY_MESSAGE}>
            <Button type="link" size="small" href={buildTranscriptionHref(item, true)}>上传授权视频后转写</Button>
          </Tooltip>
        ) : audioStatus !== "speech_detected" ? (
          <Button type="link" size="small" href={buildTranscriptionHref(item)}>
            {canTranscribeFromLink ? "转写文案" : "上传视频转写"}
          </Button>
        ) : null,
      ]}
    >
      <List.Item.Meta
        avatar={item.provider_hot_rank ? <Tag color="purple">搜索 #{item.provider_hot_rank}</Tag> : undefined}
        title={
            <Space wrap>
              <Text strong>{item.title}</Text>
              <Tag color="blue">{item.platform_label}</Tag>
              {item.system_rank && <Tag color="geekblue">系统 #{item.system_rank}</Tag>}
              {isHotspotLeaderboard && <Tag color="magenta">浏览器爆款榜</Tag>}
              {isHotspotLeaderboard && item.hotspot_list_labels && item.hotspot_list_labels.length > 0 && <Tag color="purple">{item.hotspot_list_labels.join(" / ")}</Tag>}
              {isDouyinPublicSearch && <Tag color="cyan">抖音登录搜索</Tag>}
              {isReferenceCandidate && <Tag color="orange">待人工确认</Tag>}
              {xiaohongshuManualOnly && <Tag color="orange">需 App/登录确认</Tag>}
              {item.relevance_basis && (
                <Tag color="green">{keywordMatchLabel(item.relevance_reason) || "命中关键词"}</Tag>
              )}
              {item.media_resolution_status && <Tag>{statusLabel(item.media_resolution_status)}</Tag>}
              {copyPoolTag && <Tag color={copyPoolTag.color}>{copyPoolTag.label}</Tag>}
              <Tag color={materialTag.color}>{materialTag.label}</Tag>
              <Tag color={seedTag.color}>{seedTag.label}</Tag>
              <Tag color={audioTag.color}>{audioTag.label}</Tag>
          </Space>
        }
        description={
          <Space direction="vertical" size={2}>
            <Text type="secondary">
              作者：{item.author_name}{isHotspotLeaderboard ? `；${hotspotLabel}新增播放量：${formatNumber(item.new_plays ?? item.plays)}；时长：${formatNumber(item.duration_seconds)} 秒` : isDouyinPublicSearch && item.duration_seconds != null ? `；时长：${formatNumber(item.duration_seconds)} 秒` : ""}
            </Text>
            <Text type={isTopicOnly ? "warning" : "secondary"}>{materialMessage}</Text>
            {isReferenceCandidate && <Text type="warning">这是平台搜索参考，请先打开原视频确认相关性，再进入智能创作。</Text>}
            <Text type={seedStatus === "writeable" ? "success" : "secondary"}>{item.spoken_seed_message}</Text>
            <Text type="secondary">{item.audio_message || "尚未检测文案。"}</Text>
            {item.copy_pool_status === "excluded" && item.copy_rejection_reason && (
              <Text type="secondary">未入选原因：{item.copy_rejection_reason}</Text>
            )}
            {metrics.length > 0 && (
              <Text type="secondary">
                {metrics.map(([label, value]) => `${label}：${formatNumber(value)}`).join("；")}
              </Text>
            )}
            {isHotspotLeaderboard && item.evidence?.includes(";") && (
              <Text type="secondary">
                榜单指标：{hotspotEvidenceSummary(item.evidence)}
              </Text>
            )}
            {(item.share_count !== undefined || item.collect_count !== undefined) && (
              <Text type="secondary">
                分享：{formatNumber(item.share_count)}；收藏：{formatNumber(item.collect_count)}
              </Text>
            )}
            {item.data_quality_warnings.length > 0 && (
              <Space wrap size={[4, 4]}>
                {item.data_quality_warnings.map((warning) => (
                  <Tag key={warning} color="warning">{warning}</Tag>
                ))}
              </Space>
            )}
            {displayedReasons.length > 0 && (
              <Paragraph style={{ margin: 0 }}>
                {displayedReasons.map((reason, index) => (
                  <Tag key={`${reason}-${index}`} icon={index === 0 ? <CheckCircleOutlined /> : undefined}>{reason}</Tag>
                ))}
              </Paragraph>
            )}
          </Space>
        }
      />
    </List.Item>
  );
}
