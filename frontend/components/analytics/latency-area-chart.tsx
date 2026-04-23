"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipProps,
} from "recharts";

export interface LatencyPoint {
  date: string;
  p50: number;
  p95: number;
  p99: number;
}

interface LatencyAreaChartProps {
  data: LatencyPoint[];
  loading?: boolean;
}

export function LatencyAreaChart({ data, loading = false }: LatencyAreaChartProps) {
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-zinc-50">Latency percentiles</h2>
          <p className="mt-1 text-sm text-zinc-500">p50, p95, and p99 response latency over time</p>
        </div>
      </div>

      {loading ? (
        <div className="h-80 animate-pulse rounded-lg bg-zinc-800/70" />
      ) : (
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="p50Latency" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="5%" stopColor="#6366f1" stopOpacity={0.36} />
                  <stop offset="95%" stopColor="#6366f1" stopOpacity={0.04} />
                </linearGradient>
                <linearGradient id="p95Latency" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.28} />
                  <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.03} />
                </linearGradient>
                <linearGradient id="p99Latency" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="5%" stopColor="#ef4444" stopOpacity={0.22} />
                  <stop offset="95%" stopColor="#ef4444" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="date"
                stroke="#71717a"
                tickLine={false}
                axisLine={false}
                tickFormatter={formatShortDate}
                minTickGap={24}
                fontSize={12}
              />
              <YAxis
                stroke="#71717a"
                tickLine={false}
                axisLine={false}
                tickFormatter={(value) => `${value}ms`}
                fontSize={12}
                width={54}
              />
              <Tooltip content={<LatencyTooltip />} cursor={{ stroke: "#52525b", strokeDasharray: "3 3" }} />
              <Area
                type="monotone"
                dataKey="p99"
                stackId="latency"
                stroke="#ef4444"
                strokeWidth={1.5}
                fill="url(#p99Latency)"
                name="p99"
              />
              <Area
                type="monotone"
                dataKey="p95"
                stackId="latency"
                stroke="#f59e0b"
                strokeWidth={1.5}
                fill="url(#p95Latency)"
                name="p95"
              />
              <Area
                type="monotone"
                dataKey="p50"
                stackId="latency"
                stroke="#6366f1"
                strokeWidth={1.5}
                fill="url(#p50Latency)"
                name="p50"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}

function LatencyTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) {
    return null;
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-3 shadow-xl shadow-black/30">
      <p className="mb-2 text-xs text-zinc-500">{formatLongDate(String(label))}</p>
      {payload
        .slice()
        .reverse()
        .map((item) => (
          <div key={item.dataKey} className="flex min-w-32 items-center justify-between gap-4 text-xs">
            <span style={{ color: item.color }}>{item.name}</span>
            <span className="font-mono text-zinc-200">{Math.round(Number(item.value ?? 0)).toLocaleString()}ms</span>
          </div>
        ))}
    </div>
  );
}

function formatShortDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(new Date(`${value}T00:00:00Z`));
}

function formatLongDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(
    new Date(`${value}T00:00:00Z`),
  );
}
