/**
 * 数据仪表盘
 * 直观展示业务数据概览、趋势、任务状态
 * 使用 recharts 渲染图表，替代纯 CSS 手绘
 */
import { useState } from "react";
import {
  Typography,
  Card,
  Row,
  Col,
  Statistic,
  Space,
  Progress,
  Table,
  Tag,
  List,
  Avatar,
  Select,
} from "antd";
import {
  FileTextOutlined,
  ThunderboltOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  RiseOutlined,
  VideoCameraOutlined,
  AudioOutlined,
  RocketOutlined,
} from "@ant-design/icons";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from "recharts";

const { Title, Text } = Typography;
const { Option } = Select;

/** 模拟生产数据 */
const PRODUCTION_STATS = {
  totalVideos: 1284,
  todayProduced: 47,
  totalCandidates: 3562,
  todayCandidates: 128,
  activeTasks: 12,
  completedTasks: 856,
  successRate: 94.7,
  avgProcessTime: 3.2,
};

/** 模拟周趋势数据 */
const WEEKLY_TREND = [
  { day: "周一", videos: 32, candidates: 85 },
  { day: "周二", videos: 45, candidates: 112 },
  { day: "周三", videos: 38, candidates: 96 },
  { day: "周四", videos: 52, candidates: 134 },
  { day: "周五", videos: 41, candidates: 108 },
  { day: "周六", videos: 28, candidates: 72 },
  { day: "周日", videos: 47, candidates: 128 },
];

/** 模拟最近任务 */
const RECENT_TASKS = [
  { id: "1", name: "二手车测评批量生产", type: "pipeline", status: "running", progress: 67, count: 24 },
  { id: "2", name: "竞品视频语音转写", type: "transcription", status: "completed", progress: 100, count: 8 },
  { id: "3", name: "新车对比素材采集", type: "candidate", status: "running", progress: 45, count: 156 },
  { id: "4", name: "汽车保养文案生成", type: "ai-copy", status: "completed", progress: 100, count: 12 },
  { id: "5", name: "新能源视频筛选", type: "candidate", status: "pending", progress: 0, count: 0 },
];

/** 模拟热门候选 */
const TOP_CANDIDATES = [
  { title: "选择七座车一定要清楚自己的用车需求", score: 57.4, views: "125.8万" },
  { title: "20年奥迪A6L 车况精品", score: 57.4, views: "89.2万" },
  { title: "江铃凯运 一手车 6座", score: 57.2, views: "67.4万" },
  { title: "丰田霸道4.0的故事", score: 56.8, views: "45.6万" },
];

/** 任务类型分布数据（recharts 饼图） */
const TASK_TYPE_DATA = [
  { name: "批量生产", value: 456 },
  { name: "候选采集", value: 312 },
  { name: "语音转写", value: 156 },
  { name: "AI文案", value: 89 },
];

/** 任务类型图标 */
const taskTypeIcon: Record<string, React.ReactNode> = {
  pipeline: <ThunderboltOutlined style={{ color: "var(--primary-500)" }} />,
  transcription: <AudioOutlined style={{ color: "var(--success)" }} />,
  candidate: <FileTextOutlined style={{ color: "var(--warning)" }} />,
  "ai-copy": <RocketOutlined style={{ color: "var(--error)" }} />,
};

/** recharts 饼图颜色 */
const PIE_COLORS_HEX = ["#8b5cf6", "#10b981", "#f59e0b", "#ef4444"];

/** 任务状态标签 */
const taskStatusTag: Record<string, React.ReactNode> = {
  running: <Tag color="processing">运行中</Tag>,
  completed: <Tag color="success">已完成</Tag>,
  pending: <Tag color="default">等待中</Tag>,
  failed: <Tag color="error">失败</Tag>,
};

