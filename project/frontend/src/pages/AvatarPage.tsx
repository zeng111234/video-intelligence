/**
 * 数字人口播生成页面。
 * 首版只开放公共形象 + 文本驱动，所有任务状态均来自 FastAPI。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  InputNumber,
  List,
  Progress,
  Row,
  Select,
  Slider,
  Space,
  Tag,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  CloudSyncOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RocketOutlined,
  UserOutlined,
} from "@ant-design/icons";
import {
  createAvatarJob,
  downloadAvatarJobMedia,
  getAvatarCapabilities,
  getAvatarJob,
  listAvatarAssets,
  listAvatarJobs,
} from "../api/client";
import type { AvatarAsset, AvatarCapability, AvatarJob } from "../api/types";
import { useToast } from "../components/Toast";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

const TARGET_PLATFORMS = [
  { label: "抖音", value: "douyin" },
  { label: "快手", value: "kuaishou" },
  { label: "视频号", value: "wechat_channels" },
  { label: "小红书", value: "xiaohongshu" },
];

const TERMINAL_STATUSES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "outcome_unknown",
]);

function buildIdempotencyKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `avatar-${crypto.randomUUID()}`;
  }
  return `avatar-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function statusColor(status: string) {
  if (status === "succeeded") return "success";
  if (["failed", "cancelled", "outcome_unknown"].includes(status)) return "error";
  if (status === "running") return "processing";
  return "default";
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    submitted: "已提交",
    running: "生成中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
    outcome_unknown: "待核对",
  };
  return labels[status] || status;
}

export default function AvatarPage() {
  const toast = useToast();
  const [capability, setCapability] = useState<AvatarCapability | null>(null);
  const [assets, setAssets] = useState<AvatarAsset[]>([]);
  const [jobs, setJobs] = useState<AvatarJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const [scriptText, setScriptText] = useState("");
  const [avatarId, setAvatarId] = useState<string>();
  const [voiceId, setVoiceId] = useState<string>();
  const [targetSeconds, setTargetSeconds] = useState(45);
  const [speechRate, setSpeechRate] = useState(1);
  const [targetPlatforms, setTargetPlatforms] = useState<string[]>(["douyin"]);

  const avatars = useMemo(
    () => assets.filter((item) => item.kind === "avatar"),
    [assets],
  );
  const voices = useMemo(
    () => assets.filter((item) => item.kind === "voice"),
    [assets],
  );
  const activeJob = useMemo(
    () => jobs.find((item) => item.task_id === activeJobId) || jobs[0] || null,
    [activeJobId, jobs],
  );

  const estimatedCost = useMemo(() => {
    if (!capability?.estimated_cost_cny || !capability.estimated_seconds) return null;
    return (
      capability.estimated_cost_cny *
      (targetSeconds / capability.estimated_seconds)
    );
  }, [capability, targetSeconds]);

  const refresh = useCallback(async () => {
    const [nextCapability, nextAssets, nextJobs] = await Promise.all([
      getAvatarCapabilities(),
      listAvatarAssets(),
      listAvatarJobs(),
    ]);
    setCapability(nextCapability);
    setAssets(nextAssets);
    setJobs(nextJobs);
    setAvatarId((current) => current || nextAssets.find((item) => item.kind === "avatar")?.asset_id);
    setVoiceId((current) => current || nextAssets.find((item) => item.kind === "voice")?.asset_id);
    setActiveJobId((current) => current || nextJobs[0]?.task_id || null);
  }, []);

  useEffect(() => {
    refresh()
      .catch((error) => toast.error(error.message || "读取数字人服务失败"))
      .finally(() => setLoading(false));
  }, [refresh, toast]);

  useEffect(() => {
    if (!activeJob || TERMINAL_STATUSES.has(activeJob.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        const latest = await getAvatarJob(activeJob.task_id);
        setJobs((items) =>
          items.map((item) => (item.task_id === latest.task_id ? latest : item)),
        );
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "刷新任务状态失败");
      }
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [activeJob, toast]);

  const handleSubmit = useCallback(async () => {
    if (!capability?.enabled) {
      toast.warning("数字人服务尚未可用，请先配置供应商。");
      return;
    }
    if (!avatarId || !voiceId) {
      toast.warning("缺少可用公共形象或音色。");
      return;
    }
    if (!scriptText.trim()) {
      toast.warning("请输入口播文案。");
      return;
    }
    setSubmitting(true);
    try {
      const job = await createAvatarJob({
        script_text: scriptText.trim(),
        avatar_id: avatarId,
        voice_id: voiceId,
        target_seconds: targetSeconds,
        speech_rate: speechRate,
        aspect_ratio: "9:16",
        resolution: "1080x1920",
        publish_mode: "manual",
        target_platforms: targetPlatforms,
        idempotency_key: buildIdempotencyKey(),
      });
      setJobs((items) => [job, ...items.filter((item) => item.task_id !== job.task_id)]);
      setActiveJobId(job.task_id);
      toast.success(job.is_mock ? "演示任务已创建" : "数字人任务已提交");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "提交数字人任务失败");
    } finally {
      setSubmitting(false);
    }
  }, [
    avatarId,
    capability,
    scriptText,
    speechRate,
    targetPlatforms,
    targetSeconds,
    toast,
    voiceId,
  ]);

  const handleDownload = useCallback(async (job: AvatarJob) => {
    try {
      const blob = await downloadAvatarJobMedia(job.task_id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `avatar_${job.task_id}.mp4`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "下载失败");
    }
  }, [toast]);

  const serviceUnavailable = Boolean(capability && !capability.enabled);

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          <UserOutlined /> 数字人口播生成
        </Title>
        <Text type="secondary">
          公共数字人 + 文本驱动 + 真实任务状态；成片后再生成四平台发布包。
        </Text>
      </div>

      {capability && (
        <Alert
          style={{ marginBottom: 16 }}
          type={serviceUnavailable ? "warning" : capability.mode === "sandbox" ? "info" : "success"}
          showIcon
          message={
            serviceUnavailable
              ? "数字人供应商未配置，暂不能提交真实任务"
              : capability.mode === "sandbox"
                ? "当前为演示模式：可验证任务闭环，但不会生成真实成片"
                : `${capability.display_name} 已可用`
          }
          description={
            serviceUnavailable && capability.missing_configuration.length
              ? `缺少配置：${capability.missing_configuration.join("、")}`
              : "首版只开放公共形象和文本转口播。摄像头录制、照片上传、录音上传会在供应商能力接通后再开放。"
          }
        />
      )}

      <Row gutter={[24, 24]}>
        <Col xs={24} lg={12}>
          <Card title={<Space><RocketOutlined /> 生成配置</Space>} loading={loading}>
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              <div>
                <Text strong>口播文案</Text>
                <TextArea
                  rows={6}
                  value={scriptText}
                  onChange={(event) => setScriptText(event.target.value)}
                  maxLength={capability?.max_script_chars || 240}
                  showCount
                  placeholder="输入数字人要说的内容，建议 15–60 秒内说完。"
                  style={{ marginTop: 8 }}
                />
              </div>

              <Row gutter={12}>
                <Col span={12}>
                  <Text strong>公共形象</Text>
                  <Select
                    value={avatarId}
                    onChange={setAvatarId}
                    options={avatars.map((item) => ({ label: item.name, value: item.asset_id }))}
                    placeholder="无可用形象"
                    style={{ width: "100%", marginTop: 8 }}
                  />
                </Col>
                <Col span={12}>
                  <Text strong>音色</Text>
                  <Select
                    value={voiceId}
                    onChange={setVoiceId}
                    options={voices.map((item) => ({ label: item.name, value: item.asset_id }))}
                    placeholder="无可用音色"
                    style={{ width: "100%", marginTop: 8 }}
                  />
                </Col>
              </Row>

              <Row gutter={12}>
                <Col span={12}>
                  <Text strong>目标时长</Text>
                  <InputNumber
                    min={15}
                    max={60}
                    value={targetSeconds}
                    onChange={(value) => setTargetSeconds(Number(value || 45))}
                    addonAfter="秒"
                    style={{ width: "100%", marginTop: 8 }}
                  />
                </Col>
                <Col span={12}>
                  <Text strong>输出规格</Text>
                  <Input value="1080x1920 · 9:16" disabled style={{ marginTop: 8 }} />
                </Col>
              </Row>

              <div>
                <Text strong>语速：{speechRate.toFixed(1)}x</Text>
                <Slider
                  value={speechRate}
                  onChange={setSpeechRate}
                  min={0.8}
                  max={1.2}
                  step={0.1}
                />
              </div>

              <div>
                <Text strong>目标平台</Text>
                <Select
                  mode="multiple"
                  value={targetPlatforms}
                  onChange={setTargetPlatforms}
                  options={TARGET_PLATFORMS}
                  style={{ width: "100%", marginTop: 8 }}
                />
              </div>

              <Card size="small" style={{ background: "#f8fafc" }}>
                <Space direction="vertical" size={4}>
                  <Text>供应商：{capability?.display_name || "读取中"}</Text>
                  <Text>
                    预计费用：
                    {estimatedCost === null ? "待供应商返回" : `¥${estimatedCost.toFixed(2)}`}
                  </Text>
                  <Text type="secondary">自动发布默认关闭，成片后先生成发布包和人工检查清单。</Text>
                </Space>
              </Card>

              <Button
                type="primary"
                icon={<RocketOutlined />}
                size="large"
                block
                loading={submitting}
                disabled={serviceUnavailable || !avatarId || !voiceId}
                onClick={handleSubmit}
              >
                提交数字人口播任务
              </Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card
            title={<Space><PlayCircleOutlined /> 当前任务</Space>}
            extra={<Button size="small" icon={<ReloadOutlined />} onClick={refresh}>刷新</Button>}
            style={{ marginBottom: 24 }}
          >
            {activeJob ? (
              <Space direction="vertical" size={16} style={{ width: "100%" }}>
                <Space wrap>
                  <Tag color={statusColor(activeJob.status)}>
                    {statusLabel(activeJob.status)}
                  </Tag>
                  {activeJob.is_mock && <Tag color="blue">演示</Tag>}
                  <Text type="secondary">{activeJob.provider_name}</Text>
                </Space>
                <Progress
                  percent={activeJob.progress}
                  status={activeJob.status === "failed" ? "exception" : "active"}
                />
                <Paragraph style={{ marginBottom: 0 }}>{activeJob.stage}</Paragraph>
                {activeJob.error_message && (
                  <Alert
                    type="error"
                    showIcon
                    icon={<ExclamationCircleOutlined />}
                    message={activeJob.error_message}
                  />
                )}
                {activeJob.result_url ? (
                  <Button
                    type="primary"
                    icon={<DownloadOutlined />}
                    onClick={() => handleDownload(activeJob)}
                  >
                    下载真实成片
                  </Button>
                ) : activeJob.status === "succeeded" ? (
                  <Alert type="warning" showIcon message="任务已成功，但媒体尚未转存或不可下载。" />
                ) : (
                  <Alert
                    type="info"
                    showIcon
                    icon={<CloudSyncOutlined />}
                    message="等待供应商完成后，这里会出现真实成片下载。"
                  />
                )}
              </Space>
            ) : (
              <Empty description="暂无真实任务" />
            )}
          </Card>

          <Card title="真实任务历史">
            {jobs.length ? (
              <List
                dataSource={jobs}
                renderItem={(item) => (
                  <List.Item
                    actions={[
                      <Button key="view" type="link" onClick={() => setActiveJobId(item.task_id)}>
                        查看
                      </Button>,
                      item.result_url ? (
                        <Button key="download" type="link" icon={<DownloadOutlined />} onClick={() => handleDownload(item)}>
                          下载
                        </Button>
                      ) : null,
                    ].filter(Boolean)}
                  >
                    <List.Item.Meta
                      avatar={<CheckCircleOutlined style={{ color: item.status === "succeeded" ? "#10b981" : "#64748b" }} />}
                      title={
                        <Space>
                          <Text strong>{item.title}</Text>
                          <Tag color={statusColor(item.status)}>{statusLabel(item.status)}</Tag>
                          {item.is_mock && <Tag>演示</Tag>}
                        </Space>
                      }
                      description={`${new Date(item.created_at).toLocaleString()} · ${item.avatar_name} · ${item.voice_name}`}
                    />
                  </List.Item>
                )}
              />
            ) : (
              <Empty description="提交任务后会显示真实历史记录" />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
