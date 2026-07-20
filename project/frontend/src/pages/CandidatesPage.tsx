import { useEffect, useState, useCallback } from "react";
import {
  Table,
  Input,
  Button,
  Space,
  Tag,
  Card,
  Typography,
  Select,
  Alert,
} from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import type { ColumnsType } from "antd/es/table";
import { searchCandidates } from "../api/client";
import type { CandidateItem } from "../api/types";
import { useToast } from "../components/Toast";
import { SkeletonTable } from "../components/SkeletonLoader";

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
  const toast = useToast();
  const [keyword, setKeyword] = useState("");
  const [limit, setLimit] = useState(10);
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [category, setCategory] = useState<string | undefined>();
  const [data, setData] = useState<CandidateItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const doSearch = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await searchCandidates(keyword, limit, platforms, category);
      setData(resp.items);
      setTotal(resp.total);
    } catch (err) {
      // 静默处理网络错误，避免 StrictMode 双重调用时显示两次错误
      if ((err as Error).message?.includes("网络连接失败")) {
        console.warn("后端服务未启动");
      } else {
        toast.error((err as Error).message);
      }
    } finally {
      setLoading(false);
    }
  }, [keyword, limit, platforms, category]);

  const categoryOptions = Array.from(
    new Set(data.map((item) => item.category).filter(Boolean)),
  ).map((value) => ({ value, label: value }));

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
    {
      title: "转写",
      width: 90,
      render: (_, record) => (
        <Link to={`/transcription?candidate=${encodeURIComponent(record.video_id)}`}>
          去确认
        </Link>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4}>爆火视频候选检索</Typography.Title>
      <Alert
        type="info"
        showIcon
        message="这是本地候选库检索"
        description="搜索只读取 SQLite 已入库候选，不暗示每次都会调用外部平台。候选跳转转写页后，仍需重新确认权利并上传文件或填写授权 MP4/MOV 直链；不会自动下载平台分享页。"
      />
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
          <Select
            mode="multiple"
            allowClear
            placeholder="平台筛选"
            value={platforms}
            onChange={setPlatforms}
            style={{ width: 240 }}
            options={Object.entries(PLATFORM_LABELS).map(([value, label]) => ({ value, label }))}
          />
          <Select
            allowClear
            placeholder="分类筛选"
            value={category}
            onChange={setCategory}
            style={{ width: 220 }}
            options={categoryOptions}
          />
          <Button type="primary" onClick={doSearch} loading={loading}>
            搜索
          </Button>
          <Typography.Text type="secondary">共 {total} 条结果</Typography.Text>
        </Space>
      </Card>
      <Card>
        {loading && data.length === 0 ? (
          <SkeletonTable rows={5} cols={7} />
        ) : (
          <Table
            rowKey="video_id"
            columns={columns}
            dataSource={data}
            loading={loading}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            size="middle"
          />
        )}
      </Card>
    </Space>
  );
}
