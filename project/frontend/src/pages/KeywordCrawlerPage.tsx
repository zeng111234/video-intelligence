import { useCallback, useEffect, useMemo, useState } from "react";
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
  Segmented,
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
  SearchOutlined,
} from "@ant-design/icons";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  createCrawlerBatch,
  cancelCrawlerBatchTracking,
  createPipelineFromCandidate,
  deleteCrawlerBatch,
  generateOriginalScript,
  getCrawlerBatch,
  getCrawlerCapabilities,
  getCrawlerHotWords,
  listCrawlerBatches,
  previewCrawlerCandidateMedia,
  startCrawlerBrowserDiscovery,
  startCrawlerBatchTracking,
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
import { useToast } from "../components/Toast";
import { useNavigate } from "react-router-dom";

const { Text, Title, Paragraph } = Typography;

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

type RankingMode = "total" | "trend";
type HotspotWindowHours = 1 | 24 | 72 | 168;

const HOTSPOT_WINDOW_OPTIONS: Array<{ label: string; value: HotspotWindowHours }> = [
  { label: "近1小时", value: 1 },
  { label: "近1天", value: 24 },
  { label: "近3天", value: 72 },
  { label: "近7天", value: 168 },
];

function hotspotWindowLabel(hours: number | null | undefined) {
  return HOTSPOT_WINDOW_OPTIONS.find((item) => item.value === hours)?.label || "近7天";
}

