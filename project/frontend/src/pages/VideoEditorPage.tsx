/** AI 智能剪辑：系统成片 → 分析 → 复核 → 渲染 → 发布。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Empty,
  List,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Steps,
  Switch,
  Tag,
  Typography,
  message,
} from "antd";
import {
  DownloadOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  RocketOutlined,
  SendOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import {
  createVideoEditorAnalysis,
  createVideoEditorJob,
  generateVideoEditorContentAdvice,
  getTranscription,
  getVideoEditorAnalysis,
  getVideoEditorJob,
  getVideoCapabilities,
  listTemplates,
  listVideoEditorJobs,
  listVideoEditorSources,
} from "../api/client";
import type {
  EditTemplate,
  VideoCapabilitiesResponse,
  VideoEditorAnalysis,
  VideoEditorJob,
  VideoEditorRecommendationStep,
  VideoEditorSource,
} from "../api/types";

const { Title, Text, Paragraph } = Typography;

const PLATFORM_OPTIONS = [
  { value: "douyin", label: "抖音 · 竖屏 9:16" },
  { value: "kuaishou", label: "快手 · 竖屏 9:16" },
  { value: "wechat_channels", label: "视频号 · 竖屏 9:16" },
  { value: "xiaohongshu", label: "小红书 · 竖屏 9:16" },
];

function formatBytes(size?: number | null) {
  if (!size) return "未知";
  return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${(size / 1024).toFixed(1)} KB`;
}

function statusColor(status?: string) {
  if (status === "succeeded") return "success";
  if (status === "failed") return "error";
  return "processing";
}

export default function VideoEditorPage() {
  const navigate = useNavigate();
  const [sources, setSources] = useState<VideoEditorSource[]>([]);
  const [templates, setTemplates] = useState<EditTemplate[]>([]);
  const [capabilities, setCapabilities] = useState<VideoCapabilitiesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedSourceId, setSelectedSourceId] = useState<string>();
  const [platform, setPlatform] = useState("douyin");
  const [subtitleEnabled, setSubtitleEnabled] = useState(true);
  const [subtitleModel, setSubtitleModel] = useState<"large-v3-turbo" | "base">("large-v3-turbo");
  const [analysis, setAnalysis] = useState<VideoEditorAnalysis | null>(null);
  const [steps, setSteps] = useState<VideoEditorRecommendationStep[]>([]);
  const [subtitleApproved, setSubtitleApproved] = useState(false);
  const [job, setJob] = useState<VideoEditorJob | null>(null);
  const [runningAction, setRunningAction] = useState<"analysis" | "render" | "advice" | null>(null);

  const selectedSource = useMemo(
    () => sources.find((item) => item.source_id === selectedSourceId) || null,
    [sources, selectedSourceId],
  );

  const refreshWorkspace = useCallback(async () => {
    setLoading(true);
    try {
      const [sourceResp, templateResp, caps, jobs] = await Promise.all([
        listVideoEditorSources(),
        listTemplates(),
        getVideoCapabilities(),
        listVideoEditorJobs(),
      ]);
      setSources(sourceResp.items);
      setTemplates(templateResp.items);
      setCapabilities(caps);
      setSelectedSourceId((current) => current || sourceResp.items[0]?.source_id);
      setJob((current) => current || jobs.items[0] || null);
    } catch (err) {
      message.error((err as Error).message || "智能剪辑工作台加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refreshWorkspace(); }, [refreshWorkspace]);

  const refreshReview = useCallback(async (taskId?: string | null) => {
    if (!taskId) {
      setSubtitleApproved(false);
      return;
    }
    try {
      const task = await getTranscription(taskId);
      setSubtitleApproved(Boolean(task.approved_revision_id));
    } catch {
      setSubtitleApproved(false);
    }
  }, []);

  useEffect(() => {
    void refreshReview(analysis?.subtitle_task_id);
  }, [analysis?.subtitle_task_id, refreshReview]);

  useEffect(() => {
    if (!analysis || !["queued", "running"].includes(analysis.status)) return;
    const timer = window.setInterval(() => {
      void getVideoEditorAnalysis(analysis.analysis_id)
        .then((next) => {
          setAnalysis(next);
          if (next.recommended_steps) setSteps(next.recommended_steps);
        })
        .catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [analysis]);

  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const timer = window.setInterval(() => {
      void getVideoEditorJob(job.task_id).then(setJob).catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [job]);

  const handleAnalyze = async () => {
    if (!selectedSourceId) {
      message.warning("请先选择系统内的真实成片");
      return;
    }
    setRunningAction("analysis");
    setJob(null);
    setSubtitleApproved(false);
    try {
      const next = await createVideoEditorAnalysis({
        sourceId: selectedSourceId,
        targetPlatform: platform,
        subtitleEnabled,
        subtitleModel,
      });
      setAnalysis(next);
      setSteps(next.recommended_steps || []);
      message.success("已开始分析素材");
    } catch (err) {
      message.error((err as Error).message || "分析创建失败");
    } finally {
      setRunningAction(null);
    }
  };

  const applyTemplate = (template: EditTemplate) => {
    setSteps(template.steps.map((step) => ({
      kind: step.kind,
      params: step.params || {},
      enabled: true,
      label: step.label || step.kind,
    })));
    message.success(`已应用「${template.name}」；你仍可关闭不需要的步骤。`);
  };

  const updateStepEnabled = (index: number, enabled: boolean) => {
    setSteps((current) => current.map((step, stepIndex) => stepIndex === index ? { ...step, enabled } : step));
  };

  const handleContentAdvice = async () => {
    if (!analysis) return;
    setRunningAction("advice");
    try {
      const result = await generateVideoEditorContentAdvice(analysis.analysis_id);
      message.info(result.message);
      const next = await getVideoEditorAnalysis(analysis.analysis_id);
      setAnalysis(next);
    } catch (err) {
      message.error((err as Error).message || "内容建议生成失败");
    } finally {
      setRunningAction(null);
    }
  };

  const handleRender = async () => {
    if (!analysis || analysis.status !== "succeeded") {
      message.warning("请先等待素材分析完成");
      return;
    }
    if (subtitleEnabled && !subtitleApproved) {
      message.warning("字幕已开启，请先完成字幕人工复核并确认成稿");
      return;
    }
    setRunningAction("render");
    try {
      const created = await createVideoEditorJob({
        analysisId: analysis.analysis_id,
        steps,
        outputFormat: "mp4",
        outputResolution: "1080x1920",
        outputFps: 30,
        outputBitrate: "4M",
        subtitleEnabled,
      });
      setJob(created);
      message.success("剪辑任务已进入队列");
    } catch (err) {
      message.error((err as Error).message || "剪辑任务创建失败");
    } finally {
      setRunningAction(null);
    }
  };

  if (loading) return <Spin tip="正在加载智能剪辑工作台" style={{ display: "block", marginTop: 96 }} />;

  const analysisDone = analysis?.status === "succeeded";
  const renderBlocked = !analysisDone || (subtitleEnabled && !subtitleApproved) || !capabilities?.enabled;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Title level={3} style={{ marginBottom: 4 }}><RobotOutlined style={{ color: "var(--primary-600)", marginRight: 8 }} />AI 智能剪辑</Title>
        <Text type="secondary">从系统成片出发，先看真实分析，再由你确认后输出可发布成片。</Text>
      </div>

      {!capabilities?.enabled && <Alert type="warning" showIcon message="本地剪辑引擎不可用" description="检测到 FFmpeg 不可用，当前不能执行真实剪辑。" />}

      <Steps
        current={job ? 3 : analysisDone ? 2 : selectedSourceId ? 1 : 0}
        items={[{ title: "选择成片" }, { title: "智能分析" }, { title: "复核与确认" }, { title: "生成与发布" }]}
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Card title={<Space><VideoCameraOutlined />1. 选择系统成片</Space>} extra={<Button size="small" icon={<ReloadOutlined />} onClick={() => void refreshWorkspace()}>刷新素材</Button>}>
              {sources.length === 0 ? <Empty description="暂无可用的数字人或流水线成片" /> : <>
                <Select
                  value={selectedSourceId}
                  style={{ width: "100%" }}
                  options={sources.map((item) => ({ value: item.source_id, label: `${item.source_type === "avatar" ? "数字人" : "流水线"} · ${item.title}` }))}
                  onChange={(value) => { setSelectedSourceId(value); setAnalysis(null); setJob(null); setSteps([]); }}
                />
                {selectedSource && <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
                  <Col xs={24} md={14}><video controls preload="metadata" style={{ width: "100%", borderRadius: 8, background: "#111" }} src={selectedSource.media_url} /></Col>
                  <Col xs={24} md={10}><Descriptions size="small" column={1}>
                    <Descriptions.Item label="来源">{selectedSource.source_type === "avatar" ? "数字人成片" : "流水线成片"}</Descriptions.Item>
                    <Descriptions.Item label="文件">{selectedSource.file_name}</Descriptions.Item>
                    <Descriptions.Item label="大小">{formatBytes(selectedSource.size_bytes)}</Descriptions.Item>
                  </Descriptions></Col>
                </Row>}
              </>}
            </Card>

            <Card title={<Space><FileSearchOutlined />2. 智能分析</Space>}>
              <Row gutter={[12, 12]} align="middle">
                <Col xs={24} md={8}><Text type="secondary">发布平台</Text><Select value={platform} onChange={setPlatform} options={PLATFORM_OPTIONS} style={{ width: "100%", marginTop: 4 }} /></Col>
                <Col xs={24} md={8}><Text type="secondary">字幕模式</Text><Select value={subtitleModel} onChange={setSubtitleModel} options={[{ value: "large-v3-turbo", label: "准确模式 · large-v3-turbo" }, { value: "base", label: "快速预览 · base" }]} style={{ width: "100%", marginTop: 4 }} disabled={!subtitleEnabled} /></Col>
                <Col xs={24} md={8}><Space direction="vertical"><Text type="secondary">生成字幕</Text><Switch checked={subtitleEnabled} onChange={setSubtitleEnabled} checkedChildren="需复核" unCheckedChildren="关闭" /></Space></Col>
              </Row>
              <Button type="primary" icon={<FileSearchOutlined />} loading={runningAction === "analysis"} disabled={!selectedSourceId || !capabilities?.enabled} onClick={handleAnalyze} style={{ marginTop: 16 }}>开始智能分析</Button>
              {analysis && <div style={{ marginTop: 16 }}><Progress percent={analysis.progress} status={analysis.status === "failed" ? "exception" : analysisDone ? "success" : "active"} /><Text>{analysis.stage}{analysis.error_message ? `：${analysis.error_message}` : ""}</Text></div>}
            </Card>

            <Card title={<Space><RobotOutlined />3. 建议方案与人工确认</Space>}>
              {!analysisDone ? <Empty description="完成分析后会显示基于真实媒体信息的剪辑建议" /> : <Space direction="vertical" style={{ width: "100%" }} size="middle">
                <Alert type="info" showIcon message="建议仅在你确认后执行" description={(analysis.findings || []).join(" ") || "未发现额外处理建议。"} />
                <List size="small" bordered dataSource={steps} locale={{ emptyText: "当前方案不包含强制剪辑步骤" }} renderItem={(step, index) => <List.Item actions={[<Checkbox key="enabled" checked={step.enabled} onChange={(event) => updateStepEnabled(index, event.target.checked)}>执行</Checkbox>]}><Text strong>{step.label}</Text><Text type="secondary">{step.kind}</Text></List.Item>} />
                <div><Text strong>快捷模板</Text><Space wrap style={{ marginLeft: 12 }}>{templates.map((template) => <Button key={template.template_id} size="small" onClick={() => applyTemplate(template)}>{template.name}</Button>)}</Space></div>
                {subtitleEnabled && <Alert type={subtitleApproved ? "success" : "warning"} showIcon message={subtitleApproved ? "字幕已复核，可烧录" : analysis.subtitle_error || "字幕必须人工复核后才能烧录"} action={analysis.subtitle_task_id ? <Space><Button size="small" onClick={() => void refreshReview(analysis.subtitle_task_id)}>刷新状态</Button>{!subtitleApproved && <Button type="primary" size="small" icon={<FileTextOutlined />} onClick={() => navigate(`/transcription?task=${encodeURIComponent(analysis.subtitle_task_id || "")}`)}>去复核</Button>}</Space> : undefined} />}
                <Space wrap><Button loading={runningAction === "advice"} disabled={!subtitleApproved} onClick={() => void handleContentAdvice()}>生成内容节奏建议</Button>{analysis.content_advice && <Paragraph copyable style={{ margin: 0, maxWidth: 640 }}>{analysis.content_advice}</Paragraph>}</Space>
              </Space>}
            </Card>

            <Button type="primary" size="large" block icon={<RocketOutlined />} loading={runningAction === "render"} disabled={renderBlocked} onClick={() => void handleRender()}>
              {subtitleEnabled && !subtitleApproved ? "请先完成字幕复核" : "确认方案并开始真实剪辑"}
            </Button>
          </Space>
        </Col>

        <Col xs={24} xl={10}>
          <Card title={<Space><PlayCircleOutlined />4. 成片结果</Space>} extra={job && <Tag color={statusColor(job.status)}>{job.status}</Tag>}>
            {!job ? <Empty description="开始剪辑后，这里会显示真实任务进度和成片预览" /> : <Space direction="vertical" style={{ width: "100%" }} size="middle">
              <Progress percent={job.progress} status={job.status === "failed" ? "exception" : job.status === "succeeded" ? "success" : "active"} />
              <Text>{job.stage}{job.error_message ? `：${job.error_message}` : ""}</Text>
              {job.status === "succeeded" && job.media_url && <>
                <video controls preload="metadata" style={{ width: "100%", borderRadius: 8, background: "#111" }} src={job.media_url} />
                <Text type="secondary">成片大小：{formatBytes(job.result_size_bytes)}</Text>
                <Space wrap><Button type="primary" icon={<DownloadOutlined />} href={job.download_url || undefined}>下载成片</Button><Button icon={<SendOutlined />} onClick={() => navigate(`/publish?from_edit_task=${encodeURIComponent(job.task_id)}`)}>去发布</Button><Button onClick={() => { setSelectedSourceId(job.source_id || undefined); setAnalysis(null); setJob(null); }}>继续剪辑</Button></Space>
              </>}
            </Space>}
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
