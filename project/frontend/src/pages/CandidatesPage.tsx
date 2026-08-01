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
  Tooltip,
} from "antd";
import { SearchOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";
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

function keywordRecordLabel(value: string) {
  return value.startsWith("关键词/") ? value.slice("关键词/".length) : value;
}

export default function CandidatesPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const pageSize = 10;
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [category, setCategory] = useState<string | undefined>();
  const [data, setData] = useState<CandidateItem[]>([]);
  const [total, setTotal] = useState(0);
  const [categoryOptions, setCategoryOptions] = useState<{ value: string; count: number }[]>([]);
  const [loading, setLoading] = useState(false);

  const doSearch = useCallback(async (targetPage = 1) => {
    setLoading(true);
    try {
      const resp = await searchCandidates(keyword, pageSize, platforms, category, targetPage);
      setData(resp.items);
      setTotal(resp.total);
      setCategoryOptions(
        resp.category_options?.length
          ? resp.category_options
          : Array.from(new Set(resp.items.map((item) => item.category)))
            .filter(Boolean)
            .map((value) => ({
              value,
              count: resp.items.filter((item) => item.category === value).length,
            })),
      );
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
  }, [keyword, pageSize, platforms, category]);

  const goToTranscription = (item: CandidateItem) => {
    const params = new URLSearchParams({
      candidate: item.video_id,
      title: item.title,
    });
    if (item.platform === "douyin" && item.source_url) {
      params.set("share_text", item.source_url);
    }
    navigate(`/transcription?${params.toString()}`);
  };

  useEffect(() => {
    setPage(1);
    doSearch(1);
  }, [doSearch]);

  const startSearch = () => {
    setPage(1);
    doSearch(1);
  };

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
    { title: "分类", dataIndex: "category", width: 120 },
    {
      title: "热度分",
      dataIndex: "heat_score",
      width: 100,
      sorter: (a, b) => a.heat_score - b.heat_score,
      render: (v: number) => v.toFixed(1),
    },
    {
      title: "时间",
      dataIndex: "published_at",
      width: 210,
      render: (v: string | null, item) => {
        const published = v ? new Date(v).toLocaleString("zh-CN") : "-";
        if (item.publication_time_state === "sampled_fallback") {
          const observed = item.observed_at ? new Date(item.observed_at).toLocaleString("zh-CN") : "-";
          return <Tooltip title="供应商未返回可核验的发布时间，此处不能当作作品发布日期。"><Typography.Text type="secondary">发布时间未知<br />采样于 {observed}</Typography.Text></Tooltip>;
        }
        if (!item.publication_time_state) {
          return <Tooltip title="运行中的后端尚未返回时间来源。重启 2001 后会区分真实发布时间与采样时间。"><Typography.Text type="warning">时间来源待后端更新</Typography.Text></Tooltip>;
        }
        return <span>发布于 {published}</span>;
      },
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
      title: "操作",
      width: 100,
      render: (_, item) => (
        <Tooltip title="进入转写页后仍需确认内容处理权；不会自动下载或创建任务。">
          <Button size="small" type="primary" onClick={() => goToTranscription(item)}>
            文案转写
          </Button>
        </Tooltip>
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
            onPressEnter={startSearch}
            style={{ width: 280 }}
            allowClear
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
            showSearch
            allowClear
            optionFilterProp="label"
            placeholder="选择已有关键词"
            value={category}
            onChange={setCategory}
            style={{ width: 240 }}
            options={categoryOptions.map((item) => ({
              value: item.value,
              label: `${keywordRecordLabel(item.value)} (${item.count})`,
            }))}
          />
          <Button type="primary" onClick={startSearch} loading={loading}>
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
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: false,
              showTotal: (count) => `共 ${count} 条`,
              onChange: (nextPage) => {
                setPage(nextPage);
                doSearch(nextPage);
              },
            }}
            size="middle"
          />
        )}
      </Card>
    </Space>
  );
}
