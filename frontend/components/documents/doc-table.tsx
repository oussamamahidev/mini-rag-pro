"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Check, FileText, Loader2, RefreshCw, Trash2, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { Document, DocumentStatus, DocumentStatusUpdate, FileType } from "@/types";

interface DocTableProps {
  documents: Document[];
  selectedIds: Set<string>;
  onToggleSelected: (id: string) => void;
  onToggleAll: () => void;
  onDelete: (id: string) => void;
  onReindex: (id: string) => void;
}

export function DocTable({ documents, selectedIds, onToggleSelected, onToggleAll, onDelete, onReindex }: DocTableProps) {
  if (documents.length === 0) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900 px-5 py-12 text-center text-sm text-zinc-400">
        No documents uploaded yet.
      </div>
    );
  }

  const allSelected = documents.length > 0 && documents.every((document) => selectedIds.has(document.id));

  return (
    <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] text-left text-sm">
          <thead className="border-b border-zinc-800 text-xs text-zinc-500">
            <tr>
              <th className="w-10 px-4 py-3">
                <input type="checkbox" checked={allSelected} onChange={onToggleAll} className="h-4 w-4 accent-indigo-500" />
              </th>
              <th className="w-16 px-4 py-3 font-medium">Type</th>
              <th className="px-4 py-3 font-medium">Filename</th>
              <th className="px-4 py-3 font-medium">Size</th>
              <th className="px-4 py-3 font-medium">Chunks</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Uploaded</th>
              <th className="w-24 px-4 py-3 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((document) => (
              <DocumentRow
                key={document.id}
                document={document}
                selected={selectedIds.has(document.id)}
                onToggleSelected={onToggleSelected}
                onDelete={onDelete}
                onReindex={onReindex}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DocumentRow({
  document,
  selected,
  onToggleSelected,
  onDelete,
  onReindex,
}: {
  document: Document;
  selected: boolean;
  onToggleSelected: (id: string) => void;
  onDelete: (id: string) => void;
  onReindex: (id: string) => void;
}) {
  const statusQuery = useQuery({
    queryKey: ["document-status", document.id],
    queryFn: () => api.documents.getStatus(document.id),
    enabled: !isTerminal(document.status),
    refetchInterval: (query) => {
      const data = query.state.data as DocumentStatusUpdate | undefined;
      return isTerminal(data?.status ?? document.status) ? false : 2000;
    },
  });

  const current = mergeStatus(document, statusQuery.data);

  return (
    <tr className="group border-b border-zinc-800/70 bg-zinc-950 transition hover:bg-zinc-800/70">
      <td className="px-4 py-3">
        <input type="checkbox" checked={selected} onChange={() => onToggleSelected(document.id)} className="h-4 w-4 accent-indigo-500" />
      </td>
      <td className="px-4 py-3">
        <TypeIcon type={document.file_type} />
      </td>
      <td className="max-w-[360px] px-4 py-3">
        <p className="truncate text-zinc-100">{document.original_filename}</p>
      </td>
      <td className="px-4 py-3 font-mono text-xs text-zinc-400">{formatBytes(document.file_size_bytes)}</td>
      <td className="px-4 py-3 font-mono text-xs text-zinc-300">{current.chunk_count ?? "—"}</td>
      <td className="px-4 py-3">
        <StatusPill document={current} />
      </td>
      <td className="px-4 py-3 text-xs text-zinc-400">{formatDate(document.created_at)}</td>
      <td className="px-4 py-3">
        <div className="flex opacity-0 transition group-hover:opacity-100">
          <button
            type="button"
            aria-label="Re-index document"
            onClick={() => onReindex(document.id)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-zinc-400 transition hover:bg-zinc-700 hover:text-zinc-50"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
          <button
            type="button"
            aria-label="Delete document"
            onClick={() => onDelete(document.id)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-zinc-400 transition hover:bg-red-500/10 hover:text-red-400"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </td>
    </tr>
  );
}

function TypeIcon({ type }: { type: FileType }) {
  const color: Record<FileType, string> = {
    pdf: "text-red-400",
    txt: "text-zinc-400",
    docx: "text-blue-400",
    md: "text-orange-400",
  };
  return <FileText className={`h-5 w-5 ${color[type]}`} />;
}

function StatusPill({ document }: { document: Document }) {
  const progress = document.indexing_progress ?? 0;

  return (
    <AnimatePresence mode="wait">
      <motion.span
        key={`${document.status}-${progress}`}
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        className={statusClass(document.status)}
        title={document.status === "error" ? document.error_message ?? "Document processing failed" : undefined}
      >
        {document.status === "processing" ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
        {document.status === "ready" ? <Check className="h-3 w-3" /> : null}
        {document.status === "error" ? <X className="h-3 w-3" /> : null}
        {document.status === "indexing" ? (
          <>
            <span className="absolute inset-x-0 bottom-0 h-0.5 bg-indigo-950">
              <span className="block h-full bg-indigo-400" style={{ width: `${progress}%` }} />
            </span>
            indexing {progress}%
          </>
        ) : (
          document.status
        )}
      </motion.span>
    </AnimatePresence>
  );
}

function statusClass(status: DocumentStatus): string {
  const base = "relative inline-flex min-w-24 items-center justify-center gap-1.5 overflow-hidden rounded-full border px-2.5 py-1 text-xs";
  const styles: Record<DocumentStatus, string> = {
    queued: "border-zinc-700 bg-zinc-800 text-zinc-300",
    processing: "border-amber-500/40 bg-amber-500/15 text-amber-300",
    indexing: "border-indigo-500/40 bg-indigo-500/15 text-indigo-300",
    ready: "border-emerald-500/40 bg-emerald-500/15 text-emerald-300",
    error: "border-red-500/40 bg-red-500/15 text-red-300",
  };
  return `${base} ${styles[status]}`;
}

function mergeStatus(document: Document, status?: DocumentStatusUpdate): Document {
  if (!status) {
    return document;
  }
  return {
    ...document,
    status: status.status,
    indexing_progress: status.indexing_progress ?? document.indexing_progress,
    error_message: status.error_message ?? document.error_message,
    chunk_count: status.chunk_count ?? document.chunk_count,
  };
}

function isTerminal(status: DocumentStatus): boolean {
  return status === "ready" || status === "error";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 0; index < units.length; index += 1) {
    unit = units[index];
    if (value < 1024 || index === units.length - 1) {
      break;
    }
    value /= 1024;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${unit}`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

