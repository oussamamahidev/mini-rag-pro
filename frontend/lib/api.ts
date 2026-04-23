import type {
  AnalyticsOverview,
  ApiError,
  AppSettings,
  CostBreakdown,
  Document as RagDocument,
  DocumentStatusUpdate,
  FailedQuery,
  HealthResponse,
  PaginatedResponse,
  Project,
  QueryHistoryItem,
  QueryLogItem,
  QueryParams,
  QueryResponse,
  RotateKeyResponse,
  SettingsPatch,
  StrategyComparison,
  Tenant,
  UploadResponse,
} from "@/types";

const API_PREFIX = "/api";
const API_KEY_STORAGE_KEY = "minirag_api_key";
const STORE_STORAGE_KEY = "minirag-store";

type QueryValue = string | number | boolean | null | undefined;

export class MiniRagApiError extends Error implements ApiError {
  detail: string;
  status: number;

  constructor(detail: string, status: number) {
    super(detail);
    this.name = "MiniRagApiError";
    this.detail = detail;
    this.status = status;
  }
}

async function fetchAPI<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const hasFormDataBody = options.body instanceof FormData;

  if (!hasFormDataBody && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const apiKey = getStoredApiKey();
  if (apiKey) {
    headers.set("X-API-Key", apiKey);
  }

  const response = await fetch(normalizePath(path), {
    ...options,
    headers,
  });

  if (!response.ok) {
    const error = await buildApiError(response);
    handleUnauthorized(response.status);
    throw error;
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

async function fetchBlobAPI(path: string, options: RequestInit = {}): Promise<Blob> {
  const headers = new Headers(options.headers);
  const apiKey = getStoredApiKey();
  if (apiKey) {
    headers.set("X-API-Key", apiKey);
  }

  const response = await fetch(normalizePath(path), {
    ...options,
    headers,
  });

  if (!response.ok) {
    const error = await buildApiError(response);
    handleUnauthorized(response.status);
    throw error;
  }

  return response.blob();
}

async function uploadDocument(projectId: string, file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("project_id", projectId);
  formData.append("file", file);

  return fetchAPI<UploadResponse>("/documents/upload", {
    method: "POST",
    body: formData,
  });
}

function normalizePath(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${API_PREFIX}${normalizedPath}`;
}

async function buildApiError(response: Response): Promise<MiniRagApiError> {
  const fallback = response.statusText || "Request failed";
  try {
    const payload = (await response.json()) as { detail?: unknown; error?: { message?: unknown } };
    const detail =
      typeof payload.detail === "string"
        ? payload.detail
        : typeof payload.error?.message === "string"
          ? payload.error.message
          : fallback;
    return new MiniRagApiError(detail, response.status);
  } catch {
    return new MiniRagApiError(fallback, response.status);
  }
}

function handleUnauthorized(status: number): void {
  if (status !== 401 || typeof window === "undefined") {
    return;
  }
  window.localStorage.removeItem(API_KEY_STORAGE_KEY);
  window.localStorage.removeItem(STORE_STORAGE_KEY);
  if (window.location.pathname !== "/login") {
    window.location.href = "/login";
  }
}

function getStoredApiKey(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  const directKey = window.localStorage.getItem(API_KEY_STORAGE_KEY);
  if (directKey) {
    return directKey;
  }

  const rawStore = window.localStorage.getItem(STORE_STORAGE_KEY);
  if (!rawStore) {
    return null;
  }

  try {
    const parsed = JSON.parse(rawStore) as { state?: { apiKey?: string | null } };
    return parsed.state?.apiKey ?? null;
  } catch {
    return null;
  }
}

function toSearchParams(params?: object): string {
  if (!params) {
    return "";
  }

  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params) as Array<[string, QueryValue]>) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    searchParams.set(key, String(value));
  }

  const query = searchParams.toString();
  return query ? `?${query}` : "";
}

export const api = {
  auth: {
    register: (data: { name: string; email: string }) =>
      fetchAPI<{ tenant_id: string; api_key: string }>("/auth/register", {
        method: "POST",
        body: JSON.stringify(data),
    }),
    me: () => fetchAPI<Tenant>("/auth/me"),
    rotateKey: () =>
      fetchAPI<RotateKeyResponse>("/auth/rotate-key", {
        method: "POST",
      }),
  },
  health: {
    check: () => fetchAPI<HealthResponse>("/health"),
  },
  projects: {
    list: () => fetchAPI<Project[]>("/projects"),
    create: (data: { name: string; description?: string; retrieval_strategy: string }) =>
      fetchAPI<Project>("/projects", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    get: (id: string) => fetchAPI<Project>(`/projects/${id}`),
    update: (id: string, data: Partial<Project>) =>
      fetchAPI<Project>(`/projects/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    delete: (id: string) =>
      fetchAPI<void>(`/projects/${id}`, {
        method: "DELETE",
      }),
  },
  documents: {
    list: (projectId: string, page = 1) =>
      fetchAPI<PaginatedResponse<RagDocument>>(`/documents${toSearchParams({ project_id: projectId, page })}`),
    upload: uploadDocument,
    getStatus: (id: string) => fetchAPI<DocumentStatusUpdate>(`/documents/${id}/status`),
    delete: (id: string) =>
      fetchAPI<void>(`/documents/${id}`, {
        method: "DELETE",
      }),
    reindex: (id: string) =>
      fetchAPI<void>(`/documents/${id}/reindex`, {
        method: "POST",
      }),
  },
  query: {
    ask: (data: { text: string; project_id: string; strategy: string; session_id?: string }) =>
      fetchAPI<QueryResponse>("/query", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    history: (projectId: string, page?: number) =>
      fetchAPI<PaginatedResponse<QueryHistoryItem>>(
        `/query/history${toSearchParams({ project_id: projectId, page: page ?? 1 })}`,
      ),
    clearSession: (sessionId: string) =>
      fetchAPI<void>(`/query/sessions/${sessionId}`, {
        method: "DELETE",
      }),
  },
  analytics: {
    overview: (days?: number) => fetchAPI<AnalyticsOverview>(`/analytics/overview${toSearchParams({ days })}`),
    queries: (params?: QueryParams) => fetchAPI<PaginatedResponse<QueryLogItem>>(`/analytics/queries${toSearchParams(params)}`),
    strategies: (days?: number) => fetchAPI<StrategyComparison[]>(`/analytics/strategies${toSearchParams({ days })}`),
    failed: (params?: { limit?: number; project_id?: string }) =>
      fetchAPI<FailedQuery[]>(`/analytics/failed${toSearchParams(params)}`),
    costs: () => fetchAPI<CostBreakdown>("/analytics/costs"),
    export: (params?: { date_from?: string; date_to?: string; project_id?: string }) =>
      fetchBlobAPI(`/analytics/export${toSearchParams(params)}`),
  },
  settings: {
    update: (data: SettingsPatch) =>
      fetchAPI<AppSettings>("/settings", {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
  },
};

export { fetchAPI };
