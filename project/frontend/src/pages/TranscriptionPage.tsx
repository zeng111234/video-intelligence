import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  Drawer,
  Empty,
  Input,
  InputNumber,
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
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FileAddOutlined,
  HistoryOutlined,
  LinkOutlined,
  ReloadOutlined,
  SaveOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  createTranscriptionByUrl,
  createCrawlerLinkTranscription,
  fallbackCrawlerLinkTranscription,
  getCrawlerLinkTranscriptionCapabilities,
  previewCrawlerLinkTranscription,
  clearTranscriptionHistory,
  createComplianceDraft,
  createVoiceoverDraft,
  deleteTask,
  exportTranscription,
  getTranscription,
  listTranscriptions,
  listComplianceDrafts,
  listVoiceoverDrafts,
  saveTranscriptionRevision,
  updateVoiceoverDraft,
  uploadAndTranscribe,
} from "../api/client";
import type {
  TranscriptSegment,
  TranscriptionResponse,
  CrawlerLinkTranscriptionCapabilities,
  CrawlerLinkTranscriptionPreview,
  VoiceoverDraftResponse,
} from "../api/types";
import { useToast } from "../components/Toast";
import { usePersistentState } from "../hooks/usePersistentState";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

const STATUS_COLOR: Record<string, string> = {
  queued: "default",
  pending: "default",
  running: "processing",
  succeeded: "success",
  failed: "error",
};

