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
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  ReloadOutlined,
  RocketOutlined,
  SendOutlined,
  SettingOutlined,
  TagOutlined,
  UploadOutlined,
  UserAddOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  connectPublishAccount,
  createPublishAccount,
  createPublishBatch,
  deletePublishAccount,
  deletePublishTask,
  deletePublishTasks,
  getPublishAccountStatus,
  importEditedVideoToPublish,
  listPublishAccounts,
  listPublishAssets,
  listPublishBatches,
  listPublishPlatforms,
  preflightPublish,
  recordManualPublishResult,
  resumePublishTask,
  retryPublishTask,
  uploadPublishAsset,
} from "../api/client";
import type {
  PublishAccount,
  PublishAsset,
  PublishPlatformCapability,
  PublishPreflightResponse,
  PublishResponse,
} from "../api/types";
import { SkeletonCard } from "../components/SkeletonLoader";
import { useToast } from "../components/Toast";

const { Title, Text } = Typography;
const { TextArea } = Input;

type PageStep = "configure" | "publish";
const DEFAULT_ACCOUNT_NAME = "公司主号";

const PLATFORM_LABELS: Record<string, string> = {
  douyin: "抖音",
  kuaishou: "快手",
  xiaohongshu: "小红书",
  wechat_channels: "视频号",
  bilibili: "Bilibili",
};

const STATUS_META: Record<string, { label: string; color: string }> = {
  succeeded: { label: "已发布", color: "success" },
  failed: { label: "发布失败", color: "error" },
  outcome_unknown: { label: "平台结果待确认", color: "warning" },
  action_required: { label: "等待你处理", color: "processing" },
  pending: { label: "排队中", color: "blue" },
  manual_ready: { label: "待人工完成", color: "gold" },
  waiting_user: { label: "等待你确认", color: "processing" },
};

function inferDouyinMusicHint(title: string, description: string) {
  const text = `${title} ${description}`;
  if (/(机器人|人工智能|AI|科技|智能|未来)/i.test(text)) return "科技未来 克制";
  if (/(老板|商业|赚钱|经营|公司|创业|客户)/i.test(text)) return "商业表达 平稳";
  if (/(故事|曾经|后来|经历|回忆)/i.test(text)) return "故事叙事 克制";
  if (/(焦虑|情绪|治愈|共鸣|关系|人生)/i.test(text)) return "情绪共鸣 克制";
  if (/(揭秘|真相|为什么|居然|没想到)/i.test(text)) return "悬念揭秘 有推动感";
  return "通用口播 克制";
}

function platformLabel(platform: string) {
  return PLATFORM_LABELS[platform] || platform;
}

function accountStatusMeta(status: string) {
  if (status === "ready") return { label: "已核验可发布", color: "success" };
  if (status === "browser_open") return { label: "等待扫码或核验", color: "processing" };
  if (status === "error") return { label: "需要处理", color: "error" };
  return { label: "尚未登录", color: "default" };
}

function platformHint(platform: PublishPlatformCapability) {
  if (platform.mode === "local_browser") return platform.manual_only
    ? "在本机打开官方创作者窗口；只有核验成功的账号才可创建任务。"
    : "系统在本机官方窗口上传、填文案和提交；你只处理登录和验证码。";
  if (platform.mode === "manual") return "无需账号配置；创建任务后按提示在官方平台完成发布。";
  return "当前不可用。";
}

