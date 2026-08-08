import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  Divider,
  Drawer,
  Empty,
  Input,
  Modal,
  Radio,
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
  CalendarOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  EyeOutlined,
  LeftOutlined,
  MoreOutlined,
  ReloadOutlined,
  RocketOutlined,
  SearchOutlined,
  SendOutlined,
  TagOutlined,
  UploadOutlined,
  UserAddOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { SiBilibili, SiKuaishou, SiTiktok, SiWechat, SiXiaohongshu } from "react-icons/si";
import {
  connectPublishAccount,
  createPublishAccount,
  createPublishBatch,
  deletePublishAccount,
  deletePublishTask,
  deletePublishTasks,
  generatePublishMetadata,
  getPublishAccountStatus,
  getPublishSafety,
  importEditedVideoToPublish,
  listPublishAccounts,
  listPublishAssets,
  listPublishBatches,
  listPublishPlatforms,
  preflightPublish,
  recordManualPublishResult,
  resumePublishSafety,
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
  PublishSafetyItem,
} from "../api/types";
import { SkeletonCard } from "../components/SkeletonLoader";
import { useToast } from "../components/Toast";

const { Title, Text } = Typography;
const { TextArea } = Input;

type PageStep = "configure" | "publish";
type PublishStage = "select" | "review";

const PLATFORM_LABELS: Record<string, string> = {
  douyin: "抖音",
  kuaishou: "快手",
  xiaohongshu: "小红书",
  wechat_channels: "视频号",
  bilibili: "B站",
};

const PLATFORM_ICONS: Record<string, ReactNode> = {
  douyin: <SiTiktok />,
  kuaishou: <SiKuaishou />,
  wechat_channels: <SiWechat />,
  xiaohongshu: <SiXiaohongshu />,
  bilibili: <SiBilibili />,
};

