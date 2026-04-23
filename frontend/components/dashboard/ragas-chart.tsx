"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Skeleton } from "@/components/ui/skeleton";
import type { DailyScore } from "@/types";

interface RagasChartProps {
  data: DailyScore[];
  loading?: boolean;
}

const lines = [
  { key: "faithfulness", label: "Faithfulness", color: "#6366f1" },
  { key: "answer_relevancy", label: "Answer Relevancy", color: "#10b981" },
  { key: "context_precision", label: "Context Precision", color: "#f59e0b" },
] as const;

export function RagasChart({ data, loading = false }: RagasChartProps) {
  if (loading) {
    return (
      <section className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
        <Skeleton className="h-5 w-36" />
        <Skeleton className="mt-5 h-[320px] w-full" />
      </section>
    );
  }

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium text-zinc-50">RAGAS scores</h2>
          <p className="mt-1 text-xs text-zinc-400">Evaluation quality over the last 14 days</p>
        </div>
      </div>
      <div className="h-[320px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 12, right: 16, left: -18, bottom: 10 }}>
            <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="date"
              tickFormatter={formatDate}
              tick={{ fill: "#a1a1aa", fontSize: 12 }}
              axisLine={{ stroke: "#27272a" }}
              tickLine={false}
            />
            <YAxis
              domain={[0, 1]}
              ticks={[0.2, 0.4, 0.6, 0.8, 1.0]}
              tick={{ fill: "#a1a1aa", fontSize: 12 }}
              axisLine={false}
              tickLine={false}
              width={42}
            />
            <Tooltip content={<RagasTooltip />} />
            <ReferenceLine y={0.8} stroke="#71717a" strokeDasharray="4 4" label={{ value: "target", fill: "#71717a", fontSize: 12 }} />
            {lines.map((line) => (
              <Line
                key={line.key}
                type="monotone"
                dataKey={line.key}
                name={line.label}
                stroke={line.color}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, stroke: line.color, strokeWidth: 2, fill: "#18181b" }}
                isAnimationActive
                animationDuration={900}
              />
            ))}
            <Legend content={<RagasLegend />} verticalAlign="bottom" height={32} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function RagasTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length) {
    return null;
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 shadow-xl shadow-black/30">
      <p className="mb-2 text-xs text-zinc-400">{formatDate(String(label))}</p>
      <div className="space-y-1">
        {payload.map((item) => (
          <div key={item.dataKey} className="flex min-w-44 items-center justify-between gap-4 text-xs">
            <span style={{ color: item.color }}>{item.name}</span>
            <span className="font-mono text-zinc-50">{formatScore(item.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function RagasLegend() {
  return (
    <div className="mt-3 flex flex-wrap justify-center gap-5">
      {lines.map((line) => (
        <div key={line.key} className="flex items-center gap-2 text-xs text-zinc-400">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: line.color }} />
          {line.label}
        </div>
      ))}
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

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function formatScore(value: unknown): string {
  const numeric = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(numeric)) {
    return "--";
  }
  return numeric.toFixed(3);
}

