"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { buttonClass } from "@/lib/button";
import { callApi } from "./action-button";

/** Owner review: a reasoned note in the audit trail, optionally suppressing further Discord notices for this signal. */
export function SignalReviewForm({ signalId, terminal, suppressed }: { signalId: string; terminal: boolean; suppressed: boolean }) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [suppress, setSuppress] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [, start] = useTransition();
  if (terminal) return <p className="text-xs text-muted">This signal is closed; its record is immutable. Reviews can only be added while it is open.</p>;
  return (
    <form className="space-y-2" onSubmit={async (e) => {
      e.preventDefault();
      if (suppress && !window.confirm("Suppress all further Discord notices for this signal? This is recorded in the audit log.")) return;
      setBusy(true);
      const r = await callApi(`/api/v1/admin/signals/${signalId}/review`, "POST", { note, suppress });
      setBusy(false);
      setMsg({ ok: r.ok, text: r.ok ? (suppress ? `Recorded; ${String(r.data?.suppressed_pending ?? 0)} queued notice(s) suppressed` : "Review note recorded") : r.message });
      if (r.ok) {
        setNote("");
        setSuppress(false);
        start(() => router.refresh());
      }
    }}>
      <label className="block">
        <span className="text-[11px] text-faint">Review note (audit trail)</span>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={280} rows={2} required minLength={3}
          className="mt-1 w-full rounded-lg border border-line-strong bg-surface-2 px-2.5 py-2 text-[13px]" placeholder="Why are you reviewing this signal?" />
      </label>
      <label className="flex items-center gap-2 text-[13px]">
        <input type="checkbox" checked={suppress} disabled={suppressed} onChange={(e) => setSuppress(e.target.checked)} className="size-4 accent-[var(--neg)]" />
        {suppressed ? "Alerts for this signal are already suppressed" : "Also suppress further Discord notices for this signal"}
      </label>
      <div className="flex items-center gap-3">
        <button type="submit" disabled={busy || note.trim().length < 3} className={buttonClass(suppress ? "danger" : "secondary")}>
          {busy ? "Saving…" : suppress ? "Record and suppress" : "Record review"}
        </button>
        <span role="status" aria-live="polite" className={msg?.ok ? "text-[11px] text-pos" : "text-[11px] text-neg"}>{msg?.text}</span>
      </div>
      <p className="text-[11px] text-faint">Reviews never change the probability, tier or lifecycle state; there is no way to force a signal to high conviction.</p>
    </form>
  );
}
