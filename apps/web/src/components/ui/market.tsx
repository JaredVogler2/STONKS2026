import { Badge } from "@/components/ui/primitives";
import { fmtPct, humanize } from "@/lib/format";
import type { SectorRow } from "@/server/services/dashboard";

/* Regime and sector visuals. Rule-based regime probabilities are descriptive, not forecasts, and say so. */

export const REGIME_COLOR: Record<string, string> = {
  trending_up: "var(--pos)", rebound: "var(--info)", choppy_neutral: "var(--faint)", risk_off: "var(--warn)", volatility_shock: "var(--neg)",
};
const REGIME_GLYPH: Record<string, string> = { trending_up: "↗", rebound: "↺", choppy_neutral: "↔", risk_off: "↘", volatility_shock: "⚡" };

export function RegimeRibbon({ current, probabilities, history }: {
  current: string | null; probabilities: Record<string, number>; history: { asof_at: string; regime: string }[];
}) {
  const probs = Object.entries(probabilities).sort((a, b) => b[1] - a[1]);
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span aria-hidden="true" className="text-lg" style={{ color: REGIME_COLOR[current ?? ""] ?? "var(--muted)" }}>{REGIME_GLYPH[current ?? ""] ?? "?"}</span>
        <span className="text-base font-semibold">{current ? humanize(current) : "Unknown"}</span>
        <Badge>rule-based estimate</Badge>
      </div>
      <ul className="space-y-1.5" aria-label="Regime state probabilities">
        {probs.map(([k, v]) => (
          <li key={k} className="grid grid-cols-[110px_minmax(0,1fr)_44px] items-center gap-2 text-xs">
            <span className="truncate text-muted">{humanize(k)}</span>
            <span className="h-1.5 overflow-hidden rounded-full bg-surface-3" aria-hidden="true">
              <span className="block h-full rounded-full" style={{ width: `${Math.round(v * 100)}%`, background: REGIME_COLOR[k] ?? "var(--muted)" }} />
            </span>
            <span className="num text-right">{Math.round(v * 100)}%</span>
          </li>
        ))}
      </ul>
      {history.length ? (
        <div>
          <div className="mb-1 text-[11px] text-faint">Last {history.length} sessions (oldest → newest)</div>
          <div className="flex h-5 overflow-hidden rounded" role="img"
            aria-label={`Regime history: ${history.map((h) => humanize(h.regime)).join(", ")}`}>
            {history.map((h) => (
              <span key={h.asof_at} title={`${h.asof_at.slice(0, 10)}: ${humanize(h.regime)}`} className="flex-1 border-r border-bg/60 last:border-0"
                style={{ background: REGIME_COLOR[h.regime] ?? "var(--surface-3)", opacity: 0.75 }} />
            ))}
          </div>
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
            {Object.keys(REGIME_COLOR).map((k) => (
              <span key={k} className="inline-flex items-center gap-1">
                <span aria-hidden="true" className="inline-block size-2 rounded-sm" style={{ background: REGIME_COLOR[k] }} />
                {REGIME_GLYPH[k]} {humanize(k)}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function heat(ret: number | null): string {
  if (ret === null) return "var(--surface-2)";
  const k = Math.min(1, Math.abs(ret) / 0.08);
  const c = ret >= 0 ? "var(--pos)" : "var(--neg)";
  return `color-mix(in oklab, ${c} ${Math.round(8 + k * 32)}%, var(--surface))`;
}

export function SectorHeatmap({ rows, maxPerSector = 2 }: { rows: SectorRow[]; maxPerSector?: number }) {
  return (
    <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
      {rows.map((r) => {
        const crowded = r.open_signals >= maxPerSector;
        return (
          <li key={r.sector} className="rounded-lg border border-line p-2.5" style={{ background: heat(r.ret_20) }}>
            <div className="flex items-start justify-between gap-1">
              <span className="truncate text-xs font-medium">{humanize(r.sector)}</span>
              <span className="num text-xs font-semibold">{r.ret_20 === null ? "n/a" : fmtPct(r.ret_20, 1, true)}</span>
            </div>
            <div className="num mt-1 flex flex-wrap gap-x-2 text-[11px] text-fg/90">
              <span>rank {r.rank_20 === null ? "n/a" : `${Math.round(r.rank_20 * 100)}p`}</span>
              <span>{r.rank_change_20 === null ? "" : `${r.rank_change_20 > 0 ? "▲" : r.rank_change_20 < 0 ? "▼" : "■"} ${Math.abs(Math.round(r.rank_change_20 * 100))}`}</span>
              <span>breadth {r.breadth_above_sma50 === null ? "n/a" : `${Math.round(r.breadth_above_sma50 * 100)}%`}</span>
            </div>
            <div className="mt-1 text-[11px] text-fg/90">
              {r.open_signals} open signal{r.open_signals === 1 ? "" : "s"}
              {crowded ? <span className="ml-1 font-medium text-warn">· crowded</span> : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
