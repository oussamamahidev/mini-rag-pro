"use client";

import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { ChatWindow } from "@/components/chat/chat-window";
import { InputBar } from "@/components/chat/input-bar";
import { SourcesPanel } from "@/components/chat/sources-panel";
import { api } from "@/lib/api";
import { streamQuery } from "@/lib/streaming";
import { useAppStore } from "@/lib/store";
import type { ChatMessage, RetrievalStrategy, Source } from "@/types";

export default function ChatPage({ params }: { params: { id: string } }) {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <ChatContent projectId={params.id} />
    </QueryClientProvider>
  );
}

function ChatContent({ projectId }: { projectId: string }) {
  const apiKey = useAppStore((state) => state.apiKey);
  const selectedStrategy = useAppStore((state) => state.selectedStrategy);
  const setStrategy = useAppStore((state) => state.setStrategy);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streamingContent, setStreamingContent] = useState("");
  const [streamingSources, setStreamingSources] = useState<Source[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId] = useState(createSessionId);
  const abortRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const streamingContentRef = useRef("");
  const metadataRef = useRef<{ latency_ms: number; strategy: string; routing_decision: string } | null>(null);

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.projects.get(projectId),
    retry: 1,
  });

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent]);

  async function submitQuestion(text: string) {
    if (!apiKey || isStreaming) {
      return;
    }

    const userMessage: ChatMessage = {
      id: createSessionId(),
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };

    setMessages((current) => [...current, userMessage]);
    setStreamingContent("");
    setStreamingSources([]);
    streamingContentRef.current = "";
    metadataRef.current = null;
    setIsStreaming(true);

    const abortController = new AbortController();
    abortRef.current = abortController;
    let completed = false;

    await streamQuery(
      {
        text,
        project_id: projectId,
        strategy: selectedStrategy,
        session_id: sessionId,
      },
      apiKey,
      {
        onRouting: (decision, _reason, confidence) => {
          setMessages((current) => [
            ...current,
            {
              id: createSessionId(),
              role: "system",
              content: `Routing: ${routingLabel(decision)} • ${confidence.toFixed(2)} confidence`,
              created_at: new Date().toISOString(),
            },
          ]);
        },
        onSources: (sources) => setStreamingSources(sources),
        onToken: (token) => {
          streamingContentRef.current += token;
          setStreamingContent(streamingContentRef.current);
        },
        onMetadata: (metadata) => {
          metadataRef.current = metadata;
        },
        onDone: () => {
          completed = true;
          finishAssistantMessage();
        },
        onError: (message) => {
          completed = true;
          setIsStreaming(false);
          setMessages((current) => [
            ...current,
            {
              id: createSessionId(),
              role: "assistant",
              content: message || "Streaming failed.",
              sources: [],
              created_at: new Date().toISOString(),
            },
          ]);
        },
      },
      abortController.signal,
    );

    if (!completed && streamingContentRef.current) {
      finishAssistantMessage();
    } else if (!completed) {
      setIsStreaming(false);
    }
  }

  function finishAssistantMessage() {
    const content = streamingContentRef.current.trim();
    if (content) {
      const metadata = metadataRef.current;
      setMessages((current) => [
        ...current,
        {
          id: createSessionId(),
          role: "assistant",
          content,
          sources: streamingSources,
          metadata: metadata
            ? {
                strategy: metadata.strategy,
                latency_ms: metadata.latency_ms,
              }
            : undefined,
          created_at: new Date().toISOString(),
        },
      ]);
    }
    setStreamingContent("");
    streamingContentRef.current = "";
    setIsStreaming(false);
    abortRef.current = null;
  }

  function stopStreaming() {
    abortRef.current?.abort();
    finishAssistantMessage();
  }

  return (
    <section className="mx-auto grid w-full max-w-7xl gap-6 lg:grid-cols-[65fr_35fr]">
      <div className="flex h-[calc(100vh-8rem)] min-h-[640px] flex-col overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950">
        <div className="border-b border-zinc-800 px-5 py-4">
          <h1 className="text-lg font-semibold text-zinc-50">{projectQuery.data?.name ?? "Chat"}</h1>
          <p className="mt-1 text-sm text-zinc-500">Streaming document retrieval workspace</p>
        </div>
        <ChatWindow messages={messages} streamingContent={streamingContent} isStreaming={isStreaming} messagesEndRef={messagesEndRef} />
        <InputBar
          strategy={selectedStrategy}
          onStrategyChange={(strategy: RetrievalStrategy) => setStrategy(strategy)}
          onSubmit={submitQuestion}
          onStop={stopStreaming}
          isStreaming={isStreaming}
          documentCount={projectQuery.data?.document_count ?? 0}
        />
      </div>
      <SourcesPanel sources={streamingSources.length > 0 ? streamingSources : latestAssistantSources(messages)} />
    </section>
  );
}

function latestAssistantSources(messages: ChatMessage[]): Source[] {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant" && message.sources?.length) {
      return message.sources;
    }
  }
  return [];
}

function routingLabel(decision: string): string {
  const labels: Record<string, string> = {
    rag: "Document Retrieval",
    web_search: "Web Search",
    direct: "Direct Answer",
    clarify: "Clarification",
  };
  return labels[decision] ?? decision;
}

function createSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
