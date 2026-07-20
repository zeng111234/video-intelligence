import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  Progress,
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
  DownloadOutlined,
  FileTextOutlined,
  LinkOutlined,
  ReloadOutlined,
  SaveOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import {
  createTranscriptionByUrl,
  exportTranscription,
  listTranscriptions,
  saveTranscriptionRevision,
  uploadAndTranscribe,
} from "../api/client";
import type { TranscriptSegment, TranscriptionResponse } from "../api/types";
import { useToast } from "../components/Toast";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

const STATUS_COLOR: Record<string, string> = {
  queued: "default",
  pending: "default",
  running: "processing",
  succeeded: "success",
  failed: "error",
};

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
  const [tasks, setTasks] = useState<TranscriptionResponse[]>([]);
  const [selected, setSelected] = useState<TranscriptionResponse | null>(null);
  const [segments, setSegments] = useState<TranscriptSegment[]>([]);
  const [videoUrl, setVideoUrl] = useState("");
  const [reviewer, setReviewer] = useState("校对员");
  const [filterStatus, setFilterStatus] = useState("all");
  const [searchText, setSearchText] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const items = await listTranscriptions();
      setTasks(items);
      if (selected) {
        const next = items.find((item) => item.task_id === selected.task_id) || null;
        setSelected(next);
        setSegments(next?.segments || []);
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [selected?.task_id, toast]);

  useEffect(() => {
    refresh();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const filteredTasks = useMemo(() => {
    const normalized = searchText.trim().toLowerCase();
    return tasks.filter((task) => {
      const statusOk = filterStatus === "all" || task.status === filterStatus;
      const searchOk =
        !normalized ||
        task.media_name.toLowerCase().includes(normalized) ||
        task.title.toLowerCase().includes(normalized);
      return statusOk && searchOk;
    });
  }, [tasks, filterStatus, searchText]);

  const selectTask = (task: TranscriptionResponse) => {
    setSelected(task);
    setSegments(task.segments.map((segment) => ({ ...segment, reviewed: segment.reviewed || false })));
  };

  const handleUrlTranscribe = async () => {
    const url = videoUrl.trim();
    if (!url) {
      toast.warning("请输入授权 MP4/MOV 直链");
      return;
    }
    setSubmitting(true);
    try {
      const created = await createTranscriptionByUrl(url, true);
      toast.success("转写任务已创建");
      setVideoUrl("");
      setSelected(created);
      setSegments(created.segments);
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
      const created = await uploadAndTranscribe(file);
      toast.success("文件已上传并创建转写任务");
      setSelected(created);
      setSegments(created.segments);
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const updateSegment = (index: number, patch: Partial<TranscriptSegment>) => {
    setSegments((prev) => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)));
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
      toast.success(approve ? "已确认成稿" : "校对版本已保存");
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const handleExport = async (format: "txt" | "json" | "srt") => {
    if (!selected) return;
    try {
      const blob = await exportTranscription(selected.task_id, format);
      downloadBlob(blob, `${selected.task_id}.${format}`);
      toast.success(`已导出 ${format.toUpperCase()}`);
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const columns: ColumnsType<TranscriptionResponse> = [
    { title: "任务ID", dataIndex: "task_id", width: 170, render: (value) => <Text code>{value}</Text> },
    { title: "媒体", dataIndex: "media_name", ellipsis: true },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (value: string) => <Tag color={STATUS_COLOR[value]}>{statusLabel(value)}</Tag>,
    },
    {
      title: "进度",
      dataIndex: "progress",
      width: 130,
      render: (value: number, record) => (
        <Progress
          percent={value}
          size="small"
          status={record.status === "failed" ? "exception" : record.status === "succeeded" ? "success" : "active"}
        />
      ),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 180,
      render: (value: string | null) => (value ? new Date(value).toLocaleString("zh-CN") : "-"),
    },
    {
      title: "操作",
      width: 90,
      render: (_, record) => (
        <Button type="link" onClick={() => selectTask(record)}>
          查看
        </Button>
      ),
    },
  ];

  const segmentColumns: ColumnsType<TranscriptSegment> = [
    {
      title: "时间",
      width: 150,
      render: (_, record) => <Text code>{record.start.toFixed(1)}s - {record.end.toFixed(1)}s</Text>,
    },
    {
      title: "文本",
      dataIndex: "text",
      render: (value: string, _record, index) => (
        <TextArea
          value={value}
          autoSize
          onChange={(event) => updateSegment(index, { text: event.target.value })}
        />
      ),
    },
    {
      title: "置信度",
      dataIndex: "confidence",
      width: 110,
      render: (value: number) => `${Math.round(value * 100)}%`,
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
      <div>
        <Title level={4} style={{ margin: 0 }}>语音转写</Title>
        <Text type="secondary">转写历史从后端 SQLite 加载；真实转写必须确认成稿后才能导出。</Text>
      </div>

      <Alert
        type="warning"
        showIcon
        message="权利确认边界"
        description="候选或爬虫结果跳转到此页后，不会自动下载平台分享页。请上传你有权处理的文件，或填写已授权的 MP4/MOV 直链。"
      />

      <Card title="创建转写任务">
        <Tabs
          items={[
            {
              key: "url",
              label: <span><LinkOutlined /> 授权直链</span>,
              children: (
                <Space direction="vertical" style={{ width: "100%" }}>
                  <TextArea
                    value={videoUrl}
                    onChange={(event) => setVideoUrl(event.target.value)}
                    placeholder="填写已授权的 MP4/MOV 直链；不支持平台分享页自动下载"
                    rows={3}
                  />
                  <Button type="primary" loading={submitting} onClick={handleUrlTranscribe}>
                    确认权利并创建转写
                  </Button>
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
      </Card>

      <Card
        title={<Space><FileTextOutlined /> 校对与导出</Space>}
        extra={selected && (
          <Space>
            <Select
              value="txt"
              style={{ width: 90 }}
              options={[
                { value: "txt", label: "TXT" },
                { value: "json", label: "JSON" },
                { value: "srt", label: "SRT" },
              ]}
              onSelect={(value) => handleExport(value as "txt" | "json" | "srt")}
            />
            <Button icon={<DownloadOutlined />} onClick={() => handleExport("txt")}>导出</Button>
          </Space>
        )}
      >
        {selected ? (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Space wrap>
              <Tag color={STATUS_COLOR[selected.status]}>{statusLabel(selected.status)}</Tag>
              <Text strong>{selected.media_name}</Text>
              <Text type="secondary">{selected.stage}</Text>
            </Space>
            {selected.error_message && <Alert type="error" showIcon message={selected.error_message} />}
            {segments.length > 0 ? (
              <>
                <Space>
                  <Input value={reviewer} onChange={(event) => setReviewer(event.target.value)} addonBefore="校对人" />
                  <Button icon={<SaveOutlined />} loading={saving} onClick={() => saveRevision(false)}>
                    保存校对版本
                  </Button>
                  <Button type="primary" loading={saving} onClick={() => saveRevision(true)}>
                    确认成稿
                  </Button>
                </Space>
                <Table
                  rowKey={(_, index) => String(index)}
                  columns={segmentColumns}
                  dataSource={segments}
                  pagination={false}
                  size="small"
                />
              </>
            ) : (
              <Empty description="该任务暂无可校对片段" />
            )}
          </Space>
        ) : (
          <Empty description="选择一个转写任务查看和校对" />
        )}
      </Card>

      <Card
        title="转写历史"
        extra={
          <Space>
            <Input
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              placeholder="搜索媒体"
              style={{ width: 200 }}
            />
            <Select
              value={filterStatus}
              onChange={setFilterStatus}
              style={{ width: 130 }}
              options={[
                { value: "all", label: "全部" },
                { value: "running", label: "转写中" },
                { value: "succeeded", label: "已完成" },
                { value: "failed", label: "失败" },
              ]}
            />
            <Button icon={<ReloadOutlined />} loading={loading} onClick={refresh}>刷新</Button>
          </Space>
        }
      >
        <Table
          rowKey="task_id"
          columns={columns}
          dataSource={filteredTasks}
          loading={loading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
        />
      </Card>

      {selected?.segments?.length ? (
        <Card title="纯文本预览">
          <Paragraph style={{ whiteSpace: "pre-wrap" }}>
            {segments.map((segment) => segment.text).join("\n")}
          </Paragraph>
        </Card>
      ) : null}
    </Space>
  );
}
