import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Empty,
  Input,
  Modal,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Steps,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  FileTextOutlined,
  LinkOutlined,
  ReloadOutlined,
  RocketOutlined,
  SendOutlined,
  SettingOutlined,
  SyncOutlined,
  TagOutlined,
  UploadOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  createPublishBatch,
  getPublishConfig,
  importEditedVideoToPublish,
  listPublishAssets,
  listPublishBatches,
  listPublishPlatforms,
  preflightPublish,
  recordManualPublishResult,
  retryPublishTask,
  updatePublishConfig,
  uploadPublishAsset,
} from "../api/client";
import type {
  PublishAsset,
  PublishConfigResponse,
  PublishPlatformCapability,
  PublishPreflightResponse,
  PublishResponse,
} from "../api/types";
import { useToast } from "../components/Toast";
import { SkeletonCard } from "../components/SkeletonLoader";

const { Title, Text, Link } = Typography;
const { TextArea } = Input;

const PLATFORM_LABELS: Record<string, string> = {
  douyin: "抖音",
  kuaishou: "快手",
  xiaohongshu: "小红书",
  wechat_channels: "视频号",
};

const PLATFORM_ENV_KEYS: Record<string, string[]> = {
  douyin: ["PUBLISH_DOUYIN_MODE", "PUBLISH_DOUYIN_ACCESS_TOKEN", "PUBLISH_DOUYIN_OPEN_ID"],
  kuaishou: ["PUBLISH_KUAISHOU_MODE", "PUBLISH_KUAISHOU_ACCESS_TOKEN", "PUBLISH_KUAISHOU_OPEN_ID"],
  xiaohongshu: [
    "PUBLISH_XIAOHONGSHU_MODE",
    "PUBLISH_XIAOHONGSHU_ACCESS_TOKEN",
    "PUBLISH_XIAOHONGSHU_OPEN_ID",
  ],
  wechat_channels: [
    "PUBLISH_WECHAT_CHANNELS_MODE",
    "PUBLISH_WECHAT_CHANNELS_ACCESS_TOKEN",
    "PUBLISH_WECHAT_CHANNELS_OPEN_ID",
  ],
};

type PlatformConfigDraft = {
  mode: "manual" | "official";
  access_token: string;
  open_id: string;
  client_key: string;
  client_secret: string;
};

const EMPTY_CONFIG_DRAFT: PlatformConfigDraft = {
  mode: "manual",
  access_token: "",
  open_id: "",
  client_key: "",
  client_secret: "",
};

const CONFIG_INPUTS: Array<{
  field: keyof PlatformConfigDraft;
  label: string;
  password?: boolean;
}> = [
  { field: "access_token", label: "Access Token", password: true },
  { field: "open_id", label: "Open ID" },
  { field: "client_key", label: "Client Key" },
  { field: "client_secret", label: "Client Secret", password: true },
];

const STATUS_MAP: Record<
  string,
  { color: string; icon: React.ReactNode; label: string }
> = {
  pending: { color: "default", icon: <ClockCircleOutlined />, label: "等待中" },
  manual_ready: {
    color: "warning",
    icon: <ClockCircleOutlined />,
    label: "待人工发布",
  },
  uploading: { color: "processing", icon: <SyncOutlined spin />, label: "上传中" },
  processing: { color: "processing", icon: <SyncOutlined spin />, label: "处理中" },
  succeeded: { color: "success", icon: <CheckCircleOutlined />, label: "已确认发布" },
  failed: { color: "error", icon: <CloseCircleOutlined />, label: "失败" },
  outcome_unknown: {
    color: "default",
    icon: <ClockCircleOutlined />,
    label: "结果待确认",
  },
};

function platformLabel(platform: string) {
  return PLATFORM_LABELS[platform] || platform;
}

function modeLabel(mode: string) {
  if (mode === "manual") return "人工兜底";
  if (mode === "official_unimplemented") return "官方接口待联调";
  if (mode === "disabled") return "未启用";
  return mode;
}

