/** 自动优先的 AI 智能剪辑工作台。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Drawer,
  Empty,
  Input,
  List,
  Progress,
  Row,
  Select,
  Slider,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  DownloadOutlined,
  EditOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
  SoundOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import {
  confirmVideoEditorBatchResults,
  continueVideoEditorBatchItem,
  createVideoEditorBatch,
  getTranscription,
  getVideoCapabilities,
  listVideoEditorBgm,
  listTemplates,
  listVideoEditorBatches,
  listVideoEditorLocalModels,
  listVideoEditorSources,
  retryVideoEditorBatchItem,
  saveTranscriptionRevision,
  selectVideoEditorBatchItemTitle,
  uploadVideoEditorBgm,
  uploadVideoEditorSources,
} from "../api/client";
import type {
  EditTemplate,
  TranscriptSegment,
  TranscriptionResponse,
  VideoCapabilitiesResponse,
  VideoEditorBatch,
  VideoEditorBatchItem,
  VideoEditorBgmAsset,
  VideoEditorLocalModel,
  VideoEditorSource,
} from "../api/types";

const { Title, Text, Paragraph } = Typography;

const PLATFORM_OPTIONS = [
  { value: "douyin", label: "抖音 · 竖屏 9:16" },
  { value: "kuaishou", label: "快手 · 竖屏 9:16" },
  { value: "wechat_channels", label: "视频号 · 竖屏 9:16" },
  { value: "xiaohongshu", label: "小红书 · 竖屏 9:16" },
];

const STATUS_META: Record<string, { color: string; label: string }> = {
  queued: { color: "default", label: "等待处理" },
  analyzing: { color: "processing", label: "智能分析中" },
  awaiting_subtitle_review: { color: "gold", label: "待字幕复核" },
  ready_to_render: { color: "processing", label: "准备渲染" },
  rendering: { color: "processing", label: "正在剪辑" },
  awaiting_output_confirmation: { color: "blue", label: "待确认成片" },
  ready_to_publish: { color: "success", label: "已确认" },
  failed: { color: "error", label: "失败" },
  interrupted: { color: "warning", label: "中断" },
};

function formatBytes(value?: number | null) {
  if (!value) return "大小未知";
  return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${(value / 1024).toFixed(1)} KB`;
}

function statusTag(status: string) {
  const meta = STATUS_META[status] || { color: "default", label: status };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

export default function VideoEditorPage() {
  const navigate = useNavigate();
  const [sources, setSources] = useState<VideoEditorSource[]>([]);
  const [templates, setTemplates] = useState<EditTemplate[]>([]);
  const [capabilities, setCapabilities] = useState<VideoCapabilitiesResponse | null>(null);
  const [batch, setBatch] = useState<VideoEditorBatch | null>(null);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [platform, setPlatform] = useState("douyin");
  const [subtitleEnabled, setSubtitleEnabled] = useState(true);
  const [subtitleModel, setSubtitleModel] = useState<"large-v3-turbo" | "base">("base");
  const [performanceMode, setPerformanceMode] = useState<"light" | "quality">("light");
  const [localModels, setLocalModels] = useState<VideoEditorLocalModel[]>([]);
  const [bgmAssets, setBgmAssets] = useState<VideoEditorBgmAsset[]>([]);
  const [bgmEnabled, setBgmEnabled] = useState(true);
  const [bgmId, setBgmId] = useState<string>();
  const [bgmVolume, setBgmVolume] = useState(0.24);
  const [bgmUploading, setBgmUploading] = useState(false);
  const [bgmRightsConfirmed, setBgmRightsConfirmed] = useState(false);
  const [bgmRightsHolder, setBgmRightsHolder] = useState("");
  const [bgmMood, setBgmMood] = useState("通用");
  const [templateId, setTemplateId] = useState<string>();
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [reviewItem, setReviewItem] = useState<VideoEditorBatchItem | null>(null);
  const [reviewTask, setReviewTask] = useState<TranscriptionResponse | null>(null);
  const [reviewSegments, setReviewSegments] = useState<TranscriptSegment[]>([]);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [confirmedItemIds, setConfirmedItemIds] = useState<string[]>([]);

  const refresh = useCallback(async (keepBatch = true) => {
    setLoading(true);
    try {
      const [sourceResp, templateResp, caps, batchResp, bgmResp, modelResp] = await Promise.all([
        listVideoEditorSources(), listTemplates(), getVideoCapabilities(), listVideoEditorBatches(),
        listVideoEditorBgm(), listVideoEditorLocalModels(),
      ]);
      setSources(sourceResp.items);
      setTemplates(templateResp.items);
      setCapabilities(caps);
      setBgmAssets(bgmResp.items);
      setLocalModels(modelResp.items);
      setBatch((current) => {
        if (!keepBatch) return batchResp.items[0] || null;
        return batchResp.items.find((item) => item.batch_id === current?.batch_id) || current || batchResp.items[0] || null;
      });
    } catch (error) {
      message.error((error as Error).message || "智能剪辑工作台加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(false); }, [refresh]);

  useEffect(() => {
    if (!batch?.items.some((item) => ["analyzing", "ready_to_render", "rendering"].includes(item.status))) return undefined;
    const timer = window.setInterval(() => {
      void listVideoEditorBatches().then((result) => {
        const next = result.items.find((item) => item.batch_id === batch.batch_id);
        if (next) setBatch(next);
      }).catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [batch]);

  useEffect(() => {
    setConfirmedItemIds((current) => current.filter((itemId) => batch?.items.some((item) => item.item_id === itemId && item.status === "awaiting_output_confirmation")));
  }, [batch]);

  const selectedTemplate = useMemo(
    () => templates.find((item) => item.template_id === templateId),
    [templateId, templates],
  );
  const selectedBgm = useMemo(
    () => bgmAssets.find((item) => item.asset_id === bgmId),
    [bgmAssets, bgmId],
  );
  const selectedModelStatus = localModels.find((item) => item.model_name === subtitleModel);

  const startBatch = async () => {
    if (!selectedSourceIds.length) {
      message.warning("请先选择至少一条系统成片或本地上传素材");
      return;
    }
    setSubmitting(true);
    try {
      const created = await createVideoEditorBatch({
        sourceIds: selectedSourceIds,
        targetPlatform: platform,
        subtitleEnabled,
        subtitleModel,
        steps: (selectedTemplate?.steps || []).map((step) => ({ kind: step.kind, params: step.params || {}, enabled: true })),
        outputFormat: "mp4",
        outputResolution: performanceMode === "light" ? "720x1280" : "1080x1920",
        outputFps: performanceMode === "light" ? 25 : 30,
        outputBitrate: performanceMode === "light" ? "2M" : "4M",
        bgmEnabled,
        bgmId,
        bgmVolume,
      });
      setBatch(created);
      message.success(`已启动 ${created.items.length} 条素材的自动剪辑`);
    } catch (error) {
      message.error((error as Error).message || "无法启动自动剪辑");
    } finally {
      setSubmitting(false);
    }
  };

  const uploadBgm = async (file: File) => {
    if (!bgmRightsConfirmed || !bgmRightsHolder.trim()) {
      message.warning("请先填写音乐权利主体并确认拥有使用权");
      return;
    }
    setBgmUploading(true);
    try {
      const asset = await uploadVideoEditorBgm({
        file,
        mood: bgmMood,
        rightsHolder: bgmRightsHolder.trim(),
      });
      setBgmAssets((current) => [asset, ...current.filter((item) => item.asset_id !== asset.asset_id)]);
      setBgmId(asset.asset_id);
      setBgmEnabled(true);
      message.success("背景音乐已保存到本机授权音乐库");
    } catch (error) {
      message.error((error as Error).message || "背景音乐上传失败");
    } finally {
      setBgmUploading(false);
    }
  };

  const selectTitle = async (item: VideoEditorBatchItem, title: string) => {
    if (!batch) return;
    try {
      setBatch(await selectVideoEditorBatchItemTitle(batch.batch_id, item.item_id, title));
      message.success("发布标题已保存");
    } catch (error) {
      message.error((error as Error).message || "标题保存失败");
    }
  };

  const uploadFiles = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    try {
      const result = await uploadVideoEditorSources(files, "本人/公司已授权");
      setSources((current) => [...result.items, ...current.filter((item) => !result.items.some((uploaded) => uploaded.source_id === item.source_id))]);
      setSelectedSourceIds((current) => [...new Set([...current, ...result.items.map((item) => item.source_id)])]);
      message.success(`已加入 ${result.items.length} 条本地素材`);
    } catch (error) {
      message.error((error as Error).message || "素材上传失败");
    } finally {
      setUploading(false);
    }
  };

  const openSubtitleReview = async (item: VideoEditorBatchItem) => {
    if (!item.subtitle_task_id) return;
    try {
      const task = await getTranscription(item.subtitle_task_id);
      setReviewItem(item);
      setReviewTask(task);
      setReviewSegments(task.segments.map((segment) => ({ ...segment, reviewed: segment.reviewed || false })));
    } catch (error) {
      message.error((error as Error).message || "字幕草稿加载失败");
    }
  };

  const approveSubtitle = async () => {
    if (!batch || !reviewItem || !reviewTask) return;
    setReviewSaving(true);
    try {
      await saveTranscriptionRevision({
        taskId: reviewTask.task_id,
        segments: reviewSegments.map((segment) => ({
          start: segment.start,
          end: segment.end,
          text: segment.text,
          confidence: segment.confidence,
          needs_review: segment.needs_review,
          reviewed: Boolean(segment.reviewed),
        })),
        reviewer: "当前用户",
        approve: true,
      });
      const next = await continueVideoEditorBatchItem(batch.batch_id, reviewItem.item_id);
      setBatch(next);
      setReviewItem(null);
      setReviewTask(null);
      message.success("字幕已确认，正在继续自动剪辑");
    } catch (error) {
      message.error((error as Error).message || "字幕确认失败");
    } finally {
      setReviewSaving(false);
    }
  };

  const retryItem = async (item: VideoEditorBatchItem) => {
    if (!batch) return;
    try {
      setBatch(await retryVideoEditorBatchItem(batch.batch_id, item.item_id));
      message.success("已重新加入分析队列");
    } catch (error) {
      message.error((error as Error).message || "重试失败");
    }
  };

  const confirmResults = async () => {
    if (!batch || !confirmedItemIds.length) return;
    setSubmitting(true);
    try {
      setBatch(await confirmVideoEditorBatchResults(batch.batch_id, confirmedItemIds));
      setConfirmedItemIds([]);
      message.success("已批量确认成片；可逐条进入发布配置");
    } catch (error) {
      message.error((error as Error).message || "批量确认失败");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading && !sources.length) return <div style={{ marginTop: 96, textAlign: "center" }}><Spin tip="正在加载智能剪辑工作台"><div style={{ minHeight: 48 }} /></Spin></div>;

  return <Space direction="vertical" size="large" style={{ width: "100%" }} className="video-editor-auto-page">
    <div className="video-editor-title">
      <div><Title level={3} style={{ margin: 0 }}><RobotOutlined /> AI 智能剪辑</Title><Text type="secondary">选择素材后自动分析、字幕复核和渲染；每条任务都有真实状态与可重试入口。</Text></div>
      <Button icon={<ReloadOutlined />} onClick={() => void refresh()}>刷新工作台</Button>
    </div>

    {!capabilities?.enabled && <Alert type="warning" showIcon message="本地剪辑引擎不可用" description="检测到 FFmpeg 不可用，当前不能执行真实剪辑。" />}

    <Card className="video-editor-launcher" title={<Space><PlayCircleOutlined /> 自动剪辑</Space>} extra={<Tag color="purple">默认同时 1 条</Tag>}>
      <Row gutter={[20, 20]}>
        <Col xs={24} lg={15}>
          <Text strong>1. 选择素材</Text><Text type="secondary" style={{ marginLeft: 8 }}>系统成片与本地上传可混合多选，单次最多 10 条。</Text>
          <Select
            mode="multiple"
            value={selectedSourceIds}
            onChange={setSelectedSourceIds}
            placeholder="选择系统成片"
            style={{ width: "100%", marginTop: 10 }}
            options={sources.map((source) => ({ value: source.source_id, label: `${source.source_type === "upload" ? "本地上传" : source.source_type === "avatar" ? "数字人" : "流水线"} · ${source.title} · ${formatBytes(source.size_bytes)}` }))}
          />
          <Space wrap style={{ marginTop: 12 }}>
            <Upload accept="video/mp4,video/quicktime,video/x-m4v" multiple showUploadList={false} beforeUpload={(file) => { void uploadFiles([file as File]); return Upload.LIST_IGNORE; }}>
              <Button icon={<UploadOutlined />} loading={uploading}>上传本地 MP4/MOV</Button>
            </Upload>
            <Text type="secondary">上传即确认“本人/公司已授权”；仅保存到本机。</Text>
          </Space>
        </Col>
        <Col xs={24} lg={9}>
          <Text strong>2. 自动配置</Text>
          <Select value={platform} onChange={setPlatform} options={PLATFORM_OPTIONS} style={{ width: "100%", marginTop: 10 }} />
          <Space direction="vertical" style={{ marginTop: 12 }}>
            <Select
              value={performanceMode}
              onChange={(value) => {
                setPerformanceMode(value);
                setSubtitleModel(value === "light" ? "base" : "large-v3-turbo");
              }}
              style={{ width: 260, maxWidth: "100%" }}
              options={[
                { value: "light", label: "轻量模式 · 720P · 适合 8GB" },
                { value: "quality", label: "清晰模式 · 1080P" },
              ]}
            />
            <Space><Switch checked={subtitleEnabled} onChange={setSubtitleEnabled} checkedChildren="字幕需复核" unCheckedChildren="不生成字幕" /><Text>生成并人工确认字幕</Text></Space>
            <Space wrap>
              <Select disabled={!subtitleEnabled} value={subtitleModel} onChange={setSubtitleModel} style={{ width: 260 }} options={[{ value: "base", label: "快速字幕 · base" }, { value: "large-v3-turbo", label: "准确字幕 · large-v3-turbo" }]} />
              {subtitleEnabled && <Tag color={selectedModelStatus?.installed ? "success" : "warning"}>{selectedModelStatus?.installed ? "已部署到本机" : "首次使用自动下载"}</Tag>}
            </Space>
          </Space>
        </Col>
      </Row>
      <div className="video-editor-bgm">
        <Space wrap>
          <Text strong><SoundOutlined /> 3. 本地智能配乐</Text>
          <Switch checked={bgmEnabled} onChange={setBgmEnabled} checkedChildren="自动配乐" unCheckedChildren="保持原声" />
          <Select
            allowClear
            disabled={!bgmEnabled}
            value={bgmId}
            onChange={setBgmId}
            placeholder={bgmAssets.length ? "不选择则按内容自动推荐" : "音乐库为空，将保持原声"}
            style={{ width: 260, maxWidth: "100%" }}
            options={bgmAssets.map((asset) => ({ value: asset.asset_id, label: `${asset.mood} · ${asset.title}` }))}
          />
        </Space>
        {bgmEnabled && <>
          <Space wrap style={{ marginTop: 10 }}>
            <Input value={bgmRightsHolder} onChange={(event) => setBgmRightsHolder(event.target.value)} placeholder="音乐权利主体，例如：本人/公司" style={{ width: 240 }} />
            <Input value={bgmMood} onChange={(event) => setBgmMood(event.target.value)} placeholder="情绪标签" style={{ width: 140 }} />
            <Checkbox checked={bgmRightsConfirmed} onChange={(event) => setBgmRightsConfirmed(event.target.checked)}>我确认拥有使用权</Checkbox>
            <Upload accept="audio/mpeg,audio/wav,audio/mp4,audio/aac,audio/flac" showUploadList={false} beforeUpload={(file) => { void uploadBgm(file as File); return Upload.LIST_IGNORE; }}>
              <Button icon={<UploadOutlined />} loading={bgmUploading} disabled={!bgmRightsConfirmed || !bgmRightsHolder.trim()}>上传授权音乐</Button>
            </Upload>
          </Space>
          <Space wrap style={{ marginTop: 10 }}>
            <Text type="secondary">人声视频的音量上限</Text>
            <Slider min={0.08} max={0.5} step={0.01} value={bgmVolume} onChange={setBgmVolume} style={{ width: 180 }} />
            <Text>{Math.round(bgmVolume * 100)}%</Text>
            <Text type="secondary">一键出片会自动选曲、统一响度、循环截断、淡入淡出，并在人声出现时压低 BGM。</Text>
          </Space>
          {selectedBgm && <audio controls preload="metadata" src={selectedBgm.media_url} style={{ width: "100%", maxWidth: 520, marginTop: 8 }} />}
        </>}
      </div>
      <div className="video-editor-launch-actions">
        <Button type="primary" size="large" icon={<RobotOutlined />} loading={submitting} disabled={!selectedSourceIds.length || !capabilities?.enabled} onClick={() => void startBatch()}>
          一键智能剪辑 {selectedSourceIds.length ? `${selectedSourceIds.length} 条` : ""}
        </Button>
        <Button type="link" onClick={() => setAdvancedOpen((open) => !open)}>{advancedOpen ? "收起高级设置" : "展开高级设置"}</Button>
      </div>
      {advancedOpen && <div className="video-editor-advanced"><Text strong>剪辑模板</Text><Select allowClear value={templateId} onChange={setTemplateId} placeholder="不选则按素材分析建议执行" options={templates.map((template) => ({ value: template.template_id, label: template.name }))} style={{ width: 300, maxWidth: "100%", margin: "8px 0" }} /><Paragraph type="secondary" style={{ margin: 0 }}>模板只决定剪辑步骤；字幕开启时仍必须完成当前页人工复核。</Paragraph></div>}
    </Card>

    <Card title={<Space><ClockCircleOutlined /> 执行进度</Space>} extra={batch && <Space>{statusTag(batch.status)}<Text type="secondary">批次 {batch.batch_id.slice(-6)}</Text></Space>}>
      {!batch ? <Empty description="启动自动剪辑后，这里会显示每条素材的真实执行状态" /> : <List
        dataSource={batch.items}
        locale={{ emptyText: "没有批次任务" }}
        renderItem={(item) => <List.Item className="video-editor-run-item" actions={[
          item.status === "awaiting_subtitle_review" ? <Button key="review" type="primary" size="small" icon={<EditOutlined />} onClick={() => void openSubtitleReview(item)}>复核字幕</Button> : null,
          ["failed", "interrupted"].includes(item.status) ? <Button key="retry" size="small" icon={<ReloadOutlined />} onClick={() => void retryItem(item)}>重试</Button> : null,
        ].filter(Boolean)}>
          <Space direction="vertical" size={3} style={{ width: "100%" }}>
            <Space wrap><Text strong>{item.title}</Text>{statusTag(item.status)}</Space>
            {item.analysis && <Progress percent={item.status === "analyzing" ? item.analysis.progress : item.job?.progress || (item.status === "awaiting_output_confirmation" || item.status === "ready_to_publish" ? 100 : 60)} size="small" status={["failed", "interrupted"].includes(item.status) ? "exception" : item.status === "awaiting_output_confirmation" || item.status === "ready_to_publish" ? "success" : "active"} />}
            <Text type={item.error_message ? "danger" : "secondary"}>{item.error_message || item.job?.stage || item.analysis?.stage || STATUS_META[item.status]?.label}</Text>
            {item.bgm_reason && <Text type="secondary"><SoundOutlined /> {item.bgm_reason}</Text>}
            {!!item.title_candidates.length && <Space wrap>
              <Text type="secondary">发布标题</Text>
              <Select
                size="small"
                value={item.selected_title || item.title_candidates[0]}
                onChange={(value) => void selectTitle(item, value)}
                style={{ width: 300, maxWidth: "100%" }}
                options={item.title_candidates.map((title) => ({ value: title, label: title }))}
              />
              <Tag>本地生成 · 可在发布页继续改</Tag>
            </Space>}
          </Space>
        </List.Item>}
      />}
    </Card>

    <Card title={<Space><CheckCircleOutlined /> 成片结果</Space>} extra={confirmedItemIds.length ? <Button type="primary" loading={submitting} onClick={() => void confirmResults()}>批量确认 {confirmedItemIds.length} 条</Button> : undefined}>
      {!batch?.items.some((item) => item.job?.status === "succeeded") ? <Empty description="真实成片生成后会在这里预览、下载和确认" /> : <Row gutter={[16, 16]}>{batch.items.filter((item) => item.job?.status === "succeeded").map((item) => <Col xs={24} md={12} xl={8} key={item.item_id}><Card size="small" title={<Space><Checkbox checked={confirmedItemIds.includes(item.item_id)} disabled={item.status !== "awaiting_output_confirmation"} onChange={(event) => setConfirmedItemIds((current) => event.target.checked ? [...current, item.item_id] : current.filter((id) => id !== item.item_id))} /><Text ellipsis style={{ maxWidth: 160 }}>{item.title}</Text></Space>} extra={statusTag(item.status)}>
        {item.job?.media_url && <video controls preload="metadata" src={item.job.media_url} style={{ width: "100%", borderRadius: 8, background: "#111" }} />}
        <Space wrap style={{ marginTop: 10 }}><Button size="small" icon={<DownloadOutlined />} href={item.job?.download_url || undefined}>下载</Button><Button size="small" icon={<SendOutlined />} disabled={item.status !== "ready_to_publish"} onClick={() => navigate(`/publish?from_edit_task=${encodeURIComponent(item.edit_task_id || "")}`)}>去发布</Button></Space>
      </Card></Col>)}</Row>}
    </Card>

    <Drawer title="字幕人工复核" width={720} open={Boolean(reviewItem)} onClose={() => { setReviewItem(null); setReviewTask(null); }} extra={<Button type="primary" loading={reviewSaving} onClick={() => void approveSubtitle()}>确认成稿并继续剪辑</Button>}>
      {!reviewTask ? <Spin /> : <Space direction="vertical" size="middle" style={{ width: "100%" }}><Alert type="info" showIcon message="确认后才会把字幕烧录到成片" description="你可以直接修改每一段字幕；本次只影响当前剪辑任务。" />{reviewSegments.map((segment, index) => <Card key={`${segment.start}-${index}`} size="small" title={`${segment.start?.toFixed(1) ?? "-"}s – ${segment.end?.toFixed(1) ?? "-"}s`} extra={segment.needs_review && !segment.reviewed ? <Tag color="gold">待核对</Tag> : null}><Input.TextArea value={segment.text} autoSize={{ minRows: 2, maxRows: 5 }} onChange={(event) => setReviewSegments((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, text: event.target.value, reviewed: true } : item))} /></Card>)}</Space>}
    </Drawer>

    <style>{`.video-editor-title,.video-editor-launch-actions{display:flex;align-items:center;justify-content:space-between;gap:16px}.video-editor-launch-actions{margin-top:18px}.video-editor-bgm{margin-top:18px;padding-top:16px;border-top:1px solid var(--border-color,#eef0f3)}.video-editor-advanced{margin-top:12px;padding:12px;background:var(--primary-50);border-radius:10px}.video-editor-run-item .ant-list-item-action{margin-inline-start:18px}@media(max-width:768px){.video-editor-title,.video-editor-launch-actions{align-items:flex-start;flex-direction:column}.video-editor-run-item{align-items:flex-start}.video-editor-run-item .ant-list-item-action{margin-inline-start:0;margin-top:10px}}`}</style>
  </Space>;
}
