import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
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
  LinkOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import {
  createCrawlerBatch,
  createPipelineFromCandidate,
  deleteCrawlerBatch,
  generateOriginalScript,
  getCrawlerBatch,
  getCrawlerCapabilities,
  getCrawlerHotWords,
  listCrawlerBatches,
  previewCrawlerCandidateMedia,
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

type HotspotWindowHours = 1 | 24 | 72 | 168;
type BrowserPlatform = "douyin" | "xiaohongshu" | "kuaishou" | "bilibili";

const HOTSPOT_WINDOW_OPTIONS: Array<{ label: string; value: HotspotWindowHours }> = [
  { label: "近1小时", value: 1 },
  { label: "近1天", value: 24 },
  { label: "近3天", value: 72 },
  { label: "近7天", value: 168 },
];

function hotspotWindowLabel(hours: number | null | undefined) {
  return HOTSPOT_WINDOW_OPTIONS.find((item) => item.value === hours)?.label || "近7天";
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

export default function KeywordCrawlerPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [capabilities, setCapabilities] = useState<CrawlerCapabilitiesResponse | null>(null);
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
  const [hotWords, setHotWords] = useState<CrawlerHotWordItem[]>([]);
  const [originalScript, setOriginalScript] = useState<{
    candidate: CrawlerCandidateResult;
    data: CrawlerOriginalScriptResponse;
  } | null>(null);
  const [originalScriptLoadingId, setOriginalScriptLoadingId] = useState<string | null>(null);
  const [connectionDrawerOpen, setConnectionDrawerOpen] = useState(false);
  const [connectingPlatform, setConnectingPlatform] = useState<BrowserPlatform | null>(null);

  const requestPayload = useMemo<CrawlerSearchRequest>(() => ({
    keyword: keyword.trim(),
    published_window_days: 0,
    hotspot_window_hours: hotspotWindowHours,
    count_per_platform: 30,
    force_refresh: false,
    mode: "smart",
    track_trend: false,
    target_main_count: 30,
    allow_paid_fallback: false,
    hotspot_result_limit: 30,
  }), [keyword, hotspotWindowHours]);
  const keywordLength = requestPayload.keyword.length;
  const canSearch = keywordLength >= 2 && keywordLength <= 50;
  const keywordHelp =
    keywordLength === 0
      ? "输入一个词，马上开始找素材。"
        : !canSearch
        ? "关键词需为 2–50 个字符。"
          : "每个平台最多保留30条：抖音爆款榜、小红书、快手和B站都可作为免费候选来源。";
  const isSandboxMode = capabilities?.mode === "sandbox";
  const crawlerDescription = capabilities
    ? isSandboxMode
      ? "当前为 Sandbox 演示模式，不代表真实平台生产数据。"
      : "输入一个关键词，系统会自动打开热点宝、小红书、快手和B站的公开页面并开始找素材。"
    : "加载发现能力中。";
  const browserConnections = useMemo(() => [
    capabilities?.hotspot_browser,
    ...(capabilities?.platform_browsers ?? []),
  ].filter((item): item is CrawlerBrowserDiscoveryCapabilities => Boolean(item)), [capabilities]);
  const readyBrowserCount = browserConnections.filter((item) => item.ready_to_crawl).length;

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

  const handleCreateCandidateLinkTranscription = async (candidate: CrawlerCandidateResult) => {
    const supportedPlatforms = new Set(["douyin", "xiaohongshu", "kuaishou", "bilibili"]);
    if (!supportedPlatforms.has(candidate.platform)) {
      toast.info("视频号暂不能自动解析；请上传有权处理的视频文件。");
      return;
    }
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
      toast.success("已找到近期候选；选一条即可进入原创文案或后续创作");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
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
        if (platform === "douyin") {
          return { ...current, hotspot_browser: started };
        }
        return {
          ...current,
          platform_browsers: (current.platform_browsers ?? []).map((item) => (
            item.platform === platform ? started : item
          )),
        };
      });
      toast.success(started.ready_to_crawl
        ? `${started.platform_label}已连接`
        : `${started.platform_label}浏览器已在后台打开，请从任务栏完成登录`);
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

      <Card
        title="你想做什么内容？"
        extra={(
          <Button icon={<LinkOutlined />} onClick={() => setConnectionDrawerOpen(true)}>
            {capabilities ? `账号连接（${readyBrowserCount}/4）` : "账号连接"}
          </Button>
        )}
      >
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
        <Text type="secondary" style={{ display: "block", marginTop: 12 }}>
          点“找素材”后浏览器会自动打开并开始搜索；热点宝不限制作品发布时间，小红书选择“最多点赞、视频、半年内”，快手保留近10个月，B站选择“最多播放、最近一周”。每个平台最多保留30条。
        </Text>
      </Card>

      <Drawer
        title="账号连接"
        open={connectionDrawerOpen}
        onClose={() => setConnectionDrawerOpen(false)}
        width={420}
      >
        <Paragraph type="secondary">
          平时直接点“找素材”即可。只有平台提示未登录时，才需要在这里连接一次；登录状态会保留在本机。
        </Paragraph>
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          {browserConnections.map((connection) => {
            const ready = Boolean(connection.ready_to_crawl);
            const waitingLogin = Boolean(connection.running && connection.login_required);
            const status = ready ? "已连接" : waitingLogin ? "等待登录" : "未连接";
            const color = ready ? "success" : waitingLogin ? "warning" : "default";
            const platform = connection.platform as BrowserPlatform | undefined;
            return (
              <Card
                key={connection.platform || connection.provider_name}
                size="small"
                title={connection.platform_label || connection.provider_name}
                extra={<Tag color={color}>{status}</Tag>}
              >
                <Paragraph type="secondary" style={{ marginBottom: 12 }}>
                  {ready ? "已可找素材。" : connection.message}
                </Paragraph>
                <Button
                  type={ready ? "default" : "primary"}
                  loading={platform !== undefined && connectingPlatform === platform}
                  disabled={!connection.enabled || !platform}
                  onClick={() => void handleStartBrowserConnection(connection)}
                >
                  {ready ? "打开浏览器" : `连接${connection.platform_label || "平台"}`}
                </Button>
              </Card>
            );
          })}
          {!browserConnections.length && (
            <Alert type="info" showIcon message="正在读取账号连接状态" />
          )}
        </Space>
      </Drawer>

      {selectedBatch && (
        <BatchDetail
          batch={selectedBatch}
          onResolveMedia={handleOpenCandidateMedia}
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
    </Space>
  );
}

function BatchDetail({
  batch,
  onResolveMedia,
  onCreateCandidateLinkTranscription,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
}: {
  batch: CrawlerBatchResponse;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
}) {
  const isHotspotBatch = batch.provider === "douyin_local_browser" || batch.monitoring_policy === "hotspot_single_snapshot_v1";
  const isFreeMultiPlatformBatch = batch.monitoring_policy === "free_single_snapshot_v1";
  const isSingleSnapshotBatch = isHotspotBatch || isFreeMultiPlatformBatch;

  return (
    <Card title={`本次素材：${batch.keyword}`} extra={<Tag color={STATUS_COLOR[batch.status]}>{statusLabel(batch.status)}</Tag>}>
      <Descriptions size="small" column={{ xs: 1, md: 4 }} style={{ marginBottom: 16 }}>
        {!isSingleSnapshotBatch && <Descriptions.Item label="批次ID">{batch.batch_id}</Descriptions.Item>}
        <Descriptions.Item label={isHotspotBatch ? "搜索范围" : isFreeMultiPlatformBatch ? "平台规则" : "发布时间"}>{isHotspotBatch ? "视频榜、话题榜、抖音搜索" : isFreeMultiPlatformBatch ? "热点宝不限 · 小红书近半年 · B站近一周 · 快手近10个月" : batch.published_window_days === 0 ? "不限" : batch.published_window_days === 1 ? "近 24 小时（历史）" : batch.published_window_days === 3 ? "近 3 天（历史）" : batch.published_window_days === 180 ? "近半年（历史）" : batch.published_window_days === 300 ? "近 10 个月（历史）" : "近 7 天（历史）"}</Descriptions.Item>
        <Descriptions.Item label={isSingleSnapshotBatch ? "本次候选目标" : "每平台"}>{batch.count_per_platform} 条</Descriptions.Item>
        {!isSingleSnapshotBatch && <Descriptions.Item label="本批费用">¥{batch.total_estimated_cost_cny.toFixed(2)}</Descriptions.Item>}
        {batch.mode === "smart" && <Descriptions.Item label="免费来源候选">{batch.free_candidate_count || 0} 条</Descriptions.Item>}
        {batch.mode === "smart" && <Descriptions.Item label="付费接口">{batch.paid_fallback_used ? "已使用" : "未调用 OneAPI"}</Descriptions.Item>}
      </Descriptions>
      <Space style={{ marginBottom: 12 }} wrap>
        <Text type="secondary">选一条生成原创文案或送入后续创作；每次搜索只抓取一次。</Text>
      </Space>
      {isFreeMultiPlatformBatch ? (
        <UnifiedPlatformResults
          runs={batch.platform_runs}
          batchId={batch.batch_id}
          onResolveMedia={onResolveMedia}
          onCreateCandidateLinkTranscription={onCreateCandidateLinkTranscription}
          onSendToWorkspace={onSendToWorkspace}
          onGenerateOriginalScript={onGenerateOriginalScript}
          originalScriptLoadingId={originalScriptLoadingId}
        />
      ) : (
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {batch.platform_runs.map((run) => (
            <PlatformRunDetail
              key={run.run_id}
              run={run}
              batchId={batch.batch_id}
              onResolveMedia={onResolveMedia}
              onCreateCandidateLinkTranscription={onCreateCandidateLinkTranscription}
              onSendToWorkspace={onSendToWorkspace}
              onGenerateOriginalScript={onGenerateOriginalScript}
              originalScriptLoadingId={originalScriptLoadingId}
            />
          ))}
        </Space>
      )}
    </Card>
  );
}

function emptyRunSummary(run: CrawlerPlatformRun) {
  if (run.raw_item_count > 0 && run.parsed_item_count === 0) {
    return `${run.platform_label}发现 ${run.raw_item_count} 条页面结果，但当时没有解析出可校验的标题和发布时间，未进入候选榜。`;
  }
  if (run.parsed_item_count > 0) {
    const filtered = [
      run.out_of_window_count ? `时间不符 ${run.out_of_window_count}` : "",
      run.irrelevant_count ? `关键词不符 ${run.irrelevant_count}` : "",
      run.below_heat_floor_count ? `热度不足 ${run.below_heat_floor_count}` : "",
      run.invalid_count ? `字段无效 ${run.invalid_count}` : "",
      run.duplicate_count ? `重复 ${run.duplicate_count}` : "",
    ].filter(Boolean);
    return `${run.platform_label}解析 ${run.parsed_item_count} 条，${filtered.length ? `其中${filtered.join("、")}` : "没有符合本次条件的内容"}，未进入候选榜。`;
  }
  return `${run.platform_label}没有返回搜索结果，可能需要登录或完成平台验证。`;
}

function UnifiedPlatformResults({
  runs,
  batchId,
  onResolveMedia,
  onCreateCandidateLinkTranscription,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
}: {
  runs: CrawlerPlatformRun[];
  batchId: string;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
}) {
  const candidates = runs.flatMap((run) => run.candidates);
  const emptyRuns = runs.filter((run) => run.candidates.length === 0);

  return (
    <Card
      size="small"
      title={`多平台候选榜（${candidates.length}）`}
      extra={<Text type="secondary">最多选择 30 条</Text>}
    >
      <Space wrap size={[6, 6]} style={{ marginBottom: emptyRuns.length ? 10 : 4 }}>
        {runs.map((run) => {
          const visibleCount = run.candidates.length;
          const label = visibleCount > 0
            ? `${run.platform_label} ${visibleCount} 条`
            : run.raw_item_count > 0
              ? `${run.platform_label} 发现 ${run.raw_item_count} 条 · 0 条符合`
              : `${run.platform_label} 暂无结果`;
          return (
            <Tooltip
              key={run.run_id}
              title={visibleCount > 0 ? `已加入统一候选榜 ${visibleCount} 条。` : emptyRunSummary(run)}
            >
              <Tag color={visibleCount > 0 ? "success" : run.raw_item_count > 0 ? "warning" : "default"}>
                {label}
              </Tag>
            </Tooltip>
          );
        })}
      </Space>
      {emptyRuns.length > 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: candidates.length ? 8 : 0 }}
          message="部分平台结果没有混入候选榜"
          description={emptyRuns.map(emptyRunSummary).join(" ")}
        />
      )}
      {candidates.length > 0 ? (
        <List
          dataSource={candidates}
          renderItem={(item) => (
            <CandidateListItem
              item={item}
              batchId={batchId}
              onResolveMedia={onResolveMedia}
              onCreateCandidateLinkTranscription={onCreateCandidateLinkTranscription}
              onSendToWorkspace={onSendToWorkspace}
              onGenerateOriginalScript={onGenerateOriginalScript}
              originalScriptLoading={originalScriptLoadingId === item.video_id}
            />
          )}
        />
      ) : (
        <Alert
          type="warning"
          showIcon
          message="本次没有符合条件的素材"
          description="换一个更常见的关键词，或完成上方提示的平台登录后再找。"
        />
      )}
    </Card>
  );
}

