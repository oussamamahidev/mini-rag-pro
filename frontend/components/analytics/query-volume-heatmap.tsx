"use client";

export interface QueryVolumeCell {
  date: string;
  query_count: number;
}

interface QueryVolumeHeatmapProps {
  cells: QueryVolumeCell[];
  loading?: boolean;
}

export function QueryVolumeHeatmap({ cells, loading = false }: QueryVolumeHeatmapProps) {
  const maxCount = Math.max(1, ...cells.map((cell) => cell.query_count));

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
      <div className="mb-5">
        <h2 className="text-base font-semibold text-zinc-50">Query volume</h2>
        <p className="mt-1 text-sm text-zinc-500">Daily activity across the last 13 weeks</p>
      </div>

      {loading ? (
        <div className="h-32 animate-pulse rounded-lg bg-zinc-800/70" />
      ) : (
        <div className="overflow-x-auto">
          <div className="grid w-max grid-flow-col grid-rows-7 gap-1">
            {cells.map((cell) => (
              <div
                key={cell.date}
                title={`${formatDate(cell.date)} · ${cell.query_count.toLocaleString()} queries`}
                className={`h-4 w-4 rounded-[3px] ${cellColor(cell.query_count, maxCount)}`}
                aria-label={`${formatDate(cell.date)} ${cell.query_count} queries`}
              />
            ))}
          </div>
          <div className="mt-4 flex items-center gap-2 text-xs text-zinc-500">
            <span>Less</span>
            <span className="h-3 w-3 rounded-[3px] bg-zinc-800" />
            <span className="h-3 w-3 rounded-[3px] bg-indigo-200" />
            <span className="h-3 w-3 rounded-[3px] bg-indigo-400" />
            <span className="h-3 w-3 rounded-[3px] bg-indigo-600" />
            <span className="h-3 w-3 rounded-[3px] bg-indigo-800" />
            <span>More</span>
          </div>
        </div>
      )}
    </section>
  );
}

function cellColor(count: number, maxCount: number): string {
  if (count <= 0) {
    return "bg-zinc-800";
  }
  const ratio = count / maxCount;
  if (ratio < 0.25) {
    return "bg-indigo-200";
  }
  if (ratio < 0.5) {
    return "bg-indigo-400";
  }
  if (ratio < 0.75) {
    return "bg-indigo-600";
  }
  return "bg-indigo-800";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(new Date(`${value}T00:00:00Z`));
}
