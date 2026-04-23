"use client";

import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw } from "lucide-react";
import { useState } from "react";

import { CreateProjectDialog, type CreateProjectInput } from "@/components/projects/create-project-dialog";
import { EmptyProjects } from "@/components/projects/empty-projects";
import { ProjectCard } from "@/components/projects/project-card";
import { ProjectGridSkeleton } from "@/components/projects/project-grid-skeleton";
import { api } from "@/lib/api";
import type { Project } from "@/types";

const projectsQueryKey = ["projects"];

export default function ProjectsPage() {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <ProjectsContent />
    </QueryClientProvider>
  );
}

function ProjectsContent() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const projectsQuery = useQuery({
    queryKey: projectsQueryKey,
    queryFn: api.projects.list,
    retry: 1,
  });

  const createMutation = useMutation({
    mutationFn: (input: CreateProjectInput) => api.projects.create(input),
    onSuccess: (project) => {
      queryClient.setQueryData<Project[]>(projectsQueryKey, (current = []) => [project, ...current]);
      setCreateOpen(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (project: Project) => api.projects.delete(project.id),
    onMutate: async (project) => {
      await queryClient.cancelQueries({ queryKey: projectsQueryKey });
      const previousProjects = queryClient.getQueryData<Project[]>(projectsQueryKey);
      queryClient.setQueryData<Project[]>(projectsQueryKey, (current = []) => current.filter((item) => item.id !== project.id));
      return { previousProjects };
    },
    onError: (_error, _project, context) => {
      if (context?.previousProjects) {
        queryClient.setQueryData(projectsQueryKey, context.previousProjects);
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: projectsQueryKey });
    },
  });

  const projects = projectsQuery.data ?? [];

  async function createProject(input: CreateProjectInput) {
    await createMutation.mutateAsync(input);
  }

  return (
    <section className="mx-auto flex w-full max-w-7xl flex-col gap-6">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-normal text-zinc-50">Projects</h1>
          <p className="mt-1 text-sm text-zinc-400">Manage document collections, retrieval strategy, and chat workspaces.</p>
        </div>
        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => void projectsQuery.refetch()}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-zinc-800 bg-zinc-900 px-4 text-sm text-zinc-200 transition hover:bg-zinc-800"
          >
            <RefreshCw className={`h-4 w-4 ${projectsQuery.isFetching ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-indigo-500 px-4 text-sm font-medium text-white transition hover:bg-indigo-400"
          >
            <Plus className="h-4 w-4" />
            New project
          </button>
        </div>
      </header>

      {projectsQuery.isError ? (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium text-red-300">Projects unavailable</p>
              <p className="mt-1 text-sm text-red-100/70">Check your API key and backend connection, then retry.</p>
            </div>
            <button
              type="button"
              onClick={() => void projectsQuery.refetch()}
              className="inline-flex h-9 items-center justify-center rounded-md border border-red-500/30 px-3 text-sm text-red-200 transition hover:bg-red-500/10"
            >
              Retry
            </button>
          </div>
        </div>
      ) : null}

      {deleteMutation.isError ? (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">
          Delete failed. The project was restored locally.
        </div>
      ) : null}

      {projectsQuery.isLoading ? (
        <ProjectGridSkeleton />
      ) : projects.length === 0 ? (
        <EmptyProjects onCreate={() => setCreateOpen(true)} />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {projects.map((project) => (
            <ProjectCard key={project.id} project={project} onDelete={(item) => deleteMutation.mutate(item)} />
          ))}
        </div>
      )}

      <CreateProjectDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        creating={createMutation.isPending}
        onCreate={createProject}
      />
    </section>
  );
}
