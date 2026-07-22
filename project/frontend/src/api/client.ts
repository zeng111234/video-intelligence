/**
 * 后端 API 客户端 —— 所有请求走 Vite 代理 /api → localhost:2001
 */

import type {
  AdminStatusResponse,
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
  CrawlerBatchListResponse,
  CrawlerBatchResponse,
  CrawlerCandidateMediaPreviewResponse,
  CrawlerCapabilitiesResponse,
  CrawlerDoubaoJobListResponse,
  CrawlerDoubaoMobileCapabilitiesResponse,
  CrawlerDoubaoWorkerStartResponse,
  CrawlerDueRecrawlResponse,
  CrawlerPreviewResponse,
  CrawlerSearchRequest,
  EditTemplate,
  PipelineFromCandidateRequest,
  PipelineResponse,
  PublishAsset,
  PublishAssetListResponse,
  PublishBatchListResponse,
  PublishBatchResponse,
  PublishConfigResponse,
  PublishPlatformConfig,
  PublishPlatformConfigUpdate,
  PublishPlatformsResponse,
  PublishPreflightResponse,
  PublishResponse,
  StepKindsResponse,
  SubtitleStatusResponse,
  TaskListResponse,
  TemplateCreateRequest,
  TemplateListResponse,
  TranscriptionResponse,
  VideoCapabilitiesResponse,
  VideoEditRequest,
  VideoEditResponse,
  VoiceoverDraftResponse,
} from "./types";

const BASE = "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || "GET").toUpperCase();
  const canRetry = method === "GET";
  const run = () =>
    fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
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
    const body = await resp.json().catch(() => ({}));
    const statusMessages: Record<number, string> = {
      400: "请求参数错误",
      404: "请求的资源不存在",
      500: "服务器内部错误",
      502: "后端服务未响应",
      503: "服务暂时不可用",
    };
    throw new Error(
      body.detail || body.message || statusMessages[resp.status] || `请求失败: ${resp.status}`,
    );
  }
  return resp.json();
}

/* ---- 候选搜索 ---- */

export function searchCandidates(
  keyword: string,
  limit = 10,
  platforms: string[] = [],
  category?: string,
): Promise<CandidateListResponse> {
  return request("/candidates/search", {
    method: "POST",
    body: JSON.stringify({ keyword, limit, platforms, category }),
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
): Promise<TranscriptionResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("rights_confirmed", "true");
  formData.append("rights_holder", rightsHolder);
  formData.append("model_name", modelName);
  formData.append("language", language);

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

export function getPipeline(runId: string): Promise<PipelineResponse> {
  return request(`/pipelines/${runId}`);
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

export function listPipelines(): Promise<PipelineResponse[]> {
  return request("/pipelines");
}

/* ---- 任务列表 ---- */

export function listTasks(): Promise<TaskListResponse> {
  return request("/tasks");
}

/* ---- 管理后台 ---- */

export function getAdminStatus(): Promise<AdminStatusResponse> {
  return request("/admin/status");
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

export function getCrawlerCapabilities(): Promise<CrawlerCapabilitiesResponse> {
  return request("/crawler/capabilities");
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

export function executeDueCrawlerRecrawls(limit = 5): Promise<CrawlerDueRecrawlResponse> {
  return request(`/crawler/recrawls/due?limit=${limit}`, {
    method: "POST",
  });
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

export function getCrawlerDoubaoMobileCapabilities(): Promise<CrawlerDoubaoMobileCapabilitiesResponse> {
  return request("/crawler/doubao-mobile/capabilities");
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
  platform?: string;
  target_audience?: string;
  style_prompt?: string;
  target_length?: number;
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

export function listPublishAssets(): Promise<PublishAssetListResponse> {
  return request("/publish/assets");
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
}): Promise<PublishPreflightResponse> {
  return request("/publish/preflight", {
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

export function createAvatarJob(params: AvatarJobCreateRequest): Promise<AvatarJob> {
  return request("/avatar/jobs", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function listAvatarJobs(): Promise<AvatarJob[]> {
  return request("/avatar/jobs");
}

export function getAvatarJob(taskId: string): Promise<AvatarJob> {
  return request(`/avatar/jobs/${taskId}`);
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
