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
  start: number | null;
  end: number | null;
  text: string;
  confidence: number | null;
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
  model_name: string | null;
  source_kind: string;
  timing_available: boolean;
  duration_seconds: number | null;
  approved_revision_id: string | null;
  low_confidence_count: number;
  segments: TranscriptSegment[];
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface PipelineStage {
  stage: string;
  status: string;
  task_id: string | null;
  error_message: string | null;
  outputs?: Record<string, string>;
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

export interface PipelineFromCandidateRequest {
  candidate_id: string;
  rights_confirmed: boolean;
  rights_holder: string;
  model_name?: string;
  hotwords?: string;
  target_length?: number;
  tone?: string;
  target_audience?: string;
  style_prompt?: string;
  variant_count?: number;
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
  active_platforms: string[];
  active_platform_labels: string[];
  paused_platforms: string[];
  paused_platform_labels: string[];
  missing_configuration: string[];
  permission_status: string;
  monthly_query_count: number;
  monthly_estimated_cost_cny: number;
  monthly_warning_queries: number;
  monthly_hard_limit_queries: number;
  monthly_hard_limit_cost_cny: number;
  cache_ttl_minutes: number;
  supports_usage: boolean;
  usage: CrawlerProviderUsage | null;
}

export interface VoiceoverDraftResponse {
  copywriting_task_id: string;
  source_task_id: string;
  source_revision_id: string;
  status: string;
  provider_name: string;
  model_name: string;
  is_mock: boolean;
  target_seconds: number | null;
  target_characters: number;
  source_characters: number;
  result_text: string | null;
  result_variants: string[];
  token_usage: Record<string, number>;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface CrawlerProviderUsage {
  provider?: string;
  platform_queries?: number;
  billable_units?: number | null;
  estimated_cost?: number | null;
  currency?: string;
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
  platform_unit_price_cny: number | null;
  estimated_cost_cny: number | null;
  blocked_reason: string | null;
}

export interface CrawlerPreviewResponse extends CrawlerSearchRequest {
  provider_mode: string;
  provider_name: string;
  ranking_mode: string;
  monthly_query_count: number;
  monthly_estimated_cost_cny: number;
  monthly_warning_queries: number;
  monthly_hard_limit_queries: number;
  monthly_hard_limit_cost_cny: number;
  cache_ttl_minutes: number;
  platforms: CrawlerPlatformPreview[];
  estimated_total_cost_cny: number;
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
  display_tier: "exploding" | "hot" | "potential" | "observing" | "ordinary";
  effective_interactions: number | null;
  confidence: number | null;
  pool_size: number | null;
  like_growth_per_hour: number | null;
  engagement_growth_per_hour: number | null;
  acceleration_ratio: number | null;
  valid_snapshot_count: number | null;
  recrawl_count: number | null;
  recall_count: number | null;
  missed_checkpoint_count: number | null;
  sampling_span_hours: number | null;
  anomaly_status: string | null;
  platform_rank: number | null;
  provider_hot_rank: number | null;
  system_rank: number | null;
  plays: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  favorites: number | null;
  component_scores: Record<string, number | null>;
  data_quality_warnings: string[];
  model_version: string | null;
  evidence: string | null;
  reasons: string[];
  media_resolution_status: string | null;
  media_transcription_task_id: string | null;
}

export interface CrawlerCandidateMediaPreviewResponse {
  candidate_id: string;
  resolvable: boolean;
  mode: string;
  provider: string;
  platform: string;
  platform_label: string;
  platform_item_id: string | null;
  estimated_cost_cny: number | null;
  monthly_budget_used_cny: number;
  monthly_budget_limit_cny: number;
  existing_task_id: string | null;
  last_resolution_status: string | null;
  block_reason: string | null;
  source: string;
}

export interface CrawlerDoubaoJobResponse extends TranscriptionResponse {
  candidate_id: string | null;
  source_url: string | null;
  douyin_short_url: string | null;
  doubao_conversation_url: string | null;
  fee_cny: number;
  review_required: boolean;
  prompt_version: string | null;
  worker_id: string | null;
}

export interface CrawlerDoubaoJobListResponse {
  items: CrawlerDoubaoJobResponse[];
  total: number;
}

export interface CrawlerDoubaoWorkerStartResponse {
  started: boolean;
  command: string[];
  log_path: string;
  message: string;
}

export interface CrawlerDoubaoMobileCapabilitiesResponse {
  enabled: boolean;
  worker_mode: string;
  appium_server_url: string;
  android_package: string | null;
  missing_configuration: string[];
  requirements: string[];
  message: string;
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
  raw_item_count: number;
  parsed_item_count: number;
  out_of_window_count: number;
  invalid_count: number;
  duplicate_count: number;
  result_state: string;
  payload_diagnostic: string | null;
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
  total_estimated_cost_cny: number;
}

export interface CrawlerBatchListResponse {
  items: CrawlerBatchResponse[];
  total: number;
}

export interface CrawlerDueRecrawlResponse {
  executed_batches: CrawlerBatchResponse[];
  total: number;
}

/* ---- 文案生成 ---- */

export interface CopywritingResponse {
  task_id: string;
  status: string;
  provider_name: string;
  model_name: string;
  is_mock: boolean;
  token_usage: Record<string, number>;
  result_text: string | null;
  result_variants: string[];
  error_message: string | null;
}

export interface CopywritingSummaryResponse extends CopywritingResponse {
  title: string;
  creation_mode: "generate" | "rewrite" | string;
  platform: string;
  target_audience: string;
  target_length: number;
  tone: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface CopywritingDetailResponse extends CopywritingSummaryResponse {
  source_text: string;
  content_brief: string;
  selling_points: string;
  call_to_action: string;
  style_prompt: string;
}

export interface CopywritingCapabilitiesResponse {
  provider_name: string;
  display_name: string;
  mode: string;
  enabled: boolean;
  model_name: string;
  max_input_chars: number;
  max_output_chars: number;
  supports_variants: boolean;
  max_variants: number;
  supported_platforms: string[];
  missing_configuration: string[];
}

export interface CopywritingGenerateRequest {
  content_brief: string;
  platform?: string;
  target_audience?: string;
  selling_points?: string;
  call_to_action?: string;
  style_prompt?: string;
  target_length?: number;
  tone?: string;
  variant_count?: number;
}

export interface CopywritingRewriteRequest {
  source_text: string;
  platform?: string;
  target_audience?: string;
  style_prompt?: string;
  target_length?: number;
  tone?: string;
  variant_count?: number;
}

/* ---- 多平台发布 ---- */

export interface PublishResponse {
  task_id: string;
  batch_id: string | null;
  status: string;
  publish_status: string;
  platform: string;
  title: string;
  stage: string;
  provider_name: string;
  platform_video_id: string | null;
  platform_url: string | null;
  is_mock: boolean;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface PublishPlatformsResponse {
  platforms: PublishPlatformCapability[];
}

export interface PublishPlatformCapability {
  platform: string;
  enabled: boolean;
  display_name: string;
  mode: string;
  provider_name: string;
  manual_only: boolean;
  manual_fallback: boolean;
  supports_scheduled: boolean;
  supports_tags: boolean;
  supports_cover: boolean;
  missing_configuration: string[];
}

export interface PublishPreflightPlatform {
  platform: string;
  display_name: string;
  provider_name: string;
  mode: string;
  enabled: boolean;
  manual_required: boolean;
  manual_only: boolean;
  can_create_task: boolean;
  issue: string | null;
  missing_configuration: string[];
  manual_steps: string[];
}

export interface PublishPreflightResponse {
  blocked: boolean;
  video_path: string;
  issues: string[];
  platforms: PublishPreflightPlatform[];
}

export interface PublishBatchResponse {
  batch_id: string;
  status: string;
  total: number;
  succeeded: number;
  failed: number;
  outcome_unknown: number;
  pending: number;
  created_at: string;
  updated_at: string;
  tasks: PublishResponse[];
}

export interface PublishBatchListResponse {
  items: PublishBatchResponse[];
  total: number;
}

export interface PublishAsset {
  name: string;
  path: string;
  size_bytes: number;
  updated_at?: number;
}

export interface PublishAssetListResponse {
  items: PublishAsset[];
  total: number;
}

export interface PublishVariableStatus {
  field: "mode" | "access_token" | "open_id" | "client_key" | "client_secret" | string;
  key: string;
  configured: boolean;
  masked_value: string;
  secret: boolean;
}

export interface PublishPlatformConfig {
  platform: string;
  display_name: string;
  mode: "manual" | "official" | string;
  env_path: string;
  variables: PublishVariableStatus[];
}

export interface PublishConfigResponse {
  env_path: string;
  platforms: PublishPlatformConfig[];
}

export interface PublishPlatformConfigUpdate {
  mode: "manual" | "official";
  access_token?: string;
  open_id?: string;
  client_key?: string;
  client_secret?: string;
}

/* ---- 数字人生成 ---- */

export interface AvatarCapability {
  provider_name: string;
  display_name: string;
  mode: "sandbox" | "production";
  enabled: boolean;
  permission_status: string;
  max_script_chars: number;
  supported_aspect_ratios: string[];
  estimated_cost_cny: number | null;
  estimated_seconds: number | null;
  missing_configuration: string[];
  profiles: AvatarProfile[];
}

export interface AvatarProfile {
  profile_id: string;
  display_name: string;
  description: string;
  enabled: boolean;
  estimated_cost_cny: number | null;
  estimated_seconds: number | null;
  required_vram_gb: number | null;
  missing_configuration: string[];
}

export interface AvatarAsset {
  asset_id: string;
  kind: "avatar" | "voice";
  name: string;
  preview_url: string | null;
  authorized: boolean;
}

export interface AvatarJob {
  task_id: string;
  status: string;
  progress: number;
  stage: string;
  title: string;
  script_text: string;
  avatar_id: string;
  avatar_name: string;
  voice_id: string;
  voice_name: string;
  profile_id: string;
  speech_rate: number;
  aspect_ratio: string;
  resolution: string;
  provider_name: string;
  provider_job_id: string | null;
  estimated_cost_cny: number | null;
  estimated_seconds: number | null;
  actual_seconds: number | null;
  result_url: string | null;
  error_kind: string | null;
  error_message: string | null;
  is_mock: boolean;
  created_at: string;
  updated_at: string;
}

export interface AvatarJobCreateRequest {
  industry_config_id?: string | null;
  template_version_id?: string | null;
  source_task_id?: string | null;
  source_revision_id?: string | null;
  script_text: string;
  avatar_id: string;
  voice_id: string;
  profile_id?: string;
  target_seconds: number;
  speech_rate: number;
  aspect_ratio: string;
  resolution: string;
  publish_mode: "manual" | "auto";
  target_platforms: string[];
  idempotency_key: string;
}

/* ---- 深度分析 ---- */

export interface AnalyticsTrendItem {
  topic: string;
  views: string;
  growth: number | null;
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

/* ---- 视频剪辑 ---- */

export interface VideoEditStep {
  step_id?: string;
  kind: string;
  params: Record<string, unknown>;
  order: number;
}

export interface VideoEditConfig {
  steps: VideoEditStep[];
  output_format?: string;
  output_resolution?: string;
  output_fps?: number;
  output_bitrate?: string;
}

export interface VideoEditRequest {
  source_video_path: string;
  subtitle_text?: string;
  subtitle_style?: string;
  edit_config?: VideoEditConfig;
}

export interface VideoEditResponse {
  task_id: string;
  status: string;
  result_path: string | null;
  result_size_bytes: number | null;
  error_message: string | null;
}

export interface VideoCapabilitiesResponse {
  provider_name: string;
  display_name: string;
  enabled: boolean;
  supports_trim: boolean;
  supports_subtitle: boolean;
  supports_watermark: boolean;
  supports_speed: boolean;
  supports_resize: boolean;
  supports_filter: boolean;
  supports_concat: boolean;
  supports_transition: boolean;
  supports_background_music: boolean;
  supports_ai_subtitle: boolean;
  supports_ai_volume_norm: boolean;
  supports_ai_enhance: boolean;
  supports_ai_silence_trim: boolean;
}

export interface StepKindParam {
  type: string;
  label: string;
  default?: unknown;
  required?: boolean;
  options?: string[];
  min?: number;
  max?: number;
}

export interface StepKindInfo {
  label: string;
  category: "basic" | "ai";
  requires?: string;
  params: Record<string, StepKindParam>;
}

export type StepKindsResponse = Record<string, StepKindInfo>;

// ====== 模板系统 ======

export interface TemplateStepDef {
  kind: string;
  params?: Record<string, unknown>;
  label?: string;
}

export interface EditTemplate {
  template_id: string;
  name: string;
  description: string;
  category: string;
  icon: string;
  steps: TemplateStepDef[];
  output_format: string;
  output_resolution: string;
  output_fps: number;
  output_bitrate: string;
  is_builtin: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface TemplateListResponse {
  items: EditTemplate[];
  total: number;
}

export interface TemplateCreateRequest {
  name: string;
  description?: string;
  category?: string;
  steps?: TemplateStepDef[];
  output_format?: string;
  output_resolution?: string;
  output_fps?: number;
  output_bitrate?: string;
}

export interface TemplateApplyRequest {
  source_video_path: string;
}

// ====== 字幕系统 ======

export interface SubtitleStatusResponse {
  whisper_available: boolean;
  version?: string;
  supported_formats: string[];
  install_command?: string;
  provider_name: string;
  ffmpeg_available: boolean;
  asr_mode: string;
  supported_models: string[];
  default_model: string;
  reason?: string;
}