const PLATFORM_TONES: Record<string, string> = {
  douyin: "#111827",
  kuaishou: "#ff5c35",
  wechat_channels: "#07c160",
  xiaohongshu: "#ff2442",
  bilibili: "#00aeec",
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

function publishAssetTitle(asset: PublishAsset) {
  return asset.recommended_title?.trim() || asset.name.replace(/\.(mp4|mov|m4v)$/i, "");
}

function formatAssetSize(sizeBytes: number) {
  if (!Number.isFinite(sizeBytes) || sizeBytes <= 0) return "大小未知";
  const megabytes = sizeBytes / 1024 / 1024;
  return megabytes >= 1024 ? `${(megabytes / 1024).toFixed(1)} GB` : `${megabytes.toFixed(megabytes >= 100 ? 0 : 1)} MB`;
}

function formatAssetTime(updatedAt?: number) {
  if (!updatedAt) return "时间未知";
  const date = new Date(updatedAt * 1000);
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) return `今天 ${date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}

function formatDuration(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "--:--";
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function publishAssetMediaUrl(asset: PublishAsset) {
  return asset.media_url || `/api/v1/publish/assets/media?name=${encodeURIComponent(asset.name)}`;
}

function publishAssetPreviewUrl(asset: PublishAsset) {
  const mediaUrl = publishAssetMediaUrl(asset);
  return `${mediaUrl}${mediaUrl.includes("?") ? "&" : "?"}preview=1`;
}

function platformHint(platform: PublishPlatformCapability) {
  if (platform.platform === "xiaohongshu") {
    return "系统在本机官方创作端准备视频和文案；请你检查后手动点击发布。";
  }
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
  const [publishStage, setPublishStage] = useState<PublishStage>("select");
  const [assetSearch, setAssetSearch] = useState("");
  const [assetSort, setAssetSort] = useState<"recent" | "name">("recent");
  const [taskDrawerOpen, setTaskDrawerOpen] = useState(false);
  const [selectedVideoMeta, setSelectedVideoMeta] = useState({ duration: 0, width: 0, height: 0 });
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [addingAccount, setAddingAccount] = useState(false);
  const [accounts, setAccounts] = useState<PublishAccount[]>([]);
  const [publishSafety, setPublishSafety] = useState<PublishSafetyItem[]>([]);
  const [accountNames, setAccountNames] = useState<Record<string, string>>({});
  const [selectedAccountIds, setSelectedAccountIds] = useState<Record<string, string>>({});
  const [availablePlatforms, setAvailablePlatforms] = useState<PublishPlatformCapability[]>([]);
  const [selectedConfigPlatform, setSelectedConfigPlatform] = useState("douyin");
  const [assets, setAssets] = useState<PublishAsset[]>([]);
  const [tasks, setTasks] = useState<PublishResponse[]>([]);
  const [selectedTaskIds, setSelectedTaskIds] = useState<string[]>([]);
  const [platforms, setPlatforms] = useState<string[]>(["douyin"]);
  const [videoPath, setVideoPath] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tagInput, setTagInput] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [metadataLoading, setMetadataLoading] = useState(false);
  const [metadataGenerated, setMetadataGenerated] = useState(false);
  const [metadataConfirmOpen, setMetadataConfirmOpen] = useState(false);
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

  const readyPlatformCount = useMemo(
    () => new Set(readyAccounts.map((account) => account.platform)).size,
    [readyAccounts],
  );

  const activeConfigPlatform = useMemo(
    () => availablePlatforms.find((platform) => platform.platform === selectedConfigPlatform) || availablePlatforms[0],
    [availablePlatforms, selectedConfigPlatform],
  );

  const activePlatformAccounts = useMemo(
    () => accounts.filter((account) => account.platform === activeConfigPlatform?.platform),
    [accounts, activeConfigPlatform],
  );

  const selectedAsset = useMemo(
    () => assets.find((asset) => asset.path === videoPath) || null,
    [assets, videoPath],
  );

  const filteredAssets = useMemo(() => {
    const keyword = assetSearch.trim().toLowerCase();
    const filtered = keyword
      ? assets.filter((asset) => publishAssetTitle(asset).toLowerCase().includes(keyword) || asset.name.toLowerCase().includes(keyword))
      : [...assets];
    return filtered.sort((left, right) => assetSort === "name"
      ? publishAssetTitle(left).localeCompare(publishAssetTitle(right), "zh-CN")
      : (right.updated_at || 0) - (left.updated_at || 0));
  }, [assetSearch, assetSort, assets]);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [platformData, assetData, batchData, accountData, safetyData] = await Promise.all([
        listPublishPlatforms(),
        listPublishAssets(),
        listPublishBatches(),
        listPublishAccounts(),
        getPublishSafety().catch(() => [] as PublishSafetyItem[]),
      ]);
      const readyByPlatform = accountData.reduce<Record<string, string>>((result, account) => {
        if (account.status === "ready" && !result[account.platform]) result[account.platform] = account.account_id;
        return result;
      }, {});
      setAvailablePlatforms(platformData.platforms);
      setSelectedConfigPlatform((current) => (
        platformData.platforms.some((platform) => platform.platform === current)
          ? current
          : platformData.platforms[0]?.platform || current
      ));
      setAssets((current) => [
        ...assetData.items,
        ...current.filter((asset) => !assetData.items.some((item) => item.path === asset.path)),
      ]);
      setVideoPath((current) => current || assetData.items[0]?.path || "");
      const loadedTasks = batchData.items.flatMap((batch) => batch.tasks);
      setTasks(loadedTasks);
      setSelectedTaskIds((current) => current.filter((taskId) => loadedTasks.some((task) => task.task_id === taskId)));
      setAccounts(accountData);
      setPublishSafety(safetyData);
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
        setMetadataGenerated(false);
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
    const normalizedName = (accountNames[platform] ?? "").trim();
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
      setAccountNames((current) => ({ ...current, [platform]: "" }));
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

  const generateMetadataForSelectedVideo = useCallback(async () => {
    if (!selectedAsset) {
      toast.warning("请先选择成片");
      return;
    }
    const sourceText = selectedAsset.source_text?.trim() || "";
    if (!sourceText) {
      toast.warning("这条成片没有可用口播稿，请在下方手动填写发布内容");
      return;
    }
    setMetadataLoading(true);
    try {
      const result = await generatePublishMetadata({
        source_text: sourceText,
        platforms,
        source_task_id: selectedAsset.source_task_id || undefined,
      });
      setTitle(result.title.slice(0, 100));
      setDescription(result.description.slice(0, 1000));
      setTags(result.tags.map(String).filter(Boolean).slice(0, 8));
      setPreflight(null);
      setMetadataGenerated(true);
      setMetadataConfirmOpen(false);
      toast.success("已生成发布信息，请确认后发布");
    } catch (error) {
      toast.error((error as Error).message || "生成发布信息失败");
    } finally {
      setMetadataLoading(false);
    }
  }, [platforms, selectedAsset, toast]);

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
      toast.success(platforms.includes("xiaohongshu")
        ? "发布准备已创建；小红书会准备视频和文案，请在官方创作端检查后手动点击发布。"
        : "发布任务已创建；系统会自动选抖音配乐并填写发布页");
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
        const capability = availablePlatforms.find((item) => item.platform === platform);
        if (capability && !capability.requires_account) return false;
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
        content: platforms.includes("xiaohongshu")
          ? "系统会上传视频并填写文案。小红书只会准备官方创作端，请你检查后手动点击发布；登录、验证码或风控验证也由你处理。结果不明确时系统会停止，不会重复发布。"
          : "系统会上传视频、填写文案、自动选择抖音官方推荐配乐并提交发布。你只需在官方窗口完成登录、验证码或风控验证；结果不明确时系统会停止，不会重复发布。",
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

  const activePlatformReadyAccounts = activePlatformAccounts.filter((account) => account.status === "ready");
  const accountPlatformCount = availablePlatforms.filter((platform) => platform.requires_account).length;
  const canContinueFromConfiguration = Boolean(
    activeConfigPlatform && (!activeConfigPlatform.requires_account || activePlatformReadyAccounts.length),
  );

  const continueWithActivePlatform = () => {
    if (!activeConfigPlatform || !canContinueFromConfiguration) return;
    const readyAccount = activePlatformReadyAccounts[0];
    setPlatforms([activeConfigPlatform.platform]);
    if (readyAccount) {
      setSelectedAccountIds((current) => ({ ...current, [activeConfigPlatform.platform]: readyAccount.account_id }));
    }
    setPageStep("publish");
    setPublishStage("select");
  };

  const togglePublishPlatform = (platform: string) => {
    if (platforms.includes(platform) && platforms.length === 1) {
      toast.warning("至少保留一个发布平台");
      return;
    }
    setPlatforms((current) => current.includes(platform)
      ? current.filter((item) => item !== platform)
      : [...current, platform]);
    setPreflight(null);
  };

  const chooseAsset = (asset: PublishAsset) => {
    setVideoPath(asset.path);
    setNativeMusicHint(asset.recommended_music_hint || "");
    setMetadataGenerated(false);
    setPreflight(null);
    setSelectedVideoMeta({ duration: 0, width: 0, height: 0 });
  };

  const publishTargetOptions = availablePlatforms.map((capability) => {
    const platform = capability.platform;
    const account = accounts.find((item) => item.account_id === selectedAccountIds[platform]);
    return {
      platform,
      accountName: capability?.requires_account === false ? "无需登录" : account?.name || "待选择账号",
      ready: capability?.requires_account === false || account?.status === "ready",
      statusLabel: capability?.requires_account === false ? "无需登录" : account?.status === "ready" ? "已登录" : "待登录",
    };
  });
  const selectedAccountSummary = publishTargetOptions.filter((item) => platforms.includes(item.platform));

  return <div className="publish-page">
    {publishSafety.filter((item) => item.blocked).length > 0 && (
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="有发布账号处于防封暂停"
        description={
          <div>
            {publishSafety.filter((item) => item.blocked).map((item) => (
              <div key={`${item.platform}-${item.account_id}`} style={{ marginBottom: 6 }}>
                <b>{platformLabel(item.platform)}</b>（{item.account_id === "default" ? "默认账号" : item.account_id}）：
                {item.blocked_reason}
                <Button
                  size="small"
                  style={{ marginLeft: 8 }}
                  onClick={async () => {
                    try {
                      const result = await resumePublishSafety({
                        platform: item.platform,
                        account_id: item.account_id === "default" ? undefined : item.account_id,
                      });
                      toast.success(result.message || "已恢复");
                      setPublishSafety(await getPublishSafety());
                    } catch (error) {
                      toast.error((error as Error).message || "恢复失败");
                    }
                  }}
                >
                  立即恢复
                </Button>
              </div>
            ))}
          </div>
        }
      />
    )}
    {publishSafety.some((item) => item.remaining_today < item.daily_limit) && (
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="发布保护已开启"
        description={
          <div>
            每个平台每天最多发布 {publishSafety[0]?.daily_limit ?? 5} 条，两条之间至少间隔 15 分钟（防封保护，可在 .env 调整）。
            {publishSafety.map((item) => (
              item.remaining_today < item.daily_limit ? (
                <div key={`${item.platform}-${item.account_id}`} style={{ marginTop: 4 }}>
                  {platformLabel(item.platform)}：今日已发 {item.today_published} 条，剩余 {item.remaining_today} 条
                </div>
              ) : null
            ))}
          </div>
        }
      />
    )}
    {pageStep === "configure" && <><div className="publish-page-heading">
      <div>
        <Title level={3} style={{ margin: 0 }}>发布中心</Title>
        <Text type="secondary">连接发布账号，选择成片，确认后再提交到平台。</Text>
      </div>
      <div className="publish-connection-summary">
        <span className="publish-connection-dot" />
        已连接 {readyPlatformCount} / {accountPlatformCount} 个需登录平台
      </div>
    </div>

    <div className="publish-steps" aria-label="发布步骤">
      <button className={pageStep === "configure" ? "active" : "complete"} onClick={() => setPageStep("configure")}>
        <span>1</span><b>配置账号</b>
      </button>
      <div className="publish-step-line" />
      <button onClick={() => setPageStep("publish")}>
        <span>2</span><b>选择成片</b>
      </button>
    </div></>}

    {pageStep === "configure" ? <Spin spinning={loading}>
      <section className="publish-account-workspace">
        <aside className="publish-platform-sidebar">
          <div className="publish-platform-sidebar-title">发布平台</div>
          <nav aria-label="发布平台列表">
            {availablePlatforms.map((platform) => {
              const platformAccounts = accounts.filter((account) => account.platform === platform.platform);
              const readyCount = platformAccounts.filter((account) => account.status === "ready").length;
              const waitingCount = platformAccounts.filter((account) => account.status === "browser_open").length;
              const isActive = activeConfigPlatform?.platform === platform.platform;
              const statusText = !platform.requires_account
                ? "人工发布"
                : readyCount
                  ? `已登录 ${readyCount}`
                  : waitingCount
                    ? "等待登录"
                    : "未登录";
              return <button
                key={platform.platform}
                type="button"
                className={`publish-platform-item${isActive ? " active" : ""}`}
                onClick={() => setSelectedConfigPlatform(platform.platform)}
              >
                <span className="publish-platform-icon" style={{ color: PLATFORM_TONES[platform.platform] }}>
                  {PLATFORM_ICONS[platform.platform] || <SendOutlined />}
                </span>
                <span className="publish-platform-name">{platformLabel(platform.platform)}</span>
                <span className={`publish-platform-status${readyCount ? " ready" : waitingCount ? " waiting" : ""}`}>{statusText}</span>
              </button>;
            })}
          </nav>
          <div className="publish-platform-sidebar-note">各平台账号独立保存，只在本机官方窗口登录。</div>
        </aside>

        <main className="publish-platform-detail">
          {!activeConfigPlatform ? <Empty description="暂无可配置的发布平台" /> : <>
            <header className="publish-platform-detail-header">
              <div className="publish-platform-heading-line">
                <span className="publish-platform-large-icon" style={{ color: PLATFORM_TONES[activeConfigPlatform.platform] }}>
                  {PLATFORM_ICONS[activeConfigPlatform.platform] || <SendOutlined />}
                </span>
                <div>
                  <Title level={4} style={{ margin: 0 }}>{platformLabel(activeConfigPlatform.platform)}账号</Title>
                  <Text type="secondary">{platformHint(activeConfigPlatform)}</Text>
                </div>
              </div>
              <Tag color={activeConfigPlatform.requires_account ? "blue" : "gold"}>
                {activeConfigPlatform.requires_account ? "官方窗口登录" : "人工发布"}
              </Tag>
            </header>

            {activeConfigPlatform.requires_account ? <>
              <div className="publish-add-account-row">
                <Input
                  aria-label={`${platformLabel(activeConfigPlatform.platform)}账号名称`}
                  value={accountNames[activeConfigPlatform.platform] ?? ""}
                  placeholder="例如：公司主号"
                  onChange={(event) => setAccountNames((current) => ({ ...current, [activeConfigPlatform.platform]: event.target.value }))}
                  onPressEnter={() => void addAndConnectAccount(activeConfigPlatform.platform)}
                />
                <Button type="primary" icon={<UserAddOutlined />} loading={addingAccount} onClick={() => void addAndConnectAccount(activeConfigPlatform.platform)}>添加并扫码</Button>
              </div>

              <div className="publish-account-list-heading">
                <Text strong>已添加账号</Text>
                <Text type="secondary">{activePlatformAccounts.length} 个</Text>
              </div>

              {activePlatformAccounts.length === 0 ? <div className="publish-account-empty">
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`还没有${platformLabel(activeConfigPlatform.platform)}账号`} />
              </div> : <div className="publish-account-list">
                {activePlatformAccounts.map((account) => {
                  const status = accountStatusMeta(account.status);
                  return <div className="publish-account-row" key={account.account_id}>
                    <div className="publish-account-avatar" style={{ color: PLATFORM_TONES[account.platform] }}>
                      {PLATFORM_ICONS[account.platform] || <SendOutlined />}
                    </div>
                    <div className="publish-account-copy">
                      <Text strong>{account.name}</Text>
                      <Text type="secondary">{account.message || "等待账号状态更新"}</Text>
                    </div>
                    <Tag color={status.color}>{status.label}</Tag>
                    {account.status === "ready" ? <Button
                      type="link"
                      icon={<CheckCircleOutlined />}
                      onClick={() => {
                        setSelectedAccountIds((current) => ({ ...current, [account.platform]: account.account_id }));
                        setPlatforms([account.platform]);
                        setPageStep("publish");
                      }}
                    >用于发布</Button> : <Button type="link" loading={connecting === account.account_id} onClick={() => void connectAccount(account.account_id)}>打开浏览器登录</Button>}
                    <Button type="text" danger aria-label={`移除${account.name}`} icon={<MoreOutlined />} onClick={() => removeAccount(account)} />
                  </div>;
                })}
              </div>}
            </> : <div className="publish-manual-platform">
              <CheckCircleOutlined />
              <div>
                <Text strong>无需登录账号</Text>
                <Text type="secondary">系统会准备成片、标题、描述和话题。最后一步由你在该平台官方页面完成，并回填实际结果。</Text>
              </div>
            </div>}

            <footer className="publish-platform-detail-footer">
              <Text type="secondary">
                {canContinueFromConfiguration ? "当前平台已可继续" : `请先完成${platformLabel(activeConfigPlatform.platform)}账号登录`}
              </Text>
              <Button type="primary" disabled={!canContinueFromConfiguration} onClick={continueWithActivePlatform}>去选择成片</Button>
            </footer>
          </>}
        </main>
      </section>
    </Spin> : <Spin spinning={loading || submitting}>
      {publishStage === "select" ? <section className="publish-selection-shell">
        <header className="publish-destination-bar">
          <div className="publish-target-picker">
            <div className="publish-target-label">
              <strong>发布平台</strong>
              <span>可多选 · 已选 {platforms.length} 个</span>
            </div>
            <div className="publish-target-options" role="group" aria-label="选择发布平台">
              {publishTargetOptions.map((item) => {
                const selected = platforms.includes(item.platform);
                return <button
                  key={item.platform}
                  type="button"
                  className={`publish-target-option${selected ? " selected" : ""}`}
                  aria-pressed={selected}
                  aria-label={`${platformLabel(item.platform)} ${item.accountName}`}
                  onClick={() => togglePublishPlatform(item.platform)}
                >
                  <span className="publish-target-icon" style={{ color: PLATFORM_TONES[item.platform] }}>
                    {PLATFORM_ICONS[item.platform] || <SendOutlined />}
                  </span>
                  <span>{platformLabel(item.platform)}</span>
                  <small>{item.statusLabel}</small>
                  <i className={item.ready ? "ready" : "not-ready"} />
                </button>;
              })}
            </div>
          </div>
          <div className="publish-destination-actions">
            <Button type="text" icon={<ClockCircleOutlined />} onClick={() => setTaskDrawerOpen(true)}>任务记录 <Tag color="purple">{tasks.length}</Tag></Button>
            <Button type="link" onClick={() => setPageStep("configure")}>管理账号</Button>
          </div>
        </header>

        <div className="publish-selection-heading">
          <div>
            <Title level={3}>选择成片</Title>
            <Text type="secondary">从已经完成的视频中选择一条</Text>
          </div>
          <Upload accept=".mp4,.mov,.m4v" showUploadList={false} customRequest={async (options) => {
            try {
              const asset = await uploadPublishAsset(options.file as File);
              setAssets((current) => [asset, ...current]);
              chooseAsset(asset);
              options.onSuccess?.(asset);
              toast.success("成片已上传");
            } catch (error) {
              options.onError?.(error as Error);
              toast.error((error as Error).message || "上传失败");
            }
          }}><Button icon={<UploadOutlined />}>上传成片</Button></Upload>
        </div>

        <div className="publish-selection-grid">
          <section className="publish-asset-panel" aria-label="已完成成片">
            <div className="publish-asset-toolbar">
              <Input allowClear prefix={<SearchOutlined />} value={assetSearch} placeholder="搜索视频标题" onChange={(event) => setAssetSearch(event.target.value)} />
              <Select aria-label="成片排序" value={assetSort} onChange={setAssetSort} options={[{ label: "最近完成", value: "recent" }, { label: "按名称", value: "name" }]} />
              <Button aria-label="刷新成片" icon={<ReloadOutlined />} onClick={loadData} />
            </div>
            <div className="publish-asset-list">
              {filteredAssets.length ? filteredAssets.map((asset) => {
                const selected = asset.path === videoPath;
                return <button type="button" className={`publish-asset-row${selected ? " selected" : ""}`} key={asset.path} onClick={() => chooseAsset(asset)}>
                  <video className="publish-asset-thumb" src={publishAssetMediaUrl(asset)} muted preload="metadata" onLoadedMetadata={(event) => { event.currentTarget.currentTime = Math.min(0.1, event.currentTarget.duration || 0.1); }} />
                  <span className="publish-asset-copy">
                    <strong>{publishAssetTitle(asset)}</strong>
                    <span><VideoCameraOutlined /> {formatAssetSize(asset.size_bytes)} <b>9:16</b></span>
                  </span>
                  <span className="publish-asset-updated">{formatAssetTime(asset.updated_at)}</span>
                  {selected && <CheckCircleOutlined className="publish-asset-selected-icon" />}
                </button>;
              }) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={assetSearch ? "没有匹配的成片" : "没有待发布的素材了，可回到创作步骤继续制作新视频"} />}
            </div>
          </section>

          <aside className="publish-preview-panel" aria-label="已选择的视频">
            <Text strong className="publish-preview-title">已选择的视频</Text>
            {selectedAsset ? <>
              <div className="publish-preview-frame">
                <video
                  key={selectedAsset.path}
                  controls
                  preload="metadata"
                  src={publishAssetPreviewUrl(selectedAsset)}
                  onLoadedMetadata={(event) => {
                    const video = event.currentTarget;
                    video.currentTime = Math.min(0.1, video.duration || 0.1);
                    setSelectedVideoMeta({ duration: video.duration, width: video.videoWidth, height: video.videoHeight });
                  }}
                />
              </div>
              <Title level={4} className="publish-preview-name">{publishAssetTitle(selectedAsset)}</Title>
              <div className="publish-preview-meta">
                <span><ClockCircleOutlined /><b>{formatDuration(selectedVideoMeta.duration)}</b><small>时长</small></span>
                <span><VideoCameraOutlined /><b>{selectedVideoMeta.height ? `${selectedVideoMeta.height}P` : "视频"}</b><small>分辨率</small></span>
                <span><CalendarOutlined /><b>{formatAssetTime(selectedAsset.updated_at)}</b><small>创建时间</small></span>
              </div>
              <Button icon={<EyeOutlined />} onClick={() => document.querySelector<HTMLVideoElement>(".publish-preview-frame video")?.play()}>播放预览</Button>
            </> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请先从左侧选择一条成片" />}
          </aside>
        </div>

        <footer className="publish-selection-footer">
          <Text>已选择 <b>{selectedAsset ? 1 : 0}</b> 条成片</Text>
          <Button type="primary" size="large" disabled={!selectedAsset} onClick={() => setPublishStage("review")}>下一步：检查发布内容</Button>
        </footer>
      </section> : <section className="publish-review-shell">
        <header className="publish-review-heading">
          <div>
            <Button type="text" icon={<LeftOutlined />} onClick={() => setPublishStage("select")}>返回选择成片</Button>
            <Title level={3}>检查发布内容</Title>
            <Text type="secondary">确认标题、描述和话题后再开始发布</Text>
          </div>
          <Button icon={<ClockCircleOutlined />} onClick={() => setTaskDrawerOpen(true)}>任务记录 {tasks.length}</Button>
        </header>

        <div className="publish-review-grid">
          <main className="publish-review-form">
            <div className="publish-review-asset">
              {selectedAsset && <video src={publishAssetMediaUrl(selectedAsset)} muted preload="metadata" />}
              <div><Text type="secondary">本次成片</Text><Text strong>{selectedAsset ? publishAssetTitle(selectedAsset) : "尚未选择"}</Text></div>
              <Button type="link" onClick={() => setPublishStage("select")}>重新选择</Button>
            </div>

            <div className="publish-review-section">
              <div className="publish-review-section-heading">
                <div><Text strong>发布文案</Text><Text type="secondary">可以手动填写，也可以根据口播稿生成</Text></div>
                <Button loading={metadataLoading} disabled={!selectedAsset?.source_text?.trim()} onClick={() => setMetadataConfirmOpen(true)}>{metadataGenerated ? "重新生成" : "生成发布信息"}</Button>
              </div>
              <Space direction="vertical" size={12} style={{ width: "100%" }}>
                <Input aria-label="发布标题" placeholder="输入发布标题" value={title} onChange={(event) => { setTitle(event.target.value); setPreflight(null); }} maxLength={100} showCount />
                <TextArea aria-label="发布描述" placeholder="输入发布描述（可选）" rows={5} value={description} onChange={(event) => { setDescription(event.target.value); setPreflight(null); }} maxLength={1000} showCount />
                <Space.Compact style={{ width: "100%" }}><Input aria-label="添加话题标签" prefix={<TagOutlined />} placeholder="输入话题，按回车添加" value={tagInput} onChange={(event) => setTagInput(event.target.value)} onPressEnter={addTag} /><Button onClick={addTag}>添加</Button></Space.Compact>
                <div>{tags.map((tag) => <Tag key={tag} closable color="purple" onClose={() => setTags((current) => current.filter((item) => item !== tag))}>#{tag}</Tag>)}</div>
              </Space>
            </div>

            {platforms.includes("douyin") && <Alert type="success" showIcon message="抖音原生配乐会自动匹配" description={`当前方向：${nativeMusicHint || inferDouyinMusicHint(title, description)}。只有登录或验证码需要你接手。`} />}
            {preflight && <Alert type={preflight.blocked ? "warning" : "success"} showIcon message={preflight.blocked ? "请先处理以下问题" : "发布前检查通过"} description={<Space direction="vertical" size={4}>{preflight.issues.length > 0 && <Text>{preflight.issues.join("；")}</Text>}{preflight.platforms.filter((item) => item.issue).map((item) => <Text key={item.platform}>{platformLabel(item.platform)}：{item.issue}</Text>)}</Space>} />}
          </main>

          <aside className="publish-review-summary">
            <Text strong className="publish-review-summary-title">发布确认</Text>
            <div className="publish-review-destinations">
              {selectedAccountSummary.map((item) => <div key={item.platform}>
                <span style={{ color: PLATFORM_TONES[item.platform] }}>{PLATFORM_ICONS[item.platform] || <SendOutlined />}</span>
                <div><Text strong>{platformLabel(item.platform)}</Text><Text type="secondary">{item.accountName}</Text></div>
                <Tag color={item.ready ? "success" : "warning"}>{item.ready ? "已连接" : "需配置"}</Tag>
              </div>)}
            </div>
            <Divider />
            <Text type="secondary">确认后系统才会创建发布任务；需要验证码或平台结果不明确时会暂停并提醒你。</Text>
              {!selectedAccountSummary.every((item) => item.ready) && <Button type="link" onClick={() => setPageStep("configure")}>先去配置发布账号</Button>}
              <Button type="primary" size="large" icon={<RocketOutlined />} disabled={!title.trim() || !selectedAccountSummary.every((item) => item.ready)} loading={submitting} onClick={startPublishing}>确认并开始发布</Button>
          </aside>
        </div>
      </section>}
    </Spin>}

    <Modal
      title="生成发布信息？"
      open={metadataConfirmOpen}
      onCancel={() => setMetadataConfirmOpen(false)}
      onOk={() => void generateMetadataForSelectedVideo()}
      confirmLoading={metadataLoading}
      okText="生成"
      cancelText="取消"
    >
      系统将根据这条成片对应的口播稿生成标题、描述和话题，会调用一次 AI 文案服务。费用按实际模型计费，暂无法准确估算。
    </Modal>

    <Drawer
      title={<Space><ClockCircleOutlined />任务记录<Tag color="purple">{tasks.length}</Tag></Space>}
      width={920}
      open={taskDrawerOpen}
      onClose={() => setTaskDrawerOpen(false)}
      extra={<Space><Button danger icon={<DeleteOutlined />} disabled={!selectedTaskIds.length} onClick={() => confirmDeleteTasks(selectedTaskIds)}>删除{selectedTaskIds.length ? ` ${selectedTaskIds.length} 条` : ""}</Button><Button icon={<ReloadOutlined />} onClick={loadData}>刷新</Button></Space>}
    >
      {loading ? <SkeletonCard rows={5} /> : tasks.length ? <Table rowKey="task_id" columns={columns} dataSource={tasks} rowSelection={{ selectedRowKeys: selectedTaskIds, onChange: (keys) => setSelectedTaskIds(keys.map(String)), getCheckboxProps: (task) => ({ disabled: !canDeleteTask(task) }) }} pagination={{ pageSize: 8, showSizeChanger: false }} size="middle" scroll={{ x: 760 }} /> : <Empty description="还没有发布任务" />}
    </Drawer>

    <style>{`
      .publish-page {
        width: 100%;
        max-width: 1180px;
        margin: 0 auto;
      }
      .publish-page-heading {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 24px;
        margin-bottom: 22px;
      }
      .publish-page-heading .ant-typography-secondary {
        display: block;
        margin-top: 6px;
      }
      .publish-connection-summary {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 9px 12px;
        border: 1px solid #e3e7ef;
        border-radius: 8px;
        color: var(--text-secondary, #64748b);
        background: #fff;
        font-size: 13px;
        white-space: nowrap;
      }
      .publish-connection-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #22c55e;
        box-shadow: 0 0 0 4px #dcfce7;
      }
      .publish-steps {
        display: flex;
        align-items: center;
        justify-content: flex-start;
        width: min(460px, 100%);
        margin: 0 0 22px;
      }
      .publish-steps button {
        display: flex;
        align-items: center;
        gap: 9px;
        padding: 0;
        border: 0;
        color: #94a3b8;
        background: transparent;
        cursor: pointer;
      }
      .publish-steps button span {
        display: grid;
        place-items: center;
        width: 28px;
        height: 28px;
        border: 1px solid #d8dee9;
        border-radius: 50%;
        font-size: 13px;
        font-weight: 700;
        background: #fff;
      }
      .publish-steps button.active,
      .publish-steps button.complete { color: var(--primary-600, #7c3aed); }
      .publish-steps button.active span,
      .publish-steps button.complete span {
        border-color: var(--primary-600, #7c3aed);
        color: #fff;
        background: var(--primary-600, #7c3aed);
      }
      .publish-step-line {
        flex: 1;
        height: 1px;
        margin: 0 18px;
        background: #e5e7eb;
      }
      .publish-account-workspace {
        display: grid;
        grid-template-columns: 252px minmax(0, 1fr);
        min-height: max(520px, calc(100vh - 250px));
        overflow: hidden;
        border: 1px solid #e3e7ef;
        border-radius: 14px;
        background: #fff;
        box-shadow: 0 10px 30px rgba(15, 23, 42, .04);
      }
      .publish-platform-sidebar {
        display: flex;
        flex-direction: column;
        padding: 20px 14px;
        border-right: 1px solid #e9edf3;
        background: #fafbfc;
      }
      .publish-platform-sidebar-title {
        padding: 0 12px 10px;
        color: #94a3b8;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: .08em;
      }
      .publish-platform-sidebar nav { display: grid; gap: 5px; }
      .publish-platform-item {
        display: grid;
        grid-template-columns: 28px minmax(0, 1fr) auto;
        align-items: center;
        gap: 9px;
        width: 100%;
        min-height: 48px;
        padding: 8px 10px;
        border: 1px solid transparent;
        border-radius: 9px;
        color: var(--text-primary, #172033);
        background: transparent;
        text-align: left;
        cursor: pointer;
      }
      .publish-platform-item:hover { background: #f3f4f7; }
      .publish-platform-item.active {
        border-color: #d7c8ff;
        background: #f4f0ff;
      }
      .publish-platform-icon {
        display: grid;
        place-items: center;
        font-size: 19px;
      }
      .publish-platform-name { font-weight: 650; }
      .publish-platform-status {
        color: #94a3b8;
        font-size: 12px;
        white-space: nowrap;
      }
      .publish-platform-status.ready { color: #059669; }
      .publish-platform-status.waiting { color: #d97706; }
      .publish-platform-sidebar-note {
        margin-top: auto;
        padding: 16px 12px 2px;
        border-top: 1px solid #e8ebf1;
        color: #94a3b8;
        font-size: 12px;
        line-height: 1.6;
      }
      .publish-platform-detail {
        display: flex;
        min-width: 0;
        flex-direction: column;
        padding: 28px 32px 22px;
      }
      .publish-platform-detail-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 20px;
        padding-bottom: 22px;
        border-bottom: 1px solid #eef0f4;
      }
      .publish-platform-heading-line {
        display: flex;
        align-items: center;
        gap: 14px;
      }
      .publish-platform-large-icon {
        display: grid;
        place-items: center;
        width: 44px;
        height: 44px;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        background: #fff;
        font-size: 23px;
      }
      .publish-platform-heading-line .ant-typography-secondary {
        display: block;
        margin-top: 4px;
        line-height: 1.55;
      }
      .publish-add-account-row {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 10px;
        margin-top: 22px;
      }
      .publish-manual-platform {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        margin-top: 16px;
        padding: 12px 14px;
        border: 1px solid #ddd2ff;
        border-radius: 9px;
        color: #6842b8;
        background: #faf8ff;
        font-size: 13px;
      }
      .publish-account-list-heading {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin: 24px 0 10px;
      }
      .publish-account-empty {
        display: grid;
        min-height: 150px;
        place-items: center;
        border: 1px dashed #dfe4ec;
        border-radius: 10px;
        background: #fcfcfd;
      }
      .publish-account-list {
        overflow: hidden;
        border: 1px solid #e6e9ef;
        border-radius: 10px;
      }
      .publish-account-row {
        display: grid;
        grid-template-columns: 38px minmax(160px, 1fr) auto auto 34px;
        align-items: center;
        gap: 12px;
        min-height: 72px;
        padding: 12px 14px;
      }
      .publish-account-row + .publish-account-row { border-top: 1px solid #edf0f4; }
      .publish-account-avatar {
        display: grid;
        place-items: center;
        width: 36px;
        height: 36px;
        border-radius: 50%;
        background: #f3f4f6;
        font-size: 18px;
      }
      .publish-account-copy { min-width: 0; }
      .publish-account-copy .ant-typography {
        display: block;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .publish-account-copy .ant-typography-secondary { margin-top: 3px; font-size: 12px; }
      .publish-manual-platform {
        margin-top: 24px;
        padding: 18px;
        border-color: #e4dcff;
        color: var(--primary-600, #7c3aed);
        background: #faf8ff;
      }
      .publish-manual-platform .ant-typography { display: block; }
      .publish-manual-platform .ant-typography-secondary { margin-top: 5px; line-height: 1.65; }
      .publish-platform-detail-footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 20px;
        margin-top: auto;
        padding-top: 24px;
      }
      .publish-platform-detail-footer .ant-btn { min-width: 132px; }
      .publish-selection-shell,
      .publish-review-shell {
        min-height: calc(100vh - 132px);
      }
      .publish-destination-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 20px;
        min-height: 52px;
        padding: 8px 14px;
        border: 1px solid #e3e7ef;
        border-radius: 10px;
        background: #fff;
      }
      .publish-target-picker,
      .publish-target-label,
      .publish-target-options,
      .publish-target-option,
      .publish-destination-actions {
        display: flex;
        align-items: center;
      }
      .publish-target-picker { min-width: 0; gap: 14px; }
      .publish-target-label { align-items: baseline; flex: none; gap: 7px; white-space: nowrap; }
      .publish-target-label strong { color: #172033; }
      .publish-target-label span { color: #94a3b8; font-size: 12px; }
      .publish-target-options { min-width: 0; gap: 7px; flex-wrap: wrap; }
      .publish-target-option {
        gap: 6px;
        height: 34px;
        padding: 0 10px;
        color: #64748b;
        border: 1px solid #e3e7ef;
        border-radius: 8px;
        background: #fff;
        cursor: pointer;
        transition: border-color .16s ease, background .16s ease, color .16s ease;
      }
      .publish-target-option:hover { color: #5b21b6; border-color: #c4b5fd; }
      .publish-target-option.selected { color: #5b21b6; border-color: #8b5cf6; background: #f7f3ff; box-shadow: inset 0 0 0 1px #8b5cf6; }
      .publish-target-icon { display: grid; place-items: center; font-size: 15px; }
      .publish-target-option small { color: #94a3b8; font-size: 11px; }
      .publish-target-option.selected small { color: #7c3aed; }
      .publish-target-option i { width: 6px; height: 6px; border-radius: 50%; background: #d97706; }
      .publish-target-option i.ready { background: #10b981; }
      .publish-destination-actions { gap: 4px; }
      .publish-selection-heading {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 20px;
        padding: 20px 0 14px;
      }
      .publish-selection-heading > div { display: flex; align-items: baseline; gap: 16px; }
      .publish-selection-heading .ant-typography { margin: 0; }
      .publish-selection-grid {
        display: grid;
        grid-template-columns: minmax(0, 1.55fr) minmax(330px, 1fr);
        gap: 18px;
      }
      .publish-asset-panel,
      .publish-preview-panel,
      .publish-review-form,
      .publish-review-summary {
        overflow: hidden;
        border: 1px solid #e2e6ed;
        border-radius: 12px;
        background: #fff;
        box-shadow: 0 8px 28px rgba(15, 23, 42, .035);
      }
      .publish-asset-toolbar {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 132px 40px;
        gap: 10px;
        padding: 14px;
        border-bottom: 1px solid #e8ebf0;
      }
      .publish-asset-list {
        min-height: 410px;
        max-height: calc(100vh - 390px);
        overflow-y: auto;
      }
      .publish-asset-list > .ant-empty { margin: 120px 0; }
      .publish-asset-row {
        position: relative;
        display: grid;
        grid-template-columns: 70px minmax(0, 1fr) 92px 24px;
        align-items: center;
        gap: 14px;
        width: 100%;
        min-height: 92px;
        padding: 10px 16px;
        border: 0;
        border-bottom: 1px solid #edf0f4;
        color: #172033;
        background: #fff;
        text-align: left;
        cursor: pointer;
        transition: background .16s ease, box-shadow .16s ease;
      }
      .publish-asset-row:hover { background: #faf9ff; }
      .publish-asset-row.selected {
        background: #f7f3ff;
        box-shadow: inset 3px 0 0 #7c3aed;
      }
      .publish-asset-thumb {
        width: 58px;
        height: 72px;
        border-radius: 6px;
        object-fit: cover;
        background: #111827;
      }
      .publish-asset-copy { display: block; min-width: 0; }
      .publish-asset-copy strong {
        display: block;
        overflow: hidden;
        margin-bottom: 9px;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: 15px;
      }
      .publish-asset-copy > span { display: flex; align-items: center; gap: 7px; color: #94a3b8; font-size: 12px; }
      .publish-asset-copy b { font-weight: 500; }
      .publish-asset-updated { color: #64748b; font-size: 13px; text-align: right; }
      .publish-asset-selected-icon { color: #7c3aed; font-size: 20px; }
      .publish-preview-panel {
        display: flex;
        min-height: 500px;
        flex-direction: column;
        align-items: center;
        padding: 20px 24px;
      }
      .publish-preview-title { align-self: stretch; margin-bottom: 14px; font-size: 15px; }
      .publish-preview-frame {
        display: grid;
        width: min(100%, 190px);
        aspect-ratio: 9 / 16;
        place-items: center;
        overflow: hidden;
        border-radius: 10px;
        background: #111827;
        box-shadow: 0 12px 26px rgba(15, 23, 42, .16);
      }
      .publish-preview-frame video { width: 100%; height: 100%; object-fit: contain; background: #111827; }
      .publish-preview-name { width: 100%; margin: 14px 0 10px !important; text-align: center; }
      .publish-preview-meta { display: grid; grid-template-columns: repeat(3, 1fr); width: 100%; margin-bottom: 12px; }
      .publish-preview-meta > span { display: grid; grid-template-columns: 18px 1fr; gap: 2px 5px; padding: 0 8px; border-right: 1px solid #edf0f4; }
      .publish-preview-meta > span:last-child { border-right: 0; }
      .publish-preview-meta .anticon { grid-row: 1 / 3; align-self: center; color: #64748b; }
      .publish-preview-meta b { overflow: hidden; color: #334155; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
      .publish-preview-meta small { color: #94a3b8; font-size: 11px; }
      .publish-selection-footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 20px;
        margin-top: 14px;
        padding: 14px 2px 0;
      }
      .publish-selection-footer b { color: #7c3aed; }
      .publish-selection-footer .ant-btn { min-width: 214px; }
      .publish-review-heading {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 20px;
        margin-bottom: 16px;
      }
      .publish-review-heading .ant-btn-text { margin: 0 0 8px -12px; }
      .publish-review-heading .ant-typography { display: block; margin: 0; }
      .publish-review-grid { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 18px; }
      .publish-review-form { padding: 20px; }
      .publish-review-asset { display: grid; grid-template-columns: 54px minmax(0, 1fr) auto; align-items: center; gap: 12px; padding: 12px; border: 1px solid #ececf1; border-radius: 9px; background: #fafafa; }
      .publish-review-asset video { width: 48px; height: 62px; border-radius: 5px; object-fit: cover; background: #111827; }
      .publish-review-asset .ant-typography { display: block; }
      .publish-review-section { margin-top: 20px; }
      .publish-review-section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
      .publish-review-section-heading .ant-typography { display: block; }
      .publish-review-section-heading .ant-typography-secondary { margin-top: 3px; font-size: 12px; }
      .publish-review-form > .ant-alert { margin-top: 16px; }
      .publish-review-summary { align-self: start; padding: 20px; }
      .publish-review-summary-title { display: block; margin-bottom: 14px; font-size: 16px; }
      .publish-review-destinations { display: grid; gap: 8px; }
      .publish-review-destinations > div { display: grid; grid-template-columns: 28px minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 10px; border: 1px solid #ececf1; border-radius: 8px; }
      .publish-review-destinations .ant-typography { display: block; }
      .publish-review-destinations .ant-typography-secondary { font-size: 12px; }
      .publish-review-summary > .ant-typography-secondary { display: block; line-height: 1.7; }
      .publish-review-summary > .ant-btn { width: 100%; margin-top: 18px; }
      @media (max-width: 900px) {
        .publish-page-heading { align-items: flex-start; flex-direction: column; gap: 10px; }
        .publish-account-workspace { grid-template-columns: 1fr; }
        .publish-platform-sidebar { border-right: 0; border-bottom: 1px solid #e9edf3; }
        .publish-platform-sidebar nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .publish-platform-sidebar-note { display: none; }
        .publish-platform-detail { min-height: 430px; padding: 22px 18px; }
        .publish-destination-bar { align-items: flex-start; flex-direction: column; }
        .publish-target-picker { align-items: flex-start; flex-direction: column; }
        .publish-selection-grid,
        .publish-review-grid { grid-template-columns: 1fr; }
        .publish-asset-list { max-height: none; }
        .publish-preview-panel { min-height: 0; }
      }
      @media (max-width: 620px) {
        .publish-platform-sidebar nav { grid-template-columns: 1fr; }
        .publish-platform-detail-header { align-items: flex-start; flex-direction: column; }
        .publish-add-account-row { grid-template-columns: 1fr; }
        .publish-account-row { grid-template-columns: 36px minmax(0, 1fr) auto; }
        .publish-account-row > .ant-tag { grid-column: 2; }
        .publish-account-row > .ant-btn-link { grid-column: 2; justify-self: start; padding-left: 0; }
        .publish-account-row > .ant-btn-text { grid-column: 3; grid-row: 1; }
        .publish-selection-heading { align-items: flex-start; flex-direction: column; }
        .publish-selection-heading > div { align-items: flex-start; flex-direction: column; gap: 4px; }
        .publish-asset-toolbar { grid-template-columns: 1fr 112px; }
        .publish-asset-toolbar > .ant-btn { display: none; }
        .publish-asset-row { grid-template-columns: 58px minmax(0, 1fr) 22px; gap: 10px; padding: 9px 10px; }
        .publish-asset-updated { display: none; }
        .publish-selection-footer { align-items: stretch; flex-direction: column; }
        .publish-selection-footer .ant-btn { width: 100%; }
        .publish-review-asset { grid-template-columns: 48px minmax(0, 1fr); }
        .publish-review-asset .ant-btn { grid-column: 2; justify-self: start; padding-left: 0; }
      }
    `}</style>

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
