import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Collapse,
  Drawer,
  Empty,
  Input,
  InputNumber,
  Modal,
  Progress,
  Select,
  Space,
  Statistic,
  Steps,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import {
  AppstoreAddOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RocketOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  createProductionBatch,
  createProductionProfile,
  getProductionBatchWorkspace,
  listAvatarAssets,
  listProductionBatches,
  listProductionProfiles,
  listTemplates,
  pauseProductionBatch,
  preflightProductionBatch,
  resumeProductionBatch,
  retryProductionBatchFailed,
  reviewProductionBatchItems,
  startProductionBatch,
} from "../api/client";
import type {
  AvatarAsset,
  EditTemplate,
  ProductionBatch,
  ProductionBatchItem,
  ProductionBatchPreflight,
  ProductionBatchSourceItem,
  ProductionProfile,
} from "../api/types";
import { useToast } from "../components/Toast";
import { productionTaskTitle } from "../utils/productionTask";

const { Title, Text, Paragraph } = Typography;

const STATUS: Record<string, { color: string; label: string }> = {
  planned: { color: "default", label: "待预检" },
  pending: { color: "default", label: "待预检" },
  queued: { color: "processing", label: "排队中" },
  running: { color: "processing", label: "执行中" },
  paused: { color: "warning", label: "已暂停" },
  awaiting_review: { color: "gold", label: "待文案审核" },
  awaiting_publish: { color: "gold", label: "待成片复核" },
  ready_to_publish: { color: "success", label: "待发布" },
  succeeded: { color: "success", label: "已完成" },
  failed: { color: "error", label: "失败" },
  blocked: { color: "error", label: "预检受阻" },
  partial: { color: "warning", label: "部分完成" },
};

const STAGE_LABEL: Record<string, string> = {
  media_resolution: "解析素材",
  transcription: "语音转写",
  copywriting: "生成文案",
  human_review: "文案审核",
  avatar_generation: "数字人生成",
  video_editing: "智能剪辑",
  publishing: "成片复核",
};

const SOURCE_LABEL: Record<string, string> = {
  candidate: "候选库",
  share_link: "分享链接",
  brief: "选题/概要",
  script: "完整文案",
};

type SourceMode = "candidate" | "share_link" | "brief" | "script";

function statusTag(status: string) {
  const item = STATUS[status] || { color: "default", label: status };
  return <Tag color={item.color}>{item.label}</Tag>;
}

function splitLines(value: string) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

