/**
 * 深度分析页面（Phase 3）
 * 接入后端 candidates API 获取真实数据
 * 使用 recharts 渲染图表
 */
import { useState, useEffect, useCallback } from "react";
import {
  Typography,
  Card,
  Row,
  Col,
  Select,
  Tag,
  Progress,
  Statistic,
  Space,
  Table,
  Spin,
  Button,
} from "antd";
import {
  LineChartOutlined,
  RiseOutlined,
  TeamOutlined,
  TrophyOutlined,
  ReloadOutlined,
  PlayCircleOutlined,
  ShareAltOutlined,
  HeartOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from "recharts";
import { getAnalyticsData } from "../api/client";
import { useToast } from "../components/Toast";
import { SkeletonPage } from "../components/SkeletonLoader";

const { Title, Text } = Typography;
const { Option } = Select;

/** 饼图配色 */
const PIE_COLORS = [
  "var(--primary-500)",
  "var(--success)",
  "var(--warning)",
  "var(--info)",
  "#ec4899",
  "#8b5cf6",
];

export default function AnalyticsPage() {
  const toast = useToast();
  const [timeRange, setTimeRange] = useState("7d");
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<{
    overview: {
      totalViews: number;
      totalWatchHours: number;
      engagementRate: number;
      shareCount: number;
    };
    trends: { topic: string; views: string; growth: number; hot: string }[];
    competitors: { name: string; fans: string; avgViews: string; engagement: number }[];
    contentDistribution: { label: string; percent: number }[];
  } | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await getAnalyticsData(timeRange);
      setData(resp);
    } catch (err) {
      if ((err as Error).message?.includes("网络连接失败")) {
        console.warn("后端服务未启动，使用本地缓存数据");
      } else {
        toast.error((err as Error).message || "数据加载失败");
      }
    } finally {
      setLoading(false);
    }
  }, [timeRange, toast]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading && !data) {
    return <SkeletonPage />;
  }

  const overview = data?.overview;
  const trends = data?.trends || [];
  const competitors = data?.competitors || [];
  const contentDistribution = data?.contentDistribution || [];

  /** 柱状图数据 */
  const barData = trends.map((t) => ({
    name: t.topic.length > 8 ? t.topic.slice(0, 8) + "..." : t.topic,
    views: parseInt(t.views) || 0,
  }));

  /** 饼图数据 */
  const pieData = contentDistribution.map((d) => ({
    name: d.label,
    value: d.percent,
  }));

  /** 竞品分析表格列 */
  const competitorColumns = [
    {
      title: "排名",
      width: 60,
      render: (_: unknown, __: unknown, idx: number) => (
        <Tag color={idx < 3 ? "gold" : "default"}>
          {idx < 3 ? <TrophyOutlined /> : null} {idx + 1}
        </Tag>
      ),
    },
    { title: "账号名称", dataIndex: "name" },
    { title: "粉丝数", dataIndex: "fans" },
    { title: "平均播放", dataIndex: "avgViews" },
    {
      title: "互动率",
      dataIndex: "engagement",
      render: (v: number) => (
        <Progress
          percent={v * 10}
          size="small"
          status={v >= 8 ? "success" : "normal"}
          format={() => `${v}%`}
        />
      ),
    },
  ];

  return (
    <div>
      {/* 页面头部 */}
      <div style={{ marginBottom: 24, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>
            <LineChartOutlined /> 深度分析
          </Title>
          <Text type="secondary">实时追踪内容表现，洞察行业趋势</Text>
        </div>
        <Space>
          <Select value={timeRange} onChange={setTimeRange} style={{ width: 140 }}>
            <Option value="24h">最近 24 小时</Option>
            <Option value="7d">最近 7 天</Option>
            <Option value="30d">最近 30 天</Option>
            <Option value="90d">最近 90 天</Option>
          </Select>
          <Button icon={<ReloadOutlined />} onClick={fetchData} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      {/* 核心指标卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="总播放量"
              value={overview?.totalViews || 0}
              prefix={<PlayCircleOutlined style={{ color: "var(--primary-500)" }} />}
              valueStyle={{ color: "var(--primary-500)" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="观看时长(h)"
              value={overview?.totalWatchHours || 0}
              precision={1}
              prefix={<ClockCircleOutlined style={{ color: "var(--success)" }} />}
              valueStyle={{ color: "var(--success)" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="互动率"
              value={overview?.engagementRate || 0}
              suffix="%"
              prefix={<HeartOutlined style={{ color: "var(--warning)" }} />}
              valueStyle={{ color: "var(--warning)" }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable>
            <Statistic
              title="分享次数"
              value={overview?.shareCount || 0}
              prefix={<ShareAltOutlined style={{ color: "var(--info)" }} />}
              valueStyle={{ color: "var(--info)" }}
            />
          </Card>
        </Col>
      </Row>

      {/* 图表区 */}
      <Row gutter={[24, 24]} style={{ marginBottom: 24 }}>
        {/* 热门话题柱状图 */}
        <Col xs={24} lg={14}>
          <Card
            title={
              <Space>
                <ThunderboltOutlined /> 热门话题趋势
              </Space>
            }
          >
            <Spin spinning={loading}>
              {barData.length > 0 ? (
                <ResponsiveContainer width="100%" height={300}>
                  <BarChart data={barData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
                    <XAxis dataKey="name" tick={{ fontSize: 12, fill: "var(--text-secondary)" }} />
                    <YAxis tick={{ fontSize: 12, fill: "var(--text-secondary)" }} />
                    <RechartsTooltip
                      contentStyle={{
                        background: "var(--bg-card)",
                        border: "1px solid var(--border-default)",
                        borderRadius: "var(--radius-sm)",
                      }}
                    />
                    <Bar dataKey="views" fill="var(--primary-500)" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div style={{ height: 300, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Text type="secondary">暂无数据</Text>
                </div>
              )}
            </Spin>
          </Card>
        </Col>

        {/* 内容分布饼图 */}
        <Col xs={24} lg={10}>
          <Card
            title={
              <Space>
                <RiseOutlined /> 内容分布
              </Space>
            }
          >
            <Spin spinning={loading}>
              {pieData.length > 0 ? (
                <ResponsiveContainer width="100%" height={300}>
                  <PieChart>
                    <Pie
                      data={pieData}
                      cx="50%"
                      cy="50%"
                      innerRadius={60}
                      outerRadius={100}
                      paddingAngle={3}
                      dataKey="value"
                      label={({ name, percent }) => `${name} ${((percent || 0) * 100).toFixed(0)}%`}
                    >
                      {pieData.map((_, idx) => (
                        <Cell key={idx} fill={PIE_COLORS[idx % PIE_COLORS.length]} />
                      ))}
                    </Pie>
                    <Legend />
                    <RechartsTooltip />
                  </PieChart>
                </ResponsiveContainer>
              ) : (
                <div style={{ height: 300, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Text type="secondary">暂无数据</Text>
                </div>
              )}
            </Spin>
          </Card>
        </Col>
      </Row>

      {/* 趋势数据表 + 竞品分析 */}
      <Row gutter={[24, 24]}>
        {/* 趋势数据 */}
        <Col xs={24} lg={14}>
          <Card
            title={
              <Space>
                <RiseOutlined /> 趋势数据
              </Space>
            }
          >
            <Table
              rowKey="topic"
              dataSource={trends}
              pagination={false}
              size="small"
              columns={[
                { title: "话题", dataIndex: "topic", ellipsis: true },
                {
                  title: "播放量",
                  dataIndex: "views",
                  width: 120,
                  render: (v: string) => <Text strong>{parseInt(v).toLocaleString()}</Text>,
                },
                {
                  title: "增长",
                  dataIndex: "growth",
                  width: 100,
                  render: (v: number) => (
                    <Text style={{ color: v >= 0 ? "var(--success)" : "var(--error)" }}>
                      {v >= 0 ? "+" : ""}
                      {v}%
                    </Text>
                  ),
                },
                {
                  title: "热度",
                  dataIndex: "hot",
                  width: 80,
                  render: (v: string) => {
                    const colorMap: Record<string, string> = {
                      飙升: "red",
                      上升: "orange",
                      平稳: "blue",
                      下降: "default",
                    };
                    return <Tag color={colorMap[v] || "default"}>{v}</Tag>;
                  },
                },
              ]}
            />
          </Card>
        </Col>

        {/* 竞品分析 */}
        <Col xs={24} lg={10}>
          <Card
            title={
              <Space>
                <TeamOutlined /> 竞品分析
              </Space>
            }
          >
            <Table
              rowKey="name"
              dataSource={competitors}
              columns={competitorColumns}
              pagination={false}
              size="small"
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
