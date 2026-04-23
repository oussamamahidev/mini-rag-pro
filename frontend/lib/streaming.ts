import type { Source } from "@/types";

export interface StreamCallbacks {
  onStart?: (sessionId: string | null) => void;
  onRouting?: (decision: string, reason: string, confidence: number) => void;
  onSources?: (sources: Source[]) => void;
  onToken?: (token: string) => void;
  onMetadata?: (data: { latency_ms: number; strategy: string; routing_decision: string }) => void;
  onDone?: () => void;
  onError?: (message: string) => void;
}

interface StreamEventPayload {
  type?: string;
  session_id?: string | null;
  decision?: string;
  reason?: string | null;
  confidence?: number;
  data?: unknown;
  latency_ms?: number;
  strategy?: string;
  routing_decision?: string;
  message?: string;
}

export async function streamQuery(
  params: { text: string; project_id: string; strategy: string; session_id?: string },
  apiKey: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  try {
    const response = await fetch("/api/query/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
      },
      body: JSON.stringify(params),
      signal,
    });

    if (!response.ok) {
      callbacks.onError?.(await responseErrorMessage(response));
      return;
    }

    if (!response.body) {
      callbacks.onError?.("Streaming is not supported by this browser.");
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const messages = buffer.split("\n\n");
      buffer = messages.pop() ?? "";

      for (const message of messages) {
        handleSSEMessage(message, callbacks);
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) {
      handleSSEMessage(buffer, callbacks);
    }
  } catch (error) {
    if (isAbortError(error)) {
      return;
    }
    callbacks.onError?.(error instanceof Error ? error.message : "Streaming request failed.");
  }
}

function handleSSEMessage(message: string, callbacks: StreamCallbacks): void {
  const dataLines = message
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart());

  if (dataLines.length === 0) {
    return;
  }

  try {
    const payload = JSON.parse(dataLines.join("\n")) as StreamEventPayload;
    dispatchStreamEvent(payload, callbacks);
  } catch {
    callbacks.onError?.("Received an invalid streaming event.");
  }
}

function dispatchStreamEvent(payload: StreamEventPayload, callbacks: StreamCallbacks): void {
  switch (payload.type) {
    case "start":
      callbacks.onStart?.(payload.session_id ?? null);
      break;
    case "routing":
      callbacks.onRouting?.(payload.decision ?? "", payload.reason ?? "", payload.confidence ?? 0);
      break;
    case "sources":
      callbacks.onSources?.(Array.isArray(payload.data) ? (payload.data as Source[]) : []);
      break;
    case "token":
      callbacks.onToken?.(typeof payload.data === "string" ? payload.data : "");
      break;
    case "metadata":
      callbacks.onMetadata?.({
        latency_ms: payload.latency_ms ?? 0,
        strategy: payload.strategy ?? "",
        routing_decision: payload.routing_decision ?? "",
      });
      break;
    case "done":
      callbacks.onDone?.();
      break;
    case "error":
      callbacks.onError?.(payload.message ?? "Streaming failed.");
      break;
    default:
      break;
  }
}

async function responseErrorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    return typeof payload.detail === "string" ? payload.detail : response.statusText;
  } catch {
    return response.statusText || "Streaming request failed.";
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

