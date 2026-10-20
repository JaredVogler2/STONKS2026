"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { buttonClass } from "@/lib/button";
import { cn } from "@/lib/cn";
import { callApi } from "./action-button";

/* Settings forms. Secrets are write-only: inputs start empty, are never prefilled, and the server returns only a hint. */

type Msg = { ok: boolean; text: string } | null;

function Status({ msg }: { msg: Msg }) {
  return <span role="status" aria-live="polite" className={cn("text-[12px]", msg?.ok ? "text-pos" : "text-neg")}>{msg?.text}</span>;
}

const input = "mt-1 min-h-9 w-full rounded-lg border border-line-strong bg-surface-2 px-2.5 text-[13px]";

export interface IntegrationField { key: string; label: string; secret: boolean; help: string; configured: boolean; hint: string | null }

export function IntegrationForm({ provider, fields, testable, status }: { provider: string; fields: IntegrationField[]; testable: boolean; status: string }) {
  const router = useRouter();
  const [vals, setVals] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [, start] = useTransition();
  const run = async (what: string, url: string, method: string, body?: unknown, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(what);
    const r = await callApi(url, method, body);
    setBusy(null);
    setMsg({ ok: r.ok, text: r.ok ? (what === "test" ? `${String(r.data?.status ?? "")}: ${String(r.data?.detail ?? "")}` : `${what} done`) : r.message });
    if (r.ok) {
      if (what === "save") setVals({});
      start(() => router.refresh());
    }
  };
  const anyConfigured = fields.some((f) => f.configured);
  return (
    <form className="space-y-3" autoComplete="off" onSubmit={(e) => { e.preventDefault(); void run("save", `/api/v1/admin/settings/${provider}`, "PUT", { values: vals }); }}>
      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((f) => (
          <label key={f.key} className="block">
            <span className="flex items-center justify-between text-[11px] text-faint">
              <span>{f.label}</span>
              <span>{f.configured ? (f.secret ? `stored ${f.hint ?? "(hidden)"}` : `current: ${f.hint}`) : "not set"}</span>
            </span>
            <input className={input} type={f.secret ? "password" : "text"} value={vals[f.key] ?? ""} name={`${provider}-${f.key}`}
              onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} autoComplete={f.secret ? "new-password" : "off"} spellCheck={false}
              placeholder={f.configured ? (f.secret ? "enter a new value to rotate" : "enter to change") : ""} />
            <span className="mt-1 block text-[11px] text-faint">{f.help}</span>
          </label>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button type="submit" className={buttonClass("primary")} disabled={busy !== null || !Object.values(vals).some(Boolean)}>{busy === "save" ? "Saving…" : anyConfigured ? "Save / rotate" : "Save"}</button>
        {testable ? <button type="button" className={buttonClass("secondary")} disabled={busy !== null || !anyConfigured}
          onClick={() => run("test", `/api/v1/admin/settings/${provider}/test`, "POST")}>{busy === "test" ? "Testing…" : "Test connection"}</button> : null}
        {anyConfigured && status !== "DISABLED" ? <button type="button" className={buttonClass("ghost")} disabled={busy !== null}
          onClick={() => run("disable", `/api/v1/admin/settings/${provider}?mode=disable`, "DELETE", undefined, "Disable this integration? Stored values are kept but not used.")}>Disable</button> : null}
        {anyConfigured ? <button type="button" className={buttonClass("danger")} disabled={busy !== null}
          onClick={() => run("delete", `/api/v1/admin/settings/${provider}`, "DELETE", undefined, "Delete the stored values permanently?")}>Delete</button> : null}
        <Status msg={msg} />
      </div>
    </form>
  );
}

export interface FieldSpec {
  path: string; label: string; type: "number" | "boolean" | "text" | "select" | "symbols" | "snowflake";
  min?: number; max?: number; step?: number; options?: { value: string; label: string }[]; help?: string;
}

function getPath(o: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((a, k) => (a && typeof a === "object" ? (a as Record<string, unknown>)[k] : undefined), o);
}
function setPath(o: Record<string, unknown>, path: string, v: unknown): Record<string, unknown> {
  const keys = path.split(".");
  const out = structuredClone(o);
  let cur: Record<string, unknown> = out;
  keys.slice(0, -1).forEach((k) => { cur = (cur[k] ??= {}) as Record<string, unknown>; });
  cur[keys.at(-1)!] = v;
  return out;
}

/** Versioned setting editor: the server validates (zod) and rejects stale versions, so two tabs cannot silently overwrite. */
export function SettingEditor({ settingKey, value, version, fields, note }: { settingKey: string; value: Record<string, unknown>; version: number; fields: FieldSpec[]; note?: string }) {
  const router = useRouter();
  const [draft, setDraft] = useState(value);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  const [, start] = useTransition();
  const dirty = JSON.stringify(draft) !== JSON.stringify(value);
  return (
    <form className="space-y-3" onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true);
      const r = await callApi("/api/v1/admin/settings", "PUT", { key: settingKey, value: draft, version });
      setBusy(false);
      setMsg({ ok: r.ok, text: r.ok ? `Saved as version ${String(r.data?.version)}` : r.message });
      if (r.ok) start(() => router.refresh());
    }}>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {fields.map((f) => {
          const v = getPath(draft, f.path);
          const id = `${settingKey}-${f.path}`;
          return (
            <div key={f.path} className={cn(f.type === "symbols" && "sm:col-span-2 lg:col-span-3")}>
              {f.type === "boolean" ? (
                <label className="flex min-h-9 items-center gap-2 text-[13px]" htmlFor={id}>
                  <input id={id} type="checkbox" checked={Boolean(v)} onChange={(e) => setDraft(setPath(draft, f.path, e.target.checked))} className="size-4" />
                  {f.label}
                </label>
              ) : (
                <label className="block" htmlFor={id}>
                  <span className="text-[11px] text-faint">{f.label}</span>
                  {f.type === "select" ? (
                    <select id={id} className={input} value={String(v ?? "")} onChange={(e) => setDraft(setPath(draft, f.path, /^\d+$/.test(e.target.value) ? Number(e.target.value) : e.target.value))}>
                      {f.options!.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  ) : f.type === "symbols" ? (
                    <input id={id} className={input} value={((v as string[]) ?? []).join(", ")} spellCheck={false}
                      onChange={(e) => setDraft(setPath(draft, f.path, e.target.value.toUpperCase().split(/[\s,]+/).filter(Boolean)))} />
                  ) : f.type === "snowflake" ? (
                    <input id={id} className={input} value={String(v ?? "")} inputMode="numeric" spellCheck={false}
                      onChange={(e) => setDraft(setPath(draft, f.path, e.target.value.trim() === "" ? null : e.target.value.trim()))} placeholder="not mapped" />
                  ) : (
                    <input id={id} className={input} type={f.type === "number" ? "number" : "text"} min={f.min} max={f.max} step={f.step ?? "any"} value={String(v ?? "")}
                      onChange={(e) => setDraft(setPath(draft, f.path, f.type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value))} />
                  )}
                </label>
              )}
              {f.help ? <span className="mt-1 block text-[11px] text-faint">{f.help}</span> : null}
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className={buttonClass("primary")} disabled={busy || !dirty}>{busy ? "Saving…" : "Save"}</button>
        {dirty ? <button type="button" className={buttonClass("ghost")} onClick={() => setDraft(value)}>Discard changes</button> : null}
        <Status msg={msg} />
        <span className="text-[11px] text-faint">version {version}{note ? ` · ${note}` : ""}</span>
      </div>
    </form>
  );
}

export function ComplianceSwitch({ flag, enabled, ackPhrase, isDemo }: { flag: string; enabled: boolean; ackPhrase: string; isDemo: boolean }) {
  const router = useRouter();
  const [ack, setAck] = useState("");
  const [ref, setRef] = useState("");
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  const [, start] = useTransition();
  const submit = async (next: boolean) => {
    setBusy(true);
    const r = await callApi("/api/v1/admin/compliance", "POST", { flag, enabled: next, acknowledgement: next ? ack : null, reference: next ? ref : null });
    setBusy(false);
    setMsg({ ok: r.ok, text: r.ok ? (next ? "Enabled and recorded" : "Disabled") : r.message });
    if (r.ok) start(() => router.refresh());
  };
  if (enabled) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className={buttonClass("danger")} disabled={busy} onClick={() => submit(false)}>Turn off</button>
        <Status msg={msg} />
      </div>
    );
  }
  return (
    <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); void submit(true); }}>
      <label className="block"><span className="text-[11px] text-faint">Type exactly: “{ackPhrase}”</span>
        <input className={input} value={ack} onChange={(e) => setAck(e.target.value)} disabled={isDemo} /></label>
      <label className="block"><span className="text-[11px] text-faint">External review reference (who reviewed, when, document id)</span>
        <input className={input} value={ref} onChange={(e) => setRef(e.target.value)} maxLength={200} disabled={isDemo} /></label>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className={buttonClass("secondary")} disabled={busy || isDemo || ack !== ackPhrase || ref.trim().length < 8}>Enable</button>
        <Status msg={msg} />
        {isDemo ? <span className="text-[11px] text-faint">Cannot be enabled in DEMO mode.</span> : null}
      </div>
    </form>
  );
}

