"use client";

import { motion, useMotionValue, useSpring, useTransform } from "framer-motion";
import { useEffect } from "react";

import { Skeleton } from "@/components/ui/skeleton";

type Threshold = "faithfulness" | "latency";
type ValueFormat = "number" | "percentage" | "milliseconds";

interface MetricCardProps {
  label: string;
  value: number | null;
  trend?: number;
  format?: ValueFormat;
  threshold?: Threshold;
  loading?: boolean;
}

export function MetricCard({
  label,
  value,
  trend,
  format = "number",
  threshold,
  loading = false,
}: MetricCardProps) {
  const rawValue = value ?? 0;
  const motionValue = useMotionValue(0);
  const springValue = useSpring(motionValue, { stiffness: 80, damping: 18, mass: 0.5 });
  const displayValue = useTransform(springValue, (latest) => formatValue(latest, format));

  useEffect(() => {
    motionValue.set(rawValue);
  }, [motionValue, rawValue]);

  if (loading) {
    return (
      <div className="rounded-xl border-[0.5px] border-zinc-800 bg-zinc-900 p-5">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="mt-4 h-8 w-32" />
        <Skeleton className="mt-3 h-3 w-20" />
      </div>
    );
  }

  return (
    <div className="rounded-xl border-[0.5px] border-zinc-800 bg-zinc-900 p-5 shadow-sm shadow-black/10">
      <p className="text-xs text-zinc-400">{label}</p>
      <motion.p className={`mt-3 font-mono text-[28px] font-medium leading-none ${valueColor(rawValue, threshold)}`}>
        {displayValue}
      </motion.p>
      <p className={`mt-3 text-xs ${trendColor(trend)}`}>{trend === undefined ? "No prior data" : formatTrend(trend)}</p>
    </div>
  );
}

function formatValue(value: number, format: ValueFormat): string {
  if (format === "percentage") {
    return `${value.toFixed(1)}%`;
  }
  if (format === "milliseconds") {
    return `${Math.round(value).toLocaleString()}ms`;
  }
  return Math.round(value).toLocaleString();
}

function formatTrend(value: number): string {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(1)}% vs last week`;
}

function trendColor(value?: number): string {
  if (value === undefined || value === 0) {
    return "text-zinc-500";
  }
  return value > 0 ? "text-emerald-500" : "text-red-500";
}

function valueColor(value: number, threshold?: Threshold): string {
  if (threshold === "faithfulness") {
    if (value > 80) {
      return "text-emerald-500";
    }
    if (value >= 60) {
      return "text-amber-500";
    }
    return "text-red-500";
  }

  if (threshold === "latency") {
    if (value < 500) {
      return "text-emerald-500";
    }
    if (value <= 1000) {
      return "text-amber-500";
    }
    return "text-red-500";
  }

  return "text-zinc-50";
}