function displayBrowserText(value: string | null | undefined) {
  return (value || "")
    .replace(/热点宝专用浏览器/g, "数据浏览器")
    .replace(/热点宝/g, "浏览器");
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

function displayTierLabel(tier: string) {
  const labels: Record<string, string> = {
    exploding: "爆发",
    hot: "热门",
    potential: "潜力",
    observing: "观察中",
    ordinary: "普通",
  };
  return labels[tier] || tier;
}

function displayTierColor(tier: string) {
  const colors: Record<string, string> = {
    exploding: "volcano",
    hot: "red",
    potential: "gold",
    observing: "blue",
    ordinary: "default",
  };
  return colors[tier] || "default";
}

function formatNumber(value: number | null | undefined) {
  return value === null || value === undefined ? "未返回" : value.toLocaleString("zh-CN");
}

function formatScore(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : value.toFixed(1);
}

function formatLikesPerDay(value: number | null | undefined) {
  return value === null || value === undefined ? "未返回" : `${value.toFixed(1)}/天`;
}

function formatCurrency(value: number | null | undefined, currency = "CNY") {
  if (value === null || value === undefined) {
    return "未返回";
  }
  return currency === "CNY" ? `¥${value.toFixed(2)}` : `${value.toFixed(2)} ${currency}`;
}

const OFFICIAL_HOT_NO_MATCH_MESSAGE = "官方热门池中没有匹配，不代表抖音搜索无视频。";

/** 增长阶段徽标：低饱和度配色 */
const GROWTH_STAGE_COLORS: Record<string, string> = {
  观察样本: "#8c8c8c",
  增长确认中: "#7d9dbf",
  热门候选: "#c0a062",
  爆发候选: "#b5654d",
};

function growthStageColor(stage: string) {
  return GROWTH_STAGE_COLORS[stage] || "#8c8c8c";
}

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

function TrendSamplingStatus({ item }: { item: CrawlerCandidateResult }) {
  const points = [...(item.trend_points || [])]
    .filter((point) => Number.isFinite(new Date(point.sampled_at).getTime()))
    .sort((left, right) => new Date(left.sampled_at).getTime() - new Date(right.sampled_at).getTime());
  if (points.length === 0) {
    return <Text type="secondary">尚未采集快照</Text>;
  }
  if (points.length === 1) {
    return <Tag color="processing">已采集 1 次，等待下一次采样</Tag>;
  }
  const first = points[0];
  const last = points[points.length - 1];
  const elapsedHours = (new Date(last.sampled_at).getTime() - new Date(first.sampled_at).getTime()) / 3_600_000;
  const growth = last.effective_interactions - first.effective_interactions;

  if (!Number.isFinite(elapsedHours) || elapsedHours <= 0) {
    return <Tag color="processing">已采集 {points.length} 次，等待有效时间间隔</Tag>;
  }

  const growthPerHour = growth / elapsedHours;
  const confirmed = points.length >= 3;
  return (
    <Tooltip title={`从首次到最新快照：${formatNumber(growth)} 次有效互动变化，历时 ${elapsedHours.toFixed(1)} 小时。`}>
      <Space size={4}>
        <Tag color={confirmed ? "success" : "processing"}>{confirmed ? "趋势已确认" : "增长确认中"}</Tag>
        <Text type="secondary">新增互动 {growth >= 0 ? "+" : ""}{formatNumber(growth)} · {growthPerHour.toFixed(1)}/小时</Text>
      </Space>
    </Tooltip>
  );
}

function CandidateTrendChart({ item }: { item: CrawlerCandidateResult }) {
  const points = [...(item.trend_points || [])]
    .filter((point) => Number.isFinite(new Date(point.sampled_at).getTime()))
    .sort((left, right) => new Date(left.sampled_at).getTime() - new Date(right.sampled_at).getTime())
    .map((point) => ({
      sampledAt: point.sampled_at,
      time: new Date(point.sampled_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }),
      interactions: point.effective_interactions,
      growthPerHour: point.growth_per_hour,
    }));

  if (points.length < 2) return null;

  return (
    <div style={{ height: 156, width: "100%", maxWidth: 560, marginTop: 4 }}>
      <ResponsiveContainer>
        <AreaChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={`trend-${item.video_id}`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#722ed1" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#722ed1" stopOpacity={0.03} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="time" tick={{ fontSize: 11 }} minTickGap={20} />
          <YAxis tick={{ fontSize: 11 }} width={42} />
          <RechartsTooltip
            formatter={(value, name) => [formatNumber(Number(value ?? 0)), name === "interactions" ? "互动热度" : "每小时增长"]}
            labelFormatter={(_, payload) => payload?.[0]?.payload?.time || ""}
          />
          <Area type="monotone" dataKey="interactions" name="interactions" stroke="#722ed1" fill={`url(#trend-${item.video_id})`} strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function KeywordCrawlerPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [capabilities, setCapabilities] = useState<CrawlerCapabilitiesResponse | null>(null);
  const [hotspotBrowser, setHotspotBrowser] = useState<CrawlerBrowserDiscoveryCapabilities | null>(null);
  const [hotspotStarting, setHotspotStarting] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [hotspotWindowHours] = useState<HotspotWindowHours>(168);
  const [batches, setBatches] = useState<CrawlerBatchResponse[]>([]);
  const [selectedBatch, setSelectedBatch] = useState<CrawlerBatchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [deletingBatchId, setDeletingBatchId] = useState<string | null>(null);
  const [mediaCandidate, setMediaCandidate] = useState<CrawlerCandidateResult | null>(null);
  const [mediaPreview, setMediaPreview] = useState<CrawlerCandidateMediaPreviewResponse | null>(null);
  const [mediaPreviewOpen, setMediaPreviewOpen] = useState(false);
  const [mediaSubmitting, setMediaSubmitting] = useState(false);
  const [rightsHolder, setRightsHolder] = useState("本人/公司已授权");
  const [mediaRightsConfirmed, setMediaRightsConfirmed] = useState(false);
  const [hotWords, setHotWords] = useState<CrawlerHotWordItem[]>([]);
  const [originalScript, setOriginalScript] = useState<{
    candidate: CrawlerCandidateResult;
    data: CrawlerOriginalScriptResponse;
  } | null>(null);
  const [originalScriptLoadingId, setOriginalScriptLoadingId] = useState<string | null>(null);

  const requestPayload = useMemo<CrawlerSearchRequest>(() => ({
    keyword: keyword.trim(),
    published_window_days: 7,
    hotspot_window_hours: hotspotWindowHours,
    count_per_platform: 3,
    force_refresh: false,
    mode: "smart",
    track_trend: false,
    target_main_count: 3,
    allow_paid_fallback: false,
    hotspot_result_limit: 20,
  }), [keyword, hotspotWindowHours]);
  const keywordLength = requestPayload.keyword.length;
  const canSearch = keywordLength >= 2 && keywordLength <= 50;
  const keywordHelp =
    keywordLength === 0
      ? "输入一个词，马上开始找素材。"
        : !canSearch
        ? "关键词需为 2–50 个字符。"
          : "会自动查询视频榜、话题榜和抖音搜索，先给你 3 条。";
  const isSandboxMode = capabilities?.mode === "sandbox";
  const hotspotReady = Boolean(hotspotBrowser?.ready_to_crawl);
  const hotspotMissing = hotspotBrowser?.missing_configuration || [];
  const hotspotNeedsPlaywright = hotspotMissing.includes("Playwright Python 依赖");
  const hotspotNeedsBrowser = hotspotMissing.includes("Google Chrome") || hotspotMissing.includes("Microsoft Edge");
  const crawlerDescription = hotspotReady
    ? "输入一个关键词，直接从视频榜、话题榜和抖音搜索里找可做口播的素材。"
    : capabilities
      ? isSandboxMode
        ? "当前为 Sandbox 演示模式，不代表真实平台生产数据。"
        : "先打开浏览器并完成本机授权，即可读取当前可见榜单。"
      : "加载发现能力中。";

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
      setHotspotBrowser(caps.hotspot_browser ?? null);
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

  const handleCreateCandidateLinkTranscription = async (candidate: CrawlerCandidateResult) => {
    if (!candidate.source_url) {
      toast.warning("该候选没有可用的原视频链接");
      return;
    }
    navigate(`/transcription?share_text=${encodeURIComponent(candidate.source_url)}&title=${encodeURIComponent(candidate.title)}`);
  };

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

  const handleStartTracking = (batch: CrawlerBatchResponse) => {
    Modal.confirm({
      title: "追踪这批走势？",
      content: "将最多新增 2 次 OneAPI 搜索：约 2 小时后首次采样，最后一次按真实增长安排。确认后可随时取消未执行的采样。",
      okText: "确认追踪",
      cancelText: "取消",
      onOk: async () => {
        const result = await startCrawlerBatchTracking(batch.batch_id);
        setSelectedBatch(result.batch);
        upsertBatch(result.batch);
        toast.success(result.message);
      },
    });
  };

  const handleCancelTracking = async (batch: CrawlerBatchResponse) => {
    try {
      const result = await cancelCrawlerBatchTracking(batch.batch_id);
      setSelectedBatch(result.batch);
      upsertBatch(result.batch);
      toast.success(result.message);
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  useEffect(() => {
    void loadBatches();
    void loadSecondaryData();
  }, [loadBatches, loadSecondaryData]);

  const handleSearch = async (keywordOverride?: string) => {
    const searchKeyword = (keywordOverride ?? keyword).trim();
    if (searchKeyword.length < 2 || searchKeyword.length > 50) {
      toast.warning("关键词需为 2–50 个字符");
      return;
    }
    const payload: CrawlerSearchRequest = { ...requestPayload, keyword: searchKeyword };
    setSubmitting(true);
    try {
      const batch = await createCrawlerBatch(payload);
      setSelectedBatch(batch);
      setKeyword("");
      upsertBatch(batch);
      toast.success("素材已找到，选一条送入智能创作即可");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleStartHotspot = async () => {
    setHotspotStarting(true);
    try {
      const status = await startCrawlerBrowserDiscovery();
      setHotspotBrowser(status);
      toast.info(status.message);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setHotspotStarting(false);
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
    setMediaRightsConfirmed(false);
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
    if (!mediaRightsConfirmed) {
      toast.warning("请先确认拥有媒体处理权");
      return;
    }
    setMediaSubmitting(true);
    try {
      const run = await createPipelineFromCandidate({
        candidate_id: mediaCandidate.video_id,
        rights_holder: rightsHolder,
        rights_confirmed: mediaRightsConfirmed,
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


  const batchColumns: ColumnsType<CrawlerBatchResponse> = [
    { title: "批次ID", dataIndex: "batch_id", width: 170, render: (v) => <Text code>{v}</Text> },
    { title: "关键词", dataIndex: "keyword", width: 140 },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (v: string) => <Tag color={STATUS_COLOR[v]}>{statusLabel(v)}</Tag>,
    },
    {
      title: "模式",
      dataIndex: "mode",
      width: 130,
      render: (v: string, record) => {
        const hotspot = record.provider === "douyin_local_browser" || record.monitoring_policy === "hotspot_single_snapshot_v1";
        return <Tag color={v === "sandbox" ? "orange" : hotspot ? "magenta" : "blue"}>{hotspot ? "浏览器找素材" : v}</Tag>;
      },
    },
    { title: "API调用", dataIndex: "total_api_calls", width: 90 },
    { title: "候选数", dataIndex: "total_candidates", width: 90 },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 180,
      render: (v: string | null) => (v ? new Date(v).toLocaleString("zh-CN") : "-"),
    },
    {
      title: "操作",
      width: 150,
      render: (_, record) => (
        <Space size={0}>
          <Button
            type="link"
            icon={<EyeOutlined />}
            onClick={async () => {
              try {
                setSelectedBatch(await getCrawlerBatch(record.batch_id));
              } catch (err) {
                toast.error((err as Error).message);
              }
            }}
          >
            详情
          </Button>
          <Button
            type="link"
            danger
            icon={<DeleteOutlined />}
            loading={deletingBatchId === record.batch_id}
            onClick={() => handleDeleteBatch(record)}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Title level={4} style={{ margin: 0 }}>找热门素材</Title>
        <Text type="secondary">{crawlerDescription}</Text>
      </div>

      <Card title="你想做什么内容？">
        <Space wrap align="end">
          <div>
            <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>关键词</Text>
            <Input
              prefix={<SearchOutlined />}
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              onPressEnter={() => void handleSearch()}
              placeholder="例如：餐饮获客"
              allowClear
              style={{ width: 260 }}
              status={keywordLength > 0 && !canSearch ? "error" : undefined}
            />
            <Text type={canSearch ? "secondary" : "warning"} style={{ display: "block", marginTop: 4 }}>
              {keywordHelp}
            </Text>
            {hotWords.length > 0 && (
              <div style={{ marginTop: 8, maxWidth: 520 }}>
                <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>
                  热门词（点一下直接找）
                </Text>
                <Space wrap size={[4, 4]}>
                  {hotWords.slice(0, 12).map((item) => (
                    <Tag
                      key={item.word}
                      style={{ cursor: "pointer" }}
                      color={keyword.trim() === item.word ? "gold" : undefined}
                      onClick={() => void handleSearch(item.word)}
                    >
                      {item.word}
                      {item.hot_value !== null && item.hot_value !== undefined
                        ? ` ${formatNumber(item.hot_value)}`
                        : ""}
                    </Tag>
                  ))}
                </Space>
              </div>
            )}
          </div>
          <Tooltip title={!canSearch ? keywordHelp : "直接开始找素材"}>
            <Button type="primary" loading={submitting} disabled={!canSearch} onClick={() => void handleSearch()}>
              找素材
            </Button>
          </Tooltip>
        </Space>
        <Alert
          style={{ marginTop: 12 }}
          type={hotspotBrowser?.ready_to_crawl ? "success" : hotspotMissing.length ? "warning" : "info"}
          showIcon
          message={
            hotspotBrowser?.ready_to_crawl
              ? "浏览器已授权：可检索真实榜单"
              : hotspotNeedsPlaywright
                ? "浏览器依赖未安装"
                : hotspotNeedsBrowser
                  ? "未找到可用浏览器"
                  : "先连接并授权浏览器，获得更高质量的爆款视频"
          }
          description={
            <Space direction="vertical" size={6}>
              <Text>{displayBrowserText(hotspotBrowser?.message) || "会打开独立 Chrome 窗口；请在其中扫码登录。"}</Text>
              {hotspotNeedsPlaywright ? (
                <Text type="secondary">请在项目根目录运行 <Text code>python -m pip install -r project/backend/requirements.txt</Text>，然后重启后端。</Text>
              ) : null}
              {hotspotNeedsBrowser ? (
                <Text type="secondary">请安装 {hotspotBrowser?.browser_channel === "msedge" ? "Microsoft Edge" : "Google Chrome"}，或在根目录 .env 中将 <Text code>DOUYIN_BROWSER_CHANNEL</Text> 改为已安装的浏览器后重启后端。</Text>
              ) : null}
              <Space wrap>
                <Button size="small" type="primary" loading={hotspotStarting} onClick={handleStartHotspot} disabled={hotspotBrowser?.enabled === false}>
                  {hotspotBrowser?.running ? "检查浏览器授权" : "打开浏览器并扫码"}
                </Button>
              </Space>
            </Space>
          }
        />
        <Text type="secondary" style={{ display: "block", marginTop: 12 }}>
          默认查看近 7 天；需要更细的范围或诊断时，再进入下方的历史详情。
        </Text>
      </Card>

      {selectedBatch && (
        <BatchDetail
          batch={selectedBatch}
          onStartTracking={handleStartTracking}
          onCancelTracking={handleCancelTracking}
          onResolveMedia={handleOpenCandidateMedia}
          mediaSubmitting={mediaSubmitting}
          onCreateCandidateLinkTranscription={handleCreateCandidateLinkTranscription}
          onSendToWorkspace={handleSendCandidateToWorkspace}
          onGenerateOriginalScript={handleGenerateOriginalScript}
          originalScriptLoadingId={originalScriptLoadingId}
        />
      )}

      <Card title={`历史批次（${batches.length}）`}>
        <Table
          rowKey="batch_id"
          columns={batchColumns}
          dataSource={batches}
          loading={loading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
        />
      </Card>

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
        okText="确认并提取文案"
        cancelText="取消"
        confirmLoading={mediaSubmitting}
        okButtonProps={{
          disabled: !mediaPreview?.resolvable || !mediaRightsConfirmed,
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
            <Checkbox
              checked={mediaRightsConfirmed}
              onChange={(event) => setMediaRightsConfirmed(event.target.checked)}
            >
              我确认拥有该视频用于本次私有转写和文案分析的处理权
            </Checkbox>
            <Text type="secondary">
              供应商返回的临时媒体地址只会在后端读取并立即用于转写，不会展示、保存或批量下载。
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
    </Space>
  );
}

function BatchDetail({
  batch,
  onStartTracking,
  onCancelTracking,
  onResolveMedia,
  mediaSubmitting,
  onCreateCandidateLinkTranscription,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
}: {
  batch: CrawlerBatchResponse;
  onStartTracking: (batch: CrawlerBatchResponse) => void;
  onCancelTracking: (batch: CrawlerBatchResponse) => void;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  mediaSubmitting: boolean;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
}) {
  const [rankingMode, setRankingMode] = useState<RankingMode>("total");
  const isHotspotBatch = batch.provider === "douyin_local_browser" || batch.monitoring_policy === "hotspot_single_snapshot_v1";

  return (
    <Card title={`本次素材：${batch.keyword}`} extra={<Tag color={STATUS_COLOR[batch.status]}>{statusLabel(batch.status)}</Tag>}>
      <Descriptions size="small" column={{ xs: 1, md: 4 }} style={{ marginBottom: 16 }}>
        {!isHotspotBatch && <Descriptions.Item label="批次ID">{batch.batch_id}</Descriptions.Item>}
        <Descriptions.Item label={isHotspotBatch ? "搜索范围" : "发布时间"}>{isHotspotBatch ? "视频榜、话题榜、抖音搜索" : batch.published_window_days === 0 ? "不限" : batch.published_window_days === 1 ? "近 24 小时（历史）" : "近 7 天（历史）"}</Descriptions.Item>
        <Descriptions.Item label={isHotspotBatch ? "优先给你" : "每平台"}>{batch.count_per_platform} 条</Descriptions.Item>
        {!isHotspotBatch && <Descriptions.Item label="本批费用">¥{batch.total_estimated_cost_cny.toFixed(2)}</Descriptions.Item>}
        {!isHotspotBatch && <Descriptions.Item label="走势追踪">{batch.tracking_status === "scheduled" ? `已安排，下一次 ${batch.next_tracking_at ? new Date(batch.next_tracking_at).toLocaleString("zh-CN") : "待定"}` : batch.tracking_status === "complete" ? "已完成" : batch.tracking_status === "cancelled" ? "已取消" : "未开启"}</Descriptions.Item>}
        {batch.mode === "smart" && <Descriptions.Item label="免费池候选">{batch.free_candidate_count || 0} 条</Descriptions.Item>}
        {batch.mode === "smart" && <Descriptions.Item label="付费兜底">{batch.paid_fallback_used ? "已使用" : batch.paid_fallback_blocked_reason ? "不可用，已保留免费结果" : "未使用"}</Descriptions.Item>}
      </Descriptions>
      <Space style={{ marginBottom: 12 }} wrap>
        {!isHotspotBatch && batch.tracking_status === "scheduled" ? (
          <Button danger onClick={() => onCancelTracking(batch)}>取消走势追踪</Button>
        ) : !isHotspotBatch && batch.tracking_status !== "complete" ? (
          <Button type="primary" onClick={() => onStartTracking(batch)} disabled={batch.total_candidates === 0}>追踪这批走势</Button>
        ) : null}
        {isHotspotBatch ? <Text type="secondary">选一条送入智能创作；不想参考原视频，也可以在下一步改成原创口播。</Text> : <><Text type="secondary">榜单</Text><Segmented value={rankingMode} onChange={(value) => setRankingMode(value as RankingMode)} options={[{ label: "总榜", value: "total" }, { label: "爆发趋势", value: "trend" }]} /></>}
      </Space>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {batch.platform_runs.map((run) => (
          <PlatformRunDetail
            key={run.run_id}
            run={run}
            batchId={batch.batch_id}
            rankingMode={rankingMode}
            onResolveMedia={onResolveMedia}
            mediaSubmitting={mediaSubmitting}
            onCreateCandidateLinkTranscription={onCreateCandidateLinkTranscription}
            onSendToWorkspace={onSendToWorkspace}
            onGenerateOriginalScript={onGenerateOriginalScript}
            originalScriptLoadingId={originalScriptLoadingId}
          />
        ))}
      </Space>
    </Card>
  );
}

function PlatformRunDetail({
  run,
  batchId,
  rankingMode,
  onResolveMedia,
  mediaSubmitting,
  onCreateCandidateLinkTranscription,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
}: {
  run: CrawlerPlatformRun;
  batchId: string;
  rankingMode: RankingMode;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  mediaSubmitting: boolean;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
}) {
  const isHotspotRun = run.provider === "douyin_local_browser";
  const totalRanked = [...run.candidates]
    .sort((a, b) => isHotspotRun
      ? (b.new_plays ?? b.plays ?? 0) - (a.new_plays ?? a.plays ?? 0)
      : (b.effective_interactions ?? 0) - (a.effective_interactions ?? 0));
  const candidates = isHotspotRun || rankingMode === "total"
    ? totalRanked
    : totalRanked
      .filter((item) => (item.valid_snapshot_count ?? 0) >= 3)
      .sort((a, b) => (b.trend_score ?? -1) - (a.trend_score ?? -1));
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
          <Descriptions.Item label="过滤">{`关键词不相关 ${run.irrelevant_count ?? 0} · 互动热度不足 ${run.below_heat_floor_count ?? 0} · 无效 ${run.invalid_count} · 重复 ${run.duplicate_count}`}</Descriptions.Item>
          <Descriptions.Item label="API 调用">{run.api_call_count}</Descriptions.Item>
          <Descriptions.Item label="额度">{run.quota_remaining ?? "未返回"}</Descriptions.Item>
          <Descriptions.Item label="估算费用">{formatCurrency(run.billable_units)}</Descriptions.Item>
        </Descriptions>
      )}
      {run.error && <Alert style={{ marginTop: 12 }} type="error" showIcon message={run.error} />}
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
            onCreateCandidateLinkTranscription={onCreateCandidateLinkTranscription}
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
          description="换一个词再找；也可以直接去抖音看看这个词的常见说法。"
        />
      ) : run.candidates.length === 0 ? (
        <Alert
          style={{ marginTop: 16 }}
          type="warning"
          showIcon
          message="本次没有可展示候选"
          description={run.error || resultStateMessage(run)}
        />
      ) : candidates.length === 0 ? (
        <Alert
          style={{ marginTop: 16 }}
          type="info"
          showIcon
          message={rankingMode === "total" ? "本次没有可展示候选" : "爆发趋势等待三点真实曲线"}
          description={rankingMode === "total"
            ? "严格相关候选会按有效互动量排序展示。"
            : "当前尚未完成三个真实快照与有效时间跨度；系统不会用单点数据模拟趋势。"}
        />
      ) : isHotspotRun ? (
        <HotspotCandidateTable
          candidates={candidates}
          batchId={batchId}
          onCreateCandidateLinkTranscription={onCreateCandidateLinkTranscription}
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
                      mediaSubmitting={mediaSubmitting}
                      onCreateCandidateLinkTranscription={onCreateCandidateLinkTranscription}
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
  onCreateCandidateLinkTranscription,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
}: {
  candidates: CrawlerCandidateResult[];
  batchId: string;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
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
      render: (_, item) => (
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
        </Space>
      ),
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
      width: 360,
      render: (_, item) => (
        <Space size={6}>
          <Button
            size="small"
            type="primary"
            onClick={() => onSendToWorkspace(batchId, item)}
          >
            送入智能创作
          </Button>
          <Button
            size="small"
            loading={originalScriptLoadingId === item.video_id}
            onClick={() => onGenerateOriginalScript(item)}
          >
            按这个话题写原创
          </Button>
          <Tooltip title={!item.source_url ? "该候选没有可用的原视频链接。" : "进入转写页后确认内容处理权，再提取原视频文案。"}>
            <span>
              <Button
                size="small"
                disabled={!item.source_url}
                onClick={() => onCreateCandidateLinkTranscription(item)}
              >
                去转写页提取文案
              </Button>
            </span>
          </Tooltip>
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
      ),
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
              <Button
                type="primary"
                onClick={() => onSendToWorkspace(batchId, detail)}
              >
                送入智能创作
              </Button>
              <Button
                loading={originalScriptLoadingId === detail.video_id}
                onClick={() => onGenerateOriginalScript(detail)}
              >
                按这个话题写原创
              </Button>
              <Button
                disabled={!detail.source_url}
                onClick={() => onCreateCandidateLinkTranscription(detail)}
              >
                去转写页提取文案
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
  mediaSubmitting,
  onCreateCandidateLinkTranscription,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoading,
}: {
  item: CrawlerCandidateResult;
  batchId: string;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  mediaSubmitting: boolean;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoading: boolean;
}) {
  const isHotspotLeaderboard = item.evidence?.startsWith("hotspot:") ?? false;
  const hotspotLabel = hotspotWindowLabel(item.hotspot_window_hours);
  const metrics = metricEntries(item, isHotspotLeaderboard);
  const displayedReasons = isHotspotLeaderboard
    ? item.reasons.filter((reason) => !/(按发布时间折算|有效快照|暂无可用增长速度|未满 \d+ 次复搜)/.test(reason))
    : item.reasons;
  const automationPaused = item.platform !== "douyin" && !item.media_transcription_task_id;
  // 已有原版转写（或正在/已有转写任务）的候选不提供元数据文案，避免与原版转写混淆。
  const hasOriginalTranscript =
    item.is_original_transcript === true ||
    item.copy_source === "doubao_mobile_transcript" ||
    item.copy_source === "authorized_asr_transcript" ||
    Boolean(item.media_transcription_task_id);

  return (
    <List.Item
      actions={[
        <Button
          type="link"
          size="small"
          onClick={() => onSendToWorkspace(batchId, item)}
        >
          送入智能创作
        </Button>,
        item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer">原视频</a> : <Text type="secondary">无原视频链接</Text>,
        item.media_transcription_task_id ? (
          <Button type="link" size="small" onClick={() => onResolveMedia(item)}>查看原文案</Button>
        ) : (
          <Tooltip title={!item.source_url ? "该候选没有可用原视频链接。" : "在转写页确认授权、识别链接并选择本机解析或明确确认的付费回退。"}>
            <span>
              <Button
                type="link"
                size="small"
                disabled={!item.source_url}
                onClick={() => onCreateCandidateLinkTranscription(item)}
              >
                去转写页提取文案
              </Button>
            </span>
          </Tooltip>
        ),
        !hasOriginalTranscript ? (
          <Tooltip title="基于标题、热点词与互动数据，生成适合数字人口播的短句文案；使用前请人工核对。">
            <Button
              type="link"
              size="small"
              icon={<FileTextOutlined />}
              loading={originalScriptLoading}
              onClick={() => onGenerateOriginalScript(item)}
            >
              生成原创文案
            </Button>
          </Tooltip>
        ) : null,
        item.media_transcription_task_id ? null : <Tooltip
          title={automationPaused ? "本阶段只跑通抖音自动化，不会对该平台发起付费媒体解析。" : undefined}
        >
          <span>
            <Button
              type="link"
              size="small"
              loading={mediaSubmitting}
              disabled={automationPaused}
              onClick={() => onResolveMedia(item)}
            >
              {item.media_transcription_task_id
                ? "付费解析"
                : automationPaused
                  ? "本阶段暂停"
                  : "付费自动解析"}
            </Button>
          </span>
        </Tooltip>,
      ]}
    >
      <List.Item.Meta
        avatar={item.provider_hot_rank ? <Tag color="purple">搜索 #{item.provider_hot_rank}</Tag> : undefined}
        title={
            <Space wrap>
              <Text strong>{item.title}</Text>
              {item.system_rank && <Tag color="geekblue">系统 #{item.system_rank}</Tag>}
              {isHotspotLeaderboard && <Tag color="magenta">浏览器爆款榜</Tag>}
              {isHotspotLeaderboard && item.hotspot_list_labels && item.hotspot_list_labels.length > 0 && <Tag color="purple">{item.hotspot_list_labels.join(" / ")}</Tag>}
              {!isHotspotLeaderboard && <Tag color={displayTierColor(item.display_tier)}>{displayTierLabel(item.display_tier)}</Tag>}
              {item.relevance_basis && (
                <Tag color="green">{item.relevance_reason || "标题/话题命中"}</Tag>
              )}
              {!isHotspotLeaderboard && item.growth_stage && (
                <Tag color={growthStageColor(item.growth_stage)}>{item.growth_stage}</Tag>
              )}
              {item.media_resolution_status && <Tag>{statusLabel(item.media_resolution_status)}</Tag>}
              {!isHotspotLeaderboard && item.trend_level && <Tag color={item.trend_level === "观察中" ? "default" : "red"}>{item.trend_level}</Tag>}
            {item.anomaly_status && item.anomaly_status !== "normal" && <Tag color="warning">异常：{item.anomaly_status}</Tag>}
          </Space>
        }
        description={
          <Space direction="vertical" size={2}>
            <Text type="secondary">
              作者：{item.author_name}；{isHotspotLeaderboard ? `${hotspotLabel}新增播放量：${formatNumber(item.new_plays ?? item.plays)}；时长：${formatNumber(item.duration_seconds)} 秒` : `趋势分：${formatScore(item.trend_score)}`}
            </Text>
            {!isHotspotLeaderboard && <><Space align="center" size="small"><Text type="secondary">增长采样</Text><TrendSamplingStatus item={item} /></Space><CandidateTrendChart item={item} /></>}
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
            {!isHotspotLeaderboard && <Text type="secondary">走势：{item.snapshot_count && item.snapshot_count >= 3 ? "已确认" : "等待后续采样"}{item.next_recrawl_at ? `；下次采样 ${new Date(item.next_recrawl_at).toLocaleString("zh-CN")}` : ""}</Text>}
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
