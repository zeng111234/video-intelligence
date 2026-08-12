import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Card, Collapse, Space, Tag, Typography } from "antd";
import {
  CheckCircleOutlined,
  CloudServerOutlined,
  ReloadOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { getServerStatus } from "../api/client";
import type { ServerCapabilityState, ServerStatusResponse } from "../api/types";

const { Text } = Typography;

const CAPABILITIES: Array<{
  key: keyof Pick<ServerStatusResponse, "copywriting" | "transcription" | "video_editor" | "avatar">;
  label: string;
}> = [
  { key: "copywriting", label: "AI 文案" },
  { key: "transcription", label: "云端转写" },
  { key: "video_editor", label: "云端剪辑" },
  { key: "avatar", label: "数字人" },
];

function capabilityReady(value: ServerCapabilityState): boolean {
  return value.live_ready ?? value.enabled ?? false;
}

export default function ServerStatusSection() {
  const [status, setStatus] = useState<ServerStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setStatus(await getServerStatus());
    } catch (caught) {
      setStatus(null);
      setError((caught as Error).message || "暂时无法连接公司服务");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const unavailableCount = useMemo(
    () =>
      status
        ? CAPABILITIES.filter(({ key }) => !capabilityReady(status[key])).length
        : 0,
    [status],
  );
  const companyConnected = status?.service === "ready";

  return (
    <Card
      className="admin-server-status-card"
      title={
        <span className="admin-section-title">
          <CloudServerOutlined />
          公司服务状态
        </span>
      }
      extra={
        <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => void refresh()}>
          刷新
        </Button>
      }
    >
      {error ? (
        <Space>
          <WarningOutlined className="admin-server-warning" />
          <Text type="danger">{error}</Text>
        </Space>
      ) : (
        <Space wrap size={[8, 8]}>
          <Tag
            icon={companyConnected ? <CheckCircleOutlined /> : <WarningOutlined />}
            color={companyConnected ? "success" : "warning"}
          >
            {companyConnected ? "公司服务已连接" : "本机功能可用 · 公司服务器待配置"}
          </Tag>
          <Tag color="green">素材发现由本机完成 · 不扣积分</Tag>
          {status &&
            CAPABILITIES.map(({ key, label }) => (
              <Tag key={key} color={capabilityReady(status[key]) ? "success" : "default"}>
                {label} · {capabilityReady(status[key]) ? "可用" : companyConnected ? "未开启" : "待服务器"}
              </Tag>
            ))}
        </Space>
      )}
      {status && unavailableCount > 0 && (
        <Collapse
          ghost
          className="admin-server-status-details"
          items={[
            {
              key: "details",
              label: companyConnected
                ? `${unavailableCount} 项收费能力尚未开启（不影响本地免费功能）`
                : "收费功能将在公司服务器配置后开启",
              children: (
                <Space direction="vertical" size={6}>
                  {CAPABILITIES.filter(({ key }) => !capabilityReady(status[key])).map(
                    ({ key, label }) => (
                      <Text key={key} type="secondary">
                        {label}：还需完成 {status[key].missing_configuration?.length || 1} 项服务器配置
                      </Text>
                    ),
                  )}
                </Space>
              ),
            },
          ]}
        />
      )}
    </Card>
  );
}
