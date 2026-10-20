import "server-only";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

/* XNYS session calendar exported from exchange_calendars (packages/quant/calendar/xnys.json) — the same source the Pi
 * uses, so the dashboard's market phase never disagrees with the scanner about holidays or early closes. */

type Session = [date: string, openUtc: string, closeUtc: string, earlyClose: number];
interface CalendarFile { sessions: Session[]; first_session: string; last_session: string }

export type MarketPhase = "PRE" | "OPEN" | "AFTER" | "CLOSED";

export interface PhaseInfo {
  phase: MarketPhase;
  label: string;
  et_date: string;
  et_time: string;
  session_date: string | null;
  early_close: boolean;
  next_open_utc: string | null;
  calendar_ok: boolean;
}

let cache: { byDate: Map<string, Session>; sessions: Session[]; first: string; last: string } | null = null;

function load() {
  if (cache) return cache;
  const candidates = [
    process.env.APEX_CALENDAR_PATH,
    join(process.cwd(), "../../packages/quant/calendar/xnys.json"),
    join(process.cwd(), "packages/quant/calendar/xnys.json"),
  ].filter((p): p is string => Boolean(p));
  const found = candidates.find((p) => existsSync(p));
  if (!found) return null;
  const cal = JSON.parse(readFileSync(found, "utf8")) as CalendarFile;
  cache = { byDate: new Map(cal.sessions.map((s) => [s[0], s])), sessions: cal.sessions, first: cal.first_session, last: cal.last_session };
  return cache;
}

const PARTS = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});

export function etParts(d: Date): { date: string; time: string } {
  const p = Object.fromEntries(PARTS.formatToParts(d).map((x) => [x.type, x.value]));
  const hour = p.hour === "24" ? "00" : p.hour;
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${hour}:${p.minute}` };
}

const LABEL: Record<MarketPhase, string> = { PRE: "Pre-market", OPEN: "Market open", AFTER: "After hours", CLOSED: "Market closed" };

export function marketPhase(now: Date): PhaseInfo {
  const et = etParts(now);
  const cal = load();
  const base = { et_date: et.date, et_time: et.time };
  if (!cal || et.date < cal.first || et.date > cal.last) {
    return { ...base, phase: "CLOSED", label: "Calendar unavailable", session_date: null, early_close: false, next_open_utc: null, calendar_ok: false };
  }
  const t = now.getTime();
  const nextOpen = () => cal.sessions.find((s) => new Date(s[1]).getTime() > t)?.[1] ?? null;
  const s = cal.byDate.get(et.date);
  if (!s) return { ...base, phase: "CLOSED", label: LABEL.CLOSED, session_date: null, early_close: false, next_open_utc: nextOpen(), calendar_ok: true };
  const open = new Date(s[1]).getTime();
  const close = new Date(s[2]).getTime();
  // extended hours: 04:00 ET (open - 5.5h) to 20:00 ET, or 17:00 on early-close days (close + 4h in both cases)
  const phase: MarketPhase = t < open - 5.5 * 3600_000 ? "CLOSED" : t < open ? "PRE" : t < close ? "OPEN" : t < close + 4 * 3600_000 ? "AFTER" : "CLOSED";
  return { ...base, phase, label: LABEL[phase], session_date: s[0], early_close: Boolean(s[3]), next_open_utc: phase === "OPEN" ? null : nextOpen(),
    calendar_ok: true };
}

/** Completed sessions strictly after `from` up to and including `to` (by ET session date). */
export function sessionsBetween(from: Date | string, to: Date | string): number | null {
  const cal = load();
  if (!cal) return null;
  const a = etParts(new Date(from)).date;
  const b = etParts(new Date(to)).date;
  let n = 0;
  for (const s of cal.sessions) if (s[0] > a && s[0] <= b) n++;
  return n;
}
