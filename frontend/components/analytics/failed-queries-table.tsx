"use client";

import Link from "next/link";

import type { FailedQuery } from "@/types";

interface FailedQueriesTableProps {
  queries: FailedQuery[];
  loading?: boolean;
}

export function FailedQueriesTable({ queries, loading = false }: FailedQueriesTableProps) {
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900">
      <div className="border-b border-zinc-800 p-5">
        <h2 className="text-base font-semibold text-zinc-50">Top failed queries</h2>
        <p className="mt-1 text-sm text-zinc-500">Faithfulness below 0.5, ordered by highest risk</p>
      </div>

      {loading ? (
        <div className="space-y-3 p-5">
          {Array.from({ length: 5 }).map((_, index) => (
            <div key={index} className="h-12 animate-pulse rounded-lg bg-zinc-800/70" />
          ))}
        </div>
      ) : queries.length === 0 ? (
        <div className="px-5 py-12 text-center text-sm text-zinc-500">No failed queries in this range.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="border-b border-zinc-800 text-xs text-zinc-500">
              <tr>
                <th className="px-5 py-3 font-medium">Query</th>
                <th className="px-5 py-3 font-medium">Faithfulness</th>
                <th className="px-5 py-3 font-medium">Strategy</th>
                <th className="px-5 py-3 font-medium">Date</th>
                <th className="px-5 py-3 font-medium">Detail</th>
              </tr>
            </thead>
            <tbody>
              {queries.map((query, index) => (
                <tr key={`${query.id}-${index}`} className="border-b border-zinc-800/70 transition hover:bg-zinc-800/60">
                  <td className="max-w-[420px] px-5 py-4 text-zinc-200">{truncate(query.query, 96)}</td>
                  <td className="px-5 py-4">
                    <span className={`font-mono text-sm ${scoreColor(query.faithfulness)}`}>{formatScore(query.faithfulness)}</span>
                  </td>
                  <td className="px-5 py-4">
                    <span className="rounded-full border border-zinc-800 bg-zinc-950 px-2 py-1 font-mono text-xs text-zinc-400">
                      {query.strategy}
                    </span>
                  </td>
                  <td className="px-5 py-4 text-xs text-zinc-500">{formatDate(query.created_at)}</td>
                  <td className="px-5 py-4">
                    <Link href={`/analytics/queries/${query.id}`} className="text-sm text-indigo-300 transition hover:text-indigo-200">
                      View full
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function truncate(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, maxLength - 3)}...`;
}

function formatScore(value: number | null): string {
  return value === null ? "n/a" : `${Math.round(value * 1000) / 10}%`;
}

function scoreColor(value: number | null): string {
  if (value === null) {
    return "text-zinc-500";
  }
  if (value >= 0.5) {
    return "text-amber-400";
  }
  return "text-red-400";
}

function formatDate(value: string | null): string {
  if (!value) {
    return "n/a";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "n/a";
  }
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(date);
}
