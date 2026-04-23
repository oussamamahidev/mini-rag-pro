"use client";

import { Copy } from "lucide-react";

import type { ChatMessage } from "@/types";

interface ChatWindowProps {
  messages: ChatMessage[];
  streamingContent: string;
  isStreaming: boolean;
  messagesEndRef: React.RefObject<HTMLDivElement>;
}

export function ChatWindow({ messages, streamingContent, isStreaming, messagesEndRef }: ChatWindowProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-6">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
        {messages.length === 0 && !streamingContent ? (
          <div className="py-24 text-center">
            <p className="text-lg font-medium text-zinc-100">Ask a question about your documents</p>
            <p className="mt-2 text-sm text-zinc-500">mini-rag will route, retrieve, stream, and cite the answer.</p>
          </div>
        ) : null}

        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}

        {streamingContent ? (
          <AssistantBubble
            content={streamingContent}
            isStreaming={isStreaming}
            metadata={undefined}
            onCopy={() => void navigator.clipboard.writeText(streamingContent)}
          />
        ) : null}
        <div ref={messagesEndRef} />
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === "system") {
    return (
      <div className="animate-in fade-in text-center text-xs text-zinc-600">
        {message.content}
      </div>
    );
  }

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] rounded-bl-2xl rounded-tl-2xl rounded-tr-2xl bg-indigo-500 px-4 py-3 text-sm leading-6 text-white">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <AssistantBubble
      content={message.content}
      metadata={message.metadata}
      onCopy={() => void navigator.clipboard.writeText(message.content)}
    />
  );
}

function AssistantBubble({
  content,
  isStreaming = false,
  metadata,
  onCopy,
}: {
  content: string;
  isStreaming?: boolean;
  metadata?: ChatMessage["metadata"];
  onCopy: () => void;
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-500 text-sm font-semibold text-white">
        M
      </div>
      <div className="min-w-0 max-w-[75%] rounded-2xl bg-zinc-900 px-4 py-3">
        <p className={`whitespace-pre-wrap text-[15px] leading-7 text-zinc-50 ${isStreaming ? "streaming-cursor" : ""}`}>{content}</p>
        {!isStreaming && metadata ? (
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-zinc-800 pt-3">
            <Badge>{metadata.strategy}</Badge>
            <Badge>{Math.round(metadata.latency_ms)}ms</Badge>
            <FaithfulnessMiniBar value={metadata.faithfulness} />
            <button
              type="button"
              aria-label="Copy answer"
              onClick={onCopy}
              className="ml-auto inline-flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 transition hover:bg-zinc-800 hover:text-zinc-50"
            >
              <Copy className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return <span className="rounded-full border border-zinc-800 bg-zinc-950 px-2 py-1 font-mono text-xs text-zinc-400">{children}</span>;
}

function FaithfulnessMiniBar({ value }: { value?: number }) {
  const percent = value === undefined ? 0 : Math.round(value * 100);
  return (
    <div className="flex items-center gap-2 rounded-full border border-zinc-800 bg-zinc-950 px-2 py-1">
      <span className="text-xs text-zinc-500">faith</span>
      <div className="h-1.5 w-12 overflow-hidden rounded-full bg-zinc-800">
        <div className="h-full rounded-full bg-emerald-500" style={{ width: `${percent}%` }} />
      </div>
      <span className="font-mono text-xs text-zinc-400">{value === undefined ? "pending" : `${percent}%`}</span>
    </div>
  );
}