export default function DashboardPage() {
  const [timeRange, setTimeRange] = useState("today");
  const [loading, setLoading] = useState(false);

  /** 时间范围变化时刷新数据 */
  const handleTimeRangeChange = useCallback((value: string) => {
    setTimeRange(value);
    setLoading(true);
    // 模拟数据加载
    setTimeout(() => {
      setLoading(false);
    }, 500);
  }, []);

  return (
    <div>
      {/* 页面头部 */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>
            数据仪表盘
          </Title>
          <Text type="secondary">实时监控短视频生产全链路数据</Text>
        </div>
        <Select value={timeRange} onChange={handleTimeRangeChange} style={{ width: 120 }} loading={loading}>
          <Option value="today">今日</Option>
          <Option value="week">本周</Option>
          <Option value="month">本月</Option>
        </Select>
      </div>

      {/* 核心指标卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="已生产视频"
              value={PRODUCTION_STATS.totalVideos}
              prefix={<VideoCameraOutlined style={{ color: "var(--primary-500)" }} />}
              suffix={
                <Text type="success" style={{ fontSize: 14 }}>
                  <RiseOutlined /> +{PRODUCTION_STATS.todayProduced}
                </Text>
              }
              valueStyle={{ color: "var(--primary-500)" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="候选素材"
              value={PRODUCTION_STATS.totalCandidates}
              prefix={<FileTextOutlined style={{ color: "var(--success)" }} />}
              suffix={
                <Text type="success" style={{ fontSize: 14 }}>
                  <RiseOutlined /> +{PRODUCTION_STATS.todayCandidates}
                </Text>
              }
              valueStyle={{ color: "var(--success)" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="进行中任务"
              value={PRODUCTION_STATS.activeTasks}
              prefix={<ClockCircleOutlined style={{ color: "var(--warning)" }} />}
              valueStyle={{ color: "var(--warning)" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="成功率"
              value={PRODUCTION_STATS.successRate}
              suffix="%"
              prefix={<CheckCircleOutlined style={{ color: "var(--success)" }} />}
              valueStyle={{ color: "var(--success)" }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        {/* 生产趋势 - recharts 柱状图 */}
        <Col xs={24} lg={14}>
          <Card title="本周生产趋势" extra={<Text type="secondary">单位：条</Text>}>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={WEEKLY_TREND} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--gray-200)" vertical={false} />
                <XAxis dataKey="day" fontSize={12} tickLine={false} axisLine={false} />
                <YAxis fontSize={12} tickLine={false} axisLine={false} />
                <Tooltip
                  contentStyle={{
                    borderRadius: 8,
                    border: "1px solid var(--gray-200)",
                    boxShadow: "var(--shadow-md)",
                  }}
                />
                <Bar dataKey="videos" name="生产视频" fill="#8b5cf6" radius={[6, 6, 0, 0]} maxBarSize={40} />
                <Bar dataKey="candidates" name="候选素材" fill="#10b981" radius={[6, 6, 0, 0]} maxBarSize={40} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </Col>

        {/* 任务类型分布 - recharts 饼图 */}
        <Col xs={24} lg={10}>
          <Card title="任务类型分布">
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={TASK_TYPE_DATA}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={80}
                  paddingAngle={4}
                  dataKey="value"
                >
                  {TASK_TYPE_DATA.map((_, index) => (
                    <Cell key={`cell-${index}`} fill={PIE_COLORS_HEX[index % PIE_COLORS_HEX.length]} />
                  ))}
                </Pie>
                <Tooltip />
                <Legend
                  formatter={(value: string) => <span style={{ color: "var(--text-primary)" }}>{value}</span>}
                />
              </PieChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        {/* 最近任务 */}
        <Col xs={24} lg={12}>
          <Card
            title="最近任务"
            extra={<a href="/tasks">查看全部</a>}
          >
            <Table
              dataSource={RECENT_TASKS}
              pagination={false}
              size="small"
              columns={[
                {
                  title: "任务",
                  dataIndex: "name",
                  render: (name: string, record: any) => (
                    <Space>
                      {taskTypeIcon[record.type]}
                      <Text ellipsis style={{ maxWidth: 180 }}>{name}</Text>
                    </Space>
                  ),
                },
                {
                  title: "状态",
                  dataIndex: "status",
                  width: 90,
                  render: (status: string) => taskStatusTag[status],
                },
                {
                  title: "进度",
                  dataIndex: "progress",
                  width: 120,
                  render: (progress: number) => (
                    <Progress
                      percent={progress}
                      size="small"
                      status={progress === 100 ? "success" : "active"}
                    />
                  ),
                },
              ]}
            />
          </Card>
        </Col>

        {/* 热门候选 */}
        <Col xs={24} lg={12}>
          <Card
            title="热门候选素材"
            extra={<a href="/candidates">查看全部</a>}
          >
            <List
              dataSource={TOP_CANDIDATES}
              renderItem={(item, index) => (
                <List.Item>
                  <List.Item.Meta
                    avatar={
                      <Avatar
                        style={{
                          background: index < 3 ? "#6366f1" : "#94a3b8",
                          fontSize: 14,
                        }}
                      >
                        {index + 1}
                      </Avatar>
                    }
                    title={
                      <Text ellipsis style={{ maxWidth: 280 }}>
                        {item.title}
                      </Text>
                    }
                    description={
                      <Space>
                        <Tag color="blue">热度 {item.score}</Tag>
                        <Tag color="green">{item.views} 播放</Tag>
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
