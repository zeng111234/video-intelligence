/**
 * 后端 API 客户端 —— 所有请求走 Vite 代理 /api → localhost:2001
 */

import type {
  AdminStatusResponse,
  CandidateListResponse,
  CrawlerTaskListResponse,
  CrawlerTaskResponse,
  PipelineResponse,
  TaskListResponse,
  TranscriptionResponse,
} from "./types";

const BASE = "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new Error("网络连接失败，请检查后端服务是否已启动。");
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
      body.detail || statusMessages[resp.status] || `请求失败: ${resp.status}`,
    );
  }
  return resp.json();
}

/* ---- 候选搜索 ---- */

export function searchCandidates(
  keyword: string,
  limit = 10,
): Promise<CandidateListResponse> {
  return request("/candidates/search", {
    method: "POST",
    body: JSON.stringify({ keyword, limit }),
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

/** 通过链接创建转写任务（使用 mock 端点） */
export function createTranscriptionByUrl(
  url: string,
  rightsConfirmed = true,
): Promise<TranscriptionResponse> {
  return request("/transcriptions", {
    method: "POST",
    body: JSON.stringify({
      media_name: url,
      media_type: "video/mp4",
      rights_confirmed: rightsConfirmed,
    }),
  });
}

export function getTranscription(taskId: string): Promise<TranscriptionResponse> {
  return request(`/transcriptions/${taskId}`);
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

/* ---- 任务列表 ---- */

export function listTasks(): Promise<TaskListResponse> {
  return request("/tasks");
}

/* ---- 管理后台 ---- */

export function getAdminStatus(): Promise<AdminStatusResponse> {
  return request("/admin/status");
}

/* ---- 关键词爬虫 ---- */

export function createCrawlerTask(
  keyword: string,
  platform = "douyin",
  maxResults = 10,
): Promise<CrawlerTaskResponse> {
  return request("/crawler/tasks", {
    method: "POST",
    body: JSON.stringify({
      keyword,
      platform,
      max_results: maxResults,
    }),
  });
}

export function listCrawlerTasks(): Promise<CrawlerTaskListResponse> {
  return request("/crawler/tasks");
}

export function getCrawlerTask(taskId: string): Promise<CrawlerTaskResponse> {
  return request(`/crawler/tasks/${taskId}`);
}
