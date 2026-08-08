/**
 * 后端 API 客户端 —— 所有请求走 Vite 代理 /api → localhost:2001
 */

import type {
  AdminStatusResponse,
  AsrCapabilityResponse,
  AnalyticsResponse,
  AvatarAsset,
  AvatarCapability,
  AvatarJob,
  AvatarJobCreateRequest,
  CandidateListResponse,
  CopywritingCapabilitiesResponse,
  CopywritingDetailResponse,
  CopywritingGenerateRequest,
  CopywritingResponse,
  CopywritingRewriteRequest,
  CopywritingSummaryResponse,
  CreditAdjustRequest,
  CreditBalanceResponse,
  AdminLoginResponse,
  CustomerLoginResponse,
  CrawlerBatchListResponse,
  CrawlerBatchResponse,
  CrawlerBrowserDiscoveryCapabilities,
  CrawlerBrowserDiscoveryStartResponse,
  CrawlerCandidateMediaPreviewResponse,
  CrawlerCapabilitiesResponse,
  CrawlerDoubaoJobListResponse,
  CrawlerDoubaoMobileCapabilitiesResponse,
  CrawlerDoubaoWorkerStartResponse,
  CrawlerHotWordsResponse,
  CrawlerLinkTranscriptionCapabilities,
  CrawlerLinkTranscriptionPreview,
  CrawlerLinkTranscriptionResult,
  CrawlerOfficialHotMonitorResponse,
  CrawlerOriginalScriptResponse,
  CrawlerPreviewResponse,
  CrawlerSearchRequest,
  XiaohongshuManualMaterialInput,
  XiaohongshuManualMaterialResponse,
  EditTemplate,
  PipelineFromCandidateRequest,
  GuidedPipelinePreflight,
  GuidedPipelineRequest,
  PipelineReviewDraft,
  PipelineResponse,
  ProductionBatch,
  ProductionBatchPublishPreflight,
  ProductionBatchPreflight,
  ProductionBatchReviewResult,
  ProductionBatchSourceItem,
  ProductionPublishTarget,
  ProductionProfile,
  ProductionWorkspace,
  ProductionWorkspaceConfiguration,
  PublishFeedback,
  PublishMetadataResponse,
  FeedbackRecommendations,
  KeywordRunPreflight,
  PublishAsset,
  PublishAssetListResponse,
  PublishBatchListResponse,
  PublishBatchResponse,
  PublishConnection,
  PublishConnectionStartResponse,
  PublishConfigResponse,
  PublishPlatformConfig,
  PublishPlatformConfigUpdate,
  PublishPlatformsResponse,
  PublishPreflightResponse,
  PublishResponse,
  PublishAccount,
  StepKindsResponse,
  SubtitleStatusResponse,
  TaskListResponse,
  TemplateCreateRequest,
  TemplateListResponse,
  TranscriptionResponse,
  VideoCapabilitiesResponse,
  VideoEditorAnalysis,
  VideoEditorBatch,
  VideoEditorBatchListResponse,
  VideoEditorBgmAsset,
  VideoEditorBgmListResponse,
  VideoEditorJob,
  VideoEditorJobListResponse,
  VideoEditorLocalModel,
  VideoEditorLocalModelListResponse,
  VideoEditorOutputProfile,
  VideoEditorPreflightResponse,
  VideoEditorSourceListResponse,
  VideoEditorVisualAsset,
  VideoEditRequest,
  VideoEditResponse,
  VoiceoverDraftResponse,
} from "./types";

const BASE = "/api/v1";

// 客户/管理员登录 token（localStorage），请求时按身份携带
const CUSTOMER_TOKEN_KEY = "vi_customer_token";
const ADMIN_TOKEN_KEY = "vi_admin_token";

