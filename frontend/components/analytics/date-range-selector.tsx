"use client";

export type AnalyticsRange = 7 | 30 | 90;

interface DateRangeSelectorProps {
  value: AnalyticsRange;
  onChange: (value: AnalyticsRange) => void;
}

const ranges: Array<{ value: AnalyticsRange; label: string }> = [
  { value: 7, label: "Last 7 days" },
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
];

export function DateRangeSelector({ value, onChange }: DateRangeSelectorProps) {
  return (
    <div className="inline-flex rounded-full border border-zinc-800 bg-zinc-900 p-1">
      {ranges.map((range) => (
        <button
          key={range.value}
          type="button"
          onClick={() => onChange(range.value)}
          className={`h-8 rounded-full px-3 text-sm transition ${
            value === range.value ? "bg-indigo-500 text-white" : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
          }`}
        >
          {range.label}
        </button>
      ))}
    </div>
  );
}
