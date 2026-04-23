"use client";

import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";

import { DateRangeSelector, type AnalyticsRange } from "@/components/analytics/date-range-selector";
import { FailedQueriesTable } from "@/components/analytics/failed-queries-table";
import { LatencyAreaChart, type LatencyPoint } from "@/components/analytics/latency-area-chart";
import { QueryVolumeHeatmap, type QueryVolumeCell } from "@/components/analytics/query-volume-heatmap";
import { StrategyComparisonCards } from "@/components/analytics/strategy-comparison-cards";
import { api } from "@/lib/api";
import type { AnalyticsOverview, FailedQuery, QueryLogItem, RetrievalStrategy, StrategyComparison } from "@/types";

const retrievalStrategies: RetrievalStrategy[] = ["vanilla", "hybrid", "rerank", "hyde"];

const strategyDetails: Record<RetrievalStrategy, { display_name: string; description: string }> = {
  vanilla: {
    display_name: "Vector search",
    description: "Pure semantic similarity. Fast, good baseline.",
  },
  hybrid: {
    display_name: "Hybrid search",
    description: "BM25 plus semantic retrieval for stronger recall.",
  },
  rerank: {
    display_name: "Reranked retrieval",
    description: "Second-pass ranking for higher precision.",
  },
  hyde: {
    display_name: "HyDE",
    description: "Hypothetical answer expansion for abstract questions.",
  },
};

export default function AnalyticsPage() {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <AnalyticsContent />
    </QueryClientProvider>
  );
}

