import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
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
  Spin,
  Steps,
  Tag,
  Timeline,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RocketOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  confirmProductionBatchPublish,
  createCrawlerBatch,
  createProductionBatch,
  createProductionProfile,
  getProductionWorkspaceConfiguration,
  getCrawlerBatch,
  getCrawlerHotWords,
  getProductionBatchWorkspace,
  listAvatarAssets,
  listPipelines,
  listProductionBatches,
  listProductionProfiles,
  listPublishAccounts,
  listPublishPlatforms,
  listTemplates,
  pauseProductionBatch,
  preflightProductionBatch,
  preflightProductionBatchPublish,
  previewCrawlerBatch,
  resumeProductionBatch,
  retryProductionBatchFailed,
  reviewProductionBatchItems,
  saveProductionWorkspaceConfiguration,
  startProductionBatch,
} from "../api/client";
import type {
  AvatarAsset,
  CrawlerBatchResponse,
  CrawlerCandidateResult,
  CrawlerHotWordItem,
  CrawlerSearchRequest,
  EditTemplate,
  PipelineResponse,
  ProductionBatch,
  ProductionProfile,
  ProductionPublishTarget,
  ProductionWorkspace,
  ProductionWorkspaceConfiguration,
  PublishAccount,
  PublishPlatformCapability,
} from "../api/types";

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

type SourceMode = "keyword" | "share_link" | "brief" | "script";
type ReviewStage = "transcript" | "script" | "output";

const PROFILE_STORAGE_KEY = "pipeline.lastProfileId";

const PLATFORM_LABELS: Record<string, string> = {
  douyin: "抖音",
  kuaishou: "快手",
  xiaohongshu: "小红书",
  bilibili: "哔哩哔哩",
  wechat_video: "视频号",
};

const SOURCE_OPTIONS = [
  { label: "关键词找素材", value: "keyword" },
  { label: "视频链接", value: "share_link" },
  { label: "输入选题", value: "brief" },
  { label: "已有文案", value: "script" },
];

const WORKSPACE_STEPS = [
  { title: "01 素材与选题" },
  { title: "02 文案确认" },
  { title: "03 数字人口播" },
  { title: "04 剪辑成片" },
  { title: "05 确认发布" },
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
  succeeded: "已完成",
  completed: "已完成",
  failed: "失败",
  partial: "部分完成",
  blocked: "已阻断",
  outcome_unknown: "结果待核对",
};

const STAGE_LABEL: Record<string, string> = {
  source: "素材与选题",
  transcript: "核对原转写",
  script: "确认最终文案",
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
  templates: EditTemplate[],
) {
  const avatar = assets.find((item) => item.asset_id === profile.avatar_id);
  const voice = assets.find((item) => item.asset_id === profile.voice_id);
  return Boolean(
    avatar?.kind === "avatar"
    && avatar.authorized
    && avatar.status === "ready"
    && voice?.kind === "voice"
    && voice.authorized
    && voice.status === "ready"
    && templates.some((item) => item.template_id === profile.edit_template_id),
  );
}

function stageIndex(stage: string | null | undefined) {
  if (!stage || stage === "source" || stage === "media_resolution") return 0;
  if (["transcript", "script", "transcription", "copywriting", "human_review"].includes(stage)) return 1;
  if (["avatar", "avatar_generation"].includes(stage)) return 2;
  if (["editing", "video_editing"].includes(stage)) return 3;
  return 4;
}

function statusColor(status: string | null | undefined) {
  if (["succeeded", "completed", "ready_to_publish"].includes(status || "")) return "success";
  if (["failed", "outcome_unknown"].includes(status || "")) return "error";
  if (["blocked", "partial", "awaiting_review", "awaiting_publish"].includes(status || "")) return "warning";
  if (["running", "queued"].includes(status || "")) return "processing";
  return "default";
}

function candidateRank(candidate: CrawlerCandidateResult) {
  return candidate.system_rank ?? candidate.provider_hot_rank ?? candidate.platform_rank ?? 999_999;
}

