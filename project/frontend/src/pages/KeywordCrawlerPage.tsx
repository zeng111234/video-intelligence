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
  createCrawlerBatch,
  createPipelineFromCandidate,
  deleteCrawlerBatch,
  generateOriginalScript,
  getCrawlerBatch,
  probeCrawlerBatchCopy,
  getCrawlerCapabilities,
  getCrawlerHotWords,
  listCrawlerBatches,
  previewCrawlerCandidateMedia,
  recheckCrawlerBatchLegacyNoText,
  startCrawlerBrowserDiscovery,
} from "../api/client";
import type {
  CrawlerBatchResponse,
  CrawlerBrowserDiscoveryCapabilities,
  CrawlerCandidateMediaPreviewResponse,
  CrawlerCandidateResult,
  CrawlerCapabilitiesResponse,
  CrawlerHotWordItem,
  CrawlerOriginalScriptResponse,
  CrawlerPlatformRun,
  CrawlerSearchRequest,
} from "../api/types";
import MaterialSearchExperience from "../components/MaterialSearchExperience";
import { useToast } from "../components/Toast";
import { useNavigate } from "react-router-dom";
import { cnyToCredits, handleCreditsError } from "../utils/credits";
import "./KeywordCrawlerPage.css";

const { Text, Title, Paragraph } = Typography;
const RECENT_RESULT_REUSE_MINUTES = 10;

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

