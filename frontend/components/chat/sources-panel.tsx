"use client";

import { motion } from "framer-motion";
import { useState } from "react";

import type { Source } from "@/types";

interface SourcesPanelProps {
  sources: Source[];
}

export function SourcesPanel({ sources }: SourcesPanelProps) {
  return (
    <aside className="min-h-[360px] rounded-xl border border-zinc-800 bg-zinc-950 p-4 lg:h-[calc(100vh-8rem)] lg:overflow-y-auto">
      <h2 className="text-sm font-medium text-zinc-400">Sources ({sources.length})</h2>
      {sources.length === 0 ? (
        <div className="flex h-full min-h-72 items-center justify-center text-center text-sm text-zinc-500">
          Ask a question to see which document chunks were used
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          {sources.map((source, index) => (
            <SourceCard key={`${source.chunk_id}-${index}`} source={source} index={index} />
          ))}
        </div>
      )}
    </aside>
  );
}

function SourceCard({ source, index }: { source: Source; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const score = Math.round(source.score * 100);

  return (
    <motion.button
      type="button"
      onClick={() => setExpanded((value) => !value)}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.06 }}
      className="w-full rounded-lg border border-zinc-800 border-l-indigo-500 bg-zinc-900 p-3 text-left transition hover:bg-zinc-800"
      style={{ borderLeftWidth: 2 }}
    >
      <div className="flex min-w-0 items-center justify-between gap-2">
        <p className="truncate text-[13px] font-medium text-zinc-50">{source.document_name}</p>
        {source.page_number ? (
          <span className="shrink-0 rounded-full border border-zinc-700 px-2 py-0.5 font-mono text-[11px] text-zinc-400">
            p. {source.page_number}
          </span>
        ) : null}
      </div>
      <div className="mt-3">
        <div className="mb-1 flex items-center justify-between text-[11px] text-zinc-500">
          <span>Relevance</span>
          <span className="font-mono">{score}%</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-zinc-800">
          <div className="h-full rounded-full bg-indigo-500" style={{ width: `${score}%` }} />
        </div>
      </div>
      <p className={`mt-3 text-xs leading-5 text-zinc-400 ${expanded ? "" : "line-clamp-3"}`}>{source.text}</p>
    </motion.button>
  );
}