export default function PublishPage() {
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const [pageStep, setPageStep] = useState<PageStep>("configure");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [addingAccount, setAddingAccount] = useState(false);
  const [accounts, setAccounts] = useState<PublishAccount[]>([]);
  const [accountNames, setAccountNames] = useState<Record<string, string>>({});
  const [selectedAccountIds, setSelectedAccountIds] = useState<Record<string, string>>({});
  const [availablePlatforms, setAvailablePlatforms] = useState<PublishPlatformCapability[]>([]);
  const [assets, setAssets] = useState<PublishAsset[]>([]);
  const [tasks, setTasks] = useState<PublishResponse[]>([]);
  const [selectedTaskIds, setSelectedTaskIds] = useState<string[]>([]);
  const [platforms, setPlatforms] = useState<string[]>(["douyin"]);
  const [videoPath, setVideoPath] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tagInput, setTagInput] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [nativeMusicHint, setNativeMusicHint] = useState("");
  const [preflight, setPreflight] = useState<PublishPreflightResponse | null>(null);
  const [manualTask, setManualTask] = useState<PublishResponse | null>(null);
  const [manualOutcome, setManualOutcome] = useState<"success" | "failed" | "unknown">("success");
  const [manualUrl, setManualUrl] = useState("");
  const [manualNote, setManualNote] = useState("");

  const readyAccounts = useMemo(
    () => accounts.filter((account) => account.status === "ready"),
    [accounts],
  );

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [platformData, assetData, batchData, accountData] = await Promise.all([
        listPublishPlatforms(),
        listPublishAssets(),
        listPublishBatches(),
        listPublishAccounts(),
      ]);
      const readyByPlatform = accountData.reduce<Record<string, string>>((result, account) => {
        if (account.status === "ready" && !result[account.platform]) result[account.platform] = account.account_id;
        return result;
      }, {});
      setAvailablePlatforms(platformData.platforms);
      setAssets(assetData.items);
      const loadedTasks = batchData.items.flatMap((batch) => batch.tasks);
      setTasks(loadedTasks);
      setSelectedTaskIds((current) => current.filter((taskId) => loadedTasks.some((task) => task.task_id === taskId)));
      setAccounts(accountData);
      setSelectedAccountIds((current) => ({ ...readyByPlatform, ...Object.fromEntries(Object.entries(current).filter(([, id]) => accountData.some((account) => account.account_id === id && account.status === "ready"))) }));
      if (Object.keys(readyByPlatform).length) setPageStep("publish");
    } catch (error) {
      toast.error((error as Error).message || "加载发布数据失败");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { void loadData(); }, [loadData]);

  useEffect(() => {
    const taskId = searchParams.get("from_edit_task") || searchParams.get("editTask");
    if (!taskId) return;
    void importEditedVideoToPublish(taskId)
      .then((asset) => {
        setAssets((current) => [asset, ...current.filter((item) => item.path !== asset.path)]);
        setVideoPath(asset.path);
        if (asset.recommended_title) setTitle((current) => current || asset.recommended_title || "");
        setNativeMusicHint(asset.recommended_music_hint || "");
        setPageStep("publish");
        toast.success("已带入剪辑成片");
      })
      .catch((error) => toast.error((error as Error).message || "带入剪辑成片失败"));
  }, [searchParams, toast]);

  useEffect(() => {
    if (searchParams.get("from_ai_copy") !== "1") return;
    const raw = window.sessionStorage.getItem("publish_ai_draft");
    if (!raw) return;
    try {
      const draft = JSON.parse(raw) as { title?: string; description?: string; tags?: string[] };
      setTitle(String(draft.title || "").slice(0, 100));
      setDescription(String(draft.description || "").slice(0, 1000));
      setTags(Array.isArray(draft.tags) ? draft.tags.map(String).filter(Boolean).slice(0, 8) : []);
      setPreflight(null);
      setPageStep("publish");
      window.sessionStorage.removeItem("publish_ai_draft");
      toast.success("已带入 AI 文案页生成的标题、描述和话题");
    } catch {
      window.sessionStorage.removeItem("publish_ai_draft");
      toast.error("AI 发布信息读取失败，请返回文案页重新带入");
    }
  }, [searchParams, toast]);

  const waitingAccountKey = useMemo(
    () => accounts.filter((account) => account.status === "browser_open").map((account) => account.account_id).join(","),
    [accounts],
  );

  useEffect(() => {
    if (!waitingAccountKey) return;
    const timer = window.setInterval(() => {
      const ids = waitingAccountKey.split(",").filter(Boolean);
      void Promise.all(ids.map((accountId) => getPublishAccountStatus(accountId)))
        .then((updatedAccounts) => {
          setAccounts((current) => current.map((account) => (
            updatedAccounts.find((updated) => updated.account_id === account.account_id) || account
          )));
        })
        .catch((error) => {
          const message = (error as Error).message || "";
          if (!message.includes("不存在")) return;
          setAccounts((current) => current.filter((account) => !ids.includes(account.account_id)));
          setSelectedAccountIds((current) => Object.fromEntries(Object.entries(current).filter(([, id]) => !ids.includes(id))));
          setPageStep("configure");
          toast.error("账号记录已失效，请重新添加并扫码连接");
        });
    }, 4000);
    return () => window.clearInterval(timer);
  }, [waitingAccountKey]);

  const activeTaskKey = useMemo(
    () => tasks
      .filter((task) => ["pending", "action_required", "manual_ready", "outcome_unknown"].includes(task.publish_status))
      .map((task) => task.task_id)
      .join(","),
    [tasks],
  );

  useEffect(() => {
    if (!activeTaskKey) return;
    const timer = window.setInterval(() => {
      void listPublishBatches()
        .then((data) => {
          const refreshed = data.items.flatMap((batch) => batch.tasks);
          setTasks(refreshed);
          setSelectedTaskIds((current) => current.filter((taskId) => refreshed.some((task) => task.task_id === taskId)));
        })
        // 定时读取失败不打断当前操作；用户仍可使用“刷新”看到明确错误。
        .catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [activeTaskKey]);

  const addAndConnectAccount = async (platform: string) => {
    const normalizedName = (accountNames[platform] ?? DEFAULT_ACCOUNT_NAME).trim();
    if (!normalizedName) {
      toast.error("请给账号起个名称，例如“公司主号”");
      return;
    }
    setAddingAccount(true);
    let created: PublishAccount | null = null;
    try {
      created = await createPublishAccount({ platform, name: normalizedName });
      const connected = await connectPublishAccount(created.account_id);
      setAccounts((current) => [...current, connected]);
      setAccountNames((current) => ({ ...current, [platform]: DEFAULT_ACCOUNT_NAME }));
      toast.success(`${platformLabel(platform)}登录窗口已打开，完成登录后系统会自动核验`);
    } catch (error) {
      if (created) {
        setAccounts((current) => [...current, created!]);
      }
      toast.error((error as Error).message || "账号添加或扫码窗口打开失败");
    } finally {
      setAddingAccount(false);
    }
  };

  const connectAccount = async (accountId: string) => {
    setConnecting(accountId);
    try {
      const updated = await connectPublishAccount(accountId);
      setAccounts((current) => current.map((item) => item.account_id === accountId ? updated : item));
      toast.success("官方登录窗口已打开，完成登录后系统会自动核验");
    } catch (error) {
      toast.error((error as Error).message || "无法打开官方扫码窗口");
    } finally {
      setConnecting(null);
    }
  };

  const removeAccount = (account: PublishAccount) => {
    Modal.confirm({
      title: `移除“${account.name}”？`,
      content: "这会删除本机保存的专用浏览器登录档案，不会注销或影响抖音平台账号。",
      okText: "移除本机账号",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        await deletePublishAccount(account.account_id);
        setAccounts((current) => current.filter((item) => item.account_id !== account.account_id));
        setSelectedAccountIds((current) => Object.fromEntries(Object.entries(current).filter(([, id]) => id !== account.account_id)));
        toast.success("本机账号档案已移除");
      },
    });
  };

  const addTag = () => {
    const normalized = tagInput.trim().replace(/^#/, "");
    if (!normalized || tags.includes(normalized)) return;
    setTags((current) => [...current, normalized]);
    setTagInput("");
  };

  const togglePlatform = (platform: PublishPlatformCapability) => {
    const selected = platforms.includes(platform.platform);
    if (platform.requires_account && !readyAccounts.some((account) => account.platform === platform.platform)) {
      setPageStep("configure");
      toast.error(`请先连接并核验一个${platformLabel(platform.platform)}账号`);
      return;
    }
    if (!platform.enabled && !platform.manual_fallback) {
      toast.error(`${platformLabel(platform.platform)}暂不可用`);
      return;
    }
    setPlatforms((current) => selected ? current.filter((item) => item !== platform.platform) : [...current, platform.platform]);
    setPreflight(null);
  };

  const confirmCreateBatch = async (currentAccountIds: Record<string, string>) => {
    setSubmitting(true);
    try {
      const batch = await createPublishBatch({
        video_path: videoPath,
        platforms,
        title,
        description,
        tags,
        account_ids: currentAccountIds,
        native_music_mode: platforms.includes("douyin") ? "auto_recommended" : "off",
        native_music_hint: nativeMusicHint || inferDouyinMusicHint(title, description),
        confirmation_accepted: true,
      });
      setTasks((current) => [...batch.tasks, ...current]);
      setPreflight(null);
      toast.success("发布任务已创建；系统会自动选抖音配乐并填写发布页");
    } catch (error) {
      toast.error((error as Error).message || "创建发布任务失败");
    } finally {
      setSubmitting(false);
    }
  };

  const startPublishing = async () => {
    if (!platforms.length) {
      toast.error("请至少选择一个发布平台");
      return;
    }
    if (!title.trim()) {
      toast.error("请填写发布标题");
      return;
    }
    setSubmitting(true);
    try {
      const currentAccounts = await listPublishAccounts();
      setAccounts(currentAccounts);
      const currentAccountIds = Object.fromEntries(platforms.map((platform) => [platform, selectedAccountIds[platform]]).filter(([, accountId]) => Boolean(accountId))) as Record<string, string>;
      const unavailable = platforms.find((platform) => {
        const account = currentAccounts.find((item) => item.account_id === currentAccountIds[platform]);
        return !account || account.status !== "ready" || account.platform !== platform;
      });
      if (unavailable) {
        setSelectedAccountIds((current) => ({ ...current, [unavailable]: "" }));
        setPageStep("configure");
        toast.error(`${platformLabel(unavailable)}账号尚未核验，请重新扫码或核验`);
        return;
      }
      const result = await preflightPublish({
        video_path: videoPath,
        platforms,
        title,
        description,
        tags,
        account_ids: currentAccountIds,
        native_music_mode: platforms.includes("douyin") ? "auto_recommended" : "off",
        native_music_hint: nativeMusicHint || inferDouyinMusicHint(title, description),
      });
      setPreflight(result);
      if (result.blocked) {
        if (result.platforms.some((platform) => platform.issue_code?.startsWith("account_"))) {
          setSelectedAccountIds({});
          setPageStep("configure");
        }
        toast.error("发布前检查未通过，请按提示补齐信息");
        return;
      }
      Modal.confirm({
        title: "确认创建发布任务？",
        content: `系统会上传视频、填写文案、自动选择抖音官方推荐配乐并提交发布。你只需在官方窗口完成登录、验证码或风控验证；结果不明确时系统会停止，不会重复发布。`,
        okText: "确认并开始",
        cancelText: "返回修改",
        onOk: () => confirmCreateBatch(currentAccountIds),
      });
    } catch (error) {
      toast.error((error as Error).message || "发布前检查失败");
    } finally {
      setSubmitting(false);
    }
  };

  const submitManualResult = async () => {
    if (!manualTask) return;
    try {
      const updated = await recordManualPublishResult(manualTask.task_id, {
        succeeded: manualOutcome === "unknown" ? null : manualOutcome === "success",
        platform_url: manualUrl || undefined,
        note: manualNote,
      });
      setTasks((current) => current.map((item) => item.task_id === updated.task_id ? updated : item));
      setManualTask(null);
      toast.success("发布结果已保存");
    } catch (error) {
      toast.error((error as Error).message || "保存发布结果失败");
    }
  };

  const retryTask = async (task: PublishResponse) => {
    try {
      const updated = await retryPublishTask(task.task_id);
      setTasks((current) => current.map((item) => item.task_id === updated.task_id ? updated : item));
      toast.success("已重新打开发布准备流程");
    } catch (error) {
      toast.error((error as Error).message || "重试失败");
    }
  };

  const resumeTask = async (task: PublishResponse) => {
    try {
      const updated = await resumePublishTask(task.task_id);
      setTasks((current) => current.map((item) => item.task_id === updated.task_id ? updated : item));
      toast.success("已恢复队列，将重新检查官方页面");
    } catch (error) {
      toast.error((error as Error).message || "继续发布失败");
    }
  };

  const canDeleteTask = (task: PublishResponse) => !["queued", "running"].includes(task.status);

  const deleteTasks = async (taskIds: string[]) => {
    try {
      if (taskIds.length === 1) await deletePublishTask(taskIds[0]);
      else await deletePublishTasks(taskIds);
      const removed = new Set(taskIds);
      setTasks((current) => current.filter((task) => !removed.has(task.task_id)));
      setSelectedTaskIds((current) => current.filter((taskId) => !removed.has(taskId)));
      toast.success(taskIds.length === 1 ? "发布任务已删除" : `已删除 ${taskIds.length} 条发布任务`);
    } catch (error) {
      toast.error((error as Error).message || "删除发布任务失败");
    }
  };

  const confirmDeleteTasks = (taskIds: string[]) => {
    if (!taskIds.length) return;
    Modal.confirm({
      title: taskIds.length === 1 ? "删除这条发布任务？" : `删除选中的 ${taskIds.length} 条发布任务？`,
      content: "仅删除系统中的任务记录，不会删除已上传的视频或平台上的作品。",
      okText: "确认删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => deleteTasks(taskIds),
    });
  };

  const columns: ColumnsType<PublishResponse> = [
    { title: "平台", dataIndex: "platform", width: 100, render: (value) => platformLabel(value) },
    { title: "标题", dataIndex: "title", ellipsis: true },
    {
      title: "状态",
      dataIndex: "publish_status",
      width: 130,
      render: (value) => <Tag color={STATUS_META[value]?.color || "default"}>{STATUS_META[value]?.label || value}</Tag>,
    },
    {
      title: "当前进度",
      dataIndex: "stage",
      ellipsis: true,
      render: (value, task) => <Space direction="vertical" size={0}>
        <Text>{value}</Text>
        {task.error_message && <Text type="danger" style={{ fontSize: 12 }}>{task.error_message}</Text>}
        {task.action_required && <Text type="warning" style={{ fontSize: 12 }}>{task.action_required}</Text>}
      </Space>,
    },
    {
      title: "下一步",
      width: 180,
      render: (_value, task) => <Space wrap>
        {["action_required", "manual_ready", "outcome_unknown"].includes(task.publish_status) && <Button size="small" onClick={() => { setManualTask(task); setManualOutcome("success"); setManualUrl(task.platform_url || ""); setManualNote(""); }}>回填实际结果</Button>}
        {task.publish_status === "action_required" && <Button size="small" type="primary" onClick={() => resumeTask(task)}>验证后继续</Button>}
        {task.publish_status === "failed" && <Button size="small" icon={<ReloadOutlined />} onClick={() => retryTask(task)}>重新准备</Button>}
        {canDeleteTask(task) && <Button size="small" danger icon={<DeleteOutlined />} onClick={() => confirmDeleteTasks([task.task_id])}>删除</Button>}
      </Space>,
    },
  ];

  const selectedPlatforms = availablePlatforms.filter((platform) => platforms.includes(platform.platform));

  return <div>
    <div style={{ marginBottom: 20 }}>
      <Title level={4} style={{ margin: 0 }}><RocketOutlined /> 发布中心</Title>
      <Text type="secondary">先登录账号，再选择成片。系统负责上传、配乐、填写和提交；你只处理登录与验证码。</Text>
    </div>

    <Card size="small" style={{ marginBottom: 16 }}>
      <Space wrap>
        <Button type={pageStep === "configure" ? "primary" : "default"} icon={<SettingOutlined />} onClick={() => setPageStep("configure")}>配置平台</Button>
        <Button type={pageStep === "publish" ? "primary" : "default"} icon={<SendOutlined />} onClick={() => setPageStep("publish")}>选择发布</Button>
        <Divider type="vertical" />
        <Text type="secondary">已核验账号：{readyAccounts.length} 个</Text>
      </Space>
    </Card>

    {pageStep === "configure" ? <Spin spinning={loading}>
      <Alert
        showIcon
        type="info"
        message="普通运营不需要填写 Key、Secret 或回调地址"
        description="每个平台各自使用独立本机浏览器档案。系统不会读取 Cookie；必须在官方创作者窗口扫码并完成核验。"
        style={{ marginBottom: 16 }}
      />
      <Row gutter={[16, 16]}>
        {availablePlatforms.map((platform) => <Col xs={24} md={12} key={platform.platform}>
          <Card
            title={<Space><Text strong>{platformLabel(platform.platform)}</Text><Tag color={platform.mode === "local_browser" ? "blue" : "gold"}>{platform.mode === "local_browser" ? "本机扫码" : "人工辅助"}</Tag></Space>}
            extra={platform.mode === "local_browser" && readyAccounts.some((account) => account.platform === platform.platform) ? <Tag color="success">已核验</Tag> : undefined}
          >
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
              <Text type="secondary">{platformHint(platform)}</Text>
              {platform.mode === "local_browser" ? <>
                <Space.Compact style={{ width: "100%" }}>
                  <Input aria-label={`${platformLabel(platform.platform)}账号名称`} value={accountNames[platform.platform] ?? DEFAULT_ACCOUNT_NAME} placeholder="例如：公司主号" onChange={(event) => setAccountNames((current) => ({ ...current, [platform.platform]: event.target.value }))} onPressEnter={() => void addAndConnectAccount(platform.platform)} />
                  <Button type="primary" icon={<UserAddOutlined />} loading={addingAccount} onClick={() => void addAndConnectAccount(platform.platform)}>添加并扫码</Button>
                </Space.Compact>
                {accounts.filter((account) => account.platform === platform.platform).length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`还没有${platformLabel(platform.platform)}账号`} /> : accounts.filter((account) => account.platform === platform.platform).map((account) => {
                  const status = accountStatusMeta(account.status);
                  return <Card size="small" key={account.account_id} title={account.name} extra={<Tag color={status.color}>{status.label}</Tag>}>
                    <Space direction="vertical" size={8} style={{ width: "100%" }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>{account.message}</Text>
                      <Space wrap>
                        {account.status === "ready" ? <Button size="small" type="primary" icon={<CheckCircleOutlined />} onClick={() => { setSelectedAccountIds((current) => ({ ...current, [account.platform]: account.account_id })); setPageStep("publish"); }}>用这个账号发布</Button> : <Button size="small" type="primary" loading={connecting === account.account_id} onClick={() => connectAccount(account.account_id)}>打开浏览器登录</Button>}
                        <Button size="small" danger icon={<DeleteOutlined />} onClick={() => removeAccount(account)}>移除</Button>
                      </Space>
                    </Space>
                  </Card>;
                })}
              </> : <>
                <Alert type="success" showIcon message="无需额外配置" description="选择成片后，系统会生成可复制的标题、描述和话题，并提示你在官方平台完成最后发布。" />
                <Button onClick={() => { setPlatforms((current) => current.includes(platform.platform) ? current : [...current, platform.platform]); setPageStep("publish"); }}>选择此平台发布</Button>
              </>}
            </Space>
          </Card>
        </Col>)}
      </Row>
    </Spin> : <Row gutter={[20, 20]}>
      <Col xs={24} xl={13}>
        <Card title={<Space><SendOutlined /> 选择发布</Space>}>
          <Spin spinning={loading || submitting}>
            <Space direction="vertical" size={18} style={{ width: "100%" }}>
              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>1. 选择平台</Text>
                <Row gutter={[10, 10]}>{availablePlatforms.map((platform) => {
                  const selected = platforms.includes(platform.platform);
                  const unavailable = !platform.enabled && !platform.manual_fallback;
                  return <Col xs={24} sm={12} key={platform.platform}><Card size="small" style={{ borderColor: selected ? "#1677ff" : undefined }}>
                    <Space direction="vertical" size={6} style={{ width: "100%" }}>
                      <Space><Text strong>{platformLabel(platform.platform)}</Text><Tag color={platform.mode === "local_browser" ? "blue" : "gold"}>{platform.mode === "local_browser" ? "本机扫码" : "人工辅助"}</Tag></Space>
                      <Text type="secondary" style={{ fontSize: 12 }}>{platformHint(platform)}</Text>
                      <Button block type={selected ? "primary" : "default"} disabled={unavailable} onClick={() => togglePlatform(platform)}>{selected ? "已选择" : platform.requires_account && !readyAccounts.some((account) => account.platform === platform.platform) ? "先连接账号" : "选择"}</Button>
                    </Space>
                  </Card></Col>;
                })}</Row>
              </div>

              {platforms.map((platform) => <div key={platform}>
                <Text strong style={{ display: "block", marginBottom: 8 }}>{platformLabel(platform)}发布账号</Text>
                <Select aria-label={`${platformLabel(platform)}发布账号`} style={{ width: "100%" }} value={selectedAccountIds[platform] || undefined} placeholder={`选择已核验的${platformLabel(platform)}账号`} options={readyAccounts.filter((item) => item.platform === platform).map((item) => ({ value: item.account_id, label: `${item.name} · 已核验` }))} onChange={(accountId) => setSelectedAccountIds((current) => ({ ...current, [platform]: accountId }))} />
                {!readyAccounts.some((account) => account.platform === platform) && <Alert type="warning" showIcon message={`还没有可用的${platformLabel(platform)}账号`} action={<Button size="small" onClick={() => setPageStep("configure")}>去配置</Button>} style={{ marginTop: 8 }} />}
              </div>)}

              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>2. 选择成片</Text>
                <Space.Compact style={{ width: "100%" }}>
                  <Select showSearch allowClear style={{ width: "100%" }} placeholder="选择已上传成片，或直接上传新文件" value={videoPath || undefined} options={assets.map((asset) => ({ label: asset.name, value: asset.path }))} onChange={(value) => { const asset = assets.find((item) => item.path === value); setVideoPath(value || ""); setNativeMusicHint(asset?.recommended_music_hint || ""); setPreflight(null); }} />
                  <Upload accept=".mp4,.mov,.m4v" showUploadList={false} customRequest={async (options) => {
                    try { const asset = await uploadPublishAsset(options.file as File); setAssets((current) => [asset, ...current]); setVideoPath(asset.path); options.onSuccess?.(asset); toast.success("成片已上传"); }
                    catch (error) { options.onError?.(error as Error); toast.error((error as Error).message || "上传失败"); }
                  }}><Button icon={<UploadOutlined />}>上传</Button></Upload>
                </Space.Compact>
              </div>

              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>3. 填写内容</Text>
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Input aria-label="发布标题" placeholder="输入发布标题" value={title} onChange={(event) => { setTitle(event.target.value); setPreflight(null); }} maxLength={100} showCount />
                  <TextArea aria-label="发布描述" placeholder="输入发布描述（可选）" rows={4} value={description} onChange={(event) => { setDescription(event.target.value); setPreflight(null); }} maxLength={1000} showCount />
                  <Space.Compact style={{ width: "100%" }}><Input aria-label="添加话题标签" prefix={<TagOutlined />} placeholder="输入标签，按回车添加" value={tagInput} onChange={(event) => setTagInput(event.target.value)} onPressEnter={addTag} /><Button onClick={addTag}>添加</Button></Space.Compact>
                  <div>{tags.map((tag) => <Tag key={tag} closable color="blue" onClose={() => setTags((current) => current.filter((item) => item !== tag))}>#{tag}</Tag>)}</div>
                </Space>
              </div>

              {platforms.includes("douyin") && <Alert
                type="success"
                showIcon
                message="抖音原生配乐由系统自动选择"
                description={`系统会根据文案方向“${nativeMusicHint || inferDouyinMusicHint(title, description)}”使用抖音官方推荐音乐；你不用自己打开音乐库。遇到登录或验证码时才需要你处理。`}
              />}

              {preflight && <Alert type={preflight.blocked ? "warning" : "success"} showIcon message={preflight.blocked ? "请先处理以下问题" : "发布前检查通过"} description={<Space direction="vertical" size={4}>{preflight.issues.length > 0 && <Text>{preflight.issues.join("；")}</Text>}{preflight.platforms.filter((item) => item.issue).map((item) => <Text key={item.platform}>{platformLabel(item.platform)}：{item.issue}</Text>)}</Space>} />}
              <Divider style={{ margin: "2px 0" }} />
              <Button type="primary" icon={<RocketOutlined />} loading={submitting} onClick={startPublishing}>开始发布</Button>
            </Space>
          </Spin>
        </Card>
      </Col>

      <Col xs={24} xl={11}>
        <Card title={<Space><ClockCircleOutlined /> 发布任务 <Tag color="blue">{tasks.length}</Tag></Space>} extra={<Space><Button size="small" danger icon={<DeleteOutlined />} disabled={!selectedTaskIds.length} onClick={() => confirmDeleteTasks(selectedTaskIds)}>批量删除{selectedTaskIds.length ? `（${selectedTaskIds.length}）` : ""}</Button><Button size="small" onClick={loadData}>刷新</Button></Space>}>
          <Alert type="info" showIcon message="单任务顺序处理" description={selectedPlatforms.length ? `已选择：${selectedPlatforms.map((platform) => platformLabel(platform.platform)).join("、")}。确认当前任务后，系统会自动上传、配乐、填写和提交；只在登录、验证码或结果不明确时请你接手。` : "选择平台和成片后，任务会显示在这里。"} style={{ marginBottom: 16 }} />
          {loading ? <SkeletonCard rows={4} /> : tasks.length ? <Table rowKey="task_id" columns={columns} dataSource={tasks} rowSelection={{ selectedRowKeys: selectedTaskIds, onChange: (keys) => setSelectedTaskIds(keys.map(String)), getCheckboxProps: (task) => ({ disabled: !canDeleteTask(task) }) }} pagination={{ pageSize: 8 }} size="middle" /> : <Empty description="还没有发布任务" />}
        </Card>
      </Col>
    </Row>}

    <Modal title={manualTask ? `回填 ${platformLabel(manualTask.platform)} 发布结果` : "回填发布结果"} open={Boolean(manualTask)} okText="保存结果" cancelText="取消" onOk={submitManualResult} onCancel={() => setManualTask(null)}>
      <Space direction="vertical" style={{ width: "100%" }} size={12}>
        <Alert type="info" showIcon message="请按平台实际结果填写" description="准备动作不等于已发布；只有你确认后系统才会标记为成功。" />
        <Radio.Group value={manualOutcome} onChange={(event) => setManualOutcome(event.target.value)} options={[{ label: "已发布", value: "success" }, { label: "发布失败", value: "failed" }, { label: "结果不确定", value: "unknown" }]} />
        <Input placeholder="作品链接，可选" value={manualUrl} onChange={(event) => setManualUrl(event.target.value)} />
        <TextArea rows={3} placeholder="备注或失败原因，可选" value={manualNote} onChange={(event) => setManualNote(event.target.value)} />
      </Space>
    </Modal>
  </div>;
}
