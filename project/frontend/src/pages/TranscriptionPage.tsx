import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Dropdown,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Tabs,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  CheckCircleFilled,
  ClockCircleOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExclamationCircleFilled,
  FileAddOutlined,
  FileTextOutlined,
  HistoryOutlined,
  LinkOutlined,
  MoreOutlined,
  ReloadOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  createCrawlerCandidateLinkTranscription,
  createCrawlerLinkTranscription,
  clearTranscriptionHistory,
  deleteTask,
  exportTranscription,
  getTranscription,
  getTranscriptionCapabilities,
  listTranscriptions,
  reconnectTranscription,
  retryTranscription,
  saveTranscriptionRevision,
  uploadAndTranscribe,
} from "../api/client";
import type {
  TranscriptSegment,
  TranscriptionResponse,
} from "../api/types";
import { useToast } from "../components/Toast";
import { usePersistentState } from "../hooks/usePersistentState";
import { cnyToCredits, handleCreditsError } from "../utils/credits";
import "./TranscriptionPage.css";

const { Text } = Typography;
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

function segmentNeedsAttention(segment: TranscriptSegment) {
  return !segment.reviewed && (
    segment.needs_review
    || (segment.confidence !== null && segment.confidence < 0.75)
    || ["pending", "uncertain"].includes(segment.quality_status || "")
  );
}

