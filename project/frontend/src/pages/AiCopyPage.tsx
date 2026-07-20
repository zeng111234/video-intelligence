/**
 * AI 文案生成页面（Phase 3）
 * 接入后端 /api/v1/copywriting/rewrite 接口
 * 支持多风格、多变体生成、实时预览
 */
import { useState, useCallback } from "react";
import {
  Typography,
  Card,
  Input,
  Button,
  Space,
  Select,
  Row,
  Col,
  Tag,
  Divider,
  Slider,
  Spin,
  Empty,
} from "antd";
import {
  EditOutlined,
  CopyOutlined,
  ReloadOutlined,
  FileTextOutlined,
  ThunderboltOutlined,
  HeartOutlined,
  BankOutlined,
  SmileOutlined,
  StarOutlined,
} from "@ant-design/icons";
import { rewriteCopywriting } from "../api/client";
import { useToast } from "../components/Toast";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

/** 风格预设 */
const STYLE_PRESETS = [
  { key: "engaging", label: "吸引眼球", icon: <ThunderboltOutlined />, color: "orange", desc: "制造悬念、引发好奇" },
  { key: "professional", label: "专业权威", icon: <BankOutlined />, color: "blue", desc: "数据支撑、理性分析" },
  { key: "emotional", label: "情感共鸣", icon: <HeartOutlined />, color: "red", desc: "触动人心、引发共情" },
  { key: "humorous", label: "幽默风趣", icon: <SmileOutlined />, color: "green", desc: "轻松诙谐、趣味表达" },
  { key: "storytelling", label: "故事叙述", icon: <StarOutlined />, color: "purple", desc: "悬念铺垫、引人入胜" },
];

/** 语调选项 */
const TONE_OPTIONS = [
  { value: "formal", label: "正式" },
  { value: "casual", label: "轻松" },
  { value: "energetic", label: "活力" },
  { value: "calm", label: "沉稳" },
  { value: "urgent", label: "紧迫" },
];

