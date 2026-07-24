import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Input,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { ReloadOutlined, SettingOutlined } from "@ant-design/icons";
import { getAdminStatus, getPublishConfig, updatePublishConfig } from "../api/client";
import { useToast } from "../components/Toast";
import type { AdminStatusResponse, PublishConfigResponse } from "../api/types";

const PLATFORM_LABELS: Record<string, string> = {
  douyin: "抖音",
  kuaishou: "快手",
  xiaohongshu: "小红书",
  wechat_channels: "视频号",
};

type PlatformDraft = {
  mode: "manual" | "official";
  client_key: string;
  client_secret: string;
  redirect_uri: string;
};

const EMPTY_DRAFT: PlatformDraft = {
  mode: "manual",
  client_key: "",
  client_secret: "",
  redirect_uri: "",
};

const { Text } = Typography;

export default function AdminPage() {
  const toast = useToast();
  const [status, setStatus] = useState<AdminStatusResponse | null>(null);
  const [publishConfig, setPublishConfig] = useState<PublishConfigResponse | null>(null);
  const [drafts, setDrafts] = useState<Record<string, PlatformDraft>>({});
  const [loading, setLoading] = useState(false);
  const [savingPlatform, setSavingPlatform] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await getAdminStatus();
      setStatus(resp);
    } catch (err) {
      if ((err as Error).message?.includes("网络连接失败")) {
        console.warn("后端服务未启动");
      } else {
        toast.error((err as Error).message);
      }
    } finally {
      setLoading(false);
    }
  }, [toast]);

  const fetchPublishConfig = useCallback(async () => {
    try {
      const resp = await getPublishConfig();
      setPublishConfig(resp);
      setDrafts(
        Object.fromEntries(
          resp.platforms.map((platform) => [
            platform.platform,
            {
              ...EMPTY_DRAFT,
              mode: platform.mode === "official" ? "official" : "manual",
            },
          ]),
        ),
      );
    } catch (err) {
      toast.error((err as Error).message || "加载平台接入状态失败");
    }
  }, [toast]);

  useEffect(() => {
    void fetchStatus();
    void fetchPublishConfig();
  }, [fetchPublishConfig, fetchStatus]);

  const updateDraft = (platform: string, patch: Partial<PlatformDraft>) => {
    setDrafts((current) => ({
      ...current,
      [platform]: { ...(current[platform] || EMPTY_DRAFT), ...patch },
    }));
  };

  const savePlatform = async (platform: string) => {
    const draft = drafts[platform] || EMPTY_DRAFT;
    setSavingPlatform(platform);
    try {
      await updatePublishConfig(platform, {
        mode: draft.mode,
        client_key: draft.client_key.trim() || undefined,
        client_secret: draft.client_secret.trim() || undefined,
        redirect_uri: draft.redirect_uri.trim() || undefined,
      });
      toast.success(`${PLATFORM_LABELS[platform] || platform} 接入设置已保存`);
      await fetchPublishConfig();
    } catch (err) {
      toast.error((err as Error).message || "保存平台接入设置失败");
    } finally {
      setSavingPlatform(null);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Space>
        <Typography.Title level={4} style={{ margin: 0 }}>
          系统管理
        </Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={fetchStatus} loading={loading}>
          刷新
        </Button>
      </Space>

      {status && (
        <Card>
          <Descriptions column={2} bordered>
            <Descriptions.Item label="系统状态">
              <Tag color={status.status === "ok" ? "green" : "red"}>{status.status}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="版本">{status.version}</Descriptions.Item>
            <Descriptions.Item label="仓储类型">{status.repository_type}</Descriptions.Item>
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

      <Collapse
        items={[
          {
            key: "publish-integration",
            label: <Space><SettingOutlined /> 平台接入（高级）</Space>,
            children: (
              <Space direction="vertical" size={16} style={{ width: "100%" }}>
                <Alert
                  showIcon
                  type="info"
                  message="这里只给负责平台接入的管理员使用"
                  description="普通发布用户无需填写任何技术信息。当前系统仍会优先使用发布助手；只有真实官方适配器和平台权限都完成验收后，才会启用官方自动发布。"
                />
                {publishConfig?.platforms.map((platform) => {
                  const draft = drafts[platform.platform] || EMPTY_DRAFT;
                  const clientKey = platform.variables.find((item) => item.field === "client_key");
                  const clientSecret = platform.variables.find((item) => item.field === "client_secret");
                  const redirectUri = platform.variables.find((item) => item.field === "redirect_uri");
                  return (
                    <Card
                      size="small"
                      key={platform.platform}
                      title={PLATFORM_LABELS[platform.platform] || platform.display_name}
                      extra={<Tag color={platform.mode === "official" ? "blue" : "gold"}>
                        {platform.mode === "official" ? "官方模式预留" : "发布助手"}
                      </Tag>}
                    >
                      <Space direction="vertical" size={12} style={{ width: "100%" }}>
                        <Select
                          aria-label={`${PLATFORM_LABELS[platform.platform] || platform.platform} 发布方式`}
                          value={draft.mode}
                          options={[
                            { value: "manual", label: "发布助手（推荐）" },
                            { value: "official", label: "官方发布（待适配）" },
                          ]}
                          onChange={(mode: PlatformDraft["mode"]) => updateDraft(platform.platform, { mode })}
                        />
                        <Input
                          aria-label={`${PLATFORM_LABELS[platform.platform] || platform.platform} 应用 Client Key`}
                          placeholder={clientKey?.configured ? "应用 Client Key 已设置" : "应用 Client Key（可选）"}
                          value={draft.client_key}
                          onChange={(event) => updateDraft(platform.platform, { client_key: event.target.value })}
                        />
                        <Input.Password
                          aria-label={`${PLATFORM_LABELS[platform.platform] || platform.platform} 应用 Client Secret`}
                          placeholder={clientSecret?.configured ? "应用 Client Secret 已设置" : "应用 Client Secret（可选）"}
                          value={draft.client_secret}
                          onChange={(event) => updateDraft(platform.platform, { client_secret: event.target.value })}
                        />
                        {platform.platform === "douyin" && (
                          <Input
                            aria-label="抖音授权回调地址"
                            placeholder={redirectUri?.configured ? "HTTPS 授权回调地址已设置" : "HTTPS 授权回调地址"}
                            value={draft.redirect_uri}
                            onChange={(event) => updateDraft(platform.platform, { redirect_uri: event.target.value })}
                          />
                        )}
                        <Text type="secondary">
                          抖音扫码授权需要已在开放平台备案的 HTTPS 回调地址；保存时不会回显密钥或账号凭证。
                        </Text>
                        <Button
                          type="primary"
                          loading={savingPlatform === platform.platform}
                          onClick={() => savePlatform(platform.platform)}
                        >
                          保存接入设置
                        </Button>
                      </Space>
                    </Card>
                  );
                })}
              </Space>
            ),
          },
        ]}
      />
    </Space>
  );
}
