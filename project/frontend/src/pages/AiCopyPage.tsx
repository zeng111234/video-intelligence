import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Col,
  Drawer,
  Empty,
  Input,
  List,
  Popconfirm,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  FileAddOutlined,
  FileTextOutlined,
  HistoryOutlined,
  ReloadOutlined,
  SendOutlined,
} from "@ant-design/icons";
import {
  clearCopywritingHistory,
  generateCopywriting,
  generatePublishMetadata,
  deleteTask,
  getCopywritingCapabilities,
  getCopywritingTask,
  listCopywritingTasks,
  rewriteCopywriting,
} from "../api/client";
import type {
  CopywritingCapabilitiesResponse,
  CopywritingDetailResponse,
  CopywritingResponse,
  CopywritingSummaryResponse,
  PublishMetadataResponse,
} from "../api/types";
import { useToast } from "../components/Toast";
import { usePersistentState } from "../hooks/usePersistentState";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

type CopyMode = "generate" | "rewrite";

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN") : "-";
}

function highlightedCopy(text: string, terms: string[]) {
  const uniqueTerms = [...new Set(terms.map((term) => term.trim()).filter((term) => term.length > 1 && text.includes(term)))];
  if (!uniqueTerms.length) return text;
  const escaped = uniqueTerms
    .sort((left, right) => right.length - left.length)
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const matcher = new RegExp(`(${escaped.join("|")})`, "g");
  const attentionSet = new Set(uniqueTerms);
  return text.split(matcher).map((part, index) => attentionSet.has(part) ? (
    <mark key={`${part}-${index}`} style={{ background: "#fff1b8", color: "#ad4e00", padding: "0 2px", borderRadius: 2 }}>
      {part}
    </mark>
  ) : part);
}

