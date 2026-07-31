import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Empty,
  Input,
  InputNumber,
  List,
  Modal,
  Progress,
  Segmented,
  Select,
  Space,
  Steps,
  Tag,
  Timeline,
  Typography,
  Upload,
} from "antd";
import {
  CheckCircleOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RocketOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  UploadOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  confirmPublishTaskAuto,
  confirmProductionBatchPublish,
  connectPublishAccount,
  createCrawlerBatch,
  createPublishAccount,
  createProductionBatch,
  createProductionProfile,
  getAvatarCapabilities,
  getCrawlerBrowserDiscoveryCapabilities,
  getProductionWorkspaceConfiguration,
  getCrawlerBatch,
  getCrawlerHotWords,
  getPublishAccountStatus,
  getProductionBatchWorkspace,
  listAvatarAssets,
  listProductionBatches,
  listProductionProfiles,
  listPublishAccounts,
  listPublishPlatforms,
  pauseProductionBatch,
  preparePublishOfficialPage,
  preflightProductionBatch,
  preflightProductionBatchPublish,
  previewCrawlerBatch,
  recordManualPublishResult,
  resumeProductionBatch,
  retryProductionBatchFailed,
  reviewProductionBatchItems,
  saveProductionWorkspaceConfiguration,
  startCrawlerBrowserDiscovery,
  startProductionBatch,
  trainCloudVoice,
  uploadAvatarAsset,
} from "../api/client";
import type {
  AvatarAsset,
  AvatarCapability,
  CrawlerBatchResponse,
  CrawlerBrowserDiscoveryCapabilities,
  CrawlerCandidateResult,
  CrawlerHotWordItem,
  CrawlerSearchRequest,
  ProductionBatch,
  ProductionCreativePlan,
  ProductionProfile,
  ProductionPublishTarget,
  ProductionWorkspace,
  ProductionWorkspaceConfiguration,
  PublishAccount,
  PublishPlatformCapability,
} from "../api/types";
import { productionTaskTitle } from "../utils/productionTask";

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

type SourceMode = "keyword" | "share_link" | "script";
type CreationMode = "auto" | "manual";
type ReviewStage = "transcript" | "script" | "output" | "publish";
type BrowserPlatform = "douyin" | "xiaohongshu" | "kuaishou" | "bilibili";

const PROFILE_STORAGE_KEY = "pipeline.lastProfileId";
const AUTO_PRIMARY_LIMIT = 4;
const AUTO_RESERVE_LIMIT = 2;

const PLATFORM_LABELS: Record<string, string> = {
  douyin: "抖音",
  kuaishou: "快手",
  xiaohongshu: "小红书",
  bilibili: "哔哩哔哩",
  wechat_channels: "视频号",
};

const DEFAULT_PUBLISH_ACCOUNT_NAME = "公司主号";

const SOURCE_OPTIONS = [
  { label: "关键词找素材", value: "keyword" },
  { label: "视频链接", value: "share_link" },
  { label: "已有文案", value: "script" },
];

const SOURCE_BROWSER_PLATFORMS: Array<{ platform: BrowserPlatform; label: string }> = [
  { platform: "douyin", label: "抖音热点宝" },
  { platform: "kuaishou", label: "快手" },
  { platform: "bilibili", label: "B站" },
];

const WORKSPACE_STEPS = [
  { title: "01 告诉我想做什么" },
  { title: "02 确认创作方案" },
  { title: "03 查看成片并发布" },
];

const STATUS_LABEL: Record<string, string> = {
  planned: "待预检",
  pending: "等待中",
  queued: "排队中",
  running: "执行中",
  paused: "已暂停",
  awaiting_review: "待人工确认",
  awaiting_publish: "待确认发布",
  ready_to_publish: "成片待发布",
  skipped: "已参与选稿",
  succeeded: "已完成",
  completed: "已完成",
  failed: "失败",
  partial: "部分完成",
  blocked: "已阻断",
  outcome_unknown: "结果待核对",
};

const WORKBENCH_TASK_LIMIT = 3;

const STAGE_LABEL: Record<string, string> = {
  source: "素材与选题",
  transcript: "核对原转写",
  script: "确认创作方案",
  avatar: "数字人口播",
  editing: "剪辑成片",
  output: "复核成片",
  publish: "确认发布",
  completed: "已完成",
  media_resolution: "解析素材",
  transcription: "提取原转写",
  copywriting: "生成改写稿",
  human_review: "人工确认",
  avatar_generation: "数字人口播",
  video_editing: "剪辑成片",
  publishing: "确认发布",
};

function isUsableProfile(
  profile: ProductionProfile,
  assets: AvatarAsset[],
) {
  const avatar = assets.find((item) => item.asset_id === profile.avatar_id);
  const voice = assets.find((item) => item.asset_id === profile.voice_id);
  return Boolean(
    avatar?.kind === "avatar"
    && avatar.authorized
    && avatar.status === "ready"
    && voice?.kind === "voice"
    && voice.authorized
    && voice.status === "ready",
  );
}

function stageIndex(stage: string | null | undefined) {
  if (!stage || stage === "source" || stage === "media_resolution") return 0;
  if (["transcript", "script", "transcription", "copywriting", "human_review"].includes(stage)) return 1;
  if (["avatar", "avatar_generation"].includes(stage)) return 2;
  if (["editing", "video_editing"].includes(stage)) return 3;
  return 4;
}

function businessStageIndex(stage: string | null | undefined) {
  if (!stage || stage === "source" || stage === "media_resolution") return 0;
  if (["transcript", "script", "transcription", "copywriting", "human_review"].includes(stage)) return 1;
  return 2;
}

