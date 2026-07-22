/**
 * AI 智能剪辑页面
 * 支持配置完整 VideoEditConfig（含 AI 步骤），提交到后端执行。
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Empty,
  Input,
  InputNumber,
  List,
  Modal,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  AudioOutlined,
  CheckCircleOutlined,
  CrownOutlined,
  DeleteOutlined,
  DownOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  GlobalOutlined,
  HighlightOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  RocketOutlined,
  ScissorOutlined,
  SettingOutlined,
  SoundOutlined,
  StarOutlined,
  ThunderboltOutlined,
  UpOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import {
  createTemplate,
  deleteTemplate,
  editVideo,
  getVideoCapabilities,
  getVideoStepKinds,
  listTemplates,
} from "../api/client";
import type {
  EditTemplate,
  StepKindsResponse,
  StepKindInfo,
  VideoCapabilitiesResponse,
  VideoEditResponse,
  VideoEditStep,
} from "../api/types";
import { useToast } from "../components/Toast";

const { Text, Title } = Typography;
const { Option } = Select;

/* ---- 步骤卡片图标与颜色 ---- */

const STEP_ICONS: Record<string, ReactNode> = {
  trim: <ScissorOutlined />,
  subtitle: <FileTextOutlined />,
  watermark: <VideoCameraOutlined />,
  speed: <ThunderboltOutlined />,
  resize: <SettingOutlined />,
  filter: <ExperimentOutlined />,
  background_music: <AudioOutlined />,
  concat: <VideoCameraOutlined />,
  ai_subtitle: <RobotOutlined />,
  ai_volume_norm: <AudioOutlined />,
  ai_enhance: <VideoCameraOutlined />,
  ai_silence_trim: <ScissorOutlined />,
};

const AI_STEP_COLORS: Record<string, string> = {
  ai_subtitle: "purple",
  ai_volume_norm: "blue",
  ai_enhance: "cyan",
  ai_silence_trim: "geekblue",
};

function stepColor(kind: string): string {
  if (kind.startsWith("ai_")) return AI_STEP_COLORS[kind] || "purple";
  return "default";
}

/* ---- 主组件 ---- */

