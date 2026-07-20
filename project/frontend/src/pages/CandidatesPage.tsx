import { useEffect, useState, useCallback } from "react";
import {
  Table,
  Input,
  Button,
  Space,
  Tag,
  Card,
  Typography,
  message,
  Select,
} from "antd";
import { SearchOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { searchCandidates } from "../api/client";
import type { CandidateItem } from "../api/types";

const PLATFORM_LABELS: Record<string, string> = {
  douyin: "抖音",
  xiaohongshu: "小红书",
  wechat_channels: "视频号",
  kuaishou: "快手",
};

const HEAT_COLORS: Record<string, string> = {
  S: "red",
  A: "orange",
  B: "blue",
};

export default function CandidatesPage() {
  const [keyword, setKeyword] = useState("");
  const [limit, setLimit] = useState(10);
  const [data, setData] = useState<CandidateItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const doSearch = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await searchCandidates(keyword, limit);
      setData(resp.items);
      setTotal(resp.total);
    } catch (err) {
      message.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [keyword, limit]);

  useEffect(() => {
    doSearch();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const columns: ColumnsType<CandidateItem> = [
    {
      title: "平台",
      dataIndex: "platform",
      width: 100,
      render: (v: string) => (
        <Tag color="blue">{PLATFORM_LABELS[v] || v}</Tag>
      ),
    },
    { title: "标题", dataIndex: "title", ellipsis: true },
    { title: "作者", dataIndex: "author_name", width: 120 },
    { title: "赛道", dataIndex: "category", width: 100 },
    {
      title: "热度分",
      dataIndex: "heat_score",
      width: 100,
      sorter: (a, b) => a.heat_score - b.heat_score,
      render: (v: number) => v.toFixed(1),
    },
    {
      title: "热度等级",
      dataIndex: "heat_level",
      width: 100,
      render: (v: string) => (
        <Tag color={HEAT_COLORS[v] || "default"}>{v}</Tag>
      ),
    },
    {
      title: "发布时间",
      dataIndex: "published_at",
      width: 180,
      render: (v: string | null) => (v ? new Date(v).toLocaleString("zh-CN") : "-"),
    },
    {
      title: "链接",
      dataIndex: "source_url",
      width: 80,
      render: (v: string | null) =>
        v ? (
          <a href={v} target="_blank" rel="noreferrer">
            查看
          </a>
        ) : (
          "-"
        ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4}>爆火视频候选检索</Typography.Title>
      <Card>
        <Space wrap>
          <Input
            placeholder="输入关键词搜索"
            prefix={<SearchOutlined />}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onPressEnter={doSearch}
            style={{ width: 280 }}
            allowClear
          />
          <Select
            value={limit}
            onChange={setLimit}
            style={{ width: 100 }}
            options={[5, 10, 20, 50].map((n) => ({ value: n, label: `${n} 条` }))}
          />
          <Button type="primary" onClick={doSearch} loading={loading}>
            搜索
          </Button>
          <Typography.Text type="secondary">共 {total} 条结果</Typography.Text>
        </Space>
      </Card>
      <Table
        rowKey="video_id"
        columns={columns}
        dataSource={data}
        loading={loading}
        pagination={{ pageSize: 10, showSizeChanger: false }}
        size="middle"
      />
    </Space>
  );
}
