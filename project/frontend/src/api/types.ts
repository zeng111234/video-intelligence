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

export interface CrawlerResult {
  title: string;
  author: string;
  likes: number;
  platform: string;
}

export interface CrawlerTaskResponse {
  task_id: string;
  keyword: string;
  status: string;
  platform: string;
  max_results: number;
  result_count: number;
  results: CrawlerResult[];
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface CrawlerTaskListResponse {
  items: CrawlerTaskResponse[];
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