export default function VideoEditorPage() {
  const toast = useToast();

  /* 状态 */
  const [capabilities, setCapabilities] = useState<VideoCapabilitiesResponse | null>(null);
  const [stepKinds, setStepKinds] = useState<StepKindsResponse>({});
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  /* 编辑配置 */
  const [sourcePath, setSourcePath] = useState("");
  const [steps, setSteps] = useState<VideoEditStep[]>([]);
  const [outputFormat, setOutputFormat] = useState("mp4");
  const [outputResolution, setOutputResolution] = useState("1080x1920");
  const [outputFps, setOutputFps] = useState(30);
  const [outputBitrate, setOutputBitrate] = useState("4M");

  /* 添加步骤弹窗 */
  const [addModalOpen, setAddModalOpen] = useState(false);

  /* 结果 */
  const [result, setResult] = useState<VideoEditResponse | null>(null);

  /* 模板系统 */
  const [templates, setTemplates] = useState<EditTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [templateCategory, setTemplateCategory] = useState<string>("all");
  const [createTemplateOpen, setCreateTemplateOpen] = useState(false);
  const [newTemplateName, setNewTemplateName] = useState("");
  const [newTemplateDesc, setNewTemplateDesc] = useState("");
  const [creatingTemplate, setCreatingTemplate] = useState(false);

  /* 加载模板列表 */
  const loadTemplates = useCallback(async (category?: string) => {
    setTemplatesLoading(true);
    try {
      const cat = category && category !== "all" ? category : undefined;
      const resp = await listTemplates(cat);
      setTemplates(resp.items);
      setTemplateError(null);
    } catch (err) {
      setTemplateError((err as Error).message || "加载模板失败");
    } finally {
      setTemplatesLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

  /* 应用模板 —— 填充步骤列表 + 输出设置 */
  const applyTemplateToList = useCallback(
    (tpl: EditTemplate) => {
      const mappedSteps: VideoEditStep[] = tpl.steps.map((s, i) => ({
        step_id: `step-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        kind: s.kind,
        params: s.params || {},
        order: i,
      }));
      setSteps(mappedSteps);
      if (tpl.output_format) setOutputFormat(tpl.output_format);
      if (tpl.output_resolution) setOutputResolution(tpl.output_resolution);
      if (tpl.output_fps) setOutputFps(tpl.output_fps);
      if (tpl.output_bitrate) setOutputBitrate(tpl.output_bitrate);
      message.success(`已应用模板「${tpl.name}」`);
    },
    [],
  );

  /* 删除自定义模板 */
  const handleDeleteTemplate = useCallback(
    async (templateId: string, e: React.MouseEvent) => {
      e.stopPropagation();
      try {
        await deleteTemplate(templateId);
        message.success("模板已删除");
        loadTemplates(templateCategory !== "all" ? templateCategory : undefined);
      } catch (err) {
        message.error((err as Error).message || "删除失败");
      }
    },
    [loadTemplates, templateCategory],
  );

  /* 创建自定义模板（从当前步骤） */
  const handleCreateTemplate = useCallback(async () => {
    if (!newTemplateName.trim()) {
      message.warning("请输入模板名称");
      return;
    }
    setCreatingTemplate(true);
    try {
      await createTemplate({
        name: newTemplateName.trim(),
        description: newTemplateDesc.trim() || undefined,
        category: "custom",
        steps: steps.map((s) => ({
          kind: s.kind,
          params: s.params,
          label: stepKinds[s.kind]?.label || s.kind,
        })),
        output_format: outputFormat,
        output_resolution: outputResolution,
        output_fps: outputFps,
        output_bitrate: outputBitrate,
      });
      message.success("模板创建成功");
      setCreateTemplateOpen(false);
      setNewTemplateName("");
      setNewTemplateDesc("");
      loadTemplates(templateCategory !== "all" ? templateCategory : undefined);
    } catch (err) {
      message.error((err as Error).message || "创建失败");
    } finally {
      setCreatingTemplate(false);
    }
  }, [
    newTemplateName,
    newTemplateDesc,
    steps,
    stepKinds,
    outputFormat,
    outputResolution,
    outputFps,
    outputBitrate,
    templateCategory,
    loadTemplates,
  ]);

  /* 模板分类筛选 */
  const templateCategoryOptions = useMemo(
    () => [
      { label: "全部", value: "all" },
      { label: "优化", value: "optimization" },
      { label: "字幕", value: "subtitle" },
      { label: "社媒", value: "social_media" },
      { label: "播客", value: "podcast" },
      { label: "自定义", value: "custom" },
    ],
    [],
  );

  /* 分类过滤后的模板 */
  const filteredTemplates = useMemo(() => {
    if (templateCategory === "all") return templates;
    return templates.filter((t) => t.category === templateCategory);
  }, [templates, templateCategory]);

  /* 加载能力与步骤类型 */
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [caps, kinds] = await Promise.all([
        getVideoCapabilities(),
        getVideoStepKinds(),
      ]);
      setCapabilities(caps);
      setStepKinds(kinds);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  /* 分类步骤 */
  const basicKinds = useMemo(
    () => Object.entries(stepKinds).filter(([, v]) => v.category === "basic"),
    [stepKinds],
  );
  const aiKinds = useMemo(
    () => Object.entries(stepKinds).filter(([, v]) => v.category === "ai"),
    [stepKinds],
  );

  /* 步骤是否可用（基于 capabilities） */
  const isStepAvailable = useCallback(
    (kind: string): boolean => {
      if (!capabilities) return true;
      const capKey = `supports_${kind}` as keyof VideoCapabilitiesResponse;
      const val = capabilities[capKey];
      return val === true;
    },
    [capabilities],
  );

  /* 添加步骤 */
  const addStep = useCallback(
    (kind: string) => {
      const info = stepKinds[kind];
      if (!info) return;
      const defaultParams: Record<string, unknown> = {};
      for (const [key, param] of Object.entries(info.params)) {
        if (param.default !== undefined) {
          defaultParams[key] = param.default;
        }
      }
      const newStep: VideoEditStep = {
        step_id: `step-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        kind,
        params: defaultParams,
        order: steps.length,
      };
      setSteps((prev) => [...prev, newStep]);
      setAddModalOpen(false);
    },
    [stepKinds, steps.length],
  );

  /* 删除步骤 */
  const removeStep = useCallback((index: number) => {
    setSteps((prev) =>
      prev.filter((_, i) => i !== index).map((s, i) => ({ ...s, order: i })),
    );
  }, []);

  /* 上移步骤 */
  const moveStepUp = useCallback((index: number) => {
    if (index === 0) return;
    setSteps((prev) => {
      const next = [...prev];
      [next[index - 1], next[index]] = [next[index], next[index - 1]];
      return next.map((s, i) => ({ ...s, order: i }));
    });
  }, []);

  /* 下移步骤 */
  const moveStepDown = useCallback((index: number) => {
    setSteps((prev) => {
      if (index >= prev.length - 1) return prev;
      const next = [...prev];
      [next[index], next[index + 1]] = [next[index + 1], next[index]];
      return next.map((s, i) => ({ ...s, order: i }));
    });
  }, []);

  /* 更新步骤参数 */
  const updateStepParam = useCallback(
    (index: number, key: string, value: unknown) => {
      setSteps((prev) =>
        prev.map((s, i) =>
          i === index ? { ...s, params: { ...s.params, [key]: value } } : s,
        ),
      );
    },
    [],
  );

  /* 提交 */
  const handleSubmit = useCallback(async () => {
    if (!sourcePath.trim()) {
      toast.warning("请输入源视频路径");
      return;
    }
    setSubmitting(true);
    setResult(null);
    try {
      const resp = await editVideo({
        source_video_path: sourcePath.trim(),
        edit_config:
          steps.length > 0
            ? {
                steps,
                output_format: outputFormat,
                output_resolution: outputResolution,
                output_fps: outputFps,
                output_bitrate: outputBitrate,
              }
            : undefined,
      });
      setResult(resp);
      if (resp.status === "succeeded") {
        toast.success("剪辑完成");
      } else {
        toast.error(resp.error_message || "剪辑失败");
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }, [sourcePath, steps, outputFormat, outputResolution, outputFps, outputBitrate, toast]);

  /* ---- 渲染 ---- */

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {/* 标题 */}
      <div>
        <Title level={4} style={{ margin: 0 }}>
          <RobotOutlined style={{ marginRight: 8, color: "var(--primary-600)" }} />
          AI 智能剪辑
        </Title>
        <Text type="secondary">
          配置视频剪辑步骤，支持 AI 自动字幕、音量标准化、画面增强、静音裁剪等智能处理。
        </Text>
      </div>

      {/* 能力提示 */}
      {capabilities && !capabilities.enabled && (
        <Alert
          type="warning"
          showIcon
          message="FFmpeg 不可用"
          description="当前环境未检测到 FFmpeg，视频编辑功能将降级为沙箱模式。"
        />
      )}

      <Row gutter={[16, 16]}>
        {/* 左侧：源视频 + 步骤配置 */}
        <Col xs={24} lg={14}>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            {/* 源视频路径 */}
            <Card size="small" title={<Space><FileTextOutlined /> 源视频</Space>}>
              <Input
                placeholder="输入视频文件的绝对路径，例如 C:\videos\source.mp4"
                value={sourcePath}
                onChange={(e) => setSourcePath(e.target.value)}
                allowClear
              />
            </Card>

            {/* 快速应用模板 */}
            <Card
              size="small"
              title={
                <Space>
                  <CrownOutlined style={{ color: "var(--primary-500)" }} />
                  快速应用模板
                </Space>
              }
              extra={
                <Segmented
                  size="small"
                  options={templateCategoryOptions}
                  value={templateCategory}
                  onChange={(val) => {
                    const v = val as string;
                    setTemplateCategory(v);
                    loadTemplates(v !== "all" ? v : undefined);
                  }}
                />
              }
            >
              {templatesLoading ? (
                <div style={{ textAlign: "center", padding: "16px 0" }}>
                  <Spin size="small" />
                </div>
              ) : templateError ? (
                <Alert
                  type="error"
                  showIcon
                  message="模板暂时无法加载"
                  description={templateError}
                  action={<Button size="small" onClick={() => loadTemplates(templateCategory !== "all" ? templateCategory : undefined)}>重试</Button>}
                />
              ) : (
                <Row gutter={[10, 10]}>
                  {filteredTemplates.map((tpl) => (
                    <Col xs={24} sm={12} key={tpl.template_id}>
                      <TemplateCard
                        template={tpl}
                        onApply={applyTemplateToList}
                        onDelete={handleDeleteTemplate}
                      />
                    </Col>
                  ))}

                  {/* 新建自定义模板 */}
                  {templateCategory === "all" || templateCategory === "custom" ? (
                    <Col xs={24} sm={12}>
                      <Card
                        hoverable
                        size="small"
                        style={{
                          border: "1px dashed var(--border-color)",
                          textAlign: "center",
                          cursor: "pointer",
                          minHeight: 100,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                        styles={{ body: { padding: "16px 12px" } }}
                        onClick={() => setCreateTemplateOpen(true)}
                      >
                        <Space direction="vertical" size={4}>
                          <PlusOutlined style={{ fontSize: 20, color: "var(--gray-400)" }} />
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            保存当前为模板
                          </Text>
                        </Space>
                      </Card>
                    </Col>
                  ) : null}

                  {filteredTemplates.length === 0 && templateCategory !== "custom" && (
                    <Col span={24}>
                      <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description="暂无该分类模板"
                        style={{ padding: "12px 0", margin: 0 }}
                      />
                    </Col>
                  )}
                </Row>
              )}
            </Card>

            {/* 剪辑步骤 */}
            <Card
              size="small"
              title={
                <Space>
                  <ScissorOutlined /> 剪辑步骤
                  <Tag>{steps.length}</Tag>
                </Space>
              }
              extra={
                <Button
                  type="primary"
                  icon={<PlusOutlined />}
                  size="small"
                  onClick={() => setAddModalOpen(true)}
                >
                  添加步骤
                </Button>
              }
            >
              {steps.length === 0 ? (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="暂未添加步骤，点击上方按钮添加"
                  style={{ padding: "24px 0" }}
                />
              ) : (
                <List
                  dataSource={steps}
                  renderItem={(step, index) => (
                    <StepCard
                      key={step.step_id || index}
                      step={step}
                      index={index}
                      total={steps.length}
                      stepKinds={stepKinds}
                      onRemove={() => removeStep(index)}
                      onMoveUp={() => moveStepUp(index)}
                      onMoveDown={() => moveStepDown(index)}
                      onUpdateParam={(key, val) => updateStepParam(index, key, val)}
                    />
                  )}
                />
              )}
            </Card>

            {/* 输出设置 */}
            <Card size="small" title={<Space><SettingOutlined /> 输出设置</Space>}>
              <Row gutter={[12, 12]}>
                <Col span={12}>
                  <Text type="secondary" style={{ fontSize: 12 }}>输出格式</Text>
                  <Select
                    value={outputFormat}
                    onChange={setOutputFormat}
                    style={{ width: "100%", marginTop: 4 }}
                    size="small"
                  >
                    <Option value="mp4">MP4</Option>
                    <Option value="webm">WebM</Option>
                    <Option value="avi">AVI</Option>
                    <Option value="mov">MOV</Option>
                  </Select>
                </Col>
                <Col span={12}>
                  <Text type="secondary" style={{ fontSize: 12 }}>分辨率</Text>
                  <Select
                    value={outputResolution}
                    onChange={setOutputResolution}
                    style={{ width: "100%", marginTop: 4 }}
                    size="small"
                  >
                    <Option value="1080x1920">1080x1920 (竖屏)</Option>
                    <Option value="1920x1080">1920x1080 (横屏)</Option>
                    <Option value="720x1280">720x1280 (720P 竖屏)</Option>
                    <Option value="1280x720">1280x720 (720P 横屏)</Option>
                    <Option value="640x480">640x480 (480P)</Option>
                  </Select>
                </Col>
                <Col span={12}>
                  <Text type="secondary" style={{ fontSize: 12 }}>帧率</Text>
                  <InputNumber
                    value={outputFps}
                    onChange={(v) => setOutputFps(v ?? 30)}
                    min={15}
                    max={60}
                    style={{ width: "100%", marginTop: 4 }}
                    size="small"
                  />
                </Col>
                <Col span={12}>
                  <Text type="secondary" style={{ fontSize: 12 }}>码率</Text>
                  <Select
                    value={outputBitrate}
                    onChange={setOutputBitrate}
                    style={{ width: "100%", marginTop: 4 }}
                    size="small"
                  >
                    <Option value="1M">1 Mbps</Option>
                    <Option value="2M">2 Mbps</Option>
                    <Option value="4M">4 Mbps</Option>
                    <Option value="8M">8 Mbps</Option>
                    <Option value="16M">16 Mbps</Option>
                  </Select>
                </Col>
              </Row>
            </Card>

            {/* 提交 */}
            <Button
              type="primary"
              icon={<RocketOutlined />}
              size="large"
              block
              loading={submitting}
              onClick={handleSubmit}
            >
              {steps.length > 0
                ? `执行剪辑（${steps.length} 个步骤）`
                : "执行剪辑（默认配置）"}
            </Button>
          </Space>
        </Col>

        {/* 右侧：结果 + 能力 */}
        <Col xs={24} lg={10}>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            {/* 剪辑结果 */}
            <Card size="small" title={<Space><CheckCircleOutlined /> 剪辑结果</Space>}>
              {result ? (
                <ResultPanel result={result} />
              ) : (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="提交剪辑后结果将显示在此处"
                  style={{ padding: "24px 0" }}
                />
              )}
            </Card>

            {/* 编辑器能力 */}
            <Card
              size="small"
              title={<Space><ExperimentOutlined /> 编辑器能力</Space>}
              extra={
                <Button
                  icon={<ReloadOutlined />}
                  size="small"
                  loading={loading}
                  onClick={loadData}
                >
                  刷新
                </Button>
              }
            >
              {capabilities ? (
                <CapabilitiesPanel caps={capabilities} />
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="加载中..." />
              )}
            </Card>
          </Space>
        </Col>
      </Row>

      {/* 添加步骤弹窗 */}
      <AddStepModal
        open={addModalOpen}
        onClose={() => setAddModalOpen(false)}
        basicKinds={basicKinds}
        aiKinds={aiKinds}
        isStepAvailable={isStepAvailable}
        onSelect={addStep}
      />

      {/* 创建模板弹窗 */}
      <Modal
        title="保存当前步骤为模板"
        open={createTemplateOpen}
        onCancel={() => setCreateTemplateOpen(false)}
        onOk={handleCreateTemplate}
        confirmLoading={creatingTemplate}
        okText="保存模板"
        cancelText="取消"
        width={420}
      >
        <Space direction="vertical" size="middle" style={{ width: "100%", marginTop: 12 }}>
          <div>
            <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
              模板名称 <span style={{ color: "red" }}>*</span>
            </Text>
            <Input
              placeholder="输入模板名称"
              value={newTemplateName}
              onChange={(e) => setNewTemplateName(e.target.value)}
              maxLength={50}
            />
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
              模板描述
            </Text>
            <Input.TextArea
              placeholder="输入模板描述（可选）"
              value={newTemplateDesc}
              onChange={(e) => setNewTemplateDesc(e.target.value)}
              rows={3}
              maxLength={200}
            />
          </div>
          <Alert
            type="info"
            showIcon
            message={`将保存当前 ${steps.length} 个步骤及输出设置为模板`}
          />
        </Space>
      </Modal>
    </Space>
  );
}

/* ================================================================
   子组件
   ================================================================ */

/* ---- 步骤卡片 ---- */

function StepCard({
  step,
  index,
  total,
  stepKinds,
  onRemove,
  onMoveUp,
  onMoveDown,
  onUpdateParam,
}: {
  step: VideoEditStep;
  index: number;
  total: number;
  stepKinds: StepKindsResponse;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onUpdateParam: (key: string, value: unknown) => void;
}) {
  const info = stepKinds[step.kind];
  const isAi = step.kind.startsWith("ai_");

  return (
    <List.Item style={{ padding: "8px 0" }}>
      <Card
        size="small"
        style={{
          width: "100%",
          borderLeft: isAi ? "3px solid var(--primary-500)" : undefined,
        }}
        styles={{ body: { padding: "12px 16px" } }}
      >
        {/* 头部 */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 8,
          }}
        >
          <Space>
            <Tag color={stepColor(step.kind)} icon={STEP_ICONS[step.kind]}>
              {info?.label || step.kind}
            </Tag>
            <Text type="secondary" style={{ fontSize: 12 }}>
              #{index + 1}
            </Text>
            {isAi && (
              <Tag color="gold" style={{ fontSize: 10 }}>AI</Tag>
            )}
          </Space>
          <Space size={4}>
            <Tooltip title="上移">
              <Button
                type="text"
                size="small"
                icon={<UpOutlined />}
                disabled={index === 0}
                onClick={onMoveUp}
              />
            </Tooltip>
            <Tooltip title="下移">
              <Button
                type="text"
                size="small"
                icon={<DownOutlined />}
                disabled={index === total - 1}
                onClick={onMoveDown}
              />
            </Tooltip>
            <Tooltip title="删除">
              <Button
                type="text"
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={onRemove}
              />
            </Tooltip>
          </Space>
        </div>

        {/* 参数表单 */}
        {info && Object.keys(info.params).length > 0 && (
          <ParamForm
            paramDefs={info.params}
            values={step.params}
            onChange={onUpdateParam}
          />
        )}
      </Card>
    </List.Item>
  );
}

/* ---- 参数表单 ---- */

function ParamForm({
  paramDefs,
  values,
  onChange,
}: {
  paramDefs: Record<string, import("../api/types").StepKindParam>;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}) {
  return (
    <Row gutter={[8, 8]}>
      {Object.entries(paramDefs).map(([key, def]) => (
        <Col span={12} key={key}>
          <div style={{ marginBottom: 2 }}>
            <Text type="secondary" style={{ fontSize: 11 }}>
              {def.label}
              {def.required && <span style={{ color: "red" }}> *</span>}
            </Text>
          </div>
          {def.type === "select" && def.options ? (
            <Select
              value={values[key] ?? def.default}
              onChange={(v) => onChange(key, v)}
              size="small"
              style={{ width: "100%" }}
            >
              {def.options.map((opt) => (
                <Option key={opt} value={opt}>
                  {opt}
                </Option>
              ))}
            </Select>
          ) : def.type === "number" ? (
            <InputNumber
              value={values[key] as number}
              onChange={(v) => onChange(key, v)}
              min={def.min}
              max={def.max}
              step={def.min !== undefined && def.max !== undefined && def.max - def.min <= 2 ? 0.01 : 1}
              size="small"
              style={{ width: "100%" }}
            />
          ) : (
            <Input
              value={String(values[key] ?? "")}
              onChange={(e) => onChange(key, e.target.value)}
              size="small"
              placeholder={def.label}
            />
          )}
        </Col>
      ))}
    </Row>
  );
}

/* ---- 结果面板 ---- */

function ResultPanel({ result }: { result: VideoEditResponse }) {
  const succeeded = result.status === "succeeded";

  return (
    <Space direction="vertical" size="small" style={{ width: "100%" }}>
      <Alert
        type={succeeded ? "success" : "error"}
        showIcon
        message={succeeded ? "剪辑成功" : "剪辑失败"}
        description={
          succeeded
            ? `任务 ${result.task_id} 已完成`
            : result.error_message || "未知错误"
        }
      />
      {succeeded && result.result_path && (
        <>
          <Descriptions small>
            <Descriptions.Item label="任务 ID">{result.task_id}</Descriptions.Item>
            <Descriptions.Item label="文件大小">
              {result.result_size_bytes
                ? `${(result.result_size_bytes / 1024).toFixed(1)} KB`
                : "未知"}
            </Descriptions.Item>
          </Descriptions>
          <Tooltip title={result.result_path}>
            <Text
              code
              copyable
              ellipsis
              style={{ fontSize: 12, maxWidth: "100%" }}
            >
              {result.result_path}
            </Text>
          </Tooltip>
        </>
      )}
    </Space>
  );
}

/* Descriptions tiny helper (inline to avoid extra import) */
function Descriptions({
  small,
  children,
}: {
  small?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr",
        gap: small ? "4px 12px" : "8px 16px",
        fontSize: small ? 12 : 14,
      }}
    >
      {children}
    </div>
  );
}

// Extend Descriptions with Item
function DescItem({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <Text type="secondary">{label}</Text>
      <Text>{children}</Text>
    </>
  );
}
Descriptions.Item = DescItem;

/* ---- 能力面板 ---- */

function CapabilitiesPanel({ caps }: { caps: VideoCapabilitiesResponse }) {
  const aiFeatures = [
    { key: "supports_ai_subtitle", label: "AI 自动字幕", requires: "Whisper" },
    { key: "supports_ai_volume_norm", label: "AI 音量标准化", requires: "FFmpeg" },
    { key: "supports_ai_enhance", label: "AI 画面增强", requires: "FFmpeg" },
    { key: "supports_ai_silence_trim", label: "AI 静音裁剪", requires: "FFmpeg" },
  ];

  return (
    <Space direction="vertical" size="small" style={{ width: "100%" }}>
      <div>
        <Text strong style={{ fontSize: 13 }}>{caps.display_name}</Text>
        <Tag
          color={caps.enabled ? "success" : "default"}
          style={{ marginLeft: 8 }}
        >
          {caps.enabled ? "可用" : "不可用"}
        </Tag>
      </div>
      <Divider style={{ margin: "4px 0" }} />
      <Text type="secondary" style={{ fontSize: 12 }}>AI 智能剪辑能力：</Text>
      {aiFeatures.map((f) => {
        const available = caps[f.key as keyof VideoCapabilitiesResponse] === true;
        return (
          <div
            key={f.key}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "4px 0",
            }}
          >
            <Space>
              <RobotOutlined style={{ color: available ? "var(--primary-500)" : "var(--gray-400)" }} />
              <Text style={{ fontSize: 13 }}>{f.label}</Text>
            </Space>
            <Tag color={available ? "success" : "default"}>
              {available ? "支持" : `需要 ${f.requires}`}
            </Tag>
          </div>
        );
      })}
    </Space>
  );
}

/* ---- 添加步骤弹窗 ---- */

function AddStepModal({
  open,
  onClose,
  basicKinds,
  aiKinds,
  isStepAvailable,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  basicKinds: [string, StepKindInfo][];
  aiKinds: [string, StepKindInfo][];
  isStepAvailable: (kind: string) => boolean;
  onSelect: (kind: string) => void;
}) {
  return (
    <Modal
      title="添加剪辑步骤"
      open={open}
      onCancel={onClose}
      footer={null}
      width={560}
    >
      <Divider orientation="left" style={{ margin: "12px 0 8px", fontSize: 13 }}>
        基础步骤
      </Divider>
      <Row gutter={[8, 8]}>
        {basicKinds.map(([kind, info]) => (
          <Col span={8} key={kind}>
            <Button
              block
              icon={STEP_ICONS[kind]}
              onClick={() => onSelect(kind)}
              style={{ textAlign: "left" }}
            >
              {info.label}
            </Button>
          </Col>
        ))}
      </Row>

      <Divider orientation="left" style={{ margin: "16px 0 8px", fontSize: 13 }}>
        <RobotOutlined style={{ marginRight: 4 }} /> AI 智能步骤
      </Divider>
      <Row gutter={[8, 8]}>
        {aiKinds.map(([kind, info]) => {
          const available = isStepAvailable(kind);
          return (
            <Col span={12} key={kind}>
              <Tooltip title={!available ? `需要 ${info.requires || "外部工具"}` : undefined}>
                <Button
                  block
                  icon={STEP_ICONS[kind]}
                  onClick={() => available && onSelect(kind)}
                  disabled={!available}
                  style={{
                    textAlign: "left",
                    color: available ? undefined : "var(--gray-400)",
                  }}
                >
                  {info.label}
                  {!available && (
                    <Tag color="default" style={{ marginLeft: 4, fontSize: 10 }}>
                      需要 {info.requires}
                    </Tag>
                  )}
                </Button>
              </Tooltip>
            </Col>
          );
        })}
      </Row>
     </Modal>
  );
}

/* ---- 模板卡片子组件 ---- */

/** 模板分类 → 图标 */
const TEMPLATE_CATEGORY_ICONS: Record<string, ReactNode> = {
  optimize: <ThunderboltOutlined style={{ fontSize: 20 }} />,
  subtitle: <FileTextOutlined style={{ fontSize: 20 }} />,
  social: <GlobalOutlined style={{ fontSize: 20 }} />,
  podcast: <SoundOutlined style={{ fontSize: 20 }} />,
  custom: <StarOutlined style={{ fontSize: 20 }} />,
  ai: <RobotOutlined style={{ fontSize: 20 }} />,
};

function TemplateCard({
  template,
  onApply,
  onDelete,
}: {
  template: EditTemplate;
  onApply: (tpl: EditTemplate) => void;
  onDelete: (id: string, e: React.MouseEvent) => void;
}) {
  const isAi = template.category === "ai" || template.steps.some((s) => s.kind.startsWith("ai_"));
  const categoryIcon = TEMPLATE_CATEGORY_ICONS[template.category] || <HighlightOutlined style={{ fontSize: 20 }} />;

  return (
    <Card
      hoverable
      size="small"
      style={{
        cursor: "pointer",
        borderLeft: isAi ? "3px solid #722ed1" : undefined,
        transition: "all 0.2s",
      }}
      styles={{ body: { padding: "12px" } }}
      onClick={() => onApply(template)}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 8,
            background: isAi
              ? "linear-gradient(135deg, #f9f0ff, #efdbff)"
              : "var(--primary-50, #f0f5ff)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: isAi ? "#722ed1" : "var(--primary-500)",
            flexShrink: 0,
          }}
        >
          {categoryIcon}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
            <Text strong style={{ fontSize: 13, lineHeight: "20px" }} ellipsis>
              {template.name}
            </Text>
            {template.is_builtin && (
              <Tag
                color="blue"
                style={{ fontSize: 10, lineHeight: "16px", padding: "0 4px", marginRight: 0, flexShrink: 0 }}
              >
                内置
              </Tag>
            )}
            {isAi && (
              <Tag
                color="purple"
                style={{ fontSize: 10, lineHeight: "16px", padding: "0 4px", marginRight: 0, flexShrink: 0 }}
              >
                AI
              </Tag>
            )}
            {!template.is_builtin && (
              <Button
                type="text"
                danger
                size="small"
                icon={<DeleteOutlined />}
                style={{ marginLeft: "auto", flexShrink: 0 }}
                onClick={(e) => onDelete(template.template_id, e)}
              />
            )}
          </div>
          <Text
            type="secondary"
            style={{
              fontSize: 12,
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
              lineHeight: "18px",
            }}
          >
            {template.description || "无描述"}
          </Text>
          <div style={{ marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Tag style={{ fontSize: 10, margin: 0 }}>
              {template.steps.length} 个步骤
            </Tag>
            {template.output_resolution && (
              <Tag style={{ fontSize: 10, margin: 0 }}>
                {template.output_resolution}
              </Tag>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}
