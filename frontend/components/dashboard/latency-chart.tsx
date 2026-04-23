"use client";

import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Skeleton } from "@/components/ui/skeleton";
import type { StrategyLatency } from "@/types";

interface LatencyChartProps {
  data: StrategyLatency[];
  loading?: boolean;
}

const strategyOrder = ["vanilla", "hybrid", "rerank", "hyde"];

export function LatencyChart({ data, loading = false }: LatencyChartProps) {
  if (loading) {
    return (
      <section className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="mt-5 h-[280px] w-full" />
      </section>
    );
  }

  const chartData = normalizeLatencyData(data);

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
      <div className="mb-5">
        <h2 className="text-sm font-medium text-zinc-50">Latency by strategy</h2>
        <p className="mt-1 text-xs text-zinc-400">Median and tail latency in milliseconds</p>
      </div>
      <div className="h-[280px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 20, right: 12, left: -18, bottom: 8 }}>
            <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="strategy" tick={{ fill: "#a1a1aa", fontSize: 12 }} axisLine={{ stroke: "#27272a" }} tickLine={false} />
            <YAxis tick={{ fill: "#a1a1aa", fontSize: 12 }} axisLine={false} tickLine={false} width={54} />
            <Tooltip content={<LatencyTooltip />} cursor={{ fill: "rgba(39, 39, 42, 0.45)" }} />
            <Bar dataKey="p50" name="p50" fill="#10b981" radius={[4, 4, 0, 0]} isAnimationActive animationDuration={700}>
              <LabelList dataKey="p50" position="top" formatter={formatLabel} fill="#a1a1aa" fontSize={11} />
            </Bar>
            <Bar dataKey="p95" name="p95" fill="#f59e0b" radius={[4, 4, 0, 0]} isAnimationActive animationDuration={800}>
              <LabelList dataKey="p95" position="top" formatter={formatLabel} fill="#a1a1aa" fontSize={11} />
            </Bar>
            <Bar dataKey="p99" name="p99" fill="#ef4444" radius={[4, 4, 0, 0]} isAnimationActive animationDuration={900}>
              <LabelList dataKey="p99" position="top" formatter={formatLabel} fill="#a1a1aa" fontSize={11} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

interface NormalizedLatency {
  strategy: string;
  p50: number;
  p95: number;
  p99: number;
}

function normalizeLatencyData(data: StrategyLatency[]): NormalizedLatency[] {
  const byStrategy = new Map(data.map((item) => [item.strategy, item]));
  return strategyOrder.map((strategy) => {
    const item = byStrategy.get(strategy);
    const p50 = item?.p50 ?? 0;
    const p95 = item?.p95 ?? 0;
    return {
      strategy,
      p50,
      p95,
      p99: item?.p99 ?? Math.round(p95 * 1.18),
    };
  });
}

function LatencyTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length) {
    return null;
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 shadow-xl shadow-black/30">
      <p className="mb-2 text-xs font-medium text-zinc-50">{label}</p>
      <div className="space-y-1">
        {payload.map((item) => (
          <div key={item.dataKey} className="flex min-w-28 items-center justify-between gap-4 text-xs">
            <span style={{ color: item.color }}>{item.name}</span>
            <span className="font-mono text-zinc-50">{formatLabel(item.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

interface TooltipItem {
  dataKey?: string | number;
  name?: string;
  value?: unknown;
  color?: string;
}

interface TooltipProps {
  active?: boolean;
  payload?: TooltipItem[];
  label?: string | number;
}

function formatLabel(value: unknown): string {
  const numeric = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(numeric)) {
    return "--";
  }
  return `${Math.round(numeric)}ms`;
}

