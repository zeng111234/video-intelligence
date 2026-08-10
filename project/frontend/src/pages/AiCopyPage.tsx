import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Drawer,
  Dropdown,
  Input,
  List,
  Modal,
  Popconfirm,
  Segmented,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  ArrowRightOutlined,
  CheckCircleFilled,
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  ExclamationCircleFilled,
  FileAddOutlined,
  FileTextOutlined,
  HistoryOutlined,
  InfoCircleFilled,
  MoreOutlined,
  ReloadOutlined,
  SaveOutlined,
} from "@ant-design/icons";
import {
  clearCopywritingHistory,
  generateCopywriting,
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
} from "../api/types";
import { useToast } from "../components/Toast";
import { usePersistentState } from "../hooks/usePersistentState";
import "./AiCopyPage.css";

const { Text } = Typography;
const { TextArea } = Input;

type CopyMode = "generate" | "rewrite";

const TRANSCRIPT_DEDUP_REWRITE_PROMPT = [
  "去重改写：保留原文中可核实的事实，不补充数据、案例、资质或效果承诺。",
  "先重组表达顺序，再用自然口语重新写；除必要的专有名词、参数、金额和事实外，不沿用原句或只替换同义词。",
].join("\n");

interface CopyHandoffState {
  sourceText?: unknown;
  sourceLabel?: unknown;
}

interface CopyComparisonRow {
  id: number;
  source: string;
  result: string;
  needsReview: boolean;
}

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

function splitCopySegments(text: string) {
  const normalized = text.replace(/\r\n/g, "\n").trim();
  if (!normalized) return [];
  const paragraphs = normalized.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean);
  if (paragraphs.length > 1) return paragraphs;

  const lines = normalized.split(/\n+/).map((item) => item.trim()).filter(Boolean);
  if (lines.length > 1) return lines;

  const sentences = normalized.match(/[^。！？!?；;]+[。！？!?；;]?/g)?.map((item) => item.trim()).filter(Boolean) ?? [];
  if (sentences.length <= 4) return sentences.length ? sentences : [normalized];

  const grouped: string[] = [];
  for (let index = 0; index < sentences.length; index += 2) {
    grouped.push(sentences.slice(index, index + 2).join(""));
  }
  return grouped;
}

function roundCreditsUp(value: number, minimum: number) {
  return Math.ceil(Math.max(value, minimum) * 100 - Number.EPSILON) / 100;
}

function buildComparisonRows(source: string, result: string, attentionTerms: string[], complianceStatus?: string | null) {
  const sourceSegments = splitCopySegments(source);
  const resultSegments = splitCopySegments(result);
  const rowCount = Math.max(sourceSegments.length, resultSegments.length, result ? 1 : 0);
  let fallbackReviewAssigned = false;

  return Array.from({ length: rowCount }, (_, index): CopyComparisonRow => {
    const sourceSegment = sourceSegments[index] ?? "—";
    const resultSegment = resultSegments[index] ?? "—";
    const hasAttentionTerm = attentionTerms.some((term) => term.trim().length > 1 && resultSegment.includes(term.trim()));
    const needsFallbackReview = !hasAttentionTerm
      && !fallbackReviewAssigned
      && (complianceStatus === "best_effort" || complianceStatus === "review_required");
    if (needsFallbackReview) fallbackReviewAssigned = true;
    return {
      id: index + 1,
      source: sourceSegment,
      result: resultSegment,
      needsReview: hasAttentionTerm || needsFallbackReview,
    };
  });
}

