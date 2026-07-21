import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Empty,
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
  getCrawlerBatch,
  getCrawlerCapabilities,
  listCrawlerBatches,
  previewCrawlerBatch,
} from "../api/client";
import type {
  CrawlerBatchResponse,
  CrawlerCandidateResult,
  CrawlerCapabilitiesResponse,
  CrawlerPlatformRun,
  CrawlerPreviewResponse,
  CrawlerSearchRequest,
} from "../api/types";
import { useToast } from "../components/Toast";

const { Text, Title, Paragraph } = Typography;

const STATUS_COLOR: Record<string, string> = {
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

function formatScore(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : value.toFixed(1);
}

export default function KeywordCrawlerPage() {
  const toast = useToast();
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
          : "填写完成：下一步会预览缓存、额度和三平台阻断原因。";
  const isSandboxMode = capabilities?.mode === "sandbox";
  const crawlerDescription = capabilities
    ? isSandboxMode
      ? "通过 FastAPI 调用 CommercialSearchService，当前为 Sandbox 演示模式，不代表真实平台生产数据。"
      : `通过 FastAPI 调用 ${capabilities.display_name}，当前为 Production 模式；请使用小流量关键词验证真实响应字段与授权边界。`
    : "通过 FastAPI 调用 CommercialSearchService，结果持久化到 SQLite。";
  const formFlowDescription = isSandboxMode
    ? "为了避免误触发平台调用或重复计费，本页不会在第一次点击按钮时直接爬取。Sandbox 模式会写入演示数据，但不代表真实平台生产数据。"
    : "为了避免误触发真实平台调用或重复计费，本页不会在第一次点击按钮时直接爬取。当前 Production 模式会调用供应商接口，请先用每平台 1 条做小流量验收。";

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [caps, list] = await Promise.all([
        getCrawlerCapabilities(),
        listCrawlerBatches(),
      ]);
      setCapabilities(caps);
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

  useEffect(() => {
    refresh();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
      toast.success("三平台关键词批次已写入 SQLite");
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
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
        <Title level={4} style={{ margin: 0 }}>三平台关键词爆款榜</Title>
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
            <Descriptions.Item label="月度调用量">
              {capabilities.monthly_query_count} / {capabilities.monthly_hard_limit_queries}
            </Descriptions.Item>
            <Descriptions.Item label="本地估算费用">
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
            <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>每平台条数</Text>
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
        </Space>
      </Card>

      {selectedBatch && <BatchDetail batch={selectedBatch} />}

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
        title="确认三平台搜索计划"
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
    </Space>
  );
}

function BatchDetail({ batch }: { batch: CrawlerBatchResponse }) {
  const [rankingMode, setRankingMode] = useState<RankingMode>("provider");

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
            { label: "平台热度名次", value: "provider" },
            { label: "系统评估排行", value: "system" },
          ]}
        />
      </Space>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {batch.platform_runs.map((run) => <PlatformRunDetail key={run.run_id} run={run} rankingMode={rankingMode} />)}
      </Space>
    </Card>
  );
}

function PlatformRunDetail({ run, rankingMode }: { run: CrawlerPlatformRun; rankingMode: RankingMode }) {
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
        <Descriptions.Item label="API 调用">{run.api_call_count}</Descriptions.Item>
        <Descriptions.Item label="额度">{run.quota_remaining ?? "未返回"}</Descriptions.Item>
        <Descriptions.Item label="计费单元">{run.billable_units ?? 0}</Descriptions.Item>
      </Descriptions>
      {run.error && <Alert style={{ marginTop: 12 }} type="error" showIcon message={run.error} />}
      {run.candidates.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无候选结果" />
      ) : (
        <List
          style={{ marginTop: 12 }}
          dataSource={candidates}
          renderItem={(item) => <CandidateListItem item={item} />}
        />
      )}
    </Card>
  );
}

function CandidateListItem({ item }: { item: CrawlerCandidateResult }) {
  const componentEntries = Object.entries(item.component_scores || {});

  return (
    <List.Item
      actions={[
        item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer">原视频</a> : <Text type="secondary">无链接</Text>,
        <a href={`/transcription?candidate=${encodeURIComponent(item.video_id)}`}>去转写</a>,
      ]}
    >
      <List.Item.Meta
        avatar={item.provider_hot_rank ? <Tag color="purple">热度 #{item.provider_hot_rank}</Tag> : undefined}
        title={
          <Space wrap>
            <Text strong>{item.title}</Text>
            {item.system_rank && <Tag color="geekblue">系统 #{item.system_rank}</Tag>}
            {item.trend_level && <Tag color={item.trend_level === "观察中" ? "default" : "red"}>{item.trend_level}</Tag>}
            {item.anomaly_status && item.anomaly_status !== "normal" && <Tag color="warning">异常：{item.anomaly_status}</Tag>}
          </Space>
        }
        description={
          <Space direction="vertical" size={2}>
            <Text type="secondary">
              作者：{item.author_name}；趋势分：{formatScore(item.trend_score)}；置信度：{item.confidence ?? "暂无"}；样本池：{item.pool_size ?? "暂无"}；增长：{item.like_growth_per_hour ?? "首次观测"}
            </Text>
            <Text type="secondary">
              播放：{formatNumber(item.plays)}；点赞：{formatNumber(item.likes)}；评论：{formatNumber(item.comments)}；分享：{formatNumber(item.shares)}；收藏：{formatNumber(item.favorites)}
            </Text>
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
