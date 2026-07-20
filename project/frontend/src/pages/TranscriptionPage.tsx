/**
 * 语音转写页面
 * 支持视频链接和文件上传两种方式，AI 自动转写为文字
 */
import { useState, useMemo, useCallback } from "react";
import {
  Typography,
  Card,
  Input,
  Button,
  Space,
  Table,
  Tag,
  Row,
  Col,
  Statistic,
  Select,
  Progress,
  Empty,
  Upload,
  Tabs,
} from "antd";
import { useToast } from "../components/Toast";
import {
  AudioOutlined,
  PlayCircleOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ReloadOutlined,
  UploadOutlined,
  FileTextOutlined,
  SoundOutlined,
  DownloadOutlined,
  CopyOutlined,
  SearchOutlined,
  LinkOutlined,
} from "@ant-design/icons";
import { createTranscriptionByUrl, uploadAndTranscribe } from "../api/client";

const { Title, Text, Paragraph } = Typography;
const { Option } = Select;
const { TextArea } = Input;

/** 转写状态颜色 */
const STATUS_COLOR: Record<string, string> = {
  succeeded: "success",
  running: "processing",
  failed: "error",
  pending: "default",
};

/** 模拟转写历史数据 */
const TRANSCRIPTION_HISTORY = [
  {
    id: "TR-20260720-001",
    fileName: "https://www.douyin.com/video/7663788033606503706",
    source: "url",
    platform: "抖音",
    status: "succeeded",
    duration: "5分32秒",
    wordCount: 1256,
    confidence: 92,
    startTime: "2026-07-20 15:30:00",
    segments: [
      { start: 0, end: 15.5, text: "大家好，欢迎来到今天的二手车测评节目", confidence: 0.95 },
      { start: 15.5, end: 32.8, text: "今天给大家带来的是一款非常热门的车型", confidence: 0.93 },
      { start: 32.8, end: 48.2, text: "这款车就是丰田凯美瑞，省油耐用是它的代名词", confidence: 0.91 },
      { start: 48.2, end: 65.0, text: "我们先来看看外观，这款车的前脸设计非常大气", confidence: 0.89 },
      { start: 65.0, end: 82.5, text: "车身线条流畅，整体造型时尚动感", confidence: 0.94 },
    ],
  },
  {
    id: "TR-20260720-002",
    fileName: "https://www.bilibili.com/video/BV1xx411c7mD",
    source: "url",
    platform: "B站",
    status: "succeeded",
    duration: "8分15秒",
    wordCount: 1842,
    confidence: 88,
    startTime: "2026-07-20 14:20:00",
    segments: [
      { start: 0, end: 18.3, text: "今天我们来对比两款豪华品牌的中型轿车", confidence: 0.92 },
      { start: 18.3, end: 35.6, text: "宝马3系和奔驰C级，看看谁更值得购买", confidence: 0.90 },
    ],
  },
  {
    id: "TR-20260719-003",
    fileName: "汽车保养技巧_机油选择.mp3",
    status: "running",
    duration: "3分45秒",
    wordCount: 0,
    confidence: 0,
    startTime: "2026-07-19 16:10:00",
    segments: [],
  },
  {
    id: "TR-20260719-004",
    fileName: "新能源汽车_续航测试.mp4",
    status: "failed",
    duration: "12分20秒",
    wordCount: 0,
    confidence: 0,
    startTime: "2026-07-19 11:05:00",
    segments: [],
  },
];

/** 模拟统计数据 */
const STATS = {
  totalFiles: 89,
  totalWords: 45680,
  avgConfidence: 91.5,
  avgDuration: "6分15秒",
};

