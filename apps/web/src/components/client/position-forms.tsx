"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { buttonClass } from "@/lib/button";
import { callApi } from "./action-button";

/* Self-reported position entry. Every submit carries a client idempotency key generated once per form instance, so a
 * double click or retry is recorded once. Values are what YOU report; APEX never sees a brokerage account. */

type Mode = "OPEN" | "ADD" | "PARTIAL_EXIT" | "CLOSE" | "CORRECT" | "WATCH" | "UNWATCH";

const LABEL: Record<Mode, string> = {
  OPEN: "Record a buy (new position)", ADD: "Record an added buy", PARTIAL_EXIT: "Record a partial sell", CLOSE: "Record selling everything",
  CORRECT: "Correct a mistaken entry", WATCH: "Watch a symbol", UNWATCH: "Stop watching",
};

const DEC = /^(0|[1-9][0-9]{0,15})(\.[0-9]{1,10})?$/;

function newKey() {
  return `dash-${crypto.randomUUID()}`;
}

function toUtc(local: string): string | null {
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? null : d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function localInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export interface CorrectableEvent { id: string; label: string }

export function PositionEventForm({ modes, positionId, symbol, remainingQty, nowIso, events = [], onDoneHref }: {
  modes: Mode[]; positionId?: string; symbol?: string; remainingQty?: string; nowIso: string; events?: CorrectableEvent[]; onDoneHref?: string;
}) {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>(modes[0]!);
  const [key, setKey] = useState(newKey);
  const [sym, setSym] = useState(symbol ?? "");
  const [signalId, setSignalId] = useState("");
  const [price, setPrice] = useState("");
  const [qty, setQty] = useState(mode === "CLOSE" ? (remainingQty ?? "") : "");
  const [fees, setFees] = useState("0");
  const [at, setAt] = useState(localInput(nowIso));
  const [notes, setNotes] = useState("");
  const [target, setTarget] = useState(events[0]?.id ?? "");
  const [voidIt, setVoidIt] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [, start] = useTransition();

  const needsFill = ["OPEN", "ADD", "PARTIAL_EXIT", "CLOSE"].includes(mode) || (mode === "CORRECT" && !voidIt);
  const errors = useMemo(() => {
    const e: string[] = [];
    if ((mode === "OPEN" || mode === "WATCH") && !positionId && !/^[A-Z][A-Z0-9.]{0,9}$/.test(sym)) e.push("Symbol: letters/digits, e.g. DMTEC1");
    if (needsFill) {
      if (!DEC.test(price) || Number(price) <= 0) e.push("Price must be a positive number (no commas)");
      if (!DEC.test(qty) || Number(qty) <= 0) e.push("Quantity must be a positive number");
      if (!DEC.test(fees)) e.push("Fees must be 0 or more");
      const t = toUtc(at);
      if (!t) e.push("Fill time is required");
      else if (new Date(t).getTime() > new Date(nowIso).getTime() + 5 * 60_000) e.push("Fill time cannot be in the future");
    }
    if (mode === "CORRECT" && !target) e.push("Choose the entry to correct");
    return e;
  }, [mode, sym, needsFill, price, qty, fees, at, target, positionId, nowIso]);

  const submit = async () => {
    if (errors.length) return;
    const fill = needsFill ? { price, quantity: qty, fees, fill_at: toUtc(at)! } : undefined;
    const body: Record<string, unknown> = { client_idempotency_key: key, event_type: mode === "CLOSE" ? "CLOSE" : mode };
    if (positionId) body.position_id = positionId;
    if (!positionId && sym) body.symbol = sym;
    if (!positionId && signalId) body.signal_id = signalId;
    if (notes.trim()) body.notes = notes.trim().slice(0, 280);
    if (mode === "CORRECT") body.correction = { target_event_id: target, void: voidIt, ...(voidIt ? {} : { replacement: fill }), reason: notes.trim().slice(0, 200) || "owner correction" };
    else if (fill) body.fill = fill;
    if (mode === "CLOSE" && !window.confirm(`Record selling all ${qty} shares at ${price}? This closes the position (it can be corrected later).`)) return;
    setBusy(true);
    const r = await callApi("/api/v1/admin/positions", "POST", body);
    setBusy(false);
    const warnings = ((r.data?.warnings as string[] | undefined) ?? []).join("; ");
    setMsg({ ok: r.ok, text: r.ok ? (r.data?.status === "duplicate" ? "Already recorded (duplicate submit ignored)" : `Saved.${warnings ? ` Note: ${warnings}` : ""}`) : r.message });
    if (r.ok) {
      setKey(newKey());
      setPrice("");
      setNotes("");
      const pos = r.data?.position as { position_id?: string } | undefined;
      start(() => {
        if (onDoneHref && pos?.position_id && !positionId) router.push(`${onDoneHref}/${pos.position_id}`);
        else router.refresh();
      });
    }
  };

  const field = "mt-1 min-h-9 w-full rounded-lg border border-line-strong bg-surface-2 px-2.5 text-[13px]";
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); void submit(); }} aria-describedby="pos-form-errors">
      {modes.length > 1 ? (
        <div role="radiogroup" aria-label="Action" className="flex flex-wrap gap-1.5">
          {modes.map((m) => (
            <button key={m} type="button" role="radio" aria-checked={mode === m}
              onClick={() => { setMode(m); setKey(newKey()); setMsg(null); if (m === "CLOSE") setQty(remainingQty ?? ""); }}
              className={buttonClass(mode === m ? "primary" : "ghost", "min-h-8 px-2.5 text-[12px]")}>{LABEL[m]}</button>
          ))}
        </div>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {!positionId && (mode === "OPEN" || mode === "WATCH") ? (
          <>
            <label className="block"><span className="text-[11px] text-faint">Symbol</span>
              <input className={field} value={sym} onChange={(e) => setSym(e.target.value.toUpperCase().trim())} autoCapitalize="characters" autoComplete="off" required /></label>
            <label className="block"><span className="text-[11px] text-faint">APEX signal id (optional)</span>
              <input className={field} value={signalId} onChange={(e) => setSignalId(e.target.value.trim())} placeholder="sig_…" autoComplete="off" /></label>
          </>
        ) : null}
        {mode === "CORRECT" ? (
          <>
            <label className="block sm:col-span-2"><span className="text-[11px] text-faint">Entry to correct</span>
              <select className={field} value={target} onChange={(e) => setTarget(e.target.value)}>
                {events.map((ev) => <option key={ev.id} value={ev.id}>{ev.label}</option>)}
              </select></label>
            <label className="flex items-end gap-2 pb-2 text-[13px]">
              <input type="checkbox" checked={voidIt} onChange={(e) => setVoidIt(e.target.checked)} className="size-4" /> Remove it entirely (void)
            </label>
          </>
        ) : null}
        {needsFill ? (
          <>
            <label className="block"><span className="text-[11px] text-faint">Price per share</span>
              <input className={field} value={price} onChange={(e) => setPrice(e.target.value.trim())} inputMode="decimal" autoComplete="off" required /></label>
            <label className="block"><span className="text-[11px] text-faint">Quantity{mode === "CLOSE" ? " (all remaining)" : ""}</span>
              <input className={field} value={qty} onChange={(e) => setQty(e.target.value.trim())} inputMode="decimal" autoComplete="off" required readOnly={mode === "CLOSE"} /></label>
            <label className="block"><span className="text-[11px] text-faint">Fees</span>
              <input className={field} value={fees} onChange={(e) => setFees(e.target.value.trim())} inputMode="decimal" autoComplete="off" /></label>
            <label className="block"><span className="text-[11px] text-faint">Fill time (your local time)</span>
              <input className={field} type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} required /></label>
          </>
        ) : null}
        <label className="block sm:col-span-2"><span className="text-[11px] text-faint">Note (optional, private)</span>
          <input className={field} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={280} autoComplete="off" /></label>
      </div>
      {errors.length && (price || qty || sym) ? (
        <ul id="pos-form-errors" className="list-disc pl-5 text-[12px] text-warn">{errors.map((e) => <li key={e}>{e}</li>)}</ul>
      ) : <span id="pos-form-errors" />}
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" disabled={busy || errors.length > 0} className={buttonClass(mode === "CLOSE" ? "danger" : "primary")}>
          {busy ? "Saving…" : LABEL[mode]}
        </button>
        <span role="status" aria-live="polite" className={msg?.ok ? "text-[12px] text-pos" : "text-[12px] text-neg"}>{msg?.text}</span>
      </div>
      <p className="text-[11px] text-faint">Self-reported: APEX records what you enter and never verifies it against a brokerage. Shares are FIFO lots; corrections keep the original entry in history.</p>
    </form>
  );
}