function buildTranscriptionHref(candidate: CrawlerCandidateResult) {
  const query = new URLSearchParams({
    candidate: candidate.video_id,
    title: candidate.title,
  });
  if (candidate.source_url) {
    query.set("share_text", candidate.source_url);
  } else {
    query.set("entry", "upload");
  }
  return `/transcription?${query.toString()}`;
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
  return /未(?:直接)?命中/.test(reason) ? "未命中关键词" : "命中关键词";
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
    cached: "缓存命中",
    blocked: "已阻断",
    outcome_unknown: "结果未知",
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
    all_irrelevant: `供应商返回了内容，但均未通过标题/话题的严格关键词匹配；已过滤 ${run.irrelevant_count ?? 0} 条。`,
    all_low_spoken_value: `找到了相关内容，但公开文字不足以支撑原创口播；已隐藏 ${run.low_spoken_value_count ?? 0} 条。`,
    all_below_heat_floor: `有 ${run.strict_relevant_count ?? 0} 条严格相关内容，但互动热度均低于 100，已不进入主榜。`,
    no_hot: "已得到候选，但没有达到本产品的热门/潜力阈值；它们不会被标为爆款。",
  };
  return messages[run.result_state] || "本次没有可展示候选，请查看诊断和供应商响应。";
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
  const [probingCopy, setProbingCopy] = useState(false);
  const [deletingBatchId, setDeletingBatchId] = useState<string | null>(null);
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

  const loadBatches = useCallback(async () => {
    setLoading(true);
    try {
      const list = await listCrawlerBatches();
      setBatches(list.items);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  const loadSecondaryData = useCallback(async () => {
    try {
      const caps = await getCrawlerCapabilities();
      setCapabilities(caps);
    } catch (err) {
      // The history table remains usable when the local browser status is
      // temporarily unavailable.
      toast.error(`发现能力加载失败：${(err as Error).message}`);
    }

    // Hot-word suggestions are optional and must never delay the main page.
    void getCrawlerHotWords()
      .then((response) => setHotWords(response.words))
      .catch(() => setHotWords([]));
  }, [toast]);

  const handleSendCandidateToWorkspace = (
    batchId: string,
    candidate: CrawlerCandidateResult,
  ) => {
    const query = new URLSearchParams({
      crawler_batch_id: batchId,
      candidate_id: candidate.video_id,
    });
    navigate(`/pipeline?${query.toString()}`);
  };

  useEffect(() => {
    void loadBatches();
    void loadSecondaryData();
  }, [loadBatches, loadSecondaryData]);

  const finishSearchReveal = useCallback((batch: CrawlerBatchResponse) => {
    setSelectedBatch(batch);
    upsertBatch(batch);
    setSearchResultBatch(null);
    setSearchProgress(null);
    setSubmitting(false);
    searchInFlightRef.current = false;
    toast.success("已找到素材；可排序并点选查看。");
  }, [toast, upsertBatch]);

  const handleSearch = async (keywordOverride?: string) => {
    if (searchInFlightRef.current) {
      toast.info("正在找素材，请稍等，不要重复提交。");
      return;
    }
    const searchKeyword = (keywordOverride ?? keyword).trim();
    if (searchKeyword.length < 1 || searchKeyword.length > 50) {
      toast.warning("关键词需为 1–50 个字符");
      return;
    }
    if (!platforms.length) {
      toast.warning("请至少选择一个平台");
      return;
    }
    const payload: CrawlerSearchRequest = { ...requestPayload, keyword: searchKeyword };
    searchInFlightRef.current = true;
    setSubmitting(true);
    setKeyword(searchKeyword);
    setSearchResultBatch(null);
    setSearchProgress({ startedAt: Date.now(), platforms: [...platforms] });
    try {
      const batch = await createCrawlerBatch(payload);
      setSearchResultBatch(batch);
    } catch (err) {
      if (!handleCreditsError(err, () => navigate("/admin"))) {
        toast.error((err as Error).message);
      }
      searchInFlightRef.current = false;
      setSubmitting(false);
      setSearchProgress(null);
      setSearchResultBatch(null);
    }
  };

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
    try {
      setSelectedBatch(await getCrawlerBatch(batch.batch_id));
      setKeyword(batch.keyword);
      setHistoryDrawerOpen(false);
    } catch (err) {
      toast.error((err as Error).message);
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


  return (
    <div className="keyword-crawler-page">
      <header className="crawler-page-heading">
        <div>
          <Title level={4}>找素材</Title>
          <Text type="secondary">本页找素材不消耗积分，也不会自动开启付费补充。</Text>
        </div>
        <Button icon={<HistoryOutlined />} onClick={() => setHistoryDrawerOpen(true)}>
          历史记录{batches.length ? ` ${batches.length}` : ""}
        </Button>
      </header>

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
                      <Button
                        type="link"
                        size="small"
                        loading={connectingPlatform === option.value}
                        onClick={() => void handleStartBrowserConnection(connection)}
                      >
                        登录处理
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
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
                    actions={[<Button key="open" type="link" onClick={() => void handleOpenBatch(batch)}>详情</Button>]}
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
                <Button key="open" type="link" icon={<EyeOutlined />} onClick={() => void handleOpenBatch(batch)}>查看</Button>,
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
              value={rightsHolder}
              onChange={(event) => setRightsHolder(event.target.value)}
              addonBefore="权利主体"
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
      return `抖音官网搜索已暂停：${run.error}。`;
    }
    return `${run.platform_label}本次没有完成：${run.error}`;
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
  return `${run.platform_label}已找到 ${run.returned_count} 条，未达到 ${run.requested_count} 条：${run.crawl_stop_message}`;
}

function UnifiedPlatformResults({
  batch,
  onResolveMedia,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
  displaySettings,
  onSortChange,
}: {
  batch: CrawlerBatchResponse;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
  displaySettings: MaterialDisplaySettings;
  onSortChange: (value: MaterialSort) => void;
}) {
  const runs = batch.platform_runs;
  const batchId = batch.batch_id;
  const candidates = useMemo(
    () => dedupeCandidates(runs.flatMap((run) => run.candidates)),
    [runs],
  );
  const emptyRuns = runs.filter((run) => run.candidates.length === 0);
  const cachedRuns = runs.filter((run) => run.cache_hit);
  const platformShortfalls = runs
    .filter((run) => run.candidates.length > 0)
    .map(crawlShortfallSummary)
    .filter((message): message is string => Boolean(message));
  const missingSelectedPlatforms = (batch.platforms ?? [])
    .filter((platform) => !runs.some((run) => run.platform === platform));
  const missingPlatformSummary = (platform: string) => {
    if (platform === "douyin") {
      return `抖音本批未执行（旧批次无法补回）；重新找素材会尝试抖音官网。${batch.error ? `当时提示：${batch.error}` : ""}`;
    }
    return `${materialPlatformLabel(platform)}已选中但本次未完成搜索：${batch.error || "请检查登录或验证。"}`;
  };

  return (
    <div className="crawler-unified-results">
      <div className="crawler-platform-summary">
        <Space wrap size={[6, 6]}>
        {runs.map((run) => {
          const visibleCount = run.candidates.length;
          const label = visibleCount > 0
            ? `${run.platform_label} ${visibleCount} 条`
            : run.error
              ? `${run.platform_label} 未完成`
            : run.raw_item_count > 0
              ? `${run.platform_label} 发现 ${run.raw_item_count} 条 · 0 条符合`
              : `${run.platform_label} 暂无结果`;
          return (
            <Tooltip
              key={run.run_id}
              title={visibleCount > 0 ? `${run.platform_label}提供 ${visibleCount} 条本次检测候选。` : emptyRunSummary(run)}
            >
              <Tag color={visibleCount > 0 ? "success" : run.raw_item_count > 0 ? "warning" : "default"}>
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
      {cachedRuns.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 8 }}
          message="刚刚已经搜索过，为避免访问过于频繁，本次没有重新访问平台"
          description={`${cachedRuns.map((run) => run.platform_label).join("、")}复用了 ${RECENT_RESULT_REUSE_MINUTES} 分钟内的搜索结果；超过 ${RECENT_RESULT_REUSE_MINUTES} 分钟后再搜索会重新获取。`}
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
      ) : (
        <Alert
          type="warning"
          showIcon
          message="本次没有返回可展示素材"
          description="换一个关键词，或完成上方提示的平台登录后再找。"
        />
      )}
    </div>
  );
}

function MaterialCandidateTable({
  candidates,
  batchId,
  displaySettings,
  onResolveMedia,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
  onSortChange,
}: {
  candidates: CrawlerCandidateResult[];
  batchId: string;
  displaySettings: MaterialDisplaySettings;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
  onSortChange: (value: MaterialSort) => void;
}) {
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
            <Title level={5}>本次结果 · {rows.length} 条</Title>
            <Space size={6} wrap>
              <Text type="secondary">共抓取 {candidates.length} 条，点选一条查看详情。</Text>
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
  const materialStatus = candidate?.spoken_material_status || "topic_only";
  const isTopicOnly = materialStatus === "topic_only";
  const hasOriginalTranscript = candidate?.is_original_transcript === true
    || candidate?.copy_source === "doubao_mobile_transcript"
    || candidate?.copy_source === "authorized_asr_transcript"
    || Boolean(candidate?.media_transcription_task_id);
  const status = candidate ? copyStatus(candidate) : null;
  const keywordMatch = candidate ? keywordMatchLabel(candidate.relevance_reason) : null;
  const partialHeat = candidate ? hasPartialInteractionMetrics(candidate) : false;

  if (!candidate) {
    return <aside className="crawler-candidate-preview empty"><Text type="secondary">点选一条素材查看详情</Text></aside>;
  }

  return (
    <aside className="crawler-candidate-preview">
      <Text type="secondary">已选择 1 条</Text>
      <Title level={5}>{candidate.title}</Title>
      <Space wrap size={8}>
        <Text type="secondary">{candidate.author_name || "作者未返回"}</Text>
        <Tag color="blue">{candidate.platform_label || candidate.platform}</Tag>
        {candidate.evidence?.startsWith("douyin_public_search:") && <Tag color="cyan">抖音官网搜索</Tag>}
        {keywordMatch && <Tag color="green">{keywordMatch}</Tag>}
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
      <div className="crawler-preview-actions">
        {!isTopicOnly && (
          <Button block type="primary" onClick={() => onSendToWorkspace(batchId, candidate)}>送入智能创作</Button>
        )}
        {candidate.source_url && <Button block href={candidate.source_url} target="_blank">打开原视频</Button>}
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
        {status?.filter !== "detected" && (
          <Button block href={buildTranscriptionHref(candidate)}>
            {candidate.source_url ? "转写文案" : "上传视频转写"}
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
}: {
  run: CrawlerPlatformRun;
  batchId: string;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
}) {
  const isHotspotRun = run.provider === "douyin_local_browser";
  const shortfallMessage = crawlShortfallSummary(run);
  const totalRanked = [...run.candidates]
    .sort((a, b) => isHotspotRun
      ? (b.new_plays ?? b.plays ?? 0) - (a.new_plays ?? a.plays ?? 0)
      : (b.effective_interactions ?? 0) - (a.effective_interactions ?? 0));
  const candidates = totalRanked;
  const lowIncrementalCandidates = run.low_incremental_candidates || [];

  return (
    <Card
      size="small"
      title={<Space><Tag color="blue">{run.platform_label}</Tag><Tag color={STATUS_COLOR[run.status]}>{statusLabel(run.status)}</Tag>{run.cache_hit && <Tag color="cyan">缓存</Tag>}</Space>}
    >
      {isHotspotRun ? (
        <Space wrap size={[6, 6]}>
          <Tag color="blue">已找到 {run.relevant_count ?? run.returned_count} 条</Tag>
          <Tag color="green">质量线：100 赞 + 1 赞/天</Tag>
          <Text type="secondary">视频总榜不足才会依次查话题总榜、搜索总榜。</Text>
        </Space>
      ) : (
        <Descriptions size="small" column={{ xs: 1, md: 4 }}>
          <Descriptions.Item label="主榜候选">{run.relevant_count ?? run.returned_count}/{run.requested_count}</Descriptions.Item>
          <Descriptions.Item label="原始 / 解析">{run.raw_item_count} / {run.parsed_item_count}</Descriptions.Item>
          <Descriptions.Item label="过滤">{`关键词不相关 ${run.irrelevant_count ?? 0} · 互动热度不足 ${run.below_heat_floor_count ?? 0} · 标题信息不足 ${run.low_spoken_value_count ?? 0} · 无效 ${run.invalid_count} · 重复 ${run.duplicate_count}`}</Descriptions.Item>
          <Descriptions.Item label="API 调用">{run.api_call_count}</Descriptions.Item>
          <Descriptions.Item label="额度">{run.quota_remaining ?? "未返回"}</Descriptions.Item>
          <Descriptions.Item label="估算费用">{formatCurrency(run.billable_units)}</Descriptions.Item>
        </Descriptions>
      )}
      {run.error && <Alert style={{ marginTop: 12 }} type="error" showIcon message={run.error} />}
      {shortfallMessage && <Alert style={{ marginTop: 12 }} type="info" showIcon message="本次暂未凑足目标" description={shortfallMessage} />}
      {run.payload_diagnostic && <Alert style={{ marginTop: 12 }} type="warning" showIcon message="供应商响应诊断" description={run.payload_diagnostic} />}
      {run.candidates.length === 0 && isHotspotRun && lowIncrementalCandidates.length > 0 ? (
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
      ) : run.candidates.length === 0 && isHotspotRun ? (
        <Alert
          style={{ marginTop: 16 }}
          type="warning"
          showIcon
          message="暂时没有找到素材"
          description={run.error || "换一个词再找；也可以直接去抖音看看这个词的常见说法。"}
        />
      ) : run.candidates.length === 0 ? (
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
          <Button
            size="small"
            href={buildTranscriptionHref(item)}
          >
            {item.source_url ? "转写文案" : "上传视频转写"}
          </Button>
          <Tooltip title={!item.source_url ? "该候选没有可用的原视频链接。" : undefined}>
            <span>
              <Button
                size="small"
                href={item.source_url || undefined}
                target="_blank"
                disabled={!item.source_url}
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
              {detail.source_url && <Descriptions.Item label="原视频"><a href={detail.source_url} target="_blank" rel="noreferrer">打开原视频</a></Descriptions.Item>}
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
              <Button href={buildTranscriptionHref(detail)}>
                {detail.source_url ? "转写文案" : "上传视频转写"}
              </Button>
              <Button href={detail.source_url || undefined} target="_blank" disabled={!detail.source_url}>
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
  const materialMessage = item.spoken_material_message || (isTopicOnly
    ? "仅有标题和互动数据，只能用于选题参考，不能提取原视频文案。"
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
        !isTopicOnly ? (
          <Button
            type="link"
            size="small"
            onClick={() => onSendToWorkspace(batchId, item)}
          >
            送入智能创作
          </Button>
        ) : null,
        item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer">原视频</a> : <Text type="secondary">无原视频链接</Text>,
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
        audioStatus !== "speech_detected" ? (
          <Button
            type="link"
            size="small"
            href={buildTranscriptionHref(item)}
          >
            {item.source_url ? "转写文案" : "上传视频转写"}
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
              {isDouyinPublicSearch && <Tag color="cyan">抖音官网搜索</Tag>}
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