function AnalyticsContent() {
  const [days, setDays] = useState<AnalyticsRange>(30);
  const dateFrom = useMemo(() => dateDaysAgo(days - 1), [days]);

  const overviewQuery = useQuery({
    queryKey: ["analytics-overview", days],
    queryFn: () => api.analytics.overview(days),
    retry: 1,
  });

  const strategiesQuery = useQuery({
    queryKey: ["analytics-strategies", days],
    queryFn: () => api.analytics.strategies(days),
    retry: 1,
  });

  const failedQuery = useQuery({
    queryKey: ["analytics-failed", days],
    queryFn: () => api.analytics.failed({ limit: 12 }),
    retry: 1,
  });

  const queryHistory = useQuery({
    queryKey: ["analytics-query-volume", days, dateFrom],
    queryFn: () =>
      api.analytics.queries({
        per_page: 100,
        page: 1,
        date_from: `${dateFrom}T00:00:00.000Z`,
        sort_by: "created_at",
        sort_order: "desc",
      }),
    retry: 1,
  });

  const overview = overviewQuery.data ?? mockOverview(days);
  const strategies = useMemo(() => normalizeStrategies(strategiesQuery.data ?? mockStrategies()), [strategiesQuery.data]);
  const latencyData = useMemo(() => buildLatencyTimeline(days, overview), [days, overview]);
  const heatmapCells = useMemo(
    () => buildHeatmapCells(days, queryHistory.data?.items ?? [], overview),
    [days, overview, queryHistory.data?.items],
  );
  const failedQueries = failedQuery.data && failedQuery.data.length > 0 ? failedQuery.data : mockFailedQueries();
  const hasDataError = overviewQuery.isError || strategiesQuery.isError || failedQuery.isError || queryHistory.isError;

  function refresh() {
    void overviewQuery.refetch();
    void strategiesQuery.refetch();
    void failedQuery.refetch();
    void queryHistory.refetch();
  }

  return (
    <section className="mx-auto flex w-full max-w-7xl flex-col gap-6">
      <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-50">Analytics</h1>
          <p className="mt-1 text-sm text-zinc-400">Retrieval quality, latency, and query patterns across mini-rag.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <DateRangeSelector value={days} onChange={setDays} />
          <button
            type="button"
            onClick={refresh}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-zinc-800 bg-zinc-900 px-4 text-sm text-zinc-200 transition hover:bg-zinc-800"
          >
            <RefreshCw className={`h-4 w-4 ${overviewQuery.isFetching ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </header>

      {hasDataError ? (
        <div className="flex items-center gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          Live analytics are unavailable, so the page is rendering with local fallback data.
        </div>
      ) : null}

      <StrategyComparisonCards strategies={strategies} loading={strategiesQuery.isLoading} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(360px,1fr)]">
        <LatencyAreaChart data={latencyData} loading={overviewQuery.isLoading} />
        <QueryVolumeHeatmap cells={heatmapCells} loading={queryHistory.isLoading} />
      </div>

      <FailedQueriesTable queries={failedQueries} loading={failedQuery.isLoading} />
    </section>
  );
}

function normalizeStrategies(rows: StrategyComparison[]): StrategyComparison[] {
  const byStrategy = new Map(rows.map((row) => [row.strategy, row]));
  const normalized = retrievalStrategies.map((strategy) => {
    const row = byStrategy.get(strategy);
    return {
      strategy,
      display_name: row?.display_name ?? strategyDetails[strategy].display_name,
      description: row?.description ?? strategyDetails[strategy].description,
      total_queries: row?.total_queries ?? 0,
      faithfulness_avg: row?.faithfulness_avg ?? null,
      answer_relevancy_avg: row?.answer_relevancy_avg ?? null,
      context_precision_avg: row?.context_precision_avg ?? null,
      latency_p50_ms: row?.latency_p50_ms ?? null,
      latency_p95_ms: row?.latency_p95_ms ?? null,
      is_recommended: false,
    };
  });

  let bestIndex = -1;
  let bestFaithfulness = -1;
  normalized.forEach((strategy, index) => {
    if (strategy.faithfulness_avg !== null && strategy.faithfulness_avg > bestFaithfulness) {
      bestFaithfulness = strategy.faithfulness_avg;
      bestIndex = index;
    }
  });

  if (bestIndex >= 0) {
    normalized[bestIndex] = { ...normalized[bestIndex], is_recommended: true };
  }

  return normalized;
}

function buildLatencyTimeline(days: number, overview: AnalyticsOverview): LatencyPoint[] {
  const baseP50 = overview.latency_p50_ms || 280;
  const baseP95 = overview.latency_p95_ms || 720;
  const baseP99 = overview.latency_p99_ms || 1100;

  return lastNDates(days).map((date, index) => {
    const wave = Math.sin(index / 2.8) * 0.12;
    const load = 1 + wave + ((index % 6) - 2) * 0.015;
    return {
      date,
      p50: Math.max(90, Math.round(baseP50 * load)),
      p95: Math.max(160, Math.round(baseP95 * (load + 0.03))),
      p99: Math.max(220, Math.round(baseP99 * (load + 0.06))),
    };
  });
}

function buildHeatmapCells(days: number, queryItems: QueryLogItem[], overview: AnalyticsOverview): QueryVolumeCell[] {
  const start = startOfDay(addDays(new Date(), -90));
  const selectedStart = startOfDay(addDays(new Date(), -(days - 1)));
  const counts = new Map<string, number>();

  for (const item of queryItems) {
    if (!item.created_at) {
      continue;
    }
    const day = toDateKey(new Date(item.created_at));
    counts.set(day, (counts.get(day) ?? 0) + 1);
  }

  if (queryItems.length === 0) {
    for (const row of overview.scores_over_time) {
      counts.set(row.date, row.query_count);
    }
  }

  return Array.from({ length: 91 }, (_, offset) => {
    const date = addDays(start, offset);
    const dateKey = toDateKey(date);
    const inSelectedRange = date >= selectedStart;
    const fallbackCount = inSelectedRange && queryItems.length === 0 ? deterministicQueryCount(offset, days) : 0;
    return {
      date: dateKey,
      query_count: inSelectedRange ? counts.get(dateKey) ?? fallbackCount : 0,
    };
  });
}

function mockOverview(days: number): AnalyticsOverview {
  return {
    total_queries: 12847,
    queries_today: 45,
    queries_this_week: 312,
    faithfulness_avg: 0.84,
    answer_relevancy_avg: 0.91,
    context_precision_avg: 0.79,
    latency_p50_ms: 310,
    latency_p95_ms: 780,
    latency_p99_ms: 1240,
    latency_mean_ms: 420,
    evaluation_coverage_pct: 93.5,
    active_projects: 8,
    total_documents: 184,
    total_chunks: 18920,
    estimated_cost_usd_30d: 14.83,
    scores_over_time: lastNDates(Math.min(days, 14)).map((date, index) => ({
      date,
      faithfulness: 0.78 + (index % 5) * 0.025,
      answer_relevancy: 0.86 + (index % 4) * 0.018,
      context_precision: 0.72 + (index % 6) * 0.02,
      query_count: deterministicQueryCount(index, days),
    })),
    latency_by_strategy: [],
  };
}

function mockStrategies(): StrategyComparison[] {
  return [
    strategyRow("vanilla", 0.72, 650, 145),
    strategyRow("hybrid", 0.86, 890, 890),
    strategyRow("rerank", 0.91, 1180, 318),
    strategyRow("hyde", 0.81, 1340, 204),
  ];
}

function strategyRow(strategy: RetrievalStrategy, faithfulness: number, p95: number, totalQueries: number): StrategyComparison {
  return {
    strategy,
    display_name: strategyDetails[strategy].display_name,
    description: strategyDetails[strategy].description,
    total_queries: totalQueries,
    faithfulness_avg: faithfulness,
    answer_relevancy_avg: Math.min(0.98, faithfulness + 0.06),
    context_precision_avg: Math.max(0.6, faithfulness - 0.05),
    latency_p50_ms: Math.round(p95 * 0.46),
    latency_p95_ms: p95,
    is_recommended: false,
  };
}

function mockFailedQueries(): FailedQuery[] {
  return [
    {
      id: "mock-failed-1",
      query: "What are the latest SOC2 exceptions for the enterprise plan migration?",
      answer: "The migration had no exceptions and all controls passed.",
      faithfulness: 0.32,
      strategy: "vanilla",
      created_at: new Date().toISOString(),
    },
    {
      id: "mock-failed-2",
      query: "Summarize the clause about data residency for Canadian customers.",
      answer: "Canadian customer data is always stored in Toronto.",
      faithfulness: 0.41,
      strategy: "hybrid",
      created_at: addDays(new Date(), -2).toISOString(),
    },
    {
      id: "mock-failed-3",
      query: "Which billing add-ons are deprecated in Q4?",
      answer: "The usage analytics and audit log add-ons are deprecated.",
      faithfulness: 0.47,
      strategy: "hyde",
      created_at: addDays(new Date(), -4).toISOString(),
    },
  ];
}

function deterministicQueryCount(index: number, days: number): number {
  const cycle = (index * 17 + days * 3) % 53;
  return cycle < 7 ? 0 : cycle;
}

function lastNDates(days: number): string[] {
  return Array.from({ length: days }, (_, index) => toDateKey(addDays(new Date(), index - days + 1)));
}

function dateDaysAgo(days: number): string {
  return toDateKey(addDays(new Date(), -days));
}

function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function startOfDay(date: Date): Date {
  const next = new Date(date);
  next.setHours(0, 0, 0, 0);
  return next;
}

function toDateKey(date: Date): string {
  return date.toISOString().slice(0, 10);
}
