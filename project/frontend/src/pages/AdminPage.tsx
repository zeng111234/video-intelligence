import { useEffect, useState } from "react";
import { Typography, Card, Descriptions, Tag, Space, Button } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { getAdminStatus } from "../api/client";
import { useToast } from "../components/Toast";
import type { AdminStatusResponse } from "../api/types";

export default function AdminPage() {
  const toast = useToast();
  const [status, setStatus] = useState<AdminStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchStatus = async () => {
    setLoading(true);
    try {
      const resp = await getAdminStatus();
      setStatus(resp);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatus();
  }, []);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Space>
        <Typography.Title level={4} style={{ margin: 0 }}>
          系统管理
        </Typography.Title>
        <Button
          icon={<ReloadOutlined />}
          onClick={fetchStatus}
          loading={loading}
        >
          刷新
        </Button>
      </Space>

      {status && (
        <Card>
          <Descriptions column={2} bordered>
            <Descriptions.Item label="系统状态">
              <Tag color={status.status === "ok" ? "green" : "red"}>
                {status.status}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="版本">{status.version}</Descriptions.Item>
            <Descriptions.Item label="仓储类型">
              {status.repository_type}
            </Descriptions.Item>
            <Descriptions.Item label="数据库路径">
              <Typography.Text code copyable>
                {status.database_path || "无"}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="候选数量">
              <Tag color="blue">{status.candidate_count}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="任务数量">
              <Tag color="purple">{status.task_count}</Tag>
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}
    </Space>
  );
}
