"use client";

import { QueryClient, QueryClientProvider, useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Copy, Download, Eye, EyeOff, KeyRound, Loader2, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import { api } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import type { RetrievalStrategy, SettingsPatch, Tenant } from "@/types";

const modelOptions = ["gpt-3.5-turbo", "gpt-4", "gpt-4-turbo"];
const strategyOptions: Array<{ value: RetrievalStrategy; label: string; description: string; latency: string }> = [
  { value: "vanilla", label: "Vanilla", description: "Fast, basic semantic search", latency: "~280ms" },
  { value: "hybrid", label: "Hybrid", description: "BM25 + semantic, recommended", latency: "~420ms" },
  { value: "rerank", label: "Rerank", description: "Highest quality, slower", latency: "~600ms" },
  { value: "hyde", label: "HyDE", description: "Good for abstract questions", latency: "~700ms" },
];
const rerankerModels = [
  {
    value: "cross-encoder/ms-marco-MiniLM-L-6-v2",
    label: "MiniLM L6",
    description: "Small footprint, faster ranking for interactive chat.",
  },
  {
    value: "BAAI/bge-reranker-large",
    label: "BGE reranker large",
    description: "Larger model with stronger precision and higher latency.",
  },
];

export default function SettingsPage() {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <SettingsContent />
    </QueryClientProvider>
  );
}

