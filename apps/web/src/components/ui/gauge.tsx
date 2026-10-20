import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";

/** Free-tier budget gauge with 70/85/95% warning bands. It never triggers anything; it only warns. */
export function BudgetGauge({ used, budget, label, unit = "min", digits = 0 }: { used: number; budget: number; label: string; unit?: string; digits?: number }) {
  const pct = budget > 0 ? used / budget : 0;
  const level = pct >= 0.95 ? "stop" : pct >= 0.85 ? "high" : pct >= 0.7 ? "warn" : "ok";
  const color = level === "stop" ? "var(--neg)" : level === "high" ? "var(--neg)" : level === "warn" ? "var(--warn)" : "var(--pos)";
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="num text-lg font-semibold">{fmtNum(used, digits)} <span className="text-[12px] font-normal text-muted">/ {fmtNum(budget, 0)} {unit}</span></span>
        <span className={cn("text-[12px] font-medium", level === "ok" ? "text-pos" : level === "warn" ? "text-warn" : "text-neg")}>
          {Math.round(pct * 100)}%{level === "ok" ? "" : level === "warn" ? " · 70% warning" : level === "high" ? " · 85% warning" : " · 95%: optional jobs skipped"}
        </span>
      </div>
      <div className="relative mt-1.5 h-2 overflow-hidden rounded-full bg-surface-3" role="meter" aria-valuemin={0} aria-valuemax={budget} aria-valuenow={used}
        aria-label={`${label}: ${Math.round(pct * 100)}% of budget`}>
        <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.min(100, pct * 100)}%`, background: color }} />
        {[70, 85, 95].map((m) => <span key={m} aria-hidden="true" className="absolute inset-y-0 w-px bg-bg" style={{ left: `${m}%` }} />)}
      </div>
      <div className="mt-1 text-[11px] text-faint">{label}</div>
    </div>
  );
}