interface SegmentDraft {
  taskId: string;
  serverUpdatedAt: string | null;
  segments: TranscriptSegment[];
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    pending: "等待中",
    running: "转写中",
    succeeded: "已完成",
    failed: "失败",
  };
  return labels[status] || status;
}

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN") : "-";
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
  const [segmentDrafts, setSegmentDrafts] = usePersistentState<Record<string, SegmentDraft>>("transcription_segment_drafts", {}, undefined, 1000);
  const [videoUrl, setVideoUrl] = usePersistentState("transcription_video_url", "");
  const [shareText, setShareText] = useState("");
  const [linkPreview, setLinkPreview] = useState<CrawlerLinkTranscriptionPreview | null>(null);
  const [linkCapabilities, setLinkCapabilities] = useState<CrawlerLinkTranscriptionCapabilities | null>(null);
  const [linkRightsConfirmed, setLinkRightsConfirmed] = useState(false);
  const [reviewer, setReviewer] = usePersistentState("transcription_reviewer", "校对员");
  const [filterStatus, setFilterStatus] = usePersistentState("transcription_filter_status", "all");
  const [searchText, setSearchText] = usePersistentState("transcription_search_text", "");
  const [asrModel, setAsrModel] = usePersistentState("transcription_asr_model", "large-v3-turbo");
  const [rightsHolder, setRightsHolder] = usePersistentState("transcription_rights_holder", "本人/公司已授权");
  const [targetSeconds, setTargetSeconds] = usePersistentState("transcription_target_seconds", 45);
  const [activeDraftIndex, setActiveDraftIndex] = usePersistentState("transcription_active_draft_index", 0);
  const [activePanel, setActivePanel] = usePersistentState<"review" | "voiceover" | "compliance">("transcription_active_panel", "review");

  const [createOpen, setCreateOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [voiceoverDrafts, setVoiceoverDrafts] = useState<VoiceoverDraftResponse[]>([]);
  const [activeVoiceover, setActiveVoiceover] = useState<VoiceoverDraftResponse | null>(null);
  const [complianceDrafts, setComplianceDrafts] = useState<VoiceoverDraftResponse[]>([]);
  const [activeCompliance, setActiveCompliance] = useState<VoiceoverDraftResponse | null>(null);

  const candidateFromQuery = searchParams.get("candidate")?.trim() || "";
  const candidateTitleFromQuery = searchParams.get("title")?.trim() || "";
  const shareTextFromQuery = searchParams.get("share_text")?.trim() || "";
  const urlFromQuery = searchParams.get("url")?.trim() || "";
  const taskFromQuery = searchParams.get("task")?.trim() || "";

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

  const loadVoiceoverDrafts = useCallback(async (taskId: string) => {
    try {
      const drafts = await listVoiceoverDrafts(taskId);
      setVoiceoverDrafts(drafts);
      setActiveVoiceover(drafts[0] || null);
      setActiveDraftIndex(0);
    } catch (err) {
      setVoiceoverDrafts([]);
      setActiveVoiceover(null);
      toast.error((err as Error).message || "读取口播稿历史失败");
    }
  }, [setActiveDraftIndex, toast]);

  const loadComplianceDrafts = useCallback(async (taskId: string) => {
    try {
      const drafts = await listComplianceDrafts(taskId);
      setComplianceDrafts(drafts);
      setActiveCompliance(drafts[0] || null);
    } catch (err) {
      setComplianceDrafts([]);
      setActiveCompliance(null);
      toast.error((err as Error).message || "读取合规优化历史失败");
    }
  }, [toast]);

  const applyTask = useCallback((task: TranscriptionResponse, closeHistory = true) => {
    const serverSegments = normalizeSegments(task.segments);
    const localDraft = segmentDrafts[task.task_id];
    setSelected(task);
    setSelectedTaskId(task.task_id);
    if (localDraft && localDraft.serverUpdatedAt === task.updated_at) {
      setSegments(localDraft.segments);
    } else if (localDraft) {
      Modal.confirm({
        title: "发现本机校对草稿",
        content: "服务端任务已更新，本机草稿可能基于旧版本。请选择恢复草稿或丢弃草稿。",
        okText: "恢复草稿",
        cancelText: "丢弃草稿",
        onOk: () => setSegments(localDraft.segments),
        onCancel: () => {
          setSegmentDrafts((prev) => {
            const next = { ...prev };
            delete next[task.task_id];
            return next;
          });
          setSegments(serverSegments);
        },
      });
    } else {
      setSegments(serverSegments);
    }
    loadVoiceoverDrafts(task.task_id);
    loadComplianceDrafts(task.task_id);
    if (closeHistory) setHistoryOpen(false);
  }, [loadComplianceDrafts, loadVoiceoverDrafts, segmentDrafts, setSegmentDrafts, setSelectedTaskId]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const items = await listTranscriptions();
      setTasks(items);
      const targetId = taskFromQuery || selectedTaskId;
      if (targetId) {
        const task = items.find((item) => item.task_id === targetId) || await getTranscription(targetId);
        applyTask(task, false);
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [applyTask, selectedTaskId, taskFromQuery, toast]);

  const handleDeleteTranscription = async (task: TranscriptionResponse) => {
    setDeletingTaskId(task.task_id);
    try {
      await deleteTask(task.task_id);
      setTasks((items) => items.filter((item) => item.task_id !== task.task_id));
      setSegmentDrafts((drafts) => {
        const next = { ...drafts };
        delete next[task.task_id];
        return next;
      });
      if (selectedTaskId === task.task_id) {
        setSelected(null);
        setSelectedTaskId(null);
        setSegments([]);
        setVoiceoverDrafts([]);
        setActiveVoiceover(null);
        setComplianceDrafts([]);
        setActiveCompliance(null);
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
      setSegmentDrafts({});
      setVoiceoverDrafts([]);
      setActiveVoiceover(null);
      setComplianceDrafts([]);
      setActiveCompliance(null);
      toast.success(`已删除 ${result.deleted_count} 条转写历史`);
    } catch (error) {
      toast.error((error as Error).message || "清空转写历史失败");
    } finally {
      setDeletingTaskId(null);
    }
  };

  const handleDeleteDraft = async (draft: VoiceoverDraftResponse, kind: "voiceover" | "compliance") => {
    setDeletingTaskId(draft.copywriting_task_id);
    try {
      await deleteTask(draft.copywriting_task_id);
      if (kind === "voiceover") {
        const remaining = voiceoverDrafts.filter((item) => item.copywriting_task_id !== draft.copywriting_task_id);
        setVoiceoverDrafts(remaining);
        if (activeVoiceover?.copywriting_task_id === draft.copywriting_task_id) {
          setActiveVoiceover(remaining[0] || null);
        }
      } else {
        const remaining = complianceDrafts.filter((item) => item.copywriting_task_id !== draft.copywriting_task_id);
        setComplianceDrafts(remaining);
        if (activeCompliance?.copywriting_task_id === draft.copywriting_task_id) {
          setActiveCompliance(remaining[0] || null);
        }
      }
      toast.success(kind === "voiceover" ? "口播稿历史已删除" : "合规优化历史已删除");
    } catch (error) {
      toast.error((error as Error).message || "删除历史失败");
    } finally {
      setDeletingTaskId(null);
    }
  };

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (shareTextFromQuery) setShareText(shareTextFromQuery);
    if (urlFromQuery) setVideoUrl(urlFromQuery);
    if (candidateFromQuery || shareTextFromQuery || urlFromQuery) setCreateOpen(true);
  }, [candidateFromQuery, setVideoUrl, shareTextFromQuery, urlFromQuery]);

  useEffect(() => {
    getCrawlerLinkTranscriptionCapabilities().then(setLinkCapabilities).catch(() => setLinkCapabilities(null));
  }, []);

  const handlePreviewShareLink = async () => {
    if (!shareText.trim()) return toast.warning("请粘贴抖音分享链接");
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
    if (!linkPreview || !linkRightsConfirmed) return toast.warning("请先确认拥有内容处理权");
    setSubmitting(true);
    try {
      const result = fallback
        ? await fallbackCrawlerLinkTranscription({ shareText, workId: linkPreview.work_id || "", rightsHolder, rightsConfirmed: true, idempotencyKey: `link-${Date.now()}` })
        : await createCrawlerLinkTranscription({ shareText, rightsHolder, rightsConfirmed: true, modelName: asrModel });
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
    setVoiceoverDrafts([]);
    setActiveVoiceover(null);
    setComplianceDrafts([]);
    setActiveCompliance(null);
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
      const created = await createTranscriptionByUrl(url, true, asrModel, rightsHolder);
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

  const handleFileUpload = async (file: File) => {
    setSubmitting(true);
    try {
      const created = await uploadAndTranscribe(file, asrModel, rightsHolder);
      toast.success("文件已上传并创建转写任务");
      setCreateOpen(false);
      applyTask(created, false);
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const updateSegment = (index: number, patch: Partial<TranscriptSegment>) => {
    setSegments((prev) => {
      const nextSegments = prev.map((item, i) => (i === index ? { ...item, ...patch } : item));
      if (selected) {
        setSegmentDrafts((current) => ({
          ...current,
          [selected.task_id]: {
            taskId: selected.task_id,
            serverUpdatedAt: selected.updated_at,
            segments: nextSegments,
          },
        }));
      }
      return nextSegments;
    });
  };

  const saveRevision = async (approve: boolean) => {
    if (!selected) return;
    setSaving(true);
    try {
      await saveTranscriptionRevision({
        taskId: selected.task_id,
        segments: segments.map((segment) => ({
          start: segment.start,
          end: segment.end,
          text: segment.text,
          confidence: segment.confidence,
          needs_review: segment.needs_review,
          reviewed: Boolean(segment.reviewed),
        })),
        reviewer,
        approve,
      });
      setSegmentDrafts((prev) => {
        const next = { ...prev };
        delete next[selected.task_id];
        return next;
      });
      toast.success(approve ? "已确认成稿" : "校对版本已保存");
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSaving(false);
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

  const handleCreateVoiceoverDraft = async () => {
    if (!selected) return;
    if (!selected.approved_revision_id) {
      toast.warning("请先复核低置信片段并确认成稿");
      return;
    }
    setDraftLoading(true);
    try {
      const draft = await createVoiceoverDraft({
        taskId: selected.task_id,
        targetSeconds,
        variantCount: 2,
      });
      if (draft.status !== "succeeded") {
        toast.error(draft.error_message || "口播稿生成失败");
        return;
      }
      setActiveVoiceover(draft);
      setVoiceoverDrafts((prev) => [draft, ...prev.filter((item) => item.copywriting_task_id !== draft.copywriting_task_id)]);
      setActiveDraftIndex(0);
      toast.success("已生成去重压缩口播稿，原始转写未改动");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setDraftLoading(false);
    }
  };

  const activeDraftVariants = activeVoiceover?.result_variants.length
    ? activeVoiceover.result_variants
    : activeVoiceover?.result_text
      ? [activeVoiceover.result_text]
      : [];
  const activeDraftText = activeDraftVariants[activeDraftIndex] || "";

  const updateActiveVoiceoverText = (text: string) => {
    if (!activeVoiceover) return;
    const nextVariants = [...activeDraftVariants];
    nextVariants[activeDraftIndex] = text;
    const updated = {
      ...activeVoiceover,
      result_text: activeDraftIndex === 0 ? text : activeVoiceover.result_text,
      result_variants: nextVariants,
    };
    setActiveVoiceover(updated);
    setVoiceoverDrafts((prev) => prev.map((item) => item.copywriting_task_id === updated.copywriting_task_id ? updated : item));
  };

  const saveVoiceoverDraft = async () => {
    if (!selected || !activeVoiceover || !activeDraftText.trim()) return false;
    setSaving(true);
    try {
      const saved = await updateVoiceoverDraft({
        taskId: selected.task_id,
        draftId: activeVoiceover.copywriting_task_id,
        resultText: activeDraftText,
        resultVariants: activeDraftVariants,
      });
      setActiveVoiceover(saved);
      setVoiceoverDrafts((prev) => prev.map((item) => item.copywriting_task_id === saved.copywriting_task_id ? saved : item));
      toast.success("口播稿编辑已保存");
      return true;
    } catch (err) {
      toast.error((err as Error).message || "保存口播稿失败");
      return false;
    } finally {
      setSaving(false);
    }
  };

  const handleUseForAvatar = async () => {
    if (!activeVoiceover || !activeDraftText || activeVoiceover.is_mock) return;
    const saved = await saveVoiceoverDraft();
    if (!saved) return;
    const params = new URLSearchParams({
      script: activeDraftText,
      sourceTask: activeVoiceover.copywriting_task_id,
      sourceRevision: activeVoiceover.source_revision_id,
    });
    navigate(`/avatar?${params.toString()}`);
  };

  const handleCreateComplianceDraft = async () => {
    if (!selected || !activeVoiceover) {
      toast.warning("请先生成并选择一份去重口播稿");
      return;
    }
    const saved = await saveVoiceoverDraft();
    if (!saved) return;
    setDraftLoading(true);
    try {
      const draft = await createComplianceDraft({ taskId: selected.task_id, parentDraftId: activeVoiceover.copywriting_task_id });
      setComplianceDrafts((prev) => [draft, ...prev.filter((item) => item.copywriting_task_id !== draft.copywriting_task_id)]);
      setActiveCompliance(draft);
      setActivePanel("compliance");
      toast.success("已生成待人工复核的合规优化稿");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setDraftLoading(false);
    }
  };

  const complianceText = activeCompliance?.result_text || activeCompliance?.result_variants[0] || "";
  const updateComplianceText = (text: string) => {
    if (!activeCompliance) return;
    const updated = { ...activeCompliance, result_text: text, result_variants: [text, ...activeCompliance.result_variants.slice(1)] };
    setActiveCompliance(updated);
    setComplianceDrafts((prev) => prev.map((item) => item.copywriting_task_id === updated.copywriting_task_id ? updated : item));
  };
  const saveComplianceDraft = async () => {
    if (!selected || !activeCompliance || !complianceText.trim()) return;
    setSaving(true);
    try {
      const saved = await updateVoiceoverDraft({ taskId: selected.task_id, draftId: activeCompliance.copywriting_task_id, resultText: complianceText, resultVariants: [complianceText] });
      setActiveCompliance(saved);
      setComplianceDrafts((prev) => prev.map((item) => item.copywriting_task_id === saved.copywriting_task_id ? saved : item));
      toast.success("合规优化稿编辑已保存，仍需人工终审");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSaving(false);
    }
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
      render: (value: string, _record, index) => (
        <TextArea value={value} autoSize onChange={(event) => updateSegment(index, { text: event.target.value })} />
      ),
    },
    {
      title: "置信度",
      dataIndex: "confidence",
      width: 110,
      render: (value: number | null) => value === null ? "人工导入" : `${Math.round(value * 100)}%`,
    },
    {
      title: "复核",
      width: 100,
      render: (_, record, index) => (
        <Checkbox
          checked={!record.needs_review || Boolean(record.reviewed)}
          disabled={!record.needs_review}
          onChange={(event) => updateSegment(index, { reviewed: event.target.checked })}
        >
          已复核
        </Checkbox>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Row justify="space-between" align="middle" gutter={[16, 12]}>
        <Col>
          <Title level={4} style={{ margin: 0 }}>语音转写</Title>
          <Text type="secondary">SQLite 保存转写历史；浏览器只保留当前选择和未提交校对草稿。</Text>
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
              <Text strong>{selected.media_name}</Text>
              <Text code>{selected.task_id}</Text>
              <Tag>{selected.model_name || "未知模型"}</Tag>
              {selected.source_kind === "manual_text" && <Tag color="blue">人工回填 · 无时间轴</Tag>}
              {selected.duration_seconds && <Text type="secondary">{Math.round(selected.duration_seconds)} 秒</Text>}
              {selected.low_confidence_count > 0 && <Tag color="warning">待复核 {selected.low_confidence_count} 段</Tag>}
              <Text type="secondary">{selected.stage}</Text>
            </Space>
            {selected.error_message && <Alert type="error" showIcon message={selected.error_message} />}
            <Tabs
              activeKey={activePanel}
              onChange={(key) => setActivePanel(key as "review" | "voiceover" | "compliance")}
              items={[
                {
                  key: "review",
                  label: "校对成稿",
                  children: segments.length > 0 ? (
                    <Space direction="vertical" style={{ width: "100%" }} size={16}>
                      <Space wrap>
                        <Input value={reviewer} onChange={(event) => setReviewer(event.target.value)} addonBefore="校对人" style={{ width: 240 }} />
                        <Button icon={<SaveOutlined />} loading={saving} onClick={() => saveRevision(false)}>保存校对版本</Button>
                        <Button type="primary" loading={saving} onClick={() => saveRevision(true)}>确认成稿</Button>
                      </Space>
                      {!selected.timing_available && <Alert type="info" showIcon message="人工回填文本没有时间轴，可导出 TXT/JSON；如需字幕请上传授权视频重新转写。" />}
                      <Table rowKey={(_, index) => String(index)} columns={segmentColumns} dataSource={segments} pagination={false} size="small" scroll={{ x: 720 }} />
                      <Collapse
                        size="small"
                        items={[{
                          key: "plain",
                          label: "纯文本预览",
                          children: <Paragraph style={{ whiteSpace: "pre-wrap", margin: 0 }}>{segments.map((segment) => segment.text).join("\n")}</Paragraph>,
                        }]}
                      />
                    </Space>
                  ) : <Empty description="该任务暂无可校对片段" />,
                },
                {
                  key: "voiceover",
                  label: "数字人口播稿",
                  children: (
                    <Space direction="vertical" style={{ width: "100%" }} size={16}>
                      <Alert
                        type={selected.approved_revision_id ? "info" : "warning"}
                        showIcon
                        message={selected.approved_revision_id ? "基于已确认成稿生成，不覆盖原始转写" : "先完成校对并确认成稿"}
                      />
                      <Space wrap>
                        <Text>目标时长</Text>
                        <InputNumber min={15} max={60} value={targetSeconds} onChange={(value) => setTargetSeconds(Number(value || 45))} addonAfter="秒" />
                        <Button type="primary" loading={draftLoading} disabled={!selected.approved_revision_id} onClick={handleCreateVoiceoverDraft}>
                          生成去重压缩稿
                        </Button>
                      </Space>
                      {voiceoverDrafts.length > 0 && (
                        <Space wrap>
                          {voiceoverDrafts.map((draft, index) => (
                            <Space key={draft.copywriting_task_id} size={0}>
                              <Button
                                size="small"
                                type={activeVoiceover?.copywriting_task_id === draft.copywriting_task_id ? "primary" : "default"}
                                onClick={() => {
                                  setActiveVoiceover(draft);
                                  setActiveDraftIndex(0);
                                }}
                              >
                                历史 {index + 1}
                              </Button>
                              <Popconfirm
                                title="删除这份口播稿历史？"
                                description="只删除该历史版本，不会影响原始转写。"
                                okText="删除"
                                okButtonProps={{ danger: true }}
                                cancelText="取消"
                                onConfirm={() => handleDeleteDraft(draft, "voiceover")}
                              >
                                <Button type="text" danger size="small" icon={<DeleteOutlined />} loading={deletingTaskId === draft.copywriting_task_id} />
                              </Popconfirm>
                            </Space>
                          ))}
                        </Space>
                      )}
                      {activeDraftVariants.length > 0 ? (
                        <Space direction="vertical" style={{ width: "100%" }}>
                          <Space wrap>
                            {activeDraftVariants.map((_, index) => (
                              <Button key={index} size="small" type={activeDraftIndex === index ? "primary" : "default"} onClick={() => setActiveDraftIndex(index)}>
                                版本 {index + 1}
                              </Button>
                            ))}
                            <Tag>{activeDraftText.length} 字</Tag>
                            <Tag>目标 {activeVoiceover?.target_seconds ? `${activeVoiceover.target_seconds} 秒` : "未知"}</Tag>
                            <Tag>{activeVoiceover?.model_name || "未知模型"}</Tag>
                            {activeVoiceover?.is_mock && <Tag color="warning">Sandbox 演示文案</Tag>}
                          </Space>
                          <TextArea value={activeDraftText} autoSize={{ minRows: 6 }} onChange={(event) => updateActiveVoiceoverText(event.target.value)} />
                          <Space wrap>
                            <Button icon={<SaveOutlined />} loading={saving} onClick={saveVoiceoverDraft}>保存编辑</Button>
                            <Button loading={draftLoading} disabled={!activeVoiceover || activeVoiceover.is_mock} onClick={handleCreateComplianceDraft}>生成合规优化稿</Button>
                            <Button icon={<CopyOutlined />} onClick={() => navigator.clipboard.writeText(activeDraftText).then(() => toast.success("已复制"))}>复制口播稿</Button>
                            <Button type="primary" loading={saving} disabled={activeVoiceover?.is_mock} onClick={handleUseForAvatar}>保存并带到数字人</Button>
                          </Space>
                        </Space>
                      ) : (
                        <Empty description="当前任务暂无口播稿历史" />
                      )}
                    </Space>
                  ),
                },
                {
                  key: "compliance",
                  label: "合规优化",
                  children: (
                    <Space direction="vertical" style={{ width: "100%" }} size={16}>
                      <Alert type="warning" showIcon message="AI 仅做表达风险提示与优化，不能替代法务、平台规则或人工终审。" description="不会补写事实、资质、数据或效果承诺；请在发布前逐项确认版权、广告、医疗金融等行业要求。" />
                      {complianceDrafts.length > 0 && (
                        <Space wrap>
                          {complianceDrafts.map((draft, index) => (
                            <Space key={draft.copywriting_task_id} size={0}>
                              <Button size="small" type={activeCompliance?.copywriting_task_id === draft.copywriting_task_id ? "primary" : "default"} onClick={() => setActiveCompliance(draft)}>
                                历史 {index + 1}
                              </Button>
                              <Popconfirm
                                title="删除这份合规优化历史？"
                                description="只删除该历史版本，不会影响原始转写。"
                                okText="删除"
                                okButtonProps={{ danger: true }}
                                cancelText="取消"
                                onConfirm={() => handleDeleteDraft(draft, "compliance")}
                              >
                                <Button type="text" danger size="small" icon={<DeleteOutlined />} loading={deletingTaskId === draft.copywriting_task_id} />
                              </Popconfirm>
                            </Space>
                          ))}
                        </Space>
                      )}
                      {activeCompliance ? <Space direction="vertical" style={{ width: "100%" }}><Tag color="warning">待人工复核</Tag><TextArea value={complianceText} autoSize={{ minRows: 7 }} onChange={(event) => updateComplianceText(event.target.value)} /><Space><Button icon={<SaveOutlined />} loading={saving} onClick={saveComplianceDraft}>保存编辑</Button><Button icon={<CopyOutlined />} onClick={() => navigator.clipboard.writeText(complianceText).then(() => toast.success("已复制"))}>复制合规稿</Button></Space></Space> : <Empty description="先在“数字人口播稿”生成并保存去重稿，再生成合规优化稿" />}
                    </Space>
                  ),
                },
              ]}
            />
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
            description="不会自动下载平台分享页或自动操作第三方工具。可上传有权处理的文件，或填写授权直链。"
          />
          <Space wrap>
            <Select
              value={asrModel}
              onChange={setAsrModel}
              style={{ width: 230 }}
              options={[
                { value: "large-v3-turbo", label: "准确率优先 · large-v3-turbo" },
                { value: "base", label: "快速预览 · base" },
              ]}
            />
            <Input value={rightsHolder} onChange={(event) => setRightsHolder(event.target.value)} addonBefore="权利主体" style={{ width: 300 }} />
          </Space>
          <Tabs
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
                label: <span><LinkOutlined /> 抖音分享链接</span>,
                children: (
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <Alert
                      type={linkCapabilities?.parser_enabled ? "info" : "warning"}
                      showIcon
                      message={linkCapabilities?.parser_enabled ? "本机解析已安装，待实际链接验证" : "本机解析器未就绪"}
                      description="仅处理单条、已获授权的抖音分享链接。解析失败会保留具体错误；只有你明确确认后才可使用 OneAPI 付费回退。"
                    />
                    <TextArea value={shareText} onChange={(event) => { setShareText(event.target.value); setLinkPreview(null); }} placeholder="粘贴抖音分享文案或 v.douyin.com 分享链接" rows={3} />
                    <Checkbox checked={linkRightsConfirmed} onChange={(event) => setLinkRightsConfirmed(event.target.checked)}>我确认有权处理此内容</Checkbox>
                    <Button loading={submitting} onClick={handlePreviewShareLink}>识别链接</Button>
                    {linkPreview && (
                      <Alert
                        type={linkPreview.parser_enabled ? "info" : "warning"}
                        showIcon
                        message={linkPreview.parser_enabled ? `已识别作品 ID：${linkPreview.work_id || "未返回"}` : "本机解析不可用"}
                        description={
                          <Space wrap>
                            <Text>{linkPreview.parser_message || "可开始本机解析并转写。"}</Text>
                            <Button type="primary" loading={submitting} disabled={!linkPreview.parser_enabled || !linkRightsConfirmed} onClick={() => handleShareLinkTranscribe(false)}>本机解析并转写</Button>
                            {linkPreview.oneapi_fallback_available && <Button danger loading={submitting} disabled={!linkRightsConfirmed} onClick={() => Modal.confirm({ title: "确认 OneAPI 付费回退", content: `预计 ¥${(linkPreview.oneapi_estimated_cost_cny || 0).toFixed(2)}，确认后才会调用。`, okText: "确认并继续", onOk: () => handleShareLinkTranscribe(true) })}>确认后付费回退</Button>}
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
                  <Upload.Dragger
                    accept=".mp4,.mov"
                    beforeUpload={(file) => {
                      handleFileUpload(file);
                      return false;
                    }}
                    multiple={false}
                    showUploadList={false}
                    disabled={submitting}
                  >
                    <p><UploadOutlined style={{ fontSize: 28 }} /></p>
                    <p>点击或拖拽 MP4/MOV 文件上传</p>
                  </Upload.Dragger>
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
          <Input value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="搜索媒体或任务 ID" />
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
                  title={<Space wrap><Text strong>{item.media_name}</Text><Tag color={STATUS_COLOR[item.status]}>{statusLabel(item.status)}</Tag></Space>}
                  description={
                    <Space direction="vertical" size={4}>
                      <Text code>{item.task_id}</Text>
                      <Text type="secondary">{formatTime(item.created_at)} · {item.progress}%</Text>
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
