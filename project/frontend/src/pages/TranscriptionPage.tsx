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
  Modal,
  Popconfirm,
  Progress,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  AudioOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FileAddOutlined,
  HistoryOutlined,
  LinkOutlined,
  ReloadOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  createTranscriptionByUrl,
  createCrawlerCandidateLinkTranscription,
  createCrawlerLinkTranscription,
  fallbackCrawlerLinkTranscription,
  getCrawlerLinkTranscriptionCapabilities,
  previewCrawlerLinkTranscription,
  clearTranscriptionHistory,
  deleteTask,
  exportTranscription,
  getTranscription,
  listTranscriptions,
  reconnectTranscription,
  retryTranscription,
  uploadAndTranscribe,
} from "../api/client";
import type {
  TranscriptSegment,
  TranscriptionResponse,
  CrawlerLinkTranscriptionCapabilities,
  CrawlerLinkTranscriptionPreview,
} from "../api/types";
import { useToast } from "../components/Toast";
import { usePersistentState } from "../hooks/usePersistentState";
import "./TranscriptionPage.css";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

const DEMO_LOW_CONFIDENCE_ORIGINAL = "先判断你是通勤、户外，还是长时间带妆。";
const DEMO_LOW_CONFIDENCE_REWRITE = "先看看自己主要是日常通勤、户外活动，还是需要长时间带妆。";
function isDemoLlmRewrite(segment: TranscriptSegment, isMock: boolean | undefined) {
  return Boolean(isMock && (
    segment.quality_status === "llm_rewritten"
    || segment.text === DEMO_LOW_CONFIDENCE_ORIGINAL
  ));
}

function displaySegmentText(segment: TranscriptSegment, isMock: boolean | undefined) {
  return isDemoLlmRewrite(segment, isMock) && segment.text === DEMO_LOW_CONFIDENCE_ORIGINAL
    ? DEMO_LOW_CONFIDENCE_REWRITE
    : segment.text;
}

const STATUS_COLOR: Record<string, string> = {
  queued: "default",
  pending: "default",
  running: "processing",
  submitted: "processing",
  succeeded: "success",
  failed: "error",
  outcome_unknown: "warning",
};

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    pending: "等待中",
    running: "转写中",
    submitted: "已提交云端",
    succeeded: "已完成",
    failed: "失败",
    outcome_unknown: "结果待确认",
  };
  return labels[status] || status;
}

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN") : "-";
}

function sourceLabel(sourceKind: string) {
  if (sourceKind.includes("douyin")) return "抖音素材";
  if (sourceKind.includes("kuaishou")) return "快手素材";
  if (sourceKind.includes("bilibili")) return "B站素材";
  if (sourceKind.includes("xiaohongshu") || sourceKind.includes("xhs")) return "小红书素材";
  if (sourceKind === "manual_text") return "人工转写";
  return "上传视频";
}

function looksLikeReadableTitle(value: string) {
  const baseName = value.replace(/\.[a-z0-9]+$/i, "").trim();
  const opaqueFileName = /^[A-Za-z0-9_-]+$/.test(baseName) && (
    baseName.length > 28
    || /^(input|output|uuu_?\d*|douyin-\d+|kuaishou-target-[\w-]+)$/i.test(baseName)
  );
  return /[\u4e00-\u9fff]/.test(baseName) || (/[a-z]/i.test(baseName) && !opaqueFileName);
}