export default function AiCopyPage() {
  const toast = useToast();

  /* ---- 状态 ---- */
  const [sourceText, setSourceText] = useState("");
  const [stylePreset, setStylePreset] = useState("engaging");
  const [tone, setTone] = useState("casual");
  const [targetLength, setTargetLength] = useState(200);
  const [variantCount, setVariantCount] = useState(3);
  const [loading, setLoading] = useState(false);
  const [variants, setVariants] = useState<string[]>([]);
  const [activeVariant, setActiveVariant] = useState(0);
  const [taskId, setTaskId] = useState<string | null>(null);

  /* ---- 生成文案 ---- */
  const handleGenerate = useCallback(async () => {
    if (!sourceText.trim()) {
      toast.warning("请输入原始文案内容");
      return;
    }
    setLoading(true);
    setVariants([]);
    try {
      const styleMap: Record<string, string> = {
        engaging: "吸引眼球，制造悬念，引发好奇",
        professional: "专业权威，数据支撑，理性分析",
        emotional: "情感共鸣，触动人心，引发共情",
        humorous: "幽默风趣，轻松诙谐，趣味表达",
        storytelling: "故事叙述，悬念铺垫，引人入胜",
      };
      const resp = await rewriteCopywriting({
        source_text: sourceText,
        style_prompt: styleMap[stylePreset] || styleMap.engaging,
        target_length: targetLength,
        tone,
        variant_count: variantCount,
      });

      if (resp.result_variants && resp.result_variants.length > 0) {
        setVariants(resp.result_variants);
        setActiveVariant(0);
        setTaskId(resp.task_id);
        toast.success(`已生成 ${resp.result_variants.length} 个文案变体`);
      } else if (resp.result_text) {
        setVariants([resp.result_text]);
        setActiveVariant(0);
        setTaskId(resp.task_id);
        toast.success("文案生成成功");
      } else {
        toast.warning("后端未返回有效文案，请检查输入内容");
      }
    } catch (err) {
      toast.error((err as Error).message || "文案生成失败");
    } finally {
      setLoading(false);
    }
  }, [sourceText, stylePreset, tone, targetLength, variantCount, toast]);

  /* ---- 复制文案 ---- */
  const handleCopy = useCallback((text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      toast.success("已复制到剪贴板");
    });
  }, [toast]);

  return (
    <div>
      {/* 页面头部 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          <EditOutlined /> AI 文案生成
        </Title>
        <Text type="secondary">
          输入原始文案，AI 将根据风格偏好智能改写为多版本短视频文案
        </Text>
      </div>

      <Row gutter={[24, 24]}>
        {/* 左侧：输入区 */}
        <Col xs={24} lg={10}>
          <Card
            title={
              <Space>
                <FileTextOutlined /> 原始文案
              </Space>
            }
            style={{ height: "100%" }}
          >
            <Space direction="vertical" style={{ width: "100%" }} size={16}>
              {/* 文案输入 */}
              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>
                  原始内容
                </Text>
                <TextArea
                  placeholder="粘贴你的原始文案、脚本或内容概要..."
                  rows={6}
                  value={sourceText}
                  onChange={(e) => setSourceText(e.target.value)}
                  style={{ resize: "none" }}
                  showCount
                  maxLength={5000}
                />
              </div>

              {/* 风格预设 */}
              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>
                  风格预设
                </Text>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {STYLE_PRESETS.map((preset) => (
                    <Tag
                      key={preset.key}
                      color={stylePreset === preset.key ? preset.color : undefined}
                      style={{
                        cursor: "pointer",
                        padding: "6px 12px",
                        fontSize: 13,
                        borderRadius: 8,
                        border: stylePreset === preset.key ? undefined : "1px solid var(--border-default)",
                      }}
                      onClick={() => setStylePreset(preset.key)}
                    >
                      {preset.icon} {preset.label}
                    </Tag>
                  ))}
                </div>
                <Text
                  type="secondary"
                  style={{ fontSize: 12, marginTop: 4, display: "block" }}
                >
                  {STYLE_PRESETS.find((p) => p.key === stylePreset)?.desc}
                </Text>
              </div>

              {/* 语调选择 */}
              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>
                  语调风格
                </Text>
                <Select
                  value={tone}
                  onChange={setTone}
                  style={{ width: "100%" }}
                  options={TONE_OPTIONS}
                />
              </div>

              {/* 目标长度 */}
              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>
                  目标字数：{targetLength} 字
                </Text>
                <Slider
                  min={50}
                  max={800}
                  step={50}
                  value={targetLength}
                  onChange={setTargetLength}
                  marks={{ 50: "50", 200: "200", 500: "500", 800: "800" }}
                />
              </div>

              {/* 变体数量 */}
              <div>
                <Text strong style={{ display: "block", marginBottom: 8 }}>
                  生成变体数
                </Text>
                <Select
                  value={variantCount}
                  onChange={setVariantCount}
                  style={{ width: "100%" }}
                  options={[
                    { value: 1, label: "1 个变体" },
                    { value: 2, label: "2 个变体" },
                    { value: 3, label: "3 个变体（推荐）" },
                    { value: 5, label: "5 个变体" },
                  ]}
                />
              </div>

              {/* 生成按钮 */}
              <Button
                type="primary"
                icon={<EditOutlined />}
                size="large"
                block
                loading={loading}
                onClick={handleGenerate}
                disabled={!sourceText.trim()}
              >
                AI 智能改写
              </Button>
            </Space>
          </Card>
        </Col>

        {/* 右侧：结果区 */}
        <Col xs={24} lg={14}>
          <Card
            title={
              <Space>
                <EditOutlined /> 生成结果
                {taskId && (
                  <Tag color="blue" style={{ fontSize: 11 }}>
                    任务 {taskId}
                  </Tag>
                )}
              </Space>
            }
            extra={
              variants.length > 0 && (
                <Button
                  icon={<ReloadOutlined />}
                  size="small"
                  onClick={handleGenerate}
                  loading={loading}
                >
                  重新生成
                </Button>
              )
            }
            style={{ height: "100%" }}
          >
            <Spin spinning={loading} tip="AI 正在生成文案...">
              {variants.length > 0 ? (
                <Space direction="vertical" style={{ width: "100%" }} size={16}>
                  {/* 变体切换标签 */}
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
                            borderRadius: 8,
                            border: activeVariant === idx ? undefined : "1px solid var(--border-default)",
                          }}
                          onClick={() => setActiveVariant(idx)}
                        >
                          变体 {idx + 1}
                        </Tag>
                      ))}
                    </div>
                  )}

                  {/* 文案内容 */}
                  <div
                    style={{
                      background: "var(--gray-50)",
                      borderRadius: "var(--radius-md)",
                      padding: 20,
                      border: "1px solid var(--border-default)",
                      minHeight: 200,
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

                  {/* 操作按钮 */}
                  <Space>
                    <Button
                      icon={<CopyOutlined />}
                      onClick={() => handleCopy(variants[activeVariant])}
                    >
                      复制当前变体
                    </Button>
                    <Button
                      onClick={() =>
                        handleCopy(variants.join("\n\n---\n\n"))
                      }
                    >
                      复制全部变体
                    </Button>
                  </Space>

                  {/* 统计信息 */}
                  <Divider style={{ margin: "8px 0" }} />
                  <div style={{ display: "flex", gap: 24 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      字数：{variants[activeVariant].length}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      变体数：{variants.length}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      风格：{STYLE_PRESETS.find((p) => p.key === stylePreset)?.label}
                    </Text>
                  </div>
                </Space>
              ) : (
                <Empty
                  description="输入原始文案并点击「AI 智能改写」开始生成"
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