export function PairingStarter({ serverUrl }: { serverUrl: string }) {
  const [code, setCode] = useState<{ code: string; expires_at: string } | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="space-y-3">
      <button type="button" className={buttonClass("primary")} disabled={busy} onClick={async () => {
        setBusy(true);
        const r = await callApi("/api/v1/admin/pairing", "POST", {});
        setBusy(false);
        if (r.ok) setCode(r.data as { code: string; expires_at: string });
        setMsg(r.ok ? null : { ok: false, text: r.message });
      }}>{busy ? "Creating…" : code ? "Create a new code" : "Start pairing"}</button>
      <Status msg={msg} />
      {code ? (
        <div className="rounded-lg border border-info/40 bg-info/10 p-3">
          <div className="text-[11px] text-faint">One-time code (expires {new Date(code.expires_at).toLocaleTimeString()})</div>
          <div className="num mt-1 select-all text-2xl font-semibold tracking-[0.2em]">{code.code}</div>
          <div className="mt-2 text-[12px] text-muted">On the Pi, run:</div>
          <pre className="mt-1 overflow-x-auto rounded bg-bg px-2 py-1.5 text-[12px]"><code>uv run apex pair {code.code} --server {serverUrl}</code></pre>
          <div className="mt-2 text-[11px] text-faint">Then compare the fingerprint the Pi prints with the one listed below before approving. The code works once.</div>
        </div>
      ) : null}
    </div>
  );
}
