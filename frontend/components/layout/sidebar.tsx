"use client";

import {
  BarChart2,
  FileText,
  FolderOpen,
  LayoutDashboard,
  Menu,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Settings as SettingsIcon,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo, useState } from "react";

import { useAppStore } from "@/lib/store";

interface NavItem {
  label: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
}

function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

export function Sidebar() {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const apiKey = useAppStore((state) => state.apiKey);
  const tenant = useAppStore((state) => state.tenant);
  const currentProjectId = useAppStore((state) => state.currentProjectId);
  const collapsed = useAppStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useAppStore((state) => state.toggleSidebar);

  const navItems = useMemo<NavItem[]>(() => {
    const items: NavItem[] = [
      { label: "Dashboard", href: "/", icon: LayoutDashboard },
      { label: "Projects", href: "/projects", icon: FolderOpen },
    ];

    if (currentProjectId) {
      items.push(
        {
          label: "Documents",
          href: `/projects/${currentProjectId}/documents`,
          icon: FileText,
        },
        {
          label: "Chat",
          href: `/projects/${currentProjectId}/chat`,
          icon: MessageSquare,
        },
      );
    }

    items.push(
      { label: "Analytics", href: "/analytics", icon: BarChart2 },
      { label: "Settings", href: "/settings", icon: SettingsIcon },
    );

    return items;
  }, [currentProjectId]);

  return (
    <>
      <button
        type="button"
        aria-label="Open navigation"
        onClick={() => setMobileOpen(true)}
        className="fixed left-4 top-4 z-40 inline-flex h-10 w-10 items-center justify-center rounded-md border border-zinc-800 bg-zinc-900 text-zinc-50 shadow-lg shadow-black/20 transition hover:bg-zinc-800 md:hidden"
      >
        <Menu className="h-5 w-5" />
      </button>

      {mobileOpen ? (
        <button
          type="button"
          aria-label="Close navigation backdrop"
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={() => setMobileOpen(false)}
        />
      ) : null}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex border-r border-zinc-800 bg-zinc-950/95 backdrop-blur transition-transform duration-200 md:sticky md:top-0 md:z-auto md:h-screen md:translate-x-0",
          collapsed ? "w-20" : "w-64",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex min-h-0 w-full flex-col">
          <div className="flex h-16 items-center gap-3 border-b border-zinc-800 px-4">
            <Link href="/" className="flex min-w-0 flex-1 items-center gap-3" onClick={() => setMobileOpen(false)}>
              <span
                className={cn(
                  "h-2.5 w-2.5 shrink-0 rounded-full",
                  apiKey ? "bg-emerald-500 shadow-[0_0_16px_rgba(16,185,129,0.55)]" : "bg-zinc-600",
                )}
              />
              {!collapsed ? <span className="truncate text-sm font-semibold tracking-normal text-zinc-50">mini-rag</span> : null}
            </Link>

            <button
              type="button"
              aria-label="Close navigation"
              onClick={() => setMobileOpen(false)}
              className="inline-flex h-9 w-9 items-center justify-center rounded-md text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-50 md:hidden"
            >
              <X className="h-5 w-5" />
            </button>

            <button
              type="button"
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              onClick={toggleSidebar}
              className="hidden h-9 w-9 items-center justify-center rounded-md text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-50 md:inline-flex"
            >
              {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </button>
          </div>

          <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
            {navItems.map((item) => {
              const active = isActive(pathname, item.href);
              const Icon = item.icon;

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setMobileOpen(false)}
                  className={cn(
                    "group flex h-10 items-center gap-3 rounded-md border-l-2 px-3 text-sm font-medium transition",
                    active
                      ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                      : "border-transparent text-zinc-400 hover:bg-zinc-800 hover:text-zinc-50",
                    collapsed && "justify-center px-2",
                  )}
                  title={collapsed ? item.label : undefined}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {!collapsed ? <span className="truncate">{item.label}</span> : null}
                </Link>
              );
            })}
          </nav>

          <div className="border-t border-zinc-800 p-4">
            {!collapsed ? (
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-zinc-100">{tenant?.name ?? "No tenant"}</p>
                <p className="mt-1 truncate font-mono text-xs text-zinc-400">{tenant?.api_key_prefix ?? "no-key"}</p>
              </div>
            ) : (
              <div
                className={cn("mx-auto h-2.5 w-2.5 rounded-full", apiKey ? "bg-emerald-500" : "bg-zinc-600")}
                title={tenant?.name ?? "No tenant"}
              />
            )}
          </div>
        </div>
      </aside>
    </>
  );
}

function isActive(pathname: string, href: string): boolean {
  if (href === "/") {
    return pathname === "/";
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

