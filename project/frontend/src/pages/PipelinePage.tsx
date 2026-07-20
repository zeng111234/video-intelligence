/**
 * 批量生产流水线页面
 * 创建、监控、管理短视频批量生产任务
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
  Steps,
  Row,
  Col,
  Statistic,
  Select,
  Progress,
  Timeline,
  Empty,
} from "antd";
import { useToast } from "../components/Toast";
import {
  ThunderboltOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ReloadOutlined,
  PlusOutlined,
  FileTextOutlined,
  VideoCameraOutlined,
  RocketOutlined,
} from "@ant-design/icons";

const { Title, Text } = Typography;
const { Option } = Select;

/** 流水线状态颜色 */
const STATUS_COLOR: Record<string, string> = {
  succeeded: "success",
  running: "processing",
  failed: "error",
  pending: "default",
  paused: "warning",
};

/** 流水线状态图标 */
const STATUS_ICON: Record<string, React.ReactNode> = {
  succeeded: <CheckCircleOutlined />,
  running: <PlayCircleOutlined />,
  failed: <CloseCircleOutlined />,
  pending: <ClockCircleOutlined />,
  paused: <PauseCircleOutlined />,
};

/** 模拟流水线历史数据 */
const PIPELINE_HISTORY = [
  {
    id: "PL-20260720-001",
    keyword: "二手车测评",
    status: "succeeded",
    videos: 24,
    progress: 100,
    startTime: "2026-07-20 10:30:00",
    duration: "12分35秒",
    stages: [
      { name: "素材采集", status: "succeeded", duration: "3分20秒" },
      { name: "视频筛选", status: "succeeded", duration: "2分15秒" },
      { name: "文案生成", status: "succeeded", duration: "4分10秒" },
      { name: "视频合成", status: "succeeded", duration: "2分50秒" },
    ],
  },
  {
    id: "PL-20260720-002",
    keyword: "新车对比",
    status: "running",
    videos: 18,
    progress: 67,
    startTime: "2026-07-20 14:15:00",
    duration: "进行中",
    stages: [
      { name: "素材采集", status: "succeeded", duration: "2分45秒" },
      { name: "视频筛选", status: "succeeded", duration: "1分50秒" },
      { name: "文案生成", status: "running", duration: "进行中" },
      { name: "视频合成", status: "pending", duration: "-" },
    ],
  },
  {
    id: "PL-20260719-003",
    keyword: "汽车保养技巧",
    status: "succeeded",
    videos: 16,
    progress: 100,
    startTime: "2026-07-19 16:45:00",
    duration: "10分20秒",
    stages: [
      { name: "素材采集", status: "succeeded", duration: "2分30秒" },
      { name: "视频筛选", status: "succeeded", duration: "1分40秒" },
      { name: "文案生成", status: "succeeded", duration: "3分50秒" },
      { name: "视频合成", status: "succeeded", duration: "2分20秒" },
    ],
  },
  {
    id: "PL-20260719-004",
    keyword: "新能源汽车",
    status: "failed",
    videos: 0,
    progress: 25,
    startTime: "2026-07-19 09:20:00",
    duration: "失败",
    stages: [
      { name: "素材采集", status: "succeeded", duration: "1分50秒" },
      { name: "视频筛选", status: "failed", duration: "错误" },
      { name: "文案生成", status: "pending", duration: "-" },
      { name: "视频合成", status: "pending", duration: "-" },
    ],
  },
];

/** 模拟统计数据 */
const STATS = {
  totalPipelines: 156,
  successRate: 94.2,
  totalVideos: 3847,
  avgDuration: "8分30秒",
};

