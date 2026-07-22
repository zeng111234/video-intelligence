import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Input,
  InputNumber,
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
  EyeOutlined,
  ReloadOutlined,
  SearchOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import {
  createCrawlerBatch,
  createCrawlerDoubaoMobileJobs,
  createPipelineFromCandidate,
  executeDueCrawlerRecrawls,
  getCrawlerBatch,
  getCrawlerCapabilities,
  getCrawlerDoubaoMobileCapabilities,
  listCrawlerBatches,
  listCrawlerDoubaoMobileJobs,
  previewCrawlerCandidateMedia,
  previewCrawlerBatch,
  retryCrawlerDoubaoMobileJob,
  startCrawlerDoubaoMobileWorker,
} from "../api/client";
import type {
  CrawlerBatchResponse,
  CrawlerCandidateMediaPreviewResponse,
  CrawlerCandidateResult,
  CrawlerCapabilitiesResponse,
  CrawlerDoubaoMobileCapabilitiesResponse,
  CrawlerDoubaoJobResponse,
  CrawlerPlatformRun,
  CrawlerPreviewResponse,
  CrawlerSearchRequest,
} from "../api/types";
import { useToast } from "../components/Toast";
import { Link, useNavigate } from "react-router-dom";

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

type RankingMode = "provider" | "system";

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
    ordinary: "普通相关",
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

function resultStateMessage(run: CrawlerPlatformRun) {
  const messages: Record<string, string> = {
    provider_empty: "供应商本次返回 0 条原始候选；这不是正常的关键词搜索结果，建议先核对供应商响应与用量。",
    provider_payload_invalid: "供应商有响应但未识别出候选列表；请核对响应结构与供应商接口变更。",
    all_out_of_window: `供应商返回了 ${run.raw_item_count} 条，但全部早于本次时间范围，未额外翻页以避免增加费用。`,
    all_invalid: "供应商返回的候选全部未通过平台、链接或去重校验，请核对供应商字段。",
    no_hot: "已得到候选，但没有达到本产品的热门/潜力阈值；它们不会被标为爆款。",
  };
  return messages[run.result_state] || "本次没有可展示候选，请查看诊断和供应商响应。";
}

function isDirectVideoUrl(url: string | null) {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return /\.(mp4|mov)(\?|$)/i.test(parsed.pathname + parsed.search);
  } catch {
    return false;
  }
}

