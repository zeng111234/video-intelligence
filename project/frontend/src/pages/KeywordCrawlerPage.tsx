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
  WarningOutlined,
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
  previewCrawlerBatch,
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
  CrawlerPreviewResponse,
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
  const [hotspotWindowHours, setHotspotWindowHours] = useState<HotspotWindowHours>(168);
  const [executionRequest, setExecutionRequest] = useState<CrawlerSearchRequest | null>(null);
  const [preview, setPreview] = useState<CrawlerPreviewResponse | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
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
    count_per_platform: 10,
    force_refresh: false,
    mode: "smart",
    track_trend: false,
    target_main_count: 10,
    allow_paid_fallback: false,
    hotspot_result_limit: 100,
  }), [keyword, hotspotWindowHours]);
  const keywordLength = requestPayload.keyword.length;
  const canPreview = keywordLength >= 2 && keywordLength <= 50;
  const keywordHelp =
    keywordLength === 0
      ? "请先填写关键词；此按钮会先预览调用计划，弹窗确认后才执行爬取。"
        : !canPreview
        ? "关键词需为 2–50 个字符。"
          : "填写完成：下一步预览并确认榜单检索。";
  const isSandboxMode = capabilities?.mode === "sandbox";
  const hotspotReady = Boolean(hotspotBrowser?.ready_to_crawl);
  const hotspotMissing = hotspotBrowser?.missing_configuration || [];
  const hotspotNeedsPlaywright = hotspotMissing.includes("Playwright Python 依赖");
  const hotspotNeedsBrowser = hotspotMissing.includes("Google Chrome") || hotspotMissing.includes("Microsoft Edge");
  const crawlerDescription = hotspotReady
    ? `浏览器已通过本机授权：读取${hotspotWindowLabel(hotspotWindowHours)}五类视频榜，过滤图文/时长为 0 和新增播放量不超过 1,000 的内容；最多保留 100 条，按所选周期新增播放量排序。`
    : capabilities
      ? isSandboxMode
        ? "当前为 Sandbox 演示模式，不代表真实平台生产数据。"
        : "先打开浏览器并完成本机授权，即可读取当前可见榜单。"
      : "加载发现能力中。";
  const formFlowDescription = isSandboxMode
    ? "本页会先预览再确认执行；Sandbox 模式会写入演示数据，不代表真实平台生产数据。"
    : "填写关键词后先预览，再在弹窗中确认执行榜单检索。";

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

  const handlePreview = async () => {
    if (requestPayload.keyword.length < 2) {
      toast.warning("关键词需为 2–50 个字符");
      return;
    }
    setSubmitting(true);
    try {
      const resp = await previewCrawlerBatch(requestPayload);
      setPreview(resp);
      setExecutionRequest(requestPayload);
      setPreviewOpen(true);
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

  const handleExecute = async () => {
    if (!preview || !executionRequest) return;
    setSubmitting(true);
    try {
      const batch = await createCrawlerBatch(executionRequest);
      setSelectedBatch(batch);
      setPreviewOpen(false);
      setKeyword("");
      upsertBatch(batch);
      toast.success("爆款候选已写入 SQLite");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
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
        return <Tag color={v === "sandbox" ? "orange" : hotspot ? "magenta" : "blue"}>{hotspot ? `浏览器${hotspotWindowLabel(record.hotspot_window_hours)}五榜` : v}</Tag>;
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
        <Title level={4} style={{ margin: 0 }}>抖音关键词候选池</Title>
        <Text type="secondary">{crawlerDescription}</Text>
      </div>

      <Card title="创建搜索批次">
        <Alert
          style={{ marginBottom: 16 }}
          type="info"
          showIcon
          message="执行流程：填写关键词 → 预览调用计划 → 在弹窗中确认并执行爬取"
          description={formFlowDescription}
        />
        <Space wrap align="end">
          <div>
            <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>关键词</Text>
            <Input
              prefix={<SearchOutlined />}
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              onPressEnter={() => void handlePreview()}
              placeholder="例如：二手车"
              allowClear
              style={{ width: 260 }}
              status={keywordLength > 0 && !canPreview ? "error" : undefined}
            />
            <Text type={canPreview ? "secondary" : "warning"} style={{ display: "block", marginTop: 4 }}>
              {keywordHelp}
            </Text>
            {hotWords.length > 0 && (
              <div style={{ marginTop: 8, maxWidth: 520 }}>
                <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>
                  官方热点词建议（点击填入搜索词；默认从热词里选，减少冷门词搜不到热门视频的误解）
                </Text>
                <Space wrap size={[4, 4]}>
                  {hotWords.slice(0, 12).map((item) => (
                    <Tag
                      key={item.word}
                      style={{ cursor: "pointer" }}
                      color={keyword.trim() === item.word ? "gold" : undefined}
                      onClick={() => setKeyword(item.word)}
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
          <div>
            <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>榜单统计周期</Text>
            <Segmented
              value={hotspotWindowHours}
              options={HOTSPOT_WINDOW_OPTIONS}
              onChange={(value) => setHotspotWindowHours(value as HotspotWindowHours)}
            />
          </div>
          <Tooltip title={!canPreview ? keywordHelp : `扫描${hotspotWindowLabel(hotspotWindowHours)}五类爆款榜；过滤时长为 0 和新增播放量不超过 1,000 的内容，最多保留 100 条。`}>
            <Button type="primary" loading={submitting} disabled={!canPreview} onClick={handlePreview}>
              检索爆款榜
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
              <Text>{displayBrowserText(hotspotBrowser?.message) || "会打开独立 Chrome 窗口；请在其中扫码登录。系统仅读取已渲染的榜单元数据。"}</Text>
              {hotspotNeedsPlaywright ? (
                <Text type="secondary">请在项目根目录运行 <Text code>python -m pip install -r project/backend/requirements.txt</Text>，然后重启后端。</Text>
              ) : null}
              {hotspotNeedsBrowser ? (
                <Text type="secondary">请安装 {hotspotBrowser?.browser_channel === "msedge" ? "Microsoft Edge" : "Google Chrome"}，或在根目录 .env 中将 <Text code>DOUYIN_BROWSER_CHANNEL</Text> 改为已安装的浏览器后重启后端。</Text>
              ) : null}
              <Space wrap>
                <Tag color="purple">视频总榜</Tag>
                <Tag color="purple">低粉爆款</Tag>
                <Tag color="purple">高完播率</Tag>
                <Tag color="purple">高涨粉率</Tag>
                <Tag color="purple">高点赞率</Tag>
                <Tag>{hotspotWindowLabel(hotspotWindowHours)} · 五榜合并</Tag>
                <Button size="small" type="primary" loading={hotspotStarting} onClick={handleStartHotspot} disabled={hotspotBrowser?.enabled === false}>
                  {hotspotBrowser?.running ? "检查浏览器授权" : "打开浏览器并扫码"}
                </Button>
              </Space>
            </Space>
          }
        />
        <Text type="secondary" style={{ display: "block", marginTop: 12 }}>
          扫描{hotspotWindowLabel(hotspotWindowHours)}的五类视频榜，指标表示所选周期的新增播放、点赞等，不限制视频发布时间；过滤图文/时长为 0 与新增播放量不超过 1,000 的内容。最多保留 100 条并按所选周期新增播放量排序。五榜顺序采集约需 2–3 分钟；滚动刷新以约 600 毫秒为中心，在 450–850 毫秒间随机变化，页面渲染、搜索生效和榜单切换也使用随机等待。同关键词同周期缓存 10 分钟，每次真实采集完成后随机冷却 8–12 分钟，滚动 24 小时最多 48 次真实采集。关键词会以每字约 120–220 毫秒的速度逐字输入；遇到登录、安全验证或访问频繁会停止并提示人工处理。
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
        title="确认爆款榜检索"
        width={560}
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        onOk={handleExecute}
        okText="确认并发现候选"
        cancelText="取消"
        confirmLoading={submitting}
        okButtonProps={{ disabled: !preview || preview.blocked }}
      >
        {preview && (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Alert
              type={preview.provider_mode === "sandbox" ? "warning" : "info"}
              showIcon
              message={preview.hotspot_ready ? "浏览器榜单检索" : "浏览器等待连接"}
              description={preview.hotspot_ready
                ? `扫描${hotspotWindowLabel(preview.hotspot_window_hours)}，覆盖${preview.hotspot_list_types?.length || 5}类视频榜；时长大于 0 秒且新增播放量大于 1,000；最终最多保留 ${preview.hotspot_result_limit || 100} 条并按所选周期新增播放量排序。`
                : "连接浏览器后，即可扫描所选周期的五类视频榜。"}
            />
            {preview.crawl_safety && (
              <Alert
                type={preview.crawl_safety.state === "cached" || preview.crawl_safety.state === "ready" ? "success" : "warning"}
                showIcon
                message={preview.crawl_safety.state === "cached" ? "命中安全缓存" : "采集安全状态"}
                description={`${displayBrowserText(preview.crawl_safety.message)} 已用真实采集 ${preview.crawl_safety.real_runs_in_window ?? 0}/${preview.crawl_safety.real_run_limit ?? 48} 次。${preview.crawl_safety.cooldown_remaining_seconds > 0 ? ` 剩余约 ${Math.ceil(preview.crawl_safety.cooldown_remaining_seconds / 60)} 分钟。` : ""}`}
              />
            )}
            <List
              dataSource={preview.platforms.filter((item) => item.platform === "douyin_hotspot")}
              renderItem={(item) => (
                <List.Item>
                  <Space direction="vertical" size={2}>
                    <Space>
                      <Tag color="blue">浏览器榜单（本机授权）</Tag>
                      {item.cache_hit && <Tag color="cyan">缓存命中</Tag>}
                    </Space>
                    {item.blocked_reason && (
                      <Text type="danger"><WarningOutlined /> {displayBrowserText(item.blocked_reason)}</Text>
                    )}
                  </Space>
                </List.Item>
              )}
            />
          </Space>
        )}
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
  onGenerateOriginalScript,
  originalScriptLoadingId,
}: {
  batch: CrawlerBatchResponse;
  onStartTracking: (batch: CrawlerBatchResponse) => void;
  onCancelTracking: (batch: CrawlerBatchResponse) => void;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  mediaSubmitting: boolean;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
}) {
  const [rankingMode, setRankingMode] = useState<RankingMode>("total");
  const isHotspotBatch = batch.provider === "douyin_local_browser" || batch.monitoring_policy === "hotspot_single_snapshot_v1";

  return (
    <Card title={`批次详情：${batch.keyword}`} extra={<Tag color={STATUS_COLOR[batch.status]}>{statusLabel(batch.status)}</Tag>}>
      <Descriptions size="small" column={{ xs: 1, md: 4 }} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="批次ID">{batch.batch_id}</Descriptions.Item>
        <Descriptions.Item label={isHotspotBatch ? "榜单统计周期" : "发布时间"}>{isHotspotBatch ? `${hotspotWindowLabel(batch.hotspot_window_hours)}（浏览器榜单）` : batch.published_window_days === 0 ? "不限" : batch.published_window_days === 1 ? "近 24 小时（历史）" : "近 7 天（历史）"}</Descriptions.Item>
        <Descriptions.Item label={isHotspotBatch ? "最终保留" : "每平台"}>{batch.count_per_platform} 条</Descriptions.Item>
        <Descriptions.Item label="本批费用">¥{batch.total_estimated_cost_cny.toFixed(2)}</Descriptions.Item>
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
        {isHotspotBatch ? <Text type="secondary">按{hotspotWindowLabel(batch.hotspot_window_hours)}新增播放量降序 · 单次榜单，不复爬</Text> : <><Text type="secondary">榜单</Text><Segmented value={rankingMode} onChange={(value) => setRankingMode(value as RankingMode)} options={[{ label: "总榜", value: "total" }, { label: "爆发趋势", value: "trend" }]} /></>}
      </Space>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {batch.platform_runs.map((run) => (
          <PlatformRunDetail
            key={run.run_id}
            run={run}
            rankingMode={rankingMode}
            onResolveMedia={onResolveMedia}
            mediaSubmitting={mediaSubmitting}
            onCreateCandidateLinkTranscription={onCreateCandidateLinkTranscription}
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
  rankingMode,
  onResolveMedia,
  mediaSubmitting,
  onCreateCandidateLinkTranscription,
  onGenerateOriginalScript,
  originalScriptLoadingId,
}: {
  run: CrawlerPlatformRun;
  rankingMode: RankingMode;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  mediaSubmitting: boolean;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
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
  const lowIncrementalWindow = hotspotWindowLabel(lowIncrementalCandidates[0]?.hotspot_window_hours);

  return (
    <Card
      size="small"
      title={<Space><Tag color="blue">{run.platform_label}</Tag><Tag color={STATUS_COLOR[run.status]}>{statusLabel(run.status)}</Tag>{run.cache_hit && <Tag color="cyan">缓存</Tag>}</Space>}
    >
      {isHotspotRun ? (
        <Space wrap size={[6, 6]}>
          <Tag color="blue">保留 {run.relevant_count ?? run.returned_count} 条</Tag>
          <Tag>原始 / 解析 {run.raw_item_count} / {run.parsed_item_count}</Tag>
          <Tooltip title={`关键词不相关 ${run.irrelevant_count ?? 0}；时长≤0 ${run.duration_filtered_count ?? 0}；新增播放≤1,000 ${run.incremental_play_filtered_count ?? 0}；无效 ${run.invalid_count}；重复 ${run.duplicate_count}`}>
            <Tag>已过滤 {(run.irrelevant_count ?? 0) + (run.duration_filtered_count ?? 0) + (run.incremental_play_filtered_count ?? 0) + run.invalid_count + run.duplicate_count} 条</Tag>
          </Tooltip>
          <Tag>本地浏览器采集 · ¥0</Tag>
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
            message="这个关键词可能偏冷"
            description={`已找到 ${lowIncrementalCandidates.length} 条严格相关视频，但没有一条${lowIncrementalWindow}新增播放超过 1,000。建议换一个更具体或更热点的关键词；如仍需要参考低增量视频，已在下方列出，它们不会进入爆款主榜。`}
          />
          <HotspotCandidateTable
            candidates={lowIncrementalCandidates}
            onCreateCandidateLinkTranscription={onCreateCandidateLinkTranscription}
          />
        </Space>
      ) : run.candidates.length === 0 && isHotspotRun ? (
        <Alert
          style={{ marginTop: 16 }}
          type="warning"
          showIcon
          message="这个关键词可能偏冷"
          description="本次没有发现新增播放超过 1,000 的视频，也没有可列出的低增量相关视频。建议换一个更常见、更具体或当前更热点的关键词；如果你确认该词并不冷门，请检查浏览器页面是否已登录、需要人工验证，或页面结构是否发生变化。"
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
          onCreateCandidateLinkTranscription={onCreateCandidateLinkTranscription}
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
                      onResolveMedia={onResolveMedia}
                      mediaSubmitting={mediaSubmitting}
                      onCreateCandidateLinkTranscription={onCreateCandidateLinkTranscription}
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
  onCreateCandidateLinkTranscription,
}: {
  candidates: CrawlerCandidateResult[];
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
}) {
  const [listLabel, setListLabel] = useState("all");
  const [detail, setDetail] = useState<CrawlerCandidateResult | null>(null);
  const tableWindowHours = candidates.find((item) => item.hotspot_window_hours != null)?.hotspot_window_hours;
  const labels = useMemo(
    () => Array.from(new Set(candidates.flatMap((item) => item.hotspot_list_labels || []))),
    [candidates],
  );
  const rows = useMemo(
    () => candidates
      .filter((item) => listLabel === "all" || (item.hotspot_list_labels || []).includes(listLabel))
      .sort((a, b) => (b.new_plays ?? b.plays ?? 0) - (a.new_plays ?? a.plays ?? 0)),
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
      title: "新增播放",
      width: 118,
      align: "right",
      render: (_, item) => formatNumber(item.new_plays ?? item.plays),
    },
    {
      title: "新增点赞",
      width: 106,
      align: "right",
      render: (_, item) => formatNumber(item.new_likes ?? item.likes),
    },
    {
      title: "时长",
      width: 80,
      align: "right",
      render: (_, item) => item.duration_seconds ? `${formatNumber(item.duration_seconds)} 秒` : "未返回",
    },
    {
      title: "命中榜单",
      width: 165,
      render: (_, item) => (
        <Space wrap size={[2, 2]}>
          {(item.hotspot_list_labels || []).map((label) => <Tag color="purple" key={label}>{label}</Tag>)}
        </Space>
      ),
    },
    {
      title: "操作",
      width: 250,
      render: (_, item) => (
        <Space size={6}>
          <Tooltip title={!item.source_url ? "该候选没有可用的原视频链接。" : "进入转写页后确认内容处理权，再提取原视频文案。"}>
            <span>
              <Button
                size="small"
                type="primary"
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
        <Text type="secondary">按榜单筛选</Text>
        <Select
          size="small"
          value={listLabel}
          style={{ minWidth: 155 }}
          onChange={setListLabel}
          options={[{ value: "all", label: `全部榜单（${candidates.length}）` }, ...labels.map((label) => ({ value: label, label }))]}
        />
        <Text type="secondary">按{hotspotWindowLabel(tableWindowHours)}新增播放量降序 · 每页 10 条</Text>
      </Space>
      <Table
        rowKey="video_id"
        size="small"
        scroll={{ x: 1050 }}
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
              <Descriptions.Item label={`${hotspotWindowLabel(detail.hotspot_window_hours)}新增播放量`}>{formatNumber(detail.new_plays ?? detail.plays)}</Descriptions.Item>
              <Descriptions.Item label={`${hotspotWindowLabel(detail.hotspot_window_hours)}新增点赞量`}>{formatNumber(detail.new_likes ?? detail.likes)}</Descriptions.Item>
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
  onResolveMedia,
  mediaSubmitting,
  onCreateCandidateLinkTranscription,
  onGenerateOriginalScript,
  originalScriptLoading,
}: {
  item: CrawlerCandidateResult;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  mediaSubmitting: boolean;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
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
