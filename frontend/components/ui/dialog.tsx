"use client";

import { createContext, useContext, useEffect, useMemo } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

interface DialogContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
}

const DialogContext = createContext<DialogContextValue | null>(null);

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
}

export function Dialog({ open, onOpenChange, children }: DialogProps) {
  const value = useMemo(() => ({ open, setOpen: onOpenChange }), [open, onOpenChange]);

  return <DialogContext.Provider value={value}>{children}</DialogContext.Provider>;
}

export function DialogTrigger({
  children,
  asChild = false,
}: {
  children: React.ReactElement<{ onClick?: React.MouseEventHandler }>;
  asChild?: boolean;
}) {
  const dialog = useDialogContext();

  if (asChild) {
    return {
      ...children,
      props: {
        ...children.props,
        onClick: (event: React.MouseEvent) => {
          children.props.onClick?.(event);
          dialog.setOpen(true);
        },
      },
    };
  }

  return (
    <button type="button" onClick={() => dialog.setOpen(true)}>
      {children}
    </button>
  );
}

export function DialogContent({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  const dialog = useDialogContext();

  useEffect(() => {
    if (!dialog.open) {
      return;
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        dialog.setOpen(false);
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [dialog]);

  if (!dialog.open || typeof document === "undefined") {
    return null;
  }

  return createPortal(
    <div className="fixed inset-0 z-[80] flex items-center justify-center px-4 py-6">
      <button
        type="button"
        aria-label="Close dialog backdrop"
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={() => dialog.setOpen(false)}
      />
      <div
        role="dialog"
        aria-modal="true"
        className={`relative max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-zinc-800 bg-zinc-950 p-6 shadow-2xl shadow-black/40 ${className}`}
      >
        <button
          type="button"
          aria-label="Close dialog"
          onClick={() => dialog.setOpen(false)}
          className="absolute right-4 top-4 inline-flex h-8 w-8 items-center justify-center rounded-md text-zinc-500 transition hover:bg-zinc-800 hover:text-zinc-50"
        >
          <X className="h-4 w-4" />
        </button>
        {children}
      </div>
    </div>,
    document.body,
  );
}

export function DialogHeader({ children }: { children: React.ReactNode }) {
  return <div className="pr-10">{children}</div>;
}

export function DialogTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="text-lg font-semibold text-zinc-50">{children}</h2>;
}

export function DialogDescription({ children }: { children: React.ReactNode }) {
  return <p className="mt-2 text-sm text-zinc-400">{children}</p>;
}

export function DialogFooter({ children }: { children: React.ReactNode }) {
  return <div className="mt-6 flex justify-end gap-3">{children}</div>;
}

export function DialogClose({ children }: { children: React.ReactNode }) {
  const dialog = useDialogContext();
  return (
    <button type="button" onClick={() => dialog.setOpen(false)}>
      {children}
    </button>
  );
}

function useDialogContext() {
  const context = useContext(DialogContext);
  if (!context) {
    throw new Error("Dialog components must be used inside Dialog");
  }
  return context;
}

