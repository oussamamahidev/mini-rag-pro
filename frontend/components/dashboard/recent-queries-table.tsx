"use client";

import { useRouter } from "next/navigation";

import { Skeleton } from "@/components/ui/skeleton";
import type { QueryLogItem } from "@/types";

interface RecentQueriesTableProps {
  queries: QueryLogItem[];
  loading?: boolean;
}

export function RecentQueriesTable({ queries, loading = false }: RecentQueriesTableProps) {
  const router = useRouter();

  if (loading) {
    return (
      <section className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
        <Skeleton className="h-5 w-32" />
        <div className="mt-5 space-y-2">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-12 w-full" />
          ))}
        </div>
      </section>
    );
  }

  return (
    <section className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900">
      <div className="border-b border-zinc-800 px-5 py-4">
        <h2 className="text-sm font-medium text-zinc-50">Recent queries</h2>
        <p className="mt-1 text-xs text-zinc-400">Latest retrieval and evaluation activity</p>
      </div>

      {queries.length === 0 ? (
        <div className="px-5 py-12 text-center text-sm text-zinc-400">No queries yet. Start chatting in a project.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="border-b border-zinc-800 text-xs text-zinc-500">
              <tr>
                <th className="px-5 py-3 font-medium">Query</th>
                <th className="px-5 py-3 font-medium">Strategy</th>
                <th className="px-5 py-3 font-medium">Faithfulness</th>
                <th className="px-5 py-3 font-medium">Latency</th>
                <th className="px-5 py-3 font-medium">Time</th>
              </tr>
            </thead>
            <tbody>
              {queries.slice(0, 10).map((query, index) => (
                <tr
                  key={query.id}
                  onClick={() => router.push(`/analytics/queries/${query.id}`)}
                  className={`cursor-pointer border-b border-zinc-800/70 transition hover:bg-zinc-800/80 ${
                    index % 2 === 0 ? "bg-zinc-950" : "bg-zinc-900"
                  }`}
                >
                  <td className="max-w-[360px] px-5 py-3 text-zinc-100">{truncate(query.query_truncated, 60)}</td>
                  <td className="px-5 py-3">
                    <StrategyBadge strategy={query.strategy} />
                  </td>
                  <td className="px-5 py-3">
                    <FaithfulnessBar value={query.faithfulness} />
                  </td>
                  <td className="px-5 py-3 font-mono text-zinc-300">{formatLatency(query.total_latency_ms)}</td>
                  <td className="px-5 py-3 text-zinc-400">{relativeTime(query.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function StrategyBadge({ strategy }: { strategy: string }) {
  const styles: Record<string, string> = {
    vanilla: "border-zinc-700 bg-zinc-800 text-zinc-200",
    hybrid: "border-indigo-500/40 bg-indigo-500/15 text-indigo-300",
    rerank: "border-purple-500/40 bg-purple-500/15 text-purple-300",
    hyde: "border-teal-500/40 bg-teal-500/15 text-teal-300",
  };

  return (
    <span className={`inline-flex rounded-full border px-2 py-1 font-mono text-xs ${styles[strategy] ?? styles.vanilla}`}>
      {strategy}
    </span>
  );
}

function FaithfulnessBar({ value }: { value: number | null }) {
  const percent = value === null ? 0 : Math.round(value * 100);
  const color = percent >= 80 ? "bg-emerald-500" : percent >= 60 ? "bg-amber-500" : "bg-red-500";

  return (
    <div className="flex min-w-36 items-center gap-3">
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-zinc-800">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${percent}%` }} />
      </div>
      <span className="w-10 text-right font-mono text-xs text-zinc-300">{value === null ? "--" : `${percent}%`}</span>
    </div>
  );
}

function truncate(value: string, limit: number): string {
  if (value.length <= limit) {
    return value;
  }
  return `${value.slice(0, limit - 3).trimEnd()}...`;
}

function formatLatency(value: number | null): string {
  if (value === null) {
    return "--";
  }
  return `${Math.round(value).toLocaleString()}ms`;
}

function relativeTime(value: string | null): string {
  if (!value) {
    return "--";
  }

  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return "--";
  }

  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) {
    return "just now";
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes} min ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours} hr ago`;
  }
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