function getStoredToken(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function getCustomerToken(): string | null {
  return getStoredToken(CUSTOMER_TOKEN_KEY);
}

export function getAdminToken(): string | null {
  return getStoredToken(ADMIN_TOKEN_KEY);
}

export function clearCustomerSession(): void {
  const token = getCustomerToken();
  try {
    localStorage.removeItem(CUSTOMER_TOKEN_KEY);
    localStorage.removeItem("vi_customer_name");
    localStorage.removeItem("vi_customer_code");
  } catch {
    // 忽略存储异常
  }
  if (token) {
    void fetch(`${BASE}/auth/logout`, {
      method: "POST",
      headers: { "X-Customer-Token": token },
      credentials: "include",
    }).catch(() => undefined);
  }
}

export function clearAdminSession(): void {
  const token = getAdminToken();
  try {
    localStorage.removeItem(ADMIN_TOKEN_KEY);
  } catch {
    // 忽略存储异常
  }
  if (token) {
    void fetch(`${BASE}/auth/logout`, {
      method: "POST",
      headers: { "X-Admin-Token": token },
      credentials: "include",
    }).catch(() => undefined);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const customerToken = getCustomerToken();
  const adminToken = getAdminToken();
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  // 客户 token 优先；管理操作由后端按 X-Admin-Token 单独校验
  if (customerToken && !headers.has("X-Customer-Token")) {
    headers.set("X-Customer-Token", customerToken);
  }
  if (adminToken && !headers.has("X-Admin-Token")) {
    headers.set("X-Admin-Token", adminToken);
  }
  const method = (init?.method || "GET").toUpperCase();
  const canRetry = method === "GET";
  const run = () =>
    fetch(`${BASE}${path}`, {
      ...init,
      headers,
      credentials: "include",
    });
  let resp: Response;
  try {
    resp = await run();
    if (canRetry && [502, 503].includes(resp.status)) {
      resp = await run();
    }
  } catch {
    if (!canRetry) {
      throw new Error("网络连接失败，请检查后端服务是否已启动。");
    }
    try {
      resp = await run();
    } catch {
      throw new Error("网络连接失败，请检查后端服务是否已启动。");
    }
  }
  if (!resp.ok) {
    if (resp.status === 401) {
      const body = await resp.json().catch(() => ({}));
      const errorMessage =
        typeof body?.message === "string"
          ? body.message
          : typeof body?.detail === "string"
            ? body.detail
            : "";
      if (errorMessage.includes("管理员登录已过期") || errorMessage.includes("管理员账号或密码")) {
        // 管理员凭证过期（如后端重启后内存凭证失效）：清除后刷新，
        // 由登录页/管理入口引导重新登录
        clearAdminSession();
        window.location.reload();
        throw new Error("管理员登录已过期，请重新登录。");
      }
      if (customerToken && !path.startsWith("/auth/")) {
        // 客户登录过期：清会话，由登录页兜底
        clearCustomerSession();
        window.location.reload();
      }
      const detail = typeof body.detail === "string"
        ? body.detail
        : typeof body.detail?.message === "string"
          ? body.detail.message
          : undefined;
      const statusMessages: Record<number, string> = {
        400: "请求参数错误",
        404: "请求的资源不存在",
        500: "服务器内部错误",
        502: "后端服务未响应",
        503: "服务暂时不可用",
      };
      throw new Error(
        detail || body.message || statusMessages[resp.status] || `请求失败: ${resp.status}`,
      );
    }
  }
  // 删除接口以 204 表示已完成且不返回 JSON。继续解析响应体会把成功误判为失败，
  // 从而阻断调用方即时更新页面列表。
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

/* ---- 候选搜索 ---- */

export function searchCandidates(
  keyword: string,
  limit = 10,
  platforms: string[] = [],
  category?: string,
  page = 1,
): Promise<CandidateListResponse> {
  return request("/candidates/search", {
    method: "POST",
    body: JSON.stringify({ keyword, limit, platforms, category, page }),
  });
}

/* ---- 转写任务 ---- */

export function createTranscription(
  mediaName: string,
  rightsConfirmed = true,
): Promise<TranscriptionResponse> {
  return request("/transcriptions", {
    method: "POST",
    body: JSON.stringify({
      media_name: mediaName,
      rights_confirmed: rightsConfirmed,
    }),
  });
}

/** 通过视频直链创建转写任务 */
export function createTranscriptionByUrl(
  url: string,
  rightsConfirmed = true,
  modelName = "large-v3-turbo",
  rightsHolder = "本人/公司已授权",
): Promise<TranscriptionResponse> {
  return request("/transcriptions/url", {
    method: "POST",
    body: JSON.stringify({
      url,
      rights_confirmed: rightsConfirmed,
      rights_holder: rightsHolder,
      model_name: modelName,
    }),
  });
}

export function getTranscription(taskId: string): Promise<TranscriptionResponse> {
  return request(`/transcriptions/${taskId}`);
}

export function listTranscriptions(): Promise<TranscriptionResponse[]> {
  return request("/transcriptions");
}

export function getAsrConfig(): Promise<AsrCapabilityResponse> {
  return request("/transcriptions/config");
}

export function authorizeCloudAsr(
  perTaskCapCny = 0.2,
): Promise<AsrCapabilityResponse> {
  return request("/transcriptions/authorization", {
    method: "POST",
    body: JSON.stringify({
      confirmed: true,
      per_task_cap_cny: perTaskCapCny,
    }),
  });
}

export function reconnectTranscription(
  taskId: string,
): Promise<TranscriptionResponse> {
  return request(`/transcriptions/${taskId}/reconnect`, { method: "POST" });
}

export function retryTranscription(
  taskId: string,
): Promise<TranscriptionResponse> {
  return request(`/transcriptions/${taskId}/retry`, { method: "POST" });
}

export function clearTranscriptionHistory(): Promise<{ deleted_count: number }> {
  return request("/transcriptions/history", { method: "DELETE" });
}

export function deleteTask(taskId: string): Promise<{ task_id: string; deleted: boolean }> {
  return request(`/tasks/${taskId}`, { method: "DELETE" });
}

export function importManualTranscript(params: {
  text: string;
  rightsHolder: string;
  mediaName: string;
  candidateId?: string;
  sourceUrl?: string;
}): Promise<TranscriptionResponse> {
  return request("/transcriptions/manual-text", {
    method: "POST",
    body: JSON.stringify({
      text: params.text,
      rights_confirmed: true,
      rights_holder: params.rightsHolder,
      media_name: params.mediaName,
      candidate_id: params.candidateId || null,
      source_url: params.sourceUrl || null,
    }),
  });
}

export function saveTranscriptionRevision(params: {
  taskId: string;
  segments: Array<{
    start: number | null;
    end: number | null;
    text: string;
    confidence: number | null;
    needs_review: boolean;
    reviewed: boolean;
    quality_status?: string;
    quality_source?: string;
    quality_note?: string | null;
    alternatives?: string[];
  }>;
  reviewer: string;
  approve: boolean;
}): Promise<Record<string, unknown>> {
  return request(`/transcriptions/${params.taskId}/revisions`, {
    method: "POST",
    body: JSON.stringify({
      segments: params.segments,
      reviewer: params.reviewer,
      approve: params.approve,
    }),
  });
}

export async function exportTranscription(
  taskId: string,
  format: "txt" | "json" | "srt" | "ass",
): Promise<Blob> {
  const resp = await fetch(`${BASE}/transcriptions/${taskId}/export?format=${format}`);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || body.message || "导出失败");
  }
  return resp.blob();
}

/** 上传文件并转写 */
export async function uploadAndTranscribe(
  file: File,
  modelName = "large-v3-turbo",
  rightsHolder = "本人/公司已授权",
  language = "zh",
  candidateId = "",
): Promise<TranscriptionResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("rights_confirmed", "true");
  formData.append("rights_holder", rightsHolder);
  formData.append("model_name", modelName);
  formData.append("language", language);
  if (candidateId) formData.append("candidate_id", candidateId);

  const resp = await fetch(`${BASE}/transcriptions/upload`, {
    method: "POST",
    body: formData,
  });

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || body.message || "文件上传失败");
  }
  return resp.json();
}

/* ---- 流水线 ---- */

export function createPipeline(
  keyword: string,
  config: Record<string, unknown> = {},
): Promise<PipelineResponse> {
  return request("/pipelines", {
    method: "POST",
    body: JSON.stringify({ keyword, config }),
  });
}

export function createPipelineFromCandidate(
  params: PipelineFromCandidateRequest & { idempotencyKey: string },
): Promise<PipelineResponse> {
  return request("/pipelines/from-candidate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": params.idempotencyKey,
    },
    body: JSON.stringify({
      candidate_id: params.candidate_id,
      rights_confirmed: params.rights_confirmed,
      rights_holder: params.rights_holder,
      model_name: params.model_name || "large-v3-turbo",
      hotwords: params.hotwords || "",
      target_length: params.target_length ?? 300,
      tone: params.tone || "casual",
      target_audience: params.target_audience || "",
      style_prompt: params.style_prompt || "",
      variant_count: params.variant_count ?? 2,
    }),
  });
}

export function preflightGuidedPipeline(
  params: GuidedPipelineRequest,
): Promise<GuidedPipelinePreflight> {
  return request("/pipelines/guided/preflight", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function createGuidedPipeline(
  params: GuidedPipelineRequest & { idempotencyKey: string },
): Promise<PipelineResponse> {
  const { idempotencyKey, ...body } = params;
  return request("/pipelines/guided", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(body),
  });
}

export function getPipelineReviewDraft(runId: string): Promise<PipelineReviewDraft> {
  return request(`/pipelines/${encodeURIComponent(runId)}/review-draft`);
}

export function getPipeline(runId: string): Promise<PipelineResponse> {
  return request(`/pipelines/${runId}`);
}

export function reviewPipeline(
  runId: string,
  params: { approved: boolean; reviewer: string; note?: string; approvedText?: string },
): Promise<PipelineResponse> {
  return request(`/pipelines/${encodeURIComponent(runId)}/review`, {
    method: "POST",
    body: JSON.stringify({
      approved: params.approved,
      reviewer: params.reviewer,
      note: params.note || "",
      approved_text: params.approvedText || "",
    }),
  });
}

export function retryPipeline(runId: string, idempotencyKey: string): Promise<PipelineResponse> {
  return request(`/pipelines/${encodeURIComponent(runId)}/retry`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
  });
}

export function listProductionProfiles(): Promise<{ items: ProductionProfile[] }> {
  return request("/production/profiles");
}

export function createProductionProfile(
  params: Omit<ProductionProfile, "profile_id" | "created_at" | "updated_at" | "edit_template_id">
    & { edit_template_id?: string | null },
): Promise<ProductionProfile> {
  return request("/production/profiles", { method: "POST", body: JSON.stringify(params) });
}

export function getProductionWorkspaceConfiguration(): Promise<ProductionWorkspaceConfiguration> {
  return request("/production/workspace/configuration");
}

