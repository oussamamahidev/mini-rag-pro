"use client";

import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { RetrievalStrategy } from "@/types";

interface InputBarProps {
  strategy: RetrievalStrategy;
  onStrategyChange: (strategy: RetrievalStrategy) => void;
  onSubmit: (text: string) => void;
  onStop: () => void;
  isStreaming: boolean;
  documentCount: number;
}

const strategies: RetrievalStrategy[] = ["vanilla", "hybrid", "rerank", "hyde"];

export function InputBar({ strategy, onStrategyChange, onSubmit, onStop, isStreaming, documentCount }: InputBarProps) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
  }, [text]);

  function submit() {
    const trimmed = text.trim();
    if (!trimmed || isStreaming) {
      return;
    }
    onSubmit(trimmed);
    setText("");
  }

  return (
    <div className="border-t border-zinc-800 bg-zinc-950 p-4">
      <div className="mx-auto max-w-4xl rounded-xl border border-zinc-800 bg-zinc-900 p-3">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              submit();
            }
          }}
          rows={1}
          placeholder="Ask about this project's documents..."
          className="max-h-40 min-h-10 w-full resize-none bg-transparent text-sm leading-6 text-zinc-50 outline-none placeholder:text-zinc-600"
        />
        <div className="mt-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <select
              value={strategy}
              onChange={(event) => onStrategyChange(event.target.value as RetrievalStrategy)}
              className="h-9 rounded-md border border-zinc-800 bg-zinc-950 px-2 font-mono text-xs text-zinc-300 outline-none focus:border-indigo-500"
            >
              {strategies.map((item) => (
                <option key={item} value={item}>
                  {strategyIcon(item)} {item}
                </option>
              ))}
            </select>
            <p className="hidden text-xs text-zinc-500 sm:block">mini-rag searches {documentCount.toLocaleString()} documents in this project</p>
          </div>
          {isStreaming ? (
            <button
              type="button"
              onClick={onStop}
              className="inline-flex h-9 items-center gap-2 rounded-md bg-red-500 px-3 text-sm font-medium text-white transition hover:bg-red-400"
            >
              <Square className="h-4 w-4" />
              Stop
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={!text.trim()}
              className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-indigo-500 text-white transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ArrowUp className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function strategyIcon(strategy: RetrievalStrategy): string {
  const icons: Record<RetrievalStrategy, string> = {
    vanilla: "V",
    hybrid: "H",
    rerank: "R",
    hyde: "Y",
  };
  return icons[strategy];
}