function transcriptTopic(task: TranscriptionResponse) {
  const transcript = task.segments
    .slice(0, 8)
    .map((segment) => segment.text.trim())
    .filter(Boolean)
    .join(" ");
  if (!transcript) return "";

  if (/贴标机/.test(transcript)) {
    const machine = transcript.match(/(?:平面|立式|全自动|半自动|自动)?贴标机/)?.[0] || "贴标机";
    return `${machine} · 操作说明`;
  }
  if (/(餐饮|烧烤店)/.test(transcript) && /(共享|会员|店长)/.test(transcript)) return "餐饮门店 · 共享会员模式";
  if (/机器人/.test(transcript) && /(工人|工厂|失业)/.test(transcript)) return "工业机器人 · 替代人工";
  if (/(键盘|手感)/.test(transcript) && /(游戏|手游)/.test(transcript)) return "手游操作 · 键位与手感";
  if (/发作品/.test(transcript) && /播放量/.test(transcript)) return "短视频运营 · 提升播放量";

  const firstSentence = transcript.split(/[。！？!?]/)[0] || "";
  const opening = firstSentence
    .trim()
    .replace(/^大家好[，,、]?/, "")
    .replace(/^(咱们|我们)来(看一下|聊一聊|说一说|讲一讲)/, "")
    .replace(/^今天(来)?(聊一聊|说一说|讲一讲)/, "")
    .trim();

  if (!opening || opening.length < 8) return "";
  return `主题 · ${opening.length > 18 ? `${opening.slice(0, 18)}…` : opening}`;
}

function formatTranscriptionName(task: TranscriptionResponse) {
  const topic = transcriptTopic(task);
  if (topic) return topic;

  const candidate = task.title?.trim() || task.media_name?.trim() || "";
  if (candidate && looksLikeReadableTitle(candidate)) return candidate;

  const createdAt = task.created_at ? new Date(task.created_at) : null;
  const timeLabel = createdAt && !Number.isNaN(createdAt.getTime())
    ? `${createdAt.getMonth() + 1}月${createdAt.getDate()}日 ${String(createdAt.getHours()).padStart(2, "0")}:${String(createdAt.getMinutes()).padStart(2, "0")}`
    : "未记录时间";
  return `${sourceLabel(task.source_kind)} · ${timeLabel}`;
}