export function saveProductionWorkspaceConfiguration(params: {
  rightsHolder: string;
  agreementAccepted: boolean;
  defaultProfileId?: string | null;
  defaultPublishPlatforms?: string[];
  copywritingEstimatedCostCny?: number | null;
  avatarEstimatedCostCny?: number | null;
  bundledCompute?: boolean;
}): Promise<ProductionWorkspaceConfiguration> {
  return request("/production/workspace/configuration", {
    method: "PUT",
    body: JSON.stringify({
      rights_holder: params.rightsHolder,
      agreement_accepted: params.agreementAccepted,
      default_profile_id: params.defaultProfileId || null,
      default_publish_platforms: params.defaultPublishPlatforms || ["douyin"],
      copywriting_estimated_cost_cny: params.copywritingEstimatedCostCny ?? null,
      avatar_estimated_cost_cny: params.avatarEstimatedCostCny ?? null,
      bundled_compute: params.bundledCompute ?? true,
    }),
  });
}

export function listProductionBatches(): Promise<{ items: ProductionBatch[] }> {
  return request("/production/batches");
}

export function createProductionBatch(params: {
  name: string;
  profile_id: string;
  candidate_ids?: string[];
  items?: ProductionBatchSourceItem[];
  idempotencyKey: string;
}): Promise<ProductionBatch> {
  const { idempotencyKey, ...body } = params;
  return request("/production/batches", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(body),
  });
}

export function getProductionBatch(batchId: string): Promise<ProductionBatch> {
  return request(`/production/batches/${encodeURIComponent(batchId)}`);
}

export function getProductionBatchWorkspace(batchId: string): Promise<ProductionWorkspace> {
  return request(`/production/batches/${encodeURIComponent(batchId)}/workspace`);
}

export function reviewProductionBatchItems(
  batchId: string,
  params: { stage: "transcript" | "script" | "output" | "publish"; reviewer: string; items: Array<{ run_id: string; approved_text?: string; note?: string; creative_plan?: { hook: string; key_points: string[]; call_to_action: string; visual_sections: string[] }; publish_draft?: { title: string; description: string; tags: string[] } }> },
): Promise<{ batch: ProductionBatch; results: ProductionBatchReviewResult[] }> {
  return request(`/production/batches/${encodeURIComponent(batchId)}/reviews`, {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function preflightProductionBatch(batchId: string, params: {
  rightsHolder: string;
  rightsConfirmed: boolean;
  publishPlatforms: string[];
  concurrency: number;
  maxTotalCostCny?: number | null;
  paidActionsConfirmed?: boolean;
  automationMode?: "manual" | "auto";
}): Promise<ProductionBatchPreflight> {
  return request(`/production/batches/${encodeURIComponent(batchId)}/preflight`, {
    method: "POST",
    body: JSON.stringify({
      rights_holder: params.rightsHolder,
      rights_confirmed: params.rightsConfirmed,
      publish_platforms: params.publishPlatforms,
      concurrency: params.concurrency,
      max_total_cost_cny: params.maxTotalCostCny ?? null,
      paid_actions_confirmed: params.paidActionsConfirmed ?? false,
      automation_mode: params.automationMode ?? "manual",
    }),
  });
}

export function startProductionBatch(batchId: string, params: {
  rightsHolder: string;
  rightsConfirmed: boolean;
  publishPlatforms: string[];
  concurrency: number;
  maxTotalCostCny?: number | null;
  paidActionsConfirmed?: boolean;
  automationMode?: "manual" | "auto";
  idempotencyKey: string;
}): Promise<ProductionBatch> {
  return request(`/production/batches/${encodeURIComponent(batchId)}/start`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": params.idempotencyKey,
    },
    body: JSON.stringify({
      rights_holder: params.rightsHolder,
      rights_confirmed: params.rightsConfirmed,
      publish_platforms: params.publishPlatforms,
      concurrency: params.concurrency,
      max_total_cost_cny: params.maxTotalCostCny ?? null,
      paid_actions_confirmed: params.paidActionsConfirmed ?? false,
      automation_mode: params.automationMode ?? "manual",
    }),
  });
}

export function pauseProductionBatch(batchId: string): Promise<ProductionBatch> {
  return request(`/production/batches/${encodeURIComponent(batchId)}/pause`, { method: "POST" });
}

export function resumeProductionBatch(batchId: string): Promise<ProductionBatch> {
  return request(`/production/batches/${encodeURIComponent(batchId)}/resume`, { method: "POST" });
}

export function retryProductionBatchFailed(batchId: string): Promise<ProductionBatch> {
  return request(`/production/batches/${encodeURIComponent(batchId)}/retry-failed`, { method: "POST" });
}

export function preflightProductionBatchPublish(batchId: string, params: {
  runIds: string[];
  targets?: ProductionPublishTarget[];
  publishPlatforms?: string[];
}): Promise<ProductionBatchPublishPreflight> {
  return request(`/production/batches/${encodeURIComponent(batchId)}/publish/preflight`, {
    method: "POST",
    body: JSON.stringify({
      run_ids: params.runIds,
      targets: params.targets || [],
      publish_platforms: params.publishPlatforms || params.targets?.map((item) => item.platform) || [],
    }),
  });
}

export function confirmProductionBatchPublish(batchId: string, params: {
  runIds: string[];
  targets?: ProductionPublishTarget[];
  publishPlatforms?: string[];
  idempotencyKey: string;
}): Promise<ProductionBatch> {
  return request(`/production/batches/${encodeURIComponent(batchId)}/publish`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": params.idempotencyKey,
    },
    body: JSON.stringify({
      run_ids: params.runIds,
      targets: params.targets || [],
      publish_platforms: params.publishPlatforms || params.targets?.map((item) => item.platform) || [],
      confirmation_accepted: true,
    }),
  });
}

export function preflightKeywordAutoRun(params: {
  keyword: string; candidate_count: number; profile_id: string; rights_holder: string; rights_confirmed: boolean; publish_platforms: string[];
}): Promise<KeywordRunPreflight> {
  return request("/production/keyword-runs/preflight", { method: "POST", body: JSON.stringify(params) });
}

export function startKeywordAutoRun(params: {
  keyword: string; candidate_count: number; profile_id: string; rights_holder: string; rights_confirmed: boolean; publish_platforms: string[];
}): Promise<{ run_id: string; status: string; message: string }> {
  return request("/production/keyword-runs", { method: "POST", body: JSON.stringify(params) });
}

export function listPublishFeedback(): Promise<{ items: PublishFeedback[] }> {
  return request("/feedback");
}

export function createPublishFeedback(params: Omit<PublishFeedback, "feedback_id" | "pipeline_run_id" | "platform" | "recorded_at">): Promise<PublishFeedback> {
  return request("/feedback", { method: "POST", body: JSON.stringify(params) });
}

export function getFeedbackRecommendations(): Promise<FeedbackRecommendations> {
  return request("/feedback/recommendations");
}

export function createVoiceoverDraft(params: {
  taskId: string;
  targetSeconds: number;
  speechRate?: number;
  platform?: string;
  targetAudience?: string;
  tone?: string;
  variantCount?: number;
}): Promise<VoiceoverDraftResponse> {
  return request(`/transcriptions/${params.taskId}/voiceover-drafts`, {
    method: "POST",
    body: JSON.stringify({
      target_seconds: params.targetSeconds,
      speech_rate: params.speechRate ?? 1,
      platform: params.platform ?? "douyin",
      target_audience: params.targetAudience ?? "",
      tone: params.tone ?? "casual",
      variant_count: params.variantCount ?? 2,
    }),
  });
}

export function listVoiceoverDrafts(
  taskId: string,
  limit = 50,
): Promise<VoiceoverDraftResponse[]> {
  return request(`/transcriptions/${taskId}/voiceover-drafts?limit=${limit}`);
}