export default function TranscriptionPage() {
  const toast = useToast();
  const [searchText, setSearchText] = useState("");
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [selectedTask, setSelectedTask] = useState<any>(null);
  const [videoUrl, setVideoUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [tasks, setTasks] = useState(TRANSCRIPTION_HISTORY);

  /** 通过链接转写 */
  const handleUrlTranscribe = useCallback(async () => {
    const urls = videoUrl.trim().split("\n").filter((u) => u.trim());
    if (urls.length === 0) {
      toast.warning("请输入视频链接");
      return;
    }
    setLoading(true);
    try {
      const results = await Promise.all(
        urls.map((url) => createTranscriptionByUrl(url.trim()))
      );
      const newTasks = results.map((r, i) => ({
        id: r.task_id,
        fileName: urls[i].trim(),
        source: "url",
        platform: urls[i].includes("douyin") ? "抖音" : urls[i].includes("bilibili") ? "B站" : urls[i].includes("youtube") ? "YouTube" : "链接",
        status: r.status === "succeeded" ? "succeeded" : r.status === "running" ? "running" : "pending",
        duration: r.segments?.length ? `${Math.round(r.segments[r.segments.length - 1].end / 60)}分${Math.round(r.segments[r.segments.length - 1].end % 60)}秒` : "-",
        wordCount: r.segments?.reduce((acc, s) => acc + s.text.length, 0) || 0,
        confidence: r.segments?.length ? Math.round(r.segments.reduce((acc, s) => acc + s.confidence, 0) / r.segments.length * 100) : 0,
        startTime: new Date().toLocaleString("zh-CN"),
        segments: r.segments || [],
      }));
      setTasks((prev) => [...newTasks, ...prev]);
      setVideoUrl("");
      // 自动选中第一个任务显示结果
      if (newTasks.length > 0) {
        setSelectedTask(newTasks[0]);
      }
      toast.success(`已创建 ${urls.length} 个转写任务`);
    } catch (err) {
      toast.error((err as Error).message || "转写失败");
    } finally {
      setLoading(false);
    }
  }, [videoUrl]);

  /** 通过文件上传转写 */
  const handleFileUpload = useCallback(async (file: File) => {
    setLoading(true);
    try {
      const result = await uploadAndTranscribe(file);
      const newTask = {
        id: result.task_id,
        fileName: file.name,
        source: "file" as const,
        platform: "本地文件",
        status: result.status === "succeeded" ? "succeeded" : result.status === "running" ? "running" : "pending",
        duration: result.segments?.length ? `${Math.round(result.segments[result.segments.length - 1].end / 60)}分${Math.round(result.segments[result.segments.length - 1].end % 60)}秒` : "-",
        wordCount: result.segments?.reduce((acc, s) => acc + s.text.length, 0) || 0,
        confidence: result.segments?.length ? Math.round(result.segments.reduce((acc, s) => acc + s.confidence, 0) / result.segments.length * 100) : 0,
        startTime: new Date().toLocaleString("zh-CN"),
        segments: result.segments || [],
      };
      setTasks((prev) => [newTask, ...prev]);
      setSelectedTask(newTask);
      toast.success("转写完成");
    } catch (err) {
      toast.error((err as Error).message || "转写失败");
    } finally {
      setLoading(false);
    }
  }, []);

  /** 过滤后的转写任务 */
  const filteredTasks = useMemo(() => {
    let filtered = tasks;
    if (filterStatus !== "all") {
      filtered = filtered.filter((t) => t.status === filterStatus);
    }
    if (searchText) {
      filtered = filtered.filter((t) =>
        t.fileName.toLowerCase().includes(searchText.toLowerCase())
      );
    }
    return filtered;
  }, [filterStatus, searchText, tasks]);

  /** 复制文本 */
  const handleCopyText = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success("已复制到剪贴板");
  };

  /** 导出转写结果 */
  const handleExport = useCallback((task: any, format: string = "txt") => {
    if (!task || !task.segments || task.segments.length === 0) {
      toast.warning("没有可导出的内容");
      return;
    }
    let content = "";
    const filename = `${task.fileName || "转写结果"}.${format}`;
    if (format === "txt") {
      content = task.segments.map((s: any) => s.text).join("\n");
    } else if (format === "srt") {
      content = task.segments.map((s: any, i: number) => {
        const start = new Date(s.start * 1000).toISOString().substr(11, 12).replace(".", ",");
        const end = new Date(s.end * 1000).toISOString().substr(11, 12).replace(".", ",");
        return `${i + 1}\n${start} --> ${end}\n${s.text}\n`;
      }).join("\n");
    } else if (format === "json") {
      content = JSON.stringify(task.segments, null, 2);
    }
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(`已导出 ${filename}`);
  }, [toast]);

  /** 刷新转写列表 */
  const handleRefresh = useCallback(() => {
    toast.success("转写列表已刷新");
  }, [toast]);

  /** 表格列定义 */
  const columns = [
    {
      title: "来源",
      dataIndex: "source",
      width: 80,
      render: (source: string, record: any) => (
        <Tag color={source === "url" ? "blue" : "default"} icon={source === "url" ? <LinkOutlined /> : <FileTextOutlined />}>
          {source === "url" ? record.platform || "链接" : "文件"}
        </Tag>
      ),
    },
    {
      title: "文件名/链接",
      dataIndex: "fileName",
      render: (name: string, record: any) => (
        <Space>
          {record.source === "url" ? <LinkOutlined style={{ color: "#6366f1" }} /> : <SoundOutlined style={{ color: "#6366f1" }} />}
          <Text ellipsis style={{ maxWidth: 250 }}>{name}</Text>
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (status: string) => (
        <Tag color={STATUS_COLOR[status]}>
          {status === "succeeded" ? "已完成" : status === "running" ? "转写中" : status === "failed" ? "失败" : "等待中"}
        </Tag>
      ),
    },
    {
      title: "时长",
      dataIndex: "duration",
      width: 100,
    },
    {
      title: "字数",
      dataIndex: "wordCount",
      width: 100,
      render: (count: number) => count > 0 ? <Text strong>{count.toLocaleString()}</Text> : "-",
    },
    {
      title: "置信度",
      dataIndex: "confidence",
      width: 120,
      render: (confidence: number, record: any) =>
        record.status === "succeeded" ? (
          <Progress
            percent={confidence}
            size="small"
            status={confidence >= 85 ? "success" : "normal"}
          />
        ) : (
          "-"
        ),
    },
    {
      title: "开始时间",
      dataIndex: "startTime",
      width: 160,
    },
    {
      title: "操作",
      width: 120,
      render: (_: any, record: any) => (
        <Space>
          <Button
            type="link"
            size="small"
            onClick={() => setSelectedTask(record)}
            disabled={record.status !== "succeeded"}
          >
            查看
          </Button>
          <Button
            type="link"
            size="small"
            icon={<DownloadOutlined />}
            disabled={record.status !== "succeeded"}
            onClick={() => handleExport(record)}
          >
            导出
          </Button>
        </Space>
      ),
    },
  ];

  /** 片段表格列 */
  const segmentColumns = [
    {
      title: "时间",
      width: 150,
      render: (_: any, record: any) => (
        <Text code>{record.start.toFixed(1)}s - {record.end.toFixed(1)}s</Text>
      ),
    },
    {
      title: "文本内容",
      dataIndex: "text",
      render: (text: string) => <Paragraph style={{ margin: 0 }}>{text}</Paragraph>,
    },
    {
      title: "置信度",
      dataIndex: "confidence",
      width: 120,
      render: (confidence: number) => (
        <Progress
          percent={Math.round(confidence * 100)}
          size="small"
          status={confidence >= 0.85 ? "success" : confidence >= 0.7 ? "normal" : "exception"}
        />
      ),
    },
  ];

  return (
    <div>
      {/* 页面头部 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          <AudioOutlined /> 语音转写
        </Title>
        <Text type="secondary">AI 驱动的语音识别，自动将视频/音频转为文字</Text>
      </div>

      {/* 统计卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="已处理文件"
              value={STATS.totalFiles}
              prefix={<FileTextOutlined style={{ color: "#6366f1" }} />}
              valueStyle={{ color: "#6366f1" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="总字数"
              value={STATS.totalWords}
              prefix={<SoundOutlined style={{ color: "#10b981" }} />}
              valueStyle={{ color: "#10b981" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="平均置信度"
              value={STATS.avgConfidence}
              suffix="%"
              prefix={<CheckCircleOutlined style={{ color: "#f59e0b" }} />}
              valueStyle={{ color: "#f59e0b" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="平均时长"
              value={STATS.avgDuration}
              prefix={<ClockCircleOutlined style={{ color: "#8b5cf6" }} />}
              valueStyle={{ color: "#8b5cf6" }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        {/* 输入区域 */}
        <Col xs={24} lg={8}>
          <Card title={<Space><SoundOutlined /> 创建转写任务</Space>}>
            <Tabs
              defaultActiveKey="url"
              items={[
                {
                  key: "url",
                  label: (
                    <span>
                      <LinkOutlined /> 链接转写
                    </span>
                  ),
                  children: (
                    <Space direction="vertical" style={{ width: "100%" }} size={16}>
                      <div>
                        <Text strong style={{ display: "block", marginBottom: 8 }}>视频链接</Text>
                        <TextArea
                          placeholder={"粘贴视频链接，支持：\n• 抖音/快手/B站等短视频链接\n• YouTube/TikTok 链接\n• 直链（MP4/MP3/WAV）"}
                          rows={4}
                          style={{ resize: "none" }}
                          value={videoUrl}
                          onChange={(e) => setVideoUrl(e.target.value)}
                        />
                        <Text type="secondary" style={{ fontSize: 12, marginTop: 4, display: "block" }}>
                          支持批量粘贴，每行一个链接
                        </Text>
                      </div>
                      <Button
                        type="primary"
                        icon={<PlayCircleOutlined />}
                        block
                        size="large"
                        loading={loading}
                        onClick={handleUrlTranscribe}
                      >
                        开始转写
                      </Button>
                    </Space>
                  ),
                },
                {
                  key: "file",
                  label: (
                    <span>
                      <UploadOutlined /> 文件上传
                    </span>
                  ),
                  children: (
                    <Space direction="vertical" style={{ width: "100%" }} size={16}>
                      <Upload.Dragger
                        name="file"
                        multiple={false}
                        accept=".mp4,.mp3,.wav,.m4a,.avi"
                        showUploadList={true}
                        beforeUpload={(file) => {
                          handleFileUpload(file);
                          return false; // 阻止自动上传
                        }}
                        style={{ padding: "20px 0" }}
                      >
                        <p style={{ marginBottom: 8 }}>
                          <UploadOutlined style={{ fontSize: 32, color: "#6366f1" }} />
                        </p>
                        <p style={{ marginBottom: 4 }}>点击或拖拽文件到此区域上传</p>
                        <p style={{ color: "#94a3b8", fontSize: 12 }}>
                          支持 MP4、MP3、WAV、M4A、AVI 格式
                        </p>
                      </Upload.Dragger>
                    </Space>
                  ),
                },
              ]}
            />

            {/* 转写设置 */}
            <div style={{ marginTop: 16, paddingTop: 16, borderTop: "1px solid #f0f0f0" }}>
              <Text strong style={{ display: "block", marginBottom: 8 }}>转写设置</Text>
              <Space direction="vertical" style={{ width: "100%" }}>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>识别语言</Text>
                  <Select defaultValue="zh" style={{ width: "100%", marginTop: 4 }}>
                    <Option value="zh">中文</Option>
                    <Option value="en">英文</Option>
                    <Option value="auto">自动检测</Option>
                  </Select>
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>输出格式</Text>
                  <Select defaultValue="txt" style={{ width: "100%", marginTop: 4 }}>
                    <Option value="txt">纯文本 (TXT)</Option>
                    <Option value="srt">字幕文件 (SRT)</Option>
                    <Option value="json">结构化 (JSON)</Option>
                  </Select>
                </div>
              </Space>
            </div>
          </Card>
        </Col>

        {/* 转写结果 */}
        <Col xs={24} lg={16}>
          <Card
            title={<Space><FileTextOutlined /> 转写结果</Space>}
            extra={
              selectedTask && (
                <Space>
                  <Button
                    icon={<CopyOutlined />}
                    size="small"
                    onClick={() =>
                      handleCopyText(
                        selectedTask.segments.map((s: any) => s.text).join("\n")
                      )
                    }
                  >
                    复制全文
                  </Button>
                  <Button icon={<DownloadOutlined />} size="small" onClick={() => handleExport(selectedTask)}>
                    导出
                  </Button>
                </Space>
              )
            }
          >
            {selectedTask && selectedTask.status === "succeeded" ? (
              <Space direction="vertical" style={{ width: "100%" }} size={16}>
                {/* 文件信息 */}
                <Row gutter={16}>
                  <Col span={6}>
                    <Statistic title="文件名" value={selectedTask.fileName} valueStyle={{ fontSize: 14 }} />
                  </Col>
                  <Col span={6}>
                    <Statistic title="时长" value={selectedTask.duration} valueStyle={{ fontSize: 14 }} />
                  </Col>
                  <Col span={6}>
                    <Statistic title="字数" value={selectedTask.wordCount} valueStyle={{ fontSize: 14 }} />
                  </Col>
                  <Col span={6}>
                    <Statistic
                      title="置信度"
                      value={selectedTask.confidence}
                      suffix="%"
                      valueStyle={{ fontSize: 14, color: selectedTask.confidence >= 85 ? "#10b981" : "#f59e0b" }}
                    />
                  </Col>
                </Row>

                {/* 片段列表 */}
                <Table
                  columns={segmentColumns}
                  dataSource={selectedTask.segments}
                  rowKey={(_, i) => String(i)}
                  pagination={false}
                  size="small"
                />
              </Space>
            ) : (
              <Empty description="选择一个已完成的转写任务查看结果" />
            )}
          </Card>
        </Col>
      </Row>

      {/* 转写历史 */}
      <Card
        title={<Space><ClockCircleOutlined /> 转写历史</Space>}
        extra={
          <Space>
            <Input
              placeholder="搜索文件名..."
              prefix={<SearchOutlined />}
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              style={{ width: 200 }}
              size="small"
            />
            <Select
              value={filterStatus}
              onChange={setFilterStatus}
              style={{ width: 120 }}
              size="small"
            >
              <Option value="all">全部状态</Option>
              <Option value="running">转写中</Option>
              <Option value="succeeded">已完成</Option>
              <Option value="failed">失败</Option>
            </Select>
            <Button icon={<ReloadOutlined />} size="small" onClick={handleRefresh}>
              刷新
            </Button>
          </Space>
        }
      >
        <Table
          columns={columns}
          dataSource={filteredTasks}
          rowKey="id"
          pagination={{ pageSize: 10 }}
          size="middle"
        />
      </Card>
    </div>
  );
}
