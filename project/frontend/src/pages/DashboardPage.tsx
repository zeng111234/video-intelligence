/**
 * 数据仪表盘
 * 展示业务数据概览、趋势、任务状态
 * 所有数据来自后端 API，无硬编码测试数据
 */
import { useState, useEffect, useCallback } from "react";
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
  Button,
  Select,
  Empty,
  Popconfirm,
  Spin,
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
  DeleteOutlined,
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
import {
  getDashboardStats,
  deleteTask,
  listTasks,
  searchCandidates,
  type DashboardStatsResponse,
} from "../api/client";
import type { TaskItem, CandidateItem } from "../api/types";
import { useToast } from "../components/Toast";

const { Title, Text } = Typography;
const { Option } = Select;

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
  succeeded: <Tag color="success">已完成</Tag>,
  pending: <Tag color="default">等待中</Tag>,
  failed: <Tag color="error">失败</Tag>,
};

/** 空统计卡片默认值 */
const EMPTY_STATS: DashboardStatsResponse = {
  totalVideos: 0,
  todayProduced: 0,
  totalCandidates: 0,
  todayCandidates: 0,
  activeTasks: 0,
  completedTasks: 0,
  failedTasks: 0,
  totalTasks: 0,
  successRate: 0,
  pipelineCount: 0,
};