export default function AiCopyPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [mode, setMode, clearMode] = usePersistentState<CopyMode>("ai_copy_mode", "rewrite");
  const [contentBrief, setContentBrief, clearContentBrief] = usePersistentState("ai_copy_content_brief", "");
  const [sourceText, setSourceText, clearSourceText] = usePersistentState("ai_copy_source_text", "");
  const [sellingPoints, setSellingPoints, clearSellingPoints] = usePersistentState("ai_copy_selling_points", "");
  const [callToAction, setCallToAction, clearCallToAction] = usePersistentState("ai_copy_call_to_action", "");

  const [capability, setCapability] = useState<CopywritingCapabilitiesResponse | null>(null);
  const [capabilityError, setCapabilityError] = useState("");
  const [history, setHistory] = useState<CopywritingSummaryResponse[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);
  const [clearingHistory, setClearingHistory] = useState(false);
  const [loading, setLoading] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [lastResponse, setLastResponse] = useState<CopywritingResponse | null>(null);
  const [variants, setVariants] = useState<string[]>([]);
  const [publishMetadata, setPublishMetadata] = useState<PublishMetadataResponse | null>(null);
  const [metadataLoading, setMetadataLoading] = useState(false);

  const inputReady = mode === "generate" ? contentBrief.trim().length > 0 : sourceText.trim().length > 0;
  const hasLocalDraft = Boolean(
    contentBrief.trim() ||
    sourceText.trim() ||
    sellingPoints.trim() ||
    callToAction.trim(),
  );
  const enabled = capability?.enabled === true;
  const disabledMessage = capabilityError
    ? capabilityError
    : capability && !capability.enabled
      ? `未配置 ${capability.missing_configuration.join("、") || "模型密钥"}，请在本机私密配置中设置后重启后端。`
      : "";

  const refreshHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      setHistory(await listCopywritingTasks(50));
    } catch (err) {
      toast.error((err as Error).message || "读取文案历史失败");
    } finally {
      setHistoryLoading(false);
    }
  }, [toast]);

  const handleDeleteHistory = async (item: CopywritingSummaryResponse) => {
    setDeletingTaskId(item.task_id);
    try {
      await deleteTask(item.task_id);
      if (taskId === item.task_id) {
        setTaskId(null);
        setLastResponse(null);
        setVariants([]);
        setPublishMetadata(null);
      }
      toast.success("文案历史已删除");
      await refreshHistory();
    } catch (err) {
      toast.error((err as Error).message || "删除文案历史失败");
    } finally {
      setDeletingTaskId(null);
    }
  };

  const handleClearHistory = async () => {
    setClearingHistory(true);
    try {
      const result = await clearCopywritingHistory();
      setHistory([]);
      setTaskId(null);
      setLastResponse(null);
      setVariants([]);
      setPublishMetadata(null);
      toast.success(`已删除 ${result.deleted_count} 条文案历史`);
    } catch (err) {
      toast.error((err as Error).message || "清空文案历史失败");
    } finally {
      setClearingHistory(false);
    }
  };

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
        if (!cancelled) setCapabilityError((err as Error).message || "读取模型能力失败");
      });
    refreshHistory();
    return () => {
      cancelled = true;
    };
  }, [refreshHistory]);

  const applyResult = useCallback((resp: CopywritingResponse) => {
    setTaskId(resp.task_id);
    setLastResponse(resp);
    const resultVariants = resp.result_variants.length > 0
      ? resp.result_variants
      : resp.result_text
        ? [resp.result_text]
        : [];
    setVariants(resultVariants.slice(0, 1));
    setPublishMetadata(null);
  }, []);

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
    setPublishMetadata(null);
    try {
      const common = {
        style_prompt: "",
        tone: "natural",
        variant_count: 1,
      };
      const resp = mode === "generate"
        ? await generateCopywriting({
            ...common,
            content_brief: contentBrief,
            selling_points: sellingPoints,
            call_to_action: callToAction,
          })
        : await rewriteCopywriting({ ...common, source_text: sourceText });
      applyResult(resp);
      await refreshHistory();
      if (resp.status !== "succeeded") {
        toast.error(resp.error_message || "文案生成失败");
        return;
      }
      if (!resp.result_text && resp.result_variants.length === 0) {
        toast.warning("后端未返回有效文案");
        return;
      }
      toast.success(
        resp.compliance_status === "best_effort"
          ? "已采用自动优化后的最终版本"
          : "已生成 1 篇去重口播文案",
      );
    } catch (err) {
      toast.error((err as Error).message || "文案生成失败");
    } finally {
      setLoading(false);
    }
  }, [
    applyResult,
    callToAction,
    contentBrief,
    enabled,
    inputReady,
    mode,
    refreshHistory,
    sellingPoints,
    sourceText,
    toast,
  ]);

  const handleLoadHistory = async (item: CopywritingSummaryResponse) => {
    setHistoryLoading(true);
    try {
      const detail: CopywritingDetailResponse = await getCopywritingTask(item.task_id);
      const nextMode = detail.creation_mode === "generate" ? "generate" : "rewrite";
      setMode(nextMode);
      setContentBrief(detail.content_brief);
      setSourceText(detail.source_text);
      setSellingPoints(detail.selling_points);
      setCallToAction(detail.call_to_action);
      applyResult(detail);
      setHistoryOpen(false);
    } catch (err) {
      toast.error((err as Error).message || "读取文案详情失败");
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleNewCopy = () => {
    setContentBrief("");
    setSourceText("");
    setSellingPoints("");
    setCallToAction("");
    setTaskId(null);
    setLastResponse(null);
    setVariants([]);
    setPublishMetadata(null);
  };

  const handleClearDraft = () => {
    clearMode();
    clearContentBrief();
    clearSourceText();
    clearSellingPoints();
    clearCallToAction();
    setTaskId(null);
    setLastResponse(null);
    setVariants([]);
    setPublishMetadata(null);
    toast.success("本机草稿已清空");
  };

  const handleCopy = useCallback((text: string) => {
    navigator.clipboard.writeText(text).then(() => toast.success("已复制到剪贴板"));
  }, [toast]);

  const tokenUsage = lastResponse?.token_usage ?? {};
  const activeText = variants[0] || "";
  const attentionTerms = lastResponse?.attention_terms ?? [];
  const isBestEffort = lastResponse?.compliance_status === "best_effort";
  const resultNoticeMessage = isBestEffort
    ? attentionTerms.length > 0
      ? "已使用最终优化版，并高亮其他主体名称"
      : "已使用自动优化后的最终版本"
    : attentionTerms.length > 0
      ? "已高亮可能属于其他主体的名称"
      : "自动去重和风险处理已通过";

  const handleGeneratePublishMetadata = useCallback(async () => {
    if (!activeText.trim()) {
      toast.warning("请先生成文案");
      return;
    }
    setMetadataLoading(true);
    try {
      const result = await generatePublishMetadata({
        source_text: activeText,
        platforms: ["douyin", "kuaishou", "wechat_channels", "xiaohongshu", "bilibili"],
        source_task_id: taskId || undefined,
      });
      setPublishMetadata(result);
      if (result.is_mock) toast.warning("当前为演示结果，请配置真实模型后再用于正式发布");
      else toast.success("已生成发布标题、描述和话题，请检查后带入发布");
    } catch (err) {
      toast.error((err as Error).message || "生成发布信息失败");
    } finally {
      setMetadataLoading(false);
    }
  }, [activeText, taskId, toast]);

  const handleSendToPublish = useCallback(() => {
    if (!publishMetadata?.title.trim() || !publishMetadata.description.trim()) {
      toast.warning("请先检查并补齐发布标题和描述");
      return;
    }
    window.sessionStorage.setItem("publish_ai_draft", JSON.stringify({
      title: publishMetadata.title.trim(),
      description: publishMetadata.description.trim(),
      tags: publishMetadata.tags,
      source_task_id: publishMetadata.task_id,
    }));
    navigate("/publish?from_ai_copy=1");
  }, [navigate, publishMetadata, toast]);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Row justify="space-between" align="middle" gutter={[16, 12]}>
        <Col>
          <Title level={4} style={{ margin: 0 }}>
            <EditOutlined /> AI 文案生成
          </Title>
          <Text type="secondary">真实大模型生成自然口播文案，支持需求生成、口播优化与风险表达改写</Text>
        </Col>
        <Col>
          <Space wrap>
            <Tag color={hasLocalDraft ? "green" : "default"}>本机草稿{hasLocalDraft ? "已保存" : "为空"}</Tag>
            <Button icon={<FileAddOutlined />} onClick={handleNewCopy}>新建文案</Button>
            <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>历史记录</Button>
            <Popconfirm title="清空本机草稿？" okText="清空" cancelText="取消" onConfirm={handleClearDraft}>
              <Button danger disabled={!hasLocalDraft && variants.length === 0}>清空草稿</Button>
            </Popconfirm>
          </Space>
        </Col>
      </Row>

      {disabledMessage && <Alert type={capabilityError ? "error" : "warning"} message={disabledMessage} showIcon />}

      <Row gutter={[24, 24]} align="top">
        <Col xs={24} lg={10}>
          <Card title={<Space><FileTextOutlined /> 文案输入</Space>}>
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
                  <TextArea
                    placeholder="内容概要，例如：面向中小企业老板，介绍 AI 短视频获客系统如何降低内容生产成本..."
                    rows={5}
                    value={contentBrief}
                    onChange={(e) => setContentBrief(e.target.value)}
                    showCount
                    maxLength={5000}
                    style={{ resize: "none" }}
                  />
                  <TextArea
                    placeholder="核心卖点：产品亮点、服务优势或确定可说的事实"
                    rows={3}
                    value={sellingPoints}
                    onChange={(e) => setSellingPoints(e.target.value)}
                    maxLength={1200}
                    style={{ resize: "none" }}
                  />
                  <Input
                    placeholder="行动号召，例如：私信领取行业案例清单"
                    value={callToAction}
                    onChange={(e) => setCallToAction(e.target.value)}
                    maxLength={120}
                  />
                </>
              ) : (
                <TextArea
                  placeholder="粘贴已有文案、脚本或口播稿..."
                  rows={8}
                  value={sourceText}
                  onChange={(e) => setSourceText(e.target.value)}
                  showCount
                  maxLength={5000}
                  style={{ resize: "none" }}
                />
              )}

              <Text type="secondary" style={{ fontSize: 12 }}>系统会根据内容自动判断适合的受众，并保留原文可核实的事实。</Text>

              <Button
                type="primary"
                icon={<EditOutlined />}
                size="large"
                block
                loading={loading}
                onClick={handleSubmit}
                disabled={!inputReady || !enabled}
              >
                {mode === "generate" ? "生成口播文案" : "优化口播文案"}
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
            extra={variants.length > 0 && (
              <Button icon={<ReloadOutlined />} size="small" onClick={handleSubmit} loading={loading}>
                重新生成
              </Button>
            )}
          >
            <Spin spinning={loading} tip="AI 正在生成文案...">
              {lastResponse?.status === "failed" && (
                <Alert type="error" showIcon message={lastResponse.error_message || "文案生成失败"} style={{ marginBottom: 16 }} />
              )}
              {activeText ? (
                <Space direction="vertical" style={{ width: "100%" }} size={16}>
                  <div style={{ background: "var(--gray-50)", borderRadius: 8, padding: 20, border: "1px solid var(--border-default)" }}>
                    <Paragraph style={{ fontSize: 15, lineHeight: 1.8, margin: 0, whiteSpace: "pre-wrap" }}>
                      {highlightedCopy(activeText, attentionTerms)}
                    </Paragraph>
                  </div>
                  <Space wrap>
                    <Button icon={<CopyOutlined />} onClick={() => handleCopy(activeText)}>复制文案</Button>
                  </Space>
                  <Card
                    size="small"
                    title="发布标题、描述和话题"
                    extra={<Button type="primary" loading={metadataLoading} onClick={handleGeneratePublishMetadata}>{publishMetadata ? "重新生成" : "AI 生成发布信息"}</Button>}
                    style={{ background: "#faf7ff" }}
                  >
                    {publishMetadata ? (
                      <Space direction="vertical" size={10} style={{ width: "100%" }}>
                        <Text strong>主题（发布标题）</Text>
                        <Input aria-label="AI 发布标题" value={publishMetadata.title} maxLength={100} showCount onChange={(event) => setPublishMetadata((current) => current ? { ...current, title: event.target.value } : current)} />
                        <Text strong>描述</Text>
                        <TextArea aria-label="AI 发布描述" value={publishMetadata.description} rows={4} maxLength={1000} showCount onChange={(event) => setPublishMetadata((current) => current ? { ...current, description: event.target.value } : current)} />
                        <Text strong>话题</Text>
                        <Select aria-label="AI 发布话题" mode="tags" value={publishMetadata.tags} tokenSeparators={[",", "，", " "]} placeholder="输入话题后回车" style={{ width: "100%" }} onChange={(values) => setPublishMetadata((current) => current ? { ...current, tags: values.map((value) => value.replace(/^#/, "")).filter(Boolean).slice(0, 8) } : current)} />
                        <Space wrap>
                          <Button type="primary" icon={<SendOutlined />} onClick={handleSendToPublish}>带入多平台发布</Button>
                          <Text type="secondary" style={{ fontSize: 12 }}>{publishMetadata.model_name} · {publishMetadata.is_mock ? "演示" : "真实模型"}</Text>
                        </Space>
                      </Space>
                    ) : (
                      <Text type="secondary">基于当前文案生成，生成后可修改并带入多平台发布。</Text>
                    )}
                  </Card>
                  <Alert
                    type={attentionTerms.length > 0 || isBestEffort ? "warning" : "info"}
                    showIcon
                    message={resultNoticeMessage}
                    description={
                      <Space direction="vertical" size={2}>
                        {(lastResponse?.compliance_notes || ["已按自然口播节奏优化表达。"])
                          .map((note) => <Text key={note}>{note}</Text>)}
                        {attentionTerms.length > 0 && (
                          <Space wrap size={[4, 4]}>
                            {attentionTerms.map((term) => <Tag color="orange" key={term}>{term}</Tag>)}
                          </Space>
                        )}
                        <Text type="secondary">自动处理可以降低表达风险，但不代表平台审核保证。</Text>
                      </Space>
                    }
                  />
                  <Space wrap size={[16, 8]}>
                    <Text type="secondary" style={{ fontSize: 12 }}>供应商：{lastResponse?.provider_name || "-"}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>Token：{tokenUsage.total_tokens ?? "-"}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>演示：{lastResponse?.is_mock ? "是" : "否"}</Text>
                  </Space>
                </Space>
              ) : (
                <Empty
                  description={enabled ? "填写输入后点击生成" : "模型未配置，暂不能生成真实文案"}
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  style={{ padding: "32px 0" }}
                />
              )}
            </Spin>
          </Card>
        </Col>
      </Row>

      <Drawer
        title="AI 文案历史"
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        width={420}
        extra={(
          <Space>
            <Button icon={<ReloadOutlined />} loading={historyLoading} disabled={clearingHistory} onClick={refreshHistory}>刷新</Button>
            <Popconfirm
              title="删除全部文案历史？"
              description="只删除此处的独立文案任务，不会删除转写生成的口播稿或其他任务。"
              okText="全部删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={handleClearHistory}
            >
              <Button danger icon={<DeleteOutlined />} loading={clearingHistory} disabled={!history.length || historyLoading}>全部删除</Button>
            </Popconfirm>
          </Space>
        )}
      >
        <List
          loading={historyLoading}
          dataSource={history}
          locale={{ emptyText: "暂无独立文案任务" }}
          renderItem={(item) => (
            <List.Item actions={[
              <Button type="link" onClick={() => handleLoadHistory(item)}>载入</Button>,
              <Popconfirm
                title="删除这条文案历史？"
                description="只删除本条文案任务，不影响其他历史记录。"
                okText="删除"
                okButtonProps={{ danger: true }}
                cancelText="取消"
                onConfirm={() => handleDeleteHistory(item)}
              >
                <Button type="link" danger icon={<DeleteOutlined />} loading={deletingTaskId === item.task_id} disabled={clearingHistory}>删除</Button>
              </Popconfirm>,
            ]}>
              <List.Item.Meta
                title={<Space wrap><Text strong>{item.title}</Text><Tag>{item.creation_mode === "generate" ? "需求生成" : "改写"}</Tag></Space>}
                description={
                  <Space direction="vertical" size={4}>
                    <Text type="secondary">{formatTime(item.created_at)}</Text>
                    <Text type="secondary">{item.model_name || "未知模型"} · {item.target_length} 字 · {item.result_variants.length || (item.result_text ? 1 : 0)} 版</Text>
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Drawer>
    </Space>
  );
}