function formatSeconds(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "--:--";
  const safeValue = Math.max(0, Math.round(value));
  const minutes = Math.floor(safeValue / 60);
  const seconds = safeValue % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function formatSegmentRange(segment: TranscriptSegment) {
  if (segment.start === null || segment.end === null) return "无时间轴";
  return `${formatSeconds(segment.start)}–${formatSeconds(segment.end)}`;
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
  const [shareText, setShareText] = useState("");
  const [filterStatus, setFilterStatus] = usePersistentState("transcription_filter_status", "all");
  const [searchText, setSearchText] = usePersistentState("transcription_search_text", "");
  const [rightsHolder] = usePersistentState("transcription_rights_holder", "本人/公司已授权");

  const [createOpen, setCreateOpen] = useState(false);
  const [createTab, setCreateTab] = useState("douyin-share");
  const [pendingUploadFile, setPendingUploadFile] = useState<File | null>(null);
  const [fileUploadConfirmed, setFileUploadConfirmed] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);
  const [activeSegmentIndex, setActiveSegmentIndex] = useState(0);
  const [revisionDirty, setRevisionDirty] = useState(false);
  const [isSandboxAsr, setIsSandboxAsr] = useState(false);

  const candidateFromQuery = searchParams.get("candidate")?.trim() || "";
  const candidateTitleFromQuery = searchParams.get("title")?.trim() || "";
  const entryFromQuery = searchParams.get("entry")?.trim() || "";
  const shareTextFromQuery = searchParams.get("share_text")?.trim() || "";
  const taskFromQuery = searchParams.get("task")?.trim() || "";
  const isUploadEntry = entryFromQuery === "upload";
  const isNewIntakeEntry = !taskFromQuery && Boolean(
    isUploadEntry || candidateFromQuery || shareTextFromQuery,
  );

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

  const issueIndexes = useMemo(
    () => segments
      .map((segment, index) => segmentNeedsAttention(segment) ? index : -1)
      .filter((index) => index >= 0),
    [segments],
  );
  const reviewedCount = segments.length - issueIndexes.length;

  const applyTask = useCallback((task: TranscriptionResponse, closeHistory = true) => {
    const serverSegments = normalizeSegments(task.segments);
    const firstIssueIndex = serverSegments.findIndex(segmentNeedsAttention);
    setSelected(task);
    setSelectedTaskId(task.task_id);
    setSegments(serverSegments);
    setActiveSegmentIndex(firstIssueIndex >= 0 ? firstIssueIndex : 0);
    setRevisionDirty(false);
    if (closeHistory) setHistoryOpen(false);
  }, [setSelectedTaskId]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const items = await listTranscriptions();
      setTasks(items);
      const targetId = isNewIntakeEntry ? "" : (taskFromQuery || selectedTaskId);
      if (targetId) {
        const task = items.find((item) => item.task_id === targetId) || await getTranscription(targetId);
        applyTask(task, false);
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [applyTask, isNewIntakeEntry, selectedTaskId, taskFromQuery, toast]);

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
    void getTranscriptionCapabilities()
      .then((capability) => setIsSandboxAsr(capability.is_mock))
      .catch(() => setIsSandboxAsr(false));
  }, []);

  useEffect(() => {
    if (isNewIntakeEntry) {
      setSelected(null);
      setSelectedTaskId(null);
      setSegments([]);
    }
    if (isUploadEntry) {
      setCreateTab("file");
      setPendingUploadFile(null);
      setFileUploadConfirmed(false);
      setCreateOpen(true);
      return;
    }
    if (shareTextFromQuery) setShareText(shareTextFromQuery);
    if (candidateFromQuery || shareTextFromQuery) setCreateOpen(true);
  }, [candidateFromQuery, isNewIntakeEntry, isUploadEntry, setSelectedTaskId, shareTextFromQuery]);

  useEffect(() => {
    if (!selected || !["queued", "submitted", "running"].includes(selected.status)) return undefined;
    const timer = window.setInterval(() => { void refresh(); }, 5_000);
    return () => window.clearInterval(timer);
  }, [refresh, selected]);

  const handleShareLinkTranscribe = async () => {
    if (!shareText.trim()) return toast.warning("请粘贴一条平台分享链接");
    const confirmedRightsHolder = rightsHolder.trim() || "本人/公司已授权";
    setSubmitting(true);
    try {
      const result = candidateFromQuery
        ? await createCrawlerCandidateLinkTranscription({ candidateId: candidateFromQuery, rightsHolder: confirmedRightsHolder, rightsConfirmed: true, modelName: "fun-asr" })
        : await createCrawlerLinkTranscription({ shareText, rightsHolder: confirmedRightsHolder, rightsConfirmed: true, modelName: "fun-asr" });
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
    setCreateTab("douyin-share");
    setPendingUploadFile(null);
    setFileUploadConfirmed(false);
    setCreateOpen(true);
  };

  const handleFileUpload = async () => {
    if (!pendingUploadFile) {
      toast.warning("请先选择 MP4 或 MOV 文件");
      return;
    }
    if (!fileUploadConfirmed) {
      toast.warning(isSandboxAsr ? "请先确认文件处理权" : "请先确认处理权和本次云端转写费用");
      return;
    }
    setSubmitting(true);
    try {
      const created = await uploadAndTranscribe(pendingUploadFile, "fun-asr", rightsHolder.trim() || "本人/公司已授权", "zh", candidateFromQuery);
      toast.success("文件已上传并创建转写任务");
      setPendingUploadFile(null);
      setFileUploadConfirmed(false);
      setCreateOpen(false);
      applyTask(created, false);
      await refresh();
    } catch (err) {
      if (!handleCreditsError(err, () => navigate("/admin"))) {
        toast.error((err as Error).message);
      }
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

  const selectSegment = (index: number) => {
    setActiveSegmentIndex(index);
    window.requestAnimationFrame(() => {
      document.getElementById(`transcription-segment-${index}`)?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    });
  };

  const updateSegmentText = (index: number, text: string) => {
    setSegments((items) => items.map((segment, segmentIndex) => (
      segmentIndex === index ? { ...segment, text } : segment
    )));
    setRevisionDirty(true);
  };

  const markSegmentReviewed = (index: number) => {
    setSegments((items) => items.map((segment, segmentIndex) => (
      segmentIndex === index ? { ...segment, reviewed: true } : segment
    )));
    setRevisionDirty(true);
    const nextIssue = issueIndexes.find((issueIndex) => issueIndex > index);
    if (nextIssue !== undefined) selectSegment(nextIssue);
  };

  const revisionSegments = () => segments.map((segment) => ({
    start: segment.start,
    end: segment.end,
    text: segment.text.trim(),
    confidence: segment.confidence,
    needs_review: segment.needs_review,
    reviewed: Boolean(segment.reviewed),
    quality_status: segment.quality_status,
    quality_source: segment.quality_source,
    quality_note: segment.quality_note,
    alternatives: segment.alternatives,
  }));

  const saveRevision = async (approve: boolean) => {
    if (!selected) return false;
    if (segments.some((segment) => !segment.text.trim())) {
      toast.warning("转写内容不能为空，请先补全再保存");
      return false;
    }
    const nextIssueIndex = segments.findIndex(segmentNeedsAttention);
    if (approve && nextIssueIndex >= 0) {
      selectSegment(nextIssueIndex);
      toast.warning(`还有 ${issueIndexes.length} 处待确认`);
      return false;
    }
    if (selected.is_mock) {
      if (!approve) toast.warning("演示数据不能保存草稿");
      return approve;
    }
    setSubmitting(true);
    try {
      await saveTranscriptionRevision({
        taskId: selected.task_id,
        segments: revisionSegments(),
        reviewer: "本人/公司",
        approve,
      });
      setRevisionDirty(false);
      toast.success(approve ? "转写已确认" : "草稿已保存");
      return true;
    } catch (error) {
      toast.error((error as Error).message || "保存转写失败");
      return false;
    } finally {
      setSubmitting(false);
    }
  };

  const handleSaveDraft = async () => {
    if (await saveRevision(false)) await refresh();
  };

  const handleSendToAiCopy = async () => {
    const sourceText = segments.map((segment) => displaySegmentText(segment, selected?.is_mock)).join("\n").trim();
    if (!sourceText) {
      toast.warning("当前没有可带入的转写文本");
      return;
    }
    if (selected && (!selected.approved_revision_id || revisionDirty)) {
      const saved = await saveRevision(true);
      if (!saved) return;
    }
    navigate("/ai-copy", {
      state: {
        sourceText,
        sourceLabel: selected?.media_name || "已质检转写稿",
      },
    });
  };

  return (
    <div className="transcription-page">
      {candidateFromQuery && (
        <Alert
          type="info"
          showIcon
          message="已带入候选视频"
          description={shareTextFromQuery
            ? "原视频链接已自动带入，请确认处理权后开始转写。"
            : `候选 ${candidateTitleFromQuery || candidateFromQuery} 暂无可直接转写媒体，请补充平台分享链接或上传文件。`}
        />
      )}

      {selected ? (
        <section className="transcription-workbench">
          <header className="transcription-taskbar">
            <div className="transcription-task-summary">
              <FileTextOutlined />
              <Text strong ellipsis={{ tooltip: formatTranscriptionName(selected) }}>
                {formatTranscriptionName(selected)}
              </Text>
              <Tag color={STATUS_COLOR[selected.status]}>
                {selected.status === "succeeded" ? "识别完成" : statusLabel(selected.status)}
              </Tag>
              {segments.length > 0 && (
                issueIndexes.length > 0 ? (
                  <Tag color="warning">{issueIndexes.length}处待确认</Tag>
                ) : (
                  <Tag color="success">已全部复核</Tag>
                )
              )}
            </div>
            <Space size={8}>
              <Button icon={<FileAddOutlined />} onClick={handleNewTask}>新建转写</Button>
              <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>转写历史</Button>
              <Dropdown
                trigger={["click"]}
                menu={{
                  items: [
                    { key: "refresh", icon: <ReloadOutlined />, label: "刷新任务" },
                    { type: "divider" },
                    { key: "txt", icon: <DownloadOutlined />, label: "导出 TXT" },
                    { key: "json", icon: <DownloadOutlined />, label: "导出 JSON" },
                    { key: "srt", icon: <DownloadOutlined />, label: "导出 SRT" },
                    { key: "ass", icon: <DownloadOutlined />, label: "导出 ASS" },
                  ],
                  onClick: ({ key }) => {
                    if (key === "refresh") void refresh();
                    else void handleExport(key as "txt" | "json" | "srt" | "ass");
                  },
                }}
              >
                <Button aria-label="更多操作" icon={<MoreOutlined />} />
              </Dropdown>
            </Space>
          </header>

          {selected.error_message && selected.status !== "outcome_unknown" && (
            <Alert type="error" showIcon message={selected.error_message} />
          )}
          {selected.auto_review_error && <Alert type="warning" showIcon message={selected.auto_review_error} />}
          {selected.status === "outcome_unknown" && (
            <div className="transcription-recovery-row">
              <div>
                <Text strong>
                  {selected.provider_job_id ? "云端结果暂时无法确认" : "这次云端提交没有拿到确认结果"}
                </Text>
                <br />
                <Text type="secondary">
                  {selected.provider_job_id
                    ? "系统只会查询原任务，不会重复提交或重复扣费。"
                    : "为避免重复扣费，系统已停止处理；删除记录后可重新选择素材。"}
                </Text>
              </div>
              {selected.provider_job_id ? (
                <Button type="primary" loading={submitting} onClick={handleReconnect}>重新连接原任务</Button>
              ) : (
                <Popconfirm
                  title="删除这条未完成记录？"
                  description="只删除本地记录，不会发起新的云端任务。"
                  okText="删除记录"
                  cancelText="保留"
                  onConfirm={() => void handleDeleteTranscription(selected)}
                >
                  <Button danger loading={deletingTaskId === selected.task_id}>删除记录</Button>
                </Popconfirm>
              )}
            </div>
          )}
          {selected.status === "failed" && (
            <div className="transcription-recovery-row">
              <Text>转写失败，素材仍然保留。</Text>
              <Button type="primary" loading={submitting} onClick={handleRetry}>保留素材并重试</Button>
            </div>
          )}

          {segments.length > 0 ? (
            <div className="transcription-review-layout">
              <div className="transcription-editor-column">
                <div className="transcription-document-scroll" aria-label="转写文稿">
                  {segments.map((segment, index) => {
                    const needsAttention = segmentNeedsAttention(segment);
                    const originalText = segment.alternatives?.[0];
                    return (
                      <article
                        id={`transcription-segment-${index}`}
                        key={`${segment.start}-${index}`}
                        className={`transcription-document-row${activeSegmentIndex === index ? " is-active" : ""}${needsAttention ? " needs-attention" : ""}`}
                        onClick={() => setActiveSegmentIndex(index)}
                      >
                        <button type="button" className="transcription-time-button" onClick={() => selectSegment(index)}>
                          {formatSegmentRange(segment)}
                        </button>
                        <div className="transcription-segment-editor">
                          <TextArea
                            aria-label={`转写片段 ${index + 1}`}
                            value={displaySegmentText(segment, selected.is_mock)}
                            onChange={(event) => updateSegmentText(index, event.target.value)}
                            autoSize={{ minRows: 1, maxRows: 5 }}
                            variant="borderless"
                          />
                          {originalText && originalText !== segment.text && (
                            <Text type="secondary" className="transcription-original-text">原识别：{originalText}</Text>
                          )}
                          {activeSegmentIndex === index && needsAttention && (
                            <div className="transcription-inline-review">
                              <Text type="warning">这里需要你确认</Text>
                              <Button size="small" type="link" icon={<CheckCircleFilled />} onClick={() => markSegmentReviewed(index)}>
                                确认此段
                              </Button>
                            </div>
                          )}
                        </div>
                      </article>
                    );
                  })}
                </div>
              </div>

              <aside className="transcription-issue-rail">
                <div className="transcription-issue-heading">
                  <Text strong>待确认</Text>
                  <Text type="secondary">{issueIndexes.length}处</Text>
                </div>
                {issueIndexes.length > 0 ? issueIndexes.map((index) => (
                  <button
                    type="button"
                    key={index}
                    className={`transcription-issue-link${activeSegmentIndex === index ? " is-active" : ""}`}
                    onClick={() => selectSegment(index)}
                  >
                    <ExclamationCircleFilled className="transcription-issue-dot" />
                    <ClockCircleOutlined />
                    <span>{formatSeconds(segments[index].start)}</span>
                    <span className="transcription-issue-preview">{segments[index].text}</span>
                  </button>
                )) : (
                  <div className="transcription-issue-empty">
                    <CheckCircleFilled />
                    <Text>没有待确认内容</Text>
                  </div>
                )}
              </aside>
            </div>
          ) : (
            <Empty description="该任务暂无可复核内容" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )}

          <footer className="transcription-actionbar">
            <Text>已复核 <strong>{reviewedCount}</strong> / {segments.length} 段</Text>
            <Space>
              <Button onClick={() => void handleSaveDraft()} loading={submitting} disabled={!revisionDirty || selected.is_mock}>
                保存草稿
              </Button>
              <Button
                type="primary"
                loading={submitting}
                onClick={() => void handleSendToAiCopy()}
                disabled={selected.status !== "succeeded" || segments.length === 0}
              >
                确认并带到 AI 文案
              </Button>
            </Space>
          </footer>
        </section>
      ) : (
        <section className="transcription-empty-state">
          <Empty description="新建或从历史选择一个转写任务" image={Empty.PRESENTED_IMAGE_SIMPLE}>
            <Space>
              <Button type="primary" icon={<FileAddOutlined />} onClick={handleNewTask}>新建转写</Button>
              <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>转写历史</Button>
            </Space>
          </Empty>
        </section>
      )}

      <Modal
        title="新建转写"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        width={720}
      >
        <Space direction="vertical" style={{ width: "100%" }} size={16}>
          <Tabs
            activeKey={createTab}
            onChange={setCreateTab}
            items={[
              {
                key: "douyin-share",
                label: <span><LinkOutlined /> 平台分享链接</span>,
                children: (
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <TextArea value={shareText} onChange={(event) => setShareText(event.target.value)} placeholder="粘贴抖音、小红书、快手或B站分享链接" rows={3} />
                    <Button type="primary" loading={submitting} onClick={handleShareLinkTranscribe}>开始转写</Button>
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
                      <p className="ant-upload-hint">
                        {isSandboxAsr ? "选择后确认处理权，再开始免费演示。" : "选择后先确认处理权和费用，再开始转写。"}
                      </p>
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
                          {isSandboxAsr
                            ? "我确认拥有该文件的处理权；本地演示不调用真实云服务，也不扣积分。"
                            : `我确认拥有该文件的处理权，并同意本次公司云端转写按实际时长收费，单条最多 ${cnyToCredits(0.2)} 积分。`}
                        </Checkbox>
                        <Button
                          type="primary"
                          loading={submitting}
                          disabled={!fileUploadConfirmed}
                          onClick={handleFileUpload}
                        >
                          {isSandboxAsr ? "确认权利并开始免费演示" : "确认权利并开始云端转写"}
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
    </div>
  );
}
