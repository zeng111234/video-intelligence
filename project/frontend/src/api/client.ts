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
  CopywritingGenerateRequest,
  CopywritingResponse,
  CopywritingRewriteRequest,
  CrawlerBatchListResponse,
  CrawlerBatchResponse,
  CrawlerCapabilitiesResponse,
  CrawlerPreviewResponse,
  CrawlerSearchRequest,
  PipelineResponse,
  PublishPlatformsResponse,
  PublishResponse,
  TaskListResponse,
  TranscriptionResponse,
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
): Promise<TranscriptionResponse> {
  return request("/transcriptions/url", {
    method: "POST",
    body: JSON.stringify({
      url,
      rights_confirmed: rightsConfirmed,
    }),
  });
}

export function getTranscription(taskId: string): Promise<TranscriptionResponse> {
  return request(`/transcriptions/${taskId}`);
}

export function listTranscriptions(): Promise<TranscriptionResponse[]> {
  return request("/transcriptions");
}

export function saveTranscriptionRevision(params: {
  taskId: string;
  segments: Array<{
    start: number;
    end: number;
    text: string;
    confidence: number;
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
  format: "txt" | "json" | "srt",
): Promise<Blob> {
  const resp = await fetch(`${BASE}/transcriptions/${taskId}/export?format=${format}`);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || "导出失败");
  }
  return resp.blob();
}

/** 上传文件并转写 */
export async function uploadAndTranscribe(file: File): Promise<TranscriptionResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("rights_confirmed", "true");

  const resp = await fetch(`${BASE}/transcriptions/upload`, {
    method: "POST",
    body: formData,
  });

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || "文件上传失败");
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

export function getPipeline(runId: string): Promise<PipelineResponse> {
  return request(`/pipelines/${runId}`);
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

/* ---- 数字人生成 ---- */

export function getAvatarCapabilities(): Promise<AvatarCapability> {
  return request("/avatar/capabilities");
}

export function listAvatarAssets(): Promise<AvatarAsset[]> {
  return request("/avatar/assets");
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
  return request<any[]>("/notifications").catch(() => {
    // 后端暂未实现，返回 mock 数据
    return [
      { id: "1", title: "批量生产任务完成", description: "您提交的批量生产任务已完成", time: "5 分钟前", read: false, type: "task" },
      { id: "2", title: "系统更新通知", description: "系统将于今晚进行维护升级", time: "1 小时前", read: false, type: "system" },
      { id: "3", title: "Pro 会员即将到期", description: "您的 Pro 会员将于下月到期", time: "2 小时前", read: true, type: "pro" },
    ];
  });
}

export function getMessages(): Promise<any[]> {
  return request<any[]>("/messages").catch(() => {
    // 后端暂未实现，返回 mock 数据
    return [
      { id: "1", sender: "系统助手", avatar: "🤖", content: "您的批量生产任务已排队", time: "10 分钟前", read: false },
      { id: "2", sender: "运营小助手", avatar: "💡", content: "新功能上线！AI 文案生成支持自定义风格模板", time: "2 小时前", read: false },
    ];
  });
}

export function getUserProfile(): Promise<any> {
  return request("/user/profile").catch(() => {
    // 后端暂未实现，返回 mock 数据
    return {
      username: "Admin",
      email: "admin@videoinsight.com",
      phone: "138****8888",
      role: "Pro 会员",
      twoFactorEnabled: true,
    };
  });
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