function strictCandidates(batch: CrawlerBatchResponse, preferredCandidateId = "") {
  const strict = batch.platform_runs.flatMap((run) => run.candidates || []);
  const preferred = preferredCandidateId
    ? batch.platform_runs
        .flatMap((run) => [...(run.candidates || []), ...(run.low_incremental_candidates || [])])
        .find((candidate) => candidate.video_id === preferredCandidateId)
    : undefined;
  const ranked = [...strict]
    .sort((left, right) => {
      const rank = candidateRank(left) - candidateRank(right);
      if (rank !== 0) return rank;
      return (right.trend_score ?? -1) - (left.trend_score ?? -1);
    });
  if (!preferred) return ranked.slice(0, 3);
  return [preferred, ...ranked.filter((item) => item.video_id !== preferred.video_id)].slice(0, 3);
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
  return item?.result_media_url || item?.video_path || null;
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

  const [initializing, setInitializing] = useState(true);
  const [busy, setBusy] = useState(false);
  const [polling, setPolling] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionMessage, setActionMessage] = useState("");

  const [sourceMode, setSourceMode] = useState<SourceMode>("keyword");
  const [keyword, setKeyword] = useState("");
  const [sourceValue, setSourceValue] = useState("");
  const [candidates, setCandidates] = useState<CrawlerCandidateResult[]>([]);
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [crawlerReason, setCrawlerReason] = useState<{ kind: string; message: string } | null>(null);
  const [hotWords, setHotWords] = useState<CrawlerHotWordItem[]>([]);

  const [profiles, setProfiles] = useState<ProductionProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [assets, setAssets] = useState<AvatarAsset[]>([]);
  const [templates, setTemplates] = useState<EditTemplate[]>([]);
  const [profileName, setProfileName] = useState("我的短视频 IP");
  const [avatarId, setAvatarId] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [templateId, setTemplateId] = useState("");

  const [platforms, setPlatforms] = useState<PublishPlatformCapability[]>([]);
  const [accounts, setAccounts] = useState<PublishAccount[]>([]);
  const [publishPlatforms, setPublishPlatforms] = useState<string[]>(["douyin"]);
  const [workspaceConfiguration, setWorkspaceConfiguration] = useState<ProductionWorkspaceConfiguration>({ configured: false });
  const [setupOpen, setSetupOpen] = useState(false);
  const [setupRightsHolder, setSetupRightsHolder] = useState("");
  const [setupAgreementAccepted, setSetupAgreementAccepted] = useState(false);
  const [profilePickerOpen, setProfilePickerOpen] = useState(false);
  const [costSetupOpen, setCostSetupOpen] = useState(false);
  const [copywritingCost, setCopywritingCost] = useState<number | null>(null);
  const [avatarCost, setAvatarCost] = useState<number | null>(null);

  const [batches, setBatches] = useState<ProductionBatch[]>([]);
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [workspace, setWorkspace] = useState<ProductionWorkspace | null>(null);
  const [legacyPending, setLegacyPending] = useState<PipelineResponse[]>([]);
  const [reviewText, setReviewText] = useState("");
  const [reviewNote, setReviewNote] = useState("");

  const completeProfiles = useMemo(
    () => profiles.filter((profile) => isUsableProfile(profile, assets, templates)),
    [assets, profiles, templates],
  );
  const selectedProfile = useMemo(
    () => completeProfiles.find((profile) => profile.profile_id === profileId) || null,
    [completeProfiles, profileId],
  );
  const selectedCandidate = useMemo(
    () => candidates.find((candidate) => candidate.video_id === selectedCandidateId) || null,
    [candidates, selectedCandidateId],
  );
  const activeItem = useMemo(() => {
    if (!workspace) return undefined;
    return workspace.items.find((item) => item.run_id === selectedRunId)
      || workspace.items.find((item) => item.run_id === workspace.current_run_id)
      || workspace.items[0];
  }, [selectedRunId, workspace]);
  const currentStage = activeItem?.stage || activeItem?.current_stage || workspace?.current_stage || "source";
  const nextAction = activeItem?.next_action || workspace?.next_action || "start";
  const allowedActions = activeItem?.allowed_actions || workspace?.allowed_actions || [];
  const activeReview = activeItem?.reviews;
  const activeProfile = workspace?.profile || selectedProfile;
  const profileAvatar = assets.find((asset) => asset.asset_id === activeProfile?.avatar_id);
  const profileVoice = assets.find((asset) => asset.asset_id === activeProfile?.voice_id);
  const profileTemplate = templates.find((template) => template.template_id === activeProfile?.edit_template_id);

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
    if (silent) setPolling(true);
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
    } finally {
      if (silent) setPolling(false);
    }
  }, []);

  const loadInitialData = useCallback(async () => {
    setInitializing(true);
    setLoadError("");
    try {
      const [profileData, assetData, templateData, platformData, accountData, batchData, pipelineData, configuration] =
        await Promise.all([
          listProductionProfiles(),
          listAvatarAssets(),
          listTemplates(),
          listPublishPlatforms(),
          listPublishAccounts(),
          listProductionBatches(),
          // 旧流水线只作兜底历史，不应一次把全部旧数据带进工作台。
          listPipelines({ limit: 10 }),
          getProductionWorkspaceConfiguration(),
        ]);
      setProfiles(profileData.items);
      setAssets(assetData);
      setTemplates(templateData.items);
      setPlatforms(platformData.platforms);
      setAccounts(accountData);
      setBatches(batchData.items);
      setLegacyPending(
        pipelineData.filter(
          (run) => run.status === "pending" && !String(run.config?.workflow || "").trim(),
        ),
      );
      setWorkspaceConfiguration(configuration);
      setSetupRightsHolder(configuration.rights_holder || "");
      setCopywritingCost(configuration.copywriting_estimated_cost_cny ?? null);
      setAvatarCost(configuration.avatar_estimated_cost_cny ?? null);
      if (configuration.default_publish_platforms?.length) {
        setPublishPlatforms(configuration.default_publish_platforms);
      }

      const validProfiles = profileData.items.filter(
        (profile) => isUsableProfile(profile, assetData, templateData.items),
      );
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
    } else if (nextAction === "review_script") {
      setReviewText(activeReview?.script.draft_text || activeReview?.script.approved_text || "");
    } else {
      setReviewText("");
    }
    setReviewNote("");
  }, [activeItem, activeReview, nextAction]);

  const crawlerRequest = useMemo<CrawlerSearchRequest>(() => ({
    keyword: keyword.trim(),
    published_window_days: 7,
    hotspot_window_hours: 168,
    count_per_platform: 3,
    force_refresh: false,
    mode: "smart",
    related_terms: [],
    allow_related_fallback: false,
    track_trend: false,
    target_main_count: 3,
    // 保持免费检索；服务端要求该字段至少为 1，即使不启用付费兜底。
    max_paid_calls: 1,
    allow_paid_fallback: false,
    hotspot_result_limit: 3,
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
      setSelectedCandidateId(found[0]?.video_id || "");
      if (found.length) {
        setActionMessage(`找到 ${found.length} 条相关素材，已帮你选好第 1 条。`);
      } else {
        setCrawlerReason({ kind: "暂时没找到合适素材", message: "换一个更具体的词再试试。" });
      }
    } catch (error) {
      setCrawlerReason({ kind: "检索没有完成", message: "请稍后再试，或换一个更具体的词。" });
    } finally {
      setBusy(false);
    }
  };

  const saveProfile = async () => {
    setActionError("");
    if (!profileName.trim() || !avatarId || !voiceId || !templateId) {
      setActionError("请补齐配方名称、数字人形象、音色和剪辑模板。");
      return;
    }
    setBusy(true);
    try {
      const created = await createProductionProfile({
        name: profileName.trim(),
        description: "智能创作工作台默认单条配方",
        target_audience: "",
        platform: "douyin",
        script_style: "",
        avatar_id: avatarId,
        voice_id: voiceId,
        edit_template_id: templateId,
        tags: [],
      });
      setProfiles((current) => [created, ...current]);
      setProfileId(created.profile_id);
      localStorage.setItem(PROFILE_STORAGE_KEY, created.profile_id);
      setSetupOpen(true);
      setActionMessage("出镜人已保存；再完成一次基础设置就能开始创作。");
    } catch (error) {
      setActionError((error as Error).message || "保存 IP 配方失败");
    } finally {
      setBusy(false);
    }
  };

  const saveWorkspaceSetup = async () => {
    if (!setupRightsHolder.trim() || !setupAgreementAccepted) {
      setActionError("请填写主体并勾选确认后继续。");
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
      setSetupAgreementAccepted(false);
      setActionMessage("基础设置已完成。现在只要输入关键词，其他交给我。 ");
    } catch (error) {
      setActionError((error as Error).message || "基础设置保存失败");
    } finally {
      setBusy(false);
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
    if (!publishPlatforms.length) return "请至少选择一个发布平台。";
    return "";
  };

  const validateSource = () => {
    if (!selectedProfile) return "请先补齐一个可用的 IP 配方。";
    const executionError = validateExecution();
    if (executionError) return executionError;
    if (sourceMode === "keyword" && !selectedCandidate) return "请先检索并选择一条严格相关候选。";
    if (sourceMode === "share_link" && !/^https?:\/\//i.test(sourceValue.trim())) return "请输入以 http:// 或 https:// 开头的视频分享链接。";
    if (["brief", "script"].includes(sourceMode) && !sourceValue.trim()) return "请输入选题或已有文案。";
    return "";
  };

  const executionParams = useMemo(() => ({
    rightsHolder: workspaceConfiguration.rights_holder || "",
    rightsConfirmed: workspaceConfiguration.configured,
    publishPlatforms: publishPlatforms.length ? publishPlatforms : ["douyin"],
    concurrency: 1,
    maxTotalCostCny: null,
    paidActionsConfirmed: Boolean(workspaceConfiguration.bundled_compute),
  }), [publishPlatforms, workspaceConfiguration]);

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
    setActionMessage("任务已启动，将自动推进到下一次人工确认。");
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
      const item =
        sourceMode === "keyword"
          ? {
              source_type: "candidate" as const,
              source_value: selectedCandidate!.video_id,
              display_title: selectedCandidate!.title,
            }
          : {
              source_type: sourceMode,
              source_value: sourceValue.trim(),
              display_title:
                sourceMode === "share_link" ? "分享链接"
                : sourceMode === "brief" ? sourceValue.trim().slice(0, 80)
                : "已有口播文案",
            };
      const payload = {
        name: `单条创作 · ${item.display_title || new Date().toLocaleString()}`.slice(0, 100),
        profile_id: profileId,
        items: [item],
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
    if (stage !== "output" && !reviewText.trim()) {
      setActionError(stage === "transcript" ? "转写确认必须提交非空最终文本。" : "文案确认必须提交非空最终口播稿。");
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
          approved_text: stage === "output" ? undefined : reviewText.trim(),
          note: reviewNote.trim(),
        }],
      });
      const failed = response.results.find((item) => !item.ok);
      if (failed) throw new Error(failed.error || "审核提交失败");
      setActionMessage(
        stage === "transcript" ? "原转写已确认，正在生成待审核改写稿。"
        : stage === "script" ? "最终口播稿已确认，后台将继续数字人口播与剪辑。"
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
          && account.status === "ready"
          && account.auto_publish_authorized,
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
          && account.status === "ready"
          && account.auto_publish_authorized,
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
        accountStatus: ready ? "已就绪并授权" : known ? `账号状态：${known.status}` : "未配置账号",
        mode: canAttemptReal ? "服务端复核后创建真实任务" : "生成手动发布包",
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
          target.mode === "real" ? "真实发布任务"
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
      Modal.confirm({
        title: "确认成片并发布？",
        content: "服务端会再次核验账号。已就绪且明确授权的账号创建真实任务；其余平台只生成手动发布包，不会伪报发布成功。",
        okText: "确认发布",
        cancelText: "取消",
        onOk: async () => {
          setBusy(true);
          setActionError("");
          try {
            const idempotencyKey = getOperationKey("publish", {
              batchId: data.batch.batch_id,
              runId: item.run_id,
              targets: publishTargets,
            });
            await confirmProductionBatchPublish(data.batch.batch_id, {
              runIds: [item.run_id],
              targets: publishTargets,
              idempotencyKey,
            });
            setActionMessage("发布任务或手动发布包已创建，请在发布中心查看真实状态。");
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

  const reviewOutputAndPublish = async () => {
    const reviewed = await submitReview("output");
    if (!reviewed || !workspace) return;
    const refreshed = await loadWorkspace(workspace.batch.batch_id);
    if (refreshed) await publishCurrent(refreshed);
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
        await reviewOutputAndPublish();
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
      if (!workspaceConfiguration.configured) return "完成基础设置";
      if (sourceMode === "keyword" && !candidates.length) {
        return "找素材";
      }
      return "创建单条任务并预检";
    }
    const labels: Record<string, string> = {
      preflight: "完成预检并启动",
      start: "启动智能创作",
      review_transcript: "确认转写并生成改写稿",
      review_script: "确认最终口播稿",
      review_output: "确认成片并发布",
      publish: "确认成片并发布",
      resume: "继续任务",
      retry: "安全重试",
      wait: "刷新实时状态",
      view_result: "查看成片",
      completed: "查看完成结果",
    };
    return labels[nextAction] || "刷新实时状态";
  }, [candidates.length, completeProfiles.length, nextAction, sourceMode, workspace, workspaceConfiguration.configured]);

  const showReviewEditor = ["review_transcript", "review_script"].includes(nextAction);
  const needsProfileConfiguration = !workspace && !completeProfiles.length;
  const activeVideo = resultMediaUrl(activeItem);
  const progressPercent =
    ["succeeded", "completed"].includes(workspace?.status || "") ? 100
    : workspace ? [10, 35, 60, 80, 95][stageIndex(currentStage)]
    : 0;

  if (initializing) {
    return <div className="workspace-loading"><Spin size="large" tip="正在载入智能创作工作台…" /></div>;
  }

  return (
    <div className="smart-workspace">
      <section className="workspace-hero">
        <div>
          <Title level={2}>智能创作工作台</Title>
        </div>
        <div className="hero-state" aria-live="polite">
          <Text type="secondary">当前阶段</Text>
          <strong>{STAGE_LABEL[currentStage] || currentStage}</strong>
          {polling && <Text type="secondary"><ClockCircleOutlined spin /> 正在同步</Text>}
        </div>
      </section>

      <Card className="stage-overview" bordered={false}>
        <Steps
          current={stageIndex(currentStage)}
          responsive
          items={WORKSPACE_STEPS.map((step, index) => ({
            ...step,
            status:
              index < stageIndex(currentStage) ? "finish"
              : index === stageIndex(currentStage) ? "process"
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
                    rows={sourceMode === "script" ? 8 : 5}
                    value={sourceValue}
                    placeholder={sourceMode === "brief" ? "输入一个选题或内容目标" : "粘贴已经确认的口播文案"}
                    onChange={(event) => setSourceValue(event.target.value)}
                  />
                )}

                {crawlerReason && (
                  <div className="search-empty">
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={crawlerReason.message} />
                    <Button onClick={() => void runKeywordSearch()} loading={busy}>换个词再找</Button>
                  </div>
                )}

                {candidates.length > 0 && (
                  <div>
                    <div className="section-heading">
                      <Text strong>找到这些素材</Text>
                      <Text type="secondary">已帮你选好第 1 条</Text>
                    </div>
                    <div className="candidate-stack">
                      {candidates.map((candidate, index) => (
                        <button
                          key={candidate.video_id}
                          type="button"
                          className={`candidate-card${selectedCandidateId === candidate.video_id ? " selected" : ""}`}
                          onClick={() => setSelectedCandidateId(candidate.video_id)}
                        >
                          <span className="candidate-rank">#{index + 1}</span>
                          <span className="candidate-copy">
                            <strong>{candidate.title || "未命名候选"}</strong>
                            <small>{candidate.platform_label} · {candidate.author_name || "作者未返回"}</small>
                            <small>{candidate.relevance_reason || "标题或话题严格命中"}</small>
                          </span>
                          {selectedCandidateId === candidate.video_id && <CheckCircleOutlined />}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </Space>
            )}

            {workspace && (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Alert
                  type={activeItem?.error_message || activeItem?.blocked_reasons?.length ? "warning" : "info"}
                  showIcon
                  message={STAGE_LABEL[currentStage] || currentStage}
                  description={
                    activeItem?.error_message
                    || activeItem?.blocked_reasons?.join("；")
                    || (nextAction === "wait" ? "系统正在处理，页面每 2.5 秒自动刷新。" : `下一步：${primaryLabel}`)
                  }
                />

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
                    <TextArea
                      aria-label={nextAction === "review_transcript" ? "最终转写文本" : "最终口播稿"}
                      rows={12}
                      value={reviewText}
                      onChange={(event) => setReviewText(event.target.value)}
                      placeholder={nextAction === "review_transcript" ? "核对并提交最终原转写" : "核对并提交最终口播稿"}
                    />
                    <Input
                      value={reviewNote}
                      onChange={(event) => setReviewNote(event.target.value)}
                      placeholder="复核备注（可选）"
                    />
                  </>
                )}

                {stageIndex(currentStage) === 4 && (
                  <div className="publish-review">
                    <Text strong>发布信息与账号状态</Text>
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

                <Progress percent={Math.max(0, Math.min(100, Math.round(progressPercent)))} status={workspace.status === "failed" ? "exception" : "active"} />
                <Space wrap>
                  {allowedActions.includes("pause") && (
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
                description="工作台需要一个已授权的数字人形象、音色和剪辑模板。保存后会显示完整摘要，不会只弹出提示。"
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
                <Select
                  value={templateId || undefined}
                  onChange={setTemplateId}
                  placeholder="选择剪辑模板"
                  options={templates.map((template) => ({ label: template.name, value: template.template_id }))}
                />
                <Space wrap>
                  {!assets.some((asset) => asset.kind === "avatar" && asset.authorized && asset.status === "ready") && (
                    <Link to="/avatar">去配置并授权数字人形象</Link>
                  )}
                  {!assets.some((asset) => asset.kind === "voice" && asset.authorized && asset.status === "ready") && (
                    <Link to="/avatar">去配置并授权音色</Link>
                  )}
                  {!templates.length && <Link to="/video-editor">去创建剪辑模板</Link>}
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

          <Card title={<Space><SafetyCertificateOutlined /> 当前 IP 配方</Space>}>
            {activeProfile ? (
              <div className="profile-summary">
                <div className="profile-avatar-preview">
                  {profileAvatar?.preview_url ? (
                    profileAvatar.preview_type === "video" ? (
                      <video src={profileAvatar.preview_url} muted playsInline preload="metadata" />
                    ) : <img src={profileAvatar.preview_url} alt={`${profileAvatar.name} 形象预览`} />
                  ) : <SafetyCertificateOutlined />}
                </div>
                <Descriptions size="small" column={1}>
                  <Descriptions.Item label="出镜人">{profileAvatar?.name || activeProfile.name}</Descriptions.Item>
                  <Descriptions.Item label="音色">{profileVoice?.name || activeProfile.voice_id}</Descriptions.Item>
                  <Descriptions.Item label="剪辑">{profileTemplate?.name || activeProfile.edit_template_id}</Descriptions.Item>
                  <Descriptions.Item label="发布到">{publishPlatforms.map((item) => PLATFORM_LABELS[item] || item).join("、")}</Descriptions.Item>
                </Descriptions>
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无完整 IP 配方" />
            )}
            {completeProfiles.length > 1 && !workspace && (
              <Button block onClick={() => setProfilePickerOpen(true)}>更换出镜人</Button>
            )}
          </Card>
        </div>

        <div className="workspace-right">
          <Card
            title={<Space><VideoCameraOutlined /> 预览与实时状态</Space>}
            extra={workspace && <Text code>{workspace.batch.batch_id}</Text>}
          >
            {activeVideo ? (
              <div className="video-frame">
                <video controls preload="metadata" src={activeVideo} />
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
                        { color: stageIndex(currentStage) > 1 ? "green" : stageIndex(currentStage) === 1 ? "blue" : "gray", children: "转写与最终文案人工确认" },
                        { color: stageIndex(currentStage) > 2 ? "green" : stageIndex(currentStage) === 2 ? "blue" : "gray", children: "数字人口播" },
                        { color: stageIndex(currentStage) > 3 ? "green" : stageIndex(currentStage) === 3 ? "blue" : "gray", children: "剪辑成片" },
                        { color: stageIndex(currentStage) === 4 ? "blue" : "gray", children: "成片复核与真实/手动发布" },
                      ])}
                />
              </>
            )}
          </Card>

          <Card title="工作台任务">
            {batches.length ? (
              <List
                size="small"
                dataSource={batches.slice(0, 8)}
                renderItem={(batch) => (
                  <List.Item
                    className={batch.batch_id === selectedBatchId ? "active-batch" : ""}
                    actions={[
                      <Button key="open" type="link" onClick={() => {
                        const run = batch.items[0]?.run_id || "";
                        setSelectedRunId(run);
                        navigate(`/pipeline?batch=${encodeURIComponent(batch.batch_id)}&run=${encodeURIComponent(run)}`);
                        void loadWorkspace(batch.batch_id);
                      }}>打开</Button>,
                    ]}
                  >
                    <List.Item.Meta
                      title={batch.name}
                      description={`${batch.profile_name} · ${new Date(batch.created_at).toLocaleString()}`}
                    />
                    <Tag color={statusColor(batch.status)}>{STATUS_LABEL[batch.status] || batch.status}</Tag>
                  </List.Item>
                )}
              />
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无工作台任务" />
            )}
          </Card>

          {legacyPending.length > 0 && (
            <Collapse
              items={[{
                key: "legacy",
                label: "旧记录",
                children: (
                  <>
                    <Alert type="info" showIcon message="仅显示最近 10 条旧记录；它们不计入当前任务，数据已保留。" />
                    <List
                      size="small"
                      dataSource={legacyPending.slice(0, 10)}
                      renderItem={(run) => (
                        <List.Item>
                          <List.Item.Meta title={run.keyword || "旧流水线记录"} description={run.run_id} />
                          <Tag>旧记录</Tag>
                        </List.Item>
                      )}
                    />
                  </>
                ),
              }]}
            />
          )}
        </div>
      </div>

      <Modal
        title="第一次用，简单设置一下"
        open={setupOpen}
        onCancel={() => setSetupOpen(false)}
        footer={null}
        destroyOnClose
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">填一次就行，以后直接找素材、做视频。</Text>
          <Input
            value={setupRightsHolder}
            onChange={(event) => setSetupRightsHolder(event.target.value)}
            placeholder="公司名称或本人姓名"
            autoFocus
          />
          {selectedProfile && (
            <div className="setup-profile-line">
              <span>本次出镜人</span>
              <strong>{profileAvatar?.name || selectedProfile.name}</strong>
              {completeProfiles.length > 1 && (
                <Button type="link" onClick={() => setProfilePickerOpen(true)}>换一个</Button>
              )}
            </div>
          )}
          <Checkbox checked={setupAgreementAccepted} onChange={(event) => setSetupAgreementAccepted(event.target.checked)}>
            我确认拥有本次创作所需的媒体、文案、肖像与声音处理权
          </Checkbox>
          <Button type="primary" size="large" block loading={busy} onClick={() => void saveWorkspaceSetup()}>
            开始创作
          </Button>
        </Space>
      </Modal>

      <Modal
        title="填写实际费用"
        open={costSetupOpen}
        onCancel={() => setCostSetupOpen(false)}
        footer={null}
        destroyOnClose
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
        title="选择出镜人"
        open={profilePickerOpen}
        onCancel={() => setProfilePickerOpen(false)}
        footer={null}
      >
        <div className="profile-picker-grid">
          {completeProfiles.map((profile) => {
            const avatar = assets.find((asset) => asset.asset_id === profile.avatar_id);
            return (
              <button
                type="button"
                key={profile.profile_id}
                className={`profile-picker-card${profile.profile_id === profileId ? " selected" : ""}`}
                onClick={() => {
                  setProfileId(profile.profile_id);
                  localStorage.setItem(PROFILE_STORAGE_KEY, profile.profile_id);
                  setProfilePickerOpen(false);
                }}
              >
                <span className="profile-picker-media">
                  {avatar?.preview_url ? (
                    avatar.preview_type === "video" ? <video src={avatar.preview_url} muted playsInline preload="metadata" />
                    : <img src={avatar.preview_url} alt={`${avatar.name} 形象预览`} />
                  ) : <SafetyCertificateOutlined />}
                </span>
                <span><strong>{avatar?.name || profile.name}</strong><small>{profile.name}</small></span>
              </button>
            );
          })}
        </div>
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
        .workspace-loading {
          min-height: 55vh;
          display: grid;
          place-items: center;
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
          grid-template-columns: minmax(0, .9fr) minmax(360px, 1.1fr);
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
          grid-template-columns: auto minmax(0, 1fr) auto;
          align-items: center;
          gap: 12px;
          border: 1px solid #e6e8ef;
          border-radius: 12px;
          padding: 12px;
          background: #fff;
          color: inherit;
          text-align: left;
          cursor: pointer;
        }
        .candidate-card:hover,
        .candidate-card:focus-visible { border-color: #8a65ed; outline: none; box-shadow: 0 0 0 3px rgba(111,73,232,.12); }
        .candidate-card.selected { border-color: #7652e8; background: #f7f3ff; }
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
        .candidate-copy small { color: #777e8d; }
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
          display: grid;
          grid-template-columns: 104px minmax(0, 1fr);
          gap: 14px;
          align-items: center;
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
          width: 104px;
          height: 104px;
          border-radius: 14px;
        }
        .profile-avatar-preview img,
        .profile-avatar-preview video,
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
        }
      `}</style>
    </div>
  );
}
