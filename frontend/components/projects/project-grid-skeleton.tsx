import { Skeleton } from "@/components/ui/skeleton";

export function ProjectGridSkeleton() {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, index) => (
        <div key={index} className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
          <div className="flex items-center justify-between">
            <Skeleton className="h-5 w-36" />
            <Skeleton className="h-8 w-8" />
          </div>
          <Skeleton className="mt-5 h-4 w-full" />
          <Skeleton className="mt-2 h-4 w-4/5" />
          <div className="mt-5 flex items-center justify-between">
            <Skeleton className="h-6 w-20 rounded-full" />
            <Skeleton className="h-3 w-20" />
          </div>
          <Skeleton className="mt-5 h-3 w-44" />
          <Skeleton className="mt-8 h-9 w-full" />
        </div>
      ))}
    </div>
  );
}

