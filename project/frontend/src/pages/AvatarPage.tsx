/**
 * 数字人口播生成页面。
 * 首版只开放公共形象 + 文本驱动，所有任务状态均来自 FastAPI。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Progress,
  Radio,
  Row,
  Space,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  AudioOutlined,
  CameraOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  DownOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  PlayCircleOutlined,
  RightOutlined,
  RocketOutlined,
  StopOutlined,
  UploadOutlined,
  UserOutlined,
} from "@ant-design/icons";
import {
  createAvatarJob,
  createProductShowcaseJob,
  deleteTask,
  downloadAvatarJobMedia,
  getAvatarBillingQuote,
  getAvatarCapabilities,
  getAvatarJob,
  getVideoEditorJob,
  listAvatarAssets,
  listAvatarJobs,
  retryAvatarVideoSubmission,
  trainCloudAvatar,
  trainCloudVoice,
  uploadAvatarAsset,
  uploadVideoEditorVisualAsset,
} from "../api/client";
import type {
  AvatarAsset,
  AvatarCapability,
  AvatarJob,
  AvatarProfile,
  VideoEditorJob,
  VideoEditorVisualAsset,
} from "../api/types";
import { useToast } from "../components/Toast";
import { handleCreditsError } from "../utils/credits";
import { useNavigate, useSearchParams } from "react-router-dom";

const { Title, Text } = Typography;
const { TextArea } = Input;

// 训练收费（积分）——与后端默认一致；如在 .env 调整价格需同步更新此处
const AVATAR_FACE_TRAINING_CREDITS = 100;
const AVATAR_VOICE_TRAINING_CREDITS = 60;

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
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const sourceTaskId = searchParams.get("sourceTask")?.trim() || null;
  const sourceRevisionId = searchParams.get("sourceRevision")?.trim() || null;
  const keywordFromQuery = searchParams.get("keyword")?.trim() || "";
  const scriptFromQuery = searchParams.get("script")?.trim() || "";
  const [capability, setCapability] = useState<AvatarCapability | null>(null);
  const [assets, setAssets] = useState<AvatarAsset[]>([]);
  const [jobs, setJobs] = useState<AvatarJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [quoting, setQuoting] = useState(false);
  const [uploadingAvatar, setUploadingAvatar] = useState(false);
  const [uploadingVoice, setUploadingVoice] = useState(false);
  const [recording, setRecording] = useState(false);
  const [avatarLibraryOpen, setAvatarLibraryOpen] = useState(false);
  const [voiceLibraryOpen, setVoiceLibraryOpen] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null);
  const [cameraError, setCameraError] = useState("");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [playbackJob, setPlaybackJob] = useState<AvatarJob | null>(null);
  const [playbackUrl, setPlaybackUrl] = useState<string | null>(null);
  const [playbackLoading, setPlaybackLoading] = useState(false);
  const [productShowcaseOpen, setProductShowcaseOpen] = useState(false);
  const [productShowcaseSource, setProductShowcaseSource] = useState<AvatarJob | null>(null);
  const [productAsset, setProductAsset] = useState<VideoEditorVisualAsset | null>(null);
  const [backgroundAsset, setBackgroundAsset] = useState<VideoEditorVisualAsset | null>(null);
  const [productLayout, setProductLayout] = useState<"avatar_left_product_right" | "product_canvas_avatar_pip">("avatar_left_product_right");
  const [visualUploading, setVisualUploading] = useState<"product" | "background" | null>(null);
  const [productShowcaseSubmitting, setProductShowcaseSubmitting] = useState(false);
  const [productShowcaseJob, setProductShowcaseJob] = useState<VideoEditorJob | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const currentTaskRef = useRef<HTMLDivElement>(null);
  const cameraVideoRef = useRef<HTMLVideoElement>(null);
  const cameraStreamRef = useRef<MediaStream | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordingStreamRef = useRef<MediaStream | null>(null);

  const [scriptText, setScriptText] = useState("");
  const [videoName, setVideoName] = useState("");
  const [avatarId, setAvatarId] = useState<string>();
  const [voiceId, setVoiceId] = useState<string>();
  const [profileId, setProfileId] = useState("default");
  const [speechRate, setSpeechRate] = useState(1);

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
  const recentJobs = useMemo(
    () => jobs.filter((item) => item.task_id !== activeJob?.task_id).slice(0, 5),
    [activeJob?.task_id, jobs],
  );
  const selectedAvatar = useMemo(
    () => avatars.find((item) => item.asset_id === avatarId) || null,
    [avatarId, avatars],
  );
  const selectedVoice = useMemo(
    () => voices.find((item) => item.asset_id === voiceId) || null,
    [voiceId, voices],
  );

  const profiles = useMemo<AvatarProfile[]>(
    () => capability?.profiles?.length
      ? capability.profiles
      : [{
          profile_id: "default",
          display_name: capability?.display_name || "默认方案",
          description: "由当前数字人供应商生成",
          enabled: Boolean(capability?.enabled),
          estimated_cost_cny: capability?.estimated_cost_cny ?? null,
          estimated_seconds: capability?.estimated_seconds ?? null,
          required_vram_gb: null,
          missing_configuration: [],
        }],
    [capability],
  );

  const selectedProfile = useMemo(
    () => profiles.find((item) => item.profile_id === profileId) || profiles[0],
    [profileId, profiles],
  );
  const serviceUnavailable = Boolean(capability && !capability.enabled);
  const selectedProfileUnavailable = Boolean(selectedProfile && !selectedProfile.enabled);
  const supportsLocalUpload = capability?.provider_name === "local_avatar";
  const supportsCloudAvatarTraining = Boolean(capability?.supports_cloud_avatar_training);
  const supportsVoiceCloning = Boolean(capability?.supports_voice_cloning);
  const supportsVoiceSampleUpload = Boolean(capability?.supports_voice_sample_upload);
  const canAddAvatarMaterial = supportsLocalUpload || supportsCloudAvatarTraining;
  const canAddVoiceMaterial = supportsLocalUpload || supportsVoiceSampleUpload;
  const selectedRecordedProfile = selectedProfile?.profile_id === "local_recorded_natural";

  const isReadyAsset = (asset: AvatarAsset) => asset.authorized && asset.status === "ready";
  const selectedAvatarReady = Boolean(selectedAvatar && isReadyAsset(selectedAvatar));
  const selectedVoiceReady = Boolean(selectedVoice && isReadyAsset(selectedVoice));

  const capabilityDescription = useMemo(() => {
    if (!capability) return "";
    if (serviceUnavailable) {
      if (capability.provider_name === "shuying_legacy_cloud") {
        const missingLabels = capability.missing_configuration
          .map((item) => {
            if (item.startsWith("SHUYING_AVATAR_BASE_URL")) return "公司旧网关 HTTPS 地址";
            if (item === "SHUYING_AVATAR_AVATARS_JSON") return "当前有效数字人 ID";
            if (item === "SHUYING_AVATAR_VOICES_JSON") return "当前有效音色 ID";
            if (item === "SHUYING_AVATAR_RESULT_ALLOWED_HOSTS") return "成片域名白名单";
            if (item.startsWith("SHUYING_AVATAR_AUDIO_UPLOAD_URL")) return "公司音频上传地址";
            if (item === "SHUYING_AVATAR_AUDIO_ALLOWED_HOSTS") return "音频域名白名单";
            if (item === "SHUYING_AVATAR_API_CODE") return "公司接口 Key";
            return item;
          });
        return `已切换到公司单 Key 云数字人，不再使用客户本地显卡。仍需补齐：${missingLabels.join("、")}。配置完成前不会提交计费任务。`;
      }
      return capability.missing_configuration.length
        ? "缺少供应商配置，请管理员在服务端完成配置后再开放真实生成。"
        : "服务端当前未开放数字人生成能力。";
    }
    if (capability.mode === "sandbox") {
      return "演示模式只用于验证任务提交、状态轮询和页面流程，不会产生真实成片或真实费用。";
    }
    return "";
  }, [capability, serviceUnavailable]);

  const submitButtonText = serviceUnavailable
    ? "数字人服务尚未配置"
    : selectedProfileUnavailable
      ? "生成服务尚未就绪"
      : capability?.mode === "sandbox"
        ? "创建演示任务（不生成真实成片）"
        : "生成数字人视频";

  const refresh = useCallback(async () => {
    const [nextCapability, nextAssets] = await Promise.all([
      getAvatarCapabilities(),
      listAvatarAssets(),
    ]);
    const nextJobs = await listAvatarJobs({
      includeSandbox: nextCapability.mode === "sandbox",
    });
    setCapability(nextCapability);
    setAssets(nextAssets);
    setJobs(nextJobs);
    setAvatarId((current) => current || nextAssets.find((item) => item.kind === "avatar")?.asset_id);
    setVoiceId((current) => current || nextAssets.find((item) => item.kind === "voice")?.asset_id);
    setProfileId((current) => {
      const enabled = nextCapability.profiles?.find((item) => item.enabled)?.profile_id;
      return nextCapability.profiles?.some((item) => item.profile_id === current) ? current : enabled || "default";
    });
    setActiveJobId((current) => current || nextJobs[0]?.task_id || null);
  }, []);

  const handleDeleteJob = async (taskId: string) => {
    try {
      await deleteTask(taskId);
      setActiveJobId((current) => current === taskId ? null : current);
      toast.success("数字人任务历史已删除");
      await refresh();
    } catch (error) {
      toast.error((error as Error).message || "删除任务失败");
    }
  };

  const handleViewJob = useCallback(async (taskId: string) => {
    setActiveJobId(taskId);
    window.requestAnimationFrame(() => {
      currentTaskRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    try {
      const latest = await getAvatarJob(taskId);
      setJobs((items) =>
        items.map((item) => (item.task_id === latest.task_id ? latest : item)),
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "读取任务详情失败");
    }
  }, [toast]);

  useEffect(() => {
    refresh()
      .catch((error) => toast.error(error.message || "读取数字人服务失败"))
      .finally(() => setLoading(false));
  }, [refresh, toast]);

  useEffect(() => {
    if (scriptFromQuery) {
      setScriptText(scriptFromQuery);
    }
  }, [scriptFromQuery]);

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

  useEffect(() => {
    if (!cameraOpen || !cameraStream || !cameraVideoRef.current) return;
    cameraVideoRef.current.srcObject = cameraStream;
    void cameraVideoRef.current.play().catch(() => undefined);
  }, [cameraOpen, cameraStream]);

  useEffect(() => () => {
    cameraStreamRef.current?.getTracks().forEach((track) => track.stop());
    recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  useEffect(() => () => {
    if (playbackUrl) URL.revokeObjectURL(playbackUrl);
  }, [playbackUrl]);

  const submitAvatarJob = useCallback(async () => {
    if (!capability?.enabled) {
      toast.warning("数字人服务尚未可用，请先配置供应商。");
      return;
    }
    if (!avatarId || !voiceId) {
      toast.warning("缺少可用公共形象或音色。");
      return;
    }
    if (!selectedAvatarReady || !selectedVoiceReady) {
      toast.warning("所选形象或音色尚未训练完成或未授权。");
      return;
    }
    if (selectedProfile && !selectedProfile.enabled) {
      toast.warning("当前生成服务尚未就绪。");
      return;
    }
    if (!scriptText.trim()) {
      toast.warning("请输入口播文案。");
      return;
    }
    setSubmitting(true);
    try {
      const job = await createAvatarJob({
        source_task_id: sourceTaskId,
        source_revision_id: sourceRevisionId,
        video_name: videoName.trim() || undefined,
        keyword: keywordFromQuery || undefined,
        script_text: scriptText.trim(),
        avatar_id: avatarId,
        voice_id: voiceId,
        profile_id: selectedProfile?.profile_id,
        speech_rate: speechRate,
        aspect_ratio: "9:16",
        resolution: "1080x1920",
        idempotency_key: buildIdempotencyKey(),
      });
      setJobs((items) => [job, ...items.filter((item) => item.task_id !== job.task_id)]);
      setActiveJobId(job.task_id);
      if (job.status === "failed") {
        toast.error(job.error_message || "数字人任务提交失败，文案已保留。");
      } else {
        toast.success(job.is_mock ? "演示任务已创建" : "数字人任务已提交");
      }
    } catch (error) {
      if (!handleCreditsError(error, () => navigate("/admin"))) {
        toast.error(error instanceof Error ? error.message : "提交数字人任务失败");
      }
    } finally {
      setSubmitting(false);
    }
  }, [
    avatarId,
    capability,
    scriptText,
    speechRate,
    sourceRevisionId,
    sourceTaskId,
    toast,
    videoName,
    voiceId,
    keywordFromQuery,
    selectedProfile,
    selectedAvatar,
    selectedAvatarReady,
    selectedVoice,
    selectedVoiceReady,
  ]);

  const handleSubmit = useCallback(async () => {
    if (!capability?.enabled) {
      toast.warning("数字人服务尚未可用，请先配置供应商。");
      return;
    }
    if (!scriptText.trim()) {
      toast.warning("请输入口播文案。");
      return;
    }
    if (!avatarId || !voiceId || !selectedAvatarReady || !selectedVoiceReady) {
      toast.warning("请先选择可用的形象和声音。");
      return;
    }
    if (capability.mode === "sandbox") {
      Modal.confirm({
        title: "确认创建演示任务？",
        content: "演示任务不会产生真实成片或真实费用。",
        okText: "创建演示任务",
        cancelText: "暂不生成",
        onOk: submitAvatarJob,
      });
      return;
    }
    setQuoting(true);
    try {
      const quote = await getAvatarBillingQuote({
        scriptText: scriptText.trim(),
        speechRate,
      });
      Modal.confirm({
        title: "确认费用预留",
        content: (
          <Space direction="vertical" size={6}>
            <Text>
              本次先冻结最多 {quote.reservation_credits.toFixed(2)} 积分
              （按 {quote.reservation_seconds} 秒保守上限）。
            </Text>
            <Text type="secondary">
              成片后按实际时长向上取整到整秒结算，多余积分自动退回；不会重复结算。
            </Text>
          </Space>
        ),
        okText: "确认费用并开始生成",
        cancelText: "暂不生成",
        onOk: submitAvatarJob,
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "暂时无法预估数字人费用");
    } finally {
      setQuoting(false);
    }
  }, [
    avatarId,
    capability,
    scriptText,
    selectedAvatarReady,
    selectedVoiceReady,
    speechRate,
    submitAvatarJob,
    toast,
    voiceId,
  ]);

  const handleRetryVideo = useCallback((job: AvatarJob) => {
    Modal.confirm({
      title: "确认只重试视频提交？",
      content: (
        <Space direction="vertical" size={6}>
          <Text>系统会复用已经生成的克隆声音，不会重新克隆声音。</Text>
          <Text type="warning">
            供应商费用暂无法确定，本次视频重试可能产生第三方费用。
          </Text>
        </Space>
      ),
      okText: "确认重试一次",
      cancelText: "暂不重试",
      onOk: async () => {
        try {
          const latest = await retryAvatarVideoSubmission(job.task_id);
          setJobs((items) =>
            items.map((item) => (item.task_id === latest.task_id ? latest : item)),
          );
          setActiveJobId(latest.task_id);
          if (latest.status === "failed" || latest.status === "outcome_unknown") {
            toast.error(latest.error_message || "视频提交重试未成功。");
          } else {
            toast.success("已复用克隆声音，视频提交正在继续。");
          }
        } catch (error) {
          toast.error(error instanceof Error ? error.message : "视频提交重试失败");
        }
      },
    });
  }, [toast]);

  const handleDownload = useCallback(async (job: AvatarJob) => {
    try {
      const blob = await downloadAvatarJobMedia(job.task_id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${job.video_name || job.title}.mp4`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "下载失败");
    }
  }, [toast]);

  const handlePlayJob = useCallback(async (job: AvatarJob) => {
    if (!job.result_url) return;
    setPlaybackJob(job);
    setPlaybackLoading(true);
    try {
      const blob = await downloadAvatarJobMedia(job.task_id);
      setPlaybackUrl((current) => {
        if (current) URL.revokeObjectURL(current);
        return URL.createObjectURL(blob);
      });
    } catch (error) {
      setPlaybackJob(null);
      toast.error(error instanceof Error ? error.message : "读取成片失败");
    } finally {
      setPlaybackLoading(false);
    }
  }, [toast]);

  const closePlayback = useCallback(() => {
    setPlaybackJob(null);
    setPlaybackUrl(null);
  }, []);

  const openProductShowcase = useCallback((job: AvatarJob) => {
    if (job.status !== "succeeded" || job.is_mock || !job.result_url) {
      toast.warning("只有已完成的真实数字人成片可以制作产品讲解视频。");
      return;
    }
    setProductShowcaseSource(job);
    setProductAsset(null);
    setBackgroundAsset(null);
    setProductLayout("avatar_left_product_right");
    setProductShowcaseJob(null);
    setProductShowcaseOpen(true);
  }, [toast]);

  const handleVisualAssetUpload = useCallback(async (
    kind: "product" | "background",
    file: File,
  ) => {
    setVisualUploading(kind);
    try {
      const asset = await uploadVideoEditorVisualAsset({
        kind,
        file,
        rightsHolder: "本人/公司已授权",
      });
      if (kind === "product") setProductAsset(asset);
      else setBackgroundAsset(asset);
      toast.success(kind === "product" ? "商品主图已就绪" : "背景图已就绪");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "图片上传失败");
    } finally {
      setVisualUploading(null);
    }
    return false;
  }, [toast]);

  const handleCreateProductShowcase = useCallback(async () => {
    if (!productShowcaseSource || !productAsset) {
      toast.warning("请先上传商品主图。");
      return;
    }
    setProductShowcaseSubmitting(true);
    try {
      const job = await createProductShowcaseJob({
        sourceId: `avatar:${productShowcaseSource.task_id}`,
        productAssetId: productAsset.asset_id,
        backgroundAssetId: backgroundAsset?.asset_id,
        layout: productLayout,
      });
      setProductShowcaseJob(job);
      toast.success("产品讲解合成已开始，不会重新提交数字人任务。");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建产品讲解任务失败");
    } finally {
      setProductShowcaseSubmitting(false);
    }
  }, [backgroundAsset, productAsset, productLayout, productShowcaseSource, toast]);

  useEffect(() => {
    if (!productShowcaseJob || !["queued", "running"].includes(productShowcaseJob.status)) return undefined;
    const timer = window.setTimeout(() => {
      void getVideoEditorJob(productShowcaseJob.task_id)
        .then(setProductShowcaseJob)
        .catch((error) => toast.error(error instanceof Error ? error.message : "刷新产品讲解任务失败"));
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [productShowcaseJob, toast]);

  const handleAssetUpload = useCallback(async (kind: "avatar" | "voice", file: File) => {
    if (!supportsLocalUpload) {
      toast.warning("当前供应商不支持在页面上传本地素材。");
      return false;
    }
    const setUploading = kind === "avatar" ? setUploadingAvatar : setUploadingVoice;
    setUploading(true);
    try {
      const asset = await uploadAvatarAsset({ kind, file });
      await refresh();
      if (kind === "avatar") {
        setAvatarId(asset.asset_id);
      } else {
        setVoiceId(asset.asset_id);
        setProfileId("local_recorded_natural");
      }
      toast.success(kind === "avatar" ? "本人形象已录入" : "本人完整口播录音已录入");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "上传素材失败");
    } finally {
      setUploading(false);
    }
    return false;
  }, [refresh, supportsLocalUpload, toast]);

  const handleCloudAvatarTraining = useCallback(async (file: File) => {
    if (!supportsCloudAvatarTraining) {
      toast.warning("公司云形象训练线路尚未配置。");
      return false;
    }
    const confirmed = await new Promise<boolean>((resolve) => {
      Modal.confirm({
        title: "确认提交云形象（脸部）训练？",
        content: (
          <Text type="warning">
            云形象训练需扣除 {AVATAR_FACE_TRAINING_CREDITS} 积分（约
            {AVATAR_FACE_TRAINING_CREDITS} 元）。训练提交后即使失败也不退款，请确认素材无误。
          </Text>
        ),
        okText: "确认并训练",
        cancelText: "取消",
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      });
    });
    if (!confirmed) return false;
    setUploadingAvatar(true);
    try {
      const asset = await trainCloudAvatar({
        file,
        name: file.name.replace(/\.[^.]+$/, "") || "新建云形象",
      });
      await refresh();
      setAvatarId(asset.asset_id);
      toast.success("云形象训练已提交，可在素材列表查看状态。");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "提交云形象训练失败");
    } finally {
      setUploadingAvatar(false);
    }
    return false;
  }, [refresh, supportsCloudAvatarTraining, toast]);

  const handleCloudVoiceTraining = useCallback(async (file: File) => {
    if (!supportsVoiceCloning) {
      toast.warning("公司云声音训练线路尚未就绪，暂不能提交训练。");
      return false;
    }
    const confirmed = await new Promise<boolean>((resolve) => {
      Modal.confirm({
        title: "确认提交声音训练？",
        content: (
          <Text type="warning">
            声音训练需扣除 {AVATAR_VOICE_TRAINING_CREDITS} 积分（约
            {AVATAR_VOICE_TRAINING_CREDITS} 元）。训练提交后即使失败也不退款，请确认样本无误。
          </Text>
        ),
        okText: "确认并训练",
        cancelText: "取消",
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      });
    });
    if (!confirmed) return false;
    setUploadingVoice(true);
    try {
      const asset = await trainCloudVoice({
        file,
        name: file.name.replace(/\.[^.]+$/, "") || "新建克隆声音",
      });
      await refresh();
      setVoiceId(asset.asset_id);
      toast.success("声音克隆训练已提交，可在素材列表查看状态。");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "提交声音克隆失败");
    } finally {
      setUploadingVoice(false);
    }
    return false;
  }, [refresh, supportsVoiceCloning, toast]);

  const closeCamera = useCallback(() => {
    setCameraOpen(false);
    setCameraError("");
    cameraStreamRef.current?.getTracks().forEach((track) => track.stop());
    cameraStreamRef.current = null;
    setCameraStream(null);
  }, []);

  const handleOpenCamera = useCallback(async () => {
    if (!supportsLocalUpload) {
      toast.warning("当前供应商不支持录入本地形象。");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      toast.error("当前浏览器不支持直接调用摄像头，请改用上传照片。");
      return;
    }
    setCameraError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" } });
      cameraStreamRef.current = stream;
      setCameraStream(stream);
      setCameraOpen(true);
    } catch (error) {
      setCameraError(error instanceof Error ? error.message : "无法打开摄像头，请检查权限后重试。");
      setCameraOpen(true);
    }
  }, [supportsLocalUpload, toast]);

  const handleTakePhoto = useCallback(() => {
    const video = cameraVideoRef.current;
    if (!video?.videoWidth || !video.videoHeight) {
      toast.warning("摄像头正在启动，请稍候再拍照。");
      return;
    }
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext("2d");
    if (!context) {
      toast.error("当前浏览器无法处理拍摄的照片。");
      return;
    }
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (!blob) {
        toast.error("拍照失败，请重试。");
        return;
      }
      closeCamera();
      void handleAssetUpload("avatar", new File([blob], `本人形象-${Date.now()}.jpg`, { type: "image/jpeg" }));
    }, "image/jpeg", 0.92);
  }, [closeCamera, handleAssetUpload, toast]);

  const handleRecordVoice = useCallback(async () => {
    if (!supportsLocalUpload) {
      toast.warning("当前供应商不支持录入本地录音。");
      return;
    }
    if (recording && mediaRecorderRef.current) {
      mediaRecorderRef.current.stop();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      toast.error("当前浏览器不支持直接录音，请改用上传完整口播录音。");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: BlobPart[] = [];
      const recorder = new MediaRecorder(stream);
      recordingStreamRef.current = stream;
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        recordingStreamRef.current = null;
        mediaRecorderRef.current = null;
        setRecording(false);
        const type = recorder.mimeType || "audio/webm";
        const audio = new Blob(chunks, { type });
        if (!audio.size) {
          toast.error("没有录到声音，请检查麦克风权限后重试。");
          return;
        }
        const extension = type.includes("mp4") ? "m4a" : "webm";
        void handleAssetUpload("voice", new File([audio], `完整口播录音-${Date.now()}.${extension}`, { type }));
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch (error) {
      recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
      recordingStreamRef.current = null;
      toast.error(error instanceof Error ? error.message : "无法打开麦克风，请检查权限后重试。");
    }
  }, [handleAssetUpload, recording, supportsLocalUpload, toast]);

  const handleCloseVoiceLibrary = useCallback(() => {
    if (recording) void handleRecordVoice();
    setVoiceLibraryOpen(false);
  }, [handleRecordVoice, recording]);

  return (
    <div className="avatar-studio-page">
      {capability && (serviceUnavailable || capability.mode === "sandbox") && (
        <Alert
          className="avatar-capability-alert"
          type={serviceUnavailable ? "warning" : "info"}
          showIcon
          message={
            serviceUnavailable
              ? "数字人供应商未配置，暂不能提交真实任务"
              : "当前为演示模式：可验证任务闭环，但不会生成真实成片"
          }
          description={capabilityDescription}
        />
      )}

      <Row gutter={[28, 24]} align="stretch" className="avatar-studio-layout">
        <Col xs={24} lg={16} className="avatar-composer-column">
          <section className="avatar-composer" aria-label="生成配置">
            <div className="avatar-composer-heading">
              <Title level={2}>生成配置</Title>
              <Text type="secondary">选好形象和声音，再输入口播文案。</Text>
            </div>

            <div className="avatar-form-stack">
              <Row gutter={[16, 16]}>
                <Col xs={24} sm={12}>
                  <Text strong className="avatar-field-label">形象</Text>
                  <Button
                    block
                    onClick={() => setAvatarLibraryOpen(true)}
                    disabled={!avatars.length && !canAddAvatarMaterial}
                    data-testid="avatar-library-trigger"
                    className="avatar-choice-button"
                  >
                    <span className="avatar-choice-content">
                      <UserOutlined />
                      <Text strong ellipsis>{selectedAvatar?.name || "未选择"}</Text>
                      {selectedAvatar && !isReadyAsset(selectedAvatar) && (
                        <Tag color={selectedAvatar.status === "training" ? "processing" : "error"}>
                          {selectedAvatar.status === "training" ? "训练中" : "未就绪"}
                        </Tag>
                      )}
                      <DownOutlined />
                    </span>
                  </Button>
                </Col>
                <Col xs={24} sm={12}>
                  <Text strong className="avatar-field-label">声音</Text>
                  <Button
                    block
                    onClick={() => setVoiceLibraryOpen(true)}
                    disabled={!voices.length && !canAddVoiceMaterial}
                    data-testid="voice-library-trigger"
                    className="avatar-choice-button"
                  >
                    <span className="avatar-choice-content">
                      <AudioOutlined />
                      <Text strong ellipsis>{selectedVoice?.name || "未选择"}</Text>
                      {selectedVoice && !isReadyAsset(selectedVoice) && (
                        <Tag color={selectedVoice.status === "training" ? "processing" : "warning"}>
                          {selectedVoice.status === "training" ? "训练中" : "未就绪"}
                        </Tag>
                      )}
                      <DownOutlined />
                    </span>
                  </Button>
                </Col>
              </Row>

              <div>
                <Text strong className="avatar-field-label">视频名称（选填）</Text>
                <Input
                  value={videoName}
                  onChange={(event) => setVideoName(event.target.value)}
                  maxLength={50}
                  showCount
                  placeholder="给视频取个名字，方便管理"
                  className="avatar-name-input"
                />
              </div>

              <div>
                <Text strong className="avatar-field-label">口播文案</Text>
                <TextArea
                  rows={6}
                  value={scriptText}
                  onChange={(event) => setScriptText(event.target.value)}
                  maxLength={capability?.max_script_chars || 240}
                  showCount
                  placeholder="输入数字人要说的内容，数字人会按文案自然播完。"
                  className="avatar-script-input"
                />
                {sourceTaskId && (
                  <Text type="secondary" className="avatar-inline-note">
                    已带入人工确认后的口播稿，来源任务：{sourceTaskId}
                  </Text>
                )}
                {selectedRecordedProfile && (
                  <Alert
                    className="avatar-inline-note"
                    type="info"
                    showIcon
                    message="录音驱动模式"
                    description="文案只用于任务记录，最终口播内容以你上传的完整录音为准。"
                  />
                )}
              </div>

              <div className="avatar-cost-preview">
                <Text type="secondary">计费方式</Text>
                <Text strong>
                  {capability?.mode === "sandbox"
                    ? "0 积分（演示）"
                    : "按实际成片时长整秒结算"}
                </Text>
                <Text type="secondary">提交前显示冻结上限，完成后多余自动退回</Text>
              </div>

              <div className="avatar-output-settings">
                  <div className="avatar-fixed-output">
                    <Text type="secondary">固定输出</Text>
                    <Text strong>9:16 · 1080P</Text>
                  </div>
                <div className="avatar-speech-rate">
                  <Text strong>语速</Text>
                  <Radio.Group
                    value={speechRate}
                    onChange={(event) => setSpeechRate(event.target.value)}
                    optionType="button"
                    buttonStyle="solid"
                    size="small"
                    options={[
                      { label: "0.8x", value: 0.8 },
                      { label: "0.9x", value: 0.9 },
                      { label: "1.0x", value: 1 },
                      { label: "1.1x", value: 1.1 },
                      { label: "1.2x", value: 1.2 },
                    ]}
                  />
                </div>
                <Button
                  type="primary"
                  icon={<RocketOutlined />}
                  loading={submitting || quoting}
                  disabled={
                    serviceUnavailable ||
                    selectedProfileUnavailable ||
                    !selectedAvatarReady ||
                    !selectedVoiceReady
                  }
                  onClick={handleSubmit}
                  className="avatar-primary-action"
                >
                  {submitButtonText}
                </Button>
              </div>
            </div>
          </section>
        </Col>

        <Col xs={24} lg={8} className="avatar-task-column">
          <Card className="avatar-task-rail" loading={loading}>
            <div ref={currentTaskRef} className="avatar-current-task">
              <div className="avatar-task-heading">
                <Title level={4}>当前任务</Title>
              </div>
              {activeJob ? (
                <Space direction="vertical" size={14} style={{ width: "100%" }}>
                  <Space size={10} wrap>
                    <Tag color={statusColor(activeJob.status)}>{statusLabel(activeJob.status)}</Tag>
                    {activeJob.is_mock && <Tag color="blue">演示</Tag>}
                    <Text strong ellipsis className="avatar-task-title">{activeJob.title}</Text>
                  </Space>
                  <Text type="secondary"><ClockCircleOutlined /> {new Date(activeJob.created_at).toLocaleString()}</Text>
                  <div className="avatar-task-meta">
                    <Text type="secondary">形象</Text><Text>{activeJob.avatar_name}</Text>
                    <Text type="secondary">声音</Text><Text>{activeJob.voice_name}</Text>
                  </div>
                  {activeJob.result_url && (
                    <Space size={0} wrap className="avatar-task-actions">
                      <Button type="link" icon={<PlayCircleOutlined />} onClick={() => void handlePlayJob(activeJob)}>播放</Button>
                      <Button type="link" icon={<DownloadOutlined />} onClick={() => handleDownload(activeJob)}>下载</Button>
                      {!activeJob.is_mock && activeJob.status === "succeeded" && (
                        <Button type="link" onClick={() => openProductShowcase(activeJob)}>产品讲解</Button>
                      )}
                    </Space>
                  )}
                  {!TERMINAL_STATUSES.has(activeJob.status) && (
                    <>
                      <Progress percent={activeJob.progress} size="small" status={activeJob.status === "failed" ? "exception" : "active"} />
                      <Text type="secondary">{activeJob.stage}</Text>
                    </>
                  )}
                  {activeJob.error_message && (
                    <Alert
                      type="error"
                      showIcon
                      icon={<ExclamationCircleOutlined />}
                      message={activeJob.error_message}
                      action={activeJob.can_retry_video_submit ? <Button size="small" onClick={() => handleRetryVideo(activeJob)}>仅重试视频</Button> : undefined}
                    />
                  )}
                  {activeJob.status === "succeeded" && !activeJob.result_url && (
                    <Text type="secondary">任务已完成，成片暂不可下载。</Text>
                  )}
                </Space>
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有任务，完成左侧内容即可开始" />
              )}
            </div>

            <div className="avatar-history-section">
              <div className="avatar-history-summary">
                <Title level={4}>历史任务</Title>
                <div className="avatar-history-link">
                  <Text>{jobs.length}条</Text>
                  <Button type="link" onClick={() => setHistoryOpen(true)} disabled={!jobs.length}>
                    查看全部 <RightOutlined />
                  </Button>
                </div>
              </div>

              {recentJobs.length > 0 && (
                <div className="avatar-recent-jobs" aria-label="最近历史任务">
                  {recentJobs.map((item) => (
                    <button
                      type="button"
                      key={item.task_id}
                      className="avatar-recent-job"
                      aria-label={`查看历史任务：${item.title}`}
                      onClick={() => void handleViewJob(item.task_id)}
                    >
                      <span className="avatar-recent-job-main">
                        <span className="avatar-recent-job-title">{item.title}</span>
                        <span className="avatar-recent-job-meta">
                          {new Date(item.created_at).toLocaleDateString()} · {item.avatar_name}
                        </span>
                      </span>
                      <span className="avatar-recent-job-status">
                        <Tag color={statusColor(item.status)}>{statusLabel(item.status)}</Tag>
                        <RightOutlined />
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Card>
        </Col>
      </Row>

      <Modal
        title={capability?.mode === "sandbox" ? "演示任务历史" : "历史任务"}
        open={historyOpen}
        onCancel={() => setHistoryOpen(false)}
        width={720}
        footer={null}
        destroyOnHidden
        className="avatar-history-modal"
      >
        {jobs.length ? (
          <List
            dataSource={jobs}
            pagination={jobs.length > 6 ? {
              pageSize: 6,
              size: "small",
              showSizeChanger: false,
              hideOnSinglePage: true,
              showLessItems: true,
            } : false}
            renderItem={(item) => (
              <List.Item
                className={activeJobId === item.task_id ? "avatar-history-item is-active" : "avatar-history-item"}
                role="button"
                tabIndex={0}
                aria-label={`查看${item.title}任务进度`}
                onClick={() => {
                  void handleViewJob(item.task_id);
                  setHistoryOpen(false);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    void handleViewJob(item.task_id);
                    setHistoryOpen(false);
                  }
                }}
              >
                <div className="avatar-history-item-content">
                  <List.Item.Meta
                    avatar={<CheckCircleOutlined style={{ color: item.status === "succeeded" ? "#10b981" : "#64748b" }} />}
                    title={<Space wrap><Text strong>{item.title}</Text><Tag color={statusColor(item.status)}>{statusLabel(item.status)}</Tag>{item.is_mock && <Tag>演示</Tag>}</Space>}
                    description={`${new Date(item.created_at).toLocaleString()} · ${item.avatar_name} · ${item.voice_name}`}
                  />
                  <Space size={0} wrap>
                    {item.result_url && <Button type="link" icon={<PlayCircleOutlined />} onClick={(event) => { event.stopPropagation(); void handlePlayJob(item); }}>播放</Button>}
                    {item.result_url && <Button type="link" icon={<DownloadOutlined />} onClick={(event) => { event.stopPropagation(); void handleDownload(item); }}>下载</Button>}
                    {!item.is_mock && item.status === "succeeded" && item.result_url && <Button type="link" onClick={(event) => { event.stopPropagation(); openProductShowcase(item); }}>产品讲解</Button>}
                    <span onClick={(event) => event.stopPropagation()}>
                      <Popconfirm title="删除这条数字人任务？" description="只删除任务记录，不会删除已下载到本地的成片。" okText="删除" okButtonProps={{ danger: true }} cancelText="取消" onConfirm={() => handleDeleteJob(item.task_id)}>
                        <Button type="link" danger icon={<DeleteOutlined />}>删除</Button>
                      </Popconfirm>
                    </span>
                  </Space>
                </div>
              </List.Item>
            )}
          />
        ) : (
          <Empty description="提交任务后会显示真实历史记录" />
        )}
      </Modal>

      <Modal
        title="选择 / 添加形象"
        open={avatarLibraryOpen}
        onCancel={() => setAvatarLibraryOpen(false)}
        width={860}
        destroyOnHidden
        footer={
          <Button type="primary" onClick={() => setAvatarLibraryOpen(false)}>
            完成
          </Button>
        }
      >
        <Space direction="vertical" size={20} style={{ width: "100%" }}>
          <div>
            <Text strong style={{ display: "block", fontSize: 16 }}>形象库</Text>
            <Text type="secondary">选择一个已就绪形象；主页只保留名称，不直接展示真人画面。</Text>
          </div>

          {avatars.length ? (
            <Row gutter={[16, 16]} data-testid="avatar-library-grid">
              {avatars.map((item) => {
                const ready = isReadyAsset(item);
                const selected = item.asset_id === avatarId;
                const itemStatus = ready
                  ? "可使用"
                  : item.status === "training"
                    ? "训练中"
                    : item.status === "failed"
                      ? "训练失败"
                      : "未就绪";
                return (
                  <Col xs={12} sm={8} md={6} key={item.asset_id}>
                    <button
                      type="button"
                      disabled={!ready}
                      aria-pressed={selected}
                      aria-label={`${item.name}，${itemStatus}`}
                      onClick={() => {
                        setAvatarId(item.asset_id);
                        setAvatarLibraryOpen(false);
                      }}
                      style={{
                        width: "100%",
                        padding: 0,
                        overflow: "hidden",
                        textAlign: "left",
                        borderRadius: 12,
                        border: selected ? "2px solid #7c3aed" : "1px solid var(--border-default)",
                        background: selected ? "#f5f0ff" : "#ffffff",
                        cursor: ready ? "pointer" : "not-allowed",
                        opacity: ready ? 1 : 0.65,
                      }}
                    >
                      <div
                        style={{
                          width: "100%",
                          aspectRatio: "3/4",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          overflow: "hidden",
                          background: "#f8fafc",
                        }}
                      >
                        {item.preview_url ? (
                          item.preview_type === "video" ? (
                            <video
                              src={item.preview_url}
                              aria-label={`${item.name} 形象预览`}
                              muted
                              playsInline
                              preload="metadata"
                              style={{ width: "100%", height: "100%", objectFit: "cover", pointerEvents: "none" }}
                            />
                          ) : (
                            <img
                              src={item.preview_url}
                              alt={item.name}
                              style={{ width: "100%", height: "100%", objectFit: "cover", pointerEvents: "none" }}
                            />
                          )
                        ) : (
                          <UserOutlined style={{ fontSize: 42, color: "#94a3b8" }} />
                        )}
                      </div>
                      <div style={{ padding: 12 }}>
                        <Text strong ellipsis style={{ display: "block" }}>{item.name}</Text>
                        <Tag
                          color={ready ? "success" : item.status === "training" ? "processing" : "error"}
                          style={{ marginTop: 8, marginInlineEnd: 0 }}
                        >
                          {itemStatus}
                        </Tag>
                      </div>
                    </button>
                  </Col>
                );
              })}
            </Row>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可展示形象" />
          )}

          <div style={{ borderTop: "1px solid var(--border-default)", paddingTop: 16 }}>
            <Text strong style={{ display: "block", marginBottom: 10 }}>添加新形象</Text>
            {canAddAvatarMaterial ? (
              <Space wrap>
                {supportsLocalUpload && (
                  <Button icon={<CameraOutlined />} onClick={() => void handleOpenCamera()}>
                    拍摄本人形象
                  </Button>
                )}
                <Upload
                  accept={supportsLocalUpload ? "image/png,image/jpeg,image/webp" : "video/mp4,video/quicktime,.mp4,.mov"}
                  showUploadList={false}
                  beforeUpload={(file) => {
                    void (supportsLocalUpload ? handleAssetUpload("avatar", file) : handleCloudAvatarTraining(file));
                    return false;
                  }}
                >
                  <Button
                    icon={<UploadOutlined />}
                    loading={uploadingAvatar}
                    disabled={supportsLocalUpload ? false : !supportsCloudAvatarTraining}
                  >
                    {supportsLocalUpload ? "从设备上传照片" : "上传训练视频新增云形象"}
                  </Button>
                </Upload>
              </Space>
            ) : (
              <Text type="secondary">
                {capability?.provider_name === "shuying_legacy_cloud"
                  ? "云形象训练线路配置未完成，请联系管理员检查上传地址和允许域名。"
                  : "当前账号暂未开放新增形象权限。"}
              </Text>
            )}
          </div>
        </Space>
      </Modal>

      <Modal
        title="选择 / 添加声音"
        open={voiceLibraryOpen}
        onCancel={handleCloseVoiceLibrary}
        width={760}
        destroyOnHidden
        footer={
          <Button type="primary" onClick={handleCloseVoiceLibrary}>
            完成
          </Button>
        }
      >
        <Space direction="vertical" size={20} style={{ width: "100%" }}>
          <div>
            <Text strong style={{ display: "block", fontSize: 16 }}>声音库</Text>
            <Text type="secondary">选择一个已就绪声音；有声音样本时可直接在这里试听。</Text>
          </div>

          {voices.length ? (
            <Row gutter={[16, 16]} data-testid="voice-library-grid">
              {voices.map((item) => {
                const ready = isReadyAsset(item);
                const selected = item.asset_id === voiceId;
                const previewable = item.preview_type === "audio" && Boolean(item.preview_url);
                const itemStatus = ready
                  ? "可使用"
                  : item.status === "training"
                    ? "训练中"
                    : item.status === "pending_configuration"
                      ? "待训练"
                      : item.status === "failed"
                        ? "训练失败"
                        : "未就绪";
                return (
                  <Col xs={24} sm={12} md={8} key={item.asset_id}>
                    <div
                      style={{
                        height: "100%",
                        overflow: "hidden",
                        borderRadius: 12,
                        border: selected ? "2px solid #7c3aed" : "1px solid var(--border-default)",
                        background: selected ? "#f5f0ff" : "#ffffff",
                      }}
                    >
                      <button
                        type="button"
                        disabled={!ready}
                        aria-pressed={selected}
                        aria-label={`${item.name}，${itemStatus}`}
                        onClick={() => {
                          setVoiceId(item.asset_id);
                          handleCloseVoiceLibrary();
                        }}
                        style={{
                          width: "100%",
                          minHeight: 132,
                          padding: 16,
                          border: 0,
                          background: "transparent",
                          display: "flex",
                          flexDirection: "column",
                          alignItems: "center",
                          justifyContent: "center",
                          cursor: ready ? "pointer" : "not-allowed",
                          opacity: ready ? 1 : 0.65,
                        }}
                      >
                        <AudioOutlined style={{ fontSize: 36, color: "#64748b", marginBottom: 10 }} />
                        <Text strong ellipsis style={{ display: "block", maxWidth: "100%" }}>{item.name}</Text>
                        <Tag
                          color={
                            ready
                              ? "success"
                              : item.status === "training"
                                ? "processing"
                                : item.status === "failed"
                                  ? "error"
                                  : "warning"
                          }
                          style={{ marginTop: 8, marginInlineEnd: 0 }}
                        >
                          {itemStatus}
                        </Tag>
                      </button>
                      {previewable && (
                        <div style={{ padding: "0 12px 12px" }}>
                          <audio
                            controls
                            preload="metadata"
                            src={item.preview_url || undefined}
                            aria-label={`试听声音样本：${item.name}`}
                            style={{ width: "100%", display: "block" }}
                          />
                        </div>
                      )}
                    </div>
                  </Col>
                );
              })}
            </Row>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可展示声音" />
          )}

          <div style={{ borderTop: "1px solid var(--border-default)", paddingTop: 16 }}>
            <Text strong style={{ display: "block", marginBottom: 10 }}>添加新声音</Text>
            {canAddVoiceMaterial ? (
              <>
                <Space wrap>
                  {supportsLocalUpload && (
                    <Button
                      danger={recording}
                      icon={recording ? <StopOutlined /> : <AudioOutlined />}
                      onClick={() => void handleRecordVoice()}
                    >
                      {recording ? "结束录音" : "直接录音"}
                    </Button>
                  )}
                  <Upload
                    accept={supportsLocalUpload ? "audio/wav,audio/mpeg,audio/mp3,audio/mp4,audio/webm,.wav,.mp3,.m4a,.webm" : "audio/wav,audio/mpeg,audio/mp3,audio/mp4,.wav,.mp3,.m4a"}
                    showUploadList={false}
                    beforeUpload={(file) => {
                      void (supportsLocalUpload ? handleAssetUpload("voice", file) : handleCloudVoiceTraining(file));
                      return false;
                    }}
                  >
                    <Button
                      icon={<UploadOutlined />}
                      loading={uploadingVoice}
                      disabled={supportsLocalUpload ? false : !supportsVoiceCloning}
                    >
                      {supportsLocalUpload ? "从设备上传录音" : "克隆声音（上传样本）"}
                    </Button>
                  </Upload>
                </Space>
                <Text type="secondary" style={{ display: "block", marginTop: 10 }}>
                  {!supportsLocalUpload && !supportsVoiceCloning
                    ? "声音训练线路尚未就绪，暂不能提交样本。"
                    : "上传后会立即开始训练；只有标记为“可使用”的声音可生成视频。"}
                </Text>
              </>
            ) : (
              <Text type="secondary">当前账号暂未开放新增声音权限。</Text>
            )}
          </div>
        </Space>
      </Modal>

      <Modal
        title="制作产品讲解"
        open={productShowcaseOpen}
        onCancel={() => setProductShowcaseOpen(false)}
        width={620}
        destroyOnHidden
        footer={
          productShowcaseJob?.status === "succeeded"
            ? <Button type="primary" onClick={() => setProductShowcaseOpen(false)}>完成</Button>
            : [
                <Button key="cancel" onClick={() => setProductShowcaseOpen(false)}>取消</Button>,
                <Button
                  key="submit"
                  type="primary"
                  loading={productShowcaseSubmitting}
                  disabled={!productAsset || Boolean(visualUploading) || productShowcaseJob?.status === "running"}
                  onClick={() => void handleCreateProductShowcase()}
                >
                  开始合成
                </Button>,
              ]
        }
      >
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            message="商品使用你上传的原图合成，不会让 AI 重新绘制。"
            description="可增加品牌背景画布；当前不会替换人物身后的真实场景，也不支持让数字人手持商品。"
          />
          <Text type="secondary">来源：{productShowcaseSource?.title || "已完成数字人成片"}</Text>

          <div>
            <Text strong>商品主图</Text>
            <Upload
              accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
              showUploadList={false}
              beforeUpload={(file) => handleVisualAssetUpload("product", file as File)}
            >
              <Button icon={<UploadOutlined />} loading={visualUploading === "product"} style={{ marginTop: 8 }}>
                上传 PNG / JPG / WebP
              </Button>
            </Upload>
            {productAsset && (
              <Space size={8} style={{ marginTop: 10 }}>
                <img src={productAsset.media_url} alt={productAsset.name} style={{ width: 56, height: 56, objectFit: "contain", border: "1px solid var(--border-default)", borderRadius: 8 }} />
                <Text>{productAsset.name}</Text>
              </Space>
            )}
          </div>

          <div>
            <Text strong>背景画布（可选）</Text>
            <div><Text type="secondary">不上传时使用简洁品牌模板。</Text></div>
            <Upload
              accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
              showUploadList={false}
              beforeUpload={(file) => handleVisualAssetUpload("background", file as File)}
            >
              <Button icon={<UploadOutlined />} loading={visualUploading === "background"} style={{ marginTop: 8 }}>
                上传背景图
              </Button>
            </Upload>
            {backgroundAsset && <Text type="secondary" style={{ display: "block", marginTop: 8 }}>已使用：{backgroundAsset.name}</Text>}
          </div>

          <div>
            <Text strong>展示版式</Text>
            <Radio.Group
              value={productLayout}
              onChange={(event) => setProductLayout(event.target.value)}
              style={{ display: "block", marginTop: 8 }}
            >
              <Space direction="vertical">
                <Radio value="avatar_left_product_right">数字人左侧，商品原图右侧</Radio>
                <Radio value="product_canvas_avatar_pip">商品主视觉，数字人成片小窗</Radio>
              </Space>
            </Radio.Group>
          </div>

          <div style={{ borderRadius: 12, padding: 12, background: "#f7f5ff", minHeight: 156, display: "flex", gap: 10, alignItems: "center" }}>
            <div style={{ flex: productLayout === "avatar_left_product_right" ? 1.35 : 0.7, alignSelf: "stretch", borderRadius: 8, background: "#1f2937", color: "#fff", display: "grid", placeItems: "center" }}>数字人成片</div>
            <div style={{ flex: 1, alignSelf: "stretch", borderRadius: 8, background: "#fff", display: "grid", placeItems: "center", overflow: "hidden" }}>
              {productAsset ? <img src={productAsset.media_url} alt="商品预览" style={{ width: "100%", height: "100%", objectFit: "contain" }} /> : <Text type="secondary">商品原图预览</Text>}
            </div>
          </div>

          {productShowcaseJob && (
            <Card size="small">
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                <Text strong>{productShowcaseJob.status === "succeeded" ? "产品讲解成片已生成" : productShowcaseJob.stage}</Text>
                {productShowcaseJob.status !== "succeeded" && <Progress percent={productShowcaseJob.progress} size="small" status={productShowcaseJob.status === "failed" ? "exception" : "active"} />}
                {productShowcaseJob.error_message && <Alert type="error" showIcon message={productShowcaseJob.error_message} />}
                {productShowcaseJob.status === "succeeded" && productShowcaseJob.media_url && (
                  <>
                    <video controls src={productShowcaseJob.media_url} style={{ width: "100%", borderRadius: 8 }} />
                    <Button href={productShowcaseJob.download_url || undefined} icon={<DownloadOutlined />}>下载产品讲解成片</Button>
                  </>
                )}
              </Space>
            </Card>
          )}
        </Space>
      </Modal>

      <Modal
        title="拍摄本人形象"
        open={cameraOpen}
        onCancel={closeCamera}
        destroyOnHidden
        footer={[
          <Button key="cancel" onClick={closeCamera}>取消</Button>,
          <Button key="take-photo" type="primary" icon={<CameraOutlined />} onClick={handleTakePhoto} disabled={!cameraStream}>
            拍照并使用
          </Button>,
        ]}
      >
        {cameraError ? (
          <Alert type="error" showIcon message="无法打开摄像头" description={cameraError} />
        ) : (
          <video ref={cameraVideoRef} autoPlay playsInline muted style={{ width: "100%", borderRadius: 8, background: "#0f172a" }} />
        )}
      </Modal>

      <Modal
        title={playbackJob ? `${playbackJob.title} · 成片播放` : "成片播放"}
        open={Boolean(playbackJob)}
        onCancel={closePlayback}
        footer={playbackJob ? [
          <Button key="download" icon={<DownloadOutlined />} onClick={() => handleDownload(playbackJob)}>下载成片</Button>,
          <Button key="close" type="primary" onClick={closePlayback}>关闭</Button>,
        ] : null}
        width={460}
        destroyOnHidden
      >
        {playbackLoading ? (
          <Progress percent={60} status="active" showInfo={false} />
        ) : playbackUrl ? (
          <video controls autoPlay playsInline src={playbackUrl} style={{ width: "100%", maxHeight: "70vh", background: "#0f172a", borderRadius: 8 }} />
        ) : (
          <Empty description="成片暂时不可播放" />
        )}
      </Modal>

      <style>{`
        .avatar-studio-page {
          width: 100%;
          max-width: 1360px;
          margin: 0 auto;
        }

        .avatar-capability-alert {
          margin-bottom: 16px;
        }

        .avatar-studio-layout > .ant-col {
          display: flex;
        }

        .avatar-composer {
          width: 100%;
          padding: 10px 24px 20px 26px;
        }

        .avatar-composer-heading {
          display: flex;
          align-items: baseline;
          gap: 12px;
          margin-bottom: 26px;
        }

        .avatar-composer-heading .ant-typography {
          margin: 0;
        }

        .avatar-composer-heading h2.ant-typography {
          color: var(--text-primary);
          font-size: 18px;
          line-height: 1.4;
          font-weight: 650;
          letter-spacing: 0;
        }

        .avatar-composer-heading .ant-typography-secondary {
          font-size: 13px;
        }

        .avatar-form-stack {
          display: flex;
          flex-direction: column;
          gap: 24px;
        }

        .avatar-field-label {
          display: block;
          margin-bottom: 9px;
          color: var(--text-primary);
          font-size: 15px;
        }

        .avatar-choice-button {
          height: 48px;
          padding-inline: 16px;
          border-radius: 8px;
          border-color: var(--border-default);
          box-shadow: none;
        }

        .avatar-choice-button:hover:not(:disabled),
        .avatar-choice-button:focus-visible:not(:disabled) {
          border-color: var(--primary-500);
          color: var(--text-primary);
        }

        .avatar-choice-content {
          display: grid;
          width: 100%;
          grid-template-columns: auto minmax(0, 1fr) auto auto;
          align-items: center;
          gap: 12px;
          text-align: left;
        }

        .avatar-choice-content > .ant-typography {
          min-width: 0;
        }

        .avatar-choice-content .ant-tag {
          margin-inline-end: 0;
        }

        .avatar-name-input,
        .avatar-name-input.ant-input-affix-wrapper {
          min-height: 44px;
          border-radius: 8px;
        }

        .avatar-script-input {
          min-height: 164px !important;
          padding: 12px 14px;
          border-radius: 8px;
          line-height: 1.65;
          resize: vertical;
        }

        .avatar-inline-note {
          display: block;
          margin-top: 8px;
        }

        .avatar-cost-preview {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 10px 12px;
          border: 1px solid #d9f0e4;
          border-radius: 8px;
          background: #f6fffa;
        }

        .avatar-cost-preview.is-unknown {
          border-color: #ffe0a3;
          background: #fffaf0;
        }

        .avatar-cost-preview .ant-typography:last-child {
          margin-left: auto;
        }

        .avatar-output-settings {
          display: flex;
          min-height: 48px;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          padding: 10px 12px;
          border: 1px solid var(--border-default);
          border-radius: 8px;
          background: #fff;
        }

        .avatar-fixed-output,
        .avatar-speech-rate {
          display: flex;
          align-items: center;
          gap: 10px;
          white-space: nowrap;
        }

        .avatar-fixed-output .ant-typography,
        .avatar-speech-rate > .ant-typography {
          margin: 0;
          font-size: 13px;
        }

        .avatar-speech-rate .ant-radio-button-wrapper {
          padding-inline: 8px;
        }

        .avatar-primary-action {
          flex: 0 0 190px;
          height: 40px;
          border: none;
          border-radius: 8px;
          font-size: 14px;
          font-weight: 600;
          background: #6d28d9;
          box-shadow: none;
        }

        .avatar-primary-action:hover:not(:disabled),
        .avatar-primary-action:focus-visible:not(:disabled) {
          background: #5b21b6;
        }

        .avatar-task-rail {
          width: 100%;
          min-height: 0;
          align-self: flex-start;
          border-color: var(--border-default);
          border-radius: 10px;
          box-shadow: none;
        }

        .avatar-task-rail > .ant-card-body {
          padding: 24px;
        }

        .avatar-task-heading {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding-bottom: 16px;
          margin-bottom: 18px;
          border-bottom: 1px solid var(--border-default);
        }

        .avatar-task-heading h4.ant-typography,
        .avatar-history-summary h4.ant-typography {
          margin: 0;
          color: var(--text-primary);
          font-size: 20px;
          line-height: 1.35;
        }

        .avatar-task-title {
          max-width: 200px;
          font-size: 15px;
        }

        .avatar-task-meta {
          display: grid;
          grid-template-columns: auto minmax(0, 1fr) auto minmax(0, 1fr);
          align-items: center;
          gap: 8px 10px;
        }

        .avatar-task-actions {
          margin-left: -12px;
        }

        .avatar-history-section {
          padding-top: 24px;
          margin-top: 24px;
          border-top: 1px solid var(--border-default);
        }

        .avatar-history-summary {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
        }

        .avatar-history-link {
          display: flex;
          align-items: center;
          gap: 4px;
          white-space: nowrap;
        }

        .avatar-recent-jobs {
          display: grid;
          gap: 10px;
          margin-top: 16px;
        }

        .avatar-recent-job {
          display: flex;
          width: 100%;
          min-height: 64px;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 11px 12px;
          color: var(--text-primary);
          text-align: left;
          background: #f8fafc;
          border: 1px solid var(--border-default);
          border-radius: 8px;
          cursor: pointer;
          transition: border-color 160ms ease, background-color 160ms ease;
        }

        .avatar-recent-job:nth-child(n + 4) {
          display: none;
        }

        .avatar-recent-job:hover,
        .avatar-recent-job:focus-visible {
          background: #faf7ff;
          border-color: #c4b5fd;
          outline: none;
        }

        .avatar-recent-job-main {
          display: grid;
          min-width: 0;
          gap: 4px;
        }

        .avatar-recent-job-title {
          overflow: hidden;
          font-size: 14px;
          font-weight: 600;
          line-height: 1.4;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .avatar-recent-job-meta {
          overflow: hidden;
          color: var(--text-secondary);
          font-size: 12px;
          line-height: 1.4;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .avatar-recent-job-status {
          display: flex;
          flex: 0 0 auto;
          align-items: center;
          gap: 4px;
          color: var(--text-tertiary);
          font-size: 11px;
        }

        .avatar-recent-job-status .ant-tag {
          margin-inline-end: 0;
        }

        .avatar-history-item {
          display: block !important;
          padding: 14px 12px !important;
          border-radius: 8px;
          cursor: pointer;
        }

        .avatar-history-item.is-active {
          background: #f5f0ff;
        }

        .avatar-history-item-content {
          display: flex;
          width: 100%;
          align-items: flex-start;
          flex-wrap: wrap;
          gap: 8px;
        }

        .avatar-history-item-content .ant-list-item-meta {
          flex: 1 1 280px;
          min-width: 0;
          margin-bottom: 0;
        }

        .avatar-history-modal .ant-modal-body {
          max-height: min(620px, calc(100vh - 180px));
          overflow-y: auto;
          overscroll-behavior: contain;
        }

        .avatar-history-modal .ant-list-pagination {
          margin-block: 18px 4px;
          text-align: center;
        }

        @media (max-width: 1199px) {
          .avatar-composer {
            padding-inline: 4px;
          }

          .avatar-task-rail > .ant-card-body {
            padding: 22px;
          }
        }

        @media (max-width: 991px) {
          .avatar-studio-layout > .ant-col {
            display: block;
          }

          .avatar-task-rail {
            min-height: 0;
          }
        }

        @media (min-width: 992px) {
          .avatar-composer-column {
            flex: 0 0 64%;
            max-width: 64%;
          }

          .avatar-task-column {
            flex: 0 0 36%;
            max-width: 36%;
          }
        }

        @media (min-width: 992px) and (min-height: 1000px) {
          .avatar-composer-heading {
            margin-bottom: 30px;
          }

          .avatar-form-stack {
            gap: 26px;
          }

          .avatar-script-input {
            min-height: 320px !important;
          }

          .avatar-recent-job:nth-child(n + 4) {
            display: flex;
          }
        }

        @media (max-width: 576px) {
          .avatar-composer {
            padding: 8px 0 0;
          }

          .avatar-composer-heading {
            display: block;
            margin-bottom: 22px;
          }

          .avatar-composer-heading h2.ant-typography {
            margin-bottom: 4px;
            font-size: 18px;
          }

          .avatar-primary-action {
            width: 100%;
            flex-basis: auto;
          }

          .avatar-output-settings {
            align-items: flex-start;
            flex-direction: column;
            gap: 10px;
          }

          .avatar-speech-rate {
            width: 100%;
            justify-content: space-between;
          }

          .avatar-speech-rate .ant-radio-group {
            display: flex;
            flex: 1;
          }

          .avatar-speech-rate .ant-radio-button-wrapper {
            flex: 1;
            padding-inline: 6px;
            text-align: center;
          }

          .avatar-task-rail > .ant-card-body {
            padding: 20px 16px;
          }

          .avatar-task-meta {
            grid-template-columns: auto minmax(0, 1fr);
          }
        }
      `}</style>
    </div>
  );
}
