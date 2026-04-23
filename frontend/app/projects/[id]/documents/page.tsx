"use client";

import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";

import { BulkActionsBar } from "@/components/documents/bulk-actions-bar";
import { DocTable } from "@/components/documents/doc-table";
import { UploadZone } from "@/components/documents/upload-zone";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Document, PaginatedResponse } from "@/types";

export default function DocumentsPage({ params }: { params: { id: string } }) {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <DocumentsContent projectId={params.id} />
    </QueryClientProvider>
  );
}

function DocumentsContent({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const documentsQueryKey = useMemo(() => ["documents", projectId], [projectId]);
  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.projects.get(projectId),
    retry: 1,
  });
  const documentsQuery = useQuery({
    queryKey: documentsQueryKey,
    queryFn: () => api.documents.list(projectId),
    refetchInterval: (query) => {
      const data = query.state.data as PaginatedResponse<Document> | undefined;
      const hasNonTerminalDocs = data?.items.some((document) =>
        ["queued", "processing", "indexing"].includes(document.status),
      );
      return hasNonTerminalDocs ? 2000 : false;
    },
    retry: 1,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.documents.delete(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: documentsQueryKey });
    },
  });

  const reindexMutation = useMutation({
    mutationFn: (id: string) => api.documents.reindex(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: documentsQueryKey });
    },
  });

  const documents = documentsQuery.data?.items ?? [];

  function refreshDocuments() {
    void documentsQuery.refetch();
  }

  function toggleSelected(id: string) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  function toggleAll() {
    setSelectedIds((current) => {
      if (documents.length > 0 && documents.every((document) => current.has(document.id))) {
        return new Set();
      }
      return new Set(documents.map((document) => document.id));
    });
  }

  function removeDeleted(id: string) {
    queryClient.setQueryData<PaginatedResponse<Document>>(documentsQueryKey, (current) => {
      if (!current) {
        return current;
      }
      return {
        ...current,
        items: current.items.filter((document) => document.id !== id),
        total: Math.max(0, current.total - 1),
      };
    });
    setSelectedIds((current) => {
      const next = new Set(current);
      next.delete(id);
      return next;
    });
  }

  function deleteDocument(id: string) {
    removeDeleted(id);
    deleteMutation.mutate(id);
  }

  function reindexDocument(id: string) {
    reindexMutation.mutate(id);
  }

  function deleteSelected() {
    for (const id of Array.from(selectedIds)) {
      deleteDocument(id);
    }
  }

  function reindexSelected() {
    for (const id of Array.from(selectedIds)) {
      reindexDocument(id);
    }
  }

  return (
    <section className="mx-auto flex w-full max-w-7xl flex-col gap-6">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-normal text-zinc-50">Documents</h1>
          <p className="mt-1 text-sm text-zinc-400">
            {projectQuery.data?.name ?? "Project"} · {documents.length.toLocaleString()} files indexed for retrieval
          </p>
        </div>
        <button
          type="button"
          onClick={refreshDocuments}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-zinc-800 bg-zinc-900 px-4 text-sm text-zinc-200 transition hover:bg-zinc-800"
        >
          <RefreshCw className={`h-4 w-4 ${documentsQuery.isFetching ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </header>

      {documentsQuery.isError ? (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-100">
          Documents unavailable. Check your API key and backend connection, then retry.
        </div>
      ) : null}

      <UploadZone projectId={projectId} onUploadComplete={() => void queryClient.invalidateQueries({ queryKey: documentsQueryKey })} />

      {documentsQuery.isLoading ? (
        <DocumentsSkeleton />
      ) : (
        <DocTable
          documents={documents}
          selectedIds={selectedIds}
          onToggleSelected={toggleSelected}
          onToggleAll={toggleAll}
          onDelete={deleteDocument}
          onReindex={reindexDocument}
        />
      )}

      <BulkActionsBar selectedCount={selectedIds.size} onDeleteSelected={deleteSelected} onReindexSelected={reindexSelected} />
    </section>
  );
}

function DocumentsSkeleton() {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
      <div className="space-y-3">
        {Array.from({ length: 6 }).map((_, index) => (
          <Skeleton key={index} className="h-12 w-full" />
        ))}
      </div>
    </div>
  );
}
