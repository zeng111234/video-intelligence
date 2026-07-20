import { useState } from "react";
import {
  Typography,
  Card,
  Input,
  Button,
  Space,
  Table,
  Tag,
  Steps,
  message,
} from "antd";
import { ThunderboltOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { createPipeline, getPipeline } from "../api/client";
import type { PipelineResponse, PipelineStage } from "../api/types";

const STATUS_COLOR: Record<string, string> = {
  succeeded: "green",
  running: "blue",
  failed: "red",
  pending: "default",
};

export default function PipelinePage() {
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(false);
  const [pipeline, setPipeline] = useState<PipelineResponse | null>(null);

  const handleCreate = async () => {
    if (!keyword.trim()) {
      message.warning("请输入关键词");
      return;
    }
    setLoading(true);
    try {
      const resp = await createPipeline(keyword.trim());
      // 轮询获取最新状态
      const latest = await getPipeline(resp.run_id);
      setPipeline(latest);
      message.success("流水线已创建");
    } catch (err) {
      message.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const stageColumns: ColumnsType<PipelineStage> = [
    { title: "阶段", dataIndex: "stage", width: 160 },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (v: string) => (
        <Tag color={STATUS_COLOR[v] || "default"}>{v}</Tag>
      ),
    },
    { title: "任务 ID", dataIndex: "task_id", ellipsis: true },
    {
      title: "错误信息",
      dataIndex: "error_message",
      ellipsis: true,
      render: (v: string | null) => v || "-",
    },
  ];

  const currentStep = pipeline
    ? pipeline.stages.findIndex((s) => s.status === "running")
    : 0;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4}>批量生产流水线</Typography.Title>
      <Card>
        <Space>
          <Input
            placeholder="输入关键词，如：二手车、美食"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onPressEnter={handleCreate}
            style={{ width: 300 }}
          />
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            onClick={handleCreate}
            loading={loading}
          >
            创建流水线
          </Button>
        </Space>
      </Card>

      {pipeline && (
        <Card>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Space>
              <Typography.Text strong>运行 ID：</Typography.Text>
              <Typography.Text code>{pipeline.run_id}</Typography.Text>
              <Typography.Text strong>关键词：</Typography.Text>
              <Tag>{pipeline.keyword}</Tag>
              <Typography.Text strong>状态：</Typography.Text>
              <Tag color={STATUS_COLOR[pipeline.status] || "default"}>
                {pipeline.status}
              </Tag>
            </Space>

            {pipeline.stages.length > 0 && (
              <Steps
                current={currentStep >= 0 ? currentStep : pipeline.stages.length}
                size="small"
                items={pipeline.stages.map((s) => ({
                  title: s.stage,
                  status:
                    s.status === "succeeded"
                      ? "finish"
                      : s.status === "running"
                        ? "process"
                        : s.status === "failed"
                          ? "error"
                          : "wait",
                }))}
              />
            )}

            <Table
              rowKey="stage"
              columns={stageColumns}
              dataSource={pipeline.stages}
              pagination={false}
              size="small"
            />

            {pipeline.error_message && (
              <Typography.Text type="danger">
                {pipeline.error_message}
              </Typography.Text>
            )}
          </Space>
        </Card>
      )}
    </Space>
  );
}