export function updateVoiceoverDraft(params: {
  taskId: string;
  draftId: string;
  resultText: string;
  resultVariants: string[];
}): Promise<VoiceoverDraftResponse> {
  return request(`/transcriptions/${params.taskId}/voiceover-drafts/${params.draftId}`, {
    method: "PATCH",
    body: JSON.stringify({
      result_text: params.resultText,
      result_variants: params.resultVariants,
    }),
  });
}

export function createComplianceDraft(params: {
  taskId: string;
  parentDraftId: string;
}): Promise<VoiceoverDraftResponse> {
  return request(`/transcriptions/${params.taskId}/compliance-drafts`, {
    method: "POST",
    body: JSON.stringify({ parent_draft_id: params.parentDraftId }),
  });
}

export function listComplianceDrafts(taskId: string, limit = 50): Promise<VoiceoverDraftResponse[]> {
  return request(`/transcriptions/${taskId}/compliance-drafts?limit=${limit}`);
}

export function listPipelines(params: { candidateId?: string; limit?: number } = {}): Promise<PipelineResponse[]> {
  const query = new URLSearchParams();
  if (params.candidateId) query.set("candidate_id", params.candidateId);
  if (params.limit) query.set("limit", String(params.limit));
  const suffix = query.size ? `?${query.toString()}` : "";
  return request(`/pipelines${suffix}`);
}

export function deletePipeline(runId: string): Promise<{ run_id: string; deleted: boolean }> {
  return request(`/pipelines/${runId}`, { method: "DELETE" });
}

export function deleteAllPipelines(): Promise<{ deleted_count: number }> {
  return request("/pipelines", { method: "DELETE" });
}

/* ---- 任务列表 ---- */

export function listTasks(): Promise<TaskListResponse> {
  return request("/tasks");
}

/* ---- 管理后台 ---- */

export function getAdminStatus(): Promise<AdminStatusResponse> {
  return request("/admin/status");
}

/* ---- 积分账户 ---- */

export function getCredits(): Promise<CreditBalanceResponse> {
  return request("/credits");
}

