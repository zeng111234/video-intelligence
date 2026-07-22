/**
 * 字幕生成入口。
 *
 * 识别、人工复核与导出统一复用转写工作流，确保字幕不会绕过低置信片段复核。
 */
import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Checkbox, Col, Empty, Input, Row, Select, Space, Tag, Typography, Upload, message } from "antd";
import { AudioOutlined, CheckCircleOutlined, GlobalOutlined, RocketOutlined, UploadOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { getSubtitleStatus, uploadAndTranscribe } from "../api/client";
import type { SubtitleStatusResponse } from "../api/types";

const { Text, Title } = Typography;

export default function SubtitlePage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<SubtitleStatusResponse | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [modelName, setModelName] = useState("large-v3-turbo");
  const [language, setLanguage] = useState("zh");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [rightsHolder, setRightsHolder] = useState("本人/公司已授权");

  const loadStatus = useCallback(async () => {
    setLoadingStatus(true);
    try {
      setStatus(await getSubtitleStatus());
    } catch (err) {
      message.error((err as Error).message || "获取字幕能力失败");
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const upload = useCallback(async (file: File) => {
    if (!rightsConfirmed) {
      message.warning("请先确认拥有该媒体的处理权");
      return;
    }
    if (!rightsHolder.trim()) {
      message.warning("请填写权利主体");
      return;
    }
    setSubmitting(true);
    try {
      const task = await uploadAndTranscribe(
        file,
        modelName,
        rightsHolder.trim(),
        language,
      );
      message.success("识别完成，请复核字幕后确认成稿并导出");
      navigate(`/transcription?task=${encodeURIComponent(task.task_id)}`);
    } catch (err) {
      message.error((err as Error).message || "字幕识别失败");
    } finally {
      setSubmitting(false);
    }
  }, [language, modelName, navigate, rightsConfirmed, rightsHolder]);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Title level={4} style={{ margin: 0 }}>
          <AudioOutlined style={{ marginRight: 8, color: "var(--primary-600)" }} />
          字幕生成
        </Title>
        <Text type="secondary">上传已授权视频，完成真实识别后进入校对工作区；低置信片段复核后可导出 SRT 或 ASS。</Text>
      </div>

      <Card size="small" title={<Space><GlobalOutlined /> 本地识别能力</Space>} extra={<Button size="small" onClick={loadStatus} loading={loadingStatus}>刷新</Button>}>
        {status ? (
          <Space direction="vertical" size="small" style={{ width: "100%" }}>
            <Space wrap>
              <Text strong>可用性：</Text>
              <Tag color={status.whisper_available ? "success" : "error"}>{status.whisper_available ? "可用" : "不可用"}</Tag>
              <Tag>{status.provider_name}</Tag>
              {status.version && <Tag color="blue">v{status.version}</Tag>}
              <Tag color={status.ffmpeg_available ? "success" : "error"}>FFmpeg {status.ffmpeg_available ? "可用" : "不可用"}</Tag>
            </Space>
            <Text type="secondary">默认模型：{status.default_model}；支持：{status.supported_models.join("、")}；导出：{status.supported_formats.join("、").toUpperCase()}</Text>
            {!status.whisper_available && <Alert type="warning" showIcon message={status.reason || "本地字幕能力不可用"} description={status.install_command ? <Text code>{status.install_command}</Text> : undefined} />}
          </Space>
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="正在加载能力状态" />}
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card size="small" title={<Space><UploadOutlined /> 上传并识别</Space>}>
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Alert type="info" showIcon message="仅支持已授权的 MP4/MOV，单个文件不超过 50MB、时长不超过 15 分钟。" />
              <Row gutter={[12, 12]}>
                <Col xs={24} md={12}>
                  <Text type="secondary">识别模式</Text>
                  <Select value={modelName} onChange={setModelName} style={{ width: "100%", marginTop: 4 }} options={[
                    { value: "large-v3-turbo", label: "准确优先 · large-v3-turbo" },
                    { value: "base", label: "快速预览 · base" },
                  ]} />
                </Col>
                <Col xs={24} md={12}>
                  <Text type="secondary">语言</Text>
                  <Select value={language} onChange={setLanguage} style={{ width: "100%", marginTop: 4 }} options={[
                    { value: "zh", label: "中文" },
                    { value: "en", label: "英文" },
                    { value: "ja", label: "日文" },
                    { value: "ko", label: "韩文" },
                    { value: "auto", label: "自动检测" },
                  ]} />
                </Col>
              </Row>
              <Input value={rightsHolder} onChange={(event) => setRightsHolder(event.target.value)} addonBefore="权利主体" maxLength={100} />
              <Checkbox checked={rightsConfirmed} onChange={(event) => setRightsConfirmed(event.target.checked)}>我确认拥有该文件的处理权，允许本地识别。</Checkbox>
              <Upload.Dragger
                accept=".mp4,.mov"
                beforeUpload={(file) => { void upload(file); return false; }}
                multiple={false}
                showUploadList={false}
                disabled={!status?.whisper_available || submitting}
              >
                <p><UploadOutlined style={{ fontSize: 28 }} /></p>
                <p>点击或拖拽 MP4/MOV 文件上传</p>
                <Text type="secondary">上传后会进入字幕校对，不会直接把未复核结果当作成稿。</Text>
              </Upload.Dragger>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card size="small" title={<Space><CheckCircleOutlined /> 后续流程</Space>}>
            <Space direction="vertical" size="middle">
              <Text>1. 本地 faster-whisper 识别并生成时间轴片段。</Text>
              <Text>2. 在“语音转写”工作区编辑文本并复核低置信片段。</Text>
              <Text>3. 确认成稿后下载 SRT 或 ASS 字幕。</Text>
              <Button type="primary" icon={<RocketOutlined />} disabled={!status?.whisper_available} onClick={() => message.info("请先确认权利信息，再选择文件上传。")}>开始生成字幕</Button>
            </Space>
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
