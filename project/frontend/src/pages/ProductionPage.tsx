import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Button, Card, Empty, Input, Modal, Select, Space, Statistic, Table, Tag, Typography } from "antd";
import { AppstoreAddOutlined, ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { getProductionBatchWorkspace, listProductionBatches, reviewProductionBatchItems } from "../api/client";
import type { ProductionBatch, ProductionBatchItem } from "../api/types";
import { useToast } from "../components/Toast";

const { Title, Text, Paragraph } = Typography;

const STATUS: Record<string, { color: string; label: string }> = {
  planned: { color: "default", label: "待预检" }, pending: { color: "default", label: "待预检" },
  queued: { color: "processing", label: "排队中" }, running: { color: "processing", label: "执行中" },
  paused: { color: "warning", label: "已暂停" }, awaiting_review: { color: "gold", label: "待文案审核" },
  awaiting_publish: { color: "gold", label: "待成片复核" }, ready_to_publish: { color: "success", label: "待发布" },
  succeeded: { color: "success", label: "已完成" }, failed: { color: "error", label: "失败" },
  blocked: { color: "error", label: "预检受阻" }, partial: { color: "warning", label: "部分完成" },
};

const STAGE_LABEL: Record<string, string> = {
  media_resolution: "解析素材", transcription: "语音转写", copywriting: "生成文案", human_review: "文案审核",
  avatar_generation: "数字人生成", video_editing: "智能剪辑", publishing: "成片复核",
};

const SOURCE_LABEL: Record<string, string> = { candidate: "候选库", share_link: "分享链接", brief: "选题/概要", script: "完整文案" };

function statusTag(status: string) {
  const item = STATUS[status] || { color: "default", label: status };
  return <Tag color={item.color}>{item.label}</Tag>;
}

export default function ProductionPage() {
  const toast = useToast();
  const [batches, setBatches] = useState<ProductionBatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [queueFilter, setQueueFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>();
  const [reviewItem, setReviewItem] = useState<ProductionBatchItem | null>(null);
  const [reviewBatchId, setReviewBatchId] = useState("");
  const [reviewText, setReviewText] = useState("");
  const [reviewStage, setReviewStage] = useState<"transcript" | "script">("script");
  const [reviewLoading, setReviewLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const response = await listProductionBatches();
      setBatches(response.items);
    } catch (error) {
      toast.error((error as Error).message || "任务队列加载失败");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!batches.some((batch) => ["running", "awaiting_review", "awaiting_publish"].includes(batch.status))) return undefined;
    const timer = window.setInterval(() => { void refresh(); }, 2500);
    return () => window.clearInterval(timer);
  }, [batches, refresh]);

  const queuedTasks = useMemo(() => batches.flatMap((batch) => batch.items.map((item) => ({ batch, item }))), [batches]);
  const filteredTasks = useMemo(() => queuedTasks.filter(({ batch, item }) => {
    if (statusFilter && item.status !== statusFilter) return false;
    const needle = queueFilter.trim().toLowerCase();
    return !needle || `${batch.profile_name} ${item.display_title || item.source_value || item.candidate_id}`.toLowerCase().includes(needle);
  }), [queuedTasks, queueFilter, statusFilter]);
  const summary = useMemo(() => queuedTasks.reduce((result, { item }) => {
    result.total += 1;
    if (["queued", "running"].includes(item.status)) result.running += 1;
    if (item.status === "awaiting_review") result.review += 1;
    if (["failed", "blocked"].includes(item.status)) result.failed += 1;
    if (item.status === "ready_to_publish") result.ready += 1;
    return result;
  }, { total: 0, running: 0, review: 0, failed: 0, ready: 0 }), [queuedTasks]);

  const openTextReview = async (batch: ProductionBatch, item: ProductionBatchItem) => {
    setReviewLoading(true);
    setReviewItem(item);
    setReviewBatchId(batch.batch_id);
    setReviewText("");
    try {
      const stage = item.review_stage === "transcript" ? "transcript" : "script";
      const workspace = await getProductionBatchWorkspace(batch.batch_id);
      const workspaceItem = workspace.items.find((value) => value.run_id === item.run_id);
      setReviewStage(stage);
      setReviewText(workspaceItem?.reviews[stage].draft_text || "");
    } catch (error) {
      toast.error((error as Error).message || "文案草稿加载失败");
      setReviewItem(null);
    } finally {
      setReviewLoading(false);
    }
  };

  const approveReview = async () => {
    if (!reviewItem || !reviewBatchId) return;
    setReviewLoading(true);
    try {
      await reviewProductionBatchItems(reviewBatchId, {
        stage: reviewStage,
        reviewer: "当前用户",
        items: [{ run_id: reviewItem.run_id, approved_text: reviewText }],
      });
      toast.success(reviewStage === "transcript" ? "转写已确认，后台将生成改写稿" : "文案已通过审核并继续生产");
      setReviewItem(null);
      await refresh();
    } catch (error) {
      toast.error((error as Error).message || "审核失败");
    } finally {
      setReviewLoading(false);
    }
  };

  const itemColumns: ColumnsType<{ batch: ProductionBatch; item: ProductionBatchItem }> = [
    { title: "内容", key: "content", width: 260, render: (_, { item }) => <Space direction="vertical" size={2}><Text strong ellipsis style={{ maxWidth: 230 }}>{item.display_title || item.source_value || item.candidate_id}</Text><Tag>{SOURCE_LABEL[item.source_type] || item.source_type}</Tag></Space> },
    { title: "当前阶段", key: "stage", width: 130, render: (_, { item }) => STAGE_LABEL[item.current_stage || ""] || item.current_stage || "等待开始" },
    { title: "状态", key: "status", width: 130, render: (_, { item }) => statusTag(item.status) },
    { title: "问题/结果", key: "detail", render: (_, { item }) => <Text type={item.error_message || item.blocked_reasons?.length ? "danger" : "secondary"}>{item.error_message || item.blocked_reasons?.join("；") || (item.video_path ? "成片已生成" : "-")}</Text> },
    { title: "操作", key: "action", width: 190, render: (_, { batch, item }) => <Space wrap>
      {item.status === "awaiting_review" && <Button size="small" type="primary" onClick={() => void openTextReview(batch, item)}>{item.review_stage === "transcript" ? "确认转写" : "审核文案"}</Button>}
      {item.status === "awaiting_publish" && <Button size="small" type="primary" onClick={() => { void reviewProductionBatchItems(batch.batch_id, { stage: "output", reviewer: "当前用户", items: [{ run_id: item.run_id }] }).then(refresh).catch((error: Error) => toast.error(error.message)); }}>确认成片</Button>}
      <Link to={`/pipeline?batch=${encodeURIComponent(batch.batch_id)}&run=${encodeURIComponent(item.run_id)}`}><Button size="small">详情</Button></Link>
    </Space> },
    { title: "IP 配方", key: "profile", width: 150, render: (_, row) => <Tag color="purple">{row.batch.profile_name}</Tag> },
    { title: "创建时间", key: "created", width: 180, render: (_, row) => <Text type="secondary">{new Date(row.batch.created_at).toLocaleString("zh-CN")}</Text> },
  ];

  return <Space direction="vertical" size="large" style={{ width: "100%", minWidth: 0 }}>
    <div className="production-page-title"><div><Title level={3} style={{ margin: 0 }}><AppstoreAddOutlined /> 任务队列</Title><Text type="secondary">查看每条视频的真实进度、审核和失败原因。</Text></div><Button icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading}>刷新</Button></div>
    <Alert type="info" showIcon message="单条任务处理" description="任务在智能创作中创建；这里用于查看进度，以及逐条完成文案审核和成片复核。" />
    <div className="production-stats"><Card><Statistic title="队列内容" value={summary.total} /></Card><Card><Statistic title="执行中" value={summary.running} valueStyle={{ color: "#1677ff" }} /></Card><Card><Statistic title="待文案审核" value={summary.review} valueStyle={{ color: "#d48806" }} /></Card><Card><Statistic title="失败/受阻" value={summary.failed} valueStyle={{ color: "#cf1322" }} /></Card><Card><Statistic title="待发布" value={summary.ready} valueStyle={{ color: "#389e0d" }} /></Card></div>
    <Card title="全部任务" extra={<Space wrap><Input.Search allowClear placeholder="搜索配方或内容" value={queueFilter} onChange={(event) => setQueueFilter(event.target.value)} style={{ width: 250 }} /><Select allowClear placeholder="任务状态" value={statusFilter} onChange={setStatusFilter} style={{ width: 140 }} options={Object.entries(STATUS).map(([value, item]) => ({ value, label: item.label }))} /></Space>} styles={{ body: { padding: 12 } }}>
      {filteredTasks.length ? <div className="production-table-wrap"><Table rowKey={({ item }) => item.run_id} size="middle" pagination={{ pageSize: 10, showSizeChanger: false }} columns={itemColumns} dataSource={filteredTasks} /></div> : <Empty description="暂无符合条件的任务" />}
    </Card>
    <Modal open={Boolean(reviewItem)} title={reviewStage === "transcript" ? "确认原转写" : "审核口播文案"} confirmLoading={reviewLoading} okText={reviewStage === "transcript" ? "确认并生成改写稿" : "通过并继续生产"} onOk={() => void approveReview()} onCancel={() => setReviewItem(null)} width={720}><Paragraph type="secondary">{reviewStage === "transcript" ? "必须先核对原转写，确认后才会生成 AI 改写稿。" : "通过后才会进入数字人和剪辑；可直接修改最终口播稿。"}</Paragraph><Input.TextArea value={reviewText} onChange={(event) => setReviewText(event.target.value)} autoSize={{ minRows: 12, maxRows: 20 }} /></Modal>
    <style>{`.production-page-title{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.production-stats{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:16px}.production-table-wrap{overflow-x:auto}@media(max-width:1100px){.production-stats{grid-template-columns:repeat(3,minmax(120px,1fr))}}@media(max-width:768px){.production-page-title{align-items:flex-start;flex-direction:column}.production-stats{grid-template-columns:repeat(2,minmax(120px,1fr))}}`}</style>
  </Space>;
}
