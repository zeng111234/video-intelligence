/**
 * 数字人视频生成页面
 * 支持形象录制/上传、音频录制/上传、TTS、视频生成
 */
import { useState, useRef, useCallback } from "react";
import {
  Typography,
  Card,
  Button,
  Space,
  Row,
  Col,
  Tabs,
  Upload,
  Input,
  Select,
  Slider,
  Progress,
  Tag,
  Empty,
} from "antd";
import { useToast } from "../components/Toast";
import {
  VideoCameraOutlined,
  AudioOutlined,
  UploadOutlined,
  PlayCircleOutlined,
  StopOutlined,
  SoundOutlined,
  UserOutlined,
  RocketOutlined,
  DownloadOutlined,
  ReloadOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
} from "@ant-design/icons";

const { Title, Text } = Typography;
const { Option } = Select;
const { TextArea } = Input;

/** TTS 音色选项 */
const TTS_VOICES = [
  { label: "甜美女声", value: "sweet_female" },
  { label: "磁性男声", value: "magnetic_male" },
  { label: "活力青年", value: "youth" },
  { label: "专业播音", value: "broadcast" },
  { label: "亲切客服", value: "customer_service" },
];

/** 模拟生成历史 */
const GENERATION_HISTORY = [
  {
    id: "avatar-001",
    name: "产品介绍视频",
    status: "succeeded",
    duration: "0:45",
    createdAt: "2026-07-20 14:30",
  },
  {
    id: "avatar-002",
    name: "二手车测评口播",
    status: "succeeded",
    duration: "1:20",
    createdAt: "2026-07-20 10:15",
  },
  {
    id: "avatar-003",
    name: "新车对比讲解",
    status: "running",
    duration: "-",
    createdAt: "2026-07-20 16:00",
  },
];

