"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { RetrievalStrategy, Tenant } from "@/types";

const API_KEY_STORAGE_KEY = "minirag_api_key";

interface AppState {
  apiKey: string | null;
  hasHydrated: boolean;
  setHasHydrated: (hasHydrated: boolean) => void;
  tenant: Tenant | null;
  setApiKey: (key: string) => void;
  setTenant: (tenant: Tenant | null) => void;
  logout: () => void;
  currentProjectId: string | null;
  setCurrentProjectId: (id: string | null) => void;
  selectedStrategy: RetrievalStrategy;
  setStrategy: (strategy: RetrievalStrategy) => void;
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
}

function writeApiKey(key: string | null): void {
  if (typeof window === "undefined") {
    return;
  }
  if (key) {
    window.localStorage.setItem(API_KEY_STORAGE_KEY, key);
  } else {
    window.localStorage.removeItem(API_KEY_STORAGE_KEY);
  }
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      apiKey: null,
      hasHydrated: false,
      setHasHydrated: (hasHydrated) => set({ hasHydrated }),
      tenant: null,
      setApiKey: (key) => {
        writeApiKey(key);
        set({ apiKey: key });
      },
      setTenant: (tenant) => set({ tenant }),
      logout: () => {
        writeApiKey(null);
        set({
          apiKey: null,
          tenant: null,
          currentProjectId: null,
        });
      },
      currentProjectId: null,
      setCurrentProjectId: (id) => set({ currentProjectId: id }),
      selectedStrategy: "hybrid",
      setStrategy: (strategy) => set({ selectedStrategy: strategy }),
      sidebarCollapsed: false,
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
    }),
    {
      name: "minirag-store",
      partialize: (state) => ({
        apiKey: state.apiKey,
        tenant: state.tenant,
        selectedStrategy: state.selectedStrategy,
        currentProjectId: state.currentProjectId,
      }),
      onRehydrateStorage: () => (state) => {
        if (state?.apiKey) {
          writeApiKey(state.apiKey);
        }
        state?.setHasHydrated(true);
      },
    },
  ),
);
