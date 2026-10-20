/* Display formatting shared by server and client components. Pure functions only. */

export function ageSeconds(now: Date | string, then: Date | string | null | undefined): number | null {
  if (!then) return null;
  return Math.max(0, Math.floor((new Date(now).getTime() - new Date(then).getTime()) / 1000));
}

export function fmtAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "n/a";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

const ET = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", hour12: true,
});
const ET_DATE = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric" });

/** e.g. "Oct 5, 4:15 PM ET" */
export function fmtEt(v: Date | string | null | undefined): string {
  if (!v) return "n/a";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? "n/a" : `${ET.format(d)} ET`;
}

export function fmtEtDate(v: Date | string | null | undefined): string {
  if (!v) return "n/a";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? "n/a" : ET_DATE.format(d);
}

export function fmtNum(v: unknown, digits = 2): string {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "n/a";
}

export function fmtPct(v: unknown, digits = 1, signed = false): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "n/a";
  const s = (n * 100).toFixed(digits);
  return `${signed && n > 0 ? "+" : ""}${s}%`;
}

export function fmtSigned(v: unknown, digits = 2): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "n/a";
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}`;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function humanize(code: string | null | undefined): string {
  if (!code) return "n/a";
  return code.replace(/^h\d+_/, "").replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

export const STRATEGY_LABELS: Record<string, string> = {
  h1_vcp_breakout: "H1 Volatility contraction breakout",
  h2_rs_pullback: "H2 Relative-strength pullback",
  h3_post_event_drift: "H3 Post-event drift",
  h4_oversold_recovery: "H4 Oversold recovery in uptrend",
  h5_sector_rotation: "H5 Sector rotation leadership",
  h6_gap_and_hold: "H6 Gap and hold",
  h7_catalyst_compression: "H7 Catalyst + compression concurrence",
  h8_turn_of_month: "H8 Turn-of-month seasonality (weak prior)",
  redteam_planted_alpha: "Red-team planted alpha (capability control)",
};

export function strategyLabel(id: string | null | undefined): string {
  if (!id) return "n/a";
  return STRATEGY_LABELS[id] ?? humanize(id);
}
