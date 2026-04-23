"use client";

import { motion, AnimatePresence } from "framer-motion";

interface BulkActionsBarProps {
  selectedCount: number;
  onDeleteSelected: () => void;
  onReindexSelected: () => void;
}

export function BulkActionsBar({ selectedCount, onDeleteSelected, onReindexSelected }: BulkActionsBarProps) {
  return (
    <AnimatePresence>
      {selectedCount > 0 ? (
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 40 }}
          className="fixed bottom-6 left-1/2 z-50 flex -translate-x-1/2 items-center gap-4 rounded-xl border border-zinc-800 bg-zinc-950 px-4 py-3 shadow-2xl shadow-black/40"
        >
          <span className="text-sm text-zinc-300">{selectedCount} selected</span>
          <button
            type="button"
            onClick={onDeleteSelected}
            className="h-9 rounded-md border border-red-500/30 px-3 text-sm text-red-300 transition hover:bg-red-500/10"
          >
            Delete selected
          </button>
          <button
            type="button"
            onClick={onReindexSelected}
            className="h-9 rounded-md border border-zinc-800 px-3 text-sm text-zinc-200 transition hover:bg-zinc-800"
          >
            Re-index selected
          </button>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

