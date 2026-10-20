"use client";

import {
  Activity, Bell, Cpu, FlaskConical, Gauge, LayoutDashboard, ListChecks, Menu, Newspaper, Settings, Wallet, X, type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { cn } from "@/lib/cn";
import { NAV, type NavItem } from "@/lib/nav";

const ICONS: Record<NavItem["icon"], LucideIcon> = {
  dashboard: LayoutDashboard, signals: Activity, catalysts: Newspaper, lab: FlaskConical, models: Cpu, positions: Wallet, alerts: Bell,
  operations: Gauge, settings: Settings, setup: ListChecks,
};

function active(path: string, href: string) {
  return href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`);
}

export function SideNav() {
  const path = usePathname();
  return (
    <nav aria-label="Primary" className="flex flex-col gap-0.5">
      {NAV.map((item) => {
        const Icon = ICONS[item.icon];
        const on = active(path, item.href);
        return (
          <Link key={item.href} href={item.href} aria-current={on ? "page" : undefined}
            className={cn("flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] text-muted hover:bg-surface-2 hover:text-fg",
              on && "bg-surface-2 font-medium text-fg")}>
            <Icon aria-hidden="true" className={cn("size-4 shrink-0", on ? "text-info" : "text-faint")} />
            <span className="truncate">{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

export function MobileNav() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  const primary = NAV.filter((n) => n.primary);
  return (
    <>
      <nav aria-label="Primary (mobile)" className="no-print fixed inset-x-0 bottom-0 z-40 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        <ul className="grid grid-cols-5">
          {primary.map((item) => {
            const Icon = ICONS[item.icon];
            const on = active(path, item.href);
            return (
              <li key={item.href}>
                <Link href={item.href} aria-current={on ? "page" : undefined}
                  className={cn("flex min-h-14 flex-col items-center justify-center gap-0.5 text-[11px] text-muted", on && "text-fg")}>
                  <Icon aria-hidden="true" className={cn("size-5", on ? "text-info" : "text-faint")} />
                  {item.short}
                </Link>
              </li>
            );
          })}
          <li>
            <button type="button" onClick={() => setOpen(true)} aria-expanded={open} aria-controls="mobile-more"
              className="flex min-h-14 w-full flex-col items-center justify-center gap-0.5 text-[11px] text-muted">
              <Menu aria-hidden="true" className="size-5 text-faint" />
              More
            </button>
          </li>
        </ul>
      </nav>
      {open ? (
        <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label="All sections" id="mobile-more">
          <button type="button" aria-label="Close menu" className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <div className="absolute inset-x-0 bottom-0 rounded-t-2xl border-t border-line bg-surface p-3 pb-[calc(env(safe-area-inset-bottom)+12px)]">
            <div className="mb-2 flex items-center justify-between px-1">
              <span className="text-sm font-semibold">All sections</span>
              <button type="button" onClick={() => setOpen(false)} aria-label="Close" className="rounded-md p-2 text-muted hover:bg-surface-2">
                <X aria-hidden="true" className="size-4" />
              </button>
            </div>
            <ul className="grid grid-cols-2 gap-1">
              {NAV.map((item) => {
                const Icon = ICONS[item.icon];
                return (
                  <li key={item.href}>
                    <Link href={item.href} className={cn("flex items-center gap-2 rounded-lg px-3 py-3 text-[13px] text-muted hover:bg-surface-2",
                      active(path, item.href) && "bg-surface-2 text-fg")}>
                      <Icon aria-hidden="true" className="size-4 text-faint" />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      ) : null}
    </>
  );
}
