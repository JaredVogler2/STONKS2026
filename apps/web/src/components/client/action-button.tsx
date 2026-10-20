"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition, type ReactNode } from "react";
import { buttonClass, type Variant } from "@/lib/button";
import { cn } from "@/lib/cn";

/* Every mutating control goes through this: same-origin fetch with the CSRF header, an explicit busy state, and the
 * server's real success/failure message (never an optimistic fake success). */

export { buttonClass, type Variant } from "@/lib/button";

export interface ApiResult { ok: boolean; status: number; data: Record<string, unknown> | null; message: string }

export async function callApi(url: string, method: string, body?: unknown): Promise<ApiResult> {
  try {
    const res = await fetch(url, {
      method, credentials: "same-origin", cache: "no-store",
      headers: { "x-apex-csrf": "1", ...(body !== undefined ? { "content-type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let data: Record<string, unknown> | null = null;
    try {
      data = text ? (JSON.parse(text) as Record<string, unknown>) : null;
    } catch {
      data = null;
    }
    const err = (data?.error as { code?: string; message?: string; issues?: string[] } | undefined) ?? null;
    const issues = err?.issues?.length ? `: ${err.issues.join("; ")}` : "";
    return { ok: res.ok, status: res.status, data,
      message: err ? `${err.message ?? "request failed"}${issues} (${err.code ?? res.status})` : res.ok ? "Done" : `HTTP ${res.status}` };
  } catch (e) {
    return { ok: false, status: 0, data: null, message: e instanceof Error ? `network error: ${e.message}` : "network error" };
  }
}

export function ActionButton({ url, method = "POST", body, label, busyLabel, confirm, variant = "secondary", successMessage, className, disabled,
  disabledReason, icon, duplicateMessage }: {
  url: string; method?: string; body?: unknown; label: ReactNode; busyLabel?: string; confirm?: string; variant?: Variant; successMessage?: string;
  className?: string; disabled?: boolean; disabledReason?: string; icon?: ReactNode; duplicateMessage?: string;
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ApiResult | null>(null);
  const run = async () => {
    if (confirm && !window.confirm(confirm)) return;
    setBusy(true);
    const r = await callApi(url, method, body);
    setBusy(false);
    setResult(r);
    if (r.ok) start(() => router.refresh());
  };
  const shown = !result ? null : !result.ok ? result.message
    : result.data?.status === "duplicate" && duplicateMessage ? duplicateMessage : (successMessage ?? result.message);
  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button type="button" onClick={run} disabled={disabled || busy || pending} className={buttonClass(variant, className)}
        title={disabled ? disabledReason : undefined} aria-describedby={disabled && disabledReason ? undefined : undefined}>
        {icon}
        {busy ? (busyLabel ?? "Working…") : label}
      </button>
      {disabled && disabledReason ? <span className="text-[11px] text-faint">{disabledReason}</span> : null}
      <span role="status" aria-live="polite" className={cn("text-[11px]", result?.ok ? "text-pos" : "text-neg")}>{shown}</span>
    </span>
  );
}
