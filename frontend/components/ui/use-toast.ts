"use client";

import * as React from "react";

export type ToastVariant = "default" | "success" | "destructive";

export interface Toast {
  id: string;
  title?: React.ReactNode;
  description?: React.ReactNode;
  variant?: ToastVariant;
  duration?: number;
}

type ToastInput = Omit<Toast, "id">;
type Listener = (toasts: Toast[]) => void;

const TOAST_LIMIT = 4;
const DEFAULT_DURATION_MS = 4000;

let memoryToasts: Toast[] = [];
const listeners = new Set<Listener>();

export function toast(input: ToastInput) {
  const id = createToastId();
  const nextToast: Toast = { id, ...input };

  memoryToasts = [nextToast, ...memoryToasts].slice(0, TOAST_LIMIT);
  emit();

  const duration = input.duration ?? DEFAULT_DURATION_MS;
  if (duration > 0) {
    window.setTimeout(() => dismissToast(id), duration);
  }

  return {
    id,
    dismiss: () => dismissToast(id),
  };
}

export function useToast() {
  const [toasts, setToasts] = React.useState<Toast[]>(memoryToasts);

  React.useEffect(() => {
    listeners.add(setToasts);
    return () => {
      listeners.delete(setToasts);
    };
  }, []);

  return {
    toasts,
    toast,
    dismiss: dismissToast,
  };
}

function dismissToast(id: string) {
  memoryToasts = memoryToasts.filter((toastItem) => toastItem.id !== id);
  emit();
}

function emit() {
  for (const listener of Array.from(listeners)) {
    listener(memoryToasts);
  }
}

function createToastId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
