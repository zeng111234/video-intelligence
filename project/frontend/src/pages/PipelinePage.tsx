/**
 * 批量生产流水线页面
 * 串联整个内容生产工作流：候选检索 → 语音转写 → AI文案 → 数字人 → 发布
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
  Steps,
  Row,
  Col,
  Statistic,
  Select,
  Progress,
  Empty,
  Switch,
  Divider,
} from "antd";
import { useToast } from "../components/Toast";
import {
  ThunderboltOutlined,
  PlayCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ReloadOutlined,
  PlusOutlined,
  FileTextOutlined,
  VideoCameraOutlined,
  RocketOutlined,
  SearchOutlined,
  AudioOutlined,
  EditOutlined,
  SendOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { createPipeline } from "../api/client";

const { Title, Text } = Typography;
const { Option } = Select;

/** 流水线阶段定义 - 按内容生产流程排序 */
const PIPELINE_STAGES = [
  {
    key: "crawler",
    label: "关键词爬取",
    icon: <SearchOutlined />,
    description: "爬取热门关键词和趋势视频",
    color: "#6366f1",
  },
  {
    key: "transcription",
    label: "文案提取",
    icon: <AudioOutlined />,
    description: "从视频中提取语音转为文案",
    color: "#10b981",
  },
  {
    key: "copywriting",
    label: "AI文案改写",
    icon: <EditOutlined />,
    description: "AI 智能改写文案，生成多版本",
    color: "#f59e0b",
  },
  {
    key: "avatar",
    label: "数字人生成",
    icon: <VideoCameraOutlined />,
    description: "用改写后的文案生成口播视频",
    color: "#8b5cf6",
  },
  {
    key: "publish",
    label: "多平台发布",
    icon: <SendOutlined />,
    description: "一键发布到抖音/小红书/视频号",
    color: "#ef4444",
  },
];

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
};

/** 模拟流水线历史数据 */
const PIPELINE_HISTORY = [
  {
    id: "PL-20260721-001",
    name: "二手车测评批量生产",
    keyword: "二手车测评",
    status: "succeeded",
    videos: 24,
    progress: 100,
    startTime: "2026-07-21 10:30:00",
    duration: "45分35秒",
    stages: [
      { name: "关键词爬取", status: "succeeded", duration: "5分20秒", count: 50 },
      { name: "文案提取", status: "succeeded", duration: "12分15秒", count: 50 },
      { name: "AI文案改写", status: "succeeded", duration: "8分10秒", count: 24 },
      { name: "数字人生成", status: "succeeded", duration: "15分50秒", count: 24 },
      { name: "多平台发布", status: "succeeded", duration: "4分0秒", count: 24 },
    ],
  },
  {
    id: "PL-20260721-002",
    name: "新车对比系列",
    keyword: "新车对比",
    status: "running",
    videos: 12,
    progress: 60,
    startTime: "2026-07-21 14:15:00",
    duration: "进行中",
    stages: [
      { name: "关键词爬取", status: "succeeded", duration: "4分45秒", count: 30 },
      { name: "文案提取", status: "succeeded", duration: "9分50秒", count: 30 },
      { name: "AI文案改写", status: "running", duration: "进行中", count: 12 },
      { name: "数字人生成", status: "pending", duration: "-", count: 0 },
      { name: "多平台发布", status: "pending", duration: "-", count: 0 },
    ],
  },
  {
    id: "PL-20260720-003",
    name: "汽车保养技巧",
    keyword: "汽车保养",
    status: "succeeded",
    videos: 16,
    progress: 100,
    startTime: "2026-07-20 16:45:00",
    duration: "38分20秒",
    stages: [
      { name: "关键词爬取", status: "succeeded", duration: "4分30秒", count: 40 },
      { name: "文案提取", status: "succeeded", duration: "10分40秒", count: 40 },
      { name: "AI文案改写", status: "succeeded", duration: "7分50秒", count: 16 },
      { name: "数字人生成", status: "succeeded", duration: "12分20秒", count: 16 },
      { name: "多平台发布", status: "succeeded", duration: "3分0秒", count: 16 },
    ],
  },
  {
    id: "PL-20260720-004",
    name: "新能源汽车",
    keyword: "新能源",
    status: "failed",
    videos: 0,
    progress: 20,
    startTime: "2026-07-20 09:20:00",
    duration: "失败",
    stages: [
      { name: "关键词爬取", status: "succeeded", duration: "3分50秒", count: 25 },
      { name: "文案提取", status: "failed", duration: "错误", count: 0 },
      { name: "AI文案改写", status: "pending", duration: "-", count: 0 },
      { name: "数字人生成", status: "pending", duration: "-", count: 0 },
      { name: "多平台发布", status: "pending", duration: "-", count: 0 },
    ],
  },
];