export default function PublishPage() {
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const editTaskId = searchParams.get("editTask")?.trim() || "";
  const [workflowStep, setWorkflowStep] = useState<"config" | "publish">("config");
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [availablePlatforms, setAvailablePlatforms] = useState<PublishPlatformCapability[]>([]);
  const [publishConfig, setPublishConfig] = useState<PublishConfigResponse | null>(null);
  const [configDrafts, setConfigDrafts] = useState<Record<string, PlatformConfigDraft>>({});
  const [assets, setAssets] = useState<PublishAsset[]>([]);
  const [videoPath, setVideoPath] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [preflight, setPreflight] = useState<PublishPreflightResponse | null>(null);
  const [tasks, setTasks] = useState<PublishResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [manualTask, setManualTask] = useState<PublishResponse | null>(null);
  const [manualOutcome, setManualOutcome] = useState<"success" | "failed" | "unknown">("success");
  const [manualUrl, setManualUrl] = useState("");
  const [manualNote, setManualNote] = useState("");
  const [savingPlatform, setSavingPlatform] = useState<string | null>(null);

  const selectedPlatformNames = useMemo(
    () => platforms.map(platformLabel).join("、"),
    [platforms],
  );

  const enabledPlatformCount = useMemo(
    () => availablePlatforms.filter((item) => item.enabled).length,
    [availablePlatforms],
  );

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [platformResp, assetResp, batchResp] = await Promise.all([
        listPublishPlatforms(),
        listPublishAssets(),
        listPublishBatches(),
      ]);
      const configResp = await getPublishConfig();
      setAvailablePlatforms(platformResp.platforms);
      setPublishConfig(configResp);
      setConfigDrafts(
        Object.fromEntries(
          configResp.platforms.map((item) => [
            item.platform,
            {
              ...EMPTY_CONFIG_DRAFT,
              mode: item.mode === "official" ? "official" : "manual",
            },
          ]),
        ),
      );
      setAssets(assetResp.items);
      setTasks(batchResp.items.flatMap((batch) => batch.tasks));
      setPlatforms((current) =>
        current.length > 0
          ? current
          : platformResp.platforms.filter((item) => item.enabled).map((item) => item.platform),
      );
    } catch (err) {
      toast.error((err as Error).message || "加载发布数据失败");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  const updateConfigDraft = useCallback(
    (platform: string, patch: Partial<PlatformConfigDraft>) => {
      setConfigDrafts((prev) => ({
        ...prev,
        [platform]: {
          ...(prev[platform] || EMPTY_CONFIG_DRAFT),
          ...patch,
        },
      }));
    },
    [],
  );

  const savePlatformConfig = useCallback(async (platform: string) => {
    const draft = configDrafts[platform] || EMPTY_CONFIG_DRAFT;
    setSavingPlatform(platform);
    try {
      await updatePublishConfig(platform, {
        mode: draft.mode,
        access_token: draft.access_token.trim() || undefined,
        open_id: draft.open_id.trim() || undefined,
        client_key: draft.client_key.trim() || undefined,
        client_secret: draft.client_secret.trim() || undefined,
      });
      toast.success(`${platformLabel(platform)} 配置已保存`);
      await loadData();
    } catch (err) {
      toast.error((err as Error).message || "保存发布配置失败");
    } finally {
      setSavingPlatform(null);
    }
  }, [configDrafts, loadData, toast]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    if (!editTaskId) return;
    let active = true;
    void importEditedVideoToPublish(editTaskId)
      .then((asset) => {
        if (!active) return;
        setAssets((current) => [asset, ...current.filter((item) => item.path !== asset.path)]);
        setVideoPath(asset.path);
        setWorkflowStep("publish");
        toast.success("已接收智能剪辑成片，请填写标题并完成发布预检。");
      })
      .catch((err) => {
        if (active) toast.error((err as Error).message || "接收智能剪辑成片失败");
      });
    return () => {
      active = false;
    };
  }, [editTaskId, toast]);

  const addTag = useCallback(() => {
    const trimmed = tagInput.trim().replace(/^#/, "");
    if (!trimmed) return;
    if (tags.includes(trimmed)) {
      toast.warning("标签已存在");
      return;
    }
    setTags((prev) => [...prev, trimmed]);
    setTagInput("");
  }, [tagInput, tags, toast]);

  const buildPayload = useCallback(
    () => ({
      video_path: videoPath.trim(),
      platforms,
      title: title.trim(),
      description: description.trim(),
      tags,
    }),
    [description, platforms, tags, title, videoPath],
  );

  const validateForm = useCallback(() => {
    if (!videoPath.trim()) {
      toast.warning("请先上传或选择成片文件");
      return false;
    }
    if (!title.trim()) {
      toast.warning("请输入发布标题");
      return false;
    }
    if (platforms.length === 0) {
      toast.warning("请至少选择一个平台");
      return false;
    }
    return true;
  }, [platforms.length, title, toast, videoPath]);

  const runPreflight = useCallback(async () => {
    if (!validateForm()) return null;
    const result = await preflightPublish(buildPayload());
    setPreflight(result);
    if (result.blocked) {
      toast.warning("发布预检未通过，请处理提示后再创建任务");
    } else {
      toast.success("预检通过，可以创建发布任务");
    }
    return result;
  }, [buildPayload, toast, validateForm]);

  const createBatch = useCallback(async () => {
    if (!validateForm()) return;
    setSubmitting(true);
    try {
      const result = await runPreflight();
      if (!result || result.blocked) return;
      Modal.confirm({
        title: "确认创建发布任务",
        content: `将为 ${selectedPlatformNames} 创建发布包。当前未接入官方真实发布，任务创建后需要人工到平台后台完成并回填结果。`,
        okText: "确认创建",
        cancelText: "取消",
        onOk: async () => {
          const batch = await createPublishBatch({
            ...buildPayload(),
            confirmation_accepted: true,
          });
          setTasks((prev) => [...batch.tasks, ...prev]);
          setPreflight(null);
          toast.success(`已创建 ${batch.total} 个发布任务`);
        },
      });
    } catch (err) {
      toast.error((err as Error).message || "创建发布任务失败");
    } finally {
      setSubmitting(false);
    }
  }, [buildPayload, runPreflight, selectedPlatformNames, toast, validateForm]);

  const submitManualResult = useCallback(async () => {
    if (!manualTask) return;
    const succeeded =
      manualOutcome === "success" ? true : manualOutcome === "failed" ? false : null;
    try {
      const updated = await recordManualPublishResult(manualTask.task_id, {
        succeeded,
        platform_url: manualUrl.trim() || undefined,
        note: manualNote.trim(),
      });
      setTasks((prev) =>
        prev.map((item) => (item.task_id === updated.task_id ? updated : item)),
      );
      setManualTask(null);
      setManualUrl("");
      setManualNote("");
      toast.success("发布结果已记录");
    } catch (err) {
      toast.error((err as Error).message || "记录发布结果失败");
    }
  }, [manualNote, manualOutcome, manualTask, manualUrl, toast]);

  const retryTask = useCallback(async (task: PublishResponse) => {
    try {
      const retried = await retryPublishTask(task.task_id);
      setTasks((prev) => [retried, ...prev]);
      toast.success("已创建一次重试任务");
    } catch (err) {
      toast.error((err as Error).message || "重试失败");
    }
  }, [toast]);

  const columns: ColumnsType<PublishResponse> = [
    {
      title: "任务ID",
      dataIndex: "task_id",
      width: 160,
      ellipsis: true,
      render: (value: string) => <Text code>{value}</Text>,
    },
    {
      title: "平台",
      dataIndex: "platform",
      width: 100,
      render: (value: string) => <Tag color="blue">{platformLabel(value)}</Tag>,
    },
    {
      title: "标题",
      dataIndex: "title",
      ellipsis: true,
    },
    {
      title: "状态",
      dataIndex: "publish_status",
      width: 140,
      render: (value: string) => {
        const cfg = STATUS_MAP[value] || STATUS_MAP.pending;
        return <Tag color={cfg.color} icon={cfg.icon}>{cfg.label}</Tag>;
      },
    },
    {
      title: "阶段",
      dataIndex: "stage",
      ellipsis: true,
    },
    {
      title: "链接",
      dataIndex: "platform_url",
      width: 90,
      render: (value: string | null) =>
        value ? <Link href={value} target="_blank">打开</Link> : <Text type="secondary">未填</Text>,
    },
    {
      title: "操作",
      width: 180,
      render: (_: unknown, record) => (
        <Space>
          <Button
            size="small"
            onClick={() => {
              setManualTask(record);
              setManualOutcome("success");
              setManualUrl(record.platform_url || "");
              setManualNote("");
            }}
          >
            回填结果
          </Button>
          {["failed", "outcome_unknown"].includes(record.publish_status) && (
            <Button size="small" icon={<ReloadOutlined />} onClick={() => retryTask(record)}>
              重试
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          <RocketOutlined /> 多平台发布
        </Title>
        <Text type="secondary">
          先确认平台发布配置，再选择成片和目标平台创建发布任务。
        </Text>
      </div>

      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Button
            type={workflowStep === "config" ? "primary" : "default"}
            icon={<SettingOutlined />}
            onClick={() => setWorkflowStep("config")}
          >
            配置平台
          </Button>
          <Button
            type={workflowStep === "publish" ? "primary" : "default"}
            icon={<RocketOutlined />}
            disabled={enabledPlatformCount === 0}
            onClick={() => setWorkflowStep("publish")}
          >
            选择发布
          </Button>
          <Text type="secondary">
            当前：{workflowStep === "config" ? "平台配置" : "发布选择"}
          </Text>
        </Space>
      </Card>

      <Card style={{ marginBottom: 24 }}>
        <Steps
          current={workflowStep === "config" ? 0 : 1}
          items={[
            { title: "平台配置", description: "确认账号、权限和发布模式" },
            { title: "发布选择", description: "选择成片、平台和发布内容" },
          ]}
        />
      </Card>

      {workflowStep === "config" ? (
        <Row gutter={[24, 24]}>
          <Col xs={24} lg={16}>
            <Card
              title={<Space><SettingOutlined /> 平台配置</Space>}
              extra={<Button onClick={loadData}>刷新状态</Button>}
            >
              <Spin spinning={loading}>
                <Space direction="vertical" style={{ width: "100%" }} size={16}>
                  <Alert
                    type="info"
                    showIcon
                    message="当前版本默认使用人工发布包"
                    description="只有拿到平台官方发布权限并完成适配器联调后，才会切换到官方自动发布。这里先把每个平台的模式和缺失配置讲清楚。"
                  />
                  {availablePlatforms.map((item) => {
                    const config = publishConfig?.platforms.find(
                      (candidate) => candidate.platform === item.platform,
                    );
                    const draft = configDrafts[item.platform] || EMPTY_CONFIG_DRAFT;
                    const variableStatus = (field: string) =>
                      config?.variables.find((variable) => variable.field === field);
                    const envKeys = item.missing_configuration.length > 0
                      ? item.missing_configuration
                      : PLATFORM_ENV_KEYS[item.platform] || [];
                    return (
                      <div
                        key={item.platform}
                        style={{
                          border: "1px solid var(--border-default)",
                          borderRadius: "var(--radius-md)",
                          padding: 16,
                        }}
                      >
                        <Row gutter={[16, 12]} align="middle">
                          <Col xs={24} md={7}>
                            <Space direction="vertical" size={2}>
                              <Text strong>{platformLabel(item.platform)}</Text>
                              <Text type="secondary">{item.display_name}</Text>
                            </Space>
                          </Col>
                          <Col xs={24} md={5}>
                            <Tag color={item.enabled ? "success" : "default"}>
                              {item.enabled ? "可创建任务" : "不可创建任务"}
                            </Tag>
                            <Tag color={item.mode === "manual" ? "warning" : "default"}>
                              {modeLabel(item.mode)}
                            </Tag>
                          </Col>
                          <Col xs={24} md={6}>
                            <Text type="secondary">支持能力</Text>
                            <div style={{ marginTop: 4 }}>
                              <Tag>{item.supports_tags ? "话题标签" : "无标签"}</Tag>
                              <Tag>{item.supports_cover ? "封面" : "无封面"}</Tag>
                              <Tag>{item.supports_scheduled ? "定时" : "即时"}</Tag>
                            </div>
                          </Col>
                          <Col xs={24} md={6}>
                            <Text type="secondary">配置变量</Text>
                            <div style={{ marginTop: 4 }}>
                              {envKeys.map((key) => (
                                <Tag key={key}>{key}</Tag>
                              ))}
                            </div>
                          </Col>
                        </Row>
                        <Divider style={{ margin: "16px 0" }} />
                        <Row gutter={[12, 12]} align="bottom">
                          <Col xs={24} md={8}>
                            <Text strong style={{ display: "block", marginBottom: 8 }}>
                              发布模式
                            </Text>
                            <Select
                              style={{ width: "100%" }}
                              value={draft.mode}
                              options={[
                                { label: "人工发布包", value: "manual" },
                                { label: "官方接口", value: "official" },
                              ]}
                              onChange={(value) =>
                                updateConfigDraft(item.platform, { mode: value })
                              }
                            />
                          </Col>
                          {CONFIG_INPUTS.map((input) => {
                            const status = variableStatus(input.field);
                            const placeholder = status?.configured
                              ? `已配置：${status.masked_value || "***"}`
                              : `填写 ${input.label}`;
                            const value = draft[input.field];
                            const inputNode = input.password ? (
                              <Input.Password
                                placeholder={placeholder}
                                value={value}
                                onChange={(event) =>
                                  updateConfigDraft(item.platform, {
                                    [input.field]: event.target.value,
                                  })
                                }
                              />
                            ) : (
                              <Input
                                placeholder={placeholder}
                                value={value}
                                onChange={(event) =>
                                  updateConfigDraft(item.platform, {
                                    [input.field]: event.target.value,
                                  })
                                }
                              />
                            );
                            return (
                              <Col xs={24} md={8} key={input.field}>
                                <Text strong style={{ display: "block", marginBottom: 8 }}>
                                  {input.label}
                                </Text>
                                {inputNode}
                              </Col>
                            );
                          })}
                          <Col xs={24}>
                            <Space wrap>
                              <Button
                                type="primary"
                                loading={savingPlatform === item.platform}
                                onClick={() => savePlatformConfig(item.platform)}
                              >
                                保存 {platformLabel(item.platform)} 配置
                              </Button>
                              <Text type="secondary">
                                空白字段会保留已有值；密钥不会完整回显。
                              </Text>
                            </Space>
                          </Col>
                        </Row>
                      </div>
                    );
                  })}
                </Space>
              </Spin>
            </Card>
          </Col>

          <Col xs={24} lg={8}>
            <Card title="发布前置条件">
              <Space direction="vertical" style={{ width: "100%" }} size={14}>
                <div>
                  <Text type="secondary">可创建任务的平台</Text>
                  <Title level={3} style={{ margin: "4px 0 0" }}>{enabledPlatformCount}</Title>
                </div>
                <Alert
                  type="warning"
                  showIcon
                  message="官方权限未配置时不会自动发布"
                  description="系统会生成发布包，人工到平台后台发布后再回填链接或失败原因。"
                />
                <Button
                  type="primary"
                  icon={<RocketOutlined />}
                  block
                  disabled={enabledPlatformCount === 0}
                  onClick={() => setWorkflowStep("publish")}
                >
                  进入发布选择
                </Button>
              </Space>
            </Card>
          </Col>
        </Row>
      ) : (
      <Row gutter={[24, 24]}>
        <Col xs={24} lg={10}>
          <Card title={<Space><SendOutlined /> 发布配置</Space>}>
            <Spin spinning={loading}>
              <Space direction="vertical" style={{ width: "100%" }} size={16}>
                <Button icon={<SettingOutlined />} onClick={() => setWorkflowStep("config")}>
                  配置平台
                </Button>
                <div>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>选择平台</Text>
                  <Space wrap>
                    {availablePlatforms.map((item) => {
                      const selected = platforms.includes(item.platform);
                      return (
                        <Button
                          key={item.platform}
                          type={selected ? "primary" : "default"}
                          onClick={() =>
                            setPlatforms((prev) =>
                              selected
                                ? prev.filter((value) => value !== item.platform)
                                : [...prev, item.platform],
                            )
                          }
                        >
                          {platformLabel(item.platform)}
                        </Button>
                      );
                    })}
                  </Space>
                  <div style={{ marginTop: 8 }}>
                    {availablePlatforms.map((item) => (
                      <Tag key={item.platform} color={item.mode === "manual" ? "warning" : "default"}>
                        {platformLabel(item.platform)}：{item.mode === "manual" ? "人工兜底" : item.mode}
                      </Tag>
                    ))}
                  </div>
                </div>

                <div>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>
                    <VideoCameraOutlined /> 成片文件
                  </Text>
                  <Space.Compact style={{ width: "100%" }}>
                    <Select
                      showSearch
                      allowClear
                      style={{ width: "100%" }}
                      placeholder="选择已上传成片，或直接上传新文件"
                      value={videoPath || undefined}
                      options={assets.map((asset) => ({ label: asset.name, value: asset.path }))}
                      onChange={(value) => setVideoPath(value || "")}
                    />
                    <Upload
                      accept=".mp4,.mov,.m4v"
                      showUploadList={false}
                      customRequest={async (options) => {
                        try {
                          const asset = await uploadPublishAsset(options.file as File);
                          setAssets((prev) => [asset, ...prev]);
                          setVideoPath(asset.path);
                          options.onSuccess?.(asset);
                          toast.success("成片已上传");
                        } catch (err) {
                          options.onError?.(err as Error);
                          toast.error((err as Error).message || "上传失败");
                        }
                      }}
                    >
                      <Button icon={<UploadOutlined />}>上传</Button>
                    </Upload>
                  </Space.Compact>
                  {videoPath && (
                    <Text code style={{ marginTop: 8, display: "block" }}>{videoPath}</Text>
                  )}
                </div>

                <div>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>
                    <FileTextOutlined /> 标题
                  </Text>
                  <Input
                    placeholder="输入发布标题"
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    maxLength={100}
                    showCount
                  />
                </div>

                <div>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>描述</Text>
                  <TextArea
                    placeholder="输入发布描述"
                    rows={4}
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    maxLength={1000}
                    showCount
                    style={{ resize: "none" }}
                  />
                </div>

                <div>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>
                    <TagOutlined /> 话题标签
                  </Text>
                  <Space.Compact style={{ width: "100%" }}>
                    <Input
                      placeholder="输入标签，按回车添加"
                      value={tagInput}
                      onChange={(event) => setTagInput(event.target.value)}
                      onPressEnter={addTag}
                    />
                    <Button onClick={addTag}>添加</Button>
                  </Space.Compact>
                  <div style={{ marginTop: 8 }}>
                    {tags.map((tag) => (
                      <Tag
                        key={tag}
                        closable
                        color="blue"
                        onClose={() => setTags((prev) => prev.filter((item) => item !== tag))}
                      >
                        #{tag}
                      </Tag>
                    ))}
                  </div>
                </div>

                {preflight && (
                  <Alert
                    type={preflight.blocked ? "warning" : "success"}
                    showIcon
                    message={preflight.blocked ? "预检未通过" : "预检通过"}
                    description={
                      preflight.blocked
                        ? preflight.issues.join("；") || "存在不可创建任务的平台"
                        : "当前会创建人工发布包，发布完成后需要回填平台结果。"
                    }
                  />
                )}

                <Divider style={{ margin: "4px 0" }} />
                <Space style={{ width: "100%" }}>
                  <Button onClick={runPreflight}>预检</Button>
                  <Button
                    type="primary"
                    icon={<RocketOutlined />}
                    loading={submitting}
                    onClick={createBatch}
                  >
                    创建发布任务
                  </Button>
                </Space>
              </Space>
            </Spin>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card
            title={<Space><ClockCircleOutlined /> 发布任务 <Tag color="blue">{tasks.length}</Tag></Space>}
            extra={<Button size="small" onClick={loadData}>刷新</Button>}
          >
            {loading ? (
              <SkeletonCard rows={4} />
            ) : tasks.length > 0 ? (
              <Table
                rowKey="task_id"
                columns={columns}
                dataSource={tasks}
                pagination={{ pageSize: 10 }}
                size="middle"
              />
            ) : (
              <Empty description="暂无发布任务" />
            )}
          </Card>
        </Col>
      </Row>
      )}

      <Modal
        title={manualTask ? `回填 ${platformLabel(manualTask.platform)} 发布结果` : "回填发布结果"}
        open={Boolean(manualTask)}
        okText="保存结果"
        cancelText="取消"
        onOk={submitManualResult}
        onCancel={() => setManualTask(null)}
      >
        <Space direction="vertical" style={{ width: "100%" }} size={12}>
          <Radio.Group
            value={manualOutcome}
            onChange={(event) => setManualOutcome(event.target.value)}
            options={[
              { label: "已发布", value: "success" },
              { label: "发布失败", value: "failed" },
              { label: "结果不确定", value: "unknown" },
            ]}
          />
          <Input
            prefix={<LinkOutlined />}
            placeholder="作品链接，可选"
            value={manualUrl}
            onChange={(event) => setManualUrl(event.target.value)}
          />
          <TextArea
            rows={3}
            placeholder="备注或失败原因，可选"
            value={manualNote}
            onChange={(event) => setManualNote(event.target.value)}
          />
        </Space>
      </Modal>
    </div>
  );
}
