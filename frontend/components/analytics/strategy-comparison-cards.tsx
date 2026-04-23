"use client";

import { Award } from "lucide-react";

import type { StrategyComparison } from "@/types";

interface StrategyComparisonCardsProps {
  strategies: StrategyComparison[];
  loading?: boolean;
}

export function StrategyComparisonCards({ strategies, loading = false }: StrategyComparisonCardsProps) {
  if (loading) {
    return (
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="h-48 animate-pulse rounded-xl border border-zinc-800 bg-zinc-900" />
        ))}
      </div>
    );
  }

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {strategies.map((strategy) => (
        <article key={strategy.strategy} className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-zinc-50">{strategy.display_name}</h3>
              <p className="mt-2 line-clamp-2 text-sm leading-5 text-zinc-400">{strategy.description}</p>
            </div>
            {strategy.is_recommended ? (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-xs text-emerald-300">
                <Award className="h-3 w-3" />
                Recommended
              </span>
            ) : null}
          </div>

          <div className="mt-5">
            <p className="text-xs text-zinc-500">Faithfulness</p>
            <p className={`mt-1 font-mono text-3xl font-medium ${scoreColor(strategy.faithfulness_avg)}`}>
              {formatPercent(strategy.faithfulness_avg)}
            </p>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-3 border-t border-zinc-800 pt-4">
            <div>
              <p className="text-xs text-zinc-500">p95 latency</p>
              <p className="mt-1 font-mono text-sm text-zinc-200">{formatLatency(strategy.latency_p95_ms)}</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500">Total queries</p>
              <p className="mt-1 font-mono text-sm text-zinc-200">{strategy.total_queries.toLocaleString()}</p>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

function formatPercent(value: number | null): string {
  return value === null ? "n/a" : `${Math.round(value * 1000) / 10}%`;
}

function formatLatency(value: number | null): string {
  return value === null ? "n/a" : `${Math.round(value).toLocaleString()}ms`;
}

function scoreColor(value: number | null): string {
  if (value === null) {
    return "text-zinc-500";
  }
  if (value >= 0.8) {
    return "text-emerald-400";
  }
  if (value >= 0.6) {
    return "text-amber-400";
  }
  return "text-red-400";
}
