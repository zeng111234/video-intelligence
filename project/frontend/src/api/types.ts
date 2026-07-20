/* 后端 API 响应类型 —— 与 project/backend/app/schemas/responses.py 对齐 */

export interface CandidateItem {
  video_id: string;
  title: string;
  platform: string;
  author_name: string;
  category: string;
  heat_score: number;
  heat_level: string;
  source_url: string | null;
  published_at: string | null;
}

export interface CandidateListResponse {
  items: CandidateItem[];
  total: number;
}

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
  confidence: number;
  needs_review: boolean;
  reviewed?: boolean;
}

export interface TranscriptionResponse {
  task_id: string;
  title: string;
  status: string;
  progress: number;
  stage: string;
  media_name: string;
  segments: TranscriptSegment[];
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface PipelineStage {
  stage: string;
  status: string;
  task_id: string;
  error_message: string | null;
}

export interface PipelineResponse {
  run_id: string;
  keyword: string;
  status: string;
  current_stage: string | null;
  stages: PipelineStage[];
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface TaskItem {
  task_id: string;
  kind: string;
  title: string;
  status: string;
  progress: number;
  created_at: string | null;
}

export interface TaskListResponse {
  items: TaskItem[];
  total: number;
}

export interface AdminStatusResponse {
  status: string;
  repository_type: string;
  database_path: string | null;
  candidate_count: number;
  task_count: number;
  pipeline_count?: number;
  migration_version?: number | null;
  migration_details?: Record<string, unknown> | null;
  python_version?: string | null;
  platform_info?: string | null;
  version: string;
}

/* ---- 关键词爬虫 ---- */

export interface CrawlerCapabilitiesResponse {
  provider_name: string;
  display_name: string;
  mode: string;
  enabled: boolean;
  supported_platforms: string[];
  supported_platform_labels: string[];
  missing_configuration: string[];
  permission_status: string;
  monthly_query_count: number;
  monthly_warning_queries: number;
  monthly_hard_limit_queries: number;
  cache_ttl_minutes: number;
  supports_usage: boolean;
  usage: Record<string, unknown> | null;
}

export interface CrawlerSearchRequest {
  keyword: string;
  published_window_days: 1 | 7;
  count_per_platform: number;
  force_refresh: boolean;
}

export interface CrawlerPlatformPreview {
  platform: string;
  platform_label: string;
  cache_hit: boolean;
  estimated_api_calls: number;
  blocked_reason: string | null;
}

export interface CrawlerPreviewResponse extends CrawlerSearchRequest {
  provider_mode: string;
  provider_name: string;
  monthly_query_count: number;
  monthly_warning_queries: number;
  monthly_hard_limit_queries: number;
  cache_ttl_minutes: number;
  platforms: CrawlerPlatformPreview[];
  blocked: boolean;
}

export interface CrawlerCandidateResult {
  video_id: string;
  title: string;
  author_name: string;
  platform: string;
  platform_label: string;
  source_url: string | null;
  published_at: string | null;
  trend_score: number | null;
  trend_level: string | null;
  confidence: number | null;
  pool_size: number | null;
  like_growth_per_hour: number | null;
  anomaly_status: string | null;
  platform_rank: number | null;
  evidence: string | null;
  reasons: string[];
}

export interface CrawlerPlatformRun {
  run_id: string;
  platform: string;
  platform_label: string;
  provider: string;
  mode: string;
  status: string;
  requested_count: number;
  returned_count: number;
  cache_hit: boolean;
  cached_from_run_id: string | null;
  api_call_count: number;
  billable_units: number | null;
  quota_remaining: number | null;
  error: string | null;
  errors: Record<string, unknown>[];
  started_at: string | null;
  finished_at: string | null;
  candidates: CrawlerCandidateResult[];
}

export interface CrawlerBatchResponse {
  batch_id: string;
  keyword: string;
  published_window_days: number;
  count_per_platform: number;
  provider: string;
  mode: string;
  status: string;
  force_refresh: boolean;
  created_at: string | null;
  finished_at: string | null;
  error: string | null;
  platform_runs: CrawlerPlatformRun[];
  total_api_calls: number;
  total_candidates: number;
}

export interface CrawlerBatchListResponse {
  items: CrawlerBatchResponse[];
  total: number;
}

/* ---- 文案生成 ---- */

export interface CopywritingResponse {
  task_id: string;
  status: string;
  result_text: string | null;
  result_variants: string[];
  error_message: string | null;
}

/* ---- 多平台发布 ---- */

export interface PublishResponse {
  task_id: string;
  status: string;
  platform: string;
  error_message: string | null;
}

export interface PublishPlatformsResponse {
  platforms: string[];
}

/* ---- 深度分析 ---- */

export interface AnalyticsTrendItem {
  topic: string;
  views: string;
  growth: number;
  hot: string;
}

export interface AnalyticsCompetitorItem {
  name: string;
  fans: string;
  avgViews: string;
  engagement: number;
}

export interface AnalyticsOverview {
  totalViews: number;
  totalWatchHours: number;
  engagementRate: number;
  shareCount: number;
}

export interface AnalyticsResponse {
  overview: AnalyticsOverview;
  trends: AnalyticsTrendItem[];
  competitors: AnalyticsCompetitorItem[];
  contentDistribution: { label: string; percent: number }[];
}