export default function AiCopyPage() {
  const toast = useToast();
  const location = useLocation();
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
  const [sourceLabel, setSourceLabel] = useState("手动输入");
  const [rowFilter, setRowFilter] = useState<"all" | "review">("all");
  const [reviewedRowIds, setReviewedRowIds] = useState<number[]>([]);
  const [reasonRowId, setReasonRowId] = useState<number | null>(null);
  const handledHandoffKeyRef = useRef<string | null>(null);

  const inputReady = mode === "generate" ? contentBrief.trim().length > 0 : sourceText.trim().length > 0;
  const hasLocalDraft = Boolean(
    contentBrief.trim() ||
    sourceText.trim() ||
    sellingPoints.trim() ||
    callToAction.trim(),
  );
  const enabled = capability?.enabled === true;
  const isSandbox = capability?.mode === "sandbox";
  const disabledMessage = capabilityError
    ? capabilityError
    : capability && !capability.enabled
      ? `未配置 ${capability.missing_configuration.join("、") || "模型密钥"}，请在本机私密配置中设置后重启后端。`
      : "";
  const sourceDocument = mode === "rewrite"
    ? sourceText
    : [contentBrief, sellingPoints, callToAction].map((item) => item.trim()).filter(Boolean).join("\n");
  const estimatedCredits = useMemo(() => {
    const inputRate = Number(capability?.input_price_credits_per_1k_tokens ?? "0.0015");
    const outputRate = Number(capability?.output_price_credits_per_1k_tokens ?? "0.003");
    const minimum = Number(capability?.minimum_charge_credits ?? "0.01");
    const estimatedInputTokens = Math.max(sourceDocument.trim().length, 1);
    const estimatedOutputTokens = mode === "rewrite"
      ? Math.max(sourceText.trim().length, 1)
      : Math.max(contentBrief.trim().length, 600);
    return roundCreditsUp(
      estimatedInputTokens / 1000 * inputRate + estimatedOutputTokens / 1000 * outputRate,
      minimum,
    );
  }, [capability, contentBrief, mode, sourceDocument, sourceText]);

  useEffect(() => {
    if (handledHandoffKeyRef.current === location.key) return;
    const handoff = location.state as CopyHandoffState | null;
    const handoffText = typeof handoff?.sourceText === "string" ? handoff.sourceText.trim() : "";
    if (!handoffText) return;
    handledHandoffKeyRef.current = location.key;

    setMode("rewrite");
    setSourceText(handoffText);
    setTaskId(null);
    setLastResponse(null);
    setVariants([]);
    const label = typeof handoff?.sourceLabel === "string" ? handoff.sourceLabel : "转写稿";
    setSourceLabel(label);
    toast.success(`已带入「${label}」，确认内容后再开始改写`);
  }, [location.key, location.state, setMode, setSourceText, toast]);

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
    setRowFilter("all");
    setReasonRowId(null);
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
    try {
      const common = {
        style_prompt: mode === "rewrite" ? TRANSCRIPT_DEDUP_REWRITE_PROMPT : "",
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
          : mode === "rewrite"
            ? "已完成去重改写，请核对事实后再使用"
            : "已生成 1 篇口播文案",
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

  const requestSubmit = useCallback(() => {
    if (!inputReady) {
      toast.warning(mode === "generate" ? "请输入内容概要" : "请输入原始文案");
      return;
    }
    if (!enabled) {
      toast.error("AI 文案模型未配置，无法真实生成");
      return;
    }
    Modal.confirm({
      title: mode === "generate" ? "确认生成口播文案？" : "确认开始去重改写？",
      content: isSandbox
        ? "当前为本地演示模式，不会扣积分；确认后生成演示结果。"
        : `预计约 ${estimatedCredits.toFixed(2)} 积分，最终按实际 Token 用量结算；确认后才会调用 AI 文案服务。`,
      okText: isSandbox ? "开始演示" : "确认费用并开始",
      cancelText: "暂不生成",
      onOk: handleSubmit,
    });
  }, [enabled, estimatedCredits, handleSubmit, inputReady, isSandbox, mode, toast]);

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
      setSourceLabel(item.title || "历史文案");
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
    setSourceLabel("手动输入");
    setRowFilter("all");
    setReviewedRowIds([]);
    setReasonRowId(null);
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
    setSourceLabel("手动输入");
    setRowFilter("all");
    setReviewedRowIds([]);
    setReasonRowId(null);
    toast.success("本机草稿已清空");
  };

  const handleCopy = useCallback((text: string) => {
    navigator.clipboard.writeText(text).then(() => toast.success("已复制到剪贴板"));
  }, [toast]);

  const activeText = variants[0] || "";
  const attentionTerms = lastResponse?.attention_terms ?? [];
  const comparisonRows = useMemo(
    () => buildComparisonRows(sourceDocument, activeText, attentionTerms, lastResponse?.compliance_status),
    [activeText, attentionTerms, lastResponse?.compliance_status, sourceDocument],
  );
  const reviewRows = comparisonRows.filter((row) => row.needsReview);
  const pendingReviewRows = reviewRows.filter((row) => !reviewedRowIds.includes(row.id));
  const reviewedCount = comparisonRows.length - pendingReviewRows.length;
  const visibleRows = rowFilter === "review" ? pendingReviewRows : comparisonRows;

  useEffect(() => {
    setReviewedRowIds(comparisonRows.filter((row) => !row.needsReview).map((row) => row.id));
  }, [activeText, taskId]);

  const handleConfirmRow = (rowId: number) => {
    setReviewedRowIds((current) => current.includes(rowId) ? current : [...current, rowId]);
    setReasonRowId(null);
    toast.success("这一段已确认");
  };

  const handleContinue = () => {
    if (!activeText || !taskId) return;
    if (pendingReviewRows.length > 0) {
      toast.warning(`请先确认剩余 ${pendingReviewRows.length} 处内容`);
      setRowFilter("review");
      return;
    }
    const params = new URLSearchParams({ sourceTask: taskId, script: activeText });
    navigate(`/avatar?${params.toString()}`);
  };

  return (
    <div className="ai-copy-page">
      <header className="ai-copy-toolbar">
        <Segmented
          aria-label="文案创作方式"
          value={mode}
          onChange={(value) => {
            setMode(value as CopyMode);
            setTaskId(null);
            setLastResponse(null);
            setVariants([]);
            setRowFilter("all");
          }}
          options={[
            { label: "转写稿去重改写", value: "rewrite" },
            { label: "从需求生成", value: "generate" },
          ]}
        />
        <Space size={4} wrap>
          {activeText ? (
            <Button type="text" icon={<ReloadOutlined />} loading={loading} onClick={requestSubmit}>重新改写</Button>
          ) : (
            <Button type="text" icon={<FileAddOutlined />} onClick={handleNewCopy}>新建文案</Button>
          )}
          <Button type="text" icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>历史记录</Button>
          <Dropdown
            trigger={["click"]}
            menu={{
              items: [{ key: "clear", label: "清空本机草稿", danger: true, disabled: !hasLocalDraft && variants.length === 0 }],
              onClick: ({ key }) => {
                if (key !== "clear") return;
                Modal.confirm({
                  title: "清空本机草稿？",
                  content: "输入内容和当前改写结果会被清空。",
                  okText: "清空",
                  okButtonProps: { danger: true },
                  cancelText: "取消",
                  onOk: handleClearDraft,
                });
              },
            }}
          >
            <Button type="text" aria-label="更多操作" icon={<MoreOutlined />} />
          </Dropdown>
        </Space>
      </header>

      {disabledMessage && <Alert type={capabilityError ? "error" : "warning"} message={disabledMessage} showIcon />}

      <section className={`ai-copy-workbench${activeText ? " has-result" : " is-compose"}`}>
        <div className="ai-copy-source-context">
          <Space size={8} wrap>
            <FileTextOutlined />
            <Text type="secondary">{mode === "rewrite" ? "来自转写复核" : "来自需求输入"}</Text>
            <span className="ai-copy-context-separator">·</span>
            <Text>{sourceLabel}</Text>
            <span className="ai-copy-context-separator">·</span>
            <Text type="secondary">{sourceDocument.length}字</Text>
          </Space>
          <Tag color={hasLocalDraft ? "green" : "default"}>{hasLocalDraft ? "草稿已保存" : "暂无草稿"}</Tag>
        </div>

        <Spin spinning={loading} tip={mode === "rewrite" ? "正在整理并改写文案..." : "正在生成口播文案..."}>
          {lastResponse?.status === "failed" && (
            <Alert className="ai-copy-result-error" type="error" showIcon message={lastResponse.error_message || "文案生成失败"} />
          )}

          {!activeText ? (
            <div className="ai-copy-compose-panel">
              <div className="ai-copy-compose-heading">
                <div>
                  <Text strong>{mode === "rewrite" ? "原稿" : "内容需求"}</Text>
                  <Text type="secondary">
                    {mode === "rewrite" ? "确认原文后再开始改写，不会自动生成。" : "只填写确定的信息，其余交给系统整理。"}
                  </Text>
                </div>
              </div>

              {mode === "generate" ? (
                <div className="ai-copy-input-stack">
                  <TextArea
                    aria-label="内容概要"
                    placeholder="内容概要，例如：面向中小企业老板，介绍 AI 短视频获客系统如何降低内容生产成本..."
                    rows={5}
                    value={contentBrief}
                    onChange={(event) => setContentBrief(event.target.value)}
                    showCount
                    maxLength={5000}
                  />
                  <TextArea
                    aria-label="核心卖点"
                    placeholder="核心卖点：产品亮点、服务优势或确定可说的事实"
                    rows={3}
                    value={sellingPoints}
                    onChange={(event) => setSellingPoints(event.target.value)}
                    maxLength={1200}
                  />
                  <Input
                    aria-label="行动号召"
                    placeholder="行动号召，例如：私信领取行业案例清单"
                    value={callToAction}
                    onChange={(event) => setCallToAction(event.target.value)}
                    maxLength={120}
                  />
                </div>
              ) : (
                <TextArea
                  aria-label="待去重的转写或口播稿"
                  placeholder="粘贴已确认的转写稿、口播稿或原始文案..."
                  rows={10}
                  value={sourceText}
                  onChange={(event) => {
                    setSourceText(event.target.value);
                    if (sourceLabel === "手动输入") setSourceLabel("手动输入");
                  }}
                  showCount
                  maxLength={5000}
                />
              )}

              <div className="ai-copy-compose-actions">
                <div className="ai-copy-cost-estimate">
                  {isSandbox ? (
                    <>
                      <Text strong>本地演示 · 不扣积分</Text>
                      <Text type="secondary">只验证流程和页面，不会调用真实收费服务</Text>
                    </>
                  ) : (
                    <>
                      <Text strong>预计本次约 {estimatedCredits.toFixed(2)} 积分</Text>
                      <Text type="secondary">
                        输入 {capability?.input_price_credits_per_1k_tokens ?? "0.0015"}、
                        输出 {capability?.output_price_credits_per_1k_tokens ?? "0.003"} 积分/千 Token；最终按实际用量结算
                      </Text>
                    </>
                  )}
                </div>
                <Button
                  type="primary"
                  icon={<EditOutlined />}
                  size="large"
                  loading={loading}
                  onClick={requestSubmit}
                  disabled={!inputReady || !enabled}
                >
                  {mode === "generate" ? "生成口播文案" : "开始去重改写"}
                </Button>
              </div>
            </div>
          ) : (
            <div className="ai-copy-review-panel">
              <div className="ai-copy-summary-bar">
                <Space size={10} wrap>
                  <span className="ai-copy-summary-chip is-success"><CheckCircleFilled /> {comparisonRows.length}段已改写</span>
                  <span className={`ai-copy-summary-chip${pendingReviewRows.length ? " is-warning" : " is-success"}`}>
                    {pendingReviewRows.length ? <ExclamationCircleFilled /> : <CheckCircleFilled />}
                    {pendingReviewRows.length}处需核对
                  </span>
                  <span className="ai-copy-summary-chip is-info"><InfoCircleFilled /> 事实信息已保留</span>
                  {lastResponse?.charged_credits != null && (
                    <span className="ai-copy-summary-chip is-info">
                      本次扣费 {lastResponse.charged_credits.toFixed(2)} 积分
                    </span>
                  )}
                </Space>
                <Segmented
                  aria-label="段落筛选"
                  size="small"
                  value={rowFilter}
                  onChange={(value) => setRowFilter(value as "all" | "review")}
                  options={[
                    { label: "全部段落", value: "all" },
                    { label: `需核对 ${pendingReviewRows.length || ""}`.trim(), value: "review" },
                  ]}
                />
              </div>

              <div className="ai-copy-comparison-table" role="table" aria-label="文案逐段对照">
                <div className="ai-copy-comparison-head" role="row">
                  <span>段落</span>
                  <span>原文</span>
                  <span>改写后</span>
                  <span>状态</span>
                </div>
                <div className="ai-copy-comparison-body">
                  {visibleRows.length ? visibleRows.map((row) => {
                    const isReviewed = reviewedRowIds.includes(row.id);
                    const isPending = row.needsReview && !isReviewed;
                    return (
                      <article key={row.id} className={`ai-copy-comparison-row${isPending ? " needs-review" : ""}`} role="row">
                        <span className="ai-copy-row-number">{String(row.id).padStart(2, "0")}</span>
                        <div className="ai-copy-row-source">{row.source}</div>
                        <div className="ai-copy-row-result">
                          {highlightedCopy(row.result, attentionTerms)}
                          {reasonRowId === row.id && (
                            <div className="ai-copy-review-reason">
                              {lastResponse?.compliance_notes[0] || "这段包含需要人工确认的名称、数据或效果表述。"}
                            </div>
                          )}
                        </div>
                        <div className="ai-copy-row-status">
                          {isPending ? (
                            <>
                              <span className="is-warning"><ExclamationCircleFilled /> 需核对</span>
                              <Button type="link" size="small" onClick={() => setReasonRowId(reasonRowId === row.id ? null : row.id)}>查看原因</Button>
                              <Button size="small" onClick={() => handleConfirmRow(row.id)}>确认本段</Button>
                            </>
                          ) : (
                            <span className="is-success"><CheckCircleFilled /> {row.needsReview ? "已确认" : "已优化"}</span>
                          )}
                        </div>
                      </article>
                    );
                  }) : (
                    <div className="ai-copy-review-empty">
                      <CheckCircleFilled /> 没有待确认内容
                    </div>
                  )}
                </div>
              </div>

              <footer className="ai-copy-actionbar">
                <Text>已核对 <strong>{reviewedCount}</strong> / {comparisonRows.length} 段</Text>
                <Space wrap>
                  <Button icon={<CopyOutlined />} onClick={() => handleCopy(activeText)}>复制全文</Button>
                  <Button icon={<SaveOutlined />} onClick={() => toast.success("草稿已保存在本机")}>保存草稿</Button>
                  <Button type="primary" icon={<ArrowRightOutlined />} iconPosition="end" onClick={handleContinue}>
                    确认并继续制作
                  </Button>
                </Space>
              </footer>
            </div>
          )}
        </Spin>
      </section>

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
                    <Text type="secondary">{item.target_length} 字 · {item.result_variants.length || (item.result_text ? 1 : 0)} 版</Text>
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Drawer>
    </div>
  );
}
