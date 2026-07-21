import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Empty,
  Input,
  Row,
  Segmented,
  Select,
  Slider,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  BankOutlined,
  CopyOutlined,
  EditOutlined,
  FileTextOutlined,
  HeartOutlined,
  ReloadOutlined,
  SmileOutlined,
  StarOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import {
  generateCopywriting,
  getCopywritingCapabilities,
  rewriteCopywriting,
} from "../api/client";
import type {
  CopywritingCapabilitiesResponse,
  CopywritingResponse,
} from "../api/types";
import { useToast } from "../components/Toast";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

const STYLE_PRESETS = [
  { key: "engaging", label: "吸引眼球", icon: <ThunderboltOutlined />, color: "orange", desc: "制造悬念、引发好奇" },
  { key: "professional", label: "专业权威", icon: <BankOutlined />, color: "blue", desc: "数据支撑、理性分析" },
  { key: "emotional", label: "情感共鸣", icon: <HeartOutlined />, color: "red", desc: "触动人心、引发共情" },
  { key: "humorous", label: "幽默风趣", icon: <SmileOutlined />, color: "green", desc: "轻松诙谐、趣味表达" },
  { key: "storytelling", label: "故事叙述", icon: <StarOutlined />, color: "purple", desc: "悬念铺垫、引人入胜" },
];

const STYLE_PROMPTS: Record<string, string> = {
  engaging: "吸引眼球，制造悬念，引发好奇",
  professional: "专业权威，数据支撑，理性分析",
  emotional: "情感共鸣，触动人心，引发共情",
  humorous: "幽默风趣，轻松诙谐，趣味表达",
  storytelling: "故事叙述，悬念铺垫，引人入胜",
};

const TONE_OPTIONS = [
  { value: "formal", label: "正式" },
  { value: "casual", label: "轻松" },
  { value: "energetic", label: "活力" },
  { value: "calm", label: "沉稳" },
  { value: "urgent", label: "紧迫" },
];

const PLATFORM_OPTIONS = [
  { value: "douyin", label: "抖音" },
  { value: "xiaohongshu", label: "小红书" },
  { value: "wechat_channels", label: "微信视频号" },
];

type CopyMode = "generate" | "rewrite";

