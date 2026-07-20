import { useState } from "react";
import {
  Typography,
  Card,
  Input,
  Button,
  Space,
  Tag,
  Table,
  Progress,
  Descriptions,
  message,
} from "antd";
import { PlayCircleOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { createTranscription } from "../api/client";
import type { TranscriptionResponse, TranscriptSegment } from "../api/types";

export default function TranscriptionPage() {
  const [mediaName, setMediaName] = useState("");
  const [loading, setLoading] = useState(false);
  const [task, setTask] = useState<TranscriptionResponse | null>(null);

  const handleCreate = async () => {
    if (!mediaName.trim()) {
      message.warning("请输入媒体文件名");
      return;
    }
    setLoading(true);
    try {
      const resp = await createTranscription(mediaName.trim());
      setTask(resp);
      message.success("演示转写任务已创建");
    } catch (err) {
      message.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const segColumns: ColumnsType<TranscriptSegment> = [
    {
      title: "开始",
      dataIndex: "start",
      width: 100,
      render: (v: number) => `${v.toFixed(2)}s`,
    },
    {
      title: "结束",
      dataIndex: "end",
      width: 100,
      render: (v: number) => `${v.toFixed(2)}s`,
    },
    { title: "文本", dataIndex: "text", ellipsis: true },
    {
      title: "置信度",
      dataIndex: "confidence",
      width: 120,
      render: (v: number) => (
        <Progress
          percent={Math.round(v * 100)}
          size="small"
          status={v >= 0.75 ? "normal" : "exception"}
        />
      ),
    },
    {
      title: "需复核",
      dataIndex: "needs_review",
      width: 80,
      render: (v: boolean) =>
        v ? <Tag color="warning">是</Tag> : <Tag color="success">否</Tag>,
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4}>视频音轨转文案</Typography.Title>
      <Card>
        <Space>
          <Input
            placeholder="输入演示媒体文件名，如：demo.mp4"
            value={mediaName}
            onChange={(e) => setMediaName(e.target.value)}
            onPressEnter={handleCreate}
            style={{ width: 300 }}
          />
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            onClick={handleCreate}
            loading={loading}
          >
            创建演示转写
          </Button>
        </Space>
      </Card>

      {task && (
        <Card>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Descriptions column={2} bordered size="small">
              <Descriptions.Item label="任务 ID">{task.task_id}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={task.status === "succeeded" ? "green" : "blue"}>
                  {task.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="媒体">{task.media_name}</Descriptions.Item>
              <Descriptions.Item label="阶段">{task.stage}</Descriptions.Item>
              <Descriptions.Item label="进度">
                <Progress percent={task.progress} size="small" />
              </Descriptions.Item>
              <Descriptions.Item label="片段数">
                {task.segments.length}
              </Descriptions.Item>
            </Descriptions>

            {task.segments.length > 0 && (
              <Table
                rowKey={(_, i) => String(i)}
                columns={segColumns}
                dataSource={task.segments}
                pagination={false}
                size="small"
                scroll={{ y: 400 }}
              />
            )}

            {task.error_message && (
              <Typography.Text type="danger">{task.error_message}</Typography.Text>
            )}
          </Space>
        </Card>
      )}
    </Space>
  );
}
