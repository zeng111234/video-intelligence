import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  ClockCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { createPipeline, listPipelines } from "../api/client";
import type { PipelineResponse } from "../api/types";
import { useToast } from "../components/Toast";

const { Title, Text } = Typography;

const STATUS_COLOR: Record<string, string> = {
  pending: "default",
  running: "processing",
  paused: "warning",
  succeeded: "success",
  partial: "warning",
  failed: "error",
};

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "已创建",
    running: "运行中",
    paused: "已暂停",
    succeeded: "已完成",
    partial: "部分完成",
    failed: "失败",
  };
  return labels[status] || status;
}

export default function PipelinePage() {
  const toast = useToast();
  const [keyword, setKeyword] = useState("");
  const [style, setStyle] = useState("engaging");
  const [runs, setRuns] = useState<PipelineResponse[]>([]);
  const [selected, setSelected] = useState<PipelineResponse | null>(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const items = await listPipelines();
      setRuns(items);
      if (selected) {
        setSelected(items.find((item) => item.run_id === selected.run_id) || null);
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [selected?.run_id, toast]);

  useEffect(() => {
    refresh();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo(
    () => runs.filter((item) => statusFilter === "all" || item.status === statusFilter),
    [runs, statusFilter],
  );

  const createRun = async () => {
    const trimmed = keyword.trim();
    if (!trimmed) {
      toast.warning("请输入关键词");
      return;
    }
    setCreating(true);
    try {
      const run = await createPipeline(trimmed, { style });
      setSelected(run);
      setKeyword("");
      toast.success("生产批次记录已创建");
      await refresh();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const columns: ColumnsType<PipelineResponse> = [
    { title: "批次ID", dataIndex: "run_id", width: 180, render: (value) => <Text code>{value}</Text> },
    { title: "关键词", dataIndex: "keyword", width: 160 },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (value: string) => <Tag color={STATUS_COLOR[value]}>{statusLabel(value)}</Tag>,
    },
    {
      title: "当前阶段",
      dataIndex: "current_stage",
      width: 140,
      render: (value: string | null) => value || "尚未开始",
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 180,
      render: (value: string | null) => (value ? new Date(value).toLocaleString("zh-CN") : "-"),
    },
    {
      title: "操作",
      width: 90,
      render: (_, record) => (
        <Button type="link" onClick={() => setSelected(record)}>
          详情
        </Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Title level={4} style={{ margin: 0 }}>
          <ThunderboltOutlined /> 生产流水线
        </Title>
        <Text type="secondary">列表、详情和刷新均读取 SQLite。当前按钮只创建生产批次记录，不宣称视频已经开始生成。</Text>
      </div>

      <Alert
        type="warning"
        showIcon
        message="能力边界"
        description="流水线记录可被创建并持久化；后续自动搜索、文案、数字人、剪辑和发布取决于对应服务配置与权限状态。"
      />

      <Card title={<Space><PlusOutlined /> 创建生产批次</Space>}>
        <Space wrap align="end">
          <div>
            <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>关键词</Text>
            <Input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              onPressEnter={createRun}
              placeholder="例如：二手车"
              style={{ width: 280 }}
              allowClear
            />
          </div>
          <div>
            <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>文案风格</Text>
            <Select
              value={style}
              onChange={setStyle}
              style={{ width: 160 }}
              options={[
                { value: "engaging", label: "吸引眼球" },
                { value: "professional", label: "专业权威" },
                { value: "storytelling", label: "故事叙述" },
              ]}
            />
          </div>
          <Button type="primary" loading={creating} onClick={createRun}>
            创建生产批次
          </Button>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={refresh}>
            刷新
          </Button>
        </Space>
      </Card>

      <Card title="批次详情">
        {selected ? (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Descriptions size="small" column={{ xs: 1, md: 3 }}>
              <Descriptions.Item label="批次ID">{selected.run_id}</Descriptions.Item>
              <Descriptions.Item label="关键词">{selected.keyword}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLOR[selected.status]}>{statusLabel(selected.status)}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="当前阶段">{selected.current_stage || "尚未开始"}</Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {selected.created_at ? new Date(selected.created_at).toLocaleString("zh-CN") : "-"}
              </Descriptions.Item>
              <Descriptions.Item label="错误">{selected.error_message || "-"}</Descriptions.Item>
            </Descriptions>
            {selected.stages.length > 0 ? (
              <Steps
                size="small"
                items={selected.stages.map((stage) => ({
                  title: stage.stage,
                  description: stage.error_message || stage.task_id || "",
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
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该批次还没有执行阶段记录" />
            )}
          </Space>
        ) : (
          <Empty description="选择一个批次查看详情" />
        )}
      </Card>

      <Card
        title={<Space><ClockCircleOutlined /> SQLite 批次列表</Space>}
        extra={
          <Select
            value={statusFilter}
            onChange={setStatusFilter}
            style={{ width: 140 }}
            options={[
              { value: "all", label: "全部状态" },
              { value: "pending", label: "已创建" },
              { value: "running", label: "运行中" },
              { value: "succeeded", label: "已完成" },
              { value: "failed", label: "失败" },
            ]}
          />
        }
      >
        <Table
          rowKey="run_id"
          columns={columns}
          dataSource={filtered}
          loading={loading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
        />
      </Card>
    </Space>
  );
}
