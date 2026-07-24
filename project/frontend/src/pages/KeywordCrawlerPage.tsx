import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Input,
  List,
  Modal,
  Segmented,
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
  FireOutlined,
  ReloadOutlined,
  SearchOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import {
  createCrawlerBatch,
  createCrawlerCandidateLinkTranscription,
  createCrawlerLinkTranscription,
  createPipelineFromCandidate,
  deleteCrawlerBatch,
  executeDueCrawlerRecrawls,
  generateOriginalScript,
  getCrawlerBatch,
  getCrawlerCapabilities,
  getCrawlerHotWords,
  getCrawlerLinkTranscriptionCapabilities,
  listCrawlerBatches,
  officialHotMonitor,
  previewCrawlerCandidateMedia,
  previewCrawlerLinkTranscription,
  fallbackCrawlerLinkTranscription,
  previewCrawlerBatch,
} from "../api/client";
import type {
  CrawlerBatchResponse,
  CrawlerCandidateMediaPreviewResponse,
  CrawlerCandidateResult,
  CrawlerCapabilitiesResponse,
  CrawlerHotWordItem,
  CrawlerLinkTranscriptionCapabilities,
  CrawlerLinkTranscriptionPreview,
  CrawlerOfficialHotMonitorResponse,
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

function copySourceLabel(source: string | null | undefined) {
  const labels: Record<string, string> = {
    metadata_original: "平台信息生成文案",
    doubao_mobile_transcript: "已导入转写",
    authorized_asr_transcript: "授权 ASR 转写",
  };
  return source ? labels[source] || source : "未生成";
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
    no_hot: "已得到候选，但没有达到本产品的热门/潜力阈值；它们不会被标为爆款。",
  };
  return messages[run.result_state] || "本次没有可展示候选，请查看诊断和供应商响应。";
}

