/**
 * 语音转写页面
 * 上传视频/音频文件，AI 自动转写为文字
 */
import { useState, useMemo } from "react";
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
  message,
} from "antd";
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
} from "@ant-design/icons";

const { Title, Text, Paragraph } = Typography;
const { Option } = Select;

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
    fileName: "二手车测评_李老司.mp4",
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
    fileName: "新车对比_宝马vs奔驰.mp4",
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
  const [searchText, setSearchText] = useState("");
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [selectedTask, setSelectedTask] = useState<any>(null);

  /** 过滤后的转写任务 */
  const filteredTasks = useMemo(() => {
    let tasks = TRANSCRIPTION_HISTORY;
    if (filterStatus !== "all") {
      tasks = tasks.filter((t) => t.status === filterStatus);
    }
    if (searchText) {
      tasks = tasks.filter((t) =>
        t.fileName.toLowerCase().includes(searchText.toLowerCase())
      );
    }
    return tasks;
  }, [filterStatus, searchText]);

  /** 复制文本 */
  const handleCopyText = (text: string) => {
    navigator.clipboard.writeText(text);
    message.success("已复制到剪贴板");
  };

  /** 表格列定义 */
  const columns = [
    {
      title: "文件名",
      dataIndex: "fileName",
      render: (name: string) => (
        <Space>
          <SoundOutlined style={{ color: "#6366f1" }} />
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
        {/* 上传区域 */}
        <Col xs={24} lg={8}>
          <Card title={<Space><UploadOutlined /> 上传文件</Space>}>
            <Space direction="vertical" style={{ width: "100%" }} size={16}>
              <Upload.Dragger
                name="file"
                multiple={false}
                accept=".mp4,.mp3,.wav,.m4a,.avi"
                beforeUpload={() => {
                  message.success("文件已添加，点击「开始转写」处理");
                  return false;
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

              <Button type="primary" icon={<PlayCircleOutlined />} block size="large">
                开始转写
              </Button>

              <div>
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
            </Space>
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
                  <Button icon={<DownloadOutlined />} size="small">
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
            <Button icon={<ReloadOutlined />} size="small">
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