function PlatformRunDetail({
  run,
  batchId,
  onResolveMedia,
  onCreateCandidateLinkTranscription,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoadingId,
}: {
  run: CrawlerPlatformRun;
  batchId: string;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
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
  onCreateCandidateLinkTranscription,
  onSendToWorkspace,
  onGenerateOriginalScript,
  originalScriptLoading,
}: {
  item: CrawlerCandidateResult;
  batchId: string;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
  onSendToWorkspace: (batchId: string, candidate: CrawlerCandidateResult) => void;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoading: boolean;
}) {
  const isHotspotLeaderboard = item.evidence?.startsWith("hotspot:") ?? false;
  const hotspotLabel = hotspotWindowLabel(item.hotspot_window_hours);
  const metrics = metricEntries(item, isHotspotLeaderboard);
  const displayedReasons = item.reasons.filter(
    (reason) => !/(按发布时间折算|快照|复爬|复搜|采样|增长|趋势|小时|分位 P\d+)/.test(reason),
  );
  const linkTranscriptionAvailable = ["douyin", "xiaohongshu", "kuaishou", "bilibili"].includes(item.platform);
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
          <Tooltip title={!item.source_url ? "该候选没有可用原视频链接。" : !linkTranscriptionAvailable ? "视频号暂不能自动解析，请上传有权处理的视频文件。" : item.platform === "douyin" ? "在转写页确认授权、识别链接并选择本机解析；只有抖音可明确确认付费回退。" : "在转写页确认授权，并使用已连接的平台专用浏览器解析。"}>
            <span>
              <Button
                type="link"
                size="small"
                disabled={!item.source_url || !linkTranscriptionAvailable}
                onClick={() => onCreateCandidateLinkTranscription(item)}
              >
                {linkTranscriptionAvailable ? "去转写页提取文案" : "请上传文件"}
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
              {item.relevance_basis && (
                <Tag color="green">{item.relevance_reason || "标题/话题命中"}</Tag>
              )}
              {item.media_resolution_status && <Tag>{statusLabel(item.media_resolution_status)}</Tag>}
          </Space>
        }
        description={
          <Space direction="vertical" size={2}>
            <Text type="secondary">
              作者：{item.author_name}{isHotspotLeaderboard ? `；${hotspotLabel}新增播放量：${formatNumber(item.new_plays ?? item.plays)}；时长：${formatNumber(item.duration_seconds)} 秒` : ""}
            </Text>
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
