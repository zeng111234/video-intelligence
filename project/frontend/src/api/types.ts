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
  observed_at: string | null;
  publication_time_state: "platform" | "sampled_fallback" | string;
  official_hot: boolean;
  official_rank: number | null;
  snapshot_count: number;
  growth_window_hours: number | null;
  heat_reasons: string[];
}

export interface CandidateCategoryOption {
  value: string;
  count: number;
}

export interface CandidateListResponse {
  items: CandidateItem[];
  total: number;
  category_options: CandidateCategoryOption[];
}

export interface TranscriptSegment {
  start: number | null;
  end: number | null;
  text: string;
  confidence: number | null;
  needs_review: boolean;
  reviewed?: boolean;
  quality_status?: "pending" | "accepted" | "auto_verified" | "auto_corrected" | "llm_rewritten" | "uncertain" | string;
  quality_source?: "primary_asr" | "secondary_asr" | "llm_context" | string;
  quality_note?: string | null;
  alternatives?: string[];
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
  is_mock: boolean;
  auto_reviewed: boolean;
  uncertain_segment_count: number;
  secondary_asr_count: number;
  llm_review_count: number;
  auto_review_error: string | null;
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

export interface PipelineEvent {
  event_id: string;
  action: string;
  status: string;
  stage: string | null;
  message: string;
  details: Record<string, string>;
  created_at: string;
}

export interface PipelineResponse {
  run_id: string;
  keyword: string;
  status: string;
  current_stage: string | null;
  stages: PipelineStage[];
  candidate_video_id: string | null;
  copywriting_task_id: string | null;
  avatar_task_id: string | null;
  edit_task_id: string | null;
  publish_task_ids: string[];
  config: Record<string, unknown>;
  events: PipelineEvent[];
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
  result_media_url?: string | null;
}

export interface GuidedPipelineRequest {
  source_type: "share_link" | "candidate";
  candidate_id?: string;
  share_text?: string;
  profile_id: string;
  rights_confirmed: boolean;
  rights_holder: string;
  publish_enabled: boolean;
  publish_platforms: string[];
  paid_fallback_confirmed?: boolean;
}

export interface GuidedPipelinePreflight {
  ready: boolean;
  missing: string[];
  warnings: string[];
  source: {
    type: "share_link" | "candidate";
    title?: string;
    candidate_id?: string;
    share_url?: string;
    resolvable?: boolean;
    estimated_cost_cny?: number | null;
    message?: string | null;
    parser_enabled?: boolean;
    parser_message?: string | null;
    uses_paid_fallback?: boolean;
    requires_paid_fallback_confirmation?: boolean;
  };
  profile_name: string | null;
  platforms: PublishPlatformCapability[];
}

export interface PipelineReviewDraft {
  run_id: string;
  transcription_task_id: string | null;
  transcript_text: string;
  low_confidence_count: number;
  variants: string[];
  default_text: string;
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

export interface ProductionProfile {
  profile_id: string;
  name: string;
  description: string;
  target_audience: string;
  platform: string;
  script_style: string;
  avatar_id: string | null;
  voice_id: string | null;
  edit_template_id: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface ProductionBatchItem {
  candidate_id: string;
  run_id: string;
  source_type: "candidate" | "share_link" | "brief" | "script" | string;
  source_value: string;
  display_title: string;
  profile_overrides: Record<string, string>;
  status: string;
  current_stage: string | null;
  blocked_reasons: string[];
  error_message: string | null;
  video_path: string | null;
  publish_mode: string | null;
}

export interface ProductionBatchSourceItem {
  source_type: "candidate" | "share_link" | "brief" | "script";
  source_value: string;
  display_title?: string;
  profile_overrides?: Record<string, string>;
}

export interface ProductionBatchReviewResult {
  run_id: string;
  ok: boolean;
  error?: string;
}

export interface ProductionBatch {
  batch_id: string;
  name: string;
  profile_id: string;
  profile_name: string;
  status: string;
  is_paused: boolean;
  items: ProductionBatchItem[];
  progress: Record<string, number>;
  execution_config: Record<string, unknown>;
  estimated_cost_cny: number;
  monthly_budget_used_cny: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ProductionBatchPreflightItem {
  run_id: string;
  candidate_id: string;
  source_type?: string;
  display_title?: string;
  ready: boolean;
  reasons: string[];
}

export interface ProductionBatchPreflight {
  batch_id: string;
  ready_count: number;
  blocked_count: number;
  items: ProductionBatchPreflightItem[];
  estimated_cost_cny: number;
  monthly_budget_used_cny: number;
  platforms: Array<{ platform: string; display_name: string; mode: string; enabled: boolean; manual_required: boolean }>;
  concurrency: number;
}

export interface KeywordRunPreflight {
  ready: boolean;
  missing: string[];
  candidate_count: number;
  message: string;
}

export interface PublishFeedback {
  feedback_id: string;
  publish_task_id: string;
  pipeline_run_id: string | null;
  platform: string;
  views: number;
  likes: number;
  comments: number;
  leads: number;
  recorded_by: string;
  note: string;
  recorded_at: string;
}

export interface FeedbackRecommendations {
  sample_size: number;
  message: string;
  recommendations: string[];
  platform_engagement_rates?: Record<string, number>;
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
  /** 官方热榜能力状态（后端未上线时为 undefined/null，前端容错） */
  official_hot_billboard?: CrawlerOfficialHotCapability | null;
  /** 官方实时热点词能力状态 */
  official_hot_words?: CrawlerOfficialHotCapability | null;
  hotspot_browser?: CrawlerBrowserDiscoveryCapabilities | null;
}

export interface CrawlerBrowserDiscoveryCapabilities {
  enabled: boolean;
  running: boolean;
  login_required: boolean;
  missing_configuration: string[];
  browser_channel: "chrome" | "msedge" | string;
  ready_to_crawl?: boolean;
  phase?: "disabled" | "dependency_missing" | "browser_closed" | "starting" | "waiting_login" | "ready" | "browser_open" | string;
  adapter_version?: string;
  provider_name: string;
  message: string;
}

export interface CrawlerBrowserDiscoveryStartResponse extends CrawlerBrowserDiscoveryCapabilities {
  started: boolean;
}

/** 官方热榜 / 官方热点词能力状态 */
export interface CrawlerOfficialHotCapability {
  enabled: boolean;
  missing_configuration: string[];
  provider_name: string;
}

/** 官方实时热点词 */
export interface CrawlerHotWordItem {
  word: string;
  hot_value: number | null;
  fetched_at: string;
}

export interface CrawlerHotWordsResponse {
  words: CrawlerHotWordItem[];
  /** 同步失败或适配器未配置时的原因说明（后端可选返回，前端容错） */
  error?: string | null;
}

/** 文案来源三档 */
export type CrawlerCopySource =
  | "metadata_original"
  | "doubao_mobile_transcript"
  | "authorized_asr_transcript";

/** 官方热榜一键监测 */
export interface CrawlerOfficialHotMonitorRequest {
  keyword?: string;
}

export interface CrawlerOfficialHotMonitorResponse {
  matched_count: number;
  result_state: string;
  result_message: string;
  executed_recrawls: number;
  next_recrawl_at: string | null;
  candidates: CrawlerCandidateResult[];
}

/** 基于平台信息生成的数字人口播文案。 */
export interface CrawlerOriginalScriptResponse {
  copy_source: "metadata_original";
  is_original_transcript: boolean;
  needs_manual_review: boolean;
  script: string;
}

/** 手机豆包链路本机前置条件 */
export interface CrawlerDoubaoMobilePrerequisites {
  adb: boolean;
  appium_url: boolean;
  package: boolean;
  device_ready: boolean;
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
  draft_stage: "deduplicate" | "compliance" | string;
  parent_task_id: string | null;
  needs_manual_review: boolean;
}

export interface CrawlerLinkTranscriptionCapabilities {
  experimental: boolean;
  parser_enabled: boolean;
  parser_message: string | null;
  oneapi_estimated_cost_cny: number | null;
}

export interface CrawlerLinkTranscriptionPreview {
  share_url: string;
  work_id: string | null;
  parser_enabled: boolean;
  parser_message: string | null;
  oneapi_fallback_available: boolean;
  oneapi_estimated_cost_cny: number | null;
  is_experimental: boolean;
}

export interface CrawlerLinkTranscriptionResult {
  status: "succeeded" | "fallback_required";
  message: string;
  work_id: string | null;
  oneapi_estimated_cost_cny: number | null;
  transcription: TranscriptionResponse | null;
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
  published_window_days: 0 | 1 | 7;
  /** 热点宝榜单统计周期；不等同于视频发布时间。 */
  hotspot_window_hours?: 1 | 24 | 72 | 168;
  count_per_platform: number;
  force_refresh: boolean;
  /** smart 先走热点宝；OneAPI 只在明确二次确认后使用。 */
  mode?: "official_hot" | "smart";
  /** 用户明确给出的相关赛道词；不会由系统自动扩词。 */
  related_terms?: string[];
  /** 候选不足时，是否允许仅用首个相关词做一次额外检索。 */
  allow_related_fallback?: boolean;
  /** 是否启用会产生后续调用的真实趋势跟踪。 */
  track_trend?: boolean;
  target_main_count?: number;
  max_paid_calls?: number;
  allow_paid_fallback?: boolean;
  /** 热点宝所选统计周期五榜最终保留上限（不会扩大 OneAPI 单次上限）。 */
  hotspot_result_limit?: number;
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

export interface CrawlerSafetyStatus {
  profile: string;
  state: "ready" | "cached" | "running" | "cooldown" | "safety_pause" | string;
  cache_ttl_minutes: number;
  estimated_duration_seconds: number;
  cooldown_remaining_seconds: number;
  next_available_at: string | null;
  real_runs_in_window?: number;
  real_run_limit?: number;
  rolling_window_ends_at?: string | null;
  message: string;
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
  monitoring_policy?: string;
  sampling_offsets_hours?: number[];
  max_api_calls_per_platform?: number;
  blocked: boolean;
  free_candidate_count?: number;
  free_pool_status?: "ready" | "empty" | "unavailable" | string;
  free_pool_message?: string | null;
  paid_fallback_required?: boolean;
  paid_fallback_cache_ttl_minutes?: number | null;
  paid_fallback_blocked_reason?: string | null;
  related_fallback_possible?: boolean;
  related_fallback_term?: string | null;
  trend_tracking_enabled?: boolean;
  hotspot_ready?: boolean;
  hotspot_message?: string | null;
  hotspot_time_strategy?: string;
  target_main_count?: number;
  paid_call_cap?: number;
  hotspot_result_limit?: number;
  hotspot_list_types?: string[];
  crawl_safety?: CrawlerSafetyStatus | null;
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
  /** 热点宝的 play_cnt，语义由 hotspot_window_hours 决定。 */
  new_plays?: number | null;
  /** 热点宝的 like_cnt，语义由 hotspot_window_hours 决定。 */
  new_likes?: number | null;
  duration_seconds?: number | null;
  hotspot_window_hours?: number | null;
  hotspot_list_labels?: string[];
  component_scores: Record<string, number | null>;
  data_quality_warnings: string[];
  model_version: string | null;
  evidence: string | null;
  reasons: string[];
  media_resolution_status: string | null;
  media_transcription_task_id: string | null;
  /** 增长阶段：观察样本 / 增长确认中 / 热门候选 / 爆发候选 */
  growth_stage?: string;
  /** 已采集快照数 */
  snapshot_count?: number;
  /** 下一次计划复爬时间 */
  next_recrawl_at?: string | null;
  /** 文案来源 */
  copy_source?: CrawlerCopySource | null;
  /** 是否为原视频原版转写 */
  is_original_transcript?: boolean;
  /** 是否需要人工复核 */
  needs_manual_review?: boolean;
  /** 分享数（供应商未返回时为 null，前端显示「未返回」） */
  share_count?: number | null;
  /** 收藏数（供应商未返回时为 null，前端显示「未返回」） */
  collect_count?: number | null;
  /** 严格关键词规则的命中位置 */
  relevance_basis?: "title_or_hashtag" | string | null;
  /** 可解释的关键词命中原因 */
  relevance_reason?: string | null;
  trend_points?: CrawlerTrendPoint[];
}

export interface CrawlerTrendPoint {
  sampled_at: string;
  effective_interactions: number;
  growth_per_hour?: number | null;
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
  /** 本机前置条件逐项状态（后端未上线时为 undefined/null） */
  prerequisites?: CrawlerDoubaoMobilePrerequisites | null;
  /** 创建任务费用，手机豆包链路恒为 0 */
  estimated_cost_cny?: number;
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
  /** 严格关键词匹配后可展示的候选数；旧后端缺失时回退 returned_count */
  relevant_count?: number;
  strict_relevant_count?: number;
  below_heat_floor_count?: number;
  /** 已解析但未通过标题/话题严格匹配的候选数 */
  irrelevant_count?: number;
  /** 热点宝过滤：时长为 0 或未返回时长。 */
  duration_filtered_count?: number;
  /** 热点宝过滤：新增播放量不大于 1000。 */
  incremental_play_filtered_count?: number;
  relevance_rule_version?: string | null;
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
  /** 热点宝主榜为空时，返回严格相关但新增播放量不超过 1,000 的参考视频。 */
  low_incremental_candidates?: CrawlerCandidateResult[];
}

export interface CrawlerBatchResponse {
  batch_id: string;
  keyword: string;
  published_window_days: number;
  hotspot_window_hours?: number | null;
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
  monitoring_policy?: string;
  sampling_offsets_hours?: number[];
  free_candidate_count?: number;
  paid_fallback_used?: boolean;
  paid_fallback_blocked_reason?: string | null;
  related_terms?: string[];
  related_fallback_used?: boolean;
  trend_tracking_enabled?: boolean;
  tracking_status?: "not_started" | "scheduled" | "complete" | "cancelled" | "partial" | string;
  next_tracking_at?: string | null;
  crawl_safety?: CrawlerSafetyStatus | null;
}

export interface CrawlerTrackingResponse {
  batch: CrawlerBatchResponse;
  scheduled_candidates: number;
  additional_api_calls: number;
  estimated_additional_cost_cny: number | null;
  next_tracking_at: string | null;
  message: string;
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
  compliance_status: "passed" | "review_required" | "not_checked" | string;
  compliance_notes: string[];
  compliance_rewritten: boolean;
  compliance_retry_used: boolean;
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
  target_audience?: string;
  selling_points?: string;
  call_to_action?: string;
  style_prompt?: string;
  tone?: string;
  variant_count?: number;
}

export interface CopywritingRewriteRequest {
  source_text: string;
  target_audience?: string;
  style_prompt?: string;
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
  action_required?: string | null;
  final_publish_started_at?: string | null;
  outcome_evidence?: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface PublishMetadataResponse {
  task_id: string;
  provider_name: string;
  model_name: string;
  is_mock: boolean;
  title: string;
  description: string;
  tags: string[];
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
  requires_account: boolean;
  setup_required: boolean;
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
  issue_code: "account_missing" | "account_not_ready" | string | null;
  account_status: "needs_login" | "browser_open" | "ready" | "missing" | string | null;
  missing_configuration: string[];
  manual_steps: string[];
}

export interface PublishPreflightResponse {
  blocked: boolean;
  video_path: string;
  issues: string[];
  platforms: PublishPreflightPlatform[];
}

export interface PublishAccount {
  account_id: string;
  platform: string;
  name: string;
  status: "needs_login" | "browser_open" | "ready" | "error" | string;
  message: string;
  auto_publish_authorized: boolean;
  last_verified_at: string | null;
  created_at: string | null;
  updated_at: string | null;
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
  recommended_title?: string | null;
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
  redirect_uri?: string;
}

export interface PublishConnection {
  platform: string;
  state: "not_configured" | "disconnected" | "connected" | "expired" | string;
  ready: boolean;
  account: string | null;
  expires_at: string | null;
  message: string;
  authorization_available: boolean;
}

export interface PublishConnectionStartResponse {
  platform: string;
  authorization_url: string;
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
  supports_cloud_avatar_training: boolean;
  supports_voice_cloning: boolean;
  supports_voice_sample_upload: boolean;
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
  preview_type: "image" | "video" | string;
  status: "ready" | "training" | "failed" | string;
  status_message: string | null;
  source_type: string;
}

export interface AvatarJob {
  task_id: string;
  status: string;
  progress: number;
  stage: string;
  title: string;
  video_name: string;
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
  video_name?: string | null;
  keyword?: string | null;
  script_text: string;
  avatar_id: string;
  voice_id: string;
  profile_id?: string;
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

export interface VideoEditorSource {
  source_id: string;
  source_type: "avatar" | "pipeline" | "upload";
  source_task_id: string;
  title: string;
  file_name: string;
  size_bytes: number;
  created_at: string;
  media_url: string;
  media_type: string;
}

export interface VideoEditorSourceListResponse {
  items: VideoEditorSource[];
  total: number;
}

export interface VideoEditorRecommendationStep {
  kind: string;
  params: Record<string, unknown>;
  enabled: boolean;
  label: string;
}

export interface VideoEditorAnalysis {
  analysis_id: string;
  status: string;
  progress: number;
  stage: string;
  error_message: string | null;
  source_id: string;
  target_platform: string;
  subtitle_enabled: boolean;
  subtitle_model: string;
  media?: {
    duration_seconds: number;
    width: number;
    height: number;
    fps: number;
    orientation: "vertical" | "horizontal";
    has_audio: boolean;
    size_bytes: number;
  };
  audio?: {
    available: boolean;
    mean_volume_db?: number | null;
    silence_seconds?: number;
    silence_intervals?: { start: number; end: number }[];
  };
  findings?: string[];
  recommended_steps?: VideoEditorRecommendationStep[];
  subtitle_task_id?: string | null;
  subtitle_error?: string | null;
  content_advice?: string | null;
  title_candidates?: string[];
}

export interface VideoEditorJob {
  task_id: string;
  status: string;
  progress: number;
  stage: string;
  error_message: string | null;
  result_size_bytes: number | null;
  media_url: string | null;
  download_url: string | null;
  source_id: string | null;
  analysis_id: string | null;
  publish_title: string | null;
}

export interface VideoEditorJobListResponse {
  items: VideoEditorJob[];
  total: number;
}

export interface VideoEditorBatchItem {
  item_id: string;
  source_id: string;
  title: string;
  status: string;
  analysis_id: string | null;
  subtitle_task_id: string | null;
  edit_task_id: string | null;
  title_candidates: string[];
  selected_title: string | null;
  selected_bgm_id: string | null;
  bgm_reason: string | null;
  error_message: string | null;
  confirmed_at: string | null;
  analysis: VideoEditorAnalysis | null;
  job: VideoEditorJob | null;
}

export interface VideoEditorBatch {
  batch_id: string;
  status: string;
  target_platform: string;
  subtitle_enabled: boolean;
  subtitle_model: string;
  bgm_enabled: boolean;
  bgm_id: string | null;
  bgm_volume: number;
  bgm: VideoEditorBgmAsset | null;
  items: VideoEditorBatchItem[];
  created_at: string;
  updated_at: string;
}

export interface VideoEditorBatchListResponse {
  items: VideoEditorBatch[];
  total: number;
}

export interface VideoEditorBgmAsset {
  asset_id: string;
  title: string;
  original_name: string;
  media_type: string;
  mood: string;
  rights_holder: string;
  rights_confirmed_at: string;
  created_at: string;
  duration_seconds: number;
  size_bytes: number;
  media_url: string;
}

export interface VideoEditorBgmListResponse {
  items: VideoEditorBgmAsset[];
  total: number;
}

export interface VideoEditorLocalModel {
  model_name: "base" | "large-v3-turbo";
  repository: string;
  installed: boolean;
  size_bytes: number;
  device: "cpu";
  compute_type: "int8";
  loaded_lazily: boolean;
}

export interface VideoEditorLocalModelListResponse {
  items: VideoEditorLocalModel[];
  total: number;
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
