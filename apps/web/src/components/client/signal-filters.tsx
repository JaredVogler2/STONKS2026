"use client";

import { RotateCcw } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTransition } from "react";
import { buttonClass } from "./action-button";

/* Filters live in the URL (shareable, back-button friendly) and are remembered in a cookie so returning to the
 * explorer restores them. Every filter is applied server-side to the real query. */

export interface FilterOption { value: string; label: string }

const FIELDS: { key: string; label: string; options: FilterOption[] }[] = [
  { key: "mode", label: "Data", options: [{ value: "", label: "Live + demo" }, { value: "LIVE", label: "Live only" }, { value: "DEMO", label: "Demo only" }] },
  { key: "state", label: "Status", options: [{ value: "", label: "Any status" }, { value: "PUBLISHED,ACTIVE", label: "Open (published/active)" },
    { value: "PUBLISHED", label: "Published" }, { value: "ACTIVE", label: "Active" }, { value: "CLOSED", label: "Closed" }, { value: "EXPIRED", label: "Expired" },
    { value: "INVALIDATED", label: "Invalidated" }] },
  { key: "confidence", label: "Probability", options: [{ value: "", label: "Any" }, { value: "CALIBRATED", label: "Calibrated only" },
    { value: "EXPERIMENTAL", label: "Experimental" }, { value: "UNVALIDATED", label: "Unvalidated" }] },
  { key: "tier", label: "Tier", options: [{ value: "", label: "Any tier" }, { value: "HIGH_CONVICTION", label: "High conviction" }, { value: "RESEARCH_SETUP", label: "Research setup" }] },
  { key: "min_quality", label: "Min quality", options: [{ value: "", label: "Any" }, ...[60, 70, 75, 80, 85, 90].map((v) => ({ value: String(v), label: `≥ ${v}` }))] },
  { key: "max_horizon", label: "Horizon", options: [{ value: "", label: "Any" }, { value: "5", label: "≤ 5 sessions" }, { value: "10", label: "≤ 10 sessions" }, { value: "20", label: "≤ 20 sessions" }] },
  { key: "window", label: "Session", options: [{ value: "", label: "All time" }, { value: "1d", label: "Last session" }, { value: "5d", label: "Last 5 sessions" },
    { value: "20d", label: "Last 20 sessions" }, { value: "60d", label: "Last 60 sessions" }] },
  { key: "catalyst", label: "Catalysts", options: [{ value: "", label: "Any" }, { value: "with", label: "With news/filing" }, { value: "without", label: "No catalyst" }] },
  { key: "tradability", label: "Robinhood check", options: [{ value: "", label: "Any" }, { value: "user_verified", label: "Verified by you" },
    { value: "likely_eligible", label: "Likely eligible" }, { value: "unknown", label: "Unknown" }] },
];

function remember(qs: string) {
  document.cookie = `apex_sig_filters=${encodeURIComponent(qs)}; Path=/; Max-Age=2592000; SameSite=Strict${location.protocol === "https:" ? "; Secure" : ""}`;
}

export function SignalFilters({ strategies, sectors }: { strategies: FilterOption[]; sectors: FilterOption[] }) {
  const router = useRouter();
  const path = usePathname();
  const sp = useSearchParams();
  const [pending, start] = useTransition();
  const fields = [
    ...FIELDS.slice(0, 4),
    { key: "strategy", label: "Strategy", options: [{ value: "", label: "Any strategy" }, ...strategies] },
    { key: "sector", label: "Sector", options: [{ value: "", label: "Any sector" }, ...sectors] },
    ...FIELDS.slice(4),
  ];
  const apply = (next: URLSearchParams) => {
    next.delete("page");
    const qs = next.toString();
    remember(qs);
    start(() => router.push(qs ? `${path}?${qs}` : `${path}?reset=1`));
  };
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(sp.toString());
    next.delete("reset");
    if (value) next.set(key, value);
    else next.delete(key);
    apply(next);
  };
  return (
    <form role="search" aria-label="Filter signals" className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" onSubmit={(e) => {
      e.preventDefault();
      const q = (new FormData(e.currentTarget).get("q") as string | null) ?? "";
      set("q", q.trim());
    }}>
      <label className="col-span-2 flex flex-col gap-1 sm:col-span-1">
        <span className="text-[11px] text-faint">Symbol</span>
        <input name="q" defaultValue={sp.get("q") ?? ""} placeholder="e.g. DMTEC" autoComplete="off"
          className="min-h-9 rounded-lg border border-line-strong bg-surface-2 px-2.5 text-[13px] placeholder:text-faint" />
      </label>
      {fields.map((f) => (
        <label key={f.key} className="flex min-w-0 flex-col gap-1">
          <span className="text-[11px] text-faint">{f.label}</span>
          <select value={sp.get(f.key) ?? ""} onChange={(e) => set(f.key, e.target.value)}
            className="min-h-9 min-w-0 rounded-lg border border-line-strong bg-surface-2 px-2 text-[13px]">
            {f.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      ))}
      <div className="flex items-end gap-2">
        <button type="button" className={buttonClass("ghost")} onClick={() => apply(new URLSearchParams())} disabled={pending}>
          <RotateCcw aria-hidden="true" className="size-4" /> Reset
        </button>
        <span role="status" aria-live="polite" className="text-[11px] text-faint">{pending ? "Updating…" : ""}</span>
      </div>
    </form>
  );
}
