import { useEffect, useState, useCallback } from "react";
import {
  Typography,
  Card,
  Input,
  Button,
  Space,
  Table,
  Tag,
  Select,
  InputNumber,
  List,
  Empty,
} from "antd";
import { useToast } from "../components/Toast";
import {
  SearchOutlined,
  PlusOutlined,
  ReloadOutlined,
  CheckCircleOutlined,
  SyncOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  createCrawlerTask,
  listCrawlerTasks,
} from "../api/client";
import type { CrawlerTaskResponse, CrawlerResult } from "../api/types";

const STATUS_CONFIG: Record<
  string,
  { color: string; icon: React.ReactNode; label: string }
> = {
  pending: {
    color: "default",
    icon: <ClockCircleOutlined />,
    label: "等待中",
  },
  running: {
    color: "processing",
    icon: <SyncOutlined spin />,
    label: "执行中",
  },
  succeeded: {
    color: "success",
    icon: <CheckCircleOutlined />,
    label: "已完成",
  },
  failed: {
    color: "error",
    icon: <CloseCircleOutlined />,
    label: "失败",
  },
};

const PLATFORM_OPTIONS = [
  { value: "douyin", label: "抖音" },
  { value: "xiaohongshu", label: "小红书" },
  { value: "wechat_channels", label: "视频号" },
];

export default function KeywordCrawlerPage() {
  const toast = useToast();
  const [keyword, setKeyword] = useState("");
  const [platform, setPlatform] = useState("douyin");
  const [maxResults, setMaxResults] = useState(10);
  const [tasks, setTasks] = useState<CrawlerTaskResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);

  const fetchTasks = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await listCrawlerTasks();
      setTasks(resp.items);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  const handleCreate = async () => {
    if (!keyword.trim()) {
      toast.warning("请输入关键词");
      return;
    }
    setCreating(true);
    try {
      await createCrawlerTask(keyword.trim(), platform, maxResults);
      toast.success("爬虫任务已创建");
      setKeyword("");
      await fetchTasks();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const columns: ColumnsType<CrawlerTaskResponse> = [
    {
      title: "任务ID",
      dataIndex: "task_id",
      width: 180,
      ellipsis: true,
    },
    {
      title: "关键词",
      dataIndex: "keyword",
      width: 150,
    },
    {
      title: "平台",
      dataIndex: "platform",
      width: 100,
      render: (v: string) => {
        const labels: Record<string, string> = {
          douyin: "抖音",
          xiaohongshu: "小红书",
          wechat_channels: "视频号",
        };
        return <Tag color="blue">{labels[v] || v}</Tag>;
      },
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (v: string) => {
        const cfg = STATUS_CONFIG[v] || STATUS_CONFIG.pending;
        return (
          <Tag color={cfg.color} icon={cfg.icon}>
            {cfg.label}
          </Tag>
        );
      },
    },
    {
      title: "结果数",
      dataIndex: "result_count",
      width: 80,
      render: (v: number) => <Tag color="green">{v}</Tag>,
    },
    {
      title: "最大抓取数",
      dataIndex: "max_results",
      width: 100,
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 180,
      render: (v: string | null) => (v ? new Date(v).toLocaleString("zh-CN") : "-"),
    },
    {
      title: "错误信息",
      dataIndex: "error_message",
      ellipsis: true,
      render: (v: string | null) =>
        v ? (
          <Typography.Text type="danger">{v}</Typography.Text>
        ) : (
          "-"
        ),
    },
  ];

  // Build expandable row content: show crawl results
  const expandedRowRender = (record: CrawlerTaskResponse) => {
    if (!record.results || record.results.length === 0) {
      return <Empty description="暂无抓取结果" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
    }
    return (
      <List
        size="small"
        dataSource={record.results}
        renderItem={(item: CrawlerResult, idx: number) => (
          <List.Item>
            <Space>
              <Tag>{idx + 1}</Tag>
              <Typography.Text strong>{item.title}</Typography.Text>
              <Typography.Text type="secondary">作者: {item.author}</Typography.Text>
              <Typography.Text type="secondary">
                点赞: {item.likes.toLocaleString()}
              </Typography.Text>
            </Space>
          </List.Item>
        )}
      />
    );
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4}>关键词爬虫</Typography.Title>

      {/* 创建任务卡片 */}
      <Card title="创建抓取任务">
        <Space wrap align="end">
          <div>
            <Typography.Text type="secondary" style={{ display: "block", marginBottom: 4 }}>
              关键词
            </Typography.Text>
            <Input
              placeholder="输入关键词，如：二手车、美食"
              prefix={<SearchOutlined />}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onPressEnter={handleCreate}
              style={{ width: 240 }}
              allowClear
            />
          </div>
          <div>
            <Typography.Text type="secondary" style={{ display: "block", marginBottom: 4 }}>
              目标平台
            </Typography.Text>
            <Select
              value={platform}
              onChange={setPlatform}
              style={{ width: 140 }}
              options={PLATFORM_OPTIONS}
            />
          </div>
          <div>
            <Typography.Text type="secondary" style={{ display: "block", marginBottom: 4 }}>
              最大结果数
            </Typography.Text>
            <InputNumber
              min={1}
              max={100}
              value={maxResults}
              onChange={(v) => setMaxResults(v || 10)}
              style={{ width: 100 }}
            />
          </div>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleCreate}
            loading={creating}
          >
            创建任务
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={fetchTasks}
            loading={loading}
          >
            刷新列表
          </Button>
        </Space>
      </Card>

      {/* 任务列表 */}
      <Card title={`抓取任务列表（共 ${tasks.length} 个）`}>
        <Table
          rowKey="task_id"
          columns={columns}
          dataSource={tasks}
          loading={loading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          size="middle"
          expandable={{
            expandedRowRender,
            rowExpandable: (record) =>
              record.results !== undefined && record.results.length > 0,
          }}
        />
      </Card>
    </Space>
  );
}
