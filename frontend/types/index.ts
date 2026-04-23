export type RetrievalStrategy = "vanilla" | "hybrid" | "rerank" | "hyde";
export type DocumentStatus = "queued" | "processing" | "indexing" | "ready" | "error";
export type EvaluationStatus = "pending" | "in_progress" | "completed" | "failed" | "skipped";
export type RoutingDecision = "rag" | "web_search" | "direct" | "clarify";
export type ProjectStatus = "active" | "indexing" | "error";
export type TenantPlan = "free" | "pro" | "enterprise";
export type FileType = "pdf" | "txt" | "docx" | "md";

export interface Tenant {
  id: string;
  name: string;
  email: string;
  plan: TenantPlan;
  api_key_prefix: string;
  rate_limit_per_hour: number;
  created_at: string;
  last_active_at: string | null;
}

export interface Project {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  retrieval_strategy: RetrievalStrategy;
  status: ProjectStatus;
  document_count: number;
  chunk_count: number;
  query_count: number;
  created_at: string;
  updated_at: string;
}

export interface Document {
  id: string;
  project_id: string;
  original_filename: string;
  file_size_bytes: number;
  file_type: FileType;
  status: DocumentStatus;
  indexing_progress: number | null;
  error_message: string | null;
  chunk_count: number | null;
  created_at: string;
  processing_completed_at: string | null;
}

export interface DocumentStatusUpdate {
  id: string;
  original_filename?: string;
  status: DocumentStatus;
  indexing_progress?: number | null;
  error_message?: string | null;
  chunk_count?: number | null;
}

export interface Source {
  chunk_id: string;
  document_id: string;
  document_name: string;
  text: string;
  score: number;
  page_number: number | null;
}

export interface AnswerSegment {
  text: string;
  supporting_chunk_ids: string[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  sources?: Source[];
  routing_decision?: RoutingDecision;
  routing_reason?: string;
  metadata?: {
    strategy: string;
    latency_ms: number;
    faithfulness?: number;
  };
  isStreaming?: boolean;
  created_at: string;
}

export interface QueryResponse {
  answer: string;
  sources: Source[];
  retrieval_strategy: string;
  routing_decision: RoutingDecision | null;
  routing_reason: string | null;
  routing_reason_code: string | null;
  routing_confidence: number | null;
  total_latency_ms: number;
  routing_latency_ms: number | null;
  retrieval_latency_ms: number;
  generation_latency_ms: number;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost_usd: number;
  query_log_id: string;
  answer_segments: AnswerSegment[];
  confidence: number;
  insufficient_evidence: boolean;
}

export interface QueryHistoryItem {
  query: string;
  answer: string;
  strategy: string;
  latency_ms: number;
  faithfulness: number | null;
  created_at: string | null;
}

export interface QueryLogItem {
  id: string;
  query_truncated: string;
  answer_truncated: string;
  strategy: string;
  routing_decision: RoutingDecision | null;
  faithfulness: number | null;
  answer_relevancy: number | null;
  total_latency_ms: number | null;
  evaluation_status: EvaluationStatus;
  created_at: string | null;
}

export interface QueryParams {
  page?: number;
  per_page?: number;
  project_id?: string;
  strategy?: string;
  min_faithfulness?: number;
  max_faithfulness?: number;
  routing_decision?: RoutingDecision;
  date_from?: string;
  date_to?: string;
  sort_by?: "created_at" | "faithfulness" | "latency_ms";
  sort_order?: "asc" | "desc";
}

export interface AnalyticsOverview {
  total_queries: number;
  queries_today: number;
  queries_this_week: number;
  faithfulness_avg: number | null;
  answer_relevancy_avg: number | null;
  context_precision_avg: number | null;
  latency_p50_ms: number;
  latency_p95_ms: number;
  latency_p99_ms: number;
  latency_mean_ms: number;
  evaluation_coverage_pct: number;
  active_projects: number;
  total_documents: number;
  total_chunks: number;
  estimated_cost_usd_30d: number;
  no_answer_rate_pct?: number;
  insufficient_evidence_rate_pct?: number;
  scores_over_time: DailyScore[];
  latency_by_strategy: StrategyLatency[];
}

export interface DailyScore {
  date: string;
  faithfulness: number | null;
  answer_relevancy: number | null;
  context_precision: number | null;
  query_count: number;
}

export interface StrategyLatency {
  strategy: string;
  p50: number | null;
  p95: number | null;
  p99?: number | null;
  mean: number | null;
  count: number;
}

export interface StrategyComparison {
  strategy: string;
  display_name: string;
  description: string;
  total_queries: number;
  faithfulness_avg: number | null;
  answer_relevancy_avg: number | null;
  context_precision_avg: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  is_recommended: boolean;
}

export interface FailedQuery {
  id: string;
  query: string;
  answer: string;
  faithfulness: number | null;
  answer_relevancy?: number | null;
  context_precision?: number | null;
  strategy: string;
  routing_decision?: RoutingDecision | null;
  created_at: string | null;
  quality_score?: number | null;
}

export interface CostBreakdown {
  this_month: {
    total_tokens: number;
    prompt_tokens: number;
    completion_tokens: number;
    embedding_tokens: number;
    estimated_cost_usd: number;
  };
  daily_cost: Array<{
    date: string;
    cost_usd: number;
    queries: number;
  }>;
  cost_by_model: Array<{
    model: string;
    cost_usd: number;
    pct: number;
  }>;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
  pages?: number;
}

export interface UploadResponse {
  document_id: string;
  filename: string;
  status: DocumentStatus;
  message: string;
}

export interface HealthResponse {
  status: string;
  timestamp: string;
  version: string;
  environment: string;
}

export interface RotateKeyResponse {
  new_api_key: string;
  new_prefix: string;
  old_prefix: string;
  message: string;
  rotated_at: string;
}

export interface AppSettings {
  openai_api_key?: string;
  model?: string;
  max_tokens?: number;
  temperature?: number;
  default_strategy?: RetrievalStrategy;
  top_k?: number;
  reranker_model?: string;
  updated_at?: string;
}

export type SettingsPatch = Partial<AppSettings>;

export interface ApiError {
  detail: string;
  status: number;
}
