"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { cn } from "@/lib/cn";
import { callApi } from "./action-button";

export interface ToggleRow { id: string; label: string; defaultEnabled: boolean; status: string | null }

/** Strategy on/off switches. Saved as the versioned `strategies.enabled` setting; the Pi applies it at the next scan. */
export function StrategyToggles({ rows, overrides, version }: { rows: ToggleRow[]; overrides: Record<string, boolean>; version: number }) {
  const router = useRouter();
  const [state, setState] = useState(overrides);
  const [ver, setVer] = useState(version);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [, start] = useTransition();
  const toggle = async (id: string, next: boolean) => {
    setBusy(id);
    const value = { ...state, [id]: next };
    const r = await callApi("/api/v1/admin/settings", "PUT", { key: "strategies.enabled", value, version: ver });
    setBusy(null);
    if (r.ok) {
      setState(value);
      setVer(Number(r.data?.version ?? ver + 1));
      setMsg({ ok: true, text: `Saved (version ${String(r.data?.version ?? "?")}); applies at the Pi's next scan` });
      start(() => router.refresh());
    } else setMsg({ ok: false, text: r.message });
  };
  return (
    <div>
      <ul className="divide-y divide-line">
        {rows.map((r) => {
          const on = state[r.id] ?? r.defaultEnabled;
          return (
            <li key={r.id} className="flex items-center justify-between gap-3 py-2">
              <div className="min-w-0">
                <div className="truncate text-[13px]">{r.label}</div>
                <div className="text-[11px] text-faint">{r.status ? `evidence: ${r.status.toLowerCase()}` : "no backtest yet"}{state[r.id] === undefined ? " · default" : " · overridden"}</div>
              </div>
              <button type="button" role="switch" aria-checked={on} aria-label={`${r.label}: ${on ? "enabled" : "disabled"}`} disabled={busy !== null}
                onClick={() => toggle(r.id, !on)}
                className={cn("relative h-6 w-11 shrink-0 rounded-full border transition-colors disabled:opacity-50", on ? "border-pos/60 bg-pos/30" : "border-line-strong bg-surface-3")}>
                <span aria-hidden="true" className={cn("absolute left-0 top-0.5 size-4.5 rounded-full bg-fg transition-transform", on ? "translate-x-5.5" : "translate-x-0.5")} />
              </button>
            </li>
          );
        })}
      </ul>
      <p role="status" aria-live="polite" className={cn("mt-2 text-[11px]", msg?.ok ? "text-pos" : "text-neg")}>{msg?.text}</p>
      <p className="text-[11px] text-faint">Turning a strategy on does not make it validated; it still publishes only research setups until a calibrated model passes the gates.</p>
    </div>
  );
}
