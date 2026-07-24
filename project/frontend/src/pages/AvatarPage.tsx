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
  Row,
  Select,
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
  CloudSyncOutlined,
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
  deleteTask,
  downloadAvatarJobMedia,
  getAvatarCapabilities,
  getAvatarJob,
  listAvatarAssets,
  listAvatarJobs,
  uploadAvatarAsset,
} from "../api/client";
import type { AvatarAsset, AvatarCapability, AvatarJob, AvatarProfile } from "../api/types";
import { useToast } from "../components/Toast";
import { useSearchParams } from "react-router-dom";

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
  const [searchParams] = useSearchParams();
  const sourceTaskId = searchParams.get("sourceTask")?.trim() || null;
  const sourceRevisionId = searchParams.get("sourceRevision")?.trim() || null;
  const scriptFromQuery = searchParams.get("script")?.trim() || "";
  const [capability, setCapability] = useState<AvatarCapability | null>(null);
  const [assets, setAssets] = useState<AvatarAsset[]>([]);
  const [jobs, setJobs] = useState<AvatarJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [uploadingAvatar, setUploadingAvatar] = useState(false);
  const [uploadingVoice, setUploadingVoice] = useState(false);
  const [recording, setRecording] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null);
  const [cameraError, setCameraError] = useState("");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const currentTaskRef = useRef<HTMLDivElement>(null);
  const cameraVideoRef = useRef<HTMLVideoElement>(null);
  const cameraStreamRef = useRef<MediaStream | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordingStreamRef = useRef<MediaStream | null>(null);

  const [scriptText, setScriptText] = useState("");
  const [avatarId, setAvatarId] = useState<string>();
  const [voiceId, setVoiceId] = useState<string>();
  const [profileId, setProfileId] = useState("default");
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

  const estimatedCost = selectedProfile?.estimated_cost_cny ?? null;

  const serviceUnavailable = Boolean(capability && !capability.enabled);
  const selectedProfileUnavailable = Boolean(selectedProfile && !selectedProfile.enabled);
  const supportsLocalUpload = capability?.provider_name === "local_avatar";
  const selectedRecordedProfile = selectedProfile?.profile_id === "local_recorded_natural";

  const estimatedCostText = useMemo(() => {
    if (!capability) return "读取中";
    if (capability.mode === "sandbox") return "演示任务，不计费";
    if (estimatedCost === null) return "待方案就绪后返回";
    if (estimatedCost === 0) return "本地算力（不含硬件摊销）";
    return `¥${estimatedCost.toFixed(2)}`;
  }, [capability, estimatedCost]);

  const capabilityDescription = useMemo(() => {
    if (!capability) return "";
    if (serviceUnavailable) {
      return capability.missing_configuration.length
        ? "缺少供应商配置，请管理员在服务端完成配置后再开放真实生成。"
        : "服务端当前未开放数字人生成能力。";
    }
    if (capability.mode === "sandbox") {
      return "演示模式只用于验证任务提交、状态轮询和页面流程，不会产生真实成片或真实费用。";
    }
    if (capability.provider_name === "local_avatar") {
      return "当前支持上传本人照片和完整口播录音。录音驱动不是声音克隆：最终口播以录音内容为准。";
    }
    return "当前支持从已授权的公共形象和公共音色库中选择。自定义克隆入口将在后端能力接通后开放。";
  }, [capability, serviceUnavailable]);

  const profileStatusText = selectedProfileUnavailable
    ? "该方案尚未部署完成：请管理员先完成本地语音、视频模型与授权素材配置。"
    : "";

  const submitButtonText = serviceUnavailable
    ? "数字人服务尚未配置"
    : selectedProfileUnavailable
      ? "所选方案尚未就绪"
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

  const handleSubmit = useCallback(async () => {
    if (!capability?.enabled) {
      toast.warning("数字人服务尚未可用，请先配置供应商。");
      return;
    }
    if (!avatarId || !voiceId) {
      toast.warning("缺少可用公共形象或音色。");
      return;
    }
    if (!selectedAvatar?.authorized || !selectedVoice?.authorized) {
      toast.warning("所选公共形象或音色尚未授权。");
      return;
    }
    if (selectedProfile && !selectedProfile.enabled) {
      toast.warning("所选生成方案尚未部署完成。");
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
        script_text: scriptText.trim(),
        avatar_id: avatarId,
        voice_id: voiceId,
        profile_id: selectedProfile?.profile_id,
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
    sourceRevisionId,
    sourceTaskId,
    targetPlatforms,
    toast,
    voiceId,
    selectedProfile,
    selectedAvatar,
    selectedVoice,
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
          description={capabilityDescription}
        />
      )}

      {/* 公共素材选择 */}
      <Row gutter={[24, 24]} style={{ marginBottom: 24 }}>
        <Col xs={24} lg={6}>
          <Card title={<Space><UserOutlined /> 形象素材</Space>} loading={loading}>
            <Space direction="vertical" style={{ width: "100%" }} size={14}>
              <div
                role="button"
                tabIndex={0}
                aria-label="点击打开摄像头拍照"
                onClick={() => void handleOpenCamera()}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") void handleOpenCamera();
                }}
                style={{
                  width: "100%",
                  aspectRatio: "3/4",
                  borderRadius: 12,
                  background: "#f8fafc",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  border: "2px dashed var(--border-default)",
                  overflow: "hidden",
                  cursor: supportsLocalUpload ? "pointer" : "not-allowed",
                  opacity: supportsLocalUpload ? 1 : 0.6,
                }}
              >
                {selectedAvatar?.preview_url ? (
                  <img
                    src={selectedAvatar.preview_url}
                    alt={selectedAvatar.name}
                    style={{ width: "100%", height: "100%", objectFit: "cover" }}
                  />
                ) : (
                  <>
                    <UserOutlined style={{ fontSize: 48, color: "#64748b", marginBottom: 12 }} />
                    <Text strong>{selectedAvatar?.asset_id.startsWith("local-") ? "点击重新拍照" : "点击拍照"}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>使用摄像头录入本人形象</Text>
                  </>
                )}
              </div>
              <Select
                value={avatarId}
                onChange={setAvatarId}
                options={avatars.map((item) => ({
                  label: item.authorized ? item.name : `${item.name}（未授权）`,
                  value: item.asset_id,
                  disabled: !item.authorized,
                }))}
                placeholder="选择公共形象"
                style={{ width: "100%" }}
              />
              <Upload
                accept="image/png,image/jpeg,image/webp"
                showUploadList={false}
                beforeUpload={(file) => {
                  void handleAssetUpload("avatar", file);
                  return false;
                }}
              >
                <Button
                  block
                  icon={<UploadOutlined />}
                  loading={uploadingAvatar}
                  disabled={!supportsLocalUpload}
                >
                  从设备上传照片
                </Button>
              </Upload>
              <Alert
                type="info"
                showIcon
                message="本人形象可直接使用"
                description="建议上传正脸清晰照片，需确认拥有肖像授权。"
              />
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={6}>
          <Card title={<Space><AudioOutlined /> 声音素材</Space>} loading={loading}>
            <Space direction="vertical" style={{ width: "100%" }} size={14}>
              <div
                role="button"
                tabIndex={0}
                aria-label={recording ? "点击结束录音" : "点击开始录音"}
                onClick={() => void handleRecordVoice()}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") void handleRecordVoice();
                }}
                style={{
                  width: "100%",
                  height: 160,
                  borderRadius: 12,
                  background: "#f8fafc",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  border: "2px dashed var(--border-default)",
                  cursor: supportsLocalUpload ? "pointer" : "not-allowed",
                  opacity: supportsLocalUpload ? 1 : 0.6,
                }}
              >
                {recording ? <StopOutlined style={{ fontSize: 48, color: "#ef4444", marginBottom: 12 }} /> : <AudioOutlined style={{ fontSize: 48, color: "#64748b", marginBottom: 12 }} />}
                <Text strong>{recording ? "正在录音，点击结束" : selectedVoice?.asset_id.startsWith("local-") ? "点击重新录音" : "点击开始录音"}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>浏览器会请求麦克风权限，结束后自动上传</Text>
              </div>
              <Select
                value={voiceId}
                onChange={setVoiceId}
                options={voices.map((item) => ({
                  label: item.authorized ? item.name : `${item.name}（未授权）`,
                  value: item.asset_id,
                  disabled: !item.authorized,
                }))}
                placeholder="选择公共音色"
                style={{ width: "100%" }}
              />
              <Upload
                accept="audio/wav,audio/mpeg,audio/mp3,audio/mp4,audio/webm,.wav,.mp3,.m4a,.webm"
                showUploadList={false}
                beforeUpload={(file) => {
                  void handleAssetUpload("voice", file);
                  return false;
                }}
              >
                <Button
                  block
                  icon={<UploadOutlined />}
                  loading={uploadingVoice}
                  disabled={!supportsLocalUpload}
                >
                  从设备上传录音
                </Button>
              </Upload>
              <Alert
                type="info"
                showIcon
                message="当前是录音驱动，不是声音克隆"
                description="请上传已经念完整段文案的音频；选择本人录音驱动版后，视频会按这段录音生成。"
              />
            </Space>
          </Card>
        </Col>
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
                  placeholder="输入数字人要说的内容，数字人会按文案自然播完。"
                  style={{ marginTop: 8 }}
                />
                {sourceTaskId && (
                  <Text type="secondary" style={{ display: "block", marginTop: 6 }}>
                    已带入人工确认后的 LLM 口播稿，来源任务：{sourceTaskId}
                  </Text>
                )}
              </div>

              <div>
                <Text strong>生成方案</Text>
                <Select
                  value={selectedProfile?.profile_id}
                  onChange={setProfileId}
                  style={{ width: "100%", marginTop: 8 }}
                  options={profiles.map((item) => ({
                    value: item.profile_id,
                    disabled: !item.enabled,
                    label: `${item.display_name}${item.required_vram_gb ? ` · ${item.required_vram_gb}GB 显存` : ""}`,
                  }))}
                />
                {selectedProfile && (
                  <Text type="secondary" style={{ display: "block", marginTop: 6 }}>
                    {selectedProfile.description}
                    {profileStatusText ? ` ${profileStatusText}` : ""}
                  </Text>
                )}
                {selectedRecordedProfile && (
                  <Alert
                    style={{ marginTop: 8 }}
                    type="info"
                    showIcon
                    message="录音驱动模式"
                    description="文案只用于任务记录和后续发布包，最终口播内容以你上传的完整录音为准。"
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
                    {estimatedCostText}
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
                disabled={
                  serviceUnavailable ||
                  selectedProfileUnavailable ||
                  !selectedAvatar?.authorized ||
                  !selectedVoice?.authorized
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
          </div>

          <Card title={capability?.mode === "sandbox" ? "演示任务历史" : "真实任务历史"}>
            {jobs.length ? (
              <List
                dataSource={jobs}
                renderItem={(item) => (
                  <List.Item style={{ display: "block" }}>
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
                        <Button type="link" onClick={() => void handleViewJob(item.task_id)}>
                          查看
                        </Button>
                        {item.result_url && (
                          <Button type="link" icon={<DownloadOutlined />} onClick={() => handleDownload(item)}>
                            下载
                          </Button>
                        )}
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
    </div>
  );
}
