import { useEffect, useState } from "react";
import { Typography, Card, Table, Tag, Space, Button } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { listTasks } from "../api/client";
import type { TaskItem } from "../api/types";
import { useToast } from "../components/Toast";
import { SkeletonTable } from "../components/SkeletonLoader";

const STATUS_COLOR: Record<string, string> = {
  succeeded: "green",
  running: "blue",
  failed: "red",
  pending: "default",
  queued: "default",
  submitted: "blue",
  cancelled: "default",
  outcome_unknown: "orange",
  paused: "orange",
};

const STATUS_LABELS: Record<string, string> = {
  succeeded: "已完成",
  running: "处理中",
  failed: "失败",
  pending: "待处理",
  queued: "排队中",
  submitted: "已提交",
  cancelled: "已取消",
  outcome_unknown: "结果待核对",
  paused: "等待操作",
};

const KIND_LABELS: Record<string, string> = {
  search: "搜索",
  transcription: "转写",
  avatar: "数字人",
  copywriting: "文案",
  video_editing: "视频编辑",
  publishing: "发布",
};

export default function TasksPage() {
  const toast = useToast();
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const fetchTasks = async () => {
    setLoading(true);
    try {
      const resp = await listTasks();
      setTasks(resp.items);
      setTotal(resp.total);
    } catch (err) {
      if ((err as Error).message?.includes("网络连接失败")) {
        console.warn("后端服务未启动");
      } else {
        toast.error((err as Error).message);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTasks();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const columns: ColumnsType<TaskItem> = [
    {
      title: "任务类型",
      dataIndex: "kind",
      width: 120,
      render: (v: string) => (
        <Tag color="geekblue">{KIND_LABELS[v] || v}</Tag>
      ),
    },
    { title: "标题", dataIndex: "title", ellipsis: true },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (v: string) => (
        <Tag color={STATUS_COLOR[v] || "default"}>
          {STATUS_LABELS[v] || "状态待确认"}
        </Tag>
      ),
    },
    {
      title: "进度",
      dataIndex: "progress",
      width: 100,
      render: (v: number) => `${v}%`,
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 200,
      render: (v: string | null) => (v ? new Date(v).toLocaleString("zh-CN") : "-"),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Space>
        <Typography.Title level={4} style={{ margin: 0 }}>
          任务中心
        </Typography.Title>
        <Button
          icon={<ReloadOutlined />}
          onClick={fetchTasks}
          loading={loading}
        >
          刷新
        </Button>
      </Space>
      <Card>
        {loading && tasks.length === 0 ? (
          <SkeletonTable rows={5} cols={5} />
        ) : (
          <Table
            rowKey="task_id"
            columns={columns}
            dataSource={tasks}
            loading={loading}
            pagination={{ pageSize: 20, showTotal: () => `共 ${total} 条` }}
            size="middle"
          />
        )}
      </Card>
    </Space>
  );
}