export default function AiCopyPage() {
  const toast = useToast();
  const [mode, setMode] = useState<CopyMode>("rewrite");
  const [capability, setCapability] = useState<CopywritingCapabilitiesResponse | null>(null);
  const [capabilityError, setCapabilityError] = useState("");

  const [contentBrief, setContentBrief] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [targetAudience, setTargetAudience] = useState("");
  const [sellingPoints, setSellingPoints] = useState("");
  const [callToAction, setCallToAction] = useState("");
  const [platform, setPlatform] = useState("douyin");
  const [stylePreset, setStylePreset] = useState("engaging");
  const [tone, setTone] = useState("casual");
  const [targetLength, setTargetLength] = useState(200);
  const [variantCount, setVariantCount] = useState(3);

  const [loading, setLoading] = useState(false);
  const [variants, setVariants] = useState<string[]>([]);
  const [activeVariant, setActiveVariant] = useState(0);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [lastResponse, setLastResponse] = useState<CopywritingResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCopywritingCapabilities()
      .then((resp) => {
        if (!cancelled) {
          setCapability(resp);
          setCapabilityError("");
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setCapabilityError((err as Error).message || "读取模型能力失败");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedStyle = useMemo(
    () => STYLE_PRESETS.find((item) => item.key === stylePreset) ?? STYLE_PRESETS[0],
    [stylePreset],
  );
  const inputReady =
    mode === "generate" ? contentBrief.trim().length > 0 : sourceText.trim().length > 0;
  const enabled = capability?.enabled === true;

  const handleSubmit = useCallback(async () => {
    if (!inputReady) {
      toast.warning(mode === "generate" ? "请输入内容概要" : "请输入原始文案");
      return;
    }
    if (!enabled) {
      toast.error("AI 文案模型未配置，无法真实生成");
      return;
    }
    setLoading(true);
    setVariants([]);
    setLastResponse(null);
    try {
      const common = {
        platform,
        target_audience: targetAudience,
        style_prompt: STYLE_PROMPTS[stylePreset] || STYLE_PROMPTS.engaging,
        target_length: targetLength,
        tone,
        variant_count: variantCount,
      };
      const resp =
        mode === "generate"
          ? await generateCopywriting({
              ...common,
              content_brief: contentBrief,
              selling_points: sellingPoints,
              call_to_action: callToAction,
            })
          : await rewriteCopywriting({
              ...common,
              source_text: sourceText,
            });

      setTaskId(resp.task_id);
      setLastResponse(resp);
      if (resp.status !== "succeeded") {
        toast.error(resp.error_message || "文案生成失败");
        return;
      }
      const resultVariants =
        resp.result_variants.length > 0
          ? resp.result_variants
          : resp.result_text
            ? [resp.result_text]
            : [];
      if (resultVariants.length === 0) {
        toast.warning("后端未返回有效文案");
        return;
      }
      setVariants(resultVariants);
      setActiveVariant(0);
      toast.success(`已生成 ${resultVariants.length} 个文案变体`);
    } catch (err) {
      toast.error((err as Error).message || "文案生成失败");
    } finally {
      setLoading(false);
    }
  }, [
    callToAction,
    contentBrief,
    enabled,
    inputReady,
    mode,
    platform,
    sellingPoints,
    sourceText,
    stylePreset,
    targetAudience,
    targetLength,
    tone,
    toast,
    variantCount,
  ]);

  const handleCopy = useCallback((text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      toast.success("已复制到剪贴板");
    });
  }, [toast]);

  const tokenUsage = lastResponse?.token_usage ?? {};
  const disabledMessage = capabilityError
    ? capabilityError
    : capability && !capability.enabled
      ? `未配置 ${capability.missing_configuration.join("、") || "模型密钥"}，请在本机私密配置中设置后重启后端。`
      : "";

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          <EditOutlined /> AI 文案生成
        </Title>
        <Text type="secondary">
          真实大模型生成短视频口播文案，支持需求生成与原文改写
        </Text>
      </div>

      {disabledMessage && (
        <Alert
          type={capabilityError ? "error" : "warning"}
          message={disabledMessage}
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      <Row gutter={[24, 24]}>
        <Col xs={24} lg={10}>
          <Card title={<Space><FileTextOutlined /> 文案输入</Space>} style={{ height: "100%" }}>
            <Space direction="vertical" style={{ width: "100%" }} size={16}>
              <Segmented
                block
                value={mode}
                onChange={(value) => setMode(value as CopyMode)}
                options={[
                  { label: "改写已有文案", value: "rewrite" },
                  { label: "从需求生成", value: "generate" },
                ]}
              />

              {mode === "generate" ? (
                <>
                  <div>
                    <Text strong style={{ display: "block", marginBottom: 8 }}>内容概要</Text>
                    <TextArea
                      placeholder="例如：面向中小企业老板，介绍 AI 短视频获客系统如何降低内容生产成本..."
                      rows={5}
                      value={contentBrief}
                      onChange={(e) => setContentBrief(e.target.value)}
                      showCount
                      maxLength={5000}
                      style={{ resize: "none" }}
                    />
                  </div>
                  <div>
                    <Text strong style={{ display: "block", marginBottom: 8 }}>核心卖点</Text>
                    <TextArea
                      placeholder="输入产品亮点、服务优势或确定可说的事实"
                      rows={3}
                      value={sellingPoints}
                      onChange={(e) => setSellingPoints(e.target.value)}
                      maxLength={1200}
                      style={{ resize: "none" }}
                    />
                  </div>
                  <div>
                    <Text strong style={{ display: "block", marginBottom: 8 }}>行动号召</Text>
                    <Input
                      placeholder="例如：私信领取行业案例清单"
                      value={callToAction}
                      onChange={(e) => setCallToAction(e.target.value)}
                      maxLength={120}
                    />
                  </div>
                </>
              ) : (
                <div>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>原始文案</Text>
                  <TextArea
                    placeholder="粘贴已有文案、脚本或口播稿..."
                    rows={8}
                    value={sourceText}
                    onChange={(e) => setSourceText(e.target.value)}
                    showCount
                    maxLength={5000}
                    style={{ resize: "none" }}
                  />
                </div>
              )}

              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>目标受众</Text>
                <Input
                  placeholder="例如：B2B 企业主、品牌市场负责人"
                  value={targetAudience}
                  onChange={(e) => setTargetAudience(e.target.value)}
                  maxLength={120}
                />
              </div>

              <Row gutter={12}>
                <Col span={12}>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>平台</Text>
                  <Select
                    value={platform}
                    onChange={setPlatform}
                    options={PLATFORM_OPTIONS}
                    style={{ width: "100%" }}
                  />
                </Col>
                <Col span={12}>
                  <Text strong style={{ display: "block", marginBottom: 8 }}>语调</Text>
                  <Select
                    value={tone}
                    onChange={setTone}
                    options={TONE_OPTIONS}
                    style={{ width: "100%" }}
                  />
                </Col>
              </Row>

              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>风格预设</Text>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {STYLE_PRESETS.map((preset) => (
                    <Tag
                      key={preset.key}
                      color={stylePreset === preset.key ? preset.color : undefined}
                      style={{
                        cursor: "pointer",
                        padding: "6px 12px",
                        fontSize: 13,
                        borderRadius: 6,
                        border: stylePreset === preset.key ? undefined : "1px solid var(--border-default)",
                      }}
                      onClick={() => setStylePreset(preset.key)}
                    >
                      {preset.icon} {preset.label}
                    </Tag>
                  ))}
                </div>
                <Text type="secondary" style={{ fontSize: 12, marginTop: 4, display: "block" }}>
                  {selectedStyle.desc}
                </Text>
              </div>

              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>目标字数：{targetLength} 字</Text>
                <Slider
                  min={50}
                  max={800}
                  step={50}
                  value={targetLength}
                  onChange={setTargetLength}
                  marks={{ 50: "50", 200: "200", 500: "500", 800: "800" }}
                />
              </div>

              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>生成变体数</Text>
                <Select
                  value={variantCount}
                  onChange={setVariantCount}
                  style={{ width: "100%" }}
                  options={[
                    { value: 1, label: "1 个变体" },
                    { value: 2, label: "2 个变体" },
                    { value: 3, label: "3 个变体" },
                    { value: 5, label: "5 个变体" },
                  ]}
                />
              </div>

              <Button
                type="primary"
                icon={<EditOutlined />}
                size="large"
                block
                loading={loading}
                onClick={handleSubmit}
                disabled={!inputReady || !enabled}
              >
                {mode === "generate" ? "生成文案" : "改写文案"}
              </Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card
            title={
              <Space wrap>
                <EditOutlined /> 生成结果
                {taskId && <Tag color="blue">任务 {taskId}</Tag>}
                {lastResponse?.model_name && <Tag>{lastResponse.model_name}</Tag>}
              </Space>
            }
            extra={
              variants.length > 0 && (
                <Button icon={<ReloadOutlined />} size="small" onClick={handleSubmit} loading={loading}>
                  重新生成
                </Button>
              )
            }
            style={{ height: "100%" }}
          >
            <Spin spinning={loading} tip="AI 正在生成文案...">
              {lastResponse?.status === "failed" && (
                <Alert
                  type="error"
                  showIcon
                  message={lastResponse.error_message || "文案生成失败"}
                  style={{ marginBottom: 16 }}
                />
              )}

              {variants.length > 0 ? (
                <Space direction="vertical" style={{ width: "100%" }} size={16}>
                  {variants.length > 1 && (
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      {variants.map((_, idx) => (
                        <Tag
                          key={idx}
                          color={activeVariant === idx ? "purple" : undefined}
                          style={{
                            cursor: "pointer",
                            padding: "6px 16px",
                            fontSize: 13,
                            borderRadius: 6,
                            border: activeVariant === idx ? undefined : "1px solid var(--border-default)",
                          }}
                          onClick={() => setActiveVariant(idx)}
                        >
                          变体 {idx + 1}
                        </Tag>
                      ))}
                    </div>
                  )}

                  <div
                    style={{
                      background: "var(--gray-50)",
                      borderRadius: "var(--radius-md)",
                      padding: 20,
                      border: "1px solid var(--border-default)",
                      minHeight: 220,
                    }}
                  >
                    <Paragraph
                      style={{
                        fontSize: 15,
                        lineHeight: 1.8,
                        color: "var(--text-primary)",
                        margin: 0,
                        whiteSpace: "pre-wrap",
                      }}
                    >
                      {variants[activeVariant]}
                    </Paragraph>
                  </div>

                  <Space wrap>
                    <Button icon={<CopyOutlined />} onClick={() => handleCopy(variants[activeVariant])}>
                      复制当前变体
                    </Button>
                    <Button onClick={() => handleCopy(variants.join("\n\n---\n\n"))}>
                      复制全部变体
                    </Button>
                  </Space>

                  <Divider style={{ margin: "8px 0" }} />
                  <Space wrap size={[16, 8]}>
                    <Text type="secondary" style={{ fontSize: 12 }}>字数：{variants[activeVariant].length}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>变体数：{variants.length}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>供应商：{lastResponse?.provider_name || "-"}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>模型：{lastResponse?.model_name || "-"}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>Token：{tokenUsage.total_tokens ?? "-"}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>演示：{lastResponse?.is_mock ? "是" : "否"}</Text>
                  </Space>
                </Space>
              ) : (
                <Empty
                  description={enabled ? "填写输入后点击生成" : "模型未配置，暂不能生成真实文案"}
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                />
              )}
            </Spin>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