export default function AvatarPage() {
  const toast = useToast();
  // 形象相关
  const [avatarImage, setAvatarImage] = useState<string | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);

  // 音频相关
  const [audioMode, setAudioMode] = useState<"record" | "upload" | "tts">("tts");
  const [ttsText, setTtsText] = useState("");
  const [ttsVoice, setTtsVoice] = useState("sweet_female");
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [isRecordingAudio, setIsRecordingAudio] = useState(false);

  // 生成相关
  const [generating, setGenerating] = useState(false);
  const [progress, setProgress] = useState(0);
  const [generatedVideo, setGeneratedVideo] = useState<string | null>(null);
  const [speechRate, setSpeechRate] = useState(1);

  /** 下载视频 */
  const handleDownloadVideo = useCallback(() => {
    if (!generatedVideo) {
      toast.warning("没有可下载的视频");
      return;
    }
    // 模拟下载
    const a = document.createElement("a");
    a.href = generatedVideo;
    a.download = `数字人视频_${new Date().toISOString().slice(0, 10)}.mp4`;
    a.click();
    toast.success("视频下载已开始");
  }, [generatedVideo, toast]);

  /** 下载历史视频 */
  const handleDownloadHistory = useCallback((item: any) => {
    toast.success(`正在下载: ${item.name}`);
  }, [toast]);

  /** 开始录制形象 */
  const startVideoRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 720, height: 1280, facingMode: "user" },
        audio: false,
      });
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
      setIsRecording(true);
      toast.info("开始录制形象，点击停止结束");
    } catch (err) {
      toast.error("无法访问摄像头，请检查权限");
    }
  }, []);

  /** 停止录制形象 */
  const stopVideoRecording = useCallback(() => {
    if (videoRef.current?.srcObject) {
      const stream = videoRef.current.srcObject as MediaStream;
      stream.getTracks().forEach((track) => track.stop());
    }
    setIsRecording(false);
    setAvatarImage("/avatar-placeholder.png"); // 模拟截图
    toast.success("形象录制完成");
  }, []);

  /** 开始录制音频 */
  const startAudioRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      mediaRecorder.start();
      setIsRecordingAudio(true);
      toast.info("开始录制音频，点击停止结束");
    } catch (err) {
      toast.error("无法访问麦克风，请检查权限");
    }
  }, []);

  /** 停止录制音频 */
  const stopAudioRecording = useCallback(() => {
    if (mediaRecorderRef.current) {
      mediaRecorderRef.current.stop();
      mediaRecorderRef.current.stream.getTracks().forEach((track) => track.stop());
    }
    setIsRecordingAudio(false);
    toast.success("音频录制完成");
  }, []);

  /** 开始生成视频 */
  const handleGenerate = useCallback(() => {
    if (!avatarImage && !audioFile && !ttsText) {
      toast.warning("请先录制/上传形象和音频");
      return;
    }
    setGenerating(true);
    setProgress(0);

    // 模拟生成过程
    const interval = setInterval(() => {
      setProgress((prev) => {
        if (prev >= 100) {
          clearInterval(interval);
          setGenerating(false);
          setGeneratedVideo("/generated-video.mp4");
          toast.success("数字人视频生成完成！");
          return 100;
        }
        return prev + 10;
      });
    }, 500);
  }, [avatarImage, audioFile, ttsText]);

  return (
    <div>
      {/* 页面头部 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          <UserOutlined /> 数字人视频生成
        </Title>
        <Text type="secondary">录制形象 + 音频/TTS → 生成数字人口播视频</Text>
      </div>

      <Row gutter={[24, 24]}>
        {/* 左侧：输入区 */}
        <Col xs={24} lg={12}>
          {/* 形象录制 */}
          <Card
            title={<Space><VideoCameraOutlined /> 数字人形象</Space>}
            style={{ marginBottom: 24 }}
          >
            <Tabs
              defaultActiveKey="record"
              items={[
                {
                  key: "record",
                  label: "摄像头录制",
                  children: (
                    <Space direction="vertical" style={{ width: "100%" }} size={16}>
                      <div
                        style={{
                          background: "#000",
                          borderRadius: 8,
                          overflow: "hidden",
                          aspectRatio: "9/16",
                          maxHeight: 300,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        {isRecording ? (
                          <video
                            ref={videoRef}
                            autoPlay
                            muted
                            playsInline
                            style={{ width: "100%", height: "100%", objectFit: "cover" }}
                          />
                        ) : avatarImage ? (
                          <div style={{ color: "#10b981", textAlign: "center" }}>
                            <CheckCircleOutlined style={{ fontSize: 48 }} />
                            <br />
                            <Text style={{ color: "#fff" }}>形象已录制</Text>
                          </div>
                        ) : (
                          <div style={{ color: "#666", textAlign: "center" }}>
                            <UserOutlined style={{ fontSize: 48 }} />
                            <br />
                            <Text style={{ color: "#999" }}>点击下方按钮开始录制</Text>
                          </div>
                        )}
                      </div>
                      <Button
                        type={isRecording ? "default" : "primary"}
                        danger={isRecording}
                        icon={isRecording ? <StopOutlined /> : <VideoCameraOutlined />}
                        block
                        size="large"
                        onClick={isRecording ? stopVideoRecording : startVideoRecording}
                      >
                        {isRecording ? "停止录制" : "开始录制形象"}
                      </Button>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        建议：正对摄像头，光线充足，面部清晰，录制 5-10 秒
                      </Text>
                    </Space>
                  ),
                },
                {
                  key: "upload",
                  label: "上传照片",
                  children: (
                    <Space direction="vertical" style={{ width: "100%" }} size={16}>
                      <Upload.Dragger
                        accept="image/*"
                        showUploadList={false}
                        beforeUpload={(file) => {
                          const reader = new FileReader();
                          reader.onload = (e) => {
                            setAvatarImage(e.target?.result as string);
                            toast.success("形象照片已上传");
                          };
                          reader.readAsDataURL(file);
                          return false;
                        }}
                        style={{ padding: "30px 0" }}
                      >
                        {avatarImage ? (
                          <img
                            src={avatarImage}
                            alt="avatar"
                            style={{ maxHeight: 200, borderRadius: 8 }}
                          />
                        ) : (
                          <>
                            <p style={{ marginBottom: 8 }}>
                              <UploadOutlined style={{ fontSize: 32, color: "#6366f1" }} />
                            </p>
                            <p>点击或拖拽照片到此区域</p>
                            <p style={{ color: "#94a3b8", fontSize: 12 }}>
                              支持 JPG、PNG 格式，建议正面免冠照
                            </p>
                          </>
                        )}
                      </Upload.Dragger>
                    </Space>
                  ),
                },
              ]}
            />
          </Card>

          {/* 音频输入 */}
          <Card title={<Space><AudioOutlined /> 音频内容</Space>}>
            <Tabs
              activeKey={audioMode}
              onChange={(key) => setAudioMode(key as any)}
              items={[
                {
                  key: "tts",
                  label: "文字转语音",
                  children: (
                    <Space direction="vertical" style={{ width: "100%" }} size={16}>
                      <div>
                        <Text strong style={{ display: "block", marginBottom: 8 }}>
                          输入文案
                        </Text>
                        <TextArea
                          placeholder="输入数字人要说的内容..."
                          rows={4}
                          value={ttsText}
                          onChange={(e) => setTtsText(e.target.value)}
                          showCount
                          maxLength={500}
                        />
                      </div>
                      <div>
                        <Text strong style={{ display: "block", marginBottom: 8 }}>
                          选择音色
                        </Text>
                        <Select
                          value={ttsVoice}
                          onChange={setTtsVoice}
                          style={{ width: "100%" }}
                        >
                          {TTS_VOICES.map((v) => (
                            <Option key={v.value} value={v.value}>
                              {v.label}
                            </Option>
                          ))}
                        </Select>
                      </div>
                      <div>
                        <Text strong style={{ display: "block", marginBottom: 8 }}>
                          语速: {speechRate}x
                        </Text>
                        <Slider value={speechRate} onChange={setSpeechRate} min={0.5} max={2} step={0.1} />
                      </div>
                    </Space>
                  ),
                },
                {
                  key: "record",
                  label: "录制音频",
                  children: (
                    <Space direction="vertical" style={{ width: "100%" }} size={16}>
                      <div
                        style={{
                          background: "#f8fafc",
                          borderRadius: 8,
                          padding: 40,
                          textAlign: "center",
                        }}
                      >
                        <SoundOutlined
                          style={{
                            fontSize: 48,
                            color: isRecordingAudio ? "#ef4444" : "#6366f1",
                          }}
                        />
                        <br />
                        <Text type="secondary">
                          {isRecordingAudio ? "正在录制..." : "点击下方按钮开始录制"}
                        </Text>
                      </div>
                      <Button
                        type={isRecordingAudio ? "default" : "primary"}
                        danger={isRecordingAudio}
                        icon={isRecordingAudio ? <StopOutlined /> : <AudioOutlined />}
                        block
                        size="large"
                        onClick={isRecordingAudio ? stopAudioRecording : startAudioRecording}
                      >
                        {isRecordingAudio ? "停止录制" : "开始录制音频"}
                      </Button>
                    </Space>
                  ),
                },
                {
                  key: "upload",
                  label: "上传音频",
                  children: (
                    <Upload.Dragger
                      accept="audio/*"
                      showUploadList={false}
                      beforeUpload={(file) => {
                        setAudioFile(file);
                        toast.success(`音频 "${file.name}" 已添加`);
                        return false;
                      }}
                      style={{ padding: "30px 0" }}
                    >
                      <p style={{ marginBottom: 8 }}>
                        <UploadOutlined style={{ fontSize: 32, color: "#6366f1" }} />
                      </p>
                      <p>点击或拖拽音频文件到此区域</p>
                      <p style={{ color: "#94a3b8", fontSize: 12 }}>
                        支持 MP3、WAV、M4A 格式
                      </p>
                    </Upload.Dragger>
                  ),
                },
              ]}
            />
          </Card>
        </Col>

        {/* 右侧：预览和生成 */}
        <Col xs={24} lg={12}>
          {/* 生成控制 */}
          <Card
            title={<Space><RocketOutlined /> 视频生成</Space>}
            style={{ marginBottom: 24 }}
          >
            <Space direction="vertical" style={{ width: "100%" }} size={16}>
              {/* 状态展示 */}
              <Row gutter={16}>
                <Col span={8}>
                  <Card size="small" style={{ textAlign: "center" }}>
                    <UserOutlined style={{ fontSize: 24, color: avatarImage ? "#10b981" : "#94a3b8" }} />
                    <br />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      形象 {avatarImage ? "✓" : "未设置"}
                    </Text>
                  </Card>
                </Col>
                <Col span={8}>
                  <Card size="small" style={{ textAlign: "center" }}>
                    <SoundOutlined
                      style={{
                        fontSize: 24,
                        color: audioFile || ttsText ? "#10b981" : "#94a3b8",
                      }}
                    />
                    <br />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      音频 {audioFile || ttsText ? "✓" : "未设置"}
                    </Text>
                  </Card>
                </Col>
                <Col span={8}>
                  <Card size="small" style={{ textAlign: "center" }}>
                    <VideoCameraOutlined
                      style={{
                        fontSize: 24,
                        color: generatedVideo ? "#10b981" : "#94a3b8",
                      }}
                    />
                    <br />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      视频 {generatedVideo ? "✓" : "待生成"}
                    </Text>
                  </Card>
                </Col>
              </Row>

              {/* 生成进度 */}
              {generating && (
                <div>
                  <Text strong style={{ marginBottom: 8, display: "block" }}>
                    生成进度
                  </Text>
                  <Progress
                    percent={progress}
                    status="active"
                    strokeColor={{ from: "#6366f1", to: "#10b981" }}
                  />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {progress < 30
                      ? "正在分析形象..."
                      : progress < 60
                        ? "正在合成音频..."
                        : progress < 90
                          ? "正在生成视频..."
                          : "即将完成..."}
                  </Text>
                </div>
              )}

              {/* 生成按钮 */}
              <Button
                type="primary"
                icon={<RocketOutlined />}
                size="large"
                block
                loading={generating}
                onClick={handleGenerate}
                disabled={!avatarImage || (!audioFile && !ttsText)}
              >
                {generating ? "生成中..." : "开始生成数字人视频"}
              </Button>
            </Space>
          </Card>

          {/* 预览区 */}
          <Card title={<Space><PlayCircleOutlined /> 视频预览</Space>}>
            {generatedVideo ? (
              <Space direction="vertical" style={{ width: "100%" }} size={16}>
                <div
                  style={{
                    background: "#000",
                    borderRadius: 8,
                    overflow: "hidden",
                    aspectRatio: "9/16",
                    maxHeight: 400,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <div style={{ color: "#10b981", textAlign: "center" }}>
                    <CheckCircleOutlined style={{ fontSize: 64 }} />
                    <br />
                    <Text style={{ color: "#fff", fontSize: 16 }}>视频生成完成</Text>
                  </div>
                </div>
                <Space>
                  <Button type="primary" icon={<DownloadOutlined />} onClick={handleDownloadVideo}>
                    下载视频
                  </Button>
                  <Button icon={<ReloadOutlined />} onClick={() => setGeneratedVideo(null)}>
                    重新生成
                  </Button>
                </Space>
              </Space>
            ) : (
              <Empty description="生成视频后在此预览" />
            )}
          </Card>

          {/* 生成历史 */}
          <Card
            title={<Space><ClockCircleOutlined /> 生成历史</Space>}
            style={{ marginTop: 24 }}
          >
            {GENERATION_HISTORY.map((item) => (
              <div
                key={item.id}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "12px 0",
                  borderBottom: "1px solid #f0f0f0",
                }}
              >
                <div>
                  <Text strong>{item.name}</Text>
                  <br />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {item.createdAt} · 时长 {item.duration}
                  </Text>
                </div>
                <Space>
                  <Tag color={item.status === "succeeded" ? "success" : "processing"}>
                    {item.status === "succeeded" ? "已完成" : "生成中"}
                  </Tag>
                  {item.status === "succeeded" && (
                    <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => handleDownloadHistory(item)}>
                      下载
                    </Button>
                  )}
                </Space>
              </div>
            ))}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
