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
  Slider,
  Space,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  AudioOutlined,
  CameraOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
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
  getAvatarCapabilities,
  getAvatarJob,
  getVideoEditorJob,
  listAvatarAssets,
  listAvatarJobs,
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
import { useSearchParams } from "react-router-dom";

const { Title, Text } = Typography;
const { TextArea } = Input;

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
        : "提交数字人口播任务";

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

  const handleSubmit = useCallback(async () => {
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
    if (!supportsVoiceSampleUpload) {
      toast.warning("公司云数字人服务尚未配置，暂不能保存声音样本。");
      return false;
    }
    setUploadingVoice(true);
    try {
      const asset = await trainCloudVoice({
        file,
        name: file.name.replace(/\.[^.]+$/, "") || "新建克隆声音",
      });
      await refresh();
      setVoiceId(asset.asset_id);
      toast.success(asset.status === "pending_configuration" ? "声音样本已保存，待配置声音线路后再克隆。" : "声音克隆训练已提交，可在素材列表查看状态。");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "提交声音克隆失败");
    } finally {
      setUploadingVoice(false);
    }
    return false;
  }, [refresh, supportsVoiceSampleUpload, toast]);

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
    <div>
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          <UserOutlined /> 数字人口播生成
        </Title>
        <Text type="secondary">
          选择形象和声音，输入口播文案后生成数字人成片。
        </Text>
      </div>

      {capability && (serviceUnavailable || capability.mode === "sandbox") && (
        <Alert
          style={{ marginBottom: 16 }}
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

      {/* 生成配置与任务 */}
      <Row gutter={[24, 24]} style={{ marginBottom: 24 }}>
        <Col xs={24} lg={12}>
          <Card title={<Space><RocketOutlined /> 生成配置</Space>} loading={loading}>
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              <div>
                <Text strong>素材</Text>
                <Row gutter={[8, 8]} style={{ marginTop: 8 }}>
                  <Col xs={24} sm={12}>
                    <Button
                      block
                      onClick={() => setAvatarLibraryOpen(true)}
                      disabled={!avatars.length && !canAddAvatarMaterial}
                      data-testid="avatar-library-trigger"
                      style={{ height: 40, paddingInline: 10 }}
                    >
                      <span style={{ width: "100%", display: "flex", alignItems: "center", gap: 7 }}>
                        <UserOutlined />
                        <Text type="secondary">形象</Text>
                        <Text strong ellipsis style={{ flex: 1, minWidth: 0, textAlign: "left" }}>
                          {selectedAvatar?.name || "未选择"}
                        </Text>
                        {selectedAvatar && (
                          <Tag
                            color={isReadyAsset(selectedAvatar) ? "success" : selectedAvatar.status === "training" ? "processing" : "error"}
                            style={{ marginInlineEnd: 0 }}
                          >
                            {isReadyAsset(selectedAvatar) ? "可用" : selectedAvatar.status === "training" ? "训练中" : selectedAvatar.status === "failed" ? "失败" : "未就绪"}
                          </Tag>
                        )}
                        <Text type="secondary">更换</Text>
                      </span>
                    </Button>
                  </Col>
                  <Col xs={24} sm={12}>
                    <Button
                      block
                      onClick={() => setVoiceLibraryOpen(true)}
                      disabled={!voices.length && !canAddVoiceMaterial}
                      data-testid="voice-library-trigger"
                      style={{ height: 40, paddingInline: 10 }}
                    >
                      <span style={{ width: "100%", display: "flex", alignItems: "center", gap: 7 }}>
                        <AudioOutlined />
                        <Text type="secondary">声音</Text>
                        <Text strong ellipsis style={{ flex: 1, minWidth: 0, textAlign: "left" }}>
                          {selectedVoice?.name || "未选择"}
                        </Text>
                        {selectedVoice && (
                          <Tag
                            color={
                              isReadyAsset(selectedVoice)
                                ? "success"
                                : selectedVoice.status === "training"
                                  ? "processing"
                                  : selectedVoice.status === "failed"
                                    ? "error"
                                    : "warning"
                            }
                            style={{ marginInlineEnd: 0 }}
                          >
                            {isReadyAsset(selectedVoice)
                              ? "可用"
                              : selectedVoice.status === "training"
                                ? "训练中"
                                : selectedVoice.status === "pending_configuration"
                                  ? "待训练"
                                  : selectedVoice.status === "failed"
                                    ? "失败"
                                    : "未就绪"}
                          </Tag>
                        )}
                        <Text type="secondary">更换</Text>
                      </span>
                    </Button>
                  </Col>
                </Row>
              </div>
              <div>
                <Text strong>视频名称（选填）</Text>
                <Input
                  value={videoName}
                  onChange={(event) => setVideoName(event.target.value)}
                  maxLength={100}
                  placeholder={keywordFromQuery ? `留空自动命名为“${keywordFromQuery}1”` : "留空自动命名为“数字人视频1”"}
                  style={{ marginTop: 8 }}
                />
                <Text type="secondary" style={{ display: "block", marginTop: 6 }}>
                  重名时系统会自动追加序号。
                </Text>
              </div>

              <div>
                <Text strong>口播文案</Text>
                <TextArea
                  rows={6}
                  value={scriptText}
                  onChange={(event) => setScriptText(event.target.value)}
                  maxLength={capability?.max_script_chars || 240}
                  showCount
                  placeholder="输入数字人要说的内容，数字人会按文案自然播完。"
                  style={{ marginTop: 8 }}
                />
                {sourceTaskId && (
                  <Text type="secondary" style={{ display: "block", marginTop: 6 }}>
                    已带入人工确认后的 LLM 口播稿，来源任务：{sourceTaskId}
                  </Text>
                )}
                {selectedRecordedProfile && (
                  <Alert
                    style={{ marginTop: 8 }}
                    type="info"
                    showIcon
                    message="录音驱动模式"
                    description="文案只用于任务记录，最终口播内容以你上传的完整录音为准。"
                  />
                )}
              </div>

              <div>
                <Text strong>输出规格</Text>
                <Input value="1080x1920 · 9:16" disabled style={{ marginTop: 8 }} />
              </div>

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

              <Button
                type="primary"
                icon={<RocketOutlined />}
                size="large"
                block
                loading={submitting}
                disabled={
                  serviceUnavailable ||
                  selectedProfileUnavailable ||
                  !selectedAvatarReady ||
                  !selectedVoiceReady
                }
                onClick={handleSubmit}
              >
                {submitButtonText}
              </Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <div ref={currentTaskRef}>
            <Card
              size="small"
              title={<Space><PlayCircleOutlined /> 当前任务</Space>}
              extra={<Button type="text" size="small" icon={<ReloadOutlined />} onClick={refresh}>刷新</Button>}
              style={{ marginBottom: 16 }}
            >
            {activeJob ? (
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                  <Space size={8} wrap style={{ flex: "1 1 180px", minWidth: 0 }}>
                    <Tag color={statusColor(activeJob.status)}>
                      {statusLabel(activeJob.status)}
                    </Tag>
                    {activeJob.is_mock && <Tag color="blue">演示</Tag>}
                    <Text strong ellipsis style={{ maxWidth: 180 }}>{activeJob.title}</Text>
                  </Space>
                  {activeJob.result_url && (
                    <Space size={0} wrap>
                      <Button
                        type="link"
                        size="small"
                        icon={<PlayCircleOutlined />}
                        onClick={() => void handlePlayJob(activeJob)}
                      >
                        播放
                      </Button>
                      <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => handleDownload(activeJob)}>
                        下载
                      </Button>
                      {!activeJob.is_mock && activeJob.status === "succeeded" && (
                        <Button type="link" size="small" onClick={() => openProductShowcase(activeJob)}>
                          产品讲解
                        </Button>
                      )}
                    </Space>
                  )}
                </div>
                {!TERMINAL_STATUSES.has(activeJob.status) && (
                  <>
                    <Progress
                      percent={activeJob.progress}
                      size="small"
                      status={activeJob.status === "failed" ? "exception" : "active"}
                    />
                    <Text type="secondary">{activeJob.stage}</Text>
                  </>
                )}
                {activeJob.error_message && (
                  <Alert
                    type="error"
                    showIcon
                    icon={<ExclamationCircleOutlined />}
                    message={activeJob.error_message}
                  />
                )}
                {activeJob.status === "succeeded" && !activeJob.result_url && (
                  <Text type="secondary">任务已完成，成片暂不可下载。</Text>
                )}
              </Space>
            ) : (
              <Empty description="暂无真实任务" />
            )}
            </Card>
          </div>

          <Card title={capability?.mode === "sandbox" ? "演示任务历史" : "真实任务历史"}>
            {jobs.length ? (
              <List
                dataSource={jobs}
                renderItem={(item) => (
                  <List.Item
                    style={{
                      display: "block",
                      cursor: "pointer",
                      borderRadius: 8,
                      padding: 12,
                      background: activeJobId === item.task_id ? "#f5f0ff" : undefined,
                    }}
                    role="button"
                    tabIndex={0}
                    aria-label={`查看${item.title}任务进度`}
                    onClick={() => void handleViewJob(item.task_id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        void handleViewJob(item.task_id);
                      }
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "flex-start", flexWrap: "wrap", gap: 8, width: "100%" }}>
                      <List.Item.Meta
                        style={{ flex: "1 1 200px", minWidth: 0, marginBottom: 0 }}
                        avatar={<CheckCircleOutlined style={{ color: item.status === "succeeded" ? "#10b981" : "#64748b" }} />}
                        title={
                          <Space wrap>
                            <Text strong>{item.title}</Text>
                            <Tag color={statusColor(item.status)}>{statusLabel(item.status)}</Tag>
                            {item.is_mock && <Tag>演示</Tag>}
                          </Space>
                        }
                        description={`${new Date(item.created_at).toLocaleString()} · ${item.avatar_name} · ${item.voice_name}`}
                      />
                      <Space size={0} wrap>
                        {item.result_url && (
                          <Button
                            type="link"
                            icon={<PlayCircleOutlined />}
                            onClick={(event) => {
                              event.stopPropagation();
                              void handlePlayJob(item);
                            }}
                          >
                            播放
                          </Button>
                        )}
                        {item.result_url && (
                          <Button
                            type="link"
                            icon={<DownloadOutlined />}
                            onClick={(event) => {
                              event.stopPropagation();
                              void handleDownload(item);
                            }}
                          >
                            下载
                          </Button>
                        )}
                        {!item.is_mock && item.status === "succeeded" && item.result_url && (
                          <Button
                            type="link"
                            onClick={(event) => {
                              event.stopPropagation();
                              openProductShowcase(item);
                            }}
                          >
                            产品讲解
                          </Button>
                        )}
                        <span onClick={(event) => event.stopPropagation()}>
                          <Popconfirm
                            title="删除这条数字人任务？"
                            description="只删除任务记录，不会删除已下载到本地的成片。"
                            okText="删除"
                            okButtonProps={{ danger: true }}
                            cancelText="取消"
                            onConfirm={() => handleDeleteJob(item.task_id)}
                          >
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
          </Card>
        </Col>
      </Row>

      <Modal
        title="选择 / 添加形象"
        open={avatarLibraryOpen}
        onCancel={() => setAvatarLibraryOpen(false)}
        width={860}
        destroyOnClose
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
              <Text type="secondary">当前账号暂未开放新增形象权限。</Text>
            )}
          </div>
        </Space>
      </Modal>

      <Modal
        title="选择 / 添加声音"
        open={voiceLibraryOpen}
        onCancel={handleCloseVoiceLibrary}
        width={760}
        destroyOnClose
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
                      disabled={supportsLocalUpload ? false : !supportsVoiceSampleUpload}
                    >
                      {supportsLocalUpload ? "从设备上传录音" : supportsVoiceCloning ? "克隆声音（上传样本）" : "上传声音样本"}
                    </Button>
                  </Upload>
                </Space>
                <Text type="secondary" style={{ display: "block", marginTop: 10 }}>
                  上传或录制后会出现在上方；只有标记为“可使用”的声音可生成视频。
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
        destroyOnClose
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
        destroyOnClose
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
        destroyOnClose
      >
        {playbackLoading ? (
          <Progress percent={60} status="active" showInfo={false} />
        ) : playbackUrl ? (
          <video controls autoPlay playsInline src={playbackUrl} style={{ width: "100%", maxHeight: "70vh", background: "#0f172a", borderRadius: 8 }} />
        ) : (
          <Empty description="成片暂时不可播放" />
        )}
      </Modal>
    </div>
  );
}