function metricEntries(item: CrawlerCandidateResult) {
  const entries: Array<[string, number]> = [];
  if (item.plays !== null && item.plays !== undefined && item.plays > 0) {
    entries.push(["播放", item.plays]);
  }
  for (const [label, value] of [
    ["点赞", item.likes],
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

export default function KeywordCrawlerPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [capabilities, setCapabilities] = useState<CrawlerCapabilitiesResponse | null>(null);
  const [keyword, setKeyword] = useState("");
  const [relatedTermsInput, setRelatedTermsInput] = useState("");
  const [forceRefresh, setForceRefresh] = useState(false);
  const [preview, setPreview] = useState<CrawlerPreviewResponse | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [batches, setBatches] = useState<CrawlerBatchResponse[]>([]);
  const [selectedBatch, setSelectedBatch] = useState<CrawlerBatchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [recrawling, setRecrawling] = useState(false);
  const [deletingBatchId, setDeletingBatchId] = useState<string | null>(null);
  const [mediaCandidate, setMediaCandidate] = useState<CrawlerCandidateResult | null>(null);
  const [mediaPreview, setMediaPreview] = useState<CrawlerCandidateMediaPreviewResponse | null>(null);
  const [mediaPreviewOpen, setMediaPreviewOpen] = useState(false);
  const [mediaSubmitting, setMediaSubmitting] = useState(false);
  const [rightsHolder, setRightsHolder] = useState("本人/公司已授权");
  const [mediaRightsConfirmed, setMediaRightsConfirmed] = useState(false);
  const [hotWords, setHotWords] = useState<CrawlerHotWordItem[]>([]);
  const [monitorLoading, setMonitorLoading] = useState(false);
  const [monitorResult, setMonitorResult] = useState<CrawlerOfficialHotMonitorResponse | null>(null);
  const [originalScript, setOriginalScript] = useState<{
    candidate: CrawlerCandidateResult;
    data: CrawlerOriginalScriptResponse;
  } | null>(null);
  const [originalScriptLoadingId, setOriginalScriptLoadingId] = useState<string | null>(null);
  const [linkCapabilities, setLinkCapabilities] = useState<CrawlerLinkTranscriptionCapabilities | null>(null);
  const [shareText, setShareText] = useState("");
  const [linkPreview, setLinkPreview] = useState<CrawlerLinkTranscriptionPreview | null>(null);
  const [linkRightsConfirmed, setLinkRightsConfirmed] = useState(false);
  const [linkSubmitting, setLinkSubmitting] = useState(false);
  const [candidateLinkSubmittingId, setCandidateLinkSubmittingId] = useState<string | null>(null);

  const requestPayload = useMemo<CrawlerSearchRequest>(() => ({
    keyword: keyword.trim(),
    published_window_days: 0,
    count_per_platform: 10,
    force_refresh: forceRefresh,
    mode: "smart",
    related_terms: relatedTermsInput
      .split(/[，,、\n]/)
      .map((item) => item.trim())
      .filter((item, index, values) => item.length >= 2 && values.indexOf(item) === index)
      .slice(0, 5),
  }), [keyword, forceRefresh, relatedTermsInput]);
  const keywordLength = requestPayload.keyword.length;
  const canPreview = keywordLength >= 2 && keywordLength <= 50;
  const keywordHelp =
    keywordLength === 0
      ? "请先填写关键词；此按钮会先预览调用计划，弹窗确认后才执行爬取。"
        : !canPreview
          ? "关键词需为 2–50 个字符。"
          : "填写完成：下一步会预览抖音缓存、额度和费用。";
  const isSandboxMode = capabilities?.mode === "sandbox";
  const crawlerDescription = capabilities
    ? isSandboxMode
      ? "通过 FastAPI 调用 CommercialSearchService，当前为 Sandbox 演示模式，不代表真实平台生产数据。"
      : `通过 FastAPI 调用 ${capabilities.display_name}，当前为 Production 模式；请使用小流量关键词验证真实响应字段与授权边界。`
    : "通过 FastAPI 调用 CommercialSearchService，结果持久化到 SQLite。";
  const formFlowDescription = isSandboxMode
    ? "为了避免误触发平台调用或重复计费，本页不会在第一次点击按钮时直接爬取。Sandbox 模式会写入演示数据，但不代表真实平台生产数据。"
    : "为了避免误触发真实平台调用或重复计费，本页会先预览再执行。当前自动化试运行只调用抖音，小红书和视频号不会产生请求或费用。";

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [caps, list, linkCaps] = await Promise.all([
        getCrawlerCapabilities(),
        listCrawlerBatches(),
        getCrawlerLinkTranscriptionCapabilities(),
      ]);
      // 官方热点词建议：后端未上线或拉取失败时降级为空，不影响主流程
      const hotWordsResp = await getCrawlerHotWords().catch(() => ({ words: [] as CrawlerHotWordItem[] }));
      setCapabilities(caps);
      setLinkCapabilities(linkCaps);
      setHotWords(hotWordsResp.words);
      setBatches(list.items);
      if (selectedBatch) {
        const detail = await getCrawlerBatch(selectedBatch.batch_id);
        setSelectedBatch(detail);
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [selectedBatch?.batch_id, toast]);

  const handlePreviewShareLink = async () => {
    if (!shareText.trim()) {
      toast.warning("请粘贴抖音分享链接");
      return;
    }
    setLinkSubmitting(true);
    try {
      const item = await previewCrawlerLinkTranscription(shareText);
      setLinkPreview(item);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLinkSubmitting(false);
    }
  };

  const handleCreateShareLink = async (fallback = false) => {
    if (!linkPreview || !linkRightsConfirmed) {
      toast.warning("请先确认拥有内容处理权");
      return;
    }
    setLinkSubmitting(true);
    try {
      const result = fallback
        ? await fallbackCrawlerLinkTranscription({ shareText, workId: linkPreview.work_id || "", rightsHolder, rightsConfirmed: true, idempotencyKey: `oneapi-link-${Date.now()}-${Math.random().toString(16).slice(2)}` })
        : await createCrawlerLinkTranscription({ shareText, rightsHolder, rightsConfirmed: true });
      if (result.status === "fallback_required") {
        toast.warning(result.message);
        return;
      }
      if (result.transcription) {
        toast.success(result.message);
        navigate(`/transcription?task=${encodeURIComponent(result.transcription.task_id)}`);
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLinkSubmitting(false);
    }
  };

  const handleCreateCandidateLinkTranscription = async (candidate: CrawlerCandidateResult) => {
    if (!candidate.source_url) {
      toast.warning("该候选没有可用的原视频链接");
      return;
    }
    const submit = async () => {
      setCandidateLinkSubmittingId(candidate.video_id);
      try {
        const result = await createCrawlerCandidateLinkTranscription({
          candidateId: candidate.video_id,
          rightsHolder,
          rightsConfirmed: true,
        });
        if (result.status === "fallback_required") {
          toast.warning(`${result.message} 未产生解析费用；如确有必要，请单独使用“付费自动解析”。`);
          return;
        }
        if (result.transcription) {
          toast.success(result.message);
          await refresh();
          navigate(`/transcription?task=${encodeURIComponent(result.transcription.task_id)}`);
        }
      } catch (err) {
        toast.error((err as Error).message);
      } finally {
        setCandidateLinkSubmittingId(null);
      }
    };
    if (linkRightsConfirmed) {
      await submit();
      return;
    }
    Modal.confirm({
      title: "确认免费提取原文案",
      content: (
        <Space direction="vertical" size={6}>
          <Text>将从“{candidate.title}”的公开原视频链接读取音轨，并在本机转写；不会调用 OneAPI 或云端 ASR。</Text>
          <Text type="secondary">权利主体：{rightsHolder}</Text>
          <Text type="secondary">确认后即表示你有权处理该内容；转写完成后仍需人工校对。</Text>
        </Space>
      ),
      okText: "确认并免费提取",
      cancelText: "取消",
      onOk: async () => {
        setLinkRightsConfirmed(true);
        await submit();
      },
    });
  };

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handlePreview = async () => {
    if (requestPayload.keyword.length < 2) {
      toast.warning("关键词需为 2–50 个字符");
      return;
    }
    setSubmitting(true);
    try {
      const resp = await previewCrawlerBatch(requestPayload);
      setPreview(resp);
      setPreviewOpen(true);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleExecute = async () => {
    if (!preview) return;
    setSubmitting(true);
    try {
      const batch = await createCrawlerBatch(requestPayload);
      setSelectedBatch(batch);
      setPreviewOpen(false);
      setKeyword("");
      toast.success("爆款候选已写入 SQLite");
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleExecuteDueRecrawls = async () => {
    setRecrawling(true);
    try {
      const resp = await executeDueCrawlerRecrawls();
      if (resp.total === 0) {
        toast.info("当前没有到期复爬计划");
      } else {
        setSelectedBatch(resp.executed_batches[0]);
        toast.success(`已执行 ${resp.total} 个到期复爬批次`);
      }
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setRecrawling(false);
    }
  };

  const handleDeleteBatch = (batch: CrawlerBatchResponse) => {
    Modal.confirm({
      title: "删除这条历史批次？",
      content: `将删除“${batch.keyword}”的本次搜索记录和关联运行记录；候选视频数据会保留。`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        setDeletingBatchId(batch.batch_id);
        try {
          await deleteCrawlerBatch(batch.batch_id);
          if (selectedBatch?.batch_id === batch.batch_id) {
            setSelectedBatch(null);
          }
          toast.success("历史批次已删除");
          await refresh();
        } catch (err) {
          toast.error((err as Error).message);
        } finally {
          setDeletingBatchId(null);
        }
      },
    });
  };

  const handleOfficialHotMonitor = async () => {
    setMonitorLoading(true);
    try {
      const kw = requestPayload.keyword;
      const resp = await officialHotMonitor(kw.length >= 2 && kw.length <= 50 ? kw : undefined);
      setMonitorResult(resp);
      if (resp.matched_count > 0) {
        toast.success(`官方热榜匹配 ${resp.matched_count} 条候选`);
      } else {
        toast.info(resp.result_message || resp.result_state || "官方热榜无匹配");
      }
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setMonitorLoading(false);
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
      await refresh();
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
    { title: "模式", dataIndex: "mode", width: 100, render: (v: string) => <Tag color={v === "sandbox" ? "orange" : "blue"}>{v}</Tag> },
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

      <Card title="抖音链接转文案（本机实验）">
        <Space direction="vertical" style={{ width: "100%" }} size={12}>
          <Alert
            type={linkCapabilities?.parser_enabled ? "info" : "warning"}
            showIcon
            message={linkCapabilities?.parser_enabled ? "单条分享链接将走本机实验解析，再进入原话转写" : "本机实验解析器未就绪"}
            description={linkCapabilities?.parser_enabled
              ? "仅处理单条、已获授权的抖音分享链接；成功后进入 校对 → 去重 → 合规优化 三步工作区。"
              : `${linkCapabilities?.parser_message || "请先配置本机解析器。"} 解析失败后可在本页明确确认再使用 OneAPI，预计 ¥${(linkCapabilities?.oneapi_estimated_cost_cny || 0).toFixed(2)} / 条。`}
          />
          <Input.TextArea value={shareText} onChange={(event) => { setShareText(event.target.value); setLinkPreview(null); }} rows={3} placeholder="粘贴抖音分享文案或 v.douyin.com 分享链接" />
          <Space wrap>
            <Input value={rightsHolder} onChange={(event) => setRightsHolder(event.target.value)} addonBefore="权利主体" style={{ width: 280 }} />
            <Checkbox checked={linkRightsConfirmed} onChange={(event) => setLinkRightsConfirmed(event.target.checked)}>我确认有权处理此内容</Checkbox>
            <Button loading={linkSubmitting} onClick={handlePreviewShareLink}>识别链接</Button>
          </Space>
          {linkPreview && <Alert type="info" showIcon message={`已识别作品 ID：${linkPreview.work_id || "未识别"}`} description={
            <Space wrap>
              <Button type="primary" loading={linkSubmitting} disabled={!linkPreview.parser_enabled || !linkRightsConfirmed} onClick={() => handleCreateShareLink(false)}>本机解析并转写</Button>
              {linkPreview.oneapi_fallback_available && <Button danger loading={linkSubmitting} disabled={!linkRightsConfirmed} onClick={() => Modal.confirm({ title: "确认使用 OneAPI 回退", content: `本次预计 ¥${(linkPreview.oneapi_estimated_cost_cny || 0).toFixed(2)}，确认后才会调用。`, okText: "确认并继续", onOk: () => handleCreateShareLink(true) })}>确认后用 OneAPI 回退</Button>}
            </Space>
          } />}
        </Space>
      </Card>

      {capabilities && (
        <Card title="供应商与额度状态">
          <Descriptions size="small" column={{ xs: 1, md: 3 }}>
            <Descriptions.Item label="供应商">{capabilities.display_name}</Descriptions.Item>
            <Descriptions.Item label="模式">
              <Tag color={capabilities.mode === "sandbox" ? "orange" : capabilities.mode === "local_browser" ? "green" : capabilities.enabled ? "blue" : "red"}>
                {capabilities.mode === "sandbox" ? "Sandbox" : capabilities.mode === "local_browser" ? "本机浏览器" : capabilities.enabled ? "Production" : "未配置"}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="支持平台">
              {capabilities.supported_platform_labels.length
                ? capabilities.supported_platform_labels.join(" / ")
                : "未开放"}
            </Descriptions.Item>
            <Descriptions.Item label="当前自动化">
              {capabilities.active_platform_labels.join(" / ") || "未启用"}
            </Descriptions.Item>
            <Descriptions.Item label="本阶段暂停">
              {capabilities.paused_platform_labels.join(" / ") || "无"}
            </Descriptions.Item>
            <Descriptions.Item label="本地调用量">
              {capabilities.monthly_query_count} / {capabilities.monthly_hard_limit_queries}
            </Descriptions.Item>
            <Descriptions.Item label="OneAPI 成功请求">
              {capabilities.usage?.platform_queries ?? "未返回"}
            </Descriptions.Item>
            <Descriptions.Item label="OneAPI 用量费用">
              {formatCurrency(capabilities.usage?.estimated_cost, capabilities.usage?.currency)}
            </Descriptions.Item>
            <Descriptions.Item label="本地预算占用">
              ¥{capabilities.monthly_estimated_cost_cny.toFixed(2)} / ¥{capabilities.monthly_hard_limit_cost_cny.toFixed(2)}
            </Descriptions.Item>
            <Descriptions.Item label="缓存 TTL">{capabilities.cache_ttl_minutes} 分钟</Descriptions.Item>
            <Descriptions.Item label="权限状态">{capabilities.permission_status}</Descriptions.Item>
            {capabilities.official_hot_billboard && (
              <Descriptions.Item label="官方热榜">
                <Tag color={capabilities.official_hot_billboard.enabled ? "green" : "default"}>
                  {capabilities.official_hot_billboard.enabled ? "已启用" : "未启用"}
                </Tag>
                {capabilities.official_hot_billboard.provider_name}
              </Descriptions.Item>
            )}
            {capabilities.official_hot_words && (
              <Descriptions.Item label="官方热点词">
                <Tag color={capabilities.official_hot_words.enabled ? "green" : "default"}>
                  {capabilities.official_hot_words.enabled ? "已启用" : "未启用"}
                </Tag>
                {capabilities.official_hot_words.provider_name}
              </Descriptions.Item>
            )}
          </Descriptions>
          {(capabilities.official_hot_billboard?.missing_configuration?.length ||
            capabilities.official_hot_words?.missing_configuration?.length) ? (
            <Alert
              style={{ marginTop: 12 }}
              type="warning"
              showIcon
              message="官方热榜/热点词未配置完整"
              description="请在项目根目录的 .env 填写 DOUYIN_CLIENT_KEY 与 DOUYIN_CLIENT_SECRET，然后重启后端服务。凭证在抖音开放平台创建应用后获取；未配置不影响 OneAPI 搜索发现。"
            />
          ) : null}
          {capabilities.missing_configuration.length > 0 && (
            <Alert
              style={{ marginTop: 12 }}
              type="warning"
              showIcon
              message="供应商未配置完整"
              description={capabilities.missing_configuration.join("；")}
            />
          )}
          {capabilities.enabled && capabilities.permission_status === "trial_unverified_commercial_rights" && (
            <Alert
              style={{ marginTop: 12 }}
              type="warning"
              showIcon
              message="待完成小流量验收与授权确认"
              description="OneAPI Key 已配置；当前仍处于试点状态，请用小流量关键词核对真实响应字段，并确认 B 端展示与派生分析的商业授权边界。"
            />
          )}
          {capabilities.paused_platforms.length > 0 && (
            <Alert
              style={{ marginTop: 12 }}
              type="info"
              showIcon
              message="先跑通抖音单平台闭环"
              description={`${capabilities.paused_platform_labels.join("、")} 本阶段不爬取，不产生供应商调用和费用；历史批次仍可查看。`}
            />
          )}
        </Card>
      )}

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
              onPressEnter={handlePreview}
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
            <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>相关赛道词（可选）</Text>
            <Input
              value={relatedTermsInput}
              onChange={(event) => setRelatedTermsInput(event.target.value)}
              placeholder="例如：数字人，口播"
              style={{ width: 210 }}
            />
            <Text type="secondary" style={{ display: "block", marginTop: 4 }}>
              最多 5 个，只扩展官方免费池召回。
            </Text>
          </div>
          <Checkbox checked={forceRefresh} onChange={(event) => setForceRefresh(event.target.checked)}>
            强制刷新
          </Checkbox>
          <Tooltip title={!canPreview ? keywordHelp : "先查官方免费池；不足时按预览计划调用 OneAPI，并在 6/24 小时后完成本地趋势采样"}>
            <Button type="primary" loading={submitting} disabled={!canPreview} onClick={handlePreview}>
              用 OneAPI 发现爆款
            </Button>
          </Tooltip>
          <Tooltip title="一次点击完成：先执行到期复爬，再同步官方热榜并按关键词匹配，返回匹配数与下一次复爬时间">
            <Button
              type="primary"
              ghost
              icon={<FireOutlined />}
              loading={monitorLoading}
              onClick={handleOfficialHotMonitor}
            >
              官方热榜一键监测
            </Button>
          </Tooltip>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={refresh}>
            刷新
          </Button>
          <Button loading={recrawling} onClick={handleExecuteDueRecrawls}>
            执行到期复爬
          </Button>
        </Space>
        <Text type="secondary" style={{ display: "block", marginTop: 12 }}>
          默认先匹配官方热榜与本地快照；免费候选不足 3 条时，按确认后的调用计划使用 OneAPI 搜索。OneAPI 后台自动复爬默认关闭，不会自行产生搜索费用。
        </Text>
      </Card>

      {monitorResult && (
        <Card
          title="官方热榜一键监测结果"
          extra={
            <Button size="small" onClick={() => setMonitorResult(null)}>
              收起
            </Button>
          }
        >
          <Alert
            style={{ marginBottom: 12 }}
            type={monitorResult.matched_count > 0 ? "success" : "info"}
            showIcon
            message={monitorResult.result_state}
            description={
              <Space direction="vertical" size={2}>
                <Text>{monitorResult.result_message}</Text>
                {monitorResult.matched_count === 0 &&
                  (monitorResult.result_state.includes("官方热榜无匹配") ||
                    monitorResult.result_state === "official_hot_no_match") && (
                    <Text type="warning">{OFFICIAL_HOT_NO_MATCH_MESSAGE}</Text>
                  )}
              </Space>
            }
          />
          <Descriptions size="small" column={{ xs: 1, md: 4 }} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="匹配候选数">{monitorResult.matched_count}</Descriptions.Item>
            <Descriptions.Item label="本次执行复爬">{monitorResult.executed_recrawls} 个</Descriptions.Item>
            <Descriptions.Item label="下一次复爬">
              {monitorResult.next_recrawl_at
                ? new Date(monitorResult.next_recrawl_at).toLocaleString("zh-CN")
                : "暂无计划"}
            </Descriptions.Item>
            <Descriptions.Item label="结果状态">{monitorResult.result_state}</Descriptions.Item>
          </Descriptions>
          {monitorResult.candidates.length > 0 && (
            <List
              dataSource={monitorResult.candidates}
              renderItem={(item) => (
                <CandidateListItem
                  item={item}
                  onResolveMedia={handleOpenCandidateMedia}
                  mediaSubmitting={mediaSubmitting}
                  onCreateCandidateLinkTranscription={handleCreateCandidateLinkTranscription}
                  candidateLinkSubmitting={candidateLinkSubmittingId === item.video_id}
                  linkParserEnabled={linkCapabilities?.parser_enabled === true}
                  linkParserMessage={linkCapabilities?.parser_message || undefined}
                  onGenerateOriginalScript={handleGenerateOriginalScript}
                  originalScriptLoading={originalScriptLoadingId === item.video_id}
                />
              )}
            />
          )}
        </Card>
      )}

      {selectedBatch && (
        <BatchDetail
          batch={selectedBatch}
          onResolveMedia={handleOpenCandidateMedia}
          mediaSubmitting={mediaSubmitting}
          onCreateCandidateLinkTranscription={handleCreateCandidateLinkTranscription}
          candidateLinkSubmittingId={candidateLinkSubmittingId}
          linkParserEnabled={linkCapabilities?.parser_enabled === true}
          linkParserMessage={linkCapabilities?.parser_message || undefined}
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
        title="确认 OneAPI 发现计划"
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
              message={preview.mode === "smart" ? "智能发现模式：官方免费池优先" : `当前模式：${preview.provider_mode === "sandbox" ? "Sandbox 演示" : preview.provider_mode}`}
              description={preview.mode === "smart"
                ? `免费官方池当前匹配 ${preview.free_candidate_count || 0} 条；${preview.paid_fallback_required ? "不足 3 条，将对抖音不限发布时间调用 OneAPI 检索，并在 6 / 24 小时后完成真实采样" : "已满足候选阈值，不会调用 OneAPI"}；完整监测最多调用：${preview.platforms.reduce((sum, item) => sum + item.estimated_api_calls, 0)}；预计费用上限 ¥${preview.estimated_total_cost_cny.toFixed(2)}。`
                : `不限发布时间；真实采样：${(preview.sampling_offsets_hours || [0, 6, 24]).join(" / ")} 小时；预计新增调用：${preview.platforms.reduce((sum, item) => sum + item.estimated_api_calls, 0)}；预计费用 ¥${preview.estimated_total_cost_cny.toFixed(2)}；本月已用 ${preview.monthly_query_count}/${preview.monthly_hard_limit_queries}，本地费用 ¥${preview.monthly_estimated_cost_cny.toFixed(2)}/¥${preview.monthly_hard_limit_cost_cny.toFixed(2)}`}
            />
            {preview.paid_fallback_blocked_reason && (
              <Alert type="warning" showIcon message={`低价兜底暂不可用：${preview.paid_fallback_blocked_reason}`} />
            )}
            <List
              dataSource={preview.platforms}
              renderItem={(item) => (
                <List.Item>
                  <Space direction="vertical" size={2}>
                    <Space>
                      <Tag color="blue">{item.platform_label}</Tag>
                      {item.cache_hit ? <Tag color="cyan">缓存命中</Tag> : <Tag>需查询</Tag>}
                      <Tag>预计调用 {item.estimated_api_calls}</Tag>
                      <Tag>单价 {item.platform_unit_price_cny === null ? "未知" : `¥${item.platform_unit_price_cny.toFixed(2)}`}</Tag>
                      <Tag>预计费用 {item.estimated_cost_cny === null ? "未知" : `¥${item.estimated_cost_cny.toFixed(2)}`}</Tag>
                    </Space>
                    {item.blocked_reason && (
                      <Text type="danger"><WarningOutlined /> {item.blocked_reason}</Text>
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
  onResolveMedia,
  mediaSubmitting,
  onCreateCandidateLinkTranscription,
  candidateLinkSubmittingId,
  linkParserEnabled,
  linkParserMessage,
  onGenerateOriginalScript,
  originalScriptLoadingId,
}: {
  batch: CrawlerBatchResponse;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  mediaSubmitting: boolean;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
  candidateLinkSubmittingId: string | null;
  linkParserEnabled: boolean;
  linkParserMessage?: string;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
}) {
  const [rankingMode, setRankingMode] = useState<RankingMode>("total");
  const batchCandidates = batch.platform_runs.flatMap((run) => run.candidates);

  return (
    <Card title={`批次详情：${batch.keyword}`} extra={<Tag color={STATUS_COLOR[batch.status]}>{statusLabel(batch.status)}</Tag>}>
      <Descriptions size="small" column={{ xs: 1, md: 4 }} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="批次ID">{batch.batch_id}</Descriptions.Item>
        <Descriptions.Item label="发布时间">{batch.published_window_days === 0 ? "不限" : batch.published_window_days === 1 ? "近 24 小时（历史）" : "近 7 天（历史）"}</Descriptions.Item>
        <Descriptions.Item label="每平台">{batch.count_per_platform} 条</Descriptions.Item>
        <Descriptions.Item label="强制刷新">{batch.force_refresh ? "是" : "否"}</Descriptions.Item>
        <Descriptions.Item label="本批费用">¥{batch.total_estimated_cost_cny.toFixed(2)}</Descriptions.Item>
        <Descriptions.Item label="采样节奏">{(batch.sampling_offsets_hours || [0, 6, 24]).join(" / ")} 小时</Descriptions.Item>
        {batch.mode === "smart" && <Descriptions.Item label="免费池候选">{batch.free_candidate_count || 0} 条</Descriptions.Item>}
        {batch.mode === "smart" && <Descriptions.Item label="付费兜底">{batch.paid_fallback_used ? "已使用" : batch.paid_fallback_blocked_reason ? "不可用，已保留免费结果" : "未使用"}</Descriptions.Item>}
      </Descriptions>
      {batch.mode === "smart" && batch.related_terms && batch.related_terms.length > 0 && (
        <Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
          本次相关赛道词：{batch.related_terms.join("、")}
        </Text>
      )}
      <Space style={{ marginBottom: 12 }} wrap>
        <Text type="secondary">榜单</Text>
        <Segmented
          value={rankingMode}
          onChange={(value) => setRankingMode(value as RankingMode)}
          options={[
            { label: "总榜", value: "total" },
            { label: "爆发趋势", value: "trend" },
          ]}
        />
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
            candidateLinkSubmittingId={candidateLinkSubmittingId}
            linkParserEnabled={linkParserEnabled}
            linkParserMessage={linkParserMessage}
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
  candidateLinkSubmittingId,
  linkParserEnabled,
  linkParserMessage,
  onGenerateOriginalScript,
  originalScriptLoadingId,
}: {
  run: CrawlerPlatformRun;
  rankingMode: RankingMode;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  mediaSubmitting: boolean;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
  candidateLinkSubmittingId: string | null;
  linkParserEnabled: boolean;
  linkParserMessage?: string;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoadingId: string | null;
}) {
  const totalRanked = [...run.candidates]
    .sort((a, b) => (b.effective_interactions ?? 0) - (a.effective_interactions ?? 0));
  const candidates = rankingMode === "total"
    ? totalRanked
    : totalRanked
      .filter((item) => (item.valid_snapshot_count ?? 0) >= 3)
      .sort((a, b) => (b.trend_score ?? -1) - (a.trend_score ?? -1));

  return (
    <Card
      size="small"
      title={<Space><Tag color="blue">{run.platform_label}</Tag><Tag color={STATUS_COLOR[run.status]}>{statusLabel(run.status)}</Tag>{run.cache_hit && <Tag color="cyan">缓存</Tag>}</Space>}
    >
      <Descriptions size="small" column={{ xs: 1, md: 4 }}>
        <Descriptions.Item label="严格相关">{run.relevant_count ?? run.returned_count}/{run.requested_count}</Descriptions.Item>
        <Descriptions.Item label="原始 / 解析">{run.raw_item_count} / {run.parsed_item_count}</Descriptions.Item>
        <Descriptions.Item label="过滤">关键词不相关 {run.irrelevant_count ?? 0} · 超时窗 {run.out_of_window_count} · 无效 {run.invalid_count} · 重复 {run.duplicate_count}</Descriptions.Item>
        <Descriptions.Item label="API 调用">{run.api_call_count}</Descriptions.Item>
        <Descriptions.Item label="额度">{run.quota_remaining ?? "未返回"}</Descriptions.Item>
        <Descriptions.Item label="估算费用">{formatCurrency(run.billable_units)}</Descriptions.Item>
      </Descriptions>
      {run.error && <Alert style={{ marginTop: 12 }} type="error" showIcon message={run.error} />}
      {run.payload_diagnostic && <Alert style={{ marginTop: 12 }} type="warning" showIcon message="供应商响应诊断" description={run.payload_diagnostic} />}
      {run.candidates.length === 0 ? (
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
            : "当前尚未完成首次、6 小时、24 小时三次采样；系统不会用单点数据模拟趋势。"}
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
                      candidateLinkSubmitting={candidateLinkSubmittingId === item.video_id}
                      linkParserEnabled={linkParserEnabled}
                      linkParserMessage={linkParserMessage}
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

function CandidateListItem({
  item,
  onResolveMedia,
  mediaSubmitting,
  onCreateCandidateLinkTranscription,
  candidateLinkSubmitting,
  linkParserEnabled,
  linkParserMessage,
  onGenerateOriginalScript,
  originalScriptLoading,
}: {
  item: CrawlerCandidateResult;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  mediaSubmitting: boolean;
  onCreateCandidateLinkTranscription: (candidate: CrawlerCandidateResult) => void;
  candidateLinkSubmitting: boolean;
  linkParserEnabled: boolean;
  linkParserMessage?: string;
  onGenerateOriginalScript: (candidate: CrawlerCandidateResult) => void;
  originalScriptLoading: boolean;
}) {
  const componentEntries = Object.entries(item.component_scores || {});
  const metrics = metricEntries(item);
  const shouldShowConfidence = item.confidence !== null && item.confidence !== undefined && item.confidence >= 0.6;
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
          <Tooltip title={!item.source_url ? "该候选没有可用原视频链接。" : !linkParserEnabled ? (linkParserMessage || "本机免费解析器未就绪。") : "读取公开分享页并在本机转写，不调用 OneAPI。"}>
            <span>
              <Button
                type="link"
                size="small"
                loading={candidateLinkSubmitting}
                disabled={!item.source_url || !linkParserEnabled}
                onClick={() => onCreateCandidateLinkTranscription(item)}
              >
                免费提取原文案
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
              <Tag color={displayTierColor(item.display_tier)}>
                {displayTierLabel(item.display_tier)}
              </Tag>
              {item.relevance_basis && (
                <Tag color="green">{item.relevance_reason || "标题/话题命中"}</Tag>
              )}
              {item.growth_stage && (
                <Tag color={growthStageColor(item.growth_stage)}>{item.growth_stage}</Tag>
              )}
              {item.media_resolution_status && <Tag>{statusLabel(item.media_resolution_status)}</Tag>}
              {item.trend_level && <Tag color={item.trend_level === "观察中" ? "default" : "red"}>{item.trend_level}</Tag>}
            {item.anomaly_status && item.anomaly_status !== "normal" && <Tag color="warning">异常：{item.anomaly_status}</Tag>}
          </Space>
        }
        description={
          <Space direction="vertical" size={2}>
            <Text type="secondary">
              作者：{item.author_name}；趋势分：{formatScore(item.trend_score)}
              {shouldShowConfidence ? `；置信度：${item.confidence}` : ""}
              ；样本池：{item.pool_size ?? "暂无"}；复搜：{item.recrawl_count ?? 0}/2
              ；加权增长：{item.engagement_growth_per_hour ?? "等待复爬"}
              ；有效互动：{formatNumber(item.effective_interactions)}
            </Text>
            <Text type="secondary">
              快照：{item.valid_snapshot_count ?? 0}；再次召回：{item.recall_count ?? 0}
              ；漏采：{item.missed_checkpoint_count ?? 0}
              ；跨度：{item.sampling_span_hours === null || item.sampling_span_hours === undefined ? "暂无" : `${item.sampling_span_hours} 小时`}
              ；加速度：{item.acceleration_ratio === null || item.acceleration_ratio === undefined ? "暂无" : `${item.acceleration_ratio}x`}
            </Text>
            <Space align="center" size="small">
              <Text type="secondary">增长采样</Text>
              <TrendSamplingStatus item={item} />
            </Space>
            {metrics.length > 0 && (
              <Text type="secondary">
                {metrics.map(([label, value]) => `${label}：${formatNumber(value)}`).join("；")}
              </Text>
            )}
            <Text type="secondary">
              文案来源：{copySourceLabel(item.copy_source)}
              ；原版转写：{item.is_original_transcript === undefined ? "未知" : item.is_original_transcript ? "是" : "否"}
              ；人工复核：{item.needs_manual_review === undefined ? "未知" : item.needs_manual_review ? "需要" : "不需要"}
              ；下一次复爬：{item.next_recrawl_at ? new Date(item.next_recrawl_at).toLocaleString("zh-CN") : "暂无"}
              {item.snapshot_count !== undefined && item.snapshot_count !== null ? `；快照数：${item.snapshot_count}` : ""}
            </Text>
            {(item.share_count !== undefined || item.collect_count !== undefined) && (
              <Text type="secondary">
                分享：{formatNumber(item.share_count)}；收藏：{formatNumber(item.collect_count)}
              </Text>
            )}
            {componentEntries.length > 0 && (
              <Space wrap size={[4, 4]}>
                {componentEntries.map(([name, value]) => (
                  <Tag key={name}>{name}: {value === null ? "暂无" : value.toFixed(1)}</Tag>
                ))}
              </Space>
            )}
            {item.data_quality_warnings.length > 0 && (
              <Space wrap size={[4, 4]}>
                {item.data_quality_warnings.map((warning) => (
                  <Tag key={warning} color="warning">{warning}</Tag>
                ))}
              </Space>
            )}
            {item.model_version && <Text type="secondary">模型：{item.model_version}</Text>}
            {item.evidence && <Text type="secondary">依据：{item.evidence}</Text>}
            {item.reasons.length > 0 && (
              <Paragraph style={{ margin: 0 }}>
                {item.reasons.map((reason, index) => (
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
