import Link from "next/link";
import { ActionButton } from "@/components/client/action-button";
import { DiscordPreview } from "@/components/ui/discord-preview";
import { Badge, Card, EmptyState, KeyValue, Notice, PageHeader } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import { fmtEt, humanize } from "@/lib/format";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { deliveryOverview } from "@/server/services/dashboard";
import { getSettings } from "@/server/services/settings";

export const dynamic = "force-dynamic";
export const metadata = { title: "Alerts & Delivery" };

type Json = Record<string, unknown>;
const KINDS = ["", "NEW_SIGNAL", "SIGNAL_UPDATE", "SIGNAL_CLOSED", "MARKET_INTEL", "PERFORMANCE_REPORT", "SYSTEM_ALERT"];
const STATES = ["", "sent", "pending", "leased", "dead_letter", "suppressed"];

export default async function AlertsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  await pageOwner();
  const sp = await searchParams;
  const kind = typeof sp.kind === "string" && KINDS.includes(sp.kind) ? sp.kind : "";
  const state = typeof sp.state === "string" && STATES.includes(sp.state) ? sp.state : "";
  const ctx = await appContext();
  const [d, settings] = await Promise.all([deliveryOverview(ctx.db, { kind: kind || undefined, state: state || undefined, limit: 40 }), getSettings(ctx.db)]);
  const ks = settings["alerts.kill_switch"];
  const notices = d.notices as Json[];
  const byState = d.by_state as { route: string; state: string; n: number }[];
  const dm = byState.filter((r) => r.route === "DM");
  const ch = byState.filter((r) => r.route === "CHANNEL");
  const link = (patch: Record<string, string>) => {
    const p = new URLSearchParams({ ...(kind ? { kind } : {}), ...(state ? { state } : {}), ...patch });
    for (const [k, v] of [...p.entries()]) if (!v) p.delete(k);
    return `/alerts${p.toString() ? `?${p}` : ""}`;
  };

  return (
    <>
      <PageHeader title="Alerts & Delivery"
        subtitle="Every shared-channel notice exactly as the bot posted it, plus delivery health. Private position updates are DMs to their member and are counted here, never displayed." />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Stop all alerts" definition="Fail-closed switch. While on, the server hands no investment notice to the bot; system alerts still go out. Audited and versioned."
          interval={`v${ks.version}`}>
          {ks.value.stopped ? (
            <>
              <Notice tone="neg" title="Alerts are STOPPED">{ks.value.reason || "No reason recorded."}</Notice>
              <div className="mt-3"><ActionButton url="/api/v1/admin/settings" method="PUT" label="Resume alerts" variant="primary"
                body={{ key: "alerts.kill_switch", value: { stopped: false, reason: "" }, version: ks.version }} successMessage="Alerts resumed" /></div>
            </>
          ) : (
            <ActionButton url="/api/v1/admin/settings" method="PUT" label="STOP all investment alerts" variant="danger"
              body={{ key: "alerts.kill_switch", value: { stopped: true, reason: "owner stop from dashboard" }, version: ks.version }}
              confirm="Stop every new-signal, update and intel notice until you resume? Queued notices stay queued." successMessage="Alerts stopped" />
          )}
        </Card>
        <Card title="Shared channels" definition="Notice counts by delivery state.">
          <KeyValue items={ch.map((r) => [humanize(r.state), String(r.n)] as [string, string])} />
          {!ch.length ? <p className="text-[13px] text-muted">Nothing queued yet.</p> : null}
        </Card>
        <Card title="Private updates (DMs)" definition="Counts only. Held = the member has DMs off; shown privately in their next bot reply, never posted publicly.">
          <KeyValue items={dm.map((r) => [r.state === "deferred" ? "Held privately" : humanize(r.state), String(r.n)] as [string, string])} />
          {!dm.length ? <p className="text-[13px] text-muted">No private updates yet.</p> : null}
        </Card>
      </div>

      <Card title="Delivery attempts" definition="Outcomes reported by the bot for each attempt (429s honour Discord's retry-after).">
        {(d.attempts as Json[]).length ? (
          <div className="flex flex-wrap gap-2">{(d.attempts as Json[]).map((a) => <Badge key={String(a.status)} tone={a.status === "delivered" ? "pos" : a.status === "dm_blocked" ? "neutral" : "warn"}>{humanize(String(a.status))}: {String(a.n)}{a.last_http ? ` (HTTP ${String(a.last_http)})` : ""}</Badge>)}</div>
        ) : <p className="text-[13px] text-muted">No attempts recorded{ctx.isDemo ? " (the DEMO replay records acknowledgements only)" : ""}.</p>}
      </Card>

      <Card title="Channel notices" definition="Newest first. Rendered content is stored only for shared channels."
        actions={
          <div className="flex flex-wrap gap-1">
            {KINDS.map((k) => <Link key={k || "all"} href={link({ kind: k })} aria-current={kind === k ? "page" : undefined}
              className={cn("rounded-md border px-2 py-1 text-[11px]", kind === k ? "border-info/50 bg-info/15 text-fg" : "border-line text-muted hover:text-fg")}>{k ? humanize(k) : "All kinds"}</Link>)}
            <span className="mx-1 w-px bg-line" aria-hidden="true" />
            {STATES.map((s) => <Link key={s || "any"} href={link({ state: s })} aria-current={state === s ? "page" : undefined}
              className={cn("rounded-md border px-2 py-1 text-[11px]", state === s ? "border-info/50 bg-info/15 text-fg" : "border-line text-muted hover:text-fg")}>{s ? humanize(s) : "Any state"}</Link>)}
          </div>
        }>
        {notices.length ? (
          <ul className="divide-y divide-line">
            {notices.map((n, i) => (
              <li key={String(n.id)} className="py-1">
                <details open={i === 0} className="group">
                  <summary className="flex cursor-pointer list-none flex-wrap items-center gap-1.5 py-1.5 text-[12px] [&::-webkit-details-marker]:hidden">
                    <span aria-hidden="true" className="text-faint transition-transform group-open:rotate-90">▸</span>
                    <Badge tone={n.state === "sent" ? "pos" : n.state === "dead_letter" ? "neg" : n.state === "suppressed" ? "warn" : "neutral"}>{humanize(String(n.state))}</Badge>
                    <Badge>{humanize(String(n.kind))}</Badge>
                    <span className="min-w-0 flex-1 truncate text-fg">{String((n.rendered_json as Json | null)?.content ?? n.event_key)}</span>
                    <span className="text-faint">#{String(n.channel ?? "?").replace(/_/g, "-")} · {fmtEt((n.source_event_at ?? n.created_at) as string)}</span>
                  </summary>
                  <div className="space-y-2 pb-3 pl-4">
                    <div className="flex flex-wrap items-center gap-2 text-[11px] text-faint">
                      <span>event {fmtEt((n.source_event_at ?? n.created_at) as string)} · {n.sent_at ? `delivered ${fmtEt(n.sent_at as string)}` : "not delivered"}{ctx.isDemo ? " (delivery time = replay load time)" : ""} · attempts {String(n.attempts)}</span>
                      {n.state === "dead_letter" ? <ActionButton url={`/api/v1/admin/outbox/${String(n.id)}/requeue`} label="Requeue" variant="secondary" /> : null}
                    </div>
                    {n.last_error ? <p className="text-[11px] text-neg">Last error: {String(n.last_error)}</p> : null}
                    {n.rendered_json ? <div className="max-w-2xl"><DiscordPreview message={n.rendered_json as Json} /></div> : <p className="text-[12px] text-muted">Not rendered yet (not delivered).</p>}
                  </div>
                </details>
              </li>
            ))}
          </ul>
        ) : <EmptyState title="No notices match" />}
      </Card>
    </>
  );
}
