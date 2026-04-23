"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Sidebar } from "@/components/layout/sidebar";
import { Toaster } from "@/components/ui/toaster";
import { useAppStore } from "@/lib/store";

const publicPaths = ["/login", "/register"];

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30 * 1000,
            retry: 2,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  const apiKey = useAppStore((state) => state.apiKey);
  const hasHydrated = useAppStore((state) => state.hasHydrated);
  const router = useRouter();
  const pathname = usePathname();
  const isPublic = publicPaths.includes(pathname);

  useEffect(() => {
    if (hasHydrated && !apiKey && !isPublic) {
      router.replace("/login");
    }
  }, [apiKey, hasHydrated, isPublic, router]);

  return (
    <QueryClientProvider client={queryClient}>
      {isPublic ? (
        <main className="min-h-screen bg-zinc-950 text-zinc-50">{children}</main>
      ) : (
        <div className="min-h-screen bg-zinc-950 text-zinc-50 md:flex">
          <Sidebar />
          <main className="min-w-0 flex-1 px-4 pb-8 pt-16 sm:px-6 md:px-8 md:pt-8">{children}</main>
          <Toaster />
        </div>
      )}
    </QueryClientProvider>
  );
}
