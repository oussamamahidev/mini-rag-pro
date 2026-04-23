"use client";

import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, Info, X, XCircle } from "lucide-react";

import { type ToastVariant, useToast } from "@/components/ui/use-toast";

export function Toaster() {
  const { toasts, dismiss } = useToast();

  return (
    <div className="fixed right-4 top-4 z-[100] flex w-[calc(100vw-2rem)] max-w-sm flex-col gap-3">
      <AnimatePresence initial={false}>
        {toasts.map((toast) => (
          <motion.div
            key={toast.id}
            layout
            initial={{ opacity: 0, x: 24, scale: 0.98 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: 24, scale: 0.98 }}
            className={`rounded-lg border bg-zinc-950 p-4 shadow-2xl shadow-black/30 ${variantClass(toast.variant)}`}
          >
            <div className="flex items-start gap-3">
              <VariantIcon variant={toast.variant} />
              <div className="min-w-0 flex-1">
                {toast.title ? <p className="text-sm font-medium text-zinc-50">{toast.title}</p> : null}
                {toast.description ? <p className="mt-1 text-sm leading-5 text-zinc-400">{toast.description}</p> : null}
              </div>
              <button
                type="button"
                aria-label="Dismiss notification"
                onClick={() => dismiss(toast.id)}
                className="inline-flex h-6 w-6 items-center justify-center rounded-md text-zinc-500 transition hover:bg-zinc-800 hover:text-zinc-100"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

function VariantIcon({ variant }: { variant?: ToastVariant }) {
  if (variant === "success") {
    return <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />;
  }
  if (variant === "destructive") {
    return <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />;
  }
  return <Info className="mt-0.5 h-4 w-4 shrink-0 text-indigo-400" />;
}

function variantClass(variant?: ToastVariant): string {
  if (variant === "success") {
    return "border-emerald-500/30";
  }
  if (variant === "destructive") {
    return "border-red-500/30";
  }
  return "border-zinc-800";
}