/** 管理员登录（账号+密码；兼容旧版 password-only） */
export function adminLogin(
  password: string,
  username = "admin",
): Promise<AdminLoginResponse> {
  return request("/auth/admin-login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

/** 客户激活码登录 */
export function customerLogin(code: string): Promise<CustomerLoginResponse> {
  return request("/auth/customer-login", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function adjustCredits(
  body: CreditAdjustRequest,
  adminToken?: string,
): Promise<CreditBalanceResponse> {
  return request("/credits/adjust", {
    method: "POST",
    body: JSON.stringify(body),
    headers: adminToken ? { "X-Admin-Token": adminToken } : undefined,
  });
}

/* ---- 充值请求（客户提交，管理员审批） ---- */

export interface RechargeRequestItem {
  id: number;
  customer_code: string;
  amount: string;
  reason: string | null;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  updated_at: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
}

/** 客户提交充值请求 */
export function createRechargeRequest(body: {
  amount: number;
  reason?: string;
}): Promise<RechargeRequestItem> {
  return request("/credits/recharge-request", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 客户查看自己的充值请求 */
export function listMyRechargeRequests(): Promise<RechargeRequestItem[]> {
  return request("/credits/recharge-requests/mine");
}

/** 管理员查看所有充值请求 */
export function listRechargeRequests(params?: {
  status?: string;
  customer_code?: string;
}): Promise<RechargeRequestItem[]> {
  const query = new URLSearchParams();
  if (params?.status) query.set("status", params.status);
  if (params?.customer_code) query.set("customer_code", params.customer_code);
  const qs = query.toString();
  return request(`/credits/recharge-requests${qs ? `?${qs}` : ""}`);
}

/** 管理员审批充值请求 */
export function reviewRechargeRequest(
  requestId: number,
  body: { status: "approved" | "rejected"; review_note?: string },
  adminToken: string,
): Promise<RechargeRequestItem> {
  return request(`/credits/recharge-requests/${requestId}/review`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "X-Admin-Token": adminToken },
  });
}

/* ---- 客户激活码管理（管理员） ---- */

export interface CustomerCodeItem {
  code: string;
  name: string;
  enabled: boolean;
  initial_credits: string;
  balance: string;
  created_at: string;
}

export function generateCustomerCodes(params: {
  name: string;
  initial_credits: string;
  count: number;
}): Promise<CustomerCodeItem[]> {
  return request("/admin/codes/generate", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function listCustomerCodes(): Promise<CustomerCodeItem[]> {
  return request("/admin/codes");
}

export function toggleCustomerCode(code: string): Promise<CustomerCodeItem> {
  return request(`/admin/codes/${encodeURIComponent(code)}/toggle`, {
    method: "POST",
  });
}

export interface AdminAccountItem {
  username: string;
  created_at: string;
}

export function createAdminAccount(params: {
  username: string;
  password: string;
}): Promise<AdminAccountItem> {
  return request("/admin/accounts", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function listAdminAccounts(): Promise<AdminAccountItem[]> {
  return request("/admin/accounts");
}

export interface PricingItem {
  key: string;
  label: string;
  default: string;
  value: string;
  overridden: boolean;
  updated_at?: string | null;
}

export function listPricing(): Promise<PricingItem[]> {
  return request("/admin/pricing");
}

export function updatePricing(key: string, value: string): Promise<PricingItem> {
  return request("/admin/pricing", {
    method: "PUT",
    body: JSON.stringify({ key, value }),
  });
}

export function resetAdminPassword(
  username: string,
  password: string,
): Promise<AdminAccountItem> {
  return request(`/admin/accounts/${encodeURIComponent(username)}/password`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

/* ---- Dashboard 统计 ---- */

export interface DashboardStatsResponse {
  totalVideos: number;
  todayProduced: number;
  totalCandidates: number;
  todayCandidates: number;
  activeTasks: number;
  completedTasks: number;
  failedTasks: number;
  totalTasks: number;
  successRate: number;
  pipelineCount: number;
}

export function getDashboardStats(): Promise<DashboardStatsResponse> {
  return request("/dashboard/stats");
}

/* ---- 关键词爬虫 ---- */

export async function getCrawlerCapabilities(): Promise<CrawlerCapabilitiesResponse> {
  const caps = await request<CrawlerCapabilitiesResponse>("/crawler/capabilities");
  // 后端未上线官方热榜字段时兜底，避免页面白屏
  return {
    ...caps,
    official_hot_billboard: caps.official_hot_billboard ?? null,
    official_hot_words: caps.official_hot_words ?? null,
    hotspot_browser: caps.hotspot_browser ?? null,
    platform_browsers: caps.platform_browsers ?? [],
  };
}

export function getCrawlerBrowserDiscoveryCapabilities(
  platform: "douyin" | "xiaohongshu" | "kuaishou" | "bilibili" = "douyin",
): Promise<CrawlerBrowserDiscoveryCapabilities> {
  return request(`/crawler/browser-discovery/${platform}/capabilities`);
}

export function startCrawlerBrowserDiscovery(
  platform: "douyin" | "xiaohongshu" | "kuaishou" | "bilibili" = "douyin",
): Promise<CrawlerBrowserDiscoveryStartResponse> {
  return request(`/crawler/browser-discovery/${platform}/start`, { method: "POST" });
}

/** 保存操作者已经看见的素材；服务端不会打开或抓取小红书链接。 */
export function importXiaohongshuManualMaterials(
  items: XiaohongshuManualMaterialInput[],
): Promise<XiaohongshuManualMaterialResponse> {
  return request("/crawler/manual-materials/xiaohongshu", {
    method: "POST",
    body: JSON.stringify({ items }),
  });
}

/** 官方实时热点词（用于搜索框建议）；后端未上线时由调用方 catch 降级 */
export async function getCrawlerHotWords(): Promise<CrawlerHotWordsResponse> {
  const resp = await request<CrawlerHotWordsResponse>("/crawler/hotwords");
  return { words: Array.isArray(resp?.words) ? resp.words : [] };
}

/** 官方热榜一键监测：内部先执行到期复爬，再同步热榜并匹配关键词 */
export function officialHotMonitor(keyword?: string): Promise<CrawlerOfficialHotMonitorResponse> {
  return request("/crawler/official-hot/monitor", {
    method: "POST",
    body: JSON.stringify(keyword ? { keyword } : {}),
  });
}

/** 基于平台信息生成数字人口播文案。 */
export function generateOriginalScript(videoId: string): Promise<CrawlerOriginalScriptResponse> {
  return request(`/crawler/candidates/${encodeURIComponent(videoId)}/original-script`, {
    method: "POST",
  });
}

export function previewCrawlerBatch(
  params: CrawlerSearchRequest,
): Promise<CrawlerPreviewResponse> {
  return request("/crawler/preview", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function createCrawlerBatch(
  params: CrawlerSearchRequest,
): Promise<CrawlerBatchResponse> {
  return request("/crawler/batches", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function listCrawlerBatches(): Promise<CrawlerBatchListResponse> {
  return request("/crawler/batches");
}

export function getCrawlerBatch(batchId: string): Promise<CrawlerBatchResponse> {
  return request(`/crawler/batches/${batchId}`);
}

export function probeCrawlerBatchCopy(batchId: string): Promise<CrawlerBatchResponse> {
  return request(`/crawler/batches/${batchId}/copy-probes`, { method: "POST" });
}

export function recheckCrawlerBatchLegacyNoText(
  batchId: string,
): Promise<CrawlerBatchResponse> {
  return request(`/crawler/batches/${batchId}/copy-probes/recheck-v1-no-text`, {
    method: "POST",
  });
}

export function deleteCrawlerBatch(batchId: string): Promise<{ batch_id: string; deleted: boolean }> {
  return request(`/crawler/batches/${batchId}`, { method: "DELETE" });
}

export function previewCrawlerCandidateMedia(
  candidateId: string,
): Promise<CrawlerCandidateMediaPreviewResponse> {
  return request(`/crawler/candidates/${candidateId}/media-preview`);
}

export function createCrawlerCandidateTranscription(params: {
  candidateId: string;
  rightsHolder: string;
  rightsConfirmed: boolean;
  modelName?: string;
  hotwords?: string;
  idempotencyKey: string;
}): Promise<TranscriptionResponse> {
  return request(`/crawler/candidates/${params.candidateId}/transcriptions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": params.idempotencyKey,
    },
    body: JSON.stringify({
      rights_confirmed: params.rightsConfirmed,
      rights_holder: params.rightsHolder,
      model_name: params.modelName || "large-v3-turbo",
      hotwords: params.hotwords || "",
    }),
  });
}

export function getCrawlerLinkTranscriptionCapabilities(): Promise<CrawlerLinkTranscriptionCapabilities> {
  return request("/crawler/link-transcriptions/capabilities");
}

export function previewCrawlerLinkTranscription(shareText: string): Promise<CrawlerLinkTranscriptionPreview> {
  return request("/crawler/link-transcriptions/preview", {
    method: "POST",
    body: JSON.stringify({ share_text: shareText }),
  });
}

export function createCrawlerLinkTranscription(params: {
  shareText: string; rightsHolder: string; rightsConfirmed: boolean; modelName?: string;
}): Promise<CrawlerLinkTranscriptionResult> {
  return request("/crawler/link-transcriptions", {
    method: "POST",
    body: JSON.stringify({ share_text: params.shareText, rights_holder: params.rightsHolder, rights_confirmed: params.rightsConfirmed, model_name: params.modelName || "large-v3-turbo" }),
  });
}

export function createCrawlerCandidateLinkTranscription(params: {
  candidateId: string; rightsHolder: string; rightsConfirmed: boolean; modelName?: string;
}): Promise<CrawlerLinkTranscriptionResult> {
  return request(`/crawler/link-transcriptions/candidates/${encodeURIComponent(params.candidateId)}`, {
    method: "POST",
    body: JSON.stringify({ rights_holder: params.rightsHolder, rights_confirmed: params.rightsConfirmed, model_name: params.modelName || "large-v3-turbo" }),
  });
}

export function fallbackCrawlerLinkTranscription(params: {
  shareText: string; workId: string; rightsHolder: string; rightsConfirmed: boolean; idempotencyKey: string; modelName?: string;
}): Promise<CrawlerLinkTranscriptionResult> {
  return request("/crawler/link-transcriptions/fallback", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": params.idempotencyKey },
    body: JSON.stringify({ share_text: params.shareText, work_id: params.workId, rights_holder: params.rightsHolder, rights_confirmed: params.rightsConfirmed, model_name: params.modelName || "large-v3-turbo", confirmed: true }),
  });
}

export function createCrawlerDoubaoJobs(candidateIds: string[]): Promise<CrawlerDoubaoJobListResponse> {
  return request("/crawler/doubao-browser/jobs", {
    method: "POST",
    body: JSON.stringify({ candidate_ids: candidateIds }),
  });
}

export function listCrawlerDoubaoJobs(candidateId?: string): Promise<CrawlerDoubaoJobListResponse> {
  const query = candidateId ? `?candidate_id=${encodeURIComponent(candidateId)}` : "";
  return request(`/crawler/doubao-browser/jobs${query}`);
}

export function retryCrawlerDoubaoJob(taskId: string): Promise<TranscriptionResponse> {
  return request(`/crawler/doubao-browser/jobs/${taskId}/retry`, {
    method: "POST",
  });
}

export function startCrawlerDoubaoWorker(): Promise<CrawlerDoubaoWorkerStartResponse> {
  return request("/crawler/doubao-browser/worker/start", {
    method: "POST",
  });
}

export async function getCrawlerDoubaoMobileCapabilities(): Promise<CrawlerDoubaoMobileCapabilitiesResponse> {
  const caps = await request<CrawlerDoubaoMobileCapabilitiesResponse>("/crawler/doubao-mobile/capabilities");
  // 后端未上线 prerequisites / estimated_cost_cny 字段时兜底
  return {
    ...caps,
    prerequisites: caps.prerequisites ?? null,
    estimated_cost_cny: caps.estimated_cost_cny ?? 0,
  };
}

export function createCrawlerDoubaoMobileJobs(candidateIds: string[]): Promise<CrawlerDoubaoJobListResponse> {
  return request("/crawler/doubao-mobile/jobs", {
    method: "POST",
    body: JSON.stringify({ candidate_ids: candidateIds }),
  });
}

export function listCrawlerDoubaoMobileJobs(candidateId?: string): Promise<CrawlerDoubaoJobListResponse> {
  const query = candidateId ? `?candidate_id=${encodeURIComponent(candidateId)}` : "";
  return request(`/crawler/doubao-mobile/jobs${query}`);
}

export function retryCrawlerDoubaoMobileJob(taskId: string): Promise<TranscriptionResponse> {
  return request(`/crawler/doubao-mobile/jobs/${taskId}/retry`, {
    method: "POST",
  });
}

export function startCrawlerDoubaoMobileWorker(): Promise<CrawlerDoubaoWorkerStartResponse> {
  return request("/crawler/doubao-mobile/worker/start", {
    method: "POST",
  });
}

/* ---- 文案生成 ---- */

export function rewriteCopywriting(params: {
  source_text: string;
  target_audience?: string;
  style_prompt?: string;
  tone?: string;
  variant_count?: number;
}): Promise<CopywritingResponse> {
  return request("/copywriting/rewrite", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function getCopywritingCapabilities(): Promise<CopywritingCapabilitiesResponse> {
  return request("/copywriting/capabilities");
}

export function listCopywritingTasks(limit = 50): Promise<CopywritingSummaryResponse[]> {
  return request(`/copywriting?limit=${limit}`);
}

export function clearCopywritingHistory(): Promise<{ deleted_count: number }> {
  return request("/copywriting/history", { method: "DELETE" });
}

export function getCopywritingTask(taskId: string): Promise<CopywritingDetailResponse> {
  return request(`/copywriting/${taskId}`);
}

export function generateCopywriting(
  params: CopywritingGenerateRequest,
): Promise<CopywritingResponse> {
  return request("/copywriting/generate", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function rewriteCopywritingV2(
  params: CopywritingRewriteRequest,
): Promise<CopywritingResponse> {
  return rewriteCopywriting(params);
}

/* ---- 多平台发布 ---- */

export function publishVideo(params: {
  video_path: string;
  platform: string;
  title: string;
  description?: string;
  tags?: string[];
}): Promise<PublishResponse> {
  return request("/publish", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function listPublishPlatforms(): Promise<PublishPlatformsResponse> {
  return request("/publish/platforms");
}

export function listPublishAccounts(platform?: string): Promise<PublishAccount[]> {
  const query = platform ? `?platform=${encodeURIComponent(platform)}` : "";
  return request(`/publish/accounts${query}`);
}

export interface PublishSafetyItem {
  platform: string;
  account_id: string;
  today_published: number;
  daily_limit: number;
  remaining_today: number;
  next_allowed_at: string | null;
  blocked: boolean;
  blocked_until: string | null;
  blocked_reason: string | null;
}

export function getPublishSafety(): Promise<PublishSafetyItem[]> {
  return request("/publish/safety");
}

export function resumePublishSafety(params: {
  platform: string;
  account_id?: string;
}): Promise<{ platform: string; account_id: string; resumed: boolean; message: string }> {
  return request("/publish/safety/resume", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function createPublishAccount(params: { platform: string; name: string }): Promise<PublishAccount> {
  return request("/publish/accounts", { method: "POST", body: JSON.stringify(params) });
}

export function connectPublishAccount(accountId: string): Promise<PublishAccount> {
  return request(`/publish/accounts/${encodeURIComponent(accountId)}/connect`, { method: "POST" });
}

export function verifyPublishAccount(accountId: string): Promise<PublishAccount> {
  return request(`/publish/accounts/${encodeURIComponent(accountId)}/verify`, { method: "POST" });
}

export function updatePublishAccount(accountId: string, params: { name?: string; auto_publish_authorized?: boolean }): Promise<PublishAccount> {
  return request(`/publish/accounts/${encodeURIComponent(accountId)}`, { method: "PATCH", body: JSON.stringify(params) });
}

export function getPublishAccountStatus(accountId: string): Promise<PublishAccount> {
  return request(`/publish/accounts/${encodeURIComponent(accountId)}/status`);
}

export function deletePublishAccount(accountId: string): Promise<void> {
  return request(`/publish/accounts/${encodeURIComponent(accountId)}`, { method: "DELETE" });
}

export function getPublishConfig(): Promise<PublishConfigResponse> {
  return request("/publish/config");
}

export function updatePublishConfig(
  platform: string,
  params: PublishPlatformConfigUpdate,
): Promise<PublishPlatformConfig> {
  return request(`/publish/config/${platform}`, {
    method: "PUT",
    body: JSON.stringify(params),
  });
}

export function getDouyinPublishConnection(): Promise<PublishConnection> {
  return request("/publish/connections/douyin");
}

export function startDouyinPublishConnection(): Promise<PublishConnectionStartResponse> {
  return request("/publish/connections/douyin/start", { method: "POST" });
}

export function disconnectDouyinPublishConnection(): Promise<PublishConnection> {
  return request("/publish/connections/douyin/disconnect", { method: "POST" });
}

export function listPublishAssets(): Promise<PublishAssetListResponse> {
  return request("/publish/assets");
}

export function importEditedVideoToPublish(taskId: string): Promise<PublishAsset> {
  return request(`/publish/assets/from-edit/${encodeURIComponent(taskId)}`, {
    method: "POST",
  });
}

export async function uploadPublishAsset(file: File): Promise<PublishAsset> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("rights_confirmed", "true");

  const resp = await fetch(`${BASE}/publish/assets/upload`, {
    method: "POST",
    body: formData,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || body.message || "上传发布成片失败");
  }
  return resp.json();
}

export function preflightPublish(params: {
  video_path: string;
  platforms: string[];
  title: string;
  description?: string;
  tags?: string[];
  account_ids?: Record<string, string>;
  native_music_mode?: "off" | "auto_recommended";
  native_music_hint?: string;
}): Promise<PublishPreflightResponse> {
  return request("/publish/preflight", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function generatePublishMetadata(params: {
  source_text: string;
  platforms?: string[];
  source_task_id?: string;
}): Promise<PublishMetadataResponse> {
  return request("/copywriting/publish-metadata", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function createPublishBatch(params: {
  video_path: string;
  platforms: string[];
  title: string;
  description?: string;
  tags?: string[];
  account_ids?: Record<string, string>;
  native_music_mode?: "off" | "auto_recommended";
  native_music_hint?: string;
  confirmation_accepted: boolean;
}): Promise<PublishBatchResponse> {
  return request("/publish/batches", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function listPublishBatches(): Promise<PublishBatchListResponse> {
  return request("/publish/batches");
}

export function recordManualPublishResult(
  taskId: string,
  params: {
    succeeded: boolean | null;
    platform_url?: string;
    platform_video_id?: string;
    note?: string;
  },
): Promise<PublishResponse> {
  return request(`/publish/tasks/${taskId}/manual-result`, {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function retryPublishTask(taskId: string): Promise<PublishResponse> {
  return request(`/publish/tasks/${taskId}/retry`, {
    method: "POST",
  });
}

export function preparePublishOfficialPage(taskId: string): Promise<PublishResponse> {
  return request(`/publish/tasks/${taskId}/prepare-official-page`, {
    method: "POST",
  });
}

export function confirmPublishTaskAuto(taskId: string): Promise<PublishResponse> {
  return request(`/publish/tasks/${taskId}/confirm-auto-publish`, {
    method: "POST",
    body: JSON.stringify({ confirmation_accepted: true }),
  });
}

export function resumePublishTask(taskId: string): Promise<PublishResponse> {
  return request(`/publish/tasks/${taskId}/resume`, {
    method: "POST",
  });
}

export function deletePublishTask(taskId: string): Promise<{ task_id: string; deleted: boolean }> {
  return request(`/publish/tasks/${taskId}`, { method: "DELETE" });
}

export function deletePublishTasks(taskIds: string[]): Promise<{ deleted_task_ids: string[]; deleted: number }> {
  return request("/publish/tasks/delete-batch", {
    method: "POST",
    body: JSON.stringify({ task_ids: taskIds }),
  });
}

/* ---- 数字人生成 ---- */

export function getAvatarCapabilities(): Promise<AvatarCapability> {
  return request("/avatar/capabilities");
}

export function listAvatarAssets(): Promise<AvatarAsset[]> {
  return request("/avatar/assets");
}

export async function uploadAvatarAsset(params: {
  kind: "avatar" | "voice";
  file: File;
  name?: string;
}): Promise<AvatarAsset> {
  const formData = new FormData();
  formData.append("kind", params.kind);
  formData.append("file", params.file);
  formData.append("name", params.name || params.file.name.replace(/\.[^.]+$/, ""));
  formData.append("rights_confirmed", "true");
  formData.append("rights_holder", "本人/公司已授权");

  const resp = await fetch(`${BASE}/avatar/assets/upload`, {
    method: "POST",
    body: formData,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || body.message || "上传数字人素材失败");
  }
  return resp.json();
}

async function uploadCloudAvatarMaterial(
  endpoint: "/avatar/assets/cloud-avatar" | "/avatar/assets/cloud-voice",
  params: { file: File; name: string },
): Promise<AvatarAsset> {
  const formData = new FormData();
  formData.append("file", params.file);
  formData.append("name", params.name);
  formData.append("rights_confirmed", "true");
  formData.append("rights_holder", "本人/公司已授权");
  const resp = await fetch(`${BASE}${endpoint}`, { method: "POST", body: formData });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || body.message || "上传云端训练素材失败");
  }
  return resp.json();
}

export function trainCloudAvatar(params: { file: File; name: string }): Promise<AvatarAsset> {
  return uploadCloudAvatarMaterial("/avatar/assets/cloud-avatar", params);
}

export function trainCloudVoice(params: { file: File; name: string }): Promise<AvatarAsset> {
  return uploadCloudAvatarMaterial("/avatar/assets/cloud-voice", params);
}

export function createAvatarJob(params: AvatarJobCreateRequest): Promise<AvatarJob> {
  return request("/avatar/jobs", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function listAvatarJobs(params: { includeSandbox?: boolean } = {}): Promise<AvatarJob[]> {
  const suffix = params.includeSandbox ? "?include_sandbox=true" : "";
  return request(`/avatar/jobs${suffix}`);
}

export function getAvatarJob(taskId: string): Promise<AvatarJob> {
  return request(`/avatar/jobs/${taskId}`);
}

export function retryAvatarVideoSubmission(taskId: string): Promise<AvatarJob> {
  return request(`/avatar/jobs/${taskId}/retry-video`, {
    method: "POST",
  });
}

export async function downloadAvatarJobMedia(taskId: string): Promise<Blob> {
  const resp = await fetch(`${BASE}/avatar/jobs/${taskId}/media`);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || body.message || "下载数字人成片失败");
  }
  return resp.blob();
}

/* ---- 通知/消息 ---- */

export function getNotifications(): Promise<any[]> {
  return request<any[]>("/notifications");
}

export function getMessages(): Promise<any[]> {
  return request<any[]>("/messages");
}

export function getUserProfile(): Promise<any> {
  return request("/user/profile");
}

/* ---- 深度分析 ---- */

export function getAnalyticsData(
  timeRange: string,
  keyword?: string,
): Promise<AnalyticsResponse> {
  const params = new URLSearchParams({ time_range: timeRange });
  if (keyword?.trim()) {
    params.set("keyword", keyword.trim());
  }
  return request(`/analytics/summary?${params.toString()}`);
}

/* ---- 视频剪辑 ---- */

export function getVideoCapabilities(): Promise<VideoCapabilitiesResponse> {
  return request("/video-editor/capabilities");
}

export function getVideoStepKinds(): Promise<StepKindsResponse> {
  return request("/video-editor/step-kinds");
}

export function editVideo(params: VideoEditRequest): Promise<VideoEditResponse> {
  return request("/video-editor/edit", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

// ====== 模板 API ======

export async function listTemplates(category?: string): Promise<TemplateListResponse> {
  const params = category ? `?category=${encodeURIComponent(category)}` : "";
  return request<TemplateListResponse>(`/templates${params}`);
}

export async function getTemplate(templateId: string): Promise<EditTemplate> {
  return request<EditTemplate>(`/templates/${encodeURIComponent(templateId)}`);
}

export async function createTemplate(data: TemplateCreateRequest): Promise<EditTemplate> {
  return request<EditTemplate>("/templates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function deleteTemplate(templateId: string): Promise<void> {
  return request(`/templates/${encodeURIComponent(templateId)}`, {
    method: "DELETE",
  });
}

export async function applyTemplate(
  templateId: string,
  sourceVideoPath: string,
): Promise<{ task_id: string; status: string }> {
  return request(`/templates/${encodeURIComponent(templateId)}/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_video_path: sourceVideoPath }),
  });
}

// ====== 字幕 API ======

export async function getSubtitleStatus(): Promise<SubtitleStatusResponse> {
  return request<SubtitleStatusResponse>("/subtitles/status");
}

export function listVideoEditorSources(): Promise<VideoEditorSourceListResponse> {
  return request("/video-editor/sources");
}

export function preflightVideoEditor(params: {
  sourceId: string;
  outputProfile: VideoEditorOutputProfile;
  targetPlatform: string;
}): Promise<VideoEditorPreflightResponse> {
  return request("/video-editor/preflight", {
    method: "POST",
    body: JSON.stringify({
      source_id: params.sourceId,
      output_profile: params.outputProfile,
      target_platform: params.targetPlatform,
    }),
  });
}

export function createVideoEditorAnalysis(params: {
  sourceId: string;
  targetPlatform: string;
  subtitleEnabled: boolean;
  subtitleModel: "large-v3-turbo" | "base";
  language?: string;
}): Promise<VideoEditorAnalysis> {
  return request("/video-editor/analyses", {
    method: "POST",
    body: JSON.stringify({
      source_id: params.sourceId,
      target_platform: params.targetPlatform,
      subtitle_enabled: params.subtitleEnabled,
      subtitle_model: params.subtitleModel,
      language: params.language || "zh",
    }),
  });
}

export function getVideoEditorAnalysis(analysisId: string): Promise<VideoEditorAnalysis> {
  return request(`/video-editor/analyses/${encodeURIComponent(analysisId)}`);
}

export function generateVideoEditorContentAdvice(analysisId: string): Promise<{
  enabled: boolean;
  message: string;
  advice: string[];
}> {
  return request(`/video-editor/analyses/${encodeURIComponent(analysisId)}/content-advice`, {
    method: "POST",
  });
}

export function createVideoEditorJob(params: {
  analysisId: string;
  steps: { kind: string; params: Record<string, unknown>; enabled: boolean }[];
  outputFormat: string;
  outputResolution: string;
  outputFps: number;
  outputBitrate: string;
  subtitleEnabled: boolean;
}): Promise<VideoEditorJob> {
  return request("/video-editor/jobs", {
    method: "POST",
    body: JSON.stringify({
      analysis_id: params.analysisId,
      steps: params.steps,
      output_format: params.outputFormat,
      output_resolution: params.outputResolution,
      output_fps: params.outputFps,
      output_bitrate: params.outputBitrate,
      subtitle_enabled: params.subtitleEnabled,
    }),
  });
}

export function getVideoEditorJob(taskId: string): Promise<VideoEditorJob> {
  return request(`/video-editor/jobs/${encodeURIComponent(taskId)}`);
}

export function listVideoEditorJobs(): Promise<VideoEditorJobListResponse> {
  return request("/video-editor/jobs");
}

export async function uploadVideoEditorSources(
  files: File[],
  rightsHolder: string,
): Promise<VideoEditorSourceListResponse> {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  formData.append("rights_confirmed", "true");
  formData.append("rights_holder", rightsHolder);
  const resp = await fetch(`${BASE}/video-editor/uploads`, { method: "POST", body: formData });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || body.message || "素材上传失败");
  }
  return resp.json();
}

export async function uploadVideoEditorVisualAsset(params: {
  kind: "product" | "background";
  file: File;
  rightsHolder: string;
}): Promise<VideoEditorVisualAsset> {
  const formData = new FormData();
  formData.append("kind", params.kind);
  formData.append("file", params.file);
  formData.append("rights_confirmed", "true");
  formData.append("rights_holder", params.rightsHolder);
  const resp = await fetch(`${BASE}/video-editor/visual-assets`, { method: "POST", body: formData });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || body.message || "图片上传失败");
  }
  return resp.json();
}

export function createProductShowcaseJob(params: {
  sourceId: string;
  productAssetId: string;
  backgroundAssetId?: string;
  layout: "avatar_left_product_right" | "product_canvas_avatar_pip";
}): Promise<VideoEditorJob> {
  return request("/video-editor/product-showcase/jobs", {
    method: "POST",
    body: JSON.stringify({
      source_id: params.sourceId,
      product_asset_id: params.productAssetId,
      background_asset_id: params.backgroundAssetId || null,
      layout: params.layout,
    }),
  });
}

export function listVideoEditorBgm(): Promise<VideoEditorBgmListResponse> {
  return request("/video-editor/bgm");
}

export async function uploadVideoEditorBgm(params: {
  file: File;
  mood: string;
  voiceoverCategory: string;
  energy: string;
  rightsHolder: string;
  sourceProvider: string;
  sourceUrl?: string;
  licenseUrl?: string;
  contentIdRisk?: "none" | "registered" | "unknown";
}): Promise<VideoEditorBgmAsset> {
  const formData = new FormData();
  formData.append("file", params.file);
  formData.append("mood", params.mood);
  formData.append("voiceover_category", params.voiceoverCategory);
  formData.append("energy", params.energy);
  formData.append("rights_confirmed", "true");
  formData.append("rights_holder", params.rightsHolder);
  formData.append("source_provider", params.sourceProvider);
  formData.append("source_url", params.sourceUrl || "");
  formData.append("license_url", params.licenseUrl || "");
  formData.append("content_id_risk", params.contentIdRisk || "unknown");
  const resp = await fetch(`${BASE}/video-editor/bgm`, { method: "POST", body: formData });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || body.message || "背景音乐上传失败");
  }
  return resp.json();
}

export function listVideoEditorLocalModels(): Promise<VideoEditorLocalModelListResponse> {
  return request("/video-editor/models");
}

export function prepareVideoEditorLocalModel(
  modelName: "base" | "large-v3-turbo",
): Promise<VideoEditorLocalModel> {
  return request(`/video-editor/models/${encodeURIComponent(modelName)}/prepare`, { method: "POST" });
}

export function createVideoEditorBatch(params: {
  sourceIds: string[];
  targetPlatform: string;
  subtitleEnabled?: boolean;
  subtitleModel?: "large-v3-turbo" | "base";
  steps?: { kind: string; params: Record<string, unknown>; enabled: boolean }[];
  outputFormat?: string;
  outputResolution?: string;
  outputFps?: number;
  outputBitrate?: string;
  bgmEnabled?: boolean;
  bgmId?: string;
  bgmVolume?: number;
  outputProfile?: VideoEditorOutputProfile;
  quoteId?: string;
  billingConfirmation?: {
    confirmed: boolean;
    maxCostCny: number;
  };
  idempotencyKey?: string;
}): Promise<VideoEditorBatch> {
  return request("/video-editor/batches", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(params.idempotencyKey ? { "Idempotency-Key": params.idempotencyKey } : {}),
    },
    body: JSON.stringify({
      source_ids: params.sourceIds,
      target_platform: params.targetPlatform,
      subtitle_enabled: params.subtitleEnabled ?? true,
      subtitle_model: params.subtitleModel || "large-v3-turbo",
      steps: params.steps || [],
      output_format: params.outputFormat || "mp4",
      output_resolution: params.outputResolution || (params.outputProfile === "720p" ? "720x1280" : "1080x1920"),
      output_fps: params.outputFps ?? 30,
      output_bitrate: params.outputBitrate || (params.outputProfile === "720p" ? "2.5M" : "5M"),
      bgm_enabled: params.bgmEnabled ?? true,
      bgm_id: params.bgmId || null,
      bgm_volume: params.bgmVolume ?? 0.18,
      output_profile: params.outputProfile || null,
      quote_id: params.quoteId || null,
      billing_confirmation: params.billingConfirmation
        ? {
            confirmed: params.billingConfirmation.confirmed,
            max_cost_cny: params.billingConfirmation.maxCostCny,
          }
        : null,
    }),
  });
}

export function listVideoEditorBatches(): Promise<VideoEditorBatchListResponse> {
  return request("/video-editor/batches");
}

export function getVideoEditorBatch(batchId: string): Promise<VideoEditorBatch> {
  return request(`/video-editor/batches/${encodeURIComponent(batchId)}`);
}

export function getVideoEditorBatchItemDownloadUrl(batchId: string, itemId: string): string {
  return `${BASE}/video-editor/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}/download`;
}

export function createVideoEditorLocalExport(
  batchId: string,
  itemId: string,
): Promise<VideoEditorBatch> {
  return request(
    `/video-editor/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}/local-export`,
    { method: "POST" },
  );
}

export function continueVideoEditorBatchItem(batchId: string, itemId: string): Promise<VideoEditorBatch> {
  return request(`/video-editor/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}/continue`, { method: "POST" });
}

export function reviewVideoEditorBatchItem(
  batchId: string,
  itemId: string,
  params: {
    subtitleSegments: Array<Record<string, unknown>>;
    enabledPlanStepIds: string[];
    selectedTitle: string;
    selectedBgmId?: string | null;
    confirmed: boolean;
  },
): Promise<VideoEditorBatch> {
  return request(`/video-editor/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}/review`, {
    method: "POST",
    body: JSON.stringify({
      subtitle_segments: params.subtitleSegments,
      enabled_plan_step_ids: params.enabledPlanStepIds,
      selected_title: params.selectedTitle,
      selected_bgm_id: params.selectedBgmId || null,
      confirmed: params.confirmed,
    }),
  });
}

export function selectVideoEditorBatchItemTitle(
  batchId: string,
  itemId: string,
  title: string,
): Promise<VideoEditorBatch> {
  return request(`/video-editor/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}/title`, {
    method: "PUT",
    body: JSON.stringify({ title }),
  });
}

export function retryVideoEditorBatchItem(batchId: string, itemId: string): Promise<VideoEditorBatch> {
  return request(`/video-editor/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}/retry`, { method: "POST" });
}

export function confirmVideoEditorBatchResults(batchId: string, itemIds: string[]): Promise<VideoEditorBatch> {
  return request(`/video-editor/batches/${encodeURIComponent(batchId)}/confirm-results`, {
    method: "POST",
    body: JSON.stringify({ item_ids: itemIds }),
  });
}