function buildCreativePlan(script: string): ProductionCreativePlan {
  const sentences = script
    .split(/(?<=[。！？!?])|\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  const hook = (sentences[0] || script.trim()).slice(0, 160);
  const body = sentences.slice(1, -1).filter(Boolean).slice(0, 3);
  const keyPoints = (body.length ? body : sentences.slice(0, 3)).map((value) => value.slice(0, 180));
  const callToAction = (sentences[sentences.length - 1] || "请根据实际情况选择下一步").slice(0, 160);
  return {
    hook,
    key_points: keyPoints.length ? keyPoints : ["讲清楚这条内容能帮客户解决什么"],
    call_to_action: callToAction,
    visual_sections: [
      `开场：${hook || "提出客户关心的问题"}`.slice(0, 180),
      `讲解：${(keyPoints[0] || "用一个具体要点讲清楚做法")}`.slice(0, 180),
      `收尾：${callToAction}`.slice(0, 180),
    ],
  };
}

function statusColor(status: string | null | undefined) {
  if (["succeeded", "completed", "ready_to_publish", "skipped"].includes(status || "")) return "success";
  if (["failed", "outcome_unknown"].includes(status || "")) return "error";
  if (["blocked", "partial", "awaiting_review", "awaiting_publish"].includes(status || "")) return "warning";
  if (["running", "queued"].includes(status || "")) return "processing";
  return "default";
}

function workbenchTaskAction(status: string) {
  if (status === "awaiting_review") return "继续确认";
  if (["awaiting_publish", "ready_to_publish"].includes(status)) return "确认发布";
  if (["failed", "blocked", "partial", "outcome_unknown"].includes(status)) return "查看原因";
  if (["running", "queued", "pending", "planned", "paused"].includes(status)) return "查看进度";
  return "查看结果";
}

function isFinishedBatch(batch: ProductionBatch) {
  return ["succeeded", "completed"].includes(batch.status);
}

function workbenchTaskPriority(batch: ProductionBatch, selectedBatchId: string) {
  if (batch.batch_id === selectedBatchId) return -1;
  if (["awaiting_review", "awaiting_publish", "ready_to_publish"].includes(batch.status)) return 0;
  if (["failed", "blocked", "partial", "outcome_unknown"].includes(batch.status)) return 1;
  return 2;
}

function candidateRank(candidate: CrawlerCandidateResult) {
  return candidate.system_rank ?? candidate.provider_hot_rank ?? candidate.platform_rank ?? 999_999;
}

function candidateSpokenUse(candidate: CrawlerCandidateResult) {
  if (candidate.spoken_material_status === "transcript_ready") {
    return { usable: true, label: "已有可用口播" };
  }
  if (candidate.spoken_material_status === "text_reference") {
    return { usable: true, label: "有文字，可改成口播" };
  }
  const title = (candidate.title || "").replace(/#[^#\s]+/g, " ");
  const looksLikeExplanation = /(首先|其次|为什么|怎么|如何|导致|解决|注意|避坑|教程|讲解|分析|测评|实测|区别|问题|原因|方法|技巧|如果|只要|千万|不要|老板|很多人|我们|咱们)/.test(title);
  if (looksLikeExplanation) {
    return { usable: true, label: "疑似有讲解，待 ASR 确认" };
  }
  return { usable: false, label: "疑似纯展示，仅作画面参考" };
}

function strictCandidates(batch: CrawlerBatchResponse, preferredCandidateId = "") {
  const byId = new Map<string, CrawlerCandidateResult>();
  batch.platform_runs.forEach((run) => {
    (run.candidates || []).forEach((candidate) => {
      byId.set(candidate.video_id, { ...candidate, selection_tier: "priority" });
    });
    (run.low_incremental_candidates || []).forEach((candidate) => {
      if (!byId.has(candidate.video_id)) {
        byId.set(candidate.video_id, { ...candidate, selection_tier: "reserve" });
      }
    });
  });
  const preferred = preferredCandidateId ? byId.get(preferredCandidateId) : undefined;
  const ranked = [...byId.values()]
    .sort((left, right) => {
      const spoken = Number(candidateSpokenUse(right).usable)
        - Number(candidateSpokenUse(left).usable);
      if (spoken !== 0) return spoken;
      const tier = (left.selection_tier === "reserve" ? 1 : 0)
        - (right.selection_tier === "reserve" ? 1 : 0);
      if (tier !== 0) return tier;
      const rank = candidateRank(left) - candidateRank(right);
      if (rank !== 0) return rank;
      return (right.trend_score ?? -1) - (left.trend_score ?? -1);
    });
  if (!preferred) return ranked;
  return [preferred, ...ranked.filter((item) => item.video_id !== preferred.video_id)];
}

function selectAutomaticCandidates(candidates: CrawlerCandidateResult[], limit = 4) {
  const ranked = [...candidates].sort((left, right) => {
    const tier = (left.selection_tier === "reserve" ? 1 : 0)
      - (right.selection_tier === "reserve" ? 1 : 0);
    if (tier !== 0) return tier;
    const rank = candidateRank(left) - candidateRank(right);
    if (rank !== 0) return rank;
    return (right.trend_score ?? -1) - (left.trend_score ?? -1);
  });
  const selected: CrawlerCandidateResult[] = [];
  for (const platform of ["douyin", "xiaohongshu", "kuaishou", "bilibili"]) {
    const candidate = ranked.find(
      (item) => item.platform === platform
        && !selected.some((selectedItem) => selectedItem.video_id === item.video_id),
    );
    if (candidate) selected.push(candidate);
  }
  for (const candidate of ranked) {
    if (selected.length >= limit) break;
    if (!selected.some((item) => item.video_id === candidate.video_id)) {
      selected.push(candidate);
    }
  }
  return selected.slice(0, limit);
}

function selectSpokenAutomaticCandidates(
  candidates: CrawlerCandidateResult[],
  limit = AUTO_PRIMARY_LIMIT + AUTO_RESERVE_LIMIT,
) {
  return selectAutomaticCandidates(
    candidates.filter((candidate) => candidateSpokenUse(candidate).usable),
    limit,
  );
}

function diagnoseCrawlerResult(batch: CrawlerBatchResponse) {
  const runs = batch.platform_runs || [];
  const errors = runs.filter((run) => run.error || run.status === "failed");
  if (batch.error || errors.length) {
    return {
      kind: "服务失败",
      message: batch.error || errors.map((run) => run.error).filter(Boolean).join("；") || "爬虫服务执行失败。",
    };
  }
  const returned = runs.reduce((sum, run) => sum + (run.raw_item_count || run.returned_count || 0), 0);
  const strict = runs.reduce((sum, run) => sum + (run.strict_relevant_count ?? run.relevant_count ?? run.candidates.length), 0);
  const belowFloor = runs.reduce((sum, run) => sum + (run.below_heat_floor_count || 0), 0);
  const lowIncremental = runs.reduce(
    (sum, run) => sum + Math.max(
      run.incremental_play_filtered_count || 0,
      run.low_incremental_candidates?.length || 0,
    ),
    0,
  );
  const belowThreshold = belowFloor + lowIncremental;
  if (
    belowThreshold > 0
    || runs.some((run) => ["all_below_heat_floor", "low_incremental_only"].includes(run.result_state))
  ) {
    return { kind: "未达热门阈值", message: `找到 ${Math.max(belowThreshold, strict)} 条严格相关内容，但都未达到当前热门阈值。` };
  }
  if (strict === 0 && returned > 0) {
    return { kind: "无严格相关", message: `供应商返回 ${returned} 条内容，但没有标题或话题严格命中当前关键词。` };
  }
  if (returned === 0) {
    return { kind: "供应商无返回", message: "当前数据源没有返回内容，可换词后重试或进入专业爬虫查看诊断。" };
  }
  return { kind: "无严格相关", message: "当前检索没有可进入生产的严格相关候选。" };
}

function stableFingerprint(value: unknown) {
  return JSON.stringify(value);
}

function resultMediaUrl(item: ProductionWorkspace["items"][number] | undefined) {
  // video_path 是服务端机器上的文件路径，不能也不应暴露给浏览器。
  // 兼容尚未重启的旧后端：它已有受控媒体接口，只是旧工作台遗漏了该字段。
  if (item?.result_media_url) return item.result_media_url;
  return item?.video_path && item.run_id
    ? `/api/v1/pipelines/${encodeURIComponent(item.run_id)}/media`
    : null;
}

function formatElapsedSeconds(seconds: number | null | undefined) {
  const safeSeconds = Math.max(0, Math.floor(seconds || 0));
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return minutes > 0 ? `${minutes} 分 ${remainder} 秒` : `${remainder} 秒`;
}

function formatSegmentTime(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "--:--";
  const seconds = Math.max(0, Math.floor(value));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

export default function PipelinePage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const operationKeys = useRef(new Map<string, string>());
  const reviewContextRef = useRef("");
  const voicePreviewRef = useRef<HTMLAudioElement | null>(null);

  const [initializing, setInitializing] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionMessage, setActionMessage] = useState("");

  const [sourceMode, setSourceMode] = useState<SourceMode>("keyword");
  const [creationMode, setCreationMode] = useState<CreationMode>("auto");
  const [keyword, setKeyword] = useState("");
  const [sourceValue, setSourceValue] = useState("");
  const [candidates, setCandidates] = useState<CrawlerCandidateResult[]>([]);
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [crawlerReason, setCrawlerReason] = useState<{ kind: string; message: string } | null>(null);
  const [hotWords, setHotWords] = useState<CrawlerHotWordItem[]>([]);

  const [profiles, setProfiles] = useState<ProductionProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [assets, setAssets] = useState<AvatarAsset[]>([]);
  const [profileName, setProfileName] = useState("我的短视频 IP");
  const [avatarId, setAvatarId] = useState("");
  const [voiceId, setVoiceId] = useState("");

  const [platforms, setPlatforms] = useState<PublishPlatformCapability[]>([]);
  const [accounts, setAccounts] = useState<PublishAccount[]>([]);
  const [publishPlatforms, setPublishPlatforms] = useState<string[]>(["douyin"]);
  const [publishAccountAction, setPublishAccountAction] = useState("");
  const [workspaceConfiguration, setWorkspaceConfiguration] = useState<ProductionWorkspaceConfiguration>({ configured: false });
  const [browserDiscoveries, setBrowserDiscoveries] = useState<CrawlerBrowserDiscoveryCapabilities[]>([]);
  const [startingBrowserPlatform, setStartingBrowserPlatform] = useState<BrowserPlatform | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [setupRightsHolder, setSetupRightsHolder] = useState("");
  const [profileCreateOpen, setProfileCreateOpen] = useState(false);
  const [profileEditorMode, setProfileEditorMode] = useState<"add" | "switch">("add");
  const [voiceUploadOpen, setVoiceUploadOpen] = useState(false);
  const [uploadingVoice, setUploadingVoice] = useState(false);
  const [playingVoiceId, setPlayingVoiceId] = useState("");
  const [voicePreviewError, setVoicePreviewError] = useState("");
  const [avatarCapability, setAvatarCapability] = useState<AvatarCapability | null>(null);
  const [costSetupOpen, setCostSetupOpen] = useState(false);
  const [copywritingCost, setCopywritingCost] = useState<number | null>(null);
  const [avatarCost, setAvatarCost] = useState<number | null>(null);

  const [batches, setBatches] = useState<ProductionBatch[]>([]);
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [workspace, setWorkspace] = useState<ProductionWorkspace | null>(null);
  const [reviewText, setReviewText] = useState("");
  const [reviewNote, setReviewNote] = useState("");
  const [creativePlan, setCreativePlan] = useState<ProductionCreativePlan | null>(null);
  const [creativePlanExpanded, setCreativePlanExpanded] = useState(false);
  const [publishTitle, setPublishTitle] = useState("");
  const [publishDescription, setPublishDescription] = useState("");
  const [publishTags, setPublishTags] = useState("");
  const [videoLoadError, setVideoLoadError] = useState(false);
  const [videoReloadKey, setVideoReloadKey] = useState(0);

  useEffect(() => {
    setCreativePlanExpanded(false);
  }, [selectedRunId]);

  const completeProfiles = useMemo(
    () => profiles.filter((profile) => isUsableProfile(profile, assets)),
    [assets, profiles],
  );
  const selectedProfile = useMemo(
    () => completeProfiles.find((profile) => profile.profile_id === profileId) || null,
    [completeProfiles, profileId],
  );
  const selectedCandidate = useMemo(
    () => candidates.find((candidate) => candidate.video_id === selectedCandidateId) || null,
    [candidates, selectedCandidateId],
  );
  const automaticCandidatePool = useMemo(
    () => selectSpokenAutomaticCandidates(candidates),
    [candidates],
  );
  const automaticCandidates = automaticCandidatePool.slice(0, AUTO_PRIMARY_LIMIT);
  const automaticReserveCandidates = automaticCandidatePool.slice(
    AUTO_PRIMARY_LIMIT,
    AUTO_PRIMARY_LIMIT + AUTO_RESERVE_LIMIT,
  );
  const visualReferenceCount = useMemo(
    () => candidates.filter((candidate) => !candidateSpokenUse(candidate).usable).length,
    [candidates],
  );
  const readySourceCount = browserDiscoveries.filter(
    (item) => item.enabled && item.ready_to_crawl,
  ).length;
  const sourceConnectionReady = readySourceCount > 0;
  const allSourceConnectionsReady = readySourceCount === SOURCE_BROWSER_PLATFORMS.length;
  const waitingPublishAccountKey = useMemo(
    () => accounts
      .filter((account) => account.status === "browser_open")
      .map((account) => account.account_id)
      .join(","),
    [accounts],
  );
  const voiceSampleUploadAvailable = Boolean(
    avatarCapability?.enabled
    && (avatarCapability.provider_name === "local_avatar" || avatarCapability.supports_voice_sample_upload),
  );
  const usableAvatarAssets = useMemo(
    () => assets.filter((asset) => asset.kind === "avatar" && asset.authorized && asset.status === "ready"),
    [assets],
  );
  const usableVoiceAssets = useMemo(
    () => assets.filter((asset) => asset.kind === "voice" && asset.authorized && asset.status === "ready"),
    [assets],
  );
  const pendingVoiceAssets = useMemo(
    () => assets.filter((asset) => asset.kind === "voice" && asset.authorized && asset.status !== "ready"),
    [assets],
  );
  const selectedCreatorVoice = useMemo(
    () => usableVoiceAssets.find((asset) => asset.asset_id === voiceId) || null,
    [usableVoiceAssets, voiceId],
  );
  const voicePreviewSrc = selectedCreatorVoice?.preview_url
    || (voiceId ? `/api/v1/avatar/assets/${encodeURIComponent(voiceId)}/voice-preview` : "");
  const activeItem = useMemo(() => {
    if (!workspace) return undefined;
    return workspace.items.find((item) => item.run_id === selectedRunId)
      || workspace.items.find((item) => item.run_id === workspace.current_run_id)
      || workspace.items[0];
  }, [selectedRunId, workspace]);
  const preparedPublishTaskIds = activeItem?.publish.prepared_task_ids?.length
    ? activeItem.publish.prepared_task_ids
    : (
        activeItem?.publish.status === "manual_ready"
        && activeItem.publish.stage?.startsWith("已在账号")
          ? activeItem.publish.task_ids
          : []
      );
  const publishPagePrepared = preparedPublishTaskIds.length > 0;
  const publishCompleted = activeItem?.publish.status === "succeeded";
  const currentStage = activeItem?.stage || activeItem?.current_stage || workspace?.current_stage || "source";
  const nextAction = activeItem?.next_action || workspace?.next_action || "start";
  const allowedActions = activeItem?.allowed_actions || workspace?.allowed_actions || [];
  const activeReview = activeItem?.reviews;
  const activeProfile = workspace?.profile || selectedProfile;
  const profileAvatar = assets.find((asset) => asset.asset_id === activeProfile?.avatar_id);
  const profileVoice = assets.find((asset) => asset.asset_id === activeProfile?.voice_id);
  const pendingWorkbenchBatches = useMemo(
    () => batches
      .filter((batch) => !isFinishedBatch(batch))
      .sort((left, right) => {
        const priority = workbenchTaskPriority(left, selectedBatchId) - workbenchTaskPriority(right, selectedBatchId);
        if (priority !== 0) return priority;
        return new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime();
      }),
    [batches, selectedBatchId],
  );
  const visibleWorkbenchBatches = pendingWorkbenchBatches.slice(0, WORKBENCH_TASK_LIMIT);

  const getOperationKey = useCallback((operation: string, payload: unknown) => {
    const fingerprint = `${operation}:${stableFingerprint(payload)}`;
    const existing = operationKeys.current.get(fingerprint);
    if (existing) return existing;
    const key = `workspace-${operation}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    operationKeys.current.set(fingerprint, key);
    return key;
  }, []);

  const loadWorkspace = useCallback(async (batchId: string, silent = false) => {
    if (!batchId) return null;
    try {
      const data = await getProductionBatchWorkspace(batchId);
      setWorkspace(data);
      setSelectedBatchId(batchId);
      setSelectedRunId((current) => (
        data.items.some((item) => item.run_id === current)
          ? current
          : data.current_run_id || data.items[0]?.run_id || ""
      ));
      setProfileId(data.batch.profile_id);
      const configuredPlatforms = data.batch.execution_config.publish_platforms;
      if (Array.isArray(configuredPlatforms)) {
        const restored = configuredPlatforms.filter(
          (item): item is string => typeof item === "string" && Boolean(item),
        );
        if (restored.length) setPublishPlatforms(restored);
      }
      setBatches((current) => {
        const without = current.filter((batch) => batch.batch_id !== data.batch.batch_id);
        return [data.batch, ...without];
      });
      return data;
    } catch (error) {
      if (!silent) setActionError((error as Error).message || "读取工作台失败");
      return null;
    }
  }, []);

  const loadInitialData = useCallback(async () => {
    setInitializing(true);
    setLoadError("");
    try {
      const [profileData, assetData, platformData, accountData, batchData, configuration, avatarCapabilityData] =
        await Promise.all([
          listProductionProfiles(),
          listAvatarAssets(),
          listPublishPlatforms(),
          listPublishAccounts(),
          listProductionBatches(),
          getProductionWorkspaceConfiguration(),
          getAvatarCapabilities(),
        ]);
      setProfiles(profileData.items);
      setAssets(assetData);
      setPlatforms(platformData.platforms);
      setAccounts(accountData);
      setBatches(batchData.items);
      setWorkspaceConfiguration(configuration);
      setAvatarCapability(avatarCapabilityData);
      setSetupRightsHolder(configuration.rights_holder || "");
      setCopywritingCost(configuration.copywriting_estimated_cost_cny ?? null);
      setAvatarCost(configuration.avatar_estimated_cost_cny ?? null);
      if (configuration.default_publish_platforms?.length) {
        setPublishPlatforms(configuration.default_publish_platforms);
      }

      const validProfiles = profileData.items.filter(
        (profile) => isUsableProfile(profile, assetData),
      );
      if (!configuration.configured && validProfiles.length) {
        setSetupOpen(true);
      }
      const rememberedProfile = localStorage.getItem(PROFILE_STORAGE_KEY) || "";
      setProfileId((current) =>
        validProfiles.find((profile) => profile.profile_id === current)?.profile_id
        || validProfiles.find((profile) => profile.profile_id === configuration.default_profile_id)?.profile_id
        || validProfiles.find((profile) => profile.profile_id === rememberedProfile)?.profile_id
        || validProfiles[0]?.profile_id
        || "",
      );

      const requestedBatch = searchParams.get("batch") || "";
      const requestedRun = searchParams.get("run") || "";
      const recoveredBatch = batchData.items.find(
        (batch) => batch.items.some((item) => item.run_id === requestedRun),
      );
      if (requestedBatch) {
        setSelectedRunId(requestedRun);
        setSelectedBatchId(requestedBatch);
        await loadWorkspace(requestedBatch);
      } else if (recoveredBatch) {
        setSelectedRunId(requestedRun);
        setSelectedBatchId(recoveredBatch.batch_id);
        await loadWorkspace(recoveredBatch.batch_id);
      }

      const crawlerBatchId = searchParams.get("crawler_batch_id") || "";
      const requestedCandidateId = searchParams.get("candidate_id") || "";
      if (crawlerBatchId) {
        const batch = await getCrawlerBatch(crawlerBatchId);
        const found = strictCandidates(batch, requestedCandidateId);
        const requestedCandidateFound = Boolean(
          requestedCandidateId
          && found.some((candidate) => candidate.video_id === requestedCandidateId),
        );
        setSourceMode("keyword");
        setCreationMode(requestedCandidateId ? "manual" : "auto");
        setKeyword(batch.keyword);
        setCandidates(found);
        setSelectedCandidateId(
          requestedCandidateId
            ? (requestedCandidateFound ? requestedCandidateId : "")
            : found[0]?.video_id || "",
        );
        if (requestedCandidateId && !requestedCandidateFound) {
          setCrawlerReason({
            kind: "所选候选不可用",
            message: "专业爬虫传入的候选已不在该批次结果中；不会自动替换成另一条，请返回专业爬虫重新选择。",
          });
        } else if (!found.length) {
          setCrawlerReason(diagnoseCrawlerResult(batch));
        } else if (
          requestedCandidateId
          && found[0]?.video_id === requestedCandidateId
          && !batch.platform_runs.some((run) =>
            run.candidates.some((candidate) => candidate.video_id === requestedCandidateId))
        ) {
          setCrawlerReason({
            kind: "未达热门阈值",
            message: "已按专业爬虫中的明确选择带入该候选；它未进入当前热门主榜，请确认后再创建任务。",
          });
        }
      }
    } catch (error) {
      setLoadError((error as Error).message || "工作台初始化失败");
    } finally {
      setInitializing(false);
    }
  }, [loadWorkspace, searchParams]);

  useEffect(() => {
    void loadInitialData();
  }, [loadInitialData]);

  const loadSourceConnections = useCallback(async () => {
    const statuses = await Promise.all(
      SOURCE_BROWSER_PLATFORMS.map(async ({ platform, label }) => {
        try {
          const status = await getCrawlerBrowserDiscoveryCapabilities(platform);
          return {
            ...status,
            platform,
            platform_label: label,
          };
        } catch (error) {
          return {
            platform,
            platform_label: label,
            enabled: false,
            running: false,
            login_required: false,
            missing_configuration: [],
            browser_channel: "",
            ready_to_crawl: false,
            phase: "unavailable",
            provider_name: `${platform}_local_browser`,
            message: (error as Error).message || `${label}连接状态暂时无法读取。`,
          } satisfies CrawlerBrowserDiscoveryCapabilities;
        }
      }),
    );
    setBrowserDiscoveries(statuses);
    return statuses;
  }, []);

  useEffect(() => {
    const audio = voicePreviewRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
    }
    setPlayingVoiceId("");
    setVoicePreviewError("");
  }, [voiceId]);

  useEffect(() => {
    void loadSourceConnections();
  }, [loadSourceConnections]);

  useEffect(() => {
    if (!waitingPublishAccountKey) return;
    const timer = window.setInterval(() => {
      const accountIds = waitingPublishAccountKey.split(",").filter(Boolean);
      void Promise.all(accountIds.map((accountId) => getPublishAccountStatus(accountId)))
        .then((updatedAccounts) => {
          setAccounts((current) => current.map((account) => (
            updatedAccounts.find((updated) => updated.account_id === account.account_id) || account
          )));
        })
        .catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [waitingPublishAccountKey]);

  useEffect(() => {
    let active = true;
    void getCrawlerHotWords()
      .then((response) => {
        if (active) setHotWords(response.words.slice(0, 8));
      })
      .catch(() => {
        if (active) setHotWords([]);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const status = workspace?.status || workspace?.batch.status;
    if (!selectedBatchId || ["succeeded", "failed", "partial", "completed", "outcome_unknown"].includes(status || "")) return;
    const timer = window.setInterval(() => {
      void loadWorkspace(selectedBatchId, true);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [loadWorkspace, selectedBatchId, workspace?.batch.status, workspace?.status]);

  useEffect(() => {
    if (!activeItem) return;
    const context = `${activeItem.run_id}:${nextAction}`;
    if (reviewContextRef.current === context) return;
    reviewContextRef.current = context;
    if (nextAction === "review_transcript") {
      setReviewText(activeReview?.transcript.draft_text || activeReview?.transcript.approved_text || "");
      setCreativePlan(null);
    } else if (nextAction === "review_script") {
      const script = activeReview?.script.draft_text || activeReview?.script.approved_text || "";
      setReviewText(script);
      const savedPlan = activeReview?.script.creative_plan;
      setCreativePlan(
        savedPlan
        && savedPlan.hook
        && savedPlan.call_to_action
        && savedPlan.key_points?.length
        && savedPlan.visual_sections?.length
          ? savedPlan
          : buildCreativePlan(script),
      );
    } else if (stageIndex(currentStage) === 4 && activeItem.publish.draft) {
      const draft = activeItem.publish.draft;
      setPublishTitle(draft?.title || "");
      setPublishDescription(draft?.description || "");
      setPublishTags((draft?.tags || []).join("、"));
      setReviewText("");
      setCreativePlan(null);
    } else {
      setReviewText("");
      setCreativePlan(null);
    }
    setReviewNote("");
  }, [activeItem, activeReview, currentStage, nextAction]);

  const crawlerRequest = useMemo<CrawlerSearchRequest>(() => ({
    keyword: keyword.trim(),
    published_window_days: 7,
    hotspot_window_hours: 168,
    count_per_platform: 30,
    force_refresh: false,
    mode: "smart",
    related_terms: [],
    allow_related_fallback: false,
    track_trend: false,
    target_main_count: 30,
    // 免费检索固定不允许付费兜底；需与后端免费模式的 0 次上限保持一致。
    max_paid_calls: 0,
    allow_paid_fallback: false,
    hotspot_result_limit: 30,
  }), [keyword]);

  const runKeywordSearch = async () => {
    if (keyword.trim().length < 2 || keyword.trim().length > 50) {
      setActionError("请输入 2–50 个字的关键词。");
      return;
    }
    setBusy(true);
    setActionError("");
    setActionMessage("");
    setCrawlerReason(null);
    try {
      // 先由服务端按同一口径检查；客户只需点击一次，免费搜索才会继续。
      const preview = await previewCrawlerBatch(crawlerRequest);
      if (preview.blocked) {
        setCrawlerReason({
          kind: "暂时没找到合适素材",
          message: "换一个更具体的词再试试。",
        });
        return;
      }
      const batch = await createCrawlerBatch(crawlerRequest);
      const found = strictCandidates(batch);
      setCandidates(found);
      const automatic = selectSpokenAutomaticCandidates(found);
      const primaryCount = Math.min(AUTO_PRIMARY_LIMIT, automatic.length);
      const reserveCount = Math.max(0, automatic.length - primaryCount);
      const visualCount = found.filter((candidate) => !candidateSpokenUse(candidate).usable).length;
      setSelectedCandidateId(
        (creationMode === "auto" ? automatic[0] : found[0])?.video_id || "",
      );
      if (found.length) {
        setActionMessage(
          creationMode === "auto"
            ? `找到 ${found.length} 条相关素材：${primaryCount} 条进入口播筛选${reserveCount ? `，另留 ${reserveCount} 条口播候补` : ""}${visualCount ? `；${visualCount} 条疑似纯展示，只作画面参考` : ""}。`
            : `找到 ${found.length} 条相关素材，其中 ${visualCount} 条疑似纯展示，已单独标为画面参考。`,
        );
      } else {
        setCrawlerReason({ kind: "暂时没找到合适素材", message: "换一个更具体的词再试试。" });
      }
    } catch (error) {
      setCrawlerReason({
        kind: "检索没有开始",
        message: "搜索请求没有成功提交，请刷新页面后再试；你的关键词不会丢失。",
      });
    } finally {
      setBusy(false);
    }
  };

  const saveProfile = async () => {
    setActionError("");
    if (!profileName.trim() || !avatarId || !voiceId) {
      setActionError("请填写出镜人名称，并选择形象和声音。");
      return;
    }
    // 已保存的组合即使暂时未进入“可制作”列表，也仍然可以被重新选中；
    // 否则再次点击会以相同名称新建而触发后端的重名错误。
    const matchingProfile = profiles.find(
      (profile) => profile.avatar_id === avatarId && profile.voice_id === voiceId,
    );
    if (profileEditorMode === "switch" && matchingProfile) {
      setProfileId(matchingProfile.profile_id);
      localStorage.setItem(PROFILE_STORAGE_KEY, matchingProfile.profile_id);
      setProfileCreateOpen(false);
      setSetupOpen(true);
      setActionMessage("已切换到这个出镜人和声音。");
      return;
    }
    setBusy(true);
    try {
      const requestedName = profileName.trim();
      const existingNames = new Set(profiles.map((profile) => profile.name.trim()));
      let savedName = requestedName;
      let index = 2;
      while (existingNames.has(savedName)) {
        savedName = `${requestedName} ${index}`;
        index += 1;
      }
      const created = await createProductionProfile({
        name: savedName,
        description: "智能创作工作台默认单条配方",
        target_audience: "",
        platform: "douyin",
        script_style: "",
        avatar_id: avatarId,
        voice_id: voiceId,
        tags: [],
      });
      setProfiles((current) => [created, ...current]);
      setProfileId(created.profile_id);
      localStorage.setItem(PROFILE_STORAGE_KEY, created.profile_id);
      setProfileCreateOpen(false);
      setSetupOpen(true);
      setActionMessage(
        savedName === requestedName
          ? "出镜人已保存；再完成一次基础设置就能开始创作。"
          : `出镜组合已保存为“${savedName}”；再完成一次基础设置就能开始创作。`,
      );
    } catch (error) {
      setActionError((error as Error).message || "保存 IP 配方失败");
    } finally {
      setBusy(false);
    }
  };

  const openProfileCreator = () => {
    setProfileEditorMode("add");
    setProfileName("新的短视频 IP");
    setAvatarId("");
    setVoiceId(selectedProfile?.voice_id || "");
    setVoicePreviewError("");
    setVoiceUploadOpen(false);
    setSetupOpen(false);
    setProfileCreateOpen(true);
  };

  const openProfileSwitcher = () => {
    const currentAvatarId = selectedProfile?.avatar_id || usableAvatarAssets[0]?.asset_id || "";
    const currentAvatar = usableAvatarAssets.find((asset) => asset.asset_id === currentAvatarId);
    const matchingVoiceId = usableVoiceAssets.find((asset) => asset.name === currentAvatar?.name)?.asset_id;
    setProfileEditorMode("switch");
    setProfileName(currentAvatar?.name || selectedProfile?.name || "新的出镜人");
    setAvatarId(currentAvatarId);
    setVoiceId(matchingVoiceId || selectedProfile?.voice_id || usableVoiceAssets[0]?.asset_id || "");
    setVoicePreviewError("");
    setVoiceUploadOpen(false);
    setSetupOpen(false);
    setProfileCreateOpen(true);
  };

  const selectProfileAvatar = (asset: AvatarAsset) => {
    setAvatarId(asset.asset_id);
    if (profileEditorMode === "switch") setProfileName(asset.name);
    const matchingVoice = usableVoiceAssets.find((voice) => voice.name === asset.name);
    if (matchingVoice) setVoiceId(matchingVoice.asset_id);
  };

  const uploadVoiceSample = async (file: File) => {
    const supportsLocalUpload = avatarCapability?.provider_name === "local_avatar";
    if (!supportsLocalUpload && !avatarCapability?.supports_voice_sample_upload) {
      setActionError("当前数字人服务暂未开放声音上传。");
      return false;
    }
    setUploadingVoice(true);
    setActionError("");
    try {
      const name = file.name.replace(/\.[^.]+$/, "") || "新声音";
      const asset = supportsLocalUpload
        ? await uploadAvatarAsset({ kind: "voice", file, name })
        : await trainCloudVoice({ file, name });
      setAssets((current) => [asset, ...current.filter((item) => item.asset_id !== asset.asset_id)]);
      if (asset.status === "ready") {
        setVoiceId(asset.asset_id);
        setVoiceUploadOpen(false);
        setActionMessage("声音已上传并自动选中。");
      } else {
        setVoiceUploadOpen(false);
        setActionMessage(
          asset.status_message
          || "声音样本已保存，但当前声音服务尚未开通；暂时继续使用原来的声音。",
        );
      }
    } catch (error) {
      setActionError((error as Error).message || "声音上传失败，请保留当前内容后重试。");
    } finally {
      setUploadingVoice(false);
    }
    return false;
  };

  const toggleVoicePreview = async () => {
    const audio = voicePreviewRef.current;
    if (!audio || !voiceId) return;
    if (!audio.paused) {
      audio.pause();
      audio.currentTime = 0;
      setPlayingVoiceId("");
      return;
    }
    setVoicePreviewError("");
    try {
      audio.currentTime = 0;
      await audio.play();
    } catch {
      setPlayingVoiceId("");
      setVoicePreviewError("暂时无法试听这个声音，请稍后重试。");
    }
  };

  const saveWorkspaceSetup = async () => {
    if (!setupRightsHolder.trim()) {
      setActionError("请填写公司名称或本人姓名。");
      return;
    }
    if (!publishPlatforms.length) {
      setActionError("请至少选择一个发布平台。");
      return;
    }
    const accountsNotReady = publishPlatforms.filter((platform) => {
      const capability = platforms.find((item) => item.platform === platform);
      return capability?.requires_account
        && !accounts.some((account) => account.platform === platform && account.status === "ready");
    });
    if (accountsNotReady.length) {
      setActionError(
        `请先完成发布账号扫码核验：${accountsNotReady.map((platform) => PLATFORM_LABELS[platform] || platform).join("、")}。`,
      );
      return;
    }
    if (!sourceConnectionReady) {
      setActionError("请先登录至少一个素材平台，完成后点击“我已登录，刷新状态”。");
      return;
    }
    setBusy(true);
    setActionError("");
    try {
      const configuration = await saveProductionWorkspaceConfiguration({
        rightsHolder: setupRightsHolder.trim(),
        agreementAccepted: true,
        defaultProfileId: profileId || null,
        defaultPublishPlatforms: publishPlatforms.length ? publishPlatforms : ["douyin"],
      });
      setWorkspaceConfiguration(configuration);
      setPublishPlatforms(configuration.default_publish_platforms || ["douyin"]);
      setSetupOpen(false);
      setActionMessage("开工前设置已完成。现在只要输入关键词，其他交给我。 ");
    } catch (error) {
      setActionError((error as Error).message || "基础设置保存失败");
    } finally {
      setBusy(false);
    }
  };

  const refreshSourceConnections = async () => {
    setBusy(true);
    setActionError("");
    try {
      const statuses = await loadSourceConnections();
      const readyCount = statuses.filter((item) => item.enabled && item.ready_to_crawl).length;
      if (readyCount > 0) {
        setActionMessage(`已连接 ${readyCount}/4 个素材平台，可以进入工作台。`);
      } else {
        setActionError("四个平台都还没有准备好，请完成登录后再检查一次。");
      }
    } catch (error) {
      setActionError((error as Error).message || "暂时无法检查素材平台，请稍后再试。");
    } finally {
      setBusy(false);
    }
  };

  const startSourceConnection = async (platform: BrowserPlatform) => {
    setBusy(true);
    setStartingBrowserPlatform(platform);
    setActionError("");
    try {
      const status = await startCrawlerBrowserDiscovery(platform);
      const label = SOURCE_BROWSER_PLATFORMS.find((item) => item.platform === platform)?.label || status.platform_label || platform;
      const normalized = { ...status, platform, platform_label: label };
      setBrowserDiscoveries((current) => current.map(
        (item) => item.platform === platform ? normalized : item,
      ));
      setActionMessage(
        status.ready_to_crawl
          ? `${label}已连接。`
          : `${label}登录窗口已打开，请在窗口中扫码或完成验证，然后刷新状态。`,
      );
    } catch (error) {
      setActionError((error as Error).message || "平台浏览器没有打开，请稍后再试。");
    } finally {
      setStartingBrowserPlatform(null);
      setBusy(false);
    }
  };

  const upsertPublishAccount = (account: PublishAccount) => {
    setAccounts((current) => (
      current.some((item) => item.account_id === account.account_id)
        ? current.map((item) => item.account_id === account.account_id ? account : item)
        : [...current, account]
    ));
  };

  const connectPublishAccountFromSetup = async (platform: string) => {
    setPublishAccountAction(platform);
    setActionError("");
    try {
      let account = accounts.find(
        (item) => item.platform === platform && item.status !== "ready",
      );
      if (!account) {
        account = await createPublishAccount({
          platform,
          name: DEFAULT_PUBLISH_ACCOUNT_NAME,
        });
        upsertPublishAccount(account);
      }
      const connected = await connectPublishAccount(account.account_id);
      upsertPublishAccount(connected);
      setActionMessage(
        `${PLATFORM_LABELS[platform] || platform}官方登录窗口已打开，扫码完成后系统会自动检查。`,
      );
    } catch (error) {
      setActionError((error as Error).message || "发布账号扫码窗口没有打开，请稍后再试。");
    } finally {
      setPublishAccountAction("");
    }
  };

  const refreshPublishAccount = async (accountId: string, platform: string) => {
    setPublishAccountAction(platform);
    setActionError("");
    try {
      const account = await getPublishAccountStatus(accountId);
      upsertPublishAccount(account);
      if (account.status === "ready") {
        setActionMessage(`${PLATFORM_LABELS[platform] || platform}发布账号已核验，可以发布。`);
      } else {
        setActionError(account.message || "平台还没有完成登录，请在官方窗口完成扫码后再检查。");
      }
    } catch (error) {
      setActionError((error as Error).message || "暂时无法核验发布账号，请稍后再试。");
    } finally {
      setPublishAccountAction("");
    }
  };

  const saveCostSetup = async () => {
    if (copywritingCost === null || avatarCost === null) {
      setActionError("请先填写文案和数字人的实际单次费用；不确定时先向供应商确认。 ");
      return;
    }
    setBusy(true);
    setActionError("");
    try {
      const configuration = await saveProductionWorkspaceConfiguration({
        rightsHolder: workspaceConfiguration.rights_holder || setupRightsHolder,
        agreementAccepted: true,
        defaultProfileId: workspaceConfiguration.default_profile_id || profileId || null,
        defaultPublishPlatforms: workspaceConfiguration.default_publish_platforms || publishPlatforms,
        copywritingEstimatedCostCny: copywritingCost,
        avatarEstimatedCostCny: avatarCost,
        bundledCompute: workspaceConfiguration.bundled_compute ?? true,
      });
      setWorkspaceConfiguration(configuration);
      setCostSetupOpen(false);
      setActionMessage("费用已保存。请再点击“完成预检并启动”，系统会重新核对后再开始。 ");
    } catch (error) {
      setActionError((error as Error).message || "费用设置保存失败");
    } finally {
      setBusy(false);
    }
  };

  const validateExecution = () => {
    if (!workspaceConfiguration.configured) return "请先完成一次基础设置。";
    if (!sourceConnectionReady) return "请先完成开工前的素材浏览器连接。";
    if (!publishPlatforms.length) return "请至少选择一个发布平台。";
    return "";
  };

  const validateSource = () => {
    if (!selectedProfile) return "请先补齐一个可用的 IP 配方。";
    const executionError = validateExecution();
    if (executionError) return executionError;
    if (
      sourceMode === "keyword"
      && creationMode === "auto"
      && automaticCandidates.length === 0
    ) return "这批只有画面参考，没有可用口播候选；请换一个更偏讲解、测评或避坑的关键词。";
    if (sourceMode === "keyword" && creationMode === "manual" && !selectedCandidate) {
      return "请先检索并选择一条严格相关候选。";
    }
    if (sourceMode === "share_link" && !/^https?:\/\//i.test(sourceValue.trim())) return "请输入以 http:// 或 https:// 开头的视频分享链接。";
    if (sourceMode === "script" && !sourceValue.trim()) return "请输入已有文案。";
    return "";
  };

  const executionParams = useMemo(() => ({
    rightsHolder: workspaceConfiguration.rights_holder || "",
    rightsConfirmed: workspaceConfiguration.configured,
    publishPlatforms: publishPlatforms.length ? publishPlatforms : ["douyin"],
    concurrency: 1,
    maxTotalCostCny: null,
    paidActionsConfirmed: Boolean(workspaceConfiguration.bundled_compute),
    automationMode: sourceMode === "keyword" ? creationMode : "manual" as const,
  }), [creationMode, publishPlatforms, sourceMode, workspaceConfiguration]);

  const preflightAndStart = async (batchId: string) => {
    const checked = await preflightProductionBatch(batchId, executionParams);
    if (checked.blocked_count || checked.cost_blocked || checked.ready_count === 0) {
      setActionError(
        checked.cost_issues?.join("；")
        || checked.items.flatMap((item) => item.reasons || []).join("；")
        || "预检未通过，请根据提示补齐后重试。",
      );
      if (checked.cost_blocked && !workspaceConfiguration.bundled_compute) setCostSetupOpen(true);
      await loadWorkspace(batchId);
      return;
    }
    const idempotencyKey = getOperationKey("start", { batchId, ...executionParams });
    await startProductionBatch(batchId, { ...executionParams, idempotencyKey });
    localStorage.setItem(PROFILE_STORAGE_KEY, profileId);
    setActionMessage(
      creationMode === "auto" && sourceMode === "keyword"
        ? `自动创作已启动：先转写 ${automaticCandidates.length} 条${automaticReserveCandidates.length ? `，不够时最多启用 ${automaticReserveCandidates.length} 条候补` : ""}；AI 只改写有可用口播的素材。`
        : "任务已启动，将自动推进到下一次人工确认。",
    );
    await loadWorkspace(batchId);
  };

  const createAndStart = async () => {
    // A selected candidate has already been persisted in an existing batch.  After a
    // preflight failure the page reloads without the in-memory candidate, so treating
    // it as a new task would incorrectly ask the customer to search again.
    const resumesExistingBatch = Boolean(
      selectedBatchId
      && workspace
      && (nextAction === "preflight" || workspace.batch.status === "planned"),
    );
    const validation = resumesExistingBatch ? validateExecution() : validateSource();
    if (validation) {
      setActionError(validation);
      return;
    }
    setBusy(true);
    setActionError("");
    setActionMessage("");
    try {
      if (resumesExistingBatch && selectedBatchId) {
        await preflightAndStart(selectedBatchId);
        return;
      }
      const baseCandidate = creationMode === "auto"
        ? automaticCandidates[0]
        : selectedCandidate;
      const item =
        sourceMode === "keyword"
          ? {
              source_type: "candidate" as const,
              source_value: baseCandidate!.video_id,
              display_title: baseCandidate!.title,
            }
          : {
              source_type: sourceMode,
              source_value: sourceValue.trim(),
              display_title:
                sourceMode === "share_link" ? "分享链接"
                : "已有口播文案",
            };
      const items = (
        sourceMode === "keyword" && creationMode === "auto"
          ? automaticCandidatePool.map((candidate, index) => ({
              source_type: "candidate" as const,
              source_value: candidate.video_id,
              display_title: candidate.title,
              candidate_role: index < AUTO_PRIMARY_LIMIT ? "primary" as const : "reserve" as const,
            }))
          : [item]
      );
      const payload = {
        name: `${
          sourceMode === "keyword" && creationMode === "auto"
            ? `自动创作 · ${keyword.trim()}`
            : `单条创作 · ${item.display_title || new Date().toLocaleString()}`
        }`.slice(0, 100),
        profile_id: profileId,
        items,
      };
      const created = await createProductionBatch({
        ...payload,
        idempotencyKey: getOperationKey("create", payload),
      });
      setSelectedBatchId(created.batch_id);
      setBatches((current) => [created, ...current.filter((batch) => batch.batch_id !== created.batch_id)]);
      const query = new URLSearchParams({ batch: created.batch_id, run: created.items[0]?.run_id || "" });
      navigate(`/pipeline?${query.toString()}`, { replace: true });
      await preflightAndStart(created.batch_id);
    } catch (error) {
      setActionError((error as Error).message || "创建单条任务失败");
    } finally {
      setBusy(false);
    }
  };

  const submitReview = async (stage: ReviewStage) => {
    if (!workspace || !activeItem) return false;
    if (stage !== "output" && stage !== "publish" && !reviewText.trim()) {
      setActionError(stage === "transcript" ? "转写确认必须提交非空最终文本。" : "文案确认必须提交非空最终口播稿。");
      return false;
    }
    if (stage === "script" && (!creativePlan?.hook.trim() || !creativePlan.call_to_action.trim() || !creativePlan.key_points.some((item) => item.trim()) || !creativePlan.visual_sections.some((item) => item.trim()))) {
      setActionError("请补齐开头、讲解要点、行动引导和至少一个画面段落后再确认。");
      return false;
    }
    if (stage === "publish" && !publishTitle.trim()) {
      setActionError("请先确认一个不含话题和 @ 的发布标题。");
      return false;
    }
    setBusy(true);
    setActionError("");
    try {
      const response = await reviewProductionBatchItems(workspace.batch.batch_id, {
        stage,
        reviewer: workspaceConfiguration.rights_holder || "当前操作人",
        items: [{
          run_id: activeItem.run_id,
          approved_text: stage === "output" || stage === "publish" ? undefined : reviewText.trim(),
          note: reviewNote.trim(),
          creative_plan: stage === "script" && creativePlan ? {
            hook: creativePlan.hook.trim(),
            key_points: creativePlan.key_points.map((item) => item.trim()).filter(Boolean),
            call_to_action: creativePlan.call_to_action.trim(),
            visual_sections: creativePlan.visual_sections.map((item) => item.trim()).filter(Boolean),
          } : undefined,
          publish_draft: stage === "publish" ? {
            title: publishTitle.trim(),
            description: publishDescription.trim(),
            tags: publishTags.split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean),
          } : undefined,
        }],
      });
      const failed = response.results.find((item) => !item.ok);
      if (failed) throw new Error(failed.error || "审核提交失败");
      setActionMessage(
        stage === "transcript" ? "原转写已确认，正在生成待审核改写稿。"
        : stage === "script" ? "创作方案和最终口播稿已确认，后台将继续制作成片。"
        : stage === "publish" && activeItem.publish.task_ids.length
          ? "发布信息已保存；没有启动官方页，也没有执行最终发布。"
        : stage === "publish" ? "发布标题、描述和标签已确认；现在可以准备官方发布页。"
        : "成片复核已记录。",
      );
      await loadWorkspace(workspace.batch.batch_id);
      return true;
    } catch (error) {
      setActionError((error as Error).message || "提交人工确认失败");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const publishTargets = useMemo<ProductionPublishTarget[]>(
    () => publishPlatforms.map((platform) => {
      const ready = accounts.find(
        (account) =>
          account.platform === platform
          && account.status === "ready",
      );
      return {
        platform,
        account_id: ready?.account_id,
        use_manual_fallback: true,
      };
    }),
    [accounts, publishPlatforms],
  );
  const expectedPublishDestinations = useMemo(
    () => publishPlatforms.map((platform) => {
      const capability = platforms.find((item) => item.platform === platform);
      const ready = accounts.find(
        (account) =>
          account.platform === platform
          && account.status === "ready",
      );
      const known = ready || accounts.find((account) => account.platform === platform);
      const canAttemptReal = Boolean(
        ready
        && capability?.enabled
        && !capability.manual_only,
      );
      return {
        platform,
        displayName: capability?.display_name || platform,
        accountName: known?.name || "未绑定就绪账号",
        accountStatus: ready ? "已登录，可准备官方发布页" : known ? `账号状态：${known.status}` : "未配置账号",
        mode: canAttemptReal ? "确认后自动发布" : "生成手动发布包",
      };
    }),
    [accounts, platforms, publishPlatforms],
  );
  const publishDestinations = useMemo(() => {
    const confirmed = activeItem?.publish.targets || [];
    if (!confirmed.length) return expectedPublishDestinations;
    return confirmed.map((target) => {
      const expected = expectedPublishDestinations.find((item) => item.platform === target.platform);
      return {
        platform: target.platform,
        displayName: target.display_name || expected?.displayName || target.platform,
        accountName: target.account_name || expected?.accountName || "未绑定就绪账号",
        accountStatus: target.account_id ? "服务端已核验账号" : expected?.accountStatus || "未配置账号",
        mode:
          target.mode === "real" ? "确认后自动发布"
          : target.mode === "manual" ? "手动发布包"
          : expected?.mode || "由服务端决定",
      };
    });
  }, [activeItem?.publish.targets, expectedPublishDestinations]);

  const publishCurrent = async (data = workspace) => {
    const item =
      data?.items.find((value) => value.run_id === selectedRunId)
      || data?.items.find((value) => value.run_id === data.current_run_id)
      || data?.items[0];
    if (!data || !item) return;
    setBusy(true);
    setActionError("");
    try {
      const checked = await preflightProductionBatchPublish(data.batch.batch_id, {
        runIds: [item.run_id],
        targets: publishTargets,
      });
      const blocked = checked.items.flatMap((value) => value.issues || []);
      if (checked.blocked) {
        setActionError(blocked.join("；") || "发布预检未通过。");
        return;
      }
      const automaticallyPublishablePlatforms = new Set(
        expectedPublishDestinations
          .filter((destination) => destination.mode === "确认后自动发布")
          .map((destination) => destination.platform),
      );
      const confirmedTargets = publishTargets.map((target) => (
        target.account_id && automaticallyPublishablePlatforms.has(target.platform)
          ? { ...target, auto_publish_authorized: true }
          : target
      ));
      const autoAccounts = expectedPublishDestinations
        .filter((destination) => (
          automaticallyPublishablePlatforms.has(destination.platform)
          && destination.accountName !== "未绑定就绪账号"
        ))
        .map((destination) => `${destination.displayName}“${destination.accountName}”`);
      Modal.confirm({
        title: autoAccounts.length ? "确认并发布到所选平台？" : "确认生成发布任务？",
        content: autoAccounts.length
          ? `本次将使用${autoAccounts.join("、")}上传已审核成片，并在各平台最多点击一次最终发布。遇到验证码、页面异常或结果不明确时会立即停止，不会重复点击。`
          : "当前没有可自动发布的已登录账号；系统只会生成手动发布包，不会伪报发布成功。",
        okText: autoAccounts.length ? "确认并自动发布" : "确认生成",
        cancelText: "取消",
        onOk: async () => {
          setBusy(true);
          setActionError("");
          try {
            const idempotencyKey = getOperationKey("publish", {
              batchId: data.batch.batch_id,
              runId: item.run_id,
              targets: confirmedTargets,
            });
            await confirmProductionBatchPublish(data.batch.batch_id, {
              runIds: [item.run_id],
              targets: confirmedTargets,
              idempotencyKey,
            });
            setActionMessage(
              autoAccounts.length
                ? "已开始安全自动发布；只有平台确认成功后才会显示 100%。"
                : "手动发布包已创建，请在发布中心查看真实状态。",
            );
            await loadWorkspace(data.batch.batch_id);
          } catch (error) {
            setActionError((error as Error).message || "发布确认未完成");
            throw error;
          } finally {
            setBusy(false);
          }
        },
      });
    } catch (error) {
      setActionError((error as Error).message || "发布预检失败");
    } finally {
      setBusy(false);
    }
  };

  const finalizeAutomaticOutput = async () => {
    if (!workspace || !activeItem) return;
    if (!publishTitle.trim()) {
      setActionError("请先核对成片和发布标题，再执行最终发布确认。");
      return;
    }
    setBusy(true);
    setActionError("");
    try {
      const reviewer = workspaceConfiguration.rights_holder || "当前操作人";
      const outputResult = await reviewProductionBatchItems(workspace.batch.batch_id, {
        stage: "output",
        reviewer,
        items: [{ run_id: activeItem.run_id, note: "自动创作最终确认" }],
      });
      const outputFailure = outputResult.results.find((item) => !item.ok);
      if (outputFailure) throw new Error(outputFailure.error || "成片确认失败");
      const draftResult = await reviewProductionBatchItems(workspace.batch.batch_id, {
        stage: "publish",
        reviewer,
        items: [{
          run_id: activeItem.run_id,
          note: "与成片一并确认",
          publish_draft: {
            title: publishTitle.trim(),
            description: publishDescription.trim(),
            tags: publishTags.split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean),
          },
        }],
      });
      const draftFailure = draftResult.results.find((item) => !item.ok);
      if (draftFailure) throw new Error(draftFailure.error || "发布信息确认失败");
      const refreshed = await loadWorkspace(workspace.batch.batch_id);
      if (!refreshed) throw new Error("发布前状态刷新失败，请稍后再试。");
      await publishCurrent(refreshed);
    } catch (error) {
      setActionError((error as Error).message || "最终发布确认未完成");
    } finally {
      setBusy(false);
    }
  };

  const prepareExistingPublishPages = () => {
    const taskIds = activeItem?.publish.task_ids || [];
    if (!workspace || !taskIds.length) {
      setActionError("还没有可重新准备的发布任务，请先保存发布信息。");
      return;
    }
    Modal.confirm({
      title: "准备抖音官方发布页？",
      content: "系统会打开官方创作者页面并填入已保存的标题、描述和标签；不会点击最终发布，最后一步仍由你在抖音确认。",
      okText: "继续准备",
      cancelText: "暂不处理",
      onOk: async () => {
        setBusy(true);
        setActionError("");
        try {
          for (const taskId of taskIds) {
            await preparePublishOfficialPage(taskId);
          }
          setActionMessage("正在打开抖音创作者中心并填入内容；窗口出现后，请你检查并完成最终发布。");
          // Keep the last trustworthy workspace state until the normal 2.5 s
          // refresh observes the worker result. An immediate reload can race
          // with the worker and briefly freeze the page on a stale "失败" state.
        } catch (error) {
          setActionError((error as Error).message || "官方发布页没有准备成功，请检查发布账号后重试。");
          throw error;
        } finally {
          setBusy(false);
        }
      },
    });
  };

  const confirmAutomaticPublish = () => {
    if (!workspace || !preparedPublishTaskIds.length) {
      setActionError("还没有准备好的抖音发布页，请先准备官方发布页。");
      return;
    }
    const accountName = publishDestinations.find(
      (destination) => destination.platform === "douyin",
    )?.accountName || "当前抖音账号";
    Modal.confirm({
      title: "确认并自动发布到抖音？",
      content: `本次使用“${accountName}”。系统会等待视频上传完成并只点击一次最终发布；遇到验证码、页面异常或结果不明确时会停止并让你核对。`,
      okText: "确认并自动发布",
      cancelText: "我再检查一下",
      onOk: async () => {
        setBusy(true);
        setActionError("");
        try {
          for (const taskId of preparedPublishTaskIds) {
            await confirmPublishTaskAuto(taskId);
          }
          setActionMessage("已获得本次任务授权，正在等待上传完成并安全发布。");
        } catch (error) {
          setActionError((error as Error).message || "自动发布没有启动，请检查官方页面后重试。");
          throw error;
        } finally {
          setBusy(false);
        }
      },
    });
  };

  const confirmManualPublishCompleted = () => {
    if (!workspace || !preparedPublishTaskIds.length) {
      setActionError("还没有等待确认的抖音发布页，请先准备官方发布页。");
      return;
    }
    Modal.confirm({
      title: "确认已经在抖音发布？",
      content: "只有你已经在抖音官方页面点击发布，并确认作品提交成功时才点这里。确认后，这条任务将完成并显示 100%。",
      okText: "确认已发布",
      cancelText: "还没有",
      onOk: async () => {
        setBusy(true);
        setActionError("");
        try {
          for (const taskId of preparedPublishTaskIds) {
            await recordManualPublishResult(taskId, {
              succeeded: true,
              note: "用户在智能创作工作台确认已完成抖音官方发布。",
            });
          }
          setActionMessage("已记录抖音发布完成，这条任务现在是 100%。");
          await loadWorkspace(workspace.batch.batch_id);
        } catch (error) {
          setActionError((error as Error).message || "发布完成状态没有保存成功，请保留当前内容后重试。");
          throw error;
        } finally {
          setBusy(false);
        }
      },
    });
  };

  const reviewOutput = async () => {
    await submitReview("output");
  };

  const runControl = async (action: "pause" | "resume" | "retry") => {
    if (!workspace) return;
    setBusy(true);
    setActionError("");
    try {
      if (action === "pause") await pauseProductionBatch(workspace.batch.batch_id);
      else if (action === "resume") await resumeProductionBatch(workspace.batch.batch_id);
      else await retryProductionBatchFailed(workspace.batch.batch_id);
      setActionMessage(action === "pause" ? "任务已暂停。" : action === "resume" ? "任务已继续。" : "已从可证明安全的阶段恢复。");
      await loadWorkspace(workspace.batch.batch_id);
    } catch (error) {
      setActionError((error as Error).message || "任务控制失败");
    } finally {
      setBusy(false);
    }
  };

  const handlePrimaryAction = async () => {
    try {
      if (!workspace && !completeProfiles.length) {
        await saveProfile();
        return;
      }
      if (!workspace && !workspaceConfiguration.configured) {
        setSetupOpen(true);
        return;
      }
      if (!workspace && !sourceConnectionReady) {
        setSetupOpen(true);
        return;
      }
      if (!workspace) {
        if (sourceMode === "keyword" && !candidates.length) {
          await runKeywordSearch();
          return;
        }
        await createAndStart();
        return;
      }
      if (nextAction === "preflight" || nextAction === "start" || workspace.batch.status === "planned") {
        await createAndStart();
      } else if (nextAction === "review_transcript") {
        await submitReview("transcript");
      } else if (nextAction === "review_script") {
        await submitReview("script");
      } else if (nextAction === "review_output") {
        if (workspace.automation?.mode === "auto") {
          await finalizeAutomaticOutput();
        } else {
          await reviewOutput();
        }
      } else if (nextAction === "review_publish_draft") {
        await submitReview("publish");
      } else if (nextAction === "publish") {
        await publishCurrent();
      } else if (nextAction === "resume") {
        await runControl("resume");
      } else if (nextAction === "retry") {
        await runControl("retry");
      } else {
        await loadWorkspace(workspace.batch.batch_id);
      }
    } finally {
      setBusy(false);
    }
  };

  const primaryLabel = useMemo(() => {
    if (!workspace && !completeProfiles.length) return "保存 IP 配方";
    if (!workspace) {
      if (!workspaceConfiguration.configured || !sourceConnectionReady) return "开始创作";
      if (sourceMode === "keyword" && !candidates.length) {
        return "找素材";
      }
      if (sourceMode === "keyword" && creationMode === "auto") {
        return "确认并自动制作";
      }
      return "用这条开始创作";
    }
    const labels: Record<string, string> = {
      preflight: "完成预检并启动",
      start: "启动智能创作",
      review_transcript: "确认转写并生成去重口播稿",
      review_script: "确认方案并制作视频",
      review_output: workspace.automation?.mode === "auto" ? "核对成片并发布" : "确认成片",
      review_publish_draft: "确认发布信息",
      publish: "确认并自动发布",
      resume: "继续任务",
      retry: "安全重试",
      wait: "刷新实时状态",
      view_result: "查看成片",
      completed: "查看完成结果",
    };
    return labels[nextAction] || "刷新实时状态";
  }, [candidates.length, completeProfiles.length, creationMode, nextAction, sourceConnectionReady, sourceMode, workspace, workspaceConfiguration.configured]);

  const showReviewEditor = ["review_transcript", "review_script"].includes(nextAction);
  const needsProfileConfiguration = !workspace && !completeProfiles.length;
  const activeVideo = resultMediaUrl(activeItem);
  const avatarProcessing = activeItem?.processing;
  const avatarIsDelayed = Boolean(avatarProcessing?.stage === "avatar" && avatarProcessing.delayed);
  const progressPercent =
    ["succeeded", "completed"].includes(workspace?.status || "") ? 100
    : workspace ? [10, 35, 60, 80, 95][stageIndex(currentStage)]
    : 0;

  useEffect(() => {
    setVideoLoadError(false);
  }, [activeVideo]);

  if (initializing) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
        <section
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 24,
            padding: "24px 28px",
            borderRadius: 20,
            color: "#fff",
            background: "linear-gradient(120deg, #3d7df7, #7b4ce8 58%, #9b55ee)",
            boxShadow: "0 18px 45px rgba(82, 71, 188, .18)",
          }}
        >
          <Title level={2} style={{ color: "#fff", margin: 0 }}>智能创作工作台</Title>
          <Text style={{ color: "rgba(255,255,255,.82)" }}>正在带入你的常用配置</Text>
        </section>
        <Card>
          <Steps current={0} items={WORKSPACE_STEPS} responsive={false} />
        </Card>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, .9fr) minmax(360px, 1.1fr)", gap: 16 }}>
          <Card title="当前操作">
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Text>页面已经打开，正在准备出镜人、音色和发布设置。</Text>
              <Button type="primary" size="large" block disabled>马上就好</Button>
            </Space>
          </Card>
          <Card title="预览与实时状态">
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="配置带入后即可开始创作" />
          </Card>
        </div>
      </div>
    );
  }

  const renderProfileCard = () => (
    <Card
      title={<Space><SafetyCertificateOutlined /> 当前 IP 配方</Space>}
      extra={!workspace && activeProfile ? (
        <Button type="link" icon={<SettingOutlined />} onClick={() => setSetupOpen(true)}>
          修改设置
        </Button>
      ) : null}
    >
      {activeProfile ? (
        <div className="profile-summary">
          <div className="profile-summary-details" aria-label="当前 IP 信息">
            <Descriptions size="small" column={{ xs: 1, sm: 3 }}>
              <Descriptions.Item label="出镜人">{profileAvatar?.name || activeProfile.name}</Descriptions.Item>
              <Descriptions.Item label="音色">{profileVoice?.name || activeProfile.voice_id}</Descriptions.Item>
              <Descriptions.Item label="发布到">{publishPlatforms.map((item) => PLATFORM_LABELS[item] || item).join("、")}</Descriptions.Item>
            </Descriptions>
          </div>
          <div className="profile-avatar-preview" aria-label="当前 IP 出镜人预览">
            {profileAvatar?.preview_url ? (
              profileAvatar.preview_type === "video" ? (
                <video
                  src={profileAvatar.preview_url}
                  muted
                  playsInline
                  preload="auto"
                  onLoadedMetadata={(event) => {
                    if (event.currentTarget.currentTime === 0) {
                      event.currentTarget.currentTime = 0.01;
                    }
                  }}
                />
              ) : <img src={profileAvatar.preview_url} alt={`${profileAvatar.name} 形象预览`} />
            ) : <SafetyCertificateOutlined />}
          </div>
        </div>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无完整 IP 配方" />
      )}
      {completeProfiles.length > 1 && !workspace && (
        <Button block onClick={openProfileSwitcher}>更换出镜人</Button>
      )}
    </Card>
  );

  const renderWorkbenchTasksCard = () => (
    <Card
      title="需要处理"
      extra={<Link to="/production">查看全部任务（{batches.length}）</Link>}
    >
      {visibleWorkbenchBatches.length ? (
        <List
          className="workbench-task-list"
          size="small"
          dataSource={visibleWorkbenchBatches}
          renderItem={(batch) => (
            <List.Item
              className={`workbench-task-item${batch.batch_id === selectedBatchId ? " active-batch" : ""}`}
              actions={[
                <Button key="open" type="link" onClick={() => {
                  const run = batch.items[0]?.run_id || "";
                  setSelectedRunId(run);
                  navigate(`/pipeline?batch=${encodeURIComponent(batch.batch_id)}&run=${encodeURIComponent(run)}`);
                  void loadWorkspace(batch.batch_id);
                }} aria-label={`${workbenchTaskAction(batch.status)}：${productionTaskTitle(batch.name)}`}>
                  {workbenchTaskAction(batch.status)}
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={(
                  <span className="workbench-task-title" title={productionTaskTitle(batch.name)}>
                    {productionTaskTitle(batch.name)}
                  </span>
                )}
                description={`${batch.profile_name} · ${new Date(batch.created_at).toLocaleString()}`}
              />
              <Tag color={statusColor(batch.status)}>{STATUS_LABEL[batch.status] || batch.status}</Tag>
            </List.Item>
          )}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无需要处理的任务" />
      )}
      {pendingWorkbenchBatches.length > WORKBENCH_TASK_LIMIT && (
        <div className="workbench-task-footer">
          <Text type="secondary">
            还有 {pendingWorkbenchBatches.length - WORKBENCH_TASK_LIMIT} 条需要处理
          </Text>
          <Link to="/production">去任务队列</Link>
        </div>
      )}
    </Card>
  );

  return (
    <div className="smart-workspace">
      <section className="workspace-hero">
        <div>
          <Title level={2}>智能创作工作台</Title>
        </div>
        <div className="hero-state" aria-live="polite">
          <Text type="secondary">当前阶段</Text>
          <strong>{WORKSPACE_STEPS[businessStageIndex(currentStage)].title.replace(/^\d+\s+/, "")}</strong>
        </div>
      </section>

      <Card className="stage-overview" variant="borderless">
        <Steps
          current={businessStageIndex(currentStage)}
          responsive
          items={WORKSPACE_STEPS.map((step, index) => ({
            ...step,
            status:
              index < businessStageIndex(currentStage) ? "finish"
              : index === businessStageIndex(currentStage) ? "process"
              : "wait",
          }))}
        />
      </Card>

      {loadError && <Alert type="error" showIcon message="工作台载入失败" description={loadError} />}
      {actionError && <Alert type="error" showIcon closable onClose={() => setActionError("")} message="当前操作未完成" description={actionError} />}
      {actionMessage && <Alert type="success" showIcon closable onClose={() => setActionMessage("")} message={actionMessage} />}

      <div className="workspace-grid">
        <div className="workspace-left">
          <Card
            title={<Space><RocketOutlined /> 当前操作</Space>}
            extra={workspace && <Tag color={statusColor(workspace.status)}>{STATUS_LABEL[workspace.status] || workspace.status}</Tag>}
          >
            {!workspace && (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                {sourceMode === "keyword" && (
                  <div>
                    <div className="section-heading">
                      <Text strong>创作方式</Text>
                      <Text type="secondary">
                        {creationMode === "auto" ? "系统替你完成选稿和制作" : "先看素材，再由你选一条"}
                      </Text>
                    </div>
                    <Segmented
                      block
                      size="large"
                      value={creationMode}
                      options={[
                        { label: "自动创作", value: "auto" },
                        { label: "手动选择", value: "manual" },
                      ]}
                      onChange={(value) => {
                        const nextMode = value as CreationMode;
                        setCreationMode(nextMode);
                        const nextCandidate = nextMode === "auto"
                          ? selectSpokenAutomaticCandidates(candidates)[0]
                          : candidates[0];
                        setSelectedCandidateId(nextCandidate?.video_id || "");
                        setActionError("");
                      }}
                    />
                    <Paragraph type="secondary" style={{ margin: "8px 0 0" }}>
                      {creationMode === "auto"
                        ? "最多先转写 4 条；没有有效口播时，再从候补中补足。AI 只根据真实转写改成自然口播，不会根据纯画面猜文案。"
                        : "搜索完成后展示优先素材和候补参考，你选择 1 条后再进入转写、改写、数字人和剪辑流程。"}
                    </Paragraph>
                    {creationMode === "auto" && (
                      <Paragraph type="secondary" style={{ margin: "4px 0 0" }}>
                        {workspaceConfiguration.bundled_compute
                          ? "候补转写只在必要时调用，费用已包含在当前服务中。"
                          : "候补转写只在必要时调用；阿里 ASR 费用暂无法确定，启动预检时会再次核对。"}
                      </Paragraph>
                    )}
                  </div>
                )}
                <Segmented
                  block
                  value={sourceMode}
                  options={SOURCE_OPTIONS}
                  onChange={(value) => {
                    setSourceMode(value as SourceMode);
                    setActionError("");
                    setCandidates([]);
                    setSelectedCandidateId("");
                    setCrawlerReason(null);
                  }}
                />
                {sourceMode === "keyword" ? (
                  <>
                    <Input
                      size="large"
                      value={keyword}
                      placeholder="例如：餐饮老板获客、汽修店避坑"
                      onChange={(event) => {
                        setKeyword(event.target.value);
                        setCandidates([]);
                        setSelectedCandidateId("");
                        setCrawlerReason(null);
                      }}
                      onPressEnter={() => void handlePrimaryAction()}
                    />
                    {hotWords.length > 0 && (
                      <Space wrap size={[4, 4]}>
                        <Text type="secondary">实时热点词：</Text>
                        {hotWords.map((item) => (
                          <Button
                            key={item.word}
                            type="link"
                            size="small"
                            onClick={() => {
                              setKeyword(item.word);
                              setCandidates([]);
                              setSelectedCandidateId("");
                              setCrawlerReason(null);
                            }}
                          >
                            {item.word}
                          </Button>
                        ))}
                      </Space>
                    )}
                  </>
                ) : sourceMode === "share_link" ? (
                  <Input
                    size="large"
                    value={sourceValue}
                    placeholder="粘贴已获授权的视频分享链接"
                    onChange={(event) => setSourceValue(event.target.value)}
                  />
                ) : (
                  <TextArea
                    rows={8}
                    value={sourceValue}
                    placeholder="粘贴已经确认的口播文案"
                    onChange={(event) => setSourceValue(event.target.value)}
                  />
                )}

                {crawlerReason && (
                  <div className="search-empty">
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={crawlerReason.message} />
                    <Button onClick={() => void runKeywordSearch()} loading={busy}>
                      {crawlerReason.kind === "检索没有开始" ? "重新提交" : "换个词再找"}
                    </Button>
                  </div>
                )}

                {candidates.length > 0 && (
                  <div>
                    <div className="section-heading">
                      <Text strong>
                        {creationMode === "auto"
                          ? `口播候选 ${automaticCandidates.length} 条${automaticReserveCandidates.length ? `＋候补 ${automaticReserveCandidates.length} 条` : ""}`
                          : `本次找到的全部素材（${candidates.length}）`}
                      </Text>
                      <Text type="secondary">
                        {creationMode === "auto"
                          ? `${visualReferenceCount} 条画面参考不参与 ASR`
                          : `${candidates.length - visualReferenceCount} 条口播优先 · ${visualReferenceCount} 条画面参考`}
                      </Text>
                    </div>
                    <div className="candidate-stack">
                      {(creationMode === "auto" ? automaticCandidatePool : candidates).map((candidate, index) => (
                        <div
                          key={candidate.video_id}
                          className={`candidate-card${creationMode === "manual" ? " manual" : ""}${selectedCandidateId === candidate.video_id ? " selected" : ""}`}
                        >
                          <button
                            type="button"
                            className={`candidate-select${selectedCandidateId === candidate.video_id ? " selected" : ""}`}
                            onClick={() => {
                              if (creationMode === "manual") setSelectedCandidateId(candidate.video_id);
                            }}
                          >
                            <span className="candidate-rank">#{index + 1}</span>
                            <span className="candidate-copy">
                              <strong>{candidate.title || "未命名候选"}</strong>
                              <small>{candidate.platform_label} · {candidate.author_name || "作者未返回"}</small>
                              <small>
                                {creationMode === "auto" && index >= automaticCandidates.length
                                  ? "候补参考 · "
                                  : candidate.selection_tier === "reserve" ? "低热度候补 · " : ""}
                                {candidateSpokenUse(candidate).label}
                              </small>
                            </span>
                            {creationMode === "auto" || selectedCandidateId === candidate.video_id
                              ? <CheckCircleOutlined />
                              : null}
                          </button>
                          {creationMode === "manual" && (
                            candidate.source_url ? (
                              <a
                                className="candidate-source-link"
                                href={candidate.source_url}
                                target="_blank"
                                rel="noreferrer"
                                aria-label={`查看「${candidate.title || "未命名候选"}」原视频`}
                                onClick={(event) => event.stopPropagation()}
                              >
                                <VideoCameraOutlined /> 原视频
                              </a>
                            ) : (
                              <span className="candidate-source-unavailable">暂无原视频</span>
                            )
                          )}
                        </div>
                      ))}
                    </div>
                    {creationMode === "auto" && automaticCandidatePool.length === 0 && (
                      <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description="这批只有画面参考，没有可进入口播筛选的素材。"
                      />
                    )}
                  </div>
                )}
              </Space>
            )}

            {workspace && (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                {workspace.automation?.mode === "auto" && (
                  <Alert
                    type={workspace.automation.review_state === "failed" ? "warning" : "info"}
                    showIcon
                    message={
                      workspace.automation.review_state === "completed"
                        ? "AI 已完成可用口播择优"
                        : workspace.automation.review_state === "failed"
                          ? "自动选稿已停止，可改为人工确认"
                          : (workspace.automation.reserve_activated_count || 0) > 0
                            ? `正在补转写 ${workspace.automation.reserve_activated_count} 条候补`
                          : "自动创作正在进行"
                    }
                    description={
                      workspace.automation.error
                      || workspace.automation.selection_reason
                      || `系统先处理 ${workspace.automation.target_count || "本批"} 条素材；纯画面只作参考，有效内容会改成自然口播，最终只制作 1 条成片。`
                    }
                  />
                )}
                <Alert
                  type={activeItem?.error_message || activeItem?.blocked_reasons?.length ? "warning" : "info"}
                  showIcon
                  message={publishCompleted ? "已在抖音发布" : publishPagePrepared ? "抖音发布页已准备" : (STAGE_LABEL[currentStage] || currentStage)}
                  description={
                    activeItem?.error_message
                    || activeItem?.blocked_reasons?.join("；")
                    || (publishCompleted ? "这条任务已完成，进度 100%。" : "")
                    || (publishPagePrepared ? "检查内容后，可让系统安全点击一次最终发布，也可以由你手动完成。" : "")
                    || (nextAction === "wait" ? "系统正在处理，页面每 2.5 秒自动刷新。" : `下一步：${primaryLabel}`)
                  }
                />

                {avatarIsDelayed && (
                  <Alert
                    type="warning"
                    showIcon
                    message={`数字人处理偏慢，已等待 ${formatElapsedSeconds(avatarProcessing?.elapsed_seconds)}`}
                    description="系统会继续查询这一次生成，不会重复提交。电脑关机期间，开机后才会继续下载和剪辑。"
                  />
                )}

                {workspace.cost.blocked && !workspaceConfiguration.bundled_compute && (
                  <Alert
                    type="warning"
                    showIcon
                    message="还差一次费用设置"
                    description="当前供应商没有自动返回报价。填写合同中的单次价格后，系统才能在启动前算清总费用。"
                    action={<Button type="primary" onClick={() => setCostSetupOpen(true)}>填写费用</Button>}
                  />
                )}

                {showReviewEditor && (
                  <>
                    {nextAction === "review_transcript" && (
                      <>
                        <Space wrap>
                          <Tag color={(activeReview?.transcript.low_confidence_count || 0) > 0 ? "warning" : "success"}>
                            低置信片段 {activeReview?.transcript.low_confidence_count || 0}
                          </Tag>
                          <Tag>待核对片段 {activeReview?.transcript.uncertain_segment_count || 0}</Tag>
                        </Space>
                        {(activeReview?.transcript.low_confidence_segments?.length || 0) > 0 && (
                          <List
                            size="small"
                            bordered
                            header="需要重点核对的原转写片段"
                            dataSource={activeReview?.transcript.low_confidence_segments || []}
                            renderItem={(segment) => (
                              <List.Item>
                                <Space direction="vertical" size={0}>
                                  <Text>
                                    {formatSegmentTime(segment.start)}–{formatSegmentTime(segment.end)}　{segment.text || "未返回片段文本"}
                                  </Text>
                                  <Text type="secondary">
                                    {segment.confidence === null
                                      ? "置信度未返回"
                                      : `置信度 ${Math.round(segment.confidence * 100)}%`}
                                    {segment.quality_note ? ` · ${segment.quality_note}` : ""}
                                  </Text>
                                </Space>
                              </List.Item>
                            )}
                          />
                        )}
                      </>
                    )}
                    {nextAction === "review_script" && activeReview?.script.compliance_status && (
                      <>
                        <Alert
                          type={activeReview.script.compliance_status === "passed" ? "success" : "warning"}
                          showIcon
                          message={`风险检查：${activeReview.script.compliance_status}`}
                          description={activeReview.script.compliance_notes?.join("；") || "请人工核对事实、承诺和平台规则。"}
                        />
                        {(activeReview.script.attention_terms?.length || 0) > 0 && (
                          <Space wrap>
                            <Text type="secondary">重点核对：</Text>
                            {activeReview.script.attention_terms?.map((term) => <Tag color="warning" key={term}>{term}</Tag>)}
                          </Space>
                        )}
                      </>
                    )}
                    {nextAction === "review_script" && activeReview?.script.ai_audit && (
                      <Alert
                        type={activeReview.script.ai_audit.status === "completed" && activeReview.script.ai_audit.approved ? "success" : "warning"}
                        showIcon
                        message={
                          activeReview.script.ai_audit.status === "mock"
                            ? "AI 文案审核：演示结果"
                            : activeReview.script.ai_audit.status === "unavailable"
                              ? "AI 文案审核未完成"
                              : activeReview.script.ai_audit.approved
                                ? "AI 文案审核通过"
                                : "AI 文案审核提示需核对"
                        }
                        description={
                          <Space direction="vertical" size={2}>
                            <span>{activeReview.script.ai_audit.summary}</span>
                            {(activeReview.script.ai_audit.issues || []).map((issue, index) => (
                              <span key={`${issue.category}-${index}`}>
                                {issue.severity === "block" ? "需修改" : "建议"} · {issue.category}：{issue.message}
                              </span>
                            ))}
                          </Space>
                        }
                      />
                    )}
                    <div className="review-primary-editor">
                      <div className="review-editor-heading">
                        <Text strong>{nextAction === "review_transcript" ? "最终转写文本" : "最终口播稿"}</Text>
                        <Text type="secondary">
                          {nextAction === "review_transcript"
                            ? "核对原话是否准确"
                            : "只需确认这一份，系统会自动带入后续制作"}
                        </Text>
                      </div>
                      <TextArea
                        className="primary-review-textarea"
                        aria-label={nextAction === "review_transcript" ? "最终转写文本" : "最终口播稿"}
                        autoSize={{ minRows: 6, maxRows: 12 }}
                        value={reviewText}
                        onChange={(event) => setReviewText(event.target.value)}
                        placeholder={nextAction === "review_transcript" ? "核对并提交最终原转写" : "核对并提交最终口播稿"}
                      />
                    </div>
                    {nextAction === "review_script" && creativePlan ? (
                      <details
                        className="creative-plan-details"
                        open={creativePlanExpanded}
                        onToggle={(event) => setCreativePlanExpanded(event.currentTarget.open)}
                      >
                        <summary>
                          <span>
                            <strong>查看创作拆解</strong>
                            <small>开头、讲解要点、行动引导和画面段落</small>
                          </span>
                          <span>可选修改</span>
                        </summary>
                        <div className="creative-plan-fields">
                          <Text type="secondary">系统已根据最终口播稿自动整理；不修改也可以直接确认。</Text>
                          <Input
                            aria-label="开头吸引点"
                            value={creativePlan.hook}
                            placeholder="用一句话说清客户为什么要继续看"
                            maxLength={160}
                            onChange={(event) => setCreativePlan((current) => current ? { ...current, hook: event.target.value } : current)}
                          />
                          <TextArea
                            aria-label="讲解要点"
                            rows={3}
                            value={creativePlan.key_points.join("\n")}
                            placeholder="每行一个讲解要点"
                            maxLength={900}
                            onChange={(event) => setCreativePlan((current) => current ? {
                              ...current,
                              key_points: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean).slice(0, 5),
                            } : current)}
                          />
                          <Input
                            aria-label="行动引导"
                            value={creativePlan.call_to_action}
                            placeholder="告诉客户下一步该做什么"
                            maxLength={160}
                            onChange={(event) => setCreativePlan((current) => current ? { ...current, call_to_action: event.target.value } : current)}
                          />
                          <TextArea
                            aria-label="三个画面段落"
                            rows={3}
                            value={creativePlan.visual_sections.join("\n")}
                            placeholder="每行一个画面段落，例如：开场人物口播、产品或案例、收尾行动引导"
                            maxLength={540}
                            onChange={(event) => setCreativePlan((current) => current ? {
                              ...current,
                              visual_sections: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean).slice(0, 3),
                            } : current)}
                          />
                          <Input
                            value={reviewNote}
                            onChange={(event) => setReviewNote(event.target.value)}
                            placeholder="补充备注（可选）"
                          />
                        </div>
                      </details>
                    ) : (
                      <Input
                        value={reviewNote}
                        onChange={(event) => setReviewNote(event.target.value)}
                        placeholder="复核备注（可选）"
                      />
                    )}
                  </>
                )}

                {stageIndex(currentStage) === 4 && (
                  <div className="publish-review">
                    <Text strong>发布信息与账号状态</Text>
                    {activeItem?.publish.draft && !["succeeded", "outcome_unknown"].includes(activeItem.publish.status) ? (
                      <Space direction="vertical" size={10} style={{ width: "100%" }}>
                        {publishPagePrepared && (
                          <>
                            <Alert
                              type="success"
                              showIcon
                              message="抖音发布页已准备"
                              description={`请检查任务栏里的“抖音创作者中心”窗口。${activeItem.publish.action_required || "确认标题、封面和可见范围后，可以让系统安全完成最终发布。"}`}
                            />
                            <Space wrap>
                              <Button type="primary" icon={<RocketOutlined />} loading={busy} onClick={confirmAutomaticPublish}>
                                确认并自动发布
                              </Button>
                              <Button icon={<CheckCircleOutlined />} loading={busy} onClick={confirmManualPublishCompleted}>
                                我已手动发布
                              </Button>
                            </Space>
                          </>
                        )}
                        <Text type="secondary">系统已根据最终口播稿生成标题、描述和标签；不会继承原视频的人物或话题，请你审核后再保存。</Text>
                        <Input aria-label="发布标题" value={publishTitle} maxLength={30} showCount placeholder="30 字内，不写 # 或 @" onChange={(event) => setPublishTitle(event.target.value)} />
                        <TextArea aria-label="发布描述" autoSize={{ minRows: 6, maxRows: 14 }} value={publishDescription} maxLength={1000} showCount placeholder="写给观众看的作品描述" onChange={(event) => setPublishDescription(event.target.value)} />
                        <Input aria-label="发布标签" value={publishTags} placeholder="用顿号或逗号分隔；不填也可以" onChange={(event) => setPublishTags(event.target.value)} />
                        {nextAction !== "review_publish_draft"
                          && !(workspace.automation?.mode === "auto" && nextAction === "review_output")
                          && (
                          <Space wrap>
                            <Button loading={busy} onClick={() => void submitReview("publish")}>
                              保存发布信息
                            </Button>
                            {activeItem.publish.task_ids.length > 0 && !publishPagePrepared && (
                              <Button type="primary" icon={<RocketOutlined />} loading={busy} onClick={prepareExistingPublishPages}>
                                准备抖音发布页
                              </Button>
                            )}
                          </Space>
                        )}
                      </Space>
                    ) : (
                      <Descriptions size="small" column={1} bordered>
                        <Descriptions.Item label="标题">
                          {activeItem?.publish.draft?.title || "服务端尚未生成标题"}
                        </Descriptions.Item>
                        <Descriptions.Item label="描述">
                          {activeItem?.publish.draft?.description || "服务端尚未生成描述"}
                        </Descriptions.Item>
                        <Descriptions.Item label="标签">
                          {(activeItem?.publish.draft?.tags || []).length
                            ? activeItem?.publish.draft?.tags.map((tag) => <Tag key={tag}>{tag}</Tag>)
                            : "暂无标签"}
                        </Descriptions.Item>
                      </Descriptions>
                    )}
                    <List
                      size="small"
                      dataSource={publishDestinations}
                      locale={{ emptyText: "尚未选择发布平台" }}
                      renderItem={(destination) => (
                        <List.Item extra={<Tag color={destination.mode.includes("真实") ? "processing" : "default"}>{destination.mode}</Tag>}>
                          <List.Item.Meta
                            title={destination.displayName}
                            description={`${destination.accountName} · ${destination.accountStatus}`}
                          />
                        </List.Item>
                      )}
                    />
                  </div>
                )}

                <Progress
                  percent={Math.max(0, Math.min(100, Math.round(progressPercent)))}
                  showInfo={!avatarIsDelayed}
                  status={workspace.status === "failed" ? "exception" : "active"}
                />
                <Space wrap>
                  {allowedActions.includes("pause") && !avatarProcessing?.provider_job_received && (
                    <Button icon={<PauseCircleOutlined />} disabled={busy} onClick={() => void runControl("pause")}>暂停</Button>
                  )}
                  {allowedActions.includes("resume") && (
                    <Button icon={<PlayCircleOutlined />} disabled={busy} onClick={() => void runControl("resume")}>继续</Button>
                  )}
                  {allowedActions.includes("retry") && workspace.retry_allowed && (
                    <Button icon={<ReloadOutlined />} disabled={busy} onClick={() => void runControl("retry")}>安全重试</Button>
                  )}
                  <Button icon={<ReloadOutlined />} disabled={busy} onClick={() => void loadWorkspace(workspace.batch.batch_id)}>刷新</Button>
                </Space>
              </Space>
            )}

            {needsProfileConfiguration && (
              <Alert
                className="profile-gap"
                type="warning"
                showIcon
                icon={<SettingOutlined />}
                message="先补齐 IP 配方"
                description="选择一个可用的形象和声音即可；系统会自动完成通用智能优化。"
              />
            )}

            {needsProfileConfiguration ? (
              <div className="profile-form">
                <Input value={profileName} onChange={(event) => setProfileName(event.target.value)} placeholder="配方名称" />
                <Select
                  value={avatarId || undefined}
                  onChange={setAvatarId}
                  placeholder="选择已授权数字人形象"
                  options={assets.filter((asset) => asset.kind === "avatar" && asset.authorized && asset.status === "ready").map((asset) => ({ label: asset.name, value: asset.asset_id }))}
                />
                <Select
                  value={voiceId || undefined}
                  onChange={setVoiceId}
                  placeholder="选择已授权音色"
                  options={assets.filter((asset) => asset.kind === "voice" && asset.authorized && asset.status === "ready").map((asset) => ({ label: asset.name, value: asset.asset_id }))}
                />
                <Space wrap>
                  {!assets.some((asset) => asset.kind === "avatar" && asset.authorized && asset.status === "ready") && (
                    <Link to="/avatar">去配置并授权数字人形象</Link>
                  )}
                  {!assets.some((asset) => asset.kind === "voice" && asset.authorized && asset.status === "ready") && (
                    <Link to="/avatar">去配置并授权音色</Link>
                  )}
                </Space>
              </div>
            ) : null}

            <Button
              className="workspace-primary"
              type="primary"
              size="large"
              block
              loading={busy}
              disabled={nextAction === "wait" && busy}
              onClick={() => void handlePrimaryAction()}
            >
              {primaryLabel}
            </Button>

          </Card>

          {workspace ? renderWorkbenchTasksCard() : renderProfileCard()}

        </div>

        <div className="workspace-right">
          <Card
            title={<Space><VideoCameraOutlined /> 预览与实时状态</Space>}
            extra={workspace && <Text code>{workspace.batch.batch_id}</Text>}
          >
            {activeVideo ? (
              <>
                <div className="video-frame">
                  <video
                    key={`${activeVideo}-${videoReloadKey}`}
                    controls
                    preload="auto"
                    src={activeVideo}
                    onLoadedMetadata={(event) => {
                      // 跨页面进入时，浏览器可能只拿到时长而不绘制首帧。
                      // 轻微定位即可让它准备画面，且不会自动播放。
                      if (event.currentTarget.currentTime === 0) {
                        event.currentTarget.currentTime = 0.01;
                      }
                    }}
                    onLoadedData={() => setVideoLoadError(false)}
                    onCanPlay={() => setVideoLoadError(false)}
                    onError={() => setVideoLoadError(true)}
                  />
                </div>
                {videoLoadError && (
                  <Alert
                    className="video-load-error"
                    type="warning"
                    showIcon
                    message="成片已经生成，但预览暂时没有加载出来"
                    description="可先重新加载预览；若仍不行，可直接打开成片。"
                    action={(
                      <Space size="small">
                        <Button size="small" onClick={() => setVideoReloadKey((value) => value + 1)}>重新加载</Button>
                        <Button size="small" type="link" href={activeVideo} target="_blank">打开成片</Button>
                      </Space>
                    )}
                  />
                )}
              </>
            ) : sourceMode === "keyword" && creationMode === "auto" && automaticCandidates.length ? (
              <div className="source-preview">
                <Tag color="purple">
                  口播候选 {automaticCandidates.length} 条
                  {automaticReserveCandidates.length ? `＋候补 ${automaticReserveCandidates.length} 条` : ""}
                </Tag>
                <Title level={4}>先判断口播是否可用，再由 AI 选出 1 条</Title>
                <List
                  size="small"
                  dataSource={automaticCandidatePool}
                  renderItem={(candidate, index) => (
                    <List.Item
                      actions={candidate.source_url ? [
                        <a key="source" href={candidate.source_url} target="_blank" rel="noreferrer">原视频</a>,
                      ] : []}
                    >
                      <List.Item.Meta
                        title={`${index >= automaticCandidates.length ? "候补 · " : ""}${candidate.title || "未命名候选"}`}
                        description={`${candidate.platform_label} · ${candidate.author_name || "作者未返回"} · ${candidateSpokenUse(candidate).label}`}
                      />
                    </List.Item>
                  )}
                />
              </div>
            ) : selectedCandidate ? (
              <div className="source-preview">
                <Tag color="purple">已选素材</Tag>
                <Title level={4}>{selectedCandidate.title}</Title>
                <Text>{selectedCandidate.platform_label} · {selectedCandidate.author_name || "作者未返回"}</Text>
                <Paragraph type="secondary">{selectedCandidate.relevance_reason || selectedCandidate.reasons.join("；")}</Paragraph>
                {selectedCandidate.source_url && <a href={selectedCandidate.source_url} target="_blank" rel="noreferrer">查看原视频来源</a>}
              </div>
            ) : sourceMode !== "keyword" && sourceValue ? (
              <div className="source-preview">
                <Tag>输入内容</Tag>
                <Paragraph ellipsis={{ rows: 8, expandable: true }}>{sourceValue}</Paragraph>
              </div>
            ) : (
              <Empty description="选择素材或启动任务后，这里显示预览与实时结果" />
            )}

            {workspace && (
              <>
                <div className="status-summary">
                  <div><span>批次状态</span><strong>{STATUS_LABEL[workspace.status] || workspace.status}</strong></div>
                  <div><span>当前阶段</span><strong>{STAGE_LABEL[currentStage] || currentStage}</strong></div>
                  <div>
                    <span>预计费用</span>
                    <strong>{workspace.cost.known ? `¥${Number(workspace.cost.estimated_cost_cny || 0).toFixed(2)}` : "未知 · 已阻断"}</strong>
                  </div>
                  <div><span>发布方式</span><strong>{workspace.publish.message || (workspace.publish.confirmed ? "已确认" : "待确认")}</strong></div>
                </div>
                <Timeline
                  items={(activeItem?.blocked_reasons?.length
                    ? activeItem.blocked_reasons.map((reason) => ({ color: "red", children: reason }))
                    : [
                        { color: stageIndex(currentStage) > 0 ? "green" : "blue", children: "素材与选题" },
                        { color: businessStageIndex(currentStage) > 1 ? "green" : businessStageIndex(currentStage) === 1 ? "blue" : "gray", children: "确认转写、口播稿与创作方案" },
                        { color: businessStageIndex(currentStage) === 2 ? "blue" : "gray", children: "制作成片、复核并查看发布状态" },
                      ])}
                />
              </>
            )}
          </Card>

          {workspace ? renderProfileCard() : renderWorkbenchTasksCard()}

        </div>
      </div>

      <Modal
        title="开工前准备"
        open={setupOpen}
        onCancel={() => setSetupOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">一次配好素材来源、默认配方和授权，进入后不会做到一半才被打断。</Text>
          <Alert
            type={allSourceConnectionsReady ? "success" : sourceConnectionReady ? "info" : "warning"}
            showIcon
            message={
              allSourceConnectionsReady
                ? "三个素材平台都已连接"
                : sourceConnectionReady
                  ? `已连接 ${readySourceCount}/3 个平台`
                  : "先连接至少一个素材平台"
            }
            description={
              allSourceConnectionsReady
                ? "抖音热点宝、快手和B站都可以开始找素材。"
                : "可以先使用已连接的平台，其他平台登录后再刷新状态。"
            }
          />
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            {browserDiscoveries.map((connection) => {
              const platform = connection.platform as BrowserPlatform;
              const ready = Boolean(connection.enabled && connection.ready_to_crawl);
              return (
                <Card
                  key={platform}
                  size="small"
                  title={connection.platform_label}
                  extra={<Tag color={ready ? "success" : "default"}>{ready ? "已连接" : "未连接"}</Tag>}
                >
                  <Space direction="vertical" size={8} style={{ width: "100%" }}>
                    <Text type="secondary">
                      {ready
                        ? "已可找素材。"
                        : connection.enabled
                          ? "点下面的登录按钮会打开可见窗口，扫码或完成验证后回来刷新状态。"
                          : connection.message}
                    </Text>
                    <Button
                      type={ready ? "default" : "primary"}
                      loading={startingBrowserPlatform === platform}
                      disabled={!connection.enabled || (busy && startingBrowserPlatform !== platform)}
                      onClick={() => void startSourceConnection(platform)}
                    >
                      {ready ? "打开浏览器" : `登录${connection.platform_label}`}
                    </Button>
                  </Space>
                </Card>
              );
            })}
            {!browserDiscoveries.length && <Text type="secondary">正在读取三个素材平台的连接状态…</Text>}
            <Button loading={busy && startingBrowserPlatform === null} onClick={() => void refreshSourceConnections()}>
              我已登录，刷新状态
            </Button>
          </Space>
          <Input
            value={setupRightsHolder}
            onChange={(event) => setSetupRightsHolder(event.target.value)}
            placeholder="公司名称或本人姓名"
            autoFocus
          />
          <div>
            <Text strong>发布到哪些平台？</Text>
            <Checkbox.Group
              value={publishPlatforms}
              onChange={(values) => setPublishPlatforms(values.map(String))}
              style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 10 }}
            >
              {platforms.map((platform) => {
                const selectable = platform.enabled || platform.manual_fallback || platform.manual_only;
                return (
                  <Checkbox key={platform.platform} value={platform.platform} disabled={!selectable}>
                    {PLATFORM_LABELS[platform.platform] || platform.display_name}
                  </Checkbox>
                );
              })}
            </Checkbox.Group>
            <Text type="secondary" style={{ display: "block", marginTop: 8 }}>
              可以多选。每个平台需要单独登录一次发布账号，登录状态只保留在本机。
            </Text>
            <Space direction="vertical" size={8} style={{ width: "100%", marginTop: 12 }}>
              {publishPlatforms.map((platform) => {
                const capability = platforms.find((item) => item.platform === platform);
                const readyAccount = accounts.find(
                  (account) => account.platform === platform && account.status === "ready",
                );
                const knownAccount = readyAccount || accounts.find((account) => account.platform === platform);
                const requiresAccount = Boolean(capability?.requires_account);
                const statusLabel = !requiresAccount
                  ? "无需登录"
                  : readyAccount
                    ? "已核验可发布"
                    : knownAccount?.status === "browser_open"
                      ? "等待扫码"
                      : "未登录";
                const statusColor = !requiresAccount || readyAccount
                  ? "success"
                  : knownAccount?.status === "browser_open"
                    ? "processing"
                    : "warning";
                return (
                  <Card key={platform} size="small">
                    <Space direction="vertical" size={8} style={{ width: "100%" }}>
                      <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                        <Space>
                          <Text strong>{PLATFORM_LABELS[platform] || capability?.display_name || platform}</Text>
                          <Tag color={statusColor}>{statusLabel}</Tag>
                        </Space>
                        {requiresAccount && !readyAccount && (
                          <Button
                            type="primary"
                            loading={publishAccountAction === platform}
                            disabled={Boolean(publishAccountAction && publishAccountAction !== platform)}
                            onClick={() => void connectPublishAccountFromSetup(platform)}
                          >
                            {knownAccount ? "打开扫码登录" : "添加并扫码"}
                          </Button>
                        )}
                      </Space>
                      {readyAccount && (
                        <Text type="secondary">{readyAccount.name} · 已可用于发布成片</Text>
                      )}
                      {requiresAccount && !readyAccount && knownAccount && (
                        <Space wrap>
                          <Text type="secondary">{knownAccount.message}</Text>
                          {knownAccount.status === "browser_open" && (
                            <Button
                              type="link"
                              size="small"
                              loading={publishAccountAction === platform}
                              onClick={() => void refreshPublishAccount(knownAccount.account_id, platform)}
                            >
                              我已登录，检查状态
                            </Button>
                          )}
                        </Space>
                      )}
                    </Space>
                  </Card>
                );
              })}
            </Space>
          </div>
          {selectedProfile && (
            <div className="setup-profile-line">
              <span>本次出镜人</span>
              <strong>{profileAvatar?.name || selectedProfile.name}</strong>
              <Space size={0}>
                <Button type="link" onClick={openProfileSwitcher}>换一个</Button>
                <Button type="link" onClick={openProfileCreator}>新增出镜人</Button>
              </Space>
            </div>
          )}
          <Button type="primary" size="large" block loading={busy} disabled={!sourceConnectionReady} onClick={() => void saveWorkspaceSetup()}>
            保存设置并进入工作台
          </Button>
        </Space>
      </Modal>

      <Modal
        title="填写实际费用"
        open={costSetupOpen}
        onCancel={() => setCostSetupOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">仅在按次计费时需要填写。包算力模式不需要填写，系统会按套餐内处理。</Text>
          <div>
            <Text strong>文案生成每次费用（元）</Text>
            <InputNumber
              aria-label="文案生成每次费用"
              min={0}
              precision={2}
              value={copywritingCost}
              onChange={setCopywritingCost}
              placeholder="例如 0.02"
              style={{ width: "100%", marginTop: 8 }}
            />
          </div>
          <div>
            <Text strong>数字人口播每条费用（元）</Text>
            <InputNumber
              aria-label="数字人口播每条费用"
              min={0}
              precision={2}
              value={avatarCost}
              onChange={setAvatarCost}
              placeholder="例如 2.50"
              style={{ width: "100%", marginTop: 8 }}
            />
          </div>
          <Button type="primary" size="large" block loading={busy} onClick={() => void saveCostSetup()}>
            保存费用
          </Button>
        </Space>
      </Modal>

      <Modal
        title={profileEditorMode === "switch" ? "选择出镜人和声音" : "新增出镜人"}
        aria-label={profileEditorMode === "switch" ? "选择出镜人和声音" : "新增出镜人"}
        open={profileCreateOpen}
        onCancel={() => {
          setProfileCreateOpen(false);
          setSetupOpen(true);
        }}
        footer={null}
        destroyOnHidden
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">
            {profileEditorMode === "switch"
              ? "可直接换形象或声音；切换形象时会优先带入同名声音，仍可手动调整。"
              : "选择形象和声音即可，系统会自动完成通用智能优化。"}
          </Text>
          <Input value={profileName} onChange={(event) => setProfileName(event.target.value)} placeholder="出镜人名称" />
          <div>
            <Text strong>选择形象</Text>
            {usableAvatarAssets.length ? (
              <div className="profile-avatar-grid" aria-label="形象选择列表">
                {usableAvatarAssets.map((asset) => (
                  <button
                    type="button"
                    key={asset.asset_id}
                    className={`profile-avatar-card${asset.asset_id === avatarId ? " selected" : ""}`}
                    aria-label={`选择形象：${asset.name}`}
                    aria-pressed={asset.asset_id === avatarId}
                    onClick={() => selectProfileAvatar(asset)}
                  >
                    <span className="profile-avatar-card-media">
                      {asset.preview_url ? (
                        asset.preview_type === "video" ? (
                          <video
                            src={asset.preview_url}
                            aria-label={`${asset.name} 形象预览`}
                            muted
                            playsInline
                            preload="metadata"
                          />
                        ) : (
                          <img src={asset.preview_url} alt={`${asset.name} 形象预览`} />
                        )
                      ) : (
                        <SafetyCertificateOutlined />
                      )}
                    </span>
                    <strong>{asset.name}</strong>
                    <small>{asset.asset_id === avatarId ? "已选择" : "点击选择"}</small>
                  </button>
                ))}
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可用形象" />
            )}
          </div>
          <Space.Compact block>
            <Select
              aria-label="选择声音"
              style={{ flex: 1 }}
              value={voiceId || undefined}
              onChange={setVoiceId}
              placeholder="选择已授权音色"
              options={[
                ...usableVoiceAssets.map((asset) => ({ label: asset.name, value: asset.asset_id })),
                ...pendingVoiceAssets.map((asset) => ({
                  label: `${asset.name}（等待声音服务开通）`,
                  value: asset.asset_id,
                  disabled: true,
                })),
              ]}
            />
            <Button
              icon={playingVoiceId === voiceId ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
              disabled={!voiceId}
              onClick={() => void toggleVoicePreview()}
            >
              {playingVoiceId === voiceId ? "停止" : "试听"}
            </Button>
            <Button
              aria-label="添加新声音"
              onClick={() => setVoiceUploadOpen(true)}
            >
              ＋ 添加
            </Button>
          </Space.Compact>
          <audio
            ref={voicePreviewRef}
            src={voicePreviewSrc || undefined}
            preload="none"
            aria-label={`声音试听：${selectedCreatorVoice?.name || "未选择"}`}
            onPlay={() => setPlayingVoiceId(voiceId)}
            onPause={() => setPlayingVoiceId("")}
            onEnded={() => setPlayingVoiceId("")}
            onError={() => {
              setPlayingVoiceId("");
              setVoicePreviewError("暂时无法试听这个声音，请稍后重试。");
            }}
          />
          {voicePreviewError && <Text type="danger">{voicePreviewError}</Text>}
          {pendingVoiceAssets.length > 0 && (
            <Text type="secondary">
              已保存声音样本：{pendingVoiceAssets.map((asset) => asset.name).join("、")}。当前声音服务尚未开通，暂不能选择。
            </Text>
          )}
          {voiceUploadOpen && (
            <div className="voice-upload-panel">
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <div className="voice-upload-heading">
                  <Text strong>添加新声音</Text>
                  <Button
                    type="link"
                    size="small"
                    disabled={uploadingVoice}
                    onClick={() => {
                      setVoiceUploadOpen(false);
                    }}
                  >
                    暂不添加
                  </Button>
                </div>
                <Text type="secondary">
                  {avatarCapability?.supports_voice_cloning
                    ? "支持 MP3、WAV、M4A，需在 30 秒以内。处理完成后会自动选中。"
                    : "支持 MP3、WAV、M4A，需在 30 秒以内。当前只能先保存样本，声音服务开通后才能选择。"}
                </Text>
                <Upload
                  accept="audio/wav,audio/mpeg,audio/mp3,audio/mp4,.wav,.mp3,.m4a"
                  showUploadList={false}
                  beforeUpload={(file) => {
                    void uploadVoiceSample(file);
                    return false;
                  }}
                >
                  <Button
                    type="primary"
                    icon={<UploadOutlined />}
                    loading={uploadingVoice}
                    disabled={!voiceSampleUploadAvailable}
                  >
                    {avatarCapability?.supports_voice_cloning ? "确认上传并处理" : "保存声音样本"}
                  </Button>
                </Upload>
                {!voiceSampleUploadAvailable && (
                  <Text type="danger">当前数字人服务暂未开放声音上传。</Text>
                )}
              </Space>
            </div>
          )}
          <Button type="primary" size="large" block loading={busy} onClick={() => void saveProfile()}>
            {profileEditorMode === "switch" ? "使用这个出镜组合" : "保存并使用这个出镜人"}
          </Button>
        </Space>
      </Modal>

      <style>{`
        .smart-workspace {
          --workspace-purple: #6f49e8;
          --workspace-blue: #3d7df7;
          display: flex;
          flex-direction: column;
          gap: 16px;
          min-width: 0;
        }
        .workspace-hero {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 24px;
          padding: 24px 28px;
          border-radius: 20px;
          color: #fff;
          background:
            radial-gradient(circle at 85% 20%, rgba(255,255,255,.22), transparent 32%),
            linear-gradient(120deg, #3d7df7, #7b4ce8 58%, #9b55ee);
          box-shadow: 0 18px 45px rgba(82, 71, 188, .18);
        }
        .workspace-hero h2 { color: #fff; margin: 0; }
        .hero-state {
          min-width: 170px;
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 4px;
        }
        .hero-state .ant-typography { color: rgba(255,255,255,.78); }
        .hero-state strong { font-size: 20px; }
        .stage-overview { overflow: hidden; }
        .stage-overview .ant-steps-item-process .ant-steps-item-icon {
          background: var(--workspace-purple);
          border-color: var(--workspace-purple);
        }
        .workspace-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 16px;
          align-items: start;
        }
        .workspace-left,
        .workspace-right {
          display: flex;
          flex-direction: column;
          gap: 16px;
          min-width: 0;
        }
        .section-heading {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          gap: 12px;
          margin-bottom: 10px;
        }
        .candidate-stack { display: flex; flex-direction: column; gap: 9px; }
        .candidate-card {
          width: 100%;
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          align-items: center;
          border: 1px solid #e6e8ef;
          border-radius: 12px;
          background: #fff;
          color: inherit;
          overflow: hidden;
        }
        .candidate-card:hover { border-color: #8a65ed; box-shadow: 0 0 0 3px rgba(111,73,232,.12); }
        .candidate-card.selected { border-color: #7652e8; background: #f7f3ff; }
        .candidate-select {
          min-width: 0;
          display: grid;
          grid-template-columns: auto minmax(0, 1fr) auto;
          align-items: center;
          gap: 12px;
          border: 0;
          padding: 12px;
          background: transparent;
          color: inherit;
          text-align: left;
          cursor: pointer;
        }
        .candidate-select:focus-visible {
          outline: 3px solid rgba(111,73,232,.2);
          outline-offset: -3px;
        }
        .candidate-rank {
          display: grid;
          place-items: center;
          width: 34px;
          height: 34px;
          border-radius: 9px;
          color: #603ad4;
          background: #ebe3ff;
          font-weight: 700;
        }
        .candidate-copy { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
        .candidate-copy strong,
        .candidate-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .candidate-card.manual .candidate-copy strong {
          display: -webkit-box;
          white-space: normal;
          -webkit-box-orient: vertical;
          -webkit-line-clamp: 2;
        }
        .candidate-copy small { color: #777e8d; }
        .candidate-source-link,
        .candidate-source-unavailable {
          margin-right: 12px;
          white-space: nowrap;
        }
        .candidate-source-link {
          padding: 6px 10px;
          border: 1px solid #d9d0f5;
          border-radius: 8px;
          color: #6941d9;
          background: #fff;
        }
        .candidate-source-link:hover,
        .candidate-source-link:focus-visible {
          border-color: #7652e8;
          color: #4f2bb8;
          outline: none;
        }
        .candidate-source-unavailable {
          color: #a0a5b1;
          font-size: 12px;
        }
        .profile-gap { margin-top: 18px; }
        .publish-review {
          display: flex;
          flex-direction: column;
          gap: 10px;
          padding: 12px;
          border: 1px solid #e4ddf7;
          border-radius: 12px;
          background: #fbf9ff;
        }
        .review-primary-editor {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .review-editor-heading {
          display: flex;
          align-items: baseline;
          justify-content: space-between;
          gap: 12px;
        }
        .review-editor-heading .ant-typography:last-child {
          text-align: right;
        }
        .primary-review-textarea {
          font-size: 16px;
          line-height: 1.75;
        }
        .creative-plan-details {
          margin-top: 2px;
          border-top: 1px solid #eceef4;
        }
        .creative-plan-details summary {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          padding: 12px 2px 4px;
          color: #6941d9;
          cursor: pointer;
          list-style: none;
        }
        .creative-plan-details summary::-webkit-details-marker { display: none; }
        .creative-plan-details summary > span:first-child {
          display: flex;
          flex-direction: column;
          gap: 2px;
          color: #2c3140;
        }
        .creative-plan-details summary small {
          color: #8a90a0;
          font-weight: 400;
        }
        .creative-plan-details summary:focus-visible {
          border-radius: 8px;
          outline: 3px solid rgba(111,73,232,.16);
          outline-offset: 2px;
        }
        .creative-plan-fields {
          display: flex;
          flex-direction: column;
          gap: 10px;
          margin-top: 10px;
          padding: 14px;
          border-radius: 12px;
          background: #faf9ff;
        }
        .profile-form {
          display: flex;
          flex-direction: column;
          gap: 10px;
          margin-top: 12px;
        }
        .search-empty {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 4px;
          padding: 6px 0;
        }
        .profile-summary {
          display: flex;
          flex-direction: column;
          gap: 14px;
        }
        .profile-summary-details {
          padding: 12px 14px;
          border-radius: 12px;
          background: #f8f6ff;
        }
        .profile-summary-details .ant-descriptions-item {
          padding-bottom: 0;
        }
        .profile-summary-details .ant-descriptions-item-label {
          color: #777e8d;
        }
        .profile-summary-details .ant-descriptions-item-content {
          color: #252938;
          font-weight: 600;
        }
        .profile-avatar-preview,
        .profile-picker-media {
          display: grid;
          place-items: center;
          overflow: hidden;
          background: #f2eeff;
          color: #7252dc;
        }
        .profile-avatar-preview {
          position: relative;
          align-self: center;
          width: min(42%, 168px);
          aspect-ratio: 9 / 16;
          border-radius: 14px;
        }
        .profile-avatar-preview img,
        .profile-avatar-preview video {
          position: absolute;
          inset: 0;
          display: block;
          width: 100%;
          height: 100%;
          min-width: 0;
          min-height: 0;
          object-fit: contain;
          object-position: center;
        }
        .profile-picker-media img,
        .profile-picker-media video { width: 100%; height: 100%; object-fit: cover; }
        .setup-profile-line {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 10px 12px;
          border-radius: 10px;
          background: #f7f4ff;
        }
        .setup-profile-line .ant-btn { margin-left: auto; }
        .profile-picker-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
        .profile-picker-card {
          display: flex;
          align-items: center;
          gap: 10px;
          width: 100%;
          padding: 10px;
          border: 1px solid #e2e4eb;
          border-radius: 12px;
          background: #fff;
          color: inherit;
          text-align: left;
          cursor: pointer;
        }
        .profile-picker-card.selected { border-color: #7652e8; background: #f7f3ff; }
        .profile-picker-card:focus-visible { outline: 3px solid rgba(111,73,232,.2); }
        .profile-picker-card span:last-child { display: flex; flex-direction: column; min-width: 0; }
        .profile-picker-card small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #7b8190; }
        .profile-picker-media { flex: 0 0 64px; width: 64px; height: 64px; border-radius: 10px; }
        .profile-avatar-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
          gap: 8px;
          max-height: 300px;
          margin-top: 8px;
          padding-right: 4px;
          overflow-y: auto;
        }
        .profile-avatar-card {
          display: flex;
          flex-direction: column;
          gap: 4px;
          min-width: 0;
          padding: 0 0 8px;
          overflow: hidden;
          border: 2px solid #ebeef5;
          border-radius: 12px;
          background: #fff;
          color: #262a33;
          text-align: left;
          cursor: pointer;
        }
        .profile-avatar-card.selected {
          border-color: #7652e8;
          background: #f7f3ff;
          box-shadow: 0 0 0 2px rgba(111, 73, 232, .08);
        }
        .profile-avatar-card:focus-visible { outline: 3px solid rgba(111, 73, 232, .2); }
        .profile-avatar-card strong,
        .profile-avatar-card small {
          overflow: hidden;
          padding: 0 10px;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .profile-avatar-card small { color: #7b8190; }
        .profile-avatar-card-media {
          display: grid;
          place-items: center;
          width: 100%;
          height: 108px;
          overflow: hidden;
          background: #f3efff;
          color: #7652e8;
          font-size: 28px;
        }
        .profile-avatar-card-media img,
        .profile-avatar-card-media video {
          display: block;
          width: 100%;
          height: 108px !important;
          min-height: 108px;
          max-height: 108px;
          object-fit: cover;
          object-position: center 70%;
        }
        .voice-upload-panel {
          padding: 12px;
          border: 1px solid #e9e2ff;
          border-radius: 10px;
          background: #fbf9ff;
        }
        .voice-upload-heading {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
        }
        .workspace-primary {
          height: 48px;
          margin-top: 16px;
          border: 0;
          font-weight: 700;
          background: linear-gradient(90deg, var(--workspace-blue), #8051eb);
          box-shadow: 0 10px 24px rgba(90, 72, 213, .22);
        }
        .video-frame {
          overflow: hidden;
          aspect-ratio: 16 / 9;
          border-radius: 14px;
          background: #12131a;
        }
        .video-frame video { width: 100%; height: 100%; object-fit: contain; }
        .source-preview {
          min-height: 250px;
          display: flex;
          flex-direction: column;
          justify-content: center;
          align-items: flex-start;
          padding: 28px;
          border: 1px dashed #cbc4e8;
          border-radius: 14px;
          background: linear-gradient(145deg, #faf9ff, #f1f5ff);
        }
        .status-summary {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
          margin: 18px 0 20px;
        }
        .status-summary div {
          display: flex;
          flex-direction: column;
          gap: 3px;
          padding: 12px;
          border-radius: 10px;
          background: #f7f7fb;
        }
        .status-summary span { color: #7a8090; font-size: 12px; }
        .active-batch { background: #faf7ff; }
        .workbench-task-list .ant-list-item {
          gap: 10px;
          align-items: center;
        }
        .workbench-task-list .ant-list-item-meta {
          min-width: 0;
        }
        .workbench-task-title {
          display: -webkit-box;
          overflow: hidden;
          -webkit-box-orient: vertical;
          -webkit-line-clamp: 2;
          line-clamp: 2;
          line-height: 1.5;
        }
        .workbench-task-footer {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          padding-top: 12px;
          border-top: 1px solid #f0f0f0;
        }
        @media (max-width: 1100px) {
          .workspace-grid { grid-template-columns: 1fr; }
        }
        @media (max-width: 768px) {
          .workspace-hero { align-items: flex-start; padding: 20px; }
          .hero-state { min-width: 0; align-items: flex-start; }
          .workspace-hero { flex-direction: column; }
          .stage-overview .ant-steps-item-title { font-size: 12px; }
          .status-summary { grid-template-columns: 1fr; }
          .section-heading { flex-direction: column; }
          .profile-picker-grid { grid-template-columns: 1fr; }
          .profile-avatar-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
      `}</style>
    </div>
  );
}