function SettingsContent() {
  const { toast } = useToast();
  const apiKey = useAppStore((state) => state.apiKey);
  const setApiKey = useAppStore((state) => state.setApiKey);
  const storedTenant = useAppStore((state) => state.tenant);
  const setTenant = useAppStore((state) => state.setTenant);
  const selectedStrategy = useAppStore((state) => state.selectedStrategy);
  const setStrategy = useAppStore((state) => state.setStrategy);

  const [showOpenAiKey, setShowOpenAiKey] = useState(false);
  const [openAiApiKey, setOpenAiApiKey] = useState("");
  const [model, setModel] = useState("gpt-4-turbo");
  const [maxTokens, setMaxTokens] = useState(1024);
  const [temperature, setTemperature] = useState(0.2);
  const [defaultStrategy, setDefaultStrategy] = useState<RetrievalStrategy>(selectedStrategy);
  const [topK, setTopK] = useState(5);
  const [rerankerModel, setRerankerModel] = useState(rerankerModels[0].value);
  const [rotateOpen, setRotateOpen] = useState(false);
  const [graceSeconds, setGraceSeconds] = useState(0);

  const tenantQuery = useQuery({
    queryKey: ["tenant"],
    queryFn: api.auth.me,
    retry: 1,
    enabled: Boolean(apiKey),
  });

  const activeTenant = tenantQuery.data ?? storedTenant;
  const rateLimitMax = activeTenant?.rate_limit_per_hour ?? 100;
  const rateLimitUsed = Math.min(12, rateLimitMax);
  const rateLimitPct = Math.round((rateLimitUsed / Math.max(1, rateLimitMax)) * 100);
  const maskedKey = useMemo(() => maskApiKey(apiKey, activeTenant), [apiKey, activeTenant]);

  useEffect(() => {
    if (tenantQuery.data) {
      setTenant(tenantQuery.data);
    }
  }, [setTenant, tenantQuery.data]);

  useEffect(() => {
    if (graceSeconds <= 0) {
      return;
    }
    const timer = window.setInterval(() => {
      setGraceSeconds((current) => Math.max(0, current - 60));
    }, 60000);
    return () => window.clearInterval(timer);
  }, [graceSeconds]);

  const settingsMutation = useMutation({
    mutationFn: (patch: SettingsPatch) => api.settings.update(patch),
  });

  const healthMutation = useMutation({
    mutationFn: api.health.check,
    onSuccess: (result) => {
      toast({
        variant: "success",
        title: "Connection healthy",
        description: `Backend is ${result.status} on ${result.environment}.`,
      });
    },
    onError: (error) => {
      toast({
        variant: "destructive",
        title: "Connection failed",
        description: errorMessage(error),
      });
    },
  });

  const rotateMutation = useMutation({
    mutationFn: api.auth.rotateKey,
    onSuccess: (result) => {
      setApiKey(result.new_api_key);
      if (activeTenant) {
        setTenant({ ...activeTenant, api_key_prefix: result.new_prefix });
      }
      setGraceSeconds(60 * 60);
      setRotateOpen(false);
      toast({
        variant: "success",
        title: "API key regenerated",
        description: "Your old key remains valid for 60 minutes.",
      });
    },
    onError: (error) => {
      toast({
        variant: "destructive",
        title: "Could not rotate key",
        description: errorMessage(error),
      });
    },
  });

  const exportMutation = useMutation({
    mutationFn: () => api.analytics.export(),
    onSuccess: (blob) => {
      downloadBlob(blob, `queries_${new Date().toISOString().slice(0, 10)}.csv`);
      toast({
        variant: "success",
        title: "Usage report ready",
        description: "The CSV export has started.",
      });
    },
    onError: (error) => {
      toast({
        variant: "destructive",
        title: "Export failed",
        description: errorMessage(error),
      });
    },
  });

  async function saveSettings(label: string, patch: SettingsPatch, afterSave?: () => void) {
    try {
      await settingsMutation.mutateAsync(patch);
      afterSave?.();
      toast({
        variant: "success",
        title: `${label} saved`,
        description: "Settings were patched on the backend.",
      });
    } catch (error) {
      toast({
        variant: "destructive",
        title: `Could not save ${label.toLowerCase()}`,
        description: errorMessage(error),
      });
    }
  }

  function saveApiConfiguration() {
    const patch: SettingsPatch = {
      model,
      max_tokens: maxTokens,
      temperature,
    };
    if (openAiApiKey.trim()) {
      patch.openai_api_key = openAiApiKey.trim();
    }
    void saveSettings("API configuration", patch, () => setOpenAiApiKey(""));
  }

  function saveRetrievalConfiguration() {
    void saveSettings(
      "Retrieval configuration",
      {
        default_strategy: defaultStrategy,
        top_k: topK,
        reranker_model: rerankerModel,
      },
      () => setStrategy(defaultStrategy),
    );
  }

  function copyCurrentKey() {
    if (!apiKey) {
      return;
    }
    void navigator.clipboard.writeText(apiKey);
    toast({
      variant: "success",
      title: "API key copied",
      description: "The current key was copied to your clipboard.",
    });
  }

  return (
    <section className="mx-auto flex w-full max-w-5xl flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold text-zinc-50">Settings</h1>
        <p className="mt-1 text-sm text-zinc-400">Configure generation, retrieval, and account access for mini-rag.</p>
      </header>

      <SettingsCard title="API Configuration" description="Generation model, limits, and provider connection.">
        <div className="grid gap-5">
          <label className="grid gap-2">
            <span className="text-sm font-medium text-zinc-200">OpenAI API key</span>
            <div className="flex flex-col gap-3 sm:flex-row">
              <div className="relative flex-1">
                <input
                  type={showOpenAiKey ? "text" : "password"}
                  value={openAiApiKey}
                  onChange={(event) => setOpenAiApiKey(event.target.value)}
                  placeholder="sk-..."
                  className="h-10 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 pr-10 text-sm text-zinc-100 outline-none transition placeholder:text-zinc-600 focus:border-indigo-500"
                />
                <button
                  type="button"
                  aria-label={showOpenAiKey ? "Hide API key" : "Show API key"}
                  onClick={() => setShowOpenAiKey((value) => !value)}
                  className="absolute right-2 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-zinc-500 transition hover:bg-zinc-800 hover:text-zinc-100"
                >
                  {showOpenAiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              <button
                type="button"
                onClick={() => healthMutation.mutate()}
                disabled={healthMutation.isPending}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-zinc-800 bg-zinc-950 px-4 text-sm text-zinc-200 transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {healthMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                Test Connection
              </button>
            </div>
          </label>

          <div className="grid gap-5 md:grid-cols-2">
            <label className="grid gap-2">
              <span className="text-sm font-medium text-zinc-200">Model</span>
              <select
                value={model}
                onChange={(event) => setModel(event.target.value)}
                className="h-10 rounded-md border border-zinc-800 bg-zinc-950 px-3 text-sm text-zinc-100 outline-none transition focus:border-indigo-500"
              >
                {modelOptions.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
            <RangeField
              label="Max tokens"
              value={maxTokens}
              min={256}
              max={4096}
              step={256}
              display={`${maxTokens.toLocaleString()} tokens`}
              onChange={setMaxTokens}
            />
            <RangeField
              label="Temperature"
              value={temperature}
              min={0}
              max={1}
              step={0.1}
              display={temperature.toFixed(1)}
              onChange={setTemperature}
            />
          </div>

          <div className="flex justify-end">
            <SaveButton pending={settingsMutation.isPending} onClick={saveApiConfiguration}>
              Save API Configuration
            </SaveButton>
          </div>
        </div>
      </SettingsCard>

      <SettingsCard title="Retrieval Configuration" description="Default search behavior and ranking depth.">
        <div className="grid gap-5">
          <div>
            <p className="text-sm font-medium text-zinc-200">Default strategy</p>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              {strategyOptions.map((option) => (
                <label
                  key={option.value}
                  className={`cursor-pointer rounded-lg border p-4 transition ${
                    defaultStrategy === option.value
                      ? "border-indigo-500 bg-indigo-500/10"
                      : "border-zinc-800 bg-zinc-950 hover:border-zinc-700"
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <input
                      type="radio"
                      name="default-strategy"
                      value={option.value}
                      checked={defaultStrategy === option.value}
                      onChange={() => setDefaultStrategy(option.value)}
                      className="mt-1 h-4 w-4 accent-indigo-500"
                    />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-zinc-100">{option.label}</span>
                        <span className="rounded-full bg-zinc-900 px-2 py-0.5 font-mono text-xs text-zinc-500">{option.latency}</span>
                      </div>
                      <p className="mt-1 text-sm text-zinc-500">{option.description}</p>
                    </div>
                  </div>
                </label>
              ))}
            </div>
          </div>

          <RangeField
            label="Top-K"
            value={topK}
            min={1}
            max={20}
            step={1}
            display={`Return top ${topK} chunks`}
            onChange={setTopK}
          />

          <label className="grid gap-2">
            <span className="text-sm font-medium text-zinc-200">Re-ranker model</span>
            <select
              value={rerankerModel}
              onChange={(event) => setRerankerModel(event.target.value)}
              className="h-10 rounded-md border border-zinc-800 bg-zinc-950 px-3 text-sm text-zinc-100 outline-none transition focus:border-indigo-500"
            >
              {rerankerModels.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <span className="text-xs text-zinc-500">{rerankerModels.find((option) => option.value === rerankerModel)?.description}</span>
          </label>

          <div className="flex justify-end">
            <SaveButton pending={settingsMutation.isPending} onClick={saveRetrievalConfiguration}>
              Save Retrieval Configuration
            </SaveButton>
          </div>
        </div>
      </SettingsCard>

      <SettingsCard title="Account & API Key" description="Tenant key management, hourly quota, and usage export.">
        <div className="grid gap-6">
          <div className="flex flex-col gap-3 rounded-lg border border-zinc-800 bg-zinc-950 p-4 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0">
              <p className="text-sm font-medium text-zinc-200">Current API key</p>
              <p className="mt-1 truncate font-mono text-sm text-zinc-400">{maskedKey}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={copyCurrentKey}
                disabled={!apiKey}
                className="inline-flex h-9 items-center gap-2 rounded-md border border-zinc-800 px-3 text-sm text-zinc-200 transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Copy className="h-4 w-4" />
                Copy
              </button>
              <button
                type="button"
                onClick={() => setRotateOpen(true)}
                className="inline-flex h-9 items-center gap-2 rounded-md border border-red-500/30 px-3 text-sm text-red-300 transition hover:bg-red-500/10"
              >
                <RotateCcw className="h-4 w-4" />
                Regenerate Key
              </button>
            </div>
          </div>

          {graceSeconds > 0 ? (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
              Your old key remains valid for {Math.ceil(graceSeconds / 60)} minutes.
            </div>
          ) : null}

          <div>
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium text-zinc-200">Rate limit usage</span>
              <span className="font-mono text-zinc-400">
                {rateLimitUsed} / {rateLimitMax} requests this hour
              </span>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-zinc-800">
              <div className="h-full rounded-full bg-indigo-500" style={{ width: `${rateLimitPct}%` }} />
            </div>
            <p className="mt-2 text-xs text-zinc-500">Resets in: 47 minutes</p>
          </div>

          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => exportMutation.mutate()}
              disabled={exportMutation.isPending}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-zinc-800 bg-zinc-950 px-4 text-sm text-zinc-200 transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {exportMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              Download Usage Report
            </button>
          </div>
        </div>
      </SettingsCard>

      <Dialog open={rotateOpen} onOpenChange={setRotateOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Regenerate API key</DialogTitle>
            <DialogDescription>
              A new key will be issued immediately. Your old key remains valid for 60 minutes so running clients can be updated.
            </DialogDescription>
          </DialogHeader>
          <div className="mt-5 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">
            After confirming, the new key is shown only once and stored in this browser.
          </div>
          <DialogFooter>
            <button
              type="button"
              onClick={() => setRotateOpen(false)}
              className="h-10 rounded-md border border-zinc-800 px-4 text-sm text-zinc-200 transition hover:bg-zinc-800"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => rotateMutation.mutate()}
              disabled={rotateMutation.isPending}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-red-500 px-4 text-sm font-medium text-white transition hover:bg-red-400 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {rotateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
              Regenerate Key
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

function SettingsCard({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
      <div className="mb-5 border-b border-zinc-800 pb-4">
        <h2 className="text-base font-semibold text-zinc-50">{title}</h2>
        <p className="mt-1 text-sm text-zinc-500">{description}</p>
      </div>
      {children}
    </section>
  );
}

function RangeField({
  label,
  value,
  min,
  max,
  step,
  display,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="grid gap-2">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium text-zinc-200">{label}</span>
        <span className="font-mono text-xs text-zinc-400">{display}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-2 w-full cursor-pointer accent-indigo-500"
      />
    </label>
  );
}

function SaveButton({ children, pending, onClick }: { children: React.ReactNode; pending: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={pending}
      className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-indigo-500 px-4 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
      {children}
    </button>
  );
}

function maskApiKey(apiKey: string | null, tenant: Tenant | null): string {
  if (apiKey && apiKey.length > 8) {
    return `${apiKey.slice(0, 8)}••••••••••`;
  }
  if (tenant?.api_key_prefix) {
    return `${tenant.api_key_prefix}••••••••••`;
  }
  return "No API key configured";
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Request failed.";
}