/** 模拟统计数据 */
const STATS = {
  totalPipelines: 156,
  successRate: 94.2,
  totalVideos: 3847,
  avgDuration: "38分30秒",
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
  const [pipelineHistory, setPipelineHistory] = useState(PIPELINE_HISTORY);

  /** 流水线阶段开关 */
  const [enabledStages, setEnabledStages] = useState<Record<string, boolean>>({
    crawler: true,
    transcription: true,
    copywriting: true,
    avatar: true,
    publish: false,
  });

  /** 切换阶段开关 */
  const toggleStage = (key: string) => {
    setEnabledStages((prev) => ({ ...prev, [key]: !prev[key] }));
  };

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

    const activeStages = Object.entries(enabledStages)
      .filter(([_, enabled]) => enabled)
      .map(([key]) => key);

    if (activeStages.length === 0) {
      toast.warning("请至少启用一个流水线阶段");
      return;
    }

    setLoading(true);
    try {
      const results = await Promise.all(
        keywords.map((kw) => createPipeline(kw, { count: videoCount, style: videoStyle, stages: activeStages }))
      );
      toast.success(`已创建 ${keywords.length} 个批量生产任务`);
      const newTasks = results.map((r, i) => ({
        id: r.run_id || `PL-${Date.now()}-${i}`,
        name: `${keywords[i]}批量生产`,
        keyword: keywords[i],
        status: r.status || "running",
        videos: 0,
        progress: 0,
        startTime: new Date().toLocaleString("zh-CN"),
        duration: "进行中",
        stages: PIPELINE_STAGES.filter((s) => activeStages.includes(s.key)).map((s) => ({
          name: s.label,
          status: "pending",
          duration: "-",
          count: 0,
        })),
      }));
      setPipelineHistory((prev) => [...newTasks, ...prev]);
      setKeywords([]);
    } catch (err) {
      toast.error((err as Error).message || "创建失败");
    } finally {
      setLoading(false);
    }
  };

  /** 过滤后的流水线 */
  const filteredPipelines = useMemo(() => {
    if (filterStatus === "all") return pipelineHistory;
    return pipelineHistory.filter((p) => p.status === filterStatus);
  }, [filterStatus, pipelineHistory]);

  /** 表格列定义 */
  const columns = [
    {
      title: "任务ID",
      dataIndex: "id",
      width: 160,
      render: (id: string) => <Text code>{id}</Text>,
    },
    {
      title: "任务名称",
      dataIndex: "name",
      render: (name: string) => <Text strong>{name}</Text>,
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
        <Text type="secondary">一键串联整个内容生产工作流，批量生成短视频</Text>
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

              <Divider style={{ margin: "8px 0" }} />

              {/* 流水线阶段配置 */}
              <div>
                <Text strong style={{ display: "block", marginBottom: 12 }}>
                  <SettingOutlined style={{ marginRight: 8 }} />
                  流水线阶段配置
                </Text>
                <Space direction="vertical" style={{ width: "100%" }} size={8}>
                  {PIPELINE_STAGES.map((stage) => (
                    <div
                      key={stage.key}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "8px 12px",
                        background: enabledStages[stage.key] ? `${stage.color}10` : "var(--gray-50, #f8fafc)",
                        borderRadius: 8,
                        border: `1px solid ${enabledStages[stage.key] ? stage.color + "30" : "var(--border-light, #e2e8f0)"}`,
                      }}
                    >
                      <Space>
                        <span style={{ color: stage.color }}>{stage.icon}</span>
                        <div>
                          <Text strong style={{ fontSize: 13 }}>{stage.label}</Text>
                          <br />
                          <Text type="secondary" style={{ fontSize: 11 }}>{stage.description}</Text>
                        </div>
                      </Space>
                      <Switch
                        size="small"
                        checked={enabledStages[stage.key]}
                        onChange={() => toggleStage(stage.key)}
                      />
                    </div>
                  ))}
                </Space>
              </div>

              <Divider style={{ margin: "8px 0" }} />

              {/* 高级选项 */}
              <Row gutter={16}>
                <Col span={12}>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>生产数量</Text>
                  <Select value={videoCount} onChange={setVideoCount} style={{ width: "100%" }}>
                    <Option value="5">每个关键词 5 条</Option>
                    <Option value="10">每个关键词 10 条</Option>
                    <Option value="20">每个关键词 20 条</Option>
                    <Option value="50">每个关键词 50 条</Option>
                  </Select>
                </Col>
                <Col span={12}>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>视频风格</Text>
                  <Select value={videoStyle} onChange={setVideoStyle} style={{ width: "100%" }}>
                    <Option value="engaging">吸引眼球</Option>
                    <Option value="professional">专业权威</Option>
                    <Option value="humorous">幽默风趣</Option>
                    <Option value="storytelling">故事叙述</Option>
                  </Select>
                </Col>
              </Row>

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
                      description: `${stage.duration} · ${stage.count}条`,
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
                          <Text type="secondary">{stage.duration} · 产出 {stage.count} 条</Text>
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