export default function DashboardPage() {
  const toast = useToast();
  const [timeRange, setTimeRange] = useState("today");
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<DashboardStatsResponse>(EMPTY_STATS);
  const [recentTasks, setRecentTasks] = useState<TaskItem[]>([]);
  const [topCandidates, setTopCandidates] = useState<CandidateItem[]>([]);
  const [taskTypeData, setTaskTypeData] = useState<Array<{ name: string; value: number }>>([]);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);

  /** 加载 Dashboard 数据 */
  const loadDashboardData = useCallback(async () => {
    setLoading(true);
    try {
      const [statsData, tasksData, candidatesData] = await Promise.allSettled([
        getDashboardStats(),
        listTasks(),
        searchCandidates("", 10),
      ]);

      if (statsData.status === "fulfilled") {
        setStats(statsData.value);
      }

      if (tasksData.status === "fulfilled") {
        const items = tasksData.value.items || [];
        setRecentTasks(items.slice(0, 5));
        // 计算任务类型分布
        const typeCounts: Record<string, number> = {};
        items.forEach((t) => {
          const label =
            t.kind === "pipeline" ? "视频创作" :
            t.kind === "transcription" ? "语音转写" :
            t.kind === "candidate" ? "候选采集" :
            t.kind === "copywriting" ? "AI文案" : t.kind;
          typeCounts[label] = (typeCounts[label] || 0) + 1;
        });
        setTaskTypeData(
          Object.entries(typeCounts).map(([name, value]) => ({ name, value }))
        );
      }

      if (candidatesData.status === "fulfilled") {
        setTopCandidates((candidatesData.value.items || []).slice(0, 5));
      }
    } catch {
      // 每个请求独立处理错误，不阻塞其他区域
    } finally {
      setLoading(false);
    }
  }, []);

  const handleDeleteRecentTask = async (task: TaskItem) => {
    setDeletingTaskId(task.task_id);
    try {
      await deleteTask(task.task_id);
      setRecentTasks((items) => items.filter((item) => item.task_id !== task.task_id));
      toast.success("任务记录已删除");
    } catch (error) {
      toast.error((error as Error).message || "删除任务记录失败");
    } finally {
      setDeletingTaskId(null);
    }
  };

  useEffect(() => {
    loadDashboardData();
  }, [loadDashboardData]);

  /** 时间范围变化 */
  const handleTimeRangeChange = useCallback((value: string) => {
    setTimeRange(value);
    // 后端暂无按时间范围过滤的 Dashboard API，保留选择器交互
  }, []);

  return (
    <Spin spinning={loading}>
      <div>
        {/* 页面头部 */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
          <div>
            <Title level={4} style={{ margin: 0 }}>
              数据仪表盘
            </Title>
            <Text type="secondary">查看视频创作流程和任务数据</Text>
          </div>
          <Select value={timeRange} onChange={handleTimeRangeChange} style={{ width: 120 }}>
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
                value={stats.totalVideos}
                prefix={<VideoCameraOutlined style={{ color: "var(--primary-500)" }} />}
                valueStyle={{ color: "var(--primary-500)" }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={6}>
            <Card hoverable>
              <Statistic
                title="候选素材"
                value={stats.totalCandidates}
                prefix={<FileTextOutlined style={{ color: "var(--success)" }} />}
                suffix={
                  stats.todayCandidates > 0 ? (
                    <Text type="success" style={{ fontSize: 14 }}>
                      <RiseOutlined /> +{stats.todayCandidates}
                    </Text>
                  ) : undefined
                }
                valueStyle={{ color: "var(--success)" }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={6}>
            <Card hoverable>
              <Statistic
                title="进行中任务"
                value={stats.activeTasks}
                prefix={<ClockCircleOutlined style={{ color: "var(--warning)" }} />}
                valueStyle={{ color: "var(--warning)" }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={6}>
            <Card hoverable>
              <Statistic
                title="成功率"
                value={stats.successRate}
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
              {taskTypeData.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={taskTypeData} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--gray-200)" vertical={false} />
                    <XAxis dataKey="name" fontSize={12} tickLine={false} axisLine={false} />
                    <YAxis fontSize={12} tickLine={false} axisLine={false} />
                    <Tooltip
                      contentStyle={{
                        borderRadius: 8,
                        border: "1px solid var(--gray-200)",
                        boxShadow: "var(--shadow-md)",
                      }}
                    />
                    <Bar dataKey="value" name="任务数" fill="#8b5cf6" radius={[6, 6, 0, 0]} maxBarSize={40} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <Empty description="暂无趋势数据" style={{ padding: "40px 0" }} />
              )}
            </Card>
          </Col>

          {/* 任务类型分布 - recharts 饼图 */}
          <Col xs={24} lg={10}>
            <Card title="任务类型分布">
              {taskTypeData.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie
                      data={taskTypeData}
                      cx="50%"
                      cy="50%"
                      innerRadius={50}
                      outerRadius={80}
                      paddingAngle={4}
                      dataKey="value"
                    >
                      {taskTypeData.map((_, index) => (
                        <Cell key={`cell-${index}`} fill={PIE_COLORS_HEX[index % PIE_COLORS_HEX.length]} />
                      ))}
                    </Pie>
                    <Tooltip />
                    <Legend
                      formatter={(value: string) => <span style={{ color: "var(--text-primary)" }}>{value}</span>}
                    />
                  </PieChart>
                </ResponsiveContainer>
              ) : (
                <Empty description="暂无任务数据" style={{ padding: "40px 0" }} />
              )}
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
              {recentTasks.length > 0 ? (
                <Table
                  dataSource={recentTasks}
                  pagination={false}
                  size="small"
                  rowKey="task_id"
                  columns={[
                    {
                      title: "任务",
                      dataIndex: "title",
                      render: (title: string, record: TaskItem) => (
                        <Space>
                          {taskTypeIcon[record.kind]}
                          <Text ellipsis style={{ maxWidth: 180 }}>{title}</Text>
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
                    {
                      title: "操作",
                      width: 72,
                      render: (_: unknown, record: TaskItem) => (
                        <Popconfirm
                          title="删除这条任务记录？"
                          description="只删除本条任务记录，不会删除候选视频或本地素材。"
                          okText="删除"
                          okButtonProps={{ danger: true }}
                          cancelText="取消"
                          onConfirm={() => handleDeleteRecentTask(record)}
                        >
                          <Button type="link" danger size="small" icon={<DeleteOutlined />} loading={deletingTaskId === record.task_id}>删除</Button>
                        </Popconfirm>
                      ),
                    },
                  ]}
                />
              ) : (
                <Empty description="暂无任务记录" />
              )}
            </Card>
          </Col>

          {/* 热门候选 */}
          <Col xs={24} lg={12}>
            <Card
              title="热门候选素材"
              extra={<a href="/candidates">查看全部</a>}
            >
              {topCandidates.length > 0 ? (
                <List
                  dataSource={topCandidates}
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
                            <Tag color="blue">热度 {item.heat_score}</Tag>
                            <Tag color="green">{item.platform}</Tag>
                          </Space>
                        }
                      />
                    </List.Item>
                  )}
                />
              ) : (
                <Empty description="暂无候选素材" />
              )}
            </Card>
          </Col>
        </Row>
      </div>
    </Spin>
  );
}