export default function PipelinePage() {
  const toast = useToast();
  const [keyword, setKeyword] = useState("");
  const [keywords, setKeywords] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedPipeline, setSelectedPipeline] = useState<any>(null);
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [videoCount, setVideoCount] = useState<string>("10");
  const [videoStyle, setVideoStyle] = useState<string>("engaging");
  const [refreshing, setRefreshing] = useState(false);

  /** 刷新任务列表 */
  const handleRefresh = useCallback(() => {
    setRefreshing(true);
    setTimeout(() => {
      setRefreshing(false);
      toast.success("任务列表已刷新");
    }, 1000);
  }, [toast]);

  /** 添加关键词 */
  const handleAddKeyword = () => {
    const trimmed = keyword.trim();
    if (!trimmed) {
      toast.warning("请输入关键词");
      return;
    }
    if (keywords.includes(trimmed)) {
      toast.warning("关键词已存在");
      return;
    }
    setKeywords([...keywords, trimmed]);
    setKeyword("");
  };

  /** 删除关键词 */
  const handleRemoveKeyword = (kw: string) => {
    setKeywords(keywords.filter((k) => k !== kw));
  };

  /** 创建流水线 */
  const handleCreate = async () => {
    if (keywords.length === 0) {
      toast.warning("请至少添加一个关键词");
      return;
    }
    setLoading(true);
    // 模拟创建
    setTimeout(() => {
      toast.success(`已创建 ${keywords.length} 个关键词的批量生产任务`);
      setKeywords([]);
      setLoading(false);
    }, 1500);
  };

  /** 过滤后的流水线 */
  const filteredPipelines = useMemo(() => {
    if (filterStatus === "all") return PIPELINE_HISTORY;
    return PIPELINE_HISTORY.filter((p) => p.status === filterStatus);
  }, [filterStatus]);

  /** 表格列定义 */
  const columns = [
    {
      title: "任务ID",
      dataIndex: "id",
      width: 160,
      render: (id: string) => <Text code>{id}</Text>,
    },
    {
      title: "关键词",
      dataIndex: "keyword",
      render: (keyword: string) => <Tag color="blue">{keyword}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (status: string) => (
        <Tag icon={STATUS_ICON[status]} color={STATUS_COLOR[status]}>
          {status === "succeeded" ? "已完成" : status === "running" ? "运行中" : status === "failed" ? "失败" : "等待中"}
        </Tag>
      ),
    },
    {
      title: "产出视频",
      dataIndex: "videos",
      width: 100,
      render: (videos: number) => (
        <Statistic value={videos} valueStyle={{ fontSize: 14 }} prefix={<VideoCameraOutlined />} />
      ),
    },
    {
      title: "进度",
      dataIndex: "progress",
      width: 150,
      render: (progress: number, record: any) => (
        <Progress
          percent={progress}
          size="small"
          status={record.status === "failed" ? "exception" : record.status === "succeeded" ? "success" : "active"}
        />
      ),
    },
    {
      title: "耗时",
      dataIndex: "duration",
      width: 100,
    },
    {
      title: "开始时间",
      dataIndex: "startTime",
      width: 160,
    },
    {
      title: "操作",
      width: 80,
      render: (_: any, record: any) => (
        <Button type="link" size="small" onClick={() => setSelectedPipeline(record)}>
          详情
        </Button>
      ),
    },
  ];

  return (
    <div>
      {/* 页面头部 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          <ThunderboltOutlined /> 批量生产流水线
        </Title>
        <Text type="secondary">一键批量生产短视频，支持多关键词并行处理</Text>
      </div>

      {/* 统计卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="总任务数"
              value={STATS.totalPipelines}
              prefix={<ThunderboltOutlined style={{ color: "#6366f1" }} />}
              valueStyle={{ color: "#6366f1" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="成功率"
              value={STATS.successRate}
              suffix="%"
              prefix={<CheckCircleOutlined style={{ color: "#10b981" }} />}
              valueStyle={{ color: "#10b981" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="产出视频"
              value={STATS.totalVideos}
              prefix={<VideoCameraOutlined style={{ color: "#f59e0b" }} />}
              valueStyle={{ color: "#f59e0b" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="平均耗时"
              value={STATS.avgDuration}
              prefix={<ClockCircleOutlined style={{ color: "#8b5cf6" }} />}
              valueStyle={{ color: "#8b5cf6" }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        {/* 创建任务 */}
        <Col xs={24} lg={10}>
          <Card title={<Space><PlusOutlined /> 创建批量生产任务</Space>}>
            <Space direction="vertical" style={{ width: "100%" }} size={16}>
              {/* 关键词输入 */}
              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>视频关键词</Text>
                <Space.Compact style={{ width: "100%" }}>
                  <Input
                    placeholder="输入关键词，如：二手车、美食、旅游..."
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    onPressEnter={handleAddKeyword}
                  />
                  <Button type="primary" onClick={handleAddKeyword}>
                    添加
                  </Button>
                </Space.Compact>
              </div>

              {/* 关键词列表 */}
              {keywords.length > 0 && (
                <div>
                  <Text type="secondary" style={{ marginBottom: 8, display: "block" }}>
                    已添加 {keywords.length} 个关键词：
                  </Text>
                  <Space wrap>
                    {keywords.map((kw) => (
                      <Tag
                        key={kw}
                        closable
                        onClose={() => handleRemoveKeyword(kw)}
                        color="blue"
                      >
                        {kw}
                      </Tag>
                    ))}
                  </Space>
                </div>
              )}

              {/* 高级选项 */}
              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>生产数量</Text>
                <Select value={videoCount} onChange={setVideoCount} style={{ width: "100%" }}>
                  <Option value="5">每个关键词 5 条视频</Option>
                  <Option value="10">每个关键词 10 条视频</Option>
                  <Option value="20">每个关键词 20 条视频</Option>
                  <Option value="50">每个关键词 50 条视频</Option>
                </Select>
              </div>

              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>视频风格</Text>
                <Select value={videoStyle} onChange={setVideoStyle} style={{ width: "100%" }}>
                  <Option value="engaging">吸引眼球</Option>
                  <Option value="professional">专业权威</Option>
                  <Option value="humorous">幽默风趣</Option>
                  <Option value="storytelling">故事叙述</Option>
                </Select>
              </div>

              {/* 创建按钮 */}
              <Button
                type="primary"
                icon={<RocketOutlined />}
                size="large"
                block
                loading={loading}
                onClick={handleCreate}
                disabled={keywords.length === 0}
              >
                开始批量生产
              </Button>
            </Space>
          </Card>
        </Col>

        {/* 流水线详情 */}
        <Col xs={24} lg={14}>
          <Card
            title={<Space><FileTextOutlined /> 任务详情</Space>}
            extra={
              <Button icon={<ReloadOutlined />} size="small" loading={refreshing} onClick={handleRefresh}>
                刷新
              </Button>
            }
          >
            {selectedPipeline ? (
              <Space direction="vertical" style={{ width: "100%" }} size={16}>
                {/* 基本信息 */}
                <Row gutter={16}>
                  <Col span={8}>
                    <Statistic title="任务ID" value={selectedPipeline.id} valueStyle={{ fontSize: 14 }} />
                  </Col>
                  <Col span={8}>
                    <Statistic title="关键词" value={selectedPipeline.keyword} valueStyle={{ fontSize: 14 }} />
                  </Col>
                  <Col span={8}>
                    <Statistic title="产出视频" value={selectedPipeline.videos} valueStyle={{ fontSize: 14 }} />
                  </Col>
                </Row>

                {/* 流水线步骤 */}
                <div>
                  <Text strong style={{ marginBottom: 12, display: "block" }}>生产流程</Text>
                  <Steps
                    size="small"
                    current={selectedPipeline.stages.findIndex((s: any) => s.status === "running")}
                    items={selectedPipeline.stages.map((stage: any) => ({
                      title: stage.name,
                      description: stage.duration,
                      status:
                        stage.status === "succeeded"
                          ? "finish"
                          : stage.status === "running"
                            ? "process"
                            : stage.status === "failed"
                              ? "error"
                              : "wait",
                    }))}
                  />
                </div>

                {/* 时间线 */}
                <div>
                  <Text strong style={{ marginBottom: 12, display: "block" }}>执行日志</Text>
                  <Timeline
                    items={selectedPipeline.stages.map((stage: any) => ({
                      color:
                        stage.status === "succeeded"
                          ? "green"
                          : stage.status === "running"
                            ? "blue"
                            : stage.status === "failed"
                              ? "red"
                              : "gray",
                      children: (
                        <div>
                          <Text strong>{stage.name}</Text>
                          <br />
                          <Text type="secondary">{stage.duration}</Text>
                        </div>
                      ),
                    }))}
                  />
                </div>
              </Space>
            ) : (
              <Empty description="点击任务列表中的「详情」查看流水线执行情况" />
            )}
          </Card>
        </Col>
      </Row>

      {/* 任务历史 */}
      <Card
        title={<Space><ClockCircleOutlined /> 任务历史</Space>}
        extra={
          <Space>
            <Select
              value={filterStatus}
              onChange={setFilterStatus}
              style={{ width: 120 }}
              size="small"
            >
              <Option value="all">全部状态</Option>
              <Option value="running">运行中</Option>
              <Option value="succeeded">已完成</Option>
              <Option value="failed">失败</Option>
            </Select>
          </Space>
        }
      >
        <Table
          columns={columns}
          dataSource={filteredPipelines}
          rowKey="id"
          pagination={{ pageSize: 10 }}
          size="middle"
        />
      </Card>
    </div>
  );
}