function normalizeSegments(segments: TranscriptSegment[]) {
  return segments.map((segment) => ({ ...segment, reviewed: segment.reviewed || false }));
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function TranscriptionPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [tasks, setTasks] = useState<TranscriptionResponse[]>([]);
  const [selected, setSelected] = useState<TranscriptionResponse | null>(null);
  const [segments, setSegments] = useState<TranscriptSegment[]>([]);
  const [selectedTaskId, setSelectedTaskId] = usePersistentState<string | null>("transcription_current_task_id", null);
  const [videoUrl, setVideoUrl] = usePersistentState("transcription_video_url", "");
  const [shareText, setShareText] = useState("");
  const [linkPreview, setLinkPreview] = useState<CrawlerLinkTranscriptionPreview | null>(null);
  const [linkCapabilities, setLinkCapabilities] = useState<CrawlerLinkTranscriptionCapabilities | null>(null);
  const [filterStatus, setFilterStatus] = usePersistentState("transcription_filter_status", "all");
  const [searchText, setSearchText] = usePersistentState("transcription_search_text", "");
  const [rightsHolder, setRightsHolder] = usePersistentState("transcription_rights_holder", "本人/公司已授权");

  const [createOpen, setCreateOpen] = useState(false);
  const [createTab, setCreateTab] = useState("url");
  const [pendingUploadFile, setPendingUploadFile] = useState<File | null>(null);
  const [fileUploadConfirmed, setFileUploadConfirmed] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);

  const candidateFromQuery = searchParams.get("candidate")?.trim() || "";
  const candidateTitleFromQuery = searchParams.get("title")?.trim() || "";
  const entryFromQuery = searchParams.get("entry")?.trim() || "";
  const shareTextFromQuery = searchParams.get("share_text")?.trim() || "";
  const urlFromQuery = searchParams.get("url")?.trim() || "";
  const taskFromQuery = searchParams.get("task")?.trim() || "";
  const isUploadEntry = entryFromQuery === "upload";

  const filteredTasks = useMemo(() => {
    const normalized = searchText.trim().toLowerCase();
    return tasks.filter((task) => {
      const statusOk = filterStatus === "all" || task.status === filterStatus;
      const searchOk =
        !normalized ||
        task.media_name.toLowerCase().includes(normalized) ||
        task.title.toLowerCase().includes(normalized) ||
        task.task_id.toLowerCase().includes(normalized);
      return statusOk && searchOk;
    });
  }, [filterStatus, searchText, tasks]);

  const applyTask = useCallback((task: TranscriptionResponse, closeHistory = true) => {
    const serverSegments = normalizeSegments(task.segments);
    setSelected(task);
    setSelectedTaskId(task.task_id);
    setSegments(serverSegments);
    if (closeHistory) setHistoryOpen(false);
  }, [setSelectedTaskId]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const items = await listTranscriptions();
      setTasks(items);
      const targetId = isUploadEntry ? "" : (taskFromQuery || selectedTaskId);
      if (targetId) {
        const task = items.find((item) => item.task_id === targetId) || await getTranscription(targetId);
        applyTask(task, false);
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [applyTask, isUploadEntry, selectedTaskId, taskFromQuery, toast]);

  const handleDeleteTranscription = async (task: TranscriptionResponse) => {
    setDeletingTaskId(task.task_id);
    try {
      await deleteTask(task.task_id);
      setTasks((items) => items.filter((item) => item.task_id !== task.task_id));
      if (selectedTaskId === task.task_id) {
        setSelected(null);
        setSelectedTaskId(null);
        setSegments([]);
      }
      toast.success("转写历史已删除");
    } catch (error) {
      toast.error((error as Error).message || "删除转写历史失败");
    } finally {
      setDeletingTaskId(null);
    }
  };

  const handleClearTranscriptionHistory = async () => {
    setDeletingTaskId("__all_transcriptions__");
    try {
      const result = await clearTranscriptionHistory();
      setTasks([]);
      setSelected(null);
      setSelectedTaskId(null);
      setSegments([]);
      toast.success(`已删除 ${result.deleted_count} 条转写历史`);
    } catch (error) {
      toast.error((error as Error).message || "清空转写历史失败");
    } finally {
      setDeletingTaskId(null);
    }
  };

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (isUploadEntry) {
      setSelected(null);
      setSelectedTaskId(null);
      setSegments([]);
      setCreateTab("file");
      setPendingUploadFile(null);
      setFileUploadConfirmed(false);
      setCreateOpen(true);
      return;
    }
    if (shareTextFromQuery) setShareText(shareTextFromQuery);
    if (urlFromQuery) setVideoUrl(urlFromQuery);
    if (candidateFromQuery || shareTextFromQuery || urlFromQuery) setCreateOpen(true);
  }, [candidateFromQuery, isUploadEntry, setSelectedTaskId, setVideoUrl, shareTextFromQuery, urlFromQuery]);

  useEffect(() => {
    getCrawlerLinkTranscriptionCapabilities().then(setLinkCapabilities).catch(() => setLinkCapabilities(null));
  }, []);

  useEffect(() => {
    if (!selected || !["queued", "submitted", "running"].includes(selected.status)) return undefined;
    const timer = window.setInterval(() => { void refresh(); }, 5_000);
    return () => window.clearInterval(timer);
  }, [refresh, selected]);

  const handlePreviewShareLink = async () => {
    if (!shareText.trim()) return toast.warning("请粘贴一条平台分享链接");
    setSubmitting(true);
    try {
      setLinkPreview(await previewCrawlerLinkTranscription(shareText));
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleShareLinkTranscribe = async (fallback = false) => {
    if (!linkPreview) return;
    setSubmitting(true);
    try {
      const result = fallback
        ? await fallbackCrawlerLinkTranscription({ shareText, workId: linkPreview.work_id || "", rightsHolder, rightsConfirmed: true, idempotencyKey: `link-${Date.now()}` })
        : candidateFromQuery
          ? await createCrawlerCandidateLinkTranscription({ candidateId: candidateFromQuery, rightsHolder, rightsConfirmed: true, modelName: "fun-asr" })
          : await createCrawlerLinkTranscription({ shareText, rightsHolder, rightsConfirmed: true, modelName: "fun-asr" });
      if (result.status === "fallback_required") {
        toast.warning(result.message);
        return;
      }
      if (result.transcription) {
        toast.success(result.message);
        await refresh();
        applyTask(result.transcription);
        setCreateOpen(false);
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleNewTask = () => {
    setSelected(null);
    setSelectedTaskId(null);
    setSegments([]);
    setCreateTab("url");
    setPendingUploadFile(null);
    setFileUploadConfirmed(false);
    setCreateOpen(true);
  };

  const handleUrlTranscribe = async () => {
    const url = videoUrl.trim();
    if (!url) {
      toast.warning("请输入授权 MP4/MOV 直链");
      return;
    }
    setSubmitting(true);
    try {
      const created = await createTranscriptionByUrl(url, true, "fun-asr", rightsHolder);
      toast.success("转写任务已创建");
      setVideoUrl("");
      setCreateOpen(false);
      applyTask(created, false);
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleFileUpload = async () => {
    if (!pendingUploadFile) {
      toast.warning("请先选择 MP4 或 MOV 文件");
      return;
    }
    if (!rightsHolder.trim()) {
      toast.warning("请填写权利主体");
      return;
    }
    if (!fileUploadConfirmed) {
      toast.warning("请先确认处理权和本次云端转写费用");
      return;
    }
    setSubmitting(true);
    try {
      const created = await uploadAndTranscribe(pendingUploadFile, "fun-asr", rightsHolder.trim(), "zh", candidateFromQuery);
      toast.success("文件已上传并创建转写任务");
      setPendingUploadFile(null);
      setFileUploadConfirmed(false);
      setCreateOpen(false);
      applyTask(created, false);
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleReconnect = async () => {
    if (!selected) return;
    setSubmitting(true);
    try {
      applyTask(await reconnectTranscription(selected.task_id), false);
      toast.success("已重新查询原阿里云任务，没有重复提交");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetry = async () => {
    if (!selected) return;
    setSubmitting(true);
    try {
      applyTask(await retryTranscription(selected.task_id), false);
      toast.success("已重新加入云端识别队列");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleExport = async (format: "txt" | "json" | "srt" | "ass") => {
    if (!selected) return;
    try {
      const blob = await exportTranscription(selected.task_id, format);
      downloadBlob(blob, `${selected.task_id}.${format}`);
      toast.success(`已导出 ${format.toUpperCase()}`);
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const handleSendToAiCopy = () => {
    const sourceText = segments.map((segment) => displaySegmentText(segment, selected?.is_mock)).join("\n").trim();
    if (!sourceText) {
      toast.warning("当前没有可带入的转写文本");
      return;
    }
    navigate("/ai-copy", {
      state: {
        sourceText,
        sourceLabel: selected?.media_name || "已质检转写稿",
      },
    });
  };

  const segmentColumns: ColumnsType<TranscriptSegment> = [
    {
      title: "时间",
      width: 150,
      render: (_, record) => record.start === null || record.end === null
        ? <Text type="secondary">无时间轴</Text>
        : <Text code>{record.start.toFixed(1)}s - {record.end.toFixed(1)}s</Text>,
    },
    {
      title: "文本",
      dataIndex: "text",
      render: (value: string, record) => {
        const isDemoRewrite = isDemoLlmRewrite(record, selected?.is_mock);
        const displayText = displaySegmentText(record, selected?.is_mock);
        const originalText = record.alternatives?.[0]
          || (isDemoRewrite ? value : null);
        return (
          <Space direction="vertical" size={0}>
            <Text>{displayText}</Text>
            {originalText && originalText !== displayText && (
              <Text type="secondary" style={{ fontSize: 12 }}>原识别：{originalText}</Text>
            )}
          </Space>
        );
      },
    },
    {
      title: "置信度",
      dataIndex: "confidence",
      width: 110,
      render: (value: number | null) => value === null
        ? selected?.source_kind === "manual_text" ? "人工导入" : "待人工复核"
        : `${Math.round(value * 100)}%`,
    },
    {
      title: selected?.is_mock ? "演示结果" : "复核状态",
      width: 160,
      render: (_, record) => {
        if (selected?.is_mock) {
          const isDemoRewrite = isDemoLlmRewrite(record, true);
          const isUncertain = record.needs_review || (record.confidence !== null && record.confidence < 0.75);
          return <Tag color={isDemoRewrite ? "processing" : isUncertain ? "warning" : "success"}>{isDemoRewrite ? "演示：LLM拟修订" : isUncertain ? "演示存疑" : "演示通过"}</Tag>;
        }
        const labels: Record<string, { color: string; text: string }> = {
          accepted: { color: "success", text: "识别通过" },
          auto_verified: { color: "success", text: "二次确认" },
          auto_corrected: { color: "processing", text: "AI已修正" },
          llm_rewritten: { color: "processing", text: "LLM已修订" },
          uncertain: { color: "warning", text: "AI标记存疑" },
          pending: { color: "warning", text: "待人工复核" },
        };
        const isManualText = selected?.source_kind === "manual_text";
        const isProcessing = ["queued", "pending", "running"].includes(selected?.status || "");
        const item = labels[record.quality_status || ""]
          || (isManualText
            ? { color: "blue", text: "人工导入" }
            : isProcessing
              ? { color: "processing", text: "质检中" }
              : { color: "error", text: "质检状态异常" });
        return <Tag color={item.color}>{item.text}</Tag>;
      },
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Row justify="space-between" align="middle" gutter={[16, 12]}>
        <Col>
          <Title level={4} style={{ margin: 0 }}>语音转写</Title>
          <Text type="secondary">使用公司阿里云识别，不占用客户电脑；识别结果需人工复核。</Text>
        </Col>
        <Col>
          <Space wrap>
            <Button icon={<FileAddOutlined />} onClick={handleNewTask}>新建转写</Button>
            <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>转写历史</Button>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={refresh}>刷新</Button>
          </Space>
        </Col>
      </Row>

      {candidateFromQuery && (
        <Alert
          type="info"
          showIcon
          message="已带入候选视频"
          description={urlFromQuery
            ? "已预填授权直链，请确认权利主体后创建转写。"
            : `候选 ${candidateTitleFromQuery || candidateFromQuery} 暂无可直接转写媒体，请补充已授权直链或上传文件。`}
        />
      )}

      <Card
        title={<Space><AudioOutlined /> 当前任务工作区</Space>}
        extra={selected && (
          <Space wrap>
            <Tag color={STATUS_COLOR[selected.status]}>{statusLabel(selected.status)}</Tag>
            {selected.approved_revision_id && <Tag color="success">已确认成稿</Tag>}
            <Select
              value="txt"
              style={{ width: 90 }}
              options={[
                { value: "txt", label: "TXT" },
                { value: "json", label: "JSON" },
                { value: "srt", label: "SRT" },
                { value: "ass", label: "ASS" },
              ]}
              onSelect={(value) => handleExport(value as "txt" | "json" | "srt" | "ass")}
            />
            <Button icon={<DownloadOutlined />} onClick={() => handleExport("txt")}>导出</Button>
          </Space>
        )}
      >
        {selected ? (
          <Space direction="vertical" style={{ width: "100%" }} size={16}>
            <Space wrap>
              <Text strong>{formatTranscriptionName(selected)}</Text>
              <Tag>{selected.is_mock ? "演示数据" : (selected.model_name || "识别模型未记录")}</Tag>
              {selected.provider_name && <Tag color="blue">公司云端</Tag>}
              {selected.estimated_cost_cny != null && <Tag>预计 ¥{selected.estimated_cost_cny.toFixed(4)}</Tag>}
              {selected.source_kind === "manual_text" && <Tag color="blue">人工回填 · 无时间轴</Tag>}
              {selected.duration_seconds && <Text type="secondary">{Math.round(selected.duration_seconds)} 秒</Text>}
              {selected.auto_reviewed && <Tag color="success">{selected.llm_review_count > 0 ? `LLM自动修订 ${selected.llm_review_count} 段` : "AI自动质检完成"}</Tag>}
              {selected.uncertain_segment_count > 0 && <Tag color="warning">AI标记存疑 {selected.uncertain_segment_count} 段</Tag>}
              <Text type="secondary">{selected.stage}</Text>
            </Space>
            {selected.error_message && <Alert type="error" showIcon message={selected.error_message} />}
            {selected.status === "outcome_unknown" && selected.provider_job_id && (
              <Button type="primary" loading={submitting} onClick={handleReconnect}>
                重新连接原任务
              </Button>
            )}
            {selected.status === "failed" && (
              <Button type="primary" loading={submitting} onClick={handleRetry}>
                保留素材并重试
              </Button>
            )}
            {selected.auto_review_error && <Alert type="warning" showIcon message={selected.auto_review_error} />}
            <Space direction="vertical" style={{ width: "100%" }} size={16}>
              {segments.length > 0 ? (
                <section className="transcription-review-panel">
                  <div className="transcription-review-heading">
                    <Title level={5}>
                      {selected.is_mock ? "演示结果" : selected.provider_name === "aliyun_fun_asr" ? "云端识别结果" : "AI质检结果"}
                    </Title>
                    <Text type="secondary">共 {segments.length} 段</Text>
                  </div>
                  <div className="transcription-review-notice">
                    <Alert
                      type={selected.is_mock ? "info" : selected.provider_name === "aliyun_fun_asr" ? "warning" : selected.uncertain_segment_count > 0 ? "warning" : "success"}
                      showIcon
                      message={selected.is_mock
                        ? "这是演示数据：67% 片段展示了 LLM 口播修订效果，未调用真实模型；上传授权真实视频后会自动执行真实修订。"
                        : selected.llm_review_count > 0
                          ? `LLM 已自动修订 ${selected.llm_review_count} 段低置信口播文本，高置信片段保持原样。`
                          : selected.provider_name === "aliyun_fun_asr"
                            ? `阿里云已返回 ${segments.length} 个时间轴片段。系统未做二次识别或自动改写，请人工复核后再继续。`
                            : selected.uncertain_segment_count > 0
                              ? `AI 已自动成稿；其中 ${selected.uncertain_segment_count} 段保留存疑标记。`
                              : "AI 已完成自动质检并生成成稿。"}
                    />
                    {!selected.timing_available && <Alert type="info" showIcon message="人工回填文本没有时间轴，可导出 TXT/JSON；如需字幕请上传授权视频重新转写。" />}
                  </div>
                  <Tabs
                    className="transcription-review-tabs"
                    items={[
                      {
                        key: "timeline",
                        label: `时间轴（${segments.length}）`,
                        children: (
                          <Table
                            rowKey={(_, index) => String(index)}
                            columns={segmentColumns}
                            dataSource={segments}
                            pagination={false}
                            size="small"
                            scroll={{ x: 720, y: 300 }}
                          />
                        ),
                      },
                      {
                        key: "transcript",
                        label: "完整文稿",
                        children: (
                          <div className="transcription-fulltext-scroll">
                            <Paragraph>{segments.map((segment) => displaySegmentText(segment, selected.is_mock)).join("\n")}</Paragraph>
                          </div>
                        ),
                      },
                    ]}
                  />
                </section>
              ) : <Empty description="该任务暂无可校对片段" />}

              <div className="transcription-next-step">
                <div>
                  <Text strong>下一步：AI 文案改写</Text>
                  <Text type="secondary">确认转写内容后再继续，不会自动改写。</Text>
                </div>
                <Button type="primary" onClick={handleSendToAiCopy} disabled={selected.status !== "succeeded" || segments.length === 0}>
                  确认并带到 AI 文案
                </Button>
              </div>
            </Space>
          </Space>
        ) : (
          <Empty description="新建或从历史选择一个转写任务" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ padding: "40px 0" }} />
        )}
      </Card>

      <Modal
        title="新建转写"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        width={720}
      >
        <Space direction="vertical" style={{ width: "100%" }} size={16}>
          <Alert
            type="warning"
            showIcon
            message="权利确认边界"
            description="确认有权后，可用本机浏览器解析单条平台分享链接；不会批量下载、绕过验证或自动调用付费回退。也可以上传文件或填写授权直链。"
          />
          <Space wrap>
            <Tag color="blue">公司阿里云 Fun-ASR · 单条最多 ¥0.20</Tag>
            <Input value={rightsHolder} onChange={(event) => setRightsHolder(event.target.value)} addonBefore="权利主体" style={{ width: 300 }} />
          </Space>
          <Tabs
            activeKey={createTab}
            onChange={setCreateTab}
            items={[
              {
                key: "url",
                label: <span><LinkOutlined /> 授权直链</span>,
                children: (
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <TextArea value={videoUrl} onChange={(event) => setVideoUrl(event.target.value)} placeholder="填写已授权的 MP4/MOV 直链；不支持平台分享页自动下载" rows={3} />
                    <Button type="primary" loading={submitting} onClick={handleUrlTranscribe}>确认权利并创建转写</Button>
                  </Space>
                ),
              },
              {
                key: "douyin-share",
                label: <span><LinkOutlined /> 平台分享链接</span>,
                children: (
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <Alert
                      type={linkCapabilities?.parser_enabled ? "info" : "warning"}
                      showIcon
                      message={linkCapabilities?.parser_enabled ? "链接解析已就绪" : "链接解析器未就绪"}
                      description="链接解析完成后统一交给公司阿里云识别；客户电脑不会运行本地语音识别。"
                    />
                    <TextArea value={shareText} onChange={(event) => { setShareText(event.target.value); setLinkPreview(null); }} placeholder="粘贴抖音、小红书、快手或B站分享链接" rows={3} />
                    <Button loading={submitting} onClick={handlePreviewShareLink}>识别链接</Button>
                    {linkPreview && (
                      <Alert
                        type={linkPreview.parser_enabled ? "info" : "warning"}
                        showIcon
                        message={linkPreview.parser_enabled ? `已识别${linkPreview.platform_label}作品：${linkPreview.work_id || "等待页面返回作品 ID"}` : `${linkPreview.platform_label}链接解析不可用`}
                        description={
                          <Space wrap>
                            <Text>{linkPreview.parser_message || "可开始解析并交给公司云端转写。"}</Text>
                            <Button type="primary" loading={submitting} disabled={!linkPreview.parser_enabled} onClick={() => handleShareLinkTranscribe(false)}>确认有权并转写</Button>
                            {linkPreview.oneapi_fallback_available && <Button danger loading={submitting} onClick={() => Modal.confirm({ title: "确认 OneAPI 付费回退", content: `预计 ¥${(linkPreview.oneapi_estimated_cost_cny || 0).toFixed(2)}，确认后才会调用。`, okText: "确认并继续", onOk: () => handleShareLinkTranscribe(true) })}>确认后付费回退</Button>}
                          </Space>
                        }
                      />
                    )}
                  </Space>
                ),
              },
              {
                key: "file",
                label: <span><UploadOutlined /> 上传文件</span>,
                children: (
                  <Space direction="vertical" style={{ width: "100%" }} size={16}>
                    {candidateFromQuery && (
                      <Alert
                        type="info"
                        showIcon
                        message={`待处理候选：${candidateTitleFromQuery || candidateFromQuery}`}
                        description="上传后会自动关联到这条候选素材。"
                      />
                    )}
                    <Upload.Dragger
                      accept=".mp4,.mov"
                      beforeUpload={(file) => {
                        setPendingUploadFile(file);
                        setFileUploadConfirmed(false);
                        return false;
                      }}
                      multiple={false}
                      showUploadList={false}
                      disabled={submitting}
                    >
                      <p><UploadOutlined style={{ fontSize: 28 }} /></p>
                      <p>选择 MP4/MOV 文件</p>
                      <p className="ant-upload-hint">选择后先确认处理权和费用，再开始转写。</p>
                    </Upload.Dragger>
                    {pendingUploadFile && (
                      <Space direction="vertical" style={{ width: "100%" }} size={12}>
                        <Alert
                          type="info"
                          showIcon
                          message={`已选择：${pendingUploadFile.name}`}
                          description="文件已暂存，尚未上传或创建转写任务。"
                          action={<Button type="link" onClick={() => { setPendingUploadFile(null); setFileUploadConfirmed(false); }}>移除</Button>}
                        />
                        <Checkbox checked={fileUploadConfirmed} onChange={(event) => setFileUploadConfirmed(event.target.checked)}>
                          我确认拥有该文件的处理权，并同意本次公司云端转写按实际时长收费，单条最多 ¥0.20。
                        </Checkbox>
                        <Button
                          type="primary"
                          loading={submitting}
                          disabled={!fileUploadConfirmed || !rightsHolder.trim()}
                          onClick={handleFileUpload}
                        >
                          确认权利并开始云端转写
                        </Button>
                      </Space>
                    )}
                  </Space>
                ),
              },
            ]}
          />
        </Space>
      </Modal>

      <Drawer
        title="转写历史"
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        width={420}
        extra={(
          <Space>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={refresh}>刷新</Button>
            <Popconfirm
              title="清空全部转写历史？"
              description="会删除全部转写任务和校对版本，不会删除候选视频、素材或其他任务。"
              okText="全部清空"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={handleClearTranscriptionHistory}
            >
              <Button danger icon={<DeleteOutlined />} loading={deletingTaskId === "__all_transcriptions__"} disabled={!tasks.length}>清空全部</Button>
            </Popconfirm>
          </Space>
        )}
      >
        <Space direction="vertical" style={{ width: "100%" }} size={16}>
          <Input value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="搜索视频名称、来源或任务 ID" />
          <Select
            value={filterStatus}
            onChange={setFilterStatus}
            style={{ width: "100%" }}
            options={[
              { value: "all", label: "全部" },
              { value: "running", label: "转写中" },
              { value: "succeeded", label: "已完成" },
              { value: "failed", label: "失败" },
            ]}
          />
          <List
            loading={loading}
            dataSource={filteredTasks}
            locale={{ emptyText: "暂无转写历史" }}
            renderItem={(item) => (
              <List.Item
                actions={[
                  <Button key="load" type="link" onClick={() => applyTask(item)}>载入</Button>,
                  <Popconfirm
                    key="delete"
                    title="删除这条转写历史？"
                    description="会删除转写任务和校对版本，不会删除候选视频或已下载素材。"
                    okText="删除"
                    okButtonProps={{ danger: true }}
                    cancelText="取消"
                    onConfirm={() => handleDeleteTranscription(item)}
                  >
                    <Button type="link" danger icon={<DeleteOutlined />} loading={deletingTaskId === item.task_id}>删除</Button>
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  title={<Space wrap><Text strong>{formatTranscriptionName(item)}</Text><Tag color={STATUS_COLOR[item.status]}>{statusLabel(item.status)}</Tag></Space>}
                  description={
                    <Space direction="vertical" size={4}>
                      <Text type="secondary">创建于 {formatTime(item.created_at)} · {item.progress}%</Text>
                      <Progress percent={item.progress} size="small" status={item.status === "failed" ? "exception" : item.status === "succeeded" ? "success" : "active"} />
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        </Space>
      </Drawer>
    </Space>
  );
}
