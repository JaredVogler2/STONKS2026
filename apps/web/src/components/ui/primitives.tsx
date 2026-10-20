import Link from "next/link";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { fmtAge } from "@/lib/format";

/* Server-renderable building blocks. Status always carries a text label (and a glyph) so colour is never the only cue. */

export type Tone = "neutral" | "pos" | "neg" | "warn" | "info" | "demo";

const TONE: Record<Tone, string> = {
  neutral: "border-line-strong bg-surface-2 text-muted",
  pos: "border-pos/40 bg-pos/10 text-pos",
  neg: "border-neg/40 bg-neg/10 text-neg",
  warn: "border-warn/40 bg-warn/10 text-warn",
  info: "border-info/40 bg-info/10 text-info",
  demo: "border-demo/50 bg-demo/10 text-demo",
};

export function Badge({ tone = "neutral", children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4", TONE[tone], className)}>
      {children}
    </span>
  );
}

export type HealthStatus = "OK" | "DEGRADED" | "DOWN" | "STALE" | "DISABLED" | "UNCONFIGURED" | "UNKNOWN";

const STATUS_META: Record<HealthStatus, { tone: Tone; glyph: string; label: string }> = {
  OK: { tone: "pos", glyph: "●", label: "OK" },
  DEGRADED: { tone: "warn", glyph: "▲", label: "Degraded" },
  STALE: { tone: "warn", glyph: "◆", label: "Stale" },
  DOWN: { tone: "neg", glyph: "■", label: "Down" },
  DISABLED: { tone: "neutral", glyph: "○", label: "Off" },
  UNCONFIGURED: { tone: "neutral", glyph: "◌", label: "Not set up" },
  UNKNOWN: { tone: "neutral", glyph: "?", label: "Unknown" },
};

export function StatusPill({ status, label, className }: { status: string; label?: string; className?: string }) {
  const m = STATUS_META[(status as HealthStatus) in STATUS_META ? (status as HealthStatus) : "UNKNOWN"];
  return (
    <Badge tone={m.tone} className={className}>
      <span aria-hidden="true">{m.glyph}</span>
      {label ? <span>{label}:</span> : null}
      <span>{m.label}</span>
    </Badge>
  );
}

/** Data-age badge: shows how old the number is, coloured by the freshness budget for that panel. */
export function AgeBadge({ seconds, warnAfter = 900, staleAfter = 3600, prefix = "as of", className }: {
  seconds: number | null; warnAfter?: number; staleAfter?: number; prefix?: string; className?: string;
}) {
  const tone: Tone = seconds === null ? "neutral" : seconds > staleAfter ? "neg" : seconds > warnAfter ? "warn" : "neutral";
  return (
    <Badge tone={tone} className={className} title={seconds === null ? "no timestamp" : `${seconds}s old`}>
      <span aria-hidden="true">◷</span>
      {seconds === null ? "no data" : `${prefix} ${fmtAge(seconds)} ago`}
    </Badge>
  );
}

