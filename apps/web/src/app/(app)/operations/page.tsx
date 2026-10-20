import { FastForward } from "lucide-react";
import { ActionButton } from "@/components/client/action-button";
import { BudgetGauge } from "@/components/ui/gauge";
import { Badge, Card, EmptyState, KeyValue, Notice, PageHeader, StatusPill, Table, Td, Th } from "@/components/ui/primitives";
import { ageSeconds, fmtAge, fmtBytes, fmtEt, humanize } from "@/lib/format";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { auditSearch } from "@/server/services/dashboard";
import { demoStatus } from "@/server/services/demo";
import { computeHealth, DB_STOP_BYTES, DB_WARN_BYTES } from "@/server/services/health";
import { operations } from "@/server/services/queries";
import { getSettings } from "@/server/services/settings";

export const dynamic = "force-dynamic";
export const metadata = { title: "Operations & Audit" };

type Json = Record<string, unknown>;
const FREE_DB_BYTES = 500 * 1024 * 1024;
const SECURITY_LABEL: Record<string, string> = {
  hmac_bad_signature: "Bad request signature", hmac_clock_skew: "Clock skew rejected", hmac_malformed_headers: "Malformed signed request",
  hmac_missing_headers: "Unsigned request", hmac_body_hash_mismatch: "Body tampered", hmac_not_configured: "HMAC secret not configured",
  agent_replayed_nonce: "Replayed nonce", agent_unknown_device: "Unknown device", agent_revoked_device: "Revoked device",
};