function isDouyinShortShareUrl(url: string | null) {
  if (!url) return false;
  try {
    return new URL(url).hostname === "v.douyin.com";
  } catch {
    return false;
  }
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

export default function KeywordCrawlerPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [capabilities, setCapabilities] = useState<CrawlerCapabilitiesResponse | null>(null);
  const [keyword, setKeyword] = useState("");
  const [publishedWindowDays, setPublishedWindowDays] = useState<1 | 7>(7);
  const [countPerPlatform, setCountPerPlatform] = useState(10);
  const [forceRefresh, setForceRefresh] = useState(false);
  const [preview, setPreview] = useState<CrawlerPreviewResponse | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [batches, setBatches] = useState<CrawlerBatchResponse[]>([]);
  const [selectedBatch, setSelectedBatch] = useState<CrawlerBatchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [recrawling, setRecrawling] = useState(false);
  const [mediaCandidate, setMediaCandidate] = useState<CrawlerCandidateResult | null>(null);
  const [mediaPreview, setMediaPreview] = useState<CrawlerCandidateMediaPreviewResponse | null>(null);
  const [mediaPreviewOpen, setMediaPreviewOpen] = useState(false);
  const [mediaSubmitting, setMediaSubmitting] = useState(false);
  const [doubaoJobs, setDoubaoJobs] = useState<CrawlerDoubaoJobResponse[]>([]);
  const [doubaoMobileCapabilities, setDoubaoMobileCapabilities] = useState<CrawlerDoubaoMobileCapabilitiesResponse | null>(null);
  const [doubaoSubmitting, setDoubaoSubmitting] = useState(false);
  const [doubaoWorkerStarting, setDoubaoWorkerStarting] = useState(false);
  const [rightsHolder, setRightsHolder] = useState("本人/公司已授权");
  const [mediaRightsConfirmed, setMediaRightsConfirmed] = useState(false);

  const requestPayload = useMemo<CrawlerSearchRequest>(() => ({
    keyword: keyword.trim(),
    published_window_days: publishedWindowDays,
    count_per_platform: countPerPlatform,
    force_refresh: forceRefresh,
  }), [keyword, publishedWindowDays, countPerPlatform, forceRefresh]);
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
      const [caps, list, jobs] = await Promise.all([
        getCrawlerCapabilities(),
        listCrawlerBatches(),
        listCrawlerDoubaoMobileJobs(),
      ]);
      const mobileCaps = await getCrawlerDoubaoMobileCapabilities();
      setCapabilities(caps);
      setDoubaoMobileCapabilities(mobileCaps);
      setBatches(list.items);
      setDoubaoJobs(jobs.items);
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
      toast.success("抖音关键词批次已写入 SQLite");
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

  const handleCopyDoubaoPrompt = async (candidate: CrawlerCandidateResult) => {
    if (!candidate.source_url) {
      toast.warning("当前候选没有可复制的视频链接");
      return;
    }
    const hasShortShareUrl = isDouyinShortShareUrl(candidate.source_url);
    const prompt = hasShortShareUrl
      ? [
          candidate.title,
          candidate.source_url,
          "请把这个抖音视频转成原版口播文案，尽量保留原话、口语停顿和段落，不要改写；如果无法读取视频，请说明无法访问链接。",
        ].join("\n")
      : [
          candidate.title,
          "抖音分享短链：【先打开系统里的“原视频”，点击抖音页面的“分享/复制链接”，把生成的 v.douyin.com 短链替换到这里；不要直接发送系统长链接】",
          "请把这个抖音视频转成原版口播文案，尽量保留原话、口语停顿和段落，不要改写；如果无法读取视频，请说明无法访问链接。",
        ].join("\n");
    try {
      await navigator.clipboard.writeText(prompt);
      toast.success(
        hasShortShareUrl
          ? "已复制豆包提示词，粘贴到豆包后把结果回填到本系统即可"
          : "已复制豆包模板；请先从抖音分享按钮复制短链并替换模板中的占位说明"
      );
    } catch {
      toast.error("复制失败，请手动复制原视频链接");
    }
  };

  const startDoubaoWorker = async () => {
    setDoubaoWorkerStarting(true);
    try {
      const resp = await startCrawlerDoubaoMobileWorker();
      toast.success(resp.message);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setDoubaoWorkerStarting(false);
    }
  };

  const handleCreateDoubaoJobs = async (candidates: CrawlerCandidateResult[]) => {
    const douyinCandidates = candidates.filter((item) => item.platform === "douyin");
    if (douyinCandidates.length === 0) {
      toast.warning("当前没有可自动提取的抖音候选");
      return;
    }
    setDoubaoSubmitting(true);
    try {
      const resp = await createCrawlerDoubaoMobileJobs(douyinCandidates.map((item) => item.video_id));
      setDoubaoJobs((current) => {
        const byTaskId = new Map(current.map((item) => [item.task_id, item]));
        for (const item of resp.items) byTaskId.set(item.task_id, item);
        return Array.from(byTaskId.values());
      });
      toast.success(`已创建 ${resp.total} 个手机豆包零成本任务`);
      await startDoubaoWorker();
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setDoubaoSubmitting(false);
    }
  };

  const handleRetryDoubaoJob = async (taskId: string) => {
    setDoubaoSubmitting(true);
    try {
      await retryCrawlerDoubaoMobileJob(taskId);
      toast.success("已重新排队，执行器会自动接管");
      await startDoubaoWorker();
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setDoubaoSubmitting(false);
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
      width: 90,
      render: (_, record) => (
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
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Title level={4} style={{ margin: 0 }}>抖音关键词候选池</Title>
        <Text type="secondary">{crawlerDescription}</Text>
      </div>

      {capabilities && (
        <Card title="供应商与额度状态">
          <Descriptions size="small" column={{ xs: 1, md: 3 }}>
            <Descriptions.Item label="供应商">{capabilities.display_name}</Descriptions.Item>
            <Descriptions.Item label="模式">
              <Tag color={capabilities.mode === "sandbox" ? "orange" : capabilities.enabled ? "blue" : "red"}>
                {capabilities.mode === "sandbox" ? "Sandbox" : capabilities.enabled ? "Production" : "未配置"}
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
          </Descriptions>
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
          {doubaoMobileCapabilities && (
            <Alert
              style={{ marginTop: 12 }}
              type={doubaoMobileCapabilities.enabled ? "success" : "warning"}
              showIcon
              message="手机豆包零成本链路"
              description={
                doubaoMobileCapabilities.enabled
                  ? `已配置安卓执行器：${doubaoMobileCapabilities.worker_mode}；豆包包名 ${doubaoMobileCapabilities.android_package}`
                  : `未配置完整：${doubaoMobileCapabilities.missing_configuration.join("、")}。任务可排队，但启动后会失败并显示原因。`
              }
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
          </div>
          <div>
            <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>时间范围</Text>
            <Select
              value={publishedWindowDays}
              onChange={setPublishedWindowDays}
              style={{ width: 140 }}
              options={[
                { value: 1, label: "近 24 小时" },
                { value: 7, label: "近 7 天" },
              ]}
            />
          </div>
          <div>
            <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>抖音返回条数</Text>
            <InputNumber
              min={1}
              max={10}
              value={countPerPlatform}
              onChange={(value) => setCountPerPlatform(value || 10)}
              style={{ width: 120 }}
            />
          </div>
          <Checkbox checked={forceRefresh} onChange={(event) => setForceRefresh(event.target.checked)}>
            强制刷新
          </Checkbox>
          <Tooltip title={!canPreview ? keywordHelp : "先检查缓存命中、预计新增调用和阻断原因"}>
            <Button type="primary" loading={submitting} disabled={!canPreview} onClick={handlePreview}>
              预览并确认
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
          首次搜索只生成候选和复爬计划；近 24 小时按 2/6/12 小时复爬，近 7 天按 6/24/48 小时复爬，满 3 次复爬后才进入热门/潜力判断。
        </Text>
      </Card>

      {selectedBatch && (
        <BatchDetail
          batch={selectedBatch}
          onCopyDoubaoPrompt={handleCopyDoubaoPrompt}
          onCreateDoubaoJobs={handleCreateDoubaoJobs}
          onRetryDoubaoJob={handleRetryDoubaoJob}
          doubaoJobs={doubaoJobs}
          doubaoSubmitting={doubaoSubmitting || doubaoWorkerStarting}
          onResolveMedia={handleOpenCandidateMedia}
          mediaSubmitting={mediaSubmitting}
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
        title="确认抖音搜索计划"
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        onOk={handleExecute}
        okText="确认并执行爬取"
        cancelText="取消"
        confirmLoading={submitting}
        okButtonProps={{ disabled: !preview || preview.blocked }}
      >
        {preview && (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Alert
              type={preview.provider_mode === "sandbox" ? "warning" : "info"}
              showIcon
              message={`当前模式：${preview.provider_mode === "sandbox" ? "Sandbox 演示" : preview.provider_mode}`}
              description={`排行模式：${preview.ranking_mode}；预计新增调用：${preview.platforms.reduce((sum, item) => sum + item.estimated_api_calls, 0)}；预计费用 ¥${preview.estimated_total_cost_cny.toFixed(2)}；本月已用 ${preview.monthly_query_count}/${preview.monthly_hard_limit_queries}，本地费用 ¥${preview.monthly_estimated_cost_cny.toFixed(2)}/¥${preview.monthly_hard_limit_cost_cny.toFixed(2)}`}
            />
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
  onCopyDoubaoPrompt,
  onCreateDoubaoJobs,
  onRetryDoubaoJob,
  doubaoJobs,
  doubaoSubmitting,
  onResolveMedia,
  mediaSubmitting,
}: {
  batch: CrawlerBatchResponse;
  onCopyDoubaoPrompt: (candidate: CrawlerCandidateResult) => void;
  onCreateDoubaoJobs: (candidates: CrawlerCandidateResult[]) => void;
  onRetryDoubaoJob: (taskId: string) => void;
  doubaoJobs: CrawlerDoubaoJobResponse[];
  doubaoSubmitting: boolean;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  mediaSubmitting: boolean;
}) {
  const [rankingMode, setRankingMode] = useState<RankingMode>("provider");
  const batchCandidates = batch.platform_runs.flatMap((run) => run.candidates);
  const douyinCandidates = batchCandidates.filter((candidate) => candidate.platform === "douyin");

  return (
    <Card title={`批次详情：${batch.keyword}`} extra={<Tag color={STATUS_COLOR[batch.status]}>{statusLabel(batch.status)}</Tag>}>
      <Descriptions size="small" column={{ xs: 1, md: 4 }} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="批次ID">{batch.batch_id}</Descriptions.Item>
        <Descriptions.Item label="范围">{batch.published_window_days === 1 ? "近 24 小时" : "近 7 天"}</Descriptions.Item>
        <Descriptions.Item label="每平台">{batch.count_per_platform} 条</Descriptions.Item>
        <Descriptions.Item label="强制刷新">{batch.force_refresh ? "是" : "否"}</Descriptions.Item>
        <Descriptions.Item label="本批费用">¥{batch.total_estimated_cost_cny.toFixed(2)}</Descriptions.Item>
      </Descriptions>
      <Space style={{ marginBottom: 12 }}>
        <Text type="secondary">展示顺序</Text>
        <Segmented
          value={rankingMode}
          onChange={(value) => setRankingMode(value as RankingMode)}
          options={[
            { label: "供应商搜索排序", value: "provider" },
            { label: "系统评估排行", value: "system" },
          ]}
        />
        <Button
          type="primary"
          loading={doubaoSubmitting}
          disabled={douyinCandidates.length === 0}
          onClick={() => onCreateDoubaoJobs(douyinCandidates)}
        >
          本批手机豆包免费提取
        </Button>
        <Text type="secondary">¥0；安卓测试机打开抖音复制分享短链，再发送豆包 App</Text>
      </Space>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {batch.platform_runs.map((run) => (
          <PlatformRunDetail
            key={run.run_id}
            run={run}
            rankingMode={rankingMode}
            onCopyDoubaoPrompt={onCopyDoubaoPrompt}
            onCreateDoubaoJobs={onCreateDoubaoJobs}
            onRetryDoubaoJob={onRetryDoubaoJob}
            doubaoJobs={doubaoJobs}
            doubaoSubmitting={doubaoSubmitting}
            onResolveMedia={onResolveMedia}
            mediaSubmitting={mediaSubmitting}
          />
        ))}
      </Space>
    </Card>
  );
}

function PlatformRunDetail({
  run,
  rankingMode,
  onCopyDoubaoPrompt,
  onCreateDoubaoJobs,
  onRetryDoubaoJob,
  doubaoJobs,
  doubaoSubmitting,
  onResolveMedia,
  mediaSubmitting,
}: {
  run: CrawlerPlatformRun;
  rankingMode: RankingMode;
  onCopyDoubaoPrompt: (candidate: CrawlerCandidateResult) => void;
  onCreateDoubaoJobs: (candidates: CrawlerCandidateResult[]) => void;
  onRetryDoubaoJob: (taskId: string) => void;
  doubaoJobs: CrawlerDoubaoJobResponse[];
  doubaoSubmitting: boolean;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  mediaSubmitting: boolean;
}) {
  const candidates = [...run.candidates].sort((a, b) => {
    if (rankingMode === "system") {
      return (a.system_rank ?? 999) - (b.system_rank ?? 999);
    }
    return (a.provider_hot_rank ?? a.platform_rank ?? 999) - (b.provider_hot_rank ?? b.platform_rank ?? 999);
  });

  return (
    <Card
      size="small"
      title={<Space><Tag color="blue">{run.platform_label}</Tag><Tag color={STATUS_COLOR[run.status]}>{statusLabel(run.status)}</Tag>{run.cache_hit && <Tag color="cyan">缓存</Tag>}</Space>}
    >
      <Descriptions size="small" column={{ xs: 1, md: 4 }}>
        <Descriptions.Item label="返回">{run.returned_count}/{run.requested_count}</Descriptions.Item>
        <Descriptions.Item label="原始 / 解析">{run.raw_item_count} / {run.parsed_item_count}</Descriptions.Item>
        <Descriptions.Item label="过滤">超时窗 {run.out_of_window_count} · 无效 {run.invalid_count} · 重复 {run.duplicate_count}</Descriptions.Item>
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
      ) : (
        <Space direction="vertical" style={{ width: "100%", marginTop: 12 }} size="middle">
          {([
            ["exploding", "爆发候选", "volcano"],
            ["hot", "热门候选", "red"],
            ["potential", "潜力候选", "gold"],
            ["observing", "观察样本", "blue"],
            ["ordinary", "普通相关候选", "default"],
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
                      onCopyDoubaoPrompt={onCopyDoubaoPrompt}
                      onCreateDoubaoJobs={onCreateDoubaoJobs}
                      onRetryDoubaoJob={onRetryDoubaoJob}
                      doubaoJob={doubaoJobs.find((job) => job.candidate_id === item.video_id)}
                      doubaoSubmitting={doubaoSubmitting}
                      onResolveMedia={onResolveMedia}
                      mediaSubmitting={mediaSubmitting}
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
  onCopyDoubaoPrompt,
  onCreateDoubaoJobs,
  onRetryDoubaoJob,
  doubaoJob,
  doubaoSubmitting,
  onResolveMedia,
  mediaSubmitting,
}: {
  item: CrawlerCandidateResult;
  onCopyDoubaoPrompt: (candidate: CrawlerCandidateResult) => void;
  onCreateDoubaoJobs: (candidates: CrawlerCandidateResult[]) => void;
  onRetryDoubaoJob: (taskId: string) => void;
  doubaoJob: CrawlerDoubaoJobResponse | undefined;
  doubaoSubmitting: boolean;
  onResolveMedia: (candidate: CrawlerCandidateResult) => void;
  mediaSubmitting: boolean;
}) {
  const componentEntries = Object.entries(item.component_scores || {});
  const metrics = metricEntries(item);
  const shouldShowConfidence = item.confidence !== null && item.confidence !== undefined && item.confidence >= 0.6;
  const directVideoUrl = isDirectVideoUrl(item.source_url);
  const transcriptionUrl = directVideoUrl
    ? `/transcription?candidate=${encodeURIComponent(item.video_id)}&url=${encodeURIComponent(item.source_url || "")}`
    : `/transcription?candidate=${encodeURIComponent(item.video_id)}&title=${encodeURIComponent(item.title)}&source_url=${encodeURIComponent(item.source_url || "")}&manual=1`;
  const studioUrl = `/studio?candidate_id=${encodeURIComponent(item.video_id)}`;
  const automationPaused = item.platform !== "douyin" && !item.media_transcription_task_id;
  const doubaoStatus = doubaoJob?.status;
  const doubaoTaskUrl = doubaoJob ? `/transcription?task=${encodeURIComponent(doubaoJob.task_id)}` : "";

  return (
    <List.Item
      actions={[
        item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer">原视频</a> : <Text type="secondary">无原视频链接</Text>,
        doubaoJob?.status === "succeeded" ? (
          <Link to={doubaoTaskUrl}>查看豆包文案</Link>
        ) : doubaoJob?.status === "failed" ? (
          <Button
            type="link"
            size="small"
            loading={doubaoSubmitting}
            onClick={() => onRetryDoubaoJob(doubaoJob.task_id)}
          >
            重试手机豆包提取
          </Button>
        ) : doubaoJob ? (
          <Text type="secondary">手机豆包：{statusLabel(doubaoJob.status)}</Text>
        ) : (
          <Button
            type="link"
            size="small"
            loading={doubaoSubmitting}
            disabled={item.platform !== "douyin" || !item.source_url}
            onClick={() => onCreateDoubaoJobs([item])}
          >
            手机豆包免费提取
          </Button>
        ),
        item.source_url ? (
          <Button type="link" size="small" onClick={() => onCopyDoubaoPrompt(item)}>
            手动豆包模板
          </Button>
        ) : (
          <Text type="secondary">无豆包链接</Text>
        ),
        item.media_transcription_task_id ? (
          <Button type="link" size="small" onClick={() => onResolveMedia(item)}>查看转写</Button>
        ) : (
          <Link to={transcriptionUrl}>回填文案</Link>
        ),
        <Tooltip
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
        !directVideoUrl && <Tooltip title="也可以手动填写已授权 MP4/MOV 直链或上传视频文件。"><Link to={transcriptionUrl}>补直链/上传</Link></Tooltip>,
        <Link to={studioUrl}>进入工作台</Link>,
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
              {item.media_resolution_status && <Tag>{statusLabel(item.media_resolution_status)}</Tag>}
              {doubaoStatus && <Tag color={STATUS_COLOR[doubaoStatus]}>豆包 {statusLabel(doubaoStatus)}</Tag>}
              {item.trend_level && <Tag color={item.trend_level === "观察中" ? "default" : "red"}>{item.trend_level}</Tag>}
            {item.anomaly_status && item.anomaly_status !== "normal" && <Tag color="warning">异常：{item.anomaly_status}</Tag>}
          </Space>
        }
        description={
          <Space direction="vertical" size={2}>
            <Text type="secondary">
              作者：{item.author_name}；趋势分：{formatScore(item.trend_score)}
              {shouldShowConfidence ? `；置信度：${item.confidence}` : ""}
              ；样本池：{item.pool_size ?? "暂无"}；复爬：{item.recrawl_count ?? 0}/3
              ；加权增长：{item.engagement_growth_per_hour ?? "等待复爬"}
              ；有效互动：{formatNumber(item.effective_interactions)}
            </Text>
            <Text type="secondary">
              快照：{item.valid_snapshot_count ?? 0}；再次召回：{item.recall_count ?? 0}
              ；漏采：{item.missed_checkpoint_count ?? 0}
              ；跨度：{item.sampling_span_hours === null || item.sampling_span_hours === undefined ? "暂无" : `${item.sampling_span_hours} 小时`}
              ；加速度：{item.acceleration_ratio === null || item.acceleration_ratio === undefined ? "暂无" : `${item.acceleration_ratio}x`}
            </Text>
            {metrics.length > 0 && (
              <Text type="secondary">
                {metrics.map(([label, value]) => `${label}：${formatNumber(value)}`).join("；")}
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
            {doubaoJob && (
              <Text type={doubaoJob.status === "failed" ? "danger" : "secondary"}>
                手机豆包免费链路：{doubaoJob.stage}
                {doubaoJob.douyin_short_url ? `；短链：${doubaoJob.douyin_short_url}` : ""}
                {doubaoJob.error_message ? `；原因：${doubaoJob.error_message}` : ""}
              </Text>
            )}
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