export function Card({ title, definition, interval, actions, children, className, bodyClassName, id }: {
  title: ReactNode; definition?: ReactNode; interval?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
  bodyClassName?: string; id?: string;
}) {
  return (
    <section id={id} className={cn("min-w-0 rounded-xl border border-line bg-surface", className)} aria-label={typeof title === "string" ? title : undefined}>
      <header className="flex flex-wrap items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-[13px] font-semibold tracking-wide text-fg">{title}</h2>
          {definition ? <p className="mt-0.5 text-xs leading-snug text-muted">{definition}</p> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {interval ? <span className="text-[11px] text-faint">{interval}</span> : null}
          {actions}
        </div>
      </header>
      <div className={cn("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone = "neutral", help }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone; help?: string }) {
  const valueTone = tone === "pos" ? "text-pos" : tone === "neg" ? "text-neg" : tone === "warn" ? "text-warn" : tone === "demo" ? "text-demo" : "text-fg";
  return (
    <div className="min-w-0 rounded-xl border border-line bg-surface px-3.5 py-3" title={help}>
      <div className="truncate text-[11px] font-medium uppercase tracking-wider text-faint">{label}</div>
      <div className={cn("num mt-1 truncate text-xl font-semibold", valueTone)}>{value}</div>
      {sub ? <div className="mt-0.5 line-clamp-2 text-xs leading-snug text-muted">{sub}</div> : null}
    </div>
  );
}

export function EmptyState({ title, detail, action, href }: { title: string; detail?: ReactNode; action?: string; href?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line-strong px-4 py-8 text-center">
      <div aria-hidden="true" className="text-2xl text-faint">∅</div>
      <div className="text-sm font-medium text-fg">{title}</div>
      {detail ? <div className="max-w-md text-xs text-muted">{detail}</div> : null}
      {action && href ? (
        <Link href={href} className="mt-1 text-xs font-medium text-info underline-offset-2 hover:underline">
          {action} →
        </Link>
      ) : null}
    </div>
  );
}

export function Notice({ tone = "info", title, children, action }: { tone?: Tone; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div role={tone === "neg" ? "alert" : "status"} className={cn("flex flex-wrap items-start justify-between gap-3 rounded-lg border px-3.5 py-2.5", TONE[tone])}>
      <div className="min-w-0">
        <div className="text-[13px] font-semibold">{title}</div>
        {children ? <div className="mt-0.5 text-xs text-fg/80">{children}</div> : null}
      </div>
      {action}
    </div>
  );
}

export function Table({ children, className, caption, compact }: { children: ReactNode; className?: string; caption?: string; compact?: boolean }) {
  return (
    <div className={cn("-mx-4 overflow-x-auto px-4", className)}>
      <table className={cn("w-full border-collapse text-left text-[13px]", !compact && "min-w-[560px]")}>
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        {children}
      </table>
    </div>
  );
}

export function Th({ children, className, align = "left" }: { children?: ReactNode; className?: string; align?: "left" | "right" | "center" }) {
  return (
    <th scope="col" className={cn("border-b border-line px-2 py-2 text-[11px] font-medium uppercase tracking-wider text-faint first:pl-0 last:pr-0",
      align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left", className)}>
      {children}
    </th>
  );
}

export function Td({ children, className, align = "left" }: { children?: ReactNode; className?: string; align?: "left" | "right" | "center" }) {
  return (
    <td className={cn("border-b border-line/70 px-2 py-2 align-middle first:pl-0 last:pr-0",
      align === "right" ? "num whitespace-nowrap text-right" : align === "center" ? "text-center" : "text-left", className)}>
      {children}
    </td>
  );
}

export function KeyValue({ items, className }: { items: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[13px]", className)}>
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="num min-w-0 break-words text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight text-fg md:text-xl">{title}</h1>
        {subtitle ? <p className="mt-0.5 max-w-3xl text-[13px] text-muted">{subtitle}</p> : null}
      </div>
      {actions ? <div className="no-print flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function confidenceTone(status: string | null | undefined): Tone {
  if (status === "CALIBRATED") return "pos";
  if (status === "EXPERIMENTAL") return "warn";
  return "neutral";
}

export function ConfidenceBadge({ status, probability }: { status: string; probability: number | null }) {
  if (status === "CALIBRATED" && probability !== null) {
    return <Badge tone="pos" title="Calibrated out-of-sample probability of target-before-stop">P {Math.round(probability * 100)}% calibrated</Badge>;
  }
  if (status === "EXPERIMENTAL") return <Badge tone="warn" title="Experimental model: not enough out-of-sample evidence">Experimental</Badge>;
  return <Badge tone="neutral" title="No calibrated probability; Quality is a ranking, not a chance of success">Unvalidated</Badge>;
}

const STATE_TONE: Record<string, Tone> = {
  PUBLISHED: "info", ACTIVE: "pos", CLOSED: "neutral", EXPIRED: "neutral", INVALIDATED: "neg", SUPERSEDED: "neutral", SUPPRESSED: "warn",
  DISCOVERED: "neutral", EVALUATING: "neutral", QUALIFIED: "neutral", REJECTED: "neutral",
};

export function SignalStateBadge({ state, outcome }: { state: string; outcome?: string | null }) {
  return (
    <Badge tone={STATE_TONE[state] ?? "neutral"}>
      {state.toLowerCase()}
      {outcome ? <span className="text-faint">· {outcome.toLowerCase().replace(/_/g, " ")}</span> : null}
    </Badge>
  );
}

export function DemoTag() {
  return <Badge tone="demo" title="Synthetic DEMO data: fictional tickers and prices">DEMO</Badge>;
}

export function QualityMeter({ value }: { value: number }) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <span className="inline-flex items-center gap-2" title="Quality 0-100 ranks setups against each other. It is NOT a probability.">
      <span className="num w-8 text-right font-medium">{v.toFixed(0)}</span>
      <span aria-hidden="true" className="relative hidden h-1.5 w-14 overflow-hidden rounded-full bg-surface-3 sm:inline-block">
        <span className="absolute inset-y-0 left-0 rounded-full bg-info/70" style={{ width: `${v}%` }} />
      </span>
    </span>
  );
}
