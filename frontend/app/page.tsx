"use client";

import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";

import { LatencyChart } from "@/components/dashboard/latency-chart";
import { MetricCard } from "@/components/dashboard/metric-card";
import { RagasChart } from "@/components/dashboard/ragas-chart";
import { RecentQueriesTable } from "@/components/dashboard/recent-queries-table";
import { api } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import type { AnalyticsOverview, QueryLogItem } from "@/types";

const mockOverview: AnalyticsOverview = {
  total_queries: 12847,
  queries_today: 142,
  queries_this_week: 1268,
  faithfulness_avg: 0.873,
  answer_relevancy_avg: 0.914,
  context_precision_avg: 0.831,
  latency_p50_ms: 220,
  latency_p95_ms: 340,
  latency_p99_ms: 780,
  latency_mean_ms: 286,
  evaluation_coverage_pct: 94.2,
  active_projects: 7,
  total_documents: 184,
  total_chunks: 24650,
  estimated_cost_usd_30d: 34.82,
  no_answer_rate_pct: 3.8,
  insufficient_evidence_rate_pct: 6.4,
  scores_over_time: [
    { date: "2026-04-09", faithfulness: 0.82, answer_relevancy: 0.89, context_precision: 0.78, query_count: 61 },
    { date: "2026-04-10", faithfulness: 0.84, answer_relevancy: 0.91, context_precision: 0.8, query_count: 74 },
    { date: "2026-04-11", faithfulness: 0.83, answer_relevancy: 0.9, context_precision: 0.81, query_count: 70 },
    { date: "2026-04-12", faithfulness: 0.86, answer_relevancy: 0.92, context_precision: 0.82, query_count: 83 },
    { date: "2026-04-13", faithfulness: 0.87, answer_relevancy: 0.91, context_precision: 0.83, query_count: 96 },
    { date: "2026-04-14", faithfulness: 0.85, answer_relevancy: 0.9, context_precision: 0.8, query_count: 88 },
    { date: "2026-04-15", faithfulness: 0.88, answer_relevancy: 0.93, context_precision: 0.84, query_count: 114 },
    { date: "2026-04-16", faithfulness: 0.87, answer_relevancy: 0.92, context_precision: 0.82, query_count: 105 },
    { date: "2026-04-17", faithfulness: 0.89, answer_relevancy: 0.93, context_precision: 0.85, query_count: 132 },
    { date: "2026-04-18", faithfulness: 0.86, answer_relevancy: 0.9, context_precision: 0.82, query_count: 118 },
    { date: "2026-04-19", faithfulness: 0.88, answer_relevancy: 0.92, context_precision: 0.83, query_count: 126 },
    { date: "2026-04-20", faithfulness: 0.9, answer_relevancy: 0.94, context_precision: 0.86, query_count: 137 },
    { date: "2026-04-21", faithfulness: 0.89, answer_relevancy: 0.93, context_precision: 0.85, query_count: 145 },
    { date: "2026-04-22", faithfulness: 0.873, answer_relevancy: 0.914, context_precision: 0.831, query_count: 142 },
  ],
  latency_by_strategy: [
    { strategy: "vanilla", p50: 180, p95: 430, p99: 680, mean: 230, count: 145 },
    { strategy: "hybrid", p50: 260, p95: 620, p99: 910, mean: 312, count: 890 },
    { strategy: "rerank", p50: 410, p95: 940, p99: 1320, mean: 492, count: 276 },
    { strategy: "hyde", p50: 520, p95: 1120, p99: 1580, mean: 610, count: 93 },
  ],
};

const mockQueries: QueryLogItem[] = [
  {
    id: "mock-1",
    query_truncated: "How does hybrid retrieval differ from vanilla vector search?",
    answer_truncated: "Hybrid retrieval combines semantic and keyword matching...",
    strategy: "hybrid",
    routing_decision: "rag",
    faithfulness: 0.91,
    answer_relevancy: 0.94,
    total_latency_ms: 342,
    evaluation_status: "completed",
    created_at: new Date(Date.now() - 180000).toISOString(),
  },
  {
    id: "mock-2",
    query_truncated: "Summarize the onboarding policy changes from the uploaded handbook",
    answer_truncated: "The policy updates focus on manager approvals...",
    strategy: "rerank",
    routing_decision: "rag",
    faithfulness: 0.76,
    answer_relevancy: 0.88,
    total_latency_ms: 684,
    evaluation_status: "completed",
    created_at: new Date(Date.now() - 920000).toISOString(),
  },
  {
    id: "mock-3",
    query_truncated: "Which documents mention SOC 2 audit evidence?",
    answer_truncated: "The security controls document and vendor checklist mention...",
    strategy: "vanilla",
    routing_decision: "rag",
    faithfulness: 0.84,
    answer_relevancy: 0.9,
    total_latency_ms: 218,
    evaluation_status: "completed",
    created_at: new Date(Date.now() - 2400000).toISOString(),
  },
];