export default function ProductionPage() {
  const toast = useToast();
  const createOperation = useRef({ fingerprint: "", key: "" });
  const startOperation = useRef({ fingerprint: "", key: "" });
  const [profiles, setProfiles] = useState<ProductionProfile[]>([]);
  const [assets, setAssets] = useState<AvatarAsset[]>([]);
  const [templates, setTemplates] = useState<EditTemplate[]>([]);
  const [batches, setBatches] = useState<ProductionBatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [queueFilter, setQueueFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>();
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerStep, setDrawerStep] = useState(0);
  const [draftItems, setDraftItems] = useState<ProductionBatchSourceItem[]>([]);
  const [sourceMode, setSourceMode] = useState<SourceMode>("candidate");
  const [sourceText, setSourceText] = useState("");
  const [batchName, setBatchName] = useState("");
  const [profileId, setProfileId] = useState<string>();
  const [createdBatch, setCreatedBatch] = useState<ProductionBatch | null>(null);
  const [rightsHolder, setRightsHolder] = useState("本人/公司已授权");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [concurrency, setConcurrency] = useState(1);
  const [maxTotalCost, setMaxTotalCost] = useState<number | null>(null);
  const [paidActionsConfirmed, setPaidActionsConfirmed] = useState(false);
  const [preflight, setPreflight] = useState<ProductionBatchPreflight | null>(null);
  const [reviewItem, setReviewItem] = useState<ProductionBatchItem | null>(null);
  const [reviewBatchId, setReviewBatchId] = useState("");
  const [reviewText, setReviewText] = useState("");
  const [reviewStage, setReviewStage] = useState<"transcript" | "script">("script");
  const [reviewLoading, setReviewLoading] = useState(false);
  const [profileModalOpen, setProfileModalOpen] = useState(false);
  const [newProfile, setNewProfile] = useState({ name: "", audience: "", style: "", avatarId: "", voiceId: "", templateId: "" });

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [profileResp, batchResp, assetResp, templateResp] = await Promise.all([
        listProductionProfiles(), listProductionBatches(), listAvatarAssets(), listTemplates(),
      ]);
      setProfiles(profileResp.items);
      setBatches(batchResp.items);
      setAssets(assetResp);
      setTemplates(templateResp.items);
      setProfileId((current) => current || profileResp.items[0]?.profile_id);
    } catch (error) {
      toast.error((error as Error).message || "批量生产数据加载失败");
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

  const selectedRows = useMemo(() => batches.flatMap((batch) => batch.items.map((item) => ({ batch, item }))).filter(({ item }) => selectedRunIds.includes(item.run_id)), [batches, selectedRunIds]);
  const filteredBatches = useMemo(() => batches.filter((batch) => {
    if (statusFilter && batch.status !== statusFilter) return false;
    const needle = queueFilter.trim().toLowerCase();
    if (!needle) return true;
    return `${batch.name} ${batch.profile_name} ${batch.items.map((item) => item.display_title || item.source_value).join(" ")}`.toLowerCase().includes(needle);
  }), [batches, queueFilter, statusFilter]);
  const summary = useMemo(() => batches.reduce((result, batch) => {
    result.total += batch.progress.total || batch.items.length;
    result.running += batch.progress.running || 0;
    result.review += batch.progress.awaiting_review || 0;
    result.failed += batch.progress.failed || 0;
    result.ready += batch.progress.ready_to_publish || 0;
    return result;
  }, { total: 0, running: 0, review: 0, failed: 0, ready: 0 }), [batches]);

  const closeDrawer = () => {
    setDrawerOpen(false); setDrawerStep(0); setDraftItems([]); setSourceText(""); setBatchName(""); setCreatedBatch(null); setPreflight(null); setRightsConfirmed(false); setMaxTotalCost(null); setPaidActionsConfirmed(false);
  };

  const openNewBatch = () => {
    setDrawerStep(0); setDraftItems([]); setSourceText(""); setBatchName(""); setCreatedBatch(null); setPreflight(null); setMaxTotalCost(null); setPaidActionsConfirmed(false); setDrawerOpen(true);
  };

  const openExistingBatch = (batch: ProductionBatch) => {
    setCreatedBatch(batch); setBatchName(batch.name); setProfileId(batch.profile_id); setDrawerStep(2); setPreflight(null); setDrawerOpen(true);
  };

  const addSourceLines = () => {
    const next = splitLines(sourceText).map((source_value) => ({ source_type: sourceMode, source_value }));
    if (!next.length) { toast.warning("请先每行输入一条内容"); return; }
    setDraftItems((items) => {
      const seen = new Set(items.map((item) => `${item.source_type}:${item.source_value.toLowerCase()}`));
      const unique = next.filter((item) => !seen.has(`${item.source_type}:${item.source_value.toLowerCase()}`));
      if (items.length + unique.length > 50) { toast.warning("单个批次最多 50 条内容"); return items; }
      return [...items, ...unique];
    });
    setSourceText("");
  };

  const updateDraftOverride = (index: number, key: "avatar_id" | "voice_id" | "edit_template_id", value?: string) => {
    setDraftItems((items) => items.map((item, itemIndex) => {
      if (itemIndex !== index) return item;
      const profile_overrides = { ...(item.profile_overrides || {}) };
      if (value) profile_overrides[key] = value;
      else delete profile_overrides[key];
      return { ...item, profile_overrides };
    }));
  };

  const createBatch = async () => {
    if (!batchName.trim() || !profileId || !draftItems.length) { toast.warning("请填写批次名称、选择 IP 配方并添加内容"); return; }
    setSubmitting(true);
    try {
      const payload = { name: batchName.trim(), profile_id: profileId, items: draftItems };
      const fingerprint = JSON.stringify(payload);
      if (createOperation.current.fingerprint !== fingerprint) {
        createOperation.current = {
          fingerprint,
          key: `production-create-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        };
      }
      const batch = await createProductionBatch({
        ...payload,
        idempotencyKey: createOperation.current.key,
      });
      setCreatedBatch(batch); setBatches((items) => [batch, ...items]); setDrawerStep(2);
      toast.success(`已创建 ${batch.items.length} 条待预检任务`);
    } catch (error) { toast.error((error as Error).message || "创建批次失败"); } finally { setSubmitting(false); }
  };

  const runPreflight = async () => {
    if (!createdBatch) return;
    if (!rightsHolder.trim() || !rightsConfirmed) { toast.warning("请填写授权主体并确认处理授权"); return; }
    setSubmitting(true);
    try {
      setPreflight(await preflightProductionBatch(createdBatch.batch_id, {
        rightsHolder: rightsHolder.trim(),
        rightsConfirmed,
        publishPlatforms: ["douyin"],
        concurrency,
        maxTotalCostCny: maxTotalCost,
        paidActionsConfirmed,
      }));
    } catch (error) { toast.error((error as Error).message || "批次预检失败"); } finally { setSubmitting(false); }
  };

  const startBatch = async () => {
    if (!createdBatch || !preflight?.ready_count) return;
    setSubmitting(true);
    try {
      const payload = {
        rightsHolder: rightsHolder.trim(),
        rightsConfirmed,
        publishPlatforms: ["douyin"],
        concurrency,
        maxTotalCostCny: maxTotalCost,
        paidActionsConfirmed,
      };
      const fingerprint = JSON.stringify({ batchId: createdBatch.batch_id, ...payload });
      if (startOperation.current.fingerprint !== fingerprint) {
        startOperation.current = {
          fingerprint,
          key: `production-start-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        };
      }
      await startProductionBatch(createdBatch.batch_id, {
        ...payload,
        idempotencyKey: startOperation.current.key,
      });
      toast.success("通过预检的任务已进入队列"); closeDrawer(); await refresh();
    } catch (error) { toast.error((error as Error).message || "启动批次失败"); } finally { setSubmitting(false); }
  };

  const batchAction = async (action: () => Promise<ProductionBatch>, message: string) => {
    setSubmitting(true);
    try { await action(); toast.success(message); await refresh(); } catch (error) { toast.error((error as Error).message || "批次操作失败"); } finally { setSubmitting(false); }
  };

  const openTextReview = async (batch: ProductionBatch, item: ProductionBatchItem) => {
    setReviewLoading(true); setReviewItem(item); setReviewBatchId(batch.batch_id); setReviewText("");
    try {
      const stage = item.review_stage === "transcript" ? "transcript" : "script";
      const workspace = await getProductionBatchWorkspace(batch.batch_id);
      const workspaceItem = workspace.items.find((value) => value.run_id === item.run_id);
      setReviewStage(stage);
      setReviewText(workspaceItem?.reviews[stage].draft_text || "");
    } catch (error) { toast.error((error as Error).message || "文案草稿加载失败"); setReviewItem(null); }
    finally { setReviewLoading(false); }
  };

  const approveReview = async () => {
    if (!reviewItem || !reviewBatchId) return;
    setReviewLoading(true);
    try {
      await reviewProductionBatchItems(reviewBatchId, { stage: reviewStage, reviewer: "当前用户", items: [{ run_id: reviewItem.run_id, approved_text: reviewText }] });
      toast.success(reviewStage === "transcript" ? "转写已确认，后台将生成改写稿" : "文案已通过审核并继续生产"); setReviewItem(null); await refresh();
    } catch (error) { toast.error((error as Error).message || "审核失败"); } finally { setReviewLoading(false); }
  };

  const bulkReview = async (stage: "transcript" | "script" | "output") => {
    const eligible = selectedRows.filter(({ item }) => {
      if (stage === "output") return item.status === "awaiting_publish";
      if (item.status !== "awaiting_review") return false;
      return stage === "transcript" ? item.review_stage === "transcript" : item.review_stage !== "transcript";
    });
    if (!eligible.length) { toast.warning(stage === "transcript" ? "请选择待转写确认的任务" : stage === "script" ? "请选择待文案审核的任务" : "请选择待成片复核的任务"); return; }
    setSubmitting(true);
    try {
      const byBatch = new Map<string, ProductionBatchItem[]>();
      eligible.forEach(({ batch, item }) => byBatch.set(batch.batch_id, [...(byBatch.get(batch.batch_id) || []), item]));
      const results = await Promise.all([...byBatch.entries()].map(async ([batchId, items]) => {
        const workspace = stage === "output" ? null : await getProductionBatchWorkspace(batchId);
        return reviewProductionBatchItems(batchId, {
          stage,
          reviewer: "当前用户",
          items: items.map((item) => {
            const workspaceItem = workspace?.items.find((value) => value.run_id === item.run_id);
            return {
              run_id: item.run_id,
              approved_text: stage === "output" ? undefined : workspaceItem?.reviews[stage].draft_text || "",
            };
          }),
        });
      }));
      const failed = results.flatMap((result) => result.results).filter((result) => !result.ok);
      if (failed.length) toast.warning(`${failed.length} 条任务未能通过，请查看任务原因`); else toast.success(stage === "transcript" ? "所选转写已进入文案生成" : stage === "script" ? "所选文案已进入后续生产" : "所选成片已标记为待发布");
      setSelectedRunIds([]); await refresh();
    } catch (error) { toast.error((error as Error).message || "批量审核失败"); } finally { setSubmitting(false); }
  };

  const saveProfile = async () => {
    if (!newProfile.name.trim()) { toast.warning("请填写 IP 配方名称"); return; }
    setSubmitting(true);
    try {
      const profile = await createProductionProfile({
        name: newProfile.name.trim(), description: "", target_audience: newProfile.audience.trim(), platform: "douyin", script_style: newProfile.style.trim(),
        avatar_id: newProfile.avatarId || null, voice_id: newProfile.voiceId || null, edit_template_id: newProfile.templateId || null, tags: [],
      });
      setProfiles((items) => [profile, ...items]); setProfileId(profile.profile_id); setProfileModalOpen(false); setNewProfile({ name: "", audience: "", style: "", avatarId: "", voiceId: "", templateId: "" }); toast.success("IP 配方已保存");
    } catch (error) { toast.error((error as Error).message || "保存 IP 配方失败"); } finally { setSubmitting(false); }
  };

  const itemColumns: ColumnsType<ProductionBatchItem> = [
    { title: "内容", key: "content", width: 260, render: (_, item) => <Space direction="vertical" size={2}><Text strong ellipsis style={{ maxWidth: 230 }}>{item.display_title || item.source_value || item.candidate_id}</Text><Tag>{SOURCE_LABEL[item.source_type] || item.source_type}</Tag></Space> },
    { title: "当前阶段", dataIndex: "current_stage", width: 130, render: (stage: string | null) => STAGE_LABEL[stage || ""] || stage || "等待开始" },
    { title: "状态", dataIndex: "status", width: 130, render: statusTag },
    { title: "问题/结果", key: "detail", render: (_, item) => <Text type={item.error_message || item.blocked_reasons?.length ? "danger" : "secondary"}>{item.error_message || item.blocked_reasons?.join("；") || (item.video_path ? "成片已生成" : "-")}</Text> },
    { title: "操作", key: "action", width: 190, render: (_, item) => <Space wrap>
      {item.status === "awaiting_review" && <Button size="small" type="primary" onClick={() => { const batch = batches.find((entry) => entry.items.some((entryItem) => entryItem.run_id === item.run_id)); if (batch) void openTextReview(batch, item); }}>{item.review_stage === "transcript" ? "确认转写" : "审核文案"}</Button>}
      {item.status === "awaiting_publish" && <Button size="small" type="primary" onClick={() => { const batch = batches.find((entry) => entry.items.some((entryItem) => entryItem.run_id === item.run_id)); if (batch) void reviewProductionBatchItems(batch.batch_id, { stage: "output", reviewer: "当前用户", items: [{ run_id: item.run_id }] }).then(refresh).catch((error: Error) => toast.error(error.message)); }}>确认成片</Button>}
      <Link to={`/pipeline?batch=${encodeURIComponent(batches.find((batch) => batch.items.some((value) => value.run_id === item.run_id))?.batch_id || "")}&run=${encodeURIComponent(item.run_id)}`}><Button size="small">详情</Button></Link>
    </Space> },
  ];

  const sourceTabs = ([
    ["candidate", "候选 ID", "每行一个候选 ID，可从候选检索复制"],
    ["share_link", "分享链接", "每行一个已授权的 http/https 分享链接"],
    ["brief", "选题/概要", "每行一个选题，系统先生成文案草稿"],
    ["script", "完整文案", "每行一条完整口播稿，仍需人工审核"],
  ] as Array<[SourceMode, string, string]>).map(([key, label, placeholder]) => ({ key, label, children: <Space direction="vertical" style={{ width: "100%" }}><Input.TextArea value={sourceMode === key ? sourceText : ""} onChange={(event) => { setSourceMode(key); setSourceText(event.target.value); }} placeholder={placeholder} autoSize={{ minRows: 5, maxRows: 9 }} /><Button icon={<PlusOutlined />} onClick={() => { setSourceMode(key); addSourceLines(); }}>加入批次</Button></Space> }));

  return <Space direction="vertical" size="large" style={{ width: "100%", minWidth: 0 }}>
    <div className="production-page-title"><div><Title level={3} style={{ margin: 0 }}><AppstoreAddOutlined /> 任务队列</Title><Text type="secondary">按批次管理多条视频；真实进度、审核和失败原因都来自后台任务。</Text></div><Space><Button icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading}>刷新</Button><Button type="primary" icon={<PlusOutlined />} onClick={openNewBatch}>新建批次</Button></Space></div>

    <Alert type="info" showIcon message="批量生产边界" description="候选、链接、选题和完整文案会进入同一队列；文案审核与成片复核始终保留。这里不会触发平台发布，成片通过后才标记为待发布。" />

    <div className="production-stats"><Card><Statistic title="队列内容" value={summary.total} /></Card><Card><Statistic title="执行中" value={summary.running} valueStyle={{ color: "#1677ff" }} /></Card><Card><Statistic title="待文案审核" value={summary.review} valueStyle={{ color: "#d48806" }} /></Card><Card><Statistic title="失败/受阻" value={summary.failed} valueStyle={{ color: "#cf1322" }} /></Card><Card><Statistic title="待发布" value={summary.ready} valueStyle={{ color: "#389e0d" }} /></Card></div>

    <Card title="全局批次队列" extra={<Space wrap><Input.Search allowClear placeholder="搜索批次、配方或内容" value={queueFilter} onChange={(event) => setQueueFilter(event.target.value)} style={{ width: 250 }} /><Select allowClear placeholder="批次状态" value={statusFilter} onChange={setStatusFilter} style={{ width: 140 }} options={Object.entries(STATUS).map(([value, item]) => ({ value, label: item.label }))} /></Space>} bodyStyle={{ padding: 12 }}>
      {selectedRunIds.length > 0 && <div className="production-bulk-bar"><Text>已选择 {selectedRunIds.length} 条任务</Text><Space wrap><Button loading={submitting} onClick={() => void bulkReview("transcript")}>批量确认转写</Button><Button loading={submitting} onClick={() => void bulkReview("script")}>批量通过文案</Button><Button type="primary" loading={submitting} onClick={() => void bulkReview("output")}>批量通过成片</Button><Button onClick={() => setSelectedRunIds([])}>取消选择</Button></Space></div>}
      {filteredBatches.length ? (
        <Collapse
          items={filteredBatches.map((batch) => {
            const progress = batch.progress;
            const done = (progress.succeeded || 0) + (progress.failed || 0) + (progress.ready_to_publish || 0);
            return {
              key: batch.batch_id,
              label: <div className="production-batch-header"><Space wrap><Text strong className="production-batch-name" title={productionTaskTitle(batch.name)}>{productionTaskTitle(batch.name)}</Text>{statusTag(batch.status)}<Tag color="purple">{batch.profile_name}</Tag><Badge count={batch.items.length} showZero color="#7c3aed" /></Space><Progress percent={progress.total ? Math.round(done / progress.total * 100) : 0} format={() => `${done}/${progress.total || batch.items.length}`} size="small" style={{ width: 180 }} /></div>,
              children: <Space direction="vertical" style={{ width: "100%" }} size="middle"><div className="production-batch-actions"><Text type="secondary">创建于 {new Date(batch.created_at).toLocaleString("zh-CN")}</Text><Space wrap>{["planned", "failed"].includes(batch.status) && <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => openExistingBatch(batch)}>预检并启动</Button>}{batch.status === "running" && <Button size="small" icon={<PauseCircleOutlined />} onClick={() => void batchAction(() => pauseProductionBatch(batch.batch_id), "已暂停批次")}>暂停</Button>}{batch.status === "paused" && <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => void batchAction(() => resumeProductionBatch(batch.batch_id), "已恢复批次")}>继续</Button>}{batch.progress.failed ? <Button size="small" onClick={() => void batchAction(() => retryProductionBatchFailed(batch.batch_id), "失败项已重新入队")}>重试失败项</Button> : null}</Space></div><div className="production-table-wrap"><Table rowKey="run_id" size="small" pagination={false} columns={itemColumns} dataSource={batch.items} rowSelection={{ selectedRowKeys: selectedRunIds, onChange: (keys) => setSelectedRunIds(keys.map(String)) }} /></div></Space>,
            };
          })}
        />
      ) : <Empty description="暂无符合条件的批次，先创建一个混合来源批次" />}
    </Card>

    <Drawer title={createdBatch ? `执行批次：${productionTaskTitle(createdBatch.name)}` : "新建批量生产"} width={760} open={drawerOpen} onClose={closeDrawer} destroyOnClose>
      <Steps current={drawerStep} items={[{ title: "导入内容" }, { title: "选择配方" }, { title: "预检启动" }]} style={{ marginBottom: 28 }} />
      {drawerStep === 0 && <Space direction="vertical" size="large" style={{ width: "100%" }}><Input value={batchName} onChange={(event) => setBatchName(event.target.value)} placeholder="批次名称，例如：本周企业获客口播" maxLength={100} /><Tabs activeKey={sourceMode} onChange={(value) => { setSourceMode(value as SourceMode); setSourceText(""); }} items={sourceTabs} /><Card size="small" title={`已加入 ${draftItems.length} 条`}><Table size="small" pagination={false} rowKey={(item) => `${item.source_type}-${item.source_value}`} dataSource={draftItems} columns={[{ title: "来源", dataIndex: "source_type", render: (value) => <Tag>{SOURCE_LABEL[value]}</Tag> }, { title: "内容", dataIndex: "source_value", ellipsis: true }, { title: "操作", render: (_, __, index) => <Button type="link" danger onClick={() => setDraftItems((items) => items.filter((_, itemIndex) => itemIndex !== index))}>移除</Button> }]} /></Card><Button type="primary" disabled={!batchName.trim() || !draftItems.length} onClick={() => setDrawerStep(1)}>下一步</Button></Space>}
      {drawerStep === 1 && <Space direction="vertical" size="large" style={{ width: "100%" }}><Alert type="info" showIcon message="同一批次默认复用一套 IP 配方" description="需要差异时，可仅覆盖某一条的形象、音色或剪辑模板；批量运行前会逐条检查这些资源是否可用。" /><Select value={profileId} onChange={setProfileId} placeholder="选择 IP 配方" style={{ width: "100%" }} options={profiles.map((profile) => ({ value: profile.profile_id, label: profile.name }))} />{!profiles.length && <Alert type="warning" message="还没有 IP 配方" action={<Button size="small" onClick={() => setProfileModalOpen(true)}>新建 IP 配方</Button>} />}{draftItems.length > 0 && <Card size="small" title="单条覆盖（可选）"><div className="production-table-wrap"><Table size="small" pagination={false} rowKey={(item) => `${item.source_type}-${item.source_value}`} dataSource={draftItems} columns={[{ title: "内容", key: "source", width: 200, render: (_, item) => <Text ellipsis style={{ maxWidth: 180 }}>{item.source_value}</Text> }, { title: "数字人形象", key: "avatar", render: (_, item, index) => <Select allowClear placeholder="使用批次默认" value={item.profile_overrides?.avatar_id} onChange={(value) => updateDraftOverride(index, "avatar_id", value)} style={{ minWidth: 155 }} options={assets.filter((asset) => asset.kind === "avatar").map((asset) => ({ value: asset.asset_id, label: asset.name }))} /> }, { title: "音色", key: "voice", render: (_, item, index) => <Select allowClear placeholder="使用批次默认" value={item.profile_overrides?.voice_id} onChange={(value) => updateDraftOverride(index, "voice_id", value)} style={{ minWidth: 155 }} options={assets.filter((asset) => asset.kind === "voice").map((asset) => ({ value: asset.asset_id, label: asset.name }))} /> }, { title: "剪辑模板", key: "template", render: (_, item, index) => <Select allowClear placeholder="使用批次默认" value={item.profile_overrides?.edit_template_id} onChange={(value) => updateDraftOverride(index, "edit_template_id", value)} style={{ minWidth: 155 }} options={templates.map((template) => ({ value: template.template_id, label: template.name }))} /> }]} /></div></Card>}<Space><Button onClick={() => setDrawerStep(0)}>上一步</Button><Button type="primary" disabled={!profileId} loading={submitting} onClick={() => void createBatch()}>创建并进入预检</Button></Space></Space>}
      {drawerStep === 2 && <Space direction="vertical" size="large" style={{ width: "100%" }}><Alert type="info" showIcon message="预检不会调用生成或发布供应商" description="通过后才会进入后台队列；预检受阻项会被单独隔离。" /><Input value={rightsHolder} onChange={(event) => setRightsHolder(event.target.value)} placeholder="授权主体" maxLength={80} /><Select value={concurrency} onChange={setConcurrency} options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `同时 ${value} 条` }))} style={{ width: 180 }} /><InputNumber min={0} precision={2} value={maxTotalCost} onChange={setMaxTotalCost} placeholder="本批次费用上限（元）" style={{ width: 240 }} /><Checkbox checked={rightsConfirmed} onChange={(event) => setRightsConfirmed(event.target.checked)}>我确认拥有媒体、文案、肖像和声音处理授权</Checkbox>{preflight && Number(preflight.estimated_cost_cny || 0) > 0 && <Checkbox checked={paidActionsConfirmed} onChange={(event) => setPaidActionsConfirmed(event.target.checked)}>我确认本批次预计费用 ¥{Number(preflight.estimated_cost_cny || 0).toFixed(2)}；勾选后请重新预检</Checkbox>}<Space><Button loading={submitting} onClick={() => void runPreflight()}>运行预检</Button><Button type="primary" disabled={!preflight?.ready_count || Boolean(preflight?.cost_blocked)} loading={submitting} icon={<RocketOutlined />} onClick={() => void startBatch()}>启动 {preflight?.ready_count || 0} 条通过项</Button></Space>{preflight && <Card size="small" title={`预检结果：通过 ${preflight.ready_count} 条，受阻 ${preflight.blocked_count} 条`}><Paragraph type="secondary">预计总费用 {preflight.cost_known === false ? "未知（已阻断）" : `¥${Number(preflight.estimated_cost_cny || 0).toFixed(2)}`}；本月已用 ¥{preflight.monthly_budget_used_cny.toFixed(2)}。</Paragraph>{preflight.cost_issues?.map((issue) => <Alert key={issue} showIcon type="warning" message={issue} style={{ marginBottom: 8 }} />)}{preflight.items.map((item) => <Alert key={item.run_id} showIcon type={item.ready ? "success" : "warning"} message={item.display_title || item.candidate_id || item.run_id} description={item.ready ? "可进入后台队列" : item.reasons.join("；")} style={{ marginBottom: 8 }} />)}</Card>}</Space>}
    </Drawer>

    <Modal open={Boolean(reviewItem)} title={reviewStage === "transcript" ? "确认原转写" : "审核口播文案"} confirmLoading={reviewLoading} okText={reviewStage === "transcript" ? "确认并生成改写稿" : "通过并继续生产"} onOk={() => void approveReview()} onCancel={() => setReviewItem(null)} width={720}><Paragraph type="secondary">{reviewStage === "transcript" ? "必须先核对原转写，确认后才会生成 AI 改写稿。" : "通过后才会进入数字人和剪辑；可直接修改最终口播稿。"}</Paragraph><Input.TextArea value={reviewText} onChange={(event) => setReviewText(event.target.value)} autoSize={{ minRows: 12, maxRows: 20 }} /></Modal>
    <Modal open={profileModalOpen} title="新建 IP 配方" confirmLoading={submitting} okText="保存配方" onOk={() => void saveProfile()} onCancel={() => setProfileModalOpen(false)}><Space direction="vertical" style={{ width: "100%" }}><Input value={newProfile.name} onChange={(event) => setNewProfile((value) => ({ ...value, name: event.target.value }))} placeholder="配方名称" /><Input value={newProfile.audience} onChange={(event) => setNewProfile((value) => ({ ...value, audience: event.target.value }))} placeholder="目标受众" /><Input.TextArea value={newProfile.style} onChange={(event) => setNewProfile((value) => ({ ...value, style: event.target.value }))} placeholder="文案风格与表达边界" /><Select allowClear value={newProfile.avatarId || undefined} onChange={(value) => setNewProfile((item) => ({ ...item, avatarId: value || "" }))} options={assets.filter((item) => item.kind === "avatar").map((item) => ({ value: item.asset_id, label: item.name }))} placeholder="数字人形象" /><Select allowClear value={newProfile.voiceId || undefined} onChange={(value) => setNewProfile((item) => ({ ...item, voiceId: value || "" }))} options={assets.filter((item) => item.kind === "voice").map((item) => ({ value: item.asset_id, label: item.name }))} placeholder="音色" /><Select allowClear value={newProfile.templateId || undefined} onChange={(value) => setNewProfile((item) => ({ ...item, templateId: value || "" }))} options={templates.map((item) => ({ value: item.template_id, label: item.name }))} placeholder="剪辑模板" /></Space></Modal>
    <style>{`.production-page-title{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.production-stats{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:16px}.production-bulk-bar,.production-batch-actions,.production-batch-header{display:flex;align-items:center;justify-content:space-between;gap:12px}.production-bulk-bar{padding:10px 12px;background:var(--primary-50);border-radius:8px;margin-bottom:12px}.production-table-wrap{overflow-x:auto}.production-batch-header{width:100%}.production-batch-name{display:inline-block;max-width:min(520px,48vw);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}@media(max-width:1100px){.production-stats{grid-template-columns:repeat(3,minmax(120px,1fr))}}@media(max-width:768px){.production-page-title,.production-bulk-bar,.production-batch-actions,.production-batch-header{align-items:flex-start;flex-direction:column}.production-stats{grid-template-columns:repeat(2,minmax(120px,1fr))}.production-batch-name{max-width:70vw}}`}</style>
  </Space>;
}
