"use client";

import { Check, KeyRound, UserPlus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { api } from "@/lib/api";
import { useAppStore } from "@/lib/store";

type Mode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const setApiKey = useAppStore((state) => state.setApiKey);
  const setTenant = useAppStore((state) => state.setTenant);
  const [mode, setMode] = useState<Mode>("login");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [registeredKey, setRegisteredKey] = useState("");
  const [savedKey, setSavedKey] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function login(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const key = apiKeyInput.trim();
    if (!key) {
      return;
    }

    setSubmitting(true);
    setError("");
    try {
      setApiKey(key);
      const tenant = await api.auth.me();
      setTenant(tenant);
      router.replace("/");
    } catch (err) {
      setApiKey("");
      setTenant(null);
      setError(errorMessage(err, "API key was rejected."));
    } finally {
      setSubmitting(false);
    }
  }

  async function register(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim() || !email.trim()) {
      return;
    }

    setSubmitting(true);
    setError("");
    try {
      const response = await api.auth.register({ name: name.trim(), email: email.trim() });
      setRegisteredKey(response.api_key);
      setSavedKey(false);
    } catch (err) {
      setError(errorMessage(err, "Registration failed."));
    } finally {
      setSubmitting(false);
    }
  }

  async function continueAfterRegistration() {
    if (!registeredKey || !savedKey) {
      return;
    }
    setApiKey(registeredKey);
    const tenant = await api.auth.me();
    setTenant(tenant);
    router.replace("/");
  }

  return (
    <section className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-md rounded-lg border border-zinc-800 bg-zinc-900 p-6 shadow-2xl shadow-black/30">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-zinc-50">mini-rag</h1>
          <p className="mt-2 text-sm text-zinc-400">Use an API key or create a workspace.</p>
        </div>

        <div className="mb-6 grid grid-cols-2 rounded-md border border-zinc-800 bg-zinc-950 p-1">
          <button
            type="button"
            onClick={() => setMode("login")}
            className={`inline-flex h-10 items-center justify-center gap-2 rounded text-sm ${
              mode === "login" ? "bg-zinc-800 text-zinc-50" : "text-zinc-400"
            }`}
          >
            <KeyRound className="h-4 w-4" />
            Login
          </button>
          <button
            type="button"
            onClick={() => setMode("register")}
            className={`inline-flex h-10 items-center justify-center gap-2 rounded text-sm ${
              mode === "register" ? "bg-zinc-800 text-zinc-50" : "text-zinc-400"
            }`}
          >
            <UserPlus className="h-4 w-4" />
            Register
          </button>
        </div>

        {error ? <div className="mb-4 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-100">{error}</div> : null}

        {mode === "login" ? (
          <form onSubmit={login} className="space-y-4">
            <label className="block">
              <span className="text-sm font-medium text-zinc-200">API key</span>
              <input
                value={apiKeyInput}
                onChange={(event) => setApiKeyInput(event.target.value)}
                className="mt-2 h-11 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 font-mono text-sm text-zinc-50 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
                placeholder="sk-..."
              />
            </label>
            <button
              type="submit"
              disabled={submitting || !apiKeyInput.trim()}
              className="inline-flex h-11 w-full items-center justify-center rounded-md bg-indigo-500 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? "Checking..." : "Continue"}
            </button>
          </form>
        ) : registeredKey ? (
          <div className="space-y-4">
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-4">
              <p className="text-sm font-medium text-amber-200">Save this API key now.</p>
              <p className="mt-3 break-all font-mono text-sm text-amber-50">{registeredKey}</p>
            </div>
            <label className="flex items-center gap-3 text-sm text-zinc-300">
              <input
                type="checkbox"
                checked={savedKey}
                onChange={(event) => setSavedKey(event.target.checked)}
                className="h-4 w-4 rounded border-zinc-700 bg-zinc-950"
              />
              I&apos;ve saved it
            </label>
            <button
              type="button"
              disabled={!savedKey}
              onClick={() => void continueAfterRegistration()}
              className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-md bg-indigo-500 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Check className="h-4 w-4" />
              Open dashboard
            </button>
          </div>
        ) : (
          <form onSubmit={register} className="space-y-4">
            <label className="block">
              <span className="text-sm font-medium text-zinc-200">Name</span>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="mt-2 h-11 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 text-sm text-zinc-50 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
                placeholder="Test Corp"
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-zinc-200">Email</span>
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="mt-2 h-11 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 text-sm text-zinc-50 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
                placeholder="you@example.com"
              />
            </label>
            <button
              type="submit"
              disabled={submitting || !name.trim() || !email.trim()}
              className="inline-flex h-11 w-full items-center justify-center rounded-md bg-indigo-500 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? "Creating..." : "Create account"}
            </button>
          </form>
        )}
      </div>
    </section>
  );
}

function errorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "detail" in error) {
    return String((error as { detail?: unknown }).detail || fallback);
  }
  if (error instanceof Error) {
    return error.message;
  }
  return fallback;
}