export default function DashboardPage() {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <DashboardContent />
    </QueryClientProvider>
  );
}

function DashboardContent() {
  const tenant = useAppStore((state) => state.tenant);
  const overviewQuery = useQuery({
    queryKey: ["analytics-overview"],
    queryFn: () => api.analytics.overview(),
    refetchInterval: 60000,
    retry: 1,
  });
  const recentQueriesQuery = useQuery({
    queryKey: ["analytics-queries", "recent"],
    queryFn: () => api.analytics.queries({ page: 1, per_page: 10, sort_by: "created_at", sort_order: "desc" }),
    refetchInterval: 60000,
    retry: 1,
  });

  const overview = overviewQuery.data ?? mockOverview;
  const recentQueries = recentQueriesQuery.data?.items ?? (recentQueriesQuery.isError ? mockQueries : []);
  const isUsingFallback = overviewQuery.isError || recentQueriesQuery.isError;
  const isRefreshing = overviewQuery.isFetching || recentQueriesQuery.isFetching;

  const summary = useMemo(() => buildSummary(overview), [overview]);

  function refresh() {
    void overviewQuery.refetch();
    void recentQueriesQuery.refetch();
  }

  return (
    <section className="mx-auto flex w-full max-w-7xl flex-col gap-6">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-normal text-zinc-50">Dashboard</h1>
          <p className="mt-1 text-sm text-zinc-400">System overview for {tenant?.name ?? "your workspace"}</p>
        </div>
        <button
          type="button"
          onClick={refresh}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-zinc-800 bg-zinc-900 px-4 text-sm text-zinc-200 transition hover:bg-zinc-800"
        >
          <RefreshCw className={`h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </header>

      {isUsingFallback ? (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium text-amber-300">Live analytics unavailable</p>
              <p className="mt-1 text-sm text-amber-100/70">Showing local dashboard fallback data until the API responds.</p>
            </div>
            <button
              type="button"
              onClick={refresh}
              className="inline-flex h-9 items-center justify-center rounded-md border border-amber-500/30 px-3 text-sm text-amber-200 transition hover:bg-amber-500/10"
            >
              Retry
            </button>
          </div>
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Total Queries" value={overview.total_queries} trend={12.3} loading={overviewQuery.isLoading} />
        <MetricCard
          label="Avg Faithfulness"
          value={(overview.faithfulness_avg ?? 0) * 100}
          trend={3.4}
          format="percentage"
          threshold="faithfulness"
          loading={overviewQuery.isLoading}
        />
        <MetricCard
          label="P95 Latency"
          value={overview.latency_p95_ms}
          trend={-5.1}
          format="milliseconds"
          threshold="latency"
          loading={overviewQuery.isLoading}
        />
        <MetricCard
          label="Evaluation Coverage"
          value={overview.evaluation_coverage_pct}
          trend={8.6}
          format="percentage"
          loading={overviewQuery.isLoading}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <RagasChart data={overview.scores_over_time} loading={overviewQuery.isLoading} />
        </div>
        <aside className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
          <h2 className="text-sm font-medium text-zinc-50">Summary</h2>
          <div className="mt-5 space-y-4">
            {summary.map((item) => (
              <div key={item.label} className="flex items-center justify-between gap-4 border-b border-zinc-800 pb-4 last:border-0 last:pb-0">
                <span className="text-sm text-zinc-400">{item.label}</span>
                <span className="font-mono text-sm text-zinc-100">{item.value}</span>
              </div>
            ))}
          </div>
        </aside>
      </div>

      <LatencyChart data={overview.latency_by_strategy} loading={overviewQuery.isLoading} />
      <RecentQueriesTable queries={recentQueries} loading={recentQueriesQuery.isLoading} />
    </section>
  );
}

function buildSummary(overview: AnalyticsOverview) {
  return [
    { label: "Queries today", value: overview.queries_today.toLocaleString() },
    { label: "Queries this week", value: overview.queries_this_week.toLocaleString() },
    { label: "Active projects", value: overview.active_projects.toLocaleString() },
    { label: "Documents", value: overview.total_documents.toLocaleString() },
    { label: "Chunks", value: overview.total_chunks.toLocaleString() },
    { label: "30d cost", value: `$${overview.estimated_cost_usd_30d.toFixed(2)}` },
    { label: "No-answer rate", value: `${(overview.no_answer_rate_pct ?? 0).toFixed(1)}%` },
    { label: "Insufficient evidence", value: `${(overview.insufficient_evidence_rate_pct ?? 0).toFixed(1)}%` },
  ];
}
