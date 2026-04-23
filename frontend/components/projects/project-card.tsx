"use client";

import { MoreHorizontal } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAppStore } from "@/lib/store";
import type { Project, ProjectStatus, RetrievalStrategy } from "@/types";

interface ProjectCardProps {
  project: Project;
  onDelete: (project: Project) => void;
}

const strategyStyles: Record<RetrievalStrategy, string> = {
  vanilla: "border-zinc-700 bg-zinc-800 text-zinc-200",
  hybrid: "border-indigo-500/40 bg-indigo-500/15 text-indigo-300",
  rerank: "border-purple-500/40 bg-purple-500/15 text-purple-300",
  hyde: "border-teal-500/40 bg-teal-500/15 text-teal-300",
};

const statusStyles: Record<ProjectStatus, { dot: string; text: string; pulse?: boolean }> = {
  active: { dot: "bg-emerald-500", text: "text-emerald-400" },
  indexing: { dot: "bg-amber-500", text: "text-amber-400", pulse: true },
  error: { dot: "bg-red-500", text: "text-red-400" },
};

export function ProjectCard({ project, onDelete }: ProjectCardProps) {
  const router = useRouter();
  const setCurrentProjectId = useAppStore((state) => state.setCurrentProjectId);
  const [menuOpen, setMenuOpen] = useState(false);
  const status = statusStyles[project.status];

  function openProject() {
    setCurrentProjectId(project.id);
    router.push(`/projects/${project.id}/chat`);
  }

  return (
    <article className="group relative flex min-h-[240px] flex-col rounded-xl border border-zinc-800 bg-zinc-900 p-5 transition hover:border-2 hover:border-indigo-500 hover:bg-zinc-800">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-base font-medium text-zinc-50">{project.name}</h2>
          <div className={`mt-2 flex items-center gap-2 text-xs ${status.text}`}>
            <span className={`h-2 w-2 rounded-full ${status.dot} ${status.pulse ? "animate-pulse" : ""}`} />
            <span>{project.status}</span>
          </div>
        </div>

        <div className="relative">
          <button
            type="button"
            aria-label="Project menu"
            onClick={() => setMenuOpen((open) => !open)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-zinc-500 transition hover:bg-zinc-700 hover:text-zinc-50"
          >
            <MoreHorizontal className="h-4 w-4" />
          </button>
          {menuOpen ? (
            <div className="absolute right-0 top-10 z-20 w-44 overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950 py-1 shadow-xl shadow-black/30">
              <MenuButton label="Rename" onClick={() => setMenuOpen(false)} />
              <MenuButton label="Change Strategy" onClick={() => setMenuOpen(false)} />
              <MenuButton
                label="Delete"
                destructive
                onClick={() => {
                  setMenuOpen(false);
                  onDelete(project);
                }}
              />
            </div>
          ) : null}
        </div>
      </div>

      <p className="mt-4 line-clamp-2 min-h-10 text-sm leading-5 text-zinc-400">
        {project.description || "No description provided."}
      </p>

      <div className="mt-4 flex items-center justify-between gap-3">
        <span className={`rounded-full border px-2 py-1 font-mono text-xs ${strategyStyles[project.retrieval_strategy]}`}>
          {project.retrieval_strategy}
        </span>
        <span className="text-xs text-zinc-400">{createdAgo(project.created_at)}</span>
      </div>

      <p className="mt-4 text-xs text-zinc-500">
        {project.document_count.toLocaleString()} docs · {project.chunk_count.toLocaleString()} chunks ·{" "}
        {project.query_count.toLocaleString()} queries
      </p>

      <div className="mt-auto pt-5">
        <button
          type="button"
          onClick={openProject}
          className="inline-flex h-9 w-full items-center justify-center rounded-md bg-indigo-500 px-3 text-sm font-medium text-white transition hover:bg-indigo-400"
        >
          Open
        </button>
      </div>
    </article>
  );
}

function MenuButton({
  label,
  onClick,
  destructive = false,
}: {
  label: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`block w-full px-3 py-2 text-left text-sm transition hover:bg-zinc-800 ${
        destructive ? "text-red-400" : "text-zinc-300"
      }`}
    >
      {label}
    </button>
  );
}

function createdAgo(value: string): string {
  const created = new Date(value).getTime();
  if (Number.isNaN(created)) {
    return "recently";
  }
  const days = Math.max(0, Math.floor((Date.now() - created) / 86400000));
  if (days === 0) {
    return "today";
  }
  if (days === 1) {
    return "1 day ago";
  }
  return `${days} days ago`;
}