export default async function OperationsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  await pageOwner();
  const sp = await searchParams;
  const q = typeof sp.q === "string" ? sp.q : undefined;
  const ctx = await appContext();
  const now = await ctx.now();
  const [ops, health, audit, settings, replay] = await Promise.all([operations(ctx.db), computeHealth(ctx.db, now), auditSearch(ctx.db, q),
    getSettings(ctx.db), ctx.isDemo ? demoStatus(ctx.db) : Promise.resolve(null)]);
  const hb = ops.heartbeat as Json | null;
  const payload = (hb?.payload_json ?? {}) as Json;
  const res = (payload.resources ?? {}) as Json;
  const comps = (payload.components ?? {}) as Record<string, Json>;
  const quota = (ops.quota as Json[]).filter((x) => x.provider === "alpaca");
  const today = quota[0];
  const security = ops.security_events as Json[];
  const jobs = ops.jobs as Json[];

  return (
    <>
      <PageHeader title="Operations & Audit" subtitle="Separate health per component, free-tier budgets, security rejects and the audit trail. Nothing here upgrades a plan or spends money." />

      {ctx.isDemo && replay ? (
        <Card id="replay" title="DEMO replay" definition="Advances the deterministic fixture through the same ingestion code the Pi uses. Each step is one trading session."
          interval={`step ${replay.cursor} of ${replay.total}`}>
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-[13px] text-muted">Current session {replay.session ?? "—"}; next {replay.next_session ?? "end of fixture"}</span>
            <ActionButton url="/api/v1/admin/replay" body={{ sessions: 1 }} label="Advance 1 session" variant="primary" icon={<FastForward aria-hidden="true" className="size-4" />}
              disabled={!replay.next_session} disabledReason={!replay.next_session ? "Fixture fully replayed. Delete .apex-local/pglite to start over." : undefined} />
            <ActionButton url="/api/v1/admin/replay" body={{ sessions: 5 }} label="Advance 5" variant="secondary" disabled={!replay.next_session} />
          </div>
        </Card>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Component health" definition="Each light is computed independently with its own freshness rule." interval={`checked ${fmtEt(health.generated_at)}`}>
          <Table caption="Health components">
            <thead><tr><Th>Component</Th><Th>Status</Th><Th>Detail</Th><Th>What to do</Th></tr></thead>
            <tbody>
              {health.components.map((c) => (
                <tr key={c.key}>
                  <Td className="whitespace-nowrap">{c.label}</Td><Td><StatusPill status={c.status} /></Td>
                  <Td className="text-[12px] text-muted">{c.detail}</Td><Td className="text-[12px]">{c.action ?? "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
        <Card title="Raspberry Pi" definition="From the latest signed heartbeat." interval={hb ? `${fmtAge(ageSeconds(ctx.isDemo ? now : new Date(), hb.received_at as string))} ago` : "no heartbeat"}>
          {hb ? (
            <KeyValue items={[
              ["Worker version", String(payload.worker_version ?? "?")], ["Release mode", String(payload.release_mode ?? "?")],
              ["CPU temperature", res.cpu_temp_c !== undefined ? `${String(res.cpu_temp_c)} °C` : "—"], ["Load (1 min)", String(res.load_1m ?? "—")],
              ["Memory available", res.mem_available_mb !== undefined ? `${String(res.mem_available_mb)} MB` : "—"], ["Worker RSS", res.rss_mb !== undefined ? `${String(res.rss_mb)} MB` : "—"],
              ["Disk free", res.disk_free_pct !== undefined ? `${String(res.disk_free_pct)}%` : "—"], ["Clock synced", res.clock_synced ? "yes" : "no"],
              ["Spool pending", String(res.spool_pending ?? 0)], ["Clock skew", `${Math.round(Number(hb.clock_skew_ms ?? 0) / 1000)} s`],
            ]} />
          ) : <EmptyState title="No heartbeat received" detail="Pair the Pi in Settings, then start the apex-worker service." />}
          {Object.keys(comps).length ? (
            <ul className="mt-3 space-y-1">{Object.entries(comps).map(([k, v]) => (
              <li key={k} className="flex items-center justify-between gap-2 text-[12px]"><span className="text-muted">{humanize(k)}</span><StatusPill status={String(v.status ?? "UNKNOWN")} /></li>))}
            </ul>
          ) : null}
        </Card>
      </div>

      <section aria-label="Free-tier budgets" className="grid gap-4 md:grid-cols-3">
        <Card title="Database storage" definition="Supabase Free: 500 MB. Retention prunes quotes, heartbeats, attempts and old feature snapshots.">
          <BudgetGauge used={ops.db_bytes as number / 1024 / 1024} budget={FREE_DB_BYTES / 1024 / 1024} unit="MB" digits={1} label={`warn at ${fmtBytes(DB_WARN_BYTES)}, stop at ${fmtBytes(DB_STOP_BYTES)}`} />
          <div className="mt-3"><ActionButton url="/api/v1/admin/retention" label="Run retention now" variant="secondary" successMessage="Retention applied" /></div>
        </Card>
        <Card title="Alpaca requests (today)" definition="Counted by the Pi's rate limiter; the free plan allows 200 requests/minute.">
          {today ? <BudgetGauge used={Number(today.requests)} budget={Number(today.limit_per_day ?? settings.budgets.value.alpaca_daily_requests)} unit="req" label={`day ${String(today.day).slice(0, 10)}`} />
            : <p className="text-[13px] text-muted">UNKNOWN: no usage reported{ctx.isDemo ? " (DEMO uses no API)" : ""}.</p>}
        </Card>
        <Card title="Vercel and GitHub" definition="Usage the app cannot measure from inside.">
          <p className="text-[13px] text-muted">Vercel Hobby function usage: UNKNOWN here; check the Vercel Usage page. GitHub Actions minutes: see Models &amp; Automation.</p>
        </Card>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card id="security" title="Security events" definition="Rejected agent requests and blocked access attempts (most recent 30). A burst usually means a misconfigured clock or a leaked secret.">
          {security.length ? (
            <Table caption="Security events">
              <thead><tr><Th>When</Th><Th>Event</Th><Th>Agent</Th><Th>Route</Th></tr></thead>
              <tbody>{security.map((e, i) => (
                <tr key={i}><Td className="whitespace-nowrap text-muted">{fmtEt(e.at as string)}</Td><Td><Badge tone="warn">{SECURITY_LABEL[String(e.kind)] ?? humanize(String(e.kind))}</Badge></Td>
                  <Td className="num text-[12px]">{String(e.agent_id ?? "—")}</Td><Td className="text-[12px] text-muted">{String(e.route ?? "—")}</Td></tr>))}
              </tbody>
            </Table>
          ) : <EmptyState title="No security rejects recorded" />}
        </Card>
        <Card title="Jobs and errors" definition="Pi job runs with redacted error text. Failed jobs retry on the next cycle; dead-lettered notices can be requeued from Alerts.">
          {jobs.length ? (
            <Table caption="Job runs">
              <thead><tr><Th>Started</Th><Th>Job</Th><Th>Result</Th><Th>Error</Th></tr></thead>
              <tbody>{jobs.map((j, i) => (
                <tr key={i}><Td className="whitespace-nowrap text-muted">{fmtEt(j.started_at as string)}</Td><Td>{String(j.job_name)}</Td>
                  <Td><Badge tone={j.success ? "pos" : j.success === false ? "neg" : "neutral"}>{j.success ? "ok" : j.success === false ? "failed" : "running"}</Badge></Td>
                  <Td className="text-[12px] text-muted">{String(j.last_error ?? "—")}</Td></tr>))}
              </tbody>
            </Table>
          ) : <EmptyState title="No job runs reported" detail={ctx.isDemo ? "The DEMO replay does not emit job runs." : "The Pi reports job runs with its heartbeat."} />}
        </Card>
      </div>

      <Card title="Audit trail" definition="Every owner, agent and system change (metadata redacted). Search by action, entity or id."
        actions={
          <form method="get" role="search" className="flex gap-2">
            <label className="sr-only" htmlFor="audit-q">Search audit</label>
            <input id="audit-q" name="q" defaultValue={q ?? ""} placeholder="e.g. model, settings, signal" className="min-h-8 rounded-lg border border-line-strong bg-surface-2 px-2.5 text-[12px]" />
            <button type="submit" className="min-h-8 rounded-lg border border-line-strong bg-surface-2 px-3 text-[12px]">Search</button>
          </form>
        }>
        {audit.audit.length ? (
          <Table caption="Audit events">
            <thead><tr><Th>When</Th><Th>Actor</Th><Th>Action</Th><Th>Entity</Th><Th>Details</Th><Th>Correlation</Th></tr></thead>
            <tbody>{audit.audit.map((a) => (
              <tr key={String(a.id)}><Td className="whitespace-nowrap text-muted">{fmtEt(a.at as string)}</Td><Td>{String(a.actor_type)}</Td><Td className="font-medium">{String(a.action)}</Td>
                <Td className="text-[12px] text-muted">{String(a.entity)}{a.entity_id ? ` · ${String(a.entity_id).slice(0, 28)}` : ""}</Td>
                <Td className="max-w-[320px] truncate text-[11px] text-faint" ><span title={JSON.stringify(a.redacted_metadata_json)}>{JSON.stringify(a.redacted_metadata_json)}</span></Td>
                <Td className="num text-[11px] text-faint">{String(a.correlation_id ?? "—")}</Td></tr>))}
            </tbody>
          </Table>
        ) : <EmptyState title={q ? "No audit entries match" : "No audit entries yet"} />}
      </Card>

      <Card title="Configuration history" definition="Each settings version with its redacted before/after. Secret values are never recorded.">
        {audit.history.length ? (
          <Table caption="Settings history">
            <thead><tr><Th>When</Th><Th>Setting</Th><Th align="right">Version</Th><Th>Change</Th></tr></thead>
            <tbody>{audit.history.map((hh, i) => (
              <tr key={i}><Td className="whitespace-nowrap text-muted">{fmtEt(hh.at as string)}</Td><Td>{String(hh.key)}</Td><Td align="right">{String(hh.version)}</Td>
                <Td className="max-w-[520px] truncate text-[11px] text-faint"><span title={JSON.stringify(hh.redacted_change)}>{JSON.stringify(hh.redacted_change)}</span></Td></tr>))}
            </tbody>
          </Table>
        ) : <EmptyState title="No settings changed yet" detail="Defaults are in effect." />}
      </Card>
      {(ops.dead_letters as Json[]).length ? <Notice tone="warn" title={`${(ops.dead_letters as Json[]).length} dead-lettered notice(s)`}>Inspect and requeue them under Alerts &amp; Delivery.</Notice> : null}
    </>
  );
}
