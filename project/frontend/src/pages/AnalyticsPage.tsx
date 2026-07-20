/**
 * 深度分析页面 - Pro 功能
 * 提供视频数据分析、趋势洞察、竞品对比等
 */
import { useState } from "react";
import { Card, Row, Col, Statistic, Select, Table, Tag, Progress, Space, Typography } from "antd";
import {
  RiseOutlined,
  FallOutlined,
  PlayCircleOutlined,
  EyeOutlined,
  LikeOutlined,
  ShareAltOutlined,
  BarChartOutlined,
  LineChartOutlined,
  PieChartOutlined,
} from "@ant-design/icons";

const { Title, Text } = Typography;
const { Option } = Select;

/** 模拟趋势数据 */
const TREND_DATA = [
  { key: "1", topic: "二手车测评", views: "125.8万", growth: 23.5, hot: "飙升" },
  { key: "2", topic: "汽车改装", views: "89.2万", growth: 15.2, hot: "上升" },
  { key: "3", topic: "新手买车攻略", views: "67.4万", growth: -5.3, hot: "下降" },
  { key: "4", topic: "新能源对比", views: "156.1万", growth: 45.8, hot: "飙升" },
  { key: "5", topic: "汽车保养技巧", views: "42.3万", growth: 8.1, hot: "平稳" },
];

/** 模拟竞品数据 */
const COMPETITOR_DATA = [
  { key: "1", name: "李老司讲车", fans: "320万", avgViews: "45.2万", engagement: 8.5 },
  { key: "2", name: "汽车之家", fans: "1200万", avgViews: "120万", engagement: 6.2 },
  { key: "3", name: "懂车帝", fans: "890万", avgViews: "85万", engagement: 7.1 },
  { key: "4", name: "二手车小胖", fans: "156万", avgViews: "28.5万", engagement: 9.3 },
];

const trendColumns = [
  { title: "话题", dataIndex: "topic", key: "topic" },
  { title: "播放量", dataIndex: "views", key: "views" },
  {
    title: "增长率",
    dataIndex: "growth",
    key: "growth",
    render: (val: number) => (
      <span style={{ color: val >= 0 ? "#10b981" : "#ef4444" }}>
        {val >= 0 ? <RiseOutlined /> : <FallOutlined />} {Math.abs(val)}%
      </span>
    ),
  },
  {
    title: "热度",
    dataIndex: "hot",
    key: "hot",
    render: (val: string) => {
      const color = val === "飙升" ? "red" : val === "上升" ? "orange" : val === "下降" ? "blue" : "default";
      return <Tag color={color}>{val}</Tag>;
    },
  },
];

const competitorColumns = [
  { title: "账号", dataIndex: "name", key: "name" },
  { title: "粉丝", dataIndex: "fans", key: "fans" },
  { title: "平均播放", dataIndex: "avgViews", key: "avgViews" },
  {
    title: "互动率",
    dataIndex: "engagement",
    key: "engagement",
    render: (val: number) => (
      <Space>
        <Progress percent={val * 10} size="small" style={{ width: 80 }} showInfo={false} />
        <Text>{val}%</Text>
      </Space>
    ),
  },
];

export default function AnalyticsPage() {
  const [timeRange, setTimeRange] = useState("7d");

  return (
    <div>
      {/* 页面头部 */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>
            <BarChartOutlined /> 深度分析
          </Title>
          <Text type="secondary">Pro 专属 - 数据驱动的内容策略</Text>
        </div>
        <Select value={timeRange} onChange={setTimeRange} style={{ width: 120 }}>
          <Option value="7d">近 7 天</Option>
          <Option value="30d">近 30 天</Option>
          <Option value="90d">近 90 天</Option>
        </Select>
      </div>

      {/* 数据概览 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="总播放量"
              value={2856.3}
              suffix="万"
              prefix={<PlayCircleOutlined style={{ color: "#6366f1" }} />}
              valueStyle={{ color: "#6366f1" }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="总观看时长"
              value={1847.2}
              suffix="小时"
              prefix={<EyeOutlined style={{ color: "#10b981" }} />}
              valueStyle={{ color: "#10b981" }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="互动率"
              value={7.8}
              suffix="%"
              prefix={<LikeOutlined style={{ color: "#f59e0b" }} />}
              valueStyle={{ color: "#f59e0b" }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="分享次数"
              value={12580}
              prefix={<ShareAltOutlined style={{ color: "#ef4444" }} />}
              valueStyle={{ color: "#ef4444" }}
            />
          </Card>
        </Col>
      </Row>

      {/* 趋势分析 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} lg={14}>
          <Card
            title={
              <Space>
                <LineChartOutlined /> 热门话题趋势
              </Space>
            }
          >
            <Table
              columns={trendColumns}
              dataSource={TREND_DATA}
              pagination={false}
              size="small"
            />
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card
            title={
              <Space>
                <PieChartOutlined /> 内容类型分布
              </Space>
            }
          >
            <div style={{ padding: "20px 0" }}>
              {[
                { label: "测评类", percent: 35, color: "#6366f1" },
                { label: "教程类", percent: 28, color: "#10b981" },
                { label: "Vlog类", percent: 20, color: "#f59e0b" },
                { label: "其他", percent: 17, color: "#94a3b8" },
              ].map((item) => (
                <div key={item.label} style={{ marginBottom: 16 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <Text>{item.label}</Text>
                    <Text strong>{item.percent}%</Text>
                  </div>
                  <Progress percent={item.percent} showInfo={false} strokeColor={item.color} />
                </div>
              ))}
            </div>
          </Card>
        </Col>
      </Row>

      {/* 竞品分析 */}
      <Card
        title={
          <Space>
            <BarChartOutlined /> 竞品账号分析
          </Space>
        }
      >
        <Table columns={competitorColumns} dataSource={COMPETITOR_DATA} pagination={false} size="small" />
      </Card>
    </div>
  );
}
