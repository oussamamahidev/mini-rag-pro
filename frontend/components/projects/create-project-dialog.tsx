"use client";

import { useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { RetrievalStrategy } from "@/types";

export interface CreateProjectInput {
  name: string;
  description?: string;
  retrieval_strategy: RetrievalStrategy;
}

interface CreateProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (input: CreateProjectInput) => Promise<void>;
  creating?: boolean;
}

const strategies: Array<{
  value: RetrievalStrategy;
  label: string;
  description: string;
  latency: string;
}> = [
  { value: "vanilla", label: "Vanilla", description: "Fast, basic semantic search", latency: "~280ms" },
  { value: "hybrid", label: "Hybrid", description: "BM25 + semantic, recommended", latency: "~420ms" },
  { value: "rerank", label: "Rerank", description: "Highest quality, slower", latency: "~600ms" },
  { value: "hyde", label: "HyDE", description: "Good for abstract questions", latency: "~700ms" },
];

export function CreateProjectDialog({ open, onOpenChange, onCreate, creating = false }: CreateProjectDialogProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [strategy, setStrategy] = useState<RetrievalStrategy>("hybrid");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      return;
    }

    await onCreate({
      name: trimmedName,
      description: description.trim() || undefined,
      retrieval_strategy: strategy,
    });

    setName("");
    setDescription("");
    setStrategy("hybrid");
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create project</DialogTitle>
          <DialogDescription>Choose a retrieval strategy and create a workspace for documents and chat.</DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="mt-6 space-y-5">
          <label className="block">
            <span className="text-sm font-medium text-zinc-200">Name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              className="mt-2 h-10 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 text-sm text-zinc-50 outline-none transition placeholder:text-zinc-600 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
              placeholder="Engineering knowledge base"
            />
          </label>

          <label className="block">
            <span className="text-sm font-medium text-zinc-200">Description</span>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={3}
              className="mt-2 w-full resize-none rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-50 outline-none transition placeholder:text-zinc-600 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
              placeholder="Internal documents, specs, and runbooks."
            />
          </label>

          <fieldset>
            <legend className="text-sm font-medium text-zinc-200">Retrieval strategy</legend>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {strategies.map((item) => {
                const active = strategy === item.value;
                return (
                  <label
                    key={item.value}
                    className={`cursor-pointer rounded-lg border p-4 transition ${
                      active ? "border-indigo-500 bg-indigo-500/10" : "border-zinc-800 bg-zinc-900 hover:bg-zinc-800"
                    }`}
                  >
                    <input
                      type="radio"
                      name="strategy"
                      value={item.value}
                      checked={active}
                      onChange={() => setStrategy(item.value)}
                      className="sr-only"
                    />
                    <span className="flex items-center justify-between gap-3">
                      <span className="text-sm font-medium text-zinc-50">{item.label}</span>
                      <span className="font-mono text-xs text-zinc-400">{item.latency}</span>
                    </span>
                    <span className="mt-2 block text-sm text-zinc-400">{item.description}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <DialogFooter>
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="inline-flex h-10 items-center justify-center rounded-md border border-zinc-800 px-4 text-sm text-zinc-300 transition hover:bg-zinc-800"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={creating || !name.trim()}
              className="inline-flex h-10 items-center justify-center rounded-md bg-indigo-500 px-4 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {creating ? "Creating..." : "Create project"}
            </button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

